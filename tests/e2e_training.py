"""Guided Training in a real browser (Edge), keyboard only for the trainee. Not collected by pytest.

    python tests/e2e_training.py http://127.0.0.1:8004 <admin password> <path to the LIVE database file> [screenshots]

Run it on a COPY of the v1.6 demonstration database (upgraded with `python -m app.cli init-db`). The Master switches
compulsory training ON and creates a Dispatch Operator; the operator then completes the whole Dispatch learning path
with the keyboard only: keyboard basics, long-distance dispatch, local auto per trip, per box, gate check. The test
also logs out half-way and resumes, and finally proves the live database was not changed by any of it.
"""
from __future__ import annotations

import re
import sqlite3
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

B, PW, LIVE = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
OUT = Path(sys.argv[4]) if len(sys.argv) > 4 else None
if OUT:
    OUT.mkdir(parents=True, exist_ok=True)
OP, OP_PW = f"op.train{time.strftime('%H%M%S')}", "Operator@2026x"
results: list[tuple[bool, str]] = []


def check(ok, what):
    results.append((bool(ok), what))
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    return ok


def live_counts():
    c = sqlite3.connect(LIVE)
    try:
        return {t: c.execute(f"select count(*) from {t}").fetchone()[0]
                for t in ("companies", "customers", "invoices", "dispatch_slips", "transporter_bills", "transporter_rates",
                          "outside_autos", "notifications")}
    finally:
        c.close()


