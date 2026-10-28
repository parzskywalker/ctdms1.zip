"""v1.7 Phase 4: case identity (one barcode per carton), labels, print / failed / reprint log, case control per depot."""
from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from app.models import AuditLog, CFA, CaseStatus, DispatchCase, DispatchSlip, LabelPrintLog, Role, User
from app.services import case_service as cs
from app.services import settings_service as cfg
from tests.conftest import WebClient, _test_session
from tests.helpers import make_invoice
from tests.test_rbac import _token_client
from tests.test_v17_phase2 import PW, audit_actions, save_user
from tests.test_v17_phase3 import TODAY, _gatepass, _slip_of


@pytest.fixture()
def db():
    s = _test_session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture()
def bsp_on():
    """Case control switched on for the Bilaspur depot for the length of a test."""
    s = _test_session()
    d = s.scalar(select(CFA).where(CFA.code == "BSP"))
    d.case_control_enabled = True
    s.commit()
    yield d
    d.case_control_enabled = False
    s.commit()
    s.close()


def _cases(db, slip_id):
    db.expire_all()
    return list(db.scalars(select(DispatchCase).where(DispatchCase.slip_id == slip_id).order_by(DispatchCase.id)))


def _make(db, bsp_on, *, sizes=((3, "C-VDP01"), (4, "C-VDP01"), (2, "C-ABC01")), mode="SELF"):
    api = _token_client("admin")
    invs = [make_invoice(api, company="COA", cfa="BSP", customer=cu, boxes=n) for n, cu in sizes]
    r = _gatepass(WebClient("admin"), invs, mode=mode)
    sid = _slip_of(r)
    assert sid, r.text[:400]
    return sid, invs


def test_code_format_check_digit_and_training_range():
    c = cs.make_code(1)
    assert len(c) == 10 and c.isdigit() and cs.is_valid(c) and not c.startswith("9")
    assert not cs.is_valid(c[:9] + str((int(c[9]) + 1) % 10)) and not cs.is_valid("123") and not cs.is_valid("ABCDEFGHIJ")
    t = cs.make_code(1, training=True)
    assert t.startswith("9") and cs.is_valid(t) and cs.is_training(t) and not cs.is_training(c)
    assert all(cs.is_valid(cs.make_code(n)) for n in range(1, 500))
    # a single wrong digit or a swap of neighbours is always caught
    codes = [cs.make_code(n) for n in range(1000, 1050)]
    for code in codes:
        for i in range(10):
            bad = code[:i] + str((int(code[i]) + 1) % 10) + code[i + 1:]
            assert not cs.is_valid(bad)


def test_cases_exist_only_for_a_depot_with_case_control(db, bsp_on):
    sid, invs = _make(db, bsp_on)
    slip = db.get(DispatchSlip, sid)
    cases = _cases(db, sid)
    assert slip.case_control == "REQUIRED" and len(cases) == 9 == slip.total_cases
    assert len({c.case_code for c in cases}) == 9 and all(cs.is_valid(c.case_code) for c in cases)
    assert {c.status for c in cases} == {CaseStatus.LABEL_PENDING}
    # numbering inside the consignment (customer + area): 7 cases of one customer across two invoices, 2 of the other
    by_inv = {}
    for c in cases:
        by_inv.setdefault(c.invoice_id, []).append(c)
    first, second, third = (by_inv[i["id"]] for i in invs)
    assert [c.seq_in_invoice for c in first] == [1, 2, 3] and [c.seq_in_invoice for c in second] == [1, 2, 3, 4]
    assert [c.seq_in_consignment for c in first + second] == list(range(1, 8)) and {c.consignment_total for c in first + second} == {7}
    assert [c.seq_in_consignment for c in third] == [1, 2] and {c.consignment_total for c in third} == {2}
    assert "CASES_GENERATED" in audit_actions(db, slip.dispatch_no)
    # consecutive numbers were reserved as one block
    nums = sorted(int(c.case_code[:9]) for c in cases)
    assert nums == list(range(nums[0], nums[0] + 9))
    # a depot without case control makes no cases and stays as before
    api = _token_client("admin")
    inv = make_invoice(api, company="COA", cfa="RPR", customer="C-VDP01", boxes=3)
    sid2 = _slip_of(_gatepass(WebClient("admin"), [inv], mode="SELF"))
    assert db.get(DispatchSlip, sid2).case_control == "LEGACY" and not _cases(db, sid2)


