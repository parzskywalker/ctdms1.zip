"""Request-scoped context (used by the audit trail to capture IP / session automatically).

A per-request mutable dict is stored in a ContextVar by the HTTP middleware. Because the *same dict*
is shared by the copies of the context that FastAPI's thread pool makes for sync dependencies and
endpoints, values set by the auth dependency are visible to the endpoint and to the audit service.
"""
from __future__ import annotations

from contextvars import ContextVar

_ctx: ContextVar[dict | None] = ContextVar("ctdms_request_ctx", default=None)


def begin(**values) -> dict:
    d = dict(values)
    _ctx.set(d)
    return d


def _get(key: str):
    d = _ctx.get()
    return d.get(key) if d else None


def _set(key: str, value) -> None:
    d = _ctx.get()
    if d is None:
        d = {}
        _ctx.set(d)
    d[key] = value


class _Var:
    def __init__(self, key: str):
        self.key = key

    def get(self):
        return _get(self.key)

    def set(self, value):
        _set(self.key, value)


request_ip = _Var("ip")
session_ref = _Var("session")
request_id = _Var("request_id")
