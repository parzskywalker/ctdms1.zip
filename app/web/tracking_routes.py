"""Shipment tracking (search + timeline), proof of delivery, e-way bills, and the customer's tracking link."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DeliveryStatus, DispatchSlip, Invoice, InvoiceStatus, Transporter, User
from app.security.auth import csrf_protect, require
from app.services import audit
from app.services import delivery_service as dsv
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from app.services.scope import can_see_invoice
from app.services.utils import local_today, parse_date, safe_int
from app.web.templating import flash, get_form, read_upload, redirect, render

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)
S = InvoiceStatus


def _inv(db: Session, user: User, invoice_id: int) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if not inv or not can_see_invoice(user, inv):
        raise HTTPException(404)
    return inv


# ----------------------------------------------------------------------------- staff: search & timeline
@router.get("/track")
def track(request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    q = (request.query_params.get("q") or "").strip()
    hits = dsv.search(db, user, q) if q else []
    groups = dsv.consignments_of(hits)
    if len(groups) == 1:
        return redirect(f"/track/{groups[0][0].id}")
    rows = []
    for g in groups:
        slip = db.get(DispatchSlip, g[0].dispatch_slip_id) if g[0].dispatch_slip_id else None
        rows.append({"invs": g, "slip": slip, "stage": dsv.stage(g, slip)})
    return render(request, "tracking/search.html", {"title": "Track a shipment", "q": q, "rows": rows})


@router.get("/track/{invoice_id:int}")
def track_detail(invoice_id: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require("dispatch.view"))):
    inv = _inv(db, user, invoice_id)
    invs = dsv.consignment(db, inv)
    slip = db.get(DispatchSlip, inv.dispatch_slip_id) if inv.dispatch_slip_id else None
    return render(request, "tracking/detail.html", {
        "title": f"Tracking · {', '.join(i.invoice_no for i in invs)}", "inv": inv, "invs": invs, "slip": slip,
        "steps": dsv.timeline(db, invs), "stage": dsv.stage(invs, slip), "statuses": DeliveryStatus.LABELS,
        "link": dsv.track_url(db, slip, inv.customer_id) if slip and not slip.is_void else None,
        "eway_limit": cfg.get(db, "gate.eway_required_above"), "due": dsv.due_date(db, inv)})


@router.post("/track/{invoice_id:int}/pod")
def save_pod(invoice_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
             user: User = Depends(require("dispatch.operate"))):
    inv = _inv(db, user, invoice_id)
    invs = dsv.consignment(db, inv)
    name = None
    up = form.get("photo")
    if up is not None and getattr(up, "filename", ""):
        name = dsv.save_pod_file(read_upload(up), up.filename)
    n = dsv.record(db, user, invs, status=form.get("status") or "", delivered_on=form.get("delivered_on"),
                   received_by=form.get("received_by"), note=form.get("note"), pod_file=name, source="STAFF",
                   reason=form.get("reason"))
    db.commit()
    flash(request, f"Delivery recorded for {n} invoice(s)." if n else "No change.", "success" if n else "info")
    return redirect(form.get("back") or f"/track/{inv.id}")


@router.get("/track/{invoice_id:int}/pod-file")
def pod_file(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    inv = _inv(db, user, invoice_id)
    p = dsv.pod_path(inv.pod_file) if inv.pod_file else None
    if not p:
        raise HTTPException(404)
    return FileResponse(p, media_type=dsv.POD_MEDIA.get(p.suffix.lower(), "application/octet-stream"))


@router.post("/track/{invoice_id:int}/eway")
def save_eway(invoice_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
              user: User = Depends(require("dispatch.operate"))):
    """E-way bill number and validity per invoice of the consignment: fields eway_<id>, ewayvalid_<id>."""
    inv = _inv(db, user, invoice_id)
    n = 0
    for i in dsv.consignment(db, inv):
        if f"eway_{i.id}" not in form:
            continue
        no = "".join(ch for ch in (form.get(f"eway_{i.id}") or "") if ch.isdigit()) or None
        if no and len(no) != 12:
            raise BusinessError(f"E-way bill number of invoice {i.invoice_no} must have 12 digits")
        try:
            valid = parse_date(form.get(f"ewayvalid_{i.id}")) if form.get(f"ewayvalid_{i.id}") else None
        except ValueError as e:
            raise BusinessError(f"E-way bill validity: {e}") from e
        if no:
            dup = db.scalar(select(Invoice).where(Invoice.eway_bill_no == no, Invoice.id != i.id))
            if dup:
                raise BusinessError(f"E-way bill {no} is already on invoice {dup.invoice_no}")
        if (i.eway_bill_no, i.eway_valid_until) == (no, valid):
            continue
        audit.log(db, user, "EWAY_BILL_SET", "DISPATCH", record_type="Invoice", record_id=i.id, ref=i.invoice_no,
                  field="eway_bill_no", old=i.eway_bill_no, new=no,
                  details={"valid_until": valid.isoformat() if valid else None})
        i.eway_bill_no, i.eway_valid_until = no, valid
        n += 1
    db.commit()
    flash(request, f"E-way bill saved for {n} invoice(s)." if n else "No change.", "success" if n else "info")
    return redirect(form.get("back") or f"/track/{inv.id}")


# ----------------------------------------------------------------------------- staff: deliveries pending
@router.get("/deliveries")
def deliveries(request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    tid = safe_int(request.query_params.get("transporter_id"))
    overdue = request.query_params.get("overdue") == "1"
    rows = dsv.open_deliveries(db, user, transporter_id=tid, overdue_only=overdue)
    return render(request, "tracking/deliveries.html", {
        "title": "Deliveries pending", "rows": rows, "tid": tid, "overdue": overdue,
        "days": cfg.get(db, "delivery.expected_days"), "today": local_today(),
        "transporters": list(db.scalars(select(Transporter).order_by(Transporter.name)))})


@router.post("/deliveries")
def deliveries_save(request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                    user: User = Depends(require("dispatch.operate"))):
    """Bulk 'delivered' from a bundle of POD copies: ticked consignments (pick=<invoice id>) on one date."""
    picked = [safe_int(v) for v in form.getlist("pick")] if hasattr(form, "getlist") else []
    n, errors = 0, []
    for iid in [p for p in picked if p]:
        inv = db.get(Invoice, iid)
        if not inv or not can_see_invoice(user, inv):
            continue
        try:
            with db.begin_nested():
                n += dsv.record(db, user, dsv.consignment(db, inv), status=DeliveryStatus.DELIVERED,
                                delivered_on=form.get("delivered_on"), received_by=form.get(f"rcv_{iid}"),
                                source="STAFF")
        except BusinessError as e:
            errors.append(f"{inv.invoice_no}: {e.message}")
    db.commit()
    flash(request, f"{n} invoice(s) marked delivered." if n else "Nothing ticked.", "success" if n else "info")
    for e in errors[:5]:
        flash(request, e, "error")
    return redirect("/deliveries" + (f"?transporter_id={safe_int(form.get('transporter_id'))}"
                                     if form.get("transporter_id") else ""))


# ----------------------------------------------------------------------------- customer: public tracking link
def _public(request: Request, db: Session, token: str):
    found = dsv.read_token(db, token)
    if not found:
        return None
    slip, invs = found
    return {"title": "Track your order", "user": None, "slip": slip, "invs": invs, "token": token,
            "steps": dsv.timeline(db, invs, public=True), "stage": dsv.stage(invs, slip),
            "org": cfg.get(db, "org.name"), "statuses": DeliveryStatus.LABELS,
            "can_confirm": bool(cfg.get(db, "delivery.customer_confirm")) and slip.gate_out_at is not None
            and not (invs[0].delivery_status and invs[0].pod_source == "STAFF")}


@router.get("/t/{token}")
def public_track(token: str, request: Request, db: Session = Depends(get_db)):
    ctx = _public(request, db, token)
    if ctx is None:
        return render(request, "tracking/public.html", {"title": "Link expired", "user": None, "expired": True},
                      status_code=404)
    return render(request, "tracking/public.html", ctx)


@router.post("/t/{token}")
def public_confirm(token: str, request: Request, form=Depends(get_form), db: Session = Depends(get_db)):
    ctx = _public(request, db, token)
    if ctx is None or not ctx["can_confirm"]:
        raise HTTPException(404)
    try:
        dsv.record(db, None, ctx["invs"], status=form.get("status") or "", delivered_on=form.get("delivered_on"),
                   received_by=form.get("received_by"), note=form.get("note"), source="CUSTOMER")
    except BusinessError as e:
        db.rollback()
        return render(request, "tracking/public.html", {**ctx, "error": e.message}, status_code=400)
    db.commit()
    return render(request, "tracking/public.html", {**_public(request, db, token), "thanks": True})
