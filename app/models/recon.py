from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin


class BillStatus:
    PENDING = "PENDING"
    MATCHED = "MATCHED"
    VARIANCE = "VARIANCE"
    APPROVED = "APPROVED"
    PAID = "PAID"
    REJECTED = "REJECTED"
    ALL = [PENDING, MATCHED, VARIANCE, APPROVED, PAID, REJECTED]


class LineMatch:
    MATCHED = "MATCHED"
    VARIANCE = "VARIANCE"
    NOT_FOUND = "NOT_FOUND"
    NOT_DISPATCHED = "NOT_DISPATCHED"
    DUPLICATE_BILLING = "DUPLICATE_BILLING"
    OTHER_TRANSPORTER = "OTHER_TRANSPORTER"
    UNBILLED = "UNBILLED"  # linked dispatch with no billed amount entered yet


class TransporterBill(Base, TimestampMixin):
    __tablename__ = "transporter_bills"
    __table_args__ = (UniqueConstraint("transporter_id", "bill_no", name="uq_bill_transporter_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    transporter_id: Mapped[int] = mapped_column(ForeignKey("transporters.id"), index=True)
    bill_no: Mapped[str] = mapped_column(String(50))
    bill_date: Mapped[date] = mapped_column(Date)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    cfa_id: Mapped[int | None] = mapped_column(ForeignKey("cfas.id"))
    period_from: Mapped[date | None] = mapped_column(Date)
    period_to: Mapped[date | None] = mapped_column(Date)
    bill_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    calculated_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    variance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    status: Mapped[str] = mapped_column(String(12), default=BillStatus.PENDING, index=True)
    remarks: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    paid_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    payment_ref: Mapped[str | None] = mapped_column(String(80))

    transporter = relationship("Transporter")
    company = relationship("Company")
    cfa = relationship("CFA")
    created_by = relationship("User", foreign_keys=[created_by_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])
    paid_by = relationship("User", foreign_keys=[paid_by_id])
    lines: Mapped[list["TransporterBillLine"]] = relationship(
        back_populates="bill", cascade="all, delete-orphan", order_by="TransporterBillLine.id")

    @property
    def variance_pct(self) -> Decimal | None:
        if not self.calculated_amount:
            return None
        return (self.variance / self.calculated_amount * 100).quantize(Decimal("0.01"))


class TransporterBillLine(Base):
    __tablename__ = "transporter_bill_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("transporter_bills.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), index=True)
    lr_no: Mapped[str | None] = mapped_column(String(40))
    invoice_no: Mapped[str | None] = mapped_column(String(50))
    dispatch_no: Mapped[str | None] = mapped_column(String(40))
    billed_boxes: Mapped[int | None] = mapped_column(Integer)
    billed_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    calculated_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    variance: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    match_status: Mapped[str] = mapped_column(String(20), default=LineMatch.NOT_FOUND)
    remarks: Mapped[str | None] = mapped_column(Text)

    bill: Mapped[TransporterBill] = relationship(back_populates="lines")
    invoice = relationship("Invoice")
