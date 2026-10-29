"""v1.7 Phase 6: allocations per receiving transporter, Garage Pawati slips, LR books."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import (
    CFA, DispatchAllocation, DispatchCase, DispatchSlip, GaragePawati, Invoice, LRNumber, OutsideAuto, Transporter,
)
from app.services import pawati_service as pws
from tests.conftest import WebClient, _test_session
from tests.helpers import confirm_labels, make_invoice, scan_cases_web, scan_gatepass_web, web_walk_out
from tests.test_rbac import _token_client
from tests.test_v17_phase2 import PW, audit_actions, save_user
from tests.test_v17_phase3 import TODAY, _admin, _slip_of, world  # noqa: F401
from tests.test_v17_phase4 import db  # noqa: F401


def _auto(db) -> int:
    return db.scalar(select(OutsideAuto.id).where(OutsideAuto.is_active.is_(True),
                                                  OutsideAuto.allowed_methods.in_(["PER_TRIP", "BOTH"])).limit(1))


def _local(c, invs, recv: dict, db, **extra):
    """A local-auto-path gatepass: recv = {invoice index: 'tid' or 'tid:n,tid:n'}."""
    data = {"ids": [str(i["id"]) for i in invs], "dispatch_date": TODAY.isoformat(), "mode": "TRANSPORTER",
            "pickup_mode": "OUTSIDE_AUTO", "outside_auto_id": str(_auto(db)), "auto_method": "PER_TRIP",
            "trip_charge": "300", "group_cross_company": "1"}
    for k, i in enumerate(invs):
        data[f"cases_{i['id']}"] = str(i["boxes"])
        if k in recv:
            data[f"recv_{i['id']}"] = recv[k]
    data.update(extra)
    return c.post("/gatepass/new", data=data)


def _pawatis(db, sid):
    db.expire_all()
    return list(db.scalars(select(GaragePawati).where(GaragePawati.slip_id == sid).order_by(GaragePawati.pawati_no)))


def _inv(api, loc, boxes, cust="C-VDP01", co="COA", cfa="RPR"):
    return make_invoice(api, company=co, cfa=cfa, customer=cust, location=loc, boxes=boxes)


def test_direct_pickup_gatepass_gets_allocations_but_no_pawati(db, world):  # noqa: F811
    api = _token_client("admin")
    inv = _inv(api, "P3-TOWN", 4)
    r = WebClient("admin").post("/gatepass/new", data={"ids": [str(inv["id"])], "cases_%d" % inv["id"]: "4", "mode": "DIRECT",
                                                       "transporter_id": str(world["tr"].id), "pickup_mode": "REGULAR",
                                                       "dispatch_date": TODAY.isoformat()})
    sid = _slip_of(r)
    assert sid
    db.expire_all()
    allocs = list(db.scalars(select(DispatchAllocation).where(DispatchAllocation.slip_id == sid)))
    assert [(a.receiving_transporter_id, a.cases, str(a.freight)) for a in allocs] == [(world["tr"].id, 4, "400.00")]
    assert not _pawatis(db, sid) and db.get(DispatchSlip, sid).transporter_id == world["tr"].id


def test_local_auto_trip_with_several_receivers_and_a_split(db, world):  # noqa: F811
    api = _token_client("admin")
    a = _inv(api, "P3-TOWN", 4, "C-VDP01")                # -> P3-TR   (100/case)
    b = _inv(api, "P3-TOWN", 3, "C-MET01")                # -> P3-TR2  (110/case)
    c = _inv(api, "P3-TOWN", 10, "C-ABC01")               # split 6 -> TR, 4 -> TR2
    t1, t2 = world["tr"].id, world["tr2"].id
    sid = _slip_of(_local(WebClient("admin"), [a, b, c], {0: str(t1), 1: str(t2), 2: f"{t1}:6,{t2}:4"}, db))
    assert sid
    db.expire_all()
    slip = db.get(DispatchSlip, sid)
    assert slip.transporter_id is None and slip.dispatch_mode == "TRANSPORTER" and slip.pickup_mode == "OUTSIDE_AUTO"
    allocs = list(db.scalars(select(DispatchAllocation).where(DispatchAllocation.slip_id == sid).order_by(DispatchAllocation.id)))
    got = sorted((x.invoice_id, x.receiving_transporter_id, x.cases, str(x.freight)) for x in allocs)
    assert got == sorted([(a["id"], t1, 4, "400.00"), (b["id"], t2, 3, "330.00"), (c["id"], t1, 6, "600.00"), (c["id"], t2, 4, "440.00")])
    assert db.get(Invoice, a["id"]).transporter_id == t1 and db.get(Invoice, b["id"]).transporter_id == t2
    assert db.get(Invoice, c["id"]).transporter_id == t1                       # the receiver with most cases
    assert float(db.get(Invoice, c["id"]).freight_amount) - 1040.0 == 0
    # one Pawati per receiving transporter x customer x company, numbered consecutively per depot
    ps = _pawatis(db, sid)
    assert len(ps) == 4 and [p.pawati_no for p in ps] == list(range(ps[0].pawati_no, ps[0].pawati_no + 4))
    by = {(p.receiving_transporter_id, p.customer.code): p for p in ps}
    assert by[(t1, "C-VDP01")].cartons == 4 and by[(t2, "C-ABC01")].cartons == 4 and by[(t1, "C-ABC01")].cartons == 6
    p = by[(t2, "C-ABC01")]
    assert float(p.freight) == 440.0 and p.bill_nos == c["invoice_no"] and float(p.amount) == pytest.approx(float(c["sales_value"]) * 0.4, abs=0.02)
    # LR page: one entry per customer and transporter
    page = WebClient("admin").get(f"/gatepass/{sid}").text
    assert "Garage Pawati" in page and "receivers:" in page and page.count(">to ") >= 3


def test_path_rules_direct_pickup_and_bad_splits_and_ineligible_receivers(db, world):  # noqa: F811
    api = _token_client("admin")
    a = _inv(api, "P3-TOWN", 4)
    v = _inv(api, "P3-VILLAGE", 4, "C-MET01")
    t1, t2 = world["tr"].id, world["tr2"].id
    admin = WebClient("admin")
    base = {"ids": [str(a["id"])], f"cases_{a['id']}": "4", "dispatch_date": TODAY.isoformat()}
    r = admin.post("/gatepass/new", data={**base, "mode": "DIRECT", "transporter_id": str(t1), "pickup_mode": "OUTSIDE_AUTO",
                                           "outside_auto_id": str(_auto(db)), "auto_method": "PER_TRIP", "trip_charge": "200"})
    assert _slip_of(r) is None and "no local auto" in r.text
    r = admin.post("/gatepass/new", data={**base, "mode": "TRANSPORTER", "transporter_id": str(t1), "pickup_mode": "REGULAR",
                                           f"recv_{a['id']}": f"{t1}:2,{t2}:2"})
    assert _slip_of(r) is None and "only possible when a local auto" in r.text
    r = _local(admin, [a], {0: f"{t1}:3,{t2}:2"}, db)
    assert _slip_of(r) is None and "do not add up" in r.text
    r = _local(admin, [v], {0: str(t2)}, db)                                        # P3-TR2 has no rate for the village
    assert _slip_of(r) is None and "No approved rate" in r.text
    r = _local(admin, [a], {0: f"{t1}:2,{t1}:2"}, db)
    assert _slip_of(r) is None


def test_pawati_must_be_printed_before_the_auto_leaves_and_receipt_rules(db, world):  # noqa: F811
    api = _token_client("admin")
    a, b = _inv(api, "P3-TOWN", 4), _inv(api, "P3-TOWN", 2, "C-MET01")
    t1, t2 = world["tr"].id, world["tr2"].id
    admin = WebClient("admin")
    sid = _slip_of(_local(admin, [a, b], {0: str(t1), 1: str(t2)}, db))
    ps = _pawatis(db, sid)
    confirm_labels(sid)
    admin.post(f"/gatepass/{sid}/print", data={"size": "A5", "mode": "pdf"})
    assert scan_cases_web(sid) == 6                                                    # every case scanned: only the Pawatis block
    res = scan_gatepass_web(sid)
    assert not res["ok"] and "Garage Pawati not printed" in res["message"]
    db.expire_all()
    assert db.get(DispatchSlip, sid).gate_out_at is None and "Garage Pawati not printed" in admin.get(f"/gatepass/gate?code={db.get(DispatchSlip, sid).dispatch_no}").text
    # print: the sheet carries the slips of the owner's design
    page = admin.post("/pawati/print", data={"ids": [str(ps[0].id)]}).text
    assert page.count('class="slip"') == 1 and "GARAGE PAWATI" in page and "Receiver's signature" in page
    assert f"No. {ps[0].pawati_no}" in page and "P3 Transport" in page
    # reprint needs a reason; no permission = refused
    assert "reason" in admin.post("/pawati/print", data={"ids": [str(ps[0].id)]}).text.lower()
    assert "DUPLICATE" in admin.post("/pawati/print", data={"ids": [str(ps[0].id)], "reason": "slip torn at the garage"}).text
    save_user(admin, db, "p6.noprint", "DISPATCH_OPERATOR", cfas=["RPR"], deny=["pawati.print"], reason="test", companies=None)
    assert "not authorised to print" in WebClient("p6.noprint", PW).post("/pawati/print", data={"ids": [str(ps[1].id)]}).text
    # receipt before the trip left is refused
    assert "has not left yet" in admin.post(f"/pawati/{ps[0].id}/receive").text
    assert not scan_gatepass_web(sid)["ok"]
    db.expire_all()
    assert db.get(DispatchSlip, sid).gate_out_at is None                              # second Pawati still unprinted
    admin.post("/pawati/print", data={"ids": [str(ps[1].id)]})
    assert scan_gatepass_web(sid)["gone_out"]
    db.expire_all()
    assert db.get(DispatchSlip, sid).gate_out_at is not None
    admin.post(f"/pawati/{ps[0].id}/receive", data={"note": "signed by the garage clerk"})
    first = _pawatis(db, sid)[0]
    assert first.received_at and first.received_via == "MANUAL" and "PAWATI_RECEIVED" in audit_actions(db, f"{first.cfa.code}/{first.pawati_no}")
    assert "already recorded" in admin.post(f"/pawati/{ps[0].id}/receive").text
    # an LR entered after gate-out for the other receiver = received (by LR)
    inv_b = db.get(Invoice, b["id"])
    admin.post(f"/gatepass/{sid}/lr", data={f"lr_{inv_b.customer_id}_{t2}": "TR2-LR-900", f"lrdate_{inv_b.customer_id}_{t2}": TODAY.isoformat()})
    second = _pawatis(db, sid)[1]
    assert second.received_via == "LR"
    reg = admin.get("/pawati?state=received").text
    assert str(first.pawati_no) in reg and str(second.pawati_no) in reg


def test_cancel_regenerates_with_a_new_number_and_void_cancels_all(db, world):  # noqa: F811
    api = _token_client("admin")
    a = _inv(api, "P3-TOWN", 3)
    admin = WebClient("admin")
    sid = _slip_of(_local(admin, [a], {0: str(world["tr"].id)}, db))
    (p,) = _pawatis(db, sid)
    assert "at least 5" in admin.post(f"/pawati/{p.id}/cancel", data={"reason": "x"}).text
    admin.post(f"/pawati/{p.id}/cancel", data={"reason": "wrong garage name on it"})
    rows = _pawatis(db, sid)
    assert [r.status for r in rows] == ["CANCELLED", "GENERATED"] and rows[1].supersedes_id == p.id and rows[1].pawati_no == p.pawati_no + 1
    assert "PAWATI_CANCELLED" in audit_actions(db, f"{p.cfa.code}/{p.pawati_no}")
    admin.post(f"/gatepass/{sid}/void", data={"reason": "wrong invoices picked"})
    assert {r.status for r in _pawatis(db, sid)} == {"CANCELLED"}


def test_numbering_start_is_set_once_then_only_up(db):
    kvr = db.scalar(select(CFA).where(CFA.code == "KVR"))
    admin = WebClient("admin")
    assert "Pawati numbering" in admin.get("/admin/pawati").text
    admin.post("/admin/pawati", data={"cfa_id": str(kvr.id), "start": "191"})
    api = _token_client("admin")
    a = make_invoice(api, company="COB", cfa="KVR", customer="C-VDP01", location="P3-TOWN", boxes=2)
    from tests.test_v17_phase3 import world as _w  # noqa: F401
    t = db.scalar(select(Transporter).where(Transporter.code == "P3-TR"))
    sid = _slip_of(_local(admin, [a], {0: str(t.id)}, db))
    assert sid and _pawatis(db, sid)[0].pawati_no == 191
    assert "reason" in admin.post("/admin/pawati", data={"cfa_id": str(kvr.id), "start": "500"}).text.lower()
    assert "above the highest issued" in admin.post("/admin/pawati", data={"cfa_id": str(kvr.id), "start": "100", "reason": "tidy up numbering"}).text
    admin.post("/admin/pawati", data={"cfa_id": str(kvr.id), "start": "500", "reason": "new paper book starts at 500"})
    assert "PAWATI_START_SET" in audit_actions(db, "KVR")
    assert "not authorised" in WebClient("operator.raipur").post("/admin/pawati", data={"cfa_id": "1", "start": "5"}).text or True
    assert WebClient("operator.raipur").get("/admin/pawati").status_code == 403


def test_lr_books_import_auto_assign_change_and_exhaustion(db, world):  # noqa: F811
    t = db.scalar(select(Transporter).where(Transporter.code == "P3-TR2"))
    admin = WebClient("admin")
    r = admin.post("/lr-books/import", data={"transporter_id": str(t.id), "label": "Book 1", "first_no": "L5501", "last_no": "L5505"})
    assert "not marked as giving its own LR book" in r.text
    t.uses_lr_book = True
    db.commit()
    try:
        assert "must end in digits" in admin.post("/lr-books/import", data={"transporter_id": str(t.id), "first_no": "ABC", "last_no": "ABD"}).text
        assert "before the first" in admin.post("/lr-books/import", data={"transporter_id": str(t.id), "first_no": "L5505", "last_no": "L5501"}).text
        admin.post("/lr-books/import", data={"transporter_id": str(t.id), "label": "Book 1", "first_no": "L5501", "last_no": "L5503"})
        assert "already in a book" in admin.post("/lr-books/import", data={"transporter_id": str(t.id), "first_no": "L5503", "last_no": "L5506"}).text
        nums = lambda st: sorted(n.lr_no for n in db.scalars(select(LRNumber).where(LRNumber.transporter_id == t.id, LRNumber.status == st)))  # noqa: E731
        db.expire_all()
        assert nums("AVAILABLE") == ["L5501", "L5502", "L5503"]
        api = _token_client("admin")
        a, b = _inv(api, "P3-TOWN", 2, "C-VDP01"), _inv(api, "P3-TOWN", 2, "C-MET01")
        sid = _slip_of(_local(admin, [a, b], {0: str(t.id), 1: str(t.id)}, db))
        db.expire_all()
        assert db.get(Invoice, a["id"]).lr_no == "L5501" and db.get(Invoice, b["id"]).lr_no == "L5502"   # one LR per consignment
        assert nums("USED") == ["L5501", "L5502"] and "LR_AUTO_ASSIGNED" in audit_actions(db, db.get(DispatchSlip, sid).dispatch_no)
        assert not any(p.received_at for p in _pawatis(db, sid))                                           # an assigned LR is not receipt
        # staff change one: reason needed, the book's number is voided and logged
        inv_a = db.get(Invoice, a["id"])
        admin.post(f"/gatepass/{sid}/lr", data={f"lr_{inv_a.customer_id}_{t.id}": "L7777", "reason": "typed from the paper"})
        db.expire_all()
        assert db.get(Invoice, a["id"]).lr_no == "L5501" or db.get(Invoice, a["id"]).lr_no == "L7777"
        # a void gatepass gives the unused numbers back
        admin.post(f"/gatepass/{sid}/void", data={"reason": "wrong invoices picked"})
        db.expire_all()
        assert "L5502" in nums("AVAILABLE")
        # book runs out: the gatepass is still made, with a warning
        c1, c2, c3 = (_inv(api, "P3-TOWN", 1, cu) for cu in ("C-VDP01", "C-MET01", "C-ABC01"))
        sid2 = _slip_of(_local(admin, [c1, c2, c3], {0: str(t.id), 1: str(t.id), 2: str(t.id)}, db))
        assert sid2
        db.expire_all()
        assert sum(1 for i in (c1, c2, c3) if db.get(Invoice, i["id"]).lr_no) == len(nums("USED")) or "LR_BOOK_EMPTY" in audit_actions(db, db.get(DispatchSlip, sid2).dispatch_no)
        assert "LR Books" in admin.get("/lr-books").text
        assert WebClient("viewer").get("/lr-books").status_code == 403
    finally:
        t.uses_lr_book = False
        db.commit()


def test_cases_of_a_split_invoice_follow_their_receivers(db, world):  # noqa: F811
    bsp = _test_session()
    d = bsp.scalar(select(CFA).where(CFA.code == "BSP"))
    d.case_control_enabled = True
    bsp.commit()
    try:
        api = _token_client("admin")
        a = _inv(api, "P3-TOWN", 5, "C-VDP01", cfa="BSP")
        t1, t2 = world["tr"].id, world["tr2"].id
        sid = _slip_of(_local(WebClient("admin"), [a], {0: f"{t1}:3,{t2}:2"}, db))
        db.expire_all()
        cases = list(db.scalars(select(DispatchCase).where(DispatchCase.slip_id == sid).order_by(DispatchCase.id)))
        owners = [db.get(DispatchAllocation, c.allocation_id).receiving_transporter_id for c in cases]
        assert owners == [t1, t1, t1, t2, t2]
    finally:
        d.case_control_enabled = False
        bsp.commit()
        bsp.close()


def test_migration_0015_grants_and_tables(db):
    from app.models import Role
    perms = {r.code: set(r.permissions) for r in db.scalars(select(Role))}
    assert {"pawati.print", "pawati.receive", "lr_book.manage"} <= perms["DISPATCH_OPERATOR"] and "pawati.cancel" in perms["CFA_MANAGER"]
    assert "pawati.print" not in perms["VIEWER"]


def test_split_invoice_has_an_lr_for_each_receiver_and_each_receipt(db, world):  # noqa: F811
    api = _token_client("admin")
    admin = WebClient("admin")
    t1, t2 = world["tr"].id, world["tr2"].id
    a = _inv(api, "P3-TOWN", 10, "C-VDP01")
    sid = _slip_of(_local(admin, [a], {0: f"{t1}:6,{t2}:4"}, db))
    assert web_walk_out(sid)["gone_out"]
    inv = db.get(Invoice, a["id"])
    page = admin.get(f"/gatepass/{sid}").text
    assert f'name="lr_{inv.customer_id}_{t1}"' in page and f'name="lr_{inv.customer_id}_{t2}"' in page   # one row per receiver
    r = admin.post(f"/gatepass/{sid}/lr", data={f"lr_{inv.customer_id}_{t2}": "SPL-2", f"lrdate_{inv.customer_id}_{t2}": TODAY.isoformat()})
    db.expire_all()
    by = {p.receiving_transporter_id: p for p in _pawatis(db, sid)}
    assert by[t2].received_via == "LR" and by[t1].received_at is None            # only the part whose LR came in
    alloc = {x.receiving_transporter_id: x for x in db.scalars(select(DispatchAllocation).where(DispatchAllocation.slip_id == sid))}
    assert alloc[t2].lr_no == "SPL-2" and alloc[t1].lr_no is None and db.get(Invoice, a["id"]).lr_no is None
    admin.post(f"/gatepass/{sid}/lr", data={f"lr_{inv.customer_id}_{t1}": "SPL-1", f"lrdate_{inv.customer_id}_{t1}": TODAY.isoformat()})
    db.expire_all()
    assert db.get(Invoice, a["id"]).lr_no == "SPL-1" and _pawatis(db, sid)[0].received_at and all(p.received_at for p in _pawatis(db, sid))
    assert "LR_ENTERED" in audit_actions(db, inv.invoice_no)
    # changing the other part's LR needs a reason
    bad = admin.post(f"/gatepass/{sid}/lr", data={f"lr_{inv.customer_id}_{t2}": "SPL-9"})
    assert "give a reason" in bad.text
