"""Freight-rate master rules: validation, versioning (effective dating) and maker-checker approval."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import (
    CFA, Company, Customer, ExceptionCode, FreightRate, Invoice, InvoiceStatus, Location, RateStatus, User,
    utcnow,
)
from app.services import audit
from app.services import settings_service as cfg
from app.services.errors import BusinessError

MODULE = "FREIGHT_RATE"


def describe(rate: FreightRate) -> str:
    parts = [rate.company.code if rate.company else f"co#{rate.company_id}"]
    parts.append(rate.cfa.code if rate.cfa else "ANY-CFA")
    if rate.customer:
        parts.append(rate.customer.code)
    parts.append(rate.location.name if rate.location else ("DEFAULT" if rate.is_default else "-"))
    return " / ".join(parts)


def _same_key(stmt, company_id, cfa_id, customer_id, location_id, is_default):
    def eq(col, v):
        return col.is_(None) if v is None else col == v
    return stmt.where(FreightRate.company_id == company_id, eq(FreightRate.cfa_id, cfa_id),
                      eq(FreightRate.customer_id, customer_id), eq(FreightRate.location_id, location_id),
                      FreightRate.is_default.is_(bool(is_default)))


def validate(db: Session, *, company_id, cfa_id, customer_id, location_id, is_default, rate_per_box,
             effective_from, effective_to, exclude_id: int | None = None) -> None:
    if not company_id or not db.get(Company, company_id):
        raise BusinessError("Company / Principal is required", field="company_id")
    if rate_per_box is None or Decimal(rate_per_box) <= 0:
        raise BusinessError("Rate per box must be greater than zero", field="rate_per_box")
    if Decimal(rate_per_box) > Decimal("100000"):
        raise BusinessError("Rate per box looks unrealistic", field="rate_per_box")
    if not effective_from:
        raise BusinessError("Effective From date is required", field="effective_from")
    if effective_to and effective_to < effective_from:
        raise BusinessError("Effective To cannot be before Effective From", field="effective_to")
    if is_default:
        if customer_id or location_id:
            raise BusinessError("A default rate cannot be customer- or location-specific")
    else:
        if not location_id or not db.get(Location, location_id):
            raise BusinessError("Delivery location is required (or tick 'Default rate')", field="location_id")
    if customer_id:
        if not cfa_id:
            raise BusinessError("Customer-specific rates must also specify the CFA", field="cfa_id")
        if not db.get(Customer, customer_id):
            raise BusinessError("Unknown customer", field="customer_id")
    if cfa_id:
        cfa = db.get(CFA, cfa_id)
        company = db.get(Company, company_id)
        if not cfa:
            raise BusinessError("Unknown CFA", field="cfa_id")
        if cfa not in company.cfas:
            raise BusinessError(f"CFA {cfa.code} is not mapped to company {company.code}", field="cfa_id")

    base = _same_key(select(FreightRate), company_id, cfa_id, customer_id, location_id, is_default)
    if exclude_id:
        base = base.where(FreightRate.id != exclude_id)
    pending = db.scalar(base.where(FreightRate.status == RateStatus.PENDING_APPROVAL))
    if pending:
        raise BusinessError(f"Rate #{pending.id} for the same combination is already awaiting approval")
    later = db.scalar(base.where(FreightRate.status == RateStatus.APPROVED,
                                 FreightRate.effective_from >= effective_from))
    if later:
        raise BusinessError(
            f"An approved version (#{later.id}) already starts on {later.effective_from:%d-%m-%Y}. "
            "New versions must start after the latest version; history cannot be overwritten.")


def create_rate(db: Session, user: User, *, company_id, cfa_id=None, customer_id=None, location_id=None,
                is_default=False, rate_per_box, effective_from: date, effective_to: date | None = None,
                remarks=None, change_reason=None, previous_rate_id=None, auto_approve: bool = False) -> FreightRate:
    validate(db, company_id=company_id, cfa_id=cfa_id, customer_id=customer_id, location_id=location_id,
             is_default=is_default, rate_per_box=rate_per_box, effective_from=effective_from,
             effective_to=effective_to)
    rate = FreightRate(company_id=company_id, cfa_id=cfa_id or None, customer_id=customer_id or None,
                       location_id=location_id or None, is_default=bool(is_default),
                       rate_per_box=Decimal(rate_per_box).quantize(Decimal("0.01")),
                       effective_from=effective_from, effective_to=effective_to, remarks=remarks,
                       change_reason=change_reason, previous_rate_id=previous_rate_id,
                       created_by_id=user.id if user else None, status=RateStatus.PENDING_APPROVAL)
    db.add(rate)
    db.flush()
    db.refresh(rate)
    audit.log(db, user, "RATE_CREATED" if not previous_rate_id else "RATE_REVISED", MODULE,
              record_type="FreightRate", record_id=rate.id, ref=describe(rate), field="rate_per_box",
              old=None, new=rate.rate_per_box, reason=change_reason,
              details={"effective_from": effective_from.isoformat(),
                       "effective_to": effective_to.isoformat() if effective_to else None,
                       "previous_rate_id": previous_rate_id})
    if auto_approve or not cfg.get(db, "rate.require_approval"):
        approve_rate(db, user, rate, remarks="Auto-approved (approval not required)" if not auto_approve else
                     "Seed / system approval", _skip_self_check=True)
    return rate


def revise_rate(db: Session, user: User, prev: FreightRate, *, rate_per_box, effective_from: date,
                change_reason: str, effective_to: date | None = None) -> FreightRate:
    if prev.status != RateStatus.APPROVED:
        raise BusinessError("Only an approved rate can be revised")
    if not change_reason or len(change_reason.strip()) < 3:
        raise BusinessError("Reason for rate change is required")
    if effective_from <= prev.effective_from:
        raise BusinessError("New Effective From must be after the current version's Effective From")
    return create_rate(db, user, company_id=prev.company_id, cfa_id=prev.cfa_id, customer_id=prev.customer_id,
                       location_id=prev.location_id, is_default=prev.is_default, rate_per_box=rate_per_box,
                       effective_from=effective_from, effective_to=effective_to, change_reason=change_reason,
                       previous_rate_id=prev.id)


def approve_rate(db: Session, user: User, rate: FreightRate, remarks: str | None = None,
                 _skip_self_check: bool = False) -> int:
    """Approve a pending rate; closes the previous version; re-prices RATE MISSING invoices. Returns #resolved."""
    if rate.status != RateStatus.PENDING_APPROVAL:
        raise BusinessError("Rate is not awaiting approval")
    if not _skip_self_check:
        if not user.can("rate.approve"):
            raise BusinessError("You are not authorised to approve freight rates")
        if rate.created_by_id == user.id and not cfg.get(db, "rate.allow_self_approval"):
            raise BusinessError("Maker-checker: the creator of a rate cannot approve it")

    # Close overlapping earlier versions (never overwrite them).
    prev_versions = db.scalars(
        _same_key(select(FreightRate), rate.company_id, rate.cfa_id, rate.customer_id, rate.location_id,
                  rate.is_default)
        .where(FreightRate.status == RateStatus.APPROVED, FreightRate.id != rate.id,
               FreightRate.effective_from < rate.effective_from,
               or_(FreightRate.effective_to.is_(None), FreightRate.effective_to >= rate.effective_from)))
    for pv in prev_versions:
        new_to = rate.effective_from - timedelta(days=1)
        audit.log(db, user, "RATE_VERSION_CLOSED", MODULE, record_type="FreightRate", record_id=pv.id,
                  ref=describe(pv), field="effective_to", old=pv.effective_to, new=new_to,
                  reason=f"Superseded by rate #{rate.id} from {rate.effective_from:%d-%m-%Y}")
        pv.effective_to = new_to
        if rate.previous_rate_id is None:
            rate.previous_rate_id = pv.id

    rate.status = RateStatus.APPROVED
    rate.approved_by_id = user.id if user else None
    rate.approved_at = utcnow()
    audit.log(db, user, "RATE_APPROVED", MODULE, record_type="FreightRate", record_id=rate.id, ref=describe(rate),
              field="status", old=RateStatus.PENDING_APPROVAL, new=RateStatus.APPROVED, reason=remarks)
    db.flush()

    resolved = 0
    if cfg.get(db, "rate.auto_resolve_missing"):
        resolved = resolve_missing_for_rate(db, user, rate)
    return resolved


