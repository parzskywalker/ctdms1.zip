"""Concrete ERP adapters.

Live endpoints / credentials for MARG, SAP, CBO and Intrect are NOT available yet, so these adapters are
fully wired (configuration schema, fetch + transform, error handling, sync runs) but untested against
the real systems. See docs/ERP_INTEGRATION.md for what each one needs.
"""
from __future__ import annotations

import random
import re
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx

from app.integrations.base import AdapterNotConfigured, ConfigField, ERPAdapter, _dig, register


# ----------------------------------------------------------------------------- manual file upload
@register
class ManualFileAdapter(ERPAdapter):
    key = "file"
    name = "Manual Excel / CSV upload"
    modes = ["FILE"]
    description = "Invoices are exported from the ERP and uploaded through Operations > Invoice Import."
    config_fields = []

    def test_connection(self):
        return True, "Manual upload - no connection required"

    def fetch(self, since, until):
        raise AdapterNotConfigured("This ERP uses manual file upload. Use Operations > Invoice Import.")


# ----------------------------------------------------------------------------- watched folder
@register
class FileDropAdapter(ERPAdapter):
    key = "file_drop"
    name = "Watched folder (scheduled ERP export)"
    modes = ["FILE"]
    description = ("Picks up CSV/XLSX files that the ERP (or a scheduled export job) drops into a folder, "
                   "imports them and moves them to a processed folder.")
    config_fields = [
        ConfigField("inbox_dir", "Inbox folder", True, help="Folder the ERP writes export files to"),
        ConfigField("processed_dir", "Processed folder", False, help="Default: <inbox>/processed"),
        ConfigField("pattern", "File pattern", False, default="*.csv"),
    ]

    def test_connection(self):
        missing = self.missing_config()
        if missing:
            return False, f"Not configured: {', '.join(missing)}"
        p = Path(self.cfg("inbox_dir"))
        return (p.is_dir(), f"Folder {'found' if p.is_dir() else 'NOT found'}: {p}")

    def fetch(self, since, until):
        from app.services.import_service import read_table
        self.ensure_configured()
        inbox = Path(self.cfg("inbox_dir"))
        done = Path(self.cfg("processed_dir") or inbox / "processed")
        done.mkdir(parents=True, exist_ok=True)
        records = []
        for f in sorted(inbox.glob(self.cfg("pattern", "*.csv"))):
            _, rows = read_table(f.read_bytes(), f.name)
            records.extend(rows)
            shutil.move(str(f), done / f"{datetime.now():%Y%m%d%H%M%S}_{f.name}")
        return records


