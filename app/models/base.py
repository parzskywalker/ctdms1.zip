from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column


_FAKE_NOW: datetime | None = None


def set_fake_now(dt: datetime | None) -> None:
    """Freeze the application clock (UTC, naive). Used only by the demo seeder and tests."""
    global _FAKE_NOW
    _FAKE_NOW = dt


def utcnow() -> datetime:
    """Naive UTC timestamp (stored consistently across SQLite/PostgreSQL)."""
    if _FAKE_NOW is not None:
        return _FAKE_NOW
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