def resolve_missing_for_rate(db: Session, user: User | None, rate: FreightRate) -> int:
    from app.services import invoice_service

    stmt = select(Invoice).where(Invoice.status.in_([InvoiceStatus.RATE_MISSING, InvoiceStatus.EXCEPTION]),
                                 Invoice.exception_code.in_([ExceptionCode.RATE_MISSING, ExceptionCode.RATE_CONFLICT]),
                                 Invoice.company_id == rate.company_id,
                                 Invoice.invoice_date >= rate.effective_from)
    if rate.effective_to:
        stmt = stmt.where(Invoice.invoice_date <= rate.effective_to)
    if rate.location_id:
        stmt = stmt.where(Invoice.location_id == rate.location_id)
    if rate.cfa_id:
        stmt = stmt.where(Invoice.cfa_id == rate.cfa_id)
    if rate.customer_id:
        stmt = stmt.where(Invoice.customer_id == rate.customer_id)
    n = 0
    for inv in db.scalars(stmt):
        invoice_service.apply_freight(db, inv, user, reason=f"Auto re-priced after approval of rate #{rate.id}",
                                      audit_it=True)
        if inv.rate_per_box is not None:
            n += 1
    return n


def reject_rate(db: Session, user: User, rate: FreightRate, reason: str) -> None:
    if rate.status != RateStatus.PENDING_APPROVAL:
        raise BusinessError("Rate is not awaiting approval")
    if not reason or len(reason.strip()) < 3:
        raise BusinessError("Rejection reason is required")
    rate.status = RateStatus.REJECTED
    rate.rejected_reason = reason
    rate.approved_by_id = user.id
    rate.approved_at = utcnow()
    audit.log(db, user, "RATE_REJECTED", MODULE, record_type="FreightRate", record_id=rate.id, ref=describe(rate),
              field="status", old=RateStatus.PENDING_APPROVAL, new=RateStatus.REJECTED, reason=reason)


