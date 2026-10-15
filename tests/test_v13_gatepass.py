"""v1.3 - management feedback: QR capture without ERP access, one gatepass per pickup with many invoices, LR later,
gate-out, transporter rate cards, scanned bills cross-checked with an irregularity report and a rate review."""
from __future__ import annotations

import csv
import io
import re
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import (
    CFA, AuditLog, Company, Customer, DispatchMode, DispatchSlip, Invoice, InvoiceItem, InvoiceStatus, Location,
    OutsideAuto, PickupMode, RateStatus, Transporter, TransporterBill, TransporterRate, User,
)
from app.services import dispatch_service as ds
from app.services import einvoice_service as einv
from app.services import import_service as imp
from app.services import recon_service as rs
from app.services import transporter_rate_service as trs
from app.services.errors import BusinessError
from app.services.utils import local_today
from tests.helpers import get_inv, make_invoice, new_invoice_no


def _u(db, name) -> User:
    return db.scalar(select(User).where(User.username == name))


def _tr(db, code) -> Transporter:
    return db.scalar(select(Transporter).where(Transporter.code == code))


def _cu(db, code) -> Customer:
    return db.scalar(select(Customer).where(Customer.code == code))


def _fresh(api, customer, boxes=4, company="COA"):
    return get_inv(make_invoice(api("admin"), company=company, cfa="RPR", customer=customer, boxes=boxes)["id"])


# ----------------------------------------------------------------------------- 1 capture without ERP access
def test_einvoice_qr_creates_invoice_once_and_maps_gstins(db):
    op = _u(db, "operator.raipur")
    co = db.scalar(select(Company).where(Company.code == "COC"))
    cu = _cu(db, "C-SBV01")
    no = new_invoice_no("QR")
    qr = einv.make_demo_qr(co.gstin, cu.gstin, no, local_today(), Decimal("12345.60"), item_count=4)
    d = einv.decode(qr)
    assert d.doc_no == no and d.value == Decimal("12345.60") and d.item_count == 4 and len(d.irn) == 64
    rpr = db.scalar(select(CFA).where(CFA.code == "RPR"))
    res = einv.scan(db, op, qr, cfa_id=rpr.id, cases=5)
    db.commit()
    inv = res.invoice
    assert res.created and inv.company_id == co.id and inv.customer_id == cu.id and inv.boxes == 5
    assert inv.capture_mode == "QR_SCAN" and inv.location_id == cu.location_id and inv.status == "PENDING"
    again = einv.scan(db, op, qr, cfa_id=rpr.id)
    assert not again.created and again.invoice.id == inv.id          # same QR scanned twice -> no duplicate
    by_no = einv.scan(db, op, no, cfa_id=rpr.id)                     # typed / 1D barcode invoice number
    assert by_no.invoice.id == inv.id
    db.rollback()


def test_einvoice_qr_unknown_customer_asks_for_master(db):
    op = _u(db, "operator.raipur")
    co = db.scalar(select(Company).where(Company.code == "COA"))
    qr = einv.make_demo_qr(co.gstin, "22ZZZZZ9999Z1Z9", new_invoice_no("QR"), local_today(), 1000)
    res = einv.scan(db, op, qr)
    assert res.invoice is None and res.missing == "customer" and "22ZZZZZ9999Z1Z9" in res.message
    db.rollback()


def test_gatepass_scan_endpoint(web, db):
    w = web("operator.raipur")
    co = db.scalar(select(Company).where(Company.code == "COA"))
    qr = einv.make_demo_qr(co.gstin, _cu(db, "C-HRM01").gstin, new_invoice_no("QR"), local_today(), 5500)
    r = w.c.post("/gatepass/scan", data={"code": qr, "cases": "3"}, headers={"X-CSRF-Token": w.token})
    j = r.json()
    assert j["ok"] and j["created"] and j["invoice"]["cases"] == 3 and j["invoice"]["type"] == "Retailer"
    r = w.c.post("/gatepass/scan", data={"code": "NO-SUCH-INVOICE"}, headers={"X-CSRF-Token": w.token})
    assert r.json()["ok"] is False


