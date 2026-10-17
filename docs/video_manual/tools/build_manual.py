"""Builds the PDF: Master User Manual - Demonstration Edition (uses the finished demonstration database).
Run with the playwright venv python:  build_manual.py <shots_dir> <demo.db> <exports_dir> <out.pdf> <transcript_len>"""
import sqlite3
import sys
from datetime import date
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
import demo_data as D

SH, DB, EXP, OUT = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), sys.argv[4]
VIDEO_LEN = sys.argv[5] if len(sys.argv) > 5 else ""

pdfmetrics.registerFont(TTFont("S", "C:/Windows/Fonts/segoeui.ttf"))
pdfmetrics.registerFont(TTFont("SB", "C:/Windows/Fonts/segoeuib.ttf"))
pdfmetrics.registerFont(TTFont("SI", "C:/Windows/Fonts/segoeuii.ttf"))
pdfmetrics.registerFont(TTFont("SY", "C:/Windows/Fonts/seguisym.ttf"))
pdfmetrics.registerFontFamily("S", normal="S", bold="SB", italic="SI", boldItalic="SB")

NAVY = colors.HexColor("#1f4e79"); DARK = colors.HexColor("#0f2238"); LIGHT = colors.HexColor("#eef3f8")
AMBER = colors.HexColor("#fff4e0"); RED = colors.HexColor("#fdecea"); GREEN = colors.HexColor("#e8f5e9")
base = ParagraphStyle("b", fontName="S", fontSize=10, leading=14.5, spaceAfter=5)
H1 = ParagraphStyle("h1", parent=base, fontName="SB", fontSize=20, leading=24, textColor=NAVY, spaceBefore=4, spaceAfter=10, keepWithNext=1)
H2 = ParagraphStyle("h2", parent=base, fontName="SB", fontSize=13, leading=17, textColor=NAVY, spaceBefore=10, spaceAfter=4, keepWithNext=1)
H3 = ParagraphStyle("h3", parent=base, fontName="SB", fontSize=10.5, spaceBefore=6, spaceAfter=2, keepWithNext=1)
BUL = ParagraphStyle("bul", parent=base, leftIndent=14, bulletIndent=3, spaceAfter=2.5)
CAP = ParagraphStyle("cap", parent=base, fontName="SI", fontSize=8.5, leading=11, textColor=colors.HexColor("#555"), alignment=TA_CENTER, spaceAfter=8)
CELL = ParagraphStyle("c", parent=base, fontSize=8.3, leading=10.6, spaceAfter=0)
CELLB = ParagraphStyle("cb", parent=CELL, fontName="SB")
CELLW = ParagraphStyle("cw", parent=CELLB, textColor=colors.white)


class Doc(BaseDocTemplate):
    def __init__(self, fn):
        super().__init__(fn, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm, topMargin=2.0*cm, bottomMargin=1.9*cm,
                         title="CTDMS - Master User Manual (Demonstration Edition)", author="CTDMS")
        f = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="f")
        lw, lh = landscape(A4)
        fl = Frame(1.5*cm, 1.7*cm, lw - 3*cm, lh - 3.3*cm, id="fl")
        self.addPageTemplates([PageTemplate(id="cover", frames=[f], onPage=self.cover),
                               PageTemplate(id="body", frames=[f], onPage=self.page),
                               PageTemplate(id="land", frames=[fl], pagesize=landscape(A4), onPage=self.page_l)])

    def cover(self, c, d):
        c.saveState(); c.setFillColor(DARK); c.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#b45309")); c.rect(0, A4[1]*0.36, A4[0], 5, fill=1, stroke=0); c.restoreState()

    def _foot(self, c, d, w):
        c.saveState(); c.setFont("S", 8); c.setFillColor(colors.HexColor("#666"))
        c.drawString(2*cm, 1.0*cm, "CTDMS - Master User Manual - Demonstration Edition  |  all records shown are DEMONSTRATION ENTRIES")
        c.drawRightString(w - 2*cm, 1.0*cm, "Page %d" % (d.page - 1))
        c.setStrokeColor(colors.HexColor("#cfd8e3")); c.line(2*cm, 1.4*cm, w - 2*cm, 1.4*cm); c.restoreState()

    def page(self, c, d):
        self._foot(c, d, A4[0])

    def page_l(self, c, d):
        self._foot(c, d, landscape(A4)[0])

    def afterFlowable(self, fl):
        if isinstance(fl, Paragraph) and fl.style.name in ("h1", "h2"):
            key = "k%d" % (abs(hash(fl.getPlainText())) % 10**9)
            self.canv.bookmarkPage(key)
            self.notify("TOCEntry", (0 if fl.style.name == "h1" else 1, fl.getPlainText(), self.page - 1, key))


S = []
P = lambda t, st=base: S.append(Paragraph(t, st))
def h1(t, new=True):
    if new: S.append(PageBreak())
    P(t, H1)
def h2(t): P(t, H2)
def h3(t): P(t, H3)
def bl(items):
    for i in items: S.append(Paragraph(i, BUL, bulletText="•"))
def num(items):
    for n, i in enumerate(items, 1): S.append(Paragraph(i, BUL, bulletText="%d." % n))
def shot(name, cap, w=15.4):
    f = SH / f"{name}.png"
    if not f.exists():
        return
    im = PI.open(f); r = im.height / im.width
    h = w*cm*r
    if h > 21*cm:
        w = 21*cm/r/cm
    S.append(KeepTogether([Image(str(f), width=w*cm, height=w*cm*r), Paragraph(cap, CAP)]))
