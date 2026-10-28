"""Transporter bills: entry (typed, Excel/CSV statement or scanned photo), cross-check against our gatepasses,
irregularity report, rate review, approval and payment.

A bill line is one consignment as the transporter sees it: LR / bilty / GR number, date, party, station, cases,
rate, amount. One consignment can cover several of our invoices (same customer on the same gatepass, one LR), so a
line links to all of them (`invoice_ids`). The line's system freight is what our gatepass froze for those invoices
(transporter freight + direct-pickup extra; an outside auto's charge is paid to the auto, not billed here).
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog, BillStatus, DispatchSlip, DispatchSlipLine, Invoice, InvoiceStatus, LineMatch, Location, Transporter,
    TransporterBill, TransporterBillLine, User, utcnow,
)
from app.services import audit
from app.services import settings_service as cfg
from app.services import transporter_rate_service as trs
from app.services.errors import BusinessError
from app.services.import_service import read_table
from app.services.utils import money, norm_code, parse_date, parse_decimal, parse_int, safe_int

MODULE = "RECONCILIATION"
EDITABLE = (BillStatus.PENDING, BillStatus.MATCHED, BillStatus.VARIANCE)
ZERO = Decimal("0")


def _editable(bill: TransporterBill) -> None:
    if bill.status not in EDITABLE:
        raise BusinessError(f"Bill is {bill.status}; it can no longer be changed")


def create_bill(db: Session, user: User, *, transporter_id: int, bill_no: str, bill_date: date, bill_amount,
                company_id=None, cfa_id=None, period_from=None, period_to=None, remarks=None,
                source: str = "MANUAL", total_cases=None) -> TransporterBill:
    if not transporter_id or not db.get(Transporter, transporter_id):
        raise BusinessError("Select transporter")
    if not bill_no or not bill_no.strip():
        raise BusinessError("Bill number is required")
    if not bill_date:
        raise BusinessError("Bill date is required")
    try:
        amt = parse_decimal(bill_amount)
    except ValueError as e:
        raise BusinessError(f"Bill amount: {e}") from e
    if amt is None or amt < 0:
        raise BusinessError("Bill amount is required")
    if period_from and period_to and period_to < period_from:
        raise BusinessError("Billing period end is before start")
    if db.scalar(select(TransporterBill).where(TransporterBill.transporter_id == transporter_id,
                                               TransporterBill.bill_no == bill_no.strip().upper())):
        raise BusinessError(f"Bill {bill_no} of this transporter already exists (duplicate bill)")
    bill = TransporterBill(transporter_id=transporter_id, bill_no=bill_no.strip().upper(), bill_date=bill_date,
                           bill_amount=money(amt), company_id=company_id or None, cfa_id=cfa_id or None,
                           period_from=period_from, period_to=period_to, remarks=remarks, created_by_id=user.id,
                           source=source, total_cases=total_cases, scan_files=[], extraction={})
    db.add(bill)
    db.flush()
    audit.log(db, user, "BILL_CREATED", MODULE, record_type="TransporterBill", record_id=bill.id, ref=bill.bill_no,
              field="bill_amount", new=bill.bill_amount, details={"source": source})
    return bill


# ----------------------------------------------------------------------------- consignments (our side)
def _dispatched_stmt(bill: TransporterBill):
    stmt = select(Invoice).where(Invoice.transporter_id == bill.transporter_id,
                                 Invoice.status == InvoiceStatus.DISPATCHED)
    if bill.company_id:
        stmt = stmt.where(Invoice.company_id == bill.company_id)
    if bill.cfa_id:
        stmt = stmt.where(Invoice.cfa_id == bill.cfa_id)
    return stmt


def consignments(invs: list[Invoice]) -> list[list[Invoice]]:
    """Group invoices the way a transporter books them: same LR = one consignment; without an LR, the same customer
    on the same gatepass is one consignment."""
    groups: dict[tuple, list[Invoice]] = {}
    for inv in sorted(invs, key=lambda i: (i.dispatch_date or i.invoice_date, i.dispatch_no or "", i.id)):
        key = ("LR", inv.lr_no) if inv.lr_no else ("GP", inv.dispatch_no or f"#{inv.id}", inv.customer_id)
        groups.setdefault(key, []).append(inv)
    return list(groups.values())


def _billed_elsewhere(db: Session, inv: Invoice, bill: TransporterBill) -> TransporterBill | None:
    if inv.transporter_bill_id and inv.transporter_bill_id != bill.id:
        other = db.get(TransporterBill, inv.transporter_bill_id)
        if other and other.status != BillStatus.REJECTED:
            return other
    return None


def link_dispatches(db: Session, user: User, bill: TransporterBill) -> int:
    """Add a line for every dispatched consignment of this transporter in the billing period not yet billed."""
    _editable(bill)
    if not bill.period_from or not bill.period_to:
        raise BusinessError("Set the billing period to auto-link dispatches")
    stmt = _dispatched_stmt(bill).where(Invoice.dispatch_date >= bill.period_from,
                                        Invoice.dispatch_date <= bill.period_to)
    existing = {i for ln in bill.lines for i in (ln.invoice_ids or ([ln.invoice_id] if ln.invoice_id else []))}
    fresh = [inv for inv in db.scalars(stmt.order_by(Invoice.dispatch_date, Invoice.id))
             if inv.id not in existing and not _billed_elsewhere(db, inv, bill)]
    n = 0
    for grp in consignments(fresh):
        first = grp[0]
        bill.lines.append(TransporterBillLine(
            invoice_id=first.id, invoice_ids=[i.id for i in grp], lr_no=first.lr_no, lr_date=first.lr_date,
            invoice_no=", ".join(i.invoice_no for i in grp)[:50], dispatch_no=first.dispatch_no,
            billed_boxes=sum(i.boxes or 0 for i in grp), party_name=first.customer.name[:160],
            station=first.location.name, issues=[], suggestions=[]))
        n += 1
    db.flush()
    rematch(db, bill)
    audit.log(db, user, "BILL_DISPATCHES_LINKED", MODULE, record_type="TransporterBill", record_id=bill.id,
              ref=bill.bill_no, details={"lines_added": n})
    return n


# ----------------------------------------------------------------------------- statement files / scanned lines
LINE_FIELDS = {
    "lr_no": ["lr no", "lr number", "lr", "docket no", "docket", "gr no", "g.r. no", "gr. no", "cn no",
              "consignment no", "bilty no", "bilty", "bilti no", "builty no"],
    "invoice_no": ["invoice no", "invoice number", "inv no", "bill no", "invoice"],
    "dispatch_no": ["dispatch no", "dispatch number", "slip no", "gatepass no"],
    "lr_date": ["lr date", "gr date", "bilty date", "date", "booking date"],
    "party_name": ["party name", "party", "consignee", "receiver", "customer"],
    "station": ["station", "town", "destination", "to", "place"],
    "billed_boxes": ["boxes", "no of boxes", "cases", "packages", "pkgs", "pkg", "qty", "nag", "dag", "tp",
                     "no of pkgs", "quantity"],
    "billed_rate": ["rate", "rate per case", "rate per c/s", "rate/case", "rate per box"],
    "billed_amount": ["amount", "freight", "billed amount", "freight amount", "charges", "total", "kiraya"],
    "remarks": ["remarks", "note"],
}


def _line_from(rec: dict, cmap: dict) -> tuple[TransporterBillLine | None, str | None]:
    def get(k):
        return rec.get(cmap[k]) if k in cmap else None

    try:
        amt = parse_decimal(get("billed_amount"))
        rate = parse_decimal(get("billed_rate"))
        boxes = parse_int(get("billed_boxes"))
        lr_d = parse_date(get("lr_date")) if get("lr_date") not in (None, "") else None
    except ValueError as e:
        return None, str(e)

    def txt(k, n):
        v = get(k)
        return (str(v).strip()[:n] or None) if v not in (None, "") else None

    lr = txt("lr_no", 40)
    return TransporterBillLine(
        lr_no=lr.upper() if lr else None, invoice_no=(txt("invoice_no", 50) or "").upper() or None,
        dispatch_no=(txt("dispatch_no", 40) or "").upper() or None, lr_date=lr_d,
        party_name=txt("party_name", 160), station=txt("station", 120), billed_boxes=boxes,
        billed_rate=money(rate) if rate is not None else None,
        billed_amount=money(amt) if amt is not None else None, remarks=txt("remarks", 500),
        issues=[], suggestions=[]), None


def import_lines(db: Session, user: User, bill: TransporterBill, content: bytes, filename: str) -> tuple[int, list[str]]:
    _editable(bill)
    headers, records = read_table(content, filename)
    norm = {h.lower().strip(): h for h in headers}
    cmap = {}
    for f, syns in LINE_FIELDS.items():
        for s in syns:
            if s in norm and norm[s] not in cmap.values():
                cmap[f] = norm[s]
                break
    if not ({"billed_amount", "billed_boxes"} & set(cmap)) or not ({"lr_no", "invoice_no", "dispatch_no"} & set(cmap)):
        raise BusinessError("File needs an Amount (or Cases) column and at least one of LR / Bilty No, Invoice No or "
                            "Dispatch No")
    errors, n = [], 0
    for i, rec in enumerate(records, start=2):
        ln, err = _line_from(rec, cmap)
        if err:
            errors.append(f"Row {i}: {err}")
            continue
        bill.lines.append(ln)
        n += 1
    if bill.source == "MANUAL":
        bill.source = "FILE"
    db.flush()
    rematch(db, bill)
    audit.log(db, user, "BILL_LINES_IMPORTED", MODULE, record_type="TransporterBill", record_id=bill.id,
              ref=bill.bill_no, details={"lines": n, "errors": errors[:20], "file": filename})
    return n, errors


EDIT_COLS = ("lr_no", "lr_date", "party_name", "station", "billed_boxes", "billed_rate", "billed_amount", "remarks")


def save_lines(db: Session, user: User, bill: TransporterBill, rows: list[dict]) -> int:
    """Line editor (scanned bills): rows = [{id?, lr_no, lr_date, party_name, station, billed_boxes, billed_rate,
    billed_amount, remarks, delete?}]. Every changed cell is audited."""
    _editable(bill)
    by_id = {ln.id: ln for ln in bill.lines}
    changed = 0
    for i, r in enumerate(rows, start=1):
        lid = safe_int(r.get("id"))
        ln = by_id.get(lid) if lid else None
        if r.get("delete") and ln:
            delete_line(db, user, bill, ln.id, _rematch=False)
            changed += 1
            continue
        if not ln and all(str(r.get(k) or "").strip() == "" for k in EDIT_COLS):
            continue  # empty spare row
        rec = {k: r.get(k) for k in EDIT_COLS}
        new, err = _line_from(rec, {k: k for k in EDIT_COLS})
        if err:
            raise BusinessError(f"Line {i}: {err}")
        if ln is None:
            bill.lines.append(new)
            audit.log(db, user, "BILL_LINE_ADDED", MODULE, record_type="TransporterBill", record_id=bill.id,
                      ref=bill.bill_no, details={k: audit._s(getattr(new, k)) for k in EDIT_COLS})
            changed += 1
            continue
        diffs = {k: (getattr(ln, k), getattr(new, k)) for k in EDIT_COLS
                 if k in r and audit._s(getattr(ln, k)) != audit._s(getattr(new, k))}
        if diffs:
            if "lr_no" in diffs:  # a different LR may be a different consignment
                ln.invoice_id = None
                ln.invoice_ids = []
            for k, (_, v) in diffs.items():
                setattr(ln, k, v)
            audit.log_changes(db, user, "BILL_LINE_EDITED", MODULE, diffs, record_type="TransporterBillLine",
                              record_id=ln.id, ref=f"{bill.bill_no} / {ln.lr_no or ln.invoice_no or ''}")
            changed += 1
    db.flush()
    rematch(db, bill)
    return changed


def set_line_amounts(db: Session, user: User, bill: TransporterBill, amounts: dict[int, str]) -> None:
    _editable(bill)
    by_id = {ln.id: ln for ln in bill.lines}
    for lid, raw in amounts.items():
        ln = by_id.get(safe_int(lid))
        if not ln:
            continue
        try:
            v = parse_decimal(raw)
        except ValueError as e:
            raise BusinessError(f"Line {lid}: {e}") from e
        v = money(v) if v is not None else None
        if ln.billed_amount != v:
            audit.log(db, user, "BILL_LINE_AMOUNT", MODULE, record_type="TransporterBillLine", record_id=ln.id,
                      ref=f"{bill.bill_no} / {ln.lr_no or ln.invoice_no}", field="billed_amount",
                      old=ln.billed_amount, new=v)
            ln.billed_amount = v
    rematch(db, bill)


def allocate_header(db: Session, user: User, bill: TransporterBill) -> None:
    """Spread the bill amount over lines in proportion to calculated freight (when the transporter bills a total)."""
    _editable(bill)
    lines = [ln for ln in bill.lines if ln.calculated_amount]
    total_calc = sum((ln.calculated_amount for ln in lines), ZERO)
    if not lines or not total_calc:
        raise BusinessError("No matched lines with calculated freight to allocate against")
    remaining = bill.bill_amount
    for i, ln in enumerate(lines):
        if i == len(lines) - 1:
            ln.billed_amount = money(remaining)
        else:
            share = money(bill.bill_amount * ln.calculated_amount / total_calc)
            ln.billed_amount = share
            remaining -= share
    audit.log(db, user, "BILL_ALLOCATED", MODULE, record_type="TransporterBill", record_id=bill.id, ref=bill.bill_no,
              details={"lines": len(lines), "amount": str(bill.bill_amount)})
    rematch(db, bill)


def delete_line(db: Session, user: User, bill: TransporterBill, line_id: int, _rematch: bool = True) -> None:
    _editable(bill)
    ln = next((x for x in bill.lines if x.id == line_id), None)
    if not ln:
        raise BusinessError("Line not found")
    for iid in (ln.invoice_ids or ([ln.invoice_id] if ln.invoice_id else [])):
        inv = db.get(Invoice, iid)
        if inv and inv.transporter_bill_id == bill.id:
            inv.transporter_bill_id = None
            inv.actual_freight = None
    bill.lines.remove(ln)
    audit.log(db, user, "BILL_LINE_DELETED", MODULE, record_type="TransporterBill", record_id=bill.id,
              ref=bill.bill_no, details={"lr": ln.lr_no, "invoice": ln.invoice_no, "amount": audit._s(ln.billed_amount)})
    db.flush()
    if _rematch:
        rematch(db, bill)


# ----------------------------------------------------------------------------- matching & cross-check
def _letters(s) -> str:
    return re.sub(r"[^A-Z]", "", norm_code(s))


def _is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


def place_matches(text, lo: Location | None, extra: list[str] | None = None) -> bool:
    """Does a station written on a bill ('RJN', 'Bhilai', 'BKPR', 'Raigarh') mean this delivery area?"""
    s = _letters(text)
    if not s or lo is None:
        return True
    names = [_letters(x) for x in (lo.code, lo.name, lo.city, *(extra or [])) if x]
    for n in names:
        if not n:
            continue
        if s == n or n.startswith(s) or s.startswith(n) or (len(s) >= 3 and _is_subsequence(s, n) and s[0] == n[0]):
            return True
        if difflib.SequenceMatcher(None, s, n).ratio() >= 0.75:
            return True
    return False


def party_matches(text, name: str | None) -> bool:
    a, b = _letters(text), _letters(name)
    if not a or not b:
        return True
    if a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.6:
        return True
    ta = {t for t in re.split(r"[^A-Z]+", norm_code(text)) if len(t) >= 3}
    tb = {t for t in re.split(r"[^A-Z]+", norm_code(name)) if len(t) >= 3}
    return bool(ta & tb)


def transporter_due(db: Session, inv: Invoice) -> Decimal:
    """What the transporter may bill for this invoice: frozen freight + its share of a direct-pickup extra."""
    due = inv.freight_amount or ZERO
    if inv.extra_charge and inv.dispatch_slip_id:
        slip = db.get(DispatchSlip, inv.dispatch_slip_id)
        if slip and slip.pickup_charge:
            extras = (slip.pickup_charge or ZERO) + (slip.auto_charge or ZERO)
            if extras:
                due += money(inv.extra_charge * slip.pickup_charge / extras)
    return money(due)


def _cash_paid(slip: DispatchSlip | None) -> bool:
    """The transporter's part of the gatepass was paid in cash at dispatch (CASH transporter)."""
    if not slip or not slip.transporter_id:
        return False
    transporter_part = (slip.freight_total or ZERO) + (slip.pickup_charge or ZERO)
    paid_to_transporter = (slip.amount_paid or ZERO) - (slip.auto_charge or ZERO)
    return transporter_part > 0 and paid_to_transporter >= transporter_part - Decimal("0.01")


