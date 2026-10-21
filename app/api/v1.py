"""REST API v1 (JSON). Auth: Bearer JWT from /api/v1/auth/token, or the browser session (+ X-CSRF-Token).
ERP push endpoints authenticate with a per-ERP X-API-Key instead.
"""
from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import __version__
from app.db import get_db
from app.integrations.runner import erp_for_api_key, field_map_for
from app.models import (
    CFA, Company, Customer, DispatchSlip, ERPSystem, Invoice, InvoiceStatus, Location, Transporter, User, utcnow,
)
from app.security.auth import (
    PermissionDenied, authenticate, create_access_token, current_user, current_user_optional, require, require_any,
    same_token,
)
from app.services import audit
from app.services import dispatch_service as ds
from app.services.dashboard import dashboard_data, ops_dashboard_data
from app.services.errors import BusinessError
from app.services.freight_engine import calc_freight, find_rate
from app.services.import_service import Resolver, ingest_records
from app.services.pdf_service import dispatch_slips_pdf
from app.services.reports import REPORTS, Filters, filtered_invoices
from app.services.scope import can_see, can_see_invoice

router = APIRouter(prefix="/api/v1")


async def api_csrf(request: Request, _user: User | None = Depends(current_user_optional)) -> None:
    """Writes authenticated by the browser session cookie must carry X-CSRF-Token.

    Only a request that was really authenticated by a valid Bearer token is exempt (a request with a Bearer
    header is judged by the token alone - see current_user_optional). The decision is taken from how the user
    was authenticated, not from which headers are present: an arbitrary 'Authorization: xyz' or 'X-API-Key'
    header next to a session cookie no longer switches the protection off (QA-07). ERP push endpoints use their
    own X-API-Key check and do not accept the session at all.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if getattr(request.state, "auth_via", None) != "session":
        return  # Bearer-token caller, or not signed in (the endpoint's auth check answers 401)
    tok = request.headers.get("x-csrf-token")
    if not tok or not same_token(request.session.get("csrf", ""), tok):
        raise PermissionDenied(message="X-CSRF-Token header required for session-authenticated API writes")


# ----------------------------------------------------------------------------- schemas
class TokenIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str | None = None


class InvoiceIn(BaseModel):
    invoice_no: str
    invoice_date: str = Field(description="YYYY-MM-DD or DD-MM-YYYY")
    customer_code: str
    sales_value: Decimal | str
    taxable_value: Decimal | str | None = None
    dispatch_mode: str | None = Field(default=None, description="Planned: Self / Transporter / Direct / Outside auto")
    company_code: str | None = None
    cfa_code: str | None = None
    customer_name: str | None = None
    location: str | None = None
    boxes: int | None = None
    source_invoice_id: str | None = None
    transporter_code: str | None = None
    lr_no: str | None = None
    vehicle_no: str | None = None
    remarks: str | None = None


class IngestIn(BaseModel):
    erp_code: str | None = None
    default_company_code: str | None = None
    default_cfa_code: str | None = None
    validate_only: bool = False
    invoices: list[InvoiceIn]


class PushIn(BaseModel):
    invoices: list[dict[str, Any]] = Field(description="Records in the ERP's own field names (see ERP field mapping)")
    default_company_code: str | None = None
    default_cfa_code: str | None = None


class DispatchIn(BaseModel):
    transporter_id: int | None = None
    transporter_code: str | None = None
    vehicle_no: str | None = None
    lr_no: str | None = None
    dispatch_date: date | None = None
    dispatch_time: time | None = None
    remarks: str | None = None


class ReasonIn(BaseModel):
    reason: str


class AckIn(BaseModel):
    invoice_ids: list[int]


def _inv_out(inv: Invoice, user: User | None = None) -> dict:
    # What the transporter billed is financial reconciliation data: only for roles that work with bills / final reports
    show_billed = user is not None and (user.can("recon.view") or user.can("mis.view"))
    return {
        "id": inv.id, "central_id": inv.central_id, "invoice_no": inv.invoice_no,
        "original_invoice_no": inv.invoice_no,  # v1.6: the ERP number, never changed
        "recognition_code": inv.recognition_code, "recognition_note": inv.recognition_note,
        "invoice_date": inv.invoice_date.isoformat(), "fiscal_year": inv.fiscal_year,
        "source_erp": inv.source_erp.code if inv.source_erp else None, "source_invoice_id": inv.source_invoice_id,
        "company": {"id": inv.company_id, "code": inv.company.code, "name": inv.company.name},
        "cfa": {"id": inv.cfa_id, "code": inv.cfa.code, "name": inv.cfa.name},
        "depot": {"id": inv.cfa_id, "code": inv.cfa.code, "name": inv.cfa.name, "city": inv.cfa.city,
                  "locality": inv.cfa.locality},
        "customer": {"id": inv.customer_id, "code": inv.customer.code, "erp_code": inv.customer_code,
                     "name": inv.customer.name, "type": inv.customer.party_type,
                     "type_name": inv.customer.type_label},
        "location": {"id": inv.location_id, "code": inv.location.code, "name": inv.location.name},
        "sales_value": str(inv.sales_value), "boxes": inv.boxes,
        "taxable_value": str(inv.taxable_value) if inv.taxable_value is not None else None,
        "capture_mode": inv.capture_mode,
        "rate_per_box": str(inv.rate_per_box) if inv.rate_per_box is not None else None,
        "rate_level": inv.rate_level, "freight_rate_id": inv.freight_rate_id,
        "transportation_expense": str(inv.freight_amount) if inv.freight_amount is not None else None,
        "freight_basis": inv.freight_basis, "status": inv.status, "exception_code": inv.exception_code,
        "exception_reason": inv.exception_reason,
        "dispatch": {"dispatch_no": inv.dispatch_no, "transporter": inv.transporter.code if inv.transporter else None,
                     "vehicle_no": inv.vehicle_no, "lr_no": inv.lr_no,
                     "dispatch_date": inv.dispatch_date.isoformat() if inv.dispatch_date else None,
                     "dispatch_time": inv.dispatch_time.strftime("%H:%M") if inv.dispatch_time else None,
                     "print_count": inv.print_count,
                     "completed_at": inv.completed_at.isoformat() if inv.completed_at else None},
        "actual_freight": str(inv.actual_freight) if show_billed and inv.actual_freight is not None else None,
        "eway_bill": {"no": inv.eway_bill_no,
                      "valid_until": inv.eway_valid_until.isoformat() if inv.eway_valid_until else None},
        "delivery": {"status": inv.delivery_status,
                     "delivered_on": inv.delivered_on.isoformat() if inv.delivered_on else None,
                     "received_by": inv.received_by, "note": inv.pod_note, "recorded_by": inv.pod_source,
                     "transit_days": inv.transit_days},
        "qr_signature": inv.qr_signature,
        "erp_sync_status": inv.erp_sync_status,
        "created_at": inv.created_at.isoformat(), "updated_at": inv.updated_at.isoformat(),
    }


def _get(db: Session, user: User, invoice_id: int) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if not inv or not can_see_invoice(user, inv):
        raise HTTPException(404, "Invoice not found")
    return inv


# ----------------------------------------------------------------------------- auth
@router.post("/auth/token", response_model=TokenOut, tags=["auth"])
def token(body: TokenIn, db: Session = Depends(get_db)):
    user, err = authenticate(db, body.username, body.password)
    if not user:
        raise HTTPException(401, err)
    if user.must_change_password:
        raise HTTPException(403, "Password change required - sign in to the web app and set a new password first")
    tok, exp = create_access_token(user)
    audit.log(db, user, "API_TOKEN_ISSUED", "SECURITY", record_type="User", record_id=user.id, ref=user.username)
    db.commit()
    return TokenOut(access_token=tok, expires_in=exp, role=user.role.code)


@router.get("/me", tags=["auth"])
def me(user: User = Depends(require())):
    return {"id": user.id, "username": user.username, "full_name": user.full_name, "role": user.role.code,
            "permissions": user.role.permissions, "companies": None if user.all_companies else [c.code for c in user.companies],
            "cfas": None if user.all_cfas else [c.code for c in user.cfas]}


# ----------------------------------------------------------------------------- invoices
@router.get("/invoices", tags=["invoices"])
def list_invoices(request: Request, page: int = Query(1, ge=1, le=100_000), page_size: int = Query(100, ge=1, le=1000),
                  db: Session = Depends(get_db), user: User = Depends(require("invoice.view"))):
    f = Filters.from_params(request.query_params, default_range="none")
    stmt = filtered_invoices(user, f, include_cancelled=True)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
                      .offset((page - 1) * page_size).limit(page_size))
    return {"total": total, "page": page, "page_size": page_size, "items": [_inv_out(i, user) for i in rows]}


@router.get("/invoices/{invoice_id:int}", tags=["invoices"])
def get_invoice(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(require("invoice.view"))):
    return _inv_out(_get(db, user, invoice_id), user)


@router.post("/invoices/ingest", tags=["invoices"], dependencies=[Depends(api_csrf)])
def ingest(body: IngestIn, db: Session = Depends(get_db), user: User = Depends(require("invoice.import"))):
    erp = db.scalar(select(ERPSystem).where(ERPSystem.code == body.erp_code.upper())) if body.erp_code else None
    if body.erp_code and not erp:
        raise HTTPException(400, f"Unknown ERP {body.erp_code}")
    res = Resolver(db, erp.id if erp else None)
    co = res.company(body.default_company_code) if body.default_company_code else None
    cf = res.cfa(body.default_cfa_code) if body.default_cfa_code else None
    records = [i.model_dump(mode="json", exclude_none=True) for i in body.invoices]
    batch, result = ingest_records(db, user, erp, records, source="API", company_id=co.id if co else None,
                                   cfa_id=cf.id if cf else None, commit=not body.validate_only)
    db.commit()
    return _batch_result(batch, result)


def _batch_result(batch, result) -> dict:
    return {"batch_no": batch.batch_no, "batch_id": batch.id, "status": batch.status,
            "imported": result.get("imported", 0), "exceptions": result.get("exceptions", 0),
            "rejected": result.get("REJECTED", 0), "duplicates": result.get("DUPLICATE", 0),
            "rows": [{"row": r.row_no, "status": r.status, "invoice_id": r.invoice_id,
                      "invoice_no": (r.data or {}).get("invoice_no"), "errors": r.errors, "warnings": r.warnings}
                     for r in batch.rows]}


@router.get("/freight/quote", tags=["freight"])
def quote(company_code: str, location: str, cfa_code: str | None = None, customer_code: str | None = None,
          on_date: date | None = None, boxes: int | None = None, db: Session = Depends(get_db),
          user: User = Depends(require("rate.view"))):
    res = Resolver(db, None)
    co = res.company(company_code)
    if not co:
        raise HTTPException(400, "Unknown company")
    cf = res.cfa(cfa_code) if cfa_code else None
    cu = res.customer(customer_code) if customer_code else None
    lo = res.location(location)
    if not lo:
        raise HTTPException(400, "Unknown location")
    lk = find_rate(db, company_id=co.id, cfa_id=cf.id if cf else None, customer_id=cu.id if cu else None,
                   location_id=lo.id, on_date=on_date or date.today())
    return {"found": lk.found, "code": lk.code, "message": lk.message, "level": lk.level, "trace": lk.trace,
            "rate_id": lk.rate.id if lk.rate else None, "rate_per_box": str(lk.rate_per_box) if lk.found else None,
            "boxes": boxes, "transportation_expense": str(calc_freight(boxes, lk.rate_per_box)) if boxes and lk.found else None}


# ----------------------------------------------------------------------------- dispatch
_INV_LOAD = (selectinload(Invoice.company), selectinload(Invoice.cfa), selectinload(Invoice.customer),
             selectinload(Invoice.location), selectinload(Invoice.transporter), selectinload(Invoice.source_erp))


@router.get("/dispatch/pending", tags=["dispatch"])
def pending(request: Request, page: int = Query(1, ge=1, le=100_000), page_size: int = Query(500, ge=1, le=1000),
            after: str | None = Query(None, description="next_cursor from the previous response"),
            db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    """Open (not yet dispatched) invoices, oldest first (invoice date, then id), max `page_size` per call.

    Complete, reliable reading - recommended for integrations: call once, then keep calling with
    `after=<next_cursor>` until `has_more` is false. Every open invoice is returned exactly once even if others
    are dispatched or added in between. `page=2, 3, ...` also works for quick browsing, but page numbers shift
    when invoices leave the queue meanwhile. (Before v1.2: a bare list silently cut at 2,000.)
    """
    f = Filters.from_params(request.query_params, default_range="none")
    stmt = filtered_invoices(user, f, statuses=InvoiceStatus.OPEN)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    q = stmt.options(*_INV_LOAD).order_by(Invoice.invoice_date, Invoice.id)
    if after:
        try:
            d, i = after.split("_", 1)
            d, i = date.fromisoformat(d), int(i)
        except ValueError:
            raise HTTPException(422, "after: use the next_cursor value from the previous response") from None
        q = q.where(or_(Invoice.invoice_date > d, and_(Invoice.invoice_date == d, Invoice.id > i)))
        rows = db.scalars(q.limit(page_size + 1)).all()
        has_more = len(rows) > page_size
        rows = rows[:page_size]
    else:
        rows = db.scalars(q.offset((page - 1) * page_size).limit(page_size + 1)).all()
        has_more = len(rows) > page_size
        rows = rows[:page_size]
    last = rows[-1] if rows else None
    return {"total": total, "page": None if after else page, "page_size": page_size, "has_more": has_more,
            "next_cursor": f"{last.invoice_date.isoformat()}_{last.id}" if last and has_more else None,
            "items": [_inv_out(i, user) for i in rows]}


@router.post("/invoices/{invoice_id:int}/dispatch", tags=["dispatch"], dependencies=[Depends(api_csrf)])
def save_dispatch(invoice_id: int, body: DispatchIn, db: Session = Depends(get_db),
                  user: User = Depends(require("dispatch.operate"))):
    inv = _get(db, user, invoice_id)
    tid = body.transporter_id
    if body.transporter_code and not tid:
        tr = db.scalar(select(Transporter).where(Transporter.code == body.transporter_code.upper()))
        if not tr:
            raise HTTPException(400, "Unknown transporter")
        tid = tr.id
    ds.save_details(db, user, inv, transporter_id=tid, vehicle_no=body.vehicle_no, lr_no=body.lr_no,
                    dispatch_date=body.dispatch_date, dispatch_time=body.dispatch_time, remarks=body.remarks)
    db.commit()
    return _inv_out(inv, user)


@router.post("/invoices/{invoice_id:int}/verify", tags=["dispatch"], dependencies=[Depends(api_csrf)])
def verify(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(require("dispatch.verify"))):
    inv = _get(db, user, invoice_id)
    ds.verify(db, user, inv)
    db.commit()
    return _inv_out(inv, user)


@router.post("/invoices/{invoice_id:int}/generate-slip", tags=["dispatch"], dependencies=[Depends(api_csrf)])
def generate(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(require("dispatch.operate"))):
    inv = _get(db, user, invoice_id)
    slip = ds.generate_slip(db, user, inv)
    db.commit()
    return {"dispatch_no": slip.dispatch_no, "invoice": _inv_out(inv, user)}


@router.post("/invoices/{invoice_id:int}/print", tags=["dispatch"], dependencies=[Depends(api_csrf)])
def print_slip(invoice_id: int, size: str = "A5", reason: str | None = None, db: Session = Depends(get_db),
               user: User = Depends(require("dispatch.view"))):
    """Records a print event and returns the slip PDF."""
    inv = _get(db, user, invoice_id)
    size = "A4" if size.upper() == "A4" else "A5"
    slip, log = ds.record_print(db, user, inv, f"PDF_{size}", reason)
    db.commit()
    label = "ORIGINAL" if not log.is_reprint else f"REPRINT #{log.print_no - 1} (DUPLICATE)"
    pdf = dispatch_slips_pdf([(slip.snapshot, label, f"Print #{log.print_no} by {user.full_name} (API)")], size)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{slip.dispatch_no.replace("/", "-")}.pdf"'})


@router.post("/invoices/{invoice_id:int}/complete", tags=["dispatch"], dependencies=[Depends(api_csrf)])
def complete(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(require("dispatch.complete"))):
    inv = _get(db, user, invoice_id)
    try:
        ds.complete(db, user, inv)
    except BusinessError:
        db.commit()
        raise
    db.commit()
    return _inv_out(inv, user)


@router.post("/invoices/{invoice_id:int}/cancel", tags=["dispatch"], dependencies=[Depends(api_csrf)])
def cancel(invoice_id: int, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(require("dispatch.cancel"))):
    inv = _get(db, user, invoice_id)
    ds.cancel(db, user, inv, body.reason)
    db.commit()
    return _inv_out(inv, user)


# ----------------------------------------------------------------------------- tracking & proof of delivery
class DeliveryIn(BaseModel):
    status: str = Field(description="DELIVERED / SHORT / DAMAGED / REFUSED")
    delivered_on: date
    received_by: str | None = None
    note: str | None = Field(default=None, description="Required for SHORT / DAMAGED / REFUSED")
    reason: str | None = Field(default=None, description="Required when changing an existing delivery record")


@router.get("/track", tags=["tracking"])
def track(q: str, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    """Invoice no, LR, gatepass, e-way bill or IRN -> consignments with their milestones."""
    from app.services import delivery_service as dsv
    out = []
    for group in dsv.consignments_of(dsv.search(db, user, q)):
        slip = db.get(DispatchSlip, group[0].dispatch_slip_id) if group[0].dispatch_slip_id else None
        out.append({"stage": dsv.stage(group, slip), "gatepass": slip.dispatch_no if slip else None,
                    "customer": {"code": group[0].customer.code, "name": group[0].customer.name},
                    "area": group[0].location.name, "invoices": [_inv_out(i, user) for i in group],
                    "milestones": [{"key": st.key, "label": st.label, "done": st.done, "problem": st.problem,
                                    "at": st.when.isoformat() if st.when else None, "detail": st.detail}
                                   for st in dsv.timeline(db, group)]})
    return {"query": q, "consignments": out}


@router.post("/invoices/{invoice_id:int}/delivery", tags=["tracking"], dependencies=[Depends(api_csrf)])
def delivery(invoice_id: int, body: DeliveryIn, db: Session = Depends(get_db),
             user: User = Depends(require("dispatch.operate"))):
    """Proof of delivery for the consignment this invoice travels in (all its invoices are updated)."""
    from app.services import delivery_service as dsv
    inv = _get(db, user, invoice_id)
    group = dsv.consignment(db, inv)
    dsv.record(db, user, group, status=body.status.upper(), delivered_on=body.delivered_on,
               received_by=body.received_by, note=body.note, source="STAFF", reason=body.reason)
    db.commit()
    return {"updated": [_inv_out(i, user) for i in group]}


# ----------------------------------------------------------------------------- masters / reports / dashboard
MASTER_MODELS = {"companies": Company, "cfas": CFA, "depots": CFA, "customers": Customer, "locations": Location,
                 "transporters": Transporter, "erp-systems": ERPSystem}


# Using a master (dropdowns, searches, integrations that only need to resolve a code) and managing it are separate:
# master.use returns the lookup fields only; the full records (GSTIN, PAN, bank details, contacts ...) need
# master.view, which is Administrator-only.
LOOKUP_FIELDS = {
    Company: ["id", "code", "name", "marking", "relationship_type", "is_active"],
    CFA: ["id", "code", "name", "locality", "marking", "city", "state", "is_active"],
    Customer: ["id", "code", "name", "party_type", "marking", "city", "state", "pincode", "location_id", "is_active"],
    Location: ["id", "code", "name", "city", "state", "pincode", "route", "zone", "is_active"],
    Transporter: ["id", "code", "name", "payment_mode", "service_area", "city", "is_active"],
    ERPSystem: ["id", "code", "name", "is_active"],
}


@router.get("/masters/{entity}", tags=["masters"])
def masters(entity: str, db: Session = Depends(get_db), user: User = Depends(require("master.use"))):
    model = MASTER_MODELS.get(entity)
    if not model:
        raise HTTPException(404)
    full = user.can("master.view")
    out = []
    for o in db.scalars(select(model).order_by(model.code)):
        if (model is Company and not can_see(user, o.id)) or (model is CFA and not can_see(user, None, o.id)):
            continue
        cols = [c.name for c in model.__table__.columns if c.name not in ("api_key_hash", "config")] if full \
            else LOOKUP_FIELDS[model]
        d = {c: getattr(o, c) for c in cols}
        out.append({k: (str(v) if isinstance(v, Decimal) else v.isoformat() if hasattr(v, "isoformat") else v)
                    for k, v in d.items()})
    return out


@router.get("/reports", tags=["reports"])
def report_list(user: User = Depends(require_any("report.view", "mis.view"))):
    return [{"code": r.code, "title": r.title, "group": r.group, "description": r.description}
            for r in REPORTS.values() if user.can(r.perm)]


@router.get("/reports/{code}", tags=["reports"])
def report(code: str, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    rd = REPORTS.get(code)
    if not rd:
        raise HTTPException(404)
    if not user.can(rd.perm):  # final reports need mis.view (Administrator, Accounts, Management)
        raise PermissionDenied(rd.perm, "Your role does not include this report." if rd.final else None)
    res = rd.run(db, user, Filters.from_params(request.query_params, default_range=rd.default_range))

    def j(v):
        return str(v) if isinstance(v, Decimal) else (v.isoformat() if hasattr(v, "isoformat") else v)
    return {"code": code, "title": rd.title, "date_basis": res.date_basis,
            "columns": [{"key": c.key, "label": c.label, "kind": c.kind} for c in res.columns],
            "rows": [{k: j(v) for k, v in r.items()} for r in res.rows],
            "totals": {k: j(v) for k, v in (res.totals or {}).items()} or None}


@router.get("/dashboard/summary", tags=["reports"])
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(require("dashboard.view"))):
    if not user.can("mis.view"):  # counts only - the financial figures are not even computed
        return ops_dashboard_data(db, user)
    d = dashboard_data(db, user, Filters.from_params(request.query_params, default_range="fy"))

    def j(v):
        if isinstance(v, dict):
            return {k: j(x) for k, x in v.items()}
        if isinstance(v, list):
            return [j(x) for x in v]
        return str(v) if isinstance(v, Decimal) else v
    return j(d)


# ----------------------------------------------------------------------------- ERP push / write-back (API key)
def _erp(db: Session, erp_code: str, key: str | None) -> ERPSystem:
    erp = erp_for_api_key(db, erp_code, key)
    if not erp:
        raise HTTPException(401, "Invalid ERP code or API key")
    return erp


@router.post("/integrations/{erp_code}/invoices", tags=["integrations"])
def erp_push(erp_code: str, body: PushIn, x_api_key: str | None = Header(None), db: Session = Depends(get_db)):
    erp = _erp(db, erp_code, x_api_key)
    from app.integrations.base import get_adapter
    adapter = get_adapter(erp, field_map_for(db, erp))
    records = [adapter.transform(r) for r in body.invoices]
    res = Resolver(db, erp.id)
    co = res.company(body.default_company_code) if body.default_company_code else None
    cf = res.cfa(body.default_cfa_code) if body.default_cfa_code else None
    if not co and len([c for c in db.scalars(select(Company).where(Company.erp_system_id == erp.id))]) == 1:
        co = db.scalar(select(Company).where(Company.erp_system_id == erp.id))
    batch, result = ingest_records(db, None, erp, records, source="API", company_id=co.id if co else None,
                                   cfa_id=cf.id if cf else None)
    audit.log(db, None, "ERP_PUSH", "INTEGRATION", record_type="ImportBatch", record_id=batch.id, ref=erp.code,
              details={"records": len(records), "imported": result.get("imported")})
    erp.last_sync_at = utcnow()
    db.commit()
    return _batch_result(batch, result)


@router.get("/integrations/{erp_code}/dispatch-updates", tags=["integrations"])
def dispatch_updates(erp_code: str, limit: int = Query(500, le=5000), x_api_key: str | None = Header(None),
                     db: Session = Depends(get_db)):
    erp = _erp(db, erp_code, x_api_key)
    stmt = select(Invoice).where(Invoice.source_erp_id == erp.id, Invoice.status == InvoiceStatus.DISPATCHED,
                                 Invoice.erp_sync_status == "PENDING").order_by(Invoice.completed_at).limit(limit)
    return [{"invoice_id": i.id, "invoice_no": i.invoice_no, "source_invoice_id": i.source_invoice_id,
             "dispatch_no": i.dispatch_no, "transporter": i.transporter.name if i.transporter else None,
             "vehicle_no": i.vehicle_no, "lr_no": i.lr_no, "boxes": i.boxes,
             "dispatch_date": i.dispatch_date.isoformat() if i.dispatch_date else None,
             "dispatch_time": i.dispatch_time.strftime("%H:%M") if i.dispatch_time else None,
             "transportation_expense": str(i.freight_amount)} for i in db.scalars(stmt)]


@router.post("/integrations/{erp_code}/dispatch-updates/ack", tags=["integrations"])
def dispatch_ack(erp_code: str, body: AckIn, x_api_key: str | None = Header(None), db: Session = Depends(get_db)):
    erp = _erp(db, erp_code, x_api_key)
    n = 0
    for inv in db.scalars(select(Invoice).where(Invoice.id.in_(body.invoice_ids), Invoice.source_erp_id == erp.id)):
        inv.erp_sync_status = "SYNCED"
        inv.erp_synced_at = utcnow()
        n += 1
    audit.log(db, None, "ERP_WRITEBACK_ACK", "INTEGRATION", ref=erp.code, details={"count": n})
    db.commit()
    return {"acknowledged": n}


@router.get("/health", tags=["system"])
def health(db: Session = Depends(get_db)):
    db.scalar(select(func.count(Company.id)))
    return {"status": "ok", "version": __version__}
