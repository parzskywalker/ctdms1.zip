"""Gatepass desk: scan invoices -> one gatepass per pickup -> print -> gate-out; LR numbers afterwards."""
from __future__ import annotations

import math
from collections import OrderedDict
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    CaseScan, DispatchAllocation, DispatchMode, DispatchSlip, Invoice, InvoiceStatus, LabelPrintLog, Location, OutsideAuto, PickupMode, SlipPrintLog,
    Transporter, User,
)
from app.security.auth import csrf_protect, require
from app.services import case_service as cs
from app.services import pawati_service as pws
from app.services import dispatch_service as ds
from app.services import local_auto_service as las
from app.services import einvoice_service as einv
from app.services import eligibility_service as es
from app.services import settings_service as cfg
from app.services import transporter_rate_service as trs
from app.services.errors import BusinessError
from app.services.pdf_service import dispatch_slips_pdf
from app.services.scope import apply_invoice_scope, apply_slip_scope, can_see, can_see_invoice, visible_cfas
from app.services.utils import local_now, local_today, parse_date, parse_time, safe_int
from app.web.templating import flash, get_form, redirect, render, templates

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)
S = InvoiceStatus


def _slip(db: Session, user: User, slip_id: int) -> DispatchSlip:
    slip = db.get(DispatchSlip, slip_id)
    if not slip or (slip.cfa_id and not can_see(user, None, slip.cfa_id)):
        raise HTTPException(404)
    invs = ds.slip_invoices(db, slip)
    if invs and not all(can_see_invoice(user, i) for i in invs):
        raise HTTPException(404)
    return slip


def _find_slip(db: Session, user: User, text: str) -> DispatchSlip | None:
    t = (text or "").strip().upper()
    if not t:
        return None
    stmt = apply_slip_scope(select(DispatchSlip), user)
    if len(t) == 10 and t.isdigit():  # a case label: its gatepass (before the "last digits" shortcut below)
        case = cs.find_case(db, t)
        if case:
            slip = db.scalar(stmt.where(DispatchSlip.id == case.slip_id))
            if slip:
                return slip
    slip = db.scalar(stmt.where(func.upper(DispatchSlip.dispatch_no) == t))
    if slip:
        return slip
    if t.isdigit():  # the last digits printed under the barcode
        rows = list(db.scalars(stmt.where(DispatchSlip.dispatch_no.like(f"%/{int(t):05d}"),
                                          DispatchSlip.is_void.is_(False)).order_by(DispatchSlip.id.desc()).limit(2)))
        if len(rows) == 1:
            return rows[0]
    return None


def _inv_json(db: Session, inv: Invoice) -> dict:
    blocked = None
    if inv.status not in S.PRE_SLIP:
        blocked = f"already on gatepass {inv.dispatch_no}" if inv.dispatch_no else inv.status_label
    elif inv.exception_code in ("OVERRIDE_PENDING", "RATE_CONFLICT"):
        blocked = inv.exception_reason
    elif inv.status == S.RATE_MISSING and ds.freight_source(db) == "COMPANY":
        blocked = inv.exception_reason
    cu = inv.customer
    return {"id": inv.id, "invoice_no": inv.invoice_no, "date": inv.invoice_date.strftime("%d-%m-%Y"),
            "customer": cu.name, "customer_id": cu.id, "type": cu.type_label, "location": inv.location.name,
            "location_id": inv.location_id, "company": inv.company.code, "company_id": inv.company_id,
            "cfa_id": inv.cfa_id, "rcode": inv.recognition_code or "",
            "amount": float(inv.sales_value or 0), "cases": inv.boxes, "status": inv.status_label,
            "company_freight": float(inv.freight_amount) if inv.freight_amount is not None else None,
            "company_rate": float(inv.rate_per_box) if inv.rate_per_box is not None else None,
            "blocked": blocked, "capture": inv.capture_mode,
            "plan_mode": inv.dispatch_mode if inv.status in S.PRE_SLIP else None,
            "plan_tr": inv.transporter_id if inv.status in S.PRE_SLIP else None}


def _cards(db: Session, on) -> dict:
    """Per active transporter: every effective rate as [area, depot or 0, company or 0, rate] (the screen prices a line
    with the most specific row, like the server) and the depots it serves (None = all)."""
    out = {}
    for t in db.scalars(select(Transporter).where(Transporter.is_active.is_(True))):
        served = es.depot_ids(db, t.id)
        out[str(t.id)] = {"rows": [[lo, cf, co, float(rt)] for lo, cf, co, rt in trs.card_rows(db, t.id, on)],
                          "serves": sorted(served) if served else None}
    return out