# ----------------------------------------------------------------------------- generic REST
@register
class GenericRESTAdapter(ERPAdapter):
    key = "rest"
    name = "Generic REST / JSON API"
    modes = ["API"]
    description = "Pulls invoices from any JSON REST endpoint with date-range parameters and pagination."
    config_fields = [
        ConfigField("base_url", "Base URL", True, help="e.g. https://erp.example.com/api"),
        ConfigField("endpoint", "Invoice endpoint", True, default="/invoices"),
        ConfigField("auth_type", "Auth type", False, default="bearer", help="none / bearer / basic / api_key"),
        ConfigField("token", "Bearer token", False, secret=True),
        ConfigField("username", "Username", False),
        ConfigField("password", "Password", False, secret=True),
        ConfigField("api_key_header", "API key header", False, default="X-API-Key"),
        ConfigField("api_key", "API key", False, secret=True),
        ConfigField("from_param", "From-date parameter", False, default="from_date"),
        ConfigField("to_param", "To-date parameter", False, default="to_date"),
        ConfigField("date_format", "Date parameter format", False, default="%Y-%m-%d"),
        ConfigField("records_path", "Records path in response", False, default="data",
                    help="Dot path to the list of invoices in the JSON response"),
        ConfigField("page_param", "Page parameter", False, default="page"),
        ConfigField("max_pages", "Max pages per run", False, default="50"),
        ConfigField("timeout", "Timeout (seconds)", False, default="30"),
        ConfigField("verify_tls", "Verify TLS", False, default="true"),
    ]

    def _client(self) -> httpx.Client:
        headers = {"Accept": "application/json"}
        auth = None
        t = (self.cfg("auth_type") or "none").lower()
        if t == "bearer" and self.cfg("token"):
            headers["Authorization"] = f"Bearer {self.cfg('token')}"
        elif t == "basic":
            auth = (self.cfg("username") or "", self.cfg("password") or "")
        elif t == "api_key" and self.cfg("api_key"):
            headers[self.cfg("api_key_header")] = self.cfg("api_key")
        return httpx.Client(base_url=self.cfg("base_url"), headers=headers, auth=auth,
                            timeout=float(self.cfg("timeout")), verify=str(self.cfg("verify_tls")).lower() == "true")

    def test_connection(self):
        missing = self.missing_config()
        if missing:
            return False, f"Not configured: {', '.join(missing)}"
        try:
            with self._client() as c:
                r = c.get(self.cfg("endpoint"), params={self.cfg("page_param"): 1})
            return r.status_code < 400, f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            return False, f"Connection failed: {e}"

    def fetch(self, since, until):
        self.ensure_configured()
        fmt = self.cfg("date_format")
        params = {}
        if since:
            params[self.cfg("from_param")] = since.strftime(fmt)
        if until:
            params[self.cfg("to_param")] = until.strftime(fmt)
        out: list[dict] = []
        with self._client() as c:
            for page in range(1, int(self.cfg("max_pages")) + 1):
                params[self.cfg("page_param")] = page
                r = c.get(self.cfg("endpoint"), params=params)
                r.raise_for_status()
                body = r.json()
                recs = _dig(body, self.cfg("records_path")) if self.cfg("records_path") else body
                if not recs:
                    break
                out.extend(recs)
                if len(recs) < 1 or (isinstance(body, dict) and not body.get("next") and not body.get("has_more")
                                     and "next" in body):
                    break
        return out


# ----------------------------------------------------------------------------- generic database
@register
class GenericDBAdapter(ERPAdapter):
    key = "database"
    name = "Direct database query (read-only)"
    modes = ["DB"]
    description = ("Runs a read-only SQL query against the ERP database (SQL Server, MySQL, PostgreSQL, "
                   "Oracle via SQLAlchemy URL / ODBC). Use a read-only DB user.")
    config_fields = [
        ConfigField("dsn", "SQLAlchemy connection URL", True, secret=True,
                    help="e.g. mssql+pyodbc://readonly:***@server/MARGDB?driver=ODBC+Driver+18+for+SQL+Server"),
        ConfigField("query", "Invoice query", True,
                    help="SELECT ... WHERE bill_date BETWEEN :since AND :until  (column aliases = central fields)"),
    ]

    def test_connection(self):
        missing = self.missing_config()
        if missing:
            return False, f"Not configured: {', '.join(missing)}"
        from sqlalchemy import create_engine, text
        try:
            eng = create_engine(self.cfg("dsn"))
            with eng.connect() as c:
                c.execute(text("SELECT 1"))
            return True, "Connected"
        except Exception as e:  # noqa: BLE001
            return False, f"Connection failed: {e}"

    def fetch(self, since, until):
        self.ensure_configured()
        from sqlalchemy import create_engine, text
        eng = create_engine(self.cfg("dsn"))
        since = since or datetime.now() - timedelta(days=1)
        until = until or datetime.now()
        with eng.connect() as c:
            rows = c.execute(text(self.cfg("query")), {"since": since, "until": until}).mappings().all()
        return [dict(r) for r in rows]


# ----------------------------------------------------------------------------- SAP
def _odata_date(v):
    if isinstance(v, str):
        m = re.match(r"/Date\((-?\d+)", v)
        if m:
            return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).date().isoformat()
    return v


