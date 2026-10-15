"""Capture invoices by scanning the GST e-invoice QR code printed on them - no ERP access, no typing.

Every B2B e-invoice carries a QR code signed by the GST Invoice Registration Portal (IRP). It is a JWT whose payload
holds a JSON string with: SellerGstin, BuyerGstin, DocNo, DocTyp, DocDt (dd/mm/yyyy), TotInvVal, ItemCnt,
MainHsnCode, Irn, IrnDt. Whatever ERP printed the invoice, the QR is the same - so a USB 2D barcode scanner (it types
like a keyboard) or a phone camera is enough.

* Seller GSTIN -> company / principal (company master GSTIN)
* Buyer GSTIN  -> customer (customer master GSTIN; stockist, retailer or hospital)
* IRN          -> unique per invoice: scanning the same invoice twice finds it instead of creating a duplicate

The signature is not verified here (that needs the IRP's public key); the payload is trusted the same way the
printed invoice is. Scanning a plain invoice number (1D barcode) finds an invoice already imported.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CFA, Invoice, InvoiceStatus, User, utcnow
from app.services import audit
from app.services.errors import BusinessError
from app.services.import_service import company_by_gstin, customer_by_gstin
from app.services.invoice_service import apply_freight
from app.services.scope import apply_invoice_scope, can_see
from app.services.utils import fiscal_year, local_today, money, norm_code, parse_date, parse_decimal

MODULE = "INVOICE"


@dataclass
class QRData:
    seller_gstin: str
    buyer_gstin: str
    doc_no: str
    doc_type: str
    doc_date: date
    value: Decimal
    item_count: int | None
    main_hsn: str | None
    irn: str | None
    irn_date: str | None


def _b64(s: str) -> bytes:
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def decode(text: str) -> QRData | None:
    """Decode an e-invoice QR (JWT) or its JSON payload. None when the text is not an e-invoice QR."""
    t = (text or "").strip()
    if not t:
        return None
    payload = None
    if t.count(".") == 2 and not t.startswith("{"):
        try:
            payload = json.loads(_b64(t.split(".")[1]))
        except (ValueError, json.JSONDecodeError):
            return None
    elif t.startswith("{"):
        try:
            payload = json.loads(t)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data", payload)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict) or not data.get("DocNo"):
        return None
    try:
        d = parse_date(str(data.get("DocDt", "")).strip(), "%d/%m/%Y") if "/" in str(data.get("DocDt", "")) \
            else parse_date(data.get("DocDt"))
        value = parse_decimal(data.get("TotInvVal"))
    except ValueError:
        return None
    if d is None or value is None:
        return None
    return QRData(seller_gstin=norm_code(data.get("SellerGstin")).replace(" ", ""),
                  buyer_gstin=norm_code(data.get("BuyerGstin")).replace(" ", ""),
                  doc_no=str(data["DocNo"]).strip().upper(), doc_type=str(data.get("DocTyp") or "INV").upper(),
                  doc_date=d, value=money(value),
                  item_count=int(data["ItemCnt"]) if str(data.get("ItemCnt", "")).isdigit() else None,
                  main_hsn=str(data.get("MainHsnCode") or "") or None,
                  irn=(str(data.get("Irn") or "").strip().lower() or None),
                  irn_date=str(data.get("IrnDt") or "") or None)


def make_demo_qr(seller_gstin: str, buyer_gstin: str, doc_no: str, doc_date: date, value, item_count: int = 3,
                 hsn: str = "30049099") -> str:
    """A QR payload in the IRP format (unsigned) - for the demo and tests only."""
    import hashlib
    irn = hashlib.sha256(f"{seller_gstin}{doc_no}{doc_date}".encode()).hexdigest()
    data = {"SellerGstin": seller_gstin, "BuyerGstin": buyer_gstin, "DocNo": doc_no, "DocTyp": "INV",
            "DocDt": doc_date.strftime("%d/%m/%Y"), "TotInvVal": float(value), "ItemCnt": item_count,
            "MainHsnCode": hsn, "Irn": irn, "IrnDt": f"{doc_date:%Y-%m-%d} 10:15:00"}

    def enc(o) -> str:
        return base64.urlsafe_b64encode(json.dumps(o, separators=(",", ":")).encode()).decode().rstrip("=")
    return ".".join([enc({"alg": "RS256", "kid": "DEMO", "typ": "JWT"}),
                     enc({"data": json.dumps(data, separators=(",", ":")), "iss": "NIC (CTDMS demo)"}),
                     "ZGVtby1zaWduYXR1cmU"])


@dataclass
class ScanResult:
    invoice: Invoice | None
    created: bool
    message: str
    qr: QRData | None = None
    missing: str | None = None  # "customer" / "company" when the master needs the GSTIN first


def find_invoice(db: Session, user: User, text: str, cfa_id: int | None = None) -> Invoice | None:
    """Plain invoice number (or central id CI00000123) -> the one open invoice the user can see."""
    t = norm_code(text)
    if not t:
        return None
    stmt = apply_invoice_scope(select(Invoice), user)
    if t.startswith("CI") and t[2:].isdigit():
        return db.scalar(stmt.where(Invoice.id == int(t[2:])))
    stmt = stmt.where(func.upper(Invoice.invoice_no) == t, Invoice.status != InvoiceStatus.CANCELLED)
    if cfa_id:
        stmt = stmt.where(Invoice.cfa_id == cfa_id)
    rows = list(db.scalars(stmt.order_by(Invoice.invoice_date.desc()).limit(3)))
    open_rows = [r for r in rows if r.status in InvoiceStatus.PRE_SLIP]
    if len(open_rows) == 1:
        return open_rows[0]
    return rows[0] if len(rows) == 1 else None


def scan(db: Session, user: User, text: str, *, cfa_id: int | None = None, cases=None) -> ScanResult:
    """Find (or create from the QR) the invoice a scan refers to."""
    if not user.can("dispatch.operate") and not user.can("invoice.import"):
        raise BusinessError("Not authorised to capture invoices")
    qr = decode(text)
    if qr is None:
        inv = find_invoice(db, user, text, cfa_id)
        if inv:
            return ScanResult(inv, False, f"Invoice {inv.invoice_no} found")
        return ScanResult(None, False, f"'{(text or '').strip()[:40]}' is not an e-invoice QR and no open invoice "
                                       "has that number")
    if qr.doc_type not in ("INV", "BIL"):
        return ScanResult(None, False, f"This QR is a {qr.doc_type} (credit / debit note), not an invoice", qr)
    if qr.irn:
        existing = db.scalar(select(Invoice).where(Invoice.irn == qr.irn))
        if existing:
            if not can_see(user, existing.company_id, existing.cfa_id):
                raise BusinessError("This invoice belongs to a company / CFA outside your scope")
            return ScanResult(existing, False, f"Invoice {existing.invoice_no} already captured", qr)
    company = company_by_gstin(db, qr.seller_gstin)
    if not company:
        return ScanResult(None, False, f"No company / principal has GSTIN {qr.seller_gstin}. Add the GSTIN to the "
                                       "company master once, then scan again.", qr, missing="company")
    customer = customer_by_gstin(db, qr.buyer_gstin)
    if not customer:
        return ScanResult(None, False, f"No customer has GSTIN {qr.buyer_gstin} (invoice {qr.doc_no}). Add the "
                                       "customer (or their GSTIN) once, then scan again.", qr, missing="customer")
    cfa = db.get(CFA, cfa_id) if cfa_id else None
    if cfa is None:
        mine = [c for c in company.cfas if can_see(user, company.id, c.id)]
        cfa = mine[0] if len(mine) == 1 else None
    if cfa is None:
        raise BusinessError(f"Select the CFA / warehouse for {company.name} before scanning")
    if cfa not in company.cfas:
        raise BusinessError(f"CFA {cfa.code} is not mapped to company {company.code}")
    if not can_see(user, company.id, cfa.id):
        raise BusinessError(f"You are not authorised for {company.code}/{cfa.code}")
    fy = fiscal_year(qr.doc_date)
    existing = db.scalar(select(Invoice).where(Invoice.company_id == company.id, Invoice.cfa_id == cfa.id,
                                               Invoice.fiscal_year == fy, Invoice.invoice_no == qr.doc_no))
    if existing:
        if not existing.irn:
            existing.irn = qr.irn
        return ScanResult(existing, False, f"Invoice {existing.invoice_no} already imported", qr)
    if qr.doc_date > local_today():
        raise BusinessError(f"Invoice date {qr.doc_date:%d-%m-%Y} is in the future")
    if not customer.location_id:
        raise BusinessError(f"Customer {customer.name} has no delivery area - set it in the customer master")
    boxes = None
    if cases not in (None, ""):
        try:
            boxes = int(str(cases).strip())
        except ValueError as e:
            raise BusinessError("Cases must be a whole number") from e
        if boxes <= 0:
            boxes = None
    inv = Invoice(source_erp_id=company.erp_system_id, source_invoice_id=qr.irn or qr.doc_no, company_id=company.id,
                  cfa_id=cfa.id, customer_id=customer.id, customer_code=customer.code,
                  location_id=customer.location_id, invoice_no=qr.doc_no, invoice_date=qr.doc_date, fiscal_year=fy,
                  sales_value=qr.value, boxes=boxes, boxes_source="MANUAL" if boxes else "ERP",
                  imported_at=utcnow(), imported_by_id=user.id, status=InvoiceStatus.PENDING, capture_mode="QR_SCAN",
                  irn=qr.irn, item_count=qr.item_count)
    db.add(inv)
    db.flush()
    apply_freight(db, inv)
    audit.log(db, user, "INVOICE_SCANNED", MODULE, record_type="Invoice", record_id=inv.id, ref=inv.invoice_no,
              details={"irn": qr.irn, "seller": qr.seller_gstin, "buyer": qr.buyer_gstin, "value": str(qr.value),
                       "items": qr.item_count, "cases": boxes})
    return ScanResult(inv, True, f"Invoice {inv.invoice_no} captured from the e-invoice QR "
                                 f"({customer.name}, ₹{qr.value:,.2f})", qr)
