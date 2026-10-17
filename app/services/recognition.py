"""v1.6 Invoice Recognition Code - an ADDITIONAL internal reference for every invoice.

    Original ERP invoice number : 001245                        (never changed - accounting / statutory / recon)
    Invoice Recognition Code    : ABC-RET-C025-GUMA-001245      (company + customer type + customer + depot + serial)

The pattern is configurable (System Settings > Invoice Recognition Code): parts {company} {ctype} {customer}
{depot} {serial} {erp} {fy} in any order with any separators. Each part comes from the master's "recognition code"
(marking) - Company, Customer Type, Customer, Depot. When a part cannot be found the invoice keeps no code and shows
why (recognition_note); nothing is guessed and dispatch never depends on the code.

Existing company / invoice markings of the source ERPs (Invoice Code Mappings) identify the company - and optionally
the depot / customer type - of an incoming invoice from its ERP invoice number.
"""
from __future__ import annotations

import re
import string

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import (
    CFA, Company, Customer, CustomerTypeMaster, Invoice, InvoiceMarking, InvoiceStatus, User,
)
from app.services import audit
from app.services import settings_service as cfg

TOKENS = ("company", "ctype", "customer", "depot", "serial", "erp", "fy")
_CLEAN = re.compile(r"[^A-Z0-9]+")


def enabled(db: Session) -> bool:
    return bool(cfg.get(db, "invoice_code.enabled"))


def _clean(v: str | None) -> str:
    return _CLEAN.sub("", (v or "").upper())


def serial_part(invoice_no: str | None, source: str = "LAST_DIGITS", digits: int = 6) -> str | None:
    """Numeric part of an ERP invoice number: 'MB/26/001245' -> '001245' (LAST_DIGITS)."""
    no = (invoice_no or "").strip().upper()
    if not no:
        return None
    if source == "FULL":
        val = _clean(no)
    elif source == "ALL_DIGITS":
        val = "".join(ch for ch in no if ch.isdigit())
    else:
        groups = re.findall(r"\d+", no)
        val = groups[-1] if groups else ""
    if not val:
        return None
    if digits and val.isdigit() and len(val) < digits:
        val = val.zfill(digits)
    return val


def format_tokens(fmt: str) -> list[str]:
    return [name for _, name, _, _ in string.Formatter().parse(fmt or "") if name is not None]


def _ctype(db: Session, key: str | None) -> CustomerTypeMaster | None:
    if not key:
        return None
    return db.scalar(select(CustomerTypeMaster).where(CustomerTypeMaster.code == key))


def build(db: Session, inv: Invoice) -> tuple[str | None, str | None, str | None]:
    """(code, serial, note). note explains a missing code; all three None when the feature is switched off."""
    if not enabled(db):
        return None, None, None
    fmt = cfg.get(db, "invoice_code.format") or ""
    use_master = bool(cfg.get(db, "invoice_code.use_master_code"))
    serial = serial_part(inv.invoice_no, cfg.get(db, "invoice_code.serial_source") or "LAST_DIGITS",
                         int(cfg.get(db, "invoice_code.serial_digits") or 0))
    toks = format_tokens(fmt)
    unknown = [t for t in toks if t not in TOKENS]
    if not toks or unknown:
        return None, serial, (f"The code pattern has an unknown part {{{unknown[0]}}}" if unknown
                              else "The code pattern is empty (System Settings)")
    company = inv.company or (db.get(Company, inv.company_id) if inv.company_id else None)
    depot = inv.cfa or (db.get(CFA, inv.cfa_id) if inv.cfa_id else None)
    cust = inv.customer or (db.get(Customer, inv.customer_id) if inv.customer_id else None)
    missing: list[str] = []
    parts: dict[str, str] = {}

    def own(obj, label, master_name):
        if obj is None:
            missing.append(f"{label} not identified")
            return ""
        v = _clean(obj.marking) or (_clean(obj.code) if use_master else "")
        if not v:
            missing.append(f"{label} {obj.code} has no recognition code (Master Settings > {master_name})")
        return v

    if "company" in toks:
        parts["company"] = own(company, "Company", "Companies")
    if "depot" in toks:
        parts["depot"] = own(depot, "Depot", "Depots")
    if "customer" in toks:
        parts["customer"] = own(cust, "Customer", "Customers")
    if "ctype" in toks:
        ct = _ctype(db, cust.party_type if cust else None)
        if cust is None:
            missing.append("Customer not identified")
            parts["ctype"] = ""
        elif ct is None:
            missing.append(f"Customer type '{cust.party_type}' of {cust.code} is not in Master Settings > Customer Types")
            parts["ctype"] = ""
        elif not _clean(ct.marking):
            missing.append(f"Customer type {ct.name} has no recognition code (Master Settings > Customer Types)")
            parts["ctype"] = ""
        else:
            parts["ctype"] = _clean(ct.marking)
    if "serial" in toks:
        if not serial:
            missing.append(f"ERP invoice number {inv.invoice_no} has no numeric part")
        parts["serial"] = serial or ""
    if "erp" in toks:
        parts["erp"] = _clean(inv.source_erp.code) if inv.source_erp_id and inv.source_erp else ""
    if "fy" in toks:
        parts["fy"] = (inv.fiscal_year or "").replace("-", "")
    if missing:
        return None, serial, "Code not generated: " + "; ".join(dict.fromkeys(missing))
    try:
        code = fmt.format(**parts).upper()
    except (KeyError, IndexError, ValueError):
        return None, serial, "The code pattern cannot be read (System Settings > Invoice Recognition Code)"
    return code[:100], serial, None


