"""Invoice-level rules: freight application, status derivation, manual overrides (with approval)."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ExceptionCode, Invoice, InvoiceOverride, InvoiceStatus, OverrideField, OverrideStatus, RateLevel, User,
    utcnow,
)
from app.services import audit
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from app.services.freight_engine import calc_freight, find_rate
from app.services.utils import money, parse_decimal, parse_int

MODULE = "INVOICE"


def _pending_override(db: Session, inv: Invoice) -> InvoiceOverride | None:
    return db.scalar(select(InvoiceOverride).where(InvoiceOverride.invoice_id == inv.id,
                                                   InvoiceOverride.status == OverrideStatus.PENDING))


def company_rates(db: Session) -> bool:
    """True when freight comes from the principal rate card at import (v1.2); False when the transporter rate
    card prices each invoice on the gatepass (v1.3 default)."""
    return cfg.get(db, "freight.source") == "COMPANY"


def refresh_status(db: Session, inv: Invoice) -> str:
    """Derive the pre-slip status from the invoice's data. Post-slip statuses are left untouched."""
    if inv.status not in InvoiceStatus.PRE_SLIP:
        return inv.status
    code = reason = None
    if inv.id and _pending_override(db, inv):
        code = ExceptionCode.OVERRIDE_PENDING
    elif inv.exception_code in (ExceptionCode.AREA_AMBIGUOUS, ExceptionCode.AREA_MISMATCH):
        code = inv.exception_code  # stays until the operator chooses the delivery location (customer_area_service)
    elif inv.boxes is None or inv.boxes <= 0:
        code = ExceptionCode.BOXES_MISSING
    elif inv.freight_amount is None and company_rates(db):
        code = inv.exception_code if inv.exception_code in (ExceptionCode.RATE_CONFLICT,) else ExceptionCode.RATE_MISSING
    if code:
        reason = inv.exception_reason if code == inv.exception_code and inv.exception_reason else ExceptionCode.LABELS[code]
        inv.exception_code = code
        inv.exception_reason = reason
        inv.status = InvoiceStatus.RATE_MISSING if code == ExceptionCode.RATE_MISSING else InvoiceStatus.EXCEPTION
    else:
        inv.exception_code = None
        inv.exception_reason = None
        inv.status = InvoiceStatus.VERIFIED if inv.verified_at else InvoiceStatus.PENDING
    return inv.status


def apply_freight(db: Session, inv: Invoice, user: User | None = None, *, reason: str | None = None,
                  audit_it: bool = False, force_lookup: bool = False) -> None:
    """Look up the applicable rate and (re)compute freight. Never touches slipped/dispatched/cancelled invoices
    and never re-prices invoices whose freight was set by an authorised manual override (unless forced)."""
    if inv.status not in InvoiceStatus.PRE_SLIP:
        return
    if not company_rates(db) and inv.freight_basis == "AUTO":
        refresh_status(db, inv)  # priced from the transporter rate card when the gatepass is made
        return
    if inv.freight_basis != "AUTO" and not force_lookup:
        inv.freight_amount = calc_freight(inv.boxes, inv.rate_per_box) if inv.freight_basis == "MANUAL_RATE" \
            else inv.freight_amount
        refresh_status(db, inv)
        return
    old = (inv.rate_per_box, inv.freight_amount, inv.rate_level)
    lk = find_rate(db, company_id=inv.company_id, cfa_id=inv.cfa_id, customer_id=inv.customer_id,
                   location_id=inv.location_id, on_date=inv.invoice_date)
    if lk.found:
        inv.freight_rate_id = lk.rate.id
        inv.rate_per_box = lk.rate.rate_per_box
        inv.rate_level = lk.level
        inv.freight_amount = calc_freight(inv.boxes, lk.rate.rate_per_box)
        inv.exception_code = None
        inv.exception_reason = None
    else:
        inv.freight_rate_id = None
        inv.rate_per_box = None
        inv.rate_level = None
        inv.freight_amount = None
        inv.exception_code = lk.code
        inv.exception_reason = lk.message
    inv.freight_basis = "AUTO"
    inv.freight_calculated_at = utcnow()
    if (inv.rate_per_box, inv.freight_amount) != old[:2]:
        inv.verified_at = None
        inv.verified_by_id = None
    refresh_status(db, inv)
    if audit_it and (old[0] != inv.rate_per_box or old[1] != inv.freight_amount):
        audit.log(db, user, "FREIGHT_RECALCULATED", MODULE, record_type="Invoice", record_id=inv.id,
                  ref=inv.invoice_no, field="rate_per_box / freight",
                  old=f"{old[0] if old[0] is not None else '-'} / {old[1] if old[1] is not None else '-'}",
                  new=f"{inv.rate_per_box if inv.rate_per_box is not None else '-'} / "
                      f"{inv.freight_amount if inv.freight_amount is not None else '-'}",
                  reason=reason, details={"trace": lk.trace, "level": lk.level})


