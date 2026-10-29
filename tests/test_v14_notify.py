"""v1.4 - WhatsApp / SMS dispatch alerts to customers."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (
    AuditLog, Customer, Invoice, Notification, NotifyStatus, Setting, Transporter, User,
)
from app.services import dispatch_service as ds
from app.services import notify_service as ns
from tests.helpers import get_inv, make_invoice, local_auto, print_pawatis, walk_out

ROOT = Path(__file__).resolve().parent.parent


def _set(**values) -> None:
    s = SessionLocal()
    try:
        for k, v in values.items():
            row = s.get(Setting, k)
            if row is None:
                from app.services import settings_service as cfg
                cfg.set_value(s, k, v)
            else:
                row.value = "true" if v is True else "false" if v is False else str(v)
        s.commit()
    finally:
        s.close()


@pytest.fixture()
def provider():
    """Switch the provider for one test; always back to OFF (the default) afterwards."""
    def _use(name, **extra):
        _set(**{"notify.provider": name, **extra})
    yield _use
    _set(**{"notify.provider": "OFF", "notify.http_url": "", "notify.whatsapp_phone_number_id": "",
            "notify.on_gate_out": True, "notify.on_lr": True, "notify.max_attempts": "3"})
    s = SessionLocal()  # messages a test left waiting must not be sent by the next test's provider
    try:
        s.query(Notification).filter(Notification.status == NotifyStatus.QUEUED).update(
            {"status": NotifyStatus.FAILED, "last_error": "test cleanup"})
        s.commit()
    finally:
        s.close()


def _u(db, name) -> User:
    return db.scalar(select(User).where(User.username == name))


def _dispatch(api, db, customers=("C-HRM01", "C-ABC01"), boxes=(3, 5)):
    """Two customers on one Osho Express gatepass, printed and taken through the gate."""
    invs = [get_inv(make_invoice(api("admin"), company="COA", cfa="RPR", customer=c, boxes=b)["id"])
            for c, b in zip(customers, boxes)]
    adm = _u(db, "admin")
    tr = db.scalar(select(Transporter).where(Transporter.code == "OSHO"))
    rows = [db.get(Invoice, i.id) for i in invs]
    slip = ds.create_gatepass(db, adm, rows, ds.GatepassRequest(**local_auto(db), transporter_id=tr.id,
                                                                cases={i.id: i.boxes for i in rows}))
    walk_out(db, slip)
    db.commit()
    return slip, rows


def _alerts(db, slip_id):
    return list(db.scalars(select(Notification).where(Notification.slip_id == slip_id).order_by(Notification.id)))


def test_mobile_numbers_normalised():
    assert ns.normalise_mobile("98261 20001") == "919826120001"
    assert ns.normalise_mobile("+91-98261-20001") == "919826120001"
    assert ns.normalise_mobile("09826120001") == "919826120001"
    assert ns.normalise_mobile("0771-4012345") is None      # landline
    assert ns.normalise_mobile("") is None and ns.normalise_mobile(None) is None


def test_off_by_default_nothing_is_queued(api, db):
    slip, _ = _dispatch(api, db)
    assert _alerts(db, slip.id) == []
    assert ns.send_due(db) == {"sent": 0, "failed": 0, "retry": 0}


def test_gate_out_queues_one_message_per_customer_and_log_sends(api, db, provider):
    provider("LOG")
    slip, rows = _dispatch(api, db)
    alerts = _alerts(db, slip.id)
    assert len(alerts) == 2 and {a.customer_id for a in alerts} == {r.customer_id for r in rows}
    a = alerts[0]
    assert a.event == "GATE_OUT" and a.status == NotifyStatus.QUEUED and a.to_number.startswith("91")
    assert rows[0].invoice_no in a.body and "Osho Express" in a.body and slip.dispatch_no in a.body
    assert f"{rows[0].boxes} cases" in a.body
    res = ns.send_due(db)
    assert res["sent"] >= 2
    db.expire_all()
    assert all(x.status == NotifyStatus.SENT and x.provider_ref == "LOG" for x in _alerts(db, slip.id))
    assert db.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "NOTIFY_SENT",
                                                           AuditLog.record_id == str(a.id))) == 1
    # gate-out of the same gatepass cannot be repeated, and the queue never doubles a message
    assert ns.on_gate_out(db, None, slip, rows) == 0


def test_lr_message_once_per_lr_and_again_when_corrected(api, db, provider):
    provider("LOG")
    slip, rows = _dispatch(api, db)
    adm = _u(db, "admin")
    first = [r for r in rows if r.customer_id == rows[0].customer_id]
    ds.set_lr(db, adm, first, "OS-55001", None)
    db.commit()
    lr_alerts = [a for a in _alerts(db, slip.id) if a.event == "LR_ENTERED"]
    assert len(lr_alerts) == 1 and "OS-55001" in lr_alerts[0].body
    assert ns.on_lr(db, adm, first) == 0                       # same LR again: nothing new
    ds.set_lr(db, adm, first, "OS-55002", None, reason="Transporter corrected the bilty number")
    db.commit()
    lr_alerts = [a for a in _alerts(db, slip.id) if a.event == "LR_ENTERED"]
    assert [("OS-55001" in a.body, "OS-55002" in a.body) for a in lr_alerts] == [(True, False), (False, True)]


def test_events_can_be_switched_off_individually(api, db, provider):
    provider("LOG", **{"notify.on_gate_out": False})
    slip, rows = _dispatch(api, db)
    assert _alerts(db, slip.id) == []
    ds.set_lr(db, _u(db, "admin"), [rows[0]], "OS-56001", None)
    db.commit()
    assert [a.event for a in _alerts(db, slip.id)] == ["LR_ENTERED"]


def test_customer_without_mobile_is_failed_not_sent(api, db, provider):
    provider("LOG")
    cu = db.scalar(select(Customer).where(Customer.code == "C-VDP01"))
    old = (cu.whatsapp, cu.contact_phone)
    cu.whatsapp, cu.contact_phone = None, "0771-4000000"
    db.commit()
    try:
        slip, _ = _dispatch(api, db, customers=("C-VDP01",), boxes=(2,))
        a = _alerts(db, slip.id)[0]
        assert a.status == NotifyStatus.FAILED and a.to_number is None and "mobile" in a.last_error
        with pytest.raises(Exception, match="no valid mobile"):
            ns.retry(db, _u(db, "admin"), a)
        db.rollback()
    finally:
        cu = db.get(Customer, cu.id)
        cu.whatsapp, cu.contact_phone = old
        db.commit()


class _Resp:
    def __init__(self, code, body):
        self.status_code, self.text, self._b = code, str(body), body

    def json(self):
        return self._b


def test_http_sms_retries_then_fails_and_manual_retry(api, db, provider, monkeypatch):
    provider("HTTP_SMS", **{"notify.http_url": "https://sms.example.test/send", "notify.max_attempts": "2"})
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        return _Resp(503, "gateway busy")
    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    slip, _ = _dispatch(api, db, customers=("C-HRM01",), boxes=(2,))
    a = _alerts(db, slip.id)[0]
    assert a.channel == "SMS"
    ns.send_due(db)
    db.refresh(a)
    assert a.status == NotifyStatus.QUEUED and a.attempts == 1 and "503" in a.last_error and a.next_attempt_at
    assert {"to": a.to_number, "text": a.body, "sender": "CTDMS"} in calls
    a.next_attempt_at = None                                   # pretend the 5 minutes have passed
    db.commit()
    ns.send_due(db)
    db.refresh(a)
    assert a.status == NotifyStatus.FAILED and a.attempts == 2
    monkeypatch.setattr(httpx, "post", lambda url, json=None, headers=None, timeout=None: _Resp(200, "OK id=77"))
    ns.retry(db, _u(db, "operator.raipur"), a)
    db.commit()
    ns.send_due(db)
    db.refresh(a)
    assert a.status == NotifyStatus.SENT and a.provider_ref == "OK id=77"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "NOTIFY_RETRY", AuditLog.record_id == str(a.id)))


def test_whatsapp_cloud_payload(api, db, provider, monkeypatch):
    provider("WHATSAPP_CLOUD", **{"notify.whatsapp_phone_number_id": "1234567890"})
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "notify_token", "test-token")
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return _Resp(200, {"messages": [{"id": "wamid.ABC"}]})
    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    slip, _ = _dispatch(api, db, customers=("C-ABC01",), boxes=(4,))
    ns.send_due(db)
    a = _alerts(db, slip.id)[0]
    db.refresh(a)
    assert a.status == NotifyStatus.SENT and a.provider_ref == "wamid.ABC" and a.channel == "WHATSAPP"
    assert seen["url"].endswith("/1234567890/messages") and seen["headers"]["Authorization"] == "Bearer test-token"
    body = seen["json"]
    assert body["to"] == a.to_number and body["template"]["name"] == "dispatch_update"
    assert body["template"]["components"][0]["parameters"][0]["text"] == a.body


def test_whatsapp_without_setup_fails_clearly(api, db, provider):
    provider("WHATSAPP_CLOUD", **{"notify.max_attempts": "1"})
    slip, _ = _dispatch(api, db, customers=("C-ABC01",), boxes=(2,))
    ns.send_due(db)
    a = _alerts(db, slip.id)[0]
    db.refresh(a)
    assert a.status == NotifyStatus.FAILED and "not set up" in a.last_error


def test_screen_scope_and_retry_route(api, db, web, provider):
    provider("LOG")
    slip, _ = _dispatch(api, db, customers=("C-HRM01",), boxes=(2,))
    a = _alerts(db, slip.id)[0]
    w = web("operator.raipur")
    page = w.get("/notifications")
    assert page.status_code == 200 and slip.dispatch_no in page.text and "Trial mode" in page.text
    assert slip.dispatch_no not in web("operator.bilaspur").get("/notifications").text   # other CFA
    assert w.get(f"/gatepass/{slip.id}").text.count("Customer alerts") == 1
    r = w.post("/notifications/send-now", {})
    assert "Sent" in r.text
    db.refresh(a)
    assert a.status == NotifyStatus.SENT
    r = w.post(f"/notifications/{a.id}/retry", {})
    assert "already sent" in r.text
    assert web("operator.bilaspur").post(f"/notifications/{a.id}/retry", {}).status_code == 404


def test_off_screen_explains(web):
    assert "switched off" in web("admin").get("/notifications").text


def test_cli_notify_runs():
    r = subprocess.run([sys.executable, "-m", "app.cli", "notify"], cwd=ROOT, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0 and ("switched off" in r.stdout or "Sent" in r.stdout), r.stdout + r.stderr
