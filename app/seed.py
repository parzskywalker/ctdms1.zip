"""Realistic demo data generator.

Everything is created through the real services (import pipeline, freight engine, dispatch workflow,
override approvals, reconciliation) with a simulated clock, so the demo database looks like ~3 months
of genuine operation. Dates are relative to the day the seed is run.

    python -m app.cli seed-demo --reset
"""
from __future__ import annotations

import csv
import io
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BASE_DIR, get_settings
from app.models import (
    CFA, Company, Customer, ERPCodeMapping, ERPSystem, ImportTemplate, Invoice, InvoiceStatus,
    Location, Role, Transporter, User,
)
from app.models.base import set_fake_now
from app.security.auth import hash_password
from app.security.permissions import DEFAULT_ROLES
from app.services import dispatch_service as ds
from app.services import import_service as imp
from app.services import invoice_service, rate_service, recon_service
from app.services import settings_service as cfg

SAMPLE_DIR = BASE_DIR / "sample_data"
TZ = ZoneInfo("Asia/Kolkata")
RND = random.Random(20260926)


def at(d: date, hh: int, mm: int = 0) -> datetime:
    """Local (IST) date+time -> naive UTC for the fake clock."""
    from datetime import timezone
    return datetime.combine(d, time(hh, mm)).replace(tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None)


_DB: Session | None = None


def tick(when: datetime) -> None:
    """Advance the simulated clock. Pending rows are flushed first so their default timestamps
    (evaluated at INSERT time) belong to the moment they were created."""
    if _DB is not None:
        _DB.flush()
    set_fake_now(when)


def clock(d: date, hh: int, mm: int = 0) -> None:
    tick(at(d, hh, mm))


# ----------------------------------------------------------------------------- master data
ERPS = [
    ("SAP", "SAP S/4HANA", "sap_odata", "ODATA", "Company A billing in SAP. OData credentials pending from Company A IT."),
    ("MARG", "MARG ERP 9+", "marg", "FILE", "Company B billing in MARG. Daily sales-register export; DB access TBD."),
    ("CBO", "CBO", "cbo", "API", "Company C billing in CBO. API documentation awaited from vendor."),
    ("INTRECT", "Intrect", "intrect", "API", "Own distribution billing in Intrect. API key to be issued by Intrect."),
    ("GENERIC", "Other ERP / manual Excel", "file", "FILE", "Standard CTDMS Excel/CSV template for any other system."),
    ("DEMO", "Demo ERP API (simulator)", "demo", "API", "Simulated API used to demonstrate the automated pull pipeline."),
]

CFAS = [
    ("RPR", "Raipur CFA", "Raipur Central Warehouse", "Plot 14, Urla Industrial Area", "Raipur", "Chhattisgarh", "493221"),
    ("BSP", "Bilaspur CFA", "Bilaspur Depot", "Vyapar Vihar, Link Road", "Bilaspur", "Chhattisgarh", "495001"),
    ("DRG", "Durg CFA", "Durg Depot", "GE Road, Industrial Estate", "Durg", "Chhattisgarh", "491001"),
]

LOCATIONS = [  # code, name, state, pincode, route, zone, km (from Raipur)
    ("RPR", "Raipur", "Chhattisgarh", "492001", "R-LOCAL", "Central", 0),
    ("BSP", "Bilaspur", "Chhattisgarh", "495001", "R-NORTH", "North", 118),
    ("DRG", "Durg", "Chhattisgarh", "491001", "R-WEST", "West", 38),
    ("BHL", "Bhilai", "Chhattisgarh", "490001", "R-WEST", "West", 30),
    ("KRB", "Korba", "Chhattisgarh", "495677", "R-NORTH", "North", 200),
    ("NGP", "Nagpur", "Maharashtra", "440001", "R-MH", "Maharashtra", 290),
    ("RJN", "Rajnandgaon", "Chhattisgarh", "491441", "R-WEST", "West", 70),
    ("RGH", "Raigarh", "Chhattisgarh", "496001", "R-EAST", "East", 250),
]

COMPANIES = [  # code, name, erp, type, cfas
    ("COA", "Company A Pharma Ltd", "SAP", "PRINCIPAL", ["RPR", "BSP", "DRG"]),
    ("COB", "Company B Healthcare Ltd", "MARG", "PRINCIPAL", ["RPR", "BSP"]),
    ("COC", "Company C Lifesciences Ltd", "CBO", "PRINCIPAL", ["RPR", "DRG"]),
    ("OWN", "Own Distribution Division", "INTRECT", "OWN", ["RPR"]),
]

# code, name, location, contact, phone, companies, erp codes {erp: code}
CUSTOMERS = [
    ("C-ABC01", "ABC Pharma", "BSP", "Rakesh Agrawal", "9826011111", ["COA", "COB", "COC", "OWN"],
     {"SAP": "100245", "MARG": "ABC001", "CBO": "ABCPH01", "INTRECT": "P-0012"}),
    ("C-XYZ01", "XYZ Medical", "DRG", "Suresh Jain", "9826022222", ["COA", "COB", "COC", "OWN"],
     {"SAP": "100311", "MARG": "XYZ002", "CBO": "XYZMD01", "INTRECT": "P-0019"}),
    ("C-PQR01", "PQR Distributors", "NGP", "Anil Deshmukh", "9823033333", ["COA", "COB", "COC"],
     {"SAP": "100402", "MARG": "PQR003", "CBO": "PQRDS01"}),
    ("C-SUN01", "Sunrise Agencies", "KRB", "Manoj Sahu", "9827044444", ["COA", "COB", "COC", "OWN"],
     {"SAP": "100518", "MARG": "SUN004", "CBO": "SUNAG01", "INTRECT": "P-0027"}),
    ("C-MET01", "Metro Pharma", "RPR", "Vikas Tiwari", "9826055555", ["COA", "COB", "COC", "OWN"],
     {"SAP": "100623", "MARG": "MET005", "CBO": "METPH01", "INTRECT": "P-0031"}),
    ("C-SGM01", "Shree Ganesh Medicos", "BHL", "Pankaj Verma", "9826066666", ["COA", "COB", "COC", "OWN"],
     {"SAP": "100731", "MARG": "SGM006", "CBO": "SGMED01", "INTRECT": "P-0036"}),
    ("C-JMH01", "Jeevan Medical Hall", "RJN", "Deepak Chandrakar", "9826077777", ["COA", "COB", "COC"],
     {"SAP": "100844", "MARG": "JMH007", "CBO": "JEEVN01"}),
    ("C-LDH01", "Lifeline Drug House", "RGH", "Sanjay Patel", "9826088888", ["COA", "COB"],
     {"SAP": "100952", "MARG": "LDH008"}),
    ("C-CCC01", "City Care Chemists", "RPR", "Neha Sharma", "9826099999", ["COA", "COB", "COC", "OWN"],
     {"SAP": "101067", "MARG": "CCC009", "CBO": "CITYC01", "INTRECT": "P-0044"}),
    ("C-VDP01", "Vardaan Pharma", "BSP", "Ramesh Kashyap", "9826010101", ["COA", "COB", "OWN"],
     {"SAP": "101178", "MARG": "VDP010", "INTRECT": "P-0052"}),
]

