from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.db import get_db
from app.models import (
    BatchStatus, Customer, ERPSystem, ImportBatch, ImportRow, ImportTemplate, RowStatus, User,
)
from app.security.auth import csrf_protect, require
from app.services import audit
from app.services import import_service as imp
from app.services.errors import BusinessError
from app.services.scope import can_see, visible_cfas, visible_companies
from app.services.utils import DATE_FORMATS
from app.web.templating import flash, get_form, read_upload, redirect, render

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)
SAMPLE_DIR = BASE_DIR / "sample_data"


def _all_scope(user: User) -> bool:
    return user.company_ids is None and user.cfa_ids is None and user.scope_is_open


def _batch_visible(user: User, b: ImportBatch) -> bool:
    """Restricted users see batches they uploaded, or batches tagged with their own company/CFA."""
    if _all_scope(user) or b.created_by_id == user.id:
        return True
    if b.cfa_id is None or (user.company_ids is not None and b.company_id is None):
        return False
    return can_see(user, b.company_id, b.cfa_id)


def _batch(db: Session, batch_id: int, user: User) -> ImportBatch:
    b = db.get(ImportBatch, batch_id)
    if not b or not _batch_visible(user, b):
        raise HTTPException(404)
    return b


@router.get("/imports")
def index(request: Request, db: Session = Depends(get_db), user: User = Depends(require("invoice.import"))):
    stmt = select(ImportBatch)
    if not _all_scope(user):
        own = [ImportBatch.created_by_id == user.id]
        if user.cfa_ids:
            own.append(ImportBatch.cfa_id.in_(user.cfa_ids))
        stmt = stmt.where(or_(*own))
    batches = [b for b in db.scalars(stmt.order_by(ImportBatch.id.desc()).limit(80)) if _batch_visible(user, b)][:40]
    samples = sorted(p.name for p in SAMPLE_DIR.glob("*") if p.is_file()) if SAMPLE_DIR.exists() else []
    from app.services import inbox_service
    from app.services import settings_service as cfg
    inbox = inbox_service.inbox_dir()
    return render(request, "imports/index.html", {
        "title": "Invoice Import", "batches": batches, "samples": samples,
        "inbox": {"path": str(inbox), "waiting": [p.name for p in inbox_service.pending_files()][:20],
                  "enabled": cfg.get(db, "inbox.enabled"), "every": cfg.get(db, "inbox.interval_minutes"),
                  "failed": sorted((p.name for p in (inbox / "failed").glob("*") if p.suffix != ".txt"),
                                   reverse=True)[:5]},
        "erps": list(db.scalars(select(ERPSystem).where(ERPSystem.is_active.is_(True)).order_by(ERPSystem.code))),
        "templates": list(db.scalars(select(ImportTemplate).where(ImportTemplate.is_active.is_(True)).order_by(ImportTemplate.name))),
        "companies": visible_companies(db, user), "cfas": visible_cfas(db, user)})


@router.get("/imports/sample/{name}")
def sample(name: str, user: User = Depends(require("invoice.view"))):
    p = (SAMPLE_DIR / Path(name).name)
    if not p.exists() or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)


@router.post("/imports/upload")
def upload(request: Request, file: UploadFile = File(...), erp_system_id: str = Form(""), template_id: str = Form(""),
           company_id: str = Form(""), cfa_id: str = Form(""), db: Session = Depends(get_db),
           user: User = Depends(require("invoice.import"))):
    content = read_upload(file)
    to_int = lambda v: int(v) if v and v.isdigit() else None  # noqa: E731
    batch, warning = imp.create_batch_from_file(db, user, content, file.filename or "upload.csv",
                                                erp_system_id=to_int(erp_system_id), company_id=to_int(company_id),
                                                cfa_id=to_int(cfa_id), template_id=to_int(template_id))
    imp.validate_batch(db, batch, user)
    db.commit()
    if warning:
        flash(request, warning, "warn")
    flash(request, f"Batch {batch.batch_no}: {batch.total_rows} rows read. Check the column mapping and validation, "
                   "then import.", "info")
    return redirect(f"/imports/{batch.id}")