@register
class SAPODataAdapter(GenericRESTAdapter):
    key = "sap_odata"
    name = "SAP S/4HANA OData (API_BILLING_DOCUMENT_SRV)"
    modes = ["API", "ODATA"]
    description = ("Reads billing documents via the standard SAP OData service. Box count normally comes from "
                   "the outbound delivery (handling units) - map it via a custom CDS view or supply it in the "
                   "dispatch screen.")
    config_fields = [
        ConfigField("base_url", "Service root URL", True,
                    help="https://<host>/sap/opu/odata/sap/API_BILLING_DOCUMENT_SRV"),
        ConfigField("entity_set", "Entity set", False, default="A_BillingDocument"),
        ConfigField("username", "Communication user", True),
        ConfigField("password", "Password", True, secret=True),
        ConfigField("sap_client", "SAP client (sap-client)", False, default="100"),
        ConfigField("sales_org_filter", "Sales organisation filter", False),
        ConfigField("default_cfa", "Default CFA code for this SAP plant", False),
        ConfigField("page_size", "Page size", False, default="500"),
        ConfigField("timeout", "Timeout (seconds)", False, default="60"),
        ConfigField("verify_tls", "Verify TLS", False, default="true"),
    ]
    default_field_map = {
        "invoice_no": "BillingDocument", "invoice_date": "BillingDocumentDate", "customer_code": "SoldToParty",
        "sales_value": "TotalNetAmount", "company_code": "SalesOrganization", "source_invoice_id": "BillingDocument",
    }

    def _client(self):
        return httpx.Client(base_url=self.cfg("base_url"), auth=(self.cfg("username"), self.cfg("password")),
                            headers={"Accept": "application/json"}, timeout=float(self.cfg("timeout")),
                            verify=str(self.cfg("verify_tls")).lower() == "true")

    def test_connection(self):
        missing = self.missing_config()
        if missing:
            return False, f"Not configured: {', '.join(missing)}"
        try:
            with self._client() as c:
                r = c.get("/$metadata", params={"sap-client": self.cfg("sap_client")})
            return r.status_code < 400, f"HTTP {r.status_code} ($metadata)"
        except httpx.HTTPError as e:
            return False, f"Connection failed: {e}"

    def fetch(self, since, until):
        self.ensure_configured()
        since = since or datetime.now() - timedelta(days=1)
        flt = [f"BillingDocumentDate ge datetime'{since:%Y-%m-%dT00:00:00}'"]
        if until:
            flt.append(f"BillingDocumentDate le datetime'{until:%Y-%m-%dT23:59:59}'")
        if self.cfg("sales_org_filter"):
            flt.append(f"SalesOrganization eq '{self.cfg('sales_org_filter')}'")
        top, skip, out = int(self.cfg("page_size")), 0, []
        with self._client() as c:
            while True:
                r = c.get(f"/{self.cfg('entity_set')}", params={
                    "$filter": " and ".join(flt), "$top": top, "$skip": skip, "$format": "json",
                    "sap-client": self.cfg("sap_client")})
                r.raise_for_status()
                recs = r.json().get("d", {}).get("results", [])
                out.extend(recs)
                if len(recs) < top:
                    break
                skip += top
        return out

    def transform(self, raw):
        rec = super().transform(raw)
        rec["invoice_date"] = _odata_date(rec.get("invoice_date"))
        if self.cfg("default_cfa") and not rec.get("cfa_code"):
            rec["cfa_code"] = self.cfg("default_cfa")
        return rec