# ----------------------------------------------------------------------------- 2 one gatepass, many invoices
def test_gatepass_many_invoices_priced_from_rate_card_print_then_gate_out(api, web, db):
    a = _fresh(api, "C-HRM01", boxes=4)     # Bilaspur: Osho Express card ₹25
    b = _fresh(api, "C-ABC01", boxes=6)     # Bilaspur, another customer
    osho = _tr(db, "OSHO")
    w = web("operator.raipur")
    r = w.post("/gatepass/new", {"ids": [str(a.id), str(b.id)], f"cases_{a.id}": "4", f"cases_{b.id}": "6",
                                 "mode": "TRANSPORTER", "transporter_id": str(osho.id), "pickup_mode": "REGULAR",
                                 "dispatch_date": local_today().isoformat()})
    assert r.status_code == 200 and "Gatepass DS/RPR/" in r.text
    ia, ib = get_inv(a.id), get_inv(b.id)
    assert ia.dispatch_no == ib.dispatch_no and ia.status == ib.status == "SLIP_GENERATED"
    assert ia.rate_per_box == Decimal("25.00") and ia.freight_amount == Decimal("100.00")
    assert ib.freight_amount == Decimal("150.00") and ia.rate_level == "TRANSPORTER"
    slip = db.get(DispatchSlip, ia.dispatch_slip_id)
    assert slip.invoice_count == 2 and slip.total_cases == 10 and slip.total_charge == Decimal("250.00")
    assert slip.payment_status == "TBB" and slip.amount_paid == 0
    assert len(slip.snapshot["groups"]) == 2 and slip.snapshot["transporter"]["name"] == osho.name
    assert "vehicle_no" not in slip.snapshot   # vehicle number is not on the gatepass any more
    # gate-out refused before printing
    r = w.post(f"/gatepass/{slip.id}/gate-out", {})
    assert "Gate-out refused" in r.text and get_inv(a.id).status == "SLIP_GENERATED"
    r = w.post(f"/gatepass/{slip.id}/print", {"size": "A5", "mode": "print"})
    assert r.status_code == 200 and slip.dispatch_no in r.text and "<svg" in r.text   # barcode for the gate
    r = w.post(f"/gatepass/{slip.id}/print", {"size": "A4", "mode": "pdf", "reason": "Second copy for driver"})
    assert r.content[:4] == b"%PDF"
    assert get_inv(a.id).status == "SLIP_PRINTED"
    page = w.get(f"/gatepass/gate?code={slip.dispatch_no}")
    assert "Cleared" in page.text
    w.post(f"/gatepass/{slip.id}/gate-out", {"back": "gate"})
    assert get_inv(a.id).status == get_inv(b.id).status == "DISPATCHED"
    db.expire_all()
    assert db.get(DispatchSlip, slip.id).gate_out_at is not None
    assert "Already went out" in w.get(f"/gatepass/gate?code={slip.dispatch_no}").text


def test_gatepass_blocked_without_rate_card_entry(api, db):
    inv = _fresh(api, "C-PQR01", boxes=5)   # Nagpur - Osho has no Nagpur rate
    op = _u(db, "operator.raipur")
    with pytest.raises(BusinessError, match="No approved rate.*Nagpur"):
        ds.create_gatepass(db, op, [inv], ds.GatepassRequest(transporter_id=_tr(db, "OSHO").id,
                                                             cases={inv.id: 5}))
    db.rollback()


def test_gatepass_rules_cases_cfa_and_operator_box_change(api, db):
    op = _u(db, "operator.raipur")
    a = _fresh(api, "C-HRM01", boxes=4)
    req = ds.GatepassRequest(transporter_id=_tr(db, "OSHO").id, cases={a.id: 9})
    with pytest.raises(BusinessError, match="invoice says 4"):   # operators cannot change an ERP box count here
        ds.create_gatepass(db, op, [a], req)
    db.rollback()
    adm = _u(db, "admin")                                       # holds override.direct: may correct the count
    slip = ds.create_gatepass(db, adm, [db.get(Invoice, a.id)],
                              ds.GatepassRequest(transporter_id=_tr(db, "OSHO").id, cases={a.id: 5}))
    assert db.get(Invoice, a.id).boxes == 5 and slip.total_cases == 5
    db.flush()
    assert db.scalar(select(AuditLog).where(AuditLog.record_id == str(a.id), AuditLog.action == "OVERRIDE_APPLIED"))
    db.rollback()
    b = _fresh(api, "C-HRM01", boxes=3)
    rgh = _fresh(api, "C-DHR01", boxes=3)
    rgh.cfa_id = db.scalar(select(CFA).where(CFA.code == "BSP")).id   # pretend: other warehouse
    _, _, problems = ds.plan_gatepass(db, adm, [db.get(Invoice, b.id), rgh],
                                      ds.GatepassRequest(transporter_id=_tr(db, "OSHO").id))
    assert any("same CFA" in p for p in problems)
    db.rollback()


