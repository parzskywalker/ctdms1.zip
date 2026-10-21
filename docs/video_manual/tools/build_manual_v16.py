"""PDF: CTDMS v1.6 Master User Manual - Demonstration Edition (from the finished v1.6 demonstration database).
Run with the playwright venv python:  build_manual_v16.py <shots_dir> <demo.db> <exports_dir> <out.pdf> <video_len>"""
import re
import sqlite3
import sys
from pathlib import Path

from PIL import Image as PI
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

sys.path.insert(0, str(Path(__file__).parent))

SH, DB, EXP, OUT = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), sys.argv[4]
VIDEO_LEN = sys.argv[5] if len(sys.argv) > 5 else ""

pdfmetrics.registerFont(TTFont("S", "C:/Windows/Fonts/segoeui.ttf"))
pdfmetrics.registerFont(TTFont("SB", "C:/Windows/Fonts/segoeuib.ttf"))
pdfmetrics.registerFont(TTFont("SI", "C:/Windows/Fonts/segoeuii.ttf"))
pdfmetrics.registerFont(TTFont("SY", "C:/Windows/Fonts/seguisym.ttf"))
pdfmetrics.registerFontFamily("S", normal="S", bold="SB", italic="SI", boldItalic="SB")
NAVY = colors.HexColor("#1f4e79"); DARK = colors.HexColor("#0f2238"); LIGHT = colors.HexColor("#eef3f8")
AMBER = colors.HexColor("#fff4e0"); GREEN = colors.HexColor("#e8f5e9"); RED = colors.HexColor("#fdecea")
base = ParagraphStyle("b", fontName="S", fontSize=10, leading=14.5, spaceAfter=5)
H1 = ParagraphStyle("h1", parent=base, fontName="SB", fontSize=20, leading=24, textColor=NAVY, spaceBefore=4, spaceAfter=10, keepWithNext=1)
H2 = ParagraphStyle("h2", parent=base, fontName="SB", fontSize=13, leading=17, textColor=NAVY, spaceBefore=10, spaceAfter=4, keepWithNext=1)
BUL = ParagraphStyle("bul", parent=base, leftIndent=14, bulletIndent=3, spaceAfter=2.5)
CAP = ParagraphStyle("cap", parent=base, fontName="SI", fontSize=8.5, leading=11, textColor=colors.HexColor("#555"), alignment=TA_CENTER, spaceAfter=8)
CELL = ParagraphStyle("c", parent=base, fontSize=8.3, leading=10.6, spaceAfter=0)
CELLB = ParagraphStyle("cb", parent=CELL, fontName="SB")
CELLW = ParagraphStyle("cw", parent=CELLB, textColor=colors.white)
MONO = ParagraphStyle("m", parent=CELL, fontName="SB", textColor=colors.HexColor("#1f4e79"))


class Doc(BaseDocTemplate):
    def __init__(self, fn):
        super().__init__(fn, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2.0 * cm, bottomMargin=1.9 * cm,
                         title="CTDMS v1.6 - Master User Manual (Demonstration Edition)", author="CTDMS")
        f = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="f")
        lw, lh = landscape(A4)
        fl = Frame(1.5 * cm, 1.7 * cm, lw - 3 * cm, lh - 3.3 * cm, id="fl")
        self.addPageTemplates([PageTemplate(id="cover", frames=[f], onPage=self.cover),
                               PageTemplate(id="body", frames=[f], onPage=lambda c, d: self._foot(c, d, A4[0])),
                               PageTemplate(id="land", frames=[fl], pagesize=landscape(A4),
                                            onPage=lambda c, d: self._foot(c, d, landscape(A4)[0]))])

    def cover(self, c, d):
        c.saveState(); c.setFillColor(DARK); c.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#b45309")); c.rect(0, A4[1] * 0.36, A4[0], 5, fill=1, stroke=0); c.restoreState()

    def _foot(self, c, d, w):
        c.saveState(); c.setFont("S", 8); c.setFillColor(colors.HexColor("#666"))
        c.drawString(2 * cm, 1.0 * cm, "CTDMS v1.6 - Master User Manual - Demonstration Edition  |  all records shown are DEMONSTRATION ENTRIES")
        c.drawRightString(w - 2 * cm, 1.0 * cm, "Page %d" % (d.page - 1))
        c.setStrokeColor(colors.HexColor("#cfd8e3")); c.line(2 * cm, 1.4 * cm, w - 2 * cm, 1.4 * cm); c.restoreState()

    def afterFlowable(self, fl):
        if isinstance(fl, Paragraph) and fl.style.name in ("h1", "h2"):
            key = "k%d" % (abs(hash(fl.getPlainText())) % 10 ** 9)
            self.canv.bookmarkPage(key)
            self.notify("TOCEntry", (0 if fl.style.name == "h1" else 1, fl.getPlainText(), self.page - 1, key))


S = []
P = lambda t, st=base: S.append(Paragraph(t, st))
def h1(t, new=True):
    if new:
        S.append(PageBreak())
    P(t, H1)
def h2(t): P(t, H2)
def bl(items):
    for i in items:
        S.append(Paragraph(i, BUL, bulletText="•"))
def num(items):
    for n, i in enumerate(items, 1):
        S.append(Paragraph(i, BUL, bulletText="%d." % n))
def shot(name, cap, w=15.4):
    f = SH / f"{name}.png"
    if not f.exists():
        return
    im = PI.open(f); r = im.height / im.width
    if w * r > 21:
        w = 21 / r
    S.append(KeepTogether([Image(str(f), width=w * cm, height=w * cm * r), Paragraph(cap, CAP)]))
