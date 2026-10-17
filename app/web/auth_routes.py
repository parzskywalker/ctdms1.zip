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
    ("cfa.raipur", "Demo@123", "Depot Manager · Raipur"),
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