def _builder(request: Request, db: Session, user: User, *, ids: list[int] | None = None, form=None,
             problems: list[str] | None = None, status_code: int = 200):
    today = local_today()
    cfas = visible_cfas(db, user)
    cfa_id = safe_int((form or {}).get("cfa_id") if form else request.query_params.get("cfa_id")) or \
        (cfas[0].id if len(cfas) == 1 else None)
    open_stmt = apply_invoice_scope(select(Invoice), user).where(Invoice.status.in_(S.PRE_SLIP))
    if cfa_id:
        open_stmt = open_stmt.where(Invoice.cfa_id == cfa_id)
    open_invs = list(db.scalars(open_stmt.order_by(Invoice.invoice_date, Invoice.id).limit(400)))
    chosen = []
    for i in ids or []:
        inv = db.get(Invoice, i)
        if inv and can_see_invoice(user, inv):
            row = _inv_json(db, inv)
            if form is not None and form.get(f"cases_{inv.id}"):
                row["cases"] = safe_int(form.get(f"cases_{inv.id}")) or row["cases"]
            chosen.append(row)
    transporters = list(db.scalars(select(Transporter).where(Transporter.is_active.is_(True)).order_by(Transporter.name)))
    autos = list(db.scalars(select(OutsideAuto).where(OutsideAuto.is_active.is_(True)).order_by(OutsideAuto.name)))
    return render(request, "gatepass/new.html", {
        "title": "New gatepass", "open_invs": [_inv_json(db, i) for i in open_invs], "chosen": chosen,
        "transporters": transporters, "autos": autos, "cfas": cfas, "cfa_id": cfa_id,
        "cards": _cards(db, today), "modes": DispatchMode.LABELS, "form": form or {}, "problems": problems or [],
        "freight_source": ds.freight_source(db), "today": today,
        "locations": {lo.id: lo.name for lo in db.scalars(select(Location))},
        "auto_info": {str(a.id): {"methods": a.methods, "default": a.default_method,
                                  "trip": float(a.default_trip_charge) if a.default_trip_charge else None,
                                  "box": float(a.default_box_rate) if a.default_box_rate else None,
                                  "vehicle": a.vehicle_no or ""} for a in autos},
        "auto_rates": las.rates_for_screen(db, autos, today),
        "tr_info": {str(t.id): {"pay": t.payment_mode, "pickup": float(t.direct_pickup_charge or 0),
                                "pickup_basis": t.direct_pickup_basis} for t in transporters},
        "cash_default": cfg.get(db, "gatepass.cash_paid_default"),
        "can_override_elig": user.can("transporter.override_eligibility"),
        "can_group_cross": user.can("dispatch.group_cross_company")}, status_code=status_code)


