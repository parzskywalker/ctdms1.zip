"""The trainee inside their practice database: same username and role (permissions copied from the live role)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Role, User

DEMO_PREFIX = "demo."


def practice_username(real: User) -> str:
    # the practice database has its own demonstration users (demo.master, demo.checker ...) - never collide with them
    return real.username if not real.username.startswith(DEMO_PREFIX) else "trainee." + real.username


def _load(db: Session, uid: int) -> User:
    return db.get(User, uid, options=[selectinload(User.companies), selectinload(User.cfas)])


def practice_user(db: Session, real: User, act_as: str | None = None) -> User:
    """The user the screens act as while practising. `act_as` = a demonstration user of the practice database
    (e.g. demo.checker for the two-person rule) - it exists only there and cannot sign in anywhere."""
    if act_as and act_as.startswith(DEMO_PREFIX):
        u = db.scalar(select(User).where(User.username == act_as, User.is_active.is_(True)))
        if u:
            return _load(db, u.id)
    code = real.role.code if real.role else "VIEWER"
    role = db.scalar(select(Role).where(Role.code == code))
    live_perms = list(real.role.permissions or []) if real.role else []
    if role is None:
        role = Role(code=code, name=real.role.name if real.role else code, permissions=live_perms, is_system=False)
        db.add(role)
    elif sorted(role.permissions or []) != sorted(live_perms):
        role.permissions = live_perms  # practise with exactly the permissions the live role has
    u = db.scalar(select(User).where(User.username == practice_username(real)))
    if u is None:
        u = User(username=practice_username(real), full_name=real.full_name, password_hash="!practice-only",
                 role=role, is_active=True, all_companies=True, all_cfas=True, must_change_password=False)
        db.add(u)
    elif u.role_id != (role.id or 0):
        u.role = role
    if db.new or db.dirty:
        db.commit()
    return _load(db, u.id)


def demo_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(User.username.like(DEMO_PREFIX + "%"), User.is_active.is_(True))
                           .order_by(User.username)))
