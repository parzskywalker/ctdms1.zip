"""Proof of delivery and shipment tracking - the last leg, the way a courier shows it.

A *consignment* is the invoices of one customer on one gatepass (one LR). After gate-out it is open until a proof of
delivery (POD) is recorded: delivered / short / damaged / refused, date, who received it, a note and optionally a
photo of the signed LR or receipt. Staff enter it from the transporter's POD copy; the customer can also confirm it
on their tracking link (setting `delivery.customer_confirm`). A customer confirmation never overwrites what staff
recorded.

Tracking links are signed with the app's secret key ({slip, customer}) and expire after `delivery.link_days`; they
show only that consignment, never prices.
"""
from __future__ import annotations

import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    DeliveryStatus, DispatchSlip, Invoice, InvoiceStatus, TransporterBill, User, utcnow,
)
from app.services import audit
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from app.services.scope import apply_invoice_scope
from app.services.utils import local_today, parse_date

MODULE = "DELIVERY"
POD_DIR = "pod"
POD_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
             ".pdf": "application/pdf"}
S = InvoiceStatus


# ----------------------------------------------------------------------------- consignments
def consignment(db: Session, inv: Invoice) -> list[Invoice]:
    """The invoices travelling together with `inv` (same gatepass, same customer)."""
    if not inv.dispatch_slip_id:
        return [inv]
    return list(db.scalars(select(Invoice).where(Invoice.dispatch_slip_id == inv.dispatch_slip_id,
                                                 Invoice.customer_id == inv.customer_id,
                                                 Invoice.status != S.CANCELLED).order_by(Invoice.id)))


def consignments_of(invs: list[Invoice]) -> list[list[Invoice]]:
    groups: OrderedDict = OrderedDict()
    for i in invs:
        if i.status != S.CANCELLED:
            groups.setdefault((i.dispatch_slip_id, i.customer_id), []).append(i)
    return list(groups.values())


def due_date(db: Session, inv: Invoice) -> date | None:
    if not inv.dispatch_date:
        return None
    return inv.dispatch_date + timedelta(days=int(cfg.get(db, "delivery.expected_days") or 4))


# ----------------------------------------------------------------------------- recording a POD
def save_pod_file(content: bytes, filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in POD_MEDIA:
        raise BusinessError("The proof of delivery must be a photo (JPG / PNG / WEBP) or a PDF")
    folder = get_settings().upload_dir / POD_DIR
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{local_today():%Y%m%d}-{uuid.uuid4().hex[:12]}{ext}"
    (folder / name).write_bytes(content)
    return name


def pod_path(name: str) -> Path | None:
    p = get_settings().upload_dir / POD_DIR / Path(name).name
    return p if p.exists() else None


def record(db: Session, user: User | None, invs: list[Invoice], *, status: str, delivered_on, received_by=None,
           note=None, pod_file: str | None = None, source: str = "STAFF", reason: str | None = None) -> int:
    """Record (or, by staff, correct) the proof of delivery of one consignment. Returns invoices changed."""
    if source == "STAFF" and (user is None or not user.can("dispatch.operate")):
        raise BusinessError("Not authorised to record deliveries")
    invs = [i for i in invs if i.status != S.CANCELLED]
    if not invs:
        raise BusinessError("No invoices selected")
    if status not in DeliveryStatus.ALL:
        raise BusinessError("Choose: delivered, short, damaged or refused")
    try:
        d = parse_date(delivered_on) if not isinstance(delivered_on, date) else delivered_on
    except ValueError as e:
        raise BusinessError(f"Delivery date: {e}") from e
    if d is None:
        raise BusinessError("Delivery date is required")
    if d > local_today():
        raise BusinessError("Delivery date cannot be in the future")
    note = (note or "").strip() or None
    received_by = (received_by or "").strip()[:120] or None
    if status in DeliveryStatus.PROBLEM and not note:
        raise BusinessError("Say what was wrong (e.g. '2 cases short', 'one case wet') - it is needed for the claim")
    for inv in invs:
        if inv.status != S.DISPATCHED:
            raise BusinessError(f"Invoice {inv.invoice_no} has not left the gate yet")
        if inv.dispatch_date and d < inv.dispatch_date:
            raise BusinessError(f"Delivery date is before the dispatch date of invoice {inv.invoice_no}")
        if inv.delivery_status and source == "CUSTOMER" and inv.pod_source == "STAFF":
            raise BusinessError("Delivery was already recorded by our staff - call us if something is wrong")
        if inv.delivery_status and source == "STAFF" and inv.pod_source == "STAFF" and \
                (not reason or len(reason.strip()) < 3) and (inv.delivery_status, inv.delivered_on) != (status, d):
            raise BusinessError(f"Invoice {inv.invoice_no} already has a delivery record; give a reason for changing it")
    n = 0
    for inv in invs:
        old = (inv.delivery_status, inv.delivered_on)
        if old == (status, d) and inv.received_by == received_by and inv.pod_note == note and not pod_file:
            continue
        audit.log(db, user, "DELIVERY_CHANGED" if old[0] else "DELIVERY_RECORDED", MODULE, record_type="Invoice",
                  record_id=inv.id, ref=inv.invoice_no, field="delivery_status", old=old[0], new=status,
                  reason=reason, details={"delivered_on": d.isoformat(), "received_by": received_by, "note": note,
                                          "source": source, "dispatch_no": inv.dispatch_no,
                                          "photo": pod_file or inv.pod_file})
        inv.delivery_status = status
        inv.delivered_on = d
        inv.received_by = received_by
        inv.pod_note = note
        if pod_file:
            inv.pod_file = pod_file
        inv.pod_source = source
        inv.pod_at = utcnow()
        inv.pod_by_id = user.id if user else None
        n += 1
    return n


# ----------------------------------------------------------------------------- tracking links
def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="ctdms-track")


