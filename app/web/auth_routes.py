from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app import demo_users
from app.config import get_settings
from app.db import get_db
from app.models import User, utcnow
from app.security.auth import (
    authenticate, client_ip, create_session, csrf_protect, current_user, current_user_optional, hash_password,
    password_problems, revoke_session, revoke_user_sessions, verify_password,
)
from app.services import audit
from app.web.templating import back_url, flash, home_for, redirect, render, safe_path

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)

DEMO_USERS = demo_users.login_rows()  # one list for the seed, this page and prepare-demo


@router.get("/login")
def login_page(request: Request, next: str = "/", user: User | None = Depends(current_user_optional)):
    if user:
        return redirect(safe_path(next))
    return render(request, "login.html", {"next": next, "demo_users": DEMO_USERS if get_settings().show_demo_logins else []})


THROTTLE_MINUTES = 10  # per address; a whole office shares one internet address, so the ceiling is high


def _throttled(db: Session, ip: str | None) -> bool:
    from datetime import timedelta

    from sqlalchemy import func, select

    from app.models import AuditLog, utcnow
    limit = get_settings().login_throttle_per_ip
    if not ip or not limit:
        return False
    since = utcnow() - timedelta(minutes=THROTTLE_MINUTES)
    return (db.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "LOGIN_FAILED", AuditLog.ip == ip,
                                                             AuditLog.ts >= since)) or 0) >= limit


def _start_session(request: Request, db: Session, user: User, next: str):
    token = create_session(db, user, client_ip(request), request.headers.get("user-agent"))
    request.session.clear()
    request.session["sid"] = token
    audit.log(db, user, "LOGIN", "SECURITY", record_type="User", record_id=user.id, ref=user.username)
    db.commit()
    if user.must_change_password:
        flash(request, "Please set a new password before continuing.", "warn")
        return redirect("/account/password")
    target = safe_path(next, "") if next != "/" else ""
    target = target or home_for(user)
    return redirect(target)


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/"),
          db: Session = Depends(get_db)):
    demo = DEMO_USERS if get_settings().show_demo_logins else []
    if _throttled(db, client_ip(request)):
        audit.log(db, None, "LOGIN_THROTTLED", "SECURITY", ref=username[:50])
        db.commit()
        return render(request, "login.html", {"error": f"Too many failed sign-ins from this network - wait {THROTTLE_MINUTES} "
                                              "minutes and try again.", "next": next, "username": username,
                                              "demo_users": demo}, status_code=429)
    user, err = authenticate(db, username, password)
    if not user:
        audit.log(db, None, "LOGIN_FAILED", "SECURITY", ref=username[:50], reason=err)
        db.commit()
        return render(request, "login.html", {"error": err, "next": next, "username": username, "demo_users": demo},
                      status_code=401)
    if user.totp_secret:  # password right: now the 6-digit code from the phone
        request.session.clear()
        request.session["mfa_uid"], request.session["mfa_next"] = user.id, next
        return render(request, "login.html", {"mfa": True, "next": next})
    return _start_session(request, db, user, next)


@router.post("/login/code")
def login_code(request: Request, code: str = Form(""), db: Session = Depends(get_db)):
    from app.security import totp
    uid = request.session.get("mfa_uid")
    user = db.get(User, uid) if uid else None
    if user is None or not user.is_active:
        request.session.clear()
        return redirect("/login")
    if not totp.verify(user.totp_secret, code):
        audit.log(db, None, "LOGIN_FAILED", "SECURITY", ref=user.username, reason="Wrong phone code")
        db.commit()
        if _throttled(db, client_ip(request)):
            request.session.clear()
        return render(request, "login.html", {"mfa": True, "error": "Wrong code - type the 6 digits shown in the phone app now.",
                                              "next": request.session.get("mfa_next", "/")}, status_code=401)
    return _start_session(request, db, user, request.session.get("mfa_next", "/"))


@router.get("/account/2fa")
def two_factor_page(request: Request, user: User = Depends(current_user)):
    from app.security import totp
    secret = request.session.get("mfa_new") or totp.new_secret()
    request.session["mfa_new"] = secret
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    w = QrCodeWidget(totp.uri(secret, user.username))
    d = Drawing(200, 200)
    w.barWidth = w.barHeight = 200
    d.add(w)
    svg = renderSVG.drawToString(d)
    return render(request, "two_factor.html", {"title": "Phone sign-in code", "secret": secret,
                                                "qr": svg[svg.find("<svg"):], "has": bool(user.totp_secret)})


@router.post("/account/2fa")
def two_factor_save(request: Request, code: str = Form(""), db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    from app.security import totp
    secret = request.session.get("mfa_new")
    if not totp.verify(secret, code):
        flash(request, "Wrong code - scan the picture again and type the 6 digits the app shows now.", "error")
        return redirect("/account/2fa")
    real = db.merge(user)
    real.totp_secret = secret
    request.session.pop("mfa_new", None)
    audit.log(db, real, "TWO_FACTOR_ON", "SECURITY", record_type="User", record_id=real.id, ref=real.username)
    db.commit()
    flash(request, "Phone sign-in code is on. From now on sign-in asks for it after the password.", "success")
    return redirect("/")


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
    return redirect(home_for(user))


@router.post("/account/depot")
def switch_depot(request: Request, cfa_id: str = Form(""), user: User = Depends(current_user)):
    """Work in one of the user's assigned depots (or all of them). Never widens access: only depots already assigned
    to this user can be chosen; the scope checks everywhere read the choice through User.cfa_ids."""
    mine = {c.id: c for c in user.cfas}
    if user.all_cfas or len(mine) < 2:
        return redirect(back_url(request, "/"))
    try:
        want = int(cfa_id) if cfa_id.strip() else 0
    except ValueError:
        want = -1
    if want == 0:
        request.session.pop("depot", None)
        flash(request, "Showing all your depots.", "info")
    elif want in mine:
        request.session["depot"] = want
        flash(request, f"Working in {mine[want].name} ({mine[want].code}).", "info")
    else:
        flash(request, "That depot is not assigned to you.", "error")
    return redirect(back_url(request, "/"))
