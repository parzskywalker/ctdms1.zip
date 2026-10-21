"""Transporter rate cards: rate per case for each delivery area, versioned, maker-checker approved."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Location, RateStatus, Transporter, TransporterRate, User
from app.security.auth import csrf_protect, require, require_any
from app.services import transporter_rate_service as trs
from app.services.errors import BusinessError
from app.services.import_service import read_table
from app.services.utils import local_today, norm_code, parse_date, parse_decimal, safe_int
from app.web.templating import back_url, flash, get_form, read_upload, redirect, render

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)


@router.get("/rate-cards")
def index(request: Request, db: Session = Depends(get_db), user: User = Depends(require("rate.view"))):
    today = local_today()
    rows = []
    for t in db.scalars(select(Transporter).order_by(Transporter.is_active.desc(), Transporter.name)):
        card = trs.card(db, t.id, today)
        pending = db.scalar(select(func.count(TransporterRate.id)).where(
            TransporterRate.transporter_id == t.id, TransporterRate.status == RateStatus.PENDING_APPROVAL)) or 0
        rows.append({"t": t, "areas": len(card), "min": min(card.values()) if card else None,
                     "max": max(card.values()) if card else None, "pending": pending})
    return render(request, "rate_cards/index.html", {"title": "Transporter Rate Cards", "rows": rows})


@router.get("/rate-cards/{tid:int}")
def card(tid: int, request: Request, db: Session = Depends(get_db),
         user: User = Depends(require_any("rate.view", "rate.approve"))):  # approvers open the card they verify
    t = db.get(Transporter, tid)
    if not t:
        raise HTTPException(404)
    areas = trs.current_and_pending(db, tid)
    have = {a["location"].id for a in areas}
    others = [lo for lo in db.scalars(select(Location).where(Location.is_active.is_(True)).order_by(Location.name))
              if lo.id not in have]
    return render(request, "rate_cards/card.html", {"title": f"Rate card · {t.name}", "t": t, "areas": areas,
                                                     "others": others, "today": local_today()})


@router.post("/rate-cards/{tid:int}/propose")
def propose(tid: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
            user: User = Depends(require("rate.create"))):
    """Rows: rate_<location_id>, from_<location_id>; plus new_location / new_rate / new_from for a new area."""
    t = db.get(Transporter, tid)
    if not t:
        raise HTTPException(404)
    reason = (form.get("reason") or "").strip()
    if len(reason) < 3:
        raise BusinessError("Give a reason for the rate change (e.g. transporter's letter of 1 Oct)")
    changes = []
    for key in form.keys():
        if key.startswith("rate_") and (form.get(key) or "").strip():
            changes.append((safe_int(key[5:]), form.get(key), form.get(f"from_{key[5:]}")))
    if form.get("new_location") and (form.get("new_rate") or "").strip():
        changes.append((safe_int(form.get("new_location")), form.get("new_rate"), form.get("new_from")))
    if not changes:
        raise BusinessError("Enter at least one new rate")
    n = 0
    for loc_id, raw, eff in changes:
        try:
            rate = parse_decimal(raw)
            d = parse_date(eff) if eff else local_today()
        except ValueError as e:
            raise BusinessError(str(e)) from e
        trs.propose(db, user, transporter_id=t.id, location_id=loc_id, rate_per_case=rate, effective_from=d,
                    reason=reason)
        n += 1
    db.commit()
    flash(request, f"{n} rate change(s) for {t.name} sent for approval.", "success")
    return redirect(f"/rate-cards/{t.id}")


@router.post("/rate-cards/approve-all")
def approve_all(request: Request, remarks: str = Form(""), db: Session = Depends(get_db),
                user: User = Depends(require("rate.approve"))):
    """v1.6: approve every waiting rate-card change the user may approve (e.g. after the initial master upload).
    The maker-checker rule still applies: changes the user proposed are left for someone else."""
    from sqlalchemy import select as _select
    from app.models import RateStatus
    from app.services import settings_service as cfg
    ok = skipped = 0
    for r in db.scalars(_select(TransporterRate).where(TransporterRate.status == RateStatus.PENDING_APPROVAL)
                        .order_by(TransporterRate.effective_from, TransporterRate.id)):
        if r.created_by_id == user.id and not cfg.get(db, "rate.allow_self_approval"):
            skipped += 1
            continue
        trs.approve(db, user, r, (remarks or "").strip() or "Approved together (approve all)")
        ok += 1
    db.commit()
    flash(request, f"{ok} rate-card change(s) approved." + (f" {skipped} proposed by you were left for another "
                                                            "approver." if skipped else ""), "success")
    return redirect(back_url(request, "/approvals"))


@router.post("/rate-cards/rate/{rid:int}/decide")
def decide(rid: int, request: Request, decision: str = Form(...), remarks: str = Form(""),
           db: Session = Depends(get_db), user: User = Depends(require("rate.approve"))):
    r = db.get(TransporterRate, rid)
    if not r:
        raise HTTPException(404)
    if decision == "approve":
        trs.approve(db, user, r, remarks or None)
        msg = f"{r.transporter.name} → {r.location.name}: ₹{r.rate_per_case}/case from {r.effective_from:%d-%m-%Y} approved."
    else:
        trs.reject(db, user, r, remarks)
        msg = f"Rate change for {r.transporter.name} → {r.location.name} rejected."
    db.commit()
    flash(request, msg, "success")
    return redirect(back_url(request, "/approvals"))


@router.post("/rate-cards/{tid:int}/upload")
def upload(tid: int, request: Request, file: UploadFile = File(...), reason: str = Form(""),
           db: Session = Depends(get_db), user: User = Depends(require("rate.create"))):
    """CSV / Excel with columns Area (code or name), Rate, Effective from - all go for approval."""
    t = db.get(Transporter, tid)
    if not t:
        raise HTTPException(404)
    headers, records = read_table(read_upload(file), file.filename or "rates.csv")
    norm = {h.lower().strip(): h for h in headers}
    col = lambda *names: next((norm[n] for n in names if n in norm), None)  # noqa: E731
    c_area, c_rate, c_from = col("area", "location", "station", "town"), col("rate", "rate per case", "rate_per_case"), \
        col("effective from", "effective_from", "from")
    if not c_area or not c_rate:
        raise BusinessError("The file needs 'Area' and 'Rate' columns (optional 'Effective from')")
    n, errors = 0, []
    for i, rec in enumerate(records, start=2):
        a = norm_code(rec.get(c_area))
        lo = db.scalar(select(Location).where((func.upper(Location.code) == a) | (func.upper(Location.name) == a)))
        if not lo:
            errors.append(f"Row {i}: area '{rec.get(c_area)}' not in the location master")
            continue
        try:
            with db.begin_nested():
                trs.propose(db, user, transporter_id=t.id, location_id=lo.id, rate_per_case=parse_decimal(rec.get(c_rate)),
                            effective_from=parse_date(rec.get(c_from)) if c_from and rec.get(c_from) else local_today(),
                            reason=reason or f"Rate list upload {file.filename}", source="IMPORT")
            n += 1
        except (BusinessError, ValueError) as e:
            errors.append(f"Row {i}: {getattr(e, 'message', e)}")
    db.commit()
    flash(request, f"{n} rate(s) sent for approval." + (f" {len(errors)} problem(s): " + "; ".join(errors[:5]) if errors else ""),
          "success" if not errors else "warn")
    return redirect(f"/rate-cards/{t.id}")


@router.get("/rate-cards/template.csv")
def template(user: User = Depends(require("rate.view"))):
    from fastapi.responses import Response
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Area", "Rate", "Effective from"])
    w.writerow(["Raigarh", "38.00", local_today().strftime("%d-%m-%Y")])
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="transporter_rate_card.csv"'})
