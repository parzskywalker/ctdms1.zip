"""v1.6 Guided Training: compulsory gate, Master controls, isolated practice data, progress, watermarks.

The browser side (panel, keyboard-only Dispatch Operator tutorial) is driven by tests/e2e_training.py."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.models import AuditLog, Company, DispatchSlip, Invoice, MasterUpload, TrainingProgress, TransporterBill, User
from app.training import checks, progress, runtime, scenario
from app.training.lessons import LESSONS, PATHS

ROOT = Path(__file__).parents[1]
PW = "Train@2026x"


def _live_counts(db) -> dict:
    tables = ["companies", "cfas", "customers", "invoices", "dispatch_slips", "transporter_bills", "transporter_rates",
              "master_uploads", "outside_autos", "notifications", "users"]
    db.expire_all()
    return {t: db.execute(text(f"select count(*) from {t}")).scalar() for t in tables}


def _make_user(web, username: str, role_code: str) -> int:
    from app.models import Role
    from tests.conftest import _test_session
    s = _test_session()
    try:
        role = s.scalar(select(Role).where(Role.code == role_code))
    finally:
        s.close()
    w = web("admin")
    r = w.post("/admin/users/save", data={"username": username, "full_name": username.title(), "role_id": str(role.id),
                                          "password": PW, "is_active": "1", "all_companies": "1", "all_cfas": "1"})
    assert r.status_code == 200, r.text[:300]
    s = _test_session()
    try:
        return s.scalar(select(User.id).where(User.username == username))
    finally:
        s.close()


def _switch(web, on: bool):
    r = web("admin").post("/admin/training/switch", data={"on": "1" if on else "", "reason": "test"})
    assert r.status_code == 200


def _state(w) -> dict:
    return w.get("/training/state").json()


def _step(w, action="next") -> dict:
    st = _state(w)
    return w.post("/training/step", data={"action": action, "step_id": (st.get("step") or {}).get("id", "")}).json()


# ----------------------------------------------------------------------------- lesson catalogue
def test_lessons_paths_checks_and_starting_points_are_consistent():
    assert len(PATHS["MASTER"]["lessons"]) == 15
    for code, p in PATHS.items():
        assert p["lessons"], code
        for c in p["lessons"]:
            assert c in LESSONS, (code, c)
    for ls in LESSONS.values():
        assert ls.start in scenario.CHECKPOINTS, ls.code
        assert ls.needs in checks.NEEDS, ls.code
        ids = [s.id for s in ls.steps]
        assert len(ids) == len(set(ids)), f"duplicate step ids in {ls.code}"
        assert any(s.check or s.ui for s in ls.steps), f"{ls.code} has nothing to do"
        for s in ls.steps:
            assert not s.check or s.check in checks.CHECKS, (ls.code, s.check)
            for f in s.files:
                assert f in scenario.FILES and scenario.FILES[f].exists(), f
        if ls.video:
            from app.training import video
            rng = video.resolve(ls.video)
            assert rng and 0 <= rng[0] < rng[1] <= video.chapters()[1], ls.code  # a real chapter of the guide video
    # practical lessons for every role path (completion needs real results, not clicks)
    for code in ("MASTER", "DISPATCH", "DEPOT", "CHECKER", "ACCOUNTS", "MANAGEMENT"):
        assert any(LESSONS[c].practical for c in PATHS[code]["lessons"]), code
    assert progress.path_for_role("DISPATCH_OPERATOR") == "DISPATCH"
    assert progress.path_for_role("CFA_MANAGER") == "DEPOT" and progress.path_for_role("MANAGEMENT") == "MANAGEMENT"
    assert progress.path_for_role("VIEWER") == "VIEWER"  # a Viewer is not a Management user (no final reports)


def test_starting_points_rebuild_the_v16_demonstration():
    scenario.ensure()
    import sqlite3
    c = sqlite3.connect(scenario.template_path("dispatched"))
    slips = dict(c.execute("select remarks, total_charge from dispatch_slips").fetchall())
    assert slips["Demonstration - long distance"] == 924
    assert slips["Demonstration - per trip, four invoices"] == 500  # one trip, one amount
    assert slips["Demonstration - per box"] == 432                 # 15 x 20 + 6 x 22
    assert c.execute("select count(*) from invoices").fetchone()[0] == 13
    c = sqlite3.connect(scenario.template_path("clean"))
    assert c.execute("select count(*) from companies").fetchone()[0] == 0
    c = sqlite3.connect(scenario.template_path("uploaded"))
    assert c.execute("select count(*) from transporter_rates where status='PENDING_APPROVAL'").fetchone()[0] > 0


# ----------------------------------------------------------------------------- compulsory training (Master switch)
def test_compulsory_switch_gates_new_users_and_never_locks_out_the_master(web, db):
    _switch(web, True)
    try:
        uid = _make_user(web, "trn.newop", "DISPATCH_OPERATOR")
        db.expire_all()
        p = db.scalar(select(TrainingProgress).where(TrainingProgress.user_id == uid))
        assert p and p.required and p.path == "DISPATCH" and p.status == "NOT_STARTED"
        w = web("trn.newop", PW)
        r = w.get("/dispatch/pending", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/training"   # live work closed, server-side
        r = w.post("/gatepass/new", data={}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/training"
        assert w.get("/training").status_code == 200
        # the Master is never locked out of Administration, even when marked 'required'
        admin_id = db.scalar(select(User.id).where(User.username == "admin"))
        a = web("admin")
        a.post(f"/admin/training/{admin_id}", data={"do": "require"})
        assert a.get("/admin/settings").status_code == 200 and a.get("/admin/training").status_code == 200
        assert a.get("/invoices", follow_redirects=False).status_code == 303
        a.post(f"/admin/training/{admin_id}", data={"do": "release"})
        assert a.get("/invoices", follow_redirects=False).status_code == 200
        # switching OFF opens live work again; training stays available
        _switch(web, False)
        assert w.get("/dispatch/pending", follow_redirects=False).status_code == 200
        assert w.get("/training").status_code == 200
        assert db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action.in_(["TRAINING_REQUIRED", "TRAINING_NOT_REQUIRED", "SETTING_CHANGED"]))) >= 3
    finally:
        _switch(web, False)


def test_users_created_while_switch_off_are_not_gated(web, db):
    _switch(web, False)
    uid = _make_user(web, "trn.free", "VIEWER")
    db.expire_all()
    p = db.scalar(select(TrainingProgress).where(TrainingProgress.user_id == uid))
    assert p is None or not p.required


# ----------------------------------------------------------------------------- isolation + practical checks
def test_master_workbook_lesson_runs_on_practice_data_only(web, db):
    before = _live_counts(db)
    w = web("admin")
    r = w.post("/training/start", data={"lesson": "m01-intro"})
    assert r.status_code == 200 and "TRAINING MODE" in r.text
    st = _state(w)
    assert st["active"] and st["lesson"]["code"] == "m01-intro"
    # lesson 1: read + on-screen steps (recorded by the panel)
    while _state(w).get("step"):
        _step(w)
    assert _state(w)["finished"]
    r = w.post("/training/start", data={})  # Continue -> lesson 2
    assert _state(w)["lesson"]["code"] == "m02-workbook"
    _step(w)  # open the screen (on-screen step)
    _step(w)  # read: the workbook
    out = _step(w, "check")
    assert out["message"]["ok"] is False and "Check workbook" in out["message"]["text"]  # nothing done yet
    wb = scenario.WORKBOOK.read_bytes()
    r = w.post("/masters/upload", files={"file": (scenario.WORKBOOK.name, wb)})
    assert r.status_code == 200
    out = _step(w, "check")
    assert out["message"]["ok"], out
    up_id = int(re.search(r"/masters/upload/(\d+)", r.url.path if hasattr(r.url, "path") else str(r.url)).group(1))
    _step(w)  # read the four problems
    w.post(f"/masters/upload/{up_id}/commit")
    out = _step(w, "check")
    assert out["message"]["ok"], out
    # live database untouched by the import
    assert _live_counts(db) == before
    assert db.scalar(select(func.count()).select_from(Company).where(Company.code.like("DM-%"))) == 0
    pdb = runtime.session_for(db.scalar(select(User.id).where(User.username == "admin")))
    try:
        assert pdb.scalar(select(func.count()).select_from(Company).where(Company.code == "DM-ABC")) == 1
        assert pdb.scalar(select(MasterUpload.status)) == "IMPORTED"
    finally:
        pdb.close()
    w.post("/training/exit")
    assert "TRAINING MODE" not in w.get("/").text


def test_practice_dispatch_print_bill_and_customer_never_reach_live(web, db):
    before = _live_counts(db)
    w = web("admin")
    w.post("/training/start", data={"lesson": "m01-intro"})
    admin_id = db.scalar(select(User.id).where(User.username == "admin"))
    scenario.ensure()
    runtime.install(admin_id, scenario.template_path("dispatched"))
    pdb = runtime.session_for(admin_id)
    try:
        cust_loc = pdb.execute(text("select id from locations where code='DM-RPR'")).scalar()
        slip_id = pdb.scalar(select(DispatchSlip.id).where(DispatchSlip.remarks.like("%long distance%")))
        bill_count = pdb.scalar(select(func.count()).select_from(TransporterBill))
    finally:
        pdb.close()
    # a demonstration customer, a demonstration gatepass print, an export - all inside the practice copy
    r = w.post("/masters/customers/save", data={"code": "DM-TRN1", "name": "Trainee Customer (Demo)", "party_type": "RETAILER",
                                                 "location_id": str(cust_loc), "is_active": "1"})
    assert r.status_code == 200
    r = w.post(f"/gatepass/{slip_id}/print", data={"size": "A5", "mode": "print", "reason": "training reprint"})
    assert "NOT VALID FOR BUSINESS USE" in r.text
    r = w.get("/reports/invoice-freight/export?fmt=csv&date_from=2026-01-01")
    assert r.content.decode("utf-8-sig").startswith(runtime.WATERMARK)
    r = w.get("/reports/invoice-freight/export?fmt=pdf&date_from=2026-01-01")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    # sending anything is refused while practising
    from app.services import notify_service, subscription_service
    from app.services.errors import BusinessError
    with runtime.use({"uid": admin_id, "dir": runtime.user_dir(admin_id), "uploads": runtime.user_dir(admin_id) / "uploads",
                      "inbox": runtime.user_dir(admin_id) / "inbox", "as": None}):
        with pytest.raises(notify_service.SendError):
            notify_service.deliver(None, None)
        with pytest.raises(BusinessError):
            subscription_service.send_mail(["x@example.com"], "s", "t", [])
    pdb = runtime.session_for(admin_id)
    try:
        assert pdb.execute(text("select count(*) from customers where code='DM-TRN1'")).scalar() == 1
        assert pdb.scalar(select(func.count()).select_from(TransporterBill)) == bill_count
    finally:
        pdb.close()
    after = _live_counts(db)
    assert after == before, {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert db.scalar(select(func.count()).select_from(Invoice).where(Invoice.invoice_no == "001248")) == 0
    w.post("/training/exit")


def test_two_person_rule_in_practice_uses_the_demo_checker_only(web, db):
    w = web("admin")
    w.post("/training/start", data={"lesson": "m01-intro"})
    admin_id = db.scalar(select(User.id).where(User.username == "admin"))
    runtime.install(admin_id, scenario.template_path("uploaded"))
    assert w.post("/training/act-as", data={"who": "demo.checker"}).json()["acting"] == "demo.checker"
    assert "acting as demo.checker" in w.get("/approvals").text
    r = w.post("/rate-cards/approve-all", data={"remarks": "training"})
    assert r.status_code == 200
    pdb = runtime.session_for(admin_id)
    try:
        assert pdb.execute(text("select count(*) from transporter_rates where status='PENDING_APPROVAL'")).scalar() == 0
    finally:
        pdb.close()
    assert w.post("/training/act-as", data={"who": "admin"}).status_code == 400  # only demonstration users
    w.post("/training/act-as", data={"who": ""})
    w.post("/training/exit")
    assert w.post("/training/act-as", data={"who": "demo.checker"}).status_code == 400  # not outside Training Mode


# ----------------------------------------------------------------------------- progress, resume, reset, completion
def test_progress_survives_logout_and_the_master_can_reset_it(web, db):
    uid = _make_user(web, "trn.resume", "DISPATCH_OPERATOR")
    w = web("trn.resume", PW)
    w.post("/training/start", data={})
    st = _state(w)
    assert st["lesson"]["code"] == "k00-keys"
    _step(w)
    _step(w)
    idx = _state(w)["lesson"]["index"]
    w.get("/training/state?at=" + "/dispatch/pending%3Ftab%3Dall")  # the panel reports the screen the trainee is on
    w.post("/logout")
    w2 = web("trn.resume", PW)
    assert "TRAINING MODE" not in w2.get("/").text  # a new login works on live data until training is continued
    page = w2.get("/training").text
    assert "Continue" in page
    r = w2.post("/training/start", data={}, follow_redirects=False)
    assert r.headers["location"] == "/dispatch/pending?tab=all"  # Continue returns to the screen where they stopped
    w2.get(r.headers["location"])
    st = _state(w2)
    assert st["lesson"]["code"] == "k00-keys" and st["lesson"]["index"] == idx  # resumed at the same step
    a = web("admin")
    a.post(f"/admin/training/{uid}", data={"do": "reset"})
    db.expire_all()
    p = db.scalar(select(TrainingProgress).where(TrainingProgress.user_id == uid))
    assert p.status == "NOT_STARTED" and not p.lessons and not runtime.has_practice_db(uid)
    # the Master cannot give a Dispatch Operator another role's tutorial (its screens would not open for the role)
    a.post(f"/admin/training/{uid}", data={"do": "assign", "path": "MANAGEMENT"})
    a.post(f"/admin/training/{uid}", data={"do": "assign", "path": "MASTER"})
    db.expire_all()
    assert db.scalar(select(TrainingProgress.path).where(TrainingProgress.user_id == uid)) == "DISPATCH"
    # an Administrator can practise any path; the assignment is audited
    aid = _make_user(web, "trn.adm", "ADMIN")
    a.post(f"/admin/training/{aid}", data={"do": "assign", "path": "MANAGEMENT"})
    db.expire_all()
    assert db.scalar(select(TrainingProgress.path).where(TrainingProgress.user_id == aid)) == "MANAGEMENT"
    page = a.get("/admin/training").text
    assert "trn.resume" in page and "Dispatch Operator" in page
    assert 'value="MASTER"' not in page.split("trn.resume")[1].split("</tr>")[0]  # not offered for an operator
    detail = a.get(f"/admin/training/{aid}").text
    assert "Dashboard, filters and MIS" in detail and "Reassigned" in detail


def test_completing_a_path_needs_real_results_and_unlocks_live_work(web, db):
    _switch(web, True)
    try:
        uid = _make_user(web, "trn.mgmt", "MANAGEMENT")
        w = web("trn.mgmt", PW)
        assert w.get("/", follow_redirects=False).status_code == 303
        w.post("/training/start", data={})
        while _state(w).get("step"):
            _step(w)                                        # v-dashboard: on-screen steps
        w.post("/training/start", data={})                  # v-reports
        st = _state(w)
        assert st["lesson"]["code"] == "v-reports"
        while _state(w)["step"]["kind"] != "check":
            _step(w)
        out = _step(w, "check")
        assert out["message"]["ok"] is False                # no export yet: clicking 'next' is not enough
        w.get("/reports/company-freight/export?fmt=xlsx&date_from=2026-01-01")
        out = _step(w, "check")
        assert out["message"]["ok"], out
        db.expire_all()
        p = db.scalar(select(TrainingProgress).where(TrainingProgress.user_id == uid))
        assert p.status == "COMPLETED" and p.completed_at
        w.post("/training/exit")
        assert w.get("/", follow_redirects=False).status_code == 200   # live work open
        _switch(web, False)
        _switch(web, True)                                  # switching never removes a completion
        db.expire_all()
        assert db.scalar(select(TrainingProgress.status).where(TrainingProgress.user_id == uid)) == "COMPLETED"
    finally:
        _switch(web, False)


def test_demo_files_and_video_are_served(web):
    w = web("admin")
    r = w.get("/training/files/DEMO_v1.6_Master_Workbook.xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert w.get("/training/files/..%2F..%2Fapp%2Fconfig.py").status_code == 404
    if (ROOT / "docs/video_manual/v1.6/CTDMS_v1.6_Guide_Video.mp4").exists():
        r = w.get("/training/video", headers={"Range": "bytes=0-99"})
        assert r.status_code == 206 and len(r.content) == 100      # the player can jump to a chapter
