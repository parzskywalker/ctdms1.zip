"""v1.4.1 - items from the management prompt / flowchart that were still missing."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from app.models import CFA, Company, Customer, ImportBatch, Invoice, Transporter
from app.services import import_service as imp
from app.services.reports import Filters, run_report
from app.services.utils import local_today
from tests.helpers import get_inv, ingest, new_invoice_no


def _ids(db):
    co = db.scalar(select(Company).where(Company.code == "COA"))
    cfa = db.scalar(select(CFA).where(CFA.code == "RPR"))
    cu = db.scalar(select(Customer).where(Customer.code == "C-HRM01"))
    tr = db.scalar(select(Transporter).where(Transporter.code == "OSHO"))
    return co, cfa, cu, tr


def _form(db, no, **extra):
    co, cfa, cu, tr = _ids(db)
    data = {"company_id": co.id, "cfa_id": cfa.id, "customer_id": cu.id, "invoice_no": no,
            "invoice_date": local_today().isoformat(), "sales_value": "25000", "taxable_value": "22321.43",
            "boxes": "10", "dispatch_mode": "TRANSPORTER", "transporter_id": tr.id}
    data.update(extra)
    return {k: str(v) for k, v in data.items()}


def test_add_invoice_by_hand_goes_through_import_checks(web, db):
    w = web("admin")
    page = w.get("/invoices/new")
    assert page.status_code == 200 and "Taxable amount" in page.text and "Mode of dispatch" in page.text
    no = new_invoice_no("MAN")
    r = w.post("/invoices/new", _form(db, no))
    assert r.status_code == 200 and f"Invoice {no.upper()} added" in r.text
    inv = db.scalar(select(Invoice).where(Invoice.invoice_no == no.upper()))
    co, cfa, cu, tr = _ids(db)
    assert inv.capture_mode == "MANUAL" and inv.boxes_source == "MANUAL"
    assert inv.sales_value == Decimal("25000.00") and inv.taxable_value == Decimal("22321.43")
    assert inv.dispatch_mode == "TRANSPORTER" and inv.transporter_id == tr.id
    assert inv.location_id == cu.location_id            # area comes from the stockist
    # the same invoice twice is refused, the form keeps what was typed, nothing half-saved
    batches = db.scalar(select(func.count(ImportBatch.id)))
    r = w.post("/invoices/new", _form(db, no))
    assert r.status_code == 400 and "already exists" in r.text and no.upper() in r.text
    assert db.scalar(select(func.count(ImportBatch.id))) == batches
    assert db.scalar(select(func.count(Invoice.id)).where(Invoice.invoice_no == no.upper())) == 1


def test_add_invoice_requires_fields_and_rights(web, db):
    r = web("admin").post("/invoices/new", {"invoice_no": "X1"})
    assert r.status_code == 400 and "Select the stockist" in r.text and "Invoice amount is required" in r.text
    assert web("viewer").get("/invoices/new").status_code == 403


def test_planned_mode_and_transporter_preselect_gatepass(web, db):
    w = web("admin")
    no = new_invoice_no("PLAN")
    w.post("/invoices/new", _form(db, no, dispatch_mode="SELF", transporter_id=""))
    inv = db.scalar(select(Invoice).where(Invoice.invoice_no == no.upper()))
    r = w.post("/gatepass/scan", {"code": no.upper(), "cfa_id": str(inv.cfa_id)})
    found = r.json()["invoice"]
    assert found["plan_mode"] == "SELF" and found["plan_tr"] is None


def test_import_keeps_taxable_amount_apart_from_invoice_amount(api, db):
    no = new_invoice_no("TAX")
    res = ingest(api("admin"), [{"invoice_no": no, "invoice_date": local_today().isoformat(), "company_code": "COA",
                                 "cfa_code": "RPR", "customer_code": "C-VDP01", "sales_value": "11800",
                                 "taxable_value": "10000", "boxes": 3, "dispatch_mode": "Outside auto"}])
    inv = get_inv(res["rows"][0]["invoice_id"])
    assert inv.sales_value == Decimal("11800.00") and inv.taxable_value == Decimal("10000.00")
    assert inv.dispatch_mode == "OUTSIDE_AUTO"
    # file headings: "Taxable Value" no longer lands in the invoice amount
    m = imp.auto_map(["Invoice No", "Invoice Date", "Party Code", "Invoice Amount", "Taxable Value", "Mode"])
    assert m["sales_value"] == "Invoice Amount" and m["taxable_value"] == "Taxable Value"
    assert m["dispatch_mode"] == "Mode"


def test_transporter_report_shows_trips_and_cost_per_trip(db):
    from app.models import User
    admin = db.scalar(select(User).where(User.username == "admin"))
    res = run_report(db, admin, "transporter", Filters(date_from=local_today().replace(year=2000)))
    keys = [c.key for c in res.columns]
    assert "trips" in keys and "per_trip" in keys and "per_case" in keys
    with_trips = [r for r in res.rows if r["trips"]]
    assert with_trips
    r = with_trips[0]
    assert r["per_trip"] == (Decimal(r["freight"]) / r["trips"]).quantize(Decimal("0.01"))