TRANSPORTERS = [  # code, name, contact, mobile, gstin, area, vehicles, lr prefix
    ("CGR", "Chhattisgarh Roadlines", "Harish Yadav", "9425201234", "22AABCC1234F1Z5", "Bilaspur, Korba, Raigarh",
     ["CG10AB2211", "CG10AB2212", "CG12C7788"], "CGR"),
    ("SCM", "Speed Cargo Movers", "Imran Khan", "9425205678", "22AADCS5678G1Z2", "Raipur local, Bhilai, Durg",
     ["CG04LM4455", "CG04LM4456"], "SCM"),
    ("SBT", "Shree Balaji Transport", "Mahesh Sahu", "9425209012", "22AAFFS9012H1Z9", "Durg, Bhilai, Rajnandgaon",
     ["CG07BT1010", "CG07BT2020"], "SBT"),
    ("VDL", "Vidarbha Logistics", "Prakash Wankhede", "9422103456", "27AAGCV3456J1Z1", "Nagpur, Maharashtra",
     ["MH31FK9090", "MH40AS1212"], "VDL"),
]

TRANSPORTER_BY_LOCATION = {"RPR": "SCM", "BHL": "SBT", "DRG": "SBT", "RJN": "SBT", "BSP": "CGR", "KRB": "CGR",
                           "RGH": "CGR", "NGP": "VDL"}

# (company, cfa, location, rate) - standard CFA-location rates, effective from FY start
RATES = [
    ("COA", "RPR", "BSP", "8.00"), ("COA", "RPR", "DRG", "6.00"), ("COA", "RPR", "NGP", "12.00"),
    ("COA", "RPR", "RPR", "3.00"), ("COA", "RPR", "BHL", "5.00"), ("COA", "RPR", "KRB", "10.00"),
    ("COA", "RPR", "RJN", "7.00"), ("COA", "RPR", "RGH", "11.00"),
    ("COA", "BSP", "BSP", "3.00"), ("COA", "BSP", "KRB", "6.00"), ("COA", "BSP", "RGH", "7.00"),
    ("COA", "BSP", "RPR", "8.00"),
    ("COA", "DRG", "DRG", "3.00"), ("COA", "DRG", "BHL", "3.50"), ("COA", "DRG", "RJN", "5.00"),
    ("COA", "DRG", "RPR", "6.00"),
    ("COB", "RPR", "BSP", "9.00"), ("COB", "RPR", "DRG", "7.00"), ("COB", "RPR", "NGP", "14.00"),
    ("COB", "RPR", "RPR", "3.50"), ("COB", "RPR", "BHL", "6.00"), ("COB", "RPR", "RJN", "8.00"),
    ("COB", "RPR", "RGH", "12.00"),
    ("COB", "BSP", "BSP", "3.50"), ("COB", "BSP", "RGH", "8.00"), ("COB", "BSP", "RPR", "9.00"),
    ("COC", "RPR", "RPR", "4.00"), ("COC", "RPR", "BSP", "8.50"), ("COC", "RPR", "DRG", "6.50"),
    ("COC", "RPR", "BHL", "6.00"), ("COC", "RPR", "NGP", "13.00"), ("COC", "RPR", "RJN", "7.50"),
    ("COC", "DRG", "DRG", "3.00"), ("COC", "DRG", "BHL", "3.50"), ("COC", "DRG", "RJN", "5.50"),
    ("OWN", "RPR", "RPR", "2.50"), ("OWN", "RPR", "BSP", "7.50"), ("OWN", "RPR", "DRG", "5.50"),
    ("OWN", "RPR", "BHL", "5.00"), ("OWN", "RPR", "KRB", "9.00"),
]
# NOTE: deliberately NO rate for Company C -> Korba (demonstrates RATE MISSING) and no OWN/Raigarh
# (covered by the explicit OWN company default rate).

USERS = [  # username, full name, role, cfas (None = all)
    ("admin", "System Administrator", "ADMIN", None, "Admin@123"),
    ("management", "Arvind Kumar", "MANAGEMENT", None, "Demo@123"),
    ("accounts", "Kavita Joshi", "ACCOUNTS", None, "Demo@123"),
    ("cfa.raipur", "Rohit Mishra", "CFA_MANAGER", ["RPR"], "Demo@123"),
    ("operator.raipur", "Sunil Dewangan", "DISPATCH_OPERATOR", ["RPR"], "Demo@123"),
    ("operator.bilaspur", "Ajay Kaushik", "DISPATCH_OPERATOR", ["BSP"], "Demo@123"),
    ("operator.durg", "Tarun Sinha", "DISPATCH_OPERATOR", ["DRG"], "Demo@123"),
    ("checker", "Meena Gupta", "CHECKER", None, "Demo@123"),
    ("viewer", "Audit Viewer", "VIEWER", None, "Demo@123"),
]


@dataclass
class Ctx:
    db: Session
    today: date
    start: date
    users: dict
    erps: dict
    companies: dict
    cfas: dict
    locations: dict
    customers: dict
    transporters: dict


def reset_database() -> None:
    from app.db import Base, engine
    import app.models  # noqa: F401
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def seed_core(db: Session) -> dict:
    """Roles + settings (needed in every installation)."""
    roles = {}
    for code, d in DEFAULT_ROLES.items():
        r = db.scalar(select(Role).where(Role.code == code))
        if not r:
            r = Role(code=code, name=d["name"], description=d["description"], permissions=d["permissions"],
                     is_system=True)
            db.add(r)
        roles[code] = r
    cfg.ensure_defaults(db)
    db.flush()
    return roles


