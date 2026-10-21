"""Reports e-mailed on a schedule (daily / weekly / monthly) as Excel, CSV or PDF attachments.

* DAILY   - yesterday, sent next morning
* WEEKLY  - the last 7 days up to yesterday, sent on Monday morning
* MONTHLY - the previous calendar month, sent on the 1st
Each subscription runs with its owner's data scope (companies / CFAs) and permissions; deactivated owners get nothing.
Sending time is `SEND_HOUR` local time. A background thread (or `python -m app.cli reports-mail`) sends what is due;
each run is claimed with a conditional UPDATE so several workers never send the same e-mail twice.
SMTP comes from .env: CTDMS_SMTP_HOST / _PORT / _USER / _PASSWORD / _FROM / _TLS (starttls | ssl | none).
"""
from __future__ import annotations

import logging
import re
import smtplib
import threading
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ReportSubscription, User, utcnow
from app.services import audit
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from app.services.utils import local_now, utc_start

log = logging.getLogger("ctdms.subscriptions")
MODULE = "REPORTS"
FREQUENCIES = {"DAILY": "Daily (yesterday)", "WEEKLY": "Weekly (last 7 days, Mondays)",
               "MONTHLY": "Monthly (previous month, on the 1st)"}
FORMATS = {"xlsx": "Excel", "csv": "CSV", "pdf": "PDF"}
SEND_HOUR = 7
EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


# ----------------------------------------------------------------------------- schedule
def period(freq: str, today: date) -> tuple[date, date]:
    yesterday = today - timedelta(days=1)
    if freq == "DAILY":
        return yesterday, yesterday
    if freq == "WEEKLY":
        return today - timedelta(days=7), yesterday
    last_month_end = today.replace(day=1) - timedelta(days=1)
    return last_month_end.replace(day=1), last_month_end


def next_run(freq: str, after_local: datetime) -> datetime:
    """Next sending moment (UTC-naive) strictly after the given local time."""
    d = after_local.date()
    if freq == "DAILY":
        cand = d if after_local.time() < time(SEND_HOUR) else d + timedelta(days=1)
    elif freq == "WEEKLY":
        cand = d + timedelta(days=(7 - d.weekday()) % 7)  # Monday
        if cand == d and after_local.time() >= time(SEND_HOUR):
            cand += timedelta(days=7)
    else:
        cand = d.replace(day=1)
        if cand < d or after_local.time() >= time(SEND_HOUR):
            cand = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return utc_start(cand) + timedelta(hours=SEND_HOUR)


def parse_recipients(text: str) -> list[str]:
    out = []
    for part in re.split(r"[,;\s]+", text or ""):
        p = part.strip()
        if not p:
            continue
        if not EMAIL_RE.match(p):
            raise BusinessError(f"'{p}' is not an e-mail address")
        if p.lower() not in (x.lower() for x in out):
            out.append(p)
    if not out:
        raise BusinessError("Give at least one e-mail address")
    if len(out) > 30:
        raise BusinessError("At most 30 recipients")
    return out


# ----------------------------------------------------------------------------- managing
def create(db: Session, user: User, *, name: str, report_code: str, frequency: str, recipients: str,
           fmt: str = "xlsx", params: dict | None = None) -> ReportSubscription:
    from app.services.reports import REPORTS
    rd = REPORTS.get(report_code)
    if rd is None:
        raise BusinessError("Choose a report")
    if not user.can(rd.perm) or not user.can("data.export"):
        raise BusinessError("You need rights to view and export this report")
    if frequency not in FREQUENCIES:
        raise BusinessError("Choose daily, weekly or monthly")
    if fmt not in FORMATS:
        raise BusinessError("Choose Excel, CSV or PDF")
    to = parse_recipients(recipients)
    sub = ReportSubscription(name=(name or rd.title).strip()[:120], report_code=report_code, frequency=frequency,
                             recipients=", ".join(to), fmt=fmt, params=params or {}, owner_id=user.id,
                             is_active=True, next_run_at=next_run(frequency, local_now()))
    db.add(sub)
    db.flush()
    audit.log(db, user, "SUBSCRIPTION_CREATED", MODULE, record_type="ReportSubscription", record_id=sub.id,
              ref=sub.name, details={"report": report_code, "frequency": frequency, "to": sub.recipients, "fmt": fmt})
    return sub


