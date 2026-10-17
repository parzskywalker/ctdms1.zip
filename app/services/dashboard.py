"""Management dashboard KPIs / chart data and the hierarchical drill-down MIS."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import CFA, Company, Customer, Invoice, InvoiceStatus, Location, Transporter, User
from app.services.reports import (
    AGG_DERIVED, Filters, _agg_columns, _apply_filters, _finish_agg, _ratio, month_expr,
)
from app.services.scope import apply_invoice_scope
from app.services.utils import local_today, money

S = InvoiceStatus


def _base(stmt, user, f, **kw):
    return _apply_filters(apply_invoice_scope(stmt, user), f, Invoice.invoice_date, **kw)


def _group(db, user, f, key_col, label_col, joins, limit=None, order="freight"):
    stmt = select(key_col.label("id"), label_col.label("label"), *_agg_columns()).select_from(Invoice)
    for j in joins:
        stmt = stmt.join(*j) if isinstance(j, tuple) else stmt.join(j)
    stmt = _base(stmt, user, f).group_by(key_col, label_col)
    stmt = stmt.order_by(func.sum(Invoice.freight_amount).desc() if order == "freight" else label_col)
    if limit:
        stmt = stmt.limit(limit)
    return [_finish_agg(dict(r._mapping)) for r in db.execute(stmt)]


def dashboard_data(db: Session, user: User, f: Filters) -> dict:
    tot = db.execute(_base(select(*_agg_columns()).select_from(Invoice), user, f)).one()._mapping
    k = _finish_agg(dict(tot))
    counts = db.execute(_base(select(
        func.sum(case((Invoice.status.in_([S.PENDING, S.VERIFIED, S.SLIP_GENERATED, S.SLIP_PRINTED,
                                           S.RATE_MISSING, S.EXCEPTION]), 1), else_=0)).label("pending"),
        func.sum(case((Invoice.status == S.RATE_MISSING, 1), else_=0)).label("rate_missing"),
        func.sum(case((Invoice.status == S.EXCEPTION, 1), else_=0)).label("exceptions"),
    ).select_from(Invoice), user, f)).one()._mapping
    k.update({kk: int(v or 0) for kk, v in counts.items()})

    company = _group(db, user, f, Company.id, Company.name, [Company])
    customers = _group(db, user, f, Customer.id, Customer.name, [Customer], limit=10)
    cfas = _group(db, user, f, CFA.id, CFA.name, [CFA])
    cities = {c.id: c.city for c in db.scalars(select(CFA))}
    for r in cfas:  # v1.6: one city may have several depots - show the city with the depot
        if cities.get(r["id"]) and cities[r["id"]].lower() not in (r["label"] or "").lower():
            r["label"] = f"{r['label']} ({cities[r['id']]})"
    locations = _group(db, user, f, Location.id, Location.name, [(Location, Invoice.location_id == Location.id)], limit=10)
    transporters = _group(db, user, f, Transporter.id, Transporter.name,
                          [(Transporter, Invoice.transporter_id == Transporter.id)])

    m = month_expr(db, Invoice.invoice_date)
    monthly = [_finish_agg(dict(r._mapping)) for r in db.execute(
        _base(select(m.label("month"), *_agg_columns()).select_from(Invoice), user, f).group_by(m).order_by(m))]
    for r in monthly:
        try:
            r["label"] = datetime.strptime(r["month"], "%Y-%m").strftime("%b %y")
        except (TypeError, ValueError):
            r["label"] = r["month"]

    # Pending ageing: all currently open invoices (date filter not applied - it is a "now" view)
    fo = Filters(**{kk: getattr(f, kk) for kk in Filters.KEYS})
    fo.date_from = fo.date_to = None
    today = local_today()
    buckets = [("0–1 days", 0, 1), ("2–3 days", 2, 3), ("4–7 days", 4, 7), ("8–15 days", 8, 15), ("> 15 days", 16, 99999)]
    ageing = {b[0]: {"count": 0, "boxes": 0} for b in buckets}
    stmt = _apply_filters(apply_invoice_scope(select(Invoice.invoice_date, Invoice.boxes), user), fo,
                          Invoice.invoice_date, S.OPEN)
    for d, bx in db.execute(stmt):
        age = (today - d).days
        for label, lo, hi in buckets:
            if lo <= age <= hi:
                ageing[label]["count"] += 1
                ageing[label]["boxes"] += bx or 0
                break

    return {"kpi": k, "company": company, "customers": customers, "cfas": cfas, "locations": locations,
            "transporters": transporters, "monthly": monthly,
            "ageing": [{"label": kk, **v} for kk, v in ageing.items()]}


# ----------------------------------------------------------------------------- drill-down
LEVELS = ["company", "customer", "cfa", "location", "invoice"]
LEVEL_COLS = {
    "company": (Company.id, Company.name, [Company]),
    "customer": (Customer.id, Customer.name, [Customer]),
    "cfa": (CFA.id, CFA.name, [CFA]),
    "location": (Location.id, Location.name, [(Location, Invoice.location_id == Location.id)]),
}
PARENT_FILTER = {"company": Invoice.company_id, "customer": Invoice.customer_id, "cfa": Invoice.cfa_id,
                 "location": Invoice.location_id}


def drill(db: Session, user: User, f: Filters, level: str, parents: dict[str, int]) -> list[dict]:
    if level not in LEVELS:
        raise ValueError("bad level")
    if level == "invoice":
        stmt = (select(Invoice, Transporter.name).outerjoin(Transporter, Invoice.transporter_id == Transporter.id))
        stmt = _base(stmt, user, f)
        for lvl, v in parents.items():
            stmt = stmt.where(PARENT_FILTER[lvl] == v)
        out = []
        for inv, tname in db.execute(stmt.order_by(Invoice.invoice_date, Invoice.invoice_no).limit(1000)):
            out.append({
                "id": inv.id, "label": inv.invoice_no, "invoice_date": inv.invoice_date.strftime("%d-%m-%Y"),
                "status": inv.status, "status_label": inv.status_label, "boxes": inv.boxes,
                "rate": str(inv.rate_per_box) if inv.rate_per_box is not None else None,
                "freight": str(inv.freight_amount) if inv.freight_amount is not None else None,
                "sales": str(inv.sales_value), "actual": str(inv.actual_freight) if inv.actual_freight is not None else None,
                "dispatch_no": inv.dispatch_no, "transporter": tname, "lr_no": inv.lr_no, "vehicle_no": inv.vehicle_no,
                "dispatch_date": inv.dispatch_date.strftime("%d-%m-%Y") if inv.dispatch_date else None,
            })
        return out
    key, label, joins = LEVEL_COLS[level]
    stmt = select(key.label("id"), label.label("label"), *_agg_columns()).select_from(Invoice)
    for j in joins:
        stmt = stmt.join(*j) if isinstance(j, tuple) else stmt.join(j)
    stmt = _base(stmt, user, f)
    for lvl, v in parents.items():
        stmt = stmt.where(PARENT_FILTER[lvl] == v)
    stmt = stmt.group_by(key, label).order_by(func.sum(Invoice.freight_amount).desc())
    rows = []
    for r in db.execute(stmt):
        d = _finish_agg(dict(r._mapping))
        rows.append({kk: (str(v) if isinstance(v, Decimal) else v) for kk, v in d.items()})
    return rows


def drill_totals(db: Session, user: User, f: Filters) -> dict:
    tot = db.execute(_base(select(*_agg_columns()).select_from(Invoice), user, f)).one()._mapping
    d = _finish_agg(dict(tot))
    return {kk: (str(v) if isinstance(v, Decimal) else v) for kk, v in d.items()}


__all__ = ["dashboard_data", "drill", "drill_totals", "LEVELS", "AGG_DERIVED", "_ratio", "money"]