def _resolve(db: Session, bill: TransporterBill, ln: TransporterBillLine) -> list[Invoice]:
    if ln.lr_no:
        invs = list(db.scalars(select(Invoice).where(Invoice.lr_no == ln.lr_no,
                                                     Invoice.transporter_id == bill.transporter_id,
                                                     Invoice.status != InvoiceStatus.CANCELLED).order_by(Invoice.id)))
        if invs:
            return invs
    ids = list(ln.invoice_ids or ([ln.invoice_id] if ln.invoice_id else []))
    if ids:
        invs = [i for i in (db.get(Invoice, x) for x in ids) if i is not None]
        if invs:
            return invs
    if ln.dispatch_no:
        invs = list(db.scalars(select(Invoice).where(Invoice.dispatch_no == ln.dispatch_no,
                                                     Invoice.status != InvoiceStatus.CANCELLED)))
        if len({i.customer_id for i in invs}) == 1:
            return invs
        if len(invs) == 1:
            return invs
    if ln.invoice_no and "," not in ln.invoice_no:
        stmt = select(Invoice).where(Invoice.invoice_no == ln.invoice_no, Invoice.status != InvoiceStatus.CANCELLED)
        if bill.company_id:
            stmt = stmt.where(Invoice.company_id == bill.company_id)
        cands = list(db.scalars(stmt))
        if len(cands) == 1:
            return cands
    return []


