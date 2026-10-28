"""Garage Pawati (v1.7): the handover slip given to the transporter that receives goods from our local auto.

One slip per (receiving transporter, customer, company) on a local-auto trip - the owner's yellow slip: No., Date, Garage,
To, Cartons, Bill No., Amount, Freight, Signature. It stays with the transporter; the CTDMS register is the depot's record.
Numbers are plain integers per depot (sequence PAWATI:<depot id>; the Administrator may set the starting number).
Pawatis travel with the auto, so a trip cannot leave until each is printed. A cancelled Pawati is made again under a new number.
"""
from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import (
    CFA, DispatchAllocation, DispatchSlip, GaragePawati, GaragePawatiLine, Invoice, PawatiPrintLog, PawatiStatus, Sequence,
    User, utcnow,
)
from app.models.invoice import DispatchMode, PickupMode
from app.services import audit
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from app.services.utils import local_now, money, next_sequence

MODULE = "PAWATI"


def applies(slip: DispatchSlip) -> bool:
    """Pawatis are made on the local-auto path: a transporter trip whose goods reach the transporters by a local auto."""
    return slip.dispatch_mode == DispatchMode.TRANSPORTER and slip.pickup_mode == PickupMode.OUTSIDE_AUTO


def seq_name(cfa_id: int) -> str:
    return f"PAWATI:{cfa_id}"


def _build(db: Session, user: User | None, slip: DispatchSlip, allocs: list[DispatchAllocation], *,
           supersedes: GaragePawati | None = None) -> list[GaragePawati]:
    groups: "OrderedDict[tuple, list[DispatchAllocation]]" = OrderedDict()
    inv_cache: dict[int, Invoice] = {}
    for a in allocs:
        inv = inv_cache.setdefault(a.invoice_id, db.get(Invoice, a.invoice_id))
        groups.setdefault((a.receiving_transporter_id, inv.customer_id, inv.company_id), []).append(a)
    cfa = db.get(CFA, slip.cfa_id)
    out = []
    for (tid, cust_id, co_id), items in groups.items():
        first = inv_cache[items[0].invoice_id]
        total_by_inv = {i: sum(x.cases for x in db.scalars(select(DispatchAllocation).where(DispatchAllocation.invoice_id == i,
                                                                                             DispatchAllocation.slip_id == slip.id)))
                        for i in {x.invoice_id for x in items}}
        amount = sum((money(inv_cache[a.invoice_id].sales_value or 0) * a.cases / (total_by_inv[a.invoice_id] or 1)
                      for a in items), Decimal("0"))
        no = next_sequence(db, seq_name(slip.cfa_id))
        p = GaragePawati(
            cfa_id=slip.cfa_id, pawati_no=no, slip_id=slip.id, receiving_transporter_id=tid, customer_id=cust_id,
            location_id=first.location_id, company_id=co_id, cartons=sum(a.cases for a in items),
            bill_nos=", ".join(dict.fromkeys(inv_cache[a.invoice_id].invoice_no for a in items)),
            amount=money(amount), freight=money(sum((a.freight for a in items), Decimal("0"))),
            supersedes_id=supersedes.id if supersedes else None, generated_by_id=user.id if user else None)
        tr = items[0].transporter
        p.snapshot = {
            "no": no, "date": slip.dispatch_date.strftime("%d-%m-%Y") if slip.dispatch_date else local_now().strftime("%d-%m-%Y"),
            "firm": cfg.get(db, "org.name"), "depot": cfa.name if cfa else "", "depot_address": cfa.address if cfa else "",
            "depot_phone": cfa.contact_phone if cfa else "", "company": first.company.name, "company_code": first.company.code,
            "garage": tr.name, "garage_city": tr.city or "", "to": first.customer.name,
            "to_place": (first.location.name if first.location else ""), "gatepass": slip.dispatch_no,
            "auto": slip.outside_auto.name if slip.outside_auto else "", "vehicle": slip.vehicle_no or ""}
        db.add(p)
        db.flush()
        for a in items:
            db.add(GaragePawatiLine(pawati_id=p.id, allocation_id=a.id, invoice_id=a.invoice_id, cases=a.cases))
        audit.log(db, user, "PAWATI_GENERATED", MODULE, record_type="GaragePawati", record_id=p.id,
                  ref=f"{cfa.code if cfa else ''}/{no}", details={"gatepass": slip.dispatch_no, "garage": tr.code,
                                                                  "cartons": p.cartons, "bills": p.bill_nos})
        out.append(p)
    return out


