from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin, utcnow


class InvoiceStatus:
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    RATE_MISSING = "RATE_MISSING"
    EXCEPTION = "EXCEPTION"
    SLIP_GENERATED = "SLIP_GENERATED"
    SLIP_PRINTED = "SLIP_PRINTED"
    DISPATCHED = "DISPATCHED"
    CANCELLED = "CANCELLED"

    ALL = [PENDING, VERIFIED, RATE_MISSING, EXCEPTION, SLIP_GENERATED, SLIP_PRINTED, DISPATCHED, CANCELLED]
    OPEN = [PENDING, VERIFIED, RATE_MISSING, EXCEPTION, SLIP_GENERATED, SLIP_PRINTED]
    PRE_SLIP = [PENDING, VERIFIED, RATE_MISSING, EXCEPTION]
    LABELS = {
        PENDING: "Pending", VERIFIED: "Verified", RATE_MISSING: "Rate Missing", EXCEPTION: "Exception",
        SLIP_GENERATED: "Slip Generated", SLIP_PRINTED: "Slip Printed", DISPATCHED: "Dispatched",
        CANCELLED: "Cancelled",
    }


class ExceptionCode:
    RATE_MISSING = "RATE_MISSING"
    RATE_CONFLICT = "RATE_CONFLICT"
    BOXES_MISSING = "BOXES_MISSING"
    OVERRIDE_PENDING = "OVERRIDE_PENDING"
    LABELS = {
        RATE_MISSING: "No valid freight rate",
        RATE_CONFLICT: "More than one rate matches - fix rate master",
        BOXES_MISSING: "Number of boxes not received",
        OVERRIDE_PENDING: "Manual override awaiting approval",
    }


class Invoice(Base, TimestampMixin):
    """Normalized central invoice + its dispatch + transportation expense."""

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("company_id", "cfa_id", "fiscal_year", "invoice_no", name="uq_invoice_company_cfa_fy_no"),
        Index("ix_inv_status_date", "status", "invoice_date"),
        Index("ix_inv_company_cfa_date", "company_id", "cfa_id", "invoice_date"),
        Index("ix_inv_dispatch_date", "dispatch_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_erp_id: Mapped[int | None] = mapped_column(ForeignKey("erp_systems.id"))
    source_invoice_id: Mapped[str | None] = mapped_column(String(80))
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    cfa_id: Mapped[int] = mapped_column(ForeignKey("cfas.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    customer_code: Mapped[str | None] = mapped_column(String(60))  # code as received from the ERP
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), index=True)
    invoice_no: Mapped[str] = mapped_column(String(50), index=True)
    invoice_date: Mapped[date] = mapped_column(Date)
    fiscal_year: Mapped[str] = mapped_column(String(7))
    sales_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)

    boxes: Mapped[int | None] = mapped_column(Integer)
    boxes_source: Mapped[str] = mapped_column(String(12), default="ERP")  # ERP / MANUAL / OVERRIDE
    freight_rate_id: Mapped[int | None] = mapped_column(ForeignKey("freight_rates.id"))
    rate_per_box: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    rate_level: Mapped[str | None] = mapped_column(String(20))
    freight_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    freight_basis: Mapped[str] = mapped_column(String(15), default="AUTO")  # AUTO / MANUAL_RATE / MANUAL_AMOUNT
    freight_calculated_at: Mapped[datetime | None] = mapped_column(DateTime)

    status: Mapped[str] = mapped_column(String(20), default=InvoiceStatus.PENDING, index=True)
    exception_code: Mapped[str | None] = mapped_column(String(30))
    exception_reason: Mapped[str | None] = mapped_column(String(255))

    # Dispatch details
    transporter_id: Mapped[int | None] = mapped_column(ForeignKey("transporters.id"), index=True)
    vehicle_no: Mapped[str | None] = mapped_column(String(20))
    lr_no: Mapped[str | None] = mapped_column(String(40), index=True)
    dispatch_date: Mapped[date | None] = mapped_column(Date)
    dispatch_time: Mapped[time | None] = mapped_column(Time)
    remarks: Mapped[str | None] = mapped_column(Text)
    dispatch_no: Mapped[str | None] = mapped_column(String(40), index=True)

    verified_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    slip_generated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    slip_generated_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_printed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    first_printed_at: Mapped[datetime | None] = mapped_column(DateTime)
    print_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancel_reason: Mapped[str | None] = mapped_column(Text)

    # Reconciliation (denormalised from transporter bill lines)
    actual_freight: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    transporter_bill_id: Mapped[int | None] = mapped_column(ForeignKey("transporter_bills.id"))

    # Import / sync
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"))
    imported_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    imported_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    erp_sync_status: Mapped[str] = mapped_column(String(15), default="NOT_SYNCED")
    erp_synced_at: Mapped[datetime | None] = mapped_column(DateTime)

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": version}

    source_erp = relationship("ERPSystem")
    company = relationship("Company")
    cfa = relationship("CFA")
    customer = relationship("Customer")
    location = relationship("Location")
    transporter = relationship("Transporter")
    freight_rate = relationship("FreightRate")
    verified_by = relationship("User", foreign_keys=[verified_by_id])
    slip_generated_by = relationship("User", foreign_keys=[slip_generated_by_id])
    first_printed_by = relationship("User", foreign_keys=[first_printed_by_id])
    completed_by = relationship("User", foreign_keys=[completed_by_id])
    cancelled_by = relationship("User", foreign_keys=[cancelled_by_id])
    imported_by = relationship("User", foreign_keys=[imported_by_id])
    import_batch = relationship("ImportBatch", foreign_keys=[import_batch_id])

    @property
    def central_id(self) -> str:
        return f"CI{self.id:08d}"

    @property
    def status_label(self) -> str:
        return InvoiceStatus.LABELS.get(self.status, self.status)

    @property
    def age_days(self) -> int:
        from app.services.utils import local_today
        return max((local_today() - self.invoice_date).days, 0) if self.invoice_date else 0

    @property
    def variance(self) -> Decimal | None:
        if self.actual_freight is None or self.freight_amount is None:
            return None
        return self.actual_freight - self.freight_amount


