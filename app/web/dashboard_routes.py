from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BillStatus, TransporterBill, User
from app.security.auth import csrf_protect, current_user
from app.services.dashboard import dashboard_data, ops_dashboard_data
from app.services.reports import Filters
from app.services.scope import apply_strict_scope
from app.web.templating import filter_options, home_for, redirect, render

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)


def _bill_strip(db: Session, user: User) -> dict | None:
    """Accounts: where the transporter bills stand (counts only; amounts are in the bills and the final reports)."""
    if not user.can("recon.view"):
        return None
    rows = db.execute(apply_strict_scope(select(TransporterBill.status, func.count()), user, TransporterBill)
                      .group_by(TransporterBill.status)).all()
    by = {st: int(n) for st, n in rows}
    return {"to_approve": by.get(BillStatus.MATCHED, 0) + by.get(BillStatus.VARIANCE, 0),
            "to_pay": by.get(BillStatus.APPROVED, 0), "pending": by.get(BillStatus.PENDING, 0),
            "paid": by.get(BillStatus.PAID, 0)}


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not user.can("dashboard.view"):
        target = home_for(user)
        return redirect(target if target != "/" else "/training")
    if not user.can("mis.view"):
        # No Final Reports / MIS: the Depot Operations Dashboard - counts only, no sales / freight / variance figures
        title = {"CFA_MANAGER": "Depot Operations Dashboard", "DISPATCH_OPERATOR": "Dispatch Desk"}.get(
            user.role.code if user.role else "", "Operations Dashboard")
        return render(request, "dashboard_ops.html", {"title": title, "d": ops_dashboard_data(db, user)})
    f = Filters.from_params(request.query_params, default_range="fy")
    data = dashboard_data(db, user, f)
    code = user.role.code if user.role else ""
    title = {"ACCOUNTS": "Accounts Dashboard", "MANAGEMENT": "Management Dashboard"}.get(code, "Dashboard")
    return render(request, "dashboard.html", {"title": title, "f": f, "opts": filter_options(db, user),
                                              "d": data, "qs": f.query(), "bills": _bill_strip(db, user)})
