"""PDF generation (ReportLab, pure Python): dispatch slips (A4/A5) and tabular reports."""
from __future__ import annotations

import io
from decimal import Decimal
from pathlib import Path

from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, A5, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from app.services.utils import fmt_money

FONT_DIR = Path(__file__).resolve().parent.parent / "static" / "fonts"
_FONTS_READY = False


def _fonts() -> tuple[str, str]:
    global _FONTS_READY
    if not _FONTS_READY:
        try:
            pdfmetrics.registerFont(TTFont("DejaVu", str(FONT_DIR / "DejaVuSans.ttf")))
            pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
            _FONTS_READY = True
        except Exception:  # pragma: no cover - fallback to core fonts
            return "Helvetica", "Helvetica-Bold"
    return "DejaVu", "DejaVu-Bold"


NAVY = colors.HexColor("#1f3a5f")
GREY = colors.HexColor("#5b6675")
LINE = colors.HexColor("#c9d1dc")
SOFT = colors.HexColor("#eef2f7")


def _styles(base: float):
    reg, bold = _fonts()
    return {
        "reg": ParagraphStyle("reg", fontName=reg, fontSize=base, leading=base * 1.3),
        "small": ParagraphStyle("small", fontName=reg, fontSize=base - 1.5, leading=(base - 1.5) * 1.3, textColor=GREY),
        "lbl": ParagraphStyle("lbl", fontName=reg, fontSize=base - 1.5, leading=(base - 1.5) * 1.25, textColor=GREY),
        "val": ParagraphStyle("val", fontName=bold, fontSize=base, leading=base * 1.3),
        "big": ParagraphStyle("big", fontName=bold, fontSize=base + 6, leading=(base + 6) * 1.2, textColor=NAVY),
        "title": ParagraphStyle("title", fontName=bold, fontSize=base + 3, leading=(base + 3) * 1.25),
        "center": ParagraphStyle("center", fontName=reg, fontSize=base - 1, leading=(base - 1) * 1.3,
                                 alignment=TA_CENTER, textColor=GREY),
        "right": ParagraphStyle("right", fontName=bold, fontSize=base, leading=base * 1.3, alignment=TA_RIGHT),
        "banner": ParagraphStyle("banner", fontName=bold, fontSize=base + 1, leading=(base + 1) * 1.3,
                                 alignment=TA_RIGHT, textColor=colors.HexColor("#b42318")),
    }