class OverrideField:
    BOXES = "BOXES"
    RATE = "RATE"
    FREIGHT = "FREIGHT"
    LABELS = {BOXES: "Number of boxes", RATE: "Freight rate / box", FREIGHT: "Transportation amount"}


class OverrideStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"  # applied directly by an authorised user


class InvoiceOverride(Base):
    __tablename__ = "invoice_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(12))
    old_value: Mapped[str | None] = mapped_column(String(40))
    new_value: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default=OverrideStatus.PENDING, index=True)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decision_remarks: Mapped[str | None] = mapped_column(Text)
    is_initial_entry: Mapped[bool] = mapped_column(Boolean, default=False)

    invoice = relationship("Invoice")
    requested_by = relationship("User", foreign_keys=[requested_by_id])
    decided_by = relationship("User", foreign_keys=[decided_by_id])


class DispatchSlip(Base):
    """Immutable dispatch slip. The snapshot is what gets printed (incl. reprints)."""

    __tablename__ = "dispatch_slips"

    id: Mapped[int] = mapped_column(primary_key=True)
    dispatch_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    generated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    print_count: Mapped[int] = mapped_column(Integer, default=0)
    is_void: Mapped[bool] = mapped_column(Boolean, default=False)
    voided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime)
    void_reason: Mapped[str | None] = mapped_column(Text)

    invoice = relationship("Invoice")
    generated_by = relationship("User", foreign_keys=[generated_by_id])
    voided_by = relationship("User", foreign_keys=[voided_by_id])


class SlipPrintLog(Base):
    __tablename__ = "slip_print_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    slip_id: Mapped[int] = mapped_column(ForeignKey("dispatch_slips.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    dispatch_no: Mapped[str] = mapped_column(String(40))
    print_no: Mapped[int] = mapped_column(Integer)  # 1 = first print
    is_reprint: Mapped[bool] = mapped_column(Boolean, default=False)
    output: Mapped[str] = mapped_column(String(12))  # PRINT_A4 / PRINT_A5 / PDF_A4 / PDF_A5
    reason: Mapped[str | None] = mapped_column(Text)
    printed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    printed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    ip: Mapped[str | None] = mapped_column(String(64))

    slip = relationship("DispatchSlip")
    invoice = relationship("Invoice")
    printed_by = relationship("User")
