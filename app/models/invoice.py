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


class DeliveryStatus:
    DELIVERED = "DELIVERED"
    SHORT = "SHORT"        # fewer cases received than dispatched
    DAMAGED = "DAMAGED"
    REFUSED = "REFUSED"    # customer refused / returned
    ALL = [DELIVERED, SHORT, DAMAGED, REFUSED]
    PROBLEM = [SHORT, DAMAGED, REFUSED]
    LABELS = {DELIVERED: "Delivered", SHORT: "Delivered - short", DAMAGED: "Delivered - damaged",
              REFUSED: "Refused / returned"}


class ExceptionCode:
    RATE_MISSING = "RATE_MISSING"
    RATE_CONFLICT = "RATE_CONFLICT"
    BOXES_MISSING = "BOXES_MISSING"
    OVERRIDE_PENDING = "OVERRIDE_PENDING"
    AREA_MISMATCH = "AREA_MISMATCH"      # v1.7: the file's area is not one of the customer's delivery locations
    AREA_AMBIGUOUS = "AREA_AMBIGUOUS"    # v1.7: the customer has several delivery locations - the operator picks
    LABELS = {
        RATE_MISSING: "No valid freight rate",
        RATE_CONFLICT: "More than one rate matches - fix rate master",
        BOXES_MISSING: "Number of boxes not received",
        OVERRIDE_PENDING: "Manual override awaiting approval",
        AREA_MISMATCH: "Delivery area differs from the customer's locations",
        AREA_AMBIGUOUS: "Customer has several delivery locations - choose one",
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
    # v1.7: which of the customer's delivery locations this invoice goes to (NULL = the customer's single / default one)
    customer_location_id: Mapped[int | None] = mapped_column(ForeignKey("customer_delivery_locations.id"))
    invoice_no: Mapped[str] = mapped_column(String(50), index=True)
    invoice_date: Mapped[date] = mapped_column(Date)
    fiscal_year: Mapped[str] = mapped_column(String(7))
    sales_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)  # invoice amount (incl. GST)
    taxable_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # before GST, when the source gives it

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
    lr_date: Mapped[date | None] = mapped_column(Date)
    lr_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    lr_updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    dispatch_date: Mapped[date | None] = mapped_column(Date)
    dispatch_time: Mapped[time | None] = mapped_column(Time)
    remarks: Mapped[str | None] = mapped_column(Text)
    dispatch_no: Mapped[str | None] = mapped_column(String(40), index=True)
    # current gatepass (plain column: dispatch_slips already references invoices, avoid a FK cycle)
    dispatch_slip_id: Mapped[int | None] = mapped_column(Integer, index=True)
    dispatch_mode: Mapped[str | None] = mapped_column(String(15))
    outside_auto_id: Mapped[int | None] = mapped_column(ForeignKey("outside_autos.id"))
    extra_charge: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # share of pickup / auto charges

    # Capture (how the invoice reached CTDMS) and the GST e-invoice reference
    capture_mode: Mapped[str] = mapped_column(String(10), default="IMPORT")  # IMPORT / QR_SCAN / API / MANUAL
    irn: Mapped[str | None] = mapped_column(String(70), index=True)
    item_count: Mapped[int | None] = mapped_column(Integer)
    qr_signature: Mapped[str | None] = mapped_column(String(12))  # VERIFIED / UNVERIFIED / INVALID (QR scans only)
    # v1.6 Invoice Recognition Code: an ADDITIONAL internal reference (company + customer type + customer + depot +
    # numeric part). The ERP invoice number above is never changed.
    recognition_code: Mapped[str | None] = mapped_column(String(100), index=True)
    serial_no: Mapped[str | None] = mapped_column(String(30))
    recognition_note: Mapped[str | None] = mapped_column(String(255))  # why no code could be made
    marking_id: Mapped[int | None] = mapped_column(ForeignKey("invoice_markings.id"))
    eway_bill_no: Mapped[str | None] = mapped_column(String(20), index=True)
    eway_valid_until: Mapped[date | None] = mapped_column(Date)

    # Proof of delivery (the consignment reached the customer)
    delivery_status: Mapped[str | None] = mapped_column(String(12), index=True)  # DeliveryStatus
    delivered_on: Mapped[date | None] = mapped_column(Date)
    received_by: Mapped[str | None] = mapped_column(String(120))
    pod_note: Mapped[str | None] = mapped_column(Text)
    pod_file: Mapped[str | None] = mapped_column(String(255))
    pod_source: Mapped[str | None] = mapped_column(String(10))  # STAFF / CUSTOMER
    pod_at: Mapped[datetime | None] = mapped_column(DateTime)
    pod_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

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
    customer_location = relationship("CustomerDeliveryLocation")
    transporter = relationship("Transporter")
    freight_rate = relationship("FreightRate")
    verified_by = relationship("User", foreign_keys=[verified_by_id])
    slip_generated_by = relationship("User", foreign_keys=[slip_generated_by_id])
    first_printed_by = relationship("User", foreign_keys=[first_printed_by_id])
    completed_by = relationship("User", foreign_keys=[completed_by_id])
    cancelled_by = relationship("User", foreign_keys=[cancelled_by_id])
    imported_by = relationship("User", foreign_keys=[imported_by_id])
    import_batch = relationship("ImportBatch", foreign_keys=[import_batch_id])
    lr_updated_by = relationship("User", foreign_keys=[lr_updated_by_id])
    pod_by = relationship("User", foreign_keys=[pod_by_id])
    outside_auto = relationship("OutsideAuto")
    marking = relationship("InvoiceMarking")
    dispatch_slip = relationship("DispatchSlip", primaryjoin="foreign(Invoice.dispatch_slip_id) == DispatchSlip.id",
                                 viewonly=True)
    items: Mapped[list["InvoiceItem"]] = relationship(back_populates="invoice", cascade="all, delete-orphan",
                                                      order_by="InvoiceItem.line_no")

    @property
    def total_charge(self) -> Decimal | None:
        if self.freight_amount is None and self.extra_charge is None:
            return None
        return (self.freight_amount or Decimal("0")) + (self.extra_charge or Decimal("0"))

    @property
    def delivery_label(self) -> str | None:
        return DeliveryStatus.LABELS.get(self.delivery_status) if self.delivery_status else None

    @property
    def transit_days(self) -> int | None:
        if self.delivered_on and self.dispatch_date:
            return max((self.delivered_on - self.dispatch_date).days, 0)
        return None

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


