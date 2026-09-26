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
from sqlalchemy.orm import Session, aliased

from app.models import (
    CFA, AuditLog, Company, Customer, DispatchSlip, ERPSystem, FreightRate, Invoice, InvoiceOverride,
    InvoiceStatus, Location, RateLevel, SlipPrintLog, Transporter, TransporterBill, TransporterBillLine, User,
)
from app.services.scope import apply_company_scope, apply_invoice_scope
from app.services.utils import fy_start, local_today, money, parse_date, utc_start

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

    KEYS = ["date_from", "date_to", "company_id", "customer_id", "cfa_id", "location_id", "transporter_id",
            "erp_id", "status", "invoice_no", "module", "q"]

    @classmethod
    def from_params(cls, params, default_range: str = "month") -> "Filters":
        def i(k):
            v = params.get(k)
            try:
                return int(v) if v not in (None, "", "0") else None
            except ValueError:
                return None

        def d(k):
            try:
                return parse_date(params.get(k)) if params.get(k) else None
            except ValueError:
                return None

        f = cls(date_from=d("date_from"), date_to=d("date_to"), company_id=i("company_id"),
                customer_id=i("customer_id"), cfa_id=i("cfa_id"), location_id=i("location_id"),
                transporter_id=i("transporter_id"), erp_id=i("erp_id"),
                status=(params.get("status") or None), invoice_no=(params.get("invoice_no") or "").strip() or None,
                module=(params.get("module") or None), q=(params.get("q") or "").strip() or None)
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
        for k in ("status", "invoice_no", "module", "q"):
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
    filters: list[str] = field(default_factory=lambda: ["date", "company", "customer", "cfa", "location",
                                                        "transporter", "erp", "status", "invoice_no"])
    perm: str = "report.view"
    default_range: str = "month"


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
    cols = [Col("invoice_date", "Date", "date"), Col("company", "Company"), Col("cfa", "CFA"),
            Col("customer", "Customer"), Col("location", "Location"), Col("invoice_no", "Invoice No", "invoice"),
            Col("boxes", "Boxes", "int", True), Col("rate", "Rate/Box", "rate"),
            Col("freight", "Transportation Expense", "money", True), Col("sales", "Sales Value", "money", True),
            Col("status", "Dispatch Status", "status"), Col("erp", "ERP")]
    stmt = (select(Invoice.id, Invoice.invoice_date, Company.name, CFA.name, Customer.name, Location.name,
                   Invoice.invoice_no, Invoice.boxes, Invoice.rate_per_box, Invoice.freight_amount,
                   Invoice.sales_value, Invoice.status, ERPSystem.code)
            .select_from(Invoice).join(Company).join(CFA).join(Customer).join(Location, Invoice.location_id == Location.id)
            .outerjoin(ERPSystem, Invoice.source_erp_id == ERPSystem.id))
    stmt = _apply_filters(apply_invoice_scope(stmt, user), f, Invoice.invoice_date)
    stmt = stmt.order_by(Invoice.invoice_date, Invoice.invoice_no)
    rows = [dict(invoice_id=r[0], invoice_date=r[1], company=r[2], cfa=r[3], customer=r[4], location=r[5],
                 invoice_no=r[6], boxes=r[7], rate=r[8], freight=r[9], sales=r[10], status=r[11], erp=r[12])
            for r in db.execute(stmt)]
    return ReportResult(cols, rows, _totals(cols, rows, {"rate": AGG_DERIVED["rate"]}), date_basis="Invoice date")


