"""LR (builty) books (v1.7) - only for the few transporters that give us their own book (`transporters.uses_lr_book`).

At the start of every book the staff import its LR range (first / last number, optional prefix). When a gatepass is made,
CTDMS assigns the next free LR number to each consignment (receiving transporter + customer + area) automatically; staff can
change it (reason, logged - the old number is marked VOID). An LR assigned here does NOT count as the transporter's receipt:
only an LR entered after the goods have left does (see pawati_service.receive_by_lr). Every other transporter is unchanged.
"""
from __future__ import annotations

import re

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import DispatchAllocation, DispatchSlip, Invoice, LRBook, LRNumber, Transporter, User, utcnow
from app.services import audit
from app.services.errors import BusinessError

MODULE = "LR_BOOK"
MAX_RANGE = 5000


def _split(no: str) -> tuple[str, int, int]:
    m = re.fullmatch(r"(.*?)(\d+)", (no or "").strip())
    if not m:
        raise BusinessError(f"LR number '{no}' must end in digits")
    return m.group(1), int(m.group(2)), len(m.group(2))


def import_range(db: Session, user: User, transporter: Transporter, label: str, first: str, last: str,
                 received_on=None) -> LRBook:
    if not user.can("lr_book.manage"):
        raise BusinessError("You are not authorised to import LR books")
    if not transporter.uses_lr_book:
        raise BusinessError(f"{transporter.name} is not marked as giving its own LR book (Master Settings > Transporters)")
    p1, a, w1 = _split(first)
    p2, b, _w2 = _split(last)
    if p1 != p2:
        raise BusinessError("First and last LR number must have the same prefix")
    if b < a:
        raise BusinessError("The last LR number is before the first")
    if b - a + 1 > MAX_RANGE:
        raise BusinessError(f"A book has at most {MAX_RANGE} numbers - import it in parts")
    numbers = [f"{p1}{str(n).zfill(w1)}" for n in range(a, b + 1)]
    taken = list(db.scalars(select(LRNumber.lr_no).where(LRNumber.transporter_id == transporter.id,
                                                         LRNumber.lr_no.in_(numbers)).limit(5)))
    if taken:
        raise BusinessError(f"LR number(s) {', '.join(taken)} are already in a book of {transporter.name} - "
                            "check the range (a number is never imported twice)")
    book = LRBook(transporter_id=transporter.id, label=(label or f"{first}-{last}").strip()[:80], first_no=numbers[0],
                  last_no=numbers[-1], count=len(numbers), received_on=received_on, created_by_id=user.id)
    db.add(book)
    db.flush()
    for n in numbers:
        db.add(LRNumber(transporter_id=transporter.id, lr_no=n, book_id=book.id))
    db.flush()
    audit.log(db, user, "LR_BOOK_IMPORTED", MODULE, record_type="LRBook", record_id=book.id, ref=transporter.code,
              details={"label": book.label, "first": numbers[0], "last": numbers[-1], "count": len(numbers)})
    return book


def stats(db: Session, transporter_id: int) -> dict:
    out = {"AVAILABLE": 0, "USED": 0, "VOID": 0}
    for st, n in db.execute(select(LRNumber.status, func.count()).where(LRNumber.transporter_id == transporter_id)
                            .group_by(LRNumber.status)):
        out[st] = n
    return out


def _take_next(db: Session, transporter_id: int, allocation_id: int) -> str | None:
    """Claim the lowest free number with a conditional UPDATE, so two operators can never get the same one."""
    for _ in range(5):
        row = db.execute(select(LRNumber.id, LRNumber.lr_no).where(
            LRNumber.transporter_id == transporter_id, LRNumber.status == "AVAILABLE").order_by(LRNumber.id).limit(1)).first()
        if row is None:
            return None
        got = db.execute(update(LRNumber).where(LRNumber.id == row[0], LRNumber.status == "AVAILABLE")
                         .values(status="USED", allocation_id=allocation_id, used_at=utcnow())).rowcount
        if got:
            return row[1]
    return None


def assign_for_slip(db: Session, user: User, slip: DispatchSlip) -> list[str]:
    """Give each consignment of a new gatepass the next LR of its transporter's book. Returns warnings (book empty)."""
    warnings: list[str] = []
    allocs = list(db.scalars(select(DispatchAllocation).where(DispatchAllocation.slip_id == slip.id)
                             .order_by(DispatchAllocation.id)))
    done: dict[tuple, str] = {}
    for a in allocs:
        t = db.get(Transporter, a.receiving_transporter_id)
        if not t.uses_lr_book:
            continue
        inv = db.get(Invoice, a.invoice_id)
        key = (t.id, inv.customer_id, inv.location_id)
        if key not in done:
            no = _take_next(db, t.id, a.id)
            if no is None:
                warnings.append(f"No LR numbers left in {t.name}'s book - import the next book (LR Books)")
                audit.log(db, user, "LR_BOOK_EMPTY", MODULE, record_type="DispatchSlip", record_id=slip.id,
                          ref=slip.dispatch_no, details={"transporter": t.code})
                done[key] = ""
                continue
            done[key] = no
            audit.log(db, user, "LR_AUTO_ASSIGNED", MODULE, record_type="DispatchSlip", record_id=slip.id,
                      ref=slip.dispatch_no, new=no, details={"transporter": t.code, "customer": inv.customer.code})
        no = done[key]
        if no:
            a.lr_no = no
            if inv.transporter_id == t.id and not inv.lr_no:
                inv.lr_no = no  # shown and used like any LR; entering it later (after gate-out) still counts as receipt
    return warnings


def release_for_slip(db: Session, slip: DispatchSlip) -> int:
    """A voided gatepass gives its unused LR numbers back to the book."""
    ids = [a.id for a in db.scalars(select(DispatchAllocation).where(DispatchAllocation.slip_id == slip.id))]
    if not ids:
        return 0
    n = db.execute(update(LRNumber).where(LRNumber.allocation_id.in_(ids), LRNumber.status == "USED")
                   .values(status="AVAILABLE", allocation_id=None, used_at=None)).rowcount
    return n


def on_lr_changed(db: Session, user: User, inv: Invoice, old: str | None, new: str | None, reason: str | None) -> None:
    """Staff typed a different LR than the book's: the book's number is VOID (it was not used), a book number typed by
    hand is taken. Both logged."""
    on_lr_changed_t(db, user, inv.transporter_id, old, new, reason)


def on_lr_changed_t(db: Session, user: User, transporter_id: int | None, old: str | None, new: str | None,
                    reason: str | None) -> None:
    if not transporter_id:
        return
    t = db.get(Transporter, transporter_id)
    if not t or not t.uses_lr_book:
        return
    if old and old != new:
        row = db.scalar(select(LRNumber).where(LRNumber.transporter_id == t.id, LRNumber.lr_no == old,
                                               LRNumber.status == "USED"))
        if row:
            row.status, row.void_reason = "VOID", f"Changed to {new or 'blank'}: {reason or ''}".strip()
            audit.log(db, user, "LR_BOOK_NUMBER_VOID", MODULE, record_type="LRNumber", record_id=row.id, ref=old,
                      reason=row.void_reason)
    if new:
        row = db.scalar(select(LRNumber).where(LRNumber.transporter_id == t.id, LRNumber.lr_no == new))
        if row and row.status == "AVAILABLE":
            row.status, row.used_at = "USED", utcnow()
            audit.log(db, user, "LR_BOOK_NUMBER_USED", MODULE, record_type="LRNumber", record_id=row.id, ref=new)