def assign(db: Session, inv: Invoice, user: User | None = None, *, audited: bool = False) -> bool:
    """Set the code on the invoice; returns True when it changed. The ERP invoice number is not touched."""
    code, serial, note = build(db, inv)
    if not enabled(db) and inv.recognition_code:
        return False  # switched off: existing codes stay for searching, no new ones are made
    changed = (code, serial, note) != (inv.recognition_code, inv.serial_no, inv.recognition_note)
    if changed and audited:
        audit.log(db, user, "RECOGNITION_CODE", "INVOICE", record_type="Invoice", record_id=inv.id,
                  ref=inv.invoice_no, field="recognition_code", old=inv.recognition_code, new=code,
                  details={"note": note} if note else None)
    inv.recognition_code, inv.serial_no, inv.recognition_note = code, serial, note
    return changed


def regenerate(db: Session, user: User | None, *, scope: str = "open", company_id=None, cfa_id=None,
               customer_id=None, party_type=None) -> dict:
    """Re-make codes after a master or the pattern changed. scope: open (not yet dispatched) / missing / all."""
    stmt = select(Invoice)
    if scope == "open":
        stmt = stmt.where(Invoice.status.in_(InvoiceStatus.OPEN))
    elif scope == "missing":
        stmt = stmt.where(Invoice.recognition_code.is_(None), Invoice.status != InvoiceStatus.CANCELLED)
    if company_id:
        stmt = stmt.where(Invoice.company_id == company_id)
    if cfa_id:
        stmt = stmt.where(Invoice.cfa_id == cfa_id)
    if customer_id:
        stmt = stmt.where(Invoice.customer_id == customer_id)
    if party_type:
        stmt = stmt.where(Invoice.customer_id.in_(select(Customer.id).where(Customer.party_type == party_type)))
    n = changed = missing = 0
    for inv in db.scalars(stmt):
        n += 1
        if assign(db, inv, user):
            changed += 1
        if inv.recognition_code is None and inv.recognition_note:
            missing += 1
    if user is not None and changed and not (company_id or cfa_id or customer_id or party_type):
        audit.log(db, user, "RECOGNITION_REGENERATED", "INVOICE", details={"scope": scope, "checked": n,
                                                                            "changed": changed, "missing": missing})
    db.flush()
    return {"checked": n, "changed": changed, "missing": missing}


def after_master_change(db: Session, model: type, obj) -> None:
    """A recognition code or customer type of a master changed: refresh the codes of its open / code-less invoices."""
    kw = {Company: "company_id", CFA: "cfa_id", Customer: "customer_id"}
    if model in kw:
        for scope in ("open", "missing"):
            regenerate(db, None, scope=scope, **{kw[model]: obj.id})
    elif model is CustomerTypeMaster:
        for scope in ("open", "missing"):
            regenerate(db, None, scope=scope, party_type=obj.code)


# ----------------------------------------------------------------------------- existing ERP invoice markings
def find_marking(db: Session, invoice_no: str | None, erp_id: int | None) -> InvoiceMarking | None:
    """The existing company / invoice marking an ERP invoice number carries (longest match wins)."""
    if not invoice_no:
        return None
    stmt = select(InvoiceMarking).where(InvoiceMarking.is_active.is_(True))
    if erp_id:
        stmt = stmt.where(or_(InvoiceMarking.erp_system_id == erp_id, InvoiceMarking.erp_system_id.is_(None)))
    hits = [m for m in db.scalars(stmt) if m.matches(invoice_no)]
    if not hits:
        return None
    hits.sort(key=lambda m: (m.erp_system_id is not None, len(m.marking or "")), reverse=True)
    return hits[0]


def example(db: Session) -> dict:
    """A worked example for the settings screen (from a real invoice when there is one)."""
    inv = db.scalar(select(Invoice).where(Invoice.recognition_code.isnot(None)).order_by(Invoice.id.desc()).limit(1))
    fmt = cfg.get(db, "invoice_code.format")
    if inv:
        ct = _ctype(db, inv.customer.party_type)
        return {"invoice": inv, "format": fmt, "parts": [
            ("Company", inv.company.code, _clean(inv.company.marking) or _clean(inv.company.code)),
            ("Customer type", ct.name if ct else inv.customer.party_type, _clean(ct.marking) if ct else ""),
            ("Customer", inv.customer.code, _clean(inv.customer.marking) or _clean(inv.customer.code)),
            ("Depot", inv.cfa.code, _clean(inv.cfa.marking) or _clean(inv.cfa.code)),
            ("Numeric part of the ERP invoice number", inv.invoice_no, inv.serial_no)]}
    return {"invoice": None, "format": fmt, "parts": [
        ("Company", "ABC Pharma", "ABC"), ("Customer type", "Retailer", "RET"), ("Customer", "C025", "C025"),
        ("Depot", "Guma", "GUMA"), ("Numeric part of the ERP invoice number", "001245", "001245")]}
