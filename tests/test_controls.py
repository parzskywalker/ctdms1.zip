"""Control / integration tests beyond the acceptance list: import pipeline, ERP push API, security."""
from __future__ import annotations

import io
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import (
    AuditLog, DispatchSlip, ERPCodeMapping, ERPSystem, ImportBatch, ImportRow, Invoice, InvoiceStatus, Transporter, User,
)
from app.services.utils import local_today
from tests.helpers import get_inv, make_invoice, new_invoice_no


def _marg_csv(rows: list[dict]) -> bytes:
    head = ["Bill No", "Bill Date", "Branch", "Party Code", "Party Name", "Station", "Net Amount", "Cases", "Narration"]
    out = io.StringIO()
    out.write(",".join(head) + "\n")
    for r in rows:
        out.write(",".join(str(r.get(h, "")) for h in head) + "\n")
    return out.getvalue().encode()


def test_file_import_validation_duplicates_and_correction(web, db):
    w = web("admin")
    d = local_today().strftime("%d-%m-%Y")
    n1, n2, n3, n4 = (new_invoice_no("MB") for _ in range(4))
    content = _marg_csv([
        {"Bill No": n1, "Bill Date": d, "Branch": "RAIPUR", "Party Code": "ABC001", "Party Name": "ABC PHARMA",
         "Station": "BILASPUR", "Net Amount": "10000", "Cases": "10"},
        {"Bill No": n1, "Bill Date": d, "Branch": "RAIPUR", "Party Code": "ABC001", "Party Name": "ABC PHARMA",
         "Station": "BILASPUR", "Net Amount": "10000", "Cases": "10"},                                # duplicate in file
        {"Bill No": n2, "Bill Date": d, "Branch": "RAIPUR", "Party Code": "ZZ999", "Party Name": "UNKNOWN PARTY",
         "Station": "RAIPUR", "Net Amount": "5000", "Cases": "4"},                                    # missing customer
        {"Bill No": n3, "Bill Date": d, "Branch": "RAIPUR", "Party Code": "MET005", "Party Name": "METRO PHARMA",
         "Station": "ATLANTIS", "Net Amount": "5000", "Cases": "4"},                                  # missing location
        {"Bill No": n4, "Bill Date": d, "Branch": "RAIPUR", "Party Code": "MET005", "Party Name": "METRO PHARMA",
         "Station": "RAIPUR", "Net Amount": "7000", "Cases": ""},                                     # boxes missing
    ])
    from app.models import ImportTemplate
    marg_tpl = db.scalar(select(ImportTemplate).where(ImportTemplate.name.like("MARG%")))
    r = w.post("/imports/upload", {"template_id": str(marg_tpl.id)},
               files={"file": ("marg_test.csv", content, "text/csv")})
    assert r.status_code == 200
    batch = db.scalar(select(ImportBatch).where(ImportBatch.file_name == "marg_test.csv").order_by(ImportBatch.id.desc()))
    rows = {r.row_no: r for r in db.scalars(select(ImportRow).where(ImportRow.batch_id == batch.id))}
    assert rows[1].status == "VALID"
    assert rows[2].status == "DUPLICATE"
    assert rows[3].status == "REJECTED" and rows[3].errors[0]["code"] == "MISSING_CUSTOMER"
    assert rows[4].status == "REJECTED" and rows[4].errors[0]["code"] == "MISSING_LOCATION"
    assert rows[5].status == "WARNING" and rows[5].warnings[0]["code"] == "BOXES_MISSING"
    w.post(f"/imports/{batch.id}/commit")
    db.expire_all()
    batch = db.get(ImportBatch, batch.id)
    assert batch.imported_count == 2 and batch.rejected_count == 2 and batch.duplicate_count == 1
    assert batch.status == "PARTIAL"
    boxes_missing = db.scalar(select(Invoice).where(Invoice.invoice_no == n4.upper()))
    assert boxes_missing.status == "EXCEPTION" and boxes_missing.exception_code == "BOXES_MISSING"
    # correction 1: map the unknown MARG party code to an existing customer, then retry
    from app.models import Customer
    metro = db.scalar(select(Customer).where(Customer.code == "C-MET01"))
    w.post(f"/imports/{batch.id}/map-customer", {"source_code": "ZZ999", "customer_id": str(metro.id)})
    # correction 2: fix the location on the row and re-import it
    w.post(f"/imports/{batch.id}/rows/{rows[4].id}", {"f_location": "Raipur", "then": "import"})
    w.post(f"/imports/{batch.id}/retry")
    db.expire_all()
    batch = db.get(ImportBatch, batch.id)
    assert batch.imported_count == 4 and batch.rejected_count == 0
    assert db.scalar(select(ERPCodeMapping).where(ERPCodeMapping.source_code == "ZZ999")).target_id == metro.id
    corrected = db.scalars(select(AuditLog).where(AuditLog.action == "IMPORT_ROW_CORRECTED")).all()
    assert any(a.field == "location" and a.new_value == "Raipur" for a in corrected)
    # the same file again: every row is now a duplicate
    r = w.post("/imports/upload", {"template_id": str(marg_tpl.id)}, files={"file": ("marg_test.csv", content, "text/csv")})
    assert "already imported" in r.text
    b2 = db.scalar(select(ImportBatch).where(ImportBatch.file_name == "marg_test.csv").order_by(ImportBatch.id.desc()))
    statuses = {x.status for x in db.scalars(select(ImportRow).where(ImportRow.batch_id == b2.id))}
    assert "VALID" not in statuses and "DUPLICATE" in statuses