def suggest(db: Session, bill: TransporterBill, ln: TransporterBillLine, taken: set[int]) -> list[dict]:
    """Our consignments that probably are this billed line (LR unknown to us): same transporter, dispatched within
    the window around the LR date, not billed yet; ranked by cases, station / party and date."""
    window = int(cfg.get(db, "recon.match_window_days") or 3)
    stmt = _dispatched_stmt(bill)
    if ln.lr_date:
        stmt = stmt.where(Invoice.dispatch_date >= ln.lr_date - timedelta(days=window),
                          Invoice.dispatch_date <= ln.lr_date + timedelta(days=window))
    elif bill.period_from and bill.period_to:
        stmt = stmt.where(Invoice.dispatch_date >= bill.period_from - timedelta(days=window),
                          Invoice.dispatch_date <= bill.period_to)
    else:
        return []
    invs = [i for i in db.scalars(stmt) if i.id not in taken and not _billed_elsewhere(db, i, bill)]
    out = []
    for grp in consignments(invs):
        first = grp[0]
        if first.lr_no and ln.lr_no and first.lr_no != ln.lr_no:
            continue  # it has another LR already
        cases = sum(i.boxes or 0 for i in grp)
        score = 0
        if ln.billed_boxes and cases == ln.billed_boxes:
            score += 4
        elif ln.billed_boxes and abs(cases - ln.billed_boxes) <= 1:
            score += 1
        if ln.station and place_matches(ln.station, first.location, [first.customer.city]):
            score += 2
        if ln.party_name and party_matches(ln.party_name, first.customer.name):
            score += 3
        if ln.lr_date and first.dispatch_date:
            score += max(0, 2 - abs((first.dispatch_date - ln.lr_date).days))
        if score < 4:
            continue
        out.append({"invoice_ids": [i.id for i in grp], "dispatch_no": first.dispatch_no,
                    "customer": first.customer.name, "location": first.location.name,
                    "date": first.dispatch_date.isoformat() if first.dispatch_date else None, "cases": cases,
                    "invoices": ", ".join(i.invoice_no for i in grp), "lr_no": first.lr_no, "score": score,
                    "freight": str(sum((transporter_due(db, i) for i in grp), ZERO))})
    out.sort(key=lambda x: -x["score"])
    return out[:3]


