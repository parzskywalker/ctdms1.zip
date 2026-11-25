"""v1.8.4 Planned Loads: the Depot Manager saves a vehicle plan, a Dispatch Operator makes the gatepass from it."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import DispatchSlip, Invoice, LoadPlan, Transporter, User
from app.services import case_service as cs
from app.services import dispatch_service as ds
from app.services.errors import BusinessError
from tests.helpers import local_auto, make_invoice
from tests.test_v18_labels_first import v18  # noqa: F401 - labels-first settings on for the test


def _labelled(db, api, boxes=2) -> int:
    adm = db.scalar(select(User).where(User.username == "admin"))
    inv = db.get(Invoice, make_invoice(api("admin"), customer="C-HRM01", boxes=boxes)["id"])
    cs.label_invoice(db, adm, inv, boxes)
    cs.record_invoice_print(db, adm, inv, result="PRINTED")
    db.commit()
    return inv.id


def _vehicle(db) -> dict:
    osho = db.scalar(select(Transporter).where(Transporter.code == "OSHO"))
    f = {k: str(v) for k, v in local_auto(db).items()}
    return {"mode": "TRANSPORTER", "transporter_id": str(osho.id), **f}


def test_manager_plans_operator_makes_and_tampering_changes_nothing(v18, api, web, db):  # noqa: F811
    a, b = _labelled(db, api), _labelled(db, api)
    dm, op = web("cfa.raipur"), web("operator.raipur")
    assert db.get(Invoice, a).invoice_no in dm.get("/ready-to-load").text

    r = dm.post("/load-plans/save", {"ids": [a], **_vehicle(db)})
    assert r.status_code == 200 and "Planned Loads" in r.text
    plan = db.scalar(select(LoadPlan).where(LoadPlan.status == "OPEN").order_by(LoadPlan.id.desc()))
    assert plan.invoice_ids == [a] and plan.planned_by.username == "cfa.raipur"
    assert db.get(Invoice, a).invoice_no not in dm.get("/ready-to-load").text   # held by the plan
    assert "#%d" % plan.id in op.get("/load-plans").text

    operator = db.scalar(select(User).where(User.username == "operator.raipur"))
    with pytest.raises(BusinessError):                                         # no plan: the operator still cannot
        ds.create_gatepass(db, operator, [db.get(Invoice, b)],
                           ds.GatepassRequest(**local_auto(db), transporter_id=int(_vehicle(db)["transporter_id"]),
                                              cases={b: None}))
    db.rollback()

    r = op.post(f"/load-plans/{plan.id}/make", {"ids": [a, b], "transporter_id": "999999"})  # posted values ignored
    assert r.status_code == 200
    db.expire_all()
    plan = db.get(LoadPlan, plan.id)
    slip = db.get(DispatchSlip, plan.slip_id)
    assert plan.status == "MADE" and plan.done_by.username == "operator.raipur"
    assert slip.invoice_count == 1 and str(slip.transporter_id) == _vehicle(db)["transporter_id"]
    assert "not open any more" in op.post(f"/load-plans/{plan.id}/make").text     # made once only


def test_vehicle_screen_tells_the_two_local_autos_apart(web):
    page = web("cfa.raipur").get("/gatepass/new").text
    assert "Transporter / garage (LR)" in page and "Local auto to the customer (no LR)" in page
    assert 'id="gp-local-warn"' in page and "Local auto to the garage" in page


def test_planned_invoice_is_held_and_cancel_releases_it(v18, api, web, db):  # noqa: F811
    a = _labelled(db, api)
    dm = web("cfa.raipur")
    dm.post("/load-plans/save", {"ids": [a], **_vehicle(db)})
    plan = db.scalar(select(LoadPlan).where(LoadPlan.status == "OPEN").order_by(LoadPlan.id.desc()))
    mgr = db.scalar(select(User).where(User.username == "cfa.raipur"))
    with pytest.raises(BusinessError, match="already in a vehicle plan"):       # not on a second gatepass by the side door
        ds.create_gatepass(db, mgr, [db.get(Invoice, a)],
                           ds.GatepassRequest(**local_auto(db), transporter_id=int(_vehicle(db)["transporter_id"]),
                                              cases={a: None}))
    db.rollback()
    assert "Already in another vehicle plan" in dm.post("/load-plans/save", {"ids": [a], **_vehicle(db)}).text
    assert "reason" in dm.post(f"/load-plans/{plan.id}/cancel", {"reason": "no"}).text   # reason required
    dm.post(f"/load-plans/{plan.id}/cancel", {"reason": "vehicle broke down"})
    db.expire_all()
    assert db.get(LoadPlan, plan.id).status == "CANCELLED"
    assert db.get(Invoice, a).invoice_no in dm.get("/ready-to-load").text
    assert "Vehicle plan #" in web("operator.raipur").get(f"/load-plans/{plan.id}").text  # page opens, no make button
    assert "Make gate pass" not in web("operator.raipur").get(f"/load-plans/{plan.id}").text