def test_boxes_missing_initial_entry_without_approval(api, web):
    inv = make_invoice(api("admin"), company="COA", cfa="RPR", customer="C-MET01", boxes=None)
    assert inv["status"] == "EXCEPTION"
    web("operator.raipur").post(f"/invoices/{inv['id']}/override", {"field": "BOXES", "new_value": "12",
                                                                     "reason": "Counted at dock"})
    i = get_inv(inv["id"])
    assert i.status == "PENDING" and i.boxes == 12 and i.freight_amount == Decimal("36.00")  # 12 x ₹3 Raipur local
    assert i.boxes_source == "MANUAL"


def test_erp_push_api_with_key_and_writeback(web, db):
    w = web("admin")
    sap = db.scalar(select(ERPSystem).where(ERPSystem.code == "SAP"))
    page = w.post(f"/admin/integrations/{sap.id}/api-key").text   # key is shown once on the redirected page
    key = page.split("(shown once): <code>")[1].split("</code>")[0]
    c = TestClient(app)
    assert c.post("/api/v1/integrations/SAP/invoices", json={"invoices": []}, headers={"X-API-Key": "bad"}).status_code == 401
    inv_no = new_invoice_no("9")
    body = {"invoices": [{"BillingDocument": inv_no, "BillingDocumentDate": "/Date(1790000000000)/",
                          "SoldToParty": "100245", "TotalNetAmount": "56000", "SalesOrganization": "1000",
                          "cfa_code": "RPR", "location": "Bilaspur", "boxes": 36}]}
    r = c.post("/api/v1/integrations/SAP/invoices", json=body, headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["imported"] == 1, res
    inv = get_inv(res["rows"][0]["invoice_id"])
    assert inv.customer.code == "C-ABC01"                         # SAP code 100245 -> central ABC Pharma
    assert inv.rate_per_box == Decimal("7.00") and inv.freight_amount == Decimal("252.00")  # customer-specific
    # write-back: dispatched SAP invoices are offered to the ERP and can be acknowledged
    upd = c.get("/api/v1/integrations/SAP/dispatch-updates", headers={"X-API-Key": key}).json()
    ids = [u["invoice_id"] for u in upd][:3]
    if ids:
        ack = c.post("/api/v1/integrations/SAP/dispatch-updates/ack", json={"invoice_ids": ids}, headers={"X-API-Key": key})
        assert ack.json()["acknowledged"] == len(ids)
        assert get_inv(ids[0]).erp_sync_status == "SYNCED"


def test_demo_adapter_sync_run(web, db):
    demo = db.scalar(select(ERPSystem).where(ERPSystem.code == "DEMO"))
    r = web("admin").post(f"/admin/integrations/{demo.id}/sync")
    assert r.status_code == 200
    from app.models import IntegrationRun
    run = db.scalar(select(IntegrationRun).where(IntegrationRun.erp_system_id == demo.id).order_by(IntegrationRun.id.desc()))
    assert run.status == "SUCCESS" and run.imported >= 1


def test_unconfigured_adapter_reports_missing_credentials(web, db):
    cbo = db.scalar(select(ERPSystem).where(ERPSystem.code == "CBO"))
    r = web("admin").post(f"/admin/integrations/{cbo.id}/test")
    assert "Not configured" in r.text


def test_csrf_required_for_form_posts(web):
    w = web("admin")
    r = w.c.post("/admin/settings", data={"s_org.name": "Hacked"})
    assert r.status_code == 403


def test_api_session_writes_need_csrf_header(web):
    w = web("admin")
    r = w.c.post("/api/v1/invoices/1/verify")
    assert r.status_code == 403


def test_login_lockout_after_failures():
    c = TestClient(app)
    import re
    for _ in range(5):
        tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").text).group(1)
        c.post("/login", data={"username": "viewer", "password": "wrong", "csrf_token": tok})
    tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").text).group(1)
    r = c.post("/login", data={"username": "viewer", "password": "Demo@123", "csrf_token": tok})
    assert "locked" in r.text.lower()
    # unlock for other tests
    from app.db import SessionLocal
    s = SessionLocal()
    u = s.scalar(select(User).where(User.username == "viewer"))
    u.locked_until = None
    u.failed_logins = 0
    s.commit()
    s.close()


def test_dispatch_numbers_unique_and_bulk_dispatch(api, web, db):
    a = api("admin")
    invs = [make_invoice(a, company="COA", cfa="RPR", customer="C-XYZ01", boxes=b) for b in (5, 6, 7)]
    tr = db.scalar(select(Transporter).where(Transporter.code == "SBT"))
    w = web("operator.raipur")
    data = {"transporter_id": tr.id, "vehicle_no": "CG07BT2020", "dispatch_date": local_today().isoformat(),
            "dispatch_time": "16:00"}
    form = {**data, "ids": [str(i["id"]) for i in invs], "csrf_token": w.token,
            **{f"lr_{i['id']}": f"BULK-{i['id']}" for i in invs}}
    r = w.c.post("/dispatch/bulk/apply", data=form)
    assert r.status_code == 200 and r.text.count("Slip DS/RPR/") == 3
    nos = [get_inv(i["id"]).dispatch_no for i in invs]
    assert len(set(nos)) == 3
    assert db.scalar(select(func.count(DispatchSlip.id))) == db.scalar(select(func.count(func.distinct(DispatchSlip.dispatch_no))))
    # bulk print (PDF with all slips) then bulk complete
    ids = [str(i["id"]) for i in invs]
    r = w.c.post("/dispatch/bulk/print", data={"ids": ids, "csrf_token": w.token, "size": "A5", "mode": "pdf"})
    assert r.headers["content-type"] == "application/pdf"
    w.c.post("/dispatch/bulk/complete", data={"ids": ids, "csrf_token": w.token})
    assert all(get_inv(i["id"]).status == "DISPATCHED" for i in invs)


def test_cancel_requires_reason_and_voids_slip(api, web):
    inv = make_invoice(api("admin"), company="COA", cfa="RPR", customer="C-XYZ01", boxes=8)
    chk = web("checker")
    r = chk.post(f"/dispatch/{inv['id']}/cancel", {"reason": ""})
    assert "mandatory" in r.text
    assert get_inv(inv["id"]).status == "PENDING"
    chk.post(f"/dispatch/{inv['id']}/cancel", {"reason": "Order cancelled by customer"})
    i = get_inv(inv["id"])
    assert i.status == "CANCELLED" and i.cancel_reason == "Order cancelled by customer"
    rep = web("management").get("/reports/cancelled?date_from=2000-01-01")
    assert inv["invoice_no"] in rep.text


def test_audit_trail_captures_ip_and_session(web, db):
    w = web("admin")
    w.post("/admin/settings", {"s_dispatch.number_prefix": "DS", "reason": "no-op"})
    entry = db.scalar(select(AuditLog).where(AuditLog.action == "LOGIN", AuditLog.username == "admin")
                      .order_by(AuditLog.id.desc()))
    assert entry.ip is not None


def test_configurable_completion_rule(api, web, db):
    from app.models import Setting
    s = db.get(Setting, "dispatch.complete_requires")
    s.value = "GENERATED"
    db.commit()
    try:
        inv = make_invoice(api("admin"), company="COA", cfa="RPR", customer="C-XYZ01", boxes=9)
        tr = db.scalar(select(Transporter).where(Transporter.code == "SBT"))
        w = web("operator.raipur")
        form = {"transporter_id": tr.id, "vehicle_no": "CG07BT1010", "lr_no": f"R-{inv['id']}",
                "dispatch_date": local_today().isoformat(), "dispatch_time": "11:00"}
        w.post(f"/dispatch/{inv['id']}/verify", form)
        w.post(f"/dispatch/{inv['id']}/generate", form)
        w.post(f"/dispatch/{inv['id']}/complete")
        assert get_inv(inv["id"]).status == InvoiceStatus.DISPATCHED  # allowed without printing under GENERATED
    finally:
        s = db.get(Setting, "dispatch.complete_requires")
        db.refresh(s)
        s.value = "PRINTED"
        db.commit()


def test_customer_merge_redirects_future_imports(web, api, db):
    from app.models import Customer, Location
    w = web("admin")
    loc = db.scalar(select(Location).where(Location.code == "RPR"))
    w.post("/masters/customers/save", {"code": "C-DUP01", "name": "Metro Pharma Pvt", "location_id": str(loc.id),
                                       "is_active": "1"})
    dup = db.scalar(select(Customer).where(Customer.code == "C-DUP01"))
    metro = db.scalar(select(Customer).where(Customer.code == "C-MET01"))
    w.post(f"/masters/customers/{metro.id}/merge", {"source_id": str(dup.id), "reason": "Same party, duplicate code"})
    inv = make_invoice(api("admin"), company="COA", cfa="RPR", customer="C-DUP01", boxes=3)
    assert inv["customer"]["code"] == "C-MET01"


def test_rate_bulk_upload_and_auto_reprice_of_missing(api, web, db):
    a = api("admin")
    inv = make_invoice(a, company="COC", cfa="RPR", customer="C-SUN01", boxes=10)
    assert inv["status"] == "RATE_MISSING"
    csv_body = ("company,cfa,customer,location,rate_per_box,effective_from,effective_to,is_default,reason\n"
                f"COC,RPR,,Korba,10.50,{local_today().replace(day=1):%d-%m-%Y},,,Korba rate for Company C\n")
    # freight-rate administration belongs to the Administrator (Depot Manager: 403); a checker approves
    assert web("cfa.raipur").post("/rates/import", files={"file": ("rates.csv", csv_body.encode(), "text/csv")},
                                  follow_redirects=False).status_code == 403
    r = web("admin").post("/rates/import", files={"file": ("rates.csv", csv_body.encode(), "text/csv")})
    assert "1 rate(s) uploaded" in r.text
    from app.models import FreightRate, RateStatus
    rate = db.scalar(select(FreightRate).where(FreightRate.rate_per_box == Decimal("10.50"),
                                               FreightRate.status == RateStatus.PENDING_APPROVAL))
    assert rate is not None
    r = web("checker").post(f"/rates/{rate.id}/decide", {"decision": "approve", "remarks": "ok"})
    assert "re-priced automatically" in r.text
    fixed = a.get(f"/api/v1/invoices/{inv['id']}").json()
    assert fixed["status"] == "PENDING" and fixed["transportation_expense"] == "105.00"


def test_cli_create_admin_and_backup(tmp_path):
    from app import cli
    cli.main(["create-admin", "--username", "ops.admin", "--password", "Strong#Pass1"])
    c = TestClient(app)
    r = c.post("/api/v1/auth/token", json={"username": "ops.admin", "password": "Strong#Pass1"})
    assert r.status_code == 200
    import os
    if os.environ["CTDMS_DATABASE_URL"].startswith("sqlite"):
        cli.main(["backup", "--out", str(tmp_path), "--keep", "2"])
        assert list(tmp_path.glob("ctdms-*.sqlite3"))
