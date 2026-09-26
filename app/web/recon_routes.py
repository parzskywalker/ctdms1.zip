from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BillStatus, TransporterBill, User
from app.security.auth import csrf_protect, require
from app.services import recon_service as rs
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from app.services.reports import Filters
from app.services.utils import parse_date
from app.web.templating import filter_options, flash, get_form, redirect, render

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)


def _bill(db, bill_id) -> TransporterBill:
    b = db.get(TransporterBill, bill_id)
    if not b:
        raise HTTPException(404)
    return b


@router.get("/recon")
def bill_list(request: Request, db: Session = Depends(get_db), user: User = Depends(require("recon.view"))):
    f = Filters.from_params(request.query_params, default_range="fy")
    stmt = select(TransporterBill)
    if f.date_from:
        stmt = stmt.where(TransporterBill.bill_date >= f.date_from)
    if f.date_to:
        stmt = stmt.where(TransporterBill.bill_date <= f.date_to)
    if f.transporter_id:
        stmt = stmt.where(TransporterBill.transporter_id == f.transporter_id)
    if f.status:
        stmt = stmt.where(TransporterBill.status == f.status)
    bills = list(db.scalars(stmt.order_by(TransporterBill.bill_date.desc(), TransporterBill.id.desc())))
    tot = {"calc": sum(b.calculated_amount for b in bills), "bill": sum(b.bill_amount for b in bills),
           "var": sum(b.variance for b in bills)}
    return render(request, "recon/list.html", {"title": "Transporter Bill Reconciliation", "bills": bills, "f": f,
                                               "opts": filter_options(db, user), "statuses": BillStatus.ALL, "tot": tot})


@router.get("/recon/new")
def new_bill(request: Request, db: Session = Depends(get_db), user: User = Depends(require("recon.edit"))):
    return render(request, "recon/new.html", {"title": "New transporter bill", "opts": filter_options(db, user)})


@router.post("/recon/new")
def create_bill(request: Request, transporter_id: int = Form(...), bill_no: str = Form(...), bill_date: str = Form(...),
                bill_amount: str = Form(...), company_id: str = Form(""), cfa_id: str = Form(""),
                period_from: str = Form(""), period_to: str = Form(""), remarks: str = Form(""),
                link: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require("recon.edit"))):
    try:
        bd = parse_date(bill_date)
        pf = parse_date(period_from) if period_from else None
        pt = parse_date(period_to) if period_to else None
    except ValueError as e:
        raise BusinessError(str(e)) from e
    b = rs.create_bill(db, user, transporter_id=transporter_id, bill_no=bill_no, bill_date=bd, bill_amount=bill_amount,
                       company_id=int(company_id) if company_id else None, cfa_id=int(cfa_id) if cfa_id else None,
                       period_from=pf, period_to=pt, remarks=remarks or None)
    n = rs.link_dispatches(db, user, b) if link and pf and pt else 0
    db.commit()
    flash(request, f"Bill {b.bill_no} created" + (f" with {n} dispatches linked" if n else "") + ".", "success")
    return redirect(f"/recon/{b.id}")


@router.get("/recon/{bill_id:int}")
def bill_detail(bill_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("recon.view"))):
    b = _bill(db, bill_id)
    lines_total = sum((ln.billed_amount or 0) for ln in b.lines)
    return render(request, "recon/detail.html", {"title": f"Bill {b.bill_no}", "b": b, "lines_total": lines_total,
                                                 "editable": b.status in rs.EDITABLE,
                                                 "tol_amt": cfg.get(db, "recon.tolerance_amount"),
                                                 "tol_pct": cfg.get(db, "recon.tolerance_pct")})


@router.post("/recon/{bill_id:int}/link")
def link(bill_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("recon.edit"))):
    b = _bill(db, bill_id)
    n = rs.link_dispatches(db, user, b)
    db.commit()
    flash(request, f"{n} dispatched invoice(s) linked from {b.period_from:%d-%m-%Y} to {b.period_to:%d-%m-%Y}.", "success")
    return redirect(f"/recon/{b.id}")


@router.post("/recon/{bill_id:int}/import")
def import_lines(bill_id: int, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db),
                 user: User = Depends(require("recon.edit"))):
    b = _bill(db, bill_id)
    n, errors = rs.import_lines(db, user, b, file.file.read(), file.filename or "bill.csv")
    db.commit()
    flash(request, f"{n} bill line(s) imported and matched." + (f" {len(errors)} error(s): " + "; ".join(errors[:5]) if errors else ""),
          "success" if not errors else "warn")
    return redirect(f"/recon/{b.id}")


@router.post("/recon/{bill_id:int}/amounts")
def amounts(bill_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
            user: User = Depends(require("recon.edit"))):
    b = _bill(db, bill_id)
    vals = {k[4:]: v for k, v in form.items() if k.startswith("amt_")}
    if form.get("bill_amount"):
        from app.services.utils import money, parse_decimal
        new = money(parse_decimal(form.get("bill_amount")))
        if new != b.bill_amount:
            from app.services import audit
            audit.log(db, user, "BILL_AMOUNT_CHANGED", rs.MODULE, record_type="TransporterBill", record_id=b.id,
                      ref=b.bill_no, field="bill_amount", old=b.bill_amount, new=new)
            b.bill_amount = new
    rs.set_line_amounts(db, user, b, vals)
    db.commit()
    flash(request, f"Amounts saved. Bill status: {b.status}.", "success")
    return redirect(f"/recon/{b.id}")


@router.post("/recon/{bill_id:int}/allocate")
def allocate(bill_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("recon.edit"))):
    b = _bill(db, bill_id)
    rs.allocate_header(db, user, b)
    db.commit()
    flash(request, "Bill amount allocated to lines in proportion to system freight.", "success")
    return redirect(f"/recon/{b.id}")


@router.post("/recon/{bill_id:int}/lines/{line_id:int}/delete")
def delete_line(bill_id: int, line_id: int, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require("recon.edit"))):
    b = _bill(db, bill_id)
    rs.delete_line(db, user, b, line_id)
    db.commit()
    return redirect(f"/recon/{b.id}")


@router.post("/recon/{bill_id:int}/approve")
def approve(bill_id: int, request: Request, remarks: str = Form(""), db: Session = Depends(get_db),
            user: User = Depends(require("recon.approve"))):
    b = _bill(db, bill_id)
    rs.approve(db, user, b, remarks)
    db.commit()
    flash(request, f"Bill {b.bill_no} approved.", "success")
    return redirect(f"/recon/{b.id}")


@router.post("/recon/{bill_id:int}/pay")
def pay(bill_id: int, request: Request, payment_ref: str = Form(""), db: Session = Depends(get_db),
        user: User = Depends(require("recon.pay"))):
    b = _bill(db, bill_id)
    rs.mark_paid(db, user, b, payment_ref)
    db.commit()
    flash(request, f"Bill {b.bill_no} marked PAID ({payment_ref}).", "success")
    return redirect(f"/recon/{b.id}")


@router.post("/recon/{bill_id:int}/reject")
def reject(bill_id: int, request: Request, reason: str = Form(""), db: Session = Depends(get_db),
           user: User = Depends(require("recon.approve"))):
    b = _bill(db, bill_id)
    rs.reject(db, user, b, reason)
    db.commit()
    flash(request, f"Bill {b.bill_no} rejected.", "warn")
    return redirect(f"/recon/{b.id}")
