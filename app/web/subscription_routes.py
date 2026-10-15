"""Reports e-mailed on a schedule (Reports › E-mailed reports)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ReportSubscription, User
from app.security.auth import csrf_protect, require
from app.services import audit
from app.services import subscription_service as subs
from app.services.errors import BusinessError
from app.services.reports import REPORTS
from app.services.utils import local_now, safe_int
from app.web.templating import filter_options, flash, get_form, redirect, render

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)
PARAM_KEYS = ("company_id", "customer_id", "cfa_id", "location_id", "transporter_id")


def _sub(db: Session, user: User, sub_id: int) -> ReportSubscription:
    sub = db.get(ReportSubscription, sub_id)
    if not sub or (sub.owner_id != user.id and not user.can("admin.settings")):
        raise HTTPException(404)
    return sub


@router.get("/report-subscriptions")
def index(request: Request, db: Session = Depends(get_db), user: User = Depends(require("report.view"))):
    stmt = select(ReportSubscription).order_by(ReportSubscription.id.desc())
    if not user.can("admin.settings"):
        stmt = stmt.where(ReportSubscription.owner_id == user.id)
    reports = [r for r in REPORTS.values() if user.can(r.perm)]
    return render(request, "reports/subscriptions.html", {
        "title": "E-mailed reports", "rows": list(db.scalars(stmt)), "reports": reports, "titles":
        {r.code: r.title for r in REPORTS.values()}, "freqs": subs.FREQUENCIES, "formats": subs.FORMATS,
        "smtp_ready": subs.smtp_ready(), "opts": filter_options(db, user),
        "can_export": user.can("data.export")})


@router.post("/report-subscriptions")
def create(request: Request, form=Depends(get_form), db: Session = Depends(get_db),
           user: User = Depends(require("report.view"))):
    params = {k: safe_int(form.get(k)) for k in PARAM_KEYS if safe_int(form.get(k))}
    sub = subs.create(db, user, name=form.get("name") or "", report_code=form.get("report_code") or "",
                      frequency=form.get("frequency") or "", recipients=form.get("recipients") or "",
                      fmt=form.get("fmt") or "xlsx", params=params)
    db.commit()
    flash(request, f"'{sub.name}' will be e-mailed {subs.FREQUENCIES[sub.frequency].split(' (')[0].lower()} to "
                   f"{sub.recipients}.", "success")
    return redirect("/report-subscriptions")


@router.post("/report-subscriptions/{sub_id:int}/toggle")
def toggle(sub_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("report.view"))):
    sub = _sub(db, user, sub_id)
    sub.is_active = not sub.is_active
    if sub.is_active:
        sub.next_run_at = subs.next_run(sub.frequency, local_now())
    audit.log(db, user, "SUBSCRIPTION_" + ("RESUMED" if sub.is_active else "PAUSED"), subs.MODULE,
              record_type="ReportSubscription", record_id=sub.id, ref=sub.name)
    db.commit()
    return redirect("/report-subscriptions")


@router.post("/report-subscriptions/{sub_id:int}/delete")
def delete(sub_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("report.view"))):
    sub = _sub(db, user, sub_id)
    audit.log(db, user, "SUBSCRIPTION_DELETED", subs.MODULE, record_type="ReportSubscription", record_id=sub.id,
              ref=sub.name, details={"to": sub.recipients, "report": sub.report_code})
    db.delete(sub)
    db.commit()
    flash(request, "Subscription deleted.", "success")
    return redirect("/report-subscriptions")


@router.post("/report-subscriptions/{sub_id:int}/send")
def send_now(sub_id: int, request: Request, db: Session = Depends(get_db),
             user: User = Depends(require("report.view"))):
    sub = _sub(db, user, sub_id)
    try:
        subs.send(db, sub, user)
    except BusinessError as e:
        db.commit()
        flash(request, f"Not sent: {e.message}", "error")
        return redirect("/report-subscriptions")
    except Exception as e:  # noqa: BLE001 - SMTP problems are shown, not a 500
        db.commit()
        flash(request, f"Not sent: {type(e).__name__}: {e}", "error")
        return redirect("/report-subscriptions")
    db.commit()
    flash(request, f"Sent to {sub.recipients}.", "success")
    return redirect("/report-subscriptions")
