"""v1.8.4 training videos (same style as the earlier CTDMS videos):
  dm - for management, to train the Depot Manager (labels exception, Ready to Load, plan or make, exceptions, approvals)
  op - for Dispatch Operators (labels, Planned Loads -> make gate pass, print gate pass and Garage Pawatis)

  DEMO_WORK=<folder> python run_v184_videos.py <dm|op> dry      -> fast run, writes narration.json
  DEMO_WORK=<folder> python run_v184_videos.py <dm|op> record   -> real-time run with video (needs durations.json)

Run with the video tools venv (Playwright). The app runs on a fresh DEMONSTRATION database in DEMO_WORK - never the
owner's data folder or the live server."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from demolib import Demo  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
PY = ROOT / ".venv/Scripts/python.exe"
WORK = Path(os.environ["DEMO_WORK"])
VIDEO, MODE = sys.argv[1], sys.argv[2]
PORT = 8031 if VIDEO == "dm" else 8032
BASE = f"http://127.0.0.1:{PORT}"
assert "data" not in {p.name for p in WORK.parents} and WORK.name != "data", "never the real data folder"


def env():
    e = dict(os.environ)
    e.update(PYTHONUTF8="1", PYTHONPATH=str(ROOT), CTDMS_DATABASE_URL=f"sqlite:///{(WORK / 'demo.db').as_posix()}",
             CTDMS_ENVIRONMENT="development", CTDMS_SHOW_DEMO_LOGINS="false",
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
    WORK.mkdir(parents=True, exist_ok=True)
    for p in WORK.glob("demo.db*"):
        p.unlink()
    e = env()
    for args in (["init-db"], ["seed-demo", "--reset"], ["pilot-v18"]):
        subprocess.run([str(PY), "-m", "app.cli", *args], cwd=ROOT, env=e, check=True, capture_output=True)
    r = subprocess.run([str(PY), str(Path(__file__).parent / "v184_setup.py"), VIDEO], cwd=ROOT, env=e,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    print(r.stdout.strip())
    log = open(WORK / "server.log", "w")
    subprocess.Popen([str(PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
                     cwd=ROOT, env=e, stdout=log, stderr=log)
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/login", timeout=2)
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("server did not start")


def login(d: Demo, user: str):
    d.go("/login")
    d.pg.fill("input[name=username]", user)
    d.pg.fill("input[name=password]", "Demo@123")
    d.pause(0.6)
    d.pg.click("button[type=submit]")
    d.pg.wait_for_load_state("networkidle")
    d.pause(0.8)


def nav(d: Demo, label: str):
    d.click(f'aside.side a:has-text("{label}")')


def press(d: Demo, sel: str):
    """Click a button that Playwright's visibility check misses inside the gate pass side panel (the cursor still shows)."""
    d._move(d.L(sel))
    d.pg.evaluate("() => window.__pulse && window.__pulse()")
    try:
        with d.pg.expect_navigation(wait_until="load", timeout=8000):
            d.pg.evaluate("s => document.querySelector(s).click()", sel)
    except Exception:
        d.pg.screenshot(path=str(WORK / "stuck.png"), full_page=True)
        print("STUCK", d.pg.url, d.pg.evaluate("() => [document.querySelector('#gp-why')?.textContent, [...document.querySelectorAll('.alert')].map(a=>a.textContent.trim()).join(' | ')]"))
        raise
    d.pg.wait_for_load_state("networkidle")
    d.pause(0.7)


def same_tab(d: Demo):
    """Print forms open a new tab in real use; in the video they open in the same window so they are seen."""
    d.pg.evaluate("() => document.querySelectorAll('form[target=_blank]').forEach(f => f.removeAttribute('target'))")