def test_outside_auto_per_trip_split_and_cash_paid(api, db):
    adm = _u(db, "admin")
    auto = db.scalar(select(OutsideAuto).where(OutsideAuto.code == "AUTO-RAM"))   # ₹250 per trip
    invs = [_fresh(api, "C-MET01", boxes=b) for b in (1, 2, 4)]
    slip = ds.create_gatepass(db, adm, [db.get(Invoice, i.id) for i in invs], ds.GatepassRequest(
        mode=DispatchMode.OUTSIDE_AUTO, outside_auto_id=auto.id, cases={i.id: i.boxes for i in invs}))
    shares = [ln.freight for ln in slip.lines]
    assert sum(shares) == Decimal("250.00") and shares[2] > shares[0]
    assert slip.amount_paid == Decimal("250.00") and slip.payment_status == "PAID" and slip.transporter_id is None
    db.rollback()


def test_transporter_with_auto_pickup_and_self_pickup(api, db):
    adm = _u(db, "admin")
    sonu = db.scalar(select(OutsideAuto).where(OutsideAuto.code == "AUTO-SONU"))  # ₹12 per case
    inv = _fresh(api, "C-HRM01", boxes=10)
    me = _fresh(api, "C-CCC01", boxes=3)    # create through the API before this session starts writing
    slip = ds.create_gatepass(db, adm, [db.get(Invoice, inv.id)], ds.GatepassRequest(
        transporter_id=_tr(db, "OSHO").id, pickup_mode=PickupMode.OUTSIDE_AUTO, outside_auto_id=sonu.id,
        cases={inv.id: 10}))
    assert slip.freight_total == Decimal("250.00") and slip.auto_charge == Decimal("120.00")
    assert slip.total_charge == Decimal("370.00") and slip.amount_paid == Decimal("120.00")
    assert slip.payment_status == "PARTIAL" and slip.balance == Decimal("250.00")
    s2 =ds.create_gatepass(db, adm, [db.get(Invoice, me.id)], ds.GatepassRequest(mode=DispatchMode.SELF,
                                                                                  cases={me.id: 3}))
    assert s2.total_charge == 0 and s2.payment_status == "NIL"
    db.rollback()


def test_lr_entered_after_dispatch_conflicts_and_lock(api, web, db):
    a = _fresh(api, "C-HRM01", boxes=2)
    adm = _u(db, "admin")
    slip = ds.create_gatepass(db, adm, [db.get(Invoice, a.id)], ds.GatepassRequest(
        transporter_id=_tr(db, "OSHO").id, cases={a.id: 2}))
    ds.print_gatepass(db, adm, slip, "PRINT_A5")
    ds.gate_out(db, adm, slip)
    db.commit()
    w = web("operator.raipur")
    cid = get_inv(a.id).customer_id
    w.post(f"/gatepass/{slip.id}/lr", {f"lr_{cid}": "os-9001", f"lrdate_{cid}": local_today().isoformat()})
    assert get_inv(a.id).lr_no == "OS-9001" and get_inv(a.id).lr_updated_by_id is not None
    other = db.scalar(select(Invoice).where(Invoice.transporter_id == _tr(db, "OSHO").id, Invoice.lr_no.isnot(None),
                                            Invoice.customer_id != cid))
    if other:
        with pytest.raises(BusinessError, match="already used"):
            ds.set_lr(db, adm, [db.get(Invoice, a.id)], other.lr_no, None, reason="typo test")
        db.rollback()
    with pytest.raises(BusinessError, match="give a reason"):
        ds.set_lr(db, adm, [db.get(Invoice, a.id)], "OS-9002", None)
    db.rollback()
    assert db.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "LR_ENTERED",
                                                           AuditLog.record_id == str(a.id))) == 1


