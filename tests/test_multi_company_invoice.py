"""Pilot change: one invoice may carry stock of more than one company. Invoice.company stays the main company; the others
are listed on the invoice, the gatepass, the labels and the print."""
from __future__ import annotations

from sqlalchemy import select

from app.models import Company, Invoice, Transporter, User
from app.services import dispatch_service as ds
from app.services.utils import local_today
from tests.helpers import get_inv, local_auto, new_invoice_no


def _row(no, other=None, company="COA", customer="C-HRM01", boxes=4):
    row = {"invoice_no": no, "invoice_date": local_today().isoformat(), "company_code": company, "cfa_code": "RPR",
           "customer_code": customer, "sales_value": "42000", "boxes": boxes}
    if other is not None:
        row["other_companies"] = other
    return row


def _ingest(api, row):
    r = api("admin").post("/api/v1/invoices/ingest", json={"invoices": [row]})
    assert r.status_code == 200, r.text
    return r.json()


def test_import_takes_the_other_companies_and_keeps_the_main_one(api, db):
    no = new_invoice_no("MC")
    res = _ingest(api, _row(no, other="COB, COC"))
    assert res["imported"] == 1
    inv = get_inv(res["rows"][0]["invoice_id"])
    assert inv.company.code == "COA"
    assert [c.code for c in inv.extra_companies] == ["COB", "COC"] and inv.company_codes == "COA + COB + COC"


def test_the_main_company_named_again_is_ignored_and_an_unknown_one_is_refused(api, db):
    res = _ingest(api, _row(new_invoice_no("MC"), other="COA, COB"))
    assert get_inv(res["rows"][0]["invoice_id"]).company_codes == "COA + COB"
    bad = _ingest(api, _row(new_invoice_no("MC"), other="COB, NOSUCH"))
    assert bad["imported"] == 0 and bad["rejected"] == 1
    assert "NOSUCH" in str(bad["rows"][0])
    plain = _ingest(api, _row(new_invoice_no("MC")))
    assert get_inv(plain["rows"][0]["invoice_id"]).company_codes == "COA"       # nothing changes for ordinary invoices


def test_add_invoice_form_ticks_the_other_companies(web, db):
    w = web("admin")
    page = w.get("/invoices/new").text
    assert "Also stock of these companies" in page
    coa, cob = (db.scalar(select(Company).where(Company.code == c)) for c in ("COA", "COB"))
    from app.models import CFA, Customer
    cfa = db.scalar(select(CFA).where(CFA.code == "RPR"))
    cu = db.scalar(select(Customer).where(Customer.code == "C-HRM01"))
    no = new_invoice_no("MCF")
    form = {"company_id": coa.id, "cfa_id": cfa.id, "customer_id": cu.id, "invoice_no": no,
            "invoice_date": local_today().isoformat(), "sales_value": "30000", "boxes": "6",
            f"xco_{cob.id}": "1", f"xco_{coa.id}": "1"}           # the main company ticked too: ignored
    r = w.post("/invoices/new", {k: str(v) for k, v in form.items()})
    assert r.status_code == 200 and f"Invoice {no.upper()} added" in r.text
    inv = db.scalar(select(Invoice).where(Invoice.invoice_no == no.upper()))
    assert inv.company_codes == "COA + COB"
    detail = w.get(f"/invoices/{inv.id}").text
    assert "also stock of" in detail and cob.name in detail
    assert "COA + COB" in w.get(f"/dispatch/pending?invoice_no={no}").text


def test_gatepass_labels_and_print_show_every_company(web, api, db):
    res = _ingest(api, _row(new_invoice_no("MCG"), other="COB", boxes=4))
    inv = db.get(Invoice, res["rows"][0]["invoice_id"])
    adm = db.scalar(select(User).where(User.username == "admin"))
    osho = db.scalar(select(Transporter).where(Transporter.code == "OSHO"))
    slip = ds.create_gatepass(db, adm, [inv], ds.GatepassRequest(**local_auto(db), transporter_id=osho.id, cases={inv.id: 4}))
    db.commit()
    assert slip.snapshot["groups"][0]["invoices"][0]["company_code"] == "COA + COB"
    w = web("admin")
    assert "COA + COB" in w.get(f"/gatepass/{slip.id}").text
    assert "COA + COB" in w.get(f"/gatepass/{slip.id}/labels").text
