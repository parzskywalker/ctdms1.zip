"""Small shared helpers: fiscal year, parsing, formatting, sequences."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Sequence

TWO = Decimal("0.01")


def money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(TWO, rounding=ROUND_HALF_UP)


def fiscal_year(d: date) -> str:
    """Indian FY (April-March), e.g. 2026-09-26 -> '2026-27'."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def fy_start(d: date) -> date:
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def local_now() -> datetime:
    from app.models import base as _base
    if _base._FAKE_NOW is not None:
        return to_local(_base._FAKE_NOW)
    return datetime.now(ZoneInfo(get_settings().timezone)).replace(tzinfo=None)


def local_today() -> date:
    return local_now().date()


def utc_start(d: date) -> datetime:
    """UTC-naive timestamp of local midnight at the start of date d (for filtering UTC timestamps by local date)."""
    from datetime import timezone as _tz
    loc = datetime.combine(d, time.min).replace(tzinfo=ZoneInfo(get_settings().timezone))
    return loc.astimezone(_tz.utc).replace(tzinfo=None)


def to_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    from datetime import timezone as _tz
    return dt.replace(tzinfo=_tz.utc).astimezone(ZoneInfo(get_settings().timezone)).replace(tzinfo=None)


# ----------------------------------------------------------------------------- parsing
DATE_FORMATS = ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%y",
                "%d-%m-%y", "%Y/%m/%d", "%d %b %Y", "%Y%m%d"]


def parse_date(value, fmt: str | None = "AUTO") -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 20000 < float(value) < 80000:  # Excel serial date
        return date(1899, 12, 30) + timedelta(days=int(float(value)))
    s = str(value).strip()
    if not s:
        return None
    if "T" in s and len(s) >= 10:
        s = s[:10]
    if " " in s and re.match(r"^\d{4}-\d{2}-\d{2} ", s):
        s = s[:10]
    formats = DATE_FORMATS if not fmt or fmt == "AUTO" else [fmt]
    for f in formats:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date '{value}'")


def parse_time(value) -> time | None:
    if value is None or value == "":
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
    s = str(value).strip()
    for f in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(s, f).time()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised time '{value}'")


def parse_decimal(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return _check_finite(value, value)
    if isinstance(value, (int, float)):
        return _check_finite(Decimal(str(value)), value)
    s = str(value).strip().replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
    if s == "":
        return None
    try:
        d = Decimal(s)
    except InvalidOperation as e:
        raise ValueError(f"Not a number: '{value}'") from e
    return _check_finite(d, value)


MAX_NUMBER = Decimal("1000000000000")  # 1e12: far above any real invoice value / box count / rate


def _check_finite(d: Decimal, original) -> Decimal:
    """Reject NaN / Infinity / absurd magnitudes (they crash reports and overflow NUMERIC columns)."""
    if not d.is_finite():
        raise ValueError(f"Not a number: '{original}'")
    if abs(d) >= MAX_NUMBER:
        raise ValueError(f"Number too large: '{original}'")
    return d


def safe_int(value, default: int | None = None, *, minimum: int | None = None, maximum: int = 2**62) -> int | None:
    """Lenient int for query-string / form ids: bad or out-of-range input gives `default` instead of a 500."""
    if value is None or value == "":
        return default
    try:
        i = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if i > maximum or (minimum is not None and i < minimum):
        return default
    return i


def parse_int(value) -> int | None:
    d = parse_decimal(value)
    if d is None:
        return None
    if d != d.to_integral_value():
        raise ValueError(f"Not a whole number: '{value}'")
    return int(d)


def csv_safe(value):
    """Neutralise spreadsheet formula injection: text starting with = + - @ (or tab/CR) gets a leading apostrophe
    so Excel / LibreOffice show it as text instead of executing it. Numbers are left untouched."""
    if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def norm_code(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().upper()


def norm_vehicle(value) -> str | None:
    if not value:
        return None
    return re.sub(r"[^A-Z0-9]", "", str(value).upper()) or None


VEHICLE_RE = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{3,4}$")
BH_VEHICLE_RE = re.compile(r"^\d{2}BH\d{4}[A-Z]{1,2}$")  # Bharat-series plates, e.g. 22BH1234AA


def vehicle_looks_valid(v: str | None) -> bool:
    return bool(v and (VEHICLE_RE.match(v) or BH_VEHICLE_RE.match(v) or v.startswith("SELF") or v.startswith("HAND")))


# ----------------------------------------------------------------------------- sequences
def next_sequence(db: Session, name: str) -> int:
    """Concurrency-safe counter (row-locked on PostgreSQL, serialised writes on SQLite)."""
    stmt = select(Sequence).where(Sequence.name == name)
    if db.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    seq = db.scalar(stmt)
    if seq is None:
        seq = Sequence(name=name, next_value=1)
        db.add(seq)
        db.flush()
    value = seq.next_value
    db.execute(update(Sequence).where(Sequence.name == name).values(next_value=value + 1))
    db.expire(seq)
    return value


def next_sequence_block(db: Session, name: str, n: int) -> int:
    """Reserve n consecutive numbers with ONE update of the counter (so a 200-case gatepass does not hold the write lock
    for 200 round trips). Returns the first number of the block."""
    stmt = select(Sequence).where(Sequence.name == name)
    if db.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    seq = db.scalar(stmt)
    if seq is None:
        seq = Sequence(name=name, next_value=1)
        db.add(seq)
        db.flush()
    value = seq.next_value
    db.execute(update(Sequence).where(Sequence.name == name).values(next_value=value + max(n, 1)))
    db.expire(seq)
    return value


# ----------------------------------------------------------------------------- formatting
def fmt_money(v, symbol: bool = True) -> str:
    if v is None or v == "":
        return "—"
    d = money(v)
    neg = d < 0
    d = abs(d)
    whole, frac = f"{d:.2f}".split(".")
    # Indian digit grouping: 12,34,567.00
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    out = f"{whole}.{frac}"
    if symbol:
        out = f"₹{out}"
    return f"-{out}" if neg else out


def fmt_int(v) -> str:
    if v is None or v == "":
        return "—"
    return f"{int(v):,}"


def fmt_date(v) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        try:
            v = date.fromisoformat(v[:10])
        except ValueError:
            return v
    return v.strftime("%d-%m-%Y")


def fmt_dt(v, local: bool = True) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v)
        except ValueError:
            return v
    if local:
        v = to_local(v)
    return v.strftime("%d-%m-%Y %H:%M")


def fmt_time(v) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        return v[:5]
    return v.strftime("%H:%M")
