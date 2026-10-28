"""Master data CRUD (generic engine) + ERP code / field mappings + customer merge."""
from __future__ import annotations

import csv
import difflib
import io
import math
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    CFA, Company, Customer, CustomerDeliveryLocation, CustomerTypeMaster, ERPCodeMapping, ERPFieldMapping, ERPSystem, FreightRate, ImportTemplate,
    Invoice, InvoiceMarking, LocalAutoRate, Location, OutsideAuto, Transporter, User,
)
from app.security.auth import csrf_protect, require
from app.services import audit, recognition
from app.services import customer_area_service as cas
from app.services.errors import BusinessError
from app.services.import_service import TARGET_FIELDS, add_code_mapping, find_code_mapping
from app.services.scope import apply_cfa_scope, apply_company_scope, can_see
from app.services.utils import csv_safe, parse_date, parse_decimal, parse_int, safe_int
from app.web.templating import back_url, flash, get_form, redirect, render, safe_path

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)


@dataclass
class F:
    name: str
    label: str
    kind: str = "text"  # text textarea int decimal bool select multi email date
    required: bool = False
    options: str | None = None  # erp / location / company / cfa / choice:<a,b>
    list: bool = False
    upper: bool = False
    help: str = ""


@dataclass
class MasterDef:
    key: str
    model: type
    title: str
    singular: str
    fields: list[F]
    search: list[str] = field(default_factory=lambda: ["code", "name"])
    scope: str | None = None  # company / cfa