def rpt_customer(db, user, f):
    cols = [Col("company", "Company"), Col("cfa", "CFA"), Col("customer_code", "Customer Code"),
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
    cols = [Col("company", "Company"), Col("cfa", "CFA"), Col("invoices", "Invoices", "int", True),
            Col("boxes", "Boxes", "int", True), Col("sales", "Sales", "money", True),
            Col("freight", "Transportation Expense", "money", True), Col("freight_per_box", "Freight / Box", "rate"),
            Col("freight_pct", "Freight % of Sales", "pct")]
    rows = _grouped(db, user, f, [("company", Company.name), ("cfa", CFA.name)], [Company, CFA],
                    order_by=[Company.name, CFA.name])
    return ReportResult(cols, rows, _totals(cols, rows, AGG_DERIVED), date_basis="Invoice date")


def rpt_cfa(db, user, f):
    cols = [Col("cfa", "CFA"), Col("company", "Principal"), Col("dispatched", "Dispatch Count", "int", True),
            Col("boxes", "Boxes", "int", True), Col("sales", "Sales", "money", True),
            Col("freight", "Calculated Freight", "money", True), Col("actual", "Actual Freight (billed)", "money", True),
            Col("billed_calc", "Calculated (billed inv.)", "money", True), Col("variance", "Variance", "money")]
    rows = _grouped(db, user, f, [("cfa", CFA.name), ("company", Company.name)], [Company, CFA],
                    order_by=[CFA.name, Company.name])
    return ReportResult(cols, rows, _totals(cols, rows, AGG_DERIVED), date_basis="Invoice date",
                        note="Variance = Actual billed − Calculated freight of the billed invoices (unbilled invoices excluded).")


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
            Col("variance", "Variance", "money"), Col("lr_numbers", "LR Numbers"), Col("vehicles", "Vehicle Numbers")]
    statuses = [S.SLIP_GENERATED, S.SLIP_PRINTED, S.DISPATCHED]
    rows = _grouped(db, user, f, [("transporter_id", Transporter.id), ("transporter", Transporter.name)],
                    [(Transporter, Invoice.transporter_id == Transporter.id)], order_by=[Transporter.name],
                    date_col=Invoice.dispatch_date, statuses=statuses)
    det = select(Invoice.transporter_id, Invoice.lr_no, Invoice.vehicle_no)
    det = _apply_filters(apply_invoice_scope(det, user), f, Invoice.dispatch_date, statuses)
    lrs, vehs = defaultdict(list), defaultdict(list)
    for tid, lr, veh in db.execute(det):
        if lr and lr not in lrs[tid]:
            lrs[tid].append(lr)
        if veh and veh not in vehs[tid]:
            vehs[tid].append(veh)
    for r in rows:
        r["lr_numbers"] = ", ".join(lrs[r["transporter_id"]])
        r["vehicles"] = ", ".join(vehs[r["transporter_id"]])
    return ReportResult(cols, rows, _totals(cols, rows, AGG_DERIVED), date_basis="Dispatch date")


def rpt_dispatch_register(db, user, f):
    cols = [Col("dispatch_no", "Dispatch No"), Col("invoice_no", "Invoice", "invoice"), Col("company", "Company"),
            Col("cfa", "CFA"), Col("customer", "Customer"), Col("location", "Location"),
            Col("boxes", "Boxes", "int", True), Col("freight", "Freight", "money", True),
            Col("transporter", "Transporter"), Col("vehicle", "Vehicle"), Col("lr", "LR"),
            Col("status", "Status", "status"), Col("dispatched_at", "Dispatch Date/Time")]
    stmt = (select(Invoice.id, Invoice.dispatch_no, Invoice.invoice_no, Company.name, CFA.name, Customer.name,
                   Location.name, Invoice.boxes, Invoice.freight_amount, Transporter.name, Invoice.vehicle_no,
                   Invoice.lr_no, Invoice.status, Invoice.dispatch_date, Invoice.dispatch_time)
            .select_from(Invoice).join(Company).join(CFA).join(Customer).join(Location, Invoice.location_id == Location.id)
            .outerjoin(Transporter, Invoice.transporter_id == Transporter.id)
            .where(Invoice.dispatch_no.isnot(None)))
    stmt = _apply_filters(apply_invoice_scope(stmt, user), f, Invoice.dispatch_date,
                          [S.SLIP_GENERATED, S.SLIP_PRINTED, S.DISPATCHED])
    stmt = stmt.order_by(Invoice.dispatch_date, Invoice.dispatch_time, Invoice.dispatch_no)
    rows = [dict(invoice_id=r[0], dispatch_no=r[1], invoice_no=r[2], company=r[3], cfa=r[4], customer=r[5],
                 location=r[6], boxes=r[7], freight=r[8], transporter=r[9], vehicle=r[10], lr=r[11], status=r[12],
                 dispatched_at=f"{r[13]:%d-%m-%Y} {r[14]:%H:%M}" if r[13] and r[14] else "")
            for r in db.execute(stmt)]
    return ReportResult(cols, rows, _totals(cols, rows), date_basis="Dispatch date")


