"""Caption-free screenshots of the finished v1.6 demonstration database (for the PDF manual).
Run with the playwright venv python:  shots_clean_v16.py <out_dir> <admin_password> <port>"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
PW = sys.argv[2]
B = f"http://127.0.0.1:{sys.argv[3]}"

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge")
    pg = b.new_context(viewport={"width": 1366, "height": 800}).new_page()

    def go(u):
        pg.goto(B + u)
        pg.wait_for_load_state("networkidle")

    def shot(name, full=False):
        pg.screenshot(path=str(OUT / f"{name}.png"), full_page=full)

    go("/login")
    pg.fill("input[name=username]", "admin"); pg.fill("input[name=password]", PW); pg.click("button[type=submit]")
    pg.wait_for_load_state("networkidle")
    for n, u, full in [
        ("dashboard", "/", False), ("upload", "/masters/upload", True), ("upload_detail", "/masters/upload/1?show=all", True),
        ("companies", "/masters/companies", False), ("depots", "/masters/depots", False),
        ("customer_types", "/masters/customer-types", False), ("customers", "/masters/customers", False),
        ("transporters", "/masters/transporters", False), ("autos", "/masters/autos", False),
        ("auto_rates", "/masters/local-auto-rates", False), ("markings", "/masters/invoice-markings", False),
        ("erp_mappings", "/masters/erp-mappings?entity=CUSTOMER", False), ("recognition", "/masters/recognition", True),
        ("register", "/invoices?date_from=2026-10-01", True), ("search_prefix", "/invoices?rcode=ABC-RET&date_from=2026-10-01", False),
        ("gatepasses", "/gatepass", False), ("rep_trips", "/reports/outside-auto?date_from=2026-10-01", False),
        ("rep_city", "/reports/city-freight?date_from=2026-10-01", False),
        ("rep_depot", "/reports/cfa-freight?date_from=2026-10-01", False),
        ("rep_invoice", "/reports/invoice-freight?date_from=2026-10-01", True),
        ("rep_ctype", "/reports/customer-type?date_from=2026-10-01", False), ("settings", "/admin/settings", False),
        ("gatepass_new", "/gatepass/new", False), ("auto_new", "/masters/autos/new", True)]:
        go(u)
        shot(n, full)
    go("/masters/depots"); pg.locator('main a:has-text("DM-GUMA")').first.click(); pg.wait_for_load_state("networkidle")
    shot("depot_form", True)
    go("/masters/customers"); pg.locator('main a:has-text("DM-C025")').first.click(); pg.wait_for_load_state("networkidle")
    shot("customer_form", True)
    go("/invoices?q=001245"); shot("invoice_001245")
    go("/gatepass")
    for label, name in (("/DM-GUMA/2627/00001", "gp_trip"), ("/DM-KVR/2627/00001", "gp_box"), ("/DM-BSPD/2627/00001", "gp_long")):
        go("/gatepass")
        pg.locator(f'main a:has-text("{label}")').first.click(); pg.wait_for_load_state("networkidle")
        shot(name, True)
    # v1.6 keyboard-first screens (keys pressed like an operator would)
    K = pg.keyboard
    go("/"); K.press("F1"); pg.wait_for_selector("#kb-help"); pg.wait_for_timeout(300); shot("kb_help"); K.press("Escape")
    go("/gatepass/new"); K.type("KVR"); pg.wait_for_timeout(400); shot("kb_depot"); K.press("Escape")
    pg.locator("#gp-tr-q").focus(); K.type("SRL"); pg.wait_for_timeout(400); shot("kb_transporter"); K.press("Escape")
    go("/gatepass"); K.press("ArrowDown"); K.press("ArrowDown"); pg.wait_for_timeout(300); shot("kb_list")
    go("/gatepass")
    pg.locator('main tr:has(.badge:text-is("Printed")) a').first.click(); pg.wait_for_load_state("networkidle")
    K.press("F9"); pg.wait_for_selector("#kb-ask"); pg.wait_for_timeout(300); shot("kb_confirm"); K.press("n")
    b.close()
print("clean screenshots:", len(list(OUT.glob("*.png"))))
