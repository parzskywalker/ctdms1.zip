from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BillStatus, OverrideStatus, RateStatus, User
from app.security.auth import PermissionDenied, csrf_protect, require
from app.services import audit
from app.services import settings_service as cfg
from app.services.dashboard import LEVELS, drill, drill_totals
from app.services.exporters import to_csv, to_pdf, to_xlsx
from app.services.reports import REPORT_GROUPS, REPORTS, Filters
from app.services.utils import local_now
from app.web.templating import filter_options, render

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)


def _def(code: str, user: User):
    rd = REPORTS.get(code)
    if not rd:
        raise HTTPException(404)
    if not user.can(rd.perm):
        raise PermissionDenied(rd.perm)
    return rd


def _show_filters(rd) -> tuple[list[str], list[str] | None]:
    show = [x for x in rd.filters if x in ("date", "company", "customer", "cfa", "location", "transporter", "erp",
                                           "status", "invoice_no", "module", "q")]
    choices = None
    if "bill_status" in rd.filters:
        choices = BillStatus.ALL
    elif "rate_status" in rd.filters:
        choices = RateStatus.ALL
    elif "override_status" in rd.filters:
        choices = [OverrideStatus.PENDING, OverrideStatus.APPROVED, OverrideStatus.REJECTED, OverrideStatus.APPLIED]
    return show, choices


@router.get("/reports")
def index(request: Request, user: User = Depends(require("report.view"))):
    groups = [(g, [r for r in REPORTS.values() if r.group == g and user.can(r.perm)]) for g in REPORT_GROUPS]
    return render(request, "reports/index.html", {"title": "Reports", "groups": groups})


@router.get("/mis")
def mis(request: Request, db: Session = Depends(get_db), user: User = Depends(require("report.view"))):
    f = Filters.from_params(request.query_params, default_range="fy")
    return render(request, "reports/mis.html", {"title": "Transportation MIS", "f": f, "opts": filter_options(db, user),
                                                "totals": drill_totals(db, user, f), "qs": f.query()})


@router.get("/mis/data")
def mis_data(request: Request, db: Session = Depends(get_db), user: User = Depends(require("report.view"))):
    qp = request.query_params
    level = qp.get("level", "company")
    if level not in LEVELS:
        raise HTTPException(400)
    f = Filters.from_params(qp, default_range="fy")
    parents = {}
    for lvl in LEVELS[:-1]:
        v = qp.get(f"p_{lvl}")
        if v and v.isdigit():
            parents[lvl] = int(v)
    return JSONResponse({"level": level, "rows": drill(db, user, f, level, parents)})


@router.get("/reports/{code}")
def view(code: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require("report.view"))):
    rd = _def(code, user)
    f = Filters.from_params(request.query_params, default_range=rd.default_range)
    res = rd.run(db, user, f)
    show, choices = _show_filters(rd)
    limit = 2000
    return render(request, "reports/view.html", {
        "title": rd.title, "rd": rd, "res": res, "rows": res.rows[:limit], "truncated": len(res.rows) > limit,
        "f": f, "opts": filter_options(db, user), "show": show, "choices": choices, "qs": f.query(),
        "printing": request.query_params.get("print") == "1", "generated": local_now(),
        "org": cfg.get(db, "org.name")})


@router.get("/reports/{code}/export")
def export(code: str, request: Request, fmt: str = "xlsx", db: Session = Depends(get_db),
           user: User = Depends(require("report.view"))):
    rd = _def(code, user)
    if not user.can("data.export"):
        raise PermissionDenied("data.export")
    f = Filters.from_params(request.query_params, default_range=rd.default_range)
    res = rd.run(db, user, f)
    subtitle = f"{cfg.get(db, 'org.name')} · {f.describe()} ({res.date_basis or 'date'}) · generated " \
               f"{local_now():%d-%m-%Y %H:%M} by {user.full_name}"
    stamp = local_now().strftime("%Y%m%d-%H%M")
    audit.log(db, user, "REPORT_EXPORTED", "REPORTS", ref=code, details={"format": fmt, "rows": len(res.rows),
                                                                         "filters": f.query()})
    db.commit()
    if fmt == "csv":
        return Response(to_csv(res), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{code}-{stamp}.csv"'})
    if fmt == "pdf":
        return Response(to_pdf(res, rd.title, subtitle, cfg.get(db, "org.name")), media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{code}-{stamp}.pdf"'})
    return Response(to_xlsx(res, rd.title, subtitle),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{code}-{stamp}.xlsx"'})
