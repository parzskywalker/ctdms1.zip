"""v1.7.2: case control is mandatory at every depot, goods are marked gone out ONLY by the gate staff's scan (every case, then
the gatepass), an old gatepass still at the depot gets its case labels, and the demo logins come from one list."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import select

from app import demo_users, seed
from app.models import CFA, DispatchCase, DispatchSlip, Invoice, Role, SlipPrintLog, User
from app.security.auth import verify_password
from app.security.permissions import DEFAULT_ROLES, PERMISSION_GROUPS, PERMISSIONS
from app.services import case_service as cs
from app.services import dispatch_service as ds
from app.services import gate_service
from app.services.errors import BusinessError
from tests.conftest import WebClient, _test_session
from tests.helpers import make_invoice, walk_out, web_walk_out
from tests.test_rbac import _token_client
from tests.test_v17_phase2 import audit_actions
from tests.test_v17_phase3 import _gatepass, _slip_of, world  # noqa: F401
from tests.test_v17_phase4 import db  # noqa: F401
from tests.test_v17_phase6 import _inv, _local


def _user(db, name):
    return db.scalar(select(User).where(User.username == name))


def _self_gatepass(cfa="RPR", boxes=3, customer="C-VDP01"):
    inv = make_invoice(_token_client("admin"), company="COA", cfa=cfa, customer=customer, boxes=boxes)
    sid = _slip_of(_gatepass(WebClient("admin"), [inv], mode="SELF"))
    assert sid
    return sid, inv


# ----------------------------------------------------------------------------- case control is mandatory
def test_every_depot_makes_cases_and_the_depot_switch_is_gone(db):
    for cfa in ("RPR", "BSP"):  # Raipur had case control OFF before v1.7.2
        sid, _ = _self_gatepass(cfa)
        db.expire_all()
        slip = db.get(DispatchSlip, sid)
        assert slip.case_control == "REQUIRED" and len(cs.cases_of(db, slip)) == slip.total_cases == 3
    assert all(d.case_control_enabled for d in db.scalars(select(CFA)))
    assert 'name="case_control_enabled"' not in WebClient("admin").get(
        f"/masters/depots/{db.scalar(select(CFA.id).where(CFA.code == 'RPR'))}").text
    assert not hasattr(cs, "enabled_for")


def test_labels_first_then_gatepass_then_only_the_gate_scan_sends_it_out(db):
    sid, _ = _self_gatepass("RPR", 2)
    adm, gate = _user(db, "admin"), _user(db, "gate.raipur")
    slip = db.get(DispatchSlip, sid)
    with pytest.raises(BusinessError, match="Print the case labels first"):
        ds.print_gatepass(db, adm, slip, "PRINT_A5")
    gate_service.print_labels(db, adm, slip)
    ds.print_gatepass(db, adm, slip, "PRINT_A5")
    db.commit()
    with pytest.raises(BusinessError, match="not scanned at the gate"):
        ds.gate_scan_out(db, gate, slip)                       # the gatepass alone does nothing
    db.rollback()
    slip = db.get(DispatchSlip, sid)
    assert gate_service.scan_cases(db, gate, slip) == 2
    assert ds.gate_scan_out(db, gate, slip) == 1
    db.commit()
    assert slip.gate_out_at and slip.gate_out_by.username == "gate.raipur"


# ----------------------------------------------------------------------------- no gone-out path anywhere
def test_there_is_no_gone_out_function_permission_route_or_api(db):
    for name in ("complete", "gate_out", "generate_slip", "slip_blockers"):
        assert not hasattr(ds, name), name
    assert "dispatch.complete" not in PERMISSIONS
    assert all("dispatch.complete" not in perms for _g, perms in PERMISSION_GROUPS)
    assert all("dispatch.complete" not in (r["permissions"]) for r in DEFAULT_ROLES.values())
    assert all("dispatch.complete" not in (r.permissions or []) for r in db.scalars(select(Role)))
    import inspect
    assert "legacy" not in inspect.signature(ds.plan_gatepass).parameters
    assert "legacy" not in inspect.signature(ds.create_gatepass).parameters

    sid, inv = _self_gatepass("RPR", 2)
    walk_ready = gate_service.prepare(db, _user(db, "admin"), db.get(DispatchSlip, sid))
    db.commit()
    assert walk_ready.print_count == 1
    adm = WebClient("admin")
    for method, url in (("post", f"/gatepass/{sid}/gate-out"), ("post", f"/dispatch/{inv['id']}/complete"),
                        ("post", f"/dispatch/{inv['id']}/generate"), ("post", f"/dispatch/{inv['id']}/print"),
                        ("post", f"/dispatch/{inv['id']}/save"), ("post", "/dispatch/bulk/complete"),
                        ("post", "/dispatch/bulk/apply"), ("post", "/dispatch/bulk/print"),
                        ("post", "/dispatch/bulk/prepare"), ("get", "/dispatch/bulk/prepare")):
        r = getattr(adm, method)(url) if method == "get" else adm.post(url, data={"ids": [str(inv["id"])]})
        assert r.status_code in (404, 405), (url, r.status_code)
    db.expire_all()
    assert db.get(DispatchSlip, sid).gate_out_at is None

    api = _token_client("admin")
    # the API print is still there, but it too prints the case labels first (it reaches _print_slip like the screen does)
    sid2, inv2 = _self_gatepass("RPR", 2)
    r = api.post(f"/api/v1/invoices/{inv2['id']}/print")
    assert r.status_code == 400 and "Print the case labels first" in r.text, (r.status_code, r.text)
    for path in ("complete", "generate-slip"):
        r = api.post(f"/api/v1/invoices/{inv['id']}/{path}")
        assert r.status_code == 400, (path, r.status_code, r.text)
    assert "gate staff" in api.post(f"/api/v1/invoices/{inv['id']}/complete").text
    db.expire_all()
    assert db.get(Invoice, inv["id"]).status != "DISPATCHED"


def test_no_screen_offers_a_gone_out_button(db):
    sid, inv = _self_gatepass("RPR", 2)
    gate_service.prepare(db, _user(db, "admin"), db.get(DispatchSlip, sid))
    db.commit()
    no = ("Record gate-out", "Confirm gate-out", "/gate-out", "Complete Dispatch", "Complete selected", "Bulk dispatch",
          "/dispatch/bulk", "/complete", "data-key=\"F9\"", "data-key=F9")
    for who in ("admin", "cfa.raipur", "operator.raipur"):
        w = WebClient(who)
        pages = [w.get(f"/gatepass/{sid}").text, w.get(f"/dispatch/{inv['id']}").text, w.get("/dispatch/pending").text,
                 w.get("/gatepass/gate").text, w.get(f"/gatepass/gate?code={ds.slip_invoices(db, db.get(DispatchSlip, sid))[0].dispatch_no}").text]
        for html in pages:
            for bad in no:
                assert bad not in html, (who, bad)
    page = WebClient("operator.raipur").get("/dispatch/pending").text
    assert 'action="/gatepass/prepare"' in page            # even Enter in the list form cannot reach a retired route
    entry = WebClient("operator.raipur").get(f"/dispatch/{inv['id']}").text
    assert f"/gatepass/{sid}" in entry and "Gone out is recorded only when the gate staff scan" in entry


def test_only_the_gate_scan_marks_a_trip_gone_out_and_not_every_role_can_scan(db):
    sid, _ = _self_gatepass("RPR", 2)
    slip = db.get(DispatchSlip, sid)
    gate_service.prepare(db, _user(db, "admin"), slip)
    gate_service.scan_cases(db, _user(db, "gate.raipur"), slip)
    db.commit()
    for who in ("operator.raipur", "cfa.raipur", "checker", "viewer", "management", "accounts"):
        with pytest.raises(BusinessError, match="not authorised"):
            ds.gate_scan_out(db, _user(db, who), slip)
        db.rollback()
        slip = db.get(DispatchSlip, sid)
    out = WebClient("operator.raipur").post("/gatepass/scan-case", data={"code": slip.dispatch_no, "slip_id": str(sid)})
    assert out.status_code in (302, 303, 401, 403) or not out.json().get("ok")
    db.expire_all()
    assert db.get(DispatchSlip, sid).gate_out_at is None
    res = WebClient("gate.raipur").post("/gatepass/scan-case", data={"code": slip.dispatch_no, "slip_id": str(sid)}).json()
    assert res["ok"] and res["gone_out"]
    db.expire_all()
    assert db.get(DispatchSlip, sid).gate_out_at is not None


def test_a_manager_exception_still_needs_the_gatepass_scan(db):
    sid, _ = _self_gatepass("RPR", 3)
    slip = db.get(DispatchSlip, sid)
    gate_service.prepare(db, _user(db, "admin"), slip)
    gate = _user(db, "gate.raipur")
    first = cs.cases_of(db, slip)[0]
    assert cs.scan(db, gate, first.case_code, slip)["ok"]
    cs.record_exception(db, _user(db, "cfa.raipur"), slip, "Two cartons damaged in the godown")
    db.commit()
    assert slip.gate_out_at is None
    assert gate_service.scan_cases(db, gate, slip) == 0            # the missing cartons are NOT scanned back in
    assert {c.status for c in cs.cases_of(db, slip)} == {"SCANNED", "MISSING"}
    assert ds.gate_scan_out(db, gate, slip) == 1
    db.commit()
    assert slip.gate_out_at is not None and "GATE_EXCEPTION" in audit_actions(db, slip.dispatch_no)


# ----------------------------------------------------------------------------- an old gatepass gets its labels
def _make_legacy(db, world, *, printed=True):  # noqa: F811
    """The shape of the Guma screenshot (v1.7.1, depot switch off): a local-auto trip with two receiving transporters and
    invoices of two companies, already printed, Garage Pawatis made - and no case labels at all."""
    api = _token_client("admin")
    a = _inv(api, "P3-TOWN", 4, "C-VDP01", "COA")
    b = _inv(api, "P3-TOWN", 3, "C-MET01", "COB")
    sid = _slip_of(_local(WebClient("admin"), [a, b], {0: str(world["tr"].id), 1: str(world["tr2"].id)}, db))
    assert sid
    db.expire_all()
    slip = db.get(DispatchSlip, sid)
    for c in cs.cases_of(db, slip, include_void=True):
        db.delete(c)
    slip.case_control = "LEGACY"
    if printed:
        slip.print_count = 1
        for inv in ds.slip_invoices(db, slip):
            inv.status, inv.print_count = "SLIP_PRINTED", 1
    db.commit()
    return sid, [a, b]


def test_old_gatepass_gets_its_labels_prints_again_and_leaves_only_by_the_gate_scan(db, world):  # noqa: F811
    sid, invs = _make_legacy(db, world)
    slip = db.get(DispatchSlip, sid)
    assert slip.case_control == "LEGACY" and slip.print_count == 1 and not cs.cases_of(db, slip)
    assert slip.cross_company and ds.slip_invoices(db, slip)
    op, gate = _user(db, "operator.raipur"), _user(db, "gate.raipur")

    page = WebClient("operator.raipur").get(f"/gatepass/{sid}").text
    assert "Make the case labels for this gatepass" in page and f"/gatepass/{sid}/make-labels" in page
    assert "Record gate-out" not in page and "/gate-out" not in page
    assert "Case labels</a>" not in page                       # no barcode-less old labels while it is unconverted
    gp = WebClient("gate.raipur").get(f"/gatepass/gate?code={slip.dispatch_no}").text
    assert "no case labels yet" in gp and "Confirm gate-out" not in gp

    # nothing works until the labels exist
    with pytest.raises(BusinessError, match="no case labels yet"):
        ds.print_gatepass(db, op, slip, "PRINT_A5", "Printer jam - copy torn")
    with pytest.raises(BusinessError, match="no case labels yet"):
        ds.gate_scan_out(db, gate, slip)
    db.rollback()
    old_labels = WebClient("operator.raipur").get(f"/gatepass/{sid}/labels")           # no barcode-less old labels
    assert "no case labels yet" in old_labels.text and "<svg" not in old_labels.text

    # the operator (case.print) makes them from the screen
    r = WebClient("operator.raipur").post(f"/gatepass/{sid}/make-labels")
    assert r.status_code == 200
    db.expire_all()
    slip = db.get(DispatchSlip, sid)
    cases = cs.cases_of(db, slip)
    assert slip.case_control == "REQUIRED" and len(cases) == 7 == slip.total_cases
    assert {c.status for c in cases} == {"LABEL_PENDING"}
    assert "CASES_GENERATED_LEGACY" in audit_actions(db, slip.dispatch_no)
    assert {(c.seq_in_consignment, c.consignment_total) for c in cases} == (
        {(k, 4) for k in range(1, 5)} | {(k, 3) for k in range(1, 4)})        # "case k of N" per customer, not per trip
    with pytest.raises(BusinessError, match="already has its case labels"):
        cs.convert_legacy(db, op, slip)
    db.rollback()

    # labels printed, then a REPRINT of the gatepass is allowed (with a reason); it needs the reason
    slip = db.get(DispatchSlip, sid)
    with pytest.raises(BusinessError, match="Print the case labels first"):
        ds.print_gatepass(db, op, slip, "PRINT_A5", "Printer jam - copy torn")
    db.rollback()
    gate_service.print_labels(db, op, db.get(DispatchSlip, sid))
    db.commit()
    slip = db.get(DispatchSlip, sid)
    with pytest.raises(BusinessError, match="Reason is mandatory"):
        ds.print_gatepass(db, op, slip, "PRINT_A5")
    db.rollback()
    slip = db.get(DispatchSlip, sid)
    ds.print_gatepass(db, op, slip, "PRINT_A5", "Copy was torn at the gate")
    db.commit()
    assert db.scalar(select(SlipPrintLog.is_reprint).where(SlipPrintLog.slip_id == sid).order_by(SlipPrintLog.id.desc()))

    # the gate scan clears it: Pawatis first, then every case, then the gatepass
    slip = db.get(DispatchSlip, sid)
    with pytest.raises(BusinessError, match="not scanned at the gate"):
        ds.gate_scan_out(db, gate, slip)
    db.rollback()
    slip = db.get(DispatchSlip, sid)
    gate_service.scan_cases(db, gate, slip)
    with pytest.raises(BusinessError, match="Garage Pawati not printed"):
        ds.gate_scan_out(db, gate, slip)
    db.rollback()
    slip = db.get(DispatchSlip, sid)
    gate_service.print_pawatis(db, op, slip)
    gate_service.scan_cases(db, gate, slip)
    assert ds.gate_scan_out(db, gate, slip) == 2
    db.commit()
    assert slip.gate_out_at and {i.status for i in ds.slip_invoices(db, slip)} == {"DISPATCHED"}
    assert {c.status for c in cs.cases_of(db, slip)} == {"DISPATCHED"}


def test_convert_legacy_refusals_and_partly_sent_gatepasses(db, world):  # noqa: F811
    sid, invs = _make_legacy(db, world, printed=False)
    slip = db.get(DispatchSlip, sid)
    for who in ("viewer", "gate.raipur", "checker"):                    # no case.print
        with pytest.raises(BusinessError, match="not authorised"):
            cs.convert_legacy(db, _user(db, who), slip)
        db.rollback()
        slip = db.get(DispatchSlip, sid)
    assert WebClient("gate.raipur").post(f"/gatepass/{sid}/make-labels").status_code in (401, 403)
    # a v1.7.1 per-invoice Complete could leave one invoice already gone: only the cartons still here are labelled
    gone = ds.slip_invoices(db, slip)[0]
    gone.status = "DISPATCHED"
    db.commit()
    slip = db.get(DispatchSlip, sid)
    n = cs.convert_legacy(db, _user(db, "operator.raipur"), slip)
    db.commit()
    assert n == slip.total_cases - (gone.boxes or 0) and {c.invoice_id for c in cs.cases_of(db, slip)} == {
        i.id for i in ds.slip_invoices(db, slip) if i.id != gone.id}
    # void and gone-out gatepasses are not converted
    sid2, _ = _make_legacy(db, world, printed=False)
    s2 = db.get(DispatchSlip, sid2)
    s2.is_void = True
    db.commit()
    with pytest.raises(BusinessError, match="void"):
        cs.convert_legacy(db, _user(db, "operator.raipur"), db.get(DispatchSlip, sid2))
    db.rollback()
    s3 = db.get(DispatchSlip, sid2)
    s3.is_void = False
    s3.gate_out_at = s3.generated_at
    db.commit()
    with pytest.raises(BusinessError, match="already gone out"):
        cs.convert_legacy(db, _user(db, "operator.raipur"), db.get(DispatchSlip, sid2))
    db.rollback()


def test_a_gatepass_that_already_went_out_stays_history(db, world):  # noqa: F811
    sid, _ = _make_legacy(db, world)
    slip = db.get(DispatchSlip, sid)
    slip.gate_out_at = slip.generated_at
    for inv in ds.slip_invoices(db, slip):
        inv.status = "DISPATCHED"
    db.commit()
    page = WebClient("admin").get(f"/gatepass/{sid}").text
    assert "Make the case labels for this gatepass" not in page and "Gone out" in page
    assert cs.labels_pending(db, db.get(DispatchSlip, sid)) == 0 and not cs.needs_conversion(db.get(DispatchSlip, sid))


def test_web_walk_out_and_helper_agree(db):
    sid, _ = _self_gatepass("BSP", 2)
    res = web_walk_out(sid, desk="operator.bilaspur", gate="gate.bilaspur")
    assert res["ok"] and res["gone_out"]
    sid2, _ = _self_gatepass("DRG", 2)
    assert walk_out(db, db.get(DispatchSlip, sid2), desk="operator.durg", gate="gate.durg") == 1
    db.commit()


# ----------------------------------------------------------------------------- the demo logins: one list
def test_login_page_and_seed_use_one_list_and_list_the_gate_users(db):
    from fastapi.testclient import TestClient
    from app.main import app
    html = TestClient(app).get("/login").text
    for username, pw, label in demo_users.login_rows():
        assert username in html and pw in html, username
    assert {"gate.raipur", "gate.bilaspur", "gate.durg"} <= {u for u, _p, _l in demo_users.login_rows()}
    assert [r[:2] for r in seed.USERS] == [r[:2] for r in demo_users.seed_rows()]
    have = {u.username for u in db.scalars(select(User))}
    assert {u for u, *_ in demo_users.USERS} <= have
    for username, _full, role, cfas, _pw, _label in demo_users.USERS:
        u = _user(db, username)
        assert u.role.code == role and (sorted(c.code for c in u.cfas) == sorted(cfas or []) or u.all_cfas)
        assert (u.role.code == "GATE_STAFF") == username.startswith("gate.")


def test_ensure_demo_users_adds_what_is_missing_and_touches_nothing_else(db):
    old_hash = _user(db, "gate.raipur").password_hash
    durg = _user(db, "gate.durg")
    db.delete(durg)
    db.commit()
    assert _user(db, "gate.durg") is None
    admin_hash = _user(db, "admin").password_hash
    added = seed.ensure_demo_users(db)
    db.commit()
    assert added == ["gate.durg"]
    new = _user(db, "gate.durg")
    assert new.role.code == "GATE_STAFF" and [c.code for c in new.cfas] == ["DRG"] and verify_password("Demo@123", new.password_hash)
    assert _user(db, "gate.raipur").password_hash == old_hash and _user(db, "admin").password_hash == admin_hash
    assert seed.ensure_demo_users(db) == []                                   # idempotent
    assert "USER_CREATED" in [a for a in audit_actions(db, "gate.durg")]
    # an existing user is never changed, whatever the list says
    _user(db, "gate.raipur").full_name = "Renamed By The Office"
    db.commit()
    assert seed.ensure_demo_users(db) == [] and _user(db, "gate.raipur").full_name == "Renamed By The Office"
    # not a demo database any more (the administrator changed the demo password): nothing is ever added
    admin = _user(db, "admin")
    saved = admin.password_hash
    from app.security.auth import hash_password
    admin.password_hash = hash_password("A-Real-Strong-Password-1")
    db.delete(_user(db, "gate.durg"))
    db.commit()
    try:
        assert seed.ensure_demo_users(db) == [] and _user(db, "gate.durg") is None
    finally:
        _user(db, "admin").password_hash = saved
        db.commit()
        seed.ensure_demo_users(db)
        db.commit()
    assert _user(db, "gate.durg") is not None


# ----------------------------------------------------------------------------- migration 0017
def test_migration_0017_takes_the_permission_away_and_turns_case_control_on(tmp_path):
    from tests.test_v12_fixes import ROOT
    p = tmp_path / "m.db"
    env = {**os.environ, "CTDMS_DATABASE_URL": f"sqlite:///{p}", "CTDMS_TEST_DATABASE_URL": ""}

    def alembic(*args):
        r = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, capture_output=True, text=True,
                           timeout=600)
        assert r.returncode == 0, r.stdout + r.stderr

    alembic("upgrade", "0016")
    con = sqlite3.connect(p)
    con.execute("insert into roles (code, name, permissions, is_system, created_at, updated_at) values (?,?,?,?,datetime('now'),datetime('now'))",
                ("T17", "Test role", json.dumps(["dispatch.view", "dispatch.complete", "dispatch.operate"]), 0))
    con.execute("insert into user_permissions (user_id, permission, effect, granted_at) values (7, 'dispatch.complete', 'GRANT', datetime('now'))")
    con.execute("insert into user_permissions (user_id, permission, effect, granted_at) values (7, 'dispatch.view', 'DENY', datetime('now'))")
    con.execute("insert into settings (key, value, value_type, category, updated_at) values ('dispatch.complete_requires', 'NONE', 'choice', 'Dispatch controls', datetime('now'))")
    con.commit()
    con.close()
    alembic("upgrade", "head")
    con = sqlite3.connect(p)
    assert con.execute("select version_num from alembic_version").fetchone()[0] == "0017"
    perms = {c: json.loads(x) for c, x in con.execute("select code, permissions from roles")}
    assert perms["T17"] == ["dispatch.view", "dispatch.operate"]
    assert all("dispatch.complete" not in v for v in perms.values())
    assert con.execute("select permission, effect from user_permissions").fetchall() == [("dispatch.view", "DENY")]
    assert con.execute("select count(*) from settings where key='dispatch.complete_requires'").fetchone()[0] == 0
    acts = {a for (a,) in con.execute("select action from audit_logs where username='system'")}
    assert {"ROLE_PERMISSIONS_CHANGED", "USER_PERMISSION_REMOVED"} <= acts
    con.close()
    alembic("downgrade", "0016")                                     # back to v1.7.1 works and gives the permission back
    con = sqlite3.connect(p)
    assert "dispatch.complete" in json.loads(con.execute("select permissions from roles where code='DISPATCH_OPERATOR'").fetchone()[0]) \
        if con.execute("select 1 from roles where code='DISPATCH_OPERATOR'").fetchone() else True
    con.close()
    alembic("upgrade", "head")
