"""Clean (caption-free) screenshots of the finished demonstration database, for the PDF manual.
Run with the playwright venv python after the recording:  shots_clean.py <out_dir> <admin_password>"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
PW = sys.argv[2]
B = "http://127.0.0.1:8002"

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge")
    c = b.new_context(viewport={"width": 1366, "height": 800})
    pg = c.new_page()

    def shot(name, full=False):
        pg.screenshot(path=str(OUT / f"{name}.png"), full_page=full)

    def go(u):
        pg.goto(B + u)
        pg.wait_for_load_state("networkidle")

    def link(page, text):
        go(page)
        pg.locator(f'main a:has-text("{text}")').first.click()
        pg.wait_for_load_state("networkidle")

    go("/login")
    shot("login")
    pg.fill("input[name=username]", "admin"); pg.fill("input[name=password]", PW); pg.click("button[type=submit]")
    pg.wait_for_load_state("networkidle")
    pages = [("dashboard", "/"), ("integrations", "/admin/integrations"), ("cfas", "/masters/cfas"),
             ("locations", "/masters/locations"), ("companies", "/masters/companies"), ("transporters", "/masters/transporters"),
             ("autos", "/masters/autos"), ("customers", "/masters/customers"), ("erp_mappings", "/masters/erp-mappings"),
             ("rate_cards", "/rate-cards"), ("approvals", "/approvals"), ("imports", "/imports"), ("invoices", "/invoices"),
             ("gatepasses", "/gatepass"), ("gate", "/gatepass/gate"), ("lr_pending", "/gatepass/lr-pending"),
             ("track", "/track"), ("deliveries", "/deliveries"), ("alerts", "/notifications"), ("recon", "/recon"),
             ("reports", "/reports"), ("mis", "/mis"), ("rep_transporter", "/reports/transporter"),
             ("rep_sku", "/reports/sku-dispatch"), ("rep_delivery", "/reports/delivery-performance"),
             ("rep_gatepass", "/reports/gatepass-register"), ("rep_company", "/reports/company-freight"),
             ("rep_irregular", "/reports/bill-irregularities"), ("subs", "/report-subscriptions"), ("users", "/admin/users"),
             ("roles", "/admin/roles"), ("settings", "/admin/settings"), ("audit", "/reports/audit"),
             ("exceptions", "/exceptions"), ("invoice_new", "/invoices/new"), ("gatepass_new", "/gatepass/new"),
             ("cust_new", "/masters/customers/new"), ("password", "/account/password")]
    for n, u in pages:
        go(u)
        shot(n)
    shot_full = ["invoices"]
    go("/invoices"); shot("invoices_full", full=True)
    go("/imports"); pg.locator('main a:has-text("IMP2610-0002")').first.click(); pg.wait_for_load_state("networkidle")
    shot("import_marg", full=True)
    link("/admin/integrations", "DM-CBO"); shot("integration_cbo")
    link("/masters/customers", "DM-C01"); shot("customer_detail")
    go("/rate-cards/1"); shot("rate_card_t1")
    link("/invoices", "DEMO-SAP-1003"); shot("invoice_detail", full=True)
    go("/gatepass"); pg.locator('main a:has-text("/00001")').first.click(); pg.wait_for_load_state("networkidle"); shot("gatepass_g1")
    go("/gatepass"); pg.locator('main a:has-text("/00002")').first.click(); pg.wait_for_load_state("networkidle"); shot("gatepass_g2")
    go("/gatepass/1/labels"); shot("labels")
    go("/track?q=BCM-7002"); shot("track_result", full=True)
    go("/recon"); pg.locator('main a:has-text("ART/2610/001")').first.click(); pg.wait_for_load_state("networkidle"); shot("bill1", full=True)
    go("/recon"); pg.locator('main a:has-text("BCM/2610/007")').first.click(); pg.wait_for_load_state("networkidle"); shot("bill2", full=True)
    b.close()
print("clean screenshots:", len(list(OUT.glob("*.png"))))
