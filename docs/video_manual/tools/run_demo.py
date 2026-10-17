"""Runs the full demonstration against a CLEAN database through the real web screens.

  python run_demo.py dry                -> fast run, no video, collects the narration text (narration.json)
  python run_demo.py record             -> real-time run with video; needs durations.json from the voice step
Stages can be limited:  python run_demo.py dry masters   (run only up to that stage)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import demo_data as D
from demolib import Demo

ROOT = Path(r"D:/ctdms")
PY = ROOT / ".venv/Scripts/python.exe"
WORK = Path(os.environ["DEMO_WORK"])  # scratch folder (database, video, wavs)
OUT = ROOT / "docs/video_manual"
FILES = OUT / "demo_files"
PORT = 8002
BASE = f"http://127.0.0.1:{PORT}"
ADMIN_PW = "Master@2026"
USER_PW = "Demo@Pass2026"
state: dict = {}


def env():
    e = dict(os.environ)
    e.update(PYTHONUTF8="1", CTDMS_DATABASE_URL=f"sqlite:///{(WORK / 'demo.db').as_posix()}",
             CTDMS_ENVIRONMENT="development", CTDMS_SHOW_DEMO_LOGINS="false", CTDMS_SEED_DEMO="false",
             CTDMS_DATA_DIR=str(WORK / "appdata"), CTDMS_UPLOAD_DIR=str(WORK / "appdata" / "uploads"),
             CTDMS_BACKUP_DIR=str(WORK / "appdata" / "backups"), CTDMS_LOG_DIR=str(WORK / "appdata" / "logs"))
    return e


def kill_server():
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match '--port %d' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" % PORT],
                   capture_output=True)
    time.sleep(1.5)


def reset_and_start():
    kill_server()
    for p in WORK.glob("demo.db*"):
        p.unlink()
    for sub in ("uploads",):
        pass
    e = env()
    subprocess.run([str(PY), "-m", "app.cli", "init-db"], cwd=ROOT, env=e, check=True, capture_output=True)
    subprocess.run([str(PY), "-m", "app.cli", "create-admin", "--username", "admin", "--password", ADMIN_PW,
                    "--full-name", "Master Administrator"], cwd=ROOT, env=e, check=True, capture_output=True)
    log = open(WORK / "server.log", "w")
    subprocess.Popen([str(PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
                     cwd=ROOT, env=e, stdout=log, stderr=log)
    import urllib.request
    for _ in range(40):
        try:
            urllib.request.urlopen(BASE + "/login", timeout=2)
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("server did not start")


# =====================================================================================================
def stage_intro(d: Demo):
    d.login("admin", ADMIN_PW)
    d.title_card("CTDMS - Master's Walkthrough",
                 "Central Transport & Dispatch Management System  |  Live demonstration on a clean database",
                 "Welcome. This video shows CTDMS from the very first login, exactly as the first master user "
                 "would see it. The database is completely empty. Everything you will see being created is a "
                 "demonstration entry, marked D M, or Demo, so it can be removed before real use.")
    d.go("/")
    d.say("This is the Management Dashboard. Every figure is zero because nothing has been entered yet. "
          "The menu on the left has five groups: Dispatch desk, Operations, Masters, Reports and Administration.")
    d.pause(2)
    d.shot("01_dashboard_empty")


def stage_erps(d: Demo):
    d.title_card("Step 1 - Source systems (ERPs)", "Administration > Integration Settings",
                 "Step one: tell CTDMS which billing systems exist. CTDMS sits above them. It never replaces them.")
    d.go("/admin/integrations")
    d.say("Open Administration, Integration Settings. We add four billing systems: SAP, MARG, CBO and Intrect. "
          "Each needs a short code, a name, and the way its data arrives.")
    for code, name, adapter, notes, active, key in D.ERPS:
        d.go("/admin/integrations")
        d.fill("code", code)
        d.fill("name", name)
        d.select("adapter", value=adapter)
        d.btn("Add ERP")
        d.pause(0.5)
    d.shot("02_erp_list")
    d.say("Now each one is opened to record its status. SAP is working through an Excel upload.")
    for code, name, adapter, notes, active, key in D.ERPS:
        d.go("/admin/integrations")
        d.click(f'main a:has-text("{code}")')
        d.pause(0.5)
        if code == "DM-SAP":
            d.say("Here we write a note so that everybody knows the status of this link.")
        if code == "DM-MARG":
            d.say("MARG is configured, but the direct database link is not filled in yet, so CTDMS shows a warning. "
                  "Meanwhile its sales register is imported as a file. This is a common real situation.")
        if code == "DM-CBO":
            d.say("For CBO we generate an API key. Company C's IT team can use it to push invoices straight in. "
                  "The key is shown only once.")
        if code == "DM-INTRECT":
            d.say("Intrect is planned but not connected, so we leave it inactive.")
        d.field("notes").fill(notes)
        d.check("is_active", active)
        d.btn("Save configuration")
        if key:
            d.btn("Generate API key")
            d.pause(1.5)
            try:
                state["cbo_key"] = d.pg.locator("main code").first.inner_text().strip()
            except Exception:
                pass
            d.shot("03_erp_api_key")
        if code == "DM-MARG":
            d.btn("Test connection")
            d.pause(1.5)
            d.shot("03_erp_marg_warning")
    d.go("/admin/integrations")
    d.say("The four systems now show four different statuses: working, file only, API key issued, and inactive.")
    d.pause(2)
    d.shot("04_erp_statuses")


def stage_masters(d: Demo):
    d.title_card("Step 2 - Master records", "Masters menu: CFA, Areas, Companies, Customers, Transporters",
                 "Step two: master records. These are the permanent facts of the business. Create them once, in this order.")
    # CFA
    d.go("/masters/cfas/new")
    d.say("First the CFA, which is the warehouse that dispatches the goods. Our demonstration CFA is Raipur.")
    c = D.CFA
    d.fill("code", c["code"]); d.fill("name", c["name"]); d.fill("warehouse", c["warehouse"])
    d.fill("city", c["city"]); d.fill("state", c["state"]); d.fill("pincode", c["pincode"])
    d.fill("gstin", c["gstin"]); d.fill("contact_person", c["contact_person"]); d.fill("contact_phone", c["contact_phone"])
    d.select("default_erp_id", c["erp"])
    d.shot("05_cfa_form")
    d.btn("Save")
    d.go("/masters/cfas")
    d.shot("06_cfa_list")
    # locations
    d.say("Next the five delivery areas. Freight is priced per area, so every town you deliver to must exist here.")
    for code, name, city, route, zone, km in D.LOCATIONS:
        d.go("/masters/locations/new")
        d.fill("code", code); d.fill("name", name); d.fill("city", city); d.fill("state", "Chhattisgarh")
        d.fill("route", route); d.fill("zone", zone); d.fill("distance_km", km)
        if code == "DM-RAIPUR":
            d.shot("07_location_form")
        d.btn("Save")
    d.go("/masters/locations")
    d.shot("08_locations")
    # companies
    d.say("Then the principal companies whose medicines we dispatch. Each is linked to the billing system it uses "
          "and to the CFA that serves it.")
    for code, name, erp, gstin in D.COMPANIES:
        d.go("/masters/companies/new")
        d.fill("code", code); d.fill("name", name)
        d.select("relationship_type", "Principal")
        d.select("erp_system_id", erp)
        d.fill("gstin", gstin)
        d.tick_label("cfas", "DM-RPR")
        if code == "DM-COA":
            d.shot("09_company_form")
        d.btn("Save")
    d.go("/masters/companies")
    d.shot("10_companies")
    # transporters
    d.say("Now five transporters. The important choice is the payment mode. To be billed means the transporter "
          "sends a bill later, which we check. Cash means we pay at dispatch and no bill is expected.")
    for code, name, mode, contact, mobile, gst, extra in D.TRANSPORTERS:
        d.go("/masters/transporters/new")
        d.fill("code", code); d.fill("name", name); d.fill("contact_person", contact); d.fill("mobile", mobile)
        d.fill("gstin", gst); d.fill("city", "Raipur"); d.select("payment_mode", value=mode)
        if extra:
            d.fill("direct_pickup_charge", extra[0]); d.select("direct_pickup_basis", value=extra[1])
        if code == "DM-T1":
            d.shot("11_transporter_form")
        d.btn("Save")
    d.go("/masters/transporters")
    d.shot("12_transporters")
    # outside auto
    d.say("One local delivery auto is also recorded, charged per trip.")
    d.go("/masters/autos/new")
    a = D.AUTO
    d.fill("code", a[0]); d.fill("name", a[1]); d.fill("mobile", a[2]); d.select("charge_basis", value=a[3]); d.fill("rate", a[4])
    d.btn("Save")
    # customers
    d.say("Finally the customers: stockists, retailers and a hospital. Each has an area, a mobile number and a GST number. "
          "The GST number lets CTDMS recognise the customer from an e-invoice QR code.")
    for code, name, typ, loc, gstin, mob in D.CUSTOMERS:
        d.go("/masters/customers/new")
        d.fill("code", code); d.fill("name", name); d.select("party_type", value=typ)
        d.select("location_id", loc); d.fill("city", loc); d.fill("state", "Chhattisgarh"); d.fill("contact_phone", mob)
        d.fill("whatsapp", mob); d.fill("gstin", gstin)
        for cc, *_ in D.COMPANIES:
            d.tick_label("companies", cc)
        d.tick_label("cfas", "DM-RPR")
        if code == "DM-C01":
            d.shot("13_customer_form")
        d.btn("Save")
    d.go("/masters/customers")
    d.shot("14_customers")


def stage_users(d: Demo):
    d.title_card("Step 3 - People who will use the system", "Administration > Users and Roles",
                 "Step three: people. The master creates a login for each person and decides what they may do.")
    d.go("/admin/users/new")
    d.say("Three demonstration users: a checker who approves, a dispatch operator who works at the warehouse desk, "
          "and an accounts person. Each gets a role and a password chosen by the master.")
    for i, (u, nm, role) in enumerate(D.USERS):
        d.go("/admin/users/new")
        d.fill("username", u); d.fill("full_name", nm); d.fill("email", u + "@example.com")
        d.select("role_id", role); d.fill("password", USER_PW)
        d.check("must_change", False)
        d.check("all_companies", True)
        if role == "Dispatch Operator":
            d.check("all_cfas", False)
            d.tick_label("cfas", "DM-RPR")
        else:
            d.check("all_cfas", True)
        if i == 0:
            d.shot("15_user_form")
        d.btn("Save user")
    d.go("/admin/users")
    d.shot("16_users")
    d.go("/admin/roles")
    d.say("The Roles screen is a grid of tick boxes. A role is only a list of permissions, and the master can change any box.")
    d.pause(1.5)
    d.shot("17_roles")


def stage_mappings(d: Demo):
    d.say("One more set-up detail. The same customer has a different code in each billing system. "
          "On the customer screen we record what MARG calls this customer, so that imports match automatically.")
    for cc, pairs in D.ERP_CODES.items():
        d.go("/masters/customers")
        d.click(f'main a:has-text("{cc}")')
        for erp, code in pairs:
            d.select("erp_system_id", erp)
            d.fill("source_code", code)
            d.fill("source_name", next(c[1] for c in D.CUSTOMERS if c[0] == cc).upper())
            if cc == "DM-C01":
                d.shot("18_customer_erp_code")
            d.btn("Add ERP code")


def stage_rates(d: Demo):
    d.title_card("Step 4 - Freight rate cards", "Masters > Transporter Rate Cards",
                 "Step four: freight rates. Every transporter has a rate per case for each area. "
                 "A new rate never takes effect until a second person approves it.")
    d.go("/rate-cards")
    d.shot("19_rate_cards_empty")
    tid = {}
    for i, t in enumerate(D.TRANSPORTERS, 1):
        tid[t[0]] = i
    d.say("For most transporters we upload the whole rate list from a spreadsheet. Here is Alpha Roadlines.")
    for code in ("DM-T1", "DM-T2", "DM-T3", "DM-T4"):
        d.go(f"/rate-cards/{tid[code]}")
        d.upload('main input[name="file"]', str(FILES / f"DEMO_rate_card_{code}.csv"))
        d.fill("reason", "Demonstration rate list")
        if code == "DM-T1":
            d.shot("20_rate_card_upload")
        d.btn("Upload for approval")
    d.say("The fifth transporter, Epsilon, does not serve every area. We type its three rates one by one.")
    for area, rate in D.RATES["DM-T5"].items():
        d.go(f"/rate-cards/{tid['DM-T5']}")
        d.select("new_location", area); d.fill("new_rate", rate); d.field("new_from").fill("2026-10-01")
        d.fill("reason", "Demonstration rate")
        d.btn("Send for approval")
    d.go("/rate-cards")
    d.say("All rates are now waiting. Until they are approved they are not used.")
    d.shot("21_rate_cards_waiting")
    # approvals by the checker
    d.logout()
    d.login("demo.checker", USER_PW)
    d.say("Now the checker signs in. This is a different person, so the two-person rule is respected.")
    d.go("/approvals")
    d.shot("22_approvals")


def stage_approve_rates(d: Demo):
    d.say("The checker reads each proposed rate and presses Approve. A reason is needed only to reject. "
          "The first two are done slowly; the rest follow the same way.")
    for i in range(2):
        d.click('main button:has-text("Approve")')
    d.shot("23_approve_rates")
    n = 0
    while d.pg.locator('main button:has-text("Approve")').count() and n < 40:
        d.pg.locator('main button:has-text("Approve")').first.click()
        d.pg.wait_for_load_state("networkidle")
        n += 1
    d.go("/rate-cards")
    d.say("All twenty rates are approved and now active. Fresh rates start from the effective date.")
    d.shot("24_rate_cards_active")
    d.go("/rate-cards/5")
    d.shot("25_rate_card_epsilon")
    d.logout()
    d.login("admin", ADMIN_PW)


def stage_import_sap(d: Demo):
    d.title_card("Step 5 - Bringing invoices in", "Operations > Invoice Import",
                 "Step five: invoices. They come from the billing systems. There are several ways in. We will use all of them.")
    d.go("/imports")
    d.say("First, Company A's SAP export. It is an Excel file with one line per medicine, so CTDMS also learns the "
          "SKU detail. We choose the system and company, and press Upload and preview, so nothing is saved before we look.")
    d.upload('main form[action="/imports/upload"] input[name="file"]', str(FILES / "DEMO_SAP_billing_documents_itemwise.xlsx"))
    d.select("erp_system_id", "DM-SAP")
    d.select("company_id", "DM-COA")
    d.select("cfa_id", "Raipur CFA")
    d.shot("26_import_upload_form")
    d.btn("Upload & preview")
    d.shot("27_import_preview_sap")
    d.say("CTDMS recognised the columns by their headings. Every row is valid, so we import. "
          "The five invoices and their medicine lines are now in the central database.")
    d.btn("Import 13 valid")
    d.pause(1.5)
    d.shot("28_import_done_sap")


def stage_import_marg(d: Demo):
    d.go("/imports")
    d.say("Second, Company B's MARG sales register, a plain CSV file. This one contains deliberate mistakes, "
          "which is realistic. Watch how CTDMS handles each.")
    d.upload('main form[action="/imports/upload"] input[name="file"]', str(FILES / "DEMO_MARG_sales_register.csv"))
    d.select("erp_system_id", "DM-MARG")
    d.select("company_id", "DM-COB")
    d.select("cfa_id", "Raipur CFA")
    d.btn("Upload & preview")
    d.say("The Branch column was read as a CFA code, but MARG's branch is only a name. We tell CTDMS it is not a CFA column. "
          "We also save this layout under a name, so next time any MARG file is recognised at once.")
    d.select("map_cfa_code", value="")
    d.fill("template_name", "MARG sales register (Demo)")
    d.shot("29_import_marg_mapping")
    d.btn("Apply mapping & validate")
    d.shot("30_import_marg_problems", full=True)
    d.say("CTDMS found three problems and explains each one. First, party code T H dash zero eight is unknown. "
          "The system suggests Theta Pharma Mart, which is right, so we map the code once, and every future file will know it.")
    d.select("customer_id", value="8")
    d.btn("Map DM-MARG code")
    d.shot("31_import_marg_mapped", full=True)
    d.say("Second, invoice 2006 has no case count. It is accepted as an exception, to be filled in at dispatch. "
          "Third, invoice 2003 appears twice in the file. The duplicate is refused, so an invoice can never be counted twice. "
          "Now we import the valid rows.")
    d.pause(1)
    d.btn("Import 6 valid") if d.pg.locator('main button:has-text("Import 6 valid")').count() else d.btn("Import ")
    d.pause(1.5)
    d.shot("32_import_done_marg")


def stage_import_cbo(d: Demo):
    d.go("/imports")
    d.say("Third, Company C's CBO export. Its customer codes are not known to CTDMS, but the file carries each customer's "
          "GST number, and CTDMS recognises the customer from that.")
    d.upload('main form[action="/imports/upload"] input[name="file"]', str(FILES / "DEMO_CBO_invoice_export.csv"))
    d.select("erp_system_id", "DM-CBO")
    d.select("company_id", "DM-COC")
    d.select("cfa_id", "Raipur CFA")
    d.btn("Upload & preview")
    d.shot("33_import_cbo_preview", full=True)
    d.btn("Import 3 valid")
    d.pause(1.5)
    d.go("/imports")
    d.say("The import log lists every batch with its result. Three files, three different systems, one central register.")
    d.shot("34_import_log", full=True)


def stage_manual_invoice(d: Demo):
    d.go("/invoices/new")
    d.say("Fourth, a single invoice typed by hand, for example when no export is available. Choose the stockist, "
          "and the area fills itself in. Then company, invoice number, date, amount, taxable amount and cases.")
    n, dt, cu, co, val, cases, tax = D.MANUAL
    d.select("customer_id", cu); d.select("company_id", co); d.select("cfa_id", "Raipur CFA")
    d.fill("invoice_no", n); d.field("invoice_date").fill("2026-10-17"); d.fill("sales_value", val)
    d.fill("taxable_value", tax); d.fill("boxes", cases); d.fill("remarks", "Demonstration entry")
    d.shot("35_add_invoice_form")
    d.pg.locator('main button:has-text("Add invoice")').first.click()
    d.pg.wait_for_load_state("networkidle")
    d.say("Invoices can also be captured by scanning the e-invoice QR code at the dispatch desk. We will do that next, "
          "together with the gatepass.")
    # a mistaken entry, cancelled later
    d.go("/invoices/new")
    n, dt, cu, co, val, cases, tax = D.MANUAL_WRONG
    d.select("customer_id", cu); d.select("company_id", co); d.select("cfa_id", "Raipur CFA")
    d.fill("invoice_no", n); d.field("invoice_date").fill("2026-10-17"); d.fill("sales_value", val)
    d.fill("taxable_value", tax); d.fill("boxes", cases); d.fill("remarks", "Demonstration entry - typed by mistake")
    d.pg.locator('main button:has-text("Add invoice")').first.click()
    d.pg.wait_for_load_state("networkidle")
    d.go("/invoices")
    d.say("The Invoice Register now holds every invoice from every source. The Status column tells where each one stands.")
    d.shot("36_invoice_register", full=True)


def qr_text() -> str:
    for ln in (FILES / "DEMO_einvoice_QR_for_scanning.txt").read_text(encoding="utf-8").splitlines():
        if ln and not ln.startswith("#"):
            return ln.strip()


def add_invoices(d: Demo, nos):
    for no in nos:
        d.row_btn(no, "button[data-add]")


def print_gatepass(d: Demo, shot_name: str | None = None):
    """Press Print gatepass, showing the printable page in the same tab (the real button opens a new tab)."""
    d.pg.evaluate("() => document.querySelectorAll('form[action$=\"/print\"]').forEach(f => f.removeAttribute('target'))")
    btn = d.pg.locator('main form[action$="/print"] button[value="print"]').first
    d._move(btn)
    btn.click()
    d.pg.wait_for_load_state("networkidle")
    d.pause(1.5)
    if shot_name:
        d.shot(shot_name)
    d.pause(1.2)
    d.pg.go_back()
    d.pg.wait_for_load_state("networkidle")
    d.pg.reload()
    d.pg.wait_for_load_state("networkidle")


def stage_gatepass1(d: Demo):
    d.title_card("Step 6 - The gatepass", "Dispatch desk > Scan & Gatepass",
                 "Step six: dispatch. One gatepass covers a whole pickup, even when it carries goods of different "
                 "companies, for different customers, in different towns.")
    d.go("/gatepass/new")
    d.say("The warehouse operator scans the e-invoice QR printed on an invoice. This one is not in CTDMS yet, "
          "so it is created from the QR. The operator types the number of cases counted.")
    d.pg.locator("#gp-scan").fill(qr_text())
    d._move(d.pg.locator("#gp-scan"))
    d.pg.locator("#gp-scan-cases").fill("4")
    d.pg.locator("#gp-scan-btn").click()
    d.pause(1.5)
    d.shot("37_gatepass_qr_scanned")
    d.say("The other invoices are waiting in the list below. One press of Add puts each on the same gatepass. "
          "This pickup mixes two companies, three customers and two towns.")
    add_invoices(d, ["DEMO-SAP-1001", "DEMO-MRG-2001", "DEMO-MAN-4001", "DEMO-MRG-2002"])
    d.shot("38_gatepass_mixed")
    d.say("Who carries the goods? Alpha Roadlines, a to-be-billed transporter. The panel at the right shows the freight "
          "from Alpha's rate card for each area: cases times rate. Nothing is paid now, because Alpha will send a bill later.")
    d.radio("mode", "TRANSPORTER")
    d.select("transporter_id", "Alpha Roadlines")
    d.pause(1)
    d.fill("remarks", "Demonstration gatepass 1 - mixed companies and towns")
    d.shot("39_gatepass_charges")
    d.pg.locator("#gp-submit").click()
    d.pg.wait_for_load_state("networkidle")
    d.say("The gatepass is created with its own number. It has not been printed yet, and nothing can leave the gate until it is.")
    d.shot("40_gatepass_created")
    state["g1_url"] = d.pg.url
    d.say("Print it. The printed page carries a barcode, every customer with invoices, cases and charge, and the transporter's details.")
    print_gatepass(d, "41_gatepass_print")
    d.shot("42_gatepass_printed")


def gp_no(d: Demo) -> str:
    return d.pg.locator("main h1 .mono").first.inner_text().strip()


def make_gatepass(d: Demo, invoices, mode="TRANSPORTER", transporter=None, auto=None, remarks="", pre_say=None,
                  first_try_transporter=None, shots=None):
    d.go("/gatepass/new")
    add_invoices(d, invoices)
    d.radio("mode", mode)
    if mode in ("TRANSPORTER", "DIRECT"):
        if first_try_transporter:
            d.select("transporter_id", first_try_transporter)
            d.pause(1.2)
            if shots:
                d.shot(shots + "_norate")
        d.select("transporter_id", transporter)
    if mode == "OUTSIDE_AUTO":
        d.select("outside_auto_id", auto)
    d.fill("remarks", remarks)
    d.pause(1)
    if shots:
        d.shot(shots)
    d.pg.locator("#gp-submit").click()
    d.pg.wait_for_load_state("networkidle")
    d.pause(0.8)
    return gp_no(d), d.pg.url


def gate_check(d: Demo, no: str, shot=None):
    d.go("/gatepass/gate")
    d.fill("code", no)
    d.btn("Check")
    if shot:
        d.shot(shot)
    d.pg.locator('main button:has-text("Confirm gate-out"), main button:has-text("Record gate-out")').first.scroll_into_view_if_needed()
    d.click('main button:has-text("Confirm gate-out"), main button:has-text("Record gate-out")')


def enter_lrs(d: Demo, url: str, lrs):
    """lrs: list (in page order) or dict {customer-name-fragment: lr}"""
    d.go(url.replace(BASE, ""))
    ins = d.pg.locator('main input[name^="lr_"]')
    for i in range(ins.count()):
        row = ins.nth(i).locator("xpath=ancestor::tr[1]").inner_text()
        if isinstance(lrs, dict):
            v = next((lr for k, lr in lrs.items() if k.lower() in row.lower()), None)
        else:
            v = lrs[i] if i < len(lrs) else None
        if not v:
            continue
        ins.nth(i).scroll_into_view_if_needed()
        d._move(ins.nth(i))
        ins.nth(i).fill(v)
        d.pg.locator('main input[name^="lrdate_"]').nth(i).fill("2026-10-17")
    d.btn("Save LR numbers")


def gate_out_here(d: Demo, shot=None):
    btn = d.pg.locator('main button:has-text("Record gate-out")').first
    btn.scroll_into_view_if_needed()
    d.click('main button:has-text("Record gate-out")')
    if shot:
        d.shot(shot)


def stage_gate1(d: Demo):
    no = gp_no(d) if "/gatepass/" in d.pg.url else None
    d.go(state["g1_url"].replace(BASE, ""))
    no = gp_no(d)
    state["g1_no"] = no
    d.say("At the gate, the guard scans the barcode on the printed gatepass, or types its number. CTDMS shows what must "
          "leave. The guard counts the cases and confirms gate-out. Every invoice on the gatepass becomes dispatched at that moment.")
    d.go("/gatepass/gate")
    d.fill("code", no)
    d.btn("Check")
    d.shot("43_gate_check")
    d.click('main button:has-text("Record gate-out"), main button:has-text("Confirm gate-out")')
    d.shot("44_gate_out_done")
    d.say("Later the transporter gives its receipt numbers, called LR or bilty numbers, one for each customer. "
          "They are typed on the gatepass. They can be corrected, with a reason, until the transporter's bill is approved.")
    enter_lrs(d, state["g1_url"], ["ATR-1101", "ATR-1102", "ATR-1103"])
    d.shot("45_gatepass_lr")


def stage_gp2(d: Demo):
    d.say("Second gatepass: Beta Cargo Movers carries goods of all three companies to Bilaspur and Durg. "
          "First the operator chooses Epsilon Transport by mistake. CTDMS warns at once that Epsilon has no rate for Durg, "
          "so the operator switches to Beta Cargo.")
    no, url = make_gatepass(d, ["DEMO-SAP-1002", "DEMO-SAP-1003", "DEMO-CBO-3001", "DEMO-MRG-2005"],
                            transporter="Beta Cargo", first_try_transporter="Epsilon",
                            remarks="Demonstration gatepass 2 - three companies, Bilaspur and Durg", shots="46_gatepass2")
    state["g2_url"], state["g2_no"] = url, no
    d.say("Printed, gone out at the gate, and the LR numbers entered later.")
    print_gatepass(d)
    gate_out_here(d)
    enter_lrs(d, url, {"Beta": "BCM-7001", "Gamma": "BCM-7002", "Theta": "BCM-7003"})
    d.shot("47_gatepass2_done")


def stage_gp3(d: Demo):
    d.say("Third gatepass: Gamma Express Parcel is a cash transporter. It goes to Korba with two companies' goods for the hospital. "
          "Because Gamma is paid in cash, the amount shown as paid now equals the full freight, and no bill is expected.")
    no, url = make_gatepass(d, ["DEMO-SAP-1004", "DEMO-CBO-3002"], transporter="Gamma Express",
                            remarks="Demonstration gatepass 3 - cash transporter, Korba", shots="48_gatepass3_cash")
    state["g3_url"], state["g3_no"] = url, no
    print_gatepass(d)
    gate_out_here(d)
    enter_lrs(d, url, {"Epsilon": "GEP-3301"})
    d.shot("49_gatepass3_done")


def stage_gp4(d: Demo):
    d.say("Fourth gatepass: a local delivery in Raipur using our hired auto, charged per trip, with two invoices to one customer.")
    no, url = make_gatepass(d, ["DEMO-MRG-2003", "DEMO-SAP-1005"], mode="OUTSIDE_AUTO", auto="Local Auto",
                            remarks="Demonstration gatepass 4 - outside auto, local delivery", shots="50_gatepass4_auto")
    state["g4_url"], state["g4_no"] = url, no
    print_gatepass(d)
    gate_out_here(d)


def stage_gp5(d: Demo):
    d.say("Fifth gatepass: Delta Logistics collects directly from our warehouse, so its direct pickup charge of two hundred rupees is added. "
          "We print it, but the goods have not left yet, so it stays in the Printed state.")
    no, url = make_gatepass(d, ["DEMO-CBO-3003"], mode="DIRECT", transporter="Delta Logistics",
                            remarks="Demonstration gatepass 5 - direct pickup, waiting at gate", shots="51_gatepass5_direct")
    state["g5_url"], state["g5_no"] = url, no
    print_gatepass(d)


def stage_gp6(d: Demo):
    d.say("Sixth: a gatepass made on the wrong transporter. Before the goods leave, a gatepass can be voided with a reason. "
          "The invoice goes back to the waiting list, and nothing is lost or duplicated.")
    no, url = make_gatepass(d, ["DEMO-MRG-2004"], transporter="Delta Logistics", remarks="Demonstration gatepass - wrong carrier")
    d.shot("52_gatepass_before_void")
    vr = d.pg.locator('main form[action$="/void"] input[name="reason"]').first
    d._move(vr)
    vr.fill("Wrong transporter chosen - demonstration")
    d.pg.locator('main form[action$="/void"] button').first.click()
    d.pg.wait_for_load_state("networkidle")
    d.shot("53_gatepass_voided")
    d.say("Now the correct gatepass is made, with Epsilon Transport, which does have a Bilaspur rate. "
          "We leave it generated but not printed, so you can see that status too.")
    no, url = make_gatepass(d, ["DEMO-MRG-2004"], transporter="Epsilon", remarks="Demonstration gatepass 6 - not printed yet",
                            shots="54_gatepass6")
    state["g6_url"], state["g6_no"] = url, no
    d.go("/gatepass")
    d.say("The Gatepass register shows every gatepass with its state: not printed, printed, gone out, or void.")
    d.shot("55_gatepass_register", full=True)


def invoice_link(d: Demo, no: str):
    d.go("/invoices")
    d.click(f'main a:has-text("{no}")')


def stage_cancel_fix(d: Demo):
    d.say("Two invoices still need attention. Invoice 2006 arrived without a case count. The master can correct it directly; "
          "other users must ask a checker to approve. The change is recorded with a reason.")
    invoice_link(d, "DEMO-MRG-2006")
    d.shot("56_invoice_exception")
    d.click('main a:has-text("Open dispatch")')
    d.pg.evaluate("""() => document.querySelectorAll('main details').forEach(x => { if (x.querySelector("form[action$='/override']")) x.open = true; })""")
    op = 'main details[open] form[action$="/override"]'
    d._move(d.pg.locator(op + ' select[name="field"]').first)
    d.pg.locator(op + ' select[name="field"]').first.select_option(value="BOXES")
    d.pg.locator(op + ' input[name="new_value"]').first.fill("7")
    d.pg.locator(op + ' input[name="reason"]').first.fill("Counted at the warehouse - demonstration")
    d.shot("57_override_form")
    d._move(d.pg.locator(op + ' button').first)
    d.pg.locator(op + ' button').first.click()
    d.pg.wait_for_load_state("networkidle")
    d.shot("58_override_done")
    d.say("The mistaken invoice 4002 is cancelled. Cancelling needs a reason, and the invoice stays in the register, marked cancelled, so there is a full history.")
    invoice_link(d, "DEMO-MAN-4002")
    d.click('main a:has-text("Open dispatch")')
    d.pg.evaluate("""() => document.querySelectorAll('main details').forEach(x => { if (x.querySelector("form[action$='/cancel']")) x.open = true; })""")
    d.pg.locator('main form[action$="/cancel"] input[name="reason"]').first.fill("Typed by mistake - demonstration")
    d._move(d.pg.locator('main form[action$="/cancel"] button').first)
    d.pg.locator('main form[action$="/cancel"] button').first.click()
    d.pg.wait_for_load_state("networkidle")
    d.shot("59_invoice_cancelled")


def stage_pod(d: Demo):
    d.title_card("Step 7 - Delivery and proof of delivery", "Dispatch desk > Deliveries Pending and Track Shipment",
                 "Step seven: the goods reach the customer, and the signed receipt comes back. CTDMS records this as proof of delivery.")
    d.go("/deliveries")
    d.say("Deliveries Pending lists everything that has left the warehouse but has no proof yet. "
          "Overdue consignments would be highlighted.")
    d.shot("60_deliveries_pending")
    d.say("The signed receipts for Alpha Roadlines' consignments are back, so we tick them and mark them delivered, with the date.")
    d.select("transporter_id", "Alpha Roadlines")
    d.btn("Filter")
    for cb in d.pg.locator('main input[name="pick"]').all():
        cb.check()
    d.field("delivered_on").fill("2026-10-17")
    d.shot("61_deliveries_tick")
    d.btn("Mark ticked as delivered")
    d.say("For Beta Cargo, Gamma Pharma received only twenty-two of twenty-four cases. This is recorded on the consignment as "
          "delivered short, with who received it, a note, and a photo of the signed receipt.")
    invoice_link(d, "DEMO-SAP-1003")
    d.click('main a:has-text("Track")') if d.pg.locator('main a:has-text("Track")').count() and "/track/" not in d.pg.url else None
    d.pg.goto(d.pg.url) if False else None
    d.go(d.pg.url.replace(BASE, "").replace("/invoices/", "/track/"))
    d.select("status", value="SHORT")
    d.field("delivered_on").fill("2026-10-17")
    d.fill("received_by", "Store keeper (Demo)")
    d.fill("note", "22 of 24 cases received; 2 cases short")
    d.upload('main input[name="photo"]', str(FILES / "DEMO_signed_LR_photo.png"))
    d.shot("62_pod_short_form")
    d.pg.locator('main form[action$="/pod"] button').first.click()
    d.pg.wait_for_load_state("networkidle")
    d.shot("63_track_timeline", full=True)
    d.say("The tracking page shows the whole journey of the invoice: order received, gatepass, printed, left the warehouse, LR number, and delivered.")
    d.go("/track")
    d.fill("q", "BCM-7003")
    d.btn("Track")
    d.shot("64_track_search")
    d.go("/deliveries")
    d.say("The remaining consignments, Beta's other customers and the cash and auto trips, stay on the pending list until their proof comes back.")
    d.shot("65_deliveries_remaining")


def type_lines(d: Demo, lines):
    for i, (lr, dt, party, station, boxes, rate, amt) in enumerate(lines):
        for key, val in (("lr_no", lr), ("lr_date", dt), ("party_name", party), ("station", station),
                         ("billed_boxes", boxes), ("billed_rate", rate), ("billed_amount", amt)):
            loc = d.pg.locator(f'main input[name="ln_{i}_{key}"]').first
            loc.scroll_into_view_if_needed()
            loc.fill(str(val))
        d.pause(0.3)


def stage_bills(d: Demo):
    d.title_card("Step 8 - Transporter bills", "Operations > Transporter Bills",
                 "Step eight: money. To-be-billed transporters send a bill. CTDMS checks every line against our own gatepasses "
                 "before anybody pays.")
    d.logout()
    d.login("demo.accounts", USER_PW)
    d.go("/recon/new")
    d.say("The accounts person records Alpha Roadlines' bill: number, date, total amount, and the period it covers.")
    d.select("transporter_id", "Alpha Roadlines")
    d.fill("bill_no", "ART/2610/001"); d.field("bill_date").fill("2026-10-17"); d.fill("bill_amount", "297")
    d.field("period_from").fill("2026-10-14"); d.field("period_to").fill("2026-10-17")
    d.shot("66_bill_new")
    d.btn("Create bill")
    d.say("Each line of the paper bill is typed in: LR number, date, customer, station, cases, rate and amount.")
    type_lines(d, [("ATR-1101", "2026-10-17", "Zeta Medical Hall", "Raipur", 4, "6", "24"),
                   ("ATR-1102", "2026-10-17", "Alpha Stockist", "Raipur", 18, "6", "108"),
                   ("ATR-1103", "2026-10-17", "Delta Chemists", "Bhilai", 11, "15", "165")])
    d.pg.locator('main button:has-text("Save lines & re-check")').last.click()
    d.pg.wait_for_load_state("networkidle")
    d.say("Every line is matched to our gatepass: the LR exists, the cases agree, the rate agrees with Alpha's rate card, "
          "and the arithmetic is right. All three lines are matched, and the variance is zero.")
    d.shot("67_bill1_matched", full=True)
    state["bill1_url"] = d.pg.url
    d.say("One more question must be answered: has the transporter changed any rates? Nothing has, so we save the review with nothing ticked.")
    d.fill("note", "No rate change")
    d.btn("Save rate review")
    d.shot("68_bill1_rates")


def open_details(d: Demo, form_action_suffix: str):
    d.pg.evaluate("""(suf) => document.querySelectorAll('main details').forEach(x => { if (x.querySelector("form[action$='" + suf + "']")) x.open = true; })""", form_action_suffix)


def stage_bills2(d: Demo):
    d.say("Now Beta Cargo Movers' bill. This transporter sent its statement as a spreadsheet, so we import it. "
          "It has mistakes, which is common in real life, and we want to see whether CTDMS catches them.")
    d.go("/recon/new")
    d.select("transporter_id", "Beta Cargo")
    d.fill("bill_no", "BCM/2610/007"); d.field("bill_date").fill("2026-10-17"); d.fill("bill_amount", "1132")
    d.field("period_from").fill("2026-10-14"); d.field("period_to").fill("2026-10-17")
    d.check("link", False)
    d.btn("Create bill")
    open_details(d, "/import")
    d.upload('main form[action$="/import"] input[name="file"]', str(FILES / "DEMO_transporter_bill_Beta_Cargo.csv"))
    d.pg.locator('main form[action$="/import"] button').first.click()
    d.pg.wait_for_load_state("networkidle")
    d.pause(1)
    state["bill2_url"] = d.pg.url
    d.shot("69_bill2_flags", full=True)
    d.say("CTDMS flags the lines. The first has a rate of twenty-four, but Beta's rate card says twenty. "
          "The Gamma Pharma consignment is billed twice. And one LR number does not exist in our dispatch records at all.")
    d.go(state["bill2_url"].replace(BASE, "") + "/report")
    d.say("The irregularity report lists them all in plain words. It can be printed or sent to Excel, and taken to the transporter before anything is paid.")
    d.shot("70_bill2_irregularity_report", full=True)


def stage_bills_approve(d: Demo):
    d.say("Now the maker-checker rule. The accounts person who typed bill one cannot approve it. The checker signs in.")
    d.logout()
    d.login("demo.checker", USER_PW)
    d.go(state["bill1_url"].replace(BASE, ""))
    d.fill("remarks", "Checked against gatepass - demonstration")
    d.shot("71_bill1_approve", full=True)
    d.btn("Approve for payment")
    d.shot("72_bill1_approved", full=True)
    d.go(state["bill2_url"].replace(BASE, ""))
    d.say("Beta's bill cannot be approved: it carries blocking flags. The checker would reject it, with a reason, and send it back to the transporter.")
    d.shot("73_bill2_blocked", full=True)


def stage_settings(d: Demo):
    d.title_card("Step 3b - System settings", "Administration > System Settings",
                 "The master also sets the rules of the house in System Settings. Every change asks for a reason and is audited.")
    d.go("/admin/settings")
    d.say("We give the organisation its name, which is printed on every gatepass and report, and switch customer alerts to "
          "preview mode, called L O G. In preview mode messages are prepared and shown, but nothing is sent. "
          "WhatsApp and S M S are paid services and stay locked.")
    d.fill("s_org.name", "Tulsyan CFA (Demonstration)")
    d.fill("s_org.address", "Raipur, Chhattisgarh (demonstration address)")
    d.select("s_notify.provider", value="LOG")
    d.pg.locator('main input[placeholder*="Reason"]').first.fill("First-time set-up for the demonstration")
    d.shot("15b_settings")
    d.btn("Save settings")


def stage_pay(d: Demo):
    d.say("With the bill approved, the accounts person marks it paid and records the bank reference, called the U T R.")
    d.logout()
    d.login("demo.accounts", USER_PW)
    d.go(state["bill1_url"].replace(BASE, ""))
    d.fill("payment_ref", "UTR-DEMO-000123456789")
    d.shot("74_bill1_pay", full=True)
    d.btn("Mark paid")
    d.shot("75_bill1_paid", full=True)
    d.go("/recon")
    d.say("The bill list now shows one bill paid and one needing follow-up.")
    d.shot("76_bills_list")
    d.logout()
    d.login("admin", ADMIN_PW)


def stage_alerts_api(d: Demo):
    d.title_card("Step 9 - Alerts, API and other ways to use the data", "Customer alerts, REST API, exports",
                 "Step nine: using the data. Customers can be alerted, other software can read and write through the API, "
                 "and every report can be exported.")
    d.go("/notifications")
    d.say("Customer Alerts shows the messages CTDMS prepared when goods left the gate and when LR numbers were entered. "
          "In preview mode they are marked sent without being sent. When the business decides to pay for WhatsApp, only a setting changes.")
    d.shot("77_customer_alerts", full=True)
    d.go("/admin/integrations")
    d.say("Company C's IT team can push invoices with the API key we generated. Here one invoice arrives directly from CBO, with no file and no typing.")
    import urllib.request
    body = json.dumps({"invoices": [{"invoice_no": "DEMO-CBO-3101", "invoice_date": "2026-10-17", "company_code": "DM-COC",
                                     "cfa_code": "DM-RPR", "customer_code": "DM-C07", "sales_value": 22000, "boxes": 6,
                                     "remarks": "Demonstration invoice pushed through the API"}]}).encode()
    ex = OUT / "exports"
    ex.mkdir(exist_ok=True)
    try:
        rq = urllib.request.Request(BASE + "/api/v1/integrations/DM-CBO/invoices", data=body,
                                    headers={"Content-Type": "application/json", "X-API-Key": state.get("cbo_key", "")}, method="POST")
        resp = urllib.request.urlopen(rq, timeout=20).read().decode()
        (ex / "API_push_result_CBO.json").write_text(resp, encoding="utf-8")
        state["api_push"] = resp
    except Exception as e:  # noqa
        state["api_push"] = "FAILED: %r" % e
    d.go("/invoices")
    d.shot("78_invoice_api_push", full=True)
    d.go("/api/docs")
    d.say("The API documentation page lists every call: invoices, tracking, reports, dashboard figures and ERP write-back.")
    d.pause(2)
    d.shot("79_api_docs")

    def api(method, path, data=None, token=None):
        h = {"Content-Type": "application/json"}
        if token:
            h["Authorization"] = "Bearer " + token
        r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
        return urllib.request.urlopen(r, timeout=30).read()
    tok = json.loads(api("POST", "/api/v1/auth/token", json.dumps({"username": "admin", "password": ADMIN_PW}).encode()))["access_token"]
    (ex / "API_invoices.json").write_bytes(api("GET", "/api/v1/invoices?page_size=50", token=tok))
    (ex / "API_track_BCM-7002.json").write_bytes(api("GET", "/api/v1/track?q=BCM-7002", token=tok))
    (ex / "API_report_transporter.json").write_bytes(api("GET", "/api/v1/reports/transporter", token=tok))
    (ex / "API_dashboard_summary.json").write_bytes(api("GET", "/api/v1/dashboard/summary", token=tok))


def stage_reports(d: Demo):
    d.title_card("Step 10 - Dashboard, reports and exports", "Dashboard, Reports",
                 "Step ten: what management sees. Everything entered so far now appears in the dashboard and in the reports.")
    d.go("/")
    d.say("The dashboard now has figures. Total invoices, how many are dispatched, how many are waiting, sales value, calculated freight, "
          "and freight as a percentage of sales.")
    d.pause(1)
    d.shot("80_dashboard_live")
    d.go("/reports")
    d.say("Every report can be exported. The full list is grouped into M I S, operational, reconciliation and control reports.")
    d.shot("81_reports_list")
    d.go("/reports/transporter")
    d.say("Transporter-wise: trips, cases, cost per case and cost per trip for each transporter. "
          "The buttons at the top export it as Excel, C S V or P D F.")
    d.shot("82_report_transporter")
    d.download('main a:has-text("Excel")', "Report_Transporter-wise.xlsx")
    d.get_file("/reports/transporter/export?fmt=csv", "Report_Transporter-wise.csv")
    d.get_file("/reports/transporter/export?fmt=pdf", "Report_Transporter-wise.pdf")
    for code, nm in [("gatepass-register", "Gatepass_Register"), ("sku-dispatch", "SKU-wise_Dispatch"),
                     ("delivery-register", "Delivery_Register_POD"), ("delivery-performance", "Delivery_Performance"),
                     ("bill-irregularities", "Transporter_Bill_Irregularities"), ("company-freight", "Company-wise_Freight"),
                     ("transporter-area", "Transporter_x_Area_Cost_per_Case"), ("audit", "Audit_Log")]:
        d.get_file(f"/reports/{code}/export?fmt=xlsx", f"Report_{nm}.xlsx")
    d.get_file("/reports/audit/export?fmt=pdf", "Report_Audit_Log.pdf")
    d.get_file("/reports/customer-type/export?fmt=csv", "Report_Stockists_Retailers_Hospitals.csv")
    d.go("/reports/sku-dispatch")
    d.say("SKU-wise dispatch shows which medicines went out, in what quantity, to whom.")
    d.shot("83_report_sku")
    d.go("/reports/delivery-performance")
    d.say("Delivery performance shows on-time delivery and short or damaged deliveries for each transporter and area.")
    d.shot("84_report_delivery")
    d.go("/reports/gatepass-register")
    d.shot("85_report_gatepass")
    d.go("/mis")
    d.say("The transportation M I S drills down by company, area, customer and transporter.")
    d.shot("86_mis")
    d.go("/report-subscriptions")
    d.say("Any report can also be e-mailed automatically every day, week or month. We set up a weekly Excel gatepass register for management. "
          "Sending needs the office mail server to be configured.")
    d.select("report_code", "Gatepass")
    d.fill("name", "Weekly gatepass register (Demo)")
    d.select("frequency", value="WEEKLY")
    d.select("fmt", value="xlsx")
    d.fill("recipients", "management@example.com")
    d.shot("87_subscription_form")
    d.btn("Save")
    d.shot("88_subscription_saved")
    gid = state["g1_url"].rstrip("/").split("/")[-1]
    d.go("/gatepass/" + gid)
    d.say("From any gatepass, the printable P D F and one label for every case can be produced. "
          "Scanning any case label at the gate opens its gatepass.")
    d.post_file(f"/gatepass/{gid}/print", {"mode": "pdf", "size": "A5", "reason": "Demonstration export"}, "Gatepass_G1_print.pdf")
    d.get_file(f"/gatepass/{gid}/labels", "Gatepass_G1_case_labels.html")
    d.go(f"/gatepass/{gid}/labels")
    d.shot("89_case_labels")
    d.go("/admin/users")
    d.say("Finally the master can download a complete copy of all the data, one C S V file per table, as a zip file. "
          "Passwords and keys are never included.")
    d.get_file("/admin/export", "Full_data_export.zip")


def stage_roles_audit(d: Demo):
    d.title_card("Step 11 - Control: roles, audit and what others see", "Administration",
                 "Last step: control. See what other users see, and who did what.")
    d.logout()
    d.login("demo.operator", USER_PW)
    d.go("/gatepass/new")
    d.say("This is the dispatch operator's view. The menu is shorter, because the master allowed only dispatch work. "
          "No rates, no bills, no administration.")
    d.shot("90_operator_view")
    d.logout()
    d.login("admin", ADMIN_PW)
    d.go("/reports/audit")
    d.say("The audit log records every change: who, what, when, with old and new values. Nobody can edit or erase it from the screen. "
          "This is the master's proof of what happened.")
    d.shot("91_audit_log")
    d.go("/exceptions")
    d.shot("92_exceptions")
    d.go("/invoices")
    d.say("The final invoice register shows every status side by side: dispatched, printed, waiting, cancelled, and the invoice pushed by API.")
    d.shot("93_final_register", full=True)
    d.go("/")
    d.title_card("End of the demonstration", "Every record shown is a demonstration entry marked DM, Demo or DEMO",
                 "That completes the walkthrough, from an empty system to dispatch, delivery, bills, reports and control. "
                 "Every record shown is marked as a demonstration entry, so it can be removed before real use. Thank you.")


STAGES = [("intro", stage_intro), ("erps", stage_erps), ("masters", stage_masters), ("users", stage_users),
          ("settings", stage_settings),
          ("mappings", stage_mappings), ("rates", stage_rates), ("approve", stage_approve_rates), ("sap", stage_import_sap), ("marg", stage_import_marg), ("cbo", stage_import_cbo), ("manual", stage_manual_invoice),
          ("gp1", stage_gatepass1), ("gate1", stage_gate1), ("gp2", stage_gp2), ("gp3", stage_gp3), ("gp4", stage_gp4),
          ("gp5", stage_gp5), ("gp6", stage_gp6), ("fix", stage_cancel_fix), ("pod", stage_pod), ("bills", stage_bills), ("bills2", stage_bills2), ("billsapp", stage_bills_approve), ("pay", stage_pay),
          ("alerts", stage_alerts_api), ("reports", stage_reports), ("roles", stage_roles_audit)]


def main():
    mode = sys.argv[1]
    upto = sys.argv[2] if len(sys.argv) > 2 else STAGES[-1][0]
    record = mode == "record"
    dur = json.loads((WORK / "durations.json").read_text()) if record else {}
    reset_and_start()
    d = Demo(BASE, OUT, record=record, durations=dur, speed=0.12, video_dir=WORK / "video")
    try:
        for name, fn in STAGES:
            print("== stage", name, flush=True)
            fn(d)
            if name == upto:
                break
    except Exception as e:
        print("FAILED:", repr(e)[:600])
        print("URL:", d.pg.url)
        try:
            print(d.pg.inner_text("main")[:1500])
        except Exception:
            pass
        d.shot("_error")
        raise
    finally:
        vid = d.finish()
        (WORK / "narration.json").write_text(json.dumps(d.steps, indent=1, ensure_ascii=False), encoding="utf-8")
        print("steps:", len(d.steps), "video:", vid)


if __name__ == "__main__":
    main()
