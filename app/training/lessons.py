"""Guided Training lessons and learning paths (v1.6, keyboard-first).

Built from the v1.6 guide video (its chapters, records and amounts), the v1.6 Master manual and the older Master
User Manual's administrative chapters - rewritten for the current screens (Depot, Master Settings, keyboard keys).

Every step follows: explain (what / why) -> highlight -> the trainee does it in the real screen -> CTDMS checks
-> result explained -> continue. A step is one of
    read   - explanation only (Next)
    ui     - the screen itself shows it is done (page opened, field chosen, key pressed) - checked in the browser
    check  - the real result is checked in the trainee's practice database (only these make a lesson "practical")
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Step:
    id: str
    title: str
    what: str = ""          # what this feature does
    why: str = ""           # why it matters
    do: str = ""            # what to do now
    keys: str = ""          # keyboard instructions (existing CTDMS keys only)
    expect: str = ""        # expected result
    page: str | None = None   # screen to open for this step ("Go there" link)
    hl: str | None = None     # CSS selector highlighted on the screen
    check: str | None = None  # server check (app.training.checks)
    ui: dict | None = None    # browser check
    perm: str | None = None   # step only for users holding this permission
    files: tuple = ()         # demonstration files offered for download in this step
    acting: str | None = None  # this step is done as a demonstration user (two-person rule)

    @property
    def kind(self) -> str:
        return "check" if self.check else ("ui" if self.ui else "read")


@dataclass
class Lesson:
    code: str
    title: str
    summary: str
    start: str                       # starting point (app.training.scenario) when practice data must be prepared
    needs: str = "anything"          # prerequisite check (app.training.checks.NEEDS)
    video: tuple | None = None       # chapter of the v1.6 guide video (app.training.video.resolve)
    page: str = "/"                  # first screen
    steps: list = field(default_factory=list)
    source: str = "v1.6 guide video"

    @property
    def practical(self) -> bool:
        return any(s.check for s in self.steps)


WB = "DEMO_v1.6_Master_Workbook.xlsx"
SAP = "DEMO_v1.6_SAP_billing_ABC.xlsx"
MARG = "DEMO_v1.6_MARG_sales_XYZ.csv"
CBO = "DEMO_v1.6_CBO_invoices_PQR.csv"
FILE_KEYS = ("In the file box press Space (or Enter) to open the file window, type the file name (it is in your "
             "Downloads folder), Enter.")

# =============================================================================================== Master lessons
L01 = Lesson("m01-intro", "Introduction and the Dashboard", "What CTDMS does, how the screens and keys work.",
             start="clean", needs="anything", video=("CTDMS Version 1.6",), page="/", steps=[
    Step("welcome", "Welcome to Guided Training",
         what="CTDMS keeps ONE transportation database for every company and depot: invoices arrive from SAP, MARG, "
              "CBO and Intrect, are dispatched with a transporter or a local auto, the freight is calculated, the "
              "transporter's bill is checked and management sees the MIS.",
         why="One consolidated database means one freight figure per invoice, one approval trail and one set of "
             "reports - no spreadsheets per depot.",
         do="Read this panel; it guides every exercise. You practise in YOUR OWN demonstration copy of CTDMS.",
         keys="Ctrl+Enter = Next / Check · Ctrl+/ = jump into this panel · Esc (in the panel) = back to the screen",
         expect="The orange bar at the top says TRAINING MODE - nothing you do here touches live data."),
    Step("dashboard", "The Dashboard",
         what="The dashboard shows invoices, dispatches, boxes, freight and the variance against transporter bills, "
              "with filters for date, company, depot, customer, location and transporter.",
         why="It is the first screen management opens. It is empty now because this practice CTDMS is brand new.",
         do="Make sure you are on the Dashboard.", keys="Esc goes back one level from any screen; on top-level "
         "screens it returns to the Dashboard.", expect="Dashboard with empty figures.", page="/",
         ui={"path": r"^/$"}),
    Step("help", "All keys: F1",
         what="Every screen works from the keyboard, like MARG. The same key always means the same thing.",
         why="Staff keep their hands on the keyboard: codes, Enter, arrows and function keys.",
         do="Press F1 now.", keys="F1 = list of keys; Esc closes it.",
         expect="The key list opens, with the keys of this screen at the bottom.", ui={"exists": "#kb-help"}),
    Step("help-close", "Close with Esc", do="Press Esc to close the key list.", keys="Esc",
         expect="The list closes.", ui={"absent": "#kb-help"}),
    Step("goto", "Jump to any screen: F3",
         what="F3 opens 'Go to': type part of a screen name and press Enter.",
         why="Faster than the menu - and no mouse.", do="Press F3, type  depot  and press Enter.",
         keys="F3 · type · ↓↑ to choose · Enter", expect="The Depot Master opens.", ui={"path": r"^/masters/depots"}),
    Step("back", "Back with Esc", do="Press Esc to go back.", keys="Esc", expect="You are back on the Dashboard.",
         ui={"path": r"^/$"}),
    Step("menus", "The menus",
         what="Dispatch desk (daily gatepasses), Operations (invoices, imports, bills, approvals), Master Settings "
              "(companies, depots, customers, transporters, rates, codes), Reports, Administration and Help.",
         why="Masters come first: every invoice, dispatch and report depends on them. That is the next lesson.",
         do="Press Ctrl+Enter to finish this lesson."),
])

L02 = Lesson("m02-workbook", "One-time Master Workbook upload",
             "Upload DEMO_v1.6_Master_Workbook.xlsx, read the problems, import the valid records.",
             start="clean", needs="is_clean", video=("Step 1 ",), page="/masters/upload", steps=[
    Step("open", "Initial Master Upload",
         what="This is where the consolidated master workbook is uploaded once, on the first day of CTDMS.",
         why="All companies, depots, customers, customer types, locations, transporters, local autos, rates and code "
             "mappings arrive together and are checked together.",
         do="Open Master Settings › Initial Master Upload.", keys="F3, type  upload , Enter",
         page="/masters/upload", ui={"path": r"^/masters/upload$"}, hl="h1"),
    Step("workbook", "The workbook",
         what="The blank workbook has an Instructions sheet and 11 sheets: ERP systems, locations, companies, depots, "
              "customer types, customers, transporters, local-auto rates, freight rates, invoice code mappings and ERP "
              "mappings. Product / SKU sheets are not part of v1.6.",
         why="Every row is validated before anything is saved, so a mistake never reaches the masters.",
         do="Download the demonstration workbook with the button below (it already contains four deliberate problems).",
         files=(WB,), hl="a[href='/masters/upload/template.xlsx']"),
    Step("check", "Select the demonstration workbook",
         what="Check workbook validates every row: mandatory fields, duplicates, and links between sheets.",
         do=f"In 'Upload the master workbook' choose {WB} and press Check workbook.",
         keys=FILE_KEYS + " Then F6 (Check workbook).",
         expect="A preview with accepted and rejected counts per sheet. Nothing is saved yet.",
         hl="form[action='/masters/upload'] input[name=file]", check="workbook_previewed", files=(WB,)),
    Step("problems", "Read the four problems",
         what="1) Depot DM-TEST has no city - rejected (city is mandatory). 2) Customer DM-C025 is typed twice - the "
              "second row is rejected as a duplicate. 3) DM-C099's delivery location 'Nowhere' does not exist - rejected. "
              "4) Customer type RETAILER is already in CTDMS - skipped, never duplicated or overwritten.",
         why="Each rejected row says exactly why; fix the workbook (or add the missing location first) and upload "
             "those rows again later.",
         do="Read the rows marked Rejected / Already in CTDMS on the preview (↓↑ move through the rows).",
         expect="The four problem rows, each with its reason.", ui={"path": r"^/masters/upload/\d+"}),
    Step("import", "Import the valid records",
         what="Only the valid new rows are created. The freight rates go to a checker for approval.",
         do="Press Import … valid records and answer Yes.", keys="F6, then Y (or Enter on Yes)",
         expect="Imported - the masters now exist in your practice CTDMS.",
         hl="form[action$='/commit'] button", check="workbook_imported"),
    Step("history", "Upload history",
         what="Every upload stays in the history with its counts; the rejected rows can be downloaded as an Excel "
              "error report, corrected and uploaded again.",
         why="After the first day, single new records are added in Master Settings - nobody re-uploads the workbook.",
         do="Go back to Initial Master Upload and look at the history.", keys="Esc",
         ui={"path": r"^/masters/upload$"}, hl="h3"),
], source="v1.6 guide video, v1.6 manual chapter 7 step 1")

L03 = Lesson("m03-masters", "Understanding the created Masters",
             "Companies, depots (two in Raipur), customers, types, transporters, rates, codes - and rate approval.",
             start="uploaded", needs="has_masters", video=("Step 2 ",), page="/masters/companies", steps=[
    Step("companies", "Company Master",
         what="Each company keeps its code and a short recognition code: ABC, XYZ, PQR and TDL (own distribution), "
              "linked to its billing system.", do="Open Master Settings › Companies.", keys="F3, type  compan , Enter",
         page="/masters/companies", ui={"path": r"^/masters/companies"}),
    Step("depots", "Depot Master - several depots in one city",
         what="Raipur has TWO depots: Guma and Kamal Vihar. Same city, separate depots, each with its own code, "
              "locality and recognition code. Bilaspur has its own depot.",
         why="Depot-wise reports keep Guma and Kamal Vihar apart; City-wise reports add them up under Raipur.",
         do="Open Depots, move to DM-GUMA and open it.", keys="F3 depot Enter · ↓ to the DM-GUMA row · Enter",
         page="/masters/depots", ui={"path": r"^/masters/depots/\d+"}, hl="table.data"),
    Step("depot-inside", "Inside a depot",
         what="Locality (Guma), city (Raipur), address, the companies it handles and its usual billing system.",
         do="Read the depot, then press Esc to return to the list.", keys="Esc", ui={"path": r"^/masters/depots$"}),
    Step("ctypes", "Customer Types",
         what="Customer types are a master (not fixed in the program): retailer, stockist, distributor, hospital, "
              "institution, chain pharmacy, other, plus Super Stockist and Government Supply from the workbook. "
              "Government Supply has no code yet - that comes back in lesson 5.",
         do="Open Master Settings › Customer Types.", keys="F3 customer types Enter",
         page="/masters/customer-types", ui={"path": r"^/masters/customer-types"}),
    Step("customers", "Customer Master",
         what="Customer code, name, type, recognition code (C025), delivery location, companies served, and the code "
              "the source ERP uses (SAP 100025), so imports find the customer automatically.",
         do="Open Customers and open DM-C025 Sharma Medical Store.", keys="F3 customers Enter · F4 · type C025 · Enter · ↓ Enter",
         page="/masters/customers", ui={"path": r"^/masters/customers/\d+"}),
    Step("locations", "Areas / Locations", what="Delivery areas: Raipur City, Bhilai, Durg, Bilaspur City, Korba. "
         "Freight rates are per area.", do="Open Areas / Locations.", keys="F3 locations Enter",
         page="/masters/locations", ui={"path": r"^/masters/locations"}),
    Step("transporters", "Long-distance transporters",
         what="Shree Roadlines and Vijay Transport work exactly as in v1.5: a rate card per area, LR numbers, a "
              "monthly bill.", do="Open Transporters (long-distance).", keys="F3 transporters Enter",
         page="/masters/transporters", ui={"path": r"^/masters/transporters"}),
    Step("autos", "Local Auto Transporters",
         what="Sharma Local Auto charges per trip (usually ₹500), Ravi Tempo per box, Kishore Mini Truck either way.",
         do="Open Local Auto Transporters.", keys="F3 local auto Enter", page="/masters/autos",
         ui={"path": r"^/masters/autos"}),
    Step("auto-rates", "Local Auto Box Rates",
         what="Ravi Tempo: ₹20 a box in Raipur, ₹25 to Bhilai, ₹22 for PQR's goods in Raipur - the most specific "
              "rate wins.", do="Open Local Auto Box Rates.", keys="F3 box rates Enter",
         page="/masters/local-auto-rates", ui={"path": r"^/masters/local-auto-rates"}),
    Step("rate-cards", "Freight rates (rate cards)",
         what="Each long-distance transporter has a rate per case for each area (Shree Roadlines: Bilaspur ₹22, "
              "Korba ₹30, Durg ₹14, Bhilai ₹15).", do="Open Transporter Rate Cards.", keys="F3 rate cards Enter",
         page="/rate-cards", ui={"path": r"^/rate-cards"}),
    Step("markings", "Invoice Code Mappings",
         what="The markings billing systems already put in invoice numbers: in MARG, XY/G/ = XYZ Healthcare from Guma, "
              "XY/K/ = from Kamal Vihar.", do="Open Invoice Code Mappings.", keys="F3 code mappings Enter",
         page="/masters/invoice-markings", ui={"path": r"^/masters/invoice-markings"}),
    Step("erp-maps", "ERP Mappings",
         what="The same customer has a different code in each ERP (MARG SH025, CBO CB-031 …); the SAP plant 1101 is "
              "Guma depot.", do="Open ERP Mappings.", keys="F3 erp mappings Enter", page="/masters/erp-mappings",
         ui={"path": r"^/masters/erp-mappings"}),
    Step("two-person", "Rate approval - the two-person rule",
         what="The freight rates from the workbook wait for approval. The person who uploaded them may not approve "
              "them; a checker must.",
         why="No single person can set and approve the rates that decide what transporters are paid.",
         do="Press 'Act as Demo Checker' in this panel. In practice you stay signed in as yourself - only this practice "
            "CTDMS switches to its demonstration checker."),
    Step("approve", "Approve the rates as the checker",
         do="Open Approvals and press Approve all.", keys="F3 approvals Enter · Tab to Approve all · Enter",
         expect="All rate-card rates approved by the demo checker.", page="/approvals", acting="demo.checker",
         check="rates_approved_by_checker", hl="form[action='/rate-cards/approve-all'] button"),
    Step("self", "Back to yourself", do="Press 'Act as myself' in this panel.", ui={"self": True}),
], source="v1.6 guide video")

L04 = Lesson("m04-add-record", "Adding a new Master record",
             "After day one: add Mohan's e-rickshaw in Master Settings and see it on the gatepass.",
             start="masters", needs="masters_no_mohan", video=("Step 3 ",), page="/masters/autos", steps=[
    Step("why", "Single records, not a new workbook",
         what="After the first day nobody re-uploads the workbook. A new company, depot, customer or transporter is "
              "added in Master Settings, one record at a time, with the same checks.",
         do="Next."),
    Step("new", "New local auto", do="Open Local Auto Transporters and press F2 (New).",
         keys="F3 local auto Enter · F2", page="/masters/autos", ui={"path": r"^/masters/autos/new"}),
    Step("fill", "Mohan's e-rickshaw",
         what="Charged per trip, usually ₹300.",
         do="Code DM-LA4 · Name Mohan E-Rickshaw (Demo) · Mobile 9000003004 · Vehicle CG04-ER-1004 · Charging: Per trip "
            "· Usual trip charge 300 · Save.",
         keys="Type, Enter to the next field; in 'Charging allowed' type  trip  Enter; F6 saves.",
         expect="Saved and listed.", check="mohan_added"),
    Step("available", "Available at once",
         what="The new auto is in the Local auto list of the gatepass straight away.",
         do="Open Scan & Gatepass and look at the Local auto list (choose mode Local auto).",
         keys="F3 scan Enter · Enter on the empty scan box · → → (Local auto) · Enter · ↓ to open the list",
         page="/gatepass/new", ui={"path": r"^/gatepass/new", "option": ["#gp-auto", "Mohan"]}),
    Step("never-delete", "Inactive, never deleted",
         what="A record that was used is made inactive, never deleted, so the history stays complete.", do="Finish."),
], source="v1.6 guide video")

L05 = Lesson("m05-recognition", "Invoice Recognition Codes",
             "Original ERP number vs internal code: ABC-RET-C025-GUMA-001245; a missing-code warning and its fix.",
             start="masters", needs="masters_no_invoices", video=("Step 4 ",), page="/masters/recognition", steps=[
    Step("settings", "The Invoice Recognition Code",
         what="One internal code tells at a glance which company, customer type, customer and depot an invoice "
              "belongs to: company ABC + type RET + customer C025 + depot GUMA + the number part 001245.",
         why="It makes invoices easy to read, search and report - without ever touching the ERP number.",
         do="Open Master Settings › Invoice Recognition Code.", keys="F3 recognition Enter",
         page="/masters/recognition", ui={"path": r"^/masters/recognition"}),
    Step("original", "The original number never changes",
         what="Original ERP invoice number 001245 stays exactly as SAP sent it; ABC-RET-C025-GUMA-001245 is stored "
              "beside it. The pattern can be changed here and the whole feature can be switched off.", do="Next."),
    Step("import", "Import ABC Pharma's SAP file",
         what="The plant column tells the depot (ERP mapping 1101 = Guma); SAP's customer numbers find our customers.",
         do=f"Download {SAP}. Operations › Invoice Import › 'Upload with column mapping': choose the file, ERP = SAP, "
            "Company = ABC Pharma, Upload & preview; then Import the valid rows.",
         keys=FILE_KEYS + " Tab to ERP: type SAP Enter · Company: type ABC Enter · F6 · on the batch: F6 (Import).",
         page="/imports", files=(SAP,), check="sap_imported", hl="form[action='/imports/upload']"),
    Step("view", "View a recognition code",
         do="Open the Invoice Register and search 001245.", keys="F3 invoice register Enter · F4 · type 001245 · Enter",
         expect="001245 with ABC-RET-C025-GUMA-001245 next to it.", page="/invoices",
         ui={"path": r"^/invoices", "query": "001245"}),
    Step("search", "Search by the code",
         what="Every code starts with the same parts, so ABC-RET lists every ABC Pharma invoice to retailers.",
         do="In the Recognition code filter type ABC-RET and press Enter.", keys="Tab to 'Recognition code' · type · Enter",
         page="/invoices", ui={"path": r"^/invoices", "query": "ABC-RET"}),
    Step("missing", "A missing-code warning",
         what="Invoice 001250 got no code: its customer type, Government Supply, has no short code. CTDMS does not "
              "guess - and the invoice can still be dispatched normally.",
         do="Open Invoice Recognition Code and read 'Invoices without a code'.", keys="F3 recognition Enter",
         page="/masters/recognition", ui={"path": r"^/masters/recognition"}),
    Step("fix", "Correct the customer type code",
         do="Master Settings › Customer Types › open GOVT_SUPPLY, Recognition code = GOV, Save.",
         keys="F3 customer types Enter · ↓ to GOVT_SUPPLY · Enter · Enter to Recognition code · GOV · F6",
         page="/masters/customer-types", check="gov_fixed",
         expect="001250 now has ABC-GOV-C090-BSP-001250; the number 001250 is unchanged."),
], source="v1.6 guide video")

L06 = Lesson("m06-imports", "Invoice import and recognition",
             "MARG and CBO files: company and depot recognised from the invoice-number markings.",
             start="sap", needs="sap_only", video=("Step 5 ",), page="/imports", steps=[
    Step("how", "How CTDMS recognises an invoice",
         what="Source ERP = the file / connection it came from. Company = the file's company, or a marking in the "
              "number (Invoice Code Mappings). Depot = plant code (ERP mapping) or the marking. Customer = the ERP's "
              "customer code (ERP mapping) or our code. Customer type = from the customer. Then the code is built.",
         why="A MARG export has no company or depot column - the bill number XY/G/2601 says it.", do="Next."),
    Step("markings", "The markings", do="Open Invoice Code Mappings: XY/G/, XY/K/, PQR/K/, PQR/B/.",
         keys="F3 code mappings Enter", page="/masters/invoice-markings", ui={"path": r"^/masters/invoice-markings"}),
    Step("marg", "Import XYZ Healthcare's MARG file",
         do=f"Download {MARG}. Invoice Import: choose it, ERP = MARG (no company needed), Upload & preview, Import.",
         keys=FILE_KEYS + " ERP: type MARG Enter · F6 · F6 on the batch.", page="/imports", files=(MARG,),
         check="marg_imported"),
    Step("cbo", "Import PQR Lifesciences' CBO file",
         do=f"Download {CBO}. Same steps with ERP = CBO.", keys=FILE_KEYS + " ERP: CBO.", page="/imports",
         files=(CBO,), check="cbo_imported"),
    Step("register", "The Invoice Register", what="13 invoices: original ERP number and recognition code side by side; "
         "company, depot, customer and type identified.", do="Open the Invoice Register.", keys="F3 invoice register Enter",
         page="/invoices", ui={"path": r"^/invoices$"}),
    Step("mappings", "When something is missing",
         what="An ERP customer code that is not mapped makes the row 'customer not found'. The Master adds the code "
              "(Customer › ERP codes, or ERP Mappings) and presses Re-validate & retry on the batch. SKU / product "
              "lines are not needed in v1.6.", do="Open ERP Mappings to see where codes are added.",
         keys="F3 erp mappings Enter", page="/masters/erp-mappings", ui={"path": r"^/masters/erp-mappings"}),
], source="v1.6 guide video")

GP_OPEN = Step("open", "Scan & Gatepass", what="One gatepass per pickup: transporter trip or local-auto trip.",
               do="Open Scan & Gatepass.", keys="F2 on the Dashboard, or F3 scan Enter", page="/gatepass/new",
               ui={"path": r"^/gatepass/new"})

L07 = Lesson("m07-long", "Regular long-distance dispatch",
             "Shree Roadlines from Bilaspur: invoices, transporter, rate card, gatepass, print, gate-out, LR.",
             start="invoices", needs="long_waiting", video=("Step 6 ",), page="/gatepass/new", steps=[
    Step("unchanged", "Long distance works as before",
         what="The third-party transporter workflow is unchanged from v1.5: freight = cases × the transporter's rate "
              "for the area; the transporter bills later (TBB).", do="Next."),
    GP_OPEN,
    Step("depot", "Choose the depot", do="Depot: type the code BSP and press Enter.",
         keys="The cursor is already in Depot · type BSP · Enter", ui={"value": ["#gp-cfa", "BSP|Bilaspur"]},
         hl="#gp-cfa-q"),
    Step("invoices", "Add the three invoices",
         do="Add 001248, 001249 and PQR/B/0313: type each number + Enter, or ↓ into the waiting list and Enter.",
         keys="type 001248 Enter · ↓ (into the list) · Enter on a row adds it",
         ui={"text": ["#gp-lines tbody", "001248", "001249", "PQR/B/0313"]}, hl="#gp-scan"),
    Step("transporter", "Select the transporter",
         what="Transporter boxes are searchable by code or name.",
         do="Move on to Who carries the goods and choose Shree Roadlines.",
         keys="Enter on the empty scan box · Enter (mode stays Transporter) · type SRL · ↓↑ · Enter",
         ui={"value": ["#gp-tr", "Shree"]}, hl="#gp-tr-q"),
    Step("rates", "Freight from the rate card",
         what="Rate/case comes from Shree Roadlines' card for each area (Bilaspur ₹22 per case); the charge column "
              "and the total update at once.", do="Check the Charges panel, then Next."),
    Step("generate", "Generate the gatepass", do="Generate gatepass.",
         keys="Enter through the fields to Generate gatepass, Enter - or F8 at any time", check="long_generated",
         hl="#gp-submit"),
    Step("print", "Print", what="Nothing leaves the gate without a printed gatepass. In training it prints "
         "TRAINING - NOT VALID.", do="Print the gatepass.", keys="F8", check="long_printed",
         hl="[data-key=F8]"),
    Step("gate-out", "Gate-out", do="Record gate-out and answer Yes.", keys="F9 · Enter (Yes)", check="long_gone",
         hl="[data-key=F9]"),
    Step("lr", "LR numbers", what="The transporter gives the LR (bilty) numbers later - one per customer.",
         do="Enter SRL-5501 for WellCare Pharmacy and SRL-5502 for Bilaspur Super Stockist, then Save LR numbers.",
         keys="Tab to the LR boxes · type · F6", check="long_lr", hl="input[name^=lr_]"),
], source="v1.6 guide video")

L08 = Lesson("m08-trip", "Local auto - per trip",
             "Sharma Local Auto: four invoices (5, 15, 6, 4 boxes), ONE trip charge of ₹500.",
             start="invoices", needs="trip_waiting", video=("Step 7 ",), page="/gatepass/new", steps=[
    Step("idea", "One trip, one amount",
         what="A per-trip local auto is owed one amount for the whole trip, however many invoices or boxes it carries.",
         why="₹500 is the charge for the trip - not ₹500 per invoice (that would be ₹2,000).", do="Next."),
    GP_OPEN,
    Step("depot", "Guma depot", do="Depot: type GUMA, Enter.", ui={"value": ["#gp-cfa", "GUMA|Guma"]}, hl="#gp-cfa-q"),
    Step("invoices", "Four invoices", do="Add 001245, 001246, 001247 and XY/G/2601.",
         keys="type the number + Enter, or ↓ Enter in the waiting list",
         ui={"text": ["#gp-lines tbody", "001245", "001246", "001247", "XY/G/2601"]}),
    Step("mode", "Mode: Local auto", do="Move to Mode of dispatch and choose Local auto.",
         keys="Enter on the empty scan box · → → until Local auto", ui={"checked": "input[name=mode][value=OUTSIDE_AUTO]"},
         hl=".gp-modes"),
    Step("auto", "Select Sharma Local Auto",
         what="Local autos can be charged per trip or per box; the methods allowed come from the Local Auto master.",
         do="Select Sharma Local Auto.", keys="Enter · type LA1 (or Sharma) · Enter",
         ui={"value": ["#gp-auto", "Sharma"]}, hl="#gp-auto-q"),
    Step("trip", "Enter today's actual trip charge",
         what="The usual ₹500 is filled in; it could be ₹450 or ₹550 on another day.",
         do="Make sure the trip charge is 500.", keys="type 500 over the amount · Enter",
         ui={"value": ["#gp-trip", r"^500(\.0+)?$"]}, hl="#gp-trip"),
    Step("shares", "Shared by boxes - for the MIS only",
         what="30 boxes: 5/30 × 500 = ₹83.33, 15/30 = ₹250, 6/30 = ₹100, 4/30 = ₹66.67 - together exactly ₹500.",
         do="Look at the Charge column, then Next."),
    Step("generate", "Generate", keys="Enter to Generate gatepass · Enter (or F8)", check="trip_generated",
         do="Generate the gatepass.", hl="#gp-submit"),
    Step("out", "Print and gate-out", do="Print, then record gate-out.", keys="F8 · then F9 · Enter",
         check="trip_gone"),
    Step("adds-up", "The shares add back to ₹500", what="CTDMS books each invoice's share; the auto is owed ₹500 once.",
         do="Press Ctrl+Enter - CTDMS adds up the four shares.", check="trip_allocation"),
], source="v1.6 guide video")

L09 = Lesson("m09-box", "Local auto - per box",
             "Ravi Tempo: 15 × ₹20 + 6 × ₹22 = ₹432; the missing Durg rate; Kishore Mini Truck's two methods.",
             start="invoices", needs="box_waiting", video=("Step 8 ",), page="/gatepass/new", steps=[
    Step("idea", "Boxes × rate", what="A per-box auto is paid boxes × the rate for each invoice (Local Auto Box "
         "Rates: location, company, depot - the most specific wins).", do="Next."),
    GP_OPEN,
    Step("depot", "Kamal Vihar depot", do="Depot: KVR, Enter.", ui={"value": ["#gp-cfa", "KVR|Kamal"]}),
    Step("invoices", "Three invoices", do="Add XY/K/2603, PQR/K/0311 and XY/K/2604 (Durg).",
         ui={"text": ["#gp-lines tbody", "XY/K/2603", "PQR/K/0311", "XY/K/2604"]}),
    Step("auto", "Ravi Tempo", do="Mode Local auto, then Ravi Tempo.", keys="Enter · → → · Enter · type LA2 · Enter",
         ui={"value": ["#gp-auto", "Ravi"]}),
    Step("no-rate", "The missing-rate warning",
         what="Ravi Tempo has no per-box rate for Durg. CTDMS will not generate the gatepass until that is solved.",
         why="An auto must never be paid at a guessed rate.", do="Read the message under the Generate button.",
         ui={"text": ["#gp-why", "no per-box rate for Durg"]}, hl="#gp-why"),
    Step("remove", "Handle it: take the Durg invoice off",
         what="The operator either takes XY/K/2604 off this trip (it can go with another carrier) or the Master adds a "
              "Durg box rate first.", do="Remove XY/K/2604 from the gatepass.",
         keys="Tab to the ✕ of the XY/K/2604 row · Enter",
         ui={"notext": ["#gp-lines tbody", "XY/K/2604"], "text": ["#s-total", "432"]}),
    Step("total", "15 × ₹20 + 6 × ₹22 = ₹432",
         what="XY/K/2603: 15 boxes at Ravi's Raipur rate ₹20 = ₹300. PQR/K/0311: 6 boxes at PQR's own Raipur rate ₹22 "
              "= ₹132.", do="Generate the gatepass.", keys="F8", check="box_generated"),
    Step("out", "Print and gate-out", do="Print and record gate-out.", keys="F8 · F9 · Enter", check="box_gone"),
    Step("both", "Kishore Mini Truck - either method",
         what="An auto allowed both ways shows Per trip / Per box on the gatepass; the operator chooses each trip.",
         do="New gatepass (F2 from the Dashboard or F3 scan) › Guma › add XY/G/2602 › Local auto › Kishore.",
         keys="type LA3 to find Kishore", page="/gatepass/new", ui={"visible": "#gp-m-box", "value": ["#gp-auto", "Kishore"]}),
], source="v1.6 guide video")

L10 = Lesson("m10-reports", "Reports and MIS",
             "Local Auto Trips, company / depot / city / invoice / transporter reports, dashboard, exports.",
             start="dispatched", needs="has_dispatches", video=("Step 10 ",), page="/reports/outside-auto", steps=[
    Step("trips", "Local Auto Trips",
         what="One row per trip: owed to the auto and booked on its invoices are always equal - the four-invoice trip "
              "shows ₹500, not ₹2,000. Nothing is counted twice.", do="Open Reports › Local Auto Trips.",
         keys="F10 (Reports) · ↓ · Enter, or F3 local auto trips", page="/reports/outside-auto",
         ui={"path": r"^/reports/outside-auto"}),
    Step("company", "Company-wise", do="Open Company-wise freight.", keys="F3 company-wise Enter",
         page="/reports/company-freight", ui={"path": r"^/reports/company-freight"}),
    Step("depot", "Depot-wise", what="Each depot separately, with its city: Guma and Kamal Vihar are two rows.",
         do="Open Depot-wise.", keys="F3 depot-wise Enter", page="/reports/cfa-freight", ui={"path": r"^/reports/cfa-freight"}),
    Step("city", "City-wise", what="All depots of a city together: Guma + Kamal Vihar = Raipur.", do="Open City-wise.",
         keys="F3 city Enter", page="/reports/city-freight", ui={"path": r"^/reports/city-freight"}),
    Step("invoice", "Invoice-wise", what="Original ERP number and recognition code in separate columns, with company, "
         "depot, city, customer type, boxes and transportation expense.", do="Open Invoice-wise.",
         keys="F3 invoice-wise Enter", page="/reports/invoice-freight", ui={"path": r"^/reports/invoice-freight"}),
    Step("transporter", "Transporter-wise", do="Open Transporter-wise.", keys="F3 transporter-wise Enter",
         page="/reports/transporter", ui={"path": r"^/reports/transporter"}),
    Step("dashboard", "Dashboard totals", do="Open the Dashboard.", keys="F3 dashboard Enter (or Esc)",
         page="/", ui={"path": r"^/$"}),
    Step("export", "Export", what="Every report exports to Excel, CSV or PDF. In training the file is marked "
         "TRAINING - NOT VALID FOR BUSINESS USE.", do="Open any report and press Excel (or CSV / PDF).",
         keys="Tab to the export buttons · Enter", check="report_exported", perm="data.export"),
], source="v1.6 guide video")

L11 = Lesson("m11-users", "Users, roles and depot permissions",
             "Create a practice Dispatch Operator for Guma only; roles and permissions.",
             start="masters", needs="has_masters", page="/admin/users", source="Master User Manual (users & roles)",
             steps=[
    Step("idea", "Who may do what",
         what="Every person has their own login and one role (Administrator, Management, Accounts, Depot Manager, "
              "Dispatch Operator, Checker / Approver, Read-only Viewer); company and depot access can be limited.",
         why="Approvals and audit only work when everyone signs in as themselves.",
         do="Next. (Users made here exist only in your practice CTDMS - no real login is created.)"),
    Step("list", "Administration › Users", do="Open Users.", keys="F3 users Enter", page="/admin/users",
         ui={"path": r"^/admin/users$"}),
    Step("create", "A Dispatch Operator for Guma",
         do="F2 New user: username demo.guma.op, name Guma Operator, role Dispatch Operator, NOT all depots - tick "
            "DM-GUMA only, a password, Save.",
         keys="F2 · type, Enter to move on · role: type Dispatch Enter · Space ticks · F6", check="trainee_user_created"),
    Step("roles", "Roles and permissions", what="Administration › Roles lists every permission per role; changing a "
         "role is audited and signs its users out.", do="Open Roles.", keys="F3 roles Enter", page="/admin/roles",
         ui={"path": r"^/admin/roles"}),
    Step("leavers", "Leavers", what="A leaver is made inactive (never deleted) - history keeps their name - and "
         "'sign out everywhere' ends their sessions at once.", do="Finish."),
])

L12 = Lesson("m12-controls", "System settings and rate approval controls",
             "Dispatch controls, maker-checker, a rate change approved by a second person.",
             start="masters", needs="has_masters", page="/admin/settings",
             source="Master User Manual (settings, approvals) adapted to v1.6", steps=[
    Step("settings", "System Settings",
         what="Business rules: a gatepass must be PRINTED before gate-out, reprints need a reason, rates need approval, "
              "nobody approves their own rate or bill, reconciliation tolerances, the Guided Training switch.",
         do="Open Administration › System Settings.", keys="F3 system settings Enter", page="/admin/settings",
         ui={"path": r"^/admin/settings"}),
    Step("change", "Change one setting (practice only)",
         do="Change Default slip paper size to A4, type a reason, Save settings.",
         keys="Tab / ↓ to the setting · choose · Tab to Reason · F6", check="setting_changed"),
    Step("card", "Propose a rate change", what="Rates are never changed in place: a new rate waits for approval and "
         "the old one ends the day before.", do="Transporter Rate Cards › Shree Roadlines: change Bhilai 15 → 16, reason, "
         "Send for approval.", keys="F3 rate cards Enter · ↓ Enter · Tab to Bhilai · 16 · reason · F6",
         page="/rate-cards", check="rate_proposed"),
    Step("self-block", "You cannot approve your own change",
         do="Press 'Act as Demo Checker' in this panel (a second person)."),
    Step("approve", "Approve as the second person", do="Approvals › approve the Bhilai rate.",
         keys="F3 approvals Enter · Tab to Approve · Enter", page="/approvals", acting="demo.checker",
         check="rate_second_person"),
    Step("self", "Back to yourself", do="Press 'Act as myself' in this panel.", ui={"self": True}),
])

L13 = Lesson("m13-delivery", "Delivery tracking and proof of delivery",
             "Track a shipment, record a delivery with its proof.", start="dispatched", needs="has_open_delivery",
             page="/deliveries", source="Master User Manual (tracking, POD) adapted to v1.6", steps=[
    Step("track", "Track Shipment", what="Search an invoice, LR or gatepass to see its timeline: imported, gatepass, "
         "printed, gate-out, LR, delivered.", do="Open Track Shipment and search SRL-5501.",
         keys="F3 track Enter · type SRL-5501 · Enter", page="/track", ui={"path": r"^/track"}),
    Step("pending", "Deliveries Pending", what="Consignments gone out but not yet confirmed delivered, oldest first.",
         do="Open Deliveries Pending.", keys="F3 deliveries Enter", page="/deliveries", ui={"path": r"^/deliveries"}),
    Step("record", "Record a delivery", what="Delivered, short, damaged or refused, with the date, the receiver and an "
         "optional photo of the signed copy (POD).", do="Tick a consignment and press Mark delivered.",
         keys="↓ to the row · Space ticks · Tab to Mark delivered · Enter", check="delivery_recorded"),
])

L14 = Lesson("m14-bills", "Transporter bills, reconciliation and payment",
             "Enter Shree Roadlines' bill, matching, duplicate-bill detection, approval by a second person, payment.",
             start="dispatched", needs="srl_unbilled", page="/recon",
             source="Master User Manual (freight reconciliation) adapted to v1.6", steps=[
    Step("idea", "Every bill is checked line by line",
         what="Each billed LR is matched with our gatepass: cases, rate, amount. Differences show as variance; LRs we "
              "never sent, consignments already paid in cash or billed twice are blocked.", do="Next."),
    Step("open", "Transporter Bills", do="Open Operations › Transporter Bills.", keys="F3 transporter bills Enter",
         page="/recon", ui={"path": r"^/recon"}),
    Step("enter", "Enter the bill",
         do="Enter / import bill: Shree Roadlines, bill no SRL/OCT/26-01, today's date, amount 924, period from the 1st "
            "of the month to today, tick 'link dispatches', Create bill.",
         keys="F2 (Enter / import bill) · type SRL Enter · Enter to move on · Space ticks · F6", check="bill_entered"),
    Step("amounts", "Type what the transporter billed",
         what="Each line is one LR: SRL-5501 (WellCare, 22 cases) and SRL-5502 (Super Stockist, 20 cases). CTDMS shows "
              "what it calculated from the rate card; you type what the bill says.",
         do="In the bill's lines type 484 for SRL-5501 and 440 for SRL-5502, then save the amounts.",
         keys="Tab to the first amount · type · Enter (next amount) · type · F6", check="bill_matched"),
    Step("duplicate", "Duplicate-bill detection",
         do="Try to enter the same bill number for Shree Roadlines once more.", keys="F3 transporter bills · F2 · same "
         "details · F6", expect="Refused: the bill already exists (duplicate bill).",
         ui={"text": [".alert-error", "duplicate bill"]}),
    Step("review", "Review the rates on the bill",
         what="Rates billed above the rate card can be sent for approval as new rates; otherwise record that the card "
              "rates were checked. Approval opens only after this review.",
         do="Open the bill and press Save rate review (as yourself).", keys="↓ Enter on the bill · Tab to Save rate review · Enter",
         page="/recon", check="bill_rates_reviewed"),
    Step("maker-checker", "A second person approves",
         what="The person who entered or edited a bill may not approve it - a checker does.",
         do="Press 'Act as Demo Checker' in this panel."),
    Step("approve", "Approve for payment", do="Open the bill and press Approve for payment (Y).",
         keys="↓ Enter on the bill · Tab to Approve for payment · Enter · Y", page="/recon",
         acting="demo.checker", check="bill_approved_by_other"),
    Step("pay", "Mark paid", do="Act as yourself again, open the bill, type a payment reference (UTR) and Mark paid.",
         keys="'Act as myself' · ↓ Enter · Tab · type UTR · Enter · Y", check="bill_paid"),
])

L15 = Lesson("m15-audit", "Audit log, exports and demo-versus-live safety",
             "Who changed what; exports; how demonstration data is kept apart from live data.",
             start="masters", needs="anything", page="/reports/audit",
             source="Master User Manual (audit, exports, demonstration data)", steps=[
    Step("audit", "The audit log", what="Every business change: who, when, the old and new value and the reason. It "
         "cannot be edited.", do="Open Reports › Audit.", keys="F3 audit Enter", page="/reports/audit",
         ui={"path": r"^/reports/audit"}),
    Step("filter", "Filter the log", do="Choose Module = MASTER and Apply.", keys="Tab to Module · type MASTER · Enter",
         ui={"path": r"^/reports/audit", "query": "module=MASTER"}),
    Step("export", "Export it", do="Export the audit report (Excel / CSV / PDF).", check="report_exported",
         perm="data.export"),
    Step("safety", "Demonstration versus live",
         what="Training runs in your own practice database: the orange TRAINING bar, every print and export marked "
              "TRAINING - NOT VALID, no WhatsApp / SMS / e-mail / ERP sync. Only your training progress is saved in "
              "live CTDMS. Live work starts when you leave Training Mode.", do="Finish the Master training."),
])

# =============================================================================================== role lessons
K00 = Lesson("k00-keys", "Keyboard basics (MARG style)",
             "F-keys, Enter from field to field, searchable boxes, lists, Esc.", start="invoices", needs="anything",
             video=("Step 9 ",), page="/gatepass", source="v1.6 keyboard-first guide (video step 9)", steps=[
    L01.steps[0],
    Step("help", "All keys: F1", what="The same key does the same job on every screen.", do="Press F1.",
         keys="F1", ui={"exists": "#kb-help"}),
    Step("help-close", "Close with Esc", do="Press Esc.", ui={"absent": "#kb-help"}),
    Step("goto", "F3 - go to a screen", do="Press F3, type  pending  and Enter.", keys="F3 · type · Enter",
         ui={"path": r"^/dispatch/pending"}),
    Step("list", "Arrows in a list", what="↓ from the search box goes into the list; ↓↑ move the yellow row.",
         do="Press ↓ to move into the list.", keys="↓", ui={"focus": "tr[data-kb-row]"}),
    Step("open", "Enter opens the row", do="Press Enter on a row.", keys="Enter", ui={"path": r"^/dispatch/\d+"}),
    Step("combo", "Choose by code", what="Choice boxes (company, depot, customer, transporter, local auto) find "
         "a record by its CODE or part of its NAME.", do="In Transporter type SRL and press Enter.",
         keys="type SRL · ↓↑ · Enter", ui={"value": ["select[name=transporter_id]", "Shree"]}, perm="dispatch.operate"),
    Step("esc", "Esc - back without saving", what="Esc goes back one screen; if something was typed it asks first "
         "with 'Stay here' pre-selected.", do="Press Esc, then Y to leave without saving.", keys="Esc · Y",
         ui={"path": r"^/dispatch/pending"}),
    Step("keys", "The keys you will use most", what="Enter = next field · F2 new · F4 search · F6 save · F8 "
         "gatepass / print · F9 complete dispatch · F10 reports · Esc back. Delete, void and cancel always ask with No "
         "pre-selected.", do="Finish."),
])

D02 = Lesson("d02-gate", "Gate check, LR pending and common errors",
             "Confirm a printed gatepass at the gate; what the usual messages mean.", start="dispatched",
             needs="kishore_waiting_at_gate", page="/gatepass/gate", source="v1.6 manual, problems chapter", steps=[
    Step("gate", "Gate check", what="The guard scans (or types) the gatepass number, counts the cases and confirms "
         "gate-out. Void, unprinted or already-used gatepasses show a red STOP.", do="Open Gate Check.",
         keys="F3 gate check Enter", page="/gatepass/gate", ui={"path": r"^/gatepass/gate"}),
    Step("wrong", "A wrong number", do="Type DS/XX/0000 and press Enter.", keys="type · Enter",
         expect="'No gatepass … Do not let the goods out'", ui={"text": [".alert-error", "No gatepass"]}),
    Step("confirm", "Confirm Kishore Mini Truck's gatepass",
         do="Type the Kishore gatepass number (in 'Waiting at the gate'), Enter, check the cases, Confirm gate-out.",
         keys="type the last digits · Enter · F9", check="kishore_gone"),
    Step("lr", "LR pending", what="Long-distance consignments still waiting for their LR number; type them here as the "
         "transporter sends them.", do="Open LR Pending.", keys="F3 LR pending Enter", page="/gatepass/lr-pending",
         ui={"path": r"^/gatepass/lr-pending"}),
    Step("errors", "Common messages", what="'no rate for <area> on this transporter's rate card' - choose another "
         "transporter or ask for the rate. 'enter the trip charge' - type the amount. 'no per-box rate' - take the "
         "invoice off or ask for the rate. 'same depot' - one gatepass per depot. 'must be printed' - F8 first. "
         "A reprint needs a reason.", do="Finish."),
])

DM1 = Lesson("dm-monitor", "Depot operations and dispatch monitoring",
             "Pending invoices, filters by depot, gatepasses, the gate.", start="invoices", needs="anything",
             page="/dispatch/pending", source="Master User Manual (dispatch control) adapted to v1.6", steps=[
    Step("pending", "Pending Dispatch", what="Every invoice not yet gone out, with its age, boxes, freight and status.",
         do="Open Pending Dispatch.", keys="F3 pending Enter", page="/dispatch/pending", ui={"path": r"^/dispatch/pending"}),
    Step("filter", "Only my depot", do="Open the filters, Depot = GUMA, Filter.", keys="Tab to Depot · type GUMA · Enter · Enter",
         ui={"path": r"^/dispatch/pending", "query": "cfa_id="}),
    Step("gatepasses", "Gatepasses", what="Every pickup with its state: not printed, printed, gone out, void; LR missing.",
         do="Open Gatepasses.", keys="F3 gatepasses Enter", page="/gatepass", ui={"path": r"^/gatepass$"}),
    Step("gate", "At the gate", what="Gate Check shows what is waiting at the gate.", do="Open Gate Check.",
         keys="F3 gate check Enter", page="/gatepass/gate", ui={"path": r"^/gatepass/gate"}),
])

DM2 = Lesson("dm-exceptions", "Transporter selection and freight exceptions",
             "A transporter without a rate for the area; choosing the right one.", start="invoices", needs="box_waiting",
             page="/gatepass/new", source="v1.6 manual, problems chapter", steps=[
    GP_OPEN,
    Step("depot", "Kamal Vihar", do="Depot: KVR, Enter.", ui={"value": ["#gp-cfa", "KVR|Kamal"]}),
    Step("inv", "The Durg invoice", do="Add XY/K/2604 (Durg).", ui={"text": ["#gp-lines tbody", "XY/K/2604"]}),
    Step("vijay", "A transporter without a Durg rate", do="Choose Vijay Transport Co.", keys="Enter · Enter · type VTC · Enter",
         ui={"text": ["#gp-why", "no rate for Durg"]}),
    Step("fix", "Choose one that serves Durg", what="Shree Roadlines has ₹14 per case for Durg. (Or ask the Administrator "
         "to add a Vijay rate, which a checker approves.)", do="Choose Shree Roadlines instead.", keys="Shift+Tab "
         "back to Transporter · SRL · Enter", ui={"value": ["#gp-tr", "Shree"], "notext": ["#gp-why", "no rate"]}),
    Step("exceptions", "Freight Exceptions", what="Invoices with a missing rate, missing boxes or a conflict wait here "
         "for a fix.", do="Open Freight Exceptions.", keys="F3 exceptions Enter", page="/exceptions",
         ui={"path": r"^/exceptions"}),
])

DM3 = Lesson("dm-reports", "Depot registers and delivery status",
             "Dispatch register, deliveries still pending, the gatepass register and its export - your depots only.",
             start="dispatched", needs="has_dispatches", page="/dispatch/register", steps=[
    Step("register", "Dispatch Register", what="Every dispatch slip of your depots by date: transporter, vehicle, LR, "
         "status. Final financial reports (company-wise freight, MIS, variance) are for Administrator, Accounts and "
         "Management only - you see the operational registers.", do="Open Dispatch Register.",
         keys="F3 dispatch register Enter", page="/dispatch/register", ui={"path": r"^/dispatch/register"}),
    Step("deliveries", "Deliveries pending", what="Consignments that left the gate and have no proof of delivery yet; "
         "overdue ones are marked.", do="Open Deliveries Pending.", keys="F3 deliveries Enter", page="/deliveries",
         ui={"path": r"^/deliveries"}),
    Step("gp-register", "Gatepass Register", what="Every gatepass of your depots with its state, charges paid and LR "
         "still missing.", do="Open Gatepass Register (Operational Reports).", keys="F10 (Reports) · Enter, or F3 gatepass register",
         page="/reports/gatepass-register", ui={"path": r"^/reports/gatepass-register"}),
    Step("export", "Export", what="Operational registers export to Excel, CSV or PDF. In training the file is marked "
         "TRAINING - NOT VALID FOR BUSINESS USE.", do="On the Gatepass Register press Excel (or CSV / PDF).",
         keys="Tab to the export buttons · Enter", check="report_exported", perm="data.export"),
])

C01 = Lesson("c-rates", "Pending rate approvals - two people",
             "Approve rates someone else uploaded; see that your own change waits for another approver.",
             start="uploaded", needs="rates_waiting", video=("Step 2 ", "The freight rates from the workbook"), page="/approvals",
             source="v1.6 guide video, Master User Manual (approvals)", steps=[
    Step("idea", "Maker and checker", what="Rates proposed by one person are approved by another. A checker never "
         "approves their own change.", why="The rate decides what every transporter is paid.", do="Next."),
    Step("open", "Approvals", do="Open Approvals.", keys="F3 approvals Enter", page="/approvals", ui={"path": r"^/approvals"}),
    Step("approve", "Approve all", do="Review the rate-card rates uploaded by the demo master and press Approve all.",
         keys="Tab to Approve all · Enter", check="rates_approved_by_checker"),
])

C02 = Lesson("c-exceptions", "Verification and exception handling",
             "A box-change request from the dock: approve or reject with remarks.", start="dispatched",
             needs="has_override", page="/approvals", source="Master User Manual (overrides)", steps=[
    Step("idea", "Box changes need a checker", what="The operator counted 9 cases for invoice 001250; the ERP says 8. "
         "The change waits for a checker.", do="Next."),
    Step("decide", "Decide the request", do="Approvals › the 001250 request: type remarks, Approve (or Reject).",
         keys="F3 approvals Enter · Tab to Remarks · type · Tab to Approve · Enter", page="/approvals",
         check="override_decided"),
])

C03 = Lesson("c-bills", "Reconciliation and bill approval", "Review Shree Roadlines' bill and approve it.",
             start="billed", needs="has_bill", page="/recon", source="Master User Manual (freight reconciliation)",
             steps=[L14.steps[0], L14.steps[1],
                    Step("approve", "Review and approve", do="Open bill SRL/OCT/26-01, Save rate review, Approve for "
                         "payment (Y).", keys="↓ Enter · Tab · Enter · Y", page="/recon", check="bill_approved_by_other")])

A01 = Lesson("a-expense", "Transportation expense", "Where the freight cost is, by transporter, depot and invoice.",
             start="dispatched", needs="has_dispatches", page="/", steps=[L10.steps[6], L10.steps[5], L10.steps[4],
                                                                          L10.steps[7]])

A02 = Lesson("a-bill-entry", "Transporter bills, matching and duplicates",
             "Enter the bill, type the billed amounts, see the matching, try a duplicate.", start="dispatched",
             needs="srl_unbilled", page="/recon", steps=[L14.steps[0], L14.steps[1], L14.steps[2], L14.steps[3],
                                                         L14.steps[4]])

A03 = Lesson("a-pay", "Payment controls", "Rates reviewed, a second person approves, then the bill is paid.",
             start="billed", needs="has_bill", page="/recon", steps=[L14.steps[5], L14.steps[6], L14.steps[7],
                                                                     L14.steps[8]])

V01 = Lesson("v-dashboard", "Dashboard, filters and MIS", "Figures, filters, the drill-down MIS.",
             start="dispatched", needs="has_dispatches", page="/", steps=[
    Step("dashboard", "The Dashboard", what="Invoices, dispatched, boxes, sales value, freight, freight per box, "
         "variance against bills.", do="Open the Dashboard.", keys="F3 dashboard Enter", page="/", ui={"path": r"^/$"}),
    Step("filter", "Filter by company", do="Company filter: type ABC, Enter, then Apply.", keys="Tab to Company · ABC · Enter · Enter",
         ui={"path": r"^/$", "query": "company_id="}),
    Step("mis", "Transportation MIS", what="Drill down company › depot › location › customer › invoice.",
         do="Open Transportation MIS.", keys="F3 MIS Enter", page="/mis", ui={"path": r"^/mis"}),
])

V02 = Lesson("v-reports", "Company and depot reports, exports", "Company-, depot- and city-wise; exports.",
             start="dispatched", needs="has_dispatches", page="/reports", steps=[L10.steps[1], L10.steps[2],
                                                                                 L10.steps[3], L10.steps[7]])

LESSONS: dict[str, Lesson] = {l.code: l for l in (L01, L02, L03, L04, L05, L06, L07, L08, L09, L10, L11, L12, L13,
                                                   L14, L15, K00, D02, DM1, DM2, DM3, C01, C02, C03, A01, A02, A03,
                                                   V01, V02)}

PATHS: dict[str, dict] = {
    "MASTER": {"name": "Master Administrator", "roles": ["ADMIN"],
               "lessons": ["m01-intro", "m02-workbook", "m03-masters", "m04-add-record", "m05-recognition",
                           "m06-imports", "m07-long", "m08-trip", "m09-box", "m10-reports", "m11-users",
                           "m12-controls", "m13-delivery", "m14-bills", "m15-audit"]},
    "DISPATCH": {"name": "Dispatch Operator", "roles": ["DISPATCH_OPERATOR"],
                 "lessons": ["k00-keys", "m07-long", "m08-trip", "m09-box", "d02-gate"]},
    "DEPOT": {"name": "Depot Manager", "roles": ["CFA_MANAGER"],
              "lessons": ["k00-keys", "dm-monitor", "m07-long", "dm-exceptions", "dm-reports"]},
    "CHECKER": {"name": "Checker / Approver", "roles": ["CHECKER"],
                "lessons": ["k00-keys", "c-rates", "c-exceptions", "c-bills"]},
    "ACCOUNTS": {"name": "Accounts", "roles": ["ACCOUNTS"],
                 "lessons": ["k00-keys", "a-expense", "a-bill-entry", "a-pay"]},
    "MANAGEMENT": {"name": "Management", "roles": ["MANAGEMENT"],
                   "lessons": ["v-dashboard", "v-reports"]},
    # a Viewer is not a Management user: no dashboard figures, no final reports - only the keyboard basics
    "VIEWER": {"name": "Read-only Viewer", "roles": ["VIEWER"], "lessons": ["k00-keys"]},
}


def path_for_role(role_code: str | None, can=lambda p: False) -> str:
    for code, p in PATHS.items():
        if role_code in p["roles"]:
            return code
    # a role made by the Master: decide by what it may do
    if can("admin.users"):
        return "MASTER"
    if can("mis.view") and not can("recon.pay"):
        return "MANAGEMENT"
    if can("recon.pay"):
        return "ACCOUNTS"
    if can("rate.approve") or can("override.approve"):
        return "CHECKER"
    if can("invoice.import") and can("dispatch.operate"):
        return "DEPOT"
    if can("dispatch.operate"):
        return "DISPATCH"
    return "VIEWER"


def steps_for(lesson: Lesson, user) -> list[Step]:
    """The steps this user sees (a step that needs a permission the role lacks is left out)."""
    return [s for s in lesson.steps if not s.perm or (user is not None and user.can(s.perm))]
