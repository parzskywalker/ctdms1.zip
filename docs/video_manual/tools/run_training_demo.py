"""Supplementary guide video: Guided Training (first-time users), same style as the v1.6 video.

  DEMO_WORK=<folder> python run_training_demo.py dry     -> fast run, collects the narration
  DEMO_WORK=<folder> python run_training_demo.py record  -> real-time run with video (needs durations.json)

Uses a COPY of the v1.6 demonstration database as the "live" CTDMS (upgraded to the current schema). Shows: the Master
switching compulsory training ON, a first-time Dispatch Operator logging in, Guided Training opening, interactive steps
checked by CTDMS, the keyboard-first flow, progress kept after logging out, completion unlocking live work, and the
Master's training dashboard / ON-OFF switch."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from demolib import Demo

ROOT = Path(r"D:/ctdms")
PY = ROOT / ".venv/Scripts/python.exe"
WORK = Path(os.environ["DEMO_WORK"])
OUT = ROOT / "docs/video_manual/v1.6/training"
SRC_DB = ROOT / "docs/video_manual/v1.6/demo_database/ctdms_v16_demo.db"
PORT = 8005
BASE = f"http://127.0.0.1:{PORT}"
ADMIN_PW = "Master@2026"
OP, OP_PW, OP_NAME = "mohan.lal", "Dispatch@2026x", "Mohan Lal (Demo)"


def env():
    e = dict(os.environ)
    e.update(PYTHONUTF8="1", CTDMS_DATABASE_URL=f"sqlite:///{(WORK / 'live.db').as_posix()}",
             CTDMS_ENVIRONMENT="development", CTDMS_SHOW_DEMO_LOGINS="false", CTDMS_SEED_DEMO="false",
             CTDMS_DATA_DIR=str(WORK / "appdata"), CTDMS_UPLOAD_DIR=str(WORK / "appdata" / "uploads"),
             CTDMS_BACKUP_DIR=str(WORK / "appdata" / "backups"), CTDMS_LOG_DIR=str(WORK / "appdata" / "logs"))
    return e


def kill_server():
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match "
                    "'uvicorn' -and $_.CommandLine -match '--port %d' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" % PORT],
                   capture_output=True)
    time.sleep(1.5)


def reset_and_start():
    kill_server()
    for p in WORK.glob("live.db*"):
        p.unlink()
    keep = WORK / "appdata" / "training" / "templates"  # the prepared starting points can be kept between runs
    tmp = WORK / "_templates"
    if keep.exists():
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.move(str(keep), str(tmp))
    shutil.rmtree(WORK / "appdata", ignore_errors=True)
    if tmp.exists():
        (WORK / "appdata" / "training").mkdir(parents=True)
        shutil.move(str(tmp), str(keep))
    shutil.copyfile(SRC_DB, WORK / "live.db")
    e = env()
    subprocess.run([str(PY), "-m", "app.cli", "init-db"], cwd=ROOT, env=e, check=True, capture_output=True)
    subprocess.run([str(PY), "-m", "app.cli", "training-build"], cwd=ROOT, env=e, check=True, capture_output=True)
    log = open(WORK / "server.log", "w")
    subprocess.Popen([str(PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
                     cwd=ROOT, env=e, stdout=log, stderr=log)
    for _ in range(40):
        try:
            urllib.request.urlopen(BASE + "/login", timeout=2)
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("server did not start")


def login(d: Demo, user: str, pw: str):
    d.go("/login")
    d.kb_only(True)
    d.pg.wait_for_function("document.activeElement && document.activeElement.id === 'username'")
    d.type_keys(user, after=0.3)
    d.key("Enter", after=0.3)
    d.pg.keyboard.type(pw, delay=60 if d.record else 0)
    d.key("Enter", after=0.8)
    d.pg.wait_for_url(lambda u: "/login" not in u)
    d.pg.wait_for_load_state("networkidle")


def state(d: Demo) -> dict:
    for _ in range(20):
        try:
            d.pg.wait_for_load_state("domcontentloaded")
            return d.pg.evaluate("fetch('/training/state').then(r => r.json())")
        except Exception:
            d.pg.wait_for_timeout(300)
    return {}


def wait_step(d: Demo, *ids, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = state(d)
        if (s.get("step") or {}).get("id") in ids or (not ids and s.get("finished")):
            return s
        d.pg.wait_for_timeout(250)
    raise RuntimeError(f"guide not at {ids}: {state(d).get('step')}")


# ----------------------------------------------------------------------------- fast-forward (off camera, behind a title card)
def finish_rest(d: Demo, uid: int):
    """The operator finishes the remaining lessons 'later the same day' - the same actions as tests/e2e_training.py,
    sent as form posts while the title card is on screen."""
    req = d.pg.request
    tok = d.pg.locator("input[name=csrf_token]").first.input_value() if d.pg.locator("input[name=csrf_token]").count() \
        else re.search(r'data-csrf="([^"]+)"', d.pg.content()).group(1)
    pdb = WORK / "appdata" / "training" / "users" / str(uid) / "practice.db"
    today = date.today().isoformat()

    def one(sql, *a):
        c = sqlite3.connect(pdb)
        try:
            r = c.execute(sql, a).fetchone()
            return r[0] if r else None
        finally:
            c.close()

    def post(url, data=None):
        return req.post(BASE + url, form={"csrf_token": tok, **(data or {})}, headers={"X-CSRF-Token": tok})

    def gatepass(numbers, **f):
        ids = [one("select id from invoices where invoice_no=?", n) for n in numbers]
        body = "&".join([f"ids={i}" for i in ids] + [f"cases_{i}={one('select boxes from invoices where id=?', i)}" for i in ids]
                        + [f"{k}={v}" for k, v in {"dispatch_date": today, "pickup_mode": "REGULAR", "csrf_token": tok, **f}.items()])
        req.post(BASE + "/gatepass/new", data=body, headers={"Content-Type": "application/x-www-form-urlencoded",
                                                             "X-CSRF-Token": tok})
        sid = one("select max(id) from dispatch_slips")
        post(f"/gatepass/{sid}/print", {"size": "A5", "mode": "pdf"})
        post(f"/gatepass/{sid}/gate-out")
        return sid

    def steps_through(acts):
        for _ in range(40):
            s = req.get(BASE + "/training/state").json()
            st = s.get("step")
            if not st:
                return
            a = acts.get(st["id"])
            if a:
                a()
            post("/training/step", {"action": "check" if st["kind"] == "check" else "next", "step_id": st["id"]})

    sid = one("select max(id) from dispatch_slips")
    c071, c080 = one("select id from customers where code='DM-C071'"), one("select id from customers where code='DM-C080'")
    steps_through({"lr": lambda: post(f"/gatepass/{sid}/lr", {f"lr_{c071}": "SRL-5501", f"lr_{c080}": "SRL-5502",
                                                              f"lrdate_{c071}": today, f"lrdate_{c080}": today})})
    post("/training/start")
    steps_through({"generate": lambda: gatepass(["001245", "001246", "001247", "XY/G/2601"], mode="OUTSIDE_AUTO",
                                                outside_auto_id=one("select id from outside_autos where code='DM-LA1'"),
                                                auto_method="PER_TRIP", trip_charge="500")})
    post("/training/start")
    steps_through({"total": lambda: gatepass(["XY/K/2603", "PQR/K/0311"], mode="OUTSIDE_AUTO",
                                             outside_auto_id=one("select id from outside_autos where code='DM-LA2'"),
                                             auto_method="PER_CASE")})
    post("/training/start")
    s = req.get(BASE + "/training/state").json()
    if s.get("needs") and not s["needs"]["ok"]:
        post("/training/prepare")
    kish = lambda: one("select dispatch_slip_id from invoices where invoice_no='XY/G/2602'")  # noqa: E731
    steps_through({"confirm": lambda: post(f"/gatepass/{kish()}/gate-out", {"back": "gate"})})


# ----------------------------------------------------------------------------- the video
def stage_all(d: Demo):
    d.title_card("Guided Training", "First-time users learn on demonstration data - live data stays untouched",
                 "This short video shows Guided Training. New staff learn their work by doing it, in their own practice "
                 "copy of CTDMS with the version one point six demonstration data, before they work on live data.")
    login(d, "admin", ADMIN_PW)
    d.kb_only(False)
    d.go("/admin/training")
    d.say("The Master opens Administration, Guided Training. Compulsory training is off. The Master switches it on, "
          "for users created from now on, and gives a reason. Every change here is recorded in the audit log.")
    d.fill("reason", "New dispatch staff train first")
    d.btn("Switch ON")
    d.pause(1.5)
    d.shot("t01_switch_on", full=True)
    d.go("/admin/users/new")
    d.say("A new dispatch operator joins: Mohan Lal. The Master creates his login as usual, as a Dispatch Operator.")
    d.fill("username", OP); d.fill("full_name", OP_NAME)
    d.select("role_id", "Dispatch Operator"); d.fill("password", OP_PW)
    d.check("must_change", False); d.check("all_companies", True); d.check("all_cfas", True)
    d.btn("Save user")
    d.say("CTDMS confirms: Mohan must finish Guided Training before he can work on live data.")
    d.pause(1.5)
    d.logout()

    d.title_card("First login", "Guided Training opens by itself", "Mohan logs in for the first time.")
    login(d, OP, OP_PW)
    d.say("Instead of the normal screens, Guided Training opens: his learning path for a Dispatch Operator, five "
          "lessons, all of them practice on demonstration data. Live screens stay closed until he has finished.")
    d.pause(1.5)
    d.shot("t02_first_login", full=True)
    d.say("The cursor is already on Start training, so Enter starts it.")
    d.key("Enter", after=1.2)
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(True)
    wait_step(d, "welcome")
    d.say("The orange bar says Training Mode: demonstration data only. The panel on the right explains each step: what "
          "it does, why it matters, what to do and which keys to press. Control and Enter moves on.")
    d.pause(1)
    d.shot("t03_panel")
    d.key("Control+Enter", show="Ctrl + Enter", after=1)
    wait_step(d, "help")
    d.say("Now he does the step on the real screen. F1 lists the keys; CTDMS sees it at once and moves on. Escape closes it.")
    d.key("F1", after=1.5)
    wait_step(d, "help-close")
    d.key("Escape", after=1)
    wait_step(d, "goto")
    d.say("F3 jumps to any screen: he types pending, and Enter. The guide checks that he got there.")
    d.key("F3", after=0.4)
    d.type_keys("pending", after=0.3)
    d.key("Enter", after=1)
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(True)
    wait_step(d, "list")
    d.say("The down arrow moves into the list, Enter opens the invoice, and in the transporter box he types the code "
          "S R L. Each step turns green by itself.")
    d.key("ArrowDown", show="↓", after=0.8)
    wait_step(d, "open")
    d.key("Enter", after=1)
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(True)
    wait_step(d, "combo")
    d.type_keys("SRL", after=0.4)
    d.key("Enter", after=1)
    wait_step(d, "esc")
    d.say("Escape asks before leaving unsaved work, and Y leaves. Nothing was saved, and nothing ever reaches live data.")
    d.key("Escape", after=0.8)
    d.key("y", show="Y", after=1.2)
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(True)
    wait_step(d, "keys")
    d.key("Control+Enter", show="Ctrl + Enter", after=1.2)
    d.say("Lesson one is complete. Progress: one of five. The next lesson is a real long-distance dispatch.")
    d.shot("t04_lesson_done")
    d.key("Control+Enter", show="Ctrl + Enter", after=1.2)
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(True)
    wait_step(d, "unchanged")
    d.key("Control+Enter", show="Ctrl + Enter", after=0.8)
    d.say("He opens Scan and Gatepass, types the Bilaspur depot code B S P, and adds the three invoices by number.")
    d.key("F3", after=0.3); d.type_keys("scan", after=0.3); d.key("Enter", after=1)
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(True)
    wait_step(d, "depot")
    d.type_keys("BSP", after=0.3); d.key("Enter", after=1)
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(True)
    wait_step(d, "invoices")
    for no in ("001248", "001249", "PQR/B/0313"):
        d.type_keys(no, after=0.2); d.key("Enter", after=0.6)
        d.pg.wait_for_timeout(400)
    wait_step(d, "transporter")
    d.say("Enter on the empty box moves on, he types S R L for Shree Roadlines, and Enter, Enter, takes him to Generate gatepass.")
    d.key("Enter", after=0.4); d.key("Enter", after=0.4)
    d.type_keys("SRL", after=0.3); d.key("Enter", after=0.6)
    wait_step(d, "rates")
    d.key("Control+Enter", show="Ctrl + Enter", after=0.6)
    for _ in range(8):
        if d.pg.evaluate("document.activeElement.id") == "gp-submit":
            break
        d.key("Enter", after=0.3)
    d.say("When he generates it, CTDMS does not trust a click: it looks in his practice data and confirms the gatepass "
          "with its three invoices and its freight.")
    d.key("Enter", after=1.5)
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(True)
    wait_step(d, "print")
    d.pause(1.5)
    d.shot("t05_checked", full=False)
    d.say("He saves and exits for now. The Guided Training page shows his progress and the lesson in progress.")
    d.go("/training")
    d.pause(1)
    d.shot("t06_progress", full=True)
    d.logout()
    d.title_card("The next morning", "Progress is kept", "The next day Mohan logs in again.")
    login(d, OP, OP_PW)
    d.say("Continue takes him back to exactly where he stopped: print the gatepass, with F8.")
    d.key("Enter", after=1.2)
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(True)
    wait_step(d, "print")
    with d.ctx.expect_page() as pop:
        d.key("F8", after=0.5)
    pop.value.wait_for_load_state()
    d.pause(1.2)
    pop.value.close()
    d.pg.wait_for_selector('[data-key="F9"]:not([disabled])', timeout=15000)
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(True)
    d.say("Every print in training says: training, not valid for business use. F9 records the gate-out, and Enter answers yes.")
    d.key("F9", after=0.8)
    d.key("Enter", after=1.2)
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(True)
    wait_step(d, "lr")
    uid = d.pg.evaluate("fetch('/training/state').then(r => r.json())")  # noqa: F841 - keeps the session warm
    live_uid = sqlite3.connect(WORK / "live.db").execute("select id from users where username=?", (OP,)).fetchone()[0]
    d.title_card("Later that day", "Per-trip and per-box local autos, the gate check",
                 "Later the same day, Mohan finishes the remaining lessons: local auto per trip, per box, and the gate check.")
    finish_rest(d, live_uid)
    d.go("/training")
    d.kb_only(False)
    d.say("All five lessons are complete. The completion date is recorded, and live work opens according to his role.")
    d.pause(1.5)
    d.shot("t07_complete", full=True)
    d.go("/dispatch/pending")
    d.say("Pending Dispatch now opens normally, on live data.")
    d.pause(1.2)
    d.logout()

    login(d, "admin", ADMIN_PW)
    d.kb_only(False)
    d.go("/admin/training")
    d.say("The Master sees every user: training required, not started, in training or completed, with dates. A path can "
          "be reassigned, a requirement released, and progress reset, all recorded.")
    d.pause(2)
    d.shot("t08_dashboard", full=True)
    d.say("Switching compulsory training off lets everyone work again. Completed training is never removed, and "
          "practice stays available under Help, Guided Training.")
    d.fill("reason", "Pilot complete")
    d.btn("Switch OFF")
    d.pause(1.5)
    d.title_card("Guided Training", "Help › Guided Training / Practice Mode · Administration › Guided Training",
                 "That is Guided Training: real screens, demonstration data, every step checked, fully keyboard driven, "
                 "and controlled by the Master. Thank you.")


def main():
    mode = sys.argv[1]
    record = mode == "record"
    dur = json.loads((WORK / "durations.json").read_text()) if record else {}
    reset_and_start()
    OUT.mkdir(parents=True, exist_ok=True)
    d = Demo(BASE, OUT, record=record, durations=dur, speed=0.12, video_dir=WORK / "video")
    try:
        stage_all(d)
    except Exception as e:
        print("FAILED:", repr(e)[:600])
        print("URL:", d.pg.url)
        d.shot("_error")
        raise
    finally:
        vid = d.finish()
        (WORK / "narration.json").write_text(json.dumps(d.steps, indent=1, ensure_ascii=False), encoding="utf-8")
        print("steps:", len(d.steps), "video:", vid)


if __name__ == "__main__":
    main()
