"""User access screen (v1.7 Phase 2) in a real browser. Not collected by pytest - it needs a running server on a
DEMONSTRATION database (it creates one user) and Playwright with Chromium:

    python tests/e2e_access.py http://127.0.0.1:8010 <admin password> [screenshot folder]

(CHROMIUM=/path/to/chrome if Playwright cannot find its own browser.) Checks the keyboard-first flow of the new
user screen: scope pickers (type, arrows, Enter, remove), customer types, individual permission cells that follow the
role, the live "Effective access" preview, F6 save, and that the new user really sees only what was assigned.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

B = sys.argv[1].rstrip("/")
PW = sys.argv[2]
SHOTS = Path(sys.argv[3]) if len(sys.argv) > 3 else None
results: list[tuple[bool, str]] = []
NAME = f"e2e.access.{int(time.time()) % 100000}"


def check(ok: bool, what: str):
    results.append((bool(ok), what))
    print(("PASS " if ok else "FAIL ") + what, flush=True)


def settle(pg):
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(150)


def html(pg) -> str:
    for _ in range(10):  # a form post redirects: wait until the new page is there
        try:
            pg.wait_for_load_state("load")
            return pg.content()
        except Exception:
            pg.wait_for_timeout(300)
    return pg.content()


def shot(pg, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def login(br, user, pw):
    ctx = br.new_context(viewport={"width": 1366, "height": 900})
    pg = ctx.new_page()
    pg.goto(B + "/login")
    pg.keyboard.type(user); pg.keyboard.press("Enter"); pg.keyboard.type(pw); pg.keyboard.press("Enter")
    pg.wait_for_url(lambda u: "/login" not in u); settle(pg)
    return ctx, pg


def preview_text(pg) -> str:
    pg.wait_for_timeout(700)  # the preview is debounced
    return pg.inner_text("#eff-card")


exe = os.environ.get("CHROMIUM") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None)
with sync_playwright() as p:
    br = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch(channel="msedge")
    ctx, pg = login(br, "admin", PW)
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))

    pg.goto(B + "/admin/users/new"); settle(pg)
    check(pg.locator("#user-form").count() == 1 and "Operational scope" in pg.inner_text("#user-form"), "new-user screen has identity, scope and individual-permission sections")
    check("Effective access" in preview_text(pg), "live Effective access preview is shown")

    # --- identity: Enter moves to the next field
    pg.fill("input[name=username]", NAME)
    pg.fill("input[name=full_name]", "E2E Access Test")
    pg.select_option("#role-select", label="Dispatch Operator")
    pg.fill("input[name=password]", "E2e@Access2026")
    t = preview_text(pg)
    check("Dispatch" in t and "Reports & MIS" not in t, "preview follows the role (Dispatch Operator: no Reports & MIS)")

    # --- companies / depots
    pg.check("input[name=companies][value='1']"); pg.check("input[name=cfas][value='1']")
    check("No company selected" not in preview_text(pg), "ticking a company clears the 'no company' warning")

    # --- area picker by keyboard
    pg.uncheck("input[name=all_areas]")
    check(not pg.locator("[data-kind=area] .picker-body").is_hidden(), "unticking All areas opens the area picker")
    check("No area selected" in preview_text(pg), "warning: no area selected = sees nothing")
    q = pg.locator("[data-kind=area] .picker-q")
    q.click(); q.fill(""); pg.keyboard.type("rai")
    pg.wait_for_selector("[data-kind=area] .picker-results li:has-text('Raipur')", state="visible")
    texts = pg.locator("[data-kind=area] .picker-results li").all_inner_texts()
    check(any("Raipur" in x for x in texts) and not any("Bilaspur" in x for x in texts), f"typing 'rai' lists matching areas only {texts}")
    pg.keyboard.press("Enter")  # the first hit is highlighted
    pg.wait_for_timeout(250)
    chips = pg.locator("[data-kind=area] .chip").all_inner_texts()
    check(len(chips) == 1 and "Raigarh" in chips[0] or "Raipur" in chips[0], f"Enter adds the highlighted area as a chip {chips}")
    check(pg.url.endswith("/admin/users/new"), "Enter in the picker never submits the form")
    check("No area selected" not in preview_text(pg), "warning clears once an area is chosen")
    # remove it with the mouse, add it back with Enter on the first hit
    pg.locator("[data-kind=area] .chip button").first.click()
    check(pg.locator("[data-kind=area] .chip").count() == 0, "× removes the chip")
    q.click(); pg.keyboard.type("raipur"); pg.keyboard.press("Enter")  # fast typist: Enter before the list has refreshed
    pg.wait_for_timeout(600)
    chips = pg.locator("[data-kind=area] .chip").all_inner_texts()
    check(len(chips) == 1 and "Raipur" in chips[0], f"Enter right after typing takes the first hit of THIS text, never a stale one {chips}")
    q.fill(""); pg.keyboard.type("rai"); pg.wait_for_selector("[data-kind=area] .picker-results li:has-text('Raigarh')", state="visible")  # Raipur is already chosen: not offered again
    pg.keyboard.press("Escape"); pg.wait_for_timeout(250)
    check(pg.locator("[data-kind=area] .picker-results").is_hidden() and pg.locator("#kb-ask").count() == 0,
          "Esc closes an open result list first (no 'leave this screen?' question)")

    # --- customer types
    pg.uncheck("#all-ctypes")
    check(not pg.locator("#ctype-list").is_hidden(), "unticking All customer types shows the type list")
    pg.check("input[name=customer_types][value='STOCKIST']")

    # --- individual permission cells follow the role
    pg.locator("summary:has-text('Individual permission changes')").click()
    check(pg.locator("select[data-perm='dispatch.operate'] option[value=GRANT]").evaluate("o => o.disabled") is True, "Give is disabled for what the role already has")
    check(pg.locator("select[data-perm='mis.view'] option[value=DENY]").evaluate("o => o.disabled") is True, "Take away is disabled for what the role lacks")
    pg.select_option("select[data-perm='mis.view']", "GRANT")
    t = preview_text(pg)
    check("+ mis.view" in t and "Reports & MIS" in t, "granting mis.view shows it in the preview and adds the Reports & MIS menu")
    pg.select_option("#role-select", label="Read-only Viewer")
    check(pg.locator("[data-role-has='dispatch.operate']").inner_text() == "No", "changing the role updates what 'role gives' says")
    pg.select_option("#role-select", label="Dispatch Operator")
    shot(pg, "access_new_user")

    # --- save without a reason is refused, with a reason it is saved (F6)
    pg.keyboard.press("F6"); pg.wait_for_timeout(800)
    check("Give a reason" in html(pg), "saving individual changes without a reason is refused")
    pg.goto(B + "/admin/users/new"); settle(pg)
    pg.fill("input[name=username]", NAME); pg.fill("input[name=full_name]", "E2E Access Test")
    pg.select_option("#role-select", label="Dispatch Operator"); pg.fill("input[name=password]", "E2e@Access2026")
    pg.uncheck("input[name=must_change]")
    pg.check("input[name=companies][value='1']"); pg.check("input[name=cfas][value='1']")
    pg.uncheck("input[name=all_areas]")
    q = pg.locator("[data-kind=area] .picker-q"); q.click(); pg.keyboard.type("raipur"); pg.keyboard.press("Enter")
    pg.wait_for_timeout(600)
    pg.uncheck("#all-ctypes"); pg.check("input[name=customer_types][value='STOCKIST']")
    pg.locator("summary:has-text('Individual permission changes')").click()
    pg.select_option("select[data-perm='mis.view']", "GRANT")
    pg.fill("#override-reason", "e2e: covers Accounts")
    pg.keyboard.press("F6"); pg.wait_for_url("**/admin/users/*"); settle(pg)
    check("/admin/users/" in pg.url and "saved" in html(pg).lower(), "F6 saves the user")
    check(pg.locator("[data-kind=area] .chip").count() == 1 and pg.locator("input[name=customer_types][value='STOCKIST']").is_checked(), "the saved scope is shown again on the user screen")
    check(pg.locator("select[data-perm='mis.view']").input_value() == "GRANT", "the saved individual permission is shown again")
    shot(pg, "access_saved_user")
    pg.goto(B + "/admin/users"); settle(pg)
    row = pg.locator(f"tr:has-text('{NAME}')").inner_text()
    check("1 area(s)" in row and "STOCKIST" in row and "+1" in row, f"user list summarises the scope and the exception: {row!r}")
    ctx.close()

    # --- the new user really sees only what was assigned
    ctx2, pg2 = login(br, NAME, "E2e@Access2026")
    pg2.goto(B + "/mis"); settle(pg2)
    check(pg2.url.endswith("/mis") and "Not allowed" not in pg2.inner_text("body")[:300], "the individually granted Final Reports / MIS opens for them")
    pg2.goto(B + "/invoices"); settle(pg2)
    body = pg2.inner_text("main")
    check("Raigarh" not in body and "Retailer" not in body.replace("Customer type", ""), "their invoice list is limited to the assigned area and customer type")
    r = pg2.request.get(B + "/admin/users")
    check(r.status == 403, "they cannot open user administration")
    ctx2.close()
    check(not errors, f"no JavaScript errors on the user screen {errors}")
    br.close()

bad = [w for ok, w in results if not ok]
print(f"\n{len(results) - len(bad)} of {len(results)} access-screen checks passed")
for w in bad:
    print("  FAILED:", w)
sys.exit(1 if bad else 0)
