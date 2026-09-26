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
        ("CFA", f"{f['name']} ({f['code']})"),
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
        story.append(KeepTogether(_slip_flowables(snap, doc.width, base, label, line)))
    doc.build(story)
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

    data = [[Paragraph(_esc(lbl), head_r if kind in numeric else head) for lbl, kind in columns]]
    for r in rows:
        data.append([Paragraph(_esc(v), cell_r if columns[i][1] in numeric else cell) for i, v in enumerate(r)])
    if totals:
        data.append([Paragraph(f"<b>{_esc(v)}</b>" if v not in (None, "") else "", cell_r if columns[i][1] in numeric
                               else cell) for i, v in enumerate(totals)])
    t = Table(data, repeatRows=1, colWidths=[doc.width / ncols] * ncols)
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

    doc.build([Paragraph(_esc(title), tstyle), Paragraph(_esc(subtitle), sstyle), Spacer(1, 4 * mm), t],
              onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