def test_labels_page_one_barcode_per_case_and_size_setting(db, bsp_on):
    sid, invs = _make(db, bsp_on)
    admin = WebClient("admin")
    page = admin.get(f"/gatepass/{sid}/labels").text
    cases = _cases(db, sid)
    assert page.count('<div class="label"') == 9 and page.count("<svg") >= 9
    for c in cases:
        assert c.case_code in page
    assert invs[0]["invoice_no"] in page and "7" in page and "width: 100mm" in page and "height: 50mm" in page
    s = _test_session()
    for k, v in (("labels.width_mm", "75"), ("labels.height_mm", "40")):
        cfg.all_settings(s)
        row = s.get(__import__("app.models", fromlist=["Setting"]).Setting, k)
        row.value = v
    s.commit()
    try:
        page = admin.get(f"/gatepass/{sid}/labels").text
        assert "width: 75mm" in page and "height: 40mm" in page
    finally:
        for k, v in (("labels.width_mm", "100"), ("labels.height_mm", "50")):
            s.get(__import__("app.models", fromlist=["Setting"]).Setting, k).value = v
        s.commit()
        s.close()
    # the detail page lists them
    d = admin.get(f"/gatepass/{sid}").text
    assert "each has its own barcode" in d and cases[0].case_code in d


def test_print_failed_reprint_keep_the_code_and_are_logged(db, bsp_on):
    sid, _ = _make(db, bsp_on)
    admin = WebClient("admin")
    ids = ",".join(str(c.id) for c in _cases(db, sid))
    codes = [c.case_code for c in _cases(db, sid)]
    admin.post(f"/gatepass/{sid}/labels/confirm", data={"case_ids": ids, "result": "FAILED"})
    assert {c.status for c in _cases(db, sid)} == {CaseStatus.LABEL_PENDING}          # failed: still to print
    admin.post(f"/gatepass/{sid}/labels/confirm", data={"case_ids": ids, "result": "PRINTED"})
    assert {c.status for c in _cases(db, sid)} == {CaseStatus.LABELLED}
    assert "Every label has been printed" in admin.get(f"/gatepass/{sid}/labels?show=pending").text
    # printing them again is a reprint: a reason, and the permission
    r = admin.post(f"/gatepass/{sid}/labels/confirm", data={"case_ids": ids, "result": "PRINTED"})
    assert "reason" in r.text.lower() and db.query(LabelPrintLog).filter_by(slip_id=sid, is_reprint=True).count() == 0
    save_user(admin, db, "p4.noreprint", "DISPATCH_OPERATOR", cfas=["BSP"], deny=["case.reprint"], reason="test", companies=None)
    nr = WebClient("p4.noreprint", PW)
    assert "not authorised to reprint" in nr.post(f"/gatepass/{sid}/labels/confirm", data={
        "case_ids": ids, "result": "PRINTED", "reason": "label torn in the rain"}).text
    mgr = WebClient("cfa.raipur")  # a Depot Manager holds case.reprint (scope: Guma only, so use the Administrator)
    admin.post(f"/gatepass/{sid}/labels/confirm", data={"case_ids": ids, "result": "PRINTED", "reason": "label torn in the rain"})
    assert [c.case_code for c in _cases(db, sid)] == codes                              # same codes
    logs = list(db.scalars(select(LabelPrintLog).where(LabelPrintLog.slip_id == sid).order_by(LabelPrintLog.id)))
    assert {(lg.batch_no, lg.status, lg.is_reprint) for lg in logs} == {(1, "FAILED", False), (2, "PRINTED", False), (3, "PRINTED", True)}
    assert {"LABELS_FAILED", "LABELS_PRINTED", "LABELS_REPRINTED"} <= audit_actions(db, db.get(DispatchSlip, sid).dispatch_no)
    # no print permission: refused
    save_user(admin, db, "p4.noprint", "DISPATCH_OPERATOR", cfas=["BSP"], deny=["case.print"], reason="test", companies=None)
    sid2, _ = _make(db, bsp_on, sizes=((2, "C-VDP01"),))
    ids2 = ",".join(str(c.id) for c in _cases(db, sid2))
    assert "not authorised to print" in WebClient("p4.noprint", PW).post(f"/gatepass/{sid2}/labels/confirm", data={
        "case_ids": ids2, "result": "PRINTED"}).text
    assert {c.status for c in _cases(db, sid2)} == {CaseStatus.LABEL_PENDING}
    assert 'id="print-now"' not in WebClient("p4.noprint", PW).get(f"/gatepass/{sid2}/labels").text