def test_void_gatepass_and_cancel_rules(api, db):
    adm = _u(db, "admin")
    a, b = _fresh(api, "C-HRM01", boxes=2), _fresh(api, "C-ABC01", boxes=3)
    slip = ds.create_gatepass(db, adm, [db.get(Invoice, a.id), db.get(Invoice, b.id)], ds.GatepassRequest(
        transporter_id=_tr(db, "OSHO").id, cases={a.id: 2, b.id: 3}))
    with pytest.raises(BusinessError, match="Void the gatepass first"):
        ds.cancel(db, adm, db.get(Invoice, a.id), "Order cancelled by customer")
    n = ds.void_gatepass(db, adm, slip, "Wrong transporter chosen")
    assert n == 2 and db.get(Invoice, a.id).status in ("PENDING", "VERIFIED")
    assert db.get(Invoice, a.id).freight_amount is None and db.get(Invoice, a.id).dispatch_no is None
    with pytest.raises(BusinessError, match="VOID"):
        ds.gate_out(db, adm, slip)
    db.rollback()


# ----------------------------------------------------------------------------- 3 rate cards
def test_rate_card_versioning_and_maker_checker(db):
    maker, chk = _u(db, "admin"), _u(db, "checker")
    tr = _tr(db, "JBG")
    lo = db.scalar(select(Location).where(Location.code == "KWD"))
    start = local_today() + timedelta(days=5)
    r = trs.propose(db, maker, transporter_id=tr.id, location_id=lo.id, rate_per_case="45", effective_from=start,
                    reason="Diesel increase letter")
    assert r.status == RateStatus.PENDING_APPROVAL and r.previous_rate_id is not None
    with pytest.raises(BusinessError, match="Maker-checker"):
        trs.approve(db, maker, r)
    with pytest.raises(BusinessError, match="not authorised"):
        trs.approve(db, _u(db, "cfa.raipur"), r)   # CFA manager may propose, not approve
    trs.approve(db, chk, r)
    old = db.get(TransporterRate, r.previous_rate_id)
    assert old.effective_to == start - timedelta(days=1)
    assert trs.find(db, tr.id, lo.id, start).rate_per_case == Decimal("45.00")
    assert trs.find(db, tr.id, lo.id, local_today()).rate_per_case == Decimal("40.00")
    db.rollback()


def test_rate_card_pages(web):
    w = web("cfa.raipur")
    assert "Transporter rate cards" in w.get("/rate-cards").text
    db_page = w.get("/rate-cards/1")
    assert db_page.status_code == 200


# ----------------------------------------------------------------------------- 4 bills: scan, cross-check, review
def test_seeded_scanned_bills_show_irregularities(db):
    orl = db.scalar(select(TransporterBill).where(TransporterBill.bill_no == "79"))
    codes = {i["code"] for ln in orl.lines for i in (ln.issues or [])}
    assert {"RATE_DIFF", "DUPLICATE_BILLING", "NOT_FOUND", "CASES_MORE", "ARITHMETIC"} <= codes
    assert any(ln.suggestions for ln in orl.lines)                      # unknown LR -> our likely consignment
    jbg = db.scalar(select(TransporterBill).where(TransporterBill.bill_no == "2615"))
    assert jbg.lines and all(ln.match_status == "PAID_IN_CASH" for ln in jbg.lines)
    with pytest.raises(BusinessError, match="cannot be paid"):
        rs.approve(db, _u(db, "checker"), jbg, "pay it")
    review = rs.rate_review(db, db.scalar(select(TransporterBill).where(TransporterBill.bill_no == "336")))
    assert review and review[0]["differs"] and review[0]["most"] == Decimal("28.00")
    db.rollback()


