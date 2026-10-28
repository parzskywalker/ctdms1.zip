"""Allocations, Garage Pawati slips and LR books (v1.7)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import utcnow


class DispatchAllocation(Base):
    """The part of an invoice line carried to one receiving transporter - the unit of freight, LR and Pawati."""

    __tablename__ = "dispatch_allocations"
    __table_args__ = (UniqueConstraint("line_id", "receiving_transporter_id", name="uq_alloc_line_receiver"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slip_id: Mapped[int] = mapped_column(ForeignKey("dispatch_slips.id"), index=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("dispatch_slip_lines.id"))
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    receiving_transporter_id: Mapped[int] = mapped_column(ForeignKey("transporters.id"))
    cases: Mapped[int] = mapped_column(Integer)
    rate_per_case: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    freight: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    lr_no: Mapped[str | None] = mapped_column(String(40))
    lr_date: Mapped[date | None] = mapped_column(Date)
    calc_note: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    invoice = relationship("Invoice")
    transporter = relationship("Transporter")
    line = relationship("DispatchSlipLine")


class PawatiStatus:
    GENERATED = "GENERATED"
    PRINTED = "PRINTED"
    CANCELLED = "CANCELLED"
    LABELS = {GENERATED: "Not printed", PRINTED: "Printed", CANCELLED: "Cancelled"}


class GaragePawati(Base):
    """One handover slip per receiving transporter x customer x company on a local-auto trip (stays with the transporter)."""

    __tablename__ = "garage_pawatis"
    __table_args__ = (UniqueConstraint("cfa_id", "pawati_no", name="uq_pawati_depot_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cfa_id: Mapped[int] = mapped_column(ForeignKey("cfas.id"))
    pawati_no: Mapped[int] = mapped_column(Integer)
    slip_id: Mapped[int] = mapped_column(ForeignKey("dispatch_slips.id"), index=True)
    receiving_transporter_id: Mapped[int] = mapped_column(ForeignKey("transporters.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    status: Mapped[str] = mapped_column(String(10), default=PawatiStatus.GENERATED)
    print_count: Mapped[int] = mapped_column(Integer, default=0)
    cartons: Mapped[int] = mapped_column(Integer, default=0)
    bill_nos: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    freight: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    snapshot: Mapped[dict | None] = mapped_column(JSON)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("garage_pawatis.id"))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    cancelled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    received_at: Mapped[datetime | None] = mapped_column(DateTime)
    received_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    received_via: Mapped[str | None] = mapped_column(String(8))  # LR / MANUAL
    received_note: Mapped[str | None] = mapped_column(Text)
    generated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    cfa = relationship("CFA")
    slip = relationship("DispatchSlip")
    transporter = relationship("Transporter")
    customer = relationship("Customer")
    location = relationship("Location")
    company = relationship("Company")
    received_by = relationship("User", foreign_keys=[received_by_id])
    lines = relationship("GaragePawatiLine", cascade="all, delete-orphan")


class GaragePawatiLine(Base):
    __tablename__ = "garage_pawati_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    pawati_id: Mapped[int] = mapped_column(ForeignKey("garage_pawatis.id"), index=True)
    allocation_id: Mapped[int] = mapped_column(ForeignKey("dispatch_allocations.id"))
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    cases: Mapped[int] = mapped_column(Integer)

    invoice = relationship("Invoice")


class PawatiPrintLog(Base):
    __tablename__ = "pawati_print_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    pawati_id: Mapped[int] = mapped_column(ForeignKey("garage_pawatis.id"), index=True)
    print_no: Mapped[int] = mapped_column(Integer)
    is_reprint: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(Text)
    printed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    printed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    printed_by = relationship("User")


class LRBook(Base):
    __tablename__ = "lr_books"

    id: Mapped[int] = mapped_column(primary_key=True)
    transporter_id: Mapped[int] = mapped_column(ForeignKey("transporters.id"), index=True)
    label: Mapped[str] = mapped_column(String(80))
    first_no: Mapped[str] = mapped_column(String(40))
    last_no: Mapped[str] = mapped_column(String(40))
    count: Mapped[int] = mapped_column(Integer)
    received_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(8), default="ACTIVE")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    transporter = relationship("Transporter")


class LRNumber(Base):
    __tablename__ = "lr_numbers"
    __table_args__ = (UniqueConstraint("transporter_id", "lr_no", name="uq_lr_number"),
                      Index("ix_lr_numbers_book_status", "book_id", "status"))

    id: Mapped[int] = mapped_column(primary_key=True)
    transporter_id: Mapped[int] = mapped_column(ForeignKey("transporters.id"))
    lr_no: Mapped[str] = mapped_column(String(40))
    book_id: Mapped[int] = mapped_column(ForeignKey("lr_books.id"))
    status: Mapped[str] = mapped_column(String(10), default="AVAILABLE")  # AVAILABLE / USED / VOID
    allocation_id: Mapped[int | None] = mapped_column(Integer)
    void_reason: Mapped[str | None] = mapped_column(Text)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
