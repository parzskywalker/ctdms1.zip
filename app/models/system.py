from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
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


class ReportSubscription(Base):
    """A report e-mailed on a schedule (daily / weekly / monthly) as an Excel attachment."""

    __tablename__ = "report_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    report_code: Mapped[str] = mapped_column(String(40))
    frequency: Mapped[str] = mapped_column(String(10))  # DAILY / WEEKLY / MONTHLY
    params: Mapped[dict | None] = mapped_column(JSON)    # report filters other than the date range
    recipients: Mapped[str] = mapped_column(Text)        # comma separated e-mail addresses
    fmt: Mapped[str] = mapped_column(String(5), default="xlsx")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))  # the report runs with this user's data scope
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    owner = relationship("User")


class MasterUpload(Base):
    """v1.6 one-time master workbook upload: validated preview, then import; kept as history."""

    __tablename__ = "master_uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_name: Mapped[str] = mapped_column(String(200))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(12), default="PREVIEW")  # PREVIEW / IMPORTED / CANCELLED
    counts: Mapped[dict] = mapped_column(JSON, default=dict)            # per sheet: rows / new / existing / rejected
    rows: Mapped[list] = mapped_column(JSON, default=list)              # every row with its verdict and messages
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime)
    committed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    file_path: Mapped[str | None] = mapped_column(String(255))

    uploaded_by = relationship("User", foreign_keys=[uploaded_by_id])
    committed_by = relationship("User", foreign_keys=[committed_by_id])


class TrainingProgress(Base):
    """v1.6 Guided Training: one row per user, kept in the LIVE database (the practice data itself lives in the
    trainee's own practice database, see app.training.runtime)."""

    __tablename__ = "training_progress"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    path: Mapped[str] = mapped_column(String(20))                       # MASTER / DISPATCH / DEPOT / CHECKER / ...
    required: Mapped[bool] = mapped_column(Boolean, default=False)      # must finish before live work (when ON)
    status: Mapped[str] = mapped_column(String(12), default="NOT_STARTED")  # NOT_STARTED / IN_PROGRESS / COMPLETED
    current_lesson: Mapped[str | None] = mapped_column(String(40))
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    lessons: Mapped[dict] = mapped_column(JSON, default=dict)           # code -> {status, started, done, checks{}}
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    assigned_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reset_at: Mapped[datetime | None] = mapped_column(DateTime)
    reset_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    user = relationship("User", foreign_keys=[user_id])
    assigned_by = relationship("User", foreign_keys=[assigned_by_id])