@register
class SAPIDocAdapter(FileDropAdapter):
    key = "sap_idoc"
    name = "SAP IDoc INVOIC02 (XML file port)"
    modes = ["IDOC", "FILE"]
    description = "Reads INVOIC02 IDoc XML files written by an SAP XML file port into a watched folder."
    config_fields = FileDropAdapter.config_fields[:2] + [ConfigField("pattern", "File pattern", False, default="*.xml"),
                                                         ConfigField("default_cfa", "Default CFA code", False)]

    def fetch(self, since, until):
        self.ensure_configured()
        inbox = Path(self.cfg("inbox_dir"))
        done = Path(self.cfg("processed_dir") or inbox / "processed")
        done.mkdir(parents=True, exist_ok=True)
        out = []
        for f in sorted(inbox.glob(self.cfg("pattern", "*.xml"))):
            out.extend(self.parse_invoic02(f.read_bytes()))
            shutil.move(str(f), done / f"{datetime.now():%Y%m%d%H%M%S}_{f.name}")
        return out

    def parse_invoic02(self, content: bytes) -> list[dict]:
        root = ET.fromstring(content)
        out = []
        for idoc in root.iter("IDOC"):
            rec: dict = {"cfa_code": self.cfg("default_cfa")}
            k01 = idoc.find("E1EDK01")
            if k01 is not None:
                rec["invoice_no"] = (k01.findtext("BELNR") or "").strip()
                rec["company_code"] = (k01.findtext("BSART") or "").strip() or None
            for k03 in idoc.findall("E1EDK03"):
                if k03.findtext("IDDAT") in ("012", "026") and k03.findtext("DATUM"):
                    rec["invoice_date"] = datetime.strptime(k03.findtext("DATUM"), "%Y%m%d").date().isoformat()
            for ka1 in idoc.findall("E1EDKA1"):
                if ka1.findtext("PARVW") in ("AG", "RE"):
                    rec["customer_code"] = (ka1.findtext("PARTN") or "").strip()
                    rec["customer_name"] = ka1.findtext("NAME1")
                if ka1.findtext("PARVW") == "WE":
                    rec["location"] = ka1.findtext("ORT01")
            for s01 in idoc.findall("E1EDS01"):
                if s01.findtext("SUMID") in ("011", "002"):
                    rec["sales_value"] = s01.findtext("SUMME")
            for k02 in idoc.findall("E1EDK02"):
                if k02.findtext("QUALF") == "009":
                    rec["source_invoice_id"] = k02.findtext("BELNR")
            rec.setdefault("source_invoice_id", rec.get("invoice_no"))
            out.append(rec)
        return out

    def transform(self, raw):
        return raw


# ----------------------------------------------------------------------------- MARG / CBO / Intrect presets
@register
class MargAdapter(ERPAdapter):
    key = "marg"
    name = "MARG ERP 9+"
    modes = ["FILE", "DB", "API"]
    description = ("MARG sales register export (watched folder) or read-only query on the MARG database. "
                   "Set 'mode' to FILE or DB. Column names below match the sample MARG export; confirm them "
                   "against your MARG version.")
    config_fields = [
        ConfigField("mode", "Mode", True, default="FILE", help="FILE (watched folder) or DB"),
        ConfigField("inbox_dir", "Inbox folder (FILE mode)", False),
        ConfigField("pattern", "File pattern", False, default="*.csv"),
        ConfigField("dsn", "Database URL (DB mode)", False, secret=True),
        ConfigField("query", "Sales register query (DB mode)", False),
        ConfigField("company_code", "Company code for this MARG data", False,
                    help="Used when the export has no company column"),
        ConfigField("cfa_code", "CFA code for this MARG installation", False),
    ]
    default_field_map = {
        "invoice_no": "Bill No", "invoice_date": "Bill Date", "customer_code": "Party Code",
        "customer_name": "Party Name", "location": "Station", "sales_value": "Net Amount", "boxes": "Cases",
        "remarks": "Narration",
    }

    def missing_config(self):
        mode = (self.cfg("mode") or "FILE").upper()
        need = ["inbox_dir"] if mode == "FILE" else ["dsn", "query"]
        return [k for k in need if not self.config.get(k)]

    def _delegate(self):
        mode = (self.cfg("mode") or "FILE").upper()
        cls = FileDropAdapter if mode == "FILE" else GenericDBAdapter
        return cls(self.erp)

    def test_connection(self):
        missing = self.missing_config()
        return (False, f"Not configured: {', '.join(missing)}") if missing else self._delegate().test_connection()

    def fetch(self, since, until):
        missing = self.missing_config()
        if missing:
            raise AdapterNotConfigured(f"MARG adapter missing: {', '.join(missing)}")
        return self._delegate().fetch(since, until)

    def transform(self, raw):
        rec = super().transform(raw)
        rec.setdefault("company_code", self.cfg("company_code"))
        rec.setdefault("cfa_code", self.cfg("cfa_code"))
        return rec


