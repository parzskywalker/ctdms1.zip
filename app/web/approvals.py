from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    BillStatus, FreightRate, Invoice, InvoiceOverride, InvoiceStatus, OverrideField, OverrideStatus, RateStatus,
    TransporterBill, TransporterRate, User,
)
from app.security.auth import csrf_protect, require, require_any
from app.services import invoice_service
from app.services.reports import Filters, rpt_exceptions, rpt_rate_missing
from app.services.scope import apply_invoice_scope, apply_strict_scope, can_see_invoice
from app.web.templating import back_url, filter_options, flash, redirect, render

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)


@router.get("/exceptions")
def exceptions(request: Request, db: Session = Depends(get_db), user: User = Depends(require("invoice.view"))):
    f = Filters.from_params(request.query_params, default_range="none")
    combos = rpt_rate_missing(db, user, f)
    exc = rpt_exceptions(db, user, f)
    return render(request, "exceptions.html", {"title": "Freight Exceptions", "combos": combos, "exc": exc, "f": f,
                                               "opts": filter_options(db, user)})


@router.get("/approvals")
def approvals(request: Request, db: Session = Depends(get_db),
              user: User = Depends(require_any("override.approve", "rate.approve", "recon.approve"))):
    overrides = []
    if user.can("override.approve"):
        stmt = apply_invoice_scope(select(InvoiceOverride, Invoice).join(Invoice, InvoiceOverride.invoice_id == Invoice.id),
                                   user).where(InvoiceOverride.status == OverrideStatus.PENDING)
        overrides = list(db.execute(stmt.order_by(InvoiceOverride.requested_at)))
    rates = []
    if user.can("rate.approve"):
        stmt = apply_strict_scope(select(FreightRate), user, FreightRate)
        rates = list(db.scalars(stmt.where(FreightRate.status == RateStatus.PENDING_APPROVAL).order_by(FreightRate.created_at)))
        prev = {r.id: db.get(FreightRate, r.previous_rate_id) if r.previous_rate_id else None for r in rates}
    else:
        prev = {}
    trates = []
    if user.can("rate.approve"):
        trates = list(db.scalars(select(TransporterRate).where(TransporterRate.status == RateStatus.PENDING_APPROVAL)
                                 .order_by(TransporterRate.created_at)))
    bills = []
    if user.can("recon.approve"):
        bills = list(db.scalars(apply_strict_scope(select(TransporterBill), user, TransporterBill)
                                .where(TransporterBill.status.in_([BillStatus.MATCHED, BillStatus.VARIANCE]))
                                .order_by(TransporterBill.bill_date)))
    tprev = {r.id: db.get(TransporterRate, r.previous_rate_id) if r.previous_rate_id else None for r in trates}
    return render(request, "approvals.html", {"title": "Approvals", "overrides": overrides, "rates": rates,
                                              "prev": prev, "bills": bills, "fields": OverrideField.LABELS,
                                              "trates": trates, "tprev": tprev})


@router.post("/overrides/{ov_id:int}/decide")
def decide(ov_id: int, request: Request, decision: str = Form(...), remarks: str = Form(""),
           db: Session = Depends(get_db), user: User = Depends(require("override.approve"))):
    ov = db.get(InvoiceOverride, ov_id)
    if not ov:
        raise HTTPException(404)
    inv = db.get(Invoice, ov.invoice_id)
    if not can_see_invoice(user, inv):
        raise HTTPException(404)
    invoice_service.decide_override(db, user, ov, decision == "approve", remarks)
    db.commit()
    flash(request, f"Request #{ov.id} {ov.status.lower()} for invoice {inv.invoice_no}"
                   + (f" - now {InvoiceStatus.LABELS[inv.status]}" if decision == "approve" else ""), "success")
    return redirect(back_url(request, "/approvals"))
