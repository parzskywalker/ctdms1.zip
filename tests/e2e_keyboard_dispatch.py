"""Keyboard-only check of the daily dispatch flow (v1.6). Not collected by pytest - it needs a running server,
the v1.6 demonstration database and Playwright with Microsoft Edge:

    python tests/e2e_keyboard_dispatch.py http://127.0.0.1:8004 <admin password> [screenshot folder]

Run it on a COPY of docs/video_manual/v1.6/demo_database: it first sends two new demonstration invoices for the
Kamal Vihar depot through the API (DM-KB-…), dispatches them, dispatches the waiting invoice 001250 by local auto and
adds a demonstration area. No mouse action is used anywhere: only Tab, Enter, arrows, typing, function keys and Esc.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

B = sys.argv[1].rstrip("/")
PW = sys.argv[2]
OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else None
if OUT:
    OUT.mkdir(parents=True, exist_ok=True)
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str):
    results.append((bool(ok), what))
    print(("PASS " if ok else "FAIL ") + what, flush=True)


with sync_playwright() as p:
    br = p.chromium.launch(channel="msedge")
    ctx = br.new_context(viewport={"width": 1366, "height": 820})
    pg = ctx.new_page()
    K = pg.keyboard
    pg.on("dialog", lambda d: (check(False, "browser dialog appeared: " + d.message), d.dismiss()))

    # two fresh demonstration invoices for the Kamal Vihar depot (Durg and Bhilai customers), sent like an ERP would
    stamp = time.strftime("%H%M%S")
    INV1, INV2 = f"DM-KB{stamp}-1", f"DM-KB{stamp}-2"
    tok = ctx.request.post(B + "/api/v1/auth/token", data={"username": "admin", "password": PW}).json()["access_token"]
    rows = [{"invoice_no": n, "invoice_date": time.strftime("%Y-%m-%d"), "company_code": "DM-XYZ", "cfa_code": "DM-KVR",
             "customer_code": c, "sales_value": v, "boxes": b}
            for n, c, v, b in ((INV1, "DM-C063", "21000", 9), (INV2, "DM-C052", "16500", 7))]
    r = ctx.request.post(B + "/api/v1/invoices/ingest", data={"invoices": rows},
                         headers={"Authorization": "Bearer " + tok})
    check(r.ok and r.json().get("imported") == 2, "two demonstration invoices received through the API")

    def act():
        return pg.evaluate("""() => { const a = document.activeElement || {}; return {tag: a.tagName, id: a.id || '',
            name: a.name || '', type: a.type || '', cls: String(a.className || ''), value: a.value || '',
            text: (a.innerText || '').trim().slice(0, 80)}; }""")

    def shot(n):
        if OUT:
            pg.screenshot(path=str(OUT / f"{n}.png"))

    def settle():
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(250)

    # 1 sign in: Enter moves from username to password, Enter on the password signs in
    pg.goto(B + "/login")
    check(act()["id"] == "username", "login: cursor starts in Username")
    K.type("admin"); K.press("Enter")
    check(act()["id"] == "password", "login: Enter moves to Password")
    K.type(PW); K.press("Enter")
    pg.wait_for_url(B + "/"); settle()
    check(pg.locator("#keybar .kb-item").count() >= 5, "dashboard: key bar shows the keys of the screen")

    # 1b Pending Dispatch: arrows + Enter open an invoice; the per-invoice dispatch form runs on Enter; Esc guards edits
    pg.goto(B + "/dispatch/pending"); settle()
    check(act()["id"] == "pending-q", "Pending Dispatch opens with the cursor in Search")
    K.press("ArrowDown")
    row = act()["text"]
    check(act()["tag"] == "TR", "Down arrow moves into the pending list (%s)" % row[:20])
    K.press("Enter"); pg.wait_for_url(re.compile(r".*/dispatch/\d+$")); settle()
    entry_url = pg.url
    check(act()["id"].startswith("f-auto") or "combo-input" in act()["cls"], "Enter opens the dispatch entry, cursor in Transporter")
    K.type("Shree"); K.press("Enter")
    check(act()["name"] == "vehicle_no", "Transporter typed by name + Enter, cursor moves to Vehicle No")
    K.type("CG04KB1234"); K.press("Enter")
    check(act()["name"] == "lr_no", "Enter moves to LR / Docket No")
    for _ in range(4):
        K.press("Enter")
    a = act()
    check(a["tag"] == "BUTTON" and "Generate Dispatch Slip" in a["text"], "after Remarks the cursor is on Generate Dispatch Slip (%s)" % a["text"][:30])
    shot("00_dispatch_entry")
    K.press("Escape"); pg.wait_for_selector("#kb-ask")
    check("data-no" in pg.evaluate("document.activeElement.outerHTML"), "Esc with unsaved changes asks first; 'Stay here' is pre-selected")
    K.press("Enter")
    check(pg.url == entry_url and pg.locator("#kb-ask").count() == 0, "Enter = stay on the screen, nothing lost")
    K.press("Escape"); pg.wait_for_selector("#kb-ask"); K.press("y"); pg.wait_for_url(re.compile(r".*/dispatch/pending.*")); settle()
    check(act()["tag"] == "TR" and act()["text"][:15] == row[:15], "Y leaves; back on Pending Dispatch the same invoice row is highlighted")

    # 2 F2 on the dashboard = new gatepass; the cursor is in the Depot box
    pg.goto(B + "/"); settle()
    K.press("F2"); pg.wait_for_url(re.compile(r".*/gatepass/new.*")); settle()
    a = act()
    check("combo-input" in a["cls"] and a["id"].startswith("gp-cfa"), "F2 opens New gatepass with the cursor in Depot")
    K.type("KVR"); pg.wait_for_timeout(150)
    shot("01_depot_typed")
    first = pg.locator(".combo-list:not([hidden]) .combo-opt.hi").first.inner_text()
    check("Kamal Vihar" in first, "typing the depot code KVR finds Kamal Vihar Depot (%s)" % first.replace("\n", " "))
    K.press("Enter"); pg.wait_for_url(re.compile(r".*cfa_id=\d+.*")); settle()
    check(act()["id"] == "gp-scan", "after choosing the depot the cursor is in the scan box")

    # 3 invoices: type one, pick one from the waiting list with arrow + Enter
    K.type(INV1); K.press("Enter")
    pg.wait_for_function("document.querySelectorAll('#gp-lines tbody tr').length === 1")
    check(True, "typed invoice %s + Enter adds it to the gatepass" % INV1)
    K.press("ArrowDown")
    a = act()
    check(a["tag"] == "TR" and INV2 in a["text"], "Down arrow moves into the waiting list (%s)" % a["text"][:30])
    K.press("Enter")
    pg.wait_for_function("document.querySelectorAll('#gp-lines tbody tr').length === 2")
    check(act()["id"] == "gp-scan", "Enter on the row adds %s; list empty so the cursor returns to the scan box" % INV2)
    shot("02_two_invoices")

    # 4 Enter on the empty scan box moves to who carries the goods; Enter-Enter through the fields
    K.press("Enter")
    a = act()
    check(a["name"] == "mode", "Enter on the empty scan box moves to Mode of dispatch")
    K.press("Enter")
    a = act()
    check(a["id"] == "gp-tr-q", "Enter moves to Transporter")
    K.type("SRL"); K.press("Enter")
    check(pg.locator("#gp-tr").input_value() != "", "typing SRL + Enter selects Shree Roadlines")
    check(act()["name"] == "pickup_mode", "...and moves on to how goods reach the transporter")
    for want in ("dispatch_date", "dispatch_time", "remarks"):
        K.press("Enter")
        check(act()["name"] == want, "Enter moves to " + want)
    K.type("Keyboard-only dispatch (Demo)")
    K.press("Enter")
    check(act()["name"] == "amount_paid", "Enter moves to Amount paid")
    K.press("Enter")
    a = act()
    check(a["id"] == "gp-submit", "Enter on the last field puts the cursor on Generate gatepass (%s)" % a["text"])
    shot("03_ready_to_generate")
    K.press("Enter"); pg.wait_for_url(re.compile(r".*/gatepass/\d+$")); settle()
    slip_url = pg.url
    check(True, "Enter on the button generates the gatepass: " + pg.locator("h1").inner_text().split("\n")[0])
    shot("04_gatepass")

    # 5 F8 prints (new tab), F9 records gate-out with a Yes/No dialog
    with ctx.expect_page() as pi:
        K.press("F8")
    pop = pi.value
    pop.wait_for_load_state()
    pop.close()
    check(True, "F8 prints the gatepass (print opens in a new tab)")
    pg.wait_for_timeout(2200); settle()
    # Enter on a button that asks first must open the question, never answer it
    pg.evaluate("document.querySelector('[data-key=F9]').focus()")
    K.press("Enter")
    pg.wait_for_timeout(300)
    check(pg.locator("#kb-ask").count() == 1 and "/gatepass/" in pg.url, "Enter on Record gate-out opens the Yes/No question (not answered by the same Enter)")
    K.press("n")
    check(pg.locator("#kb-ask").count() == 0, "N answers No")
    K.press("F9")
    pg.wait_for_selector("#kb-ask")
    a = act()
    check("data-yes" in pg.evaluate("document.activeElement.outerHTML"), "F9 asks to confirm gate-out; Yes is pre-selected")
    shot("05_confirm_gateout")
    K.press("Enter"); pg.wait_for_load_state("networkidle"); settle()
    check("Gone out" in pg.locator("h1").inner_text(), "Enter confirms: gatepass is Gone out (invoices DISPATCHED)")

    # 6 Esc back to the list, arrows + Enter open a record, Esc returns to the same row
    K.press("Escape"); pg.wait_for_url(re.compile(r".*/gatepass$")); settle()
    check(act()["name"] == "q", "Esc goes back to the Gatepasses list with the cursor in Search")
    K.press("ArrowDown"); K.press("ArrowDown")
    second = act()["text"][:22]
    check(act()["tag"] == "TR", "Down arrow moves through the gatepass rows (%s)" % second)
    K.press("Enter"); pg.wait_for_url(re.compile(r".*/gatepass/\d+$")); settle()
    K.press("Escape"); pg.wait_for_url(re.compile(r".*/gatepass$")); settle()
    check(act()["tag"] == "TR" and act()["text"][:22] == second, "after Esc the same row is highlighted - Down for the next one")

    # 6b local auto, per trip: arrows choose the mode, the auto is found by its code, trip charge comes pre-filled
    K.press("F2"); pg.wait_for_url(re.compile(r".*/gatepass/new.*")); settle()
    K.type("BSP"); K.press("Enter"); pg.wait_for_url(re.compile(r".*cfa_id=\d+.*")); settle()
    K.type("001250"); K.press("Enter")
    pg.wait_for_function("document.querySelectorAll('#gp-lines tbody tr').length === 1")
    K.press("Enter")
    check(act()["name"] == "mode", "invoice 001250 added; Enter moves to Mode")
    K.press("ArrowRight"); K.press("ArrowRight")
    check(act()["value"] == "OUTSIDE_AUTO", "Right arrow twice selects Local auto")
    K.press("Enter")
    check(act()["id"] == "gp-auto-q", "Enter moves to Local auto")
    K.type("LA1"); K.press("Enter")
    a = act()
    check(a["id"] == "gp-trip" and a["value"].startswith("500"), "LA1 + Enter picks Sharma Local Auto; trip charge 500 pre-filled")
    K.press("Enter")
    check(act()["id"] == "gp-veh" and act()["value"] == "CG04-AB-1001", "Enter moves to Vehicle (pre-filled from the master)")
    shot("07_local_auto_trip")
    K.press("F8"); pg.wait_for_url(re.compile(r".*/gatepass/\d+$")); settle()
    check("per trip" in pg.content().lower(), "F8 generates the local-auto gatepass (per trip)")

    # 7 danger guard: a red button's Yes/No dialog starts on No; Enter = No
    pg.goto(slip_url); settle()
    r = pg.evaluate("""() => new Promise((res) => { CTDMSKeys.ask('Void?', {danger: true}).then(res);
        setTimeout(() => { res(document.activeElement.hasAttribute('data-no') ? 'no-focused' : 'yes-focused'); }, 50); })""")
    check(r == "no-focused", "for void / cancel / reject the dialog starts on No")
    K.press("Enter")
    check(pg.locator("#kb-ask").count() == 0, "Enter answers No and closes the dialog")

    # 8 master entry from the keyboard: F2, type, Enter, choice box by code, F6 save
    pg.goto(B + "/masters/locations"); settle()
    check(act()["name"] == "q", "master list opens with the cursor in Search")
    K.press("F2"); pg.wait_for_url(re.compile(r".*/masters/locations/new$")); settle()
    check(act()["name"] == "code", "F2 = New area, cursor in Code")
    K.type("DM-KBT"); K.press("Enter"); K.type("Keyboard Test Area (Demo)"); K.press("Enter")
    check(act()["name"] != "code", "Enter moves to the next field")
    K.press("F6"); settle()
    check("/masters/locations" in pg.url and "DM-KBT" in pg.content(), "F6 saves the new area")

    # 9 F3 go-to, F10 reports, F1 help, F4 search
    K.press("F3"); pg.wait_for_selector("#kb-goto")
    K.type("city"); K.press("Enter"); pg.wait_for_url(re.compile(r".*/reports/city-freight.*")); settle()
    check(True, "F3 + 'city' + Enter opens the City-wise report")
    K.press("F10"); pg.wait_for_url(re.compile(r".*/reports$")); settle()
    check(True, "F10 opens Reports")
    K.press("F1"); pg.wait_for_selector("#kb-help")
    shot("06_help")
    K.press("Escape")
    check(pg.locator("#kb-help").count() == 0, "F1 shows the key list, Esc closes it")
    pg.goto(B + "/invoices"); settle()
    K.press("Tab"); K.press("F4")
    check(act()["type"] == "search", "F4 puts the cursor in the search box")
    K.type("ABC-RET"); K.press("Enter"); settle()
    check("ABC-RET" in pg.url, "Enter in search applies it (recognition code ABC-RET)")

    # 10 filter choice boxes: Enter picks, a second Enter applies the filter
    pg.goto(B + "/reports/invoice-freight?date_from=2026-10-01"); settle()
    K.press("F4")  # cursor to the search box at the top, then Tab to the Depot filter
    for _ in range(20):
        K.press("Tab")
        if pg.evaluate("document.activeElement.id").startswith(pg.evaluate("document.querySelector('.filters select[name=cfa_id]').id")):
            break
    K.type("GUMA")
    hi = pg.locator(".combo-list:not([hidden]) .combo-opt.hi").first.inner_text()
    check("Guma" in hi, "report filter: GUMA finds Guma Depot (%s)" % hi.replace("\n", " "))
    K.press("Enter"); K.press("Enter"); settle()
    check("cfa_id=" in pg.url and "cfa_id=&" not in pg.url, "Enter picks the depot, the next Enter applies the filter")

    # 11 code search in a master form: customer type by its short code
    pg.goto(B + "/masters/customers/new"); settle()
    for _ in range(8):
        if pg.evaluate("document.activeElement.id").startswith(pg.evaluate("(document.querySelector('select[name=party_type]') || {}).id || 'x'")):
            break
        K.press("Enter")
    K.type("SST")
    hi = pg.locator(".combo-list:not([hidden]) .combo-opt.hi").first.inner_text()
    check("Super Stockist" in hi, "customer form: customer type code SST finds Super Stockist")
    K.press("Escape")
    K.press("Escape")
    if pg.locator("#kb-ask").count():
        K.press("y")
    pg.wait_for_url(re.compile(r".*/masters/customers$")); settle()
    check(True, "Esc closes the list, a second Esc leaves the form")
    br.close()

bad = [w for ok, w in results if not ok]
print("\n%d checks, %d failed" % (len(results), len(bad)))
sys.exit(1 if bad else 0)
