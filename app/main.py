"""FastAPI application factory."""
from __future__ import annotations

import logging
import logging.handlers
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.config import get_settings
from app.security.auth import LoginRequired, PasswordChangeRequired, PermissionDenied, client_ip
from app.services import context
from app.services.errors import BusinessError
from app.web.templating import back_url, flash, redirect, render

log = logging.getLogger("ctdms")

# Everything (JS, CSS, Chart.js) is served locally; inline <script>/<style> blocks are still used by a few templates.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; form-action 'self'; base-uri 'self'; "
       "object-src 'none'")


class ReadRequestMiddleware:
    """Page views (GET/HEAD) run their database reads without taking SQLite's single write lock (see app.db).

    Safety net: if such a request still collides with a writer ("database is locked") before anything was sent
    to the browser, it is run once more - holding the write lock - instead of showing an error page. It is NOT
    re-run if it had already saved something (that could save it twice) or had already been waiting for the
    write lock (that would only double the wait). Each attempt starts with fresh request state.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return
        from app.db import db_mode, is_busy_error, request_db_state
        original_state = dict(scope.get("state") or {})
        for attempt in range(2):
            started = False
            notes = {"immediate": False, "committed": False}
            attempt_scope = dict(scope)
            attempt_scope["state"] = dict(original_state)

            async def _send(message, _send=send):
                nonlocal started
                if message["type"] == "http.response.start":
                    started = True
                await _send(message)

            if attempt == 0:
                rcv = receive
            else:
                sent_body = False

                async def rcv():  # a GET has no body; replay an empty one, then pass through (disconnects)
                    nonlocal sent_body
                    if not sent_body:
                        sent_body = True
                        return {"type": "http.request", "body": b"", "more_body": False}
                    return await receive()

            token = db_mode.set("read" if attempt == 0 else "write")
            token2 = request_db_state.set(notes)
            try:
                await self.app(attempt_scope, rcv, _send)
                return
            except Exception as exc:
                if started or attempt == 1 or notes["immediate"] or notes["committed"] or not is_busy_error(exc):
                    raise
                log.warning("GET %s collided with a database writer - running it once more", scope.get("path"))
            finally:
                request_db_state.reset(token2)
                db_mode.reset(token)


def _setup_logging() -> None:
    s = get_settings()
    root = logging.getLogger()
    if any(getattr(h, "_ctdms", False) for h in root.handlers):
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    fh = logging.handlers.RotatingFileHandler(s.log_dir / "ctdms.log", maxBytes=5_000_000, backupCount=10,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    fh._ctdms = True  # type: ignore[attr-defined]
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh._ctdms = True  # type: ignore[attr-defined]
    root.addHandler(fh)
    root.addHandler(sh)
    root.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    s = get_settings()
    if s.is_production and (s.secret_key.startswith("change-me") or len(s.secret_key) < 32):
        # Sessions and API tokens are signed with this key; the default is public (it is in the source code).
        raise RuntimeError("CTDMS_SECRET_KEY is missing/weak. Set a random value of 32+ characters in .env before "
                           "running in production (python -c \"import secrets; print(secrets.token_urlsafe(48))\").")
    if s.is_production and s.show_demo_logins:
        log.warning("CTDMS_SHOW_DEMO_LOGINS is on in production - demo usernames/passwords are shown on the login page!")
    log.info("CTDMS %s starting (%s, db=%s)", __version__, s.environment, s.database_url.split("@")[-1])
    if s.environment != "test":
        from app.services import inbox_service, notify_service
        inbox_service.start_watcher()  # ERP exports dropped into data/inbox are imported automatically
        notify_service.start_sender()  # queued WhatsApp / SMS dispatch alerts go out in the background
        from app.services import subscription_service
        subscription_service.start_sender()  # e-mailed reports (when SMTP is set up)
    yield
    if s.environment != "test":
        from app.services import inbox_service, notify_service
        inbox_service.stop_watcher()
        notify_service.stop_sender()
        from app.services import subscription_service
        subscription_service.stop_sender()


def create_app() -> FastAPI:
    s = get_settings()
    public_docs = s.api_docs_public if s.api_docs_public is not None else not s.is_production
    app = FastAPI(title="CTDMS - Central Transport & Dispatch API", version=__version__,
                  docs_url="/api/docs" if public_docs else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if public_docs else None, lifespan=lifespan)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = uuid.uuid4().hex[:12]
        context.begin(request_id=rid, ip=client_ip(request), session=None)
        start = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "same-origin"
        if not request.url.path.startswith("/api/docs"):
            response.headers.setdefault("Content-Security-Policy", CSP)
        if s.session_cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if not request.url.path.startswith("/static"):
            log.info("%s %s %s %.0fms", request.method, request.url.path, response.status_code, ms)
        return response

    app.add_middleware(SessionMiddleware, secret_key=s.secret_key, session_cookie=s.session_cookie_name,
                       max_age=s.session_idle_minutes * 60 * 3, same_site="lax", https_only=s.session_cookie_secure)
    app.add_middleware(ReadRequestMiddleware)  # outermost of ours: a retry re-runs session + request handling

    from pathlib import Path
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    def is_api(request: Request) -> bool:
        return request.url.path.startswith("/api/")

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        if is_api(request):
            return JSONResponse({"detail": "Authentication required"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={nxt}", status_code=303)

    @app.exception_handler(PasswordChangeRequired)
    async def _pw_change(request: Request, exc: PasswordChangeRequired):
        if is_api(request):
            return JSONResponse({"detail": "Password change required - sign in to the web app and set a new password"},
                                status_code=403)
        flash(request, "Please set a new password before continuing.", "warn")
        return redirect("/account/password")

    @app.exception_handler(PermissionDenied)
    async def _denied(request: Request, exc: PermissionDenied):
        if is_api(request):
            return JSONResponse({"detail": exc.message}, status_code=403)
        return render(request, "error.html", {"code": 403, "title": "Access denied", "message": exc.message},
                      status_code=403)

    @app.exception_handler(BusinessError)
    async def _business(request: Request, exc: BusinessError):
        if is_api(request):
            return JSONResponse({"detail": exc.message, "field": exc.field}, status_code=400)
        flash(request, exc.message, "error")
        back = back_url(request, "/")
        return redirect(back)

    @app.exception_handler(StaleDataError)
    async def _stale(request: Request, exc: StaleDataError):
        msg = "This record was changed by another user a moment ago. Reload and try again."
        if is_api(request):
            return JSONResponse({"detail": msg}, status_code=409)
        flash(request, msg, "error")
        return redirect(back_url(request, "/"))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        if is_api(request):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        titles = {404: "Not found", 405: "Method not allowed"}
        return render(request, "error.html", {"code": exc.status_code, "title": titles.get(exc.status_code, "Error"),
                                              "message": exc.detail if exc.status_code != 404 else
                                              "The page or record you asked for does not exist."},
                      status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        if is_api(request):
            # jsonable_encoder: errors can contain raw bytes (e.g. a form body sent to a JSON endpoint) which
            # json.dumps cannot serialise -> previously turned every such 422 into a 500.
            return JSONResponse({"detail": jsonable_encoder(exc.errors(), custom_encoder={bytes: lambda b: b.decode("utf-8", "replace")[:200]})},
                                status_code=422)
        flash(request, "Some fields were invalid: " + "; ".join(
            f"{'.'.join(str(x) for x in e.get('loc', [])[1:])}: {e.get('msg')}" for e in exc.errors()[:5]), "error")
        return redirect(back_url(request, "/"))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s (req %s)", request.method, request.url.path, context.request_id.get())
        if is_api(request):
            return JSONResponse({"detail": "Internal server error", "request_id": context.request_id.get()},
                                status_code=500)
        return render(request, "error.html", {"code": 500, "title": "Something went wrong",
                                              "message": f"The error has been logged (reference {context.request_id.get()})."},
                      status_code=500)

    from app.api import v1
    from app.web import (
        admin, approvals, auth_routes, dashboard_routes, dispatch_routes, gatepass_routes, imports_routes,
        invoices_routes, masters_routes, notify_routes, rate_card_routes, rates_routes, recon_routes, reports_routes,
        subscription_routes, tracking_routes,
    )
    for mod in (auth_routes, dashboard_routes, invoices_routes, imports_routes, dispatch_routes, gatepass_routes,
                notify_routes, tracking_routes, approvals, masters_routes, rates_routes, rate_card_routes, recon_routes,
                subscription_routes, reports_routes, admin):
        app.include_router(mod.router)
    app.include_router(v1.router)

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
