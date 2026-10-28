"""Guided Training lesson "Local-auto trip to transporters: Garage Pawati" (v1.7): every practical step is done through
the real screens' form posts in the trainee's own practice database, and each server check must pass."""
from __future__ import annotations

import re
import sqlite3

from sqlalchemy import select

from app.models import TrainingProgress
from app.services.utils import local_today
from app.training import runtime
from app.training.lessons import LESSONS, PATHS
from tests.test_training import PW, _make_user, _state, _step, _switch


import pytest


@pytest.mark.parametrize("role,path,username", [("DISPATCH_OPERATOR", "DISPATCH", "trn.pawati"),
                                                ("CFA_MANAGER", "DEPOT", "trn.pawati.dm")])
def test_pawati_lesson_is_on_the_dispatch_and_depot_paths_and_passes_with_real_actions(web, db, role, path, username):
    assert PATHS[path]["lessons"][-1] == "d03-pawati" and LESSONS["d03-pawati"].practical
    _switch(web, True)
    try:
        uid = _make_user(web, username, role)
        db.expire_all()
        p = db.scalar(select(TrainingProgress).where(TrainingProgress.user_id == uid))
        p.lessons = {c: {"done": "2026-10-01T00:00:00"} for c in PATHS[path]["lessons"][:-1]}
        db.commit()
        w = web(username, PW)
        today = local_today().isoformat()
        w.post("/training/start", data={"lesson": "d03-pawati"})
        st = _state(w)
        assert st["lesson"]["code"] == "d03-pawati" and st.get("needs", {}).get("ok", True), st.get("needs")
        path = runtime.db_path(uid)

        def one(sql, *args):
            c = sqlite3.connect(path)
            try:
                row = c.execute(sql, args).fetchone()
                return row[0] if row else None
            finally:
                c.close()

        inv = one("select id from invoices where invoice_no='001250'")
        srl = one("select id from transporters where code='DM-SRL'")
        vtc = one("select id from transporters where code='DM-VTC'")
        sid = lambda: one("select max(id) from dispatch_slips")  # noqa: E731
        pids = lambda: [r for r in sqlite3.connect(path).execute("select id from garage_pawatis where print_count=0 order by pawati_no")]  # noqa: E731
        seen = {}

        def blocked():
            r = w.post(f"/gatepass/{sid()}/gate-out")
            seen["msg"] = r.text
            assert "Garage Pawati not printed" in r.text

        actions = {
            "generate": lambda: w.post("/gatepass/new", data={
                "ids": [str(inv)], "dispatch_date": today, "mode": "TRANSPORTER", "pickup_mode": "OUTSIDE_AUTO",
                "outside_auto_id": str(one("select id from outside_autos where code='DM-LA1'")), "auto_method": "PER_TRIP",
                "trip_charge": "500", f"cases_{inv}": "8", f"recv_{inv}": f"{srl}:5,{vtc}:3"}),
            "print": lambda: w.post(f"/gatepass/{sid()}/print", data={"size": "A5", "mode": "print"}),
            "blocked": blocked,
            "pawati-print": lambda: w.post("/pawati/print", data={"ids": [str(i[0]) for i in pids()]}),
            "out": lambda: w.post(f"/gatepass/{sid()}/gate-out"),
            "receive-hand": lambda: w.post(f"/pawati/{one('select id from garage_pawatis where receiving_transporter_id=? order by id desc', srl)}/receive",
                                           data={"note": "garage clerk signed"}),
            "receive-lr": lambda: seen.update(lr=w.post(f"/gatepass/{sid()}/lr", data={
                f"lr_{one('select customer_id from invoices where id=?', inv)}_{vtc}": "VTC-7001",
                f"lrdate_{one('select customer_id from invoices where id=?', inv)}_{vtc}": today}).text),
        }
        guard = 0
        while _state(w).get("step"):
            guard += 1
            assert guard < 30
            s = _state(w)["step"]
            act = actions.get(s["id"])
            if act:
                act()
            if s["kind"] == "check":
                out = _step(w, "check")
                assert out["message"]["ok"], (s["id"], out["message"], re.findall(r'alert[^>]*>([^<]+)', seen.get("lr", ""))[:3])
            else:
                _step(w, "next")
        assert _state(w).get("finished")
        assert 'alert-error' in seen["msg"]
        # the practice data really holds what the lesson taught
        assert one("select count(*) from garage_pawatis where status='PRINTED' and received_at is not null") == 2
        assert one("select received_via from garage_pawatis where receiving_transporter_id=?", vtc) == "LR"
        assert one("select total_charge from dispatch_slips where id=?", sid()) == 734     # 150 + 84 freight + 500 trip
        w.post("/training/exit")
        assert re.search(r"d03-pawati", w.get("/training").text)
    finally:
        _switch(web, False)
