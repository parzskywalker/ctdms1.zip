"""v1.9 Labels to Print: two sections (left to print / already printed + Reprint), Print all, 100 x 100 mm stickers."""
from __future__ import annotations

from sqlalchemy import select

from app.models import CaseStatus, DispatchCase, Invoice
from app.services import settings_service as cfg
from tests.helpers import make_invoice
from tests.test_v18_labels_first import v18  # noqa: F401


def _cases(db, inv_id):
    db.expire_all()
    return list(db.scalars(select(DispatchCase).where(DispatchCase.invoice_id == inv_id,
                                                      DispatchCase.status != CaseStatus.VOID)))


def test_print_all_then_the_invoices_move_to_printed_with_reprint(v18, api, web, db):  # noqa: F811
    a = make_invoice(api("admin"), customer="C-HRM01", boxes=2)["id"]
    b = make_invoice(api("admin"), customer="C-HRM01", boxes=3)["id"]
    cfg.set_value(db, "labels.height_mm", 100)  # the 100 x 100 mm stickers, whatever an earlier test left
    db.commit()
    w = web("cfa.raipur")
    page = w.get("/labels-desk").text
    assert "1 · Labels left to print" in page and "Print all" in page and "2 · Labels already printed" in page

    sheet = w.post("/labels-desk/print-all").text   # one print run for every invoice still to print
    na, nb = (db.get(Invoice, i).invoice_no for i in (a, b))
    assert na in sheet and nb in sheet and "invoices (Print all)" in sheet and "100 × 100 mm" in sheet
    assert len(_cases(db, a)) == 2 and len(_cases(db, b)) == 3
    assert "2 / 2" in sheet and "3 / 3" in sheet                      # numbered per invoice, not per run

    ids = sheet.split('name="ids" value="')[1].split('"')[0]
    w.post("/labels-desk/all/confirm", {"ids": ids, "result": "PRINTED"})
    assert all(c.status == CaseStatus.LABELLED for c in _cases(db, a) + _cases(db, b))
    page = w.get("/labels-desk").text
    printed = page.split("2 · Labels already printed")[1]
    assert na in printed and nb in printed and f'href="/labels-desk/{a}/print">Reprint' in printed


def test_label_height_defaults_to_100mm():
    assert cfg.DEFAULTS["labels.height_mm"][0] == "100" and cfg.DEFAULTS["labels.width_mm"][0] == "100"
