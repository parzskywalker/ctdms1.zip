from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import utcnow


class AuditLog(Base):
    """Append-only audit trail. One row per action or per changed field."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_record", "record_type", "record_id"),
        Index("ix_audit_ts", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    username: Mapped[str | None] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(40), index=True)
    module: Mapped[str] = mapped_column(String(40), index=True)
    record_type: Mapped[str | None] = mapped_column(String(40))
    record_id: Mapped[str | None] = mapped_column(String(40))
    ref: Mapped[str | None] = mapped_column(String(120))
    field: Mapped[str | None] = mapped_column(String(60))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    session_ref: Mapped[str | None] = mapped_column(String(20))
    details: Mapped[dict | None] = mapped_column(JSON)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    value_type: Mapped[str] = mapped_column(String(10), default="str")  # str/int/bool/decimal/choice
    choices: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40), default="General")
    description: Mapped[str | None] = mapped_column(Text)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Sequence(Base):
    __tablename__ = "sequences"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, default=1)


class IntegrationRun(Base):
    __tablename__ = "integration_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    erp_system_id: Mapped[int] = mapped_column(ForeignKey("erp_systems.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(12), default="RUNNING")  # RUNNING/SUCCESS/PARTIAL/FAILED
    trigger: Mapped[str] = mapped_column(String(12), default="MANUAL")  # MANUAL / SCHEDULED / PUSH
    triggered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    imported: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"))
    message: Mapped[str | None] = mapped_column(Text)

    erp_system = relationship("ERPSystem")
    triggered_by = relationship("User")
