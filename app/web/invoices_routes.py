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
from app.services import settings_service as cfg
from app.services.dispatch_service import actions_for
from app.services.freight_engine import find_rate
from app.services.reports import Filters, filtered_invoices
from app.services.scope import apply_invoice_scope, can_see_invoice
from app.services.utils import safe_int
from app.web.templating import filter_options, flash, get_form, redirect, render, safe_path

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)
PAGE = 50


def get_invoice(db: Session, user: User, invoice_id: int) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404)
    if not can_see_invoice(user, inv):
        raise PermissionDenied(message="This invoice belongs to a company/depot you are not assigned to")
    return inv


@router.get("/invoices")
def register(request: Request, db: Session = Depends(get_db), user: User = Depends(require("invoice.view"))):
    q = (request.query_params.get("q") or "").strip()
    if q:
        stmt = apply_invoice_scope(select(Invoice), user).where(or_(
            func.upper(Invoice.invoice_no) == q.upper(), func.upper(Invoice.lr_no) == q.upper(),
            func.upper(Invoice.dispatch_no) == q.upper(), Invoice.source_invoice_id == q,
            func.upper(Invoice.recognition_code) == q.upper()))
        hits = list(db.scalars(stmt.limit(2)))
        if len(hits) == 1:
            return redirect(f"/invoices/{hits[0].id}")
        stmt = apply_invoice_scope(select(Invoice), user).where(or_(
            Invoice.invoice_no.ilike(f"%{q}%"), Invoice.lr_no.ilike(f"%{q}%"), Invoice.dispatch_no.ilike(f"%{q}%"),
            Invoice.recognition_code.ilike(f"%{q}%")))
        f = Filters()
    else:
        f = Filters.from_params(request.query_params, default_range="quarter")
        stmt = filtered_invoices(user, f, include_cancelled=True)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    page = safe_int(request.query_params.get("page"), 1, minimum=1)
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
    from app.models import DispatchSlipLine
    slips = list(db.scalars(select(DispatchSlip).join(DispatchSlipLine, DispatchSlipLine.slip_id == DispatchSlip.id)
                            .where(DispatchSlipLine.invoice_id == inv.id).order_by(DispatchSlip.id)))
    prints = list(db.scalars(select(SlipPrintLog).where(SlipPrintLog.invoice_id == inv.id).order_by(SlipPrintLog.id)))
    history = list(db.scalars(select(AuditLog).where(AuditLog.record_type == "Invoice",
                                                     AuditLog.record_id == str(inv.id)).order_by(AuditLog.ts)))
    # what the transporter billed for this invoice: only for roles that work with transporter bills
    bill_lines = list(db.execute(select(TransporterBillLine, TransporterBill)
                                 .join(TransporterBill, TransporterBillLine.bill_id == TransporterBill.id)
                                 .where(TransporterBillLine.invoice_id == inv.id))) if user.can("recon.view") else []
    current = find_rate(db, company_id=inv.company_id, cfa_id=inv.cfa_id, customer_id=inv.customer_id,
                        location_id=inv.location_id, on_date=inv.invoice_date)
    return render(request, "invoices/detail.html", {
        "title": f"Invoice {inv.invoice_no}", "inv": inv, "overrides": overrides, "slips": slips, "prints": prints,
        "history": history, "bill_lines": bill_lines, "current": current, "acts": actions_for(db, user, inv),
        "fields": OverrideField.LABELS, "sku_lines": cfg.get(db, "features.sku_lines")})


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


