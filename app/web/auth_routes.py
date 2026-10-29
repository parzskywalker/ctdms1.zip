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
from app.web.templating import back_url, flash, home_for, redirect, render, safe_path

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)

from app.demo_users import login_rows  # noqa: E402

DEMO_USERS = login_rows()


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
    target = target or home_for(user)
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
