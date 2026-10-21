"""Report engine: filter model, report definitions and aggregated MIS queries.

Every report returns a ReportResult(columns, rows, totals) that the UI renders and the exporters
convert to Excel / CSV / PDF. All invoice-based queries respect the user's company/CFA scope.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable
from urllib.parse import urlencode

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, aliased, selectinload

from app.models import (
    CFA, AuditLog, Company, Customer, CustomerType, CustomerTypeMaster, DispatchMode, DispatchSlip, DispatchSlipLine,
    ERPSystem,
    FreightRate, Invoice, InvoiceItem, InvoiceOverride, InvoiceStatus, Location, OutsideAuto, RateLevel,
    SlipPrintLog, Transporter, TransporterBill, TransporterBillLine, User,
)
from app.services.scope import apply_invoice_scope, apply_scope_allow_null_cfa, apply_strict_scope
from app.services.utils import fy_start, local_today, money, parse_date, safe_int, utc_start

S = InvoiceStatus
ZERO = Decimal("0")


# ----------------------------------------------------------------------------- filters
@dataclass
class Filters:
    date_from: date | None = None
    date_to: date | None = None
    company_id: int | None = None
    customer_id: int | None = None
    cfa_id: int | None = None
    location_id: int | None = None
    transporter_id: int | None = None
    erp_id: int | None = None
    status: str | None = None
    invoice_no: str | None = None
    module: str | None = None
    q: str | None = None
    # v1.6
    city: str | None = None          # depot city (one city may have several depots)
    ctype: str | None = None         # customer type key
    rcode: str | None = None         # Invoice Recognition Code (part of it, e.g. ABC-RET)
    auto_method: str | None = None   # local auto PER_TRIP / PER_CASE

    KEYS = ["date_from", "date_to", "company_id", "customer_id", "cfa_id", "location_id", "transporter_id",
            "erp_id", "status", "invoice_no", "module", "q", "city", "ctype", "rcode", "auto_method"]

    @classmethod
    def from_params(cls, params, default_range: str = "month") -> "Filters":
        def i(k):
            return safe_int(params.get(k), minimum=1)

        def d(k):
            try:
                return parse_date(params.get(k)) if params.get(k) else None
            except ValueError:
                return None

        f = cls(date_from=d("date_from"), date_to=d("date_to"), company_id=i("company_id"),
                customer_id=i("customer_id"), cfa_id=i("cfa_id"), location_id=i("location_id"),
                transporter_id=i("transporter_id"), erp_id=i("erp_id"),
                status=(params.get("status") or None), invoice_no=(params.get("invoice_no") or "").strip() or None,
                module=(params.get("module") or None), q=(params.get("q") or "").strip() or None,
                city=(params.get("city") or "").strip() or None, ctype=(params.get("ctype") or "").strip() or None,
                rcode=(params.get("rcode") or "").strip().upper() or None,
                auto_method=params.get("auto_method") if params.get("auto_method") in ("PER_TRIP", "PER_CASE") else None)
        if "date_from" not in params and "date_to" not in params:
            today = local_today()
            if default_range == "month":
                f.date_from, f.date_to = today.replace(day=1), today
            elif default_range == "fy":
                f.date_from, f.date_to = fy_start(today), today
            elif default_range == "quarter":
                f.date_from, f.date_to = today - timedelta(days=90), today
        return f

    def query(self, **override) -> str:
        vals = {}
        for k in self.KEYS:
            v = override.get(k, getattr(self, k))
            if v not in (None, ""):
                vals[k] = v.isoformat() if isinstance(v, date) else v
        return urlencode(vals)

    def describe(self) -> str:
        parts = []
        if self.date_from or self.date_to:
            parts.append(f"{self.date_from:%d-%m-%Y}" if self.date_from else "…")
            parts[-1] += " to " + (f"{self.date_to:%d-%m-%Y}" if self.date_to else "…")
        for k in ("status", "invoice_no", "module", "q", "city", "ctype", "rcode", "auto_method"):
            if getattr(self, k):
                parts.append(f"{k}={getattr(self, k)}")
        return " · ".join(parts) or "All dates"


def filtered_invoices(user: User, f: Filters, *, date_col=None, statuses: list[str] | None = None,
                      include_cancelled: bool = False):
    date_col = date_col if date_col is not None else Invoice.invoice_date
    stmt = select(Invoice)
    stmt = apply_invoice_scope(stmt, user)
    return _apply_filters(stmt, f, date_col, statuses, include_cancelled)


def _apply_filters(stmt, f: Filters, date_col, statuses=None, include_cancelled=False):
    if f.date_from:
        stmt = stmt.where(date_col >= f.date_from)
    if f.date_to:
        stmt = stmt.where(date_col <= f.date_to)
    for attr, col in (("company_id", Invoice.company_id), ("customer_id", Invoice.customer_id),
                      ("cfa_id", Invoice.cfa_id), ("location_id", Invoice.location_id),
                      ("transporter_id", Invoice.transporter_id), ("erp_id", Invoice.source_erp_id)):
        v = getattr(f, attr)
        if v:
            stmt = stmt.where(col == v)
    if f.invoice_no:
        stmt = stmt.where(Invoice.invoice_no.ilike(f"%{f.invoice_no}%"))
    if f.rcode:
        stmt = stmt.where(Invoice.recognition_code.ilike(f"%{f.rcode}%"))
    if f.city:
        stmt = stmt.where(Invoice.cfa_id.in_(select(CFA.id).where(func.upper(CFA.city) == f.city.upper())))
    if f.ctype:
        stmt = stmt.where(Invoice.customer_id.in_(select(Customer.id).where(Customer.party_type == f.ctype)))
    if f.auto_method:
        stmt = stmt.where(Invoice.dispatch_slip_id.in_(select(DispatchSlip.id).where(
            DispatchSlip.auto_method == f.auto_method, DispatchSlip.outside_auto_id.isnot(None))))
    if statuses:
        stmt = stmt.where(Invoice.status.in_(statuses))
    if f.status:
        stmt = stmt.where(Invoice.status == f.status)
    elif not include_cancelled:
        stmt = stmt.where(Invoice.status != S.CANCELLED)
    return stmt


def month_expr(db: Session, col):
    if db.get_bind().dialect.name == "postgresql":
        return func.to_char(col, "YYYY-MM")
    return func.strftime("%Y-%m", col)


# ----------------------------------------------------------------------------- result model
@dataclass
class Col:
    key: str
    label: str
    kind: str = "text"  # text/int/money/rate/pct/date/datetime/status/invoice/num
    total: bool = False


@dataclass
class ReportResult:
    columns: list[Col]
    rows: list[dict]
    totals: dict | None = None
    note: str | None = None
    date_basis: str | None = None


@dataclass
class ReportDef:
    code: str
    title: str
    group: str
    description: str
    run: Callable[[Session, User, Filters], ReportResult]
    filters: list[str] = field(default_factory=lambda: ["date", "company", "customer", "cfa", "city", "ctype",
                                                        "location", "transporter", "erp", "status", "invoice_no",
                                                        "rcode"])
    # Who may open / export it. The default is the strict one: every consolidated, financial or management report
    # is a "final report" and needs mis.view (Administrator, Accounts, Management - fixed baseline). Only operational
    # registers and control reports name report.view (or another permission) explicitly.
    perm: str = "mis.view"
    default_range: str = "month"

    @property
    def final(self) -> bool:
        return self.perm == "mis.view"


def _ratio(a, b, mult=1):
    a, b = Decimal(a or 0), Decimal(b or 0)
    return (a / b * mult).quantize(Decimal("0.01")) if b else None


def _totals(cols: list[Col], rows: list[dict], derived: dict[str, Callable[[dict], object]] | None = None) -> dict:
    t: dict = {}
    for c in cols:
        if c.total:
            t[c.key] = sum((Decimal(str(r.get(c.key) or 0)) for r in rows), ZERO)
    for k, fn in (derived or {}).items():
        t[k] = fn(t)
    if cols:
        t[cols[0].key] = "TOTAL"
    return t


def _agg_columns():
    return [
        func.count(Invoice.id).label("invoices"),
        func.coalesce(func.sum(Invoice.boxes), 0).label("boxes"),
        func.coalesce(func.sum(Invoice.sales_value), 0).label("sales"),
        func.coalesce(func.sum(Invoice.freight_amount), 0).label("freight"),
        func.coalesce(func.sum(Invoice.actual_freight), 0).label("actual"),
        func.coalesce(func.sum(case((Invoice.actual_freight.isnot(None), Invoice.freight_amount), else_=0)), 0)
        .label("billed_calc"),
        func.sum(case((Invoice.status == S.DISPATCHED, 1), else_=0)).label("dispatched"),
    ]


def _finish_agg(r: dict) -> dict:
    for k in ("sales", "freight", "actual", "billed_calc"):
        r[k] = money(r.get(k))
    r["boxes"] = int(r.get("boxes") or 0)
    r["freight_per_box"] = _ratio(r["freight"], r["boxes"])
    r["freight_pct"] = _ratio(r["freight"], r["sales"], 100)
    r["variance"] = money(r["actual"] - r["billed_calc"]) if r["actual"] else None
    return r


AGG_DERIVED = {
    "freight_per_box": lambda t: _ratio(t.get("freight"), t.get("boxes")),
    "freight_pct": lambda t: _ratio(t.get("freight"), t.get("sales"), 100),
    "rate": lambda t: _ratio(t.get("freight"), t.get("boxes")),
    "variance": lambda t: money(Decimal(t.get("actual") or 0) - Decimal(t.get("billed_calc") or 0))
    if t.get("actual") else None,
}


def _grouped(db, user, f, keys: list[tuple[str, object]], joins: list, order_by=None, date_col=None,
             statuses=None) -> list[dict]:
    stmt = select(*[c.label(k) for k, c in keys], *_agg_columns()).select_from(Invoice)
    for j in joins:
        stmt = stmt.join(*j) if isinstance(j, tuple) else stmt.join(j)
    stmt = apply_invoice_scope(stmt, user)
    stmt = _apply_filters(stmt, f, date_col if date_col is not None else Invoice.invoice_date, statuses)
    stmt = stmt.group_by(*[c for _, c in keys])
    if order_by is not None:
        stmt = stmt.order_by(*order_by)
    return [_finish_agg(dict(r._mapping)) for r in db.execute(stmt)]


# ----------------------------------------------------------------------------- report implementations
def rpt_invoice_freight(db, user, f):
    cols = [Col("invoice_date", "Date", "date"), Col("company", "Company"), Col("cfa", "Depot"), Col("city", "City"),
            Col("customer", "Customer"), Col("ctype", "Customer Type"), Col("location", "Location"),
            Col("invoice_no", "Original ERP Invoice No", "invoice"), Col("rcode", "Invoice Recognition Code"),
            Col("boxes", "Boxes", "int", True), Col("rate", "Rate/Box", "rate"),
            Col("freight", "Transportation Expense", "money", True), Col("sales", "Sales Value", "money", True),
            Col("taxable", "Taxable Amount", "money", True),
            Col("status", "Dispatch Status", "status"), Col("erp", "ERP")]
    stmt = (select(Invoice.id, Invoice.invoice_date, Company.name, CFA.name, Customer.name, Location.name,
                   Invoice.invoice_no, Invoice.boxes, Invoice.rate_per_box, Invoice.freight_amount,
                   Invoice.sales_value, Invoice.status, ERPSystem.code, Invoice.taxable_value, CFA.city,
                   Customer.party_type, Invoice.recognition_code, Invoice.extra_charge)
            .select_from(Invoice).join(Company).join(CFA).join(Customer).join(Location, Invoice.location_id == Location.id)
            .outerjoin(ERPSystem, Invoice.source_erp_id == ERPSystem.id))
    stmt = _apply_filters(apply_invoice_scope(stmt, user), f, Invoice.invoice_date)
    stmt = stmt.order_by(Invoice.invoice_date, Invoice.invoice_no)
    tl = type_labels(db)
    rows = [dict(invoice_id=r[0], invoice_date=r[1], company=r[2], cfa=r[3], customer=r[4], location=r[5],
                 invoice_no=r[6], boxes=r[7], rate=r[8], freight=r[9], sales=r[10], status=r[11], erp=r[12], taxable=r[13], city=r[14], ctype=tl.get(r[15], r[15]),
                 rcode=r[16] or "")
            for r in db.execute(stmt)]
    return ReportResult(cols, rows, _totals(cols, rows, {"rate": AGG_DERIVED["rate"]}), date_basis="Invoice date")


def rpt_invoice_register(db, user, f):
    """Operational invoice list for the Invoice Register export: who / what / where / status - no sales value, rate or
    freight (those are the final report 'Invoice-wise Transportation Expense', which needs Final Reports / MIS)."""
    cols = [Col("invoice_date", "Date", "date"), Col("company", "Company"), Col("cfa", "Depot"), Col("city", "City"),
            Col("customer", "Customer"), Col("ctype", "Customer Type"), Col("location", "Location"),
            Col("invoice_no", "Original ERP Invoice No", "invoice"), Col("rcode", "Invoice Recognition Code"),
            Col("boxes", "Boxes", "int", True), Col("status", "Dispatch Status", "status"),
            Col("dispatch_no", "Dispatch No"), Col("transporter", "Transporter"), Col("lr_no", "LR No"),
            Col("vehicle_no", "Vehicle"), Col("erp", "ERP")]
    stmt = (select(Invoice.id, Invoice.invoice_date, Company.name, CFA.name, Customer.name, Location.name,
                   Invoice.invoice_no, Invoice.boxes, Invoice.status, ERPSystem.code, CFA.city, Customer.party_type,
                   Invoice.recognition_code, Invoice.dispatch_no, Transporter.name, Invoice.lr_no, Invoice.vehicle_no)
            .select_from(Invoice).join(Company).join(CFA).join(Customer)
            .join(Location, Invoice.location_id == Location.id)
            .outerjoin(ERPSystem, Invoice.source_erp_id == ERPSystem.id)
            .outerjoin(Transporter, Invoice.transporter_id == Transporter.id))
    stmt = _apply_filters(apply_invoice_scope(stmt, user), f, Invoice.invoice_date)
    stmt = stmt.order_by(Invoice.invoice_date, Invoice.invoice_no)
    tl = type_labels(db)
    rows = [dict(invoice_id=r[0], invoice_date=r[1], company=r[2], cfa=r[3], customer=r[4], location=r[5],
                 invoice_no=r[6], boxes=r[7], status=r[8], erp=r[9], city=r[10], ctype=tl.get(r[11], r[11]),
                 rcode=r[12] or "", dispatch_no=r[13] or "", transporter=r[14] or "", lr_no=r[15] or "",
                 vehicle_no=r[16] or "")
            for r in db.execute(stmt)]
    return ReportResult(cols, rows, _totals(cols, rows), date_basis="Invoice date")


def rpt_customer(db, user, f):
    cols = [Col("company", "Company"), Col("cfa", "Depot"), Col("customer_code", "Customer Code"),
            Col("customer", "Customer Name"), Col("location", "Customer Location"),
            Col("invoices", "Invoices", "int", True), Col("boxes", "Boxes", "int", True),
            Col("sales", "Sales Value", "money", True), Col("rate", "Rate/Box (avg)", "rate"),
            Col("freight", "Transportation Expense", "money", True), Col("freight_pct", "Freight % Sales", "pct")]
    rows = _grouped(db, user, f, [("company", Company.name), ("cfa", CFA.name), ("customer_code", Customer.code),
                                  ("customer", Customer.name), ("location", Location.name)],
                    [Company, CFA, Customer, (Location, Invoice.location_id == Location.id)],
                    order_by=[Company.name, CFA.name, func.sum(Invoice.freight_amount).desc()])
    for r in rows:
        r["rate"] = r["freight_per_box"]
    return ReportResult(cols, rows, _totals(cols, rows, AGG_DERIVED), date_basis="Invoice date")


def rpt_company(db, user, f):
    cols = [Col("company", "Company"), Col("cfa", "Depot"), Col("invoices", "Invoices", "int", True),
            Col("boxes", "Boxes", "int", True), Col("sales", "Sales", "money", True),
            Col("freight", "Transportation Expense", "money", True), Col("freight_per_box", "Freight / Box", "rate"),
            Col("freight_pct", "Freight % of Sales", "pct")]
    rows = _grouped(db, user, f, [("company", Company.name), ("cfa", CFA.name)], [Company, CFA],
                    order_by=[Company.name, CFA.name])
    return ReportResult(cols, rows, _totals(cols, rows, AGG_DERIVED), date_basis="Invoice date")


def rpt_cfa(db, user, f):
    cols = [Col("city", "City"), Col("cfa", "Depot"), Col("company", "Principal"), Col("dispatched", "Dispatch Count", "int", True),
            Col("boxes", "Boxes", "int", True), Col("sales", "Sales", "money", True),
            Col("freight", "Calculated Freight", "money", True), Col("actual", "Actual Freight (billed)", "money", True),
            Col("billed_calc", "Calculated (billed inv.)", "money", True), Col("variance", "Variance", "money")]
    rows = _grouped(db, user, f, [("city", CFA.city), ("cfa", CFA.name), ("company", Company.name)], [Company, CFA],
                    order_by=[CFA.city, CFA.name, Company.name])
    return ReportResult(cols, rows, _totals(cols, rows, AGG_DERIVED), date_basis="Invoice date",
                        note="One city may have several depots (e.g. Raipur: Guma, Kamal Vihar) - each depot is a row. "
                             "Variance = Actual billed − Calculated freight of the billed invoices (unbilled excluded).")


def rpt_city(db, user, f):
    """v1.6: freight by the CITY of the dispatching depot - all depots of a city together."""
    cols = [Col("city", "City"), Col("depots", "Depots"), Col("invoices", "Invoices", "int", True),
            Col("dispatched", "Dispatched", "int", True), Col("boxes", "Boxes", "int", True),
            Col("sales", "Sales", "money", True), Col("freight", "Calculated Freight", "money", True),
            Col("freight_per_box", "Freight / Box", "rate"), Col("freight_pct", "Freight % of Sales", "pct")]
    rows = _grouped(db, user, f, [("city", CFA.city)], [CFA], order_by=[CFA.city])
    depot_rows = _grouped(db, user, f, [("city", CFA.city), ("cfa", CFA.name)], [CFA])
    names: dict = defaultdict(list)
    for r in depot_rows:
        names[r["city"]].append(r["cfa"])
    for r in rows:
        r["depots"] = ", ".join(sorted(names.get(r["city"], []))) or ""
        r["city"] = r["city"] or "(city not set)"
    return ReportResult(cols, rows, _totals(cols, rows, AGG_DERIVED), date_basis="Invoice date",
                        note="See Depot-wise for each depot separately.")


def rpt_location(db, user, f):
    cols = [Col("location", "Location"), Col("zone", "Zone"), Col("invoices", "Invoices", "int", True),
            Col("boxes", "Boxes", "int", True), Col("rate", "Rate (avg/box)", "rate"),
            Col("freight", "Freight", "money", True), Col("sales", "Sales", "money", True),
            Col("freight_per_box", "Freight / Box", "rate"), Col("freight_pct", "Freight % of Sales", "pct")]
    rows = _grouped(db, user, f, [("location", Location.name), ("zone", Location.zone)],
                    [(Location, Invoice.location_id == Location.id)], order_by=[func.sum(Invoice.freight_amount).desc()])
    for r in rows:
        r["rate"] = r["freight_per_box"]
    return ReportResult(cols, rows, _totals(cols, rows, AGG_DERIVED), date_basis="Invoice date")


def rpt_transporter(db, user, f):
    cols = [Col("transporter", "Transporter"), Col("invoices", "Invoices", "int", True),
            Col("boxes", "Boxes", "int", True), Col("freight", "Calculated Freight", "money", True),
            Col("actual", "Actual Transporter Bill", "money", True), Col("billed_calc", "Calculated (billed)", "money", True),
            Col("variance", "Variance", "money"), Col("trips", "Trips (gatepasses)", "int", True),
            Col("per_case", "Cost / case", "rate"), Col("per_trip", "Cost / trip", "rate"),
            Col("lr_numbers", "LR Numbers"), Col("vehicles", "Vehicle Numbers")]
    statuses = [S.SLIP_GENERATED, S.SLIP_PRINTED, S.DISPATCHED]
    rows = _grouped(db, user, f, [("transporter_id", Transporter.id), ("transporter", Transporter.name)],
                    [(Transporter, Invoice.transporter_id == Transporter.id)], order_by=[Transporter.name],
                    date_col=Invoice.dispatch_date, statuses=statuses)
    det = select(Invoice.transporter_id, Invoice.lr_no, Invoice.vehicle_no, Invoice.dispatch_slip_id)
    det = _apply_filters(apply_invoice_scope(det, user), f, Invoice.dispatch_date, statuses)
    lrs, vehs, trips = defaultdict(list), defaultdict(list), defaultdict(set)
    for tid, lr, veh, slip_id in db.execute(det):
        if lr and lr not in lrs[tid]:
            lrs[tid].append(lr)
        if veh and veh not in vehs[tid]:
            vehs[tid].append(veh)
        if slip_id:
            trips[tid].add(slip_id)
    for r in rows:
        r["lr_numbers"] = ", ".join(lrs[r["transporter_id"]])
        r["vehicles"] = ", ".join(vehs[r["transporter_id"]])
        r["trips"] = len(trips[r["transporter_id"]])
        r["per_case"] = _ratio(r["freight"], r["boxes"])
        r["per_trip"] = _ratio(r["freight"], r["trips"])
    derived = {**AGG_DERIVED, "per_case": lambda t: _ratio(t.get("freight"), t.get("boxes")),
               "per_trip": lambda t: _ratio(t.get("freight"), t.get("trips"))}
    return ReportResult(cols, rows, _totals(cols, rows, derived), date_basis="Dispatch date",
                        note="A trip is one gatepass. Cost = calculated freight of the invoices on it.")


def rpt_dispatch_register(db, user, f):
    cols = [Col("dispatch_no", "Dispatch No"), Col("invoice_no", "Original ERP Invoice No", "invoice"),
            Col("rcode", "Invoice Recognition Code"), Col("company", "Company"),
            Col("cfa", "Depot"), Col("customer", "Customer"), Col("location", "Location"),
            Col("boxes", "Boxes", "int", True), Col("freight", "Freight", "money", True),
            Col("transporter", "Transporter"), Col("vehicle", "Vehicle"), Col("lr", "LR"),
            Col("status", "Status", "status"), Col("dispatched_at", "Dispatch Date/Time")]
    stmt = (select(Invoice.id, Invoice.dispatch_no, Invoice.invoice_no, Company.name, CFA.name, Customer.name,
                   Location.name, Invoice.boxes, Invoice.freight_amount, Transporter.name, Invoice.vehicle_no,
                   Invoice.lr_no, Invoice.status, Invoice.dispatch_date, Invoice.dispatch_time, Invoice.recognition_code)
            .select_from(Invoice).join(Company).join(CFA).join(Customer).join(Location, Invoice.location_id == Location.id)
            .outerjoin(Transporter, Invoice.transporter_id == Transporter.id)
            .where(Invoice.dispatch_no.isnot(None)))
    stmt = _apply_filters(apply_invoice_scope(stmt, user), f, Invoice.dispatch_date,
                          [S.SLIP_GENERATED, S.SLIP_PRINTED, S.DISPATCHED])
    stmt = stmt.order_by(Invoice.dispatch_date, Invoice.dispatch_time, Invoice.dispatch_no)
    rows = [dict(invoice_id=r[0], dispatch_no=r[1], invoice_no=r[2], company=r[3], cfa=r[4], customer=r[5],
                 location=r[6], boxes=r[7], freight=r[8], transporter=r[9], vehicle=r[10], lr=r[11], status=r[12],
                 dispatched_at=f"{r[13]:%d-%m-%Y} {r[14]:%H:%M}" if r[13] and r[14] else "", rcode=r[15] or "")
            for r in db.execute(stmt)]
    return ReportResult(cols, rows, _totals(cols, rows), date_basis="Dispatch date")


def rpt_pending(db, user, f):
    cols = [Col("invoice_no", "Invoice", "invoice"), Col("invoice_date", "Inv. Date", "date"),
            Col("age", "Age (days)", "int"), Col("company", "Company"), Col("cfa", "Depot"),
            Col("customer", "Customer"), Col("location", "Location"), Col("boxes", "Boxes", "int", True),
            Col("rate", "Rate/Box", "rate"), Col("freight", "Freight", "money", True),
            Col("sales", "Sales", "money", True), Col("status", "Status", "status"), Col("reason", "Exception")]
    stmt = (select(Invoice.id, Invoice.invoice_no, Invoice.invoice_date, Company.name, CFA.name, Customer.name,
                   Location.name, Invoice.boxes, Invoice.rate_per_box, Invoice.freight_amount, Invoice.sales_value,
                   Invoice.status, Invoice.exception_reason)
            .select_from(Invoice).join(Company).join(CFA).join(Customer).join(Location, Invoice.location_id == Location.id))
    stmt = _apply_filters(apply_invoice_scope(stmt, user), f, Invoice.invoice_date, S.OPEN)
    today = local_today()
    rows = [dict(invoice_id=r[0], invoice_no=r[1], invoice_date=r[2], age=(today - r[2]).days, company=r[3],
                 cfa=r[4], customer=r[5], location=r[6], boxes=r[7], rate=r[8], freight=r[9], sales=r[10],
                 status=r[11], reason=r[12])
            for r in db.execute(stmt.order_by(Invoice.invoice_date))]
    return ReportResult(cols, rows, _totals(cols, rows), date_basis="Invoice date")


def rpt_monthly(db, user, f):
    cols = [Col("month", "Month"), Col("invoices", "Invoices", "int", True), Col("dispatched", "Dispatched", "int", True),
            Col("boxes", "Boxes", "int", True), Col("sales", "Sales", "money", True),
            Col("freight", "Calculated Freight", "money", True), Col("freight_per_box", "Freight / Box", "rate"),
            Col("freight_pct", "Freight % Sales", "pct"), Col("actual", "Actual (billed)", "money", True),
            Col("variance", "Variance", "money")]
    m = month_expr(db, Invoice.invoice_date)
    rows = _grouped(db, user, f, [("month", m)], [], order_by=[m])
    for r in rows:
        try:
            r["month"] = datetime.strptime(r["month"], "%Y-%m").strftime("%b %Y")
        except (TypeError, ValueError):
            pass
    return ReportResult(cols, rows, _totals(cols, rows, AGG_DERIVED), date_basis="Invoice date")


def _bill_base(user, f):
    stmt = select(TransporterBill).join(Transporter)
    stmt = apply_strict_scope(stmt, user, TransporterBill)
    if f.date_from:
        stmt = stmt.where(TransporterBill.bill_date >= f.date_from)
    if f.date_to:
        stmt = stmt.where(TransporterBill.bill_date <= f.date_to)
    if f.transporter_id:
        stmt = stmt.where(TransporterBill.transporter_id == f.transporter_id)
    if f.company_id:
        stmt = stmt.where(TransporterBill.company_id == f.company_id)
    if f.cfa_id:
        stmt = stmt.where(TransporterBill.cfa_id == f.cfa_id)
    if f.status:
        stmt = stmt.where(TransporterBill.status == f.status)
    return stmt


def rpt_freight_vs_actual(db, user, f):
    cols = [Col("bill_no", "Bill No"), Col("bill_date", "Bill Date", "date"), Col("transporter", "Transporter"),
            Col("company", "Company"), Col("cfa", "Depot"), Col("period", "Billing Period"),
            Col("lines", "Lines", "int", True), Col("calculated", "System Freight", "money", True),
            Col("actual", "Actual Bill", "money", True), Col("variance", "Variance", "money", True),
            Col("variance_pct", "Variance %", "pct"), Col("status", "Status", "status")]
    rows = []
    for b in db.scalars(_bill_base(user, f).order_by(TransporterBill.bill_date)):
        rows.append(dict(bill_id=b.id, bill_no=b.bill_no, bill_date=b.bill_date, transporter=b.transporter.name,
                         company=b.company.name if b.company else "All", cfa=b.cfa.name if b.cfa else "All",
                         period=(f"{b.period_from:%d-%m-%Y} – {b.period_to:%d-%m-%Y}" if b.period_from and b.period_to
                                 else ""), lines=len(b.lines), calculated=b.calculated_amount, actual=b.bill_amount,
                         variance=b.variance, variance_pct=b.variance_pct, status=b.status))
    t = _totals(cols, rows, {"variance_pct": lambda t: _ratio(t.get("variance"), t.get("calculated"), 100)})
    return ReportResult(cols, rows, t, date_basis="Bill date")


def rpt_variance(db, user, f):
    cols = [Col("bill_no", "Bill No"), Col("bill_date", "Bill Date", "date"), Col("transporter", "Transporter"),
            Col("invoice_no", "Invoice", "invoice"), Col("lr_no", "LR No"), Col("dispatch_no", "Dispatch No"),
            Col("sys_boxes", "System Boxes", "int", True), Col("billed_boxes", "Billed Boxes", "int", True),
            Col("calculated", "System Freight", "money", True), Col("billed", "Billed Amount", "money", True),
            Col("variance", "Variance", "money", True), Col("match", "Match Status", "status"), Col("remarks", "Remarks")]
    bills = _bill_base(user, f).subquery()
    stmt = (select(TransporterBillLine, TransporterBill, Transporter, Invoice)
            .join(TransporterBill, TransporterBillLine.bill_id == TransporterBill.id)
            .join(Transporter, TransporterBill.transporter_id == Transporter.id)
            .outerjoin(Invoice, TransporterBillLine.invoice_id == Invoice.id)
            .where(TransporterBill.id.in_(select(bills.c.id)))
            .where(or_(TransporterBillLine.match_status != "MATCHED",
                       and_(TransporterBillLine.variance.isnot(None), TransporterBillLine.variance != 0)))
            .order_by(TransporterBill.bill_date, TransporterBillLine.id))
    if f.q:
        stmt = stmt.where(or_(TransporterBillLine.lr_no.ilike(f"%{f.q}%"), TransporterBillLine.invoice_no.ilike(f"%{f.q}%")))
    rows = []
    for ln, b, tr, inv in db.execute(stmt):
        rows.append(dict(invoice_id=inv.id if inv else None, bill_no=b.bill_no, bill_date=b.bill_date,
                         transporter=tr.name, invoice_no=ln.invoice_no, lr_no=ln.lr_no, dispatch_no=ln.dispatch_no,
                         sys_boxes=inv.boxes if inv else None, billed_boxes=ln.billed_boxes,
                         calculated=ln.calculated_amount, billed=ln.billed_amount, variance=ln.variance,
                         match=ln.match_status, remarks=ln.remarks))
    return ReportResult(cols, rows, _totals(cols, rows), date_basis="Bill date",
                        note="Lines that are not an exact match: amount variance, not found, duplicate billing, "
                             "not dispatched or other transporter.")


def rpt_exceptions(db, user, f):
    cols = [Col("invoice_no", "Invoice", "invoice"), Col("invoice_date", "Inv. Date", "date"),
            Col("age", "Age (days)", "int"), Col("company", "Company"), Col("cfa", "Depot"), Col("customer", "Customer"),
            Col("location", "Location"), Col("boxes", "Boxes", "int"), Col("status", "Status", "status"),
            Col("code", "Exception"), Col("reason", "Details")]
    stmt = (select(Invoice.id, Invoice.invoice_no, Invoice.invoice_date, Company.name, CFA.name, Customer.name,
                   Location.name, Invoice.boxes, Invoice.status, Invoice.exception_code, Invoice.exception_reason)
            .select_from(Invoice).join(Company).join(CFA).join(Customer).join(Location, Invoice.location_id == Location.id))
    stmt = _apply_filters(apply_invoice_scope(stmt, user), f, Invoice.invoice_date, [S.RATE_MISSING, S.EXCEPTION])
    today = local_today()
    rows = [dict(invoice_id=r[0], invoice_no=r[1], invoice_date=r[2], age=(today - r[2]).days, company=r[3], cfa=r[4],
                 customer=r[5], location=r[6], boxes=r[7], status=r[8], code=r[9], reason=r[10])
            for r in db.execute(stmt.order_by(Invoice.invoice_date))]
    return ReportResult(cols, rows, None, date_basis="Invoice date")


def rpt_rate_missing(db, user, f):
    cols = [Col("company", "Company"), Col("cfa", "Depot"), Col("location", "Location"),
            Col("invoices", "Invoices", "int", True), Col("boxes", "Boxes", "int", True),
            Col("sales", "Sales", "money", True), Col("first_date", "Earliest Invoice", "date"),
            Col("fix", "Action")]
    stmt = (select(Company.id, Company.name, CFA.id, CFA.name, Location.id, Location.name, func.count(Invoice.id),
                   func.coalesce(func.sum(Invoice.boxes), 0), func.coalesce(func.sum(Invoice.sales_value), 0),
                   func.min(Invoice.invoice_date))
            .select_from(Invoice).join(Company).join(CFA).join(Location, Invoice.location_id == Location.id))
    stmt = _apply_filters(apply_invoice_scope(stmt, user), f, Invoice.invoice_date, [S.RATE_MISSING, S.EXCEPTION])
    stmt = stmt.where(Invoice.exception_code.in_(["RATE_MISSING", "RATE_CONFLICT"]))
    stmt = stmt.group_by(Company.id, Company.name, CFA.id, CFA.name, Location.id, Location.name)
    rows = [dict(company=r[1], cfa=r[3], location=r[5], invoices=r[6], boxes=int(r[7] or 0), sales=money(r[8]),
                 first_date=r[9], fix=f"/rates/new?company_id={r[0]}&cfa_id={r[2]}&location_id={r[4]}"
                                    f"&effective_from={r[9].isoformat() if r[9] else ''}")
            for r in db.execute(stmt)]
    return ReportResult(cols, rows, _totals(cols, rows), date_basis="Invoice date",
                        note="Grouped by missing combination. Use 'Add rate' to create the rate; once approved, "
                             "matching invoices are re-priced automatically.")


def rpt_audit(db, user, f):
    cols = [Col("ts", "Date/Time", "datetime"), Col("username", "User"), Col("module", "Module"),
            Col("action", "Action"), Col("record", "Record"), Col("ref", "Reference"), Col("field", "Field"),
            Col("old", "Old Value"), Col("new", "New Value"), Col("reason", "Reason"), Col("ip", "IP / Session")]
    stmt = select(AuditLog)
    if f.date_from:
        stmt = stmt.where(AuditLog.ts >= utc_start(f.date_from))
    if f.date_to:
        stmt = stmt.where(AuditLog.ts < utc_start(f.date_to + timedelta(days=1)))
    if f.module:
        stmt = stmt.where(AuditLog.module == f.module)
    if f.q:
        like = f"%{f.q}%"
        stmt = stmt.where(or_(AuditLog.ref.ilike(like), AuditLog.action.ilike(like), AuditLog.username.ilike(like),
                              AuditLog.record_id == f.q))
    if f.invoice_no:
        stmt = stmt.where(AuditLog.ref.ilike(f"%{f.invoice_no}%"))
    stmt = stmt.order_by(AuditLog.ts.desc(), AuditLog.id.desc()).limit(5000)
    rows = [dict(ts=a.ts, username=a.username, module=a.module, action=a.action,
                 record=f"{a.record_type or ''} {a.record_id or ''}".strip(), ref=a.ref, field=a.field,
                 old=a.old_value, new=a.new_value, reason=a.reason,
                 ip=" / ".join(x for x in (a.ip, a.session_ref) if x)) for a in db.scalars(stmt)]
    return ReportResult(cols, rows, None, date_basis="Action timestamp", note="Latest 5,000 entries for the filter.")


def rpt_rate_changes(db, user, f):
    cols = [Col("created", "Created", "datetime"), Col("company", "Company"), Col("cfa", "Depot"),
            Col("customer", "Customer"), Col("location", "Location"), Col("level", "Level"),
            Col("old_rate", "Previous Rate", "rate"), Col("new_rate", "Rate", "rate"), Col("change_pct", "Change %", "pct"),
            Col("effective_from", "Effective From", "date"), Col("effective_to", "Effective To", "date"),
            Col("status", "Status", "status"), Col("created_by", "Created By"), Col("approved_by", "Approved By"),
            Col("reason", "Reason")]
    Prev = aliased(FreightRate)
    stmt = select(FreightRate, Prev).outerjoin(Prev, FreightRate.previous_rate_id == Prev.id)
    stmt = apply_scope_allow_null_cfa(stmt, user, FreightRate)
    if f.date_from:
        stmt = stmt.where(or_(FreightRate.created_at >= utc_start(f.date_from),
                              FreightRate.effective_from >= f.date_from))
    if f.date_to:
        stmt = stmt.where(FreightRate.created_at < utc_start(f.date_to + timedelta(days=1)))
    if f.company_id:
        stmt = stmt.where(FreightRate.company_id == f.company_id)
    if f.cfa_id:
        stmt = stmt.where(FreightRate.cfa_id == f.cfa_id)
    if f.location_id:
        stmt = stmt.where(FreightRate.location_id == f.location_id)
    if f.customer_id:
        stmt = stmt.where(FreightRate.customer_id == f.customer_id)
    if f.status:
        stmt = stmt.where(FreightRate.status == f.status)
    rows = []
    for r, p in db.execute(stmt.order_by(FreightRate.created_at.desc(), FreightRate.id.desc())):
        rows.append(dict(created=r.created_at, company=r.company.name, cfa=r.cfa.name if r.cfa else "Any",
                         customer=r.customer.name if r.customer else "", location=r.location.name if r.location
                         else "DEFAULT", level=RateLevel.LABELS.get(r.level, r.level).split(" (")[0],
                         old_rate=p.rate_per_box if p else None, new_rate=r.rate_per_box,
                         change_pct=_ratio((r.rate_per_box - p.rate_per_box), p.rate_per_box, 100) if p else None,
                         effective_from=r.effective_from, effective_to=r.effective_to, status=r.status,
                         created_by=r.created_by.full_name if r.created_by else "",
                         approved_by=r.approved_by.full_name if r.approved_by else "",
                         reason=r.change_reason or r.remarks or r.rejected_reason))
    return ReportResult(cols, rows, None, date_basis="Created / effective date")


def rpt_overrides(db, user, f):
    cols = [Col("requested_at", "Requested", "datetime"), Col("invoice_no", "Invoice", "invoice"),
            Col("company", "Company"), Col("cfa", "Depot"), Col("customer", "Customer"), Col("field", "Field"),
            Col("old", "Old Value"), Col("new", "New Value"), Col("reason", "Reason"),
            Col("requested_by", "Requested By"), Col("status", "Status", "status"), Col("decided_by", "Decided By"),
            Col("decided_at", "Decided", "datetime"), Col("remarks", "Decision Remarks")]
    Req, Dec = aliased(User), aliased(User)
    stmt = (select(InvoiceOverride, Invoice, Company.name, CFA.name, Customer.name, Req.full_name, Dec.full_name)
            .join(Invoice, InvoiceOverride.invoice_id == Invoice.id).join(Company, Invoice.company_id == Company.id)
            .join(CFA, Invoice.cfa_id == CFA.id).join(Customer, Invoice.customer_id == Customer.id)
            .outerjoin(Req, InvoiceOverride.requested_by_id == Req.id)
            .outerjoin(Dec, InvoiceOverride.decided_by_id == Dec.id))
    stmt = apply_invoice_scope(stmt, user)
    if f.date_from:
        stmt = stmt.where(InvoiceOverride.requested_at >= utc_start(f.date_from))
    if f.date_to:
        stmt = stmt.where(InvoiceOverride.requested_at < utc_start(f.date_to + timedelta(days=1)))
    for attr, col in (("company_id", Invoice.company_id), ("cfa_id", Invoice.cfa_id), ("customer_id", Invoice.customer_id)):
        if getattr(f, attr):
            stmt = stmt.where(col == getattr(f, attr))
    if f.status:
        stmt = stmt.where(InvoiceOverride.status == f.status)
    if f.invoice_no:
        stmt = stmt.where(Invoice.invoice_no.ilike(f"%{f.invoice_no}%"))
    rows = [dict(invoice_id=inv.id, requested_at=o.requested_at, invoice_no=inv.invoice_no, company=co, cfa=cf,
                 customer=cu, field=o.field + (" (initial entry)" if o.is_initial_entry else ""), old=o.old_value,
                 new=o.new_value, reason=o.reason, requested_by=rq, status=o.status, decided_by=dc,
                 decided_at=o.decided_at, remarks=o.decision_remarks)
            for o, inv, co, cf, cu, rq, dc in db.execute(stmt.order_by(InvoiceOverride.requested_at.desc()))]
    return ReportResult(cols, rows, None, date_basis="Request date")


def rpt_reprints(db, user, f):
    cols = [Col("printed_at", "Printed", "datetime"), Col("dispatch_no", "Dispatch No"),
            Col("invoice_no", "Invoice", "invoice"), Col("company", "Company"), Col("cfa", "Depot"),
            Col("print_no", "Print #", "int"), Col("output", "Output"), Col("reason", "Reason"),
            Col("printed_by", "Printed By"), Col("ip", "IP")]
    stmt = (select(SlipPrintLog, Invoice, Company.name, CFA.name, User.full_name)
            .join(Invoice, SlipPrintLog.invoice_id == Invoice.id).join(Company, Invoice.company_id == Company.id)
            .join(CFA, Invoice.cfa_id == CFA.id).outerjoin(User, SlipPrintLog.printed_by_id == User.id)
            .where(SlipPrintLog.is_reprint.is_(True)))
    stmt = apply_invoice_scope(stmt, user)
    if f.date_from:
        stmt = stmt.where(SlipPrintLog.printed_at >= utc_start(f.date_from))
    if f.date_to:
        stmt = stmt.where(SlipPrintLog.printed_at < utc_start(f.date_to + timedelta(days=1)))
    for attr, col in (("company_id", Invoice.company_id), ("cfa_id", Invoice.cfa_id)):
        if getattr(f, attr):
            stmt = stmt.where(col == getattr(f, attr))
    if f.invoice_no:
        stmt = stmt.where(Invoice.invoice_no.ilike(f"%{f.invoice_no}%"))
    rows = [dict(invoice_id=inv.id, printed_at=p.printed_at, dispatch_no=p.dispatch_no, invoice_no=inv.invoice_no,
                 company=co, cfa=cf, print_no=p.print_no, output=p.output, reason=p.reason, printed_by=u, ip=p.ip)
            for p, inv, co, cf, u in db.execute(stmt.order_by(SlipPrintLog.printed_at.desc()))]
    return ReportResult(cols, rows, None, date_basis="Print date")


def rpt_cancelled(db, user, f):
    cols = [Col("cancelled_at", "Cancelled", "datetime"), Col("invoice_no", "Invoice", "invoice"),
            Col("invoice_date", "Inv. Date", "date"), Col("company", "Company"), Col("cfa", "Depot"),
            Col("customer", "Customer"), Col("location", "Location"), Col("boxes", "Boxes", "int", True),
            Col("freight", "Freight", "money", True), Col("dispatch_no", "Voided Slip"), Col("reason", "Reason"),
            Col("cancelled_by", "Cancelled By")]
    stmt = (select(Invoice, Company.name, CFA.name, Customer.name, Location.name, User.full_name)
            .select_from(Invoice).join(Company).join(CFA).join(Customer).join(Location, Invoice.location_id == Location.id)
            .outerjoin(User, Invoice.cancelled_by_id == User.id).where(Invoice.status == S.CANCELLED))
    stmt = apply_invoice_scope(stmt, user)
    fx = Filters(**{k: getattr(f, k) for k in Filters.KEYS})
    fx.status = S.CANCELLED
    stmt = _apply_filters(stmt, fx, Invoice.invoice_date)
    rows = []
    for inv, co, cf, cu, lo, u in db.execute(stmt.order_by(Invoice.cancelled_at.desc())):
        slip = inv.dispatch_no or db.scalar(
            select(DispatchSlip.dispatch_no).join(DispatchSlipLine, DispatchSlipLine.slip_id == DispatchSlip.id)
            .where(DispatchSlipLine.invoice_id == inv.id).order_by(DispatchSlip.id.desc()))
        rows.append(dict(invoice_id=inv.id, cancelled_at=inv.cancelled_at, invoice_no=inv.invoice_no,
                         invoice_date=inv.invoice_date, company=co, cfa=cf, customer=cu, location=lo, boxes=inv.boxes,
                         freight=inv.freight_amount, dispatch_no=slip, reason=inv.cancel_reason, cancelled_by=u))
    return ReportResult(cols, rows, _totals(cols, rows), date_basis="Invoice date")


# ----------------------------------------------------------------------------- v1.3 analysis reports
COST = func.coalesce(Invoice.freight_amount, 0) + func.coalesce(Invoice.extra_charge, 0)


def type_labels(db) -> dict:
    labels = dict(CustomerType.LABELS)
    labels.update({c: n for c, n in db.execute(select(CustomerTypeMaster.code, CustomerTypeMaster.name))})
    return labels


def rpt_customer_type(db, user, f):
    """Stockists vs retailers vs hospitals: volume, value and what it costs to serve them."""
    cols = [Col("type", "Customer type"), Col("customers", "Customers", "int", True),
            Col("invoices", "Invoices", "int", True), Col("boxes", "Cases", "int", True),
            Col("sales", "Sales value", "money", True), Col("cost", "Freight + pickup/auto", "money", True),
            Col("cost_per_case", "Cost / case", "rate"), Col("cost_pct", "Cost % of sales", "pct")]
    stmt = (select(Customer.party_type, func.count(func.distinct(Invoice.customer_id)), func.count(Invoice.id),
                   func.coalesce(func.sum(Invoice.boxes), 0), func.coalesce(func.sum(Invoice.sales_value), 0),
                   func.coalesce(func.sum(COST), 0))
            .select_from(Invoice).join(Customer, Invoice.customer_id == Customer.id))
    stmt = _apply_filters(apply_invoice_scope(stmt, user), f, Invoice.invoice_date).group_by(Customer.party_type)
    rows = []
    for t, cu, n, cases, sales, cost in db.execute(stmt):
        rows.append(dict(type=type_labels(db).get(t, t), customers=cu, invoices=n, boxes=int(cases),
                         sales=money(sales), cost=money(cost), cost_per_case=_ratio(cost, cases),
                         cost_pct=_ratio(cost, sales, 100)))
    rows.sort(key=lambda r: -r["sales"])
    return ReportResult(cols, rows, _totals(cols, rows, {"cost_per_case": lambda t: _ratio(t.get("cost"), t.get("boxes")),
                                                         "cost_pct": lambda t: _ratio(t.get("cost"), t.get("sales"), 100)}),
                        date_basis="Invoice date")


def rpt_sku(db, user, f):
    """What was dispatched: quantity and value per company and SKU (from item-wise ERP exports)."""
    cols = [Col("company", "Company"), Col("sku_code", "SKU code"), Col("sku_name", "SKU / item"),
            Col("invoices", "Invoices", "int", True), Col("customers", "Customers", "int"),
            Col("qty", "Quantity", "num", True), Col("free_qty", "Free qty", "num", True),
            Col("value", "Value", "money", True)]
    stmt = (select(Company.name, InvoiceItem.sku_code, func.max(InvoiceItem.sku_name),
                   func.count(func.distinct(Invoice.id)), func.count(func.distinct(Invoice.customer_id)),
                   func.coalesce(func.sum(InvoiceItem.qty), 0), func.coalesce(func.sum(InvoiceItem.free_qty), 0),
                   func.coalesce(func.sum(InvoiceItem.value), 0))
            .select_from(InvoiceItem).join(Invoice, InvoiceItem.invoice_id == Invoice.id).join(Company))
    stmt = _apply_filters(apply_invoice_scope(stmt, user), f, Invoice.invoice_date)
    stmt = stmt.group_by(Company.name, InvoiceItem.sku_code).order_by(Company.name, func.sum(InvoiceItem.value).desc())
    rows = [dict(company=co, sku_code=code, sku_name=name, invoices=n, customers=cu, qty=Decimal(q).normalize(),
                 free_qty=Decimal(fq).normalize(), value=money(v))
            for co, code, name, n, cu, q, fq, v in db.execute(stmt)]
    return ReportResult(cols, rows, _totals(cols, rows), date_basis="Invoice date",
                        note="SKU lines come from item-wise ERP exports (drop folder / one-click import). Invoices "
                             "captured only by QR scan have no SKU lines until that export arrives.")


def rpt_gatepass(db, user, f):
    cols = [Col("dispatch_date", "Date", "date"), Col("dispatch_no", "Gatepass"), Col("mode", "Mode"),
            Col("carrier", "Transporter / auto"), Col("invoices", "Invoices", "int", True),
            Col("cases", "Cases", "int", True), Col("freight", "Freight", "money", True),
            Col("pickup", "Direct pickup", "money", True), Col("auto", "Auto", "money", True),
            Col("total", "Total charged", "money", True), Col("paid", "Paid at dispatch", "money", True),
            Col("balance", "To be billed", "money", True), Col("lr_missing", "LR missing", "int", True),
            Col("state", "State")]
    from app.services.scope import apply_cfa_scope
    stmt = apply_cfa_scope(select(DispatchSlip), user, DispatchSlip.cfa_id).where(DispatchSlip.is_void.is_(False))
    if f.date_from:
        stmt = stmt.where(DispatchSlip.dispatch_date >= f.date_from)
    if f.date_to:
        stmt = stmt.where(DispatchSlip.dispatch_date <= f.date_to)
    if f.transporter_id:
        stmt = stmt.where(DispatchSlip.transporter_id == f.transporter_id)
    if f.cfa_id:
        stmt = stmt.where(DispatchSlip.cfa_id == f.cfa_id)
    missing = dict(db.execute(select(Invoice.dispatch_slip_id, func.count(Invoice.id)).where(
        Invoice.lr_no.is_(None), Invoice.transporter_id.isnot(None), Invoice.status != S.CANCELLED,
        Invoice.dispatch_slip_id.isnot(None)).group_by(Invoice.dispatch_slip_id)).all())
    rows = []
    for s in db.scalars(stmt.order_by(DispatchSlip.dispatch_date, DispatchSlip.id)):
        carrier = " · ".join(x for x in [s.transporter.name if s.transporter else None,
                                         s.outside_auto.name if s.outside_auto else None] if x) or "—"
        rows.append(dict(dispatch_date=s.dispatch_date, dispatch_no=s.dispatch_no, mode=s.mode_label, carrier=carrier,
                         invoices=s.invoice_count, cases=s.total_cases, freight=s.freight_total,
                         pickup=s.pickup_charge, auto=s.auto_charge, total=s.total_charge, paid=s.amount_paid,
                         balance=s.balance, lr_missing=missing.get(s.id, 0) if s.transporter_id else 0,
                         state={"DISPATCHED": "Gone out", "SLIP_PRINTED": "Printed", "SLIP_GENERATED": "Not printed"}
                         .get(s.state, s.state)))
    return ReportResult(cols, rows, _totals(cols, rows), date_basis="Dispatch date")


def rpt_mode(db, user, f):
    """How goods leave: transporter, direct pickup, outside auto, self pickup - volume and cost per case."""
    cols = [Col("mode", "Mode of dispatch"), Col("invoices", "Invoices", "int", True), Col("boxes", "Cases", "int", True),
            Col("sales", "Sales value", "money", True), Col("freight", "Freight", "money", True),
            Col("extra", "Pickup / auto", "money", True), Col("cost", "Total cost", "money", True),
            Col("cost_per_case", "Cost / case", "rate")]
    stmt = (select(Invoice.dispatch_mode, func.count(Invoice.id), func.coalesce(func.sum(Invoice.boxes), 0),
                   func.coalesce(func.sum(Invoice.sales_value), 0), func.coalesce(func.sum(Invoice.freight_amount), 0),
                   func.coalesce(func.sum(Invoice.extra_charge), 0))
            .where(Invoice.dispatch_mode.isnot(None)))
    stmt = _apply_filters(apply_invoice_scope(stmt, user), f, Invoice.dispatch_date,
                          [S.SLIP_GENERATED, S.SLIP_PRINTED, S.DISPATCHED]).group_by(Invoice.dispatch_mode)
    rows = []
    for m, n, cases, sales, fr, ex in db.execute(stmt):
        rows.append(dict(mode=DispatchMode.LABELS.get(m, m), invoices=n, boxes=int(cases), sales=money(sales),
                         freight=money(fr), extra=money(ex), cost=money(fr + ex), cost_per_case=_ratio(fr + ex, cases)))
    return ReportResult(cols, rows, _totals(cols, rows, {"cost_per_case": lambda t: _ratio(t.get("cost"), t.get("boxes"))}),
                        date_basis="Dispatch date")


def rpt_outside_auto(db, user, f):
    """v1.6 Local Auto Trips: one row per trip. Per trip = ONE amount however many invoices; the share booked on the
    invoices (for MIS) always adds up to the trip amount - nothing is counted twice."""
    cols = [Col("dispatch_date", "Date", "date"), Col("dispatch_no", "Gatepass"), Col("depot", "Depot"),
            Col("auto", "Local auto"), Col("method", "Charging"), Col("invoices", "Invoices", "int", True),
            Col("cases", "Boxes", "int", True), Col("trip_charge", "Trip / box charge (owed to the auto)", "money", True),
            Col("allocated", "Booked on the invoices (MIS)", "money", True), Col("per_box", "Cost / box", "rate"),
            Col("paid", "Paid at dispatch", "money", True), Col("state", "State")]
    from app.services.scope import apply_cfa_scope
    stmt = (select(DispatchSlip).where(DispatchSlip.is_void.is_(False), DispatchSlip.outside_auto_id.isnot(None)))
    stmt = apply_cfa_scope(stmt, user, DispatchSlip.cfa_id)
    if f.date_from:
        stmt = stmt.where(DispatchSlip.dispatch_date >= f.date_from)
    if f.date_to:
        stmt = stmt.where(DispatchSlip.dispatch_date <= f.date_to)
    if f.cfa_id:
        stmt = stmt.where(DispatchSlip.cfa_id == f.cfa_id)
    if f.auto_method:
        stmt = stmt.where(DispatchSlip.auto_method == f.auto_method)
    rows = []
    for s in db.scalars(stmt.order_by(DispatchSlip.dispatch_date, DispatchSlip.id)):
        direct = s.dispatch_mode == DispatchMode.OUTSIDE_AUTO
        owed = s.freight_total if direct else s.auto_charge
        if direct:
            allocated = sum((ln.freight for ln in s.lines), ZERO)
        else:  # goods taken to a transporter: the auto's share sits with the pickup charge in the invoices' extra
            allocated = sum((ln.extra_charge for ln in s.lines), ZERO) - (s.pickup_charge or ZERO)
        method = s.auto_method or (s.outside_auto.charge_basis if s.outside_auto else None)
        label = "Per trip" if method == "PER_TRIP" else "Per box"
        if not direct:
            label += " (to transporter)"
        rows.append(dict(dispatch_date=s.dispatch_date, dispatch_no=s.dispatch_no, depot=s.cfa.name if s.cfa else "",
                         auto=s.outside_auto.name if s.outside_auto else "", method=label, invoices=s.invoice_count,
                         cases=s.total_cases, trip_charge=money(owed), allocated=money(allocated),
                         per_box=_ratio(owed, s.total_cases),
                         paid=s.amount_paid, state={"DISPATCHED": "Gone out", "SLIP_PRINTED": "Printed",
                                                    "SLIP_GENERATED": "Not printed"}.get(s.state, s.state)))
    return ReportResult(cols, rows, _totals(cols, rows, {"per_box": lambda t: _ratio(t.get("trip_charge"), t.get("cases"))}),
                        date_basis="Dispatch date",
                        note="Per trip: the auto is owed ONE amount for the trip, however many invoices it carries "
                             "(4 invoices at ₹500 = ₹500, not ₹2,000). It is shared over the invoices by boxes for "
                             "the MIS; the two money columns are always equal.")


def rpt_transporter_area(db, user, f):
    """Cost per case by transporter and area - the basis for negotiating rates and choosing carriers."""
    from app.services import transporter_rate_service as trs
    cols = [Col("transporter", "Transporter"), Col("location", "Area"), Col("invoices", "Invoices", "int", True),
            Col("boxes", "Cases", "int", True), Col("freight", "Freight", "money", True),
            Col("avg_rate", "Average / case", "rate"), Col("card_rate", "Rate card today", "rate"),
            Col("actual", "Billed by transporter", "money", True)]
    stmt = (select(Transporter.id, Transporter.name, Location.id, Location.name, func.count(Invoice.id),
                   func.coalesce(func.sum(Invoice.boxes), 0), func.coalesce(func.sum(Invoice.freight_amount), 0),
                   func.coalesce(func.sum(Invoice.actual_freight), 0))
            .select_from(Invoice).join(Transporter, Invoice.transporter_id == Transporter.id)
            .join(Location, Invoice.location_id == Location.id))
    stmt = _apply_filters(apply_invoice_scope(stmt, user), f, Invoice.dispatch_date,
                          [S.SLIP_GENERATED, S.SLIP_PRINTED, S.DISPATCHED])
    stmt = stmt.group_by(Transporter.id, Transporter.name, Location.id, Location.name).order_by(Transporter.name, Location.name)
    today = local_today()
    rows = []
    for tid, tname, lid, lname, n, cases, fr, act in db.execute(stmt):
        lk = trs.find(db, tid, lid, today)
        rows.append(dict(transporter=tname, location=lname, invoices=n, boxes=int(cases), freight=money(fr),
                         avg_rate=_ratio(fr, cases), card_rate=lk.rate_per_case, actual=money(act) if act else None))
    return ReportResult(cols, rows, _totals(cols, rows), date_basis="Dispatch date")


def rpt_bill_irregularities(db, user, f):
    cols = [Col("bill_date", "Bill date", "date"), Col("transporter", "Transporter"), Col("bill_no", "Bill"),
            Col("lr_no", "LR"), Col("party", "Party / station"), Col("billed", "Billed", "money", True),
            Col("ours", "Our freight", "money", True), Col("result", "Result"), Col("findings", "Findings"),
            Col("bill_status", "Bill status")]
    stmt = select(TransporterBillLine, TransporterBill).join(TransporterBill)
    stmt = apply_strict_scope(stmt, user, TransporterBill)
    if f.date_from:
        stmt = stmt.where(TransporterBill.bill_date >= f.date_from)
    if f.date_to:
        stmt = stmt.where(TransporterBill.bill_date <= f.date_to)
    if f.transporter_id:
        stmt = stmt.where(TransporterBill.transporter_id == f.transporter_id)
    rows = []
    for ln, b in db.execute(stmt.order_by(TransporterBill.bill_date.desc(), TransporterBill.id, TransporterBillLine.id)):
        found = [i for i in (ln.issues or []) if i.get("severity") != "info"]
        if not found:
            continue
        rows.append(dict(bill_date=b.bill_date, transporter=b.transporter.name, bill_no=b.bill_no, lr_no=ln.lr_no,
                         party=" · ".join(x for x in (ln.party_name, ln.station) if x), billed=ln.billed_amount,
                         ours=ln.calculated_amount, result=ln.match_status.replace("_", " ").title(),
                         findings="; ".join(i["msg"] for i in found), bill_status=b.status))
    return ReportResult(cols, rows, _totals(cols, rows), date_basis="Bill date")


def _consignment_rows(db, user, f):
    """Dispatched consignments (customer on a gatepass) in the period - delivery analytics work per consignment."""
    from app.services import delivery_service as dsv
    stmt = _apply_filters(apply_invoice_scope(select(Invoice), user), f, Invoice.dispatch_date, [S.DISPATCHED])
    stmt = (stmt.where(Invoice.dispatch_slip_id.isnot(None)).order_by(Invoice.dispatch_date, Invoice.id)
            .options(selectinload(Invoice.transporter), selectinload(Invoice.location),
                     selectinload(Invoice.customer)))
    return dsv.consignments_of(list(db.scalars(stmt)))


def rpt_delivery_performance(db, user, f):
    """Transit time and delivery outcome per transporter and area - who delivers on time, where."""
    from app.services import settings_service as cfg
    days = int(cfg.get(db, "delivery.expected_days") or 4)
    cols = [Col("transporter", "Transporter / mode"), Col("location", "Area"),
            Col("consignments", "Consignments", "int", True), Col("cases", "Cases", "int", True),
            Col("delivered", "Delivered", "int", True), Col("on_time", f"On time (≤{days} d)", "int", True),
            Col("late", "Late", "int", True), Col("problems", "Short / damaged / refused", "int", True),
            Col("open", "No POD yet", "int", True), Col("overdue", "Overdue now", "int", True),
            Col("avg_days", "Average transit days", "num"), Col("on_time_pct", "On-time %", "pct")]
    today = local_today()
    acc: dict = {}
    for g in _consignment_rows(db, user, f):
        first = g[0]
        carrier = first.transporter.name if first.transporter else DispatchMode.LABELS.get(first.dispatch_mode or "", "—")
        r = acc.setdefault((carrier, first.location.name), dict(
            transporter=carrier, location=first.location.name, consignments=0, cases=0, delivered=0, on_time=0,
            late=0, problems=0, open=0, overdue=0, _days=0))
        r["consignments"] += 1
        r["cases"] += sum(i.boxes or 0 for i in g)
        if first.delivery_status:
            r["delivered"] += 1
            t = first.transit_days or 0
            r["_days"] += t
            r["on_time" if t <= days else "late"] += 1
            if first.delivery_status in ("SHORT", "DAMAGED", "REFUSED"):
                r["problems"] += 1
        else:
            r["open"] += 1
            if first.dispatch_date and (today - first.dispatch_date).days > days:
                r["overdue"] += 1
    rows = []
    for r in sorted(acc.values(), key=lambda x: (x["transporter"], x["location"])):
        d = r.pop("_days")
        r["avg_days"] = round(d / r["delivered"], 1) if r["delivered"] else None
        r["on_time_pct"] = _ratio(r["on_time"], r["delivered"], 100)
        rows.append(r)
    tot = _totals(cols, rows, {"on_time_pct": lambda t: _ratio(t.get("on_time"), t.get("delivered"), 100)})
    return ReportResult(cols, rows, tot, date_basis="Dispatch date",
                        note=f"On time = delivered within {days} day(s) of gate-out (setting delivery.expected_days). "
                             "A consignment is the invoices of one customer on one gatepass.")


def rpt_delivery_register(db, user, f):
    """Every dispatched consignment with its proof of delivery (or how long it has been on the road)."""
    cols = [Col("dispatch_date", "Dispatched", "date"), Col("dispatch_no", "Gatepass"), Col("customer", "Customer"),
            Col("location", "Area"), Col("invoices", "Invoices"), Col("cases", "Cases", "int", True),
            Col("carrier", "Transporter / mode"), Col("lr", "LR"), Col("eway", "E-way bill"),
            Col("status", "Delivery"), Col("delivered_on", "Delivered on", "date"), Col("days", "Days", "int"),
            Col("received_by", "Received by"), Col("note", "Note"), Col("source", "Recorded by")]
    today = local_today()
    rows = []
    for g in _consignment_rows(db, user, f):
        first = g[0]
        rows.append(dict(
            dispatch_date=first.dispatch_date, dispatch_no=first.dispatch_no, customer=first.customer.name,
            location=first.location.name, invoices=", ".join(i.invoice_no for i in g),
            cases=sum(i.boxes or 0 for i in g),
            carrier=first.transporter.name if first.transporter else DispatchMode.LABELS.get(first.dispatch_mode or "", ""),
            lr=first.lr_no, eway=", ".join(i.eway_bill_no for i in g if i.eway_bill_no),
            status=first.delivery_label or "In transit", delivered_on=first.delivered_on,
            days=first.transit_days if first.delivery_status else ((today - first.dispatch_date).days
                                                                   if first.dispatch_date else None),
            received_by=first.received_by, note=first.pod_note,
            source={"STAFF": "Staff", "CUSTOMER": "Customer"}.get(first.pod_source or "", "")))
    return ReportResult(cols, rows, _totals(cols, rows), date_basis="Dispatch date",
                        note="Days = transit days when delivered, otherwise days since gate-out.")


INV_F = ["date", "company", "customer", "cfa", "location", "transporter", "erp", "status", "invoice_no"]
REPORTS: dict[str, ReportDef] = {r.code: r for r in [
    ReportDef("gatepass-register", "Gatepass Register", "Operational",
              "Every gatepass: mode, carrier, invoices, cases, charges, cash paid, to be billed, LR still missing.",
              rpt_gatepass, filters=["date", "cfa", "transporter"], perm="report.view"),
    ReportDef("invoice-register", "Invoice Register (operational list)", "Operational",
              "Invoices with their dispatch status, transporter and LR - no sales value, rate or freight.",
              rpt_invoice_register, filters=INV_F, default_range="quarter", perm="report.view"),
    ReportDef("customer-type", "Customer-type-wise (Retailer / Stockist / Hospital ...)", "MIS",
              "Volume, sales and cost to serve by customer type.", rpt_customer_type, default_range="fy"),
    ReportDef("sku-dispatch", "SKU-wise Dispatch (per company)", "MIS",
              "Quantity and value dispatched per company and SKU.", rpt_sku,
              filters=["date", "company", "customer", "cfa", "location"], default_range="fy"),
    ReportDef("mode-of-dispatch", "Mode of Dispatch", "MIS",
              "Transporter / direct pickup / local auto / self pickup: volume and cost per case.", rpt_mode,
              filters=["date", "company", "customer", "cfa", "location", "transporter"], default_range="fy"),
    ReportDef("transporter-area", "Transporter × Area Cost per Case", "MIS",
              "Average cost per case by transporter and area vs the rate card - for rate negotiation and planning.",
              rpt_transporter_area, filters=["date", "company", "cfa", "location", "transporter"], default_range="fy"),
    ReportDef("outside-auto", "Local Auto Trips (per trip / per box)", "MIS",
              "Every local auto trip: charging method, invoices, boxes, the amount owed and its share on the invoices.",
              rpt_outside_auto, filters=["date", "cfa", "auto_method"], default_range="fy"),
    ReportDef("bill-irregularities", "Transporter Bill Irregularities", "Reconciliation",
              "Every billed line that did not match our gatepasses or rate card, across bills.",
              rpt_bill_irregularities, filters=["date", "transporter"], default_range="fy"),
    ReportDef("delivery-performance", "Delivery Performance (transit time)", "MIS",
              "On-time %, average transit days, late and damaged deliveries per transporter and area.",
              rpt_delivery_performance, filters=["date", "company", "customer", "cfa", "location", "transporter"],
              default_range="fy"),
    ReportDef("delivery-register", "Delivery Register (POD)", "Operational",
              "Every dispatched consignment with its proof of delivery, e-way bill and LR.", rpt_delivery_register,
              filters=["date", "company", "customer", "cfa", "location", "transporter"], default_range="month", perm="report.view"),
    ReportDef("invoice-freight", "Invoice-wise Transportation Expense", "MIS",
              "Every invoice with boxes, rate per box and calculated transportation expense.", rpt_invoice_freight),
    ReportDef("customer-freight", "Customer-wise Transportation Expense", "MIS",
              "Freight by customer within company and depot.", rpt_customer, default_range="fy"),
    ReportDef("company-freight", "Company / Principal-wise Freight", "MIS",
              "Freight, freight per box and freight % of sales by principal and depot.", rpt_company, default_range="fy"),
    ReportDef("cfa-freight", "Depot-wise Freight (Calculated vs Actual)", "MIS",
              "Dispatch count, calculated and actual (billed) freight with variance per depot (several depots may be in one city).", rpt_cfa, default_range="fy"),
    ReportDef("city-freight", "City-wise Freight (all depots of a city)", "MIS",
              "Freight by the city of the dispatching depot; Depot-wise shows each depot.", rpt_city, default_range="fy"),
    ReportDef("location-freight", "Location-wise Freight", "MIS",
              "Freight cost by delivery location, sorted by highest cost.", rpt_location, default_range="fy"),
    ReportDef("transporter", "Transporter-wise Report", "MIS",
              "Invoices, boxes, calculated vs actual bill, LR and vehicle numbers per transporter.", rpt_transporter, default_range="fy"),
    ReportDef("monthly-mis", "Monthly Transportation MIS", "MIS",
              "Month-wise invoices, dispatches, freight, freight % of sales and billed variance.", rpt_monthly,
              default_range="fy"),
    ReportDef("dispatch-register", "Daily Dispatch Register", "Operational",
              "All generated dispatch slips by dispatch date.", rpt_dispatch_register, perm="report.view"),
    ReportDef("pending-dispatch", "Pending Dispatch Report", "Operational",
              "Open invoices not yet dispatched, with ageing.", rpt_pending, default_range="quarter", perm="report.view"),
    ReportDef("freight-vs-actual", "Freight vs Actual Transporter Bill", "Reconciliation",
              "Bill-wise system freight vs actual billed amount.", rpt_freight_vs_actual,
              filters=["date", "company", "cfa", "transporter", "bill_status"], default_range="fy"),
    ReportDef("freight-variance", "Freight Variance Report", "Reconciliation",
              "Bill lines with amount variance or matching problems.", rpt_variance,
              filters=["date", "transporter", "company", "cfa", "q"], default_range="fy"),
    ReportDef("dispatch-exceptions", "Dispatch Exception Report", "Controls",
              "Invoices blocked by rate missing, boxes missing, override pending or rate conflict.", rpt_exceptions,
              filters=["date", "company", "customer", "cfa", "location", "erp", "invoice_no"], default_range="quarter", perm="report.view"),
    ReportDef("rate-missing", "Rate Missing Report", "Controls",
              "Missing Company/Depot/Location rate combinations with the invoices they block.", rpt_rate_missing,
              filters=["date", "company", "cfa", "location"], default_range="quarter", perm="report.view"),
    ReportDef("audit", "Audit Report", "Controls", "Every audited action with old/new values, user, IP and reason.",
              rpt_audit, filters=["date", "module", "q", "invoice_no"], perm="audit.view"),
    ReportDef("rate-changes", "Freight Rate Change Report", "Controls",
              "Rate versions with previous rate, change %, effective dates and approvals.", rpt_rate_changes,
              filters=["date", "company", "cfa", "customer", "location", "rate_status"], default_range="fy", perm="rate.view"),
    ReportDef("overrides", "Manual Override Report", "Controls",
              "Every manual change of boxes, rate or freight with requester, approver and reason.", rpt_overrides,
              filters=["date", "company", "cfa", "customer", "override_status", "invoice_no"], default_range="fy", perm="report.view"),
    ReportDef("reprints", "Dispatch Slip Reprint Report", "Controls", "All slip reprints with reason and user.",
              rpt_reprints, filters=["date", "company", "cfa", "invoice_no"], default_range="fy", perm="report.view"),
    ReportDef("cancelled", "Cancelled Dispatch Report", "Controls",
              "Cancelled invoices / dispatches with reason and user.", rpt_cancelled,
              filters=["date", "company", "customer", "cfa", "location", "invoice_no"], default_range="fy", perm="report.view"),
]}

REPORT_GROUPS = ["MIS", "Operational", "Reconciliation", "Controls"]


def run_report(db: Session, user: User, code: str, f: Filters) -> ReportResult:
    return REPORTS[code].run(db, user, f)
