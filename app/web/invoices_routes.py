from __future__ import annotations

import math

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    AuditLog, DispatchSlip, Invoice, InvoiceOverride, OverrideField, SlipPrintLog, TransporterBill,
    TransporterBillLine, User,
)
from app.security.auth import PermissionDenied, csrf_protect, require
from app.services import invoice_service
from app.services.dispatch_service import actions_for
from app.services.freight_engine import find_rate
from app.services.reports import Filters, filtered_invoices
from app.services.scope import apply_invoice_scope, can_see_invoice
from app.web.templating import filter_options, flash, redirect, render, safe_path

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)
PAGE = 50


def get_invoice(db: Session, user: User, invoice_id: int) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404)
    if not can_see_invoice(user, inv):
        raise PermissionDenied(message="This invoice belongs to a company/CFA you are not assigned to")
    return inv


@router.get("/invoices")
def register(request: Request, db: Session = Depends(get_db), user: User = Depends(require("invoice.view"))):
    q = (request.query_params.get("q") or "").strip()
    if q:
        stmt = apply_invoice_scope(select(Invoice), user).where(or_(
            func.upper(Invoice.invoice_no) == q.upper(), func.upper(Invoice.lr_no) == q.upper(),
            func.upper(Invoice.dispatch_no) == q.upper(), Invoice.source_invoice_id == q))
        hits = list(db.scalars(stmt.limit(2)))
        if len(hits) == 1:
            return redirect(f"/invoices/{hits[0].id}")
        stmt = apply_invoice_scope(select(Invoice), user).where(or_(
            Invoice.invoice_no.ilike(f"%{q}%"), Invoice.lr_no.ilike(f"%{q}%"), Invoice.dispatch_no.ilike(f"%{q}%")))
        f = Filters()
    else:
        f = Filters.from_params(request.query_params, default_range="quarter")
        stmt = filtered_invoices(user, f, include_cancelled=True)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    page = max(int(request.query_params.get("page", 1) or 1), 1)
    pages = max(math.ceil(total / PAGE), 1)
    rows = list(db.scalars(stmt.order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
                           .offset((page - 1) * PAGE).limit(PAGE)))
    sq = stmt.subquery()
    sums = db.execute(select(func.coalesce(func.sum(sq.c.boxes), 0), func.coalesce(func.sum(sq.c.freight_amount), 0),
                             func.coalesce(func.sum(sq.c.sales_value), 0))).one()
    return render(request, "invoices/list.html", {
        "title": "Invoice Register", "rows": rows, "f": f, "opts": filter_options(db, user), "total": total,
        "page": page, "pages": pages, "qs": f.query() + (f"&q={q}" if q else ""), "q": q, "sums": sums})


@router.get("/invoices/{invoice_id:int}")
def detail(invoice_id: int, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require("invoice.view"))):
    inv = get_invoice(db, user, invoice_id)
    overrides = list(db.scalars(select(InvoiceOverride).where(InvoiceOverride.invoice_id == inv.id)
                                .order_by(InvoiceOverride.requested_at.desc())))
    slips = list(db.scalars(select(DispatchSlip).where(DispatchSlip.invoice_id == inv.id).order_by(DispatchSlip.id)))
    prints = list(db.scalars(select(SlipPrintLog).where(SlipPrintLog.invoice_id == inv.id).order_by(SlipPrintLog.id)))
    history = list(db.scalars(select(AuditLog).where(AuditLog.record_type == "Invoice",
                                                     AuditLog.record_id == str(inv.id)).order_by(AuditLog.ts)))
    bill_lines = list(db.execute(select(TransporterBillLine, TransporterBill)
                                 .join(TransporterBill, TransporterBillLine.bill_id == TransporterBill.id)
                                 .where(TransporterBillLine.invoice_id == inv.id)))
    current = find_rate(db, company_id=inv.company_id, cfa_id=inv.cfa_id, customer_id=inv.customer_id,
                        location_id=inv.location_id, on_date=inv.invoice_date)
    return render(request, "invoices/detail.html", {
        "title": f"Invoice {inv.invoice_no}", "inv": inv, "overrides": overrides, "slips": slips, "prints": prints,
        "history": history, "bill_lines": bill_lines, "current": current, "acts": actions_for(db, user, inv),
        "fields": OverrideField.LABELS})


@router.post("/invoices/{invoice_id:int}/override")
def override(invoice_id: int, request: Request, field: str = Form(...), new_value: str = Form(...),
             reason: str = Form(""), back: str = Form(""), db: Session = Depends(get_db),
             user: User = Depends(require("invoice.view"))):
    inv = get_invoice(db, user, invoice_id)
    ov = invoice_service.request_override(db, user, inv, field, new_value, reason)
    db.commit()
    if ov.status == "PENDING":
        flash(request, f"Change request #{ov.id} sent for approval. Invoice is on hold until decided.", "warn")
    else:
        flash(request, f"{OverrideField.LABELS[field]} updated ({ov.old_value or '—'} → {ov.new_value}). Audited.", "success")
    return redirect(safe_path(back, f"/invoices/{inv.id}"))


@router.post("/invoices/{invoice_id:int}/recalculate")
def recalculate(invoice_id: int, request: Request, reason: str = Form(""), back: str = Form(""),
                db: Session = Depends(get_db), user: User = Depends(require("invoice.recalculate"))):
    inv = get_invoice(db, user, invoice_id)
    invoice_service.recalculate(db, user, inv, reason)
    db.commit()
    if inv.freight_amount is not None:
        flash(request, f"Freight recalculated: {inv.boxes} × ₹{inv.rate_per_box} = ₹{inv.freight_amount}", "success")
    else:
        flash(request, f"Still no rate: {inv.exception_reason}", "warn")
    return redirect(safe_path(back, f"/invoices/{inv.id}"))
