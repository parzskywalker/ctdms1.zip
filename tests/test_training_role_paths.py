"""Role learning paths (Depot Manager, Checker / Approver, Accounts) completed with real actions, one practice copy
per trainee, the live database untouched. (Dispatch Operator: tests/e2e_training.py in the browser; Management:
test_training.py; Master: test_training_master_path.py.)"""
from __future__ import annotations

import re
import sqlite3
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.models import Role, TrainingProgress, User
from app.services.utils import local_today
from app.training import runtime
from app.training.lessons import PATHS
from tests.conftest import _test_session
from tests.training_gate import trip_steps

PW = "Path@2026xy"


def _user(web, username, role_code):
    s = _test_session()
    try:
        rid = s.scalar(select(Role.id).where(Role.code == role_code))
    finally:
        s.close()
    web("admin").post("/admin/users/save", data={"username": username, "full_name": username, "role_id": str(rid),
                                                 "password": PW, "is_active": "1", "all_companies": "1", "all_cfas": "1"})
    s = _test_session()
    try:
        return s.scalar(select(User.id).where(User.username == username))
    finally:
        s.close()


def _run_path(web, uid, username, path, actions):
    w = web(username, PW)
    db_file = runtime.db_path(uid)
    today = local_today().isoformat()

    def one(sql, *args):
        c = sqlite3.connect(db_file)
        try:
            r = c.execute(sql, args).fetchone()
            return r[0] if r else None
        finally:
            c.close()

    def state():
        return w.get("/training/state").json()

    gate = {}
    for lesson, kw in (("m07-long", {}), ("d03-pawati", {"pawati": True})):    # labels, act as the gate staff, scan
        for key, fn in trip_steps(w, db_file, lesson, **kw).items():
            gate[key] = (lambda f: (lambda w_, one_, t_: f()))(fn)
    actions = {**actions, **gate}

    for code in PATHS[path]["lessons"]:
        w.post("/training/start", data={})
        st = state()
        assert st["lesson"]["code"] == code
        if not st.get("needs", {}).get("ok", True):
            w.post("/training/prepare")
        guard = 0
        while state().get("step"):
            guard += 1
            assert guard < 40, code
            s = state()["step"]
            act = actions.get((code, s["id"]))
            if act:
                act(w, one, today)
            out = w.post("/training/step", data={"action": "check" if s["kind"] == "check" else "next",
                                                 "step_id": s["id"]}).json()
            if s["kind"] == "check":
                assert out["message"]["ok"], (code, s["id"], out["message"])
        assert state().get("finished"), code
    s = _test_session()
    try:
        p = s.scalar(select(TrainingProgress).where(TrainingProgress.user_id == uid))
        assert p.status == "COMPLETED", (path, p.status)
    finally:
        s.close()
    w.post("/training/exit")
    return one


def _gatepass(w, one, today, numbers, **fields):
    ids = [one("select id from invoices where invoice_no=?", n) for n in numbers]
    data = {"ids": [str(i) for i in ids], "dispatch_date": today, "pickup_mode": "REGULAR",
            "group_cross_company": "1", **fields}  # the lessons' trips mix two companies: the operator ticks the box
    for n, i in zip(numbers, ids):
        data[f"cases_{i}"] = str(one("select boxes from invoices where id=?", i))
    r = w.post("/gatepass/new", data=data)
    return int(re.search(r"/gatepass/(\d+)$", str(r.url)).group(1))


def _long_distance(w, one, today):
    _gatepass(w, one, today, ["001248", "001249", "PQR/B/0313"], mode="TRANSPORTER", pickup_mode="OUTSIDE_AUTO",
              auto_method="PER_TRIP", trip_charge="500",
              outside_auto_id=str(one("select id from outside_autos where code='DM-LA1'")),
              transporter_id=str(one("select id from transporters where code='DM-SRL'")))


def _slip(one):
    return one("select max(id) from dispatch_slips")


LONG = {
    ("m07-long", "generate"): _long_distance,
    ("m07-long", "print"): lambda w, one, t: w.post(f"/gatepass/{_slip(one)}/print", data={"size": "A5", "mode": "pdf"}),
    ("m07-long", "pawati-print"): lambda w, one, t: w.post("/pawati/print", data={"ids": _pawati_ids(one)}),
    ("m07-long", "lr"): lambda w, one, t: w.post(f"/gatepass/{_slip(one)}/lr", data={
        f"lr_{one('select id from customers where code=?', c)}": lr for c, lr in (("DM-C071", "SRL-5501"), ("DM-C080", "SRL-5502"))}
        | {f"lrdate_{one('select id from customers where code=?', c)}": t for c in ("DM-C071", "DM-C080")}),
}

def _pawati_trip(w, one, t):
    inv = one("select id from invoices where invoice_no='001250'")
    srl, vtc = (one("select id from transporters where code=?", c) for c in ("DM-SRL", "DM-VTC"))
    return w.post("/gatepass/new", data={
        "ids": [str(inv)], "dispatch_date": t, "mode": "TRANSPORTER", "pickup_mode": "OUTSIDE_AUTO",
        "outside_auto_id": str(one("select id from outside_autos where code='DM-LA1'")), "auto_method": "PER_TRIP",
        "trip_charge": "500", f"cases_{inv}": "8", f"recv_{inv}": f"{srl}:5,{vtc}:3"})


