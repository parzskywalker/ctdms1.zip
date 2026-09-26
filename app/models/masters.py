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


class Customer(Base, TimestampMixin):
    """Customer / Party master."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
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


class Transporter(Base, TimestampMixin):
    __tablename__ = "transporters"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
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
