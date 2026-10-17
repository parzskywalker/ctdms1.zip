from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Table, Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin

company_cfas = Table(
    "company_cfas",
    Base.metadata,
    Column("company_id", ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
    Column("cfa_id", ForeignKey("cfas.id", ondelete="CASCADE"), primary_key=True),
)

customer_companies = Table(
    "customer_companies",
    Base.metadata,
    Column("customer_id", ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True),
    Column("company_id", ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
)

customer_cfas = Table(
    "customer_cfas",
    Base.metadata,
    Column("customer_id", ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True),
    Column("cfa_id", ForeignKey("cfas.id", ondelete="CASCADE"), primary_key=True),
)


class ERPSystem(Base, TimestampMixin):
    """A source billing / ERP system (MARG, SAP, CBO, Intrect, ...)."""

    __tablename__ = "erp_systems"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    adapter: Mapped[str] = mapped_column(String(40), default="file")  # key in integrations registry
    integration_mode: Mapped[str] = mapped_column(String(20), default="FILE")  # API/DB/ODATA/IDOC/FILE
    import_format: Mapped[str] = mapped_column(String(20), default="CSV/XLSX")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    api_key_hash: Mapped[str | None] = mapped_column(String(64))
    api_key_hint: Mapped[str | None] = mapped_column(String(12))
    auto_sync: Mapped[bool] = mapped_column(Boolean, default=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Company(Base, TimestampMixin):
    """Company / Principal."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    erp_system_id: Mapped[int | None] = mapped_column(ForeignKey("erp_systems.id"))
    relationship_type: Mapped[str] = mapped_column(String(20), default="PRINCIPAL")  # PRINCIPAL / OWN
    gstin: Mapped[str | None] = mapped_column(String(20))
    contact_person: Mapped[str | None] = mapped_column(String(100))
    contact_phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(160))
    address: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    # v1.6: short marking used in the Invoice Recognition Code (blank = the company code is used)
    marking: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    erp_system: Mapped[ERPSystem | None] = relationship()
    cfas: Mapped[list["CFA"]] = relationship(secondary=company_cfas, back_populates="companies")


class CFA(Base, TimestampMixin):
    """DEPOT (v1.6): the physical godown / location goods are dispatched from. Several depots may be in one city
    (Raipur: Guma, Kamal Vihar ...); each has its own unique code. The table keeps its v1.0 name `cfas`."""

    __tablename__ = "cfas"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    locality: Mapped[str | None] = mapped_column(String(120))  # v1.6: area / locality inside the city
    marking: Mapped[str | None] = mapped_column(String(20))    # v1.6: Invoice Recognition Code part
    warehouse: Mapped[str | None] = mapped_column(String(150))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(80))
    pincode: Mapped[str | None] = mapped_column(String(10))
    gstin: Mapped[str | None] = mapped_column(String(20))
    contact_person: Mapped[str | None] = mapped_column(String(100))
    contact_phone: Mapped[str | None] = mapped_column(String(30))
    default_erp_id: Mapped[int | None] = mapped_column(ForeignKey("erp_systems.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    default_erp: Mapped[ERPSystem | None] = relationship()
    companies: Mapped[list[Company]] = relationship(secondary=company_cfas, back_populates="cfas")


class Location(Base, TimestampMixin):
    """Delivery location / route master."""

    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    city: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(80))
    pincode: Mapped[str | None] = mapped_column(String(10))
    route: Mapped[str | None] = mapped_column(String(80))
    zone: Mapped[str | None] = mapped_column(String(80))
    distance_km: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CustomerType:
    STOCKIST = "STOCKIST"
    RETAILER = "RETAILER"
    HOSPITAL = "HOSPITAL"
    ALL = [STOCKIST, RETAILER, HOSPITAL]
    LABELS = {STOCKIST: "Stockist", RETAILER: "Retailer", HOSPITAL: "Hospital"}
    # v1.6: the list lives in the customer_types master; these are seeded into a new installation (all editable)
    DEFAULTS = [("RETAILER", "Retailer", "RET"), ("STOCKIST", "Stockist", "STK"), ("DISTRIBUTOR", "Distributor", "DST"),
                ("HOSPITAL", "Hospital", "HSP"), ("INSTITUTION", "Institution", "INS"),
                ("CHAIN_PHARMACY", "Chain Pharmacy", "CHN"), ("OTHER", "Other", "OTH")]


class CustomerTypeMaster(Base, TimestampMixin):
    """Configurable customer type (v1.6). `code` is the key stored on the customer; `marking` is the short code
    used in the Invoice Recognition Code (e.g. RET)."""

    __tablename__ = "customer_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    marking: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(String(200))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Customer(Base, TimestampMixin):
    """Customer / Party master (stockist, retailer or hospital)."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    party_type: Mapped[str] = mapped_column(String(30), default=CustomerType.STOCKIST, index=True)
    whatsapp: Mapped[str | None] = mapped_column(String(20))
    billing_address: Mapped[str | None] = mapped_column(Text)
    delivery_address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(80))
    pincode: Mapped[str | None] = mapped_column(String(10))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    contact_person: Mapped[str | None] = mapped_column(String(100))
    contact_phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(160))
    gstin: Mapped[str | None] = mapped_column(String(20))
    drug_license_no: Mapped[str | None] = mapped_column(String(60))
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    # v1.6: Invoice Recognition Code part (blank = the customer code) and the ERP the customer came from
    marking: Mapped[str | None] = mapped_column(String(20))
    source_erp_id: Mapped[int | None] = mapped_column(ForeignKey("erp_systems.id"))
    source_erp_code: Mapped[str | None] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    location: Mapped[Location | None] = relationship()
    companies: Mapped[list[Company]] = relationship(secondary=customer_companies)
    cfas: Mapped[list[CFA]] = relationship(secondary=customer_cfas)
    source_erp: Mapped[ERPSystem | None] = relationship()
    customer_type: Mapped[CustomerTypeMaster | None] = relationship(
        primaryjoin="foreign(Customer.party_type) == CustomerTypeMaster.code", viewonly=True)

    @property
    def type_label(self) -> str:
        if self.customer_type is not None:
            return self.customer_type.name
        return CustomerType.LABELS.get(self.party_type, (self.party_type or "").replace("_", " ").title())


class PaymentMode:
    CASH = "CASH"   # paid at dispatch, no bill expected
    TBB = "TBB"     # "to be billed": the transporter sends a periodic bill
    ALL = [TBB, CASH]
    LABELS = {CASH: "Cash at dispatch", TBB: "TBB (to be billed)"}


class ChargeBasis:
    PER_TRIP = "PER_TRIP"
    PER_CASE = "PER_CASE"   # shown as "Per box" for local autos
    BOTH = "BOTH"           # local auto: either one, chosen on the gatepass
    ALL = [PER_TRIP, PER_CASE]
    LABELS = {PER_TRIP: "Per trip", PER_CASE: "Per case"}
    AUTO_LABELS = {PER_TRIP: "Per trip", PER_CASE: "Per box", BOTH: "Per trip or per box"}


class Transporter(Base, TimestampMixin):
    __tablename__ = "transporters"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    payment_mode: Mapped[str] = mapped_column(String(10), default=PaymentMode.TBB)
    direct_pickup_charge: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    direct_pickup_basis: Mapped[str] = mapped_column(String(10), default=ChargeBasis.PER_TRIP)
    contact_person: Mapped[str | None] = mapped_column(String(100))
    mobile: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(160))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(80))
    gstin: Mapped[str | None] = mapped_column(String(20))
    pan: Mapped[str | None] = mapped_column(String(12))
    service_area: Mapped[str | None] = mapped_column(String(200))
    payment_terms: Mapped[str | None] = mapped_column(String(120))
    credit_days: Mapped[int | None] = mapped_column(Integer)
    bank_details: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def payment_label(self) -> str:
        return PaymentMode.LABELS.get(self.payment_mode, self.payment_mode or "")


