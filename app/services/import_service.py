"""Controlled invoice import pipeline (shared by Excel/CSV upload, REST push and ERP adapters).

upload -> preview -> map columns -> validate -> commit -> correct & retry rejected rows
"""
from __future__ import annotations

import csv
import logging
import difflib
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    CFA, BatchStatus, Company, Customer, DispatchMode, ERPCodeMapping, ERPSystem, ImportBatch, ImportRow,
    ImportTemplate, Invoice, InvoiceItem, InvoiceStatus, Location, RowStatus, Transporter, User, utcnow,
)
from app.services import audit, recognition
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from app.services.freight_engine import find_rate
from app.services.invoice_service import apply_freight
from app.services.scope import can_see
from app.services.utils import (
    fiscal_year, local_today, money, next_sequence, norm_code, norm_vehicle, parse_date, parse_decimal, parse_int,
)

MODULE = "IMPORT"

# key: (label, required, synonyms)
TARGET_FIELDS: dict[str, tuple[str, bool, list[str]]] = {
    "company_code": ("Company / Principal code", False,
                     ["company", "company code", "principal", "principal code", "division", "company name",
                      "comp code", "sales org", "sales organization"]),
    "cfa_code": ("Depot code", False, ["cfa", "cfa code", "depot", "depot code", "plant", "branch", "warehouse",
                                     "godown", "cfa name"]),
    "invoice_no": ("Invoice number", True, ["invoice no", "invoice number", "inv no", "bill no", "bill number",
                                            "billing document", "voucher no", "doc no", "document no",
                                            "invoice", "inv number", "vch no"]),
    "invoice_date": ("Invoice date", True, ["invoice date", "inv date", "bill date", "billing date", "date",
                                            "voucher date", "doc date", "document date", "vch date"]),
    "customer_code": ("Customer code", True, ["customer code", "party code", "cust code", "sold-to party",
                                              "sold to party", "ship-to party", "customer", "party id",
                                              "account code", "retailer code", "stockist code"]),
    "customer_name": ("Customer name", False, ["customer name", "party name", "cust name", "sold-to name",
                                               "name of party", "stockist name", "party"]),
    "location": ("Delivery location / city", False, ["location", "delivery location", "station", "city",
                                                     "ship-to city", "destination", "town", "delivery city",
                                                     "route", "place"]),
    "sales_value": ("Sales value", True, ["sales value", "net amount", "net value", "invoice value",
                                          "bill amount", "amount", "total", "invoice amount", "net amt",
                                          "grand total"]),
    "taxable_value": ("Taxable amount", False, ["taxable value", "taxable amount", "taxable amt", "assessable value",
                                                "ass value", "basic amount", "value before tax"]),
    "boxes": ("Number of boxes", False, ["boxes", "no of boxes", "no. of boxes", "cases", "no of cases",
                                         "cartons", "no. of packages", "packages", "pkgs", "box", "shippers",
                                         "cartoon", "qty boxes"]),
    "source_invoice_id": ("Source ERP invoice ID", False, ["source id", "erp id", "doc id", "invoice id",
                                                           "document id", "guid", "internal no"]),
    "transporter_code": ("Transporter (code or name)", False, ["transporter", "transporter code",
                                                               "transporter name", "carrier", "forwarding agent"]),
    "dispatch_mode": ("Mode of dispatch (planned)", False, ["mode of dispatch", "dispatch mode", "despatch mode",
                                                            "mode"]),
    "lr_no": ("LR / docket number", False, ["lr no", "lr number", "docket no", "docket", "gr no", "lr"]),
    "vehicle_no": ("Vehicle number", False, ["vehicle no", "vehicle number", "truck no", "vehicle"]),
    "remarks": ("Remarks", False, ["remarks", "narration", "note", "notes", "comment"]),
    # v1.3: party GSTIN (finds the customer when the ERP code is not mapped yet), e-invoice IRN, SKU lines
    "customer_gstin": ("Customer GSTIN", False, ["party gstin", "customer gstin", "gstin", "buyer gstin",
                                                 "gst no", "gstin/uin", "party gst no"]),
    "irn": ("E-invoice IRN", False, ["irn", "irn no", "e-invoice irn", "einvoice irn", "ack irn"]),
    "sku_code": ("SKU / item code", False, ["item code", "sku", "sku code", "product code", "material",
                                           "material code", "itemcode"]),
    "sku_name": ("SKU / item name", False, ["item name", "product name", "item description", "product",
                                           "material description", "description", "itemname"]),
    "hsn": ("HSN code", False, ["hsn", "hsn code", "hsn/sac"]),
    "batch_no": ("Batch number", False, ["batch", "batch no", "batch number"]),
    "qty": ("Quantity (units)", False, ["qty", "quantity", "billed qty", "units", "pack qty"]),
    "free_qty": ("Free quantity", False, ["free", "free qty", "scheme qty", "bonus qty"]),
    "item_value": ("Line value", False, ["item value", "line value", "line amount", "item amount", "value"]),
}
ITEM_FIELDS = ("sku_code", "sku_name", "hsn", "batch_no", "qty", "free_qty", "item_value")

REQUIRED_ALWAYS = [k for k, v in TARGET_FIELDS.items() if v[1]]

log = logging.getLogger("ctdms.import")


