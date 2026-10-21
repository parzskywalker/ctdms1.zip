"""Jinja2 environment, render helper, flash messages, navigation."""
from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__
from app.config import get_settings
from app.models import (
    CFA, Customer, CustomerTypeMaster, ERPSystem, InvoiceStatus, Location, RateLevel, Transporter, User,
)
from app.security.auth import csrf_token
from app.services.exporters import display
from app.services.scope import visible_cfas, visible_companies
from app.services.utils import fmt_date, fmt_dt, fmt_int, fmt_money, fmt_time, fy_start, local_today

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
env = templates.env


def _pct(v):
    if v is None or v == "":
        return "—"
    return f"{Decimal(str(v)):.2f}%"


def _rate(v):
    if v is None or v == "":
        return "—"
    return f"₹{Decimal(str(v)):,.2f}"


def _kpi_money(v):
    """Compact Indian notation for KPI tiles: ₹2.73 Cr / ₹27.40 L; smaller values in full."""
    if v is None or v == "":
        return "—"
    d = Decimal(str(v))
    a = abs(d)
    sign = "-" if d < 0 else ""
    if a >= Decimal("10000000"):
        return f"{sign}₹{a / Decimal('10000000'):.2f} Cr"
    if a >= Decimal("1000000"):
        return f"{sign}₹{a / Decimal('100000'):.2f} L"
    return fmt_money(d)


