"""Transporter rate card: rate per case for each delivery area, effective-dated, maker-checker approved.

Same rules as the principal freight-rate master: approved versions are never edited, a change is a new version
with a later Effective From that ends the previous one the day before, and the creator cannot approve it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Location, RateStatus, Transporter, TransporterRate, User, utcnow
from app.services import audit
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from app.services.utils import money

MODULE = "TRANSPORTER_RATE"


def describe(r: TransporterRate) -> str:
    return f"{r.transporter.code if r.transporter else r.transporter_id} / " \
           f"{r.location.name if r.location else r.location_id}"


@dataclass
class CardRate:
    rate: TransporterRate | None
    message: str

    @property
    def found(self) -> bool:
        return self.rate is not None

    @property
    def rate_per_case(self) -> Decimal | None:
        return self.rate.rate_per_case if self.rate else None


def _effective(stmt, on_date: date):
    return stmt.where(TransporterRate.status == RateStatus.APPROVED, TransporterRate.effective_from <= on_date,
                      or_(TransporterRate.effective_to.is_(None), TransporterRate.effective_to >= on_date))


def find(db: Session, transporter_id: int, location_id: int, on_date: date) -> CardRate:
    rows = list(db.scalars(_effective(select(TransporterRate).where(
        TransporterRate.transporter_id == transporter_id, TransporterRate.location_id == location_id), on_date)))
    if len(rows) == 1:
        return CardRate(rows[0], f"₹{rows[0].rate_per_case}/case")
    if len(rows) > 1:  # cannot happen through the service (versions never overlap); refuse rather than guess
        return CardRate(None, f"{len(rows)} overlapping rate-card entries - fix the rate card")
    tr = db.get(Transporter, transporter_id)
    lo = db.get(Location, location_id)
    return CardRate(None, f"No approved rate for {tr.name if tr else 'transporter'} to "
                          f"{lo.name if lo else 'this area'} on {on_date:%d-%m-%Y}")


def card(db: Session, transporter_id: int, on_date: date) -> dict[int, Decimal]:
    """{location_id: rate} effective on the date - used by the gatepass screen to price lines as they are added."""
    return {r.location_id: r.rate_per_case for r in db.scalars(_effective(
        select(TransporterRate).where(TransporterRate.transporter_id == transporter_id), on_date))}


def current_and_pending(db: Session, transporter_id: int) -> list[dict]:
    """One row per area the transporter serves: approved rate today, pending proposal (if any)."""
    from app.services.utils import local_today
    today = local_today()
    out: dict[int, dict] = {}
    for r in db.scalars(select(TransporterRate).where(TransporterRate.transporter_id == transporter_id)
                        .order_by(TransporterRate.effective_from)):
        row = out.setdefault(r.location_id, {"location": r.location, "current": None, "pending": None,
                                             "future": None, "history": []})
        row["history"].append(r)
        if r.status == RateStatus.APPROVED and r.covers(today):
            row["current"] = r
        elif r.status == RateStatus.APPROVED and r.effective_from > today:
            row["future"] = r
        elif r.status == RateStatus.PENDING_APPROVAL:
            row["pending"] = r
    return sorted(out.values(), key=lambda x: x["location"].name)


def _validate(db: Session, transporter_id, location_id, rate, effective_from, exclude_id=None) -> None:
    if not transporter_id or not db.get(Transporter, transporter_id):
        raise BusinessError("Transporter is required")
    if not location_id or not db.get(Location, location_id):
        raise BusinessError("Delivery area is required")
    if rate is None or Decimal(rate) <= 0:
        raise BusinessError("Rate per case must be greater than zero")
    if Decimal(rate) > Decimal("10000"):
        raise BusinessError("Rate per case looks unrealistic")
    if not effective_from:
        raise BusinessError("Effective From date is required")
    base = select(TransporterRate).where(TransporterRate.transporter_id == transporter_id,
                                         TransporterRate.location_id == location_id)
    if exclude_id:
        base = base.where(TransporterRate.id != exclude_id)
    pending = db.scalar(base.where(TransporterRate.status == RateStatus.PENDING_APPROVAL))
    if pending:
        raise BusinessError(f"A change for {pending.location.name} (₹{pending.rate_per_case} from "
                            f"{pending.effective_from:%d-%m-%Y}) is already awaiting approval")
    later = db.scalar(base.where(TransporterRate.status == RateStatus.APPROVED,
                                 TransporterRate.effective_from >= effective_from))
    if later:
        raise BusinessError(f"An approved rate already starts on {later.effective_from:%d-%m-%Y}; a change must start "
                            "after the latest version")


def propose(db: Session, user: User | None, *, transporter_id: int, location_id: int, rate_per_case,
            effective_from: date, reason: str | None = None, source: str = "MANUAL",
            source_bill_id: int | None = None, auto_approve: bool = False,
            check_permission: bool = True) -> TransporterRate:
    _validate(db, transporter_id, location_id, rate_per_case, effective_from)
    if user is not None and check_permission and not auto_approve and not user.can("rate.create"):
        raise BusinessError("You are not authorised to change rate cards")
    prev = db.scalar(_effective(select(TransporterRate).where(TransporterRate.transporter_id == transporter_id,
                                                              TransporterRate.location_id == location_id),
                                effective_from))
    r = TransporterRate(transporter_id=transporter_id, location_id=location_id, rate_per_case=money(rate_per_case),
                        effective_from=effective_from, status=RateStatus.PENDING_APPROVAL, source=source,
                        source_bill_id=source_bill_id, change_reason=(reason or "").strip() or None,
                        previous_rate_id=prev.id if prev else None, created_by_id=user.id if user else None)
    db.add(r)
    db.flush()
    db.refresh(r)
    audit.log(db, user, "TRATE_PROPOSED", MODULE, record_type="TransporterRate", record_id=r.id, ref=describe(r),
              field="rate_per_case", old=prev.rate_per_case if prev else None, new=r.rate_per_case, reason=reason,
              details={"effective_from": effective_from.isoformat(), "source": source, "bill_id": source_bill_id})
    if auto_approve or not cfg.get(db, "rate.require_approval"):
        approve(db, user, r, remarks="Seed / system approval" if auto_approve else "Approval not required",
                _skip_checks=True)
    return r


def approve(db: Session, user: User | None, r: TransporterRate, remarks: str | None = None,
            _skip_checks: bool = False) -> None:
    if r.status != RateStatus.PENDING_APPROVAL:
        raise BusinessError("This rate is not awaiting approval")
    if not _skip_checks:
        if not user.can("rate.approve"):
            raise BusinessError("You are not authorised to approve rate changes")
        if r.created_by_id == user.id and not cfg.get(db, "rate.allow_self_approval"):
            raise BusinessError("Maker-checker: the person who proposed a rate cannot approve it")
    for pv in db.scalars(select(TransporterRate).where(
            TransporterRate.transporter_id == r.transporter_id, TransporterRate.location_id == r.location_id,
            TransporterRate.status == RateStatus.APPROVED, TransporterRate.id != r.id,
            TransporterRate.effective_from < r.effective_from,
            or_(TransporterRate.effective_to.is_(None), TransporterRate.effective_to >= r.effective_from))):
        new_to = r.effective_from - timedelta(days=1)
        audit.log(db, user, "TRATE_VERSION_CLOSED", MODULE, record_type="TransporterRate", record_id=pv.id,
                  ref=describe(pv), field="effective_to", old=pv.effective_to, new=new_to,
                  reason=f"Superseded by ₹{r.rate_per_case} from {r.effective_from:%d-%m-%Y}")
        pv.effective_to = new_to
    r.status = RateStatus.APPROVED
    r.approved_by_id = user.id if user else None
    r.approved_at = utcnow()
    audit.log(db, user, "TRATE_APPROVED", MODULE, record_type="TransporterRate", record_id=r.id, ref=describe(r),
              field="status", old=RateStatus.PENDING_APPROVAL, new=RateStatus.APPROVED, reason=remarks)
    db.flush()


def reject(db: Session, user: User, r: TransporterRate, reason: str) -> None:
    if r.status != RateStatus.PENDING_APPROVAL:
        raise BusinessError("This rate is not awaiting approval")
    if not user.can("rate.approve"):
        raise BusinessError("You are not authorised to decide rate changes")
    if not reason or len(reason.strip()) < 3:
        raise BusinessError("Rejection reason is required")
    r.status = RateStatus.REJECTED
    r.rejected_reason = reason.strip()
    r.approved_by_id = user.id
    r.approved_at = utcnow()
    audit.log(db, user, "TRATE_REJECTED", MODULE, record_type="TransporterRate", record_id=r.id, ref=describe(r),
              field="status", old=RateStatus.PENDING_APPROVAL, new=RateStatus.REJECTED, reason=reason)
