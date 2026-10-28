from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Table, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin, utcnow

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
        return "*" in (self.permissions or []) or perm in (self.permissions or [])


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
    # v1.7 operational scope beyond company / depot: delivery areas (Location), customer types, customers.
    # Default True = unrestricted, so every user of an older installation keeps what they could see.
    all_areas: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    all_customer_types: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    all_customers: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    responsibility: Mapped[str | None] = mapped_column(String(120))  # e.g. "Guma dispatch desk" (a label, not a permission)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    tokens_valid_after: Mapped[datetime | None] = mapped_column(DateTime)  # API tokens issued earlier are refused

    role: Mapped[Role] = relationship(back_populates="users", lazy="joined")
    companies: Mapped[list["Company"]] = relationship(secondary=user_companies)  # noqa: F821
    cfas: Mapped[list["CFA"]] = relationship(secondary=user_cfas)  # noqa: F821
    # Loaded together with the user (selectin): can() and the scope helpers run on every request - also on a user whose
    # session has closed (error pages, Training Mode), where a lazy load would fail.
    permission_overrides: Mapped[list["UserPermission"]] = relationship(
        cascade="all, delete-orphan", back_populates="user", foreign_keys="UserPermission.user_id", lazy="selectin")
    scope_rows: Mapped[list["UserScope"]] = relationship(cascade="all, delete-orphan", back_populates="user",
                                                         lazy="selectin")

    def can(self, perm: str) -> bool:
        """Effective permission = the role's, plus this user's own GRANTs, minus this user's own DENYs (DENY wins)."""
        if not (self.is_active and self.role):
            return False
        for o in self.permission_overrides:
            if o.permission == perm:
                if o.effect == "DENY":
                    return False
                if o.effect == "GRANT":
                    return True
        return self.role.has(perm)

    def effective_permissions(self) -> set[str]:
        from app.security.permissions import PERMISSIONS
        return {p for p in PERMISSIONS if self.can(p)}

    def effective_permission_list(self) -> list[str]:
        """What a practice copy of this user's role must hold (Training Mode): ["*"] while nothing is denied."""
        if "*" in (self.role.permissions or []) and not any(o.effect == "DENY" for o in self.permission_overrides):
            return ["*"]
        return sorted(self.effective_permissions())

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

    # ---- v1.7 areas / customer types / customers (None = unrestricted)
    @property
    def area_ids(self) -> set[int] | None:
        return None if self.all_areas else {r.ref_id for r in self.scope_rows if r.dimension == "AREA"}

    @property
    def customer_type_codes(self) -> set[str] | None:
        return None if self.all_customer_types else {r.ref_code for r in self.scope_rows if r.dimension == "CUSTOMER_TYPE"}

    @property
    def customer_ids(self) -> set[int] | None:
        return None if self.all_customers else {r.ref_id for r in self.scope_rows if r.dimension == "CUSTOMER"}

    @property
    def scope_is_open(self) -> bool:
        """No area / customer-type / customer restriction at all (company and depot are separate)."""
        return self.all_areas and self.all_customer_types and self.all_customers


class UserPermission(Base):
    """An individual permission change for one user (v1.7): GRANT adds to the role, DENY takes away from it."""

    __tablename__ = "user_permissions"
    __table_args__ = (UniqueConstraint("user_id", "permission", name="uq_user_permission"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    permission: Mapped[str] = mapped_column(String(60))
    effect: Mapped[str] = mapped_column(String(5))  # GRANT / DENY
    reason: Mapped[str | None] = mapped_column(Text)
    granted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped["User"] = relationship(back_populates="permission_overrides", foreign_keys=[user_id])


class UserScope(Base):
    """One assigned area (Location id), customer type (code) or customer (id) of a user (v1.7). Only read while the
    matching all_* flag on the user is False."""

    __tablename__ = "user_scope"
    __table_args__ = (UniqueConstraint("user_id", "dimension", "ref_id", "ref_code", name="uq_user_scope"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    dimension: Mapped[str] = mapped_column(String(16))  # AREA / CUSTOMER_TYPE / CUSTOMER
    ref_id: Mapped[int | None] = mapped_column(Integer)      # AREA, CUSTOMER
    ref_code: Mapped[str | None] = mapped_column(String(40))  # CUSTOMER_TYPE (customer_types.code)

    user: Mapped["User"] = relationship(back_populates="scope_rows")


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
