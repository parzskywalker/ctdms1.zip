"""v1.8.4 Planned Loads: the Depot Manager saves a vehicle plan from the gatepass builder (invoices + vehicle + charges);
a Dispatch Operator makes and prints the gatepass from it. The direct path (Depot Manager makes the gatepass) stays.

The gatepass is always rebuilt from the STORED plan, never from what the operator's browser posts."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DispatchMode, Invoice, LoadPlan, OutsideAuto, Transporter, User, utcnow
from app.security.auth import csrf_protect, require
from app.services import audit
from app.services import dispatch_service as ds
from app.services.errors import BusinessError
from app.services.scope import can_see_invoice, visible_cfas
from app.services.utils import safe_int
from app.web.gatepass_routes import _builder, _request_from
from app.web.templating import flash, get_form, redirect, render

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)
MODULE = "DISPATCH"
# builder fields kept with a plan; the date and time are set when the operator makes the gatepass
KEEP = ("mode", "transporter_id", "outside_auto_id", "pickup_mode", "auto_method", "trip_charge", "vehicle_no",
        "remarks", "amount_paid", "paid_edited", "group_cross_company", "eligibility_override_reason", "urgent_reason")


class _Form(dict):
    """The stored plan, shaped like a posted form for _request_from."""

    def getlist(self, key):
        v = self.get(key)
        return v if isinstance(v, list) else ([] if v is None else [v])


def open_plan_invoice_ids(db: Session) -> set[int]:
    return {i for p in db.scalars(select(LoadPlan).where(LoadPlan.status == LoadPlan.OPEN)) for i in (p.invoice_ids or [])}


def _plan(db: Session, user: User, plan_id: int) -> LoadPlan:
    p = db.get(LoadPlan, plan_id)
    if p is None or p.cfa_id not in {c.id for c in visible_cfas(db, user, active_only=False)}:
        raise BusinessError("Plan not found")
    return p


def _invoices(db: Session, user: User, ids: list[int]) -> list[Invoice]:
    invs = []
    for i in ids:
        inv = db.get(Invoice, i)
        if inv is None or not can_see_invoice(user, inv):
            raise BusinessError("An invoice on the plan is not available any more")
        invs.append(inv)
    return invs


def _stored_request(plan: LoadPlan) -> ds.GatepassRequest:
    f = _Form(plan.fields or {})
    req = _request_from(f)
    req.cases = {i: f.get(f"cases_{i}") for i in plan.invoice_ids}
    return req


@router.post("/load-plans/save")
def save(request: Request, form=Depends(get_form), db: Session = Depends(get_db), user: User = Depends(require("load.plan"))):
    ids = list(dict.fromkeys(i for i in (safe_int(x) for x in form.getlist("ids")) if i))
    try:
        if not ids:
            raise BusinessError("Choose the invoices for this vehicle")
        invs = _invoices(db, user, ids)
        held = open_plan_invoice_ids(db) & set(ids)
        if held:
            raise BusinessError("Already in another vehicle plan: " +
                                ", ".join(i.invoice_no for i in invs if i.id in held))
        fields = {k: form.get(k) for k in KEEP if form.get(k)}
        fields.update({f"cases_{i}": form.get(f"cases_{i}") for i in ids if form.get(f"cases_{i}")})
        req = _request_from(_Form(fields))
        req.cases = {i: fields.get(f"cases_{i}") for i in ids}
        _, _, problems = ds.plan_gatepass(db, user, invs, req)  # the same checks the gatepass will face
        if problems:
            raise BusinessError("; ".join(problems))
    except BusinessError as e:
        db.rollback()
        return _builder(request, db, user, ids=ids, form=form,
                        problems=[p.strip() for p in e.message.replace("Cannot generate gatepass: ", "").split(";") if p.strip()])
    p = LoadPlan(cfa_id=invs[0].cfa_id, invoice_ids=ids, fields=fields, planned_by_id=user.id, status=LoadPlan.OPEN)
    db.add(p)
    db.flush()
    audit.log(db, user, "LOAD_PLAN_SAVED", MODULE, record_type="LoadPlan", record_id=p.id, ref=f"PLAN-{p.id}",
              details={"invoices": [i.invoice_no for i in invs], **{k: v for k, v in fields.items() if k in KEEP}})
    db.commit()
    flash(request, f"Vehicle plan #{p.id} saved ({len(invs)} invoice(s)). An operator makes and prints the gatepass "
                   "from Planned Loads.", "success")
    return redirect("/load-plans")


@router.get("/load-plans")
def plans(request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    cfa_ids = [c.id for c in visible_cfas(db, user, active_only=False)]
    rows = list(db.scalars(select(LoadPlan).where(LoadPlan.cfa_id.in_(cfa_ids)).order_by(LoadPlan.id.desc()).limit(60)))
    return render(request, "v18/load_plans.html", {"title": "Planned Loads", "open": [p for p in rows if p.status == LoadPlan.OPEN],
                                                   "done": [p for p in rows if p.status != LoadPlan.OPEN][:20],
                                                   "summary": {p.id: _summary(db, p) for p in rows}})


def _summary(db: Session, p: LoadPlan) -> dict:
    f = p.fields or {}
    tr = db.get(Transporter, safe_int(f.get("transporter_id"))) if f.get("transporter_id") else None
    auto = db.get(OutsideAuto, safe_int(f.get("outside_auto_id"))) if f.get("outside_auto_id") else None
    who = " + ".join(x for x in (tr.name if tr else None, f"local auto {auto.name}" if auto else None) if x)
    return {"mode": DispatchMode.LABELS.get(f.get("mode"), f.get("mode") or "—"), "carrier": who or "Customer / own vehicle",
            "vehicle": f.get("vehicle_no") or "", "remarks": f.get("remarks") or "", "urgent": f.get("urgent_reason") or "",
            "invoices": [db.get(Invoice, i) for i in p.invoice_ids or []],
            "cases": sum(safe_int(f.get(f"cases_{i}")) or 0 for i in p.invoice_ids or [])}


@router.get("/load-plans/{plan_id:int}")
def plan_detail(plan_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    p = _plan(db, user, plan_id)
    return render(request, "v18/load_plan.html", {"title": f"Vehicle plan #{p.id}", "p": p, "s": _summary(db, p)})


@router.post("/load-plans/{plan_id:int}/make")
def make(plan_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.operate"))):
    p = _plan(db, user, plan_id)
    # one maker only: the row is claimed before anything else happens (a second click / second operator gets nothing)
    if db.execute(update(LoadPlan).where(LoadPlan.id == p.id, LoadPlan.status == LoadPlan.OPEN)
                  .values(status="MAKING")).rowcount != 1:
        db.rollback()
        flash(request, f"Plan #{p.id} is not open any more (already made or cancelled).", "warn")
        return redirect("/load-plans")
    try:
        slip = ds.create_gatepass(db, user, _invoices(db, user, p.invoice_ids), _stored_request(p), plan=p)
    except BusinessError as e:
        db.rollback()
        flash(request, f"Plan #{p.id} cannot be made into a gatepass: {e.message.replace('Cannot generate gatepass: ', '')}. "
                       "Ask the Depot Manager to cancel the plan and plan the vehicle again.", "error")
        return redirect(f"/load-plans/{p.id}")
    p.status, p.slip_id, p.done_by_id, p.done_at = LoadPlan.MADE, slip.id, user.id, utcnow()
    audit.log(db, user, "LOAD_PLAN_MADE", MODULE, record_type="LoadPlan", record_id=p.id, ref=f"PLAN-{p.id}",
              details={"dispatch_no": slip.dispatch_no, "planned_by": p.planned_by.username})
    db.commit()
    flash(request, f"Gatepass {slip.dispatch_no} made from plan #{p.id}: {slip.invoice_count} invoice(s), "
                   f"{slip.total_cases} cases. Print it - the goods may leave only with the printed gatepass.", "success")
    return redirect(f"/gatepass/{slip.id}")


@router.post("/load-plans/{plan_id:int}/cancel")
def cancel(plan_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
           user: User = Depends(require("load.plan"))):
    p = _plan(db, user, plan_id)
    reason = (form.get("reason") or "").strip()
    if p.status != LoadPlan.OPEN:
        flash(request, f"Plan #{p.id} is not open any more.", "warn")
        return redirect("/load-plans")
    if len(reason) < 5:
        flash(request, "Give the reason for cancelling the plan (at least 5 characters).", "error")
        return redirect(f"/load-plans/{p.id}")
    p.status, p.cancel_reason, p.done_by_id, p.done_at = LoadPlan.CANCELLED, reason, user.id, utcnow()
    audit.log(db, user, "LOAD_PLAN_CANCELLED", MODULE, record_type="LoadPlan", record_id=p.id, ref=f"PLAN-{p.id}",
              reason=reason)
    db.commit()
    flash(request, f"Plan #{p.id} cancelled; its invoices are back on Ready to Load.", "success")
    return redirect("/load-plans")
