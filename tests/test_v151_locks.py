"""v1.5.1 - paid services locked by default, no SSO, admin sets passwords."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import User
from app.services import bill_scan_service as scan_svc
from app.services import notify_service as ns


@pytest.fixture()
def locked(monkeypatch):
    monkeypatch.setattr(get_settings(), "paid_services", False)
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "sk-test")


def test_bill_reader_locked_without_paid_services(locked, web):
    assert scan_svc.locked() and not scan_svc.available()
    page = web("accounts").get("/recon/scan")
    assert "Automatic bill reading is locked" in page.text and "Upload and type the lines" in page.text


def test_whatsapp_sms_locked_but_log_allowed(locked, db):
    from app.services import settings_service as cfg
    for prov, effective in (("WHATSAPP_CLOUD", "OFF"), ("HTTP_SMS", "OFF"), ("LOG", "LOG")):
        cfg.set_value(db, "notify.provider", prov)
        db.flush()
        assert ns.provider(db) == effective
    db.rollback()


def test_no_sso_routes(web):
    w = web("admin")
    assert w.get("/login/sso").status_code == 404
    assert "company account" not in w.c.get("/login").text


def test_admin_creates_user_with_password_that_need_not_change(web, db):
    from app.models import Role
    w = web("admin")
    role = db.scalar(select(Role).where(Role.code == "VIEWER")) or db.scalar(select(Role))
    r = w.post("/admin/users/save", {"username": "gate.guard1", "full_name": "Gate Guard One",
                                     "role_id": str(role.id), "password": "Gate#Pass2026", "is_active": "1",
                                     "all_companies": "1", "all_cfas": "1"})
    assert "User gate.guard1 saved" in r.text
    u = db.scalar(select(User).where(User.username == "gate.guard1"))
    assert u.must_change_password is False
    from tests.conftest import WebClient
    assert WebClient("gate.guard1", "Gate#Pass2026").get("/account/password").status_code == 200
    r = w.post(f"/admin/users/{u.id}/password", {"password": "New#Guard2026", "must_change": "1"})
    assert "they must change it" in r.text
    db.expire_all()
    assert db.get(User, u.id).must_change_password is True