@router.api_route("/gatepass/new", methods=["GET"])
def new_gatepass(request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.operate"))):
    ids = [safe_int(x) for x in request.query_params.getlist("ids")]
    return _builder(request, db, user, ids=[i for i in ids if i])


@router.post("/gatepass/prepare")
def prepare(request: Request, form=Depends(get_form), db: Session = Depends(get_db),
            user: User = Depends(require("dispatch.operate"))):
    """From the pending-dispatch list: selected invoices open in the gatepass builder."""
    ids = [safe_int(x) for x in form.getlist("ids")]
    return _builder(request, db, user, ids=[i for i in ids if i])


@router.post("/gatepass/scan")
def scan(request: Request, form=Depends(get_form), db: Session = Depends(get_db),
         user: User = Depends(require("dispatch.operate"))):
    """AJAX: a scanned e-invoice QR / typed invoice number -> the invoice row for the gatepass (created from the QR
    when it is not in CTDMS yet)."""
    text = form.get("code") or ""
    try:
        res = einv.scan(db, user, text, cfa_id=safe_int(form.get("cfa_id")), cases=form.get("cases"))
    except BusinessError as e:
        db.rollback()
        return JSONResponse({"ok": False, "message": e.message})
    if res.invoice is None:
        db.rollback()
        out = {"ok": False, "message": res.message, "missing": res.missing}
        if res.qr and res.missing == "customer":
            out["add_url"] = f"/masters/customers/new?gstin={res.qr.buyer_gstin}&back=/gatepass/new"
        if res.qr and res.missing == "company":
            out["add_url"] = "/masters/companies"
        return JSONResponse(out)
    db.commit()
    return JSONResponse({"ok": True, "created": res.created, "message": res.message,
                         "invoice": _inv_json(db, res.invoice)})


def _request_from(form) -> ds.GatepassRequest:
    try:
        d = parse_date(form.get("dispatch_date")) if form.get("dispatch_date") else local_today()
        t = parse_time(form.get("dispatch_time")) if form.get("dispatch_time") else None
    except ValueError as e:
        raise BusinessError(str(e)) from e
    paid_raw = (form.get("amount_paid") or "").strip()
    req = ds.GatepassRequest(
        mode=form.get("mode") or DispatchMode.TRANSPORTER, transporter_id=safe_int(form.get("transporter_id")),
        outside_auto_id=safe_int(form.get("outside_auto_id")),
        pickup_mode=PickupMode.OUTSIDE_AUTO if form.get("pickup_mode") == PickupMode.OUTSIDE_AUTO else PickupMode.REGULAR,
        dispatch_date=d, dispatch_time=t, cases={}, remarks=form.get("remarks"),
        amount_paid=ds.parse_money(paid_raw) if paid_raw and form.get("paid_edited") == "1" else None,
        auto_method=(form.get("auto_method") or None), vehicle_no=(form.get("vehicle_no") or "").strip() or None,
        group_cross_company=form.get("group_cross_company") == "1",
        eligibility_override_reason=(form.get("eligibility_override_reason") or "").strip() or None)
    trip_raw = (form.get("trip_charge") or "").strip()
    if trip_raw:
        req.trip_charge = ds.parse_money(trip_raw)
        if req.trip_charge is not None and ds.parse_decimal(trip_raw) != req.trip_charge:
            raise BusinessError(f"Trip charge \"{trip_raw}\" is not a rupee amount - type it like 500 or 450.50")
    return req


@router.post("/gatepass/new")
def create(request: Request, form=Depends(get_form), db: Session = Depends(get_db),
           user: User = Depends(require("dispatch.operate"))):
    ids = []
    for x in form.getlist("ids"):
        i = safe_int(x)
        if i and i not in ids:
            ids.append(i)
    try:
        req = _request_from(form)
        invs = []
        for i in ids:
            inv = db.get(Invoice, i)
            if not inv or not can_see_invoice(user, inv):
                raise BusinessError("An invoice on the list is not available any more - reload")
            invs.append(inv)
            req.cases[inv.id] = form.get(f"cases_{inv.id}")
        recv = {}
        for inv in invs:  # v1.7 local-auto path: recv_<invoice> = "12" (one receiver) or "12:3,14:2" (split)
            raw = (form.get(f"recv_{inv.id}") or "").strip()
            if raw:
                n_all = safe_int(form.get(f"cases_{inv.id}")) or inv.boxes or 0
                parts = [x.split(":") for x in raw.split(",") if x.strip()]
                recv[inv.id] = [(safe_int(p[0]), safe_int(p[1]) if len(p) > 1 else n_all) for p in parts]
        req.receivers = recv or None
        slip = ds.create_gatepass(db, user, invs, req)
    except BusinessError as e:
        db.rollback()
        problems = [p.strip() for p in e.message.replace("Cannot generate gatepass: ", "").split(";") if p.strip()]
        return _builder(request, db, user, ids=ids, form=form, problems=problems, status_code=200)
    for w in getattr(slip, "lr_warnings", []) or []:
        flash(request, w, "warn")
    db.commit()
    flash(request, f"Gatepass {slip.dispatch_no} generated: {slip.invoice_count} invoice(s), {slip.total_cases} cases, "
                   f"₹{slip.total_charge:,.2f}. Print it - the goods may leave only with the printed gatepass.",
          "success")
    return redirect(f"/gatepass/{slip.id}")


# ----------------------------------------------------------------------------- register / detail
STATE_FILTERS = {"open": "Not yet out", "out": "Gone out", "void": "Void", "all": "All"}


@router.get("/gatepass")
def register(request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    qp = request.query_params
    state = qp.get("state", "all") if qp.get("state") in STATE_FILTERS else "all"
    try:
        d_from = parse_date(qp.get("date_from")) if qp.get("date_from") else local_today() - timedelta(days=30)
        d_to = parse_date(qp.get("date_to")) if qp.get("date_to") else local_today()
    except ValueError:
        d_from, d_to = local_today() - timedelta(days=30), local_today()
    stmt = apply_slip_scope(select(DispatchSlip), user).where(
        DispatchSlip.dispatch_date >= d_from, DispatchSlip.dispatch_date <= d_to)
    tid = safe_int(qp.get("transporter_id"))
    if tid:
        stmt = stmt.where(DispatchSlip.transporter_id == tid)
    if qp.get("mode") in DispatchMode.ALL:
        stmt = stmt.where(DispatchSlip.dispatch_mode == qp.get("mode"))
    if state == "open":
        stmt = stmt.where(DispatchSlip.is_void.is_(False), DispatchSlip.gate_out_at.is_(None))
    elif state == "out":
        stmt = stmt.where(DispatchSlip.gate_out_at.isnot(None))
    elif state == "void":
        stmt = stmt.where(DispatchSlip.is_void.is_(True))
    q = (qp.get("q") or "").strip()
    if q:
        sub = select(Invoice.dispatch_slip_id).where(or_(Invoice.invoice_no.ilike(f"%{q}%"), Invoice.lr_no.ilike(f"%{q}%")))
        stmt = stmt.where(or_(DispatchSlip.dispatch_no.ilike(f"%{q}%"), DispatchSlip.id.in_(sub)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    page = safe_int(qp.get("page"), 1, minimum=1)
    per = 50
    slips = list(db.scalars(stmt.order_by(DispatchSlip.dispatch_date.desc(), DispatchSlip.id.desc())
                            .offset((page - 1) * per).limit(per)))
    lr_missing = {}
    for s in slips:
        if s.transporter_id and not s.is_void:
            lr_missing[s.id] = db.scalar(select(func.count(Invoice.id)).where(
                Invoice.dispatch_slip_id == s.id, Invoice.lr_no.is_(None), Invoice.status != S.CANCELLED)) or 0
    live = stmt.where(DispatchSlip.is_void.is_(False)).subquery()
    sums = db.execute(select(func.count(live.c.id), func.coalesce(func.sum(live.c.total_cases), 0),
                             func.coalesce(func.sum(live.c.total_charge), 0),
                             func.coalesce(func.sum(live.c.amount_paid), 0))).one() if total else (0, 0, 0, 0)
    qs = "&".join(f"{k}={v}" for k, v in qp.items() if k != "page")
    return render(request, "gatepass/list.html", {
        "title": "Gatepasses", "slips": slips, "state": state, "states": STATE_FILTERS, "d_from": d_from, "d_to": d_to,
        "transporters": list(db.scalars(select(Transporter).order_by(Transporter.name))), "tid": tid,
        "mode": qp.get("mode", ""), "modes": DispatchMode.LABELS, "q": q, "total": total, "page": page,
        "pages": max(math.ceil(total / per), 1), "qs": qs, "lr_missing": lr_missing, "sums": sums})


def _lr_groups(db: Session, slip: DispatchSlip, invs: list) -> "OrderedDict":
    """The consignments of a gatepass as the LR screen shows them: customer (and, when several transporters receive,
    customer + receiver). An invoice split between two receivers appears once under each, with that receiver's cases,
    rate and freight; the invoice itself carries the main receiver's LR, the other part's LR lives on its allocation."""
    from types import SimpleNamespace
    multi = not slip.transporter_id and slip.dispatch_mode == "TRANSPORTER"
    line_by_inv = {ln.invoice_id: ln for ln in slip.lines}
    parts: dict = {}
    if multi:
        for a in db.scalars(select(DispatchAllocation).where(DispatchAllocation.slip_id == slip.id)
                            .order_by(DispatchAllocation.id)):
            parts.setdefault(a.invoice_id, []).append(a)
    groups: OrderedDict = OrderedDict()

    def grp(inv, key, receiver):
        return groups.setdefault(key, {"customer": inv.customer, "location": inv.location, "invs": [], "key": key,
                                       "receiver": receiver, "prim": [], "sec": [], "lr": None})

    for inv in invs:
        ln = line_by_inv.get(inv.id)
        split = parts.get(inv.id, [])
        if multi and len(split) > 1:
            for a in split:
                g = grp(inv, f"{inv.customer_id}_{a.receiving_transporter_id}", a.transporter)
                g["invs"].append((inv, SimpleNamespace(cases=a.cases, rate_per_case=a.rate_per_case, freight=a.freight,
                                                       extra_charge=0, calc_note=a.calc_note,
                                                       consignment_no=ln.consignment_no if ln else None)))
                (g["prim"] if a.receiving_transporter_id == inv.transporter_id else g["sec"]).append(
                    inv if a.receiving_transporter_id == inv.transporter_id else a)
        else:
            key = inv.customer_id if not multi else f"{inv.customer_id}_{inv.transporter_id}"
            g = grp(inv, key, inv.transporter if multi else None)
            g["invs"].append((inv, ln))
            g["prim"].append(inv)
    for g in groups.values():
        g["lr"] = g["prim"][0] if g["prim"] else g["sec"][0]
    return groups


@router.get("/gatepass/{slip_id:int}")
def detail(slip_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    slip = _slip(db, user, slip_id)
    invs = ds.slip_invoices(db, slip)
    groups = _lr_groups(db, slip, invs)
    prints = list(db.scalars(select(SlipPrintLog).where(SlipPrintLog.slip_id == slip.id)
                             .order_by(SlipPrintLog.id)))
    seen, print_events = set(), []
    for p in prints:
        if p.print_no not in seen:
            seen.add(p.print_no)
            print_events.append(p)
    complete_blockers = sorted({b for i in invs for b in ds.completion_blockers(db, i) if b != "Already dispatched"}) \
        if not slip.gate_out_at and not slip.is_void else []
    for g in groups.values():
        g["locked"] = next((r for r in (ds.lr_locked_reason(db, inv) for inv in g["prim"]) if r), None)
    from app.models import Notification
    alerts = list(db.scalars(select(Notification).where(Notification.slip_id == slip.id).order_by(Notification.id)))
    cases = cs.cases_of(db, slip, include_void=True) if slip.case_control == "REQUIRED" else []
    return render(request, "gatepass/detail.html", {
        "pawatis": pws.active(db, slip) if pws.applies(slip) else [],
        "labels_pending": cs.labels_pending(db, slip),
        "gate_prog": cs.progress(db, slip) if slip.case_control == "REQUIRED" else None,
        "cases": cases, "case_counts": cs.counts(db, slip) if cases else {},
        "label_log": list({lg.batch_no: lg for lg in db.scalars(select(LabelPrintLog).where(
            LabelPrintLog.slip_id == slip.id).order_by(LabelPrintLog.id.desc()))}.values()) if cases else [],
        "title": f"Gatepass {slip.dispatch_no}", "slip": slip, "groups": list(groups.values()), "invs": invs,
        "print_events": print_events, "complete_blockers": complete_blockers, "alerts": alerts,
        "needs_conversion": cs.needs_conversion(slip), "slip_size": cfg.get(db, "dispatch.default_slip_size")})


@router.get("/gatepass/{slip_id:int}/labels")
def case_labels(slip_id: int, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require("dispatch.view"))):
    """One label per case: gatepass barcode (scanning any case at the gate opens its gatepass), customer, area,
    invoices, carrier and 'case k of N' per consignment."""
    slip = _slip(db, user, slip_id)
    if slip.is_void:
        raise BusinessError("This gatepass is void - no labels")
    if slip.case_control == "REQUIRED":
        return _case_labels(request, db, user, slip)
    if cs.needs_conversion(slip):
        raise BusinessError(f"Gatepass {slip.dispatch_no} has no case labels yet - choose Make the case labels for this "
                            "gatepass on the gatepass screen")
    line_by_inv = {ln.invoice_id: ln for ln in slip.lines}
    groups: OrderedDict = OrderedDict()
    for inv in ds.slip_invoices(db, slip):
        if inv.status != S.CANCELLED:
            groups.setdefault(inv.customer_id, []).append(inv)
    carrier = slip.transporter.name if slip.transporter else (slip.outside_auto.name if slip.outside_auto
                                                               else "Self pickup")
    labels = []
    for invs in groups.values():
        n = sum((line_by_inv[i.id].cases if i.id in line_by_inv else i.boxes or 0) for i in invs)
        for k in range(1, n + 1):
            labels.append({"k": k, "n": n, "customer": invs[0].customer, "location": invs[0].location,
                           "invoices": ", ".join(i.invoice_no for i in invs), "lr": invs[0].lr_no})
    return templates.TemplateResponse(request, "gatepass/labels.html", {
        "request": request, "slip": slip, "labels": labels[:2000], "carrier": carrier,
        "org": cfg.get(db, "org.name"), "sheet": request.query_params.get("sheet") == "a4"})


def _case_labels(request: Request, db: Session, user: User, slip: DispatchSlip):
    """v1.7: one label per case, each with its own barcode. Default = the labels not printed yet; ?show=all lists every
    case (printing labels that were already printed is a reprint: it needs a reason). ?from= / ?to= pick a range."""
    qp = request.query_params
    allc = cs.cases_of(db, slip)
    pending = [c for c in allc if c.status == "LABEL_PENDING"]
    show = qp.get("show") or ("pending" if pending else "all")
    chosen = allc if show == "all" else pending
    lo, hi = safe_int(qp.get("from"), 1, minimum=1), safe_int(qp.get("to"), len(allc) or 1, minimum=1)
    pos = {c.id: i + 1 for i, c in enumerate(allc)}
    chosen = [c for c in chosen if lo <= pos[c.id] <= hi]
    carrier = slip.transporter.name if slip.transporter else (slip.outside_auto.name if slip.outside_auto
                                                               else "Self pickup")
    log = list(db.scalars(select(LabelPrintLog).where(LabelPrintLog.slip_id == slip.id).order_by(LabelPrintLog.id.desc())
                          .limit(50)))
    return templates.TemplateResponse(request, "gatepass/labels_cases.html", {
        "request": request, "slip": slip, "rows": cs.label_rows(db, slip, chosen), "show": show, "total": len(allc),
        "pending": len(pending), "carrier": carrier, "org": cfg.get(db, "org.name"), "pos": pos,
        "sheet": qp.get("sheet") == "a4", "w": cfg.get(db, "labels.width_mm") or 100,
        "h": cfg.get(db, "labels.height_mm") or 50, "reprint": show == "all" or any(
            c.status != "LABEL_PENDING" for c in chosen), "log": log, "csrf": request.session.get("csrf", ""),
        "can_print": user.can("case.print"), "can_reprint": user.can("case.reprint"), "lo": lo, "hi": hi,
        "ids": ",".join(str(c.id) for c in chosen)})


@router.post("/gatepass/{slip_id:int}/labels/confirm")
def labels_confirm(slip_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                   user: User = Depends(require("dispatch.view"))):
    slip = _slip(db, user, slip_id)
    ids = [i for i in (safe_int(x) for x in (form.get("case_ids") or "").split(",")) if i]
    batch = cs.record_print(db, user, slip, ids, result=form.get("result") or "", reason=form.get("reason"))
    db.commit()
    flash(request, f"Label batch {batch} recorded ({'printed' if form.get('result') == 'PRINTED' else 'FAILED - print again'}).",
          "success" if form.get("result") == "PRINTED" else "warn")
    return redirect(f"/gatepass/{slip.id}")


def _label(slip: DispatchSlip, log: SlipPrintLog) -> tuple[str, str]:
    label = "ORIGINAL" if not log.is_reprint else f"REPRINT #{log.print_no - 1} (DUPLICATE)"
    who = log.printed_by.full_name if log.printed_by else ""
    return label, f"Print #{log.print_no} by {who} on {local_now():%d-%m-%Y %H:%M}"


def print_response(request: Request, items: list, size: str, mode: str):
    if mode == "pdf":
        pdf = dispatch_slips_pdf(items, size=size)
        name = items[0][0]["dispatch_no"].replace("/", "-") if len(items) == 1 else "gatepasses"
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{name}.pdf"'})
    return templates.TemplateResponse(request, "dispatch/slip_print.html",
                                      {"items": items, "size": size, "request": request})


@router.post("/gatepass/{slip_id:int}/print")
def print_slip(slip_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
               user: User = Depends(require("dispatch.view"))):
    slip = _slip(db, user, slip_id)
    size = "A4" if form.get("size") == "A4" else "A5"
    mode = "pdf" if form.get("mode") == "pdf" else "print"
    log = ds.print_gatepass(db, user, slip, f"{'PDF' if mode == 'pdf' else 'PRINT'}_{size}", form.get("reason"))
    db.commit()
    db.refresh(log)
    return print_response(request, [(slip.snapshot, *_label(slip, log))], size, mode)


@router.post("/gatepass/{slip_id:int}/make-labels")
def make_labels(slip_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("case.print"))):
    """A gatepass made before case labels were mandatory gets its case labels (one barcode per carton still to leave)."""
    slip = _slip(db, user, slip_id)
    n = cs.convert_legacy(db, user, slip)
    db.commit()
    flash(request, f"{n} case label(s) made for gatepass {slip.dispatch_no}. Print the labels, then print the gatepass.",
          "success")
    return redirect(f"/gatepass/{slip.id}")


@router.post("/gatepass/{slip_id:int}/void")
def void(slip_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
         user: User = Depends(require("dispatch.void_slip"))):
    slip = _slip(db, user, slip_id)
    n = ds.void_gatepass(db, user, slip, form.get("reason") or "")
    db.commit()
    flash(request, f"Gatepass {slip.dispatch_no} voided; {n} invoice(s) are back in the dispatch queue.", "warn")
    return redirect(f"/gatepass/{slip.id}")


@router.post("/gatepass/{slip_id:int}/lr")
def save_lr(slip_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
            user: User = Depends(require("dispatch.operate"))):
    """LR per consignment (customer) on this gatepass: fields lr_<customer_id>, lrdate_<customer_id>."""
    slip = _slip(db, user, slip_id)
    invs = [i for i in ds.slip_invoices(db, slip) if i.status != S.CANCELLED]
    n = 0
    for key, g in _lr_groups(db, slip, invs).items():   # several receiving transporters: one LR per customer AND transporter
        field = f"lr_{key}"
        if field not in form:
            continue
        lr = (form.get(field) or "").strip().upper() or None
        d = form.get(f"lrdate_{key}") or None
        group = g["prim"]
        if group and not (all(i.lr_no == lr for i in group) and (not d or all(str(i.lr_date) == d for i in group))):
            n += ds.set_lr(db, user, group, lr, d, reason=form.get("reason"))
        if g["sec"]:
            n += ds.set_alloc_lr(db, user, slip, g["sec"], lr, d, reason=form.get("reason"))
    db.commit()
    flash(request, f"LR saved for {n} invoice(s)." if n else "No LR changes.", "success" if n else "info")
    return redirect(form.get("back") or f"/gatepass/{slip.id}")


# ----------------------------------------------------------------------------- gate check & LR pending
@router.get("/gatepass/gate")
def gate(request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    code = (request.query_params.get("code") or "").strip()
    slip = _find_slip(db, user, code) if code else None
    invs = ds.slip_invoices(db, slip) if slip else []
    blockers = sorted({b for i in invs for b in ds.completion_blockers(db, i) if b != "Already dispatched"}) \
        if slip and not slip.gate_out_at and not slip.is_void else []
    waiting = list(db.scalars(apply_slip_scope(select(DispatchSlip), user).where(
        DispatchSlip.is_void.is_(False), DispatchSlip.gate_out_at.is_(None)).order_by(DispatchSlip.id.desc()).limit(30)))
    prog = cs.progress(db, slip) if slip and slip.case_control == "REQUIRED" else None
    # cases still to scan is the only thing in the way: that is progress, not a stop (the counter shows how far they are)
    case_block = cs.gate_clear(db, slip) if prog and not slip.gate_out_at and not slip.is_void else []
    scanning = bool(case_block) and blockers == case_block
    # the gatepass barcode scanned into this screen marks the trip out - but only when every case is already scanned, and never
    # when the screen is merely opened from the list (?open=1) or after an exception was recorded
    finish = bool(slip and prog and user.can("gate.scan") and code.upper() == slip.dispatch_no.upper()
                  and not slip.gate_out_at and not slip.is_void and not case_block
                  and request.query_params.get("open") != "1")
    return render(request, "gatepass/gate.html", {"title": "Gate check", "code": code, "slip": slip, "invs": invs,
                                                  "blockers": blockers, "waiting": waiting, "prog": prog,
                                                  "scanning": scanning,
                                                  "autoscan": code if cs.is_valid(code) and slip else "",
                                                  "autofinish": finish,
                                                  "can_exception": user.can("gate.exception"),
                                                  "not_found": bool(code and not slip)})


def _scan_json(db: Session, slip: DispatchSlip, res: dict | None) -> dict:
    p = cs.progress(db, slip)
    blockers = sorted({b for i in ds.slip_invoices(db, slip) if i.status != S.CANCELLED
                       for b in ds.completion_blockers(db, i) if b != "Already dispatched"}) \
        if not slip.gate_out_at and not slip.is_void else []
    out = {"scanned": p["scanned"], "total": p["total"], "complete": p["complete"], "exception": p["exception"],
           "cleared": not blockers and not slip.is_void and not slip.gate_out_at, "blockers": blockers,
           "per_invoice": [{"invoice": r["invoice"].invoice_no, "scanned": r["scanned"], "total": r["total"]}
                           for r in p["per_invoice"]],
           "missing": [{"code": c.case_code, "invoice": c.invoice.invoice_no, "k": c.seq_in_consignment,
                        "n": c.consignment_total} for c in p["missing"]][:200]}
    if res is not None:
        out.update(result=res["result"], ok=res["ok"], message=res["message"])
    return out


@router.post("/gatepass/scan-case")
def scan_case(request: Request, form=Depends(get_form), db: Session = Depends(get_db),
              user: User = Depends(require("gate.scan"))):
    """The gate staff's scanner: a case code (optionally inside a gatepass the screen has open) - or, after the last
    case, the gatepass itself, which marks a case-control gatepass gone out. Always recorded."""
    slip = _slip(db, user, safe_int(form.get("slip_id"))) if form.get("slip_id") else None
    code = (form.get("code") or "").strip()
    station = form.get("station") or None
    if not cs.is_valid(code):
        gp = db.scalar(apply_slip_scope(select(DispatchSlip), user).where(func.upper(DispatchSlip.dispatch_no) == code.upper())) \
            if code else None
        if gp is not None:
            return JSONResponse(_scan_gatepass(db, user, gp, slip, code, station))
    res = cs.scan(db, user, code, slip, station=station)
    db.commit()
    target = slip or (_slip(db, user, res["slip_id"]) if res["slip_id"] else None)
    if target is None:
        return JSONResponse({"result": res["result"], "ok": False, "message": res["message"]})
    payload = _scan_json(db, target, res)
    payload["slip_id"] = target.id
    payload["dispatch_no"] = target.dispatch_no
    return JSONResponse(payload)


def _scan_gatepass(db: Session, user: User, gp: DispatchSlip, open_slip: DispatchSlip | None, code: str,
                   station: str | None) -> dict:
    """The gatepass barcode scanned at the gate: marks the trip gone out when everything is in order."""
    if open_slip is not None and open_slip.id != gp.id:
        res = {"result": "WRONG_GATEPASS", "ok": False,
               "message": f"This is gatepass {gp.dispatch_no} - the screen is on {open_slip.dispatch_no}"}
    else:
        try:
            n = ds.gate_scan_out(db, user, gp)
            res = {"result": "GONE_OUT", "ok": True, "gone_out": True,
                   "message": f"Gatepass {gp.dispatch_no} GONE OUT - {n} invoice(s), every case scanned"}
        except BusinessError as e:
            res = {"result": "NOT_CLEARED", "ok": False, "message": e.message}
    db.add(CaseScan(slip_id=gp.id, case_id=None, scanned_code=code[:60], result=res["result"],
                    scanned_by_id=user.id, station=station))
    db.commit()  # also keeps the GATE_OUT_BLOCKED audit entry of a refusal
    payload = _scan_json(db, gp, res)
    payload.update(slip_id=gp.id, dispatch_no=gp.dispatch_no, gone_out=bool(res.get("gone_out")))
    return payload


@router.post("/gatepass/{slip_id:int}/gate-exception")
def gate_exception(slip_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                   user: User = Depends(require("dispatch.view"))):
    slip = _slip(db, user, slip_id)
    n = cs.record_exception(db, user, slip, form.get("reason") or "")
    db.commit()
    flash(request, f"Exception recorded: {n} case(s) marked missing. Now the gate staff scan the gatepass to let it leave.", "warn")
    return redirect(f"/gatepass/gate?code={slip.dispatch_no}&open=1")


@router.get("/gatepass/lr-pending")
def lr_pending(request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    tid = safe_int(request.query_params.get("transporter_id"))
    stmt = apply_invoice_scope(select(Invoice), user).where(
        Invoice.status.in_([S.SLIP_GENERATED, S.SLIP_PRINTED, S.DISPATCHED]), Invoice.lr_no.is_(None),
        Invoice.transporter_id.isnot(None))
    if tid:
        stmt = stmt.where(Invoice.transporter_id == tid)
    invs = list(db.scalars(stmt.order_by(Invoice.dispatch_date, Invoice.dispatch_no).limit(1000)))
    rows: OrderedDict = OrderedDict()
    for inv in invs:
        k = (inv.dispatch_slip_id, inv.customer_id)
        r = rows.setdefault(k, {"slip_id": inv.dispatch_slip_id, "dispatch_no": inv.dispatch_no,
                                "date": inv.dispatch_date, "transporter": inv.transporter, "customer": inv.customer,
                                "location": inv.location, "invs": [], "cases": 0})
        r["invs"].append(inv)
        r["cases"] += inv.boxes or 0
    today = local_today()
    return render(request, "gatepass/lr_pending.html", {
        "title": "LR pending", "rows": list(rows.values()), "today": today, "tid": tid,
        "transporters": list(db.scalars(select(Transporter).order_by(Transporter.name)))})


@router.post("/gatepass/lr-pending")
def lr_pending_save(request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                    user: User = Depends(require("dispatch.operate"))):
    """Bulk LR entry: fields lr_<slip_id>_<customer_id>, lrdate_<slip_id>_<customer_id>."""
    n, errors = 0, []
    for key, val in form.items():
        if not key.startswith("lr_") or not (val or "").strip():
            continue
        parts = key.split("_")
        if len(parts) != 3:
            continue
        slip_id, cid = safe_int(parts[1]), safe_int(parts[2])
        invs = list(db.scalars(apply_invoice_scope(select(Invoice), user).where(
            Invoice.dispatch_slip_id == slip_id, Invoice.customer_id == cid, Invoice.status != S.CANCELLED)))
        try:
            with db.begin_nested():
                n += ds.set_lr(db, user, invs, val, form.get(f"lrdate_{parts[1]}_{parts[2]}") or None)
        except BusinessError as e:
            errors.append(e.message)
    db.commit()
    if n:
        flash(request, f"LR saved for {n} invoice(s).", "success")
    for e in errors[:5]:
        flash(request, e, "error")
    tid = safe_int(form.get("transporter_id"))
    return redirect("/gatepass/lr-pending" + (f"?transporter_id={tid}" if tid else ""))
