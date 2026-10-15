"""v1.5 - proof of delivery & tracking, e-way bill rule, e-invoice QR signature, case labels, e-mailed reports, SSO."""
from __future__ import annotations

import base64
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    CFA, AuditLog, Company, Customer, DispatchSlip, Invoice, ReportSubscription, Setting, Transporter, User,
)
from app.services import delivery_service as dsv
from app.services import dispatch_service as ds
from app.services import einvoice_service as einv
from app.services import notify_service as ns
from app.services import subscription_service as subs
from app.services.errors import BusinessError
from app.services.utils import local_today
from tests.helpers import get_inv, make_invoice, new_invoice_no


def _set(**values) -> None:
    from app.services import settings_service as cfg
    s = SessionLocal()
    try:
        for k, v in values.items():
            cfg.set_value(s, k, v)
        s.commit()
    finally:
        s.close()


def _u(db, name) -> User:
    return db.scalar(select(User).where(User.username == name))


def _dispatch(api, db, customers=("C-HRM01", "C-ABC01"), boxes=(3, 5), gate_out=True):
    invs = [get_inv(make_invoice(api("admin"), company="COA", cfa="RPR", customer=c, boxes=b)["id"])
            for c, b in zip(customers, boxes)]
    adm = _u(db, "admin")
    tr = db.scalar(select(Transporter).where(Transporter.code == "OSHO"))
    rows = [db.get(Invoice, i.id) for i in invs]
    slip = ds.create_gatepass(db, adm, rows, ds.GatepassRequest(transporter_id=tr.id,
                                                                cases={i.id: i.boxes for i in rows}))
    ds.print_gatepass(db, adm, slip, "PRINT_A5")
    if gate_out:
        ds.gate_out(db, adm, slip)
    db.commit()
    return slip, rows


# ----------------------------------------------------------------------------- proof of delivery
def test_pod_rules_timeline_and_pending_list(api, db):
    slip, (a, b) = _dispatch(api, db)
    op = _u(db, "operator.raipur")
    assert any(r["slip_id"] == slip.id for r in dsv.open_deliveries(db, op))
    steps = {s.key: s for s in dsv.timeline(db, [a])}
    assert steps["gate_out"].done and not steps["delivered"].done and not steps["lr"].done
    with pytest.raises(BusinessError, match="what was wrong"):
        dsv.record(db, op, [a], status="SHORT", delivered_on=local_today())
    with pytest.raises(BusinessError, match="future"):
        dsv.record(db, op, [a], status="DELIVERED", delivered_on=local_today() + timedelta(days=1))
    with pytest.raises(BusinessError, match="Not authorised"):
        dsv.record(db, _u(db, "viewer"), [a], status="DELIVERED", delivered_on=local_today())
    assert dsv.record(db, op, [a], status="SHORT", delivered_on=local_today(), received_by="Ramesh",
                      note="1 case short") == 1
    db.commit()
    inv = get_inv(a.id)
    assert inv.delivery_status == "SHORT" and inv.received_by == "Ramesh" and inv.pod_source == "STAFF"
    assert inv.transit_days == 0
    steps = {s.key: s for s in dsv.timeline(db, [db.get(Invoice, a.id)])}
    assert steps["delivered"].done and steps["delivered"].problem
    with pytest.raises(BusinessError, match="give a reason"):
        dsv.record(db, op, [db.get(Invoice, a.id)], status="DELIVERED", delivered_on=local_today())
    assert dsv.record(db, op, [db.get(Invoice, a.id)], status="DELIVERED", delivered_on=local_today(),
                      reason="Short case found at the transporter's office") == 1
    db.commit()
    assert not any(r["slip_id"] == slip.id and r["customer"].id == a.customer_id for r in dsv.open_deliveries(db, op))
    assert db.scalar(select(AuditLog).where(AuditLog.action == "DELIVERY_CHANGED", AuditLog.record_id == str(a.id)))


def test_pod_only_after_gate_out(api, db):
    slip, (a, _) = _dispatch(api, db, gate_out=False)
    with pytest.raises(BusinessError, match="not left the gate"):
        dsv.record(db, _u(db, "admin"), [a], status="DELIVERED", delivered_on=local_today())
    db.rollback()