def _issue(code: str, msg: str, severity: str = "warn") -> dict:
    return {"code": code, "msg": msg, "severity": severity}


ISSUE_LABELS = {
    "NOT_FOUND": "LR not in our dispatches", "DUPLICATE_BILLING": "Billed twice", "NOT_DISPATCHED": "Not dispatched",
    "OTHER_TRANSPORTER": "Sent with another transporter", "PAID_IN_CASH": "Already paid in cash",
    "CASES_MORE": "More cases than dispatched", "CASES_LESS": "Fewer cases than dispatched",
    "RATE_DIFF": "Rate differs from rate card", "ARITHMETIC": "Cases × rate ≠ amount",
    "STATION": "Station / area mismatch", "PARTY": "Party name mismatch", "DATE": "Date outside period / before dispatch",
    "AMOUNT_VARIANCE": "Amount differs from our freight", "NO_RATE_CARD": "No rate card for this area",
}


def rematch(db: Session, bill: TransporterBill) -> None:
    tol_amt = cfg.get(db, "recon.tolerance_amount")
    tol_pct = cfg.get(db, "recon.tolerance_pct")
    tol_rate = cfg.get(db, "recon.rate_tolerance")
    linked_before = set(db.scalars(select(Invoice.id).where(Invoice.transporter_bill_id == bill.id)))
    linked_now: set[int] = set()
    problems = 0
    calc_total = ZERO
    seen_invoices: set[int] = set()

    for ln in bill.lines:
        issues: list[dict] = []
        ln.suggestions = []
        if ln.billed_rate is not None and ln.billed_boxes and ln.billed_amount is not None:
            expect = money(ln.billed_rate * ln.billed_boxes)
            if abs(expect - ln.billed_amount) > Decimal("1.00"):  # the transporter's own arithmetic
                issues.append(_issue("ARITHMETIC", f"{ln.billed_boxes} × ₹{ln.billed_rate} = ₹{expect}, bill says "
                                                   f"₹{ln.billed_amount}", "high"))
        invs = _resolve(db, bill, ln)
        if not invs:
            ln.invoice_id = None
            ln.invoice_ids = []
            ln.slip_id = None
            ln.calculated_amount = None
            ln.system_cases = None
            ln.card_rate = None
            ln.variance = None
            ln.match_status = LineMatch.NOT_FOUND
            ln.suggestions = suggest(db, bill, ln, seen_invoices | linked_now)
            issues.append(_issue("NOT_FOUND", f"LR {ln.lr_no or ln.invoice_no or '?'} is not in our dispatch records"
                                 + (" - possible match suggested" if ln.suggestions else ""), "high"))
            ln.issues = issues
            problems += 1
            continue
        first = invs[0]
        ln.invoice_id = first.id
        ln.invoice_ids = [i.id for i in invs]
        ln.lr_no = ln.lr_no or first.lr_no
        ln.invoice_no = ln.invoice_no or ", ".join(i.invoice_no for i in invs)[:50]
        ln.dispatch_no = ln.dispatch_no or first.dispatch_no
        ln.slip_id = first.dispatch_slip_id
        ln.system_cases = sum(i.boxes or 0 for i in invs)
        calc = sum((transporter_due(db, i) for i in invs), ZERO)
        ln.calculated_amount = money(calc)
        others = [b for b in (_billed_elsewhere(db, i, bill) for i in invs) if b]
        if any(i.id in seen_invoices for i in invs) or others:
            ln.match_status = LineMatch.DUPLICATE_BILLING
            why = f"Already billed on {others[0].bill_no}" if others else "Duplicate line in this bill"
            ln.variance = None
            issues.append(_issue("DUPLICATE_BILLING", why, "high"))
            ln.issues = issues
            problems += 1
            continue
        seen_invoices.update(i.id for i in invs)
        slip = db.get(DispatchSlip, first.dispatch_slip_id) if first.dispatch_slip_id else None
        if any(i.status != InvoiceStatus.DISPATCHED for i in invs):
            ln.match_status = LineMatch.NOT_DISPATCHED
            issues.append(_issue("NOT_DISPATCHED", "Our records show this consignment has not left the gate", "high"))
            problems += 1
        elif any(i.transporter_id != bill.transporter_id for i in invs):
            ln.match_status = LineMatch.OTHER_TRANSPORTER
            issues.append(_issue("OTHER_TRANSPORTER", "This consignment went with another transporter", "high"))
            problems += 1
        elif _cash_paid(slip):
            ln.match_status = LineMatch.PAID_IN_CASH
            ln.variance = None
            issues.append(_issue("PAID_IN_CASH", f"Freight for gatepass {slip.dispatch_no} was paid in cash at "
                                                 "dispatch - paying this line would pay twice", "high"))
            problems += 1
        elif ln.billed_amount is None:
            ln.match_status = LineMatch.UNBILLED
            ln.variance = None
        else:
            ln.variance = money(ln.billed_amount - calc)
            ln.match_status = LineMatch.MATCHED if abs(ln.variance) <= tol_amt else LineMatch.VARIANCE
            if ln.match_status == LineMatch.VARIANCE:
                problems += 1
                issues.append(_issue("AMOUNT_VARIANCE", f"Billed ₹{ln.billed_amount} vs our ₹{money(calc)} "
                                                        f"({'+' if ln.variance > 0 else ''}{ln.variance})",
                                     "high" if ln.variance > 0 else "info"))
        # --- irregularity checks on what the transporter wrote
        if ln.billed_boxes is not None and ln.billed_boxes != ln.system_cases:
            if ln.billed_boxes > ln.system_cases:
                issues.append(_issue("CASES_MORE", f"Billed {ln.billed_boxes} cases, gatepass shows "
                                                   f"{ln.system_cases}", "high"))
            else:
                issues.append(_issue("CASES_LESS", f"Billed {ln.billed_boxes} cases, gatepass shows "
                                                   f"{ln.system_cases}", "info"))
        on = ln.lr_date or first.dispatch_date or bill.bill_date
        lk = trs.find(db, bill.transporter_id, first.location_id, on, cfa_id=first.cfa_id, company_id=first.company_id) \
            if first.location_id else None
        ln.card_rate = lk.rate_per_case if lk and lk.found else None
        rate_billed = ln.billed_rate
        if rate_billed is None and ln.billed_amount is not None and ln.billed_boxes:
            extras = calc - sum((i.freight_amount or ZERO for i in invs), ZERO)  # direct-pickup share in the amount
            rate_billed = money((ln.billed_amount - extras) / ln.billed_boxes)
        if ln.card_rate is not None and rate_billed is not None and abs(rate_billed - ln.card_rate) > tol_rate:
            issues.append(_issue("RATE_DIFF", f"Charged ₹{rate_billed}/case to {first.location.name}; rate card "
                                              f"₹{ln.card_rate}", "high" if rate_billed > ln.card_rate else "info"))
        elif ln.card_rate is None and first.rate_level == "TRANSPORTER":
            issues.append(_issue("NO_RATE_CARD", f"No rate card for {first.location.name} on {on:%d-%m-%Y}", "info"))
        if ln.station and not place_matches(ln.station, first.location, [first.customer.city]):
            issues.append(_issue("STATION", f"Bill says '{ln.station}', our consignment went to "
                                            f"{first.location.name}", "warn"))
        if ln.party_name and not party_matches(ln.party_name, first.customer.name):
            issues.append(_issue("PARTY", f"Bill says '{ln.party_name}', our consignee is {first.customer.name}",
                                 "warn"))
        if ln.lr_date and first.dispatch_date and ln.lr_date < first.dispatch_date:
            issues.append(_issue("DATE", f"LR dated {ln.lr_date:%d-%m-%Y}, before our gate-out on "
                                         f"{first.dispatch_date:%d-%m-%Y}", "warn"))
        elif ln.lr_date and bill.period_from and bill.period_to and not (bill.period_from <= ln.lr_date <= bill.period_to):
            issues.append(_issue("DATE", f"LR date {ln.lr_date:%d-%m-%Y} is outside the bill period", "info"))
        ln.issues = issues
        if ln.match_status in (LineMatch.MATCHED, LineMatch.VARIANCE, LineMatch.UNBILLED):
            # only lines that genuinely belong to this bill count towards the system freight total; not-dispatched
            # or other-transporter lines used to inflate it and hide over-billing
            calc_total += calc
            shares = _shares(ln.billed_amount, invs, db)
            for inv, share in zip(invs, shares):
                inv.transporter_bill_id = bill.id
                inv.actual_freight = share
                linked_now.add(inv.id)

    for inv_id in linked_before - linked_now:
        inv = db.get(Invoice, inv_id)
        if inv and inv.transporter_bill_id == bill.id:
            inv.transporter_bill_id = None
            inv.actual_freight = None

    bill.calculated_amount = money(calc_total)
    bill.variance = money(bill.bill_amount - calc_total)
    if bill.status in EDITABLE:
        if not bill.lines:
            bill.status = BillStatus.PENDING
        else:
            pct_ok = calc_total and abs(bill.variance) <= calc_total * tol_pct / 100
            unbilled = any(ln.match_status == LineMatch.UNBILLED for ln in bill.lines)
            bill.status = BillStatus.MATCHED if (problems == 0 and pct_ok and not unbilled) else BillStatus.VARIANCE
    db.flush()


