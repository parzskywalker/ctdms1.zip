from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin


class RateStatus:
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    INACTIVE = "INACTIVE"
    ALL = [PENDING_APPROVAL, APPROVED, REJECTED, INACTIVE]


class RateLevel:
    CUSTOMER = "CUSTOMER"          # Customer + Company + CFA + Location
    CFA_LOCATION = "CFA_LOCATION"  # Company + CFA + Location
    LOCATION = "LOCATION"          # Company + Location (any CFA)
    DEFAULT = "DEFAULT"            # Explicitly configured default
    MANUAL = "MANUAL"              # Authorised manual override on an invoice
    ORDER = [CUSTOMER, CFA_LOCATION, LOCATION, DEFAULT]
    LABELS = {
        CUSTOMER: "1 · Customer-specific (Company+CFA+Customer+Location)",
        CFA_LOCATION: "2 · Company + CFA + Location",
        LOCATION: "3 · Company + Location (any CFA)",
        DEFAULT: "4 · Configured default",
        MANUAL: "Manual override",
    }


class FreightRate(Base, TimestampMixin):
    """Effective-dated freight rate per box.

    Approved rates are never edited in place: a change is a new version with a new
    Effective From date; approving it closes the previous version the day before.
    """

    __tablename__ = "freight_rates"
    __table_args__ = (
        Index("ix_rate_lookup", "company_id", "location_id", "cfa_id", "customer_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    cfa_id: Mapped[int | None] = mapped_column(ForeignKey("cfas.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    rate_per_box: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default=RateStatus.PENDING_APPROVAL, index=True)
    remarks: Mapped[str | None] = mapped_column(Text)
    change_reason: Mapped[str | None] = mapped_column(Text)
    previous_rate_id: Mapped[int | None] = mapped_column(ForeignKey("freight_rates.id"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    deactivated_reason: Mapped[str | None] = mapped_column(Text)

    company = relationship("Company")
    cfa = relationship("CFA")
    customer = relationship("Customer")
    location = relationship("Location")
    created_by = relationship("User", foreign_keys=[created_by_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])
    previous_rate = relationship("FreightRate", remote_side=[id])

    @property
    def level(self) -> str:
        if self.is_default:
            return RateLevel.DEFAULT
        if self.customer_id:
            return RateLevel.CUSTOMER
        if self.cfa_id:
            return RateLevel.CFA_LOCATION
        return RateLevel.LOCATION

    @property
    def key(self) -> tuple:
        return (self.company_id, self.cfa_id, self.customer_id, self.location_id, bool(self.is_default))

    def covers(self, d: date) -> bool:
        return self.effective_from <= d and (self.effective_to is None or self.effective_to >= d)