# ----------------------------------------------------------------------------- Depot Manager (for management)
def video_dm(d: Demo):
    d.title_card("CTDMS 1.8 - Training the Depot Manager",
                 "For management: what to teach the Depot Manager, screen by screen",
                 "This video is for the management team who will train the Depot Manager at the Guma depot. "
                 "It walks through the Depot Manager's screens in the order of a normal day. Every record shown is demonstration data.")
    login(d, "cfa.raipur")
    d.say("The Depot Manager signs in with their own username and password. The left menu shows only the screens "
          "their role may use. The working screens are under Dispatch desk.", title="The menu")
    for item in ("Labels to Print", "Ready to Load", "Make Gatepass", "Planned Loads", "Dispatch Exceptions"):
        d._move(d.L(f'aside.side a:has-text("{item}")'))
        d.pause(0.5)
    d.sync()

    nav(d, "Labels to Print")
    d.say("Step one of the day belongs to the operators: printing carton labels for every invoice. "
          "Teach the Depot Manager one thing here: if the cartons packed differ from the invoice, only the Depot Manager "
          "may confirm the new count, with a reason. That becomes an exception, and it is listed on the gate pass.",
          title="Labels to Print")
    d._move(d.L('main input[name="count"]'))
    d.pause(1.2)
    if d.pg.locator('main input[name="reason"]').count():
        d._move(d.L('main input[name="reason"]'))
    d.sync()

    nav(d, "Ready to Load")
    d.say("Ready to Load shows invoices whose cartons are all labelled and not yet on a vehicle, grouped by area. "
          "The Depot Manager plans each vehicle here: tick the invoices for one vehicle. The space bar ticks too.",
          title="Ready to Load - planning a vehicle")
    boxes = d.pg.locator('main input[name="ids"]')
    for i in range(2):
        b = boxes.nth(i)
        d._move(b)
        b.check()
        d.pause(0.5)
    d.sync()
    d.say("Then press Make gatepass for this vehicle.")
    d.btn("Make gatepass for this vehicle")
    d.say("This screen shows the chosen invoices with their labelled cartons. On the right, the Depot Manager chooses "
          "who carries the goods. Here a local auto, paid one amount for the trip.", title="Choosing the vehicle")
    d.radio("mode", "OUTSIDE_AUTO")
    d.select("outside_auto_id", "Kishore")
    d.radio("auto_method", "PER_TRIP")
    d.fill("trip_charge", "600")
    d.sync()
    d.say("Now the important choice. Generate gatepass makes it straight away. Save as plan for an operator keeps "
          "the plan, and an operator makes and prints the gate pass from it. Both are allowed; the plan is the normal "
          "way when the operators handle the printing.", title="Plan, or make it yourself")
    d._move(d.L("#gp-submit"))
    d.pause(1.0)
    d._move(d.L("#gp-plan"))
    d.sync()
    press(d, "#gp-plan")
    d.say("The plan is saved and waits under Planned Loads, with the vehicle, the cartons and who planned it. "
          "Its invoices leave Ready to Load, so nobody can put them on a second vehicle.", title="Planned Loads")
    d.sync()

    nav(d, "Ready to Load")
    d.say("For an urgent case the Depot Manager can still make the gate pass directly. Tick the invoice, choose the vehicle "
          "and press Generate gatepass.", title="Making the gate pass directly")
    b = d.pg.locator('main input[name="ids"]').first
    d._move(b)
    b.check()
    d.btn("Make gatepass for this vehicle")
    d.radio("mode", "OUTSIDE_AUTO")
    d.select("outside_auto_id", "Ramesh")
    d.fill("trip_charge", "250")
    d.sync()
    press(d, "#gp-submit")
    d.say("The gate pass is made. It still has to be printed before the goods may leave; the guard checks it at the gate "
          "with the phone.", title="The gate pass")
    d.sync()

    d.go("/load-plans")
    d.say("If a plan is wrong, for example the vehicle broke down, the Depot Manager opens it and cancels it with a "
          "reason. Its invoices go back to Ready to Load.", title="Cancelling a plan")
    d.click('main a:has-text("#")')
    d._move(d.L('main input[name="reason"]'))
    d.sync()

    nav(d, "Dispatch Exceptions")
    d.say("Dispatch Exceptions lists every exception: carton counts that differ, urgent single trips, transporters "
          "not normally allowed, and gate exceptions. The Depot Manager signs beside them on the printed gate pass, and "
          "the guard photographs the signed copy before the vehicle leaves.", title="Exceptions")
    d.sync()

    if d.pg.locator('aside.side a:has-text("Approvals")').count():
        nav(d, "Approvals")
        d.say("Approvals is where the Depot Manager approves new freight rates. A rate only counts once it is approved.",
              title="Approvals")
        d.sync()

    d.title_card("Training checklist", "Labels exception · Ready to Load · plan or make · cancel a plan · exceptions · approvals",
                 "To train the Depot Manager: let them do each step once on a test invoice, watch the operator make a gate "
                 "pass from their plan, and void the test gate pass afterwards. Thank you.")


