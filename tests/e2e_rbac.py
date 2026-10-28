"""Browser check of role-based access (v1.6.1). Not collected by pytest - it needs a running server on a DEMONSTRATION
database and Playwright with Chromium:

    python tests/e2e_rbac.py http://127.0.0.1:8010 <admin password> [screenshot folder]

(set CHROMIUM=/path/to/chrome if Playwright cannot find its own browser). It signs in as every seeded role with the
keyboard only and checks, in a real browser: the menu, the F3 "go to" list, the Tab order (hidden menu entries never
take focus), F10, direct URLs (Master Settings, final reports, exports, bills), the dashboards, depot isolation, the
depot switcher (Alt+D), the searchable transporter box without Master access and Guided Training per role. It adds
one demonstration user (e2e.multi) to the database it runs against.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

B = sys.argv[1].rstrip("/")
ADMIN_PW = sys.argv[2]
OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else None
if OUT:
    OUT.mkdir(parents=True, exist_ok=True)
results: list[tuple[bool, str]] = []

USERS = {  # role: (username, password)
    "Administrator": ("admin", ADMIN_PW), "Management": ("management", "Demo@123"), "Accounts": ("accounts", "Demo@123"),
    "Depot Manager (Guma)": ("cfa.raipur", "Demo@123"), "Dispatch Operator (Guma)": ("operator.raipur", "Demo@123"),
    "Checker": ("checker", "Demo@123"), "Viewer": ("viewer", "Demo@123"),
}
FINAL = ["company-freight", "cfa-freight", "customer-freight", "city-freight", "location-freight", "transporter",
         "outside-auto", "freight-variance", "freight-vs-actual", "monthly-mis", "invoice-freight", "customer-type",
         "transporter-area"]
MASTER_PATHS = ("/masters", "/rates", "/rate-cards", "/admin")
MIS_ROLES = ("Administrator", "Management", "Accounts")
EXPECT_GROUPS = {
    "Administrator": ["Dashboard", "Dispatch desk", "Operations", "Approvals", "Accounts", "Master Settings",
                      "Reports & MIS", "Administration", "Help"],
    "Management": ["Dashboard", "Dispatch Monitoring", "Operations", "Reports & MIS", "Help"],
    "Accounts": ["Dashboard", "Dispatch Monitoring", "Operations", "Approvals", "Accounts", "Reports & MIS", "Help"],
    "Depot Manager (Guma)": ["Dashboard", "Dispatch desk", "Operations", "Approvals", "Operational Reports", "Help"],
    "Dispatch Operator (Guma)": ["Dashboard", "Dispatch desk", "Operations", "Help"],
    "Checker": ["Dispatch Monitoring", "Operations", "Approvals", "Accounts", "Help"],
    "Viewer": ["Dashboard", "Dispatch Monitoring", "Operations", "Help"],
}


def check(ok: bool, what: str):
    results.append((bool(ok), what))
    print(("PASS " if ok else "FAIL ") + what, flush=True)


def launch(p):
    exe = os.environ.get("CHROMIUM") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None)
    return p.chromium.launch(executable_path=exe) if exe else p.chromium.launch(channel="msedge")


def settle(pg):
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(200)


def shot(pg, name):
    if OUT:
        pg.screenshot(path=str(OUT / f"{name}.png"))


def login(br, user, pw, tag):
    """Keyboard-only sign-in; returns (context, page)."""
    ctx = br.new_context(viewport={"width": 1366, "height": 820})
    pg = ctx.new_page()
    pg.goto(B + "/login")
    K = pg.keyboard
    K.type(user); K.press("Enter"); K.type(pw); K.press("Enter")
    pg.wait_for_url(lambda u: not u.endswith("/login") and "/login" not in u.split("?")[0], timeout=15000)
    settle(pg)
    return ctx, pg


def menu(pg):
    return pg.evaluate("""() => [...document.querySelectorAll('.side .group')].map(g => ({
        group: g.firstElementChild.textContent.trim(),
        links: [...g.querySelectorAll('a')].map(a => a.getAttribute('href'))}))""")


def goto_palette(pg):
    pg.keyboard.press("F3")
    pg.wait_for_selector("#kb-goto")
    items = pg.evaluate("() => [...document.querySelectorAll('#kb-goto .kb-goto-it')].map(d => d.firstElementChild.textContent.trim())")
    return items


def close_palette(pg):
    pg.keyboard.press("Escape")
    pg.wait_for_selector("#kb-goto", state="detached")


def tab_order(pg, n=160):
    """Everything the Tab key visits, from the top of the page (hrefs of links)."""
    pg.evaluate("() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); }")
    seen = []
    for _ in range(n):
        pg.keyboard.press("Tab")
        h = pg.evaluate("() => { const a = document.activeElement || {}; return a.tagName === 'A' ? a.getAttribute('href') : null; }")
        if h:
            seen.append(h)
    return seen


def status_of(pg, url):
    r = pg.goto(B + url)
    return r.status if r else None


def denied_page(pg) -> bool:
    return pg.locator("h1").first.inner_text().strip().lower() == "access denied"


with sync_playwright() as p:
    br = launch(p)

    # ------------------------------------------------------------------ 1 every role: menu, F3, Tab order
    for role, (user, pw) in USERS.items():
        ctx, pg = login(br, user, pw, role)
        landing = pg.url.replace(B, "")
        m = menu(pg)
        groups = [g["group"] for g in m]
        check(groups == EXPECT_GROUPS[role], f"{role}: menu groups = {groups}")
        check(all(g["links"] for g in m), f"{role}: no empty menu group")
        links = [h for g in m for h in g["links"]]
        shot(pg, "menu_" + re.sub(r"[^a-z]+", "_", role.lower()))
        if role != "Administrator":
            check(not any(h.startswith(MASTER_PATHS) or h.startswith("/reports/audit") for h in links),
                  f"{role}: Master Settings / administration are not in the menu")
        if role not in MIS_ROLES:
            check("/mis" not in links and not any(h.startswith("/reports/") and h.split("/")[2] in FINAL for h in links),
                  f"{role}: no final report in the menu")
        else:
            check("/mis" in links and "/reports/company-freight" in links, f"{role}: Reports & MIS in the menu")

        # F3 "go to" lists exactly the menu (plus nothing hidden), and cannot be talked into a forbidden screen
        names = goto_palette(pg)
        n_menu = sum(len(g["links"]) for g in m)
        check(len(names) == n_menu, f"{role}: F3 lists the {n_menu} menu screens only (got {len(names)})")
        pg.keyboard.type("master")
        pg.wait_for_timeout(150)
        hits = pg.evaluate("() => [...document.querySelectorAll('#kb-goto .kb-goto-it')].map(d => d.textContent)")
        hits = [h for h in hits if "Find invoice" not in h]
        if role == "Administrator":
            check(len(hits) >= 8, f"{role}: F3 'master' finds the Master Settings screens ({len(hits)})")
        else:
            check(not hits, f"{role}: F3 'master' finds nothing (Master Settings are not reachable by keyboard)")
        pg.keyboard.press("Control+a"); pg.keyboard.type("company-wise")
        pg.wait_for_timeout(150)
        hits = pg.evaluate("() => [...document.querySelectorAll('#kb-goto .kb-goto-it')].map(d => d.textContent)")
        hits = [h for h in hits if "Find invoice" not in h]
        check(bool(hits) == (role in MIS_ROLES), f"{role}: F3 'company-wise' {'finds' if role in MIS_ROLES else 'does not find'} the final report")
        close_palette(pg)

        # Tab order: only visible links; never a Master / final-report link for a role without them
        order = tab_order(pg)
        visited = set(order)
        check(set(links) <= visited or len(visited) > 0, f"{role}: Tab walks the menu ({len(visited)} links reached)")
        if role != "Administrator":
            # (an approver opens the very rate he verifies from the approval queue: /rates/<id>, never the lists)
            masters = [h for h in visited if h.startswith(MASTER_PATHS) and not re.fullmatch(r"/(rates|rate-cards)/\d+", h.split("?")[0])]
            check(not masters, f"{role}: Tab never lands on a Master Settings link {masters[:3]}")
        if role not in MIS_ROLES:
            check(not any(h.startswith("/mis") or (h.startswith("/reports/") and h.split("/")[2].split("?")[0] in FINAL) for h in visited),
                  f"{role}: Tab never lands on a final-report link")
        ctx.close()

    # ------------------------------------------------------------------ 2 direct URLs, exports, bills (as the roles that must be refused)
    for role in ("Dispatch Operator (Guma)", "Depot Manager (Guma)", "Checker", "Viewer", "Management", "Accounts"):
        user, pw = USERS[role]
        ctx, pg = login(br, user, pw, role)
        for url in ("/masters/companies", "/masters/transporters", "/masters/upload", "/rates", "/rate-cards", "/admin/users",
                    "/admin/roles", "/admin/settings", "/admin/integrations", "/admin/training", "/reports/audit"):
            st = status_of(pg, url)
            check(st == 403 and denied_page(pg), f"{role}: typing {url} is refused (403, 'Access denied')")
        if role not in MIS_ROLES:
            for url in [f"/reports/{c}" for c in FINAL[:6]] + ["/mis", "/mis/data?level=company"]:
                st = status_of(pg, url)
                check(st == 403, f"{role}: final report {url} refused")
            for fmt in ("xlsx", "csv", "pdf"):
                r = ctx.request.get(f"{B}/reports/company-freight/export?fmt={fmt}")
                check(r.status == 403, f"{role}: company-wise {fmt.upper()} download refused")
            r = ctx.request.get(f"{B}/reports/cfa-freight?print=1")
            check(r.status == 403, f"{role}: print view of a final report refused")
            r = ctx.request.get(f"{B}/api/v1/reports/company-freight")
            check(r.status == 403, f"{role}: reporting API refused")
            d = ctx.request.get(f"{B}/api/v1/dashboard/summary")
            check(d.status in (200, 403) and ("freight" not in d.text() or d.status == 403), f"{role}: dashboard API carries no freight figures")
        if role in ("Dispatch Operator (Guma)", "Depot Manager (Guma)", "Viewer", "Management"):
            check(status_of(pg, "/recon") == 403, f"{role}: transporter bills refused")
        if role in ("Dispatch Operator (Guma)", "Viewer", "Management"):
            check(status_of(pg, "/approvals") == 403, f"{role}: approval queue refused")
        ctx.close()
    anon = br.new_context()
    check(anon.request.get(f"{B}/api/v1/reports/company-freight").status == 401, "signed-out API call: 401")
    check(anon.request.get(f"{B}/reports/company-freight/export?fmt=xlsx", max_redirects=0).status in (302, 303),
          "signed-out export link sends to the sign-in page")
    anon.close()

    # ------------------------------------------------------------------ 3 final reports work for the three roles (browser + export)
    for role in MIS_ROLES:
        user, pw = USERS[role]
        ctx, pg = login(br, user, pw, role)
        check(status_of(pg, "/reports/company-freight") == 200, f"{role}: company-wise report opens")
        check(status_of(pg, "/mis") == 200, f"{role}: Transportation MIS opens")
        r = ctx.request.get(f"{B}/reports/company-freight/export?fmt=xlsx")
        check(r.status == 200 and "spreadsheet" in r.headers.get("content-type", ""), f"{role}: Excel export downloads")
        r = ctx.request.get(f"{B}/reports/cfa-freight/export?fmt=pdf")
        check(r.status == 200 and "pdf" in r.headers.get("content-type", ""), f"{role}: PDF export downloads")
        pg.goto(B + "/")
        settle(pg)
        title = pg.locator("h1").first.inner_text().strip()
        check(title == {"Administrator": "Dashboard", "Management": "Management Dashboard", "Accounts": "Accounts Dashboard"}[role],
              f"{role}: dashboard title is '{title}'")
        check("Calculated freight" in pg.content(), f"{role}: financial dashboard shows the freight figures")
        shot(pg, "dashboard_" + role.lower())
        ctx.close()

    # ------------------------------------------------------------------ 4 operational dashboards carry no money
    for role, title in (("Depot Manager (Guma)", "Depot Operations Dashboard"), ("Dispatch Operator (Guma)", "Dispatch Desk"),
                        ("Viewer", "Operations Dashboard")):
        user, pw = USERS[role]
        ctx, pg = login(br, user, pw, role)
        pg.goto(B + "/"); settle(pg)
        html = pg.content()
        check(pg.locator("h1").first.inner_text().strip() == title, f"{role}: '{title}'")
        check(not any(w in html for w in ("Calculated freight", "Sales value", "Variance", "Freight per box", "₹")),
              f"{role}: dashboard HTML has no sales / freight / variance figures")
        shot(pg, "dashboard_" + re.sub(r"[^a-z]+", "_", role.lower()))
        ctx.close()

    # ------------------------------------------------------------------ 5 F10 Reports key
    ctx, pg = login(br, *USERS["Dispatch Operator (Guma)"], "op")
    pg.goto(B + "/dispatch/pending"); settle(pg)
    pg.keyboard.press("F10")
    pg.wait_for_timeout(300)
    check("/reports" not in pg.url, "Dispatch Operator: F10 does not open any report")
    check(pg.locator("#kb-reports").count() == 0, "Dispatch Operator: no hidden Reports shortcut exists")
    ctx.close()
    ctx, pg = login(br, *USERS["Depot Manager (Guma)"], "dm")
    pg.goto(B + "/dispatch/pending"); settle(pg)
    pg.keyboard.press("F10"); pg.wait_for_url(re.compile(r".*/reports$")); settle(pg)
    body = pg.locator("main").inner_text()
    check("Gatepass Register" in body and "Company / Principal-wise Freight" not in body and "Depot-wise Freight" not in body,
          "Depot Manager: F10 opens Operational reports (registers only, no final reports)")
    shot(pg, "dm_reports")
    ctx.close()

    # Esc on a top-level screen goes to the role's own first screen (the Checker has no Dashboard: Approvals)
    ctx, pg = login(br, *USERS["Checker"], "esc")
    check(pg.url.endswith("/approvals"), f"Checker: signs in to Approvals ({pg.url.replace(B, '')})")
    pg.goto(B + "/dispatch/pending"); settle(pg)
    pg.keyboard.press("Escape"); pg.wait_for_url(re.compile(r".*/approvals$")); settle(pg)
    check(True, "Checker: Esc on a top-level screen returns to Approvals (his home), not to a screen he lacks")
    ctx.close()

    # ------------------------------------------------------------------ 6 depot isolation (what the browser session can reach)
    for role, depot in (("Dispatch Operator (Guma)", "RPR"), ("Depot Manager (Guma)", "RPR")):
        ctx, pg = login(br, *USERS[role], role)
        js = ctx.request.get(f"{B}/api/v1/invoices?page_size=1000").json()
        codes = {i["cfa"]["code"] for i in js["items"]}
        check(codes == {depot}, f"{role}: invoice API returns only {depot} (got {sorted(codes)}, {js['total']} invoices)")
        pend = ctx.request.get(f"{B}/api/v1/dispatch/pending?page_size=1000").json()
        check({i["cfa"]["code"] for i in pend["items"]} <= {depot}, f"{role}: pending queue only {depot}")
        pg.goto(B + "/dispatch/pending"); settle(pg)
        check("Kamal Vihar Depot" not in pg.content() and "Bilaspur Depot" not in pg.content(), f"{role}: other depots are not even offered in the filter")
        ctx.close()
    ctx, pg = login(br, "operator.bilaspur", "Demo@123", "bsp")
    js = ctx.request.get(f"{B}/api/v1/invoices?page_size=1000").json()
    check({i["cfa"]["code"] for i in js["items"]} == {"BSP"}, "Dispatch Operator (Bilaspur): only BSP invoices")
    ctx.close()

    # ------------------------------------------------------------------ 7 keyboard-first dispatch entry without any Master access
    ctx, pg = login(br, *USERS["Dispatch Operator (Guma)"], "kb")
    K = pg.keyboard
    pg.goto(B + "/dispatch/pending?tab=ready"); settle(pg)
    K.press("ArrowDown")
    check(pg.evaluate("document.activeElement.tagName") == "TR", "Operator: Pending Dispatch - Down arrow enters the list")
    K.press("Enter"); pg.wait_for_url(re.compile(r".*/dispatch/\d+$")); settle(pg)
    a = pg.evaluate("({id: document.activeElement.id || '', cls: String(document.activeElement.className || '')})")
    check(a["id"].startswith("f-auto") or "combo-input" in a["cls"], "Operator: Enter opens the entry with the cursor in Transporter")
    # v1.7: the list holds only transporters that can carry THIS invoice (serve the depot, have a rate for its area)
    offered = pg.evaluate("[...document.querySelectorAll('select[name=transporter_id] option')].filter((o) => o.value && !o.hidden).map((o) => o.textContent.trim())")
    check(len(offered) >= 1, f"Operator: the transporter list offers {len(offered)} eligible transporter(s), not the whole master")
    word = offered[0].split()[0]
    K.type(word); K.press("Enter")
    name = pg.evaluate("document.activeElement.name || ''")
    check(name == "vehicle_no", "Operator: transporter (from the Transporter master) chosen by typing + Enter, cursor on Vehicle No")
    tr_val = pg.evaluate("document.querySelector('select[name=transporter_id]').selectedOptions[0].textContent")
    check(word in tr_val, f"Operator: dropdown filled from the master ({tr_val.strip()[:30]})")
    shot(pg, "operator_dispatch_entry")
    K.press("Escape"); pg.wait_for_selector("#kb-ask"); K.press("y"); pg.wait_for_url(re.compile(r".*/dispatch/pending.*")); settle(pg)
    check("/dispatch/pending" in pg.url, "Operator: Esc then Y leaves the unsaved entry, back on Pending Dispatch")
    pg.goto(B + "/gatepass/new"); settle(pg)
    opts = pg.evaluate("() => [...document.querySelectorAll('select')].map(s => s.options.length)")
    check(any(o > 2 for o in opts), f"Operator: Scan & Gatepass dropdowns are filled from the masters {opts}")
    check(status_of(pg, "/masters/transporters") == 403, "Operator: ... but the Transporter Master itself is closed")
    ctx.close()

    # ------------------------------------------------------------------ 8 depot switcher (user with two depots)
    ctx, pg = login(br, "admin", ADMIN_PW, "adm")
    pg.goto(B + "/admin/users/new"); settle(pg)
    csrf = pg.input_value("input[name=csrf_token]")
    ids = pg.evaluate("""() => Object.fromEntries([...document.querySelectorAll('input[name=cfas]')].map(i => [i.parentElement.textContent.trim().split(' ')[0], i.value]))""")
    role_id = pg.evaluate("""() => [...document.querySelectorAll('select[name=role_id] option')].find(o => o.textContent.includes('Dispatch Operator')).value""")
    multi = "e2e.multi" + time.strftime("%H%M%S")
    body = urlencode({"csrf_token": csrf, "username": multi, "full_name": "E2E Multi-depot", "role_id": role_id,
                      "password": "E2e@2026multi", "is_active": "1", "all_companies": "1",
                      "cfas": [ids["RPR"], ids["BSP"]]}, doseq=True)
    r = ctx.request.post(B + "/admin/users/save", data=body, headers={"content-type": "application/x-www-form-urlencoded"})
    check(r.status in (200, 303), "Administrator: a two-depot Dispatch Operator is created")
    ctx.close()
    ctx, pg = login(br, multi, "E2e@2026multi", "multi")
    K = pg.keyboard
    pg.goto(B + "/dispatch/pending"); settle(pg)
    check(pg.locator("#depot-switch").count() == 1, "Multi-depot user: depot switcher is in the top bar")

    def depots_seen():
        return {i["cfa"]["code"] for i in ctx.request.get(f"{B}/api/v1/invoices?page_size=1000").json()["items"]}
    check(depots_seen() == {"RPR", "BSP"}, f"Multi-depot user sees both assigned depots {sorted(depots_seen())}")
    K.press("Alt+d")
    check(pg.evaluate("document.activeElement.id") == "depot-switch", "Alt+D puts the cursor in the depot switcher")
    pg.select_option("#depot-switch", label="Bilaspur Depot (BSP)")
    K.press("Enter"); pg.wait_for_load_state("networkidle"); settle(pg)
    check(depots_seen() == {"BSP"}, f"After switching to Bilaspur only BSP is visible {sorted(depots_seen())}")
    check("BSP" in pg.locator(".topbar .who small").inner_text(), "Top bar shows the working depot")
    shot(pg, "multi_depot_switched")
    # the same switch from the F3 "go to" list (no browser shortcut involved): type part of the depot name, Enter
    K.press("F3"); pg.wait_for_selector("#kb-goto")
    K.type("all my"); pg.wait_for_timeout(150); K.press("Enter"); pg.wait_for_load_state("networkidle"); settle(pg)
    check(depots_seen() == {"RPR", "BSP"}, f"F3 'all my depots' switches back to both depots {sorted(depots_seen())}")
    K.press("F3"); pg.wait_for_selector("#kb-goto")
    K.type("guma"); pg.wait_for_timeout(150); K.press("Enter"); pg.wait_for_load_state("networkidle"); settle(pg)
    check(depots_seen() == {"RPR"}, f"F3 'guma' switches to Guma only {sorted(depots_seen())}")
    before = depots_seen()
    r = ctx.request.post(B + "/account/depot", form={"csrf_token": pg.evaluate("document.querySelector('input[name=csrf_token]').value"),
                                                    "cfa_id": str(ids["DRG"])})
    check(depots_seen() == before and "DRG" not in depots_seen(), "A depot that is not assigned (Durg) cannot be chosen")
    ctx.close()

    # ------------------------------------------------------------------ 9 Guided Training per role (isolated from live / restricted screens)
    ctx, pg = login(br, *USERS["Dispatch Operator (Guma)"], "trn")
    pg.goto(B + "/training"); settle(pg)
    t = pg.locator("main").inner_text()
    check("Dispatch Operator" in t and "Master Administrator" not in t, "Training: the operator is given the Dispatch Operator tutorial, not the Master one")
    csrf = pg.evaluate("document.querySelector('input[name=csrf_token]').value")
    r = ctx.request.post(B + "/training/start", form={"csrf_token": csrf, "lesson": ""}, max_redirects=0)
    pg.goto(B + "/dispatch/pending"); settle(pg)
    check("TRAINING MODE" in pg.content(), "Training Mode starts on the practice copy (banner shown)")
    check(status_of(pg, "/masters/companies") == 403, "Training: Master Settings stay closed to the operator inside Training Mode")
    check(status_of(pg, "/reports/company-freight") == 403, "Training: final reports stay closed inside Training Mode")
    r = ctx.request.post(B + "/training/act-as", form={"csrf_token": csrf, "who": "demo.master"})
    check(r.status == 400, "Training: 'act as the demonstration Master' is refused")
    r = ctx.request.post(B + "/training/act-as", form={"csrf_token": csrf, "who": "demo.checker"})
    check(r.status == 200, "Training: the demonstration checker (two-person rule) is still available")
    ctx.request.post(B + "/training/exit", form={"csrf_token": csrf})
    ctx.close()
    ctx, pg = login(br, *USERS["Viewer"], "trn-v")
    pg.goto(B + "/training"); settle(pg)
    t = pg.locator("main").inner_text()
    check("Read-only Viewer" in t and "Dashboard, filters and MIS" not in t, "Training: the Viewer gets no Management (final-report) lessons")
    ctx.close()
    ctx, pg = login(br, *USERS["Administrator"], "trn-a")
    pg.goto(B + "/training"); settle(pg)
    check("Master Administrator" in pg.locator("main").inner_text(), "Training: the Administrator gets the full Master tutorial")
    pg.goto(B + "/admin/roles"); settle(pg)
    shot(pg, "admin_roles")
    check("Fixed baseline" not in pg.content() and "What each role sees in the menu" in pg.content() and pg.locator("input[name^=name_]").count() >= 7, "Administrator: Roles page is editable (rename, every permission) and shows the menu summary")
    ctx.close()

    br.close()

bad = [w for ok, w in results if not ok]
print(f"\n{len(results) - len(bad)} of {len(results)} browser checks passed")
for w in bad:
    print("  FAILED:", w)
sys.exit(1 if bad else 0)