def _shares(billed: Decimal | None, invs: list[Invoice], db: Session) -> list[Decimal | None]:
    if billed is None:
        return [None] * len(invs)
    if len(invs) == 1:
        return [billed]
    due = [transporter_due(db, i) for i in invs]
    tot = sum(due, ZERO)
    weights = due if tot else [Decimal(i.boxes or 1) for i in invs]
    w_sum = sum(weights, ZERO)
    out = [money(billed * w / w_sum) for w in weights]
    out[-1] += billed - sum(out, ZERO)
    return out


def link_suggestion(db: Session, user: User, bill: TransporterBill, line_id: int, choice: int) -> None:
    """Accept a suggested match for a billed line whose LR we did not have: the line is linked to that consignment
    and the transporter's LR number is written onto our invoices (audited)."""
    _editable(bill)
    ln = next((x for x in bill.lines if x.id == line_id), None)
    if not ln or not ln.suggestions or not (0 <= choice < len(ln.suggestions)):
        raise BusinessError("Suggestion not found - refresh the page")
    sug = ln.suggestions[choice]
    invs = [db.get(Invoice, i) for i in sug["invoice_ids"]]
    if any(i is None for i in invs):
        raise BusinessError("The suggested invoices no longer exist")
    if ln.lr_no and any(i.lr_no and i.lr_no != ln.lr_no for i in invs):
        raise BusinessError("That consignment already has a different LR number")
    if ln.lr_no:
        from app.services import dispatch_service as ds
        ds.set_lr(db, user, invs, ln.lr_no, ln.lr_date, reason=f"LR taken from transporter bill {bill.bill_no}",
                  from_bill=True)
    ln.invoice_ids = [i.id for i in invs]
    ln.invoice_id = invs[0].id
    audit.log(db, user, "BILL_LINE_LINKED", MODULE, record_type="TransporterBillLine", record_id=ln.id,
              ref=f"{bill.bill_no} / {ln.lr_no or ''}", details={"invoices": [i.invoice_no for i in invs],
                                                                 "dispatch_no": sug.get("dispatch_no")})
    db.flush()
    rematch(db, bill)


