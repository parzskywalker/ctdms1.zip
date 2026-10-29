"""The shared label / gate steps of the Guided Training lessons, done through the real screens' requests in a trainee's
practice database (v1.7.2): print the case labels, act as the demonstration gate staff, scan every case, scan the gatepass.

    actions.update(trip_steps(w, db_path, "m08-trip"))               # labels, gate-act, gate-cases, gate-out, gate-self
    actions.update(trip_steps(w, db_path, "d03-pawati", pawati=True)) # the same, with the refused first gatepass scan
"""
from __future__ import annotations

import sqlite3


def _q(path, sql, *args):
    c = sqlite3.connect(path)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def trip_steps(w, db_path, lesson: str, *, slip=None, pawati: bool = False) -> dict:
    """{(lesson, step id): action()} for the shared blocks. `slip` = a function returning the gatepass id (default: the newest)."""
    sid = slip or (lambda: _q(db_path, "select max(id) from dispatch_slips")[0][0])
    number = lambda: _q(db_path, "select dispatch_no from dispatch_slips where id=?", sid())[0][0]  # noqa: E731

    def act(who):
        assert w.post("/training/act-as", data={"who": who}).status_code == 200, who

    def labels():
        ids = [str(r[0]) for r in _q(db_path, "select id from dispatch_cases where slip_id=? and status='LABEL_PENDING'", sid())]
        w.post(f"/gatepass/{sid()}/labels/confirm", data={"case_ids": ",".join(ids), "result": "PRINTED"})

    def cases():
        for (code,) in _q(db_path, "select case_code from dispatch_cases where slip_id=? and status='LABELLED' order by id", sid()):
            res = w.post("/gatepass/scan-case", data={"code": code, "slip_id": str(sid())}).json()
            assert res["ok"], res

    def gatepass():
        return w.post("/gatepass/scan-case", data={"code": number(), "slip_id": str(sid())}).json()

    steps = {"labels": labels, "gate-act": lambda: act("demo.gate"), "gate-cases": cases,
             "gate-out": gatepass, "gate-self": lambda: act("")}
    if pawati:  # d03: the first gatepass scan is refused (Pawatis not printed); act as yourself, print them, scan again
        def refused():
            res = gatepass()
            assert not res["ok"] and "Garage Pawati not printed" in res["message"], res

        steps = {"labels": labels, "gate-act": lambda: act("demo.gate"), "gate-cases": cases, "blocked": refused,
                 "gate-self-1": lambda: act(""), "gate-act-2": lambda: act("demo.gate"), "gate-out-2": gatepass,
                 "gate-self-2": lambda: act("")}
    return {(lesson, k): v for k, v in steps.items()}
