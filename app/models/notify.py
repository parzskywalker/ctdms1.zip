from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import utcnow


class NotifyEvent:
    GATE_OUT = "GATE_OUT"        # the consignment left our gate
    LR_ENTERED = "LR_ENTERED"    # the transporter's LR / bilty number is known
    LABELS = {GATE_OUT: "Dispatched", LR_ENTERED: "LR number"}


class NotifyStatus:
    QUEUED = "QUEUED"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    ALL = [QUEUED, SENDING, SENT, FAILED]


class Notification(Base):
    """One message to one customer about one consignment (customer on a gatepass)."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notify_status_next", "status", "next_attempt_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    event: Mapped[str] = mapped_column(String(15))
    # the same event for the same consignment is queued only once
    dedupe_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    slip_id: Mapped[int | None] = mapped_column(ForeignKey("dispatch_slips.id"), index=True)
    cfa_id: Mapped[int | None] = mapped_column(ForeignKey("cfas.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    invoice_ids: Mapped[list] = mapped_column(JSON, default=list)
    channel: Mapped[str] = mapped_column(String(10))       # WHATSAPP / SMS / LOG
    to_number: Mapped[str | None] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default=NotifyStatus.QUEUED, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(String(20))
    provider_ref: Mapped[str | None] = mapped_column(String(120))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    slip = relationship("DispatchSlip")
    customer = relationship("Customer")
    created_by = relationship("User")

    @property
    def event_label(self) -> str:
        return NotifyEvent.LABELS.get(self.event, self.event)
