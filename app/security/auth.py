"""Authentication: password hashing, server-side sessions, JWT for the REST API, dependencies."""
from __future__ import annotations

import hashlib
import secrets
import time
from datetime import timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, Request
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import set_committed_value

from app.config import get_settings
from app.db import get_db, is_busy_error, quick_write
from app.models import User, UserSession, utcnow
from app.services import context


class PasswordChangeRequired(Exception):
    pass


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


COMMON_PASSWORDS = {"password", "password1", "password123", "passw0rd", "12345678", "123456789", "1234567890",
                    "qwerty123", "welcome1", "welcome123", "admin123", "admin@123", "demo@123", "abc12345",
                    "iloveyou1", "india123", "india@123", "ctdms123", "letmein1", "changeme1", "test1234"}


def password_problems(password: str, username: str | None = None) -> list[str]:
    problems = []
    if password.lower() in COMMON_PASSWORDS:
        problems.append("not a common / demo password")
    if username and username.lower() in password.lower():
        problems.append("must not contain the username")
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
    # Same wording for unknown users and wrong passwords (no username enumeration), and it tells honest users
    # about the lockout rule before they hit it.
    generic = (f"Invalid username or password. Note: {s.max_failed_logins} wrong attempts lock the account "
               f"for {s.lockout_minutes} minutes.")
    user = db.scalar(select(User).where(User.username == username.strip().lower()))
    now = utcnow()
    if not user:
        verify_password(password, "$2b$12$" + "x" * 53)  # constant-ish time
        return None, generic
    if not user.is_active:
        verify_password(password, "$2b$12$" + "x" * 53)
        return None, generic
    if user.locked_until and user.locked_until > now:
        mins = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
        return None, (f"Too many failed attempts - sign-in is blocked for about {mins} more minute(s). "
                      "Ask an administrator if you need access sooner.")
    if not verify_password(password, user.password_hash):
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= s.max_failed_logins:
            user.locked_until = now + timedelta(minutes=s.lockout_minutes)
            user.failed_logins = 0
        db.commit()
        return None, generic
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


def sign_out_everywhere(db: Session, user: User) -> None:
    """End every browser session of the user AND make every API token issued so far invalid.

    Used when an account is deactivated, gets a different role, or is recovered from the command line. The
    token stamp is stored on the user, so an old token does not come back to life if the account is
    re-activated later (QA-03).
    """
    revoke_user_sessions(db, user.id)
    user.tokens_valid_after = utcnow()


def _epoch(dt) -> float:
    return dt.replace(tzinfo=timezone.utc).timestamp()


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
        _touch(db, sess, revoked_at=now)
        return None, None
    user = _load_user(db, sess.user_id)
    if not user or not user.is_active:
        return None, None
    if not sess.last_seen_at or (now - sess.last_seen_at).total_seconds() > 60:
        _touch(db, sess, last_seen_at=now)
    return user, sess


def _touch(db: Session, sess: UserSession, **values) -> None:
    """Session bookkeeping ("last seen" / idle sign-out) - best effort, never makes a page wait.

    It runs in its own tiny transaction on a separate connection and waits at most 0.25 s for SQLite's write
    lock; if someone is saving right then it is skipped and simply tried again on the next page (both writes
    are harmless to repeat). Before, a page view could wait 30 s here and then fail.
    """
    db.commit()  # end this request's read transaction first (no lock held)
    try:
        with quick_write(db.get_bind()) as conn:
            conn.execute(update(UserSession).where(UserSession.id == sess.id).values(**values))
    except OperationalError as exc:
        if not is_busy_error(exc):
            raise
        return
    for k, v in values.items():
        set_committed_value(sess, k, v)


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
        "ts": _epoch(utcnow()),  # exact issue time, compared with user.tokens_valid_after
    }
    return jwt.encode(payload, s.secret_key, algorithm="HS256"), exp_minutes * 60


def token_user(db: Session, token: str) -> User | None:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    try:
        user = _load_user(db, int(payload.get("sub", 0)))
    except (TypeError, ValueError):
        return None
    if not user or not user.is_active:
        return None
    pwd = int(user.password_changed_at.timestamp()) if user.password_changed_at else 0
    if payload.get("pwd", 0) != pwd:  # password changed / reset / recovered -> old tokens invalid
        return None
    if payload.get("role") != (user.role.code if user.role else None):  # role changed -> ask for a new token
        return None
    if user.tokens_valid_after and float(payload.get("ts") or 0) <= _epoch(user.tokens_valid_after):
        return None  # issued before the account was deactivated / changed role / recovered
    return user


# --------------------------------------------------------------------------- dependencies
def client_ip(request: Request) -> str | None:
    """Client address for the audit trail.

    X-Forwarded-For is NOT read here: anyone can send that header. Uvicorn/gunicorn already replace
    request.client with the forwarded address, but only for proxies listed in FORWARDED_ALLOW_IPS.
    """
    return request.client.host if request.client else None


def current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    if getattr(request.state, "user_loaded", False):
        return request.state.user
    user, via = None, None
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        # An API client: the token alone decides who this is. An invalid token is NOT rescued by a browser
        # cookie that happens to be sent along (answer: 401).
        user = token_user(db, auth[7:].strip())
        if user:
            via = "token"
            context.session_ref.set("JWT")
    else:
        token = request.session.get("sid") if "session" in request.scope else None
        if token:
            user, sess = session_user(db, token)
            if sess:
                context.session_ref.set(f"S{sess.id}")
            if user:
                via = "session"
            else:
                request.session.pop("sid", None)
    request.state.user = user
    # How THIS request was actually authenticated. The API's CSRF rule depends on it: a browser cookie needs the
    # CSRF token, a valid Bearer token does not. Merely *sending* an Authorization header proves nothing.
    request.state.auth_via = via
    request.state.user_loaded = True
    return user


PASSWORD_CHANGE_PATHS = ("/account/password", "/logout", "/login")


def current_user(request: Request, user: User | None = Depends(current_user_optional)) -> User:
    if not user:
        raise LoginRequired()
    if user.must_change_password and not request.url.path.startswith(PASSWORD_CHANGE_PATHS):
        # First login / admin reset: nothing else is usable until the password is changed.
        raise PasswordChangeRequired()
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
def same_token(expected, supplied) -> bool:
    """Constant-time comparison that also copes with non-ASCII input (plain compare_digest raised TypeError
    for it, which surfaced as HTTP 500 instead of 403)."""
    return secrets.compare_digest(str(expected).encode("utf-8"), str(supplied).encode("utf-8"))


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
    if not expected or not supplied or not same_token(expected, supplied):
        raise PermissionDenied(message="Security token expired or invalid. Please reload the page and try again.")
