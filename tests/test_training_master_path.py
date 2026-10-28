"""The complete Master Administrator learning path (16 lessons), every practical step done through the real screens'
form posts, in the Master's own practice database. On-screen steps (page opened, key pressed) are confirmed the way
the guidance panel does after it sees them; every 'check' step must pass the server's check of the practice data."""
from __future__ import annotations

import re
import sqlite3
from datetime import timedelta

from sqlalchemy import select, text

from app.models import TrainingProgress, User
from app.services.utils import local_today
from app.training import runtime, scenario
from app.training.lessons import PATHS


def test_master_path_completes_with_real_actions_and_leaves_live_data_alone(web, db):
    tables = ["companies", "customers", "invoices", "dispatch_slips", "transporter_bills", "transporter_rates",
              "master_uploads", "outside_autos", "users"]
    before = {t: db.execute(text(f"select count(*) from {t}")).scalar() for t in tables}
    admin_id = db.scalar(select(User.id).where(User.username == "admin"))
    w = web("admin")
    w.post(f"/admin/training/{admin_id}", data={"do": "reset"})
    path = runtime.db_path(admin_id)
    today = local_today().isoformat()

    def one(sql, *args):
        c = sqlite3.connect(path)
        try:
            row = c.execute(sql, args).fetchone()
            return row[0] if row else None
        finally:
            c.close()

    def state():
        return w.get("/training/state").json()

    def post_step(action):
        st = state()
        return w.post("/training/step", data={"action": action, "step_id": (st.get("step") or {}).get("id", "")}).json()

    def upload_import(fname, erp, company=None):
        f = scenario.FILES[fname]
        data = {"erp_system_id": str(one("select id from erp_systems where code=?", erp))}
        if company:
            data["company_id"] = str(one("select id from companies where code=?", company))
        r = w.post("/imports/upload", data=data, files={"file": (fname, f.read_bytes())})
        bid = int(re.search(r"/imports/(\d+)", str(r.url)).group(1))
        w.post(f"/imports/{bid}/commit")

    def inv_id(no):
        return one("select id from invoices where invoice_no=?", no)

    def gatepass(numbers, **fields):
        data = {"ids": [str(inv_id(n)) for n in numbers], "dispatch_date": today, "pickup_mode": "REGULAR",
                "group_cross_company": "1", **fields}  # the lessons' trips mix two companies: the operator ticks the box
        for n in numbers:
            data[f"cases_{inv_id(n)}"] = str(one("select boxes from invoices where invoice_no=?", n))
        r = w.post("/gatepass/new", data=data)
        sid = int(re.search(r"/gatepass/(\d+)$", str(r.url)).group(1))
        return sid

    def print_and_out(sid):
        assert "NOT VALID FOR BUSINESS USE" in w.post(f"/gatepass/{sid}/print", data={"size": "A5", "mode": "print"}).text
        w.post(f"/gatepass/{sid}/gate-out")

    def act_as(who):
        assert w.post("/training/act-as", data={"who": who}).status_code == 200

    def bill_id():
        return one("select id from transporter_bills order by id desc limit 1")

    actions = {
        ("m02-workbook", "check"): lambda: w.post("/masters/upload", files={"file": (scenario.WORKBOOK.name, scenario.WORKBOOK.read_bytes())}),
        ("m02-workbook", "import"): lambda: w.post(f"/masters/upload/{one('select max(id) from master_uploads')}/commit"),
        ("m03-masters", "approve"): lambda: (act_as("demo.checker"), w.post("/rate-cards/approve-all", data={"remarks": "training"})),
        ("m03-masters", "self"): lambda: act_as(""),
        ("m04-add-record", "fill"): lambda: w.post("/masters/autos/save", data={
            "code": "DM-LA4", "name": "Mohan E-Rickshaw (Demo)", "mobile": "9000003004", "vehicle_no": "CG04-ER-1004",
            "allowed_methods": "PER_TRIP", "default_trip_charge": "300", "is_active": "1"}),
        ("m05-recognition", "import"): lambda: upload_import(scenario.SAP_FILE.name, "SAP", "DM-ABC"),
        ("m05-recognition", "fix"): lambda: w.post("/masters/customer-types/save", data={
            "id": str(one("select id from customer_types where code='GOVT_SUPPLY'")), "code": "GOVT_SUPPLY",
            "name": "Government Supply", "marking": "GOV", "sort_order": "9", "is_active": "1"}),
        ("m06-imports", "marg"): lambda: upload_import(scenario.MARG_FILE.name, "MARG"),
        ("m06-imports", "cbo"): lambda: upload_import(scenario.CBO_FILE.name, "CBO"),
        ("m07-long", "generate"): lambda: gatepass(["001248", "001249", "PQR/B/0313"], mode="TRANSPORTER",
                                                   transporter_id=str(one("select id from transporters where code='DM-SRL'"))),
        ("m07-long", "print"): lambda: w.post(f"/gatepass/{one('select max(id) from dispatch_slips')}/print", data={"size": "A5", "mode": "print"}),
        ("m07-long", "gate-out"): lambda: w.post(f"/gatepass/{one('select max(id) from dispatch_slips')}/gate-out"),
        ("m07-long", "lr"): lambda: w.post(f"/gatepass/{one('select max(id) from dispatch_slips')}/lr", data={
            f"lr_{one('select id from customers where code=?', 'DM-C071')}": "SRL-5501",
            f"lr_{one('select id from customers where code=?', 'DM-C080')}": "SRL-5502",
            f"lrdate_{one('select id from customers where code=?', 'DM-C071')}": today,
            f"lrdate_{one('select id from customers where code=?', 'DM-C080')}": today}),
        ("m08-trip", "generate"): lambda: gatepass(["001245", "001246", "001247", "XY/G/2601"], mode="OUTSIDE_AUTO",
                                                   outside_auto_id=str(one("select id from outside_autos where code='DM-LA1'")),
                                                   auto_method="PER_TRIP", trip_charge="500"),
        ("m08-trip", "out"): lambda: print_and_out(one("select max(id) from dispatch_slips")),
        ("m09-box", "total"): lambda: gatepass(["XY/K/2603", "PQR/K/0311"], mode="OUTSIDE_AUTO",
                                               outside_auto_id=str(one("select id from outside_autos where code='DM-LA2'")),
                                               auto_method="PER_CASE"),
        ("m09-box", "out"): lambda: print_and_out(one("select max(id) from dispatch_slips")),
        ("m10-reports", "export"): lambda: w.get("/reports/invoice-freight/export?fmt=xlsx&date_from=2026-01-01"),
        ("m11-users", "create"): lambda: w.post("/admin/users/save", data={
            "username": "guma.op", "full_name": "Guma Operator", "is_active": "1", "password": "Guma@2026x",
            "role_id": str(one("select id from roles where code='DISPATCH_OPERATOR'")), "all_companies": "1",
            "cfas": str(one("select id from cfas where code='DM-GUMA'"))}),
        ("m12-controls", "change"): lambda: w.post("/admin/settings", data={"s_dispatch.default_slip_size": "A4",
                                                                            "reason": "training"}),
        ("m12-controls", "card"): lambda: w.post(f"/rate-cards/{one('select id from transporters where code=?', 'DM-SRL')}/propose", data={
            f"rate_{one('select id from locations where code=?', 'DM-BHL')}": "16",
            f"from_{one('select id from locations where code=?', 'DM-BHL')}": today, "reason": "transporter letter (training)"}),
        ("m12-controls", "approve"): lambda: (act_as("demo.checker"), w.post(
            f"/rate-cards/rate/{one('select max(id) from transporter_rates where status=?', 'PENDING_APPROVAL')}/decide",
            data={"decision": "approve", "remarks": "checked"})),
        ("m12-controls", "self"): lambda: act_as(""),
        ("m13-delivery", "record"): lambda: w.post("/deliveries", data={
            "pick": str(one("select id from invoices where status='DISPATCHED' and delivery_status is null order by id")),
            "delivered_on": today}),
        ("m14-bills", "enter"): lambda: w.post("/recon/new", data={
            "transporter_id": str(one("select id from transporters where code='DM-SRL'")), "bill_no": "SRL/OCT/26-01",
            "bill_date": today, "bill_amount": "924", "period_from": (local_today() - timedelta(days=30)).isoformat(),
            "period_to": today, "link": "1"}),
        ("m14-bills", "amounts"): lambda: w.post(f"/recon/{bill_id()}/amounts", data={
            f"amt_{one('select id from transporter_bill_lines where lr_no=?', 'SRL-5501')}": "484",
            f"amt_{one('select id from transporter_bill_lines where lr_no=?', 'SRL-5502')}": "440"}),
        ("m14-bills", "duplicate"): lambda: w.post("/recon/new", data={
            "transporter_id": str(one("select id from transporters where code='DM-SRL'")), "bill_no": "SRL/OCT/26-01",
            "bill_date": today, "bill_amount": "924"}),
        ("m14-bills", "review"): lambda: w.post(f"/recon/{bill_id()}/rates", data={"note": "card rates checked"}),
        ("m14-bills", "approve"): lambda: (act_as("demo.checker"), w.post(f"/recon/{bill_id()}/approve", data={"remarks": "ok"})),
        ("m14-bills", "pay"): lambda: (act_as(""), w.post(f"/recon/{bill_id()}/pay", data={"payment_ref": "UTR-TRAINING-1"})),
        ("d03-pawati", "generate"): lambda: w.post("/gatepass/new", data={
            "ids": [str(inv_id("001250"))], "dispatch_date": today, "mode": "TRANSPORTER", "pickup_mode": "OUTSIDE_AUTO",
            "outside_auto_id": str(one("select id from outside_autos where code='DM-LA1'")), "auto_method": "PER_TRIP",
            "trip_charge": "500", f"cases_{inv_id('001250')}": "8",
            f"recv_{inv_id('001250')}": f"{one('select id from transporters where code=?', 'DM-SRL')}:5,"
                                         f"{one('select id from transporters where code=?', 'DM-VTC')}:3"}),
        ("d03-pawati", "print"): lambda: w.post(f"/gatepass/{one('select max(id) from dispatch_slips')}/print", data={"size": "A5", "mode": "print"}),
        ("d03-pawati", "blocked"): lambda: w.post(f"/gatepass/{one('select max(id) from dispatch_slips')}/gate-out"),
        ("d03-pawati", "pawati-print"): lambda: w.post("/pawati/print", data={"ids": [
            str(r[0]) for r in sqlite3.connect(path).execute("select id from garage_pawatis order by pawati_no")]}),
        ("d03-pawati", "out"): lambda: w.post(f"/gatepass/{one('select max(id) from dispatch_slips')}/gate-out"),
        ("d03-pawati", "receive-hand"): lambda: w.post(
            f"/pawati/{one('select id from garage_pawatis where receiving_transporter_id=?', one('select id from transporters where code=?', 'DM-SRL'))}/receive",
            data={"note": "garage clerk signed"}),
        ("d03-pawati", "receive-lr"): lambda: w.post(f"/gatepass/{one('select max(id) from dispatch_slips')}/lr", data={
            f"lr_{one('select customer_id from invoices where id=?', inv_id('001250'))}_{one('select id from transporters where code=?', 'DM-VTC')}": "VTC-7001",
            f"lrdate_{one('select customer_id from invoices where id=?', inv_id('001250'))}_{one('select id from transporters where code=?', 'DM-VTC')}": today}),
        ("m15-audit", "export"): lambda: w.get("/reports/audit/export?fmt=csv&date_from=2026-01-01"),
    }

    for code in PATHS["MASTER"]["lessons"]:
        w.post("/training/start", data={})
        st = state()
        assert st["lesson"]["code"] == code, (code, st.get("lesson"))
        assert st.get("needs", {}).get("ok", True), (code, st["needs"])  # the Master's own work feeds the next lesson
        guard = 0
        while state().get("step"):
            guard += 1
            assert guard < 40, code
            s = state()["step"]
            act = actions.get((code, s["id"]))
            if act:
                act()
            if s["kind"] == "check":
                out = post_step("check")
                assert out["message"]["ok"], (code, s["id"], out["message"])
            else:
                post_step("next")
        assert state().get("finished"), code
    db.expire_all()
    p = db.scalar(select(TrainingProgress).where(TrainingProgress.user_id == admin_id))
    assert p.status == "COMPLETED" and len([c for c in p.lessons.values() if c.get("done")]) == 16
    w.post("/training/exit")
    db.expire_all()
    after = {t: db.execute(text(f"select count(*) from {t}")).scalar() for t in tables}
    assert after == before
    # the Master's practice CTDMS really contains the v1.6 scenario the trainee built
    assert one("select total_charge from dispatch_slips where auto_method='PER_TRIP'") == 500
    assert one("select total_charge from dispatch_slips where auto_method='PER_CASE'") == 432
    assert one("select status from transporter_bills") == "PAID"
    assert one("select recognition_code from invoices where invoice_no='001245'") == "ABC-RET-C025-GUMA-001245"