def seed_masters(db: Session, today: date) -> Ctx:
    roles = seed_core(db)
    erps = {}
    for code, name, adapter, mode, notes in ERPS:
        e = ERPSystem(code=code, name=name, adapter=adapter, integration_mode=mode, notes=notes,
                      import_format="CSV/XLSX", config={"records_per_run": "4"} if adapter == "demo" else {})
        db.add(e)
        erps[code] = e
    db.flush()
    cfas = {}
    for code, name, wh, addr, city, state, pin in CFAS:
        c = CFA(code=code, name=name, warehouse=wh, address=addr, city=city, state=state, pincode=pin,
                contact_person="Warehouse In-charge", contact_phone="0771-40" + str(RND.randint(10000, 99999)))
        db.add(c)
        cfas[code] = c
    locations = {}
    for code, name, state, pin, route, zone, km in LOCATIONS:
        lo = Location(code=code, name=name, city=name, state=state, pincode=pin, route=route, zone=zone,
                      distance_km=Decimal(km))
        db.add(lo)
        locations[code] = lo
    companies = {}
    for code, name, erp, rtype, cfa_codes in COMPANIES:
        co = Company(code=code, name=name, erp_system=erps[erp], relationship_type=rtype,
                     contact_person="Distribution Manager", contact_phone="022-6" + str(RND.randint(1000000, 9999999)),
                     email=f"logistics@{code.lower()}.example.com", cfas=[cfas[c] for c in cfa_codes])
        db.add(co)
        companies[code] = co
    db.flush()
    customers = {}
    for code, name, loc, person, phone, cos, erp_codes in CUSTOMERS:
        lo = locations[loc]
        cu = Customer(code=code, name=name, location=lo, city=lo.name, state=lo.state, pincode=lo.pincode,
                      billing_address=f"{RND.randint(1, 250)}, Main Market, {lo.name}",
                      delivery_address=f"Godown {RND.randint(1, 30)}, Transport Nagar, {lo.name}",
                      contact_person=person, contact_phone=phone, email=f"orders@{name.split()[0].lower()}.example.in",
                      gstin=f"22AAB{RND.randint(1000, 9999)}C1Z{RND.randint(1, 9)}" if lo.state == "Chhattisgarh" else
                      f"27AAC{RND.randint(1000, 9999)}D1Z{RND.randint(1, 9)}",
                      drug_license_no=f"CG-DL-{RND.randint(10000, 99999)}",
                      companies=[companies[c] for c in cos], cfas=list(cfas.values()))
        db.add(cu)
        customers[code] = cu
    db.flush()
    # ERP code mappings: the same party has a different code in every ERP
    for code, name, loc, person, phone, cos, erp_codes in CUSTOMERS:
        for erp, src in erp_codes.items():
            db.add(ERPCodeMapping(erp_system=erps[erp], entity_type="CUSTOMER", source_code=src, source_name=name.upper(),
                                  target_id=customers[code].id))
    # company / CFA codes inside the ERPs
    for erp, src, tgt in [("SAP", "1000", "COA"), ("CBO", "CLS", "COC"), ("INTRECT", "OWN-DIST", "OWN")]:
        db.add(ERPCodeMapping(erp_system=erps[erp], entity_type="COMPANY", source_code=src, target_id=companies[tgt].id,
                              source_name=companies[tgt].name))
    for erp, src, tgt in [("SAP", "1101", "RPR"), ("SAP", "1102", "BSP"), ("SAP", "1103", "DRG"),
                          ("MARG", "RAIPUR", "RPR"), ("MARG", "BILASPUR", "BSP"),
                          ("CBO", "D-RPR", "RPR"), ("CBO", "D-DRG", "DRG"), ("INTRECT", "RAIPUR-HO", "RPR")]:
        db.add(ERPCodeMapping(erp_system=erps[erp], entity_type="CFA", source_code=src, target_id=cfas[tgt].id))
    db.add(ERPCodeMapping(erp_system=erps["MARG"], entity_type="LOCATION", source_code="BHILAI NAGAR",
                          target_id=locations["BHL"].id, source_name="Bhilai Nagar"))
    transporters = {}
    for code, name, person, mobile, gst, area, veh, lrp in TRANSPORTERS:
        t = Transporter(code=code, name=name, contact_person=person, mobile=mobile, gstin=gst, pan=gst[2:12],
                        service_area=area, payment_terms="Monthly bill, 30 days", credit_days=30,
                        address=f"Transport Nagar, {'Nagpur' if code == 'VDL' else 'Raipur'}",
                        city="Nagpur" if code == "VDL" else "Raipur",
                        state="Maharashtra" if code == "VDL" else "Chhattisgarh")
        db.add(t)
        transporters[code] = t
    users = {}
    for uname, full, role, cfa_codes, pwd in USERS:
        u = User(username=uname, full_name=full, role=roles[role], password_hash=hash_password(pwd),
                 all_companies=True, all_cfas=cfa_codes is None,
                 email=f"{uname.replace('.', '_')}@example.com")
        if cfa_codes:
            u.cfas = [cfas[c] for c in cfa_codes]
        db.add(u)
        users[uname] = u
    db.flush()
    # Import templates (one per ERP export format)
    templates = [
        ("SAP - Billing documents (Company A)", "SAP", None, {
            "company_code": "Sales Org", "cfa_code": "Plant", "invoice_no": "Billing Document",
            "invoice_date": "Billing Date", "customer_code": "Sold-To Party", "customer_name": "Sold-To Name",
            "location": "Ship-To City", "sales_value": "Net Value", "boxes": "No. of Packages",
            "source_invoice_id": "Billing Document"}, "AUTO"),
        ("MARG - Sales register (Company B)", "MARG", "COB", {
            "cfa_code": "Branch", "invoice_no": "Bill No", "invoice_date": "Bill Date", "customer_code": "Party Code",
            "customer_name": "Party Name", "location": "Station", "sales_value": "Net Amount", "boxes": "Cases",
            "remarks": "Narration"}, "%d-%m-%Y"),
        ("CBO - Invoice export (Company C)", "CBO", None, {
            "company_code": "Division", "cfa_code": "Depot", "invoice_no": "InvoiceNo", "invoice_date": "InvoiceDate",
            "customer_code": "CustomerCode", "customer_name": "CustomerName", "location": "City",
            "sales_value": "NetAmount", "boxes": "NoOfCases", "source_invoice_id": "InvoiceId"}, "%d/%m/%Y"),
        ("Intrect - Invoice export (Own distribution)", "INTRECT", None, {
            "company_code": "principal", "cfa_code": "branch", "invoice_no": "inv_no", "invoice_date": "inv_date",
            "customer_code": "party_id", "customer_name": "party_name", "location": "delivery_city",
            "sales_value": "net_value", "boxes": "boxes", "source_invoice_id": "doc_id"}, "AUTO"),
        ("Standard CTDMS template", "GENERIC", None, {k: k for k in imp.TARGET_FIELDS}, "AUTO"),
    ]
    for name, erp, co, cmap, dfmt in templates:
        db.add(ImportTemplate(name=name, erp_system=erps[erp], company_id=companies[co].id if co else None,
                              column_map=cmap, date_format=dfmt, created_by_id=users["admin"].id))
    db.flush()
    return Ctx(db, today, today, users, erps, companies, cfas, locations, customers, transporters)


