"""FastAPI application factory."""
from __future__ import annotations

import logging
import logging.handlers
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.config import get_settings
from app.security.auth import LoginRequired, PermissionDenied, client_ip
from app.services import context
from app.services.errors import BusinessError
from app.web.templating import back_url, flash, redirect, render

log = logging.getLogger("ctdms")


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
    if s.is_production and s.secret_key.startswith("change-me"):
        log.warning("CTDMS_SECRET_KEY is the default value - set a strong secret in production!")
    log.info("CTDMS %s starting (%s, db=%s)", __version__, s.environment, s.database_url.split("@")[-1])
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="CTDMS - Central Transport & Dispatch API", version=__version__,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json", lifespan=lifespan)

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
        if s.session_cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if not request.url.path.startswith("/static"):
            log.info("%s %s %s %.0fms", request.method, request.url.path, response.status_code, ms)
        return response

    app.add_middleware(SessionMiddleware, secret_key=s.secret_key, session_cookie=s.session_cookie_name,
                       max_age=s.session_idle_minutes * 60 * 3, same_site="lax", https_only=s.session_cookie_secure)

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
            return JSONResponse({"detail": exc.errors()}, status_code=422)
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
        admin, approvals, auth_routes, dashboard_routes, dispatch_routes, imports_routes, invoices_routes,
        masters_routes, rates_routes, recon_routes, reports_routes,
    )
    for mod in (auth_routes, dashboard_routes, invoices_routes, imports_routes, dispatch_routes, approvals,
                masters_routes, rates_routes, recon_routes, reports_routes, admin):
        app.include_router(mod.router)
    app.include_router(v1.router)

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