# ----------------------------------------------------------------------------- file reading
def read_table(content: bytes, filename: str, sheet: str | None = None, header_row: int = 1) -> tuple[list[str], list[dict]]:
    """Parse an uploaded CSV / Excel file. Every failure becomes a friendly BusinessError (never an HTTP 500)."""
    from app.config import get_settings
    max_mb = get_settings().max_upload_mb
    if len(content) > max_mb * 1024 * 1024:
        raise BusinessError(f"File too large (max {max_mb} MB). Split it into smaller files.")
    if not content:
        raise BusinessError("The uploaded file is empty")
    name = (filename or "").lower()
    try:
        return _read_table(content, name, sheet, header_row)
    except BusinessError:
        raise
    except Exception as e:  # corrupt / password-protected / mislabelled files
        kind = "Excel" if name.endswith((".xlsx", ".xlsm", ".xls")) else "CSV"
        log.warning("Could not read uploaded file %s: %s: %s", filename, type(e).__name__, e)
        raise BusinessError(f"Could not read '{filename}' as a {kind} file - it may be damaged, password-protected "
                            f"or saved in a different format. Open it in Excel and use 'Save As' "
                            f"(.xlsx or .csv), then upload again.") from e


MAX_ROWS = 100_000
PLAUSIBLE_MAX_BOXES = 2_000        # above this a row is flagged (WARNING) for a second look
PLAUSIBLE_MAX_FREIGHT_PCT = 10     # freight above 10% of sales value is almost certainly a data-entry error


def _capped(rows_iter, header_row: int) -> list:
    out = []
    for i, r in enumerate(rows_iter):
        if i >= MAX_ROWS + header_row:
            raise BusinessError(f"Too many rows; the limit is {MAX_ROWS:,} rows per file. Split the file.")
        out.append(r)
    return out


def _read_table(content: bytes, name: str, sheet: str | None, header_row: int) -> tuple[list[str], list[dict]]:
    if name.endswith(".csv") or name.endswith(".txt"):
        text = None
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = _capped(csv.reader(io.StringIO(text), dialect), header_row)
    elif name.endswith(".xlsx") or name.endswith(".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.worksheets[0]
        rows = _capped((list(r) for r in ws.iter_rows(values_only=True)), header_row)
        wb.close()
    elif name.endswith(".xls"):
        import xlrd
        book = xlrd.open_workbook(file_contents=content)
        sh = book.sheet_by_name(sheet) if sheet and sheet in book.sheet_names() else book.sheet_by_index(0)
        if sh.nrows > MAX_ROWS + header_row:
            raise BusinessError(f"Too many rows ({sh.nrows:,}); the limit is {MAX_ROWS:,} per file. Split the file.")
        rows = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    row.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode))
                else:
                    row.append(cell.value)
            rows.append(row)
    else:
        raise BusinessError("Unsupported file type. Upload .csv, .xlsx or .xls")

    rows = rows[max(header_row - 1, 0):]
    if not rows:
        raise BusinessError("The file is empty")
    headers = []
    for i, h in enumerate(rows[0]):
        h = str(h).strip() if h is not None and str(h).strip() else f"Column {i + 1}"
        base, n = h, 2
        while h in headers:
            h = f"{base} ({n})"
            n += 1
        headers.append(h)
    records = []
    for r in rows[1:]:
        if r is None or all(v is None or str(v).strip() == "" for v in r):
            continue
        rec = {}
        for i, h in enumerate(headers):
            v = r[i] if i < len(r) else None
            if hasattr(v, "isoformat"):
                v = v.isoformat()
            elif isinstance(v, float) and v.is_integer():
                v = int(v)
            elif isinstance(v, str):
                v = v.strip()
            rec[h] = v
        records.append(rec)
    return headers, records


def _norm_header(h: str) -> str:
    return " ".join(str(h).lower().replace("_", " ").replace(".", ". ").replace("  ", " ").split()).replace(". ", ".").strip()


