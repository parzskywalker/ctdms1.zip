"""v1.8 screens: Labels to Print (labels before any gatepass), Ready to Load, the guard's phone gate check, the signed-
gatepass photo and the dispatch exceptions log. The gatepass itself is still made by the existing gatepass builder."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    CaseScan, CaseStatus, DispatchCase, DispatchException, DispatchSlip, Invoice, InvoiceStatus, User, utcnow,
)
from app.models.pawati import GaragePawati, PawatiStatus
from app.security.auth import csrf_protect, require
from app.services import audit
from app.services import case_service as cs
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from app.services.scope import apply_invoice_scope, apply_slip_scope, can_see_invoice
from app.services.storage import upload_dir
from app.services.utils import safe_int
from app.web.templating import flash, get_form, redirect, render, templates

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)
OPEN = [s for s in InvoiceStatus.PRE_SLIP if s != InvoiceStatus.CANCELLED]


def _inv(db: Session, user: User, invoice_id: int) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if inv is None or not can_see_invoice(user, inv):
        raise BusinessError("Invoice not found")
    return inv


def _carton_state(db: Session, ids: list[int]) -> dict[int, dict]:
    """invoice id -> {total, pending} of its live cartons labelled before any gatepass."""
    out: dict[int, dict] = {}
    if not ids:
        return out
    for inv_id, status, n in db.execute(select(DispatchCase.invoice_id, DispatchCase.status, func.count()).where(
            DispatchCase.invoice_id.in_(ids), DispatchCase.label_first.is_(True), DispatchCase.slip_id.is_(None),
            DispatchCase.status != CaseStatus.VOID).group_by(DispatchCase.invoice_id, DispatchCase.status)):
        row = out.setdefault(inv_id, {"total": 0, "pending": 0})
        row["total"] += n
        row["pending"] += n if status == CaseStatus.LABEL_PENDING else 0
    return out


def _open_invoices(db: Session, user: User) -> list[Invoice]:
    return list(db.scalars(apply_invoice_scope(select(Invoice), user).where(Invoice.status.in_(OPEN))
                           .order_by(Invoice.invoice_date, Invoice.id).limit(500)))


# ----------------------------------------------------------------------------- Labels to Print (operator)
@router.get("/labels-desk")
def labels_desk(request: Request, db: Session = Depends(get_db), user: User = Depends(require("case.print"))):
    invs = _open_invoices(db, user)
    state = _carton_state(db, [i.id for i in invs])
    todo = [i for i in invs if i.id not in state or state[i.id]["pending"]]
    return render(request, "v18/labels_desk.html", {"title": "Labels to Print", "rows": todo, "state": state,
                                                     "can_exception": user.can("exception.approve")})


@router.post("/labels-desk/{invoice_id:int}")
def labels_make(invoice_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                user: User = Depends(require("case.print"))):
    inv = _inv(db, user, invoice_id)
    try:
        cs.label_invoice(db, user, inv, safe_int(form.get("count")) or 0, form.get("reason"))
    except BusinessError as e:
        db.rollback()
        flash(request, f"Invoice {inv.invoice_no}: {e.message}", "error")
        return redirect("/labels-desk")
    db.commit()
    return redirect(f"/labels-desk/{inv.id}/print")


@router.get("/labels-desk/{invoice_id:int}/print")
def labels_print(invoice_id: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require("case.print"))):
    inv = _inv(db, user, invoice_id)
    cartons = cs.label_first_cartons(db, inv.id)
    if not cartons:
        raise BusinessError(f"Invoice {inv.invoice_no} has no labels waiting - make them at Labels to Print")
    reprint = all(c.status != CaseStatus.LABEL_PENDING for c in cartons)
    return templates.TemplateResponse(request, "v18/labels_invoice.html", {
        "request": request, "inv": inv, "rows": cs.label_rows(db, None, cartons), "reprint": reprint,
        "org": cfg.get(db, "org.name"), "logo": cfg.get(db, "org.logo_url"), "today": datetime.now(),
        "w": cfg.get(db, "labels.width_mm") or 100, "h": cfg.get(db, "labels.height_mm") or 50,
        "csrf": request.session.get("csrf", ""), "can_reprint": user.can("case.reprint")})


@router.post("/labels-desk/{invoice_id:int}/confirm")
def labels_confirm(invoice_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                   user: User = Depends(require("case.print"))):
    inv = _inv(db, user, invoice_id)
    result = form.get("result") or ""
    cs.record_invoice_print(db, user, inv, result=result, reason=form.get("reason"))
    db.commit()
    flash(request, f"Invoice {inv.invoice_no}: labels {'printed - stick them on the cartons' if result == 'PRINTED' else 'FAILED - print again'}.",
          "success" if result == "PRINTED" else "warn")
    return redirect("/labels-desk")


# ----------------------------------------------------------------------------- Ready to Load (Depot Manager)
@router.get("/ready-to-load")
def ready_to_load(request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    invs = _open_invoices(db, user)
    state = _carton_state(db, [i.id for i in invs])
    ready = [i for i in invs if i.id in state and not state[i.id]["pending"]]
    groups: OrderedDict = OrderedDict()
    for i in sorted(ready, key=lambda x: ((x.location.name if x.location else ""), x.invoice_date, x.id)):
        groups.setdefault(i.location.name if i.location else "No area", []).append(i)
    totals = {a: sum(state[i.id]["total"] for i in invs) for a, invs in groups.items()}
    return render(request, "v18/ready_to_load.html", {"title": "Ready to Load", "groups": groups, "state": state,
                                                       "totals": totals,
                                                       "can_plan": user.can("load.plan")})


# ----------------------------------------------------------------------------- guard's phone gate check
@router.get("/gate/phone")
def gate_phone(request: Request, user: User = Depends(require("gate.scan"))):
    return render(request, "v18/gate_phone.html", {"title": "Check gatepass"})


def _slip_state(db: Session, slip: DispatchSlip) -> dict:
    p = cs.progress(db, slip)
    pawatis = list(db.scalars(select(GaragePawati).where(GaragePawati.slip_id == slip.id,
                                                         GaragePawati.status != PawatiStatus.CANCELLED)))
    seen = {code for (code,) in db.execute(select(CaseScan.scanned_code).where(CaseScan.slip_id == slip.id,
                                                                                CaseScan.result == "PAWATI_OK"))}
    exceptions = list(db.scalars(select(DispatchException).where(DispatchException.slip_id == slip.id)))
    return {"slip_id": slip.id, "dispatch_no": slip.dispatch_no, "vehicle": slip.vehicle_no or "",
            "scanned": p["scanned"], "total": p["total"], "complete": p["complete"], "gone_out": bool(slip.gate_out_at),
            "void": slip.is_void,
            "pawatis": [{"code": f"PW{w.id}", "no": w.pawati_no, "garage": w.transporter.name if w.transporter else "",
                         "done": f"PW{w.id}" in seen} for w in pawatis],
            "exceptions": [{"kind": e.label, "reason": e.reason} for e in exceptions],
            "photo": bool(slip.signature_photo)}


@router.post("/gate/phone/open")
def gate_phone_open(request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                    user: User = Depends(require("gate.scan"))):
    code = (form.get("code") or "").strip()
    slip = db.scalar(apply_slip_scope(select(DispatchSlip), user).where(func.upper(DispatchSlip.dispatch_no) == code.upper())) \
        if code else None
    if slip is None:
        return JSONResponse({"ok": False, "message": "Not a gatepass of this depot - scan the gatepass barcode"})
    if slip.is_void:
        return JSONResponse({"ok": False, "message": f"Gatepass {slip.dispatch_no} is VOID - the goods must not leave"})
    return JSONResponse({"ok": True, **_slip_state(db, slip)})


@router.post("/gate/phone/pawati")
def gate_phone_pawati(request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                      user: User = Depends(require("gate.scan"))):
    slip = db.scalar(apply_slip_scope(select(DispatchSlip), user).where(DispatchSlip.id == safe_int(form.get("slip_id"))))
    if slip is None:
        return JSONResponse({"ok": False, "message": "Scan the gatepass first"})
    code = (form.get("code") or "").strip().upper()
    ok = any(w["code"] == code for w in _slip_state(db, slip)["pawatis"])
    db.add(CaseScan(slip_id=slip.id, scanned_code=code[:60], result="PAWATI_OK" if ok else "PAWATI_WRONG",
                    scanned_by_id=user.id, station="phone"))
    db.commit()
    return JSONResponse({"ok": ok, "message": "Garage Pawati OK" if ok else "STOP - this Pawati is not for this gatepass",
                         **_slip_state(db, slip)})


@router.post("/gate/phone/photo")
async def gate_phone_photo(request: Request, photo: UploadFile = File(...), db: Session = Depends(get_db),
                           user: User = Depends(require("gate.scan"))):
    form = await request.form()
    slip = db.scalar(apply_slip_scope(select(DispatchSlip), user).where(DispatchSlip.id == safe_int(form.get("slip_id"))))
    if slip is None or slip.is_void or slip.gate_out_at:
        return JSONResponse({"ok": False, "message": "Scan an open gatepass first"})
    data = await photo.read()
    if not data or len(data) > 8 * 1024 * 1024 or not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"):
        return JSONResponse({"ok": False, "message": "Take the photo again (JPEG or PNG, under 8 MB)"})
    folder = upload_dir() / "signatures"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{slip.id}_{utcnow():%Y%m%d%H%M%S}.{'png' if data[:4] == b'\x89PNG' else 'jpg'}"
    (folder / name).write_bytes(data)
    slip.signature_photo, slip.signature_photo_at, slip.signature_photo_by_id = f"signatures/{name}", utcnow(), user.id
    audit.log(db, user, "SIGNATURE_PHOTO", "CASE", record_type="DispatchSlip", record_id=slip.id, ref=slip.dispatch_no,
              details={"file": name, "bytes": len(data)})
    db.commit()
    return JSONResponse({"ok": True, "message": "Photo saved - now scan the gatepass to dispatch", **_slip_state(db, slip)})


@router.get("/gatepass/{slip_id:int}/signature-photo")
def signature_photo(slip_id: int, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    slip = db.scalar(apply_slip_scope(select(DispatchSlip), user).where(DispatchSlip.id == slip_id))
    if slip is None or not slip.signature_photo:
        raise BusinessError("No photo for this gatepass")
    path = (upload_dir() / slip.signature_photo).resolve()
    if upload_dir().resolve() not in path.parents or not Path(path).is_file():
        raise BusinessError("Photo file missing")
    return FileResponse(path)


# ----------------------------------------------------------------------------- exceptions log
@router.get("/dispatch-exceptions")
def exceptions_log(request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    rows = [e for e in db.scalars(select(DispatchException).order_by(DispatchException.id.desc()).limit(500))
            if (e.invoice and can_see_invoice(user, e.invoice)) or
            (e.slip and db.scalar(apply_slip_scope(select(DispatchSlip.id), user).where(DispatchSlip.id == e.slip_id)))]
    return render(request, "v18/exceptions_log.html", {"title": "Dispatch Exceptions", "rows": rows})