def rpt_pending(db, user, f):
    cols = [Col("invoice_no", "Invoice", "invoice"), Col("invoice_date", "Inv. Date", "date"),
            Col("age", "Age (days)", "int"), Col("company", "Company"), Col("cfa", "CFA"),
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
    stmt = apply_company_scope(stmt, user, TransporterBill.company_id) if user.company_ids is not None else stmt
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
            Col("company", "Company"), Col("cfa", "CFA"), Col("period", "Billing Period"),
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
            Col("age", "Age (days)", "int"), Col("company", "Company"), Col("cfa", "CFA"), Col("customer", "Customer"),
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
    cols = [Col("company", "Company"), Col("cfa", "CFA"), Col("location", "Location"),
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
    cols = [Col("created", "Created", "datetime"), Col("company", "Company"), Col("cfa", "CFA"),
            Col("customer", "Customer"), Col("location", "Location"), Col("level", "Level"),
            Col("old_rate", "Previous Rate", "rate"), Col("new_rate", "Rate", "rate"), Col("change_pct", "Change %", "pct"),
            Col("effective_from", "Effective From", "date"), Col("effective_to", "Effective To", "date"),
            Col("status", "Status", "status"), Col("created_by", "Created By"), Col("approved_by", "Approved By"),
            Col("reason", "Reason")]
    Prev = aliased(FreightRate)
    stmt = select(FreightRate, Prev).outerjoin(Prev, FreightRate.previous_rate_id == Prev.id)
    stmt = apply_company_scope(stmt, user, FreightRate.company_id)
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
            Col("company", "Company"), Col("cfa", "CFA"), Col("customer", "Customer"), Col("field", "Field"),
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
            Col("invoice_no", "Invoice", "invoice"), Col("company", "Company"), Col("cfa", "CFA"),
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
            Col("invoice_date", "Inv. Date", "date"), Col("company", "Company"), Col("cfa", "CFA"),
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
        slip = db.scalar(select(DispatchSlip.dispatch_no).where(DispatchSlip.invoice_id == inv.id)
                         .order_by(DispatchSlip.id.desc()))
        rows.append(dict(invoice_id=inv.id, cancelled_at=inv.cancelled_at, invoice_no=inv.invoice_no,
                         invoice_date=inv.invoice_date, company=co, cfa=cf, customer=cu, location=lo, boxes=inv.boxes,
                         freight=inv.freight_amount, dispatch_no=slip, reason=inv.cancel_reason, cancelled_by=u))
    return ReportResult(cols, rows, _totals(cols, rows), date_basis="Invoice date")


INV_F = ["date", "company", "customer", "cfa", "location", "transporter", "erp", "status", "invoice_no"]
REPORTS: dict[str, ReportDef] = {r.code: r for r in [
    ReportDef("invoice-freight", "Invoice-wise Transportation Expense", "MIS",
              "Every invoice with boxes, rate per box and calculated transportation expense.", rpt_invoice_freight),
    ReportDef("customer-freight", "Customer-wise Transportation Expense", "MIS",
              "Freight by customer within company and CFA.", rpt_customer, default_range="fy"),
    ReportDef("company-freight", "Company / Principal-wise Freight", "MIS",
              "Freight, freight per box and freight % of sales by principal and CFA.", rpt_company, default_range="fy"),
    ReportDef("cfa-freight", "CFA-wise Freight (Calculated vs Actual)", "MIS",
              "Dispatch count, calculated and actual (billed) freight with variance per CFA.", rpt_cfa, default_range="fy"),
    ReportDef("location-freight", "Location-wise Freight", "MIS",
              "Freight cost by delivery location, sorted by highest cost.", rpt_location, default_range="fy"),
    ReportDef("transporter", "Transporter-wise Report", "MIS",
              "Invoices, boxes, calculated vs actual bill, LR and vehicle numbers per transporter.", rpt_transporter, default_range="fy"),
    ReportDef("monthly-mis", "Monthly Transportation MIS", "MIS",
              "Month-wise invoices, dispatches, freight, freight % of sales and billed variance.", rpt_monthly,
              default_range="fy"),
    ReportDef("dispatch-register", "Daily Dispatch Register", "Operational",
              "All generated dispatch slips by dispatch date.", rpt_dispatch_register),
    ReportDef("pending-dispatch", "Pending Dispatch Report", "Operational",
              "Open invoices not yet dispatched, with ageing.", rpt_pending, default_range="quarter"),
    ReportDef("freight-vs-actual", "Freight vs Actual Transporter Bill", "Reconciliation",
              "Bill-wise system freight vs actual billed amount.", rpt_freight_vs_actual,
              filters=["date", "company", "cfa", "transporter", "bill_status"], default_range="fy"),
    ReportDef("freight-variance", "Freight Variance Report", "Reconciliation",
              "Bill lines with amount variance or matching problems.", rpt_variance,
              filters=["date", "transporter", "company", "cfa", "q"], default_range="fy"),
    ReportDef("dispatch-exceptions", "Dispatch Exception Report", "Controls",
              "Invoices blocked by rate missing, boxes missing, override pending or rate conflict.", rpt_exceptions,
              filters=["date", "company", "customer", "cfa", "location", "erp", "invoice_no"], default_range="quarter"),
    ReportDef("rate-missing", "Rate Missing Report", "Controls",
              "Missing Company/CFA/Location rate combinations with the invoices they block.", rpt_rate_missing,
              filters=["date", "company", "cfa", "location"], default_range="quarter"),
    ReportDef("audit", "Audit Report", "Controls", "Every audited action with old/new values, user, IP and reason.",
              rpt_audit, filters=["date", "module", "q", "invoice_no"], perm="audit.view"),
    ReportDef("rate-changes", "Freight Rate Change Report", "Controls",
              "Rate versions with previous rate, change %, effective dates and approvals.", rpt_rate_changes,
              filters=["date", "company", "cfa", "customer", "location", "rate_status"], default_range="fy"),
    ReportDef("overrides", "Manual Override Report", "Controls",
              "Every manual change of boxes, rate or freight with requester, approver and reason.", rpt_overrides,
              filters=["date", "company", "cfa", "customer", "override_status", "invoice_no"], default_range="fy"),
    ReportDef("reprints", "Dispatch Slip Reprint Report", "Controls", "All slip reprints with reason and user.",
              rpt_reprints, filters=["date", "company", "cfa", "invoice_no"], default_range="fy"),
    ReportDef("cancelled", "Cancelled Dispatch Report", "Controls",
              "Cancelled invoices / dispatches with reason and user.", rpt_cancelled,
              filters=["date", "company", "customer", "cfa", "location", "invoice_no"], default_range="fy"),
]}

REPORT_GROUPS = ["MIS", "Operational", "Reconciliation", "Controls"]


def run_report(db: Session, user: User, code: str, f: Filters) -> ReportResult:
    return REPORTS[code].run(db, user, f)
