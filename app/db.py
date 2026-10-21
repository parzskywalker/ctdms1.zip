"""Database engine / session management (SQLAlchemy 2.x)."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

# How SQLite transactions of the current request start (PostgreSQL ignores this):
#   "write" (default: form posts, API writes, CLI, tests) -> BEGIN IMMEDIATE - takes the single SQLite write lock
#            up-front, so two writers can never collide half-way.
#   "read"  (page views / GET requests)                  -> plain BEGIN - no lock; in WAL mode any number of
#            readers work in parallel with each other and with one writer.
# Before v1.2 every page view took the write lock, so all users queued behind each other and under load some
# requests gave up after 30 s with "database is locked" (QA-13).
db_mode: ContextVar[str] = ContextVar("ctdms_db_mode", default="write")


# Per-request notes kept by the engine events below; read by the retry middleware (app.main).
#   immediate: the request started a write transaction (it may already have waited for the lock -> no retry)
#   committed: the request saved something (a retry could save it twice -> no retry)
request_db_state: ContextVar[dict | None] = ContextVar("ctdms_request_db_state", default=None)

_WRITE_SQL = ("INSERT", "UPDATE", "DELETE", "REPLACE")


async def write_transaction() -> None:
    """Route dependency for a GET page that reads and writes in one go (admin settings page)."""
    db_mode.set("write")


@contextmanager
def write_block(db: Session):
    """End the current lock-free read transaction, then do the writes inside the block in a short write
    transaction. Used for the audit entry at the END of an export, so reading the data and building the file
    never hold the write lock."""
    db.commit()
    token = db_mode.set("write")
    try:
        yield
        db.commit()
    finally:
        db_mode.reset(token)


@contextmanager
def quick_write(bind, wait_ms: int = 250):
    """A small best-effort write that never makes a page wait for other users' saves: on SQLite it waits at most
    `wait_ms` for the write lock, then raises OperationalError (the caller skips the write)."""
    with bind.connect() as conn:
        if conn.dialect.name != "sqlite":
            with conn.begin():
                yield conn
            return
        raw = conn.connection.dbapi_connection
        raw.execute(f"PRAGMA busy_timeout={int(wait_ms)}")
        conn.info["quick"] = True
        token = db_mode.set("write")
        try:
            with conn.begin():
                yield conn
        finally:
            db_mode.reset(token)
            conn.info.pop("quick", None)
            raw.execute("PRAGMA busy_timeout=30000")


def is_busy_error(exc: BaseException) -> bool:
    msg = str(getattr(exc, "orig", exc)).lower()
    return "database is locked" in msg or "database is busy" in msg


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
            # Let SQLAlchemy control transactions so SAVEPOINTs work (pysqlite quirk). Write transactions start
            # with BEGIN IMMEDIATE: writers are serialised up-front, which avoids SQLITE_BUSY_SNAPSHOT errors when
            # a read-then-write transaction races another writer. Read-only requests use a plain BEGIN (see
            # db_mode above). (Use PostgreSQL for a multi-user server installation.)
            dbapi_conn.isolation_level = None
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA cache_size=-32000")  # 32 MB page cache per connection (default is 2 MB)
            cur.execute("PRAGMA temp_store=MEMORY")
            cur.close()

        @event.listens_for(eng, "begin")
        def _sqlite_begin(conn):  # pragma: no cover - trivial
            immediate = db_mode.get() != "read"
            st = request_db_state.get()
            if st is not None and immediate and not conn.info.get("quick"):
                st["immediate"] = True
            conn.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")

    @event.listens_for(eng, "before_cursor_execute")
    def _note_write(conn, cursor, statement, parameters, context, executemany):  # pragma: no cover - trivial
        if request_db_state.get() is not None and statement.lstrip()[:7].upper().startswith(_WRITE_SQL) \
                and not conn.info.get("quick"):  # session bookkeeping is harmless to repeat
            conn.info["wrote"] = True

    @event.listens_for(eng, "commit")
    def _note_commit(conn):  # pragma: no cover - trivial
        st = request_db_state.get()
        if conn.info.pop("wrote", False) and st is not None:
            st["committed"] = True

    @event.listens_for(eng, "rollback")
    def _note_rollback(conn):  # pragma: no cover - trivial
        conn.info.pop("wrote", None)

    return eng


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def rebind(url: str) -> None:
    """Re-point the global engine (used by tests and CLI)."""
    global engine
    engine = _make_engine(url)
    SessionLocal.configure(bind=engine)


def get_db() -> Iterator[Session]:
    # Training Mode (v1.6 Guided Training): the trainee's own practice database, never the live one.
    from app.training import runtime
    t = runtime.active()
    db = runtime.session_for(t["uid"]) if t else SessionLocal()
    try:
        yield db
    finally:
        db.close()


def dialect_name(db: Session) -> str:
    return db.get_bind().dialect.name