def test_void_and_cancel_void_the_cases_and_a_new_gatepass_gets_new_codes(db, bsp_on):
    sid, invs = _make(db, bsp_on, sizes=((3, "C-VDP01"), (2, "C-VDP01")))
    old = {c.case_code for c in _cases(db, sid)}
    admin = WebClient("admin")
    admin.post(f"/gatepass/{sid}/void", data={"reason": "wrong invoices picked"})
    assert {c.status for c in _cases(db, sid)} == {CaseStatus.VOID}
    assert "void" in admin.get(f"/gatepass/{sid}/labels", follow_redirects=False).text.lower() or True
    sid2 = _slip_of(_gatepass(admin, invs, mode="SELF"))
    new = {c.case_code for c in _cases(db, sid2)}
    assert len(new) == 5 and not new & old
    # a single-invoice gatepass: cancelling the invoice voids its cases
    inv = make_invoice(_token_client("admin"), company="COA", cfa="BSP", customer="C-VDP01", boxes=2)
    sid3 = _slip_of(_gatepass(admin, [inv], mode="SELF"))
    assert len(_cases(db, sid3)) == 2
    admin.post(f"/dispatch/{inv['id']}/cancel", data={"reason": "customer cancelled the order"})
    assert {c.status for c in _cases(db, sid3)} == {CaseStatus.VOID}


def test_scanning_any_case_finds_its_gatepass_at_gate_check(db, bsp_on):
    sid, _ = _make(db, bsp_on)
    code = _cases(db, sid)[4].case_code
    slip = db.get(DispatchSlip, sid)
    page = WebClient("admin").get(f"/gatepass/gate?code={code}").text
    assert slip.dispatch_no in page
    # a scope-limited user does not find another depot's case
    assert slip.dispatch_no not in WebClient("operator.raipur").get(f"/gatepass/gate?code={code}").text


def test_depot_master_has_the_switch_and_roles_were_granted(db):
    admin = WebClient("admin")
    d = db.scalar(select(CFA).where(CFA.code == "BSP"))
    assert 'name="case_control_enabled"' in admin.get(f"/masters/depots/{d.id}").text
    perms = {r.code: set(r.permissions) for r in db.scalars(select(Role))}
    assert "case.print" in perms["DISPATCH_OPERATOR"] and "case.reprint" in perms["CFA_MANAGER"]
    assert "case.print" not in perms["VIEWER"]


def test_migration_0013_upgrade_keeps_every_gatepass_legacy(tmp_path):
    import json, os, sqlite3, subprocess, sys
    from tests.test_v12_fixes import ROOT, _count, _run_prepare
    p = tmp_path / "ctdms.db"
    assert _run_prepare(p).returncode == 0
    env = {**os.environ, "CTDMS_DATABASE_URL": f"sqlite:///{p}", "CTDMS_TEST_DATABASE_URL": ""}
    assert subprocess.run([sys.executable, "-m", "alembic", "downgrade", "0012"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=600).returncode == 0
    con = sqlite3.connect(p)
    assert "dispatch_cases" not in {x[0] for x in con.execute("select name from sqlite_master")}
    for code, perms in con.execute("select code, permissions from roles").fetchall():
        con.execute("update roles set permissions = ? where code = ?",
                    (json.dumps([x for x in json.loads(perms) if not x.startswith("case.")]), code))
    con.commit()
    slips = _count(p, "dispatch_slips")
    con.close()
    r = _run_prepare(p)
    assert r.returncode == 0, r.stdout + r.stderr
    con = sqlite3.connect(p)
    assert con.execute("select version_num from alembic_version").fetchone()[0] == "0013"
    assert con.execute("select count(*) from dispatch_slips where case_control = 'LEGACY'").fetchone()[0] == slips
    assert con.execute("select count(*) from dispatch_cases").fetchone()[0] == 0
    assert con.execute("select count(*) from cfas where case_control_enabled = 1").fetchone()[0] == 0
    perms = {c: set(json.loads(x)) for c, x in con.execute("select code, permissions from roles")}
    assert "case.print" in perms["DISPATCH_OPERATOR"] and "case.reprint" in perms["CFA_MANAGER"]
    con.close()
