"""Audit trail helper. Every important action goes through `log()` / `log_changes()`."""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, User
from app.services import context


def _s(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, Decimal):
        return f"{v:.2f}"
    if isinstance(v, (date, datetime, time)):
        return v.isoformat()
    if isinstance(v, (list, tuple, set)):
        return ", ".join(str(x) for x in v)
    return str(v)


def log(db: Session, user: User | None, action: str, module: str, *, record_type: str | None = None,
        record_id: Any = None, ref: str | None = None, field: str | None = None, old: Any = None,
        new: Any = None, reason: str | None = None, details: dict | None = None) -> AuditLog:
    entry = AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else "system",
        action=action,
        module=module,
        record_type=record_type,
        record_id=str(record_id) if record_id is not None else None,
        ref=ref,
        field=field,
        old_value=_s(old),
        new_value=_s(new),
        reason=reason,
        ip=context.request_ip.get(),
        session_ref=context.session_ref.get(),
        details=details,
    )
    db.add(entry)
    return entry


def log_changes(db: Session, user: User | None, action: str, module: str, changes: dict[str, tuple[Any, Any]],
                *, record_type: str, record_id: Any, ref: str | None = None, reason: str | None = None) -> int:
    n = 0
    for fld, (old, new) in changes.items():
        if _s(old) == _s(new):
            continue
        log(db, user, action, module, record_type=record_type, record_id=record_id, ref=ref, field=fld,
            old=old, new=new, reason=reason)
        n += 1
    return n


def diff(obj: Any, values: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """Return {field: (old, new)} for attributes that would change."""
    out = {}
    for k, v in values.items():
        old = getattr(obj, k, None)
        if _s(old) != _s(v):
            out[k] = (old, v)
    return out
