"""Actual transporter bill entry / import and reconciliation against calculated system freight."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    BillStatus, Invoice, InvoiceStatus, LineMatch, Transporter, TransporterBill, TransporterBillLine, User, utcnow,
)
from app.services import audit
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from app.services.import_service import read_table
from app.services.utils import money, parse_decimal, parse_int

MODULE = "RECONCILIATION"
EDITABLE = (BillStatus.PENDING, BillStatus.MATCHED, BillStatus.VARIANCE)


def _editable(bill: TransporterBill) -> None:
    if bill.status not in EDITABLE:
        raise BusinessError(f"Bill is {bill.status}; it can no longer be changed")


def create_bill(db: Session, user: User, *, transporter_id: int, bill_no: str, bill_date: date, bill_amount,
                company_id=None, cfa_id=None, period_from=None, period_to=None, remarks=None) -> TransporterBill:
    if not transporter_id or not db.get(Transporter, transporter_id):
        raise BusinessError("Select transporter")
    if not bill_no or not bill_no.strip():
        raise BusinessError("Bill number is required")
    if not bill_date:
        raise BusinessError("Bill date is required")
    amt = parse_decimal(bill_amount)
    if amt is None or amt < 0:
        raise BusinessError("Bill amount is required")
    if period_from and period_to and period_to < period_from:
        raise BusinessError("Billing period end is before start")
    if db.scalar(select(TransporterBill).where(TransporterBill.transporter_id == transporter_id,
                                               TransporterBill.bill_no == bill_no.strip().upper())):
        raise BusinessError(f"Bill {bill_no} of this transporter already exists (duplicate bill)")
    bill = TransporterBill(transporter_id=transporter_id, bill_no=bill_no.strip().upper(), bill_date=bill_date,
                           bill_amount=money(amt), company_id=company_id or None, cfa_id=cfa_id or None,
                           period_from=period_from, period_to=period_to, remarks=remarks, created_by_id=user.id)
    db.add(bill)
    db.flush()
    audit.log(db, user, "BILL_CREATED", MODULE, record_type="TransporterBill", record_id=bill.id, ref=bill.bill_no,
              field="bill_amount", new=bill.bill_amount)
    return bill


def link_dispatches(db: Session, user: User, bill: TransporterBill) -> int:
    """Add a line for every dispatched invoice of this transporter in the billing period not yet billed."""
    _editable(bill)
    if not bill.period_from or not bill.period_to:
        raise BusinessError("Set the billing period to auto-link dispatches")
    stmt = select(Invoice).where(Invoice.transporter_id == bill.transporter_id,
                                 Invoice.status == InvoiceStatus.DISPATCHED,
                                 Invoice.dispatch_date >= bill.period_from, Invoice.dispatch_date <= bill.period_to)
    if bill.company_id:
        stmt = stmt.where(Invoice.company_id == bill.company_id)
    if bill.cfa_id:
        stmt = stmt.where(Invoice.cfa_id == bill.cfa_id)
    existing = {ln.invoice_id for ln in bill.lines if ln.invoice_id}
    n = 0
    for inv in db.scalars(stmt.order_by(Invoice.dispatch_date, Invoice.id)):
        if inv.id in existing:
            continue
        if inv.transporter_bill_id and inv.transporter_bill_id != bill.id:
            other = db.get(TransporterBill, inv.transporter_bill_id)
            if other and other.status != BillStatus.REJECTED:
                continue
        bill.lines.append(TransporterBillLine(invoice_id=inv.id, lr_no=inv.lr_no, invoice_no=inv.invoice_no,
                                              dispatch_no=inv.dispatch_no, billed_boxes=inv.boxes))
        n += 1
    db.flush()
    rematch(db, bill)
    audit.log(db, user, "BILL_DISPATCHES_LINKED", MODULE, record_type="TransporterBill", record_id=bill.id,
              ref=bill.bill_no, details={"lines_added": n})
    return n


LINE_FIELDS = {
    "lr_no": ["lr no", "lr number", "lr", "docket no", "docket", "gr no", "cn no", "consignment no"],
    "invoice_no": ["invoice no", "invoice number", "inv no", "bill no", "invoice"],
    "dispatch_no": ["dispatch no", "dispatch number", "slip no"],
    "billed_boxes": ["boxes", "no of boxes", "cases", "packages", "pkgs", "qty"],
    "billed_amount": ["amount", "freight", "billed amount", "freight amount", "charges", "total"],
    "remarks": ["remarks", "note"],
}


def import_lines(db: Session, user: User, bill: TransporterBill, content: bytes, filename: str) -> tuple[int, list[str]]:
    _editable(bill)
    headers, records = read_table(content, filename)
    norm = {h.lower().strip(): h for h in headers}
    cmap = {}
    for f, syns in LINE_FIELDS.items():
        for s in syns:
            if s in norm:
                cmap[f] = norm[s]
                break
    if "billed_amount" not in cmap or not ({"lr_no", "invoice_no", "dispatch_no"} & set(cmap)):
        raise BusinessError("File needs an Amount column and at least one of LR No / Invoice No / Dispatch No")
    errors, n = [], 0
    for i, rec in enumerate(records, start=2):
        try:
            amt = parse_decimal(rec.get(cmap["billed_amount"]))
            boxes = parse_int(rec.get(cmap["billed_boxes"])) if "billed_boxes" in cmap else None
        except ValueError as e:
            errors.append(f"Row {i}: {e}")
            continue
        bill.lines.append(TransporterBillLine(
            lr_no=str(rec.get(cmap.get("lr_no")) or "").strip().upper() or None if "lr_no" in cmap else None,
            invoice_no=str(rec.get(cmap.get("invoice_no")) or "").strip().upper() or None if "invoice_no" in cmap else None,
            dispatch_no=str(rec.get(cmap.get("dispatch_no")) or "").strip().upper() or None if "dispatch_no" in cmap else None,
            billed_boxes=boxes, billed_amount=money(amt) if amt is not None else None,
            remarks=rec.get(cmap["remarks"]) if "remarks" in cmap else None))
        n += 1
    db.flush()
    rematch(db, bill)
    audit.log(db, user, "BILL_LINES_IMPORTED", MODULE, record_type="TransporterBill", record_id=bill.id,
              ref=bill.bill_no, details={"lines": n, "errors": errors[:20], "file": filename})
    return n, errors


def set_line_amounts(db: Session, user: User, bill: TransporterBill, amounts: dict[int, str]) -> None:
    _editable(bill)
    by_id = {ln.id: ln for ln in bill.lines}
    for lid, raw in amounts.items():
        ln = by_id.get(int(lid))
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
    total_calc = sum((ln.calculated_amount for ln in lines), Decimal("0"))
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


def delete_line(db: Session, user: User, bill: TransporterBill, line_id: int) -> None:
    _editable(bill)
    ln = next((x for x in bill.lines if x.id == line_id), None)
    if not ln:
        raise BusinessError("Line not found")
    if ln.invoice_id:
        inv = db.get(Invoice, ln.invoice_id)
        if inv and inv.transporter_bill_id == bill.id:
            inv.transporter_bill_id = None
            inv.actual_freight = None
    bill.lines.remove(ln)
    audit.log(db, user, "BILL_LINE_DELETED", MODULE, record_type="TransporterBill", record_id=bill.id,
              ref=bill.bill_no, details={"lr": ln.lr_no, "invoice": ln.invoice_no})
    db.flush()
    rematch(db, bill)


def rematch(db: Session, bill: TransporterBill) -> None:
    tol_amt = cfg.get(db, "recon.tolerance_amount")
    tol_pct = cfg.get(db, "recon.tolerance_pct")
    linked_before = set(db.scalars(select(Invoice.id).where(Invoice.transporter_bill_id == bill.id)))
    linked_now: set[int] = set()
    problems = 0
    calc_total = Decimal("0")
    seen_invoices: set[int] = set()

    for ln in bill.lines:
        inv = db.get(Invoice, ln.invoice_id) if ln.invoice_id else None
        if not inv and ln.dispatch_no:
            inv = db.scalar(select(Invoice).where(Invoice.dispatch_no == ln.dispatch_no))
        if not inv and ln.lr_no:
            inv = db.scalar(select(Invoice).where(Invoice.lr_no == ln.lr_no,
                                                  Invoice.transporter_id == bill.transporter_id,
                                                  Invoice.status != InvoiceStatus.CANCELLED))
        if not inv and ln.invoice_no:
            stmt = select(Invoice).where(Invoice.invoice_no == ln.invoice_no, Invoice.status != InvoiceStatus.CANCELLED)
            if bill.company_id:
                stmt = stmt.where(Invoice.company_id == bill.company_id)
            cands = list(db.scalars(stmt))
            inv = cands[0] if len(cands) == 1 else None
        if not inv:
            ln.invoice_id = None
            ln.calculated_amount = None
            ln.variance = None
            ln.match_status = LineMatch.NOT_FOUND
            problems += 1
            continue
        ln.invoice_id = inv.id
        ln.lr_no = ln.lr_no or inv.lr_no
        ln.invoice_no = ln.invoice_no or inv.invoice_no
        ln.dispatch_no = ln.dispatch_no or inv.dispatch_no
        ln.calculated_amount = inv.freight_amount
        other = None
        if inv.transporter_bill_id and inv.transporter_bill_id != bill.id:
            other = db.get(TransporterBill, inv.transporter_bill_id)
        if inv.id in seen_invoices or (other and other.status != BillStatus.REJECTED):
            ln.match_status = LineMatch.DUPLICATE_BILLING
            ln.remarks = f"Already billed on {other.bill_no}" if other else "Duplicate line in this bill"
            ln.variance = None
            problems += 1
            continue
        seen_invoices.add(inv.id)
        if inv.status != InvoiceStatus.DISPATCHED:
            ln.match_status = LineMatch.NOT_DISPATCHED
            problems += 1
        elif inv.transporter_id != bill.transporter_id:
            ln.match_status = LineMatch.OTHER_TRANSPORTER
            problems += 1
        elif ln.billed_amount is None:
            ln.match_status = LineMatch.UNBILLED
            ln.variance = None
        else:
            ln.variance = money(ln.billed_amount - (inv.freight_amount or 0))
            ln.match_status = LineMatch.MATCHED if abs(ln.variance) <= tol_amt else LineMatch.VARIANCE
            if ln.match_status == LineMatch.VARIANCE:
                problems += 1
        calc_total += inv.freight_amount or 0
        if ln.match_status in (LineMatch.MATCHED, LineMatch.VARIANCE, LineMatch.UNBILLED):
            inv.transporter_bill_id = bill.id
            inv.actual_freight = ln.billed_amount
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


def approve(db: Session, user: User, bill: TransporterBill, remarks: str | None) -> None:
    if not user.can("recon.approve"):
        raise BusinessError("Not authorised to approve bills")
    if bill.status not in (BillStatus.MATCHED, BillStatus.VARIANCE):
        raise BusinessError("Only MATCHED or VARIANCE bills can be approved")
    if bill.created_by_id == user.id and not cfg.get(db, "recon.allow_self_approval"):
        raise BusinessError("Maker-checker: the person who entered the bill cannot approve it")
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