with sync_playwright() as p:
    import os
    exe = os.environ.get("CHROMIUM") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None)
    br = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch(channel="msedge")
    ctx = br.new_context(viewport={"width": 1440, "height": 860})
    pg = ctx.new_page()
    K = pg.keyboard
    pg.on("dialog", lambda d: (check(False, "browser dialog: " + d.message), d.dismiss()))

    def settle():
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(350)

    def shot(n):
        if OUT:
            pg.screenshot(path=str(OUT / f"{n}.png"))

    def state():
        for _ in range(20):  # the page may be navigating (a form post, a reload after printing)
            try:
                pg.wait_for_load_state("domcontentloaded")
                return pg.evaluate("fetch('/training/state').then(r => r.json())")
            except Exception:
                pg.wait_for_timeout(300)
        return {}

    def step_id():
        s = state()
        return (s.get("step") or {}).get("id"), (s.get("lesson") or {}).get("code"), s

    def wait_step(sid=None, lesson=None, timeout=8):
        ok_ids = sid if isinstance(sid, (tuple, list)) else (sid,)
        t0 = time.time()
        while time.time() - t0 < timeout:
            cur, les, s = step_id()
            if (sid is None or cur in ok_ids) and (lesson is None or les == lesson):
                return s
            pg.wait_for_timeout(250)
        cur, les, s = step_id()
        check(False, f"expected step {lesson}/{sid}, still at {les}/{cur}: {(s.get('message') or {}).get('text', '')}")
        return s

    def nxt(sid, lesson=None):
        """Ctrl+Enter in the panel = Next / Check. Waits until the guide is on `sid`."""
        K.press("Control+Enter")
        return wait_step(sid, lesson)

    def login(u, pw):
        pg.goto(B + "/login"); settle()
        pg.wait_for_function("document.activeElement && document.activeElement.id === 'username'")
        K.type(u); K.press("Enter"); K.type(pw); K.press("Enter")
        pg.wait_for_url(lambda url: "/login" not in url, timeout=15000)
        settle()

    before = live_counts()
    # ---------------------------------------------------------------- the Master switches compulsory training ON
    login("admin", PW)
    pg.goto(B + "/admin/training"); settle()
    if "OFF</span>" in pg.content():
        pg.fill("input[name=reason]", "Pilot: new staff train first")
        pg.click("form[action='/admin/training/switch'] button")
        pg.wait_for_selector("#kb-ask"); K.press("y"); settle()
    check("ON</span>" in pg.content(), "Master: compulsory training switched ON (Administration › Guided Training)")
    shot("t01_master_switch")
    pg.goto(B + "/admin/users/new"); settle()
    pg.fill("input[name=username]", OP); pg.fill("input[name=full_name]", "Training Operator")
    pg.select_option("select[name=role_id]", label="Dispatch Operator")
    pg.fill("input[name=password]", OP_PW)
    if pg.locator("input[name=must_change]").is_checked():
        pg.uncheck("input[name=must_change]")
    for n in ("all_companies", "all_cfas"):
        if not pg.locator(f"input[name={n}]").is_checked():
            pg.check(f"input[name={n}]")
    pg.click("form[action='/admin/users/save'] button[type=submit]"); settle()
    check("must finish Guided Training" in pg.content(), "Master: new user created - marked 'training required'")
    pg.click("button:has-text('Sign out')"); settle()

    # ---------------------------------------------------------------- first login of the operator: training opens
    login(OP, OP_PW)
    check(pg.url.endswith("/training"), "first login goes straight to Guided Training")
    pg.goto(B + "/dispatch/pending"); settle()
    check(pg.url.endswith("/training"), "live screens are closed until training is complete (server-side)")
    shot("t02_training_home")
    K.press("Enter")  # 'Start training' has the cursor
    settle()
    s = wait_step("welcome", "k00-keys")
    check(pg.locator("#trn-panel").is_visible() and "TRAINING MODE" in pg.inner_text(".trn-banner"),
          "Training Mode: orange TRAINING bar and the guidance panel")
    shot("t03_panel_first_step")

    # ---------------------------------------------------------------- lesson: keyboard basics
    nxt("help")
    K.press("F1"); wait_step("help-close")
    K.press("Escape"); wait_step("goto")
    K.press("F3"); K.type("pending"); K.press("Enter"); settle()
    wait_step("list")
    K.press("ArrowDown"); wait_step("open")
    K.press("Enter"); settle()
    wait_step("combo")
    K.type("SRL"); K.press("Enter")
    wait_step("esc")
    K.press("Escape"); pg.wait_for_selector("#kb-ask"); K.press("y"); settle()
    s = wait_step("keys")
    check(True, "keyboard basics: F1, Esc, F3, arrows, Enter, code search, Esc guard - each checked on screen")
    K.press("Control+Enter")
    pg.wait_for_timeout(600)
    s = state()
    check(s.get("finished") and s["summary"]["done"] == 1, "lesson 1 complete; progress 1 of 6 saved")
    shot("t04_lesson_done")

    # ---------------------------------------------------------------- lesson: long-distance dispatch
    K.press("Control+Enter"); settle()   # Start next lesson
    wait_step("unchanged", "m07-long")
    nxt(("open", "depot"))
    K.press("F3"); K.type("scan"); K.press("Enter"); settle()
    wait_step("depot")
    K.type("BSP"); K.press("Enter"); settle()
    wait_step("invoices")
    for no in ("001248", "001249", "PQR/B/0313"):
        K.type(no); K.press("Enter"); pg.wait_for_timeout(500)
    wait_step("cross")          # two companies: Enter on the empty scan box goes to the tick box, Space ticks it
    K.press("Enter"); K.press("Space"); pg.wait_for_timeout(400)
    wait_step("transporter")
    K.press("Enter")            # tick box -> mode
    K.press("Enter")            # mode stays Transporter -> transporter box
    K.type("SRL"); K.press("Enter")
    wait_step("rates")
    shot("t05_long_distance")
    nxt("generate")
    for _ in range(8):          # Enter through the fields to the Generate button
        if pg.evaluate("document.activeElement.id") == "gp-submit":
            break
        K.press("Enter")
    K.press("Enter"); settle()
    wait_step("print")
    with ctx.expect_page() as pop:
        K.press("F8")
    pop.value.close()
    pg.wait_for_timeout(2300); settle()
    wait_step("gate-out")
    K.press("F9"); pg.wait_for_selector("#kb-ask"); K.press("Enter"); settle()
    wait_step("lr")
    check(pg.evaluate("document.activeElement.name || ''").startswith("lr_"), "after gate-out the cursor is in the first LR box")
    # first LR box = WellCare (C071) or Super Stockist (C080): read the row to type the right number
    for _ in range(2):
        row = pg.evaluate("document.activeElement.closest('tr').innerText")
        K.type("SRL-5501" if "WellCare" in row else "SRL-5502")
        K.press("Enter"); K.press("Enter")   # LR no -> LR date -> next LR no
    pg.keyboard.press("F6"); settle()
    s = state()
    check(s.get("finished") and s["summary"]["done"] == 2, "long-distance lesson complete (generate, print, gate-out, LR - checked in practice data)")

    # ---------------------------------------------------------------- interrupted session: log out, come back
    pg.goto(B + "/training"); settle()
    pg.click("button:has-text('Sign out')") if pg.locator("button:has-text('Sign out')").count() else None
    settle()
    login(OP, OP_PW)
    check(pg.url.endswith("/training") and "2 of 6" in pg.content(), "after logging out and in: progress 2 of 6 kept")
    K.press("Enter"); settle()
    wait_step("idea", "m08-trip")
    check(True, "Continue resumes at the next lesson (local auto per trip)")

    # ---------------------------------------------------------------- lesson: local auto per trip
    nxt(("open", "depot"))
    K.press("F3"); K.type("scan"); K.press("Enter"); settle()
    wait_step("depot")
    K.type("GUMA"); K.press("Enter"); settle()
    wait_step("invoices")
    for no in ("001245", "001246", "001247", "XY/G/2601"):
        K.type(no); K.press("Enter"); pg.wait_for_timeout(450)
    wait_step("cross")
    K.press("Enter"); K.press("Space"); pg.wait_for_timeout(400)
    wait_step("mode")
    K.press("Enter"); K.press("ArrowRight"); K.press("ArrowRight")
    wait_step("auto")
    K.press("Enter"); K.type("LA1"); K.press("Enter")
    wait_step("trip")
    if not re.match(r"^500(\.0+)?$", pg.input_value("#gp-trip")):
        K.type("500")
    wait_step("shares")
    shot("t06_per_trip")
    nxt("generate")
    K.press("F8"); settle()
    wait_step("out")
    with ctx.expect_page() as pop:
        K.press("F8")
    pop.value.close()
    pg.wait_for_timeout(2300); settle()
    K.press("F9"); pg.wait_for_selector("#kb-ask"); K.press("Enter"); settle()
    s = wait_step("adds-up")
    K.press("Control+Enter"); pg.wait_for_timeout(700)
    s = state()
    check(s.get("finished") and "83.33" in ((s.get("message") or {}).get("text") or "") or s["summary"]["done"] == 3,
          "per-trip lesson complete: four shares add back to the one ₹500 trip charge")

    # ---------------------------------------------------------------- lesson: local auto per box
    K.press("Control+Enter"); settle()
    wait_step("idea", "m09-box")
    nxt(("open", "depot"))
    K.press("F3"); K.type("scan"); K.press("Enter"); settle()
    wait_step("depot")
    K.type("KVR"); K.press("Enter"); settle()
    wait_step("invoices")
    for no in ("XY/K/2603", "PQR/K/0311", "XY/K/2604"):
        K.type(no); K.press("Enter"); pg.wait_for_timeout(450)
    wait_step("cross")
    K.press("Enter"); K.press("Space"); pg.wait_for_timeout(400)
    wait_step("auto")
    K.press("Enter"); K.press("ArrowRight"); K.press("ArrowRight"); K.press("Enter"); K.type("LA2"); K.press("Enter")
    wait_step("no-rate")
    shot("t07_no_rate")
    nxt("remove")
    # keyboard: Tab from the scan box to the remove button of the Durg row
    for _ in range(40):
        a = pg.evaluate("[document.activeElement.getAttribute('aria-label') || '', document.activeElement.id]")
        if a[0] == "Remove XY/K/2604":
            break
        K.press("Shift+Tab")
    K.press("Enter")
    wait_step("total")
    K.press("F8"); settle()
    wait_step("out")
    with ctx.expect_page() as pop:
        K.press("F8")
    pop.value.close()
    pg.wait_for_timeout(2300); settle()
    K.press("F9"); pg.wait_for_selector("#kb-ask"); K.press("Enter"); settle()
    wait_step("both")
    K.press("F3"); K.type("scan"); K.press("Enter"); settle()
    K.type("GUMA"); K.press("Enter"); settle()
    K.type("XY/G/2602"); K.press("Enter"); pg.wait_for_timeout(450)
    K.press("Enter"); K.press("ArrowRight"); K.press("ArrowRight"); K.press("Enter"); K.type("LA3"); K.press("Enter")
    pg.wait_for_timeout(800)
    s = state()
    check(s.get("finished") and s["summary"]["done"] == 4, "per-box lesson complete: ₹432, Durg handled, Kishore's two methods seen")

    # ---------------------------------------------------------------- lesson: gate check (needs prepared data)
    K.press("Control+Enter"); settle()
    s = state()
    if s.get("needs") and not s["needs"]["ok"]:
        check(True, "gate lesson: practice data is not at its start - the panel offers 'Prepare practice data'")
        K.press("Control+Enter"); pg.wait_for_selector("#kb-ask"); K.press("y"); settle()
    settle()
    wait_step(("gate", "wrong"), "d02-gate")
    K.press("F3"); K.type("gate check"); K.press("Enter"); settle()
    wait_step("wrong")
    K.type("DS/XX/0000"); K.press("Enter"); settle()
    wait_step("confirm")
    K.type("00002"); K.press("Enter"); settle()
    K.press("F9"); settle()
    wait_step("lr")
    K.press("F3"); K.type("LR pending"); K.press("Enter"); settle()
    wait_step("errors")
    K.press("Control+Enter"); pg.wait_for_timeout(700)
    s = state()
    check(s.get("complete") and s["summary"]["done"] == 5, "Dispatch Operator path COMPLETE (5 of 5)")
    shot("t08_complete")
    K.press("Control+Enter"); settle()      # Leave Training Mode
    pg.goto(B + "/dispatch/pending"); settle()
    check("/dispatch/pending" in pg.url and "TRAINING MODE" not in pg.content(), "after completion: live work is open")

    # ---------------------------------------------------------------- the Master's view, switch OFF again
    pg.click("button:has-text('Sign out')"); settle()
    login("admin", PW)
    pg.goto(B + "/admin/training"); settle()
    row = pg.locator(f"tr:has-text('{OP}')").inner_text()
    check("Completed" in row and "5 / 5" in row, "Master dashboard: operator Completed, 5 / 5, with dates")
    shot("t09_master_dashboard")
    pg.fill("input[name=reason]", "Pilot finished")
    pg.click("form[action='/admin/training/switch'] button"); pg.wait_for_selector("#kb-ask"); K.press("y"); settle()
    check("OFF</span>" in pg.content(), "Master: compulsory training switched OFF again")
    br.close()

after = live_counts()
check(after == before, f"live database unchanged by all training work: {after}")
bad = [w for ok, w in results if not ok]
print(f"\n{len(results)} checks, {len(bad)} failed")
sys.exit(1 if bad else 0)
