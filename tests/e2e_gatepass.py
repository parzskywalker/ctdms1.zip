"""Gatepass builder (v1.7 Phase 3) in a real browser. Not collected by pytest - it needs a running server on a
DEMONSTRATION database (it makes one gatepass and briefly changes one transporter's depots) and Playwright:

    python tests/e2e_gatepass.py http://127.0.0.1:8010 <admin password> [screenshot folder]

(CHROMIUM=/path/to/chrome if Playwright cannot find its own browser.) Checks: invoices of two companies need the
"Group across companies" tick, only eligible transporters are offered (a depot restriction hides one), the override box
appears for a person who may use it, the consignment panel, and what the gatepass screen shows afterwards.
"""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

B = sys.argv[1].rstrip("/")
PW = sys.argv[2]
SHOTS = Path(sys.argv[3]) if len(sys.argv) > 3 else None
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str):
    results.append((bool(ok), what))
    print(("PASS " if ok else "FAIL ") + what, flush=True)


def settle(pg):
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(200)


def shot(pg, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def eligible_options(pg) -> list[str]:
    return pg.evaluate("""() => Array.from(document.querySelectorAll('#gp-tr option'))
        .filter((o) => o.value && !o.hidden && !o.disabled).map((o) => o.value + '|' + (o.dataset.name || o.text))""")


exe = os.environ.get("CHROMIUM") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None)
with sync_playwright() as p:
    br = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch(channel="msedge")
    ctx = br.new_context(viewport={"width": 1366, "height": 1000})
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(B + "/login")
    pg.keyboard.type("admin"); pg.keyboard.press("Enter"); pg.keyboard.type(PW); pg.keyboard.press("Enter")
    pg.wait_for_url(B + "/"); settle(pg)

    # the Guma depot id, and two fresh invoices of different companies (made through the API, so the test can be repeated)
    pg.goto(B + "/gatepass/new"); settle(pg)
    rpr = pg.evaluate("(() => { const o = Array.from(document.querySelectorAll('#gp-cfa option')).find((x) => x.dataset.code === 'RPR'); return o ? o.value : ''; })()")
    tok = ctx.request.post(B + "/api/v1/auth/token", data={"username": "admin", "password": PW}).json()["access_token"]
    hdr = {"Authorization": "Bearer " + tok}
    cust = next(c["code"] for c in ctx.request.get(B + "/api/v1/masters/customers", headers=hdr).json() if c.get("location_id"))
    stamp = str(int(time.time()))[-7:]
    made = []
    for i, (co, cu) in enumerate((("COA", cust), ("COB", cust))):  # one customer + area, two companies
        row = {"invoice_no": f"E2EGP{stamp}{i}", "invoice_date": date.today().isoformat(), "company_code": co, "cfa_code": "RPR",
               "customer_code": cu, "sales_value": "9000", "boxes": 5 + i}
        r = ctx.request.post(B + "/api/v1/invoices/ingest", headers=hdr, data={"invoices": [row]}).json()
        made.append({"no": row["invoice_no"], "co": co, "ok": r.get("imported") == 1})
    first, second = made
    check(first["ok"] and second["ok"], f"two fresh Guma invoices of two companies were made {first['no']} / {second['no']}")
    pg.goto(B + f"/gatepass/new?cfa_id={rpr}"); settle(pg)

    # ---- one company: no tick asked for
    pg.fill("#gp-scan", first["no"]); pg.keyboard.press("Enter"); pg.wait_for_timeout(500)
    check(pg.locator("#gp-cross-box").is_hidden(), "one company: the 'Group across companies' box is not shown")
    check(pg.locator("#gp-cons-list li").count() == 1, "the consignment panel lists the customer + area")
    n_one = len(eligible_options(pg))
    check("can carry" in pg.inner_text("#gp-elig-msg"), f"the screen says who can carry these goods ({pg.inner_text('#gp-elig-msg')[:90]!r})")
    total = pg.evaluate("document.querySelectorAll('#gp-tr option[value]:not([value=\"\"])').length")
    check(0 < n_one <= total, f"only eligible transporters are offered ({n_one} of {total})")

    # ---- two companies: the tick is required
    pg.fill("#gp-scan", second["no"]); pg.keyboard.press("Enter"); pg.wait_for_timeout(500)
    check(pg.locator("#gp-cross-box").is_visible(), "two companies: the 'Group across companies' box appears")
    opts = eligible_options(pg)
    check(len(opts) > 0, f"transporters for both areas are offered ({len(opts)})")
    pg.focus("#gp-scan"); pg.keyboard.press("Enter"); pg.wait_for_timeout(200)
    check(pg.evaluate("document.activeElement.id") == "gp-cross", "Enter on the empty scan box goes to the tick box first")
    pg.select_option("#gp-tr", opts[0].split("|")[0]); pg.wait_for_timeout(300)
    check(pg.locator("#gp-submit").is_disabled() and "Group across companies" in pg.inner_text("#gp-why"),
          "generate is blocked until the tick is given, and says why")
    shot(pg, "gp_two_companies")
    pg.focus("#gp-cross"); pg.keyboard.press("Space"); pg.wait_for_timeout(300)
    check(pg.locator("#gp-submit").is_enabled(), "ticking 'Group across companies' enables Generate")
    pg.keyboard.press("Enter"); pg.wait_for_timeout(200)
    check(pg.evaluate("!!document.activeElement.closest('#gp-who')"), "Enter on the ticked box moves on to who carries the goods")
    tr_id = opts[0].split("|")[0]

    # ---- a depot restriction hides the transporter and the override brings it back (audited)
    pg.goto(B + f"/masters/transporters/{tr_id}"); settle(pg)
    boxes = pg.locator("input[name=served_depots]")
    check(boxes.count() >= 2, "the transporter screen lists the depots it can serve")
    other = pg.evaluate(f"(() => {{ const b = Array.from(document.querySelectorAll('input[name=served_depots]')).find((x) => x.value !== '{rpr}'); return b ? b.value : ''; }})()")
    pg.check(f"input[name=served_depots][value='{other}']")
    pg.keyboard.press("F6"); pg.wait_for_url(re.compile(r".*/masters/transporters/\d+$")); settle(pg)
    try:
        pg.goto(B + f"/gatepass/new?cfa_id={rpr}"); settle(pg)
        for no in (first["no"], second["no"]):
            pg.fill("#gp-scan", no); pg.keyboard.press("Enter"); pg.wait_for_timeout(400)
        names = [o.split("|")[0] for o in eligible_options(pg)]
        check(tr_id not in names, "a transporter that does not serve this depot is no longer offered")
        check(pg.locator("#gp-elig-box").is_visible(), "a person who may override is offered the override box")
        pg.check("#gp-override"); pg.wait_for_timeout(300)
        names = [o.split("|")[0] for o in eligible_options(pg)]
        check(tr_id in names, "with the override ticked the transporter can be chosen again")
        pg.select_option("#gp-tr", tr_id); pg.wait_for_timeout(200)
        pg.focus("#gp-cross"); pg.keyboard.press("Space"); pg.wait_for_timeout(200)
        check(pg.locator("#gp-submit").is_disabled() and "reason" in pg.inner_text("#gp-why"), "the override needs a reason")
        pg.fill("#gp-override-reason", "e2e: only vehicle going that way today"); pg.wait_for_timeout(300)
        check(pg.locator("#gp-submit").is_enabled(), "with a reason Generate is enabled")
        shot(pg, "gp_override")
        pg.keyboard.press("F8"); pg.wait_for_url(re.compile(r".*/gatepass/\d+$")); settle(pg)
        body = pg.inner_text("main")
        check("different companies" in body and "does not serve this depot" in body, "the gatepass shows both audited choices")
        check("How freight was calculated" in body, "the gatepass shows how the freight was calculated")
        shot(pg, "gp_detail")
    finally:  # leave the demonstration transporter as it was: serves every depot
        pg.goto(B + f"/masters/transporters/{tr_id}"); settle(pg)
        pg.evaluate("document.querySelectorAll('input[name=served_depots]').forEach((b) => { b.checked = false; })")
        pg.keyboard.press("F6"); settle(pg)
    check(not errors, f"no JavaScript errors {errors}")
    br.close()

bad = [w for ok, w in results if not ok]
print(f"\n{len(results) - len(bad)} of {len(results)} gatepass checks passed")
for w in bad:
    print("  FAILED:", w)
sys.exit(1 if bad else 0)