class InvoiceItem(Base):
    """SKU line of an invoice (from item-wise ERP exports) - what was dispatched, per company and SKU."""

    __tablename__ = "invoice_items"
    __table_args__ = (Index("ix_item_sku", "sku_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    sku_code: Mapped[str | None] = mapped_column(String(60))
    sku_name: Mapped[str | None] = mapped_column(String(200))
    hsn: Mapped[str | None] = mapped_column(String(12))
    batch_no: Mapped[str | None] = mapped_column(String(40))
    qty: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    free_qty: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    invoice: Mapped["Invoice"] = relationship(back_populates="items")


class DispatchMode:
    TRANSPORTER = "TRANSPORTER"    # booked with a transporter (we take the goods to them)
    DIRECT = "DIRECT"              # transporter picks up directly from our warehouse (direct-pickup extra applies)
    OUTSIDE_AUTO = "OUTSIDE_AUTO"  # local auto delivers straight to the customer (local)
    SELF = "SELF"                  # customer collects / own delivery - no freight
    ALL = [TRANSPORTER, DIRECT, OUTSIDE_AUTO, SELF]
    LABELS = {TRANSPORTER: "Transporter (long distance)", DIRECT: "Transporter - direct pickup",
              OUTSIDE_AUTO: "Local auto", SELF: "Self pickup"}


class PickupMode:
    REGULAR = "REGULAR"            # our regular arrangement to the transporter's booking office
    OUTSIDE_AUTO = "OUTSIDE_AUTO"  # a local auto carries the goods to the transporter (auto charge added)
    LABELS = {REGULAR: "Regular", OUTSIDE_AUTO: "Local auto"}


class DispatchSlip(Base):
    """Dispatch slip / gatepass: one pickup (transporter, outside auto or self) carrying one or more invoices.

    The snapshot is what gets printed (incl. reprints) and never changes. LR numbers are not part of it: they arrive
    later from the transporter and are kept (audited) on the invoices.
    """

    __tablename__ = "dispatch_slips"

    id: Mapped[int] = mapped_column(primary_key=True)
    dispatch_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    cfa_id: Mapped[int | None] = mapped_column(ForeignKey("cfas.id"), index=True)
    dispatch_mode: Mapped[str] = mapped_column(String(15), default=DispatchMode.TRANSPORTER)
    pickup_mode: Mapped[str] = mapped_column(String(15), default=PickupMode.REGULAR)
    transporter_id: Mapped[int | None] = mapped_column(ForeignKey("transporters.id"), index=True)
    outside_auto_id: Mapped[int | None] = mapped_column(ForeignKey("outside_autos.id"))
    auto_method: Mapped[str | None] = mapped_column(String(10))       # v1.6 local auto: PER_TRIP / PER_CASE
    trip_charge: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # v1.6: the one amount for a per-trip auto
    vehicle_no: Mapped[str | None] = mapped_column(String(20))
    dispatch_date: Mapped[date | None] = mapped_column(Date, index=True)
    invoice_count: Mapped[int] = mapped_column(Integer, default=0)
    total_cases: Mapped[int] = mapped_column(Integer, default=0)
    freight_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    pickup_charge: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    auto_charge: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    total_charge: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    payment_status: Mapped[str] = mapped_column(String(10), default="TBB")  # TBB / PAID / PARTIAL / NIL
    remarks: Mapped[str | None] = mapped_column(Text)
    gate_out_at: Mapped[datetime | None] = mapped_column(DateTime)
    gate_out_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    generated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    print_count: Mapped[int] = mapped_column(Integer, default=0)
    is_void: Mapped[bool] = mapped_column(Boolean, default=False)
    voided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime)
    void_reason: Mapped[str | None] = mapped_column(Text)
    # v1.7: invoices of different companies grouped on purpose (explicit choice, audited), and a transporter chosen
    # although it does not serve this depot (permission + reason, audited)
    cross_company: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    eligibility_override_reason: Mapped[str | None] = mapped_column(Text)

    invoice = relationship("Invoice", foreign_keys=[invoice_id])
    cfa = relationship("CFA")
    transporter = relationship("Transporter")
    outside_auto = relationship("OutsideAuto")
    generated_by = relationship("User", foreign_keys=[generated_by_id])
    voided_by = relationship("User", foreign_keys=[voided_by_id])
    gate_out_by = relationship("User", foreign_keys=[gate_out_by_id])
    lines: Mapped[list["DispatchSlipLine"]] = relationship(back_populates="slip", cascade="all, delete-orphan",
                                                           order_by="DispatchSlipLine.id")

    @property
    def mode_label(self) -> str:
        label = DispatchMode.LABELS.get(self.dispatch_mode, self.dispatch_mode)
        if self.pickup_mode == PickupMode.OUTSIDE_AUTO and self.dispatch_mode == DispatchMode.TRANSPORTER:
            label += " (via local auto)"
        if self.auto_method and self.outside_auto_id:
            label += " - per trip" if self.auto_method == "PER_TRIP" else " - per box"
        return label

    @property
    def balance(self) -> Decimal:
        return (self.total_charge or Decimal("0")) - (self.amount_paid or Decimal("0"))

    @property
    def state(self) -> str:
        if self.is_void:
            return "VOID"
        if self.gate_out_at:
            return "DISPATCHED"
        return "SLIP_PRINTED" if self.print_count else "SLIP_GENERATED"


class DispatchSlipLine(Base):
    """One invoice on a gatepass, with the case count, rate and charge frozen when the gatepass was generated."""

    __tablename__ = "dispatch_slip_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    slip_id: Mapped[int] = mapped_column(ForeignKey("dispatch_slips.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    cases: Mapped[int] = mapped_column(Integer, default=0)
    rate_per_case: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    freight: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    extra_charge: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    # v1.7: how the freight was found (rate row, rule, quantity, amount) and the consignment (customer + area) number
    rate_id: Mapped[int | None] = mapped_column(ForeignKey("transporter_rates.id"))
    calc_note: Mapped[dict | None] = mapped_column(JSON)
    consignment_no: Mapped[int | None] = mapped_column(Integer)

    slip: Mapped[DispatchSlip] = relationship(back_populates="lines")
    invoice = relationship("Invoice")
    customer = relationship("Customer")
    location = relationship("Location")


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
