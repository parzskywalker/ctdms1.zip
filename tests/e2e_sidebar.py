"""Sidebar scroll position in a real browser (v1.7). Not collected by pytest - it needs a running server on a
DEMONSTRATION database and Playwright with Chromium:

    python tests/e2e_sidebar.py http://127.0.0.1:8010 <admin password>

(CHROMIUM=/path/to/chrome if Playwright cannot find its own browser.) The viewport is deliberately short so the
Administrator's long menu scrolls. It checks that the sidebar keeps its scroll position when a screen is opened
by click, by keyboard, by F3, by a form submission and by Back / Forward, and that the selected entry stays visible.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

B = sys.argv[1].rstrip("/")
PW = sys.argv[2]
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str):
    results.append((bool(ok), what))
    print(("PASS " if ok else "FAIL ") + what, flush=True)


def top(pg) -> int:
    return pg.evaluate("Math.round(document.querySelector('.side').scrollTop)")


def active_visible(pg) -> bool:
    return pg.evaluate("""() => {
        const s = document.querySelector('.side'), a = s.querySelector('a.active');
        if (!a) return true;
        const r = a.getBoundingClientRect(), b = s.getBoundingClientRect();
        return r.top >= b.top - 1 && r.bottom <= b.bottom + 1;
    }""")


def settle(pg):
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(150)


def scroll_to(pg, y):
    pg.evaluate("y => { document.querySelector('.side').scrollTop = y; }", y)
    pg.wait_for_timeout(120)  # let the scroll listener store it


exe = os.environ.get("CHROMIUM") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None)
with sync_playwright() as p:
    br = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch(channel="msedge")
    ctx = br.new_context(viewport={"width": 1366, "height": 520})
    pg = ctx.new_page()
    pg.goto(B + "/login")
    pg.keyboard.type("admin"); pg.keyboard.press("Enter"); pg.keyboard.type(PW); pg.keyboard.press("Enter")
    pg.wait_for_url(B + "/"); settle(pg)
    max_scroll = pg.evaluate("(() => { const s = document.querySelector('.side'); return s.scrollHeight - s.clientHeight; })()")
    check(max_scroll > 200, f"menu is long enough to scroll in this window ({max_scroll}px)")
    check(top(pg) == 0 and active_visible(pg), "first screen: sidebar at the top, active entry visible")

    # 1 click an entry far down: the sidebar stays where the user had scrolled it
    scroll_to(pg, max_scroll)
    y0 = top(pg)
    links = pg.locator(".side a[href]")
    target = None
    for i in range(links.count() - 1, -1, -1):
        h = links.nth(i).get_attribute("href")
        if h and h not in ("/",) and not h.startswith("/training"):
            target = links.nth(i); break
    href = target.get_attribute("href")
    target.click(); settle(pg)
    check(abs(top(pg) - y0) <= 1, f"click on {href}: sidebar stays at {y0} (now {top(pg)})")
    check(active_visible(pg), "the selected entry is visible")

    # 2 repeated navigation by click, each from the current position
    for k in range(6):
        scroll_to(pg, max(0, y0 - 40 * k))
        l2 = pg.locator(".side a[href]")
        n = l2.count()
        tgt = l2.nth((n // 2 + k) % n)
        h2 = tgt.get_attribute("href")
        tgt.scroll_into_view_if_needed(); pg.wait_for_timeout(120)
        before = top(pg)
        tgt.click(); settle(pg)
        check(abs(top(pg) - before) <= 1, f"navigation {k + 1} ({h2}): {before} -> {top(pg)}")

    # 3 scroll up to the top and open an entry near the top: no jump to the bottom
    scroll_to(pg, 0)
    pg.locator(".side a[href='/dispatch/pending']").click(); settle(pg)
    check(top(pg) == 0, f"entry near the top opened from the top: stays at 0 (now {top(pg)})")

    # 4 Back / Forward: whatever position the sidebar had when the user left a screen is the one they come back to
    scroll_to(pg, 0)
    pg.locator(".side a[href='/dispatch/pending']").click(); settle(pg)
    scroll_to(pg, 60)
    y1 = top(pg)
    pg.locator(".side a[href='/invoices']").click(); settle(pg)
    y_inv = top(pg)
    check(abs(y_inv - y1) <= 1, f"click on a visible entry keeps the position ({y1} -> {y_inv})")
    pg.go_back(); settle(pg)
    check(abs(top(pg) - y_inv) <= 1 and active_visible(pg), f"Back: sidebar {top(pg)} (left at {y_inv}), entry visible")
    pg.go_forward(); settle(pg)
    check(abs(top(pg) - y_inv) <= 1 and active_visible(pg), f"Forward: sidebar {top(pg)} (left at {y_inv}), entry visible")

    # 5 keyboard: Tab through the sidebar to the next entry, Enter - the position the focus moved to is kept
    scroll_to(pg, max_scroll)
    pg.evaluate("""() => { const s = document.querySelector('.side'), b = s.getBoundingClientRect();
        const a = [...s.querySelectorAll('a')].find((x) => x.getBoundingClientRect().top > b.top + 80); a.focus(); }""")
    pg.keyboard.press("Tab")
    y2 = top(pg)
    h = pg.evaluate("document.activeElement.getAttribute('href')")
    pg.keyboard.press("Enter"); settle(pg)
    check(abs(top(pg) - y2) <= 1 and active_visible(pg), f"keyboard Tab+Enter ({h}): sidebar {y2} -> {top(pg)}, entry visible")

    # 6 F3 palette to a screen far down
    scroll_to(pg, max_scroll)
    y3 = top(pg)
    pg.keyboard.press("F3"); pg.wait_for_selector("#kb-goto")
    pg.keyboard.type("roles"); pg.keyboard.press("Enter"); pg.wait_for_url("**/admin/roles"); settle(pg)
    check(abs(top(pg) - y3) <= 1 and active_visible(pg), f"F3 'roles': sidebar {y3} -> {top(pg)}, entry visible")

    # 7 form submission that redirects (Roles save with no change)
    scroll_to(pg, max_scroll)
    y4 = top(pg)
    pg.locator("button:has-text('Save permissions')").first.click(); pg.wait_for_url("**/admin/roles"); settle(pg)
    check(abs(top(pg) - y4) <= 1, f"form submit + redirect: sidebar {y4} -> {top(pg)}")

    # 8 reload keeps the position of this tab
    y5 = top(pg)
    pg.reload(); settle(pg)
    check(abs(top(pg) - y5) <= 1, f"reload: sidebar {y5} -> {top(pg)}")

    # 9 a deliberately selected entry below the visible part is revealed
    scroll_to(pg, 0)
    pg.goto(B + "/admin/integrations"); settle(pg)
    check(active_visible(pg), "opening a lower screen directly (typed URL) reveals its entry")
    br.close()

bad = [w for ok, w in results if not ok]
print(f"\n{len(results) - len(bad)} of {len(results)} sidebar checks passed")
for w in bad:
    print("  FAILED:", w)
sys.exit(1 if bad else 0)
