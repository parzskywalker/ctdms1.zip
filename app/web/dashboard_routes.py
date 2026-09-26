from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security.auth import csrf_protect, current_user
from app.services.dashboard import dashboard_data
from app.services.reports import Filters
from app.web.templating import filter_options, redirect, render

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not user.can("dashboard.view"):
        if user.can("dispatch.view"):
            return redirect("/dispatch/pending")
        if user.can("report.view"):
            return redirect("/reports")
        return redirect("/invoices")
    f = Filters.from_params(request.query_params, default_range="fy")
    data = dashboard_data(db, user, f)
    return render(request, "dashboard.html", {"title": "Dashboard", "f": f, "opts": filter_options(db, user),
                                              "d": data, "qs": f.query()})