MASTERS: dict[str, MasterDef] = {
    "companies": MasterDef("companies", Company, "Company Master (principals)", "Company", [
        F("code", "Company code", required=True, list=True, upper=True),
        F("name", "Company name", required=True, list=True),
        F("marking", "Recognition code", list=True, upper=True,
          help="Short code used in the Invoice Recognition Code, e.g. ABC (blank = the company code)"),
        F("relationship_type", "Relationship", "select", options="choice:PRINCIPAL,OWN", list=True,
          help="PRINCIPAL = company we dispatch for · OWN = own distribution business"),
        F("erp_system_id", "Source ERP", "select", options="erp", list=True),
        F("cfas", "Depots handling this company", "multi", options="cfa", list=True),
        F("gstin", "GSTIN", upper=True), F("contact_person", "Contact person"), F("contact_phone", "Contact number"),
        F("email", "Email", "email"), F("address", "Address", "textarea"), F("notes", "Reference notes", "textarea"),
        F("is_active", "Active", "bool", list=True),
    ], scope="company"),
    "depots": MasterDef("depots", CFA, "Depot Master (godowns / dispatch locations)", "Depot", [
        F("code", "Depot code", required=True, list=True, upper=True,
          help="Unique for every depot, also when several depots are in the same city"),
        F("name", "Depot name", required=True, list=True),
        F("locality", "Locality / area", list=True, help="e.g. Guma, Kamal Vihar"),
        F("city", "City", required=True, list=True), F("state", "State", list=True), F("pincode", "PIN code"),
        F("address", "Address", "textarea"),
        F("marking", "Recognition code", list=True, upper=True,
          help="Short code used in the Invoice Recognition Code, e.g. GUMA (blank = the depot code)"),
        F("companies", "Companies handled", "multi", options="company", list=True),
        F("default_erp_id", "Source ERP (usual)", "select", options="erp", list=True),
        F("warehouse", "Godown / building"), F("gstin", "GSTIN", upper=True), F("contact_person", "Contact person"),
        F("contact_phone", "Contact number"),
        F("case_control_enabled", "Case barcode labels and gate scanning", "bool",
          help="Every case of a gatepass from this depot gets its own barcode label and is scanned before gate-out. "
               "Switch it on when the depot has its printer and scanner ready."),
        F("is_active", "Active", "bool", list=True),
    ], search=["code", "name", "city", "locality"], scope="cfa"),
    "customer-types": MasterDef("customer-types", CustomerTypeMaster, "Customer Types", "Customer type", [
        F("code", "Type key", required=True, list=True, upper=True, help="e.g. RETAILER, CHAIN_PHARMACY"),
        F("name", "Name shown", required=True, list=True),
        F("marking", "Recognition code", list=True, upper=True,
          help="Short code used in the Invoice Recognition Code, e.g. RET"),
        F("description", "Description"), F("sort_order", "Order in lists", "int", list=True),
        F("is_active", "Active", "bool", list=True),
    ]),
    "customers": MasterDef("customers", Customer, "Customer Master", "Customer", [
        F("code", "Customer code (central)", required=True, list=True, upper=True),
        F("name", "Customer / Party name", required=True, list=True),
        F("party_type", "Customer type", "select", options="ctype", required=True, list=True,
          help="From Master Settings > Customer Types"),
        F("marking", "Recognition code", list=True, upper=True,
          help="Short code used in the Invoice Recognition Code, e.g. C025 (blank = the customer code)"),
        F("billing_address", "Billing address", "textarea"), F("delivery_address", "Delivery address", "textarea"),
        F("city", "City", list=True), F("state", "State"), F("pincode", "PIN code"),
        F("location_id", "Delivery area / location", "select", options="location", required=True, list=True),
        F("contact_person", "Contact person"), F("contact_phone", "Mobile number", list=True),
        F("whatsapp", "WhatsApp number"),
        F("email", "Email", "email"),
        F("gstin", "GSTIN", upper=True, list=True,
          help="Needed for e-invoice QR scanning: the buyer GSTIN on the QR finds this customer"),
        F("drug_license_no", "Drug licence no"),
        F("companies", "Company mapping", "multi", options="company", list=True),
        F("cfas", "Depot mapping", "multi", options="cfa"),
        F("source_erp_id", "Source ERP", "select", options="erp"),
        F("source_erp_code", "Source ERP customer code", upper=True,
          help="The customer's code in that ERP - recorded as an ERP mapping so imports find this customer"),
        F("is_active", "Active", "bool", list=True),
    ], search=["code", "name", "city", "marking"]),
    "locations": MasterDef("locations", Location, "Location / Route Master", "Location", [
        F("code", "Location code", required=True, list=True, upper=True),
        F("name", "Location name", required=True, list=True),
        F("city", "City", list=True), F("state", "State", list=True), F("pincode", "PIN code", list=True),
        F("route", "Route", list=True), F("zone", "Zone", list=True),
        F("distance_km", "Distance (km, optional)", "decimal", list=True),
        F("is_active", "Active", "bool", list=True),
    ], search=["code", "name", "city", "route"]),
    "transporters": MasterDef("transporters", Transporter, "Transporter Master (regular / long-distance)", "Transporter", [
        F("code", "Transporter code", required=True, list=True, upper=True),
        F("name", "Transporter name", required=True, list=True),
        F("contact_person", "Contact person", list=True), F("mobile", "Mobile number", list=True),
        F("email", "Email", "email"), F("address", "Address", "textarea"), F("city", "City"), F("state", "State"),
        F("gstin", "GST number", upper=True, list=True), F("pan", "PAN", upper=True),
        F("service_area", "Areas served", list=True, help="Rates per area: Master Settings › Transporter Rate Cards"),
        F("served_depots", "Depots served", "multi", options="cfa",
          help="Only these depots are offered this transporter. Leave all unticked = serves every depot."),
        F("uses_lr_book", "Gives us its own LR (builty) book", "bool",
          help="For the few transporters whose LR numbers come from their own book (imported per book)"),
        F("payment_mode", "Mode of payment", "select", options="choice:TBB,CASH", required=True, list=True,
          help="TBB = to be billed (periodic bill, reconciled) · CASH = paid at dispatch, no bill expected"),
        F("direct_pickup_charge", "Direct pickup charge (extra, ₹)", "decimal",
          help="Added when the transporter collects directly from our depot"),
        F("direct_pickup_basis", "Direct pickup charged", "select", options="choice:PER_TRIP,PER_CASE"),
        F("payment_terms", "Payment terms"),
        F("credit_days", "Credit days", "int"), F("bank_details", "Bank details", "textarea"),
        F("notes", "Other commercial details", "textarea"),
        F("is_active", "Active", "bool", list=True),
    ]),
    "autos": MasterDef("autos", OutsideAuto, "Local Auto Transporters", "Local auto", [
        F("code", "Code", required=True, list=True, upper=True),
        F("name", "Name (owner / driver)", required=True, list=True),
        F("mobile", "Mobile number", list=True),
        F("vehicle_no", "Vehicle number", upper=True, list=True),
        F("allowed_methods", "Allowed charging", "select", options="choice:PER_TRIP,PER_CASE,BOTH", required=True,
          list=True, help="Per trip = one amount for the whole trip · Per box = boxes × rate · Both = chosen on the gatepass"),
        F("charge_basis", "Usual method", "select", options="choice:PER_TRIP,PER_CASE",
          help="Pre-selected on the gatepass when both are allowed"),
        F("default_trip_charge", "Usual trip charge (₹)", "decimal", list=True,
          help="Only a suggestion - the actual amount of each trip is entered or confirmed on the gatepass"),
        F("default_box_rate", "Default rate per box (₹)", "decimal", list=True,
          help="Used when no matching Local Auto Box Rate exists"),
        F("notes", "Notes (usual routes …)", "textarea"),
        F("is_active", "Active", "bool", list=True),
    ], search=["code", "name", "mobile", "vehicle_no"]),
    "local-auto-rates": MasterDef("local-auto-rates", LocalAutoRate, "Local Auto Box Rates", "Box rate", [
        F("code", "Rate code", required=True, list=True, upper=True),
        F("outside_auto_id", "Local auto", "select", options="auto", required=True, list=True),
        F("location_id", "Location (blank = all)", "select", options="location", list=True),
        F("company_id", "Company (blank = all)", "select", options="company", list=True),
        F("cfa_id", "Depot (blank = all)", "select", options="cfa", list=True),
        F("rate_per_box", "Rate per box (₹)", "decimal", required=True, list=True),
        F("effective_from", "Effective from", "date", required=True, list=True),
        F("effective_to", "Effective to (optional)", "date", list=True),
        F("remarks", "Remarks"),
        F("is_active", "Active", "bool", list=True),
    ], search=["code", "remarks"]),
    "invoice-markings": MasterDef("invoice-markings", InvoiceMarking, "Invoice Code Mappings (existing ERP invoice markings)",
                                  "Invoice marking", [
        F("code", "Mapping code", required=True, list=True, upper=True),
        F("erp_system_id", "Source ERP (blank = any)", "select", options="erp", list=True),
        F("marking", "Marking in the ERP invoice number", required=True, list=True, upper=True,
          help="e.g. MB/ - the part of the ERP invoice number that shows the company"),
        F("match_type", "Where", "select", options="choice:PREFIX,CONTAINS", list=True),
        F("company_id", "Means company", "select", options="company", required=True, list=True),
        F("cfa_id", "Means depot (optional)", "select", options="cfa", list=True),
        F("customer_type", "Customer type (optional)", "select", options="ctype"),
        F("notes", "Notes"),
        F("is_active", "Active", "bool", list=True),
    ], search=["code", "marking"]),
}
MASTERS["cfas"] = MASTERS["depots"]  # v1.5 links