def generate(db: Session, user: User | None, slip: DispatchSlip) -> list[GaragePawati]:
    if not applies(slip):
        return []
    allocs = list(db.scalars(select(DispatchAllocation).where(DispatchAllocation.slip_id == slip.id)
                             .order_by(DispatchAllocation.id)))
    return _build(db, user, slip, allocs)


def active(db: Session, slip: DispatchSlip) -> list[GaragePawati]:
    return list(db.scalars(select(GaragePawati).where(GaragePawati.slip_id == slip.id,
                                                      GaragePawati.status != PawatiStatus.CANCELLED)
                           .order_by(GaragePawati.pawati_no)))


def gate_clear(db: Session, slip: DispatchSlip) -> list[str]:
    """Pawatis travel with the auto: every one of them must be printed before the trip leaves."""
    if not applies(slip):
        return []
    open_ = [p for p in active(db, slip) if p.print_count == 0]
    if not open_:
        return []
    return [f"Garage Pawati not printed: {', '.join(str(p.pawati_no) for p in open_)} - print them before the auto leaves"]


def cancel_for_slip(db: Session, user: User | None, slip: DispatchSlip, reason: str) -> int:
    n = 0
    for p in active(db, slip):
        p.status = PawatiStatus.CANCELLED
        p.cancel_reason, p.cancelled_at, p.cancelled_by_id = reason, utcnow(), user.id if user else None
        n += 1
    return n


def cancel(db: Session, user: User, p: GaragePawati, reason: str, *, regenerate: bool = True) -> GaragePawati | None:
    if not user.can("pawati.cancel"):
        raise BusinessError("You are not authorised to cancel a Garage Pawati")
    if p.status == PawatiStatus.CANCELLED:
        raise BusinessError("Already cancelled")
    slip = db.get(DispatchSlip, p.slip_id)
    if slip.gate_out_at or slip.is_void:
        raise BusinessError("The trip has already left (or is void) - its Pawatis can no longer be changed")
    if len((reason or "").strip()) < 5:
        raise BusinessError("Give a reason (at least 5 characters)")
    p.status = PawatiStatus.CANCELLED
    p.cancel_reason, p.cancelled_at, p.cancelled_by_id = reason.strip(), utcnow(), user.id
    audit.log(db, user, "PAWATI_CANCELLED", MODULE, record_type="GaragePawati", record_id=p.id,
              ref=f"{p.cfa.code}/{p.pawati_no}", reason=reason)
    if not regenerate:
        return None
    allocs = [db.get(DispatchAllocation, l.allocation_id) for l in p.lines]
    new = _build(db, user, slip, allocs, supersedes=p)
    return new[0] if new else None


def print_sheet(db: Session, user: User, pawatis: list[GaragePawati], reason: str | None = None) -> list[GaragePawati]:
    """Record a print of these Pawatis (first print or reprint). Reprint = a reason, audited."""
    if not user.can("pawati.print"):
        raise BusinessError("You are not authorised to print Garage Pawatis")
    reprint = any(p.print_count for p in pawatis)
    if reprint and len((reason or "").strip()) < 5:
        raise BusinessError("Give a reason (at least 5 characters) for reprinting a Garage Pawati")
    for p in pawatis:
        if p.status == PawatiStatus.CANCELLED:
            raise BusinessError(f"Pawati {p.pawati_no} is cancelled")
        p.print_count += 1
        p.status = PawatiStatus.PRINTED
        db.add(PawatiPrintLog(pawati_id=p.id, print_no=p.print_count, is_reprint=p.print_count > 1,
                              reason=(reason or "").strip() or None, printed_by_id=user.id))
        audit.log(db, user, "PAWATI_REPRINTED" if p.print_count > 1 else "PAWATI_PRINTED", MODULE,
                  record_type="GaragePawati", record_id=p.id, ref=f"{p.cfa.code}/{p.pawati_no}", reason=reason)
    db.flush()
    return pawatis