# ----------------------------------------------------------------------------- irregularity report
def report(db: Session, bill: TransporterBill) -> dict:
    """Everything wrong with the bill, for the conversation with the transporter."""
    lines_total = sum((ln.billed_amount or ZERO for ln in bill.lines), ZERO)
    billed_cases = sum((ln.billed_boxes or 0 for ln in bill.lines))
    header: list[dict] = []
    if bill.lines and abs(lines_total - bill.bill_amount) > Decimal("1.00") and any(ln.billed_amount for ln in bill.lines):
        header.append(_issue("TOTAL", f"Lines add up to ₹{money(lines_total)} but the bill total is "
                                      f"₹{bill.bill_amount} (difference ₹{money(bill.bill_amount - lines_total)})", "high"))
    if bill.total_cases is not None and billed_cases and bill.total_cases != billed_cases:
        header.append(_issue("TOTAL_CASES", f"Lines add up to {billed_cases} cases but the bill says "
                                            f"{bill.total_cases}", "warn"))
    counts = Counter(i["code"] for ln in bill.lines for i in (ln.issues or []))
    unbilled = []
    if bill.period_from and bill.period_to:
        on_bills = set(db.scalars(select(Invoice.id).where(Invoice.transporter_bill_id.isnot(None))))
        stmt = _dispatched_stmt(bill).where(Invoice.dispatch_date >= bill.period_from,
                                            Invoice.dispatch_date <= bill.period_to)
        rest = [i for i in db.scalars(stmt) if i.id not in on_bills and not _cash_paid(
            db.get(DispatchSlip, i.dispatch_slip_id) if i.dispatch_slip_id else None)]
        for grp in consignments(rest):
            first = grp[0]
            unbilled.append({"dispatch_no": first.dispatch_no, "date": first.dispatch_date,
                             "customer": first.customer.name, "location": first.location.name,
                             "invoices": ", ".join(i.invoice_no for i in grp), "lr_no": first.lr_no,
                             "cases": sum(i.boxes or 0 for i in grp),
                             "freight": sum((transporter_due(db, i) for i in grp), ZERO)})
    over = sum((ln.variance for ln in bill.lines if ln.variance and ln.variance > 0), ZERO)
    blocked = sum((ln.billed_amount or ZERO for ln in bill.lines if ln.match_status in BLOCKING_LINES), ZERO)
    return {"header": header, "counts": counts, "unbilled": unbilled, "lines_total": lines_total,
            "billed_cases": billed_cases, "overbilled": over, "blocked_amount": blocked,
            "labels": ISSUE_LABELS,
            "problem_lines": [ln for ln in bill.lines if ln.issues and any(i["severity"] != "info" for i in ln.issues)]}