def _options(db: Session, user: User) -> dict:
    return {
        "erp": list(db.scalars(select(ERPSystem).order_by(ERPSystem.code))),
        "location": list(db.scalars(select(Location).order_by(Location.name))),
        "company": list(db.scalars(select(Company).order_by(Company.code))),
        "cfa": list(db.scalars(select(CFA).order_by(CFA.city, CFA.code))),
        "auto": list(db.scalars(select(OutsideAuto).order_by(OutsideAuto.name))),
        "ctype": [{"id": t.code, "code": t.marking or t.code, "name": t.name} for t in db.scalars(
            select(CustomerTypeMaster).where(CustomerTypeMaster.is_active.is_(True))
            .order_by(CustomerTypeMaster.sort_order, CustomerTypeMaster.name))],
    }


REL = {"company": Company, "cfa": CFA}


def _get_def(key: str) -> MasterDef:
    if key not in MASTERS:
        raise HTTPException(404)
    return MASTERS[key]


SELECT_REL = {"erp_system_id": "erp_system", "default_erp_id": "default_erp", "location_id": "location",
              "source_erp_id": "source_erp", "outside_auto_id": "outside_auto", "company_id": "company", "cfa_id": "cfa"}


def _scoped(stmt, md: MasterDef, user: User):
    if md.scope == "company":
        return apply_company_scope(stmt, user, md.model.id)
    if md.scope == "cfa":
        return apply_cfa_scope(stmt, user, md.model.id)
    return stmt