def _legacy_rate(ctx, basis: str):
    p = ctx.get_current_parameters()
    if (p.get("charge_basis") or ChargeBasis.PER_TRIP) == basis and p.get("rate"):
        return p.get("rate")
    return None


class OutsideAuto(Base, TimestampMixin):
    """LOCAL AUTO transporter (v1.6; "outside auto" before): hired auto / tempo / local delivery vehicle.

    Charging (v1.6): `allowed_methods` PER_TRIP, PER_CASE (per box) or BOTH. Per trip = one amount for the whole trip,
    entered or confirmed on the gatepass (`default_trip_charge` is only the suggestion). Per box = boxes x rate, the
    rate from Local Auto Box Rates (auto / location / company / depot / date) or `default_box_rate`.
    `charge_basis` is the default method; `rate` is the v1.5 single rate, kept in step for older screens."""

    __tablename__ = "outside_autos"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))  # name of the person
    mobile: Mapped[str | None] = mapped_column(String(20))
    vehicle_no: Mapped[str | None] = mapped_column(String(20))
    charge_basis: Mapped[str] = mapped_column(String(10), default=ChargeBasis.PER_TRIP)
    # defaults derived from the v1.5 fields, so records created the old way (charge_basis + rate) keep working
    allowed_methods: Mapped[str] = mapped_column(
        String(10), default=lambda ctx: ctx.get_current_parameters().get("charge_basis") or ChargeBasis.PER_TRIP)
    default_trip_charge: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=lambda ctx: _legacy_rate(
        ctx, ChargeBasis.PER_TRIP))
    default_box_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=lambda ctx: _legacy_rate(
        ctx, ChargeBasis.PER_CASE))
    rate: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def methods(self) -> list[str]:
        if self.allowed_methods == ChargeBasis.BOTH:
            return [ChargeBasis.PER_TRIP, ChargeBasis.PER_CASE]
        if self.allowed_methods in ChargeBasis.ALL:
            return [self.allowed_methods]
        return [self.charge_basis or ChargeBasis.PER_TRIP]

    @property
    def default_method(self) -> str:
        return self.charge_basis if self.charge_basis in self.methods else self.methods[0]

    @property
    def charge_label(self) -> str:
        parts = []
        if ChargeBasis.PER_TRIP in self.methods:
            parts.append(f"per trip (usually ₹{self.default_trip_charge:,.0f})" if self.default_trip_charge else "per trip")
        if ChargeBasis.PER_CASE in self.methods:
            parts.append(f"per box (₹{self.default_box_rate:,.2f})" if self.default_box_rate else "per box")
        return " or ".join(parts)

    def sync_legacy(self) -> None:
        """Keep the v1.5 fields (charge_basis, rate) in step with the v1.6 ones."""
        if self.allowed_methods not in (ChargeBasis.PER_TRIP, ChargeBasis.PER_CASE, ChargeBasis.BOTH):
            self.allowed_methods = self.charge_basis or ChargeBasis.PER_TRIP
        if self.charge_basis not in self.methods:
            self.charge_basis = self.methods[0]
        cur = self.default_trip_charge if self.charge_basis == ChargeBasis.PER_TRIP else self.default_box_rate
        self.rate = cur or Decimal("0")