def _pawati_ids(one):
    c = sqlite3.connect(runtime_path[0])
    try:
        return [str(r[0]) for r in c.execute("select id from garage_pawatis where print_count=0 order by pawati_no")]
    finally:
        c.close()


runtime_path: list = [None]
PAWATI = {
    ("d03-pawati", "generate"): _pawati_trip,
    ("d03-pawati", "print"): lambda w, one, t: w.post(f"/gatepass/{_slip(one)}/print", data={"size": "A5", "mode": "pdf"}),
    ("d03-pawati", "pawati-print"): lambda w, one, t: w.post("/pawati/print", data={"ids": _pawati_ids(one)}),
    ("d03-pawati", "receive-hand"): lambda w, one, t: w.post(
        f"/pawati/{one('select id from garage_pawatis where receiving_transporter_id=? order by id desc', one('select id from transporters where code=?', 'DM-SRL'))}/receive",
        data={"note": "signed"}),
    ("d03-pawati", "receive-lr"): lambda w, one, t: w.post(f"/gatepass/{_slip(one)}/lr", data={
        f"lr_{one('select customer_id from invoices where invoice_no=?', '001250')}_{one('select id from transporters where code=?', 'DM-VTC')}": "VTC-7001",
        f"lrdate_{one('select customer_id from invoices where invoice_no=?', '001250')}_{one('select id from transporters where code=?', 'DM-VTC')}": t}),
}

EXPORT = lambda w, one, t: w.get("/reports/cfa-freight/export?fmt=xlsx&date_from=2026-01-01")  # noqa: E731  (Accounts: final report)
REGISTER_EXPORT = lambda w, one, t: w.get("/reports/gatepass-register/export?fmt=xlsx&date_from=2026-01-01")  # noqa: E731  (Depot Manager: operational register)


def _live(db):
    db.expire_all()
    return {t: db.execute(text(f"select count(*) from {t}")).scalar()
            for t in ("invoices", "dispatch_slips", "transporter_bills", "transporter_rates", "invoice_overrides")}


@pytest.mark.parametrize("path,role", [("DEPOT", "CFA_MANAGER"), ("CHECKER", "CHECKER"), ("ACCOUNTS", "ACCOUNTS")])
def test_role_path_completes_with_real_actions(web, db, path, role):
    before = _live(db)
    username = f"path.{path.lower()}"
    uid = _user(web, username, role)
    bill = lambda one: one("select id from transporter_bills order by id desc limit 1")  # noqa: E731
    actions = dict(LONG)
    actions.update(PAWATI)
    runtime_path[0] = runtime.db_path(uid)
    actions.update({
        ("dm-reports", "export"): REGISTER_EXPORT,
        ("c-rates", "approve"): lambda w, one, t: w.post("/rate-cards/approve-all", data={"remarks": "checked"}),
        ("c-exceptions", "decide"): lambda w, one, t: w.post(
            f"/overrides/{one('select id from invoice_overrides where status=?', 'PENDING')}/decide",
            data={"decision": "approve", "remarks": "recounted at the dock"}),
        ("c-bills", "approve"): lambda w, one, t: w.post(f"/recon/{bill(one)}/approve", data={"remarks": "ok"}),
        ("a-expense", "export"): EXPORT,
        ("a-bill-entry", "enter"): lambda w, one, t: w.post("/recon/new", data={
            "transporter_id": str(one("select id from transporters where code='DM-SRL'")), "bill_no": "SRL/OCT/26-01",
            "bill_date": t, "bill_amount": "924", "period_from": (local_today() - timedelta(days=30)).isoformat(),
            "period_to": t, "link": "1"}),
        ("a-bill-entry", "amounts"): lambda w, one, t: w.post(f"/recon/{bill(one)}/amounts", data={
            f"amt_{one('select id from transporter_bill_lines where lr_no=?', 'SRL-5501')}": "484",
            f"amt_{one('select id from transporter_bill_lines where lr_no=?', 'SRL-5502')}": "440"}),
        ("a-pay", "review"): lambda w, one, t: w.post(f"/recon/{bill(one)}/rates", data={"note": "card rates"}),
        ("a-pay", "approve"): lambda w, one, t: (w.post("/training/act-as", data={"who": "demo.checker"}),
                                                 w.post(f"/recon/{bill(one)}/approve", data={"remarks": "ok"})),
        ("a-pay", "pay"): lambda w, one, t: (w.post("/training/act-as", data={"who": ""}),
                                             w.post(f"/recon/{bill(one)}/pay", data={"payment_ref": "UTR-1"})),
    })
    one = _run_path(web, uid, username, path, actions)
    if path == "CHECKER":
        assert one("select count(*) from transporter_rates where status='PENDING_APPROVAL'") == 0
        assert one("select status from transporter_bills") == "APPROVED"
    if path == "ACCOUNTS":
        assert one("select status from transporter_bills order by id desc") == "PAID"
    assert _live(db) == before
