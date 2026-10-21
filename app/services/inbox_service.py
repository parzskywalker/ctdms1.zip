"""Drop folder: ERP exports saved into data/inbox are imported automatically.

Most billing systems (MARG, Tally-style ERPs, SAP report variants, CBO, Intrect) can save or schedule an Excel / CSV
export to a folder even when they offer no API. Point that export at `data/inbox` (or `data/inbox/<ERP code>` to
tell CTDMS which ERP it is). Every few minutes CTDMS:

1. claims each new file by renaming it (safe with several app workers),
2. recognises the layout from its column headings (saved import templates),
3. imports every good row; rows with problems stay in the batch for correction on the Invoice Import screen,
4. moves the file to `processed/` (or `failed/` with a .txt note when it cannot be read).

Duplicate invoices are refused as usual, so re-dropping the same file never creates double entries.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ERPSystem
from app.services import import_service as imp
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from app.services.utils import local_now

log = logging.getLogger("ctdms.inbox")
SUFFIXES = (".csv", ".xlsx", ".xls", ".xlsm", ".txt")


def inbox_dir() -> Path:
    from app.training import runtime
    # Training Mode: the trainee's own (empty) drop folder - the live ERP drop folder is never read while practising
    p = runtime.inbox_dir() if runtime.active() else get_settings().data_dir / "inbox"
    for sub in ("", "processed", "failed"):
        (p / sub).mkdir(parents=True, exist_ok=True)
    return p


def pending_files() -> list[Path]:
    root = inbox_dir()
    out = []
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in SUFFIXES or f.name.startswith(("~$", ".")):
            continue
        if any(part in ("processed", "failed") for part in f.relative_to(root).parts[:-1]):
            continue
        out.append(f)
    return out


def _erp_for(db: Session, f: Path) -> int | None:
    root = inbox_dir()
    parts = f.relative_to(root).parts
    if len(parts) < 2:
        return None
    erp = db.scalar(select(ERPSystem).where(func.upper(ERPSystem.code) == parts[0].upper()))
    return erp.id if erp else None


def scan(db: Session) -> list[dict]:
    """Import everything waiting in the drop folder. Returns one result per file (also written to the log)."""
    root = inbox_dir()
    results = []
    for f in pending_files():
        stamp = local_now().strftime("%Y%m%d-%H%M%S")
        claimed = f.with_name(f"{f.stem}.{stamp}.processing{f.suffix}")
        try:
            f.rename(claimed)  # another worker got it first -> skip
        except OSError:
            continue
        try:
            content = claimed.read_bytes()
            erp_id = _erp_for(db, f)
            batch, result, warning = imp.quick_import(db, None, content, f.name, erp_system_id=erp_id)
            batch.notes = ((batch.notes or "") + f" Drop folder: {f.relative_to(root)}").strip()
            db.commit()
            dest = root / "processed" / f"{f.stem}.{stamp}{f.suffix}"
            claimed.rename(dest)
            msg = (f"{f.name}: batch {batch.batch_no} - {result.get('imported', 0)} invoice(s) imported, "
                   f"{result.get('items_added', 0)} given SKU lines, {result.get('REJECTED', 0)} row(s) need "
                   f"correction, {result.get('DUPLICATE', 0)} duplicate(s)" + (f". {warning}" if warning else ""))
            log.info(msg)
            results.append({"file": f.name, "ok": True, "batch": batch.batch_no, "message": msg})
        except (BusinessError, Exception) as e:  # noqa: BLE001 - one bad file must not stop the others
            db.rollback()
            dest = root / "failed" / f"{f.stem}.{stamp}{f.suffix}"
            try:
                claimed.rename(dest)
                dest.with_suffix(dest.suffix + ".txt").write_text(
                    getattr(e, "message", None) or f"{type(e).__name__}: {e}", encoding="utf-8")
            except OSError:
                pass
            log.warning("Drop folder: %s failed: %s", f.name, e)
            results.append({"file": f.name, "ok": False, "message": getattr(e, "message", None) or str(e)})
    return results


_thread: threading.Thread | None = None
_stop = threading.Event()


def _loop() -> None:
    from app.db import SessionLocal
    while not _stop.is_set():
        wait = 5
        try:
            with SessionLocal() as db:
                if cfg.get(db, "inbox.enabled"):
                    if pending_files():
                        scan(db)
                    db.commit()
                wait = max(1, int(cfg.get(db, "inbox.interval_minutes") or 5))
        except Exception:  # noqa: BLE001 - keep watching
            log.exception("Drop-folder check failed")
        _stop.wait(wait * 60)


def start_watcher() -> None:
    """Background thread in the web app (each worker; files are claimed atomically)."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="ctdms-inbox", daemon=True)
    _thread.start()


def stop_watcher() -> None:
    _stop.set()
    time.sleep(0)
