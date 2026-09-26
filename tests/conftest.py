"""Test fixtures.

The whole suite runs against a freshly seeded demo database:
  * SQLite (default) - a temporary file
  * PostgreSQL        - set CTDMS_TEST_DATABASE_URL=postgresql+psycopg://user:pwd@host/db  (DB is wiped!)
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="ctdms-test-"))
os.environ["CTDMS_DATABASE_URL"] = os.environ.get("CTDMS_TEST_DATABASE_URL") or f"sqlite:///{_tmp / 'test.db'}"
os.environ["CTDMS_ENVIRONMENT"] = "test"
os.environ["CTDMS_DATA_DIR"] = str(_tmp)
os.environ["CTDMS_UPLOAD_DIR"] = str(_tmp / "uploads")
os.environ["CTDMS_BACKUP_DIR"] = str(_tmp / "backups")
os.environ["CTDMS_LOG_DIR"] = str(_tmp / "logs")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def seeded():
    from app import seed
    seed.run(reset=True, quiet=True)
    yield


def _test_session():
    """Session for assertions. On SQLite it uses the driver's default (autocommit reads) so the test never
    holds a lock or a stale snapshot while the app handles requests."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    url = os.environ["CTDMS_DATABASE_URL"]
    if url.startswith("sqlite"):
        global _TEST_ENGINE
        if "_TEST_ENGINE" not in globals():
            _TEST_ENGINE = create_engine(url, connect_args={"timeout": 30})
        return Session(bind=_TEST_ENGINE, expire_on_commit=False, autoflush=False)
    return SessionLocal()


@pytest.fixture()
def db():
    s = _test_session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _csrf(html: str) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "csrf token not found"
    return m.group(1)


class WebClient:
    """TestClient wrapper that logs in through the real login form and handles CSRF tokens."""

    def __init__(self, username: str, password: str | None = None):
        password = password or ("Admin@123" if username == "admin" else "Demo@123")
        self.c = TestClient(app)
        r = self.c.get("/login")
        r = self.c.post("/login", data={"username": username, "password": password, "csrf_token": _csrf(r.text),
                                         "next": "/"}, follow_redirects=False)
        assert r.status_code == 303, f"login failed for {username}: {r.status_code}"
        self.token = _csrf(self.c.get("/account/password").text)

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, files=None, follow_redirects=True, **kw):
        data = dict(data or {})
        data["csrf_token"] = self.token
        return self.c.post(url, data=data, files=files, follow_redirects=follow_redirects, **kw)


@pytest.fixture()
def web():
    return WebClient


@pytest.fixture()
def api():
    """Returns a function giving a TestClient with a Bearer token for the user."""
    def _make(username: str, password: str | None = None) -> TestClient:
        password = password or ("Admin@123" if username == "admin" else "Demo@123")
        c = TestClient(app)
        r = c.post("/api/v1/auth/token", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        return c
    return _make
