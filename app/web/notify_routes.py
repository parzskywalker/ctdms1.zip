"""Customer dispatch alerts (WhatsApp / SMS): queue, status, retry."""
from __future__ import annotations

import math
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Customer, Notification, NotifyEvent, NotifyStatus, User
from app.security.auth import csrf_protect, require
from app.services import notify_service as ns
from app.services import settings_service as cfg
from app.services.scope import apply_cfa_scope, can_see
from app.services.utils import local_today, parse_date, safe_int, utc_start
from app.web.templating import back_url, flash, get_form, redirect, render

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)


def _get(db: Session, user: User, n_id: int) -> Notification:
    n = db.get(Notification, n_id)
    if not n or (n.cfa_id and not can_see(user, None, n.cfa_id)):
        raise HTTPException(404)
    return n


@router.get("/notifications")
def index(request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    qp = request.query_params
    status = qp.get("status") if qp.get("status") in NotifyStatus.ALL else ""
    event = qp.get("event") if qp.get("event") in NotifyEvent.LABELS else ""
    try:
        d_from = parse_date(qp.get("date_from")) if qp.get("date_from") else local_today() - timedelta(days=7)
        d_to = parse_date(qp.get("date_to")) if qp.get("date_to") else local_today()
    except ValueError:
        d_from, d_to = local_today() - timedelta(days=7), local_today()
    stmt = apply_cfa_scope(select(Notification), user, Notification.cfa_id).where(
        Notification.created_at >= utc_start(d_from), Notification.created_at < utc_start(d_to + timedelta(days=1)))
    if status:
        stmt = stmt.where(Notification.status == status)
    if event:
        stmt = stmt.where(Notification.event == event)
    q = (qp.get("q") or "").strip()
    if q:
        stmt = stmt.join(Customer, Notification.customer_id == Customer.id).where(
            Customer.name.ilike(f"%{q}%") | Notification.body.ilike(f"%{q}%") | Notification.to_number.ilike(f"%{q}%"))
    counts = {}
    for s in NotifyStatus.ALL:
        counts[s] = db.scalar(select(func.count()).select_from(
            apply_cfa_scope(select(Notification.id), user, Notification.cfa_id)
            .where(Notification.status == s).subquery())) or 0
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    page = safe_int(qp.get("page"), 1, minimum=1)
    per = 100
    rows = list(db.scalars(stmt.order_by(Notification.id.desc()).offset((page - 1) * per).limit(per)))
    qs = "&".join(f"{k}={v}" for k, v in qp.items() if k != "page")
    return render(request, "notifications/index.html", {
        "title": "Customer notifications", "rows": rows, "status": status, "event": event, "q": q,
        "d_from": d_from, "d_to": d_to, "statuses": NotifyStatus.ALL, "events": NotifyEvent.LABELS, "counts": counts,
        "total": total, "page": page, "pages": max(math.ceil(total / per), 1), "qs": qs,
        "provider": ns.provider(db), "paid_locked": ns.configured_provider(db) in ns.PAID_PROVIDERS
        and ns.provider(db) == "OFF", "on_gate_out": cfg.get(db, "notify.on_gate_out"),
        "on_lr": cfg.get(db, "notify.on_lr")})


@router.post("/notifications/{n_id:int}/retry")
def retry(n_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.operate"))):
    n = _get(db, user, n_id)
    ns.retry(db, user, n)
    db.commit()
    flash(request, f"Message #{n.id} queued again - it goes out with the next sending round.", "success")
    return redirect(back_url(request, "/notifications"))


@router.post("/notifications/send-now")
def send_now(request: Request, form=Depends(get_form), db: Session = Depends(get_db),
             user: User = Depends(require("dispatch.operate"))):
    if ns.provider(db) == "OFF":
        flash(request, "Notifications are switched off (System Settings › notify.provider).", "warn")
        return redirect("/notifications")
    res = ns.send_due(db, user=user)
    flash(request, f"Sent {res['sent']}, will retry {res['retry']}, failed {res['failed']}.",
          "success" if not res["failed"] else "warn")
    return redirect(back_url(request, "/notifications"))