def auto_map(headers: list[str], template: ImportTemplate | None = None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if template:
        for tgt, src in (template.column_map or {}).items():
            if src in headers:
                mapping[tgt] = src
    norm = {_norm_header(h): h for h in headers}
    used = set(mapping.values())
    for tgt, (_, _, syns) in TARGET_FIELDS.items():
        if tgt in mapping:
            continue
        for syn in syns:
            key = _norm_header(syn)
            if key in norm and norm[key] not in used:
                mapping[tgt] = norm[key]
                used.add(norm[key])
                break
    return mapping


# ----------------------------------------------------------------------------- resolver
@dataclass
class Resolver:
    db: Session
    erp_id: int | None
    cache: dict = field(default_factory=dict)

    def _mapping(self, entity: str, code: str, company_id: int | None = None) -> int | None:
        if not self.erp_id or not code:
            return None
        stmt = select(ERPCodeMapping).where(ERPCodeMapping.erp_system_id == self.erp_id,
                                            ERPCodeMapping.entity_type == entity,
                                            func.upper(ERPCodeMapping.source_code) == norm_code(code),
                                            ERPCodeMapping.is_active.is_(True))
        rows = list(self.db.scalars(stmt))
        if company_id:
            scoped = [m for m in rows if m.company_id == company_id]
            if scoped:
                return scoped[0].target_id
        glob = [m for m in rows if m.company_id is None]
        return glob[0].target_id if glob else None

    def _by_code_or_name(self, model, value):
        v = norm_code(value)
        if not v:
            return None
        obj = self.db.scalar(select(model).where(func.upper(model.code) == v))
        if obj:
            return obj
        return self.db.scalar(select(model).where(func.upper(model.name) == v))

    def company(self, code) -> Company | None:
        k = ("co", norm_code(code))
        if k not in self.cache:
            tid = self._mapping("COMPANY", code)
            self.cache[k] = self.db.get(Company, tid) if tid else self._by_code_or_name(Company, code)
        return self.cache[k]

    def cfa(self, code, company_id=None) -> CFA | None:
        k = ("cfa", norm_code(code), company_id)
        if k not in self.cache:
            tid = self._mapping("CFA", code, company_id)
            self.cache[k] = self.db.get(CFA, tid) if tid else self._by_code_or_name(CFA, code)
        return self.cache[k]

    def customer(self, code, company_id=None, gstin=None) -> Customer | None:
        k = ("cu", norm_code(code), company_id, norm_code(gstin))
        if k not in self.cache:
            tid = self._mapping("CUSTOMER", code, company_id) if code not in (None, "") else None
            obj = self.db.get(Customer, tid) if tid else None
            if not obj and code not in (None, ""):
                obj = self.db.scalar(select(Customer).where(func.upper(Customer.code) == norm_code(code)))
            if not obj and gstin:  # the same party's GSTIN is identical in every ERP
                obj = customer_by_gstin(self.db, gstin)
            hops = 0
            while obj is not None and obj.merged_into_id and hops < 5:  # follow customer merges
                obj = self.db.get(Customer, obj.merged_into_id)
                hops += 1
            self.cache[k] = obj
        return self.cache[k]

    def location(self, value, company_id=None) -> Location | None:
        k = ("lo", norm_code(value), company_id)
        if k not in self.cache:
            tid = self._mapping("LOCATION", value, company_id)
            self.cache[k] = self.db.get(Location, tid) if tid else self._by_code_or_name(Location, value)
            if not self.cache[k] and value:
                self.cache[k] = self.db.scalar(select(Location).where(func.upper(Location.city) == norm_code(value)))
        return self.cache[k]

    def transporter(self, value) -> Transporter | None:
        k = ("tr", norm_code(value))
        if k not in self.cache:
            tid = self._mapping("TRANSPORTER", value)
            self.cache[k] = self.db.get(Transporter, tid) if tid else self._by_code_or_name(Transporter, value)
        return self.cache[k]

    def customer_suggestions(self, name: str | None, code: str | None, limit: int = 3) -> list[Customer]:
        custs = list(self.db.scalars(select(Customer).where(Customer.is_active.is_(True))))
        by_name = {c.name.upper(): c for c in custs}
        out: list[Customer] = []
        if name:
            for m in difflib.get_close_matches(name.upper(), list(by_name), n=limit, cutoff=0.55):
                out.append(by_name[m])
        if code and len(out) < limit:
            by_code = {c.code.upper(): c for c in custs}
            for m in difflib.get_close_matches(code.upper(), list(by_code), n=limit, cutoff=0.7):
                if by_code[m] not in out:
                    out.append(by_code[m])
        return out[:limit]


def customer_by_gstin(db: Session, gstin) -> Customer | None:
    g = norm_code(gstin).replace(" ", "")
    if len(g) != 15:
        return None
    rows = list(db.scalars(select(Customer).where(func.upper(Customer.gstin) == g, Customer.merged_into_id.is_(None))))
    active = [c for c in rows if c.is_active] or rows
    return active[0] if len(active) == 1 else None


def company_by_gstin(db: Session, gstin) -> Company | None:
    g = norm_code(gstin).replace(" ", "")
    if len(g) != 15:
        return None
    rows = list(db.scalars(select(Company).where(func.upper(Company.gstin) == g)))
    return rows[0] if len(rows) == 1 else None


# ----------------------------------------------------------------------------- layout recognition
def match_template(db: Session, headers: list[str]) -> ImportTemplate | None:
    """Recognise an ERP export by its column headings: the saved layout whose columns are all present (and the
    most of them) wins. This is what lets a file be dropped in without choosing anything."""
    have = {_norm_header(h) for h in headers}
    best, best_score = None, 0
    for t in db.scalars(select(ImportTemplate).where(ImportTemplate.is_active.is_(True))):
        cols = [c for c in (t.column_map or {}).values() if c]
        if not cols or t.column_map.get("invoice_no") is None:
            continue
        needed = [t.column_map.get(k) for k in ("invoice_no", "invoice_date") if t.column_map.get(k)]
        if any(_norm_header(c) not in have for c in needed):
            continue
        hit = sum(1 for c in cols if _norm_header(c) in have)
        if hit < max(3, int(len(cols) * 0.7)):
            continue
        score = hit * 100 + len(cols)  # all-columns-present beats partial; larger layouts beat generic ones
        if score > best_score:
            best, best_score = t, score
    return best


# ----------------------------------------------------------------------------- batches
def _new_batch_no(db: Session) -> str:
    today = local_today()
    n = next_sequence(db, f"IMPORT:{today:%Y%m}")
    return f"IMP{today:%y%m}-{n:04d}"


def create_batch(db: Session, user: User, *, headers: list[str], records: list[dict], source: str = "FILE",
                 erp_system_id: int | None = None, company_id: int | None = None, cfa_id: int | None = None,
                 template: ImportTemplate | None = None, file_name: str | None = None,
                 file_hash: str | None = None, file_type: str | None = None, column_map: dict | None = None,
                 date_format: str = "AUTO") -> ImportBatch:
    if not records:
        raise BusinessError("No data rows found")
    batch = ImportBatch(batch_no=_new_batch_no(db), source=source, erp_system_id=erp_system_id,
                        company_id=company_id, cfa_id=cfa_id, template_id=template.id if template else None,
                        file_name=file_name, file_hash=file_hash, file_type=file_type, headers=headers,
                        date_format=(template.date_format if template else date_format) or "AUTO",
                        created_by_id=user.id if user else None, total_rows=len(records))
    batch.column_map = column_map if column_map is not None else auto_map(headers, template)
    db.add(batch)
    db.flush()
    for i, rec in enumerate(records, start=1):
        db.add(ImportRow(batch_id=batch.id, row_no=i, raw=rec, data={}))
    db.flush()
    db.refresh(batch)
    remap(db, batch)
    audit.log(db, user, "IMPORT_UPLOADED", MODULE, record_type="ImportBatch", record_id=batch.id,
              ref=batch.batch_no, details={"file": file_name, "rows": len(records), "source": source})
    return batch


def create_batch_from_file(db: Session, user: User, content: bytes, filename: str, *, erp_system_id=None,
                           company_id=None, cfa_id=None, template_id=None, sheet=None) -> tuple[ImportBatch, str | None]:
    from app.config import get_settings
    s = get_settings()
    if len(content) > s.max_upload_mb * 1024 * 1024:
        raise BusinessError(f"File too large (max {s.max_upload_mb} MB)")
    template = db.get(ImportTemplate, template_id) if template_id else None
    headers, records = read_table(content, filename, sheet or (template.sheet_name if template else None),
                                  template.header_row if template else 1)
    if template is None and template_id in (None, "", 0):
        template = match_template(db, headers)  # recognised by its column headings
    if template and not erp_system_id:
        erp_system_id = template.erp_system_id
    if template and not company_id:
        company_id = template.company_id
    if template and not cfa_id:
        cfa_id = template.cfa_id
    fhash = hashlib.sha256(content).hexdigest()
    warning = None
    prev = db.scalar(select(ImportBatch).where(ImportBatch.file_hash == fhash,
                                               ImportBatch.status.in_([BatchStatus.COMPLETED, BatchStatus.PARTIAL])))
    if prev:
        warning = f"This exact file was already imported in batch {prev.batch_no}. Duplicates will be rejected."
    batch = create_batch(db, user, headers=headers, records=records, erp_system_id=erp_system_id,
                         company_id=company_id, cfa_id=cfa_id, template=template, file_name=filename,
                         file_hash=fhash, file_type=Path(filename).suffix.lower().lstrip("."))
    try:  # keep the original file for traceability
        dest = s.upload_dir / f"{batch.batch_no}{Path(filename).suffix.lower()}"
        dest.write_bytes(content)
    except OSError:
        pass
    return batch, warning


def remap(db: Session, batch: ImportBatch) -> None:
    """Apply the batch column mapping to raw rows (keeps user corrections on corrected rows)."""
    cmap = batch.column_map or {}
    for row in batch.rows:
        if row.status in (RowStatus.IMPORTED,) or row.corrected:
            continue
        row.data = {tgt: row.raw.get(src) for tgt, src in cmap.items() if src}
        row.status = RowStatus.NEW
        row.errors = []
        row.warnings = []


def _err(code: str, msg: str, **extra) -> dict:
    return {"code": code, "msg": msg, **extra}


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def item_mode(batch: ImportBatch) -> bool:
    """Item-wise export (one row per SKU): rows of the same invoice are grouped into one invoice with SKU lines."""
    cmap = batch.column_map or {}
    return bool(cmap.get("sku_code") or cmap.get("sku_name"))


def _items_of(rows: list[ImportRow]) -> list[dict]:
    out = []
    for r in rows:
        d = r.data or {}
        if all(_blank(d.get(k)) for k in ("sku_code", "sku_name")):
            continue
        it = {"sku_code": None if _blank(d.get("sku_code")) else str(d.get("sku_code")).strip().upper()[:60],
              "sku_name": None if _blank(d.get("sku_name")) else str(d.get("sku_name")).strip()[:200],
              "hsn": None if _blank(d.get("hsn")) else str(d.get("hsn")).strip()[:12],
              "batch_no": None if _blank(d.get("batch_no")) else str(d.get("batch_no")).strip()[:40]}
        for k in ("qty", "free_qty", "item_value"):
            try:
                it[k] = parse_decimal(d.get(k)) if not _blank(d.get(k)) else None
            except ValueError:
                it[k] = None
        out.append(it)
    return out


def group_rows(batch: ImportBatch) -> list[list[ImportRow]]:
    """Rows in file order, grouped per invoice (company + CFA + invoice number as written in the file)."""
    if not item_mode(batch):
        return [[r] for r in batch.rows]
    groups: dict[tuple, list[ImportRow]] = {}
    for r in batch.rows:
        d = r.data or {}
        key = tuple(norm_code(d.get(k)) for k in ("company_code", "cfa_code", "invoice_no"))
        if not key[2]:
            key = ("__row__", r.id, "")
        groups.setdefault(key, []).append(r)
    return list(groups.values())


def _dispatch_mode(text) -> str | None:
    """'Self', 'Transporter', 'Direct', 'Outside auto' (and common spellings) -> DispatchMode."""
    t = re.sub(r"[^a-z]", "", str(text).lower())
    if t in ("self", "selfpickup", "own", "hand", "byhand", "counter"):
        return DispatchMode.SELF
    if t.startswith("direct"):
        return DispatchMode.DIRECT
    if "auto" in t:
        return DispatchMode.OUTSIDE_AUTO
    if t.startswith("transport") or t in ("courier", "road", "lorry", "truck"):
        return DispatchMode.TRANSPORTER
    return None


def validate_row(db: Session, batch: ImportBatch, row: ImportRow, res: Resolver, user: User | None,
                 seen: set, group: list[ImportRow] | None = None) -> dict | None:
    """Validate one row (or the header row of an item-wise invoice group). Returns resolved values (for commit)
    or None when rejected."""
    d = row.data or {}
    errors, warnings = [], []
    allow_blank_boxes = cfg.get(db, "import.allow_blank_boxes")
    sku_on = bool(cfg.get(db, "features.sku_lines"))  # v1.6: SKU lines postponed (rows are still grouped)
    items = _items_of(group) if group else []
    if items and _blank(d.get("sales_value")):
        line_total = sum((it["item_value"] for it in items if it["item_value"] is not None), Decimal("0"))
        if line_total:
            d = {**d, "sales_value": str(line_total)}

    def val(k):
        v = d.get(k)
        return None if v is None or (isinstance(v, str) and v.strip() == "") else v

    for k in REQUIRED_ALWAYS:
        if k == "customer_code" and val("customer_gstin") is not None:
            continue  # the party can be found by its GSTIN
        if val(k) is None:
            errors.append(_err("REQUIRED", f"{TARGET_FIELDS[k][0]} is required", field=k))
    if not allow_blank_boxes and val("boxes") is None:
        errors.append(_err("REQUIRED", "Number of boxes is required", field="boxes"))

    inv_date = sales = boxes = None
    try:
        inv_date = parse_date(val("invoice_date"), batch.date_format) if val("invoice_date") is not None else None
        if inv_date and inv_date > local_today() + timedelta(days=1):
            errors.append(_err("FUTURE_DATE", f"Invoice date {inv_date:%d-%m-%Y} is in the future", field="invoice_date"))
    except ValueError as e:
        errors.append(_err("INVALID_DATE", str(e), field="invoice_date"))
    try:
        sales = parse_decimal(val("sales_value"))
        if sales is not None and sales < 0:
            errors.append(_err("NEGATIVE", "Sales value cannot be negative", field="sales_value"))
    except ValueError as e:
        errors.append(_err("INVALID_NUMBER", str(e), field="sales_value"))
    taxable = None
    try:
        taxable = parse_decimal(val("taxable_value"))
        if taxable is not None and taxable < 0:
            errors.append(_err("NEGATIVE", "Taxable amount cannot be negative", field="taxable_value"))
        elif taxable is not None and sales is not None and taxable > sales:
            warnings.append(_err("TAXABLE_HIGH", "Taxable amount is more than the invoice amount - check the columns",
                                 field="taxable_value"))
    except ValueError as e:
        errors.append(_err("INVALID_NUMBER", str(e), field="taxable_value"))
    mode = None
    if val("dispatch_mode") is not None:
        mode = _dispatch_mode(val("dispatch_mode"))
        if mode is None:
            warnings.append(_err("MODE_UNKNOWN", f"Mode of dispatch '{val('dispatch_mode')}' not understood - ignored "
                                                 "(Self / Transporter / Direct / Outside auto)"))
    try:
        boxes = parse_int(val("boxes"))
        if boxes is not None and boxes <= 0:
            if boxes < 0:
                errors.append(_err("NEGATIVE", "Boxes cannot be negative", field="boxes"))
            boxes = None
    except ValueError as e:
        errors.append(_err("INVALID_NUMBER", str(e), field="boxes"))

    # company: the file's company column, else the existing ERP invoice marking (v1.6), else the default company
    company = None
    marking = None
    if val("company_code") is not None:
        company = res.company(val("company_code"))
        if not company:
            errors.append(_err("UNKNOWN_COMPANY", f"Company '{val('company_code')}' not found / not mapped", field="company_code"))
    else:
        marking = recognition.find_marking(db, val("invoice_no"), batch.erp_system_id)
        if marking is not None:
            company = marking.company
        elif batch.company_id:
            company = db.get(Company, batch.company_id)
        else:
            errors.append(_err("REQUIRED", "Company not in file, not recognised from an invoice marking and no default "
                                           "company selected", field="company_code"))
    if company and not company.is_active:
        errors.append(_err("INACTIVE", f"Company {company.code} is inactive", field="company_code"))

    # depot (the table is still called cfas)
    cfa = None
    if val("cfa_code") is not None:
        cfa = res.cfa(val("cfa_code"), company.id if company else None)
        if not cfa:
            errors.append(_err("UNKNOWN_CFA", f"Depot '{val('cfa_code')}' not found / not mapped", field="cfa_code"))
    elif marking is not None and marking.cfa_id:
        cfa = marking.cfa
    elif batch.cfa_id:
        cfa = db.get(CFA, batch.cfa_id)
    elif company and len(company.cfas) == 1:
        cfa = company.cfas[0]
    else:
        errors.append(_err("REQUIRED", "Depot not in file and no default depot selected", field="cfa_code"))
    if company and cfa and cfa not in company.cfas:
        errors.append(_err("CFA_NOT_MAPPED", f"Depot {cfa.code} does not handle company {company.code} "
                                             "(Master Settings > Depots > companies handled)", field="cfa_code"))
    if user and company and cfa and not can_see(user, company.id, cfa.id):
        errors.append(_err("SCOPE", f"You are not authorised to import for {company.code}/{cfa.code}"))

    # customer
    customer = None
    if val("customer_code") is not None or val("customer_gstin") is not None:
        customer = res.customer(val("customer_code"), company.id if company else None, gstin=val("customer_gstin"))
        if not customer:
            sugg = res.customer_suggestions(val("customer_name"), str(val("customer_code") or ""))
            ident = val("customer_code") or f"GSTIN {val('customer_gstin')}"
            errors.append(_err("MISSING_CUSTOMER",
                               f"Customer '{ident}' ({val('customer_name') or 'no name'}) not found",
                               field="customer_code",
                               suggestions=[{"id": c.id, "code": c.code, "name": c.name} for c in sugg]))
        elif not customer.is_active:
            errors.append(_err("INACTIVE", f"Customer {customer.code} is inactive", field="customer_code"))
        elif company and customer.companies and company not in customer.companies:
            warnings.append(_err("CUSTOMER_COMPANY", f"Customer {customer.code} is not mapped to {company.code}"))

    # location
    location = None
    if val("location") is not None:
        location = res.location(val("location"), company.id if company else None)
        if not location:
            errors.append(_err("MISSING_LOCATION", f"Delivery location '{val('location')}' not found in location master",
                               field="location"))
    elif customer:
        location = customer.location
        if not location:
            errors.append(_err("MISSING_LOCATION", f"Customer {customer.code} has no delivery location", field="location"))
    if location and not location.is_active:
        errors.append(_err("INACTIVE", f"Location {location.name} is inactive", field="location"))

    invoice_no = str(val("invoice_no")).strip().upper() if val("invoice_no") is not None else None
    fy = fiscal_year(inv_date) if inv_date else None
    attach_to = None
    if company and cfa and invoice_no and fy:
        key = (company.id, cfa.id, fy, invoice_no)
        if key in seen:
            errors.append(_err("DUPLICATE_IN_FILE", f"Invoice {invoice_no} appears more than once in this file"))
        else:
            seen.add(key)
            dup = db.scalar(select(Invoice).where(Invoice.company_id == company.id, Invoice.cfa_id == cfa.id,
                                                  Invoice.fiscal_year == fy, Invoice.invoice_no == invoice_no))
            if dup and dup.id != row.invoice_id:
                if items and sku_on and not dup.items and dup.status != InvoiceStatus.CANCELLED:
                    # scanned at the desk earlier; the item-wise export now brings its SKU lines - add them
                    attach_to = dup
                    warnings.append(_err("ITEMS_TO_EXISTING", f"Invoice {invoice_no} already exists ({dup.central_id}"
                                                              f", {dup.status_label}): {len(items)} SKU line(s) will "
                                                              "be added to it", invoice_id=dup.id))
                else:
                    errors.append(_err("DUPLICATE", f"Invoice {invoice_no} already exists ({dup.central_id}, "
                                                    f"{dup.status_label})", invoice_id=dup.id))
    irn = str(val("irn")).strip().lower() if val("irn") is not None else None
    if irn and not errors and not attach_to:
        dup = db.scalar(select(Invoice).where(Invoice.irn == irn))
        if dup:
            errors.append(_err("DUPLICATE", f"E-invoice IRN already captured as invoice {dup.invoice_no}",
                               invoice_id=dup.id))
    src_id = str(val("source_invoice_id")).strip() if val("source_invoice_id") is not None else None
    if src_id and batch.erp_system_id and not errors and not attach_to:
        dup = db.scalar(select(Invoice).where(Invoice.source_erp_id == batch.erp_system_id,
                                              Invoice.source_invoice_id == src_id))
        if dup:
            errors.append(_err("DUPLICATE", f"Source document {src_id} already imported as {dup.invoice_no}",
                               invoice_id=dup.id))

    transporter = None
    if val("transporter_code") is not None:
        transporter = res.transporter(val("transporter_code"))
        if not transporter:
            warnings.append(_err("TRANSPORTER_UNKNOWN", f"Transporter '{val('transporter_code')}' not found - ignored"))

    if not errors and not attach_to:
        if boxes is None:
            warnings.append(_err("BOXES_MISSING", "No box count - will import as EXCEPTION (enter boxes at dispatch)"))
        company_rates = cfg.get(db, "freight.source") == "COMPANY"
        lk = find_rate(db, company_id=company.id, cfa_id=cfa.id, customer_id=customer.id,
                       location_id=location.id, on_date=inv_date) if company_rates else None
        if lk is not None and not lk.found:
            warnings.append(_err(lk.code, lk.message + " - will import as RATE MISSING"))
        # plausibility (typing mistakes such as 99999 boxes): flagged for review, not rejected
        if boxes is not None and boxes > PLAUSIBLE_MAX_BOXES:
            warnings.append(_err("BOXES_HIGH", f"{boxes:,} boxes is unusually high for one invoice - please check",
                                 field="boxes"))
        if lk is not None and lk.found and boxes and sales and sales > 0:
            freight = Decimal(boxes) * lk.rate_per_box
            pct = freight * 100 / sales
            if pct > PLAUSIBLE_MAX_FREIGHT_PCT:
                warnings.append(_err("FREIGHT_HIGH", f"Freight ₹{freight:,.2f} would be {pct:.1f}% of the sales value "
                                                     f"- check boxes / sales value", field="boxes"))

    row.errors = errors
    row.warnings = warnings
    if errors:
        row.status = RowStatus.DUPLICATE if all(e["code"] in ("DUPLICATE", "DUPLICATE_IN_FILE") for e in errors) \
            else RowStatus.REJECTED
        return None
    row.status = RowStatus.WARNING if warnings else RowStatus.VALID
    return {
        "company": company, "cfa": cfa, "customer": customer, "location": location, "invoice_no": invoice_no,
        "invoice_date": inv_date, "fy": fy, "sales": sales or Decimal("0"), "boxes": boxes, "taxable": taxable,
        "mode": mode,
        "customer_code": str(val("customer_code") or customer.code), "source_invoice_id": src_id or invoice_no,
        "transporter": transporter, "lr_no": (str(val("lr_no")).strip().upper() if val("lr_no") else None),
        "vehicle_no": norm_vehicle(val("vehicle_no")), "remarks": val("remarks"), "irn": irn,
        "items": items if sku_on else [], "attach_to": attach_to, "marking": marking,
    }


def _follow(header: ImportRow, others: list[ImportRow]) -> None:
    """SKU rows after the first row of an invoice take the header's verdict (they are not separate invoices)."""
    for r in others:
        if r.status in (RowStatus.IMPORTED, RowStatus.SKIPPED):
            continue
        r.status = header.status
        r.errors = [] if header.status in (RowStatus.VALID, RowStatus.WARNING) else \
            [_err("HEADER", f"SKU line of the invoice in row {header.row_no} - see that row")]
        r.warnings = [_err("SKU_LINE", f"SKU line of the invoice in row {header.row_no}")]


def _open_rows(rows: list[ImportRow]) -> list[ImportRow]:
    return [r for r in rows if r.status not in (RowStatus.IMPORTED, RowStatus.SKIPPED)]


def validate_batch(db: Session, batch: ImportBatch, user: User | None = None) -> dict:
    res = Resolver(db, batch.erp_system_id)
    seen: set = set()
    grouped = item_mode(batch)
    # already-imported rows of this batch occupy their keys
    for grp in group_rows(batch):
        rows = [r for r in grp if r.status in (RowStatus.NEW, RowStatus.VALID, RowStatus.WARNING, RowStatus.REJECTED,
                                               RowStatus.DUPLICATE)]
        if not rows:
            continue
        validate_row(db, batch, rows[0], res, user, seen, grp if grouped else None)
        _follow(rows[0], rows[1:])
    if batch.status == BatchStatus.UPLOADED:
        batch.status = BatchStatus.VALIDATED
    db.flush()
    return summarize(batch)


def _add_items(inv: Invoice, items: list[dict]) -> None:
    start = len(inv.items)
    for n, it in enumerate(items, start=start + 1):
        inv.items.append(InvoiceItem(line_no=n, sku_code=it["sku_code"], sku_name=it["sku_name"], hsn=it["hsn"],
                                     batch_no=it["batch_no"], qty=it["qty"], free_qty=it["free_qty"],
                                     value=money(it["item_value"]) if it["item_value"] is not None else None))
    if items:
        inv.item_count = len(inv.items)


def summarize(batch: ImportBatch) -> dict:
    out = {s: 0 for s in (RowStatus.NEW, RowStatus.VALID, RowStatus.WARNING, RowStatus.IMPORTED, RowStatus.REJECTED,
                          RowStatus.DUPLICATE, RowStatus.SKIPPED)}
    for r in batch.rows:
        out[r.status] = out.get(r.status, 0) + 1
    out["total"] = len(batch.rows)
    return out


def commit_batch(db: Session, user: User, batch: ImportBatch) -> dict:
    if batch.status == BatchStatus.CANCELLED:
        raise BusinessError("Batch is cancelled")
    res = Resolver(db, batch.erp_system_id)
    seen: set = set()
    imported = exceptions = attached = 0
    grouped = item_mode(batch)
    for grp in group_rows(batch):
        rows = _open_rows(grp)
        if not rows:
            continue
        row = rows[0]
        v = validate_row(db, batch, row, res, user, seen, grp if grouped else None)
        _follow(row, rows[1:])
        if not v:
            continue
        if v["attach_to"] is not None:
            inv = v["attach_to"]
            _add_items(inv, v["items"])
            if inv.boxes is None and v["boxes"]:
                audit.log(db, user, "BOXES_ENTERED", "OVERRIDE", record_type="Invoice", record_id=inv.id,
                          ref=inv.invoice_no, field="BOXES", old=None, new=v["boxes"],
                          reason=f"From ERP export {batch.batch_no}")
                inv.boxes = v["boxes"]
                inv.boxes_source = "ERP"
                apply_freight(db, inv)
            audit.log(db, user, "INVOICE_ITEMS_ADDED", MODULE, record_type="Invoice", record_id=inv.id,
                      ref=inv.invoice_no, details={"batch": batch.batch_no, "lines": len(v["items"])})
            attached += 1
        else:
            inv = Invoice(
                source_erp_id=batch.erp_system_id, source_invoice_id=v["source_invoice_id"],
                company_id=v["company"].id, cfa_id=v["cfa"].id, customer_id=v["customer"].id,
                customer_code=v["customer_code"], location_id=v["location"].id, invoice_no=v["invoice_no"],
                invoice_date=v["invoice_date"], fiscal_year=v["fy"], sales_value=v["sales"], boxes=v["boxes"],
                taxable_value=v["taxable"], dispatch_mode=v["mode"],
                boxes_source="MANUAL" if batch.source == "MANUAL" else "ERP", remarks=v["remarks"],
                transporter_id=v["transporter"].id if v["transporter"] else None,
                lr_no=v["lr_no"], vehicle_no=v["vehicle_no"], import_batch_id=batch.id, imported_at=utcnow(),
                imported_by_id=user.id if user else None, status=InvoiceStatus.PENDING, irn=v["irn"],
                capture_mode={"API": "API", "ADAPTER": "API", "MANUAL": "MANUAL"}.get(batch.source, "IMPORT"),
                marking_id=v["marking"].id if v.get("marking") is not None else None,
            )
            db.add(inv)
            db.flush()
            recognition.assign(db, inv)
            _add_items(inv, v["items"])
            apply_freight(db, inv)
            imported += 1
            if inv.status in (InvoiceStatus.RATE_MISSING, InvoiceStatus.EXCEPTION):
                exceptions += 1
        for r in [row, *rows[1:]]:
            r.invoice_id = inv.id
            r.status = RowStatus.IMPORTED
    _recount(batch, committed=True)
    batch.committed_at = utcnow()
    db.flush()
    audit.log(db, user, "IMPORT_COMMITTED", MODULE, record_type="ImportBatch", record_id=batch.id, ref=batch.batch_no,
              details={"imported_now": imported, "exceptions_now": exceptions, "imported_total": batch.imported_count,
                       "rejected": batch.rejected_count, "duplicates": batch.duplicate_count,
                       "items_added_to_existing": attached})
    return {"imported": imported, "exceptions": exceptions, "items_added": attached, **summarize(batch)}


def _recount(batch: ImportBatch, committed: bool = False) -> None:
    s = summarize(batch)
    batch.imported_count = s[RowStatus.IMPORTED]
    batch.rejected_count = s[RowStatus.REJECTED]
    batch.duplicate_count = s[RowStatus.DUPLICATE]
    batch.exception_count = sum(1 for r in batch.rows if r.status == RowStatus.IMPORTED and r.warnings)
    open_rows = s[RowStatus.REJECTED] + s[RowStatus.DUPLICATE] + s[RowStatus.NEW] + s[RowStatus.VALID] + s[RowStatus.WARNING]
    if not committed and not batch.committed_at:
        return  # not committed yet - keep UPLOADED / VALIDATED
    if s[RowStatus.IMPORTED] == 0:
        batch.status = BatchStatus.FAILED if open_rows else BatchStatus.CANCELLED
    elif open_rows:
        batch.status = BatchStatus.PARTIAL
    else:
        batch.status = BatchStatus.COMPLETED


def correct_row(db: Session, user: User, row: ImportRow, values: dict) -> None:
    if row.status == RowStatus.IMPORTED:
        raise BusinessError("Row already imported")
    old = dict(row.data or {})
    new = dict(old)
    for k, v in values.items():
        if k in TARGET_FIELDS:
            new[k] = v if v not in ("",) else None
    changes = {k: (old.get(k), new.get(k)) for k in new if str(old.get(k)) != str(new.get(k))}
    if not changes:
        return
    row.data = new
    row.corrected = True
    row.status = RowStatus.NEW
    audit.log_changes(db, user, "IMPORT_ROW_CORRECTED", MODULE, changes, record_type="ImportRow", record_id=row.id,
                      ref=f"{row.batch.batch_no} row {row.row_no}")


def skip_row(db: Session, user: User, row: ImportRow, reason: str) -> None:
    if row.status == RowStatus.IMPORTED:
        raise BusinessError("Row already imported")
    row.status = RowStatus.SKIPPED
    audit.log(db, user, "IMPORT_ROW_SKIPPED", MODULE, record_type="ImportRow", record_id=row.id,
              ref=f"{row.batch.batch_no} row {row.row_no}", reason=reason)
    _recount(row.batch)


def find_code_mapping(db: Session, erp_system_id: int, entity_type: str, source_code: str,
                      company_id: int | None = None) -> ERPCodeMapping | None:
    return db.scalar(select(ERPCodeMapping).where(
        ERPCodeMapping.erp_system_id == erp_system_id, ERPCodeMapping.entity_type == entity_type,
        func.upper(ERPCodeMapping.source_code) == norm_code(source_code),
        ERPCodeMapping.company_id.is_(None) if company_id is None else ERPCodeMapping.company_id == company_id))


def add_code_mapping(db: Session, user: User, *, erp_system_id: int, entity_type: str, source_code: str,
                     target_id: int, source_name: str | None = None, company_id: int | None = None) -> ERPCodeMapping:
    if not erp_system_id:
        raise BusinessError("Select the source ERP for the mapping")
    existing = db.scalar(select(ERPCodeMapping).where(
        ERPCodeMapping.erp_system_id == erp_system_id, ERPCodeMapping.entity_type == entity_type,
        func.upper(ERPCodeMapping.source_code) == norm_code(source_code),
        ERPCodeMapping.company_id.is_(None) if company_id is None else ERPCodeMapping.company_id == company_id))
    if existing:
        old = existing.target_id
        existing.target_id = target_id
        existing.is_active = True
        audit.log(db, user, "ERP_MAPPING_CHANGED", "ERP_MAPPING", record_type="ERPCodeMapping", record_id=existing.id,
                  ref=f"{entity_type}:{source_code}", field="target_id", old=old, new=target_id)
        return existing
    m = ERPCodeMapping(erp_system_id=erp_system_id, entity_type=entity_type, source_code=str(source_code).strip(),
                       source_name=source_name, target_id=target_id, company_id=company_id, created_by_id=user.id)
    db.add(m)
    db.flush()
    audit.log(db, user, "ERP_MAPPING_CREATED", "ERP_MAPPING", record_type="ERPCodeMapping", record_id=m.id,
              ref=f"{entity_type}:{source_code}", field="target_id", new=target_id)
    return m


def save_template(db: Session, user: User, batch: ImportBatch, name: str) -> ImportTemplate:
    if not name or not name.strip():
        raise BusinessError("Template name required")
    t = db.scalar(select(ImportTemplate).where(ImportTemplate.name == name.strip()))
    if t is None:
        t = ImportTemplate(name=name.strip(), created_by_id=user.id)
        db.add(t)
    t.erp_system_id = batch.erp_system_id
    t.company_id = batch.company_id
    t.cfa_id = batch.cfa_id
    t.column_map = dict(batch.column_map or {})
    t.date_format = batch.date_format or "AUTO"
    db.flush()
    batch.template_id = t.id
    audit.log(db, user, "IMPORT_TEMPLATE_SAVED", MODULE, record_type="ImportTemplate", record_id=t.id, ref=t.name,
              details={"column_map": t.column_map})
    return t


def quick_import(db: Session, user: User | None, content: bytes, filename: str, *, erp_system_id=None,
                 company_id=None, cfa_id=None) -> tuple[ImportBatch, dict, str | None]:
    """One step: recognise the layout, validate, import every good row. Problem rows stay in the batch for
    correction on the import screen (nothing is guessed)."""
    batch, warning = create_batch_from_file(db, user, content, filename, erp_system_id=erp_system_id,
                                            company_id=company_id, cfa_id=cfa_id)
    if "invoice_no" not in (batch.column_map or {}):
        validate_batch(db, batch, user)
        return batch, {"imported": 0, **summarize(batch)}, (warning or "") + \
            " Layout not recognised - open the batch and map the columns once (then save it as a template)."
    validate_batch(db, batch, user)
    result = commit_batch(db, user, batch)  # user None = drop folder, audited as "system"
    return batch, result, warning


def ingest_records(db: Session, user: User | None, erp: ERPSystem | None, records: list[dict], *,
                   source: str = "API", company_id=None, cfa_id=None, commit: bool = True,
                   field_map: dict | None = None) -> tuple[ImportBatch, dict]:
    """Ingest already-structured records (REST push / adapters). Records use central field names unless a
    field_map {central_field: source_field} is supplied."""
    if field_map:
        records = [{tgt: rec.get(src) for tgt, src in field_map.items()} | {k: v for k, v in rec.items()
                   if k in TARGET_FIELDS and k not in field_map} for rec in records]
    headers = sorted({k for r in records for k in r})
    batch = create_batch(db, user, headers=headers, records=records, source=source,
                         erp_system_id=erp.id if erp else None, company_id=company_id, cfa_id=cfa_id,
                         column_map={h: h for h in headers if h in TARGET_FIELDS})
    result = commit_batch(db, user, batch) if commit else validate_batch(db, batch, user)
    return batch, result