def close_rate(db: Session, user: User, rate: FreightRate, effective_to: date, reason: str) -> None:
    """End-date an approved rate (history kept). If it never became effective it is made INACTIVE."""
    if rate.status != RateStatus.APPROVED:
        raise BusinessError("Only approved rates can be closed")
    if not reason or len(reason.strip()) < 3:
        raise BusinessError("Reason is required")
    if rate.effective_to and effective_to > rate.effective_to:
        raise BusinessError("Cannot extend a closed rate; create a new version instead")
    if effective_to < rate.effective_from:
        audit.log(db, user, "RATE_DEACTIVATED", MODULE, record_type="FreightRate", record_id=rate.id,
                  ref=describe(rate), field="status", old=rate.status, new=RateStatus.INACTIVE, reason=reason)
        rate.status = RateStatus.INACTIVE
        rate.deactivated_reason = reason
        return
    audit.log(db, user, "RATE_CLOSED", MODULE, record_type="FreightRate", record_id=rate.id, ref=describe(rate),
              field="effective_to", old=rate.effective_to, new=effective_to, reason=reason)
    rate.effective_to = effective_to
    rate.deactivated_reason = reason


def rate_history(db: Session, rate: FreightRate) -> list[FreightRate]:
    stmt = _same_key(select(FreightRate), rate.company_id, rate.cfa_id, rate.customer_id, rate.location_id,
                     rate.is_default).order_by(FreightRate.effective_from.desc(), FreightRate.id.desc())
    return list(db.scalars(stmt))