# ----------------------------------------------------------------------------- ERP mappings (declared first)
@router.get("/masters/erp-mappings")
def erp_mappings(request: Request, db: Session = Depends(get_db), user: User = Depends(require("master.view"))):
    qp = request.query_params
    ent = qp.get("entity", "CUSTOMER")
    if ent not in ("CUSTOMER", "LOCATION", "COMPANY", "CFA", "TRANSPORTER"):
        ent = "CUSTOMER"
    erp_id = safe_int(qp.get("erp_id"))
    stmt = select(ERPCodeMapping).where(ERPCodeMapping.entity_type == ent)
    if erp_id:
        stmt = stmt.where(ERPCodeMapping.erp_system_id == erp_id)
    maps = list(db.scalars(stmt.order_by(ERPCodeMapping.erp_system_id, ERPCodeMapping.source_code)))
    targets = {"CUSTOMER": Customer, "LOCATION": Location, "COMPANY": Company, "CFA": CFA, "TRANSPORTER": Transporter}
    model = targets[ent]
    tmap = {o.id: o for o in db.scalars(select(model))}
    erps = list(db.scalars(select(ERPSystem).order_by(ERPSystem.code)))
    fmaps = {e.id: list(db.scalars(select(ERPFieldMapping).where(ERPFieldMapping.erp_system_id == e.id)
                                   .order_by(ERPFieldMapping.target_field))) for e in erps}
    templates = list(db.scalars(select(ImportTemplate).order_by(ImportTemplate.name)))
    counts = {e: db.scalar(select(func.count(ERPCodeMapping.id)).where(ERPCodeMapping.entity_type == e))
              for e in targets}
    return render(request, "masters/erp_mappings.html", {
        "title": "ERP Mappings", "maps": maps, "ent": ent, "erp_id": erp_id, "erps": erps, "tmap": tmap,
        "targets": sorted(tmap.values(), key=lambda o: o.name), "fmaps": fmaps, "templates": templates,
        "counts": counts, "target_fields": TARGET_FIELDS, "companies": list(db.scalars(select(Company).order_by(Company.code)))})


@router.post("/masters/erp-mappings")
def add_mapping(request: Request, erp_system_id: int = Form(...), entity_type: str = Form(...),
                source_code: str = Form(...), source_name: str = Form(""), target_id: int = Form(...),
                company_id: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require("master.edit"))):
    add_code_mapping(db, user, erp_system_id=erp_system_id, entity_type=entity_type, source_code=source_code,
                     source_name=source_name or None, target_id=target_id,
                     company_id=parse_int(company_id) if company_id else None)
    db.commit()
    flash(request, f"Mapping {entity_type} '{source_code}' saved.", "success")
    return redirect(back_url(request, f"/masters/erp-mappings?entity={entity_type}"))


@router.post("/masters/erp-mappings/{mid:int}/toggle")
def toggle_mapping(mid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("master.edit"))):
    m = db.get(ERPCodeMapping, mid)
    if not m:
        raise HTTPException(404)
    m.is_active = not m.is_active
    audit.log(db, user, "ERP_MAPPING_TOGGLED", "ERP_MAPPING", record_type="ERPCodeMapping", record_id=m.id,
              ref=f"{m.entity_type}:{m.source_code}", field="is_active", old=not m.is_active, new=m.is_active)
    db.commit()
    return redirect(back_url(request, "/masters/erp-mappings"))


