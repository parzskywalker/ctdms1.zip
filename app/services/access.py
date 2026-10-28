"""Access administration helpers (v1.7): effective permissions of a hypothetical user, the last-Administrator guard.

An "Administrator" here is not a role name (roles can be renamed and edited): it is any ACTIVE user whose effective
permissions include BOTH admin.users and admin.roles. At least one such user must always exist.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.security.permissions import PERMISSIONS
from app.services.errors import BusinessError

ADMIN_PERMS = ("admin.users", "admin.roles")


def effective_for(role_permissions: list[str] | None, grants: set[str], denies: set[str]) -> set[str]:
    """Role + grants - denies, for the preview of a user that is not saved yet (same rule as User.can)."""
    base = set(PERMISSIONS) if "*" in (role_permissions or []) else {p for p in (role_permissions or []) if p in PERMISSIONS}
    return (base | {g for g in grants if g in PERMISSIONS}) - denies


def administrators(db: Session) -> list[User]:
    return [u for u in db.scalars(select(User).where(User.is_active.is_(True)))
            if all(u.can(p) for p in ADMIN_PERMS)]


def ensure_admin_remains(db: Session) -> None:
    """Call after a role / user / override change has been applied in the session (flushed, not committed): refuses the
    change when it would leave nobody able to manage users and roles."""
    db.flush()
    db.expire_all()
    if not administrators(db):
        raise BusinessError("This change would leave no active user who can manage users and roles. "
                            "At least one Administrator must always remain.")
