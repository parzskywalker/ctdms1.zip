from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    AuditLog, BillStatus, FreightRate, Invoice, InvoiceOverride, InvoiceStatus, OverrideField, OverrideStatus,
    RateStatus, TransporterBill, TransporterRate, User, utcnow,
)
from app.security.auth import csrf_protect, require, require_any
from app.services import invoice_service
from app.services.reports import Filters, rpt_exceptions, rpt_rate_missing
from app.services.scope import apply_bill_scope, apply_invoice_scope, apply_strict_scope, can_see_invoice
from app.services.utils import safe_int
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
        bills = list(db.scalars(apply_bill_scope(select(TransporterBill), user, TransporterBill)
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


@router.get("/approvals/history")
def approval_history(request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_any("override.approve", "rate.approve", "recon.approve"))):
    """What has been decided, by whom and when - only the kinds of item this user may approve, only inside the
    user's company / depot scope. The minimum needed to see how earlier approvals went (no financial reports)."""
    days = min(safe_int(request.query_params.get("days"), 90, minimum=1), 730)
    since = utcnow() - timedelta(days=days)
    rows: list[dict] = []

    if user.can("override.approve"):
        stmt = apply_invoice_scope(select(InvoiceOverride, Invoice).join(Invoice, InvoiceOverride.invoice_id == Invoice.id),
                                   user).where(InvoiceOverride.status.in_([OverrideStatus.APPROVED, OverrideStatus.REJECTED]),
                                               InvoiceOverride.decided_at >= since)
        for ov, inv in db.execute(stmt):
            rows.append({"when": ov.decided_at, "kind": "Box / rate change", "ref": inv.invoice_no,
                         "link": f"/invoices/{inv.id}" if user.can("invoice.view") else None,
                         "what": f"{OverrideField.LABELS.get(ov.field, ov.field)}: {ov.old_value or '—'} → {ov.new_value}",
                         "by": ov.requested_by.full_name if ov.requested_by else "",
                         "decision": ov.status, "decider": ov.decided_by.full_name if ov.decided_by else "",
                         "remarks": ov.decision_remarks or ""})

    rejected: dict[tuple[str, int], AuditLog] = {}

    def rejections(rtype: str, action: str, ids: list[int]) -> None:
        if not ids:
            return
        for a in db.scalars(select(AuditLog).where(AuditLog.action == action, AuditLog.record_type == rtype,
                                                    AuditLog.record_id.in_([str(i) for i in ids]))
                            .order_by(AuditLog.ts)):
            rejected[(rtype, int(a.record_id))] = a

    if user.can("rate.approve"):
        fr = list(db.scalars(apply_strict_scope(select(FreightRate), user, FreightRate).where(
            FreightRate.status.in_([RateStatus.APPROVED, RateStatus.REJECTED, RateStatus.INACTIVE]),
            (FreightRate.approved_at >= since) | (FreightRate.updated_at >= since))))
        rejections("FreightRate", "RATE_REJECTED", [r.id for r in fr if r.status == RateStatus.REJECTED])
        for r in fr:
            rej = rejected.get(("FreightRate", r.id)) if r.status == RateStatus.REJECTED else None
            when = rej.ts if rej else r.approved_at
            if not when or when < since or (r.status == RateStatus.INACTIVE and not r.approved_at):
                continue
            rows.append({"when": when, "kind": "Freight rate", "ref": f"Rate #{r.id}",
                         "link": f"/rates/{r.id}", "what": f"{r.company.code} · {r.cfa.code if r.cfa else 'Any depot'} · "
                         f"{r.location.name if r.location else 'default'} · ₹{r.rate_per_box}/box from {r.effective_from:%d-%m-%Y}",
                         "by": r.created_by.full_name if r.created_by else "",
                         "decision": "REJECTED" if rej else "APPROVED",
                         "decider": (rej.username if rej else (r.approved_by.full_name if r.approved_by else "")) or "",
                         "remarks": (r.rejected_reason if rej else r.change_reason) or ""})
        tr = list(db.scalars(select(TransporterRate).where(
            TransporterRate.status.in_([RateStatus.APPROVED, RateStatus.REJECTED, RateStatus.INACTIVE]),
            (TransporterRate.approved_at >= since) | (TransporterRate.updated_at >= since))))
        rejections("TransporterRate", "TRATE_REJECTED", [r.id for r in tr if r.status == RateStatus.REJECTED])
        for r in tr:
            rej = rejected.get(("TransporterRate", r.id)) if r.status == RateStatus.REJECTED else None
            when = rej.ts if rej else r.approved_at
            if not when or when < since:
                continue
            rows.append({"when": when, "kind": "Transporter rate", "ref": r.transporter.name,
                         "link": f"/rate-cards/{r.transporter_id}",
                         "what": f"{r.location.name} · ₹{r.rate_per_case}/case from {r.effective_from:%d-%m-%Y}",
                         "by": r.created_by.full_name if r.created_by else "",
                         "decision": "REJECTED" if rej else "APPROVED",
                         "decider": (rej.username if rej else (r.approved_by.full_name if r.approved_by else "")) or "",
                         "remarks": (r.rejected_reason if rej else r.change_reason) or ""})

    if user.can("recon.approve"):
        bl = list(db.scalars(apply_bill_scope(select(TransporterBill), user, TransporterBill).where(
            TransporterBill.status.in_([BillStatus.APPROVED, BillStatus.PAID, BillStatus.REJECTED]),
            (TransporterBill.approved_at >= since) | (TransporterBill.updated_at >= since))))
        rejections("TransporterBill", "BILL_REJECTED", [b.id for b in bl if b.status == BillStatus.REJECTED])
        for b in bl:
            rej = rejected.get(("TransporterBill", b.id)) if b.status == BillStatus.REJECTED else None
            when = rej.ts if rej else b.approved_at
            if not when or when < since:
                continue
            rows.append({"when": when, "kind": "Transporter bill", "ref": b.bill_no, "link": f"/recon/{b.id}",
                         "what": f"{b.transporter.name} · bill dated {b.bill_date:%d-%m-%Y}"
                                 + (" · paid" if b.status == BillStatus.PAID else ""),
                         "by": b.created_by.full_name if b.created_by else "",
                         "decision": "REJECTED" if rej else "APPROVED",
                         "decider": (rej.username if rej else (b.approved_by.full_name if b.approved_by else "")) or "",
                         "remarks": (rej.reason if rej else "") or ""})

    rows.sort(key=lambda r: r["when"], reverse=True)
    return render(request, "approvals_history.html", {"title": "Approval History", "rows": rows[:500], "days": days,
                                                      "truncated": len(rows) > 500})