def recalculate(db: Session, user: User, inv: Invoice, reason: str) -> None:
    if not user.can("invoice.recalculate"):
        raise BusinessError("Not authorised to recalculate freight")
    if inv.status not in InvoiceStatus.PRE_SLIP:
        raise BusinessError("Freight is frozen once the dispatch slip is generated")
    if not reason or len(reason.strip()) < 3:
        raise BusinessError("Reason is required")
    if not company_rates(db):
        raise BusinessError("Freight comes from the transporter rate card when the gatepass is made - nothing to "
                            "recalculate here")
    apply_freight(db, inv, user, reason=reason, audit_it=True, force_lookup=True)


# ----------------------------------------------------------------------------- overrides
def _parse_override_value(field: str, value) -> Decimal | int:
    try:
        if field == OverrideField.BOXES:
            v = parse_int(value)
            if v is None or v <= 0 or v > 100000:
                raise ValueError
            return v
        v = parse_decimal(value)
        if v is None or v < 0:
            raise ValueError
        return money(v)
    except ValueError as e:
        raise BusinessError(f"Invalid value for {OverrideField.LABELS[field]}") from e


def _current_value(inv: Invoice, field: str):
    return {OverrideField.BOXES: inv.boxes, OverrideField.RATE: inv.rate_per_box,
            OverrideField.FREIGHT: inv.freight_amount}[field]


def request_override(db: Session, user: User, inv: Invoice, field: str, new_value, reason: str) -> InvoiceOverride:
    if field not in OverrideField.LABELS:
        raise BusinessError("Unknown override field")
    if inv.status not in InvoiceStatus.PRE_SLIP:
        raise BusinessError("Values are locked after slip generation. Void the slip first (authorised users).")
    if not reason or len(reason.strip()) < 5:
        raise BusinessError("A reason (min 5 characters) is required for every manual change")
    if _pending_override(db, inv):
        raise BusinessError("Another change request is already awaiting approval for this invoice")
    value = _parse_override_value(field, new_value)
    old = _current_value(inv, field)
    if old is not None and str(old) == str(value):
        raise BusinessError("New value is the same as the current value")

    initial_entry = field == OverrideField.BOXES and inv.boxes is None
    direct = user.can("override.direct")
    if field == OverrideField.BOXES and not direct:
        if not (user.can("override.request") or user.can("dispatch.operate")):
            raise BusinessError("Not authorised to change boxes")
        if initial_entry or not cfg.get(db, "override.boxes_requires_approval"):
            direct = True
    if field in (OverrideField.RATE, OverrideField.FREIGHT) and not direct and not user.can("override.request"):
        raise BusinessError("Not authorised to request freight overrides")

    ov = InvoiceOverride(invoice_id=inv.id, field=field, old_value=None if old is None else str(old),
                         new_value=str(value), reason=reason.strip(), requested_by_id=user.id,
                         is_initial_entry=initial_entry)
    db.add(ov)
    if direct:
        ov.status = OverrideStatus.APPLIED
        ov.decided_by_id = user.id
        ov.decided_at = utcnow()
        ov.decision_remarks = "Initial entry (not received from ERP)" if initial_entry else "Applied directly by authorised user"
        _apply(db, inv, field, value)
        audit.log(db, user, "BOXES_ENTERED" if initial_entry else "OVERRIDE_APPLIED", "OVERRIDE",
                  record_type="Invoice", record_id=inv.id, ref=inv.invoice_no, field=field, old=old, new=value,
                  reason=reason)
    else:
        db.flush()
        audit.log(db, user, "OVERRIDE_REQUESTED", "OVERRIDE", record_type="Invoice", record_id=inv.id,
                  ref=inv.invoice_no, field=field, old=old, new=value, reason=reason)
    db.flush()
    refresh_status(db, inv)
    return ov


