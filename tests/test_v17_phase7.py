"""v1.7 Phase 7: the whole dispatch workflow end to end (case control + receiving transporters + Garage Pawati + LR book),
its audit trail, and the two new reports."""
from __future__ import annotations

import csv
import io

from sqlalchemy import select

from app.models import CaseStatus, DispatchAllocation, DispatchCase, DispatchSlip, GaragePawati, LRNumber, Transporter
from tests.conftest import WebClient
from tests.test_rbac import _token_client
from tests.test_v17_phase2 import PW, audit_actions, save_user
from tests.test_v17_phase3 import TODAY, _slip_of, world  # noqa: F401
from tests.test_v17_phase4 import db  # noqa: F401
from tests.test_v17_phase5 import _codes, _gate_out, _scan
from tests.test_v17_phase6 import _auto, _inv, _pawatis


def _csv(c, code):
    r = c.get(f"/reports/{code}/export?fmt=csv&date_from=2020-01-01&date_to=2099-12-31")
    return list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))


def test_whole_workflow_with_case_control_pawati_and_lr_book(db, world):  # noqa: F811
    api = _token_client("admin")
    admin = WebClient("admin")
    t1, t2 = world["tr"].id, world["tr2"].id
    book_tr = db.get(Transporter, t2)
    book_tr.uses_lr_book = True
    db.commit()
    assert "book imported" in admin.post("/lr-books/import", data={"transporter_id": str(t2), "label": "P7 book", "first_no": "P75000",
                                                              "last_no": "P75009"}).text.lower()
    a, b = _inv(api, "P3-TOWN", 4, cfa="BSP"), _inv(api, "P3-TOWN", 2, cust="C-ABC01", cfa="BSP")
    data = {"ids": [str(a["id"]), str(b["id"])], "dispatch_date": TODAY.isoformat(), "mode": "TRANSPORTER",
            "pickup_mode": "OUTSIDE_AUTO", "outside_auto_id": str(_auto(db)), "auto_method": "PER_TRIP", "trip_charge": "300",
            "group_cross_company": "1", f"cases_{a['id']}": "4", f"cases_{b['id']}": "2",
            f"recv_{a['id']}": str(t1), f"recv_{b['id']}": str(t2)}
    sid = _slip_of(admin.post("/gatepass/new", data=data))
    assert sid
    db.expire_all()
    slip = db.get(DispatchSlip, sid)
    assert slip.case_control == "REQUIRED" and slip.transporter_id is None
    # every case belongs to exactly one allocation (= one receiver); every allocation has its cases
    cases = list(db.scalars(select(DispatchCase).where(DispatchCase.slip_id == sid)))
    allocs = {x.id: x for x in db.scalars(select(DispatchAllocation).where(DispatchAllocation.slip_id == sid))}
    assert len(cases) == 6 and all(c.allocation_id in allocs for c in cases)
    assert sorted(sum(1 for c in cases if c.allocation_id == i) for i in allocs) == [2, 4]
    ps = _pawatis(db, sid)
    assert len(ps) == 2 and {p.receiving_transporter_id for p in ps} == {t1, t2}
    # the book gave the second receiver's consignment its LR number at creation
    t2_alloc = next(i for i, x in allocs.items() if x.receiving_transporter_id == t2)
    assert db.scalar(select(LRNumber.lr_no).where(LRNumber.transporter_id == t2, LRNumber.allocation_id == t2_alloc)) == "P75000"
    # the gatepass cannot be printed before the labels (v1.7.1) ...
    assert "Print the case labels first" in admin.post(f"/gatepass/{sid}/print", data={"size": "A5", "mode": "pdf"}).text
    admin.post(f"/gatepass/{sid}/labels/confirm", data={"case_ids": ",".join(str(c.id) for c in cases), "result": "PRINTED"})
    admin.post(f"/gatepass/{sid}/print", data={"size": "A5", "mode": "pdf"})
    # ... and gone out only comes from the gate scan: cases, then the gatepass
    assert admin.post(f"/gatepass/{sid}/gate-out").status_code in (404, 405)       # the route no longer exists
    db.expire_all()
    assert db.get(DispatchSlip, sid).gate_out_at is None
    guard = WebClient("gate.bilaspur")
    codes = _codes(db, sid)
    for code in codes[:-1]:
        assert _scan(guard, code, sid)["ok"]
    early = _gate_out(db, sid)
    assert not early["ok"] and "not scanned" in early["message"]            # one case still unscanned
    last = _scan(guard, codes[-1], sid)
    assert last["ok"] and last["scanned"] == last["total"] == 6 and not last["cleared"]   # the Pawatis still block
    blocked = _gate_out(db, sid)
    assert not blocked["ok"] and "Garage Pawati not printed" in blocked["message"]
    admin.post("/pawati/print", data={"ids": [str(p.id) for p in ps]})
    assert _gate_out(db, sid)["gone_out"]
    db.expire_all()
    slip = db.get(DispatchSlip, sid)
    assert slip.gate_out_at is not None
    assert {c.status for c in db.scalars(select(DispatchCase).where(DispatchCase.slip_id == sid))} == {CaseStatus.DISPATCHED}
    # receipt: one ticked by hand, the other by an LR typed after gate-out
    by = {p.receiving_transporter_id: p for p in _pawatis(db, sid)}
    admin.post(f"/pawati/{by[t1].id}/receive", data={"note": "garage clerk signed"})
    inv_b = db.get(DispatchAllocation, t2_alloc).invoice
    admin.post(f"/gatepass/{sid}/lr", data={f"lr_{inv_b.customer_id}_{t2}": "P7-CHANGED-1", f"lrdate_{inv_b.customer_id}_{t2}": TODAY.isoformat()})
    by = {p.receiving_transporter_id: p for p in _pawatis(db, sid)}
    assert by[t1].received_via == "MANUAL" and by[t2].received_via == "LR"
    # audit trail: every step is on record
    ref = db.get(DispatchSlip, sid).dispatch_no
    acts = audit_actions(db, ref)
    on_pawati = audit_actions(db, f"{by[t1].cfa.code}/{by[t1].pawati_no}") | audit_actions(db, f"{by[t2].cfa.code}/{by[t2].pawati_no}")
    assert {"PAWATI_PRINTED", "PAWATI_RECEIVED"} <= on_pawati
    assert "LR_AUTO_ASSIGNED" in acts
    # the Pawati register report shows both as received, with totals
    rows = _csv(admin, "garage-pawati")
    mine = [r for r in rows if r and r[2] in {str(p.pawati_no) for p in by.values()} and r[1] == by[t1].cfa.name]
    assert len(mine) == 2 and all(r[10] == "Received" for r in mine)
    # scope: a user of another depot sees none of it
    save_user(admin, db, "p7.other", "CFA_MANAGER", cfas=["RPR"], reason="test", companies=None)
    url = "/reports/garage-pawati?date_from=2020-01-01&date_to=2099-12-31"
    assert a["invoice_no"] in admin.get(url).text
    seen = WebClient("p7.other", PW).get(url)
    assert seen.status_code == 200 and a["invoice_no"] not in seen.text and b["invoice_no"] not in seen.text
    book_tr.uses_lr_book = False
    db.commit()


def test_gate_exception_report_lists_who_allowed_it_and_why(db, world):  # noqa: F811
    api = _token_client("admin")
    admin = WebClient("admin")
    inv = _inv(api, "P3-TOWN", 4, cfa="BSP")
    from tests.test_v17_phase3 import _gatepass
    sid = _slip_of(_gatepass(admin, [inv], mode="SELF"))
    codes = _codes(db, sid)
    admin.post(f"/gatepass/{sid}/labels/confirm", data={"case_ids": ",".join(
        str(i) for i in db.scalars(select(DispatchCase.id).where(DispatchCase.slip_id == sid))), "result": "PRINTED"})
    admin.post(f"/gatepass/{sid}/print", data={"size": "A5", "mode": "pdf"})
    _scan(WebClient("admin"), codes[0], sid)
    admin.post(f"/gatepass/{sid}/gate-exception", data={"reason": "three cartons damaged, kept for the claim"})
    db.expire_all()
    no = db.get(DispatchSlip, sid).dispatch_no
    rows = _csv(admin, "gate-exceptions")
    row = next(r for r in rows if no in r)
    assert row[3] == "4" and row[4] == "3" and "damaged" in row[-1]