def _tojson(v):
    def default(o):
        if isinstance(o, Decimal):
            return float(o)
        if hasattr(o, "isoformat"):
            return o.isoformat()
        return str(o)
    # Safe inside <script>: escape characters that could close the tag or start HTML
    return (json.dumps(v, default=default).replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _barcode_svg(text):
    """Code 128 barcode as inline SVG (printed gatepasses are scanned at the gate)."""
    from markupsafe import Markup
    if not text:
        return ""
    try:
        from reportlab.graphics import renderSVG
        from reportlab.graphics.barcode import createBarcodeDrawing
        d = createBarcodeDrawing("Code128", value=str(text), barHeight=32, barWidth=1.0, humanReadable=False)
        svg = renderSVG.drawToString(d)
        return Markup(svg[svg.find("<svg"):])
    except Exception:  # noqa: BLE001 - the number is printed anyway
        return ""


env.filters.update(money=fmt_money, kpi_money=_kpi_money, int=fmt_int, date=fmt_date, dt=fmt_dt, time=fmt_time, pct=_pct, rate=_rate,
                   status_label=lambda s: InvoiceStatus.LABELS.get(s, (s or "").replace("_", " ").title()),
                   level_label=lambda s: RateLevel.LABELS.get(s, s or "—"), display=display, jsdata=_tojson,
                   barcode_svg=_barcode_svg)
env.globals.update(app_version=__version__, InvoiceStatus=InvoiceStatus)

NAV = [
    ("Dashboard", [("Dashboard", "/", "dashboard.view", "grid")]),
    ("Dispatch desk", [
        ("Scan & Gatepass", "/gatepass/new", "dispatch.operate", "scan"),
        ("Gatepasses", "/gatepass", "dispatch.view", "clipboard"),
        ("Gate Check", "/gatepass/gate", "dispatch.view", "shield"),
        ("LR Pending", "/gatepass/lr-pending", "dispatch.view", "list"),
        ("Track Shipment", "/track", "dispatch.view", "pin"),
        ("Deliveries Pending", "/deliveries", "dispatch.view", "check"),
        ("Customer Alerts", "/notifications", "dispatch.view", "bell"),
        ("Pending Dispatch", "/dispatch/pending", "dispatch.view", "truck"),
    ]),
    ("Operations", [
        ("Add Invoice", "/invoices/new", "invoice.import", "plus"),
        ("Invoice Import", "/imports", "invoice.import", "upload"),
        ("Invoice Register", "/invoices", "invoice.view", "list"),
        ("Dispatch Register", "/dispatch/register", "dispatch.view", "clipboard"),
        ("Freight Exceptions", "/exceptions", "invoice.view", "alert"),
        ("Approvals", "/approvals", "override.approve|rate.approve|recon.approve", "check"),
        ("Transporter Bills", "/recon", "recon.view", "scale"),
        ("Scan a Bill", "/recon/scan", "recon.edit", "scan"),
    ]),
    ("Master Settings", [
        ("Initial Master Upload", "/masters/upload", "master.edit", "upload"),
        ("Companies", "/masters/companies", "master.view", "building"),
        ("Depots", "/masters/depots", "master.view", "warehouse"),
        ("Customers", "/masters/customers", "master.view", "users"),
        ("Customer Types", "/masters/customer-types", "master.view", "users"),
        ("Areas / Locations", "/masters/locations", "master.view", "pin"),
        ("Transporters (long-distance)", "/masters/transporters", "master.view", "truck"),
        ("Transporter Rate Cards", "/rate-cards", "rate.view", "rupee"),
        ("Local Auto Transporters", "/masters/autos", "master.view", "truck"),
        ("Local Auto Box Rates", "/masters/local-auto-rates", "master.view", "rupee"),
        ("Invoice Code Mappings", "/masters/invoice-markings", "master.view", "link"),
        ("Invoice Recognition Code", "/masters/recognition", "master.view", "list"),
        ("Principal Freight Rates", "/rates", "rate.view", "rupee"),
        ("ERP Mappings", "/masters/erp-mappings", "master.view", "link"),
    ]),
    ("Reports", [
        ("Transportation MIS", "/mis", "report.view", "layers"),
        ("Gatepass Register", "/reports/gatepass-register", "report.view", "bar"),
        ("Transporter × Area", "/reports/transporter-area", "report.view", "bar"),
        ("Customer-type-wise", "/reports/customer-type", "report.view", "bar"),
        ("Local Auto Trips", "/reports/outside-auto", "report.view", "bar"),
        ("Delivery Performance", "/reports/delivery-performance", "report.view", "bar"),
        ("Delivery Register", "/reports/delivery-register", "report.view", "bar"),
        ("Bill Irregularities", "/reports/bill-irregularities", "report.view", "bar"),
        ("Company-wise", "/reports/company-freight", "report.view", "bar"),
        ("Customer-wise", "/reports/customer-freight", "report.view", "bar"),
        ("Depot-wise", "/reports/cfa-freight", "report.view", "bar"),
        ("City-wise", "/reports/city-freight", "report.view", "bar"),
        ("Location-wise", "/reports/location-freight", "report.view", "bar"),
        ("Transporter-wise", "/reports/transporter", "report.view", "bar"),
        ("Dispatch Register", "/reports/dispatch-register", "report.view", "bar"),
        ("Variance", "/reports/freight-variance", "report.view", "bar"),
        ("Audit", "/reports/audit", "audit.view", "bar"),
        ("All Reports", "/reports", "report.view", "list"),
        ("E-mailed Reports", "/report-subscriptions", "report.view", "mail"),
    ]),
    ("Administration", [
        ("Users", "/admin/users", "admin.users", "user"),
        ("Guided Training", "/admin/training", "admin.users", "help"),
        ("Roles", "/admin/roles", "admin.roles", "shield"),
        ("System Settings", "/admin/settings", "admin.settings", "cog"),
        ("Audit Log", "/reports/audit", "audit.view", "history"),
        ("Integration Settings", "/admin/integrations", "admin.integrations", "plug"),
    ]),
    ("Help", [("Guided Training / Practice", "/training", "any", "help")]),
]


def nav_for(user: User | None) -> list:
    if not user:
        return []
    out = []
    for group, items in NAV:
        vis = [(label, url, icon) for label, url, perm, icon in items
           if perm == "any" or any(user.can(p) for p in perm.split("|"))]
        if vis:
            out.append((group, vis))
    return out


def flash(request: Request, message: str, kind: str = "info") -> None:
    msgs = request.session.get("flash", [])
    msgs.append({"m": message, "k": kind})
    request.session["flash"] = msgs[-6:]


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _training_watermark() -> str | None:
    from app.training import runtime
    return runtime.WATERMARK if runtime.active() else None


templates.env.globals["training_watermark"] = _training_watermark


def _training_ctx(request: Request) -> dict | None:
    """Training Mode banner / watermark details for templates (None = live work)."""
    from app.training import runtime
    t = runtime.active()
    if not t:
        return None
    return {"banner": runtime.BANNER, "watermark": runtime.WATERMARK, "acting": t.get("as")}


def render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
    user = getattr(request.state, "user", None)
    s = get_settings()
    has_session = "session" in request.scope
    today = local_today()
    last_month_end = today.replace(day=1) - timedelta(days=1)
    nav = nav_for(user)
    path = request.url.path
    urls = [u for _, items in nav for _, u, _ in items]
    matches = [u for u in urls if (u == "/" and path == "/") or (u != "/" and (path == u or path.startswith(u + "/")))]
    base = {
        "request": request,
        "user": user,
        "nav": nav,
        "active_url": max(matches, key=len) if matches else None,  # most specific menu entry only
        "csrf": csrf_token(request) if has_session else "",
        "flashes": request.session.pop("flash", []) if has_session else [],
        "app_title": s.app_title,
        "path": request.url.path,
        "can": (lambda p: bool(user and user.can(p))),
        "today": today,
        "last_month": (last_month_end.replace(day=1), last_month_end),
        "fy_from": fy_start(today),
        "training": _training_ctx(request),
    }
    base.update(ctx or {})
    return templates.TemplateResponse(request, name, base, status_code=status_code)


def filter_options(db: Session, user: User) -> dict:
    return {
        "companies": visible_companies(db, user, active_only=False),
        "cfas": visible_cfas(db, user, active_only=False),
        "customers": list(db.scalars(select(Customer).order_by(Customer.name))),
        "locations": list(db.scalars(select(Location).order_by(Location.name))),
        "transporters": list(db.scalars(select(Transporter).order_by(Transporter.name))),
        "erps": list(db.scalars(select(ERPSystem).order_by(ERPSystem.code))),
        "statuses": InvoiceStatus.ALL,
        # v1.6: depot cities (several depots may share one city) and customer types
        "cities": sorted({c.city for c in visible_cfas(db, user, active_only=False) if c.city}),
        "ctypes": list(db.scalars(select(CustomerTypeMaster).order_by(CustomerTypeMaster.sort_order,
                                                                      CustomerTypeMaster.name))),
    }


def active_lists(db: Session) -> dict:
    return {
        "transporters": list(db.scalars(select(Transporter).where(Transporter.is_active.is_(True)).order_by(Transporter.name))),
        "cfas_all": list(db.scalars(select(CFA).order_by(CFA.name))),
    }


async def get_form(request: Request):
    """Dependency: parsed form data, so endpoints can stay synchronous (run in the thread pool)."""
    return await request.form()


def read_upload(upload) -> bytes:
    """Read an UploadFile without ever holding more than max_upload_mb (+1 byte) in memory."""
    from app.services.errors import BusinessError
    limit = get_settings().max_upload_mb * 1024 * 1024
    content = upload.file.read(limit + 1)
    if len(content) > limit:
        raise BusinessError(f"File too large (max {get_settings().max_upload_mb} MB). Split it into smaller files.")
    if not content:
        raise BusinessError("The uploaded file is empty")
    return content


def safe_path(target: str | None, default: str = "/") -> str:
    """Only allow same-site relative redirects (prevents open redirects via ?next= / back=)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return default


def back_url(request: Request, default: str = "/") -> str:
    """Same-site Referer (path + query) or the default."""
    from urllib.parse import urlparse
    ref = request.headers.get("referer")
    if not ref:
        return default
    u = urlparse(ref)
    if u.netloc and u.netloc != request.url.netloc:
        return default
    return safe_path((u.path or "/") + (f"?{u.query}" if u.query else ""), default)