@router.post("/imports/quick")
def quick(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db),
          user: User = Depends(require("invoice.import"))):
    """One click: layout recognised from the column headings, good rows imported, problem rows kept for correction."""
    batch, result, warning = imp.quick_import(db, user, read_upload(file), file.filename or "upload.csv")
    db.commit()
    if warning:
        flash(request, warning.strip(), "warn")
    tpl = batch.template.name if batch.template else "column names"
    flash(request, f"{batch.batch_no} ({tpl}): {result.get('imported', 0)} invoice(s) imported"
                   + (f", SKU lines added to {result['items_added']} scanned invoice(s)" if result.get("items_added") else "")
                   + f"; {result.get('REJECTED', 0)} row(s) need correction, {result.get('DUPLICATE', 0)} duplicate(s).",
          "success" if not result.get("REJECTED") else "warn")
    return redirect(f"/imports/{batch.id}")


@router.post("/imports/inbox/run")
def inbox_run(request: Request, db: Session = Depends(get_db), user: User = Depends(require("invoice.import"))):
    from app.services import inbox_service
    results = inbox_service.scan(db)
    if not results:
        flash(request, "The drop folder is empty.", "info")
    for r in results[:10]:
        flash(request, r["message"], "success" if r["ok"] else "error")
    return redirect("/imports")


@router.get("/imports/{batch_id:int}")
def batch_view(batch_id: int, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require("invoice.import"))):
    b = _batch(db, batch_id, user)
    show = request.query_params.get("show", "problems" if b.committed_at else "all")
    rows = b.rows
    if show == "problems":
        rows = [r for r in rows if r.status in (RowStatus.REJECTED, RowStatus.DUPLICATE, RowStatus.WARNING)]
    elif show in (RowStatus.IMPORTED, RowStatus.REJECTED, RowStatus.DUPLICATE, RowStatus.WARNING, RowStatus.VALID,
                  RowStatus.SKIPPED):
        rows = [r for r in rows if r.status == show]
    summary = imp.summarize(b)
    return render(request, "imports/batch.html", {
        "title": f"Import {b.batch_no}", "b": b, "rows": rows[:300], "row_total": len(rows), "summary": summary,
        "show": show, "fields": imp.TARGET_FIELDS, "date_formats": ["AUTO"] + DATE_FORMATS,
        "companies": visible_companies(db, user), "cfas": visible_cfas(db, user),
        "customers": list(db.scalars(select(Customer).where(Customer.is_active.is_(True)).order_by(Customer.name))),
        "erps": list(db.scalars(select(ERPSystem).order_by(ERPSystem.code))), "editable": b.status in (
            BatchStatus.UPLOADED, BatchStatus.VALIDATED, BatchStatus.PARTIAL, BatchStatus.FAILED)})


@router.post("/imports/{batch_id:int}/mapping")
def set_mapping(batch_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                user: User = Depends(require("invoice.import"))):
    b = _batch(db, batch_id, user)
    if b.committed_at:
        raise BusinessError("Mapping cannot be changed after rows were imported; correct individual rows instead")
    cmap = {t: form.get(f"map_{t}") for t in imp.TARGET_FIELDS if form.get(f"map_{t}")}
    old = dict(b.column_map or {})
    b.column_map = cmap
    b.date_format = form.get("date_format") or "AUTO"
    b.erp_system_id = int(form["erp_system_id"]) if form.get("erp_system_id") else None
    b.company_id = int(form["company_id"]) if form.get("company_id") else None
    b.cfa_id = int(form["cfa_id"]) if form.get("cfa_id") else None
    imp.remap(db, b)
    s = imp.validate_batch(db, b, user)
    if old != cmap:
        audit.log(db, user, "IMPORT_MAPPING_CHANGED", "IMPORT", record_type="ImportBatch", record_id=b.id,
                  ref=b.batch_no, old=str(old), new=str(cmap))
    if form.get("template_name"):
        imp.save_template(db, user, b, form.get("template_name"))
        flash(request, f"Template '{form.get('template_name')}' saved for reuse.", "success")
    db.commit()
    flash(request, f"Validated: {s['VALID']} ok, {s['WARNING']} with warnings, {s['REJECTED']} rejected, "
                   f"{s['DUPLICATE']} duplicates.", "info")
    return redirect(f"/imports/{b.id}")


@router.post("/imports/{batch_id:int}/commit")
def commit(batch_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("invoice.import"))):
    b = _batch(db, batch_id, user)
    res = imp.commit_batch(db, user, b)
    db.commit()
    flash(request, f"Imported {res['imported']} invoice(s)"
                   + (f", {res['exceptions']} landed as exceptions (rate / boxes missing)" if res["exceptions"] else "")
                   + f". Rejected: {res['REJECTED']}, duplicates: {res['DUPLICATE']}.",
          "success" if res["imported"] else "warn")
    return redirect(f"/imports/{b.id}")