def seed_rates(c: Ctx, fy_from: date, revision_from: date) -> None:
    db, admin = c.db, c.users["admin"]
    for co, cfa, loc, rate in RATES:
        rate_service.create_rate(db, admin, company_id=c.companies[co].id, cfa_id=c.cfas[cfa].id,
                                 location_id=c.locations[loc].id, rate_per_box=Decimal(rate), effective_from=fy_from,
                                 remarks="Annual freight agreement", change_reason="Initial rate card", auto_approve=True)
    # Level 1 - customer-specific: ABC Pharma negotiated ₹7 (vs ₹8) for Company A Raipur -> Bilaspur
    rate_service.create_rate(db, admin, company_id=c.companies["COA"].id, cfa_id=c.cfas["RPR"].id,
                             customer_id=c.customers["C-ABC01"].id, location_id=c.locations["BSP"].id,
                             rate_per_box=Decimal("7.00"), effective_from=fy_from, auto_approve=True,
                             remarks="Customer-specific: volume customer", change_reason="Negotiated volume rate")
    # Level 3 - company-wide location rate: Company B Korba from any CFA
    rate_service.create_rate(db, admin, company_id=c.companies["COB"].id, location_id=c.locations["KRB"].id,
                             rate_per_box=Decimal("11.00"), effective_from=fy_from, auto_approve=True,
                             remarks="Company-wide Korba rate (any CFA)", change_reason="Initial rate card")
    # Level 4 - explicitly configured default for own distribution
    rate_service.create_rate(db, admin, company_id=c.companies["OWN"].id, is_default=True,
                             rate_per_box=Decimal("10.00"), effective_from=fy_from, auto_approve=True,
                             remarks="Own distribution default for unlisted locations", change_reason="Default rate")


def rate_revision(c: Ctx, revision_from: date) -> None:
    """Company A / Raipur CFA / Durg: ₹6.00 -> ₹6.50 from the 1st of this month (maker: CFA manager, checker approves)."""
    db = c.db
    from app.models import FreightRate, RateStatus
    prev = db.scalar(select(FreightRate).where(FreightRate.company_id == c.companies["COA"].id,
                                               FreightRate.cfa_id == c.cfas["RPR"].id,
                                               FreightRate.location_id == c.locations["DRG"].id,
                                               FreightRate.customer_id.is_(None), FreightRate.status == RateStatus.APPROVED))
    clock(revision_from - timedelta(days=3), 11, 5)
    new = rate_service.revise_rate(db, c.users["cfa.raipur"], prev, rate_per_box=Decimal("6.50"),
                                   effective_from=revision_from, change_reason="Diesel price increase - revised by Company A")
    clock(revision_from - timedelta(days=2), 16, 40)
    rate_service.approve_rate(db, c.users["checker"], new, remarks="Verified against Company A circular")


def pending_rate_proposal(c: Ctx) -> None:
    from app.models import FreightRate, RateStatus
    db = c.db
    prev = db.scalar(select(FreightRate).where(FreightRate.company_id == c.companies["COB"].id,
                                               FreightRate.cfa_id == c.cfas["RPR"].id,
                                               FreightRate.location_id == c.locations["NGP"].id,
                                               FreightRate.status == RateStatus.APPROVED))
    nxt = (c.today.replace(day=1) + timedelta(days=32)).replace(day=1)
    clock(c.today, 10, 15)
    rate_service.revise_rate(db, c.users["cfa.raipur"], prev, rate_per_box=Decimal("15.00"), effective_from=nxt,
                             change_reason="Vidarbha Logistics revised Nagpur tariff")


# ----------------------------------------------------------------------------- invoice generation
@dataclass
class Rec:
    erp: str
    company: str
    cfa: str
    customer: str
    location: str
    inv_no: str
    inv_date: date
    sales: Decimal
    boxes: int | None


def _serial(prefix: str, n: int) -> str:
    return f"{prefix}{n:05d}"


def generate_invoices(c: Ctx, start: date, end: date) -> list[Rec]:
    recs: list[Rec] = []
    counters = {"SAP": 90012000, "MARG": 1200, "CBO": 4500, "INTRECT": 7800}
    by_company = {"COA": "SAP", "COB": "MARG", "COC": "CBO", "OWN": "INTRECT"}
    weights = {"COA": 5, "COB": 4, "COC": 3, "OWN": 2}
    d = start
    while d <= end:
        if d.weekday() != 6 and d.day in (4, 14, 24):
            # Anchor invoices for the spec examples: Company A / Raipur CFA to Durg (rate revised this month),
            # Bilaspur (customer-specific ABC Pharma rate + standard rate), Nagpur; Company B / Raipur likewise.
            for co, cu_code in [("COA", "C-XYZ01"), ("COA", "C-ABC01"), ("COA", "C-VDP01"), ("COA", "C-PQR01"),
                                ("COB", "C-ABC01"), ("COB", "C-XYZ01"), ("COB", "C-PQR01")]:
                erp = by_company[co]
                cu = next(x for x in CUSTOMERS if x[0] == cu_code)
                counters[erp] += 1
                n = counters[erp]
                inv_no = {"SAP": str(n), "MARG": f"MB/{d:%y}/{n:05d}"}[erp]
                boxes = 36 if (cu_code == "C-ABC01" and co == "COA") else max(4, int(RND.gauss(24, 8)))
                sales = Decimal(boxes * RND.randint(700, 1800)).quantize(Decimal("1"))
                recs.append(Rec(erp, co, "RPR", cu_code, cu[2], inv_no, d, sales, boxes))
        if d.weekday() != 6:  # no Sunday dispatches
            n_today = RND.randint(2, 4) if d < end - timedelta(days=1) else RND.randint(8, 11)
            for _ in range(n_today):
                co = RND.choices(list(weights), weights=list(weights.values()))[0]
                erp = by_company[co]
                cust_rows = [x for x in CUSTOMERS if co in x[5]]
                cu = RND.choice(cust_rows)
                cfa_codes = next(x[4] for x in COMPANIES if x[0] == co)
                # nearest CFA for the customer's location
                pref = {"BSP": "BSP", "KRB": "BSP", "RGH": "BSP", "DRG": "DRG", "BHL": "DRG", "RJN": "DRG"}.get(cu[2], "RPR")
                cfa = pref if pref in cfa_codes and RND.random() < 0.8 else "RPR"
                if co == "COA" and cfa == "BSP" and cu[2] not in ("BSP", "KRB", "RGH", "RPR"):
                    cfa = "RPR"
                if co == "COA" and cfa == "DRG" and cu[2] not in ("DRG", "BHL", "RJN", "RPR"):
                    cfa = "RPR"
                if co == "COB" and cfa == "BSP" and cu[2] not in ("BSP", "RGH", "RPR", "KRB"):
                    cfa = "RPR"
                if co == "COC" and cfa == "DRG" and cu[2] not in ("DRG", "BHL", "RJN"):
                    cfa = "RPR"
                counters[erp] += RND.randint(1, 3)
                n = counters[erp]
                inv_no = {"SAP": str(n), "MARG": f"MB/{d:%y}/{n:05d}", "CBO": f"CBO/{cfa}/{n:05d}",
                          "INTRECT": f"INV{d:%y}-{n:05d}"}[erp]
                boxes = max(1, int(RND.gauss(22, 11)))
                sales = Decimal(boxes * RND.randint(650, 1900) + RND.randint(0, 999)).quantize(Decimal("1"))
                recs.append(Rec(erp, co, cfa, cu[0], cu[2], inv_no, d, sales, boxes))
        d += timedelta(days=1)
    recs.sort(key=lambda r: (r.inv_date, r.erp, r.inv_no))
    return recs


