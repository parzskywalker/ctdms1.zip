"""v1.8 dispatch: labels before any gatepass, the Depot Manager makes the gatepass from Ready to Load, exceptions are listed
on the gatepass and the signed copy must be photographed before the gate scan can send it out."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import CaseStatus, DispatchException, Invoice, Transporter, User
from app.services import case_service as cs
from app.services import dispatch_service as ds
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from tests.helpers import local_auto, make_invoice, walk_out


@pytest.fixture()
def v18(db):
    keys = ("dispatch.labels_first", "dispatch.signature_photo_required", "dispatch.cross_company_check")
    before = {k: cfg.get(db, k) for k in keys}
    cfg.set_value(db, "dispatch.labels_first", True)
    cfg.set_value(db, "dispatch.signature_photo_required", True)
    db.commit()
    yield
    for k, v in before.items():
        cfg.set_value(db, k, v)
    db.commit()


def _req(db, inv_id, **kw):
    osho = db.scalar(select(Transporter).where(Transporter.code == "OSHO"))
    return ds.GatepassRequest(**local_auto(db), transporter_id=osho.id, cases={inv_id: None}, **kw)


def test_labels_first_bind_void_release_and_signed_photo_before_gate_out(v18, api, db):
    adm = db.scalar(select(User).where(User.username == "admin"))
    inv = db.get(Invoice, make_invoice(api("admin"), customer="C-HRM01", boxes=3)["id"])
    with pytest.raises(BusinessError, match="labels not printed yet"):
        ds.create_gatepass(db, adm, [inv], _req(db, inv.id))
    db.rollback()
    inv = db.get(Invoice, inv.id)

    cartons = cs.label_invoice(db, adm, inv, 4, reason="one carton packed extra")   # differs from 3: exception
    assert inv.boxes == 4 and len(cartons) == 4 and all(c.slip_id is None for c in cartons)
    assert db.scalar(select(DispatchException.kind).where(DispatchException.invoice_id == inv.id)) == "BOX_COUNT"
    assert cs.scan(db, adm, cartons[0].case_code)["result"] == "NOT_PLANNED"
    _, _, problems = ds.plan_gatepass(db, adm, [inv], _req(db, inv.id))
    assert any("not confirmed as printed" in p for p in problems)
    cs.record_invoice_print(db, adm, inv, result="PRINTED")
    codes = [c.case_code for c in cs.label_first_cartons(db, inv.id)]

    slip = ds.create_gatepass(db, adm, [inv], _req(db, inv.id))
    assert [c.case_code for c in cs.cases_of(db, slip)] == codes            # the same labels, now on the gatepass
    assert [e["kind"] for e in slip.snapshot["exceptions"]] == ["Carton count differs from invoice"]

    ds.void_gatepass(db, adm, slip, "wrong vehicle chosen")                   # labels stay valid
    back = cs.label_first_cartons(db, inv.id)
    assert [c.case_code for c in back] == codes and all(c.status == CaseStatus.LABELLED for c in back)

    slip2 = ds.create_gatepass(db, adm, [db.get(Invoice, inv.id)], _req(db, inv.id))
    assert [c.case_code for c in cs.cases_of(db, slip2)] == codes
    with pytest.raises(BusinessError, match="photograph the gatepass"):
        walk_out(db, slip2)
    slip2.signature_photo = "signatures/test.jpg"
    from app.services import gate_service
    assert gate_service.scan_out(db, adm, slip2) == 1                       # everything already printed: now it leaves
    db.rollback()


def test_urgent_single_trip_only_with_reason_and_is_an_exception(v18, api, db):
    adm = db.scalar(select(User).where(User.username == "admin"))
    inv = db.get(Invoice, make_invoice(api("admin"), customer="C-HRM01", boxes=2)["id"])
    slip = ds.create_gatepass(db, adm, [inv], _req(db, inv.id, urgent_reason="Hospital emergency order"))
    assert slip.urgent_reason and len(cs.cases_of(db, slip)) == 2               # labels made with the gatepass, as before
    assert [e["kind"] for e in slip.snapshot["exceptions"]] == ["Urgent single trip"]
    db.rollback()


def test_new_screens_open_from_the_keyboard_first_menu(v18, web):
    w = web("admin")
    for url, text in (("/labels-desk", "Labels to Print"), ("/ready-to-load", "Ready to Load"),
                      ("/gate/phone", "Check gatepass"), ("/dispatch-exceptions", "Dispatch Exceptions")):
        r = w.get(url)
        assert r.status_code == 200 and text in r.text, url