def make_token(slip_id: int, customer_id: int) -> str:
    return _serializer().dumps({"s": slip_id, "c": customer_id})


def read_token(db: Session, token: str) -> tuple[DispatchSlip, list[Invoice]] | None:
    days = int(cfg.get(db, "delivery.link_days") or 60)
    try:
        data = _serializer().loads(token, max_age=days * 86400)
    except (BadSignature, SignatureExpired):
        return None
    slip = db.get(DispatchSlip, int(data.get("s") or 0))
    if slip is None or slip.is_void:
        return None
    invs = list(db.scalars(select(Invoice).where(Invoice.dispatch_slip_id == slip.id,
                                                 Invoice.customer_id == int(data.get("c") or 0),
                                                 Invoice.status != S.CANCELLED).order_by(Invoice.id)))
    return (slip, invs) if invs else None


def track_url(db: Session, slip: DispatchSlip, customer_id: int) -> str | None:
    base = (cfg.get(db, "org.public_url") or "").strip().rstrip("/")
    return f"{base}/t/{make_token(slip.id, customer_id)}" if base else None


# ----------------------------------------------------------------------------- timeline
@dataclass
class Step:
    key: str
    label: str
    when: datetime | date | None
    detail: str = ""
    done: bool = False
    problem: bool = False