def test_link_suggestion_fills_our_lr(db):
    orl = db.scalar(select(TransporterBill).where(TransporterBill.bill_no == "79"))
    ln = next(x for x in orl.lines if x.suggestions)
    lr = ln.lr_no
    rs.link_suggestion(db, _u(db, "accounts"), orl, ln.id, 0)
    assert ln.match_status not in ("NOT_FOUND",) and ln.invoice_ids
    assert all(db.get(Invoice, i).lr_no == lr for i in ln.invoice_ids)
    assert db.scalar(select(AuditLog).where(AuditLog.action == "LR_ENTERED", AuditLog.reason.like("%bill 79%")))
    db.rollback()


def test_bill_lines_editor_and_checks(db):
    acc = _u(db, "accounts")
    tr = _tr(db, "PRK")
    grp = rs.consignments(list(db.scalars(select(Invoice).where(
        Invoice.transporter_id == tr.id, Invoice.status == "DISPATCHED", Invoice.lr_no.isnot(None),
        Invoice.transporter_bill_id.is_(None), Invoice.dispatch_mode == "TRANSPORTER"))))[0]
    cases = sum(i.boxes for i in grp)
    bill = rs.create_bill(db, acc, transporter_id=tr.id, bill_no="T13-PRK", bill_date=local_today(), bill_amount="0",
                          source="SCAN")
    rs.save_lines(db, acc, bill, [
        {"lr_no": grp[0].lr_no, "billed_boxes": str(cases), "billed_rate": "35", "billed_amount": str(35 * cases),
         "station": grp[0].location.name, "party_name": grp[0].customer.name},
        {"lr_no": grp[0].lr_no, "billed_boxes": str(cases), "billed_rate": "35", "billed_amount": str(35 * cases)},
        {"lr_no": "NOPE-1", "billed_boxes": "4", "billed_rate": "35", "billed_amount": "150", "station": "Durg"},
    ])
    first, dup, unknown = bill.lines
    assert first.match_status == "MATCHED" and first.system_cases == cases and first.card_rate == Decimal("35.00")
    assert dup.match_status == "DUPLICATE_BILLING"
    assert unknown.match_status == "NOT_FOUND" and "ARITHMETIC" in {i["code"] for i in unknown.issues}  # 4 x 35 != 150
    rs.save_lines(db, acc, bill, [{"id": str(unknown.id), "billed_amount": "140"}])
    assert not any(i["code"] == "ARITHMETIC" for i in unknown.issues)
    rep = rs.report(db, bill)
    assert len(rep["problem_lines"]) == 2
    db.rollback()


def test_rate_review_proposes_rate_change_for_approval(db):
    osho = db.scalar(select(TransporterBill).where(TransporterBill.bill_no == "336"))
    acc = _u(db, "accounts")
    review = rs.rate_review(db, osho)
    loc = review[0]["location"]
    n = rs.apply_rate_review(db, acc, osho, [{"location_id": loc.id, "rate": "28.00",
                                              "effective_from": osho.period_from.isoformat()}], "Osho letter")
    assert n == 1 and osho.rates_reviewed_at is not None
    pending = db.scalar(select(TransporterRate).where(TransporterRate.source_bill_id == osho.id))
    assert pending.status == RateStatus.PENDING_APPROVAL and pending.rate_per_case == Decimal("28.00")
    db.rollback()