# ----------------------------------------------------------------------------- add an invoice by hand
def _new_form_ctx(db: Session, user: User, form: dict | None = None, errors: list[str] | None = None) -> dict:
    from app.models import Customer, DispatchMode, Transporter
    from app.services.scope import visible_cfas, visible_companies
    companies = visible_companies(db, user)
    cfas = visible_cfas(db, user)
    customers = list(db.scalars(select(Customer).where(Customer.is_active.is_(True), Customer.merged_into_id.is_(None))
                                .order_by(Customer.name)))
    recent = list(db.scalars(apply_invoice_scope(select(Invoice), user)
                             .order_by(Invoice.id.desc()).limit(15)))
    return {"title": "Add invoice", "companies": companies, "cfas": cfas, "customers": customers,
            "areas": {c.id: (c.location.name if c.location else "") for c in customers},
            "customer_types": {c.id: c.type_label for c in customers},
            "transporters": list(db.scalars(select(Transporter).where(Transporter.is_active.is_(True))
                                            .order_by(Transporter.name))),
            "modes": DispatchMode.LABELS, "form": form or {}, "errors": errors or [], "recent": recent}


@router.get("/invoices/new")
def new_invoice(request: Request, db: Session = Depends(get_db), user: User = Depends(require("invoice.import"))):
    return render(request, "invoices/new.html", _new_form_ctx(db, user, dict(request.query_params)))


@router.post("/invoices/new")
def new_invoice_save(request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                     user: User = Depends(require("invoice.import"))):
    """One invoice typed in (when it is neither in an ERP export nor carrying an e-invoice QR). It goes through the
    same checks as an import: duplicates, scope, customer / area, rate card."""
    from app.models import CFA, Company, Customer, Transporter
    from app.services import import_service as imp
    from app.services.errors import BusinessError
    form = dict(form)
    errors: list[str] = []
    co = db.get(Company, safe_int(form.get("company_id")) or 0)
    cfa = db.get(CFA, safe_int(form.get("cfa_id")) or 0)
    cu = db.get(Customer, safe_int(form.get("customer_id")) or 0)
    tr = db.get(Transporter, safe_int(form.get("transporter_id")) or 0) if form.get("transporter_id") else None
    if not co:
        errors.append("Select the company")
    if not cfa:
        errors.append("Select the depot")
    if not cu:
        errors.append("Select the stockist / customer")
    for k, label in (("invoice_no", "Invoice number"), ("invoice_date", "Invoice date"),
                     ("sales_value", "Invoice amount")):
        if not (form.get(k) or "").strip():
            errors.append(f"{label} is required")
    if errors:
        return render(request, "invoices/new.html", _new_form_ctx(db, user, form, errors), status_code=400)
    rec = {"company_code": co.code, "cfa_code": cfa.code, "customer_code": cu.code,
           "invoice_no": form.get("invoice_no"), "invoice_date": form.get("invoice_date"),
           "sales_value": form.get("sales_value"), "taxable_value": form.get("taxable_value") or None,
           "boxes": form.get("boxes") or None, "dispatch_mode": form.get("dispatch_mode") or None,
           "transporter_code": tr.code if tr else None, "remarks": form.get("remarks") or None}
    try:
        with db.begin_nested() as sp:
            batch, result = imp.ingest_records(db, user, None, [rec], source="MANUAL")
            if not result.get("imported"):
                row = batch.rows[0] if batch.rows else None
                errors = [e["msg"] for e in (row.errors if row else [])] or ["The invoice could not be saved"]
                sp.rollback()
    except BusinessError as e:
        errors = [e.message]
    if errors:
        return render(request, "invoices/new.html", _new_form_ctx(db, user, form, errors), status_code=400)
    inv = db.get(Invoice, batch.rows[0].invoice_id)
    db.commit()
    warns = [w["msg"] for w in (batch.rows[0].warnings or []) if w.get("code") not in ("BOXES_MISSING",)]
    flash(request, f"Invoice {inv.invoice_no} added ({inv.central_id}, {inv.status_label})."
          + (" " + " ".join(warns) if warns else ""), "warn" if warns else "success")
    if form.get("next") == "gatepass":
        return redirect(f"/gatepass/new?cfa_id={inv.cfa_id}")
    keep = {k: form.get(k) for k in ("company_id", "cfa_id", "invoice_date", "dispatch_mode", "transporter_id")}
    from urllib.parse import urlencode
    return redirect("/invoices/new?" + urlencode({k: v for k, v in keep.items() if v}))
