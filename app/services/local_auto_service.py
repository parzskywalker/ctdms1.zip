"""v1.6 Local Auto charging (local delivery vehicles only - long-distance transporters keep the rate-card workflow).

* PER_TRIP: one amount for the whole trip, entered or confirmed on the gatepass. 5 boxes or 15 boxes, 1 invoice or
  4 invoices: the auto is owed that one amount. For MIS it is shared over the invoices by boxes; the shares always add
  up to the trip amount, so nothing is counted twice.
* PER_CASE ("per box"): boxes x rate; the rate is looked up per invoice from Local Auto Box Rates by auto + location
  + company + depot + date (blank = any; most specific wins), else the auto's default rate per box.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import ChargeBasis, LocalAutoRate, OutsideAuto


@dataclass
class BoxRate:
    rate: Decimal | None
    source: str          # "rate <code>" / "default rate" / ""
    rate_id: int | None = None


def box_rate(db: Session, auto: OutsideAuto, *, location_id: int | None, company_id: int | None,
             cfa_id: int | None, on: date) -> BoxRate:
    stmt = select(LocalAutoRate).where(
        LocalAutoRate.outside_auto_id == auto.id, LocalAutoRate.is_active.is_(True),
        LocalAutoRate.effective_from <= on,
        or_(LocalAutoRate.effective_to.is_(None), LocalAutoRate.effective_to >= on))
    best, best_key = None, None
    for r in db.scalars(stmt):
        if (r.location_id and r.location_id != location_id) or (r.company_id and r.company_id != company_id) \
                or (r.cfa_id and r.cfa_id != cfa_id):
            continue
        key = (4 if r.location_id else 0) + (2 if r.company_id else 0) + (1 if r.cfa_id else 0), r.effective_from, r.id
        if best_key is None or key > best_key:
            best, best_key = r, key
    if best is not None:
        return BoxRate(best.rate_per_box, f"box rate {best.code}", best.id)
    if auto.default_box_rate:
        return BoxRate(auto.default_box_rate, "default rate of the auto")
    return BoxRate(None, "")


def method_for(auto: OutsideAuto | None, requested: str | None) -> tuple[str | None, str | None]:
    """(method, problem). The requested method must be one the auto allows."""
    if auto is None:
        return None, None
    if requested in (None, ""):
        return auto.default_method, None
    if requested == "PER_BOX":
        requested = ChargeBasis.PER_CASE
    if requested not in auto.methods:
        allowed = " / ".join(ChargeBasis.AUTO_LABELS[m].lower() for m in auto.methods)
        return auto.default_method, f"{auto.name} is charged {allowed} only"
    return requested, None


def rates_for_screen(db: Session, autos: list[OutsideAuto], on: date) -> dict:
    """Active per-box rates per auto, for the live charge preview on the gatepass screen."""
    out: dict = {}
    ids = [a.id for a in autos]
    if not ids:
        return out
    stmt = select(LocalAutoRate).where(LocalAutoRate.outside_auto_id.in_(ids), LocalAutoRate.is_active.is_(True),
                                       LocalAutoRate.effective_from <= on,
                                       or_(LocalAutoRate.effective_to.is_(None), LocalAutoRate.effective_to >= on))
    for r in db.scalars(stmt):
        out.setdefault(str(r.outside_auto_id), []).append(
            {"loc": r.location_id, "co": r.company_id, "cfa": r.cfa_id, "rate": float(r.rate_per_box),
             "from": r.effective_from.isoformat(), "id": r.id})
    return out
