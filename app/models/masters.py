from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Table, Text,
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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    erp_system: Mapped[ERPSystem | None] = relationship()
    cfas: Mapped[list["CFA"]] = relationship(secondary=company_cfas, back_populates="companies")


class CFA(Base, TimestampMixin):
    __tablename__ = "cfas"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
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


class Customer(Base, TimestampMixin):
    """Customer / Party master (stockist, retailer or hospital)."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    party_type: Mapped[str] = mapped_column(String(12), default=CustomerType.STOCKIST, index=True)
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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    location: Mapped[Location | None] = relationship()
    companies: Mapped[list[Company]] = relationship(secondary=customer_companies)
    cfas: Mapped[list[CFA]] = relationship(secondary=customer_cfas)

    @property
    def type_label(self) -> str:
        return CustomerType.LABELS.get(self.party_type, self.party_type or "")


class PaymentMode:
    CASH = "CASH"   # paid at dispatch, no bill expected
    TBB = "TBB"     # "to be billed": the transporter sends a periodic bill
    ALL = [TBB, CASH]
    LABELS = {CASH: "Cash at dispatch", TBB: "TBB (to be billed)"}


class ChargeBasis:
    PER_TRIP = "PER_TRIP"
    PER_CASE = "PER_CASE"
    ALL = [PER_TRIP, PER_CASE]
    LABELS = {PER_TRIP: "Per trip", PER_CASE: "Per case"}


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


class OutsideAuto(Base, TimestampMixin):
    """Hired auto / tempo that carries goods (to a transporter's booking office or straight to the customer)."""

    __tablename__ = "outside_autos"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))  # name of the person
    mobile: Mapped[str | None] = mapped_column(String(20))
    charge_basis: Mapped[str] = mapped_column(String(10), default=ChargeBasis.PER_TRIP)
    rate: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def charge_label(self) -> str:
        return f"₹{self.rate or 0:,.2f} {ChargeBasis.LABELS.get(self.charge_basis, '').lower()}"


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