def _esc(v) -> str:
    if v is None or v == "":
        return "—"
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _slip_flowables(snap: dict, width: float, base: float, print_label: str, printed_line: str):
    st = _styles(base)
    P = Paragraph
    out = []

    # Header
    bc = code128.Code128(snap["dispatch_no"], barHeight=9 * mm if base > 8 else 7 * mm, barWidth=0.9 if base > 8 else 0.7)
    head = Table([
        [P(f"<b>{_esc(snap.get('org_name'))}</b>", st["title"]), P(print_label, st["banner"])],
        [P(_esc(snap.get("org_address")), st["small"]), ""],
        [P("DISPATCH SLIP", st["big"]), bc],
        [P(f"Dispatch No: <b>{_esc(snap['dispatch_no'])}</b>", st["reg"]),
         P(f"Generated: {_esc(snap.get('generated_at'))}", st["small"])],
    ], colWidths=[width * 0.6, width * 0.4])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("TOPPADDING", (0, 0), (-1, -1), 1),
                              ("LINEBELOW", (0, -1), (-1, -1), 1.2, NAVY)]))
    out += [head, Spacer(1, 2.5 * mm)]

    def grid(pairs, cols=2):
        cells, row = [], []
        for lbl, val in pairs:
            row.append([P(lbl, st["lbl"]), P(_esc(val), st["val"])])
            if len(row) == cols:
                cells.append(row)
                row = []
        if row:
            while len(row) < cols:
                row.append("")
            cells.append(row)
        t = Table(cells, colWidths=[width / cols] * cols)
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                               ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE), ("LEFTPADDING", (0, 0), (-1, -1), 4),
                               ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        return t

    c, f, cu, lo = snap["company"], snap["cfa"], snap["customer"], snap["location"]
    out.append(grid([
        ("Company / Principal", f"{c['name']} ({c['code']})"),
        ("Depot", f"{f['name']} ({f['code']})"),
        ("Invoice No", snap["invoice_no"]),
        ("Invoice Date", snap["invoice_date"]),
    ]))
    out.append(Spacer(1, 2 * mm))
    erp_code = f" · ERP code {cu['erp_code']}" if cu.get("erp_code") and cu.get("erp_code") != cu.get("code") else ""
    out.append(grid([
        ("Customer / Party", cu["name"]),
        ("Customer Code", f"{cu['code']}{erp_code}"),
        ("Delivery Address", cu.get("address")),
        ("Delivery Location", " · ".join(x for x in [lo["name"], lo.get("route") and f"Route {lo['route']}",
                                                     lo.get("zone")] if x)),
    ]))
    out.append(Spacer(1, 2 * mm))

    money_tbl = Table([
        [P("No. of Boxes", st["lbl"]), P("Rate per Box", st["lbl"]), P("Transportation Expense", st["lbl"])],
        [P(f"<font size={base + 8}><b>{snap['boxes']}</b></font>", st["reg"]),
         P(fmt_money(Decimal(snap["rate_per_box"])), st["val"]),
         P(f"<font size={base + 3}><b>{fmt_money(Decimal(snap['freight_amount']))}</b></font>", st["reg"])],
    ], colWidths=[width / 3] * 3, rowHeights=[None, (base + 12) * 1.1])
    money_tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("BOX", (0, 0), (-1, -1), 0.8, NAVY),
                                   ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 5)]))
    out += [money_tbl, Spacer(1, 2 * mm)]

    tr = snap.get("transporter") or {}
    out.append(grid([
        ("Transporter", f"{tr.get('name', '—')}" + (f" ({tr['mobile']})" if tr.get("mobile") else "")),
        ("Vehicle No", snap.get("vehicle_no")),
        ("LR / Docket No", snap.get("lr_no")),
        ("Dispatch Date & Time", f"{snap.get('dispatch_date') or '—'}  {snap.get('dispatch_time') or ''}"),
    ]))
    out.append(Spacer(1, 2 * mm))
    out.append(grid([("Remarks", snap.get("remarks") or "")], cols=1))
    out.append(Spacer(1, 3 * mm))

    sig_h = 14 * mm if base > 8 else 10 * mm
    sig = Table([
        [P("Prepared by", st["lbl"]), P("Checked by", st["lbl"]), P("Dispatched by", st["lbl"]),
         P("Transporter / Driver", st["lbl"])],
        [P(_esc(snap.get("prepared_by")), st["reg"]), P(_esc(snap.get("checked_by")), st["reg"]), "", ""],
        ["", "", "", ""],
        [P("Signature", st["small"]), P("Signature", st["small"]), P("Signature", st["small"]),
         P("Signature & mobile", st["small"])],
    ], colWidths=[width / 4] * 4, rowHeights=[None, None, sig_h, None])
    sig.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.3, LINE),
                             ("LINEBELOW", (0, 2), (-1, 2), 0.6, GREY), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    out += [sig, Spacer(1, 2 * mm)]
    out.append(P(_esc(snap.get("footer")), st["center"]))
    out.append(P(f"{snap.get('central_id', '')} · {printed_line}", st["center"]))
    return out


