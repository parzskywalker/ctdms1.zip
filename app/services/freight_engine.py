"""Freight calculation engine.

Transportation Expense = Number of Boxes x Applicable Rate per Box

Rate priority (first level with exactly one APPROVED rate effective on the invoice date wins):
  1. CUSTOMER      Customer + Company + CFA + Location
  2. CFA_LOCATION  Company + CFA + Location
  3. LOCATION      Company + Location (rate defined for any CFA)
  4. DEFAULT       Explicitly configured default (CFA-specific default preferred over company-wide)

If no level matches -> RATE_MISSING. If more than one rate matches at the same level
-> RATE_CONFLICT. The engine never silently falls back to a wrong/zero rate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import ExceptionCode, FreightRate, RateLevel, RateStatus


@dataclass
class RateLookup:
    rate: FreightRate | None
    level: str | None
    code: str | None  # None when found, else RATE_MISSING / RATE_CONFLICT
    message: str
    trace: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.rate is not None

    @property
    def rate_per_box(self) -> Decimal | None:
        return self.rate.rate_per_box if self.rate else None


def calc_freight(boxes: int | None, rate: Decimal | None) -> Decimal | None:
    if boxes is None or rate is None:
        return None
    return (Decimal(boxes) * Decimal(rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _effective(stmt, on_date: date):
    return stmt.where(
        FreightRate.status == RateStatus.APPROVED,
        FreightRate.effective_from <= on_date,
        or_(FreightRate.effective_to.is_(None), FreightRate.effective_to >= on_date),
    )


def find_rate(db: Session, *, company_id: int, cfa_id: int | None, customer_id: int | None,
              location_id: int | None, on_date: date) -> RateLookup:
    base = _effective(select(FreightRate).where(FreightRate.company_id == company_id), on_date)
    trace: list[str] = []

    levels: list[tuple[str, object]] = []
    if customer_id and cfa_id and location_id:
        levels.append((RateLevel.CUSTOMER, base.where(
            FreightRate.customer_id == customer_id, FreightRate.cfa_id == cfa_id,
            FreightRate.location_id == location_id, FreightRate.is_default.is_(False))))
    if cfa_id and location_id:
        levels.append((RateLevel.CFA_LOCATION, base.where(
            FreightRate.customer_id.is_(None), FreightRate.cfa_id == cfa_id,
            FreightRate.location_id == location_id, FreightRate.is_default.is_(False))))
    if location_id:
        levels.append((RateLevel.LOCATION, base.where(
            FreightRate.customer_id.is_(None), FreightRate.cfa_id.is_(None),
            FreightRate.location_id == location_id, FreightRate.is_default.is_(False))))

    for level, stmt in levels:
        rows = list(db.scalars(stmt))
        if len(rows) == 1:
            trace.append(f"{level}: matched rate #{rows[0].id}")
            return RateLookup(rows[0], level, None, f"{RateLevel.LABELS[level]} @ ₹{rows[0].rate_per_box}/box", trace)
        if len(rows) > 1:
            trace.append(f"{level}: {len(rows)} overlapping rates {[r.id for r in rows]}")
            return RateLookup(None, level, ExceptionCode.RATE_CONFLICT,
                              f"{len(rows)} overlapping approved rates at level {level} (ids {[r.id for r in rows]})",
                              trace)
        trace.append(f"{level}: none")

    # Level 4 - explicitly configured defaults only
    defaults = list(db.scalars(base.where(FreightRate.is_default.is_(True), FreightRate.customer_id.is_(None),
                                          FreightRate.location_id.is_(None))))
    cfa_defaults = [r for r in defaults if cfa_id and r.cfa_id == cfa_id]
    co_defaults = [r for r in defaults if r.cfa_id is None]
    for pool, label in ((cfa_defaults, "CFA default"), (co_defaults, "Company default")):
        if len(pool) == 1:
            trace.append(f"DEFAULT: matched {label} #{pool[0].id}")
            return RateLookup(pool[0], RateLevel.DEFAULT, None,
                              f"{RateLevel.LABELS[RateLevel.DEFAULT]} ({label}) @ ₹{pool[0].rate_per_box}/box", trace)
        if len(pool) > 1:
            return RateLookup(None, RateLevel.DEFAULT, ExceptionCode.RATE_CONFLICT,
                              f"{len(pool)} overlapping default rates", trace)
    trace.append("DEFAULT: none configured")
    return RateLookup(None, None, ExceptionCode.RATE_MISSING,
                      "No approved freight rate effective on the invoice date for this Company/CFA/Location", trace)