def _erp_row(r: Rec) -> dict:
    cu = next(x for x in CUSTOMERS if x[0] == r.customer)
    loc_name = next(x[1] for x in LOCATIONS if x[0] == r.location)
    if r.erp == "SAP":
        plant = {"RPR": "1101", "BSP": "1102", "DRG": "1103"}[r.cfa]
        return {"Billing Document": r.inv_no, "Billing Date": r.inv_date, "Sales Org": "1000", "Plant": plant,
                "Sold-To Party": cu[6]["SAP"], "Sold-To Name": cu[1].upper(), "Ship-To City": loc_name.upper(),
                "Net Value": float(r.sales), "Currency": "INR", "No. of Packages": r.boxes}
    if r.erp == "MARG":
        branch = {"RPR": "RAIPUR", "BSP": "BILASPUR"}[r.cfa]
        station = "BHILAI NAGAR" if r.location == "BHL" else loc_name.upper()
        return {"Bill No": r.inv_no, "Bill Date": r.inv_date.strftime("%d-%m-%Y"), "Branch": branch,
                "Party Code": cu[6]["MARG"], "Party Name": cu[1].upper(), "Station": station,
                "Net Amount": f"{r.sales:.2f}", "Cases": "" if r.boxes is None else r.boxes, "Narration": ""}
    if r.erp == "CBO":
        return {"InvoiceId": f"CBO-{r.inv_no.split('/')[-1]}", "InvoiceNo": r.inv_no,
                "InvoiceDate": r.inv_date.strftime("%d/%m/%Y"), "Division": "CLS", "Depot": f"D-{r.cfa}",
                "CustomerCode": cu[6]["CBO"], "CustomerName": cu[1], "City": loc_name, "NetAmount": f"{r.sales:.2f}",
                "NoOfCases": r.boxes}
    return {"doc_id": f"INTR-{r.inv_no}", "inv_no": r.inv_no, "inv_date": r.inv_date.isoformat(),
            "principal": "OWN-DIST", "branch": "RAIPUR-HO", "party_id": cu[6]["INTRECT"], "party_name": cu[1],
            "delivery_city": loc_name, "net_value": float(r.sales), "boxes": r.boxes}


def _to_bytes(rows: list[dict], kind: str) -> tuple[bytes, str]:
    headers = list(rows[0].keys())
    if kind == "xlsx":
        wb = Workbook()
        ws = wb.active
        ws.append(headers)
        for r in rows:
            ws.append([r.get(h) for h in headers])
        from openpyxl.styles import Font
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, date):
                    cell.number_format = "dd.mm.yyyy"
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue(), "xlsx"
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers)
    w.writeheader()
    for r in rows:
        w.writerow({k: (v.strftime("%d-%m-%Y") if isinstance(v, date) else v) for k, v in r.items()})
    return buf.getvalue().encode("utf-8-sig"), "csv"


FORMAT = {"SAP": "xlsx", "MARG": "csv", "CBO": "csv", "INTRECT": "xlsx"}
TEMPLATE = {"SAP": "SAP - Billing documents (Company A)", "MARG": "MARG - Sales register (Company B)",
            "CBO": "CBO - Invoice export (Company C)", "INTRECT": "Intrect - Invoice export (Own distribution)"}
IMPORTER = {"SAP": "admin", "MARG": "admin", "CBO": "admin", "INTRECT": "admin"}


def import_files(c: Ctx, recs: list[Rec], label: str, when: datetime, extra_rows: dict | None = None) -> list:
    db = c.db
    batches = []
    for erp in ("SAP", "MARG", "CBO", "INTRECT"):
        rows = [_erp_row(r) for r in recs if r.erp == erp]
        rows += (extra_rows or {}).get(erp, [])
        if not rows:
            continue
        content, ext = _to_bytes(rows, FORMAT[erp])
        tick(when)
        tpl = db.scalar(select(ImportTemplate).where(ImportTemplate.name == TEMPLATE[erp]))
        user = c.users[IMPORTER[erp]]
        batch, _ = imp.create_batch_from_file(db, user, content, f"{erp}_{label}.{ext}", template_id=tpl.id)
        imp.validate_batch(db, batch, user)
        tick(when + timedelta(minutes=4))
        imp.commit_batch(db, user, batch)
        batches.append(batch)
    db.flush()
    return batches


# ----------------------------------------------------------------------------- dispatch simulation
VEH_IDX: dict[str, int] = {}
LR_SEQ: dict[str, int] = {}


def _operator(c: Ctx, inv: Invoice) -> User:
    return {"RPR": c.users["operator.raipur"], "BSP": c.users["operator.bilaspur"],
            "DRG": c.users["operator.durg"]}[inv.cfa.code]


def _transporter(c: Ctx, inv: Invoice) -> Transporter:
    code = TRANSPORTER_BY_LOCATION[inv.location.code]
    if inv.location.code == "RPR" and RND.random() < 0.3:
        code = "SBT"
    return c.transporters[code]