def receive(db: Session, user: User, p: GaragePawati, note: str | None = None, *, via: str = "MANUAL") -> None:
    if via == "MANUAL" and not user.can("pawati.receive"):
        raise BusinessError("You are not authorised to record receipt of a Garage Pawati")
    if p.status == PawatiStatus.CANCELLED:
        raise BusinessError("This Pawati is cancelled")
    slip = db.get(DispatchSlip, p.slip_id)
    if not slip.gate_out_at:
        raise BusinessError("The trip has not left yet - receipt can be recorded after gate-out")
    if p.received_at:
        raise BusinessError("Receipt is already recorded")
    p.received_at, p.received_by_id, p.received_via = utcnow(), user.id, via
    p.received_note = (note or "").strip() or None
    audit.log(db, user, "PAWATI_RECEIVED", MODULE, record_type="GaragePawati", record_id=p.id,
              ref=f"{p.cfa.code}/{p.pawati_no}", details={"via": via, "note": p.received_note})


def receive_by_lr(db: Session, user: User, slip: DispatchSlip, invoice_ids: set[int], transporter_id: int | None) -> int:
    """An LR entered AFTER gate-out confirms the receiving transporter has the goods: their Pawati is received. (An LR
    assigned from an LR book at gatepass generation does not count - it exists before the auto leaves.)"""
    if not slip.gate_out_at or not applies(slip):
        return 0
    n = 0
    for p in active(db, slip):
        if p.received_at or (transporter_id and p.receiving_transporter_id != transporter_id):
            continue
        if {l.invoice_id for l in p.lines} & invoice_ids:
            receive(db, user, p, "LR entered", via="LR")
            n += 1
    return n


# ----------------------------------------------------------------------------- starting number (Administration)
def depot_state(db: Session, cfa: CFA) -> dict:
    nxt = db.scalar(select(Sequence.next_value).where(Sequence.name == seq_name(cfa.id))) or 1
    issued = db.scalar(select(func.count(GaragePawati.id)).where(GaragePawati.cfa_id == cfa.id)) or 0
    top = db.scalar(select(func.max(GaragePawati.pawati_no)).where(GaragePawati.cfa_id == cfa.id)) or 0
    return {"cfa": cfa, "next": nxt, "issued": issued, "highest": top}


def set_start(db: Session, user: User, cfa: CFA, start: int, reason: str | None = None) -> None:
    if not user.can("admin.settings"):
        raise BusinessError("You are not authorised to change Pawati numbering")
    st = depot_state(db, cfa)
    if start < 1 or start > 99_999_999:
        raise BusinessError("The starting number must be a whole number from 1")
    if st["issued"]:
        if len((reason or "").strip()) < 5:
            raise BusinessError("Pawatis were already issued for this depot - give a reason to change the numbering")
        if start <= st["highest"]:
            raise BusinessError(f"The next number must be above the highest issued ({st['highest']})")
    seq = db.get(Sequence, seq_name(cfa.id))
    if seq is None:
        db.add(Sequence(name=seq_name(cfa.id), next_value=start))
    else:
        db.execute(update(Sequence).where(Sequence.name == seq_name(cfa.id)).values(next_value=start))
    audit.log(db, user, "PAWATI_START_SET", MODULE, record_type="CFA", record_id=cfa.id, ref=cfa.code,
              field="next_pawati_no", old=st["next"], new=start, reason=reason)
