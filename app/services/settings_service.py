"""Configurable business rules (stored in the `settings` table, editable by admins)."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting

# key: (default, type, category, description, choices)
DEFAULTS: dict[str, tuple[str, str, str, str, str | None]] = {
    "org.name": ("Demo Pharma Distributors & CFA", "str", "Organisation", "Organisation name printed on slips/reports", None),
    "org.address": ("Ring Road No. 1, Raipur, Chhattisgarh", "str", "Organisation", "Organisation address line", None),
    "dispatch.complete_requires": ("PRINTED", "choice", "Dispatch controls",
                                   "Dispatch cannot be COMPLETED unless the slip is: PRINTED / GENERATED / NONE (no control)",
                                   "PRINTED,GENERATED,NONE"),
    "dispatch.require_verify_before_slip": ("true", "bool", "Dispatch controls",
                                            "Invoice must be Verified before a Dispatch Slip can be generated", None),
    "dispatch.lr_mandatory": ("true", "bool", "Dispatch controls", "LR / docket number mandatory for slip generation", None),
    "dispatch.vehicle_mandatory": ("true", "bool", "Dispatch controls", "Vehicle number mandatory for slip generation", None),
    "dispatch.allow_future_date": ("false", "bool", "Dispatch controls", "Allow dispatch date later than today", None),
    "dispatch.reprint_reason_required": ("true", "bool", "Dispatch controls", "Reason mandatory for every slip reprint", None),
    "dispatch.default_slip_size": ("A5", "choice", "Dispatch controls", "Default slip paper size", "A4,A5"),
    "dispatch.number_prefix": ("DS", "str", "Dispatch controls", "Dispatch number prefix (DS/CFA/FY/sequence)", None),
    "override.boxes_requires_approval": ("true", "bool", "Overrides",
                                         "Box-count changes by users without 'override.direct' need approval", None),
    "override.allow_self_approval": ("false", "bool", "Overrides", "Allow the requester to approve their own override", None),
    "rate.require_approval": ("true", "bool", "Freight rates", "New / revised freight rates need approval before use", None),
    "rate.allow_self_approval": ("false", "bool", "Freight rates", "Allow the creator of a rate to approve it", None),
    "rate.auto_resolve_missing": ("true", "bool", "Freight rates",
                                  "When a rate is approved, automatically re-price matching RATE MISSING invoices", None),
    "import.allow_blank_boxes": ("true", "bool", "Import",
                                 "Accept invoices without box count (they land as EXCEPTION - boxes missing)", None),
    "recon.tolerance_amount": ("10.00", "decimal", "Reconciliation", "Variance tolerance per line (₹) to auto-MATCH", None),
    "recon.tolerance_pct": ("1.0", "decimal", "Reconciliation", "Variance tolerance per bill (%) to auto-MATCH", None),
    "recon.allow_self_approval": ("false", "bool", "Reconciliation", "Allow the bill creator to approve it", None),
    "slip.footer_note": ("Goods dispatched in good condition. Please check seals and box count on receipt.",
                         "str", "Dispatch slip", "Footer note printed on each dispatch slip", None),
}


def _convert(value: str | None, vtype: str) -> Any:
    if value is None:
        return None
    if vtype == "bool":
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if vtype == "int":
        return int(value)
    if vtype == "decimal":
        return Decimal(str(value))
    return value


def get(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    if row is not None:
        return _convert(row.value, row.value_type)
    if key in DEFAULTS:
        d = DEFAULTS[key]
        return _convert(d[0], d[1])
    raise KeyError(key)


def set_value(db: Session, key: str, value: Any, user_id: int | None = None) -> None:
    row = db.get(Setting, key)
    if row is None:
        d = DEFAULTS.get(key, (None, "str", "General", None, None))
        row = Setting(key=key, value_type=d[1], category=d[2], description=d[3], choices=d[4])
        db.add(row)
    if isinstance(value, bool):
        value = "true" if value else "false"
    row.value = None if value is None else str(value)
    row.updated_by_id = user_id


def ensure_defaults(db: Session) -> None:
    existing = {s.key for s in db.scalars(select(Setting))}
    for key, (val, vtype, cat, desc, choices) in DEFAULTS.items():
        if key not in existing:
            db.add(Setting(key=key, value=val, value_type=vtype, category=cat, description=desc, choices=choices))
    db.flush()


def all_settings(db: Session) -> list[Setting]:
    ensure_defaults(db)
    return list(db.scalars(select(Setting).order_by(Setting.category, Setting.key)))