def test_tracking_pages_bulk_delivery_and_pod_photo(api, web, db):
    slip, (a, b) = _dispatch(api, db)
    w = web("operator.raipur")
    page = w.get(f"/track?q={a.invoice_no}")
    assert page.status_code == 200 and "Shipment progress" in page.text and "Left our warehouse" in page.text
    r = w.post(f"/track/{a.id}/pod", {"status": "DAMAGED", "delivered_on": local_today().isoformat(),
                                      "note": "one case wet", "received_by": "Store"},
               files={"photo": ("pod.jpg", b"\xff\xd8\xff fake jpeg", "image/jpeg")})
    assert "Delivery recorded" in r.text
    inv = get_inv(a.id)
    assert inv.delivery_status == "DAMAGED" and inv.pod_file and inv.pod_file.endswith(".jpg")
    assert w.get(f"/track/{a.id}/pod-file").content.startswith(b"\xff\xd8")
    bad = w.post(f"/track/{a.id}/pod", {"status": "DELIVERED", "delivered_on": local_today().isoformat(),
                                        "reason": "fix"}, files={"photo": ("x.exe", b"MZ", "application/octet-stream")})
    assert "photo" in bad.text.lower()
    lst = w.get("/deliveries")
    assert lst.status_code == 200 and b.invoice_no in lst.text
    r = w.post("/deliveries", {"pick": str(b.id), "delivered_on": local_today().isoformat(), f"rcv_{b.id}": "Clerk"})
    assert "1 invoice(s) marked delivered" in r.text
    assert get_inv(b.id).delivery_status == "DELIVERED" and get_inv(b.id).received_by == "Clerk"
    assert web("viewer").post(f"/track/{a.id}/pod", {"status": "DELIVERED"}).status_code == 403


# ----------------------------------------------------------------------------- customer tracking link
def test_customer_tracking_link_and_confirmation(api, web, db):
    _set(**{"org.public_url": "https://track.example.com"})
    try:
        slip, (a, _) = _dispatch(api, db)
        url = dsv.track_url(db, slip, a.customer_id)
        assert url.startswith("https://track.example.com/t/")
        token = url.rsplit("/", 1)[1]
        # the link is in the WhatsApp / SMS text
        text = ns.render(db, "GATE_OUT", db.get(DispatchSlip, slip.id), [db.get(Invoice, a.id)])
        assert url in text
        from fastapi.testclient import TestClient  # a browser without a login
        from app.main import app
        c = TestClient(app)
        page = c.get(f"/t/{token}")
        assert page.status_code == 200 and a.invoice_no in page.text and "Received the goods?" in page.text
        assert "Freight" not in page.text and "₹" not in page.text          # no prices for the customer
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        r = c.post(f"/t/{token}", data={"csrf_token": csrf, "status": "DELIVERED",
                                        "delivered_on": local_today().isoformat(), "received_by": "Owner"})
        assert r.status_code == 200 and "Thank you" in r.text
        inv = get_inv(a.id)
        assert inv.delivery_status == "DELIVERED" and inv.pod_source == "CUSTOMER" and inv.received_by == "Owner"
        # staff can still correct it; afterwards the customer cannot overwrite staff's record
        op = _u(db, "operator.raipur")
        db.expire_all()
        dsv.record(db, op, [db.get(Invoice, a.id)], status="SHORT", delivered_on=local_today(), note="2 short")
        db.commit()
        page = c.get(f"/t/{token}")
        assert "Received the goods?" not in page.text and "Delivered - short" in page.text
        assert c.get("/t/not-a-real-token").status_code == 404
        assert c.get(f"/t/{token[:-3]}abc").status_code == 404                # tampered
    finally:
        _set(**{"org.public_url": ""})