def box(txt, kind="tip", width=17):
    bg, label = {"tip": (GREEN, "TIP"), "warn": (AMBER, "CAUTION"), "prob": (RED, "PROBLEM"), "demo": (AMBER, "DEMONSTRATION")}[kind]
    t = Table([[Paragraph("<b>%s</b> &nbsp;%s" % (label, txt), CELL)]], colWidths=[width*cm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#b9c4d0")),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    S.append(t); S.append(Spacer(1, 7))
def table(head, rows, widths, style=CELL):
    data = [[Paragraph(h, CELLW) for h in head]] + [[Paragraph(str(c), style) for c in r] for r in rows]
    t = Table(data, colWidths=[w*cm for w in widths], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                           ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#c4cfdb")),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    S.append(t); S.append(Spacer(1, 8))
def inr(x):
    return "-" if x is None else f"₹{float(x):,.2f}".replace(".00", "")
TICK = '<font name="SY">☑</font>'

# ------------------------------------------------------------------------------------- database facts
db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
inv_rows = list(db.execute("""
 select i.invoice_no, i.invoice_date, c.code co, cu.name cust, l.name area, i.boxes, i.sales_value, i.status,
        i.freight_amount, i.extra_charge, t.name carrier, i.dispatch_no, i.lr_no, i.delivery_status, i.capture_mode,
        e.code erp, i.exception_code, i.dispatch_mode, i.cancel_reason, i.item_count,
        (select count(*) from invoice_items x where x.invoice_id=i.id) skus
 from invoices i join companies c on c.id=i.company_id join customers cu on cu.id=i.customer_id
 join locations l on l.id=i.location_id left join transporters t on t.id=i.transporter_id
 left join erp_systems e on e.id=i.source_erp_id order by i.invoice_no"""))
slip_rows = list(db.execute("""
 select dispatch_no, dispatch_mode, invoice_count, total_cases, total_charge, amount_paid, payment_status, is_void, print_count,
        gate_out_at, (select name from transporters t where t.id=s.transporter_id) carrier,
        (select name from outside_autos a where a.id=s.outside_auto_id) auto, void_reason
 from dispatch_slips s where id in (select min(id) from dispatch_slips group by dispatch_no) order by dispatch_no"""))
bill_rows = list(db.execute("""
 select b.bill_no, b.bill_date, b.bill_amount, b.calculated_amount, b.variance, b.status, b.payment_ref,
        (select name from transporters t where t.id=b.transporter_id) carrier,
        (select count(*) from transporter_bill_lines l where l.bill_id=b.id) lines,
        (select username from users u where u.id=b.created_by_id) maker, (select username from users u where u.id=b.approved_by_id) checker
 from transporter_bills b order by b.id"""))
bill_lines = {}
for r in db.execute("select b.bill_no, l.lr_no, l.billed_boxes, l.system_cases, l.billed_rate, l.card_rate, l.billed_amount, "
                    "l.calculated_amount, l.match_status, l.remarks from transporter_bill_lines l join transporter_bills b on b.id=l.bill_id order by l.id"):
    bill_lines.setdefault(r["bill_no"], []).append(r)
notif_n = db.execute("select count(*) from notifications").fetchone()[0]
audit_n = db.execute("select count(*) from audit_logs").fetchone()[0]
n_users = db.execute("select count(*) from users").fetchone()[0]
slip_by_no = {r["dispatch_no"]: r for r in slip_rows}
gp_label = {}
for i, r in enumerate(slip_rows, 1):
    gp_label[r["dispatch_no"]] = f"G{i}"

# ------------------------------------------------------------------------------------- cover
S.append(NextPageTemplate("body"))
S.append(Spacer(1, 5.6*cm))
S.append(Paragraph("CTDMS", ParagraphStyle("t", fontName="SB", fontSize=44, leading=50, textColor=colors.white)))
S.append(Paragraph("Central Transport &amp; Dispatch Management System", ParagraphStyle("t2", fontName="S", fontSize=16, leading=22, textColor=colors.HexColor("#b9d3ee"))))
S.append(Spacer(1, 1.8*cm))
S.append(Paragraph("Master (Administrator) User Manual", ParagraphStyle("t3", fontName="SB", fontSize=26, leading=32, textColor=colors.white)))
S.append(Paragraph("Demonstration Edition", ParagraphStyle("t3b", fontName="SB", fontSize=20, leading=26, textColor=colors.HexColor("#f5a524"))))
S.append(Spacer(1, .5*cm))
S.append(Paragraph("From a completely empty database to dispatch, delivery, bill checking, reports and control, with a "
                   "companion video and transcript", ParagraphStyle("t4", fontName="SI", fontSize=12, leading=16, textColor=colors.HexColor("#b9d3ee"))))
S.append(Spacer(1, 3.4*cm))
S.append(Paragraph("Prepared for Tulsyan CFA &nbsp;|&nbsp; Application version 1.5.1 &nbsp;|&nbsp; October 2026<br/>"
                   "<b>Every master record, invoice, gatepass and bill in this document is a DEMONSTRATION ENTRY.</b> "
                   "They are marked DM-, (Demo) or DEMO- and live in a separate demonstration database.",
                   ParagraphStyle("t5", fontName="S", fontSize=9.5, leading=14, textColor=colors.HexColor("#9fb4c9"))))
S.append(PageBreak())

toc = TableOfContents()
toc.levelStyles = [ParagraphStyle("t1", fontName="SB", fontSize=10.5, leading=17, leftIndent=0),
                   ParagraphStyle("t2", fontName="S", fontSize=9, leading=13, leftIndent=16, textColor=colors.HexColor("#444"))]
P("Contents", ParagraphStyle("ct", parent=H1)); S.append(toc)

# ------------------------------------------------------------------------------------- 1 read first
h1("1. Read this first")
box("Nothing in this manual is real business data. The demonstration was run on a brand-new, completely empty database, "
    "through the same screens the staff will use. No sample data was pre-loaded by a script: every master record and every "
    "transaction was typed or uploaded through the application, exactly as a first-time Master would do it, and the video shows it.", "demo")
h2("How demonstration entries are labelled")
table(["Kind of record", "How it is marked", "Example"], [
    ["Master records (companies, customers, transporters, areas, CFA, users, ERPs)", "Code starts with <b>DM-</b>; names of companies, customers, transporters and users end with <b>(Demo)</b> or start with <b>demo.</b>", "DM-C01 · Alpha Stockist (Demo)"],
    ["Invoices (every source)", "Invoice number starts with <b>DEMO-</b>; remarks say 'Demonstration'", "DEMO-SAP-1001"],
    ["Gatepasses", "Remarks say 'Demonstration gatepass …' and carry the DM-RPR CFA code", "DS/DM-RPR/2627/00001"],
    ["Transporter bills", "Bill numbers are made-up; the company is demonstration", "ART/2610/001"],
    ["Import files and sample photo", "File names start with <b>DEMO_</b>", "DEMO_MARG_sales_register.csv"],
    ["Screen and video", "A permanent orange banner reads 'DEMONSTRATION DATA - NOT REAL'", "top centre of every video frame"],
], [5.3, 7.5, 4.2])
h2("Why they can be removed without touching real work")
bl(["The demonstration lives in its own database file (<b>demo_database/ctdms_demo.db</b>), started on its own address (port 8002) with its own batch file. "
    "Your real database (<b>data/ctdms.db</b>) was not opened, read or changed.",
    "To go live, start the application normally against a fresh database: run <i>python -m app.cli init-db</i> and <i>create-admin</i>. Nothing from the demonstration comes with it.",
    "If somebody ever loads these records into a live database by mistake, every one is recognisable by the DM- / (Demo) / DEMO- marks. "
    "CTDMS never deletes records that have been used (it deactivates them, so history stays), so keep demonstration and live data apart."])
h2("What is delivered together with this manual")
table(["Item", "File"], [
    ["This manual", "CTDMS_Master_User_Manual_Demonstration_Edition.pdf"],
    ["Live working video (screen recording with voice and on-screen captions)" + (f", {VIDEO_LEN}" if VIDEO_LEN else ""), "CTDMS_Master_Walkthrough_Video.mp4"],
    ["Transcript of the video (with time stamps) and subtitle file", "CTDMS_Master_Walkthrough_Transcript.txt, CTDMS_Master_Walkthrough_Subtitles.srt"],
    ["Files that were imported or exported during the demonstration", "demo_files\\ and exports\\ folders"],
    ["The demonstration database and a one-click starter", "demo_database\\ctdms_demo.db, start_demonstration.bat"],
    ["Scripts that produced everything (for repeating the demonstration)", "tools\\"],
], [9.5, 7.5])

# ------------------------------------------------------------------------------------- 2 summary
SUMMARY = ("CTDMS is the single control room between your billing systems (SAP, MARG, CBO, Intrect) and the trucks on the road. "
 "<b>1. Invoices arrive</b> from each system by Excel or CSV file, by direct API push, by scanning the e-invoice QR code, or by typing. "
 "CTDMS checks every one for duplicates, unknown customers and missing details, and shows the problem instead of hiding it. "
 "<b>2. Freight is priced automatically</b> from each transporter's approved rate card: cases multiplied by the rate for the customer's area. "
 "A new rate never works until a second person approves it. "
 "<b>3. One gatepass</b> covers a whole pickup, even with several companies, customers and towns. It must be printed before goods leave; "
 "the guard scans it and confirms gate-out. "
 "<b>4. The transporter's receipt (LR) number</b> is recorded and customers can be alerted. "
 "<b>5. Delivery is proven</b>: delivered, short, damaged or refused, with the signed receipt photographed. "
 "<b>6. Transporter bills are audited</b>: every line is compared with our own gatepasses. Wrong rates, double billing, unknown LRs and "
 "goods already paid in cash are caught before a different person approves and pays. "
 "<b>7. Management sees</b> cost per case, per transporter and per area, on-time delivery and variance, and can export everything to "
 "Excel, CSV, PDF or the API. "
 "<b>8. Control</b>: every change is logged with who, when and why, and each person sees only what their role allows.")
import re as _re
words = len(_re.sub(r"<[^>]+>", "", SUMMARY).split())
assert words < 300, words
h1("2. Summary for top management")
P("<i>How the system works from start to finish (%d words)</i>" % words)
P(SUMMARY, ParagraphStyle("sum", parent=base, fontSize=11, leading=16.5))
box("What the owners gain: fewer overpaid freight bills, proof that goods reached the customer, a record of who authorised every change, "
    "and one set of figures in place of four billing systems.", "tip")

# ------------------------------------------------------------------------------------- 3 checklist
h1("3. Checklist of master records created")
P("Every record below was created by hand through the screens, in the order shown (each needs the one before it). "
  "Tick-marks mean the record exists in the demonstration database. <b>Nothing else was created.</b>")
ok = lambda: TICK
h2("3.1 Source systems (Administration › Integration Settings)")
table(["", "Code", "Name", "How data arrives", "Status shown"], [
    [ok(), e[0], e[1], {"file": "Excel / CSV upload", "marg": "MARG adapter (file meanwhile)", "rest": "REST API push"}[e[2]],
     {"DM-SAP": "Working (file)", "DM-MARG": "File only; database link not configured", "DM-CBO": "API key issued", "DM-INTRECT": "Inactive (planned)"}[e[0]]] for e in D.ERPS],
    [0.8, 2.3, 6, 4.2, 3.7])
h2("3.2 CFA (warehouse) and delivery areas")
table(["", "Code", "Name", "Detail"], [[ok(), D.CFA["code"], D.CFA["name"], "Raipur, linked to DM-SAP as default source"]] +
      [[ok(), l[0], l[1], f"route {l[3]}, {l[5]} km" if l[5] else "route Local"] for l in D.LOCATIONS], [0.8, 3.2, 5, 8])
h2("3.3 Companies (principals)")
table(["", "Code", "Name", "Source ERP", "GSTIN (demonstration)"], [[ok(), c[0], c[1], c[2], c[3]] for c in D.COMPANIES], [0.8, 2.3, 6.2, 3.2, 4.5])
h2("3.4 Customers")
table(["", "Code", "Name", "Type", "Area", "ERP codes recorded"], [
    [ok(), c[0], c[1], c[2].title(), c[3], ", ".join(f"{e}: {code}" for e, code in D.ERP_CODES.get(c[0], [])) or "-"] for c in D.CUSTOMERS],
    [0.8, 1.8, 5.2, 2, 2, 5.2])
h2("3.5 Transporters and outside auto")
table(["", "Code", "Name", "Payment", "Special"], [
    [ok(), t[0], t[1], "Cash at dispatch" if t[2] == "CASH" else "To be billed (bill later)", "Direct pickup ₹200 per trip" if t[6] else "-"] for t in D.TRANSPORTERS] +
    [[ok(), D.AUTO[0], D.AUTO[1], "Cash", "₹150 per trip"]], [0.8, 2.2, 6, 4.5, 3.5])
h2("3.6 Transporter rate cards (₹ per case, effective 01-10-2026, each approved by the checker)")
areas = [l[1] for l in D.LOCATIONS]
table(["Transporter"] + areas, [[next(t[1] for t in D.TRANSPORTERS if t[0] == k)] + [str(v.get(a, "no rate")) for a in areas] for k, v in D.RATES.items()],
      [5.2, 2.4, 2.4, 2.4, 2.4, 2.4])
P("Gamma Express serves only Korba and Bilaspur, and Epsilon Transport does not serve Durg or Korba. This is deliberate: it shows the 'no rate' warning at the gatepass.")
h2("3.7 Users (Administration › Users)")
table(["", "Username", "Name", "Role", "Why it is needed"], [
    [ok(), "admin", "Master Administrator", "Administrator", "The Master: created from the command line, as the first user always is"]] +
    [[ok(), u[0], u[1], u[2], {"Checker / Approver": "Approves rates and bills (two-person rule)", "Dispatch Operator": "Warehouse desk; sees only Raipur CFA", "Accounts": "Types and pays transporter bills"}[u[2]]] for u in D.USERS],
    [0.8, 2.8, 4, 3.2, 6.2])
h2("3.8 Settings changed by the Master")
table(["", "Setting", "Value", "Reason"], [
    [ok(), "org.name / org.address", "Tulsyan CFA (Demonstration) / Raipur …", "Printed on gatepasses and reports"],
    [ok(), "notify.provider", "LOG (preview)", "Customer alerts are prepared and shown, nothing is sent; WhatsApp / SMS are paid and locked"],
    ["", "All other settings", "left at their default values", "Defaults are safe: approvals ON, printed gatepass required"]], [0.8, 4, 5.2, 7])

# ------------------------------------------------------------------------------------- 4 data table
S.append(NextPageTemplate("land")); S.append(PageBreak())
P("4. All information given and used as samples", H1)
P("Everything below was given to the system during the demonstration. Dates are 14 to 17 October 2026.")
h2("4.1 Files that were uploaded")
table(["File (folder demo_files)", "Source system", "Format", "Content", "What it demonstrates"], [
    ["DEMO_SAP_billing_documents_itemwise.xlsx", "SAP, Company A", "Excel, one row per medicine", "5 invoices, 13 medicine lines, 6 SKUs", "Column auto-detection; SKU detail; customer found by central code"],
    ["DEMO_MARG_sales_register.csv", "MARG, Company B", "CSV, one row per invoice", "7 rows: 6 invoices + 1 duplicate", "Unknown party code mapped once; blank case count becomes an exception; duplicate refused; layout saved as a template"],
    ["DEMO_CBO_invoice_export.csv", "CBO, Company C", "CSV", "3 invoices", "Customer recognised by GST number when the code is unknown"],
    ["(typed on screen)", "Manual entry", "Add Invoice form", "2 invoices (one cancelled)", "Hand entry; cancel with a reason"],
    ["DEMO_einvoice_QR_for_scanning.txt", "e-invoice QR", "QR text", "1 invoice (4 cases counted at the desk)", "Invoice created from a QR scan at the dispatch desk"],
    ["(API call, key issued to CBO)", "CBO API push", "JSON", "1 invoice DEMO-CBO-3101", "Another system writing into CTDMS with no file"],
    ["DEMO_rate_card_DM-T1 … DM-T4.csv", "Transporters", "CSV (Area, Rate, Effective from)", "4 rate lists, 20 rates", "Bulk rate upload with approval"],
    ["(typed on screen)", "Epsilon Transport", "Rate form", "3 rates", "Typing rates one by one"],
    ["DEMO_transporter_bill_Beta_Cargo.csv", "Beta Cargo Movers", "CSV statement", "5 bill lines with 3 deliberate errors", "Rate too high, same LR billed twice, LR not in our records"],
    ["DEMO_signed_LR_photo.png", "Customer receipt", "Image", "Sample photo of a signed LR", "Proof of delivery attachment"],
], [6.2, 3, 3.6, 5.2, 8.5])
h2("4.2 Invoices as they stand at the end of the demonstration")
status_txt = {"PENDING": "Waiting", "VERIFIED": "Verified", "SLIP_GENERATED": "Gatepass made, not printed", "SLIP_PRINTED": "Gatepass printed",
              "DISPATCHED": "Gone out", "CANCELLED": "Cancelled", "EXCEPTION": "Exception", "RATE_MISSING": "Rate missing"}
dstat = {"DELIVERED": "Delivered", "SHORT": "Delivered short", "DAMAGED": "Damaged", "REFUSED": "Refused", None: "-"}
rows = []
for r in inv_rows:
    src = r["erp"] or {"MANUAL": "Typed", "QR_SCAN": "QR scan", "API": "API"}.get(r["capture_mode"], r["capture_mode"])
    gp = gp_label.get(r["dispatch_no"], "-")
    rows.append([r["invoice_no"], f"{src} · {r['co'].replace('DM-', '')}", f"{r['cust']} · {r['area']}", f"{r['boxes'] if r['boxes'] is not None else '-'} · {inr(r['sales_value'])}",
                 status_txt.get(r["status"], r["status"]) + (f" ({r['exception_code']})" if r["exception_code"] else ""),
                 f"{gp} · {r['carrier'] or ('Outside auto' if r['dispatch_mode'] == 'OUTSIDE_AUTO' else '-')} · LR {r['lr_no'] or '-'}", inr(r["freight_amount"]) if r["freight_amount"] else "-",
                 dstat.get(r["delivery_status"], r["delivery_status"] or "-")])
table(["Invoice", "Source · company", "Customer · area", "Cases · value", "Status now", "Gatepass · carrier · LR", "Freight", "Delivery"], rows,
      [3.1, 3.0, 5.6, 2.6, 3.4, 5.0, 1.7, 2.3])
h2("4.3 Gatepasses")
label_story = {
 "G1": "Mixed: 2 companies (A and B), 3 customers, 2 towns (Raipur, Bhilai), 5 invoices from SAP, MARG, QR scan and hand entry. Carrier: Alpha Roadlines, to be billed.",
 "G2": "Mixed: 3 companies (A, B and C), 3 customers, 2 towns (Bilaspur, Durg), 4 invoices. Carrier: Beta Cargo Movers, to be billed.",
 "G3": "2 companies (A and C), 1 hospital in Korba. Carrier: Gamma Express, CASH, paid at the gate.",
 "G4": "Local delivery in Raipur: our own hired auto, ₹150 per trip, 2 invoices of 2 companies to the same customer.",
 "G5": "Direct pickup by Delta Logistics (₹200 extra per trip). Printed, goods not yet gone out.",
 "G6": "Voided: made on the wrong carrier (Delta) and cancelled with a reason before gate-out.",
 "G7": "Replacement for the voided one: Epsilon Transport, generated but not printed yet."}
srows = []
for r in slip_rows:
    g = gp_label[r["dispatch_no"]]
    state = "Void" if r["is_void"] else ("Gone out" if r["gate_out_at"] else ("Printed" if r["print_count"] else "Not printed"))
    srows.append([g, r["dispatch_no"], r["carrier"] or r["auto"] or "-", r["dispatch_mode"], f"{r['invoice_count']} inv · {r['total_cases']} cases", inr(r["total_charge"]), inr(r["amount_paid"]), state, label_story.get(g, "")])
table(["Ref", "Gatepass number", "Carrier", "Mode", "Load", "Freight charged", "Paid at gate", "State", "What it shows"], srows, [1.0, 3.9, 4.0, 2.4, 2.8, 2.0, 1.8, 2.0, 7.0])
h2("4.4 Transporter bills")
brows = []
for b in bill_rows:
    brows.append([b["bill_no"], b["carrier"], b["lines"], inr(b["bill_amount"]), inr(b["calculated_amount"]), inr(b["variance"]), b["status"].replace("_", " ").title(),
                  (f"Typed by {b['maker']}; approved by {b['checker']}; UTR {b['payment_ref']}" if b["payment_ref"] else f"Entered by {b['maker']}; blocked, not approvable")])
table(["Bill", "Transporter", "Lines", "Billed", "Our freight", "Variance", "Status", "History"], brows, [3.0, 4.2, 1.2, 2.2, 2.2, 2.2, 2.4, 9])
h2("4.5 Other information recorded")
table(["Item", "Content"], [
    ["Customer alerts (preview mode)", f"{notif_n} messages prepared: 'dispatched' and 'LR number' for each customer of each gatepass; marked sent, nothing transmitted"],
    ["Proof of delivery", "Alpha Roadlines' consignments delivered 17-10-2026; Gamma Pharma delivered short (22 of 24 cases) with a note and a photo; the others still pending"],
    ["Override", "DEMO-MRG-2006: case count entered as 7 by the Master with a reason (audited); invoice stays pending"],
    ["Cancellation", "DEMO-MAN-4002 cancelled with the reason 'Typed by mistake'; stays in the register"],
    ["Audit trail", f"{audit_n} audit entries written automatically during the demonstration"],
    ["E-mailed report", "Weekly Excel gatepass register for management@example.com (needs the mail server to send)"],
    ["Users", f"{n_users} users: admin, demo.checker, demo.operator, demo.accounts; the demonstration password is in the appendix"]], [5, 21.5])

# ------------------------------------------------------------------------------------- 5 plain language
S.append(NextPageTemplate("body")); S.append(PageBreak())
P("5. Every invoice and bill explained in plain language", H1)
P("For each demonstration entry: what it represents, and how it touches freight cost, dispatch, the customer, delivery, the transporter's bill and management reports.")
STORY = {
 "DEMO-SAP-1001": ("A normal order from Company A's SAP to a Raipur stockist, 12 cases of three medicines.",
                   "It shared a truck (gatepass 1) with MARG and QR invoices going to the same stockist. Freight is 12 cases at ₹6, billed by Alpha Roadlines, who were paid after the checker approved their bill."),
 "DEMO-SAP-1002": ("An order from Company A to a Bilaspur stockist.", "Carried by Beta Cargo Movers. Its LR number appears on Beta's bill, where Beta charged ₹24 a case instead of the agreed ₹20. CTDMS flags the difference."),
 "DEMO-SAP-1003": ("A large order from Company A to a Durg stockist, 14 cases.", "One LR covers it together with Company C's invoice for the same customer (24 cases). The customer received only 22, so the delivery is recorded as short. Beta's bill charges this LR twice; CTDMS blocks it."),
 "DEMO-SAP-1004": ("A hospital order, 18 cases, the biggest value in the demonstration.", "Sent to Korba with cash transporter Gamma Express; freight is paid at the gate, so no transporter bill is expected. Delivery proof is still awaited."),
 "DEMO-SAP-1005": ("A small order to a Raipur retailer, 3 cases.", "Delivered locally by our own hired auto together with MARG invoice 2003. The auto charge of ₹150 is for the trip, not per case, and is paid in cash."),
 "DEMO-MRG-2001": ("A MARG invoice from Company B to the same Raipur stockist as SAP-1001.", "MARG calls the customer ALP001; the code was recorded once on the customer screen, so the invoice matched without help. Same truck, same bill as SAP-1001."),
 "DEMO-MRG-2002": ("A MARG invoice to a Bhilai retailer, 5 cases.", "Bhilai freight is ₹15 a case, higher than Raipur, because of distance. On gatepass 1."),
 "DEMO-MRG-2003": ("A MARG invoice that appeared twice in the file.", "The second copy was refused as a duplicate, so freight and sales cannot be counted twice. The first went by the local auto."),
 "DEMO-MRG-2004": ("A MARG invoice for a Bilaspur stockist, 8 cases.", "First put on the wrong carrier, which was voided before the goods left, then on Epsilon Transport. The gatepass is generated but not printed, so the goods cannot leave yet."),
 "DEMO-MRG-2005": ("A MARG invoice whose party code TH-08 CTDMS did not know.", "The system suggested the right customer; the Master mapped the code once, and every future file will be recognised. Delivered by Beta Cargo (LR BCM-7003)."),
 "DEMO-MRG-2006": ("A MARG invoice that arrived without a case count, so it was held as an Exception.", "Without cases, freight cannot be calculated, so CTDMS refuses to dispatch it. The Master entered 7 cases with a reason (recorded). It now waits for a gatepass."),
 "DEMO-CBO-3001": ("Company C's invoice to the Durg stockist.", "Company C's code CB-GAMMA was unknown, but the GST number identified the customer. It shares one LR with SAP-1003 and was delivered short."),
 "DEMO-CBO-3002": ("Company C's invoice to the Korba hospital, 16 cases.", "On the same cash gatepass as SAP-1004, so the hospital receives both companies' goods in one consignment."),
 "DEMO-CBO-3003": ("Company C's invoice to a Durg retailer, 4 cases.", "Delta Logistics collects it from our warehouse, so a direct pickup charge of ₹200 is added. The gatepass is printed but the goods have not left, so the invoice is still not dispatched."),
 "DEMO-CBO-3101": ("An invoice pushed by Company C's system through the API.", "No file and no typing. It simply arrived, was validated, and is waiting for dispatch."),
 "DEMO-MAN-4001": ("An invoice typed by hand for a Bhilai retailer.", "Useful when no export exists. It went out on gatepass 1 like any other."),
 "DEMO-MAN-4002": ("An invoice typed by mistake.", "Cancelled with a written reason. It stays in the register, marked cancelled, so there is a complete history, and it is excluded from totals."),
 "DEMO-QR-5001": ("An invoice created at the warehouse desk by scanning the e-invoice QR code printed on the paper invoice.", "The operator only counted the cases (4). The QR gave company, customer, number, date and value. Gatepass 1."),
}
for r in inv_rows:
    ex = STORY.get(r["invoice_no"])
    if not ex:
        continue
    gp = gp_label.get(r["dispatch_no"], "no gatepass yet")
    facts = (f"<b>Now:</b> {status_txt.get(r['status'], r['status'])}; gatepass {gp}; freight {inr(r['freight_amount']) if r['freight_amount'] else 'not yet calculated'}; "
             f"delivery {dstat.get(r['delivery_status'], '-').lower()}.")
    t = Table([[Paragraph(f"<b>{r['invoice_no']}</b> &nbsp;<font color='#555'>{r['cust']} · {r['area']} · {r['boxes'] if r['boxes'] is not None else 'no'} cases · {inr(r['sales_value'])}</font>", CELLB)],
               [Paragraph(f"<b>What it represents:</b> {ex[0]}<br/><b>How it affects the business:</b> {ex[1]}<br/>{facts}", CELL)]], colWidths=[17*cm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), LIGHT), ("BOX", (0, 0), (-1, -1), .4, colors.HexColor("#c4cfdb")),
                           ("LEFTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    S.append(KeepTogether([t, Spacer(1, 5)]))
h2("The two transporter bills")
bstory = {
 "ART/2610/001": ("Alpha Roadlines' bill for gatepass 1: three lines, ₹297 (33 cases).",
                  "Every line matched our gatepass: same LR, same cases, same rate as the rate card. The variance is zero, so the checker could approve it and accounts paid it with the bank reference (UTR). "
                  "Effect: freight cost is confirmed and the bill is closed; the consignments' timelines show 'bill checked' and 'freight paid'."),
 "BCM/2610/007": ("Beta Cargo Movers' bill for gatepass 2: the transporter says ₹1,132; our own calculation is ₹544.",
                  "It has three deliberate errors: a rate of ₹24 instead of ₹20, one LR billed twice, and an LR that does not exist in our records. CTDMS flags five lines, shows the variance, and will not let the bill be approved until the lines are corrected or removed. "
                  "Effect: the business avoids paying about ₹588 more than it owes."),
}
for b in bill_rows:
    st = bstory.get(b["bill_no"])
    if st:
        t = Table([[Paragraph(f"<b>Bill {b['bill_no']}</b> · {b['carrier']} · billed {inr(b['bill_amount'])} · our freight {inr(b['calculated_amount'])} · status {b['status'].replace('_', ' ').title()}", CELLB)],
                   [Paragraph(f"<b>What it represents:</b> {st[0]}<br/><b>How it affects the business:</b> {st[1]}", CELL)]], colWidths=[17*cm])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), AMBER), ("BOX", (0, 0), (-1, -1), .4, colors.HexColor("#c4cfdb")),
                               ("LEFTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        S.append(KeepTogether([t, Spacer(1, 5)]))
    lines = bill_lines.get(b["bill_no"], [])
    if lines:
        table(["LR", "Cases billed / ours", "Rate billed / card", "Amount billed", "Result"],
              [[l["lr_no"], f"{l['billed_boxes']} / {l['system_cases'] if l['system_cases'] is not None else '-'}", f"{l['billed_rate'] if l['billed_rate'] is not None else '-'} / {l['card_rate'] if l['card_rate'] is not None else '-'}",
                inr(l["billed_amount"]), (l["match_status"] or "").replace("_", " ").title()] for l in lines], [3, 3.5, 3.5, 3, 4])

# ------------------------------------------------------------------------------------- 6 walkthrough
h1("6. Step-by-step manual with screens")
P("Each step follows the video. The screens are taken from the finished demonstration database.")
h2("Step 1. Sign in, change your password, read the dashboard")
P("The Master signs in with the user name and password created on the command line (<i>create-admin</i>), and changes the password. "
  "Five wrong attempts lock an account for 15 minutes; the Master can unlock it from Users.")
shot("dashboard", "Dashboard after the demonstration: 17 invoices, 13 dispatched, freight and variance.")
h2("Step 2. Add the source systems (ERPs)")
P("Administration › Integration Settings › Add ERP: a code, a name and an adapter. Then open each one and write its status in the notes. "
  "For CBO, <i>Generate API key</i> (shown once). For MARG, <i>Test connection</i> shows what is still missing. Untick Active to park a system.")
shot("integrations", "Four systems, four statuses.")
shot("integration_cbo", "CBO: API key hint, push address, field mapping and sync history.")
h2("Step 3. Master records, in order")
bl(["<b>CFA</b> (the warehouse) with its default ERP; <b>Areas</b>; <b>Companies</b> (linked to their ERP and the CFA); <b>Transporters</b> (cash or to-be-billed); <b>Outside auto</b>; <b>Customers</b> (area, mobile, GST number, companies served)."])
shot("companies", "Companies, each tied to its billing system.")
shot("customers", "Customers: stockists, retailers and a hospital.")
shot("transporters", "Transporters with payment mode.")
shot("customer_detail", "On a customer, record what each ERP calls it ('Add ERP code'); duplicates can be merged.")
h2("Step 4. Users, roles, settings")
shot("users", "Users with role and last login.")
shot("roles", "Roles and permissions grid.")
shot("settings", "System settings: organisation name, alerts, dispatch rules, approvals, reconciliation tolerances.")
h2("Step 5. Rate cards and the two-person rule")
P("Open a transporter in Masters › Transporter Rate Cards; upload a CSV (Area, Rate, Effective from) or type a rate and an effective date. Every rate goes to Approvals; "
  "a different person (the checker) presses Approve. Only approved rates price gatepasses.")
shot("rate_card_t1", "Alpha Roadlines' approved rate card.")
shot("approvals", "Approvals queue (empty once everything has been approved).")
h2("Step 6. Bring in the invoices")
shot("imports", "Invoice Import: one-click, step-by-step, drop folder and the import log.")
shot("import_marg", "MARG file with three problems explained row by row: unknown party code (with a suggested match), no case count, duplicate.")
bl(["Choose the ERP, company and CFA, press <b>Upload &amp; preview</b>; CTDMS reads the column headings.",
    "Fix what is flagged: <i>Map code</i>, <i>Correct row</i>, <i>Skip</i>. Name the layout so next time it is recognised automatically.",
    "Press <b>Import N valid rows</b>. Nothing is stored before you press it."])
shot("invoices_full", "Invoice Register: all sources, all statuses, in one list.", w=13)
shot("invoice_detail", "One invoice in full: freight calculation, dispatch, SKU lines, overrides, prints, reconciliation and audit trail.", w=13)
h2("Step 7. The gatepass: scan, group, print, gate-out, LR")
shot("gatepass_new", "Scan & Gatepass: scan the QR or press Add on waiting invoices; choose the carrier; see the charges.")
shot("gatepass_g1", "Gatepass 1 after gate-out: five invoices, three customers, two towns, LR numbers entered.")
shot("gate", "Gate Check: the guard types or scans the gatepass number.")
shot("gatepass_g2", "Gatepass 2: three companies in one consignment.")
shot("gatepasses", "The gatepass register: gone out, printed, not printed, void.")
shot("lr_pending", "LR Pending: consignments still waiting for the transporter's LR number.")
h2("Step 8. Delivery, proof and tracking")
shot("deliveries", "Deliveries Pending (tick, choose the date, mark delivered).")
shot("track_result", "Track Shipment: the full timeline of one consignment, with proof of delivery recorded as short.", w=13)
shot("alerts", "Customer Alerts in preview mode.")
h2("Step 9. Transporter bills")
shot("recon", "Transporter bills and their status.")
shot("bill1", "Bill 1: all lines matched, variance zero, approved by the checker and paid.", w=13)
shot("bill2", "Bill 2: five flagged lines; it cannot be approved.", w=13)
h2("Step 10. Reports and exports")
shot("reports", "The full list of reports.")
shot("rep_transporter", "Transporter-wise: trips, cases, cost per case, cost per trip; export buttons at the top.")
shot("rep_delivery", "Delivery performance by transporter and area.")
shot("mis", "Transportation MIS.")
shot("subs", "E-mailed reports.")
h2("Step 11. Control: audit")
shot("audit", "Audit log: who changed what and when.")

# ------------------------------------------------------------------------------------- 7 formats
S.append(NextPageTemplate("land")); S.append(PageBreak())
P("7. All the formats in which data can be taken out and used", H1)
P("Each format below was produced during the demonstration. The sample files are in the <b>exports</b> folder.")
files = sorted(p.name for p in EXP.iterdir()) if EXP.exists() else []
def f(name_part):
    return ", ".join(n for n in files if name_part in n) or "-"
table(["Format", "How to get it", "Good for", "Demonstration file(s)"], [
    ["Printed gatepass (A5 / A4)", "Gatepass page › Print gatepass", "Paper copy carried with the goods, barcode for the gate", "(printed screen in the video)"],
    ["PDF gatepass", "Gatepass page › PDF", "E-mail to the transporter, archive", f("Gatepass_G1_print")],
    ["Case labels (100 × 50 mm or A4)", "Gatepass page › Case labels", "One label per case, with barcode", f("case_labels")],
    ["Excel report (.xlsx)", "Any report › Excel", "Management analysis, pivot tables", f("Report_Gatepass") + ", " + f("SKU") + ", " + f("Delivery_Register")],
    ["CSV report (.csv)", "Any report › CSV", "Import into other software", f(".csv")],
    ["PDF report (.pdf)", "Any report › PDF; Audit log › PDF", "Printing, board papers", f("Report_Audit_Log.pdf") + ", " + f("Transporter-wise.pdf")],
    ["Irregularity report for a bill", "Bill page › Irregularity report / Excel", "Discussion with the transporter", f("Irregularities")],
    ["E-mailed reports", "Reports › E-mailed Reports (daily / weekly / monthly)", "Management receives reports automatically", "(subscription saved; needs mail server)"],
    ["Full data export (.zip of CSVs)", "Administration › Users › Full data export", "A complete copy of the data for review or migration", f("Full_data_export")],
    ["REST API (JSON)", "/api/v1/… with a token; documentation at /api/docs", "Other software reads invoices, tracking, reports, dashboard", f("API_")],
    ["API write-in (ERP push)", "POST /api/v1/integrations/{ERP}/invoices with the ERP's API key", "ERP sends invoices without files", f("API_push")],
    ["Drop-folder import", "ERP saves its export into data/inbox; read every 5 minutes", "Automatic daily file import", "(described in the user guide; not run in this demonstration)"],
    ["Customer tracking link", "Alerts contain a signed link when org.public_url is set", "Customers see the timeline without prices", "(needs public address and paid alerts)"],
    ["Screen (live)", "Dashboard, MIS drill-down, Track Shipment", "Day-to-day viewing", "the video"],
], [4.7, 6.8, 6.4, 8.6])

# ------------------------------------------------------------------------------------- 8 problems
S.append(NextPageTemplate("body")); S.append(PageBreak())
P("8. Problems that appeared (on purpose) and what to do", H1)
P("The demonstration contains these real-life problems. Each one is visible in the video.")
table(["Problem shown", "What CTDMS did", "What the person does"], [
    ["MARG party code 'TH-08' unknown", "Rejected the row, suggested the closest customer", "Press 'Map code'; never needed again"],
    ["MARG invoice without a case count", "Imported as an Exception; cannot be dispatched", "Enter the cases (override, audited); other users need approval"],
    ["Invoice twice in one file", "Marked Duplicate, not stored twice", "Skip it"],
    ["Customer code unknown in CBO file", "Matched through the GST number", "Nothing"],
    ["Transporter has no rate for the area", "Shows 'no rate' beside the invoice at the gatepass", "Choose another transporter, or add the rate and get it approved"],
    ["Gatepass made on the wrong transporter", "Void with a reason; invoices return to the waiting list", "Make a new gatepass"],
    ["Gatepass not printed", "Gate-out is refused", "Print, then confirm gate-out"],
    ["Short delivery", "Recorded on the consignment with note and photo", "Follow up with the transporter; appears in Delivery Performance"],
    ["Invoice entered by mistake", "Cancel with a reason; stays in history", "None"],
    ["Rate billed higher than the rate card", "Flagged on the bill line", "Query the transporter, or approve with remarks if agreed"],
    ["LR billed twice", "Blocks approval", "Delete the duplicate line"],
    ["LR not in our records", "Blocks approval; may suggest a likely consignment", "Link it to the right consignment or remove it"],
    ["Person tries to approve own work", "Refused (two-person rule)", "Ask the checker"],
    ["Wrong password five times", "Account locked for 15 minutes", "Master: Users › Set password &amp; unlock"],
], [5.2, 6, 5.8])

# ------------------------------------------------------------------------------------- 9 going live
h1("9. Before real use")
num(["Create a fresh, empty database: <i>python -m app.cli init-db</i>, then <i>python -m app.cli create-admin --username … --password …</i>. Do not copy anything from the demonstration database.",
     "Enter your real ERPs, CFAs, areas, companies, customers, transporters and rate cards, in the order of section 3. Ask a second person to approve each rate card.",
     "Create each real user with the right role and scope. Remove or deactivate the demonstration users if they were ever created.",
     "Set the organisation name and address, and review the dispatch rules in System Settings.",
     "Use the drop folder or file import first; add API connections later.",
     "Keep paid services (AI bill reading, WhatsApp, SMS) locked unless the business decides to pay for them.",
     "Make a database backup and test a restore."])
box("Demonstration logins (for the demonstration database only): admin / Master@2026; demo.checker, demo.operator, demo.accounts / Demo@Pass2026. "
    "Change them if the demonstration is ever shared outside the office.", "warn")
h2("Starting the demonstration again")
P("Double-click <b>start_demonstration.bat</b> in this folder. It starts the application on <b>http://127.0.0.1:8002</b> with the demonstration database. "
  "Close the window to stop. Your real data is not involved.")

doc = Doc(OUT)
doc.multiBuild(S)
print("pdf written", OUT)
