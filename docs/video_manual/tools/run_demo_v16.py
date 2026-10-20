"""v1.6 guide video: runs the whole v1.6 workflow on a CLEAN database through the real screens.

  python run_demo_v16.py dry      -> fast run, collects the narration (narration.json)
  python run_demo_v16.py record   -> real-time run with video (needs durations.json from the voice step)
Same presentation style as the v1.5 video (captions, cursor, demonstration banner, title cards, voice)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import demo_data_v16 as D
from demolib import Demo

ROOT = Path(r"D:/ctdms")
PY = ROOT / ".venv/Scripts/python.exe"
WORK = Path(os.environ["DEMO_WORK"])
OUT = ROOT / "docs/video_manual/v1.6"
FILES = OUT / "demo_files"
PORT = 8004
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
                    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match "
                    "'uvicorn' -and $_.CommandLine -match '--port %d' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" % PORT],
                   capture_output=True)
    time.sleep(1.5)


def reset_and_start():
    kill_server()
    for p in WORK.glob("demo.db*"):
        p.unlink()
    import shutil
    shutil.rmtree(WORK / "appdata", ignore_errors=True)
    e = env()
    subprocess.run([str(PY), "-m", "app.cli", "init-db"], cwd=ROOT, env=e, check=True, capture_output=True)
    subprocess.run([str(PY), "-m", "app.cli", "create-admin", "--username", "admin", "--password", ADMIN_PW,
                    "--full-name", "Master Administrator"], cwd=ROOT, env=e, check=True, capture_output=True)
    log = open(WORK / "server.log", "w")
    subprocess.Popen([str(PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
                     cwd=ROOT, env=e, stdout=log, stderr=log)
    for _ in range(40):
        try:
            urllib.request.urlopen(BASE + "/login", timeout=2)
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("server did not start")


# ----------------------------------------------------------------------------- helpers
def add_invoices(d: Demo, nos):
    for no in nos:
        d.row_btn(no, "button[data-add]")


def gp_no(d: Demo) -> str:
    return d.pg.locator("main h1 .mono").first.inner_text().strip()


def print_gatepass(d: Demo, shot_name: str | None = None):
    d.pg.evaluate("""() => document.querySelectorAll("form[action$='/print']").forEach(f => f.removeAttribute('target'))""")
    btn = d.pg.locator('main form[action$="/print"] button[value="print"]').first
    d._move(btn)
    btn.click()
    d.pg.wait_for_load_state("networkidle")
    d.pause(1.5)
    if shot_name:
        d.shot(shot_name, full=True)
    d.pause(1.5)
    d.pg.go_back()
    d.pg.wait_for_load_state("networkidle")
    d.pg.reload()
    d.pg.wait_for_load_state("networkidle")


def gate_out_here(d: Demo):
    d.pg.locator('main button:has-text("Record gate-out")').first.scroll_into_view_if_needed()
    d.click('main button:has-text("Record gate-out")')


def choose_auto(d: Demo, name_part: str):
    opts = d.pg.locator("#gp-auto option").all_inner_texts()
    label = next(o for o in opts if name_part.lower() in o.lower())
    d.select("outside_auto_id", label)


def enter_lrs(d: Demo, lrs: dict):
    ins = d.pg.locator('main input[name^="lr_"]')
    for i in range(ins.count()):
        row = ins.nth(i).locator("xpath=ancestor::tr[1]").inner_text()
        v = next((lr for k, lr in lrs.items() if k.lower() in row.lower()), None)
        if not v:
            continue
        ins.nth(i).scroll_into_view_if_needed()
        d._move(ins.nth(i))
        ins.nth(i).fill(v)
        d.pg.locator('main input[name^="lrdate_"]').nth(i).fill("2026-10-18")
    d.btn("Save LR numbers")


def import_file(d: Demo, path: Path, erp: str, company: str | None = None):
    d.go("/imports")
    d.upload('main form[action="/imports/upload"] input[name="file"]', str(path))
    d.select("erp_system_id", erp)
    if company:
        d.select("company_id", company)
    d.btn("Upload & preview")


def press_import(d: Demo):
    d.click('main form[action$="/commit"] button')
    d.pause(1.2)


# ============================================================================= stages
def stage_intro(d: Demo):
    d.login("admin", ADMIN_PW)
    d.title_card("CTDMS Version 1.6 - Master's Walkthrough",
                 "What changed after the management review  |  live demonstration on a clean database",
                 "Welcome to the version 1.6 walkthrough. It shows the changes asked for after the management review: "
                 "one master workbook, depots instead of C F A, customer types and codes, the invoice recognition code, "
                 "local autos charged per trip or per box, and keyboard-first operation for staff used to MARG. "
                 "The database is empty. Every record you will see is a demonstration entry.")
    d.go("/")
    d.say("This is the dashboard of a brand-new installation. The menu now says Depot wherever it used to say C F A, "
          "and all the master records live under Master Settings.")
    d.pause(1.5)
    d.shot("01_dashboard_empty")
    d.go("/admin/users/new")
    d.say("First, one more person: a checker, because rates still need a second person's approval.")
    d.fill("username", "demo.checker"); d.fill("full_name", "Demo Checker"); d.fill("email", "checker@example.com")
    d.select("role_id", "Checker / Approver"); d.fill("password", USER_PW)
    d.check("must_change", False); d.check("all_companies", True); d.check("all_cfas", True)
    d.btn("Save user")
    d.go("/admin/settings")
    d.fill("s_org.name", "Tulsyan (Demonstration)")
    d.pg.locator('main input[placeholder*="Reason"]').first.fill("Demonstration set-up")
    d.btn("Save settings")


def stage_upload(d: Demo):
    d.title_card("Step 1 - One master workbook", "Master Settings > Initial Master Upload",
                 "Step one. On the first day, all the master data compiled from MARG, SAP, CBO and Intrect is uploaded once, "
                 "in one cleaned workbook.")
    d.go("/masters/upload")
    d.say("Master Settings, Initial Master Upload. The blank workbook can be downloaded here. Its first sheet explains "
          "every column; the other sheets are companies, depots, customers, customer types, transporters, locations, "
          "local auto rates, freight rates, invoice code mappings and E R P mappings. Product and S K U sheets are not part of version one point six.")
    d.pause(1)
    d.shot("02_upload_page", full=True)
    d.get_file("/masters/upload/template.xlsx", "Blank_master_workbook_template.xlsx")
    d.say("We upload the demonstration workbook. Nothing is saved yet: CTDMS first checks every row.")
    d.upload('main form[action="/masters/upload"] input[name="file"]', str(FILES / "DEMO_v1.6_Master_Workbook.xlsx"))
    d.btn("Check workbook")
    d.pause(1)
    d.shot("03_upload_preview", full=True)
    d.say("The preview gives accepted and rejected counts for each sheet. Four rows have problems, and each one says why: "
          "a depot without a city, a customer typed twice, a customer whose delivery location does not exist, and the "
          "Retailer customer type, which already exists in CTDMS and is therefore skipped, not duplicated.")
    d.pause(2)
    d.say("Only the valid rows will be imported. We press Import.")
    d.click('main form[action$="/commit"] button')
    d.pause(1.5)
    d.shot("04_upload_imported", full=True)
    d.say("Fifty two master records were created in one go. The rejected rows stay in the history with an Excel error "
          "report, so they can be corrected. From now on, single new records are added under Master Settings.")
    up_id = d.pg.url.split("/masters/upload/")[1].split("?")[0]
    d.get_file(f"/masters/upload/{up_id}/errors.xlsx", "Master_upload_error_report.xlsx")
    d.go("/masters/upload")
    d.shot("05_upload_history")


def stage_masters(d: Demo):
    d.title_card("Step 2 - What the workbook created", "Companies, Depots, Customers, Customer Types, Transporters",
                 "Step two. A quick look at the masters the workbook created.")
    d.go("/masters/companies")
    d.say("The Company Master. Each company keeps its code and has a short recognition code: A B C, X Y Z, P Q R and T D L "
          "for our own distribution. Each is linked to its billing system.")
    d.pause(1)
    d.shot("06_companies")
    d.go("/masters/depots")
    d.say("The Depot Master. Raipur has two depots, Guma and Kamal Vihar. They are in the same city but are separate depots, "
          "each with its own code, locality and recognition code. Bilaspur has its own depot.")
    d.pause(2)
    d.shot("07_depots")
    d.click('main a:has-text("DM-GUMA")')
    d.say("Inside a depot: locality, city, address, the companies it handles and its usual billing system.")
    d.pause(1)
    d.shot("08_depot_form", full=True)
    d.go("/masters/customer-types")
    d.say("Customer Types are now a master of their own, not fixed in the program: retailer, stockist, distributor, "
          "hospital, institution, chain pharmacy, other, plus the two the workbook added. Each has a short code, like R E T. "
          "Notice that Government Supply has no code yet. We will come back to that.")
    d.pause(1.5)
    d.shot("09_customer_types")
    d.go("/masters/customers")
    d.say("The Customer Master: customer code, name, customer type, recognition code like C zero two five, delivery location "
          "and the companies served.")
    d.pause(1)
    d.shot("10_customers")
    d.click('main a:has-text("DM-C025")')
    d.say("Each customer also keeps its source E R P and the code that E R P uses, so imports find the customer automatically.")
    d.pause(1)
    d.shot("11_customer_form", full=True)
    d.go("/masters/transporters")
    d.say("Long-distance transporters work exactly as in version one point five: a rate card per area, L R numbers and a monthly bill.")
    d.shot("12_transporters")
    d.go("/masters/autos")
    d.say("Local autos are listed separately. Sharma Local Auto charges per trip, usually five hundred rupees. Ravi Tempo "
          "charges per box. Kishore Mini Truck can be charged either way.")
    d.pause(1.5)
    d.shot("13_local_autos")
    d.go("/masters/local-auto-rates")
    d.say("Per-box rates can depend on the location, the company and the depot. Ravi Tempo charges twenty rupees a box in "
          "Raipur, twenty five to Bhilai, and twenty two for P Q R's goods in Raipur.")
    d.pause(1.5)
    d.shot("14_local_auto_rates")
    d.go("/masters/invoice-markings")
    d.say("Invoice Code Mappings hold the markings our billing systems already put in their invoice numbers. In MARG, an "
          "invoice starting with X Y slash G is X Y Z Healthcare from the Guma depot; X Y slash K is from Kamal Vihar.")
    d.pause(1.5)
    d.shot("15_invoice_markings")


def stage_rates(d: Demo):
    d.logout()
    d.login("demo.checker", USER_PW)
    d.go("/approvals")
    d.say("The freight rates from the workbook wait for the checker. The checker reviews them and presses Approve all. "
          "The person who uploaded them could not approve them.")
    d.pause(1)
    d.shot("16_approvals")
    d.click('main form[action="/rate-cards/approve-all"] button')
    d.pause(1)
    d.logout()
    d.login("admin", ADMIN_PW)


def stage_future(d: Demo):
    d.title_card("Step 3 - Adding a record later", "Master Settings",
                 "Step three. After the first day, nobody re-uploads the whole workbook. A new record is simply added in Master Settings.")
    d.go("/masters/autos/new")
    d.say("A new local auto has joined: Mohan's e-rickshaw, charged per trip, usually three hundred rupees.")
    d.fill("code", "DM-LA4"); d.fill("name", "Mohan E-Rickshaw (Demo)"); d.fill("mobile", "9000003004")
    d.fill("vehicle_no", "CG04-ER-1004"); d.select("allowed_methods", value="PER_TRIP"); d.fill("default_trip_charge", "300")
    d.shot("17_new_auto_form")
    d.btn("Save")
    d.go("/masters/autos")
    d.say("It is saved, and it will be in the local auto list on the gatepass straight away.")
    d.pause(1)
    d.shot("18_new_auto_listed")


def stage_recognition(d: Demo):
    d.title_card("Step 4 - Invoice Recognition Code", "Master Settings > Invoice Recognition Code",
                 "Step four, the new provision: one internal code that tells, at a glance, which company, customer type, "
                 "customer and depot an invoice belongs to.")
    d.go("/masters/recognition")
    d.say("The code is built from the short codes in the masters: company, customer type, customer, depot, and the number "
          "part of the E R P invoice number. For example A B C, R E T, C zero two five, GUMA, then zero zero one two four five. "
          "The pattern can be changed here, and the whole feature can be switched off. The original invoice number is never touched.")
    d.pause(2)
    d.shot("19_recognition_settings", full=True)


def stage_import(d: Demo):
    d.title_card("Step 5 - Invoices arrive", "Operations > Invoice Import",
                 "Step five. Invoices arrive from the billing systems, and CTDMS recognises everything it can from the masters.")
    import_file(d, FILES / "DEMO_v1.6_SAP_billing_ABC.xlsx", "SAP", "DM-ABC")
    d.say("A B C Pharma's SAP export. The plant column tells the depot, through the E R P mapping from the workbook, and "
          "SAP's own customer numbers find our customers. All six rows are valid.")
    d.pause(1)
    d.shot("20_import_sap", full=True)
    press_import(d)
    import_file(d, FILES / "DEMO_v1.6_MARG_sales_XYZ.csv", "MARG")
    d.say("X Y Z Healthcare's MARG file has no company or depot column at all. The marking X Y slash G or X Y slash K in "
          "each bill number tells CTDMS both the company and the depot.")
    d.pause(1)
    d.shot("21_import_marg", full=True)
    press_import(d)
    import_file(d, FILES / "DEMO_v1.6_CBO_invoices_PQR.csv", "CBO")
    d.say("And P Q R Lifesciences from CBO, recognised the same way.")
    press_import(d)


def stage_register(d: Demo):
    d.go("/invoices")
    d.say("The Invoice Register now shows two numbers side by side. On the left, the original E R P invoice number, exactly as "
          "it came. Next to it, the Invoice Recognition Code. Company, depot, customer and customer type are all identified.")
    d.pause(2)
    d.shot("22_register", full=True)
    d.go("/invoices?q=001245")
    d.say("Here is invoice zero zero one two four five from SAP. Its number is unchanged. Its recognition code is A B C dash "
          "R E T dash C zero two five dash GUMA dash zero zero one two four five: A B C Pharma, a retailer, customer C zero "
          "two five, from the Guma depot.")
    d.pause(2.5)
    d.shot("23_invoice_001245")


def stage_missing(d: Demo):
    d.go("/masters/recognition")
    d.say("One invoice could not get a code. The reason is clear: the customer type Government Supply has no recognition code. "
          "CTDMS does not guess. The invoice can still be dispatched normally.")
    d.pause(2)
    d.shot("24_missing_code", full=True)
    d.go("/masters/customer-types")
    d.click('main a:has-text("GOVT_SUPPLY")')
    d.say("We give Government Supply the code G O V in Master Settings and save.")
    d.fill("marking", "GOV")
    d.btn("Save")
    d.go("/masters/recognition")
    d.say("The code is made at once: A B C dash G O V dash C zero nine zero dash B S P, then the invoice number.")
    d.pause(1.5)
    d.shot("25_missing_fixed", full=True)


def stage_search(d: Demo):
    d.go("/invoices?rcode=ABC-RET&date_from=2026-10-01")
    d.say("Because every code starts with the same parts, searching is simple. Typing A B C dash R E T lists every A B C "
          "Pharma invoice to retailers.")
    d.pause(1.5)
    d.shot("26_search_prefix", full=True)
    d.go("/invoices?city=Raipur&date_from=2026-10-01")
    d.say("Filtering by the city Raipur brings both Raipur depots together, Guma and Kamal Vihar. The depot filter would show just one of them.")
    d.pause(1.5)
    d.shot("27_filter_city", full=True)


def stage_long_distance(d: Demo):
    d.title_card("Step 6 - Dispatch", "Dispatch desk > Scan & Gatepass",
                 "Step six, dispatch. First the normal long-distance transporter, which works exactly as before.")
    d.go("/gatepass/new")
    d.say("Three invoices from the Bilaspur depot go with Shree Roadlines, a long-distance transporter. Freight comes from "
          "its rate card for each area, and Shree Roadlines will send its bill later.")
    add_invoices(d, ["001248", "001249", "PQR/B/0313"])
    d.radio("mode", "TRANSPORTER")
    d.select("transporter_id", "Shree Roadlines")
    d.fill("remarks", "Demonstration - long distance")
    d.pause(1)
    d.shot("28_gp_long_distance")
    d.pg.locator("#gp-submit").click()
    d.pg.wait_for_load_state("networkidle")
    state["g_long"] = d.pg.url
    print_gatepass(d)
    gate_out_here(d)
    enter_lrs(d, {"WellCare": "SRL-5501", "Super Stockist": "SRL-5502"})
    d.say("Printed, out of the gate, L R numbers entered later. Nothing changed in this part.")
    d.shot("29_gp_long_done")


def stage_per_trip(d: Demo):
    d.title_card("Step 7 - Local auto, per trip", "One trip, one amount",
                 "Step seven. A local auto charged per trip.")
    d.go("/gatepass/new")
    d.say("Sharma Local Auto takes four invoices from the Guma depot to customers in Raipur: five, fifteen, six and four boxes.")
    add_invoices(d, ["001245", "001246", "001247", "XY/G/2601"])
    d.radio("mode", "OUTSIDE_AUTO")
    choose_auto(d, "Sharma Local Auto")
    d.pause(1)
    d.say("The operator types the actual trip charge, five hundred rupees today. It could be four fifty or five fifty on another day. "
          "The total stays five hundred: one trip, one amount. It is not five hundred times four invoices.")
    d.fill("trip_charge", "500")
    d.pg.dispatch_event("#gp-trip", "input")
    d.pause(1.5)
    d.shot("30_gp_per_trip")
    d.say("For the M I S, the five hundred is shared by boxes: about eighty three, two hundred and fifty, one hundred, and "
          "sixty seven rupees. The shares always add up to five hundred.")
    d.pause(1)
    d.fill("remarks", "Demonstration - per trip, four invoices")
    d.pg.locator("#gp-submit").click()
    d.pg.wait_for_load_state("networkidle")
    state["g_trip"] = d.pg.url
    d.shot("31_gp_per_trip_created")
    d.say("The dispatch slip prints the trip charge once, for the whole trip, with each invoice's original number and its recognition code.")
    print_gatepass(d, "32_slip_per_trip")
    gate_out_here(d)


def stage_per_box(d: Demo):
    d.title_card("Step 8 - Local auto, per box", "Boxes x rate",
                 "Step eight. A local auto charged per box.")
    d.go("/gatepass/new")
    d.say("Ravi Tempo charges per box. From the Kamal Vihar depot, the operator first adds an invoice for Durg as well.")
    add_invoices(d, ["XY/K/2603", "PQR/K/0311", "XY/K/2604"])
    d.radio("mode", "OUTSIDE_AUTO")
    choose_auto(d, "Ravi Tempo")
    d.pause(1.5)
    d.shot("33_gp_per_box_norate")
    d.say("CTDMS warns that Ravi Tempo has no per-box rate for Durg. That invoice is taken off this trip.")
    d.click('#gp-lines tr:has-text("XY/K/2604") [data-remove]', wait=False)
    d.pause(1)
    d.say("Now: fifteen boxes at twenty rupees is three hundred, and six boxes of P Q R's goods at its own rate of twenty two "
          "is one hundred and thirty two. Total four hundred and thirty two rupees.")
    d.fill("remarks", "Demonstration - per box")
    d.pause(1.5)
    d.shot("34_gp_per_box")
    d.pg.locator("#gp-submit").click()
    d.pg.wait_for_load_state("networkidle")
    print_gatepass(d)
    gate_out_here(d)


def stage_both(d: Demo):
    d.go("/gatepass/new")
    d.say("Kishore Mini Truck can be charged either way, so both choices appear. Today it is per trip, five hundred and fifty rupees, "
          "for one invoice to Bhilai.")
    add_invoices(d, ["XY/G/2602"])
    d.radio("mode", "OUTSIDE_AUTO")
    choose_auto(d, "Kishore")
    d.radio("auto_method", "PER_CASE")
    d.pause(1.5)
    d.shot("35_gp_both_box")
    d.radio("auto_method", "PER_TRIP")
    d.fill("trip_charge", "550")
    d.pg.dispatch_event("#gp-trip", "input")
    d.pause(1)
    d.shot("36_gp_both_trip")
    d.pg.locator("#gp-submit").click()
    d.pg.wait_for_load_state("networkidle")
    print_gatepass(d)


def stage_keyboard(d: Demo):
    d.title_card("Step 9 - Keyboard-only dispatch", "Codes, Enter, arrows and function keys - like MARG",
                 "Step nine. A complete dispatch done only with the keyboard, the way the staff already work in MARG. "
                 "The mouse is not touched in this part.")
    d.go("/")
    d.kb_only(True)
    d.say("Every screen shows its keys in the dark bar at the bottom, and F1 lists all of them. A key means the same thing "
          "on every screen: F2 new, F4 search, F6 save, F8 gatepass or print, F9 complete the dispatch, F10 reports, "
          "and Escape goes back.")
    d.key("F1", after=2.5)
    d.shot("42_kb_help")
    d.key("Escape")
    d.say("F2 starts a new gatepass. The cursor is already in the Depot box. Typing the depot code, K V R, finds the "
          "Kamal Vihar depot, and Enter selects it.")
    d.key("F2", after=1.2)
    d.pg.wait_for_url("**/gatepass/new**")
    d.type_keys("KVR", after=1.0)
    d.shot("43_kb_depot_code")
    d.key("Enter", after=1.2)
    d.pg.wait_for_url("**cfa_id=**")
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(True)
    d.say("The invoice number is typed, or scanned, and Enter adds it. The down arrow goes into the waiting list, and "
          "Enter adds the highlighted invoice.")
    d.type_keys("XY/K/2604")
    d.key("Enter", after=1.0)
    d.key("ArrowDown", show="↓", after=1.0)
    d.key("Enter", after=1.0)
    d.say("This is the Durg invoice that Ravi Tempo could not take per box. Today both go by Shree Roadlines. Enter on the "
          "empty box moves on to who carries the goods, and Enter moves from field to field. In the transporter box, "
          "typing S R L finds Shree Roadlines.")
    d.key("Enter")
    d.key("Enter")
    d.type_keys("SRL", after=0.9)
    d.shot("44_kb_transporter_code")
    d.key("Enter", after=0.8)
    d.say("Enter, Enter, through the date and time to the remarks.")
    d.key("Enter", after=0.5)
    d.key("Enter", after=0.5)
    d.key("Enter", after=0.5)
    d.type_keys("Keyboard only (Demo)")
    d.key("Enter", after=0.6)
    d.say("After the last field the cursor lands on Generate gatepass. One more Enter generates it.")
    d.key("Enter", after=1.2)
    d.shot("45_kb_ready")
    d.key("Enter", after=1.0)
    d.pg.wait_for_url(lambda u: u.rstrip("/").split("/")[-1].isdigit())
    d.pg.wait_for_load_state("networkidle")
    state["g_kb"] = d.pg.url
    d.kb_only(True)
    d.say("F8 prints the gatepass. F9 completes the dispatch: CTDMS asks first, and Enter answers yes.")
    with d.ctx.expect_page() as pop:
        d.key("F8", after=0.5)
    pop.value.wait_for_load_state()
    d.pause(1.5)
    pop.value.close()
    d.pg.wait_for_selector('[data-key="F9"]:not([disabled])', timeout=15000)  # the page refreshes itself after printing
    d.pg.wait_for_load_state("networkidle")
    d.pause(1.0)
    d.kb_only(True)
    d.key("F9", after=1.2)
    d.shot("46_kb_confirm")
    d.key("Enter", after=1.0)
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(True)
    d.pause(1.0)
    d.shot("47_kb_gone_out")
    d.say("Escape goes back to the list. The arrows move through the gatepasses, Enter opens one, and Escape returns to "
          "the same row, ready for the next record.")
    d.key("Escape", after=1.0)
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(True)
    d.key("ArrowDown", show="↓", after=0.6)
    d.key("ArrowDown", show="↓", after=0.8)
    d.key("Enter", after=1.2)
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(True)
    d.key("Escape", after=1.0)
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(True)
    d.pause(0.8)
    d.shot("48_kb_back_on_row")
    d.say("Delete, void, cancel and reject always ask first with No already selected, so a stray Enter never cancels "
          "anything. And F3 jumps to any screen by typing part of its name.")
    d.key("F3", after=0.6)
    d.type_keys("city", after=1.0)
    d.shot("49_kb_goto")
    d.key("Enter", after=1.0)
    d.pg.wait_for_load_state("networkidle")
    d.kb_only(False)


def stage_reports(d: Demo):
    d.title_card("Step 10 - Reports and MIS", "Reports",
                 "Last step: the reports management sees.")
    d.go("/reports/outside-auto?date_from=2026-10-01")
    d.say("The Local Auto Trips report. One row per trip. The amount owed to the auto, and the amount booked on its invoices, "
          "are always equal. The four-invoice trip shows five hundred rupees, not two thousand. Nothing is counted twice.")
    d.pause(2)
    d.shot("37_local_auto_trips", full=True)
    d.go("/reports/city-freight?date_from=2026-10-01")
    d.say("City-wise freight puts Guma and Kamal Vihar together under Raipur.")
    d.pause(1)
    d.shot("38_city_wise")
    d.go("/reports/cfa-freight?date_from=2026-10-01")
    d.say("Depot-wise freight shows each depot separately, with its city.")
    d.pause(1)
    d.shot("39_depot_wise")
    d.go("/reports/invoice-freight?date_from=2026-10-01")
    d.say("Every invoice report shows the original E R P invoice number and the Invoice Recognition Code in separate columns, "
          "together with company, depot, city, customer type, boxes and transportation expense. All of it exports to Excel, C S V or P D F.")
    d.pause(2)
    d.shot("40_invoice_wise", full=True)
    for code, nm in [("outside-auto", "Local_Auto_Trips"), ("city-freight", "City-wise_Freight"),
                     ("cfa-freight", "Depot-wise_Freight"), ("invoice-freight", "Invoice-wise_with_Recognition_Code"),
                     ("customer-type", "Customer-type-wise"), ("gatepass-register", "Gatepass_Register")]:
        d.get_file(f"/reports/{code}/export?fmt=xlsx&date_from=2026-10-01", f"Report_{nm}.xlsx")
    d.get_file("/reports/outside-auto/export?fmt=pdf&date_from=2026-10-01", "Report_Local_Auto_Trips.pdf")
    d.get_file("/reports/invoice-freight/export?fmt=csv&date_from=2026-10-01", "Report_Invoice-wise.csv")
    d.go("/")
    d.say("And the dashboard now shows the figures, including freight by depot with each depot's city.")
    d.pause(2)
    d.shot("41_dashboard", full=True)
    d.title_card("End of the version 1.6 walkthrough", "Every record shown is a demonstration entry (DM- codes, names ending (Demo))",
                 "That completes version one point six: one master workbook, depots with several in one city, customer types "
                 "and codes, the invoice recognition code beside the unchanged E R P number, local autos charged per trip "
                 "or per box without double counting, and a dispatch desk that runs from the keyboard. Thank you.")


STAGES = [("intro", stage_intro), ("upload", stage_upload), ("masters", stage_masters), ("rates", stage_rates),
          ("future", stage_future), ("recognition", stage_recognition), ("import", stage_import),
          ("register", stage_register), ("missing", stage_missing), ("search", stage_search),
          ("long", stage_long_distance), ("trip", stage_per_trip), ("box", stage_per_box), ("both", stage_both),
          ("keyboard", stage_keyboard), ("reports", stage_reports)]


def main():
    mode = sys.argv[1]
    upto = sys.argv[2] if len(sys.argv) > 2 else STAGES[-1][0]
    record = mode == "record"
    dur = json.loads((WORK / "durations.json").read_text()) if record else {}
    reset_and_start()
    OUT.mkdir(parents=True, exist_ok=True)
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
