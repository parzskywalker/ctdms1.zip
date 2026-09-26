"""ERP adapter contract.

Every source system is reached through an adapter that knows how to FETCH raw invoice records and
TRANSFORM them into the central import fields (see import_service.TARGET_FIELDS). The central
import pipeline then does validation, duplicate detection, rate lookup and exception handling
identically for every ERP - nothing downstream is ERP-specific.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.models import ERPSystem


@dataclass
class ConfigField:
    key: str
    label: str
    required: bool = False
    secret: bool = False
    help: str = ""
    default: str | None = None


class AdapterNotConfigured(Exception):
    pass


class ERPAdapter(ABC):
    key: str = "base"
    name: str = "Base adapter"
    modes: list[str] = []
    description: str = ""
    config_fields: list[ConfigField] = []
    #: default mapping central_field -> source field name (overridable per ERP in ERP Mappings)
    default_field_map: dict[str, str] = {}
    supports_write_back: bool = False

    def __init__(self, erp: ERPSystem, field_map: dict[str, str] | None = None):
        self.erp = erp
        self.config: dict[str, Any] = dict(erp.config or {})
        self.field_map = {**self.default_field_map, **(field_map or {})}

    # -- configuration -------------------------------------------------------
    def cfg(self, key: str, default: Any = None) -> Any:
        v = self.config.get(key)
        if v in (None, ""):
            fld = next((f for f in self.config_fields if f.key == key), None)
            return fld.default if fld and fld.default is not None else default
        return v

    def missing_config(self) -> list[str]:
        return [f.label for f in self.config_fields if f.required and self.config.get(f.key) in (None, "")]

    def ensure_configured(self) -> None:
        missing = self.missing_config()
        if missing:
            raise AdapterNotConfigured(f"{self.erp.code}: missing configuration - {', '.join(missing)}")

    # -- operations ------------------------------------------------------------
    def test_connection(self) -> tuple[bool, str]:
        missing = self.missing_config()
        if missing:
            return False, f"Not configured: {', '.join(missing)}"
        return True, "Configuration present (no live check implemented for this adapter)"

    @abstractmethod
    def fetch(self, since: datetime | None, until: datetime | None) -> list[dict]:
        """Return raw source records."""

    def transform(self, raw: dict) -> dict:
        """Map a raw record to central import fields using the field map."""
        out = {}
        for central, source in self.field_map.items():
            out[central] = _dig(raw, source)
        for k, v in raw.items():  # records that already use central names pass straight through
            if k not in out and k in CENTRAL_FIELDS:
                out[k] = v
        return out

    def push_dispatch(self, invoice) -> tuple[bool, str]:
        """Optional write-back of dispatch details (LR, vehicle, dispatch no) to the ERP."""
        return False, "Write-back not supported by this adapter - ERP can pull via /api/v1/integrations/{erp}/dispatch-updates"


CENTRAL_FIELDS = {"company_code", "cfa_code", "invoice_no", "invoice_date", "customer_code", "customer_name",
                  "location", "sales_value", "boxes", "source_invoice_id", "transporter_code", "lr_no",
                  "vehicle_no", "remarks"}


def _dig(obj: Any, path: str) -> Any:
    """Read 'a.b.0.c' style paths from nested dicts/lists."""
    if not path:
        return None
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
        if cur is None:
            return None
    if isinstance(cur, (datetime, date)):
        return cur.isoformat()
    return cur


REGISTRY: dict[str, type[ERPAdapter]] = {}


def register(cls: type[ERPAdapter]) -> type[ERPAdapter]:
    REGISTRY[cls.key] = cls
    return cls


def get_adapter(erp: ERPSystem, field_map: dict[str, str] | None = None) -> ERPAdapter:
    from app.integrations import adapters  # noqa: F401  (registers adapters)
    cls = REGISTRY.get(erp.adapter or "file")
    if not cls:
        raise AdapterNotConfigured(f"Unknown adapter '{erp.adapter}'")
    return cls(erp, field_map)
