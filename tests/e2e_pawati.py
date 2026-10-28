"""Local-auto path in a real browser (v1.7 Phase 6): per-invoice receiving transporters, the split, the gatepass screen and
the Garage Pawati sheet. Not collected by pytest - needs a running server on a DEMONSTRATION database and Playwright:

    python tests/e2e_pawati.py http://127.0.0.1:8010 <admin password> [screenshot folder]
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
    tok = ctx.request.post(B + "/api/v1/auth/token", data={"username": "admin", "password": PW}).json()["access_token"]
    hdr = {"Authorization": "Bearer " + tok}
    cust = next(c["code"] for c in ctx.request.get(B + "/api/v1/masters/customers", headers=hdr).json() if c.get("location_id"))
    stamp = str(int(time.time()))[-7:]
    nos = []
    for i in range(2):
        row = {"invoice_no": f"E2EPW{stamp}{i}", "invoice_date": date.today().isoformat(), "company_code": "COA", "cfa_code": "RPR",
               "customer_code": cust, "sales_value": "8000", "boxes": 6}
        r = ctx.request.post(B + "/api/v1/invoices/ingest", headers=hdr, data={"invoices": [row]}).json()
        check(r.get("imported") == 1, f"invoice {row['invoice_no']} made")
        nos.append(row["invoice_no"])
    pg.goto(B + "/gatepass/new"); settle(pg)
    rpr = pg.evaluate("(() => { const o = Array.from(document.querySelectorAll('#gp-cfa option')).find((x) => x.dataset.code === 'RPR'); return o ? o.value : ''; })()")
    pg.goto(B + f"/gatepass/new?cfa_id={rpr}"); settle(pg)
    for no in nos:
        pg.fill("#gp-scan", no); pg.keyboard.press("Enter"); pg.wait_for_timeout(400)
    check(pg.locator("#gp-lines tr").count() >= 2, "both invoices are on the builder")
    check(pg.locator(".th-recv").first.is_hidden(), "ordinary path: no receiving-transporter column")
    pg.check("input[name=mode][value=TRANSPORTER]")
    pg.check("input[name=pickup_mode][value=OUTSIDE_AUTO]"); pg.wait_for_timeout(300)
    check(pg.locator(".th-recv").first.is_visible(), "local-auto path: the receiving-transporter column appears")
    opts = pg.evaluate("Array.from(document.querySelectorAll('.rv1')[0].options).filter((o) => o.value).map((o) => o.value)")
    check(len(opts) >= 1, f"receiving transporters are offered per invoice ({len(opts)})")
    auto = pg.evaluate("(() => { const o = Array.from(document.querySelectorAll('#gp-auto option')).find((x) => x.value); return o ? o.value : ''; })()")
    pg.select_option("#gp-auto", auto)
    if pg.locator("#gp-trip").count():
        pg.fill("#gp-trip", "300")
    pg.locator(".rv1").nth(0).select_option(opts[0])
    if len(opts) > 1:  # second invoice split across two receivers
        pg.locator(".rv1").nth(1).select_option(opts[0])
        pg.locator(".rv-split").nth(1).click()
        pg.locator(".rv2").nth(1).select_option(opts[1])
        pg.locator(".rvn").nth(1).fill("2"); pg.keyboard.press("Tab"); pg.wait_for_timeout(300)
    shot(pg, "pw_builder")
    check(pg.locator("#gp-submit").is_enabled(), f"Generate is enabled ({pg.inner_text('#gp-why')[:80]!r})")
    pg.click("#gp-submit"); pg.wait_for_url(re.compile(r".*/gatepass/\d+$")); settle(pg)
    body = pg.inner_text("main")
    check("Garage Pawati" in body, "the gatepass shows the Garage Pawati panel")
    shot(pg, "pw_detail")
    ids = pg.evaluate("Array.from(document.querySelectorAll('input[name=ids]')).map((x) => x.value)")
    check(len(ids) >= 1, f"Pawatis listed for printing ({len(ids)})")
    pg.goto(B + "/pawati?state=unprinted"); settle(pg)
    check("Pawati" in pg.inner_text("main"), "the Pawati register opens")
    shot(pg, "pw_register")
    check(not errors, f"no JavaScript errors {errors}")
    br.close()

bad = [w for ok, w in results if not ok]
print(f"\n{len(results) - len(bad)} of {len(results)} Pawati checks passed")
for w in bad:
    print("  FAILED:", w)
sys.exit(1 if bad else 0)
