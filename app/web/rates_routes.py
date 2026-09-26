from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Company, FreightRate, Invoice, Location, RateLevel, RateStatus, User
from app.security.auth import csrf_protect, require
from app.services import rate_service
from app.services.errors import BusinessError
from app.services.freight_engine import calc_freight, find_rate
from app.services.import_service import Resolver, read_table
from app.services.scope import apply_company_scope, can_see
from app.services.utils import local_today, parse_date, parse_decimal
from app.web.templating import back_url, filter_options, flash, redirect, render

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)


def _int(v):
    try:
        return int(v) if v not in (None, "", "0") else None
    except ValueError:
        return None


def _get_rate(db, user, rate_id) -> FreightRate:
    r = db.get(FreightRate, rate_id)
    if not r or not can_see(user, r.company_id):
        raise HTTPException(404)
    return r


@router.get("/rates")
def rate_list(request: Request, db: Session = Depends(get_db), user: User = Depends(require("rate.view"))):
    qp = request.query_params
    view = qp.get("view", "current")
    stmt = apply_company_scope(select(FreightRate), user, FreightRate.company_id)
    today = local_today()
    if view == "current":
        stmt = stmt.where(FreightRate.status == RateStatus.APPROVED, FreightRate.effective_from <= today,
                          or_(FreightRate.effective_to.is_(None), FreightRate.effective_to >= today))
    elif view == "future":
        stmt = stmt.where(FreightRate.status == RateStatus.APPROVED, FreightRate.effective_from > today)
    elif view == "pending":
        stmt = stmt.where(FreightRate.status == RateStatus.PENDING_APPROVAL)
    elif view == "history":
        stmt = stmt.where(FreightRate.status == RateStatus.APPROVED, FreightRate.effective_to < today)
    elif view == "closed":
        stmt = stmt.where(FreightRate.status.in_([RateStatus.REJECTED, RateStatus.INACTIVE]))
    for k, col in (("company_id", FreightRate.company_id), ("cfa_id", FreightRate.cfa_id),
                   ("location_id", FreightRate.location_id), ("customer_id", FreightRate.customer_id)):
        if _int(qp.get(k)):
            stmt = stmt.where(col == _int(qp.get(k)))
    rows = list(db.scalars(stmt.join(Company, FreightRate.company_id == Company.id)
                           .outerjoin(Location, FreightRate.location_id == Location.id)
                           .order_by(Company.code, FreightRate.cfa_id, Location.name, FreightRate.customer_id,
                                     FreightRate.effective_from.desc())))
    counts = {}
    for v in ("pending",):
        counts[v] = db.scalar(select(func.count()).select_from(
            apply_company_scope(select(FreightRate), user, FreightRate.company_id)
            .where(FreightRate.status == RateStatus.PENDING_APPROVAL).subquery()))
    return render(request, "rates/list.html", {"title": "Freight Rates", "rows": rows, "view": view, "counts": counts,
                                               "opts": filter_options(db, user), "qp": qp, "levels": RateLevel.LABELS})


@router.get("/rates/finder")
def finder(request: Request, db: Session = Depends(get_db), user: User = Depends(require("rate.view"))):
    qp = request.query_params
    result = None
    if _int(qp.get("company_id")):
        try:
            d = parse_date(qp.get("on_date")) if qp.get("on_date") else local_today()
        except ValueError:
            d = local_today()
        lk = find_rate(db, company_id=_int(qp.get("company_id")), cfa_id=_int(qp.get("cfa_id")),
                       customer_id=_int(qp.get("customer_id")), location_id=_int(qp.get("location_id")), on_date=d)
        boxes = _int(qp.get("boxes"))
        result = {"lk": lk, "date": d, "boxes": boxes,
                  "freight": calc_freight(boxes, lk.rate_per_box) if boxes and lk.found else None}
    return render(request, "rates/finder.html", {"title": "Rate finder", "opts": filter_options(db, user), "qp": qp,
                                                 "result": result})


@router.get("/rates/new")
def new_rate(request: Request, db: Session = Depends(get_db), user: User = Depends(require("rate.create"))):
    qp = request.query_params
    return render(request, "rates/form.html", {"title": "New freight rate", "opts": filter_options(db, user),
                                               "qp": qp, "today": local_today()})


@router.post("/rates")
def create(request: Request, company_id: int = Form(...), cfa_id: str = Form(""), customer_id: str = Form(""),
           location_id: str = Form(""), is_default: str = Form(""), rate_per_box: str = Form(...),
           effective_from: str = Form(...), effective_to: str = Form(""), remarks: str = Form(""),
           db: Session = Depends(get_db), user: User = Depends(require("rate.create"))):
    if not can_see(user, company_id):
        raise BusinessError("You cannot create rates for this company")
    try:
        ef = parse_date(effective_from)
        et = parse_date(effective_to) if effective_to else None
        rate = parse_decimal(rate_per_box)
    except ValueError as e:
        raise BusinessError(str(e)) from e
    r = rate_service.create_rate(db, user, company_id=company_id, cfa_id=_int(cfa_id), customer_id=_int(customer_id),
                                 location_id=_int(location_id), is_default=bool(is_default), rate_per_box=rate,
                                 effective_from=ef, effective_to=et, remarks=remarks or None,
                                 change_reason=remarks or "New rate")
    db.commit()
    if r.status == RateStatus.APPROVED:
        flash(request, f"Rate #{r.id} created and active (approval not required).", "success")
    else:
        flash(request, f"Rate #{r.id} created - awaiting approval by a checker before it is used.", "warn")
    return redirect(f"/rates/{r.id}")