@router.post("/masters/erp-field-mappings/{erp_id:int}")
def save_field_mappings(erp_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                        user: User = Depends(require("admin.integrations"))):
    erp = db.get(ERPSystem, erp_id)
    if not erp:
        raise HTTPException(404)
    existing = {m.target_field: m for m in db.scalars(select(ERPFieldMapping).where(ERPFieldMapping.erp_system_id == erp_id))}
    for tgt in TARGET_FIELDS:
        src = (form.get(f"src_{tgt}") or "").strip()
        m = existing.get(tgt)
        if src and not m:
            db.add(ERPFieldMapping(erp_system_id=erp_id, target_field=tgt, source_field=src))
            audit.log(db, user, "ERP_FIELD_MAP", "ERP_MAPPING", record_type="ERPSystem", record_id=erp_id, ref=erp.code,
                      field=tgt, old=None, new=src)
        elif m and src != m.source_field:
            audit.log(db, user, "ERP_FIELD_MAP", "ERP_MAPPING", record_type="ERPSystem", record_id=erp_id, ref=erp.code,
                      field=tgt, old=m.source_field, new=src or None)
            if src:
                m.source_field = src
            else:
                db.delete(m)
    db.commit()
    flash(request, f"Invoice-field mapping for {erp.code} saved.", "success")
    return redirect(f"/admin/integrations/{erp_id:int}")


