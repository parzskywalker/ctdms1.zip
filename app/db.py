"""Database engine / session management (SQLAlchemy 2.x)."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str | None = None):
    settings = get_settings()
    url = url or settings.database_url
    kwargs: dict = {"echo": settings.db_echo, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        kwargs.update(pool_pre_ping=True, pool_size=10, max_overflow=20)
    eng = create_engine(url, **kwargs)

    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            # Let SQLAlchemy control transactions so SAVEPOINTs work (pysqlite quirk). Transactions start with
            # BEGIN IMMEDIATE: writers are serialised up-front, which avoids SQLITE_BUSY_SNAPSHOT errors when a
            # read-then-write transaction races another writer. (Use PostgreSQL for multi-user production.)
            dbapi_conn.isolation_level = None
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        @event.listens_for(eng, "begin")
        def _sqlite_begin(conn):  # pragma: no cover - trivial
            conn.exec_driver_sql("BEGIN IMMEDIATE")

    return eng


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def rebind(url: str) -> None:
    """Re-point the global engine (used by tests and CLI)."""
    global engine
    engine = _make_engine(url)
    SessionLocal.configure(bind=engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def dialect_name(db: Session) -> str:
    return db.get_bind().dialect.name