class LocalAutoRate(Base, TimestampMixin):
    """Per-box rate of a local auto (v1.6). Location / company / depot are optional: blank matches all; the most
    specific active rate on the dispatch date wins."""

    __tablename__ = "local_auto_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    outside_auto_id: Mapped[int] = mapped_column(ForeignKey("outside_autos.id"), index=True)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    cfa_id: Mapped[int | None] = mapped_column(ForeignKey("cfas.id"))
    rate_per_box: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    remarks: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    outside_auto: Mapped[OutsideAuto] = relationship()
    location: Mapped[Location | None] = relationship()
    company: Mapped[Company | None] = relationship()
    cfa: Mapped[CFA | None] = relationship()

    @property
    def name(self) -> str:
        return f"{self.outside_auto.name if self.outside_auto else ''} ₹{self.rate_per_box}/box"


class InvoiceMarking(Base, TimestampMixin):
    """Existing company / invoice marking inside source-ERP invoice numbers (v1.6), e.g. 'MB/' in MARG = Company B.
    Used to recognise the company (and optionally the depot / customer type) of an incoming invoice."""

    __tablename__ = "invoice_markings"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    erp_system_id: Mapped[int | None] = mapped_column(ForeignKey("erp_systems.id"))
    marking: Mapped[str] = mapped_column(String(40))
    match_type: Mapped[str] = mapped_column(String(10), default="PREFIX")  # PREFIX / CONTAINS
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    cfa_id: Mapped[int | None] = mapped_column(ForeignKey("cfas.id"))
    customer_type: Mapped[str | None] = mapped_column(String(30))
    notes: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    erp_system: Mapped[ERPSystem | None] = relationship()
    company: Mapped[Company] = relationship()
    cfa: Mapped[CFA | None] = relationship()

    @property
    def name(self) -> str:
        return f"{self.marking} = {self.company.code if self.company else ''}"

    def matches(self, invoice_no: str | None) -> bool:
        no = (invoice_no or "").strip().upper()
        mk = (self.marking or "").strip().upper()
        if not no or not mk:
            return False
        return no.startswith(mk) if self.match_type != "CONTAINS" else mk in no


class ERPCodeMapping(Base, TimestampMixin):
    """Maps a code used inside a source ERP to a central master record.

    This is how the same customer (or location/company/CFA) coming from different
    ERPs with different codes is unified into one central record.
    """

    __tablename__ = "erp_code_mappings"
    __table_args__ = (
        Index("ix_erp_map_lookup", "erp_system_id", "entity_type", "source_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    erp_system_id: Mapped[int] = mapped_column(ForeignKey("erp_systems.id"))
    entity_type: Mapped[str] = mapped_column(String(20))  # COMPANY/CFA/CUSTOMER/LOCATION/TRANSPORTER
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))  # optional scope
    source_code: Mapped[str] = mapped_column(String(80))
    source_name: Mapped[str | None] = mapped_column(String(160))
    target_id: Mapped[int] = mapped_column(Integer)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    erp_system: Mapped[ERPSystem] = relationship()
    company: Mapped[Company | None] = relationship()


class ERPFieldMapping(Base, TimestampMixin):
    """Invoice-field mapping per ERP (source field -> central field) used by adapters/API pushes."""

    __tablename__ = "erp_field_mappings"

    id: Mapped[int] = mapped_column(primary_key=True)
    erp_system_id: Mapped[int] = mapped_column(ForeignKey("erp_systems.id"), index=True)
    target_field: Mapped[str] = mapped_column(String(40))
    source_field: Mapped[str] = mapped_column(String(120))
    transform: Mapped[str | None] = mapped_column(String(40))  # e.g. upper, strip, date:%d.%m.%Y
    default_value: Mapped[str | None] = mapped_column(String(120))
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