# ----------------------------------------------------------------------------- rate review
def rate_review(db: Session, bill: TransporterBill) -> list[dict]:
    """Per delivery area on this bill: rate-card rate vs the rate actually charged (stated, or amount / cases)."""
    rows: dict[int, dict] = {}
    on = bill.period_to or bill.bill_date
    for ln in bill.lines:
        if not ln.invoice_ids or ln.match_status in (LineMatch.NOT_FOUND, LineMatch.DUPLICATE_BILLING):
            continue
        inv = db.get(Invoice, ln.invoice_ids[0])
        if not inv:
            continue
        r = ln.billed_rate
        if r is None and ln.billed_amount is not None and ln.billed_boxes:
            invs = [i for i in (db.get(Invoice, x) for x in ln.invoice_ids) if i]
            extras = (ln.calculated_amount or ZERO) - sum((i.freight_amount or ZERO for i in invs), ZERO)
            r = money((ln.billed_amount - extras) / ln.billed_boxes)
        row = rows.setdefault(inv.location_id, {"location": inv.location, "card": None, "charged": Counter(),
                                                "lines": 0})
        row["lines"] += 1
        if r is not None:
            row["charged"][r] += 1
    tol = cfg.get(db, "recon.rate_tolerance")
    out = []
    for loc_id, row in rows.items():
        lk = trs.find(db, bill.transporter_id, loc_id, on)
        row["card"] = lk.rate_per_case
        row["most"] = row["charged"].most_common(1)[0][0] if row["charged"] else None
        row["differs"] = row["most"] is not None and (row["card"] is None or abs(row["most"] - row["card"]) > tol)
        row["pending"] = next((p["pending"] for p in trs.current_and_pending(db, bill.transporter_id)
                               if p["location"].id == loc_id), None)
        out.append(row)
    return sorted(out, key=lambda x: (not x["differs"], x["location"].name))


def apply_rate_review(db: Session, user: User, bill: TransporterBill, changes: list[dict], note: str | None) -> int:
    """changes = [{location_id, rate, effective_from}] -> new rate-card versions awaiting approval (source BILL).
    An empty list records "no rate changes" for this bill."""
    if not user.can("recon.edit") and not user.can("rate.create"):
        raise BusinessError("Not authorised to record rate changes")
    n = 0
    for ch in changes:
        loc_id = safe_int(ch.get("location_id"))
        try:
            rate = parse_decimal(ch.get("rate"))
            eff = parse_date(ch.get("effective_from")) if ch.get("effective_from") else (bill.period_from or bill.bill_date)
        except ValueError as e:
            raise BusinessError(str(e)) from e
        if not loc_id or rate is None:
            continue
        # accounts staff may put a change forward from the bill; a rate approver (not them) decides it
        trs.propose(db, user, transporter_id=bill.transporter_id, location_id=loc_id, rate_per_case=rate,
                    effective_from=eff, source="BILL", source_bill_id=bill.id, check_permission=False,
                    reason=f"Rate charged on bill {bill.bill_no}" + (f" - {note}" if note else ""))
        n += 1
    bill.rates_reviewed_at = utcnow()
    bill.rates_reviewed_by_id = user.id
    bill.rates_review_note = (note or "").strip() or ("No rate changes" if not n else f"{n} rate change(s) proposed")
    audit.log(db, user, "BILL_RATES_REVIEWED", MODULE, record_type="TransporterBill", record_id=bill.id,
              ref=bill.bill_no, reason=note, details={"changes": n})
    return n


# ----------------------------------------------------------------------------- approval & payment
BLOCKING_LINES = (LineMatch.NOT_FOUND, LineMatch.NOT_DISPATCHED, LineMatch.DUPLICATE_BILLING,
                  LineMatch.OTHER_TRANSPORTER, LineMatch.PAID_IN_CASH)
EDIT_ACTIONS = ("BILL_DISPATCHES_LINKED", "BILL_LINES_IMPORTED", "BILL_LINE_AMOUNT", "BILL_ALLOCATED",
                "BILL_LINE_DELETED", "BILL_AMOUNT_CHANGED", "BILL_LINE_EDITED", "BILL_LINE_ADDED", "BILL_LINE_LINKED",
                "BILL_HEADER_CHANGED")