def _gatepass_flowables(snap: dict, width: float, base: float, print_label: str, printed_line: str):
    """v1.3 gatepass: one pickup, invoices grouped per customer, charges, payment. No vehicle / LR needed."""
    st = _styles(base)
    P = Paragraph
    out = []
    bc = code128.Code128(snap["dispatch_no"], barHeight=9 * mm if base > 8 else 7 * mm, barWidth=0.9 if base > 8 else 0.7)
    head = Table([
        [P(f"<b>{_esc(snap.get('org_name'))}</b>", st["title"]), P(print_label, st["banner"])],
        [P(_esc(snap.get("org_address")), st["small"]), ""],
        [P("GATEPASS / DISPATCH SLIP", st["big"]), bc],
        [P(f"No: <b>{_esc(snap['dispatch_no'])}</b>", st["reg"]),
         P(f"Generated: {_esc(snap.get('generated_at'))}", st["small"])],
    ], colWidths=[width * 0.6, width * 0.4])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("TOPPADDING", (0, 0), (-1, -1), 1),
                              ("LINEBELOW", (0, -1), (-1, -1), 1.2, NAVY)]))
    out += [head, Spacer(1, 2 * mm)]
    tr, auto = snap.get("transporter") or {}, snap.get("outside_auto") or {}
    carrier = (f"{tr.get('name')} · {tr.get('contact') or ''} {tr.get('mobile') or ''}"
               + (f" · GST {tr['gstin']}" if tr.get("gstin") else "") + f" · {tr.get('payment_label') or ''}") if tr \
        else ("Customer / own vehicle" if snap.get("mode") == "SELF" else "—")
    info = [[P("Dispatch date &amp; time", st["lbl"]), P(f"{_esc(snap.get('dispatch_date'))} {snap.get('dispatch_time') or ''}", st["val"]),
             P("From", st["lbl"]), P(_esc(f"{snap['cfa']['name']} ({snap['cfa']['code']})"), st["val"])],
            [P("Mode", st["lbl"]), P(_esc(snap.get("mode_label")), st["val"]), P("Carrier", st["lbl"]), P(_esc(carrier), st["val"])]]
    if auto:
        method = auto.get("method") or auto.get("charge_basis")
        if method == "PER_CASE":
            charge = "per box (boxes × rate)"
        else:
            charge = f"₹{Decimal(auto.get('trip_charge') or auto.get('rate') or 0):,.2f} for the whole trip"
        veh = f" · vehicle {auto['vehicle_no']}" if auto.get("vehicle_no") else ""
        info.append([P("Local auto", st["lbl"]), P(_esc(f"{auto.get('name')} {auto.get('mobile') or ''}{veh}"), st["val"]),
                     P("Auto charge", st["lbl"]), P(_esc(charge), st["val"])])
    t = Table(info, colWidths=[width * 0.14, width * 0.32, width * 0.12, width * 0.42])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    out += [t, Spacer(1, 2 * mm)]
    rows = [[P(x, st["lbl"]) for x in ("Customer · area / invoice", "Date", "Co.", "Cases", "Rate", "Charge")]]
    styles = [("BOX", (0, 0), (-1, -1), 0.6, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.3, LINE),
              ("BACKGROUND", (0, 0), (-1, 0), SOFT), ("ALIGN", (3, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP")]
    for g in snap.get("groups", []):
        cu = g["customer"]
        rows.append([P(f"<b>{_esc(cu['name'])}</b> ({_esc(cu.get('type'))}) — {_esc(g['location']['name'])}<br/>"
                       f"<font size={base - 1.5}>{_esc(cu.get('address'))} · {_esc(cu.get('phone'))} · LR: __________</font>",
                       st["reg"]), "", "", P(f"<b>{g['cases']}</b>", st["right"]), "",
                     P(f"<b>{fmt_money(Decimal(g['freight']) + Decimal(g.get('extra') or 0))}</b>", st["right"])])
        styles.append(("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), colors.HexColor("#f7f9fc")))
        styles.append(("SPAN", (0, len(rows) - 1), (2, len(rows) - 1)))
        for i in g["invoices"]:
            rc = f"<br/>&nbsp;&nbsp;<font size={base - 2}>{_esc(i['recognition_code'])}</font>" if i.get("recognition_code") else ""
            rows.append([P(f"&nbsp;&nbsp;{_esc(i['invoice_no'])}{rc}", st["reg"]), P(_esc(i["invoice_date"]), st["small"]),
                         P(_esc(i["company_code"]), st["small"]), P(str(i["cases"]), st["right"]),
                         P(fmt_money(Decimal(i["rate"])) if i.get("rate") is not None else "trip", st["right"]),
                         P(fmt_money(Decimal(i["freight"])), st["right"])])
    lines = Table(rows, colWidths=[width * 0.42, width * 0.13, width * 0.08, width * 0.1, width * 0.12, width * 0.15],
                  repeatRows=1)
    lines.setStyle(TableStyle(styles))
    out += [lines, Spacer(1, 2 * mm)]
    tot = snap.get("totals", {})
    extra = ""
    if Decimal(tot.get("pickup") or 0):
        extra += f" + pickup {fmt_money(Decimal(tot['pickup']))}"
    if Decimal(tot.get("auto") or 0):
        extra += f" + auto {fmt_money(Decimal(tot['auto']))}"
    money_tbl = Table([
        [P("Invoices", st["lbl"]), P("Total cases", st["lbl"]), P("Freight" + extra, st["lbl"]), P("Total charged", st["lbl"])],
        [P(f"<font size={base + 6}><b>{tot.get('invoices')}</b></font>", st["reg"]),
         P(f"<font size={base + 6}><b>{tot.get('cases')}</b></font>", st["reg"]),
         P(fmt_money(Decimal(tot.get("freight") or 0)), st["val"]),
         P(f"<font size={base + 3}><b>{fmt_money(Decimal(tot.get('total') or 0))}</b></font><br/>"
           f"<font size={base - 1}>paid now {fmt_money(Decimal(tot.get('paid') or 0))}"
           + (f" · to be billed {fmt_money(Decimal(tot.get('balance') or 0))}" if Decimal(tot.get("balance") or 0) > 0 else "")
           + "</font>", st["reg"])],
    ], colWidths=[width * 0.18, width * 0.18, width * 0.28, width * 0.36])
    money_tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("BOX", (0, 0), (-1, -1), 0.8, NAVY),
                                   ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    out += [money_tbl, Spacer(1, 2 * mm)]
    if snap.get("remarks"):
        out += [P(f"Remarks: {_esc(snap['remarks'])}", st["reg"]), Spacer(1, 2 * mm)]
    sig_h = 13 * mm if base > 8 else 9 * mm
    sig = Table([
        [P("Prepared by", st["lbl"]), P("Cases counted by", st["lbl"]), P("Gate / security", st["lbl"]),
         P("Received by transporter / driver", st["lbl"])],
        [P(_esc(snap.get("prepared_by")), st["reg"]), "", "", ""],
        ["", "", "", ""],
        [P("Signature", st["small"]), P("Signature", st["small"]), P("Signature &amp; time", st["small"]),
         P("Signature &amp; mobile", st["small"])],
    ], colWidths=[width / 4] * 4, rowHeights=[None, None, sig_h, None])
    sig.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.3, LINE),
                             ("LINEBELOW", (0, 2), (-1, 2), 0.6, GREY), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    out += [sig, Spacer(1, 2 * mm), P(_esc(snap.get("footer")), st["center"]),
            P(f"{_esc(snap.get('central_id'))} · {printed_line}", st["center"])]
    return out


def _training_page(canvas, doc) -> None:
    """Guided Training: every page made from practice data says it is not valid for business use."""
    from app.training import runtime
    if not runtime.active():
        return
    w, h = doc.pagesize
    reg, bold = _fonts()
    canvas.saveState()
    canvas.setFillColor(colors.Color(0.71, 0.33, 0.04, alpha=0.16))
    canvas.setFont(bold, 30 if w < 500 else 44)
    canvas.translate(w / 2, h / 2)
    canvas.rotate(35)
    canvas.drawCentredString(0, 12, "TRAINING - DEMONSTRATION ONLY")
    canvas.drawCentredString(0, -30, "NOT VALID FOR BUSINESS USE")
    canvas.restoreState()
    canvas.saveState()
    canvas.setFillColor(colors.Color(0.71, 0.33, 0.04))
    canvas.setFont(bold, 7)
    canvas.drawCentredString(w / 2, h - 7, runtime.WATERMARK)
    canvas.restoreState()


def dispatch_slips_pdf(items: list[tuple[dict, str, str]], size: str = "A5") -> bytes:
    """items: [(snapshot, print_label, printed_line)] -> one PDF, one slip per page."""
    pagesize = A5 if size == "A5" else A4
    margin = 9 * mm if size == "A5" else 14 * mm
    base = 7.6 if size == "A5" else 9.6
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=margin, rightMargin=margin, topMargin=margin,
                            bottomMargin=margin, title="Dispatch Slip", author="CTDMS")
    story = []
    for i, (snap, label, line) in enumerate(items):
        if i:
            story.append(PageBreak())
        if snap.get("v") == 2:  # a gatepass may run to a second page when it carries many invoices
            story.extend(_gatepass_flowables(snap, doc.width, base, label, line))
        else:
            story.append(KeepTogether(_slip_flowables(snap, doc.width, base, label, line)))
    doc.build(story, onFirstPage=_training_page, onLaterPages=_training_page)
    return buf.getvalue()


def table_pdf(title: str, subtitle: str, columns: list[tuple[str, str]], rows: list[list], totals: list | None = None,
              org: str = "") -> bytes:
    """Generic landscape report PDF. columns = [(label, kind)]"""
    reg, bold = _fonts()
    buf = io.BytesIO()
    ncols = len(columns)
    pagesize = landscape(A4)
    doc = SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=10 * mm, rightMargin=10 * mm, topMargin=12 * mm,
                            bottomMargin=12 * mm, title=title, author="CTDMS")
    fs = 7.2 if ncols <= 12 else 6.2
    cell = ParagraphStyle("c", fontName=reg, fontSize=fs, leading=fs * 1.2)
    cell_r = ParagraphStyle("cr", parent=cell, alignment=TA_RIGHT)
    head = ParagraphStyle("h", fontName=bold, fontSize=fs, leading=fs * 1.2, textColor=colors.white)
    head_r = ParagraphStyle("hr", parent=head, alignment=TA_RIGHT)
    tstyle = ParagraphStyle("t", fontName=bold, fontSize=13, leading=16, textColor=NAVY)
    sstyle = ParagraphStyle("s", fontName=reg, fontSize=8, leading=10, textColor=GREY)
    numeric = {"int", "money", "rate", "pct", "num"}
    note_head = ParagraphStyle("nh", fontName=bold, fontSize=8, leading=10, textColor=NAVY, spaceBefore=4)
    note_body = ParagraphStyle("nb", fontName=reg, fontSize=7, leading=8.6)

    # Very long cells (e.g. a transporter's 3,000 LR numbers in one row) cannot fit in a table row that has to
    # stay on one page - ReportLab raised LayoutError and the export returned HTTP 500 (QA-10). Such cells are
    # shown as "N items - see note [k]" and printed in full, across as many pages as needed, after the table.
    notes: list[tuple[str, str]] = []

    def _cell_text(v, i, r):
        s = "" if v is None else str(v)
        n = s.count(",") + 1 if "," in s else 0
        long_list = n > LONG_LIST_ITEMS and len(s) > 120
        if len(s) <= LONG_CELL_CHARS and not long_list:
            return _esc(v)
        label = "" if i == 0 or r[0] in (None, "") else str(r[0])
        label = label if 0 < len(label) <= 60 else (label[:57] + "..." if label else f"row {len(data)}")
        notes.append((f"[{len(notes) + 1}] {label} - {columns[i][0]}", s))
        what = f"{n:,} items" if n > 1 else f"{len(s):,} characters"
        return f"<i>{what} - full list in note [{len(notes)}] below</i>"

    data = [[Paragraph(_esc(lbl), head_r if kind in numeric else head) for lbl, kind in columns]]
    for r in rows:
        data.append([Paragraph(_cell_text(v, i, r), cell_r if columns[i][1] in numeric else cell)
                     for i, v in enumerate(r)])
    if totals:
        data.append([Paragraph(f"<b>{_esc(v)}</b>" if v not in (None, "") else "", cell_r if columns[i][1] in numeric
                               else cell) for i, v in enumerate(totals)])
    # splitInRow: a row that is still taller than a page (many medium-length cells) continues on the next page
    # instead of crashing.
    t = Table(data, repeatRows=1, colWidths=[doc.width / ncols] * ncols, splitInRow=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("GRID", (0, 0), (-1, -1), 0.25, LINE),
             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
             ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    if totals:
        style.append(("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#dde4ee")))
    t.setStyle(TableStyle(style))

    def _footer(canvas, d):
        canvas.saveState()
        canvas.setFont(reg, 7)
        canvas.setFillColor(GREY)
        canvas.drawString(10 * mm, 7 * mm, f"{org} · {title}")
        canvas.drawRightString(pagesize[0] - 10 * mm, 7 * mm, f"Page {d.page}")
        canvas.restoreState()

    story = [Paragraph(_esc(title), tstyle), Paragraph(_esc(subtitle), sstyle), Spacer(1, 4 * mm), t]
    if notes:
        story += [Spacer(1, 5 * mm), Paragraph("Notes - full text of long cells", tstyle)]
        for heading, text in notes:
            story.append(Paragraph(_esc(heading), note_head))
            # moderate-size paragraphs so ReportLab can break them across pages quickly; cut only between items
            while text:
                cut = len(text) if len(text) <= 3000 else (text.rfind(" ", 0, 3000) + 1 or 3000)
                story.append(Paragraph(_esc(text[:cut]), note_body))
                text = text[cut:]
    def _page(c, d):
        _footer(c, d)
        _training_page(c, d)

    doc.build(story, onFirstPage=_page, onLaterPages=_page)
    return buf.getvalue()


LONG_CELL_CHARS = 400   # longer text moves to the notes after the table
LONG_LIST_ITEMS = 10    # so does a comma-separated list with more items than this (LR / vehicle lists)