def test_bill_scan_upload_without_ai_key_creates_bill_for_typing(web, db, monkeypatch):
    monkeypatch.delenv("CTDMS_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, format="PNG")
    w = web("accounts")
    tr = _tr(db, "BJR")
    r = w.post("/recon/scan", {"transporter_id": str(tr.id), "bill_no": "BJR-T13", "bill_date": local_today().isoformat()},
               files={"files": ("bill.png", buf.getvalue(), "image/png")})
    assert r.status_code == 200 and "Type the bill lines" in r.text
    bill = db.scalar(select(TransporterBill).where(TransporterBill.bill_no == "BJR-T13"))
    assert bill.source == "SCAN" and bill.scan_files and not bill.lines
    assert w.get(f"/recon/{bill.id}/file/0").headers["content-type"] == "image/png"
    r = w.post("/recon/scan", {"transporter_id": str(tr.id), "bill_no": "BJR-T13"},
               files={"files": ("bill.png", buf.getvalue(), "image/png")})
    assert "duplicate bill" in r.text


def test_bill_scan_with_reader_creates_lines(web, db, monkeypatch):
    from app.services import bill_scan_service as svc
    tr = _tr(db, "ORL")
    inv = db.scalar(select(Invoice).where(Invoice.transporter_id == tr.id, Invoice.lr_no.isnot(None),
                                          Invoice.status == "DISPATCHED"))
    fake = svc.ScannedBill(transporter_name="Orient Logistics Service", bill_no="ORL-AI-1", bill_date="01-10-2026",
                           total_amount=500.0, lines=[svc.ScannedLine(lr_no=inv.lr_no, cases=inv.boxes, rate=38,
                                                                      amount=float(inv.freight_amount)),
                                                      svc.ScannedLine(lr_no="X-1", cases=2, amount=100, unclear=True)])
    monkeypatch.setattr(svc, "available", lambda: True)
    monkeypatch.setattr(svc, "read_bill", lambda files, hint=None: fake)
    w = web("accounts")
    r = w.post("/recon/scan", {"transporter_id": str(tr.id)}, files={"files": ("p1.jpg", b"\xff\xd8fake", "image/jpeg")})
    assert "2 line(s) read" in r.text
    bill = db.scalar(select(TransporterBill).where(TransporterBill.bill_no == "ORL-AI-1"))
    assert len(bill.lines) == 2 and bill.lines[1].remarks.startswith("[unclear]")
    assert bill.extraction["bill_no"] == "ORL-AI-1"
    assert w.get(f"/recon/{bill.id}").status_code == 200 and w.get(f"/recon/{bill.id}/report").status_code == 200
    x = w.get(f"/recon/{bill.id}/report?export=xlsx")
    assert x.headers["content-type"].startswith("application/vnd.openxml")


# ----------------------------------------------------------------------------- 5 imports without re-typing
def test_item_wise_export_groups_rows_and_adds_skus_to_scanned_invoice(db):
    adm = _u(db, "admin")
    rpr = db.scalar(select(CFA).where(CFA.code == "RPR"))
    co = db.scalar(select(Company).where(Company.code == "COA"))
    cu = _cu(db, "C-UTP01")
    scanned_no = new_invoice_no("QRI")
    res = einv.scan(db, adm, einv.make_demo_qr(co.gstin, cu.gstin, scanned_no, local_today(), 3000), cfa_id=rpr.id,
                    cases=2)
    assert res.created and not res.invoice.items
    new_no = new_invoice_no("ITM")
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["Bill No", "Bill Date", "Company", "Branch", "Party GSTIN", "Item Code", "Item Name", "Qty",
                 "Item Value", "Cases"])
    d = local_today().strftime("%d-%m-%Y")
    for no in (scanned_no, new_no):
        wr.writerow([no, d, "COA", "RPR", cu.gstin, "SKU-A", "Tab A 10s", "20", "1200", "2"])
        wr.writerow([no, d, "COA", "RPR", cu.gstin, "SKU-B", "Syrup B", "10", "1800", "2"])
    batch, result, _ = imp.quick_import(db, adm, buf.getvalue().encode(), "itemwise.csv")
    assert result["imported"] == 1 and result["items_added"] == 1, (result, [r.errors for r in batch.rows])
    fresh = db.scalar(select(Invoice).where(Invoice.invoice_no == new_no.upper()))
    assert fresh.sales_value == Decimal("3000.00") and [i.sku_code for i in fresh.items] == ["SKU-A", "SKU-B"]
    assert fresh.customer_id == cu.id                                       # found by GSTIN, no code mapping needed
    assert [i.sku_code for i in db.get(Invoice, res.invoice.id).items] == ["SKU-A", "SKU-B"]
    db.rollback()


def test_layout_recognised_from_headers(db):
    headers = ["doc_id", "inv_no", "inv_date", "principal", "branch", "party_id", "party_name", "delivery_city",
               "net_value", "boxes", "item_code", "item_name", "qty", "line_value"]
    t = imp.match_template(db, headers)
    assert t is not None and "Intrect" in t.name
    assert imp.match_template(db, ["Foo", "Bar"]) is None