# ----------------------------------------------------------------------------- Dispatch Operator
def video_op(d: Demo):
    d.title_card("CTDMS 1.8 - Dispatch Operator", "Labels first, then gate passes from the Depot Manager's plans",
                 "This video is for the dispatch operators at the Guma depot. You print carton labels, and you make and "
                 "print the gate passes that the Depot Manager has planned. Every record shown is demonstration data.")
    login(d, "operator.raipur")
    d.say("Sign in with your own username and password. Your screens are in the left menu.", title="Signing in")
    d.sync()

    nav(d, "Labels to Print")
    d.say("Every invoice needs carton labels first. The carton count is already filled from the invoice. "
          "Check it against the packed cartons and press Enter.", title="1. Labels to Print")
    cnt = d.L('main input[name="count"]')
    d._move(cnt)
    cnt.click()
    d.sync()
    d.key("Enter", after=1.2)
    d.say("The label sheet opens with the Tulsyan name and logo and a barcode on every label. Print it, stick one "
          "label on each carton, then confirm: press Y if all printed, or N to print again.")
    d.sync()
    d.key("y", show="Y", after=1.2)
    d.say("Done. If the packed cartons differ from the invoice, call the Depot Manager: only they may confirm a different count.")
    d.sync()

    nav(d, "Planned Loads")
    d.say("The Depot Manager plans each vehicle. Their plans wait here under Planned Loads, with the vehicle and the cartons.",
          title="2. Planned Loads")
    d.sync()
    d.click('main a:has-text("#")')
    d.say("Open a plan. Check the invoices and the vehicle, then press Make gate pass, or F8.", title="3. Make the gate pass")
    d.sync()
    d.click('main button:has-text("Make gate pass")')
    d.say("The gate pass is made exactly as the Depot Manager planned it. You cannot change the vehicle or the invoices here; "
          "if something is wrong, ask the Depot Manager to cancel the plan and plan again.")
    d.sync()
    same_tab(d)
    d.say("Now print it: press Print gatepass. The goods may leave only with the printed gate pass.", title="4. Print")
    d.click('main button:has-text("Print gatepass")')
    d.say("This is the gate pass with the Tulsyan name and logo, the barcode the guard scans, the invoices and the charge. "
          "Give it to the driver with the Garage Pawatis, if there are any.")
    d.pg.mouse.wheel(0, 300)
    d.sync()

    d.title_card("Your day in four steps", "Labels to Print · Planned Loads · Make gate pass · Print",
                 "That is the whole job: labels first, then make each planned gate pass and print it. The guard checks the "
                 "gate pass and the cartons with the phone at the gate. Thank you.")


def main():
    durs = {}
    if MODE == "record":
        durs = json.loads((WORK / "durations.json").read_text(encoding="utf-8"))
    reset_and_start()
    d = Demo(BASE, WORK, record=MODE == "record", durations=durs, speed=0.15,
             video_dir=WORK / "video" if MODE == "record" else None)
    try:
        (video_dm if VIDEO == "dm" else video_op)(d)
    finally:
        d.finish()
        kill_server()
    (WORK / "narration.json").write_text(json.dumps(d.steps, indent=1), encoding="utf-8")
    print("steps", len(d.steps))


if __name__ == "__main__":
    main()
