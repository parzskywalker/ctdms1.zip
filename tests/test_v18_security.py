"""v1.8 basic internet security: a pause after many failed sign-ins from one address, and the phone sign-in code."""
from __future__ import annotations

from sqlalchemy import delete, select

from app.config import get_settings
from app.models import AuditLog, User
from app.security import totp


def test_many_failed_sign_ins_from_one_address_pause_it(db, monkeypatch):
    from app.web.auth_routes import _throttled
    ip = "203.0.113.77"
    monkeypatch.setattr(get_settings(), "login_throttle_per_ip", 3)
    try:
        assert not _throttled(db, ip)
        for _ in range(3):
            db.add(AuditLog(action="LOGIN_FAILED", module="SECURITY", ip=ip))
        db.flush()
        assert _throttled(db, ip) and not _throttled(db, "203.0.113.78")
    finally:
        db.execute(delete(AuditLog).where(AuditLog.ip == ip))
        db.commit()


def test_phone_code_is_asked_after_the_password(web, db):
    secret = totp.new_secret()
    u = db.scalar(select(User).where(User.username == "accounts"))
    u.totp_secret = secret
    db.commit()
    try:
        from fastapi.testclient import TestClient

        from app.main import app
        from tests.conftest import _csrf
        c = TestClient(app)
        page = c.post("/login", data={"username": "accounts", "password": "Demo@123", "next": "/",
                                      "csrf_token": _csrf(c.get("/login").text)})
        assert "6-digit code from your phone app" in page.text
        bad = c.post("/login/code", data={"code": "000000", "csrf_token": _csrf(page.text)})
        assert bad.status_code == 401
        ok = c.post("/login/code", data={"code": totp.code_at(secret), "csrf_token": _csrf(bad.text)},
                    follow_redirects=False)
        assert ok.status_code == 303
        assert c.get("/account/password").status_code == 200       # signed in
    finally:
        u.totp_secret = None
        db.commit()


def test_admin_in_training_without_phone_code_sets_it_up_first_no_redirect_loop(web, db, monkeypatch):
    from app.models import TrainingProgress
    from tests.test_training import _switch
    _switch(web, True)
    admin = db.scalar(select(User).where(User.username == "admin"))
    p = db.scalar(select(TrainingProgress).where(TrainingProgress.user_id == admin.id))
    if p is None:
        p = TrainingProgress(user_id=admin.id, path="MASTER")
        db.add(p)
    p.required, p.status = True, "NOT_STARTED"
    db.commit()
    monkeypatch.setattr(get_settings(), "admin_2fa", True)
    try:
        w = web("admin")
        assert w.get("/training", follow_redirects=False).headers["location"] == "/account/2fa"
        assert w.get("/account/2fa", follow_redirects=False).status_code == 200   # was a 303 back to /training
    finally:
        monkeypatch.setattr(get_settings(), "admin_2fa", False)
        p.required = False
        db.commit()
        _switch(web, False)


def test_setup_page_shows_a_qr_picture(web):
    page = web("admin").get("/account/2fa").text
    assert "<svg" in page and "Turn on" in page
