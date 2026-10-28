"""v1.7 Phase 5: the guard scans every case; a case-control gatepass cannot leave until all are scanned (or an authorised
exception) - on every completion path; every scan is recorded."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import CFA, CaseScan, CaseStatus, DispatchCase, DispatchSlip, Invoice
from app.services import case_service as cs
from tests.conftest import WebClient, _test_session
from tests.helpers import make_invoice
from tests.test_rbac import _token_client
from tests.test_v17_phase2 import PW, audit_actions, save_user
from tests.test_v17_phase3 import _gatepass, _slip_of
from tests.test_v17_phase4 import bsp_on, db  # noqa: F401


def _make(bsp_on, sizes=((3, "C-VDP01"),), mode="SELF"):  # noqa: F811
    api = _token_client("admin")
    invs = [make_invoice(api, company="COA", cfa="BSP", customer=cu, boxes=n) for n, cu in sizes]
    sid = _slip_of(_gatepass(WebClient("admin"), invs, mode=mode))
    assert sid
    return sid, invs


def _codes(db, sid):
    db.expire_all()
    return [c.case_code for c in db.scalars(select(DispatchCase).where(DispatchCase.slip_id == sid).order_by(DispatchCase.id))]


def _scan(c, code, sid=None):
    data = {"code": code}
    if sid:
        data["slip_id"] = str(sid)
    return c.post("/gatepass/scan-case", data=data).json()


def _print_all(db, sid):
    WebClient("admin").post(f"/gatepass/{sid}/print", data={"size": "A5", "mode": "pdf"})   # the gatepass must be printed too
    WebClient("admin").post(f"/gatepass/{sid}/labels/confirm", data={
        "case_ids": ",".join(str(i) for i in db.scalars(select(DispatchCase.id).where(DispatchCase.slip_id == sid))), "result": "PRINTED"})


def test_scan_rules_and_every_scan_is_recorded(db, bsp_on):
    sid, _ = _make(bsp_on, sizes=((3, "C-VDP01"), (2, "C-ABC01")))
    sid_other, _ = _make(bsp_on, sizes=((2, "C-VDP01"),))
    codes, other = _codes(db, sid), _codes(db, sid_other)
    g = WebClient("operator.bilaspur")
    r = _scan(g, codes[0], sid)
    assert r["ok"] and r["scanned"] == 1 and r["total"] == 5 and not r["cleared"]
    assert sorted((x["scanned"], x["total"]) for x in r["per_invoice"]) == [(0, 2), (1, 3)]
    dup = _scan(g, codes[0], sid)
    assert dup["result"] == "DUPLICATE" and not dup["ok"] and dup["scanned"] == 1          # the same case twice counts once
    assert _scan(g, other[0], sid)["result"] == "WRONG_GATEPASS"
    assert _scan(g, "12345", sid)["result"] == "INVALID"
    bad = codes[1][:9] + str((int(codes[1][9]) + 1) % 10)
    assert _scan(g, bad, sid)["result"] == "INVALID"
    assert _scan(g, cs.make_code(9999999), sid)["result"] == "UNKNOWN"
    assert _scan(g, cs.make_code(5, training=True), sid)["result"] == "TRAINING"
    db.expire_all()
    assert db.get(DispatchSlip, sid).verified_cases == 1
    results = [x.result for x in db.scalars(select(CaseScan).where(CaseScan.slip_id == sid).order_by(CaseScan.id))]
    assert results[:3] == ["OK", "DUPLICATE", "WRONG_GATEPASS"]
    assert db.query(CaseScan).filter(CaseScan.scanned_code == "12345").count() >= 1        # even garbage is on record
    # a void label stops
    WebClient("admin").post(f"/gatepass/{sid_other}/void", data={"reason": "wrong goods loaded"})
    assert _scan(g, other[1])["result"] == "VOID"
    # scanning a case without naming the gatepass finds it
    r = _scan(g, codes[1])
    assert r["ok"] and r["dispatch_no"] == db.get(DispatchSlip, sid).dispatch_no and r["scanned"] == 2


def test_gate_out_is_blocked_on_every_path_until_all_cases_are_scanned(db, bsp_on):
    sid, (inv,) = _make(bsp_on, sizes=((3, "C-VDP01"),))
    _print_all(db, sid)
    admin, api = WebClient("admin"), _token_client("admin")
    iid = inv["id"]

    def status():
        db.expire_all()
        return db.get(Invoice, iid).status, db.get(DispatchSlip, sid).gate_out_at

    admin.post(f"/gatepass/{sid}/gate-out")                                         # 1 the gatepass screen
    admin.post(f"/dispatch/{iid}/complete")                                          # 2 the one-invoice screen
    admin.post("/dispatch/bulk/complete", data={"ids": [str(iid)]})                  # 3 bulk
    r = api.post(f"/api/v1/invoices/{iid}/complete")                                 # 4 the API
    assert r.status_code == 400 and "not scanned" in r.text
    assert status()[0] != "DISPATCHED" and status()[1] is None
    assert "GATE_OUT_BLOCKED" in audit_actions(db, db.get(DispatchSlip, sid).dispatch_no)
    codes = _codes(db, sid)
    g = WebClient("operator.bilaspur")
    for c in codes[:2]:
        _scan(g, c, sid)
    admin.post(f"/gatepass/{sid}/gate-out")
    assert status()[1] is None                                                       # 2 of 3: still blocked
    last = _scan(g, codes[2], sid)
    assert last["complete"] and last["cleared"]
    admin.post(f"/gatepass/{sid}/gate-out")
    st, out = status()
    assert st == "DISPATCHED" and out is not None
    assert {c.status for c in db.scalars(select(DispatchCase).where(DispatchCase.slip_id == sid))} == {CaseStatus.DISPATCHED}
    assert _scan(g, codes[0], sid)["result"] == "ALREADY_OUT"


def test_exception_needs_permission_reason_and_leaves_a_trail(db, bsp_on):
    sid, _ = _make(bsp_on, sizes=((4, "C-VDP01"),))
    _print_all(db, sid)
    codes = _codes(db, sid)
    g = WebClient("operator.bilaspur")
    _scan(g, codes[0], sid)
    _scan(g, codes[1], sid)
    admin = WebClient("admin")
    save_user(admin, db, "p5.noexc", "DISPATCH_OPERATOR", cfas=["BSP"], deny=["gate.exception"], reason="test", companies=None)
    assert "not authorised to record a gate exception" in WebClient("p5.noexc", PW).post(f"/gatepass/{sid}/gate-exception", data={"reason": "box damaged in the rain"}).text
    assert "at least 5 characters" in admin.post(f"/gatepass/{sid}/gate-exception", data={"reason": "no"}).text
    db.expire_all()
    assert db.get(DispatchSlip, sid).gate_exception_reason is None
    admin.post(f"/gatepass/{sid}/gate-exception", data={"reason": "2 cartons damaged, retained for claim"})
    db.expire_all()
    slip = db.get(DispatchSlip, sid)
    assert slip.gate_exception_reason and slip.gate_exception_by_id
    states = sorted(c.status for c in db.scalars(select(DispatchCase).where(DispatchCase.slip_id == sid)))
    assert states == [CaseStatus.MISSING, CaseStatus.MISSING, CaseStatus.SCANNED, CaseStatus.SCANNED]
    assert "GATE_EXCEPTION" in audit_actions(db, slip.dispatch_no)
    admin.post(f"/gatepass/{sid}/gate-out")
    db.expire_all()
    assert db.get(DispatchSlip, sid).gate_out_at is not None
    # nothing missing -> nothing to except
    sid2, _ = _make(bsp_on, sizes=((1, "C-VDP01"),))
    _scan(g, _codes(db, sid2)[0], sid2)
    assert "no exception needed" in admin.post(f"/gatepass/{sid2}/gate-exception", data={"reason": "not needed at all"}).text


def test_gate_screen_has_the_scan_loop_and_a_case_code_opens_it(db, bsp_on):
    sid, _ = _make(bsp_on, sizes=((2, "C-VDP01"),))
    slip = db.get(DispatchSlip, sid)
    code = _codes(db, sid)[0]
    page = WebClient("operator.bilaspur").get(f"/gatepass/gate?code={code}").text
    assert slip.dispatch_no in page and 'id="case-code"' in page and f'data-auto="{code}"' in page
    assert "STOP" in page and "0 / 2" in page                                    # not cleared until scanned
    # the confirm button is not offered while cases are unscanned
    assert 'name="back" value="gate"' not in page


def test_old_gatepasses_and_other_depots_still_leave_without_scanning(db):
    api = _token_client("admin")
    inv = make_invoice(api, company="COA", cfa="RPR", customer="C-VDP01", boxes=2)
    sid = _slip_of(_gatepass(WebClient("admin"), [inv], mode="SELF"))
    WebClient("admin").post(f"/gatepass/{sid}/print", data={"size": "A5", "mode": "pdf"})
    WebClient("admin").post(f"/gatepass/{sid}/gate-out")
    db.expire_all()
    assert db.get(DispatchSlip, sid).gate_out_at is not None and db.get(DispatchSlip, sid).case_control == "LEGACY"


def test_migration_0014_gives_gate_exception_to_roles_that_could_void(db):
    from app.models import Role
    perms = {r.code: set(r.permissions) for r in db.scalars(select(Role))}
    assert "gate.exception" in perms["CFA_MANAGER"] and "gate.exception" in perms["CHECKER"]
    assert "gate.exception" not in perms["DISPATCH_OPERATOR"] and "gate.exception" not in perms["VIEWER"]
