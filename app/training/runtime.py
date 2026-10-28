"""Training Mode runtime: every trainee works in a private practice database and upload folder.

While a browser session is in Training Mode (session key "trn"), the request middleware activates it here and
`app.db.get_db` hands every screen a session on the trainee's own SQLite file instead of the live database. Sign-in
and training progress stay on the live database (see app.security.auth / app.training.progress). Nothing in Training
Mode can reach live business data, because the live database is simply never opened for it.

    data/training/templates/<checkpoint>.db   prepared starting points (built from the v1.6 demonstration scenario)
    data/training/users/<user id>/practice.db  the trainee's own practice database
    data/training/users/<user id>/uploads/     files uploaded while practising (workbooks, invoice files, POD, bills)
"""
from __future__ import annotations

import os
import shutil
import threading
from contextvars import ContextVar
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_active: ContextVar[dict | None] = ContextVar("ctdms_training", default=None)
_lock = threading.RLock()
_factories: dict[int, tuple] = {}

BANNER = "TRAINING MODE – DEMONSTRATION DATA ONLY"
WATERMARK = "TRAINING – DEMONSTRATION ONLY – NOT VALID FOR BUSINESS USE"


def root() -> Path:
    p = Path(get_settings().data_dir) / "training"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_dir(uid: int) -> Path:
    return root() / "users" / str(int(uid))


def db_path(uid: int) -> Path:
    return user_dir(uid) / "practice.db"


def has_practice_db(uid: int) -> bool:
    return db_path(uid).exists()


# ----------------------------------------------------------------------------- the active training context
def active() -> dict | None:
    """The training context of the current request (None = live work)."""
    return _active.get()


def activate(uid: int, act_as: str | None = None):
    d = user_dir(uid)
    return _active.set({"uid": int(uid), "dir": d, "uploads": d / "uploads", "inbox": d / "inbox", "as": act_as})


def deactivate(token) -> None:
    _active.reset(token)


class use:
    """Context manager used outside requests (template building, tests): `with runtime.use(uid_dir_ctx): ...`."""

    def __init__(self, ctx: dict):
        self.ctx = ctx

    def __enter__(self):
        self.token = _active.set(self.ctx)
        return self.ctx

    def __exit__(self, *exc):
        _active.reset(self.token)


def upload_dir() -> Path:
    t = active()
    p = Path(t["uploads"]) if t else Path(get_settings().upload_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def inbox_dir() -> Path:
    t = active()
    if not t:
        raise RuntimeError("inbox_dir() is only for Training Mode")
    p = Path(t["inbox"])
    p.mkdir(parents=True, exist_ok=True)
    return p


def guard(what: str) -> None:
    """Raise when an action that leaves CTDMS (messages, e-mail, ERP sync, backups) is attempted in Training Mode."""
    if active():
        from app.services.errors import BusinessError
        raise BusinessError(f"Training Mode: {what} is switched off while practising - nothing leaves CTDMS and "
                            "no live system is touched.")


# ----------------------------------------------------------------------------- practice database sessions
def sync_schema(eng) -> None:
    """Bring a practice database made by an OLDER version up to the current structure before it is used: missing
    tables are created and missing columns added (the structure only ever grows - migrations are additive). Without
    this, a trainee who was mid-lesson at an upgrade would hit "no such column" on the first screen."""
    from sqlalchemy import inspect
    from sqlalchemy.schema import CreateColumn

    import app.models  # noqa: F401 - every table registered
    from app.db import Base
    insp = inspect(eng)
    had = set(insp.get_table_names())
    Base.metadata.create_all(eng)
    for t in Base.metadata.sorted_tables:
        if t.name not in had:
            continue
        have = {c["name"] for c in insp.get_columns(t.name)}
        for col in t.columns:
            if col.name in have or col.primary_key:
                continue
            ddl = str(CreateColumn(col).compile(dialect=eng.dialect))
            with eng.begin() as cx:
                cx.exec_driver_sql(f"ALTER TABLE {t.name} ADD COLUMN {ddl}")


def _factory(uid: int):
    with _lock:
        f = _factories.get(uid)
        if f is None:
            from app.db import _make_engine
            eng = _make_engine("sqlite:///" + db_path(uid).as_posix())
            sync_schema(eng)
            f = (eng, sessionmaker(bind=eng, autoflush=False, expire_on_commit=False))
            _factories[uid] = f
        return f


def session_for(uid: int) -> Session:
    return _factory(uid)[1]()


def engine_for(uid: int):
    return _factory(uid)[0]


def close(uid: int) -> None:
    """Release the trainee's database file (needed on Windows before it is replaced)."""
    with _lock:
        f = _factories.pop(uid, None)
    if f:
        f[0].dispose()


def install(uid: int, template: Path, keep_uploads: bool = False) -> None:
    """Give the trainee a fresh copy of a prepared starting point (a few hundred KB - takes well under a second)."""
    d = user_dir(uid)
    d.mkdir(parents=True, exist_ok=True)
    close(uid)
    tmp = d / "practice.db.new"
    shutil.copyfile(template, tmp)
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(str(db_path(uid)) + suffix)
        except FileNotFoundError:
            pass
    os.replace(tmp, db_path(uid))
    if not keep_uploads:
        shutil.rmtree(d / "uploads", ignore_errors=True)
    (d / "uploads").mkdir(parents=True, exist_ok=True)
    tpl_uploads = template.with_suffix("").parent / (template.stem + "_uploads")
    if tpl_uploads.exists():
        shutil.copytree(tpl_uploads, d / "uploads", dirs_exist_ok=True)


def remove(uid: int) -> None:
    close(uid)
    shutil.rmtree(user_dir(uid), ignore_errors=True)
