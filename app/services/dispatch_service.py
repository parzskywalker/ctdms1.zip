"""Dispatch workflow (state machine) with the mandatory slip controls.

PENDING -> VERIFIED -> SLIP_GENERATED -> SLIP_PRINTED -> DISPATCHED
(RATE_MISSING / EXCEPTION block verification; CANCELLED is terminal unless reinstated.)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DispatchSlip, Invoice, InvoiceStatus, SlipPrintLog, Transporter, TransporterBill, User, utcnow,
)
from app.models.recon import BillStatus
from app.services import audit
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from app.services.invoice_service import refresh_status
from app.services.utils import (
    fiscal_year, local_now, local_today, next_sequence, norm_vehicle, parse_date, parse_time,
)
from app.services import context

MODULE = "DISPATCH"
S = InvoiceStatus


def _perm(user: User, perm: str) -> None:
    if not user.can(perm):
        raise BusinessError(f"You are not authorised for this action ({perm})")


def active_slip(db: Session, inv: Invoice) -> DispatchSlip | None:
    return db.scalar(select(DispatchSlip).where(DispatchSlip.invoice_id == inv.id, DispatchSlip.is_void.is_(False))
                     .order_by(DispatchSlip.id.desc()))


# ----------------------------------------------------------------------------- details
def save_details(db: Session, user: User, inv: Invoice, *, transporter_id=None, vehicle_no=None, lr_no=None,
                 dispatch_date=None, dispatch_time=None, remarks=None) -> list[str]:
    """Save draft dispatch details. Returns list of changed fields."""
    _perm(user, "dispatch.operate")
    if inv.status in (S.DISPATCHED, S.CANCELLED):
        raise BusinessError(f"Invoice is {inv.status_label}; dispatch details are locked")
    if inv.status in (S.SLIP_GENERATED, S.SLIP_PRINTED):
        raise BusinessError("Dispatch slip already generated - details are locked. Void the slip to correct them.")

    try:
        d = parse_date(dispatch_date) if dispatch_date not in (None, "") else None
        t = parse_time(dispatch_time) if dispatch_time not in (None, "") else None
    except ValueError as e:
        raise BusinessError(str(e)) from e
    tid = int(transporter_id) if transporter_id not in (None, "", "0") else None
    if tid:
        tr = db.get(Transporter, tid)
        if not tr or not tr.is_active:
            raise BusinessError("Select an active transporter")
    values = {
        "transporter_id": tid,
        "vehicle_no": norm_vehicle(vehicle_no),
        "lr_no": (str(lr_no).strip().upper() or None) if lr_no is not None else None,
        "dispatch_date": d,
        "dispatch_time": t,
        "remarks": (remarks or "").strip() or None,
    }
    changes = audit.diff(inv, values)
    if not changes:
        return []
    for k, v in values.items():
        setattr(inv, k, v)
    tracked = {k: v for k, v in changes.items() if k in ("transporter_id", "vehicle_no", "lr_no")}
    if tracked and any(o is not None for o, _ in tracked.values()):
        audit.log_changes(db, user, "DISPATCH_DETAILS_CHANGED", MODULE, tracked, record_type="Invoice",
                          record_id=inv.id, ref=inv.invoice_no)
    else:
        audit.log(db, user, "DISPATCH_DRAFT_SAVED", MODULE, record_type="Invoice", record_id=inv.id,
                  ref=inv.invoice_no, details={k: audit._s(v[1]) for k, v in changes.items()})
    return list(changes)


# ----------------------------------------------------------------------------- verify
def verify(db: Session, user: User, inv: Invoice) -> None:
    _perm(user, "dispatch.verify")
    if inv.status == S.VERIFIED:
        return
    if inv.status == S.RATE_MISSING:
        raise BusinessError("Cannot verify: freight rate missing. Resolve the rate exception first.")
    if inv.status == S.EXCEPTION:
        raise BusinessError(f"Cannot verify: {inv.exception_reason or 'invoice has an open exception'}")
    if inv.status != S.PENDING:
        raise BusinessError(f"Cannot verify an invoice in status {inv.status_label}")
    if not inv.boxes or inv.boxes <= 0 or inv.freight_amount is None:
        raise BusinessError("Boxes and freight must be available before verification")
    inv.verified_at = utcnow()
    inv.verified_by_id = user.id
    inv.status = S.VERIFIED
    audit.log(db, user, "DISPATCH_VERIFIED", MODULE, record_type="Invoice", record_id=inv.id, ref=inv.invoice_no,
              field="status", old=S.PENDING, new=S.VERIFIED,
              details={"boxes": inv.boxes, "rate": str(inv.rate_per_box), "freight": str(inv.freight_amount)})


# ----------------------------------------------------------------------------- slip
def _slip_snapshot(db: Session, inv: Invoice, dispatch_no: str, user: User) -> dict:
    c, f, cu, lo, tr = inv.company, inv.cfa, inv.customer, inv.location, inv.transporter
    return {
        "org_name": cfg.get(db, "org.name"),
        "org_address": cfg.get(db, "org.address"),
        "footer": cfg.get(db, "slip.footer_note"),
        "dispatch_no": dispatch_no,
        "generated_at": local_now().strftime("%d-%m-%Y %H:%M"),
        "central_id": inv.central_id,
        "company": {"code": c.code, "name": c.name},
        "cfa": {"code": f.code, "name": f.name, "address": f.address, "city": f.city, "state": f.state,
                "phone": f.contact_phone},
        "invoice_no": inv.invoice_no,
        "invoice_date": inv.invoice_date.strftime("%d-%m-%Y"),
        "source_erp": inv.source_erp.code if inv.source_erp else None,
        "customer": {"code": cu.code, "erp_code": inv.customer_code, "name": cu.name,
                     "address": cu.delivery_address or cu.billing_address, "city": cu.city,
                     "contact": cu.contact_person, "phone": cu.contact_phone},
        "location": {"code": lo.code, "name": lo.name, "route": lo.route, "zone": lo.zone},
        "boxes": inv.boxes,
        "rate_per_box": str(inv.rate_per_box),
        "freight_amount": str(inv.freight_amount),
        "rate_level": inv.rate_level,
        "freight_basis": inv.freight_basis,
        "sales_value": str(inv.sales_value),
        "transporter": {"code": tr.code, "name": tr.name, "mobile": tr.mobile} if tr else None,
        "vehicle_no": inv.vehicle_no,
        "lr_no": inv.lr_no,
        "dispatch_date": inv.dispatch_date.strftime("%d-%m-%Y") if inv.dispatch_date else None,
        "dispatch_time": inv.dispatch_time.strftime("%H:%M") if inv.dispatch_time else None,
        "remarks": inv.remarks,
        "prepared_by": user.full_name,
        "checked_by": inv.verified_by.full_name if inv.verified_by else None,
    }


def slip_blockers(db: Session, inv: Invoice) -> list[str]:
    problems = []
    if inv.status in (S.SLIP_GENERATED, S.SLIP_PRINTED, S.DISPATCHED):
        return ["Slip already generated"]
    if inv.status == S.CANCELLED:
        return ["Invoice cancelled"]
    if inv.status in (S.RATE_MISSING, S.EXCEPTION):
        problems.append(inv.exception_reason or "Open exception")
    if cfg.get(db, "dispatch.require_verify_before_slip") and inv.status != S.VERIFIED:
        problems.append("Invoice must be verified first")
    if not inv.boxes:
        problems.append("Number of boxes missing")
    if inv.freight_amount is None:
        problems.append("Freight not calculated")
    if not inv.transporter_id:
        problems.append("Transporter not selected")
    if cfg.get(db, "dispatch.vehicle_mandatory") and not inv.vehicle_no:
        problems.append("Vehicle number missing")
    if cfg.get(db, "dispatch.lr_mandatory") and not inv.lr_no:
        problems.append("LR / docket number missing")
    if not inv.dispatch_date:
        problems.append("Dispatch date missing")
    elif inv.dispatch_date < inv.invoice_date:
        problems.append("Dispatch date is before invoice date")
    elif inv.dispatch_date > local_today() and not cfg.get(db, "dispatch.allow_future_date"):
        problems.append("Dispatch date cannot be in the future")
    if not inv.dispatch_time:
        problems.append("Dispatch time missing")
    return problems


def generate_slip(db: Session, user: User, inv: Invoice) -> DispatchSlip:
    _perm(user, "dispatch.operate")
    problems = slip_blockers(db, inv)
    if problems:
        raise BusinessError("Cannot generate slip: " + "; ".join(problems))
    if inv.lr_no:
        dup = db.scalar(select(Invoice).where(Invoice.lr_no == inv.lr_no, Invoice.transporter_id == inv.transporter_id,
                                              Invoice.id != inv.id, Invoice.status != S.CANCELLED,
                                              Invoice.customer_id != inv.customer_id))
        if dup:
            raise BusinessError(f"LR {inv.lr_no} of this transporter is already used for invoice {dup.invoice_no} "
                                f"(different customer)")
    prefix = cfg.get(db, "dispatch.number_prefix") or "DS"
    fy = fiscal_year(inv.dispatch_date or local_today())
    seq = next_sequence(db, f"DISPATCH:{inv.cfa.code}:{fy}")
    dispatch_no = f"{prefix}/{inv.cfa.code}/{fy.replace('-', '')[2:]}/{seq:05d}"
    slip = DispatchSlip(dispatch_no=dispatch_no, invoice_id=inv.id, generated_by_id=user.id,
                        snapshot=_slip_snapshot(db, inv, dispatch_no, user))
    db.add(slip)
    inv.dispatch_no = dispatch_no
    inv.slip_generated_at = utcnow()
    inv.slip_generated_by_id = user.id
    old = inv.status
    inv.status = S.SLIP_GENERATED
    db.flush()
    audit.log(db, user, "SLIP_GENERATED", MODULE, record_type="Invoice", record_id=inv.id, ref=inv.invoice_no,
              field="status", old=old, new=S.SLIP_GENERATED, details={"dispatch_no": dispatch_no})
    return slip


def record_print(db: Session, user: User, inv: Invoice, output: str, reason: str | None = None) -> tuple[DispatchSlip, SlipPrintLog]:
    if inv.print_count == 0:
        _perm(user, "dispatch.operate")
    slip = active_slip(db, inv)
    if not slip:
        raise BusinessError("Generate the dispatch slip first")
    if output not in ("PRINT_A4", "PRINT_A5", "PDF_A4", "PDF_A5"):
        raise BusinessError("Unknown print format")
    is_reprint = slip.print_count > 0
    if is_reprint:
        if not user.can("dispatch.reprint"):
            raise BusinessError("Not authorised to reprint dispatch slips")
        if cfg.get(db, "dispatch.reprint_reason_required") and (not reason or len(reason.strip()) < 3):
            raise BusinessError("Reason is mandatory for reprinting a dispatch slip")
    slip.print_count += 1
    inv.print_count = (inv.print_count or 0) + 1
    log = SlipPrintLog(slip_id=slip.id, invoice_id=inv.id, dispatch_no=slip.dispatch_no, print_no=slip.print_count,
                       is_reprint=is_reprint, output=output, reason=(reason or "").strip() or None,
                       printed_by_id=user.id, ip=context.request_ip.get())
    db.add(log)
    if not is_reprint:
        inv.first_printed_at = utcnow()
        inv.first_printed_by_id = user.id
        if inv.status == S.SLIP_GENERATED:
            inv.status = S.SLIP_PRINTED
    audit.log(db, user, "SLIP_REPRINTED" if is_reprint else "SLIP_PRINTED", MODULE, record_type="Invoice",
              record_id=inv.id, ref=inv.invoice_no, field="print_count", old=slip.print_count - 1,
              new=slip.print_count, reason=reason, details={"dispatch_no": slip.dispatch_no, "output": output})
    db.flush()
    return slip, log


def completion_blockers(db: Session, inv: Invoice) -> list[str]:
    rule = cfg.get(db, "dispatch.complete_requires")
    if inv.status == S.DISPATCHED:
        return ["Already dispatched"]
    if inv.status == S.CANCELLED:
        return ["Invoice cancelled"]
    if rule == "PRINTED" and inv.status != S.SLIP_PRINTED:
        return ["Dispatch Slip must be generated AND printed before completing dispatch (control: PRINTED)"]
    if rule == "GENERATED" and inv.status not in (S.SLIP_GENERATED, S.SLIP_PRINTED):
        return ["Dispatch Slip must be generated before completing dispatch (control: GENERATED)"]
    if rule == "NONE" and inv.status not in (S.VERIFIED, S.SLIP_GENERATED, S.SLIP_PRINTED):
        return ["Invoice must at least be verified"]
    return []


def complete(db: Session, user: User, inv: Invoice) -> None:
    _perm(user, "dispatch.complete")
    problems = completion_blockers(db, inv)
    if problems:
        audit.log(db, user, "COMPLETE_BLOCKED", MODULE, record_type="Invoice", record_id=inv.id, ref=inv.invoice_no,
                  reason="; ".join(problems))
        raise BusinessError("Cannot complete dispatch: " + "; ".join(problems))
    old = inv.status
    inv.status = S.DISPATCHED
    inv.completed_at = utcnow()
    inv.completed_by_id = user.id
    inv.erp_sync_status = "PENDING"
    audit.log(db, user, "DISPATCH_COMPLETED", MODULE, record_type="Invoice", record_id=inv.id, ref=inv.invoice_no,
              field="status", old=old, new=S.DISPATCHED, details={"dispatch_no": inv.dispatch_no})


def void_slip(db: Session, user: User, inv: Invoice, reason: str) -> None:
    _perm(user, "dispatch.void_slip")
    if inv.status not in (S.SLIP_GENERATED, S.SLIP_PRINTED):
        raise BusinessError("Only a generated/printed slip of an open dispatch can be voided")
    if not reason or len(reason.strip()) < 5:
        raise BusinessError("Reason (min 5 characters) is required to void a slip")
    slip = active_slip(db, inv)
    if slip:
        slip.is_void = True
        slip.voided_at = utcnow()
        slip.voided_by_id = user.id
        slip.void_reason = reason
    old = inv.status
    inv.dispatch_no = None
    inv.slip_generated_at = None
    inv.print_count = 0
    inv.first_printed_at = None
    inv.status = S.VERIFIED if inv.verified_at else S.PENDING
    audit.log(db, user, "SLIP_VOIDED", MODULE, record_type="Invoice", record_id=inv.id, ref=inv.invoice_no,
              field="status", old=old, new=inv.status, reason=reason,
              details={"dispatch_no": slip.dispatch_no if slip else None})
    refresh_status(db, inv)


def cancel(db: Session, user: User, inv: Invoice, reason: str) -> None:
    _perm(user, "dispatch.cancel")
    if inv.status == S.CANCELLED:
        raise BusinessError("Already cancelled")
    if inv.status == S.DISPATCHED and not user.can("dispatch.cancel_dispatched"):
        raise BusinessError("Only an administrator can cancel a completed dispatch")
    if not reason or len(reason.strip()) < 5:
        raise BusinessError("Cancellation reason (min 5 characters) is mandatory")
    if inv.transporter_bill_id:
        bill = db.get(TransporterBill, inv.transporter_bill_id)
        if bill and bill.status in (BillStatus.APPROVED, BillStatus.PAID):
            raise BusinessError(f"Invoice is on approved/paid transporter bill {bill.bill_no}; cannot cancel")
    slip = active_slip(db, inv)
    if slip:
        slip.is_void = True
        slip.voided_at = utcnow()
        slip.voided_by_id = user.id
        slip.void_reason = f"Dispatch cancelled: {reason}"
    old = inv.status
    inv.status = S.CANCELLED
    inv.cancelled_at = utcnow()
    inv.cancelled_by_id = user.id
    inv.cancel_reason = reason.strip()
    audit.log(db, user, "DISPATCH_CANCELLED", MODULE, record_type="Invoice", record_id=inv.id, ref=inv.invoice_no,
              field="status", old=old, new=S.CANCELLED, reason=reason,
              details={"dispatch_no": slip.dispatch_no if slip else None})


def reinstate(db: Session, user: User, inv: Invoice, reason: str) -> None:
    _perm(user, "dispatch.cancel")
    if not user.can("override.approve"):
        raise BusinessError("Only an approver can reinstate a cancelled invoice")
    if inv.status != S.CANCELLED:
        raise BusinessError("Only cancelled invoices can be reinstated")
    if not reason or len(reason.strip()) < 5:
        raise BusinessError("Reason is mandatory")
    inv.status = S.PENDING
    inv.dispatch_no = None
    inv.slip_generated_at = None
    inv.print_count = 0
    inv.first_printed_at = None
    inv.completed_at = None
    inv.verified_at = None
    inv.cancelled_at = None
    refresh_status(db, inv)
    audit.log(db, user, "DISPATCH_REINSTATED", MODULE, record_type="Invoice", record_id=inv.id, ref=inv.invoice_no,
              field="status", old=S.CANCELLED, new=inv.status, reason=reason)


# ----------------------------------------------------------------------------- UI helpers
@dataclass
class Actions:
    can_edit: bool
    can_verify: bool
    can_generate: bool
    can_print: bool
    can_reprint: bool
    can_complete: bool
    can_void: bool
    can_cancel: bool
    can_reinstate: bool
    can_override: bool
    slip_blockers: list[str]
    complete_blockers: list[str]


def actions_for(db: Session, user: User, inv: Invoice) -> Actions:
    pre = inv.status in S.PRE_SLIP
    slipped = inv.status in (S.SLIP_GENERATED, S.SLIP_PRINTED)
    sb = slip_blockers(db, inv) if pre else []
    cb = completion_blockers(db, inv) if inv.status not in (S.DISPATCHED, S.CANCELLED) else []
    return Actions(
        can_edit=pre and user.can("dispatch.operate"),
        can_verify=inv.status == S.PENDING and user.can("dispatch.verify"),
        can_generate=pre and user.can("dispatch.operate"),
        can_print=(slipped or inv.status == S.DISPATCHED) and inv.print_count == 0 and user.can("dispatch.operate"),
        can_reprint=inv.print_count > 0 and inv.status != S.CANCELLED and user.can("dispatch.reprint"),
        can_complete=inv.status not in (S.DISPATCHED, S.CANCELLED) and user.can("dispatch.complete"),
        can_void=slipped and user.can("dispatch.void_slip"),
        can_cancel=(inv.status != S.CANCELLED and user.can("dispatch.cancel")
                    and (inv.status != S.DISPATCHED or user.can("dispatch.cancel_dispatched"))),
        can_reinstate=inv.status == S.CANCELLED and user.can("dispatch.cancel") and user.can("override.approve"),
        can_override=pre and (user.can("override.request") or user.can("override.direct")),
        slip_blockers=sb,
        complete_blockers=cb,
    )


def default_dispatch_datetime() -> tuple[date, time]:
    now = local_now()
    return now.date(), now.time().replace(second=0, microsecond=0)