def dispatch_invoice(c: Ctx, inv: Invoice, stop_at: str = "DISPATCHED", reprint: bool = False) -> None:
    """Run the real workflow with a simulated clock. stop_at: PENDING_DETAILS/VERIFIED/SLIP_GENERATED/SLIP_PRINTED/DISPATCHED"""
    db = c.db
    if inv.status not in (InvoiceStatus.PENDING, InvoiceStatus.VERIFIED):
        return
    op = _operator(c, inv)
    same_day = RND.random() < 0.7 or inv.invoice_date >= c.today
    d = inv.invoice_date if same_day else inv.invoice_date + timedelta(days=1)
    if d.weekday() == 6:  # no Sunday dispatch
        d += timedelta(days=1)
    if d > c.today:
        d = c.today
    tr = _transporter(c, inv)
    veh_pool = next(x[6] for x in TRANSPORTERS if x[0] == tr.code)
    VEH_IDX[tr.code] = VEH_IDX.get(tr.code, 0) + 1
    veh = veh_pool[VEH_IDX[tr.code] % len(veh_pool)]
    LR_SEQ[tr.code] = LR_SEQ.get(tr.code, 5000) + RND.randint(1, 4)
    lr = f"{tr.code}{LR_SEQ[tr.code]}"
    hh = RND.randint(13, 18) if d == inv.invoice_date else RND.randint(9, 16)
    mm = RND.choice([0, 10, 15, 25, 30, 40, 45, 50])
    clock(d, hh, mm)
    ds.save_details(db, op, inv, transporter_id=tr.id, vehicle_no=veh, lr_no=lr, dispatch_date=d,
                    dispatch_time=time(hh, mm), remarks=None)
    if stop_at == "PENDING_DETAILS":
        return
    clock(d, hh, mm + 2 if mm < 55 else mm)
    ds.verify(db, op if RND.random() < 0.8 else c.users["checker"], inv)
    if stop_at == "VERIFIED":
        return
    clock(d, hh, min(mm + 4, 59))
    ds.generate_slip(db, op, inv)
    if stop_at == "SLIP_GENERATED":
        return
    ds.record_print(db, op, inv, RND.choice(["PRINT_A5", "PRINT_A5", "PRINT_A4", "PDF_A5"]))
    if reprint:
        clock(d, min(hh + 1, 22), 5)
        ds.record_print(db, op, inv, "PRINT_A5", RND.choice(["Printer jam - first copy torn",
                                                             "Transporter lost the copy",
                                                             "Customer requested duplicate for gate pass"]))
    if stop_at == "SLIP_PRINTED":
        return
    clock(d, min(hh + 1, 22), 20)
    ds.complete(db, op, inv)


# ----------------------------------------------------------------------------- main seed
def run(reset: bool = True, quiet: bool = False) -> dict:
    from app.db import SessionLocal
    if reset:
        reset_database()
    now = datetime.now(TZ)
    real_today = now.date()
    # The simulated working day ends at 19:30. Before that, simulate up to yesterday so that no demo event
    # carries a timestamp later than the real current time.
    today = real_today if now.time() >= time(19, 30) else real_today - timedelta(days=1)
    m0 = today.replace(day=1)                                   # this month
    m1 = (m0 - timedelta(days=1)).replace(day=1)                # last month
    m2 = (m1 - timedelta(days=1)).replace(day=1)                # two months ago
    fy_from = date(today.year if today.month >= 4 else today.year - 1, 4, 1)
    db = SessionLocal()
    global _DB
    _DB = db
    log = (lambda *a: None) if quiet else print
    try:
        clock(fy_from if fy_from < m2 else m2, 9, 0)
        c = seed_masters(db, today)
        seed_rates(c, fy_from, m0)
        db.commit()
        log("masters & rates ✓")

        # ---- daily ERP exports imported at 12:30 each working day; rate revision before this month
        all_recs = generate_invoices(c, m2, today)
        d = m2
        revised = False
        while d <= today:
            if not revised and d >= m0 - timedelta(days=3):
                rate_revision(c, m0)
                revised = True
            day_recs = [r for r in all_recs if r.inv_date == d]
            extra = _problem_rows(c, day_recs, [r for r in all_recs if r.inv_date < m0]) if d == today else None
            if day_recs or extra:
                import_files(c, day_recs, f"{d:%Y%m%d}", at(d, 12, 30), extra)
            d += timedelta(days=1)
        if not revised:
            rate_revision(c, m0)
        db.commit()
        log(f"imports ✓ ({len(all_recs)} invoices)")

        # ---- dispatch simulation
        invs = list(db.scalars(select(Invoice).order_by(Invoice.invoice_date, Invoice.id)))
        recent_cut = today - timedelta(days=3)
        open_recent = []
        for inv in invs:
            if inv.status not in (InvoiceStatus.PENDING,):
                continue
            if inv.invoice_date < recent_cut:
                dispatch_invoice(c, inv, "DISPATCHED", reprint=RND.random() < 0.05)
            else:
                open_recent.append(inv)
        # recent invoices in every stage so the pending queue is realistic
        stages = ["DISPATCHED", "SLIP_PRINTED", "SLIP_GENERATED", "VERIFIED", "PENDING_DETAILS", "NONE", "NONE"]
        for i, inv in enumerate(open_recent):
            st = stages[i % len(stages)]
            if st != "NONE":
                dispatch_invoice(c, inv, st)
        db.commit()
        log("dispatch workflow ✓")

        _overrides_and_cancellations(c, invs, today)
        db.commit()
        log("overrides / cancellations ✓")

        _bills(c, m2, m1)
        db.commit()
        log("transporter bills ✓")

        pending_rate_proposal(c)
        # a demo API pull so the integration log is not empty
        from app.integrations.runner import run_sync
        clock(today, 8, 45)
        run_sync(db, c.erps["DEMO"], c.users["admin"], trigger="SCHEDULED",
                 since=datetime.combine(today - timedelta(days=1), time.min))
        # ERP write-back: older dispatches already pulled back into the ERPs
        for inv in db.scalars(select(Invoice).where(Invoice.status == InvoiceStatus.DISPATCHED,
                                                    Invoice.dispatch_date < m1)):
            inv.erp_sync_status = "SYNCED"
            inv.erp_synced_at = inv.completed_at
        db.commit()
        set_fake_now(None)
        if get_settings().environment != "test":
            write_sample_files(c, real_today)
        counts = {s_: db.query(Invoice).filter(Invoice.status == s_).count() for s_ in InvoiceStatus.ALL}
        log("status counts:", counts)
        return counts
    finally:
        set_fake_now(None)
        _DB = None
        db.close()


