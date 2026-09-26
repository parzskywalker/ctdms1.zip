"""Authentication: password hashing, server-side sessions, JWT for the REST API, dependencies."""
from __future__ import annotations

import hashlib
import secrets
import time
from datetime import timedelta

import bcrypt
import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.db import get_db
from app.models import User, UserSession, utcnow
from app.services import context


class LoginRequired(Exception):
    pass


class PermissionDenied(Exception):
    def __init__(self, perm: str | None = None, message: str | None = None):
        self.perm = perm
        self.message = message or (f"You do not have permission: {perm}" if perm else "Access denied")
        super().__init__(self.message)


# --------------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode())
    except (ValueError, TypeError):
        return False


def password_problems(password: str) -> list[str]:
    problems = []
    if len(password) < 8:
        problems.append("at least 8 characters")
    if not any(c.isdigit() for c in password):
        problems.append("at least one digit")
    if not any(c.isalpha() for c in password):
        problems.append("at least one letter")
    return problems


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------------- login
def authenticate(db: Session, username: str, password: str) -> tuple[User | None, str | None]:
    s = get_settings()
    user = db.scalar(select(User).where(User.username == username.strip().lower()))
    now = utcnow()
    if not user:
        verify_password(password, "$2b$12$" + "x" * 53)  # constant-ish time
        return None, "Invalid username or password"
    if not user.is_active:
        return None, "User is inactive"
    if user.locked_until and user.locked_until > now:
        return None, f"Account locked after repeated failures. Try again after {s.lockout_minutes} minutes."
    if not verify_password(password, user.password_hash):
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= s.max_failed_logins:
            user.locked_until = now + timedelta(minutes=s.lockout_minutes)
            user.failed_logins = 0
        db.commit()
        return None, "Invalid username or password"
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    db.commit()
    return user, None


def create_session(db: Session, user: User, ip: str | None, user_agent: str | None) -> str:
    token = secrets.token_urlsafe(32)
    db.add(UserSession(token_hash=_hash_token(token), user_id=user.id, ip=ip,
                       user_agent=(user_agent or "")[:250]))
    db.commit()
    return token


def revoke_session(db: Session, token: str | None) -> None:
    if not token:
        return
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == _hash_token(token)))
    if sess and not sess.revoked_at:
        sess.revoked_at = utcnow()
        db.commit()


def revoke_user_sessions(db: Session, user_id: int) -> None:
    for sess in db.scalars(select(UserSession).where(UserSession.user_id == user_id,
                                                     UserSession.revoked_at.is_(None))):
        sess.revoked_at = utcnow()


def _load_user(db: Session, user_id: int) -> User | None:
    """Load the user with role and data-scope eagerly, so templates/handlers can use them even after the
    request's DB session is closed (e.g. while rendering an error page)."""
    return db.get(User, user_id, options=[selectinload(User.companies), selectinload(User.cfas)])


def session_user(db: Session, token: str) -> tuple[User | None, UserSession | None]:
    s = get_settings()
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == _hash_token(token)))
    if not sess or sess.revoked_at:
        return None, None
    now = utcnow()
    if sess.last_seen_at and now - sess.last_seen_at > timedelta(minutes=s.session_idle_minutes):
        sess.revoked_at = now
        db.commit()
        return None, None
    user = _load_user(db, sess.user_id)
    if not user or not user.is_active:
        return None, None
    if not sess.last_seen_at or (now - sess.last_seen_at).total_seconds() > 60:
        sess.last_seen_at = now
        db.commit()
    return user, sess


# --------------------------------------------------------------------------- JWT (REST API)
def create_access_token(user: User) -> tuple[str, int]:
    s = get_settings()
    exp_minutes = s.jwt_expire_minutes
    payload = {
        "sub": str(user.id),
        "usr": user.username,
        "role": user.role.code if user.role else None,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_minutes * 60,
        "pwd": int(user.password_changed_at.timestamp()) if user.password_changed_at else 0,
    }
    return jwt.encode(payload, s.secret_key, algorithm="HS256"), exp_minutes * 60


def token_user(db: Session, token: str) -> User | None:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    user = _load_user(db, int(payload.get("sub", 0)))
    if not user or not user.is_active:
        return None
    pwd = int(user.password_changed_at.timestamp()) if user.password_changed_at else 0
    if payload.get("pwd", 0) != pwd:  # password changed -> old tokens invalid
        return None
    return user


# --------------------------------------------------------------------------- dependencies
def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    if getattr(request.state, "user_loaded", False):
        return request.state.user
    user = None
    token = request.session.get("sid") if "session" in request.scope else None
    if token:
        user, sess = session_user(db, token)
        if sess:
            context.session_ref.set(f"S{sess.id}")
        if not user:
            request.session.pop("sid", None)
    if not user:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            user = token_user(db, auth[7:].strip())
            if user:
                context.session_ref.set("JWT")
    request.state.user = user
    request.state.user_loaded = True
    return user


def current_user(user: User | None = Depends(current_user_optional)) -> User:
    if not user:
        raise LoginRequired()
    return user


def require(*perms: str):
    """Dependency factory: user must hold ALL listed permissions."""

    def _dep(user: User = Depends(current_user)) -> User:
        for p in perms:
            if not user.can(p):
                raise PermissionDenied(p)
        return user

    return _dep


def require_any(*perms: str):
    def _dep(user: User = Depends(current_user)) -> User:
        if not any(user.can(p) for p in perms):
            raise PermissionDenied(" or ".join(perms))
        return user

    return _dep


# --------------------------------------------------------------------------- CSRF
def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.session["csrf"] = tok
    return tok


async def csrf_protect(request: Request) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    expected = request.session.get("csrf")
    supplied = request.headers.get("x-csrf-token")
    if not supplied:
        ctype = request.headers.get("content-type", "")
        if "form" in ctype:
            form = await request.form()
            supplied = form.get("csrf_token")
    if not expected or not supplied or not secrets.compare_digest(str(expected), str(supplied)):
        raise PermissionDenied(message="Security token expired or invalid. Please reload the page and try again.")