# ----------------------------------------------------------------------------- e-way bill
def test_eway_bill_required_above_limit_blocks_gate_out(api, web, db):
    _set(**{"gate.eway_required_above": "50000"})
    try:
        slip, (big, small) = _dispatch(api, db, boxes=(2, 2), gate_out=False)
        db.get(Invoice, big.id).sales_value = Decimal("95000")
        db.get(Invoice, small.id).sales_value = Decimal("12000")
        db.commit()
        adm = _u(db, "admin")
        with pytest.raises(BusinessError, match="E-way bill number missing for invoice " + big.invoice_no):
            ds.gate_out(db, adm, db.get(DispatchSlip, slip.id))
        db.rollback()
        w = web("operator.raipur")
        r = w.post(f"/track/{big.id}/eway", {f"eway_{big.id}": "1234"})
        assert "12 digits" in r.text
        r = w.post(f"/track/{big.id}/eway", {f"eway_{big.id}": "3410 2345 6789",
                                             f"ewayvalid_{big.id}": (local_today() - timedelta(days=1)).isoformat()})
        assert "E-way bill saved" in r.text and get_inv(big.id).eway_bill_no == "341023456789"
        db.expire_all()
        with pytest.raises(BusinessError, match="expired"):
            ds.gate_out(db, adm, db.get(DispatchSlip, slip.id))
        db.rollback()
        w.post(f"/track/{big.id}/eway", {f"eway_{big.id}": "341023456789",
                                         f"ewayvalid_{big.id}": (local_today() + timedelta(days=1)).isoformat()})
        db.expire_all()
        assert ds.gate_out(db, adm, db.get(DispatchSlip, slip.id)) == 2
        db.commit()
        other = _dispatch(api, db, customers=("C-ABC01",), boxes=(1,), gate_out=False)[1][0]
        r = w.post(f"/track/{other.id}/eway", {f"eway_{other.id}": "341023456789"})
        assert "already on invoice" in r.text
        assert "341023456789" in w.get(f"/track?q=341023456789", follow_redirects=True).text
    finally:
        _set(**{"gate.eway_required_above": "0"})


