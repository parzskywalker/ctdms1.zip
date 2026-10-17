"""v1.6 Master Settings: one-time Initial Master Upload + Invoice Recognition Code settings.

Declared before the generic /masters/{key} routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CustomerTypeMaster, Invoice, InvoiceStatus, MasterUpload, User
from app.security.auth import csrf_protect, require
from app.services import audit, recognition
from app.services import master_upload_service as mus
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from app.services.utils import safe_int
from app.web.templating import flash, get_form, redirect, render

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)


@router.get("/masters/upload")
def upload_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require("master.edit"))):
    history = list(db.scalars(select(MasterUpload).order_by(MasterUpload.id.desc()).limit(30)))
    done = next((u for u in history if u.status == "IMPORTED"), None)
    return render(request, "masters/upload.html", {"title": "Initial Master Upload", "history": history,
                                                   "done": done, "sheets": mus.SHEETS, "totals": mus.totals})


@router.get("/masters/upload/template.xlsx")
def upload_template(user: User = Depends(require("master.view"))):
    return Response(mus.template_workbook(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="CTDMS_v1.6_master_workbook_template.xlsx"'})


@router.post("/masters/upload")
async def upload_post(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db),
                      user: User = Depends(require("master.edit"))):
    content = await file.read()
    try:
        up = mus.preview(db, user, content, file.filename or "masters.xlsx")
    except BusinessError as e:
        db.rollback()
        flash(request, e.message, "error")
        return redirect("/masters/upload")
    db.commit()
    t = mus.totals(up)
    flash(request, f"Workbook checked: {t['rows']} rows - {t['new']} new, {t['exists']} already in CTDMS, "
                   f"{t['rejected']} rejected. Nothing is saved until you press Import.", "info")
    return redirect(f"/masters/upload/{up.id}")


def _get(db: Session, uid: int) -> MasterUpload:
    up = db.get(MasterUpload, uid)
    if not up:
        raise HTTPException(404)
    return up


@router.get("/masters/upload/{uid:int}")
def upload_detail(uid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("master.edit"))):
    up = _get(db, uid)
    show = request.query_params.get("show", "problems")
    rows = up.rows or []
    if show == "problems":
        rows = [r for r in rows if r["status"] in ("REJECTED", "DUPLICATE", "EXISTS") or r["warnings"]]
    elif show in ("NEW", "IMPORTED", "REJECTED", "EXISTS", "DUPLICATE"):
        rows = [r for r in rows if r["status"] == show]
    sheet = request.query_params.get("sheet")
    if sheet:
        rows = [r for r in rows if r["sheet"] == sheet]
    return render(request, "masters/upload_detail.html", {
        "title": f"Master upload #{up.id}", "up": up, "rows": rows, "show": show, "sheet": sheet,
        "totals": mus.totals(up), "order": [s for s in mus.ORDER if s in (up.counts or {})],
        "titles": {s.name: s.title for s in mus.SHEETS}})


@router.post("/masters/upload/{uid:int}/commit")
def upload_commit(uid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("master.edit"))):
    up = _get(db, uid)
    try:
        created = mus.commit(db, user, up)
    except BusinessError as e:
        db.rollback()
        flash(request, e.message, "error")
        return redirect(f"/masters/upload/{uid}")
    db.commit()
    n = sum(created.values())
    flash(request, f"{n} master records created: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in created.items() if v)
          + ". Rejected rows were not imported - see the error report. Add later records under Master Settings.",
          "success")
    return redirect(f"/masters/upload/{uid}?show=all")


@router.post("/masters/upload/{uid:int}/cancel")
def upload_cancel(uid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("master.edit"))):
    up = _get(db, uid)
    if up.status == "PREVIEW":
        up.status = "CANCELLED"
        audit.log(db, user, "MASTER_UPLOAD_CANCELLED", mus.MODULE, record_type="MasterUpload", record_id=up.id,
                  ref=up.file_name)
        db.commit()
        flash(request, "Upload cancelled - nothing was imported.", "info")
    return redirect("/masters/upload")


@router.get("/masters/upload/{uid:int}/errors.xlsx")
def upload_errors(uid: int, db: Session = Depends(get_db), user: User = Depends(require("master.edit"))):
    up = _get(db, uid)
    return Response(mus.error_report(up),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="master_upload_{up.id}_error_report.xlsx"'})


# ----------------------------------------------------------------------------- Invoice Recognition Code
KEYS = ["invoice_code.enabled", "invoice_code.format", "invoice_code.serial_digits", "invoice_code.serial_source",
        "invoice_code.use_master_code"]


@router.get("/masters/recognition")
def recognition_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require("master.view"))):
    base = select(func.count(Invoice.id)).where(Invoice.status != InvoiceStatus.CANCELLED)
    stats = {"total": db.scalar(base),
             "with_code": db.scalar(base.where(Invoice.recognition_code.isnot(None))),
             "missing": db.scalar(base.where(Invoice.recognition_code.is_(None), Invoice.recognition_note.isnot(None)))}
    missing = list(db.scalars(select(Invoice).where(Invoice.recognition_code.is_(None),
                                                    Invoice.recognition_note.isnot(None),
                                                    Invoice.status != InvoiceStatus.CANCELLED)
                              .order_by(Invoice.id.desc()).limit(30)))
    recent = list(db.scalars(select(Invoice).where(Invoice.recognition_code.isnot(None))
                             .order_by(Invoice.id.desc()).limit(12)))
    types = list(db.scalars(select(CustomerTypeMaster).order_by(CustomerTypeMaster.sort_order, CustomerTypeMaster.name)))
    vals = {k: cfg.get(db, k) for k in KEYS}
    return render(request, "masters/recognition.html", {
        "title": "Invoice Recognition Code", "stats": stats, "missing": missing, "recent": recent, "vals": vals,
        "example": recognition.example(db), "types": types, "tokens": recognition.TOKENS,
        "can_settings": user.can("admin.settings")})


@router.post("/masters/recognition")
def recognition_save(request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                     user: User = Depends(require("admin.settings"))):
    fmt = (form.get("invoice_code.format") or "").strip()
    unknown = [t for t in recognition.format_tokens(fmt) if t not in recognition.TOKENS]
    if not fmt or unknown or not recognition.format_tokens(fmt):
        raise BusinessError("The pattern needs parts in braces, e.g. {company}-{ctype}-{customer}-{depot}-{serial}"
                            + (f" (unknown: {{{unknown[0]}}})" if unknown else ""))
    new = {"invoice_code.enabled": form.get("invoice_code.enabled") in ("1", "on", "true"),
           "invoice_code.format": fmt,
           "invoice_code.serial_digits": max(0, min(12, safe_int(form.get("invoice_code.serial_digits"), 6) or 0)),
           "invoice_code.serial_source": form.get("invoice_code.serial_source") if form.get("invoice_code.serial_source")
           in ("LAST_DIGITS", "ALL_DIGITS", "FULL") else "LAST_DIGITS",
           "invoice_code.use_master_code": form.get("invoice_code.use_master_code") in ("1", "on", "true")}
    changed = 0
    for k, v in new.items():
        old = cfg.get(db, k)
        if str(old).lower() != str(v).lower():
            cfg.set_value(db, k, v, user.id)
            audit.log(db, user, "SETTING_CHANGED", "SETTINGS", record_type="Setting", record_id=k, ref=k, field=k,
                      old=old, new=v, reason=(form.get("reason") or "").strip() or "Invoice Recognition Code settings")
            changed += 1
    db.flush()
    res = recognition.regenerate(db, user, scope="open") if changed else None
    db.commit()
    flash(request, f"{changed} setting(s) saved." + (f" Codes of {res['checked']} open invoices re-made "
                                                      f"({res['missing']} still missing a code)." if res else ""),
          "success")
    return redirect("/masters/recognition")


@router.post("/masters/recognition/regenerate")
def recognition_regenerate(request: Request, scope: str = Form("open"), db: Session = Depends(get_db),
                           user: User = Depends(require("master.edit"))):
    scope = scope if scope in ("open", "missing", "all") else "open"
    res = recognition.regenerate(db, user, scope=scope)
    db.commit()
    flash(request, f"Checked {res['checked']} invoices: {res['changed']} codes changed, {res['missing']} still "
                   "without a code (see the reasons below).", "success")
    return redirect("/masters/recognition")