def _problem_rows(c: Ctx, part: list[Rec], hist: list[Rec]) -> dict:
    """Rows that must fail validation in the latest MARG file: unknown customer, unknown location, duplicate."""
    d = c.today
    dup = next((r for r in hist if r.erp == "MARG"), None)
    rows = [
        {"Bill No": f"MB/{d:%y}/09901", "Bill Date": d.strftime("%d-%m-%Y"), "Branch": "RAIPUR", "Party Code": "NEW777",
         "Party Name": "NAVJEEVAN MEDICAL AGENCIES", "Station": "RAIPUR", "Net Amount": "48250.00", "Cases": 14,
         "Narration": "New party - not yet in master"},
        {"Bill No": f"MB/{d:%y}/09902", "Bill Date": d.strftime("%d-%m-%Y"), "Branch": "RAIPUR", "Party Code": "ABC 001",
         "Party Name": "A.B.C. PHARMA", "Station": "BILASPUR", "Net Amount": "22110.00", "Cases": 9,
         "Narration": "Code typed with a space in MARG"},
        {"Bill No": f"MB/{d:%y}/09903", "Bill Date": d.strftime("%d-%m-%Y"), "Branch": "RAIPUR", "Party Code": "MET005",
         "Party Name": "METRO PHARMA", "Station": "MAHASAMUND", "Net Amount": "15400.00", "Cases": 6,
         "Narration": "Location not in master"},
        {"Bill No": f"MB/{d:%y}/09904", "Bill Date": d.strftime("%d-%m-%Y"), "Branch": "RAIPUR", "Party Code": "SGM006",
         "Party Name": "SHREE GANESH MEDICOS", "Station": "BHILAI NAGAR", "Net Amount": "30980.00", "Cases": "",
         "Narration": "Cases not entered in MARG"},
    ]
    if dup:
        r = _erp_row(dup)
        r["Narration"] = "Re-exported by mistake"
        rows.append(r)
    return {"MARG": rows}