# ----------------------------------------------------------------------------- building & sending
def build(db: Session, sub: ReportSubscription, today: date | None = None) -> tuple[str, bytes, str, str]:
    """(file name, content, media type, subject) of the report for the subscription's period."""
    from app.services.exporters import to_csv, to_pdf, to_xlsx
    from app.services.reports import REPORTS, Filters
    owner = sub.owner
    rd = REPORTS.get(sub.report_code)
    if rd is None:
        raise BusinessError(f"Report {sub.report_code} no longer exists")
    if not owner or not owner.is_active or not owner.can(rd.perm) or not owner.can("data.export"):
        raise BusinessError("The owner of this subscription is inactive or may no longer export this report")
    today = today or local_now().date()
    d_from, d_to = period(sub.frequency, today)
    f = Filters.from_params({**{k: str(v) for k, v in (sub.params or {}).items()},
                             "date_from": d_from.isoformat(), "date_to": d_to.isoformat()})
    res = rd.run(db, owner, f)
    org = cfg.get(db, "org.name")
    subtitle = f"{org} · {f.describe()} ({res.date_basis or 'date'}) · e-mailed report for {owner.full_name}"
    stamp = f"{d_from:%Y%m%d}" + (f"-{d_to:%Y%m%d}" if d_to != d_from else "")
    if sub.fmt == "csv":
        body, media = to_csv(res), "text/csv"
    elif sub.fmt == "pdf":
        body, media = to_pdf(res, rd.title, subtitle, org), "application/pdf"
    else:
        body, media = to_xlsx(res, rd.title, subtitle), \
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    period_txt = f"{d_from:%d-%m-%Y}" + (f" to {d_to:%d-%m-%Y}" if d_to != d_from else "")
    return f"{sub.report_code}-{stamp}.{sub.fmt}", body, media, f"{rd.title} · {period_txt} ({len(res.rows)} rows)"


def smtp_ready() -> bool:
    s = get_settings()
    return bool(s.smtp_host and (s.smtp_from or s.smtp_user))


def send_mail(to: list[str], subject: str, text: str, attachments: list[tuple[str, bytes, str]]) -> None:
    from app.training import runtime
    runtime.guard("e-mail")
    s = get_settings()
    if not smtp_ready():
        raise BusinessError("E-mail is not set up: CTDMS_SMTP_HOST and CTDMS_SMTP_FROM are needed in .env")
    msg = EmailMessage()
    msg["From"] = s.smtp_from or s.smtp_user
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(text)
    for name, content, media in attachments:
        main, _, sub = media.partition("/")
        msg.add_attachment(content, maintype=main, subtype=sub, filename=name)
    tls = (s.smtp_tls or "starttls").lower()
    cls = smtplib.SMTP_SSL if tls == "ssl" else smtplib.SMTP
    with cls(s.smtp_host, s.smtp_port, timeout=30) as smtp:
        if tls == "starttls":
            smtp.starttls()
        if s.smtp_user and s.smtp_password:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)


def send(db: Session, sub: ReportSubscription, user: User | None = None) -> None:
    """Build and e-mail one subscription now; records success / failure on it."""
    try:
        name, content, media, subject = build(db, sub)
        org = cfg.get(db, "org.name")
        send_mail([x.strip() for x in sub.recipients.split(",") if x.strip()], f"{org}: {subject}",
                  f"{subject}\n\nAttached: {name}\nSubscription: {sub.name} ({FREQUENCIES[sub.frequency]})\n\n"
                  "Sent automatically by CTDMS. To stop it, open Reports › E-mailed reports.",
                  [(name, content, media)])
    except Exception as e:  # noqa: BLE001 - SMTP / report errors are recorded, the loop goes on
        sub.last_error = (e.message if isinstance(e, BusinessError) else f"{type(e).__name__}: {e}")[:1000]
        audit.log(db, user, "SUBSCRIPTION_FAILED", MODULE, record_type="ReportSubscription", record_id=sub.id,
                  ref=sub.name, reason=sub.last_error[:300])
        raise
    sub.last_sent_at = utcnow()
    sub.last_error = None
    audit.log(db, user, "SUBSCRIPTION_SENT", MODULE, record_type="ReportSubscription", record_id=sub.id,
              ref=sub.name, details={"to": sub.recipients, "file": name})


def send_due(db: Session) -> dict:
    out = {"sent": 0, "failed": 0}
    now = utcnow()
    ids = list(db.scalars(select(ReportSubscription.id).where(
        ReportSubscription.is_active.is_(True), ReportSubscription.next_run_at.isnot(None),
        ReportSubscription.next_run_at <= now).order_by(ReportSubscription.id)))
    for sid in ids:
        sub = db.get(ReportSubscription, sid)
        nxt = next_run(sub.frequency, local_now())
        claimed = db.execute(update(ReportSubscription).where(ReportSubscription.id == sid,
                                                              ReportSubscription.next_run_at == sub.next_run_at)
                             .values(next_run_at=nxt)).rowcount == 1
        db.commit()
        if not claimed:
            continue  # another worker took it
        db.refresh(sub)
        try:
            send(db, sub)
            out["sent"] += 1
        except Exception:  # noqa: BLE001
            out["failed"] += 1
        db.commit()
    return out


# ----------------------------------------------------------------------------- background sender
_thread: threading.Thread | None = None
_stop = threading.Event()


def _loop() -> None:
    from app.db import SessionLocal
    while not _stop.is_set():
        try:
            if smtp_ready():
                with SessionLocal() as db:
                    send_due(db)
                    db.commit()
        except Exception:  # noqa: BLE001 - keep the sender alive
            log.exception("Report subscription sender failed")
        _stop.wait(300)


def start_sender() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="ctdms-report-mail", daemon=True)
    _thread.start()


def stop_sender() -> None:
    _stop.set()