# ----------------------------------------------------------------------------- generic list / form
@router.get("/masters/{key}")
def master_list(key: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require("master.view"))):
    md = _get_def(key)
    qp = request.query_params
    stmt = _scoped(select(md.model), md, user)
    q = (qp.get("q") or "").strip()
    if q:
        stmt = stmt.where(or_(*[getattr(md.model, c).ilike(f"%{q}%") for c in md.search]))
    act = qp.get("active", "1")
    if act in ("1", "0"):
        stmt = stmt.where(md.model.is_active.is_(act == "1"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    if qp.get("export") == "csv" and user.can("data.export"):
        rows = list(db.scalars(stmt.order_by(md.model.code)))
        buf = io.StringIO()
        w = csv.writer(buf)
        cols = [f for f in md.fields]
        w.writerow([f.label for f in cols])
        for r in rows:
            w.writerow([csv_safe(_cell(r, f)) for f in cols])
        return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{key}.csv"'})
    page = safe_int(qp.get("page"), 1, minimum=1)
    per = 50
    rows = list(db.scalars(stmt.order_by(md.model.code).offset((page - 1) * per).limit(per)))
    return render(request, "masters/list.html", {"title": md.title, "md": md, "rows": rows, "q": q, "active": act,
                                                 "total": total, "page": page, "pages": max(math.ceil(total / per), 1),
                                                 "cols": [f for f in md.fields if f.list], "cell": _cell,
                                                 "qs": f"q={q}&active={act}"})


def _cell(obj, f: F):
    v = getattr(obj, f.name, None)
    if f.kind == "multi":
        return ", ".join(x.code for x in (v or []))
    if f.kind == "select" and f.options in ("erp", "location", "company", "cfa", "auto"):
        rel = getattr(obj, SELECT_REL.get(f.name, ""), None)
        if rel is None:
            return "" if f.options != "location" or f.required else "all"
        return rel.name if f.options in ("location", "auto") else rel.code
    if f.kind == "select" and f.options == "ctype":
        ct = getattr(obj, "customer_type", None)
        if isinstance(ct, str) or ct is None:
            return getattr(obj, "type_label", None) or (v or "")
        return ct.name
    if f.kind == "select" and (f.options or "").startswith("choice:"):
        return CHOICE_LABELS.get(v, (v or "").replace("_", " ").title())
    if f.kind == "bool":
        return "Yes" if v else "No"
    return "" if v is None else v


@router.get("/masters/{key}/new")
def master_new(key: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require("master.edit"))):
    md = _get_def(key)
    obj = md.model()
    obj.is_active = True
    for f in md.fields:  # show the default of fixed lists (customer type, payment mode, charge basis)
        if f.kind == "select" and (f.options or "").startswith("choice:") and getattr(obj, f.name, None) is None:
            setattr(obj, f.name, f.options[7:].split(",")[0])
    for k, v in request.query_params.items():  # prefill (e.g. from import correction)
        if hasattr(obj, k) and k not in ("id",):
            try:
                setattr(obj, k, int(v) if k.endswith("_id") and v.isdigit() else v)
            except Exception:  # noqa: BLE001
                pass
    return render(request, "masters/form.html", {"title": f"New {md.singular}", "md": md, "obj": obj, "is_new": True,
                                                 "opts": _options(db, user), "back": request.query_params.get("back", "")})


@router.get("/masters/{key}/{obj_id:int}")
def master_edit(key: str, obj_id: int, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require("master.view"))):
    md = _get_def(key)
    obj = db.get(md.model, obj_id)
    if not obj or (md.model is Company and not can_see(user, obj.id)) or (md.model is CFA and not can_see(user, None, obj.id)):
        raise HTTPException(404)
    extra = {}
    if key == "customers":
        extra["erp_maps"] = list(db.scalars(select(ERPCodeMapping).where(ERPCodeMapping.entity_type == "CUSTOMER",
                                                                         ERPCodeMapping.target_id == obj.id)))
        extra["erps"] = list(db.scalars(select(ERPSystem).order_by(ERPSystem.code)))
        others = list(db.scalars(select(Customer).where(Customer.id != obj.id)))
        names = {c.name.upper(): c for c in others}
        extra["similar"] = [names[n] for n in difflib.get_close_matches(obj.name.upper(), list(names), n=5, cutoff=0.6)]
        extra["all_customers"] = sorted(others, key=lambda c: c.name)
        extra["invoice_count"] = db.scalar(select(func.count(Invoice.id)).where(Invoice.customer_id == obj.id))
        extra["dlocs"] = cas.delivery_locations(db, obj, active_only=False)
        extra["areas"] = list(db.scalars(select(Location).where(Location.is_active.is_(True)).order_by(Location.name)))
    history = list(db.scalars(select(audit.AuditLog).where(audit.AuditLog.record_type == md.model.__name__,
                                                           audit.AuditLog.record_id == str(obj.id))
                              .order_by(audit.AuditLog.ts.desc()).limit(50)))
    return render(request, "masters/form.html", {"title": f"{md.singular} {obj.code}", "md": md, "obj": obj,
                                                 "is_new": False, "opts": _options(db, user), "history": history,
                                                 "back": "", **extra})


CHOICE_LABELS = {"TBB": "TBB (to be billed)", "CASH": "Cash at dispatch", "PER_TRIP": "Per trip", "PER_CASE": "Per box",
                 "BOTH": "Per trip or per box", "PREFIX": "Starts with", "CONTAINS": "Contains",
                 "PRINCIPAL": "Principal", "OWN": "Own"}


