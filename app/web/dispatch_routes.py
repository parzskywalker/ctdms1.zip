from __future__ import annotations

import math
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Customer, Invoice, InvoiceStatus, OverrideField, User
from app.security.auth import csrf_protect, require
from app.services import dispatch_service as ds
from app.services import eligibility_service as es
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from app.services.pdf_service import dispatch_slips_pdf
from app.services.reports import Filters, _apply_filters
from app.services.scope import apply_invoice_scope
from app.services.utils import local_now, safe_int
from app.web.invoices_routes import get_invoice
from app.web.templating import active_lists, filter_options, flash, get_form, redirect, render, safe_path, templates

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
        "qs": f.query() + f"&tab={tab}" + (f"&q={quote_plus(q)}" if q else ""), **active_lists(db)})


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
    inv = get_invoice(db, user, invoice_id)
    d, t = ds.default_dispatch_datetime()
    from app.models import InvoiceOverride, OverrideStatus, SlipPrintLog
    pending_ov = db.scalar(select(InvoiceOverride).where(InvoiceOverride.invoice_id == inv.id,
                                                         InvoiceOverride.status == OverrideStatus.PENDING))
    prints = list(db.scalars(select(SlipPrintLog).where(SlipPrintLog.invoice_id == inv.id).order_by(SlipPrintLog.id)))
    acts = ds.actions_for(db, user, inv)
    # The form is pre-filled with today's date / current time and those values are saved when the slip is
    # generated, so "missing" date/time is not a real blocker - don't alarm the operator with it.
    prefilled = {"Dispatch date missing"} if not inv.dispatch_date else set()
    prefilled |= {"Dispatch time missing"} if not inv.dispatch_time else set()
    acts.slip_blockers = [b for b in acts.slip_blockers if b not in prefilled]
    return render(request, "dispatch/entry.html", {
        "title": f"Dispatch · {inv.invoice_no}", "inv": inv, "acts": acts,
        "data_warnings": ds.data_warnings(db, inv) if acts.can_edit else [],
        "default_date": d, "default_time": t, "pending_ov": pending_ov, "prints": prints,
        "slip_size": cfg.get(db, "dispatch.default_slip_size"),
        "fields": OverrideField.LABELS, **(_eligible_lists(db, [inv]) if acts.can_edit else active_lists(db))})


def _warn(request: Request, db, inv) -> None:
    for w in ds.data_warnings(db, inv):
        flash(request, w, "warn")


def _save_from_form(db, user, inv, form) -> None:
    if inv.status in S.PRE_SLIP and user.can("dispatch.operate"):
        ds.save_details(db, user, inv, transporter_id=form.get("transporter_id"), vehicle_no=form.get("vehicle_no"),
                        lr_no=form.get("lr_no"), dispatch_date=form.get("dispatch_date"),
                        dispatch_time=form.get("dispatch_time"), remarks=form.get("remarks"))


@router.post("/dispatch/{invoice_id:int}/save")
def save(invoice_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
               user: User = Depends(require("dispatch.operate"))):
    inv = get_invoice(db, user, invoice_id)
    changed = ds.save_details(db, user, inv, transporter_id=form.get("transporter_id"),
                              vehicle_no=form.get("vehicle_no"), lr_no=form.get("lr_no"),
                              dispatch_date=form.get("dispatch_date"), dispatch_time=form.get("dispatch_time"),
                              remarks=form.get("remarks"))
    db.commit()
    flash(request, "Draft saved." if changed else "No changes.", "success" if changed else "info")
    _warn(request, db, inv)
    return redirect(f"/dispatch/{inv.id}")


@router.post("/dispatch/{invoice_id:int}/verify")
def verify(invoice_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                 user: User = Depends(require("dispatch.verify"))):
    inv = get_invoice(db, user, invoice_id)
    _save_from_form(db, user, inv, form)
    ds.verify(db, user, inv)
    db.commit()
    flash(request, f"Verified: {inv.boxes} boxes × ₹{inv.rate_per_box} = ₹{inv.freight_amount}", "success")
    _warn(request, db, inv)
    return redirect(f"/dispatch/{inv.id}")


def _slip_label(log, slip) -> tuple[str, str]:
    label = "ORIGINAL" if not log.is_reprint else f"REPRINT #{log.print_no - 1} (DUPLICATE)"
    who = log.printed_by.full_name if log.printed_by else ""
    line = f"Print #{log.print_no} by {who} on {local_now():%d-%m-%Y %H:%M}"
    return label, line


def _print_response(request: Request, items: list, size: str, mode: str):
    if mode == "pdf":
        pdf = dispatch_slips_pdf(items, size=size)
        name = items[0][0]["dispatch_no"].replace("/", "-") if len(items) == 1 else "dispatch-slips"
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{name}.pdf"'})
    return templates.TemplateResponse(request, "dispatch/slip_print.html",
                                      {"items": items, "size": size, "request": request})


@router.post("/dispatch/{invoice_id:int}/print")
def print_slip(invoice_id: int, request: Request, size: str = Form("A5"), mode: str = Form("print"),
               reason: str = Form(""), db: Session = Depends(get_db),
               user: User = Depends(require("dispatch.view"))):
    inv = get_invoice(db, user, invoice_id)
    size = "A4" if size == "A4" else "A5"
    mode = "pdf" if mode == "pdf" else "print"
    slip, log = ds.record_print(db, user, inv, f"{'PDF' if mode == 'pdf' else 'PRINT'}_{size}", reason)
    db.commit()
    db.refresh(log)
    return _print_response(request, [(slip.snapshot, *_slip_label(log, slip))], size, mode)


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


# ----------------------------------------------------------------------------- bulk
def _eligible_lists(db: Session, invs: list[Invoice]) -> dict:
    """The transporter drop-down of the quick dispatch screens: only transporters that can carry these invoices (the
    one already chosen stays, so a saved choice never silently disappears). The server re-checks on generation."""
    lists = active_lists(db)
    if invs and len({i.cfa_id for i in invs}) == 1:
        d = (invs[0].dispatch_date or local_now().date())
        ok = {t.id for t in es.eligible(db, invs, d, require_rate=ds.freight_source(db) != "COMPANY")}
        keep = {i.transporter_id for i in invs if i.transporter_id}
        lists["transporters"] = [t for t in lists["transporters"] if t.id in ok or t.id in keep]
    return lists


def _selected(db, user, ids) -> list[Invoice]:
    out = []
    for i in ids:
        try:
            out.append(get_invoice(db, user, int(i)))
        except Exception:  # noqa: BLE001 - skip unknown / out-of-scope ids
            continue
    return out