@router.get("/rates/{rate_id:int}")
def detail(rate_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("rate.view"))):
    r = _get_rate(db, user, rate_id)
    history = rate_service.rate_history(db, r)
    used = db.scalar(select(func.count(Invoice.id)).where(Invoice.freight_rate_id == r.id))
    return render(request, "rates/detail.html", {"title": f"Rate #{r.id}", "r": r, "history": history, "used": used,
                                                 "desc": rate_service.describe(r), "today": local_today()})


@router.post("/rates/{rate_id:int}/revise")
def revise(rate_id: int, request: Request, rate_per_box: str = Form(...), effective_from: str = Form(...),
           change_reason: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require("rate.create"))):
    prev = _get_rate(db, user, rate_id)
    try:
        ef = parse_date(effective_from)
        rate = parse_decimal(rate_per_box)
    except ValueError as e:
        raise BusinessError(str(e)) from e
    r = rate_service.revise_rate(db, user, prev, rate_per_box=rate, effective_from=ef, change_reason=change_reason)
    db.commit()
    flash(request, f"Revision #{r.id} (₹{prev.rate_per_box} → ₹{r.rate_per_box} from {ef:%d-%m-%Y}) "
                   f"{'is active' if r.status == RateStatus.APPROVED else 'awaits approval'}. Existing invoices keep their rate.",
          "success" if r.status == RateStatus.APPROVED else "warn")
    return redirect(f"/rates/{r.id}")


@router.post("/rates/{rate_id:int}/decide")
def decide(rate_id: int, request: Request, decision: str = Form(...), remarks: str = Form(""),
           db: Session = Depends(get_db), user: User = Depends(require("rate.approve"))):
    r = _get_rate(db, user, rate_id)
    if decision == "approve":
        n = rate_service.approve_rate(db, user, r, remarks or None)
        db.commit()
        flash(request, f"Rate #{r.id} approved." + (f" {n} rate-missing invoice(s) re-priced automatically." if n else ""),
              "success")
    else:
        rate_service.reject_rate(db, user, r, remarks)
        db.commit()
        flash(request, f"Rate #{r.id} rejected.", "warn")
    return redirect(back_url(request, f"/rates/{r.id}"))


@router.post("/rates/{rate_id:int}/close")
def close(rate_id: int, request: Request, effective_to: str = Form(...), reason: str = Form(""),
          db: Session = Depends(get_db), user: User = Depends(require("rate.approve"))):
    r = _get_rate(db, user, rate_id)
    try:
        et = parse_date(effective_to)
    except ValueError as e:
        raise BusinessError(str(e)) from e
    rate_service.close_rate(db, user, r, et, reason)
    db.commit()
    flash(request, f"Rate #{r.id} closed with effect to {et:%d-%m-%Y}.", "success")
    return redirect(f"/rates/{r.id}")


@router.post("/rates/import")
def import_rates(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db),
                 user: User = Depends(require("rate.create"))):
    """Bulk upload: company, cfa, customer (optional), location, rate_per_box, effective_from, effective_to."""
    content = file.file.read()
    _, records = read_table(content, file.filename or "rates.csv")
    res = Resolver(db, None)
    ok, errors = 0, []
    for i, rec in enumerate(records, start=2):
        r = {str(k).strip().lower().replace(" ", "_").replace("/", "_"): v for k, v in rec.items()}
        try:
            with db.begin_nested():
                co = res.company(r.get("company") or r.get("company_code"))
                if not co:
                    raise BusinessError(f"unknown company '{r.get('company') or r.get('company_code')}'")
                if not can_see(user, co.id):
                    raise BusinessError(f"not authorised for {co.code}")
                cfa = res.cfa(r.get("cfa") or r.get("cfa_code")) if (r.get("cfa") or r.get("cfa_code")) else None
                cu = res.customer(r.get("customer") or r.get("customer_code")) if (r.get("customer") or r.get("customer_code")) else None
                loc_v = r.get("location") or r.get("delivery_location")
                loc = res.location(loc_v) if loc_v else None
                if loc_v and not loc:
                    raise BusinessError(f"unknown location '{loc_v}'")
                is_def = str(r.get("is_default") or "").strip().lower() in ("1", "y", "yes", "true")
                rate_service.create_rate(
                    db, user, company_id=co.id, cfa_id=cfa.id if cfa else None, customer_id=cu.id if cu else None,
                    location_id=loc.id if loc else None, is_default=is_def,
                    rate_per_box=parse_decimal(r.get("rate_per_box") or r.get("rate")),
                    effective_from=parse_date(r.get("effective_from")) or date.today(),
                    effective_to=parse_date(r.get("effective_to")) if r.get("effective_to") else None,
                    remarks=f"Bulk upload {file.filename}", change_reason=r.get("reason") or "Bulk upload")
                ok += 1
        except (BusinessError, ValueError) as e:
            errors.append(f"Row {i}: {getattr(e, 'message', str(e))}")
    db.commit()
    flash(request, f"{ok} rate(s) uploaded" + (" (awaiting approval)." if ok else ".") +
          (f" {len(errors)} error(s): " + "; ".join(errors[:6]) if errors else ""), "success" if not errors else "warn")
    return redirect("/rates?view=pending" if ok else "/rates")