def _parse(db: Session, md: MasterDef, form) -> tuple[dict, dict]:
    values, m2m = {}, {}
    for f in md.fields:
        raw = form.get(f.name)
        if f.kind == "multi":
            ids = [int(x) for x in form.getlist(f.name) if str(x).isdigit()]
            m2m[f.name] = list(db.scalars(select(REL[f.options]).where(REL[f.options].id.in_(ids)))) if ids else []
            continue
        if f.kind == "bool":
            values[f.name] = raw in ("1", "on", "true", "yes")
            continue
        raw = (raw or "").strip()
        if not raw and f.kind == "select" and (f.options or "").startswith("choice:"):
            raw = f.options[7:].split(",")[0]  # fixed lists default to their first choice
        if not raw and f.kind == "select" and f.options == "ctype" and f.required:
            first = db.scalar(select(CustomerTypeMaster.code).where(CustomerTypeMaster.is_active.is_(True))
                              .order_by(CustomerTypeMaster.sort_order, CustomerTypeMaster.name).limit(1))
            raw = first or ""
        if f.required and not raw:
            raise BusinessError(f"{f.label} is required", field=f.name)
        if not raw:
            values[f.name] = None
            continue
        if f.kind == "select" and (f.options or "").startswith("choice:") and raw not in f.options[7:].split(","):
            raise BusinessError(f"{f.label}: choose one of the listed values", field=f.name)
        if f.kind == "select" and f.options == "ctype":
            if not db.scalar(select(CustomerTypeMaster.id).where(CustomerTypeMaster.code == raw.upper())):
                raise BusinessError(f"{f.label}: '{raw}' is not in Master Settings > Customer Types", field=f.name)
            values[f.name] = raw.upper()
            continue
        try:
            if f.kind == "int" or (f.kind == "select" and f.options in ("erp", "location", "company", "cfa", "auto")):
                values[f.name] = parse_int(raw)
            elif f.kind == "decimal":
                values[f.name] = parse_decimal(raw)
            elif f.kind == "date":
                values[f.name] = parse_date(raw)
            else:
                values[f.name] = raw.upper() if f.upper else raw
        except ValueError as e:
            raise BusinessError(f"{f.label}: {e}", field=f.name) from e
    return values, m2m


@router.post("/masters/{key}/save")
def master_save(key: str, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                user: User = Depends(require("master.edit"))):
    md = _get_def(key)
    obj_id = parse_int(form.get("id")) if form.get("id") else None
    values, m2m = _parse(db, md, form)
    dup = db.scalar(select(md.model).where(func.upper(md.model.code) == values["code"].upper(),
                                           md.model.id != (obj_id or 0)))
    if dup:
        raise BusinessError(f"Code {values['code']} already exists ({dup.name})")
    if obj_id:
        obj = db.get(md.model, obj_id)
        if not obj:
            raise HTTPException(404)
        changes = audit.diff(obj, values)
        for fname, new_list in m2m.items():
            old = sorted(x.code for x in getattr(obj, fname))
            new = sorted(x.code for x in new_list)
            if old != new:
                changes[fname] = (old, new)
        for k, v in values.items():
            setattr(obj, k, v)
        for k, v in m2m.items():
            setattr(obj, k, v)
        _after_save(db, user, md, obj, changes)
        reason = (form.get("change_reason") or "").strip() or None
        n = audit.log_changes(db, user, "MASTER_UPDATED", "MASTER", changes, record_type=md.model.__name__,
                              record_id=obj.id, ref=f"{md.singular} {obj.code}", reason=reason)
        db.commit()
        flash(request, f"{md.singular} {obj.code} saved ({n} change{'s' if n != 1 else ''} audited).", "success")
    else:
        obj = md.model(**values)
        for k, v in m2m.items():
            setattr(obj, k, v)
        db.add(obj)
        db.flush()
        _after_save(db, user, md, obj, None)
        audit.log(db, user, "MASTER_CREATED", "MASTER", record_type=md.model.__name__, record_id=obj.id,
                  ref=f"{md.singular} {obj.code}", details={k: audit._s(v) for k, v in values.items()})
        db.commit()
        flash(request, f"{md.singular} {obj.code} created.", "success")
        back = form.get("back") or ""
        if safe_path(back, ""):
            return redirect(safe_path(back))
    return redirect(f"/masters/{key}/{obj.id}")


def _after_save(db: Session, user: User, md: MasterDef, obj, changes: dict | None) -> None:
    """v1.6 follow-ups of a master save: local auto legacy fields, ERP code of a customer, recognition codes."""
    if md.model is OutsideAuto:
        obj.sync_legacy()
    if md.model is LocalAutoRate and obj.effective_to and obj.effective_to < obj.effective_from:
        raise BusinessError("Effective to is before effective from")
    if md.model is Customer and obj.source_erp_id and obj.source_erp_code:
        if not find_code_mapping(db, obj.source_erp_id, "CUSTOMER", obj.source_erp_code):
            add_code_mapping(db, user, erp_system_id=obj.source_erp_id, entity_type="CUSTOMER",
                             source_code=obj.source_erp_code, source_name=obj.name, target_id=obj.id)
    if md.model in (Company, CFA, Customer, CustomerTypeMaster) and (
            changes is None or {"marking", "party_type", "code"} & set(changes)):
        db.flush()
        recognition.after_master_change(db, md.model, obj)


