from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin, utcnow
from app.security.permissions import baseline_allows

user_companies = Table(
    "user_companies",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("company_id", ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
)

user_cfas = Table(
    "user_cfas",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("cfa_id", ForeignKey("cfas.id", ondelete="CASCADE"), primary_key=True),
)


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)

    users: Mapped[list["User"]] = relationship(back_populates="role")

    def has(self, perm: str) -> bool:
        if not ("*" in (self.permissions or []) or perm in (self.permissions or [])):
            return False
        # fixed baseline: Master Settings / administration / Final Reports are decided by the role, not by the list
        return baseline_allows(self.code, perm)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(160))
    mobile: Mapped[str | None] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(200))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    all_companies: Mapped[bool] = mapped_column(Boolean, default=False)
    all_cfas: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    tokens_valid_after: Mapped[datetime | None] = mapped_column(DateTime)  # API tokens issued earlier are refused

    role: Mapped[Role] = relationship(back_populates="users", lazy="joined")
    companies: Mapped[list["Company"]] = relationship(secondary=user_companies)  # noqa: F821
    cfas: Mapped[list["CFA"]] = relationship(secondary=user_cfas)  # noqa: F821

    def can(self, perm: str) -> bool:
        return bool(self.is_active and self.role and self.role.has(perm))

    @property
    def company_ids(self) -> set[int] | None:
        """None means unrestricted."""
        return None if self.all_companies else {c.id for c in self.companies}

    # Request-time only (never stored): the depot a multi-depot user has switched to, set from the session by
    # app.security.auth. While it is set every list, search, report, export and API call of that request is limited to
    # that one depot - cfa_ids is the single place all scope checks read.
    _active_cfa = None

    @property
    def active_cfa(self) -> int | None:
        if self.all_cfas or self._active_cfa not in {c.id for c in self.cfas}:
            return None
        return self._active_cfa

    @property
    def cfa_ids(self) -> set[int] | None:
        if self.all_cfas:
            return None
        ids = {c.id for c in self.cfas}
        act = self._active_cfa
        return {act} if act in ids else ids


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)

    user: Mapped[User] = relationship()
