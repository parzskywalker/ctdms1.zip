from __future__ import annotations

import math
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Customer, Invoice, InvoiceStatus, OverrideField, User
from app.security.auth import csrf_protect, require
from app.services import dispatch_service as ds
from app.services.reports import Filters, _apply_filters
from app.services.scope import apply_invoice_scope
from app.services.utils import safe_int
from app.web.invoices_routes import get_invoice
from app.web.templating import filter_options, flash, get_form, redirect, render

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)
S = InvoiceStatus

TABS = {
    "open": ("All open", S.OPEN),
    "ready": ("Ready to dispatch", [S.PENDING, S.VERIFIED]),
    "slip": ("Slip generated / printed", [S.SLIP_GENERATED, S.SLIP_PRINTED]),
    "exceptions": ("Exceptions", [S.RATE_MISSING, S.EXCEPTION]),
}


@router.get("/dispatch/pending")
def pending(request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    tab = request.query_params.get("tab", "open")
    tab = tab if tab in TABS else "open"
    q = (request.query_params.get("q") or "").strip()
    f = Filters.from_params(request.query_params, default_range="none")
    base = apply_invoice_scope(select(Invoice), user)
    base = _apply_filters(base, f, Invoice.invoice_date, S.OPEN)
    if q:
        exact = list(db.scalars(base.where(func.upper(Invoice.invoice_no) == q.upper()).limit(2)))
        if len(exact) == 1:  # barcode / exact invoice number -> straight to dispatch entry
            return redirect(f"/dispatch/{exact[0].id}")
        base = base.join(Customer, Invoice.customer_id == Customer.id).where(or_(
            Invoice.invoice_no.ilike(f"%{q}%"), Customer.name.ilike(f"%{q}%"), Invoice.lr_no.ilike(f"%{q}%")))
    counts = {}
    for key, (_, sts) in TABS.items():
        counts[key] = db.scalar(select(func.count()).select_from(base.where(Invoice.status.in_(sts)).subquery()))
    stmt = base.where(Invoice.status.in_(TABS[tab][1]))
    total = counts[tab]
    page = safe_int(request.query_params.get("page"), 1, minimum=1)
    per = 100
    # Work the operator can do now first (oldest first); invoices blocked by rate / box exceptions go to the end.
    blocked_last = case((Invoice.status.in_([S.RATE_MISSING, S.EXCEPTION]), 1), else_=0)
    rows = list(db.scalars(stmt.order_by(blocked_last, Invoice.invoice_date, Invoice.id)
                           .offset((page - 1) * per).limit(per)))
    return render(request, "dispatch/pending.html", {
        "title": "Pending Dispatch", "rows": rows, "tab": tab, "tabs": TABS, "counts": counts, "q": q, "f": f,
        "opts": filter_options(db, user), "total": total, "page": page, "pages": max(math.ceil(total / per), 1),
        "qs": f.query() + f"&tab={tab}" + (f"&q={quote_plus(q)}" if q else "")})


@router.get("/dispatch/register")
def register(request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    f = Filters.from_params(request.query_params, default_range="month")
    stmt = apply_invoice_scope(select(Invoice), user).where(Invoice.dispatch_no.isnot(None))
    stmt = _apply_filters(stmt, f, Invoice.dispatch_date, [S.SLIP_GENERATED, S.SLIP_PRINTED, S.DISPATCHED])
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    page = safe_int(request.query_params.get("page"), 1, minimum=1)
    per = 100
    rows = list(db.scalars(stmt.order_by(Invoice.dispatch_date.desc(), Invoice.dispatch_time.desc(), Invoice.id.desc())
                           .offset((page - 1) * per).limit(per)))
    return render(request, "dispatch/register.html", {
        "title": "Dispatch Register", "rows": rows, "f": f, "opts": filter_options(db, user), "total": total,
        "page": page, "pages": max(math.ceil(total / per), 1), "qs": f.query()})


@router.get("/dispatch/{invoice_id:int}")
def entry(invoice_id: int, request: Request, db: Session = Depends(get_db),
          user: User = Depends(require("dispatch.view"))):
    """One invoice on its way out: status, freight, the gatepass it is on. Goods leave only on a gatepass (Scan & Gatepass /
    Pending Dispatch > Make one gatepass) and are marked gone out only by the gate staff's scan."""
    inv = get_invoice(db, user, invoice_id)
    from app.models import InvoiceOverride, OverrideStatus, SlipPrintLog
    pending_ov = db.scalar(select(InvoiceOverride).where(InvoiceOverride.invoice_id == inv.id,
                                                         InvoiceOverride.status == OverrideStatus.PENDING))
    prints = list(db.scalars(select(SlipPrintLog).where(SlipPrintLog.invoice_id == inv.id).order_by(SlipPrintLog.id)))
    acts = ds.actions_for(db, user, inv)
    return render(request, "dispatch/entry.html", {
        "title": f"Dispatch · {inv.invoice_no}", "inv": inv, "acts": acts, "slip": ds.active_slip(db, inv),
        "data_warnings": ds.data_warnings(db, inv) if acts.can_edit else [],
        "pending_ov": pending_ov, "prints": prints, "fields": OverrideField.LABELS})


def _warn(request: Request, db, inv) -> None:
    for w in ds.data_warnings(db, inv):
        flash(request, w, "warn")


@router.post("/dispatch/{invoice_id:int}/verify")
def verify(invoice_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                 user: User = Depends(require("dispatch.verify"))):
    inv = get_invoice(db, user, invoice_id)
    ds.verify(db, user, inv)
    db.commit()
    flash(request, f"Verified: {inv.boxes} boxes × ₹{inv.rate_per_box} = ₹{inv.freight_amount}", "success")
    _warn(request, db, inv)
    return redirect(f"/dispatch/{inv.id}")


@router.post("/dispatch/{invoice_id:int}/void")
def void(invoice_id: int, request: Request, reason: str = Form(""), db: Session = Depends(get_db),
         user: User = Depends(require("dispatch.void_slip"))):
    inv = get_invoice(db, user, invoice_id)
    ds.void_slip(db, user, inv, reason)
    db.commit()
    flash(request, "Slip voided. Dispatch details are unlocked for correction; a new dispatch number will be issued.", "warn")
    return redirect(f"/dispatch/{inv.id}")


@router.post("/dispatch/{invoice_id:int}/cancel")
def cancel(invoice_id: int, request: Request, reason: str = Form(""), db: Session = Depends(get_db),
           user: User = Depends(require("dispatch.cancel"))):
    inv = get_invoice(db, user, invoice_id)
    ds.cancel(db, user, inv, reason)
    db.commit()
    flash(request, f"Invoice {inv.invoice_no} cancelled.", "warn")
    return redirect(f"/dispatch/{inv.id}")


@router.post("/dispatch/{invoice_id:int}/reinstate")
def reinstate(invoice_id: int, request: Request, reason: str = Form(""), db: Session = Depends(get_db),
              user: User = Depends(require("dispatch.cancel"))):
    inv = get_invoice(db, user, invoice_id)
    ds.reinstate(db, user, inv, reason)
    db.commit()
    flash(request, f"Invoice {inv.invoice_no} reinstated to {inv.status_label}.", "success")
    return redirect(f"/dispatch/{inv.id}")
