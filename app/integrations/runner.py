"""Runs an adapter sync and feeds the records through the central import pipeline."""
from __future__ import annotations

import hashlib
import secrets
import traceback
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.base import AdapterNotConfigured, get_adapter
from app.models import ERPFieldMapping, ERPSystem, IntegrationRun, User, utcnow
from app.services import audit
from app.services.import_service import ingest_records


def field_map_for(db: Session, erp: ERPSystem) -> dict[str, str]:
    return {m.target_field: m.source_field for m in
            db.scalars(select(ERPFieldMapping).where(ERPFieldMapping.erp_system_id == erp.id))}


def run_sync(db: Session, erp: ERPSystem, user: User | None, trigger: str = "MANUAL",
             since: datetime | None = None, until: datetime | None = None) -> IntegrationRun:
    run = IntegrationRun(erp_system_id=erp.id, trigger=trigger, triggered_by_id=user.id if user else None)
    db.add(run)
    db.flush()
    try:
        adapter = get_adapter(erp, field_map_for(db, erp))
        since = since or erp.last_sync_at or (datetime.now() - timedelta(days=1))
        raw = adapter.fetch(since, until)
        run.fetched = len(raw)
        if raw:
            records = [adapter.transform(r) for r in raw]
            batch, result = ingest_records(db, user, erp, records, source="ADAPTER")
            run.batch_id = batch.id
            run.imported = result.get("imported", 0)
            run.rejected = result.get("REJECTED", 0) + result.get("DUPLICATE", 0)
            run.status = "SUCCESS" if run.rejected == 0 else ("PARTIAL" if run.imported else "FAILED")
            run.message = f"Batch {batch.batch_no}: {run.imported} imported, {run.rejected} rejected/duplicate"
        else:
            run.status = "SUCCESS"
            run.message = "No new records"
        erp.last_sync_at = utcnow()
    except AdapterNotConfigured as e:
        run.status = "FAILED"
        run.message = str(e)
    except Exception as e:  # noqa: BLE001 - record any adapter failure on the run
        run.status = "FAILED"
        run.message = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"[:4000]
    run.finished_at = utcnow()
    audit.log(db, user, "ERP_SYNC", "INTEGRATION", record_type="IntegrationRun", record_id=run.id, ref=erp.code,
              details={"status": run.status, "fetched": run.fetched, "imported": run.imported,
                       "message": (run.message or "")[:300]})
    db.flush()
    return run


def new_api_key(db: Session, erp: ERPSystem, user: User) -> str:
    key = f"ctdms_{erp.code.lower()}_{secrets.token_urlsafe(24)}"
    erp.api_key_hash = hashlib.sha256(key.encode()).hexdigest()
    erp.api_key_hint = key[-6:]
    audit.log(db, user, "ERP_API_KEY_ROTATED", "INTEGRATION", record_type="ERPSystem", record_id=erp.id, ref=erp.code)
    return key


def erp_for_api_key(db: Session, code: str, key: str | None) -> ERPSystem | None:
    if not key:
        return None
    erp = db.scalar(select(ERPSystem).where(ERPSystem.code == code.upper(), ERPSystem.is_active.is_(True)))
    if not erp or not erp.api_key_hash:
        return None
    if not secrets.compare_digest(erp.api_key_hash, hashlib.sha256(key.encode()).hexdigest()):
        return None
    return erp
