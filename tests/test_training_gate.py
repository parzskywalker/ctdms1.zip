"""Guided Training lesson "Gate check, LR pending and common errors" (d02-gate, Dispatch Operator path, v1.7.2): the trainee acts
as the demonstration gate staff, scans every case of the Kishore Mini Truck gatepass (made - labels and gatepass printed - by the
practice scenario) and then the gatepass; every practical step is done through the real screens' requests in the trainee's own
practice database and each server check must pass."""
from __future__ import annotations

import sqlite3

from sqlalchemy import select

from app.models import TrainingProgress
from app.training import runtime
from app.training.lessons import LESSONS, PATHS
from tests.test_training import PW, _make_user, _state, _step, _switch
from tests.training_gate import trip_steps


def test_gate_lesson_is_taught_by_scanning_and_the_gate_staff_send_the_trip_out(web, db):
    assert "d02-gate" in PATHS["DISPATCH"]["lessons"] and LESSONS["d02-gate"].practical
    ids = [s.id for s in LESSONS["d02-gate"].steps]
    assert ids.index("gate-act") < ids.index("gate-cases") < ids.index("gate-out") < ids.index("gate-self")
    _switch(web, True)
    try:
        uid = _make_user(web, "trn.gate", "DISPATCH_OPERATOR")
        db.expire_all()
        p = db.scalar(select(TrainingProgress).where(TrainingProgress.user_id == uid))
        p.lessons = {c: {"done": "2026-10-01T00:00:00"} for c in PATHS["DISPATCH"]["lessons"][:PATHS["DISPATCH"]["lessons"].index("d02-gate")]}
        db.commit()
        w = web("trn.gate", PW)
        w.post("/training/start", data={"lesson": "d02-gate"})
        st = _state(w)
        assert st["lesson"]["code"] == "d02-gate" and st.get("needs", {}).get("ok", True), st.get("needs")
        path = runtime.db_path(uid)

        def one(sql, *args):
            c = sqlite3.connect(path)
            try:
                row = c.execute(sql, args).fetchone()
                return row[0] if row else None
            finally:
                c.close()

        kishore = lambda: one("select dispatch_slip_id from invoices where invoice_no='XY/G/2602'")  # noqa: E731
        # what the scenario left waiting at the gate: case control, labels and gatepass already printed, nothing scanned
        assert one("select case_control from dispatch_slips where id=?", kishore()) == "REQUIRED"
        assert one("select count(*) from dispatch_cases where slip_id=? and status='LABELLED'", kishore()) == 10
        assert one("select gate_out_at from dispatch_slips where id=?", kishore()) is None
        actions = {k[1]: v for k, v in trip_steps(w, path, "d02-gate", slip=kishore).items()}
        guard = 0
        while _state(w).get("step"):
            guard += 1
            assert guard < 30
            s = _state(w)["step"]
            act = actions.get(s["id"])
            if act:
                act()
            out = _step(w, "check" if s["kind"] == "check" else "next")
            if s["kind"] == "check":
                assert out["message"]["ok"], (s["id"], out["message"])
        assert _state(w).get("finished")
        # the trip went out ONLY through the gate staff's scan: the demonstration gate user, 10 cases then the gatepass
        assert one("select u.username from dispatch_slips s join users u on u.id=s.gate_out_by_id where s.id=?", kishore()) == "demo.gate"
        assert one("select count(*) from dispatch_cases where slip_id=? and status='DISPATCHED'", kishore()) == 10
        assert one("select count(*) from case_scans where slip_id=? and result='OK'", kishore()) == 10
        assert one("select status from invoices where invoice_no='XY/G/2602'") == "DISPATCHED"
        w.post("/training/exit")
    finally:
        _switch(web, False)