def _apply(db: Session, inv: Invoice, field: str, value) -> None:
    if field == OverrideField.BOXES:
        was_missing = inv.boxes is None
        inv.boxes = int(value)
        inv.boxes_source = "MANUAL" if was_missing else "OVERRIDE"
        if inv.freight_basis == "MANUAL_AMOUNT":
            pass
        elif inv.rate_per_box is not None:
            inv.freight_amount = calc_freight(inv.boxes, inv.rate_per_box)  # keep the frozen rate
        else:
            apply_freight(db, inv)  # rate was missing - try lookup now that boxes exist
    elif field == OverrideField.RATE:
        inv.rate_per_box = money(value)
        inv.rate_level = RateLevel.MANUAL
        inv.freight_basis = "MANUAL_RATE"
        inv.freight_amount = calc_freight(inv.boxes, inv.rate_per_box)
        inv.exception_code = None
        inv.exception_reason = None
    elif field == OverrideField.FREIGHT:
        inv.freight_amount = money(value)
        inv.freight_basis = "MANUAL_AMOUNT"
        if inv.rate_per_box is None:
            inv.rate_level = RateLevel.MANUAL
            if inv.boxes:
                inv.rate_per_box = money(Decimal(value) / inv.boxes)
        inv.exception_code = None
        inv.exception_reason = None
    inv.freight_calculated_at = utcnow()
    inv.verified_at = None
    inv.verified_by_id = None


def decide_override(db: Session, user: User, ov: InvoiceOverride, approve: bool, remarks: str | None) -> None:
    if ov.status != OverrideStatus.PENDING:
        raise BusinessError("This request has already been decided")
    if not user.can("override.approve"):
        raise BusinessError("Not authorised to approve overrides")
    if ov.requested_by_id == user.id and not cfg.get(db, "override.allow_self_approval"):
        raise BusinessError("Maker-checker: you cannot approve your own request")
    inv = db.get(Invoice, ov.invoice_id)
    if inv.status not in InvoiceStatus.PRE_SLIP:
        raise BusinessError("Invoice is no longer editable")
    if not approve and (not remarks or len(remarks.strip()) < 3):
        raise BusinessError("Rejection remarks are required")
    ov.status = OverrideStatus.APPROVED if approve else OverrideStatus.REJECTED
    ov.decided_by_id = user.id
    ov.decided_at = utcnow()
    ov.decision_remarks = remarks
    db.flush()
    if approve:
        current = _current_value(inv, ov.field)
        _apply(db, inv, ov.field, _parse_override_value(ov.field, ov.new_value))
        audit.log(db, user, "OVERRIDE_APPROVED", "OVERRIDE", record_type="Invoice", record_id=inv.id,
                  ref=inv.invoice_no, field=ov.field, old=current, new=ov.new_value,
                  reason=f"Request #{ov.id}: {ov.reason}" + (f" | Approver: {remarks}" if remarks else ""))
    else:
        audit.log(db, user, "OVERRIDE_REJECTED", "OVERRIDE", record_type="Invoice", record_id=inv.id,
                  ref=inv.invoice_no, field=ov.field, old=ov.old_value, new=ov.new_value, reason=remarks)
    refresh_status(db, inv)