@router.post("/imports/{batch_id:int}/rows/{row_id:int}")
def correct_row(batch_id: int, row_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                user: User = Depends(require("invoice.import"))):
    b = _batch(db, batch_id, user)
    row = db.get(ImportRow, row_id)
    if not row or row.batch_id != b.id:
        raise HTTPException(404)
    values = {k[2:]: v for k, v in form.items() if k.startswith("f_")}
    imp.correct_row(db, user, row, values)
    res = imp.Resolver(db, b.erp_system_id)
    imp.validate_row(db, b, row, res, user, set())
    action = form.get("then")
    if action == "import" and row.status in (RowStatus.VALID, RowStatus.WARNING):
        imp.commit_batch(db, user, b)
    db.commit()
    flash(request, f"Row {row.row_no}: {row.status.lower()}" + (f" - {row.errors[0]['msg']}" if row.errors else ""),
          "success" if row.status in (RowStatus.VALID, RowStatus.WARNING, RowStatus.IMPORTED) else "error")
    return redirect(f"/imports/{b.id}?show=problems#row-{row.id}")


@router.post("/imports/{batch_id:int}/rows/{row_id:int}/skip")
def skip_row(batch_id: int, row_id: int, request: Request, reason: str = Form("Skipped by user"),
             db: Session = Depends(get_db), user: User = Depends(require("invoice.import"))):
    b = _batch(db, batch_id, user)
    row = db.get(ImportRow, row_id)
    if not row or row.batch_id != b.id:
        raise HTTPException(404)
    imp.skip_row(db, user, row, reason)
    db.commit()
    return redirect(f"/imports/{b.id}?show=problems")


@router.post("/imports/{batch_id:int}/map-customer")
def map_customer(batch_id: int, request: Request, source_code: str = Form(...), customer_id: int = Form(...),
                 source_name: str = Form(""), db: Session = Depends(get_db),
                 user: User = Depends(require("invoice.import"))):
    b = _batch(db, batch_id, user)
    existing = imp.find_code_mapping(db, b.erp_system_id, "CUSTOMER", source_code) if b.erp_system_id else None
    if existing and existing.target_id != customer_id and not user.can("master.edit"):
        raise BusinessError(f"ERP code '{source_code}' is already mapped to another customer. Changing an existing "
                            "mapping affects every future import - ask a user with Masters edit rights.")
    if not b.erp_system_id:
        raise BusinessError("Set the source ERP on this batch first (mapping section)")
    imp.add_code_mapping(db, user, erp_system_id=b.erp_system_id, entity_type="CUSTOMER", source_code=source_code,
                         source_name=source_name or None, target_id=customer_id)
    db.flush()
    s = imp.validate_batch(db, b, user)
    db.commit()
    flash(request, f"{b.erp_system.code} customer code '{source_code}' mapped. Re-validated: {s['REJECTED']} still rejected.",
          "success")
    return redirect(f"/imports/{b.id}?show=problems")


@router.post("/imports/{batch_id:int}/retry")
def retry(batch_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("invoice.import"))):
    b = _batch(db, batch_id, user)
    before = imp.summarize(b)[RowStatus.IMPORTED]
    imp.commit_batch(db, user, b)
    db.commit()
    after = imp.summarize(b)[RowStatus.IMPORTED]
    flash(request, f"Retry imported {after - before} more row(s).", "success" if after > before else "warn")
    return redirect(f"/imports/{b.id}?show=problems")


@router.post("/imports/{batch_id:int}/cancel")
def cancel(batch_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("invoice.import"))):
    b = _batch(db, batch_id, user)
    if b.committed_at and imp.summarize(b)[RowStatus.IMPORTED]:
        raise BusinessError("Rows from this batch are already imported; skip the remaining rows instead")
    b.status = BatchStatus.CANCELLED
    audit.log(db, user, "IMPORT_CANCELLED", "IMPORT", record_type="ImportBatch", record_id=b.id, ref=b.batch_no)
    db.commit()
    flash(request, f"Batch {b.batch_no} cancelled.", "warn")
    return redirect("/imports")