def _edited_by(db: Session, bill: TransporterBill, user: User) -> bool:
    from sqlalchemy import and_, or_
    line_ids = [str(ln.id) for ln in bill.lines]
    cond = and_(AuditLog.record_type == "TransporterBill", AuditLog.record_id == str(bill.id))
    if line_ids:
        cond = or_(cond, and_(AuditLog.record_type == "TransporterBillLine", AuditLog.record_id.in_(line_ids)))
    return bool(db.scalar(select(AuditLog.id).where(AuditLog.user_id == user.id, AuditLog.action.in_(EDIT_ACTIONS),
                                                    cond).limit(1)))


def approve(db: Session, user: User, bill: TransporterBill, remarks: str | None) -> None:
    if not user.can("recon.approve"):
        raise BusinessError("Not authorised to approve bills")
    if bill.status not in (BillStatus.MATCHED, BillStatus.VARIANCE):
        raise BusinessError("Only MATCHED or VARIANCE bills can be approved")
    if not cfg.get(db, "recon.allow_self_approval"):
        if bill.created_by_id == user.id:
            raise BusinessError("Maker-checker: the person who entered the bill cannot approve it")
        if _edited_by(db, bill, user):
            raise BusinessError("Maker-checker: you changed amounts/lines on this bill, so another approver must approve it")
    bad = [ln for ln in bill.lines if ln.match_status in BLOCKING_LINES]
    if bad:
        kinds = sorted({ln.match_status.replace("_", " ").lower() for ln in bad})
        raise BusinessError(f"{len(bad)} line(s) cannot be paid on this bill ({', '.join(kinds)}). Delete or correct "
                            "them first - otherwise the same dispatch could be paid twice.")
    if bill.status == BillStatus.VARIANCE and (not remarks or len(remarks.strip()) < 5):
        raise BusinessError("Approving a bill with variance requires remarks explaining the variance")
    old = bill.status
    bill.status = BillStatus.APPROVED
    bill.approved_by_id = user.id
    bill.approved_at = utcnow()
    if remarks:
        bill.remarks = ((bill.remarks + " | ") if bill.remarks else "") + f"Approval: {remarks}"
    audit.log(db, user, "BILL_APPROVED", MODULE, record_type="TransporterBill", record_id=bill.id, ref=bill.bill_no,
              field="status", old=old, new=BillStatus.APPROVED, reason=remarks,
              details={"bill": str(bill.bill_amount), "calculated": str(bill.calculated_amount),
                       "variance": str(bill.variance)})


def mark_paid(db: Session, user: User, bill: TransporterBill, payment_ref: str) -> None:
    if not user.can("recon.pay"):
        raise BusinessError("Not authorised to mark bills paid")
    if bill.status != BillStatus.APPROVED:
        raise BusinessError("Only approved bills can be marked paid")
    if not payment_ref or not payment_ref.strip():
        raise BusinessError("Payment reference (UTR / cheque no) is required")
    bill.status = BillStatus.PAID
    bill.paid_at = utcnow()
    bill.paid_by_id = user.id
    bill.payment_ref = payment_ref.strip()
    audit.log(db, user, "BILL_PAID", MODULE, record_type="TransporterBill", record_id=bill.id, ref=bill.bill_no,
              field="status", old=BillStatus.APPROVED, new=BillStatus.PAID, reason=payment_ref)


def reject(db: Session, user: User, bill: TransporterBill, reason: str) -> None:
    if not user.can("recon.approve"):
        raise BusinessError("Not authorised")
    if bill.status in (BillStatus.PAID, BillStatus.REJECTED):
        raise BusinessError("Bill cannot be rejected in its current status")
    if not reason or len(reason.strip()) < 5:
        raise BusinessError("Reason required")
    old = bill.status
    bill.status = BillStatus.REJECTED
    for inv in db.scalars(select(Invoice).where(Invoice.transporter_bill_id == bill.id)):
        inv.transporter_bill_id = None
        inv.actual_freight = None
    audit.log(db, user, "BILL_REJECTED", MODULE, record_type="TransporterBill", record_id=bill.id, ref=bill.bill_no,
              field="status", old=old, new=BillStatus.REJECTED, reason=reason)


def update_header(db: Session, user: User, bill: TransporterBill, *, bill_no=None, bill_date=None, period_from=None,
                  period_to=None, total_cases=None) -> None:
    _editable(bill)
    try:
        values = {"bill_no": (bill_no or bill.bill_no).strip().upper(),
                  "bill_date": parse_date(bill_date) if bill_date else bill.bill_date,
                  "period_from": parse_date(period_from) if period_from else None,
                  "period_to": parse_date(period_to) if period_to else None,
                  "total_cases": parse_int(total_cases) if total_cases not in (None, "") else None}
    except ValueError as e:
        raise BusinessError(str(e)) from e
    if values["period_from"] and values["period_to"] and values["period_to"] < values["period_from"]:
        raise BusinessError("Billing period end is before start")
    if values["bill_no"] != bill.bill_no and db.scalar(select(TransporterBill).where(
            TransporterBill.transporter_id == bill.transporter_id, TransporterBill.bill_no == values["bill_no"],
            TransporterBill.id != bill.id)):
        raise BusinessError(f"Bill {values['bill_no']} of this transporter already exists (duplicate bill)")
    changes = audit.diff(bill, values)
    for k, v in values.items():
        setattr(bill, k, v)
    audit.log_changes(db, user, "BILL_HEADER_CHANGED", MODULE, changes, record_type="TransporterBill",
                      record_id=bill.id, ref=bill.bill_no)
    rematch(db, bill)