# ----------------------------------------------------------------------------- QR signature
@pytest.fixture()
def irp_key(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    path = tmp_path / "irp.pem"
    path.write_bytes(pem)
    monkeypatch.setattr(get_settings(), "irp_public_key_file", path)
    einv._key_cache.clear()
    yield key
    einv._key_cache.clear()


def _signed_qr(key, seller, buyer, no, value):
    import jwt
    data = {"SellerGstin": seller, "BuyerGstin": buyer, "DocNo": no, "DocTyp": "INV",
            "DocDt": local_today().strftime("%d/%m/%Y"), "TotInvVal": value, "ItemCnt": 2, "MainHsnCode": "3004",
            "Irn": ("a" * 40 + no.lower().replace("-", ""))[-64:].rjust(64, "0"), "IrnDt": "2026-10-15 10:00:00"}
    return jwt.encode({"data": json.dumps(data), "iss": "NIC"}, key, algorithm="RS256")


def _tamper(token: str) -> str:
    h, p, sig = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    d = json.loads(payload["data"])
    d["TotInvVal"] = 1.0
    payload["data"] = json.dumps(d)
    p2 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return ".".join([h, p2, sig])


def test_qr_signature_verified_flagged_or_blocked(db, irp_key):
    op = _u(db, "operator.raipur")
    co = db.scalar(select(Company).where(Company.code == "COC"))
    cu = db.scalar(select(Customer).where(Customer.code == "C-SBV01"))
    rpr = db.scalar(select(CFA).where(CFA.code == "RPR"))
    good = _signed_qr(irp_key, co.gstin, cu.gstin, new_invoice_no("SIG"), 5000.0)
    assert einv.signature_status(good) == "VERIFIED"
    assert einv.signature_status(_tamper(good)) == "INVALID"
    assert einv.signature_status(einv.make_demo_qr(co.gstin, cu.gstin, "X1", local_today(), 1)) == "INVALID"
    res = einv.scan(db, op, good, cfa_id=rpr.id, cases=2)
    assert res.created and res.invoice.qr_signature == "VERIFIED"
    db.rollback()
    _set(**{"einvoice.signature_check": "WARN"})
    fake = _tamper(_signed_qr(irp_key, co.gstin, cu.gstin, new_invoice_no("FAKE"), 5000.0))
    res = einv.scan(db, op, fake, cfa_id=rpr.id, cases=2)
    assert res.created and res.invoice.qr_signature == "INVALID" and "NOT valid" in res.message
    db.rollback()
    _set(**{"einvoice.signature_check": "BLOCK"})
    try:
        fake = _tamper(_signed_qr(irp_key, co.gstin, cu.gstin, new_invoice_no("FAKE"), 5000.0))
        res = einv.scan(db, op, fake, cfa_id=rpr.id, cases=2)
        assert res.invoice is None and "NOT signed by the GST portal" in res.message
        assert db.scalar(select(AuditLog).where(AuditLog.action == "QR_SIGNATURE_INVALID").order_by(AuditLog.id.desc()))
    finally:
        db.rollback()
        _set(**{"einvoice.signature_check": "WARN"})


def test_qr_without_key_is_unverified(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "irp_public_key_file", None)
    co = db.scalar(select(Company).where(Company.code == "COC"))
    cu = db.scalar(select(Customer).where(Customer.code == "C-SBV01"))
    assert einv.signature_status(einv.make_demo_qr(co.gstin, cu.gstin, "X1", local_today(), 1)) == "UNVERIFIED"


# ----------------------------------------------------------------------------- case labels
def test_case_labels_one_per_case(api, web, db):
    slip, (a, b) = _dispatch(api, db, boxes=(3, 2), gate_out=False)
    r = web("operator.raipur").get(f"/gatepass/{slip.id}/labels")
    assert r.status_code == 200 and r.text.count('class="label"') == 5
    assert "1 / 3" in r.text and "3 / 3" in r.text and "2 / 2" in r.text and "<svg" in r.text
    assert "10 per page" in web("operator.raipur").get(f"/gatepass/{slip.id}/labels?sheet=a4").text


# ----------------------------------------------------------------------------- reports & API
@pytest.mark.parametrize("code", ["delivery-performance", "delivery-register"])
def test_delivery_reports(web, code):
    w = web("management")
    r = w.get(f"/reports/{code}?date_from=2000-01-01")
    assert r.status_code == 200 and "TOTAL" in r.text
    assert w.get(f"/reports/{code}/export?date_from=2000-01-01&fmt=xlsx").status_code == 200


def test_delivery_performance_counts(api, db):
    slip, (a, b) = _dispatch(api, db)
    adm = _u(db, "admin")
    dsv.record(db, adm, [a], status="DELIVERED", delivered_on=local_today())
    db.commit()
    from app.services.reports import Filters, run_report
    res = run_report(db, adm, "delivery-performance", Filters(date_from=local_today(), date_to=local_today()))
    t = res.totals
    assert t["consignments"] >= 2 and t["delivered"] >= 1 and t["open"] >= 1
    assert t["delivered"] + t["open"] == t["consignments"]


def test_api_track_and_delivery(api, db):
    slip, (a, _) = _dispatch(api, db)
    c = api("operator.raipur")
    r = c.get("/api/v1/track", params={"q": a.invoice_no})
    assert r.status_code == 200
    con = r.json()["consignments"][0]
    assert con["stage"] == "In transit" and any(m["key"] == "gate_out" and m["done"] for m in con["milestones"])
    r = c.post(f"/api/v1/invoices/{a.id}/delivery", json={"status": "delivered",
                                                          "delivered_on": local_today().isoformat(),
                                                          "received_by": "API"})
    assert r.status_code == 200 and r.json()["updated"][0]["delivery"]["status"] == "DELIVERED"
    r = c.post(f"/api/v1/invoices/{a.id}/delivery", json={"status": "REFUSED", "delivered_on": local_today().isoformat()})
    assert r.status_code == 400 and "what was wrong" in r.json()["detail"]


# ----------------------------------------------------------------------------- e-mailed reports
def test_subscription_schedule():
    d = date(2026, 10, 15)  # a Thursday
    assert subs.period("DAILY", d) == (date(2026, 10, 14), date(2026, 10, 14))
    assert subs.period("WEEKLY", d) == (date(2026, 10, 8), date(2026, 10, 14))
    assert subs.period("MONTHLY", d) == (date(2026, 9, 1), date(2026, 9, 30))
    from app.services.utils import to_local
    assert to_local(subs.next_run("DAILY", datetime(2026, 10, 15, 6, 0))) == datetime(2026, 10, 15, 7, 0)
    assert to_local(subs.next_run("DAILY", datetime(2026, 10, 15, 7, 0))) == datetime(2026, 10, 16, 7, 0)
    assert to_local(subs.next_run("WEEKLY", datetime(2026, 10, 15, 9, 0))) == datetime(2026, 10, 19, 7, 0)
    assert to_local(subs.next_run("MONTHLY", datetime(2026, 10, 15, 9, 0))) == datetime(2026, 11, 1, 7, 0)
    assert to_local(subs.next_run("MONTHLY", datetime(2026, 12, 1, 6, 0))) == datetime(2026, 12, 1, 7, 0)
    with pytest.raises(BusinessError, match="not an e-mail"):
        subs.parse_recipients("md@example.com, nonsense")
    assert subs.parse_recipients("a@x.in; A@x.in b@y.com") == ["a@x.in", "b@y.com"]


def test_subscription_created_sent_once_and_scoped(web, db, monkeypatch):
    sent = []
    monkeypatch.setattr(subs, "send_mail", lambda to, subject, text, att: sent.append((to, subject, att)))
    monkeypatch.setattr(subs, "smtp_ready", lambda: True)
    w = web("management")
    page = w.get("/report-subscriptions")
    assert page.status_code == 200 and "E-mailed reports" in page.text
    r = w.post("/report-subscriptions", {"report_code": "transporter", "frequency": "WEEKLY", "fmt": "xlsx",
                                         "recipients": "md@example.com, accounts@example.com"})
    assert "will be e-mailed weekly" in r.text
    sub = db.scalar(select(ReportSubscription).order_by(ReportSubscription.id.desc()))
    assert sub.owner.username == "management" and sub.next_run_at is not None
    r = w.post(f"/report-subscriptions/{sub.id}/send", {})
    assert "Sent to md@example.com" in r.text
    to, subject, att = sent[-1]
    assert to == ["md@example.com", "accounts@example.com"] and "Transporter" in subject
    assert att[0][0].endswith(".xlsx") and att[0][1][:2] == b"PK"
    # due -> sent exactly once even when two workers look at the same moment
    s = SessionLocal()
    try:
        s.get(ReportSubscription, sub.id).next_run_at = datetime(2000, 1, 1)
        s.commit()
        n = len(sent)
        assert subs.send_due(s)["sent"] >= 1 and subs.send_due(s)["sent"] == 0
        assert len(sent) == n + 1 and s.get(ReportSubscription, sub.id).next_run_at > datetime(2026, 1, 1)
    finally:
        s.close()
    bad = w.post("/report-subscriptions", {"report_code": "transporter", "frequency": "DAILY", "recipients": "x"})
    assert "not an e-mail address" in bad.text
    assert web("viewer").post(f"/report-subscriptions/{sub.id}/delete", {}).status_code in (403, 404)
    sid = sub.id
    w.post(f"/report-subscriptions/{sid}/delete", {})
    db.expunge_all()
    assert db.scalar(select(ReportSubscription.id).where(ReportSubscription.id == sid)) is None


def test_subscription_fails_cleanly_without_smtp(web, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_host", None)
    w = web("management")
    w.post("/report-subscriptions", {"report_code": "gatepass-register", "frequency": "DAILY",
                                     "recipients": "md@example.com"})
    sub = db.scalar(select(ReportSubscription).order_by(ReportSubscription.id.desc()))
    r = w.post(f"/report-subscriptions/{sub.id}/send", {})
    assert "Not sent" in r.text and "CTDMS_SMTP_HOST" in r.text
    db.expire_all()
    assert "CTDMS_SMTP_HOST" in db.get(ReportSubscription, sub.id).last_error