def box(txt, kind="tip", width=17):
    bg, label = {"tip": (GREEN, "TIP"), "warn": (AMBER, "CAUTION"), "demo": (AMBER, "DEMONSTRATION"), "prob": (RED, "PROBLEM")}[kind]
    t = Table([[Paragraph(f"<b>{label}</b> &nbsp;{txt}", CELL)]], colWidths=[width * cm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#b9c4d0")),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    S.append(t); S.append(Spacer(1, 7))
def table(head, rows, widths, styles=None):
    data = [[Paragraph(h, CELLW) for h in head]] + [[c if isinstance(c, Paragraph) else Paragraph(str(c), CELL) for c in r] for r in rows]
    t = Table(data, colWidths=[w * cm for w in widths], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                           ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#c4cfdb")),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    S.append(t); S.append(Spacer(1, 8))
def inr(x):
    return "-" if x is None else f"₹{float(x):,.2f}"
TICK = '<font name="SY">☑</font>'
mono = lambda t: Paragraph(f'<font name="SB" color="#1f4e79">{t}</font>', CELL)

# ------------------------------------------------------------------------- facts from the demonstration database
db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
q = lambda sql: list(db.execute(sql))
erps = q("select code, name, adapter, is_active, notes from erp_systems order by id")
companies = q("select c.code, c.name, c.marking, e.code erp, c.relationship_type rel from companies c left join erp_systems e on e.id=c.erp_system_id order by c.id")
depots = q("select id, code, name, locality, city, marking, (select group_concat(co.code, ', ') from company_cfas cc join companies co on co.id=cc.company_id where cc.cfa_id=cfas.id) cos from cfas order by city, code")
ctypes = q("select code, name, marking from customer_types order by sort_order, name")
customers = q("""select cu.code, cu.name, cu.party_type, ct.name tname, cu.marking, l.name loc, e.code erp, cu.source_erp_code
                 from customers cu left join customer_types ct on ct.code=cu.party_type left join locations l on l.id=cu.location_id
                 left join erp_systems e on e.id=cu.source_erp_id order by cu.code""")
locations = q("select code, name, city from locations order by id")
transporters = q("select code, name, payment_mode from transporters order by id")
autos = q("select code, name, allowed_methods, default_trip_charge, default_box_rate, vehicle_no from outside_autos order by id")
auto_rates = q("""select r.code, a.name auto, l.name loc, c.code co, d.code dp, r.rate_per_box from local_auto_rates r
                  join outside_autos a on a.id=r.outside_auto_id left join locations l on l.id=r.location_id
                  left join companies c on c.id=r.company_id left join cfas d on d.id=r.cfa_id order by r.id""")
rates = q("""select t.name tr, l.name loc, r.rate_per_case, r.status from transporter_rates r join transporters t on t.id=r.transporter_id
             join locations l on l.id=r.location_id order by r.id""")
markings = q("""select m.code, e.code erp, m.marking, co.code co, d.code dp from invoice_markings m left join erp_systems e on e.id=m.erp_system_id
                join companies co on co.id=m.company_id left join cfas d on d.id=m.cfa_id order by m.id""")
erpmaps = q("""select e.code erp, m.entity_type ent, m.source_code, m.target_id from erp_code_mappings m join erp_systems e on e.id=m.erp_system_id order by m.id""")
users = q("select username, full_name from users order by id")
invoices = q("""select i.invoice_no, i.recognition_code, i.recognition_note, co.code co, co.name coname, d.name depot, d.city,
                       cu.code cucode, cu.name cust, ct.name ctype, l.name loc, i.boxes, i.sales_value, i.status, i.freight_amount,
                       e.code erp, i.dispatch_no, a.name auto, t.name tr, s.auto_method, s.trip_charge, i.marking_id,
                       (select marking from invoice_markings mk where mk.id=i.marking_id) mk
                from invoices i join companies co on co.id=i.company_id join cfas d on d.id=i.cfa_id
                join customers cu on cu.id=i.customer_id left join customer_types ct on ct.code=cu.party_type
                join locations l on l.id=i.location_id left join erp_systems e on e.id=i.source_erp_id
                left join dispatch_slips s on s.id=i.dispatch_slip_id left join outside_autos a on a.id=s.outside_auto_id
                left join transporters t on t.id=s.transporter_id order by i.id""")
slips = q("""select s.dispatch_no, s.dispatch_mode, s.auto_method, s.trip_charge, s.invoice_count, s.total_cases, s.total_charge,
                    s.freight_total, s.gate_out_at, s.print_count, a.name auto, t.name tr, d.name depot,
                    (select sum(freight) from dispatch_slip_lines ln where ln.slip_id=s.id) allocated
             from dispatch_slips s left join outside_autos a on a.id=s.outside_auto_id left join transporters t on t.id=s.transporter_id
             join cfas d on d.id=s.cfa_id where s.is_void=0 order by s.id""")
upload = q("select id, file_name, counts, status from master_uploads order by id")
audit_n = db.execute("select count(*) from audit_logs").fetchone()[0]
import json
up_counts = json.loads(upload[0]["counts"]) if upload else {}
STATUS = {"PENDING": "Waiting", "SLIP_GENERATED": "Gatepass made", "SLIP_PRINTED": "Gatepass printed", "DISPATCHED": "Gone out",
          "EXCEPTION": "Exception", "CANCELLED": "Cancelled", "VERIFIED": "Verified", "RATE_MISSING": "Rate missing"}

# ------------------------------------------------------------------------- cover + contents
S.append(NextPageTemplate("body"))
S.append(Spacer(1, 5.4 * cm))
S.append(Paragraph("CTDMS", ParagraphStyle("t", fontName="SB", fontSize=44, leading=50, textColor=colors.white)))
S.append(Paragraph("Central Transport &amp; Dispatch Management System", ParagraphStyle("t2", fontName="S", fontSize=16, leading=22, textColor=colors.HexColor("#b9d3ee"))))
S.append(Spacer(1, 1.8 * cm))
S.append(Paragraph("Version 1.6 - Master User Manual", ParagraphStyle("t3", fontName="SB", fontSize=26, leading=32, textColor=colors.white)))
S.append(Paragraph("Demonstration Edition", ParagraphStyle("t3b", fontName="SB", fontSize=20, leading=26, textColor=colors.HexColor("#f5a524"))))
S.append(Spacer(1, .5 * cm))
S.append(Paragraph("One master workbook · Depots (several in one city) · Customer types and codes · Invoice Recognition Code · "
                   "Local autos per trip / per box · Keyboard-first operation", ParagraphStyle("t4", fontName="SI", fontSize=12, leading=16, textColor=colors.HexColor("#b9d3ee"))))
S.append(Spacer(1, 3.2 * cm))
S.append(Paragraph("Prepared for Tulsyan &nbsp;|&nbsp; Application version 1.6.0 &nbsp;|&nbsp; October 2026<br/>"
                   "<b>Every master record, invoice and gatepass in this document is a DEMONSTRATION ENTRY</b> (codes DM-…, names ending (Demo)), "
                   "created on a clean demonstration database that is separate from live data.",
                   ParagraphStyle("t5", fontName="S", fontSize=9.5, leading=14, textColor=colors.HexColor("#9fb4c9"))))
S.append(PageBreak())
toc = TableOfContents()
toc.levelStyles = [ParagraphStyle("t1", fontName="SB", fontSize=10.5, leading=17), ParagraphStyle("t2", fontName="S", fontSize=9, leading=13, leftIndent=16, textColor=colors.HexColor("#444"))]
P("Contents", ParagraphStyle("ct", parent=H1)); S.append(toc)

# ------------------------------------------------------------------------- 1 changes completed
h1("1. Version 1.6 - Changes Completed")
P("What management asked for after the version 1.5 review, and where it now is in the application.")
table(["#", "Request", "What was done", "Where in CTDMS"], [
    ["1, 11", "One-time master upload", "One workbook (Instructions + 11 sheets), validated row by row, preview with accepted / rejected counts, import of valid rows only, history and Excel error report; existing codes never duplicated; later additions one by one", "Master Settings › Initial Master Upload"],
    ["2", "CFA → Depot", "Every screen, print, report, export and role says Depot (Depot Master, Depot Manager, Depot-wise report); no business-type split", "everywhere"],
    ["3", "Several depots in one city", "Depot code (unique), name, locality, city, state, PIN, address, companies handled, source ERP, active", "Master Settings › Depots"],
    ["4", "Customer master and types", "Customer types are a master (not fixed in the program); customers have type, source ERP and its customer code, depot and company mapping", "Master Settings › Customers / Customer Types"],
    ["5-7", "Customer / type codes, recognition code, start + end of the number", "Short recognition codes on company, customer type, customer and depot. Code = prefix (company, type, customer, depot) + numeric part of the ERP number, pattern configurable. No invoice-range feature", "Master Settings › Invoice Recognition Code"],
    ["6", "ERP number not replaced", "The original ERP invoice number is stored and shown exactly as received; the code is a separate field", "register, invoice, slip, reports, API"],
    ["8", "Recognition logic", "Company, depot and customer type recognised from the masters, ERP mappings and existing invoice markings (Invoice Code Mappings); a missing code gives a clear reason", "imports, QR scan, typing, API"],
    ["9", "Coding as a provision", "Switch on / off, pattern changeable, dispatch never depends on it, regenerate codes after master changes", "Invoice Recognition Code page"],
    ["10", "No product / SKU work", "Item-wise exports still accepted, lines not stored or shown; SKU report hidden; can be switched on later", "System Settings › features.sku_lines (off)"],
    ["12-16", "Local autos per trip / per box", "Local Auto Transporters with allowed methods (per trip, per box, both), usual trip charge, rate per box; per-box rates by location / company / depot / date; one trip amount shared by boxes, never multiplied", "Master Settings › Local Auto Transporters / Box Rates; gatepass"],
    ["17", "Simple daily flow", "Invoice in → recognised → boxes → carrier (long distance unchanged, or local auto) → slip → print → gate-out → MIS", "Dispatch desk"],
    ["18", "Master Settings", "All masters in one menu; active / inactive instead of deleting", "Master Settings menu"],
    ["19", "Reports and search", "Filters for recognition code, depot, city, customer type, local-auto method; City-wise, Depot-wise, Local Auto Trips; ERP number and code in separate columns", "Reports, Invoice Register"],
    ["20-21", "Demo data and video", "New v1.6 demonstration workbook and invoices; the guide video re-recorded in the same style", "this manual, the video"],
    ["22", "Keyboard first, mouse optional (MARG style)", "Same function keys on every screen (F1 help, F2 new, F3 go to, F4 search, F6 save, F7 edit, F8 gatepass / print, F9 complete dispatch, F10 reports, Esc back); Enter moves field to field and then to the main button; choice boxes find a company, depot, customer, customer type, area or transporter by code or name; arrows + Enter in every list; return to the same row; clear yellow focus; key bar at the bottom of every screen; delete / void / cancel ask first with No pre-selected", "every screen - F1 shows the keys"],
    ["23", "Guided Training / onboarding", "New users practise their real workflow in their own practice copy (v1.6 demonstration data), every step checked by CTDMS; learning paths per role; compulsory ON/OFF by the Master; progress, reset, reassign; nothing reaches live data", "Help › Guided Training; Administration › Guided Training"],
], [1.2, 3.4, 8.2, 4.2])

# ------------------------------------------------------------------------- 2 summary
SUMMARY = ("<b>One clean start.</b> On the first day the master data from MARG, SAP, CBO and Intrect is uploaded once in one "
 "workbook. CTDMS checks every row, shows what is accepted and what is wrong, and creates only valid records. New companies, depots, "
 "customers or transporters are added later one by one. "
 "<b>Depots, not CFA.</b> A depot is the godown goods leave from; one city can have several, such as Guma and Kamal Vihar in Raipur. "
 "Reports work by city and by depot. "
 "<b>Codes that explain an invoice.</b> Company, customer type, customer and depot each have a short code. Together with the number "
 "part of the ERP invoice they form the Invoice Recognition Code, for example ABC-RET-C025-GUMA-001245: ABC Pharma, retailer, "
 "customer C025, Guma depot, invoice 001245. The original ERP number is never changed. The code is a provision: it can be "
 "switched off or changed, and dispatch never depends on it. "
 "<b>Local autos charged correctly.</b> A per-trip auto is owed one amount for the trip however many invoices it carries; the "
 "amount is only shared over the invoices for the MIS. A per-box auto is boxes times its rate. Long-distance transporters work as "
 "before. "
 "<b>Keyboard first.</b> Staff used to MARG work with codes, Enter, arrows and function keys; the mouse is optional. "
 "<b>Result:</b> one consistent master list, invoices recognised automatically, simple daily dispatch, and transportation "
 "expense that adds up exactly - prepared for using the same structure across CFA, distribution and super-stockist work later.")
words = len(re.sub(r"<[^>]+>", "", SUMMARY).split())
assert words < 300, words
h1("2. Summary for top management")
P(f"<i>How version 1.6 works, end to end ({words} words)</i>")
P(SUMMARY, ParagraphStyle("sum", parent=base, fontSize=11, leading=16.5))

# ------------------------------------------------------------------------- 3 read first
h1("3. Read this first - demonstration entries")
box("The demonstration started from a completely empty database. Only the administrator login was created on the command line. "
    "Everything else came from the demonstration master workbook or was entered through the screens, and the video shows it.", "demo")
table(["Kind", "How it is marked", "Example"], [
    ["Master codes", "start with <b>DM-</b>", "DM-ABC, DM-GUMA, DM-C025"],
    ["Names", "end with <b>(Demo)</b>", "ABC Pharma Ltd (Demo)"],
    ["Recognition codes", "short codes as management asked (they are what appears in the Invoice Recognition Code)", "ABC, GUMA, C025"],
    ["Files", "start with <b>DEMO_v1.6_</b>", "DEMO_v1.6_Master_Workbook.xlsx"],
    ["Screens and video", "orange banner 'DEMONSTRATION DATA - NOT REAL'", "every video frame"],
], [4, 8, 5])
P("The demonstration has its own database (<b>v1.6/demo_database/ctdms_v16_demo.db</b>, started with <b>start_demonstration_v16.bat</b> on port 8004). "
  "Live data is never mixed with it. To go live, start from an empty database and upload your own master workbook.")
table(["Delivered", "File"], [
    ["This manual", "v1.6/CTDMS_v1.6_Master_User_Manual_Demonstration_Edition.pdf"],
    ["Guide video (screen recording, captions, voice)" + (f", {VIDEO_LEN}" if VIDEO_LEN else ""), "v1.6/CTDMS_v1.6_Guide_Video.mp4"],
    ["Transcript with time stamps, subtitles", "v1.6/CTDMS_v1.6_Guide_Video_Transcript.txt, …_Subtitles.srt"],
    ["Guided Training video (first-time users, 5 min) + transcript", "v1.6/CTDMS_v1.6_Guided_Training.mp4, …_Transcript.txt"],
    ["Demonstration master workbook and invoice files", "v1.6/demo_files/"],
    ["Exports produced during the demonstration", "v1.6/exports/"],
    ["Demonstration database + starter", "v1.6/demo_database/, start_demonstration_v16.bat"],
], [8, 9])

# ------------------------------------------------------------------------- 4 checklist
h1("4. Checklist of master records created")
P("All of these came from the one demonstration workbook, except the checker user and the local auto added later in Master "
  "Settings (to show how later additions work).")
h2("4.1 Master upload result")
table(["Sheet", "Rows", "Imported", "Already in CTDMS", "Rejected / duplicate"],
      [[s, c.get("rows"), c.get("imported", c.get("new")), c.get("exists"), c.get("rejected")] for s, c in up_counts.items()],
      [5, 2.5, 2.5, 3.5, 3.5])
h2("4.2 Companies and depots")
table(["", "Company code", "Name", "Recognition code", "ERP", "Relationship"],
      [[TICK, r["code"], r["name"], mono(r["marking"] or ""), r["erp"] or "", r["rel"].title()] for r in companies], [.8, 2.4, 5.3, 2.8, 2.2, 3.5])
table(["", "Depot code", "Depot", "Locality", "City", "Recognition code", "Companies handled"],
      [[TICK, r["code"], r["name"], r["locality"] or "", r["city"] or "", mono(r["marking"] or ""), r["cos"] or ""] for r in depots],
      [.8, 2.2, 3.8, 2.4, 2, 2.6, 3.2])
P("<b>Raipur has two depots</b> (Guma and Kamal Vihar), each with its own code and recognition code.")
h2("4.3 Customer types and customers")
table(["", "Type key", "Name", "Recognition code"], [[TICK, r["code"], r["name"], mono(r["marking"] or "(none)")] for r in ctypes], [.8, 5, 6, 5.2])
table(["", "Code", "Customer", "Type", "Recognition code", "Location", "Source ERP code"],
      [[TICK, r["code"], r["name"], r["tname"] or r["party_type"], mono(r["marking"] or ""), r["loc"] or "",
        f"{r['erp'] or ''} {r['source_erp_code'] or ''}"] for r in customers], [.8, 1.9, 5, 2.5, 2.4, 2.2, 2.2])
h2("4.4 Locations, transporters, local autos")
table(["", "Code", "Location", "City"], [[TICK, r["code"], r["name"], r["city"] or ""] for r in locations], [.8, 4, 6, 6.2])
table(["", "Code", "Long-distance transporter", "Payment"], [[TICK, r["code"], r["name"], "To be billed" if r["payment_mode"] == "TBB" else "Cash"] for r in transporters], [.8, 3, 8, 5.2])
ML = {"PER_TRIP": "Per trip", "PER_CASE": "Per box", "BOTH": "Per trip or per box"}
table(["", "Code", "Local auto", "Charging", "Usual trip", "Rate / box", "Vehicle"],
      [[TICK, r["code"], r["name"], ML.get(r["allowed_methods"], r["allowed_methods"]), inr(r["default_trip_charge"]) if r["default_trip_charge"] else "-",
        inr(r["default_box_rate"]) if r["default_box_rate"] else "-", r["vehicle_no"] or ""] for r in autos], [.8, 1.8, 4.6, 3, 2.2, 2.2, 2.4])
table(["", "Rate code", "Local auto", "Location", "Company", "Depot", "Rate / box"],
      [[TICK, r["code"], r["auto"], r["loc"] or "all", r["co"] or "all", r["dp"] or "all", inr(r["rate_per_box"])] for r in auto_rates],
      [.8, 3.2, 4.2, 2.6, 2, 2, 2.2])
h2("4.5 Rates, invoice markings, ERP mappings, users")
table(["", "Transporter", "Location", "Rate / case", "Status"], [[TICK, r["tr"], r["loc"], inr(r["rate_per_case"]), r["status"].replace("_", " ").title()] for r in rates], [.8, 5, 4, 3, 4.2])
table(["", "Mapping", "ERP", "Marking in the ERP invoice number", "Company", "Depot"],
      [[TICK, r["code"], r["erp"] or "any", mono(r["marking"]), r["co"], r["dp"] or ""] for r in markings], [.8, 3, 2, 5, 3, 3.2])
P(f"ERP code mappings created: {len(erpmaps)} (SAP plants → depots, MARG and CBO customer codes → customers). "
  f"Users: {', '.join(u['username'] for u in users)}.")

# ------------------------------------------------------------------------- 5 samples
S.append(NextPageTemplate("land")); S.append(PageBreak())
P("5. All information given and used as samples", H1)
h2("5.1 Files")
table(["File (v1.6/demo_files)", "What it is", "What it demonstrates"], [
    ["DEMO_v1.6_Master_Workbook.xlsx", "The one-time master workbook (Instructions + all sheets)", "Validation, preview, rejected rows (depot without city, customer typed twice, unknown location), existing code skipped"],
    ["DEMO_v1.6_SAP_billing_ABC.xlsx", "ABC Pharma's SAP billing export (6 invoices)", "Depot from the SAP plant code, customer from SAP's customer number"],
    ["DEMO_v1.6_MARG_sales_XYZ.csv", "XYZ Healthcare's MARG sales register (4 invoices)", "Company AND depot recognised from the marking XY/G/ or XY/K/ in the bill number"],
    ["DEMO_v1.6_CBO_invoices_PQR.csv", "PQR Lifesciences' CBO export (3 invoices)", "Marking PQR/K/ or PQR/B/; CBO customer codes through ERP mappings"],
], [6.5, 8, 12])
h2("5.2 Every invoice as it stands at the end of the demonstration")
rows = []
for r in invoices:
    carrier = r["auto"] or r["tr"] or "-"
    rows.append([r["invoice_no"], mono(r["recognition_code"] or "(not generated)"), f"{r['erp']} · {r['coname']}", f"{r['depot']} · {r['city']}",
                 f"{r['cust']} · {r['ctype']}", r["loc"], f"{r['boxes']} · {inr(r['sales_value'])}", STATUS.get(r["status"], r["status"]),
                 carrier, inr(r["freight_amount"]) if r["freight_amount"] is not None else "-"])
table(["Original ERP no", "Invoice Recognition Code", "Source · company", "Depot · city", "Customer · type", "Location", "Boxes · value",
       "Status", "Carrier", "Freight (share)"], rows, [2.3, 3.6, 3.4, 3.2, 4.2, 1.9, 2.6, 2.0, 2.4, 1.6])
h2("5.3 Gatepasses")
srows = []
for s in slips:
    how = (f"Local auto {ML.get(s['auto_method'], '')}" if s["auto"] else "Long-distance transporter")
    owed = s["trip_charge"] if s["auto_method"] == "PER_TRIP" else s["total_charge"]
    srows.append([s["dispatch_no"], s["depot"], how, s["auto"] or s["tr"], s["invoice_count"], s["total_cases"],
                  inr(owed), inr(s["allocated"]), "Gone out" if s["gate_out_at"] else ("Printed" if s["print_count"] else "Not printed")])
table(["Gatepass", "Depot", "How", "Carrier", "Invoices", "Boxes", "Owed to carrier", "Booked on invoices", "State"], srows,
      [4.2, 3.6, 4.2, 4.2, 1.6, 1.4, 2.6, 2.6, 2])
P("For every local-auto trip the two money columns are equal: the trip amount is shared over the invoices for the MIS and never multiplied.")

# ------------------------------------------------------------------------- 6 plain language
S.append(NextPageTemplate("body")); S.append(PageBreak())
P("6. Every invoice and trip explained in plain language", H1)
SPECIAL = {
    "001245": "The example management asked for: original number <b>001245</b>, Invoice Recognition Code <b>ABC-RET-C025-GUMA-001245</b>. The number was not changed in any way.",
    "001250": "Its customer type, Government Supply, had no short code, so at first no code was made and the reason was shown. After the code GOV was entered in Master Settings the code appeared at once. It is the invoice still waiting for dispatch.",
    "XY/K/2604": "Was added to Ravi Tempo's per-box trip, but Ravi Tempo has no per-box rate for Durg, so CTDMS refused it. It then went with Shree Roadlines in the keyboard-only dispatch (Step 9 of the video), together with PQR/K/0312.",
    "PQR/K/0312": "Dispatched in the keyboard-only demonstration: picked from the waiting list with the Down arrow and Enter, transporter found by typing its code SRL, gatepass generated, printed and completed with Enter, F8 and F9.",
    "XY/K/2603": "15 boxes at Ravi Tempo's Raipur rate of ₹20 = ₹300 (the per-box example).",
    "PQR/K/0311": "6 boxes at ₹22 - Ravi Tempo's own rate for PQR's goods in Raipur, which wins over the general Raipur rate.",
    "XY/G/2602": "Kishore Mini Truck can be charged either way; today per trip, ₹550 for this one invoice.",
}
for r in invoices:
    parts = []
    how_found = (f"Company and depot recognised from the marking <b>{r['mk']}</b> in the ERP number." if r["mk"]
                 else "Company chosen for the SAP file; depot from the SAP plant code.")
    code_txt = (f"Recognition code <b>{r['recognition_code']}</b> = {r['co'].replace('DM-', '')} company · {r['ctype']} · customer {r['cucode']} · {r['depot']}."
                if r["recognition_code"] else f"No recognition code: {r['recognition_note']}")
    if r["auto"]:
        disp = (f"Carried by local auto {r['auto']} "
                + (f"(per trip ₹{float(r['trip_charge']):,.0f} for the whole trip; this invoice's share {inr(r['freight_amount'])})."
                   if r["auto_method"] == "PER_TRIP" else f"(per box; {inr(r['freight_amount'])})."))
    elif r["tr"]:
        disp = f"Sent with long-distance transporter {r['tr']} from the rate card ({inr(r['freight_amount'])}), bill later."
    else:
        disp = "Not dispatched yet."
    t = Table([[Paragraph(f"<b>{r['invoice_no']}</b> &nbsp;<font color='#555'>{r['coname']} → {r['cust']} ({r['ctype']}), {r['loc']} · {r['boxes']} boxes · {inr(r['sales_value'])}</font>", CELLB)],
               [Paragraph(f"<b>What it is:</b> {r['erp']} invoice billed from {r['depot']} ({r['city']}). {how_found}<br/>"
                          f"<b>Code:</b> {code_txt}<br/><b>Dispatch and cost:</b> {disp}"
                          + (f"<br/><b>Why it matters:</b> {SPECIAL[r['invoice_no']]}" if r['invoice_no'] in SPECIAL else ""), CELL)]],
              colWidths=[17 * cm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), LIGHT), ("BOX", (0, 0), (-1, -1), .4, colors.HexColor("#c4cfdb")),
                           ("LEFTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    S.append(KeepTogether([t, Spacer(1, 5)]))
h2("The local-auto trips")
for s in slips:
    if not s["auto"]:
        continue
    if s["auto_method"] == "PER_TRIP":
        txt = (f"{s['auto']} carried {s['invoice_count']} invoice(s), {s['total_cases']} boxes, for one trip charge of "
               f"<b>₹{float(s['trip_charge']):,.2f}</b>. The auto is owed exactly that. For the MIS it is shared by boxes; the shares add "
               f"up to {inr(s['allocated'])}." + (" Charging per invoice would have shown "
               f"₹{float(s['trip_charge']) * s['invoice_count']:,.0f} - that double counting cannot happen." if s["invoice_count"] > 1 else ""))
    else:
        txt = f"{s['auto']} charged per box: {s['total_cases']} boxes, each invoice at its own rate, total <b>{inr(s['total_charge'])}</b>."
    P(f"<b>{s['dispatch_no']}</b> - {txt}")

# ------------------------------------------------------------------------- 7 screens
h1("7. Step-by-step with the screens")
h2("Step 1. Initial Master Upload")
num(["Master Settings › Initial Master Upload › <b>Download the blank workbook</b>. Read the Instructions sheet.",
     "Fill the sheets with the cleaned data from all ERPs (codes unique, mandatory columns marked *).",
     "Upload › <b>Check workbook</b>. Read the per-sheet counts and every problem row.",
     "<b>Import N valid records</b>. Download the error report, correct the rejected rows, upload them again if needed.",
     "Ask the checker to approve the freight rates (Approvals › Approve all)."])
shot("upload", "Initial Master Upload: template, steps and history.")
shot("upload_detail", "Result of the demonstration workbook: per-sheet counts and every row with its result.", w=13)
h2("Step 2. The masters")
shot("companies", "Company Master with recognition codes.")
shot("depots", "Depot Master: Guma and Kamal Vihar in Raipur, Bilaspur.")
shot("depot_form", "A depot: locality, city, companies handled, recognition code.", w=13)
shot("customer_types", "Customer Types master with short codes.")
shot("customers", "Customer Master.")
shot("customer_form", "A customer: type, recognition code, source ERP and its code.", w=13)
shot("autos", "Local Auto Transporters: per trip, per box, both.")
shot("auto_rates", "Local Auto Box Rates by location / company / depot.")
shot("markings", "Invoice Code Mappings: existing ERP invoice markings.")
h2("Step 3. Adding a record later")
P("Master Settings › the master › <b>New</b>. Example: a new local auto. It appears in the gatepass dropdown at once. "
  "Records already used are made inactive, never deleted.")
shot("auto_new", "Adding a local auto.", w=12)
h2("Step 4. Invoice Recognition Code")
shot("recognition", "Settings, example, missing codes, regenerate.", w=13)
h2("Step 5. Invoices and search")
shot("register", "Invoice Register: original ERP number and Invoice Recognition Code side by side.", w=13)
shot("invoice_001245", "Invoice 001245 - ABC-RET-C025-GUMA-001245; the ERP number is unchanged.")
shot("search_prefix", "Search by the start of the code: ABC-RET = ABC Pharma invoices to retailers.")
h2("Step 6. Dispatch")
shot("gp_long", "Long-distance transporter: unchanged v1.5 workflow.", w=13)
shot("gp_trip", "Local auto per trip: four invoices, one trip charge of ₹500.", w=13)
shot("gp_box", "Local auto per box: boxes × the rate for each invoice.", w=13)
h2("Step 7. Keyboard-only dispatch (MARG style)")
P("Every daily screen can be worked without the mouse. A key always means the same thing; the dark bar at the bottom of the "
  "screen shows the keys that work on that screen, and <b>F1</b> lists all of them. F5 (refresh), F11 (full screen) and F12 are "
  "left to the browser.")
table(["Key", "Meaning (same on every screen)", "Key", "Moving around"], [
    ["F1", "Help - list of keys", "Enter", "Next field; after the last field the cursor goes to the main button, Enter again presses it"],
    ["F2", "New / Add (dispatch desk: new gatepass)", "Shift + Enter", "Previous field"],
    ["F3", "Go to any screen by typing its name (also Ctrl+K)", "Type", "In a choice box: the code (GUMA, C025, SRL) or part of the name; ↓ ↑ to pick, Enter to select"],
    ["F4", "Search box of the screen", "↓ ↑", "Move through a table; ↓ from a search box goes into its table"],
    ["F6", "Save (also Ctrl+S)", "Enter on a row", "Open it (gatepass screen: add the invoice)"],
    ["F7", "Edit", "Space", "Tick / untick"],
    ["F8", "Generate the gatepass / dispatch slip, or print it", "Esc", "Close a list or question; otherwise back one screen - to the same list and row"],
    ["F9", "Complete the dispatch (gate-out)", "/", "Find an invoice, LR or gatepass from any screen"],
    ["F10", "Reports", "", ""],
], [1.4, 6.2, 2.4, 7])
num(["<b>F2</b> - New gatepass. The cursor is in Depot: type <b>KVR</b>, Enter.",
     "Type or scan the invoice number, Enter. Or <b>↓</b> into the waiting list and Enter on the invoice.",
     "Enter on the empty scan box - Mode of dispatch (arrows change it), Enter - Transporter: type <b>SRL</b>, Enter. "
     "For a local auto: type its code, Enter; the trip charge and vehicle come from the master, Enter, Enter.",
     "Enter through date, time and remarks; the cursor lands on <b>Generate gatepass</b>; Enter.",
     "<b>F8</b> prints. <b>F9</b> completes the dispatch - CTDMS asks first, Enter = Yes.",
     "<b>Esc</b> back to the list; ↓ for the next record. Delete, void, cancel and reject always ask with <b>No</b> pre-selected."])
shot("kb_depot", "F2 - New gatepass: typing the depot code KVR finds Kamal Vihar Depot.", w=13)
shot("kb_transporter", "Transporter box: typing SRL finds Shree Roadlines; Enter selects and moves on.", w=13)
shot("kb_confirm", "F9 - complete the dispatch: a Yes / No question; for void / cancel the safe answer No is pre-selected.", w=13)
shot("kb_list", "Lists: ↓ ↑ move the yellow row, Enter opens it, Esc comes back to the same row.", w=13)
shot("kb_help", "F1 - all keys, and the keys of the current screen.", w=13)
box("Tested: the whole dispatch - depot, invoices, transporter or local auto, generate, print, gate-out, back to the list - was "
    "done with the keyboard only (58 automatic checks, tests/e2e_keyboard_dispatch.py).", "tip")
h2("Step 8. Reports")
shot("rep_trips", "Local Auto Trips: owed to the auto = booked on the invoices.")
shot("rep_city", "City-wise freight: all Raipur depots together.")
shot("rep_depot", "Depot-wise freight: each depot with its city.")
shot("rep_invoice", "Invoice-wise: original ERP number and recognition code in separate columns.", w=13)
shot("dashboard", "Dashboard.")

# ------------------------------------------------------------------------- 8 workbook reference
SPEC = json.loads((Path(__file__).parent / "workbook_spec_v16.json").read_text(encoding="utf-8"))
S.append(NextPageTemplate("land")); S.append(PageBreak())
P("8. The master workbook - every column", H1)
for sp in SPEC:
    h2(f"{sp['name']} - {sp['title']}")
    table(["Column", "Mandatory", "What it means", "Allowed values", "Example"],
          [[c["header"], "Yes" if c["required"] else "", c["help"], ", ".join(c["choices"]) or {"bool": "Yes / No", "date": "DD-MM-YYYY",
            "decimal": "number", "list": "codes, comma separated"}.get(c["kind"], ""), c["example"]] for c in sp["cols"]],
          [5, 2.2, 10.5, 5, 4])

# ------------------------------------------------------------------------- 9 problems + going live
S.append(NextPageTemplate("body")); S.append(PageBreak())
P("9. Problems you may meet, and what to do", H1)
table(["What you see", "Why", "What to do"], [
    ["Workbook row 'rejected - … is mandatory'", "A mandatory column is empty", "Fill it, upload the corrected rows again"],
    ["'… not found (not in this workbook and not in CTDMS)'", "A link points to a code that does not exist (e.g. delivery location)", "Add the missing record or correct the code"],
    ["'already exists in CTDMS - skipped'", "The code is already in the system", "Nothing - edit it in Master Settings if it changed"],
    ["'appears more than once in this sheet'", "Same code twice in the workbook", "Keep one row"],
    ["Invoice shows 'code not generated'", "A company / depot / customer / customer type has no recognition code", "Fill the code in Master Settings; codes are re-made automatically (or press Regenerate)"],
    ["Import 'Depot does not handle company …'", "The depot's 'Companies handled' does not include the company", "Tick the company on the depot"],
    ["Gatepass 'Enter the trip charge'", "Per-trip local auto without an amount", "Type the actual trip amount"],
    ["Gatepass 'no per-box rate for …'", "Per-box local auto has no rate for that location / company", "Add a Local Auto Box Rate, or use another auto"],
    ["'… is charged per trip only'", "The chosen method is not allowed for that auto", "Choose the allowed method or change the auto's settings"],
    ["Long-distance 'no rate'", "Rate card has no rate for the area (as in v1.5)", "Add the rate; the checker approves it"],
    ["A function key shows 'not used on this screen'", "That key has no job on this screen", "Look at the key bar at the bottom, or press F1"],
    ["Esc asks 'Leave this screen?'", "Something was typed and not saved", "N (or Enter) stays; Y leaves without saving"],
    ["'Generate gatepass' is grey when Enter reaches it", "Something is still missing", "The message says what (e.g. choose the transporter); Shift+Enter goes back"],
    ["A choice box says 'Nothing matches'", "The typed code or name is not in that master (or is inactive)", "Type less, check the code in Master Settings, or Esc to keep the old value"],
], [5, 6, 6])
h1("10. Before real use", new=False)
num(["Start from an empty database (init-db, create-admin) - do not reuse the demonstration database.",
     "Prepare the real master workbook from MARG, SAP, CBO, Intrect and other ERPs; upload, check the preview, import.",
     "Set the recognition codes you want to use, or switch the Invoice Recognition Code off for the pilot.",
     "Enter local autos with their charging method; add per-box rates where needed.",
     "Create the real users and roles; keep paid services locked unless the business decides otherwise.",
     "Back up the database and test a restore."])
P(f"Audit entries written during the demonstration: {audit_n}.")

# ------------------------------------------------------------------------- 11 guided training
h1("11. Guided Training - first-time users")
P("New staff learn their work by doing it on the real screens, with the v1.6 demonstration data, in their own private "
  "practice copy of CTDMS. Live data is never touched; only their training progress is saved.")
table(["", "How it works"], [
    ["Start", "Help › Guided Training / Practice Mode › Start (or Continue). A practice copy is prepared from the v1.6 "
              "demonstration (same workbook, invoices, transporters and amounts as the video) and the first screen opens."],
    ["Each step", "The panel on the right explains what the feature does, why it matters, what to do, which keys to press "
                  "and what to expect, and highlights the field. The trainee does it on the real screen."],
    ["Checked", "Practical steps pass only when CTDMS finds the real result in the practice copy (workbook imported, "
                "gatepass generated / printed / gone out, bill approved by a second person …). Clicking through is not enough."],
    ["Keys", "Ctrl+Enter = Next / Check · Ctrl+/ = into the panel (Tab, Enter, Esc back) · every CTDMS key works as usual."],
    ["Video", "Watch demonstration plays the matching chapter of the v1.6 guide video; Esc returns to the same step."],
    ["Safe", "Orange TRAINING MODE bar; prints and exports say TRAINING - NOT VALID FOR BUSINESS USE; no WhatsApp / SMS / "
             "e-mail / ERP sync; every trainee has a separate practice copy (no restart, several at once)."],
    ["Progress", "Saved after every step; logging out and in resumes at the same step; Replay lesson and Prepare practice "
                 "data reset only the trainee's own practice copy."],
], [3, 14])
h2("Learning paths (from the user's role)")
import importlib.util  # noqa: E402 - the lesson list itself (no app import: this venv has no web framework)
_spec = importlib.util.spec_from_file_location("ctdms_lessons", Path(__file__).parents[3] / "app" / "training" / "lessons.py")
_les = importlib.util.module_from_spec(_spec)
sys.modules["ctdms_lessons"] = _les
_spec.loader.exec_module(_les)
LESSONS, PATHS = _les.LESSONS, _les.PATHS
table(["Path", "Lessons"], [[p["name"], " · ".join(f"{n}. {LESSONS[c].title}" for n, c in enumerate(p["lessons"], 1))]
                             for p in PATHS.values()], [3.6, 13.4])
h2("For the Master - compulsory training")
bl(["Administration › Guided Training (or System Settings › Guided Training): switch compulsory training ON or OFF; "
    "every change is in the audit log.",
    "ON: users created from then on must finish their path before any live screen opens - the server enforces it.",
    "OFF: everyone works according to their permissions; training stays voluntary under Help. Completed training is never removed.",
    "Per user: path, required / voluntary, status, progress, start, last activity and completion dates; Assign another "
    "path, Require / Not required, Reset (progress and practice copy).",
    "The Master is never locked out of Administration; last resort: python -m app.cli training-release --username <name>."])
for n, cap in (("t02_training_home", "First login of a new operator: Guided Training opens; live screens stay closed."),
               ("t05_long_distance", "A practical step: the panel explains and checks; the trainee works with the keyboard."),
               ("t07_no_rate", "Lesson 9: the missing per-box rate for Durg, explained in the panel."),
               ("t09_master_dashboard", "Administration › Guided Training: compulsory switch, totals and every user's progress.")):
    shot(n, cap, w=15)

Doc(OUT).multiBuild(S)
print("pdf written", OUT)
