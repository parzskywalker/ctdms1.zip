from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User, utcnow
from app.security.auth import (
    authenticate, client_ip, create_session, csrf_protect, current_user, current_user_optional, hash_password,
    password_problems, revoke_session, revoke_user_sessions, verify_password,
)
from app.services import audit
from app.web.templating import flash, redirect, render, safe_path

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)

DEMO_USERS = [
    ("admin", "Admin@123", "Administrator"),
    ("management", "Demo@123", "Management"),
    ("accounts", "Demo@123", "Accounts"),
    ("cfa.raipur", "Demo@123", "CFA Manager · Raipur"),
    ("operator.raipur", "Demo@123", "Dispatch Operator · Raipur"),
    ("operator.bilaspur", "Demo@123", "Dispatch Operator · Bilaspur"),
    ("operator.durg", "Demo@123", "Dispatch Operator · Durg"),
    ("checker", "Demo@123", "Checker / Approver"),
    ("viewer", "Demo@123", "Read-only Viewer"),
]


def _home_for(user: User) -> str:
    if user.can("dashboard.view"):
        return "/"
    if user.can("dispatch.operate"):
        return "/dispatch/pending"
    if user.can("report.view"):
        return "/reports"
    return "/invoices"


@router.get("/login")
def login_page(request: Request, next: str = "/", user: User | None = Depends(current_user_optional)):
    if user:
        return redirect(safe_path(next))
    return render(request, "login.html", {"next": next, "demo_users": DEMO_USERS if get_settings().show_demo_logins else []})


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/"),
          db: Session = Depends(get_db)):
    user, err = authenticate(db, username, password)
    if not user:
        audit.log(db, None, "LOGIN_FAILED", "SECURITY", ref=username[:50], reason=err)
        db.commit()
        return render(request, "login.html", {"error": err, "next": next, "username": username,
                                              "demo_users": DEMO_USERS if get_settings().show_demo_logins else []},
                      status_code=401)
    token = create_session(db, user, client_ip(request), request.headers.get("user-agent"))
    request.session.clear()
    request.session["sid"] = token
    audit.log(db, user, "LOGIN", "SECURITY", record_type="User", record_id=user.id, ref=user.username)
    db.commit()
    if user.must_change_password:
        flash(request, "Please set a new password before continuing.", "warn")
        return redirect("/account/password")
    target = safe_path(next, "") if next != "/" else ""
    target = target or _home_for(user)
    return redirect(target)


def _sso_redirect_uri(request: Request, db: Session) -> str:
    from app.services import settings_service as cfg
    base = (cfg.get(db, "org.public_url") or "").strip().rstrip("/") or str(request.base_url).rstrip("/")
    return base + "/login/sso/callback"


def _login_error(request: Request, msg: str, status: int = 401):
    return render(request, "login.html", {"error": msg, "next": "/", "demo_users": DEMO_USERS
                                          if get_settings().show_demo_logins else []}, status_code=status)


@router.get("/login/sso")
def sso_start(request: Request, next: str = "/", db: Session = Depends(get_db)):
    from app.services import sso_service
    from app.services.errors import BusinessError
    if not get_settings().sso_enabled:
        return redirect("/login")
    try:
        url, saved = sso_service.start(_sso_redirect_uri(request, db))
    except BusinessError as e:
        return _login_error(request, e.message, 503)
    except Exception:  # noqa: BLE001 - provider down / misconfigured
        return _login_error(request, "The sign-in provider cannot be reached right now - use your password", 503)
    request.session["sso"] = {**saved, "next": safe_path(next)}
    return redirect(url)


@router.get("/login/sso/callback")
def sso_callback(request: Request, db: Session = Depends(get_db)):
    from app.services import sso_service
    from app.services.errors import BusinessError
    saved = request.session.pop("sso", None)
    qp = request.query_params
    if qp.get("error"):
        return _login_error(request, f"Sign-in cancelled ({qp.get('error')})")
    try:
        claims = sso_service.finish(qp.get("code") or "", qp.get("state") or "", saved)
        user = sso_service.user_for(db, claims)
    except BusinessError as e:
        audit.log(db, None, "LOGIN_FAILED", "SECURITY", ref=str((saved or {}).get("state", ""))[:8] + " SSO",
                  reason=e.message)
        db.commit()
        return _login_error(request, e.message)
    except Exception:  # noqa: BLE001
        return _login_error(request, "Sign-in failed - please try again or use your password", 503)
    token = create_session(db, user, client_ip(request), request.headers.get("user-agent"))
    request.session.clear()
    request.session["sid"] = token
    user.last_login_at = utcnow()
    user.failed_logins = 0
    audit.log(db, user, "LOGIN", "SECURITY", record_type="User", record_id=user.id, ref=user.username,
              details={"method": "SSO", "issuer": claims.get("iss")})
    db.commit()
    nxt = (saved or {}).get("next") or "/"
    return redirect(safe_path(nxt, "") if nxt != "/" else _home_for(user))


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db), user: User | None = Depends(current_user_optional)):
    revoke_session(db, request.session.get("sid"))
    if user:
        audit.log(db, user, "LOGOUT", "SECURITY", record_type="User", record_id=user.id, ref=user.username)
        db.commit()
    request.session.clear()
    return redirect("/login")


@router.get("/account/password")
def password_page(request: Request, user: User = Depends(current_user)):
    return render(request, "password.html", {"title": "Change password"})


@router.post("/account/password")
def change_password(request: Request, current: str = Form(...), new: str = Form(...), confirm: str = Form(...),
                    db: Session = Depends(get_db), user: User = Depends(current_user)):
    user = db.merge(user)
    if not verify_password(current, user.password_hash):
        flash(request, "Current password is incorrect", "error")
        return redirect("/account/password")
    if new != confirm:
        flash(request, "New passwords do not match", "error")
        return redirect("/account/password")
    problems = password_problems(new, user.username)
    if problems:
        flash(request, "Password needs " + ", ".join(problems), "error")
        return redirect("/account/password")
    user.password_hash = hash_password(new)
    user.must_change_password = False
    user.password_changed_at = utcnow()
    revoke_user_sessions(db, user.id)
    audit.log(db, user, "PASSWORD_CHANGED", "SECURITY", record_type="User", record_id=user.id, ref=user.username)
    token = create_session(db, user, client_ip(request), request.headers.get("user-agent"))
    request.session["sid"] = token
    db.commit()
    flash(request, "Password changed. Other sessions were signed out.", "success")
    return redirect(_home_for(user))
