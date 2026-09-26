from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin


class ImportTemplate(Base, TimestampMixin):
    """Reusable column mapping for an ERP export format."""

    __tablename__ = "import_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    erp_system_id: Mapped[int | None] = mapped_column(ForeignKey("erp_systems.id"))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    cfa_id: Mapped[int | None] = mapped_column(ForeignKey("cfas.id"))
    column_map: Mapped[dict] = mapped_column(JSON, default=dict)  # target_field -> source header
    date_format: Mapped[str] = mapped_column(String(20), default="AUTO")
    header_row: Mapped[int] = mapped_column(Integer, default=1)
    sheet_name: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    erp_system = relationship("ERPSystem")
    company = relationship("Company")
    cfa = relationship("CFA")


class BatchStatus:
    UPLOADED = "UPLOADED"
    VALIDATED = "VALIDATED"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RowStatus:
    NEW = "NEW"
    VALID = "VALID"
    WARNING = "WARNING"      # will import but land as an exception (e.g. rate missing)
    IMPORTED = "IMPORTED"
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"
    SKIPPED = "SKIPPED"


class ImportBatch(Base, TimestampMixin):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(12), default="FILE")  # FILE / API / ADAPTER
    erp_system_id: Mapped[int | None] = mapped_column(ForeignKey("erp_systems.id"))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))  # default if not in file
    cfa_id: Mapped[int | None] = mapped_column(ForeignKey("cfas.id"))
    template_id: Mapped[int | None] = mapped_column(ForeignKey("import_templates.id"))
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    file_type: Mapped[str | None] = mapped_column(String(10))
    sheet_name: Mapped[str | None] = mapped_column(String(80))
    headers: Mapped[list] = mapped_column(JSON, default=list)
    column_map: Mapped[dict] = mapped_column(JSON, default=dict)
    date_format: Mapped[str] = mapped_column(String(20), default="AUTO")
    status: Mapped[str] = mapped_column(String(12), default=BatchStatus.UPLOADED, index=True)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    exception_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)

    erp_system = relationship("ERPSystem")
    company = relationship("Company")
    cfa = relationship("CFA")
    template = relationship("ImportTemplate")
    created_by = relationship("User")
    rows: Mapped[list["ImportRow"]] = relationship(back_populates="batch", order_by="ImportRow.row_no",
                                                   cascade="all, delete-orphan")


class ImportRow(Base):
    __tablename__ = "import_rows"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), index=True)
    row_no: Mapped[int] = mapped_column(Integer)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # mapped (and user-corrected) values
    status: Mapped[str] = mapped_column(String(12), default=RowStatus.NEW, index=True)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"))
    corrected: Mapped[bool] = mapped_column(Boolean, default=False)

    batch: Mapped[ImportBatch] = relationship(back_populates="rows")