@router.post("/masters/customers/{cid:int}/locations")
def customer_location_add(cid: int, request: Request, location_id: str = Form(""), label: str = Form(""),
                          address: str = Form(""), ship_to_code: str = Form(""), is_default: str | None = Form(None),
                          db: Session = Depends(get_db), user: User = Depends(require("master.edit"))):
    cust = db.get(Customer, cid)
    if not cust:
        raise HTTPException(404)
    row = cas.add_location(db, user, cust, location_id=safe_int(location_id) or 0, label=label, address=address,
                           ship_to_code=ship_to_code, is_default=bool(is_default))
    db.commit()
    flash(request, f"Delivery location {row.name} added to {cust.name}.", "success")
    return redirect(f"/masters/customers/{cust.id}")


@router.post("/masters/customers/{cid:int}/locations/{lid:int}/{action}")
def customer_location_action(cid: int, lid: int, action: str, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("master.edit"))):
    cust = db.get(Customer, cid)
    if not cust or action not in ("default", "remove"):
        raise HTTPException(404)
    (cas.set_default if action == "default" else cas.deactivate)(db, user, cust, lid)
    db.commit()
    flash(request, "Delivery locations updated.", "success")
    return redirect(f"/masters/customers/{cust.id}")


@router.post("/masters/customers/{cid:int}/merge")
def merge_customer(cid: int, request: Request, source_id: int = Form(...), reason: str = Form(""),
                   db: Session = Depends(get_db), user: User = Depends(require("master.edit"))):
    """Merge duplicate customer `source` into `target` (cid): invoices, rates and ERP mappings move over."""
    target = db.get(Customer, cid)
    source = db.get(Customer, source_id)
    if not target or not source or target.id == source.id:
        raise BusinessError("Select a different customer to merge")
    if not reason or len(reason.strip()) < 5:
        raise BusinessError("Reason is required for a merge")
    n_inv = db.execute(update(Invoice).where(Invoice.customer_id == source.id).values(customer_id=target.id)).rowcount
    n_rate = db.execute(update(FreightRate).where(FreightRate.customer_id == source.id)
                        .values(customer_id=target.id)).rowcount
    n_map = db.execute(update(ERPCodeMapping).where(ERPCodeMapping.entity_type == "CUSTOMER",
                                                    ERPCodeMapping.target_id == source.id)
                       .values(target_id=target.id)).rowcount
    have = {(r.location_id, r.label) for r in cas.delivery_locations(db, target, active_only=False)}
    for r in cas.delivery_locations(db, source, active_only=False):  # the source's delivery locations move over
        if (r.location_id, r.label) in have:
            r.is_active = False
        else:
            r.customer_id = target.id
            r.is_default = False
            have.add((r.location_id, r.label))
    source.merged_into_id = target.id  # future imports using the old code resolve to the target
    for c in source.companies:
        if c not in target.companies:
            target.companies.append(c)
    source.is_active = False
    source.name = f"{source.name} [merged into {target.code}]"[:160]
    db.flush()
    recognition.after_master_change(db, Customer, target)
    audit.log(db, user, "CUSTOMER_MERGED", "MASTER", record_type="Customer", record_id=target.id,
              ref=f"{source.code} → {target.code}", reason=reason,
              details={"invoices": n_inv, "rates": n_rate, "mappings": n_map})
    db.commit()
    flash(request, f"Merged {source.code} into {target.code}: {n_inv} invoices, {n_rate} rates, {n_map} ERP mappings moved. "
                   f"{source.code} deactivated.", "success")
    return redirect(f"/masters/customers/{target.id}")