def _overrides_and_cancellations(c: Ctx, invs: list[Invoice], today: date) -> None:
    db = c.db
    open_invs = [i for i in invs if i.status in (InvoiceStatus.PENDING,) and (i.boxes or 0) >= 6]
    # 1) operator requests a box change, checker approves
    if open_invs:
        inv = open_invs[0]
        clock(today, 10, 5)
        ov = invoice_service.request_override(db, c.users["operator.raipur" if inv.cfa.code == "RPR" else
                                                           "operator.bilaspur" if inv.cfa.code == "BSP" else
                                                           "operator.durg"], inv, "BOXES", inv.boxes + 2,
                                              "Physical count at dock: 2 extra boxes (split carton)")
        clock(today, 10, 40)
        invoice_service.decide_override(db, c.users["checker"], ov, True, "Checked physically with warehouse in-charge")
    # 2) pending request - stays in the approval queue (invoice on hold)
    if len(open_invs) > 1:
        inv = open_invs[1]
        clock(today, 11, 20)
        invoice_service.request_override(db, _operator(c, inv), inv, "BOXES", max(1, inv.boxes - 3),
                                         "3 boxes short-shipped from principal; confirm before dispatch")
    # 3) checker applies a special freight rate directly (audited)
    if len(open_invs) > 2:
        inv = open_invs[2]
        clock(today, 12, 5)
        invoice_service.request_override(db, c.users["checker"], inv, "RATE", "5.00",
                                         "Shared vehicle with another consignment - special rate agreed by management")
    # 4) boxes missing (from MARG row) -> operator enters them (initial entry, no approval needed)
    miss = [i for i in invs if i.exception_code == "BOXES_MISSING"]
    for inv in miss[:0]:  # keep it open for the demo
        pass
    # cancellations: one open invoice cancelled (ERP credit note), one historic slip voided & regenerated
    cand = [i for i in invs if i.status in (InvoiceStatus.PENDING, InvoiceStatus.VERIFIED)]
    if len(cand) > 4:
        inv = cand[-1]
        clock(today, 12, 30)
        ds.cancel(db, c.users["cfa.raipur"] if inv.cfa.code == "RPR" else c.users["checker"], inv,
                  "Invoice cancelled in ERP - credit note issued (order returned)")
    slipped = [i for i in invs if i.status == InvoiceStatus.SLIP_GENERATED]
    if slipped:
        inv = slipped[0]
        clock(today, 13, 10)
        ds.void_slip(db, c.users["checker"], inv, "Vehicle changed by transporter after slip was generated")
        tr_new = c.transporters["SBT"] if inv.transporter.code != "SBT" else c.transporters["SCM"]
        ds.save_details(db, _operator(c, inv), inv, transporter_id=tr_new.id, vehicle_no="CG07BT3030",
                        lr_no=f"SBT{RND.randint(9000, 9999)}", dispatch_date=inv.dispatch_date,
                        dispatch_time=inv.dispatch_time, remarks="Re-routed via Shree Balaji")
        clock(today, 13, 20)
        ds.generate_slip(db, _operator(c, inv), inv)
    hist_dispatched = [i for i in invs if i.status == InvoiceStatus.DISPATCHED]
    if hist_dispatched:
        inv = hist_dispatched[len(hist_dispatched) // 3]
        clock(inv.dispatch_date + timedelta(days=2), 15, 0)
        ds.cancel(db, c.users["admin"], inv, "Consignment returned undelivered - customer shop closed (admin cancellation)")


def _bills(c: Ctx, m2: date, m1: date) -> None:
    db = c.db
    acc, chk = c.users["accounts"], c.users["checker"]

    def period(m):
        return m, (m + timedelta(days=32)).replace(day=1) - timedelta(days=1)

    def make(tcode, m, suffix, tweak=None):
        pf, pt = period(m)
        clock(pt + timedelta(days=4), 11, 0)
        b = recon_service.create_bill(db, acc, transporter_id=c.transporters[tcode].id, bill_no=f"{tcode}/{m:%y%m}/{suffix}",
                                      bill_date=pt + timedelta(days=3), bill_amount="0", period_from=pf, period_to=pt,
                                      remarks=f"Monthly freight bill {m:%b %Y}")
        recon_service.link_dispatches(db, acc, b)
        amounts = {}
        for ln in b.lines:
            amt = ln.calculated_amount or Decimal("0")
            if tweak:
                amt = tweak(ln, amt)
            amounts[ln.id] = str(amt)
        recon_service.set_line_amounts(db, acc, b, amounts)
        b.bill_amount = sum((ln.billed_amount or 0) for ln in b.lines)
        recon_service.rematch(db, b)
        return b

    # M-2: exact bills, approved and paid
    for tcode in ("CGR", "SBT", "SCM"):
        b = make(tcode, m2, "01")
        clock(period(m2)[1] + timedelta(days=6), 15, 30)
        recon_service.approve(db, chk, b, "Matched with system freight")
        clock(period(m2)[1] + timedelta(days=12), 12, 0)
        recon_service.mark_paid(db, acc, b, f"UTR{RND.randint(10**11, 10**12 - 1)}")
    # M-1: one exact (approved), one with variances (awaiting approval), one matched awaiting approval
    b = make("SBT", m1, "01")
    clock(period(m1)[1] + timedelta(days=6), 15, 30)
    recon_service.approve(db, chk, b, "Matched")
    k = {"n": 0}

    def overbill(ln, amt):
        k["n"] += 1
        if k["n"] % 4 == 0:
            return amt + Decimal("60.00")  # detention / extra charges billed
        if k["n"] % 7 == 0:
            return amt + Decimal("25.00")
        return amt
    b2 = make("SCM", m1, "01", overbill)
    # a line the transporter billed that the system cannot find
    from app.models import TransporterBillLine
    b2.lines.append(TransporterBillLine(lr_no="SCM99999", invoice_no="UNKNOWN-1", billed_boxes=12,
                                        billed_amount=Decimal("420.00"), remarks="LR not in dispatch records"))
    db.flush()
    b2.bill_amount = sum((ln.billed_amount or 0) for ln in b2.lines)
    recon_service.rematch(db, b2)
    make("CGR", m1, "01")
    make("VDL", m2, "01")


def write_sample_files(c: Ctx, today: date) -> None:
    """Fresh ERP exports dated today (not yet imported) that users can upload to try the import screen."""
    SAMPLE_DIR.mkdir(exist_ok=True)
    recs = []
    base = {"SAP": 91000000, "MARG": 20000, "CBO": 80000, "INTRECT": 60000}
    plan = [("SAP", "COA", "RPR", "C-ABC01", "BSP", 36), ("SAP", "COA", "RPR", "C-XYZ01", "DRG", 18),
            ("SAP", "COA", "RPR", "C-PQR01", "NGP", 42), ("SAP", "COA", "BSP", "C-SUN01", "KRB", 25),
            ("SAP", "COA", "DRG", "C-SGM01", "BHL", 12), ("SAP", "COA", "RPR", "C-MET01", "RPR", 8),
            ("MARG", "COB", "RPR", "C-ABC01", "BSP", 30), ("MARG", "COB", "RPR", "C-XYZ01", "DRG", 16),
            ("MARG", "COB", "RPR", "C-PQR01", "NGP", 28), ("MARG", "COB", "BSP", "C-VDP01", "BSP", 11),
            ("MARG", "COB", "RPR", "C-SUN01", "KRB", 21),
            ("CBO", "COC", "RPR", "C-SUN01", "KRB", 19), ("CBO", "COC", "RPR", "C-MET01", "RPR", 10),
            ("CBO", "COC", "DRG", "C-JMH01", "RJN", 14),
            ("INTRECT", "OWN", "RPR", "C-CCC01", "RPR", 6), ("INTRECT", "OWN", "RPR", "C-SUN01", "KRB", 17),
            ("INTRECT", "OWN", "RPR", "C-VDP01", "BSP", 9)]
    for i, (erp, co, cfa, cu, loc, bx) in enumerate(plan):
        n = base[erp] + i
        inv_no = {"SAP": str(n), "MARG": f"MB/{today:%y}/{n}", "CBO": f"CBO/{cfa}/{n}", "INTRECT": f"INV{today:%y}-{n}"}[erp]
        recs.append(Rec(erp, co, cfa, cu, loc, inv_no, today, Decimal(bx * 1150 + i * 17), bx))
    names = {"SAP": "SAP_billing_documents_sample", "MARG": "MARG_sales_register_sample",
             "CBO": "CBO_invoice_export_sample", "INTRECT": "Intrect_invoice_export_sample"}
    for erp in names:
        rows = [_erp_row(r) for r in recs if r.erp == erp]
        if erp == "MARG":
            rows.append({"Bill No": f"MB/{today:%y}/29999", "Bill Date": today.strftime("%d-%m-%Y"), "Branch": "RAIPUR",
                         "Party Code": "NEW888", "Party Name": "SANJIVANI MEDICAL STORES", "Station": "RAIPUR",
                         "Net Amount": "18900.00", "Cases": 7, "Narration": "New party"})
        content, ext = _to_bytes(rows, FORMAT[erp])
        (SAMPLE_DIR / f"{names[erp]}.{ext}").write_bytes(content)
    # standard template
    std = [{"company_code": "COA", "cfa_code": "RPR", "invoice_no": f"STD-{today:%y%m%d}-001",
            "invoice_date": today.strftime("%d-%m-%Y"), "customer_code": "C-ABC01", "customer_name": "ABC Pharma",
            "location": "Bilaspur", "sales_value": "115000.00", "boxes": 36, "source_invoice_id": "", "remarks": ""},
           {"company_code": "COB", "cfa_code": "RPR", "invoice_no": f"STD-{today:%y%m%d}-002",
            "invoice_date": today.strftime("%d-%m-%Y"), "customer_code": "C-PQR01", "customer_name": "PQR Distributors",
            "location": "Nagpur", "sales_value": "88400.00", "boxes": 24, "source_invoice_id": "", "remarks": ""},
           {"company_code": "COC", "cfa_code": "RPR", "invoice_no": f"STD-{today:%y%m%d}-003",
            "invoice_date": today.strftime("%d-%m-%Y"), "customer_code": "C-SUN01", "customer_name": "Sunrise Agencies",
            "location": "Korba", "sales_value": "41250.00", "boxes": 15, "source_invoice_id": "",
            "remarks": "No Company C Korba rate -> RATE MISSING"}]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(std[0]))
    w.writeheader()
    w.writerows(std)
    (SAMPLE_DIR / "standard_invoice_template.csv").write_text(buf.getvalue(), encoding="utf-8")
    # freight rate upload template
    (SAMPLE_DIR / "freight_rates_template.csv").write_text(
        "company,cfa,customer,location,rate_per_box,effective_from,effective_to,is_default,reason\n"
        f"COC,RPR,,Korba,10.50,{today.replace(day=1):%d-%m-%Y},,,Korba rate for Company C\n"
        f"COA,RPR,C-MET01,Raipur,2.50,{today:%d-%m-%Y},,,Customer-specific local rate\n", encoding="utf-8")
    # transporter statement for this month's Vidarbha dispatches (with two variances)
    db = c.db
    invs = list(db.scalars(select(Invoice).where(Invoice.transporter_id == c.transporters["VDL"].id,
                                                 Invoice.status == InvoiceStatus.DISPATCHED,
                                                 Invoice.dispatch_date >= c.today.replace(day=1)).order_by(Invoice.dispatch_date)))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["LR No", "Invoice No", "Boxes", "Amount", "Remarks"])
    for i, inv in enumerate(invs):
        amt = inv.freight_amount + (Decimal("75.00") if i == 1 else Decimal("0"))
        bx = inv.boxes + (2 if i == 2 else 0)
        w.writerow([inv.lr_no, inv.invoice_no, bx, f"{amt:.2f}", "Loading charges" if i == 1 else ""])
    w.writerow(["VDL77777", "", 10, "1400.00", "Not in our dispatch records"])
    (SAMPLE_DIR / "transporter_bill_sample.csv").write_text(buf.getvalue(), encoding="utf-8")