def timeline(db: Session, invs: list[Invoice], *, public: bool = False) -> list[Step]:
    """Milestones of a consignment, oldest first; steps not reached yet are listed as pending."""
    first = invs[0]
    slip = db.get(DispatchSlip, first.dispatch_slip_id) if first.dispatch_slip_id else None
    how = {"QR_SCAN": "e-invoice QR scanned", "IMPORT": "from the ERP export", "API": "from the ERP",
           "MANUAL": "typed in"}.get(first.capture_mode, "")
    steps = [Step("captured", "Order received", first.imported_at,
                  f"Invoice {', '.join(i.invoice_no for i in invs)}" + ("" if public else f" · {how}"), True)]
    carrier = ""
    if slip:
        carrier = (slip.transporter.name if slip.transporter else slip.outside_auto.name if slip.outside_auto
                   else "self pickup")
    steps.append(Step("gatepass", "Packed - gatepass made", slip.generated_at if slip else None,
                      f"{slip.dispatch_no} · {sum(i.boxes or 0 for i in invs)} cases · {carrier}" if slip else "",
                      bool(slip)))
    if not public:
        printed = first.first_printed_at
        steps.append(Step("printed", "Gatepass printed", printed, f"{first.print_count or 0} print(s)",
                          bool(printed)))
    out = slip.gate_out_at if slip else None
    steps.append(Step("gate_out", "Left our warehouse", out, carrier if out else "", bool(out)))
    lr = first.lr_no
    steps.append(Step("lr", "With the transporter", first.lr_updated_at if lr else None,
                      f"LR {lr}" + (f" dated {first.lr_date:%d-%m-%Y}" if first.lr_date else "") if lr else
                      ("not needed" if slip and not slip.transporter_id else ""),
                      bool(lr) or bool(out and slip and not slip.transporter_id)))
    st = first.delivery_status
    who = f"received by {first.received_by}" if first.received_by else ""
    detail = " · ".join(x for x in [first.delivery_label or "", who, first.pod_note or ""] if x)
    due = due_date(db, first)
    if not st and due:
        detail = f"expected by {due:%d-%m-%Y}" + (" - OVERDUE" if local_today() > due and out else "")
    steps.append(Step("delivered", "Delivered", first.delivered_on, detail, bool(st),
                      problem=bool(st in DeliveryStatus.PROBLEM or (not st and due and out and local_today() > due))))
    if not public and first.transporter_id:
        bill = db.get(TransporterBill, first.transporter_bill_id) if first.transporter_bill_id else None
        steps.append(Step("billed", "Transporter bill checked", bill.created_at if bill else None,
                          f"bill {bill.bill_no} · {bill.status}" if bill else "", bool(bill)))
        steps.append(Step("paid", "Freight paid", bill.paid_at if bill and bill.paid_at else None,
                          bill.payment_ref or "" if bill and bill.paid_at else "", bool(bill and bill.paid_at)))
    return steps


def stage(invs: list[Invoice], slip: DispatchSlip | None) -> str:
    first = invs[0]
    if first.delivery_status:
        return first.delivery_label
    if slip and slip.gate_out_at:
        return "In transit"
    if slip:
        return "Ready to leave"
    return "Order received"


# ----------------------------------------------------------------------------- search & lists
def search(db: Session, user: User, q: str, limit: int = 50) -> list[Invoice]:
    """Invoice no, LR, gatepass, e-way bill or IRN -> invoices (within the user's scope)."""
    t = (q or "").strip()
    if not t:
        return []
    up = t.upper()
    stmt = apply_invoice_scope(select(Invoice), user).where(or_(
        func.upper(Invoice.invoice_no) == up, func.upper(Invoice.lr_no) == up, func.upper(Invoice.dispatch_no) == up,
        Invoice.eway_bill_no == "".join(ch for ch in t if ch.isdigit()), Invoice.irn == t.lower()))
    rows = list(db.scalars(stmt.order_by(Invoice.id.desc()).limit(limit)))
    if not rows and len(t) >= 3:
        like = f"%{t}%"
        rows = list(db.scalars(apply_invoice_scope(select(Invoice), user).where(or_(
            Invoice.invoice_no.ilike(like), Invoice.lr_no.ilike(like), Invoice.dispatch_no.ilike(like)))
            .order_by(Invoice.id.desc()).limit(limit)))
    return rows


def open_deliveries(db: Session, user: User, *, transporter_id: int | None = None, overdue_only: bool = False,
                    limit: int = 2000) -> list[dict]:
    """Consignments that left the gate and have no proof of delivery yet, oldest first."""
    stmt = apply_invoice_scope(select(Invoice), user).where(
        Invoice.status == S.DISPATCHED, Invoice.delivery_status.is_(None), Invoice.dispatch_slip_id.isnot(None))
    if transporter_id:
        stmt = stmt.where(Invoice.transporter_id == transporter_id)
    days = int(cfg.get(db, "delivery.expected_days") or 4)
    today = local_today()
    if overdue_only:
        stmt = stmt.where(Invoice.dispatch_date < today - timedelta(days=days))
    invs = list(db.scalars(stmt.order_by(Invoice.dispatch_date, Invoice.dispatch_no, Invoice.id).limit(limit)))
    rows = []
    for group in consignments_of(invs):
        f = group[0]
        age = (today - f.dispatch_date).days if f.dispatch_date else 0
        rows.append({"slip_id": f.dispatch_slip_id, "dispatch_no": f.dispatch_no, "date": f.dispatch_date, "age": age,
                     "overdue": age > days, "customer": f.customer, "location": f.location,
                     "transporter": f.transporter, "lr_no": f.lr_no, "invs": group,
                     "cases": sum(i.boxes or 0 for i in group)})
    return rows