@register
class CBOAdapter(GenericRESTAdapter):
    key = "cbo"
    name = "CBO (API / export)"
    modes = ["API", "DB", "FILE"]
    description = "CBO invoice API (REST/JSON). Endpoint, auth and field names to be confirmed with the CBO vendor."
    default_field_map = {
        "invoice_no": "InvoiceNo", "invoice_date": "InvoiceDate", "customer_code": "CustomerCode",
        "customer_name": "CustomerName", "location": "City", "sales_value": "NetAmount", "boxes": "NoOfCases",
        "company_code": "DivisionCode", "cfa_code": "DepotCode", "source_invoice_id": "InvoiceId",
    }


@register
class IntrectAdapter(GenericRESTAdapter):
    key = "intrect"
    name = "Intrect (API / export)"
    modes = ["API", "DB", "FILE"]
    description = "Intrect invoice API (REST/JSON). Endpoint, auth and field names to be confirmed with Intrect."
    default_field_map = {
        "invoice_no": "inv_no", "invoice_date": "inv_date", "customer_code": "party_id",
        "customer_name": "party_name", "location": "delivery_city", "sales_value": "net_value", "boxes": "boxes",
        "company_code": "principal", "cfa_code": "branch", "source_invoice_id": "doc_id",
    }


# ----------------------------------------------------------------------------- demo
@register
class DemoAdapter(ERPAdapter):
    key = "demo"
    name = "Demo generator (simulated API pull)"
    modes = ["API"]
    description = "Simulates an ERP API returning new invoices, so the automated pull pipeline can be demonstrated."
    config_fields = [ConfigField("records_per_run", "Invoices per run", False, default="5")]

    def test_connection(self):
        return True, "Demo adapter - always available"

    def fetch(self, since, until):
        from sqlalchemy import select
        from sqlalchemy.orm import object_session

        from app.models import Company, Customer, ERPCodeMapping
        db = object_session(self.erp)
        companies = list(db.scalars(select(Company).where(Company.erp_system_id == self.erp.id,
                                                          Company.is_active.is_(True))))
        if not companies:  # simulator: fall back to every active principal
            companies = list(db.scalars(select(Company).where(Company.is_active.is_(True))))
        if not companies:
            raise AdapterNotConfigured("No active company to simulate invoices for")
        maps = {m.target_id: m.source_code for m in db.scalars(select(ERPCodeMapping).where(
            ERPCodeMapping.erp_system_id == self.erp.id, ERPCodeMapping.entity_type == "CUSTOMER"))}
        from app.services.utils import local_now
        rnd = random.Random()
        n = int(self.cfg("records_per_run"))
        now = local_now()
        today = now.date()
        out = []
        for i in range(n):
            co = rnd.choice(companies)
            custs = [c for c in db.scalars(select(Customer).where(Customer.is_active.is_(True)))
                     if not c.companies or co in c.companies]
            cu = rnd.choice(custs)
            cfa = rnd.choice(co.cfas) if co.cfas else None
            stamp = now.strftime("%m%d%H%M%S")
            out.append({"company_code": co.code, "cfa_code": cfa.code if cfa else None,
                        "invoice_no": f"{self.erp.code[:3]}API{stamp}{i}", "invoice_date": today.isoformat(),
                        "customer_code": maps.get(cu.id, cu.code), "customer_name": cu.name,
                        "sales_value": str(Decimal(rnd.randint(4000, 60000))), "boxes": rnd.randint(2, 45)})
        return out