def test_drop_folder_imports_and_moves_file(db, tmp_path, monkeypatch):
    from app.services import inbox_service
    monkeypatch.setattr(inbox_service, "inbox_dir", lambda: _mk(tmp_path))
    no = new_invoice_no("DROP")
    body = ("Bill No,Bill Date,Branch,Party Code,Party Name,Station,Net Amount,Cases,Narration\n"
            f"{no},{local_today():%d-%m-%Y},RAIPUR,ABC001,ABC PHARMA,BILASPUR,15000.00,5,\n")
    (tmp_path / "MARG").mkdir()
    (tmp_path / "MARG" / "sales.csv").write_text(body, encoding="utf-8")
    results = inbox_service.scan(db)
    assert results and results[0]["ok"], results
    assert db.scalar(select(Invoice).where(Invoice.invoice_no == no.upper()))
    assert not (tmp_path / "MARG" / "sales.csv").exists() and list((tmp_path / "processed").glob("sales.*.csv"))
    (tmp_path / "broken.xlsx").write_bytes(b"not excel")
    assert inbox_service.scan(db)[0]["ok"] is False and list((tmp_path / "failed").glob("broken.*"))


def _mk(p):
    for sub in ("processed", "failed"):
        (p / sub).mkdir(exist_ok=True)
    return p


# ----------------------------------------------------------------------------- 6 screens & reports
@pytest.mark.parametrize("url", ["/gatepass/new", "/gatepass", "/gatepass/gate", "/gatepass/lr-pending", "/imports",
                                 "/recon", "/recon/scan", "/approvals", "/masters/autos", "/masters/customers/new"])
def test_pages_render(web, url):
    r = web("admin").get(url)
    assert r.status_code == 200, url


@pytest.mark.parametrize("code", ["gatepass-register", "customer-type", "sku-dispatch", "mode-of-dispatch",
                                  "transporter-area", "outside-auto", "bill-irregularities"])
def test_new_reports(web, code):
    w = web("management")
    r = w.get(f"/reports/{code}?date_from=2000-01-01")
    assert r.status_code == 200 and "TOTAL" in r.text
    x = w.get(f"/reports/{code}/export?date_from=2000-01-01&fmt=xlsx")
    assert x.status_code == 200


def test_customer_types_in_report(api):
    rows = api("management").get("/api/v1/reports/customer-type", params={"date_from": "2000-01-01"}).json()["rows"]
    assert {r["type"] for r in rows} >= {"Stockist", "Retailer", "Hospital"}


def test_gatepass_detail_and_lr_pending_pages(web, db):
    w = web("admin")
    slip = db.scalar(select(DispatchSlip).where(DispatchSlip.is_void.is_(False), DispatchSlip.invoice_count > 1))
    if slip is None:
        slip = db.scalar(select(DispatchSlip).where(DispatchSlip.is_void.is_(False)))
    page = w.get(f"/gatepass/{slip.id}").text
    assert slip.dispatch_no in page and "Consignments" in page
    assert w.get("/gatepass/lr-pending").status_code == 200
    assert re.search(r"Gatepasses", w.get("/gatepass?state=all").text)


def test_gatepass_list_totals_not_multiplied(web, db):
    page = web("admin").get("/gatepass?state=all&date_from=2000-01-01").text
    shown = int(re.search(r'Gatepasses</div><div class="val">(\d+)<', page).group(1))
    live = db.scalar(select(func.count(DispatchSlip.id)).where(DispatchSlip.is_void.is_(False)))
    assert 0 < shown <= live


def test_master_save_defaults_choice_fields(web, db):
    w = web("admin")
    lo = db.scalar(select(Location).where(Location.code == "RPR"))
    w.post("/masters/customers/save", {"code": "C-T13X", "name": "Test Hospital", "location_id": str(lo.id),
                                       "party_type": "HOSPITAL", "is_active": "1"})
    assert _cu(db, "C-T13X").party_type == "HOSPITAL"
    w.post("/masters/autos/save", {"code": "AUTO-T13", "name": "Test Auto", "rate": "300", "is_active": "1"})
    a = db.scalar(select(OutsideAuto).where(OutsideAuto.code == "AUTO-T13"))
    assert a.charge_basis == "PER_TRIP" and a.rate == Decimal("300.00")


def test_items_present_from_itemwise_demo_import(db):
    assert db.scalar(select(func.count(InvoiceItem.id))) > 0
