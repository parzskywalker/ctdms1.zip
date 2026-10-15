"""Dispatch alerts to customers by WhatsApp / SMS, the way a courier tells you your parcel is on the way.

Two moments per consignment (one customer on one gatepass):

* GATE_OUT   - the goods left our gate: invoices, cases, carrier and the carrier's mobile, gatepass number;
* LR_ENTERED - the transporter's LR / bilty number is known (sent again if it is corrected).

Messages are queued in `notifications` (one per consignment and event - never twice) and sent by a background
thread (every `notify.interval_seconds`) or `python -m app.cli notify`. Rows are claimed with a conditional UPDATE,
so several app workers never send the same message twice. Failed sends are retried after 5, 10, 15 ... minutes up to
`notify.max_attempts`, then marked FAILED for a person to retry from the Notifications screen.

Providers (setting `notify.provider`):
  OFF            nothing is queued or sent (default)
  LOG            queued and marked sent without leaving the server - to try the feature
  WHATSAPP_CLOUD Meta WhatsApp Business Cloud API, an approved template with one body parameter
  HTTP_SMS       any SMS gateway reachable by HTTP (JSON POST or GET query string)
The bearer token comes from CTDMS_NOTIFY_TOKEN, never from the settings page.
"""
from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    DispatchMode, DispatchSlip, Invoice, InvoiceStatus, Notification, NotifyEvent, NotifyStatus, User, utcnow,
)
from app.services import audit
from app.services import settings_service as cfg

MODULE = "NOTIFY"
log = logging.getLogger("ctdms.notify")


# ----------------------------------------------------------------------------- numbers & text
def normalise_mobile(raw) -> str | None:
    """Indian mobile -> 12 digits with country code (91XXXXXXXXXX). Landlines / junk -> None."""
    s = str(raw or "").strip()
    if re.match(r"^0\d{2,4}[-\s/]", s):  # STD code + landline number, e.g. 0771-4012345
        return None
    d = re.sub(r"\D", "", s)
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) == 10 and d[0] in "6789":
        return "91" + d
    if len(d) == 12 and d.startswith("91") and d[2] in "6789":
        return d
    return None


def provider(db: Session) -> str:
    return cfg.get(db, "notify.provider") or "OFF"


def enabled(db: Session, event: str) -> bool:
    if provider(db) == "OFF":
        return False
    return bool(cfg.get(db, "notify.on_gate_out" if event == NotifyEvent.GATE_OUT else "notify.on_lr"))


class _Safe(dict):
    def __missing__(self, key):  # a typo in a template prints the placeholder instead of failing the dispatch
        return "{" + key + "}"


def render(db: Session, event: str, slip: DispatchSlip, invs: list[Invoice]) -> str:
    first = invs[0]
    tr, auto = slip.transporter, slip.outside_auto
    if slip.dispatch_mode == DispatchMode.SELF:
        carrier, mobile = "self pickup", None
    elif tr:
        carrier, mobile = tr.name, tr.mobile
    elif auto:
        carrier, mobile = f"auto {auto.name}", auto.mobile
    else:
        carrier, mobile = "our vehicle", None
    values = _Safe(
        org=cfg.get(db, "org.name") or "",
        customer=first.customer.name,
        invoices=", ".join(i.invoice_no for i in invs),
        companies=", ".join(sorted({i.company.name for i in invs})),
        cases=sum(i.boxes or 0 for i in invs),
        carrier=carrier,
        carrier_mobile=f" ({mobile})" if mobile else "",
        gatepass=slip.dispatch_no,
        date=(slip.dispatch_date or first.dispatch_date).strftime("%d-%m-%Y") if (slip.dispatch_date or first.dispatch_date) else "",
        lr=first.lr_no or "",
        lr_date=f" dated {first.lr_date:%d-%m-%Y}" if first.lr_date else "",
    )
    tpl = cfg.get(db, "notify.template_gate_out" if event == NotifyEvent.GATE_OUT else "notify.template_lr")
    text = tpl.format_map(values)
    return re.sub(r"\s+", " ", text).strip()[:1000]  # WhatsApp template parameters cannot hold newlines


# ----------------------------------------------------------------------------- queueing
def _consignments(invs: list[Invoice]) -> list[list[Invoice]]:
    groups: OrderedDict = OrderedDict()
    for i in invs:
        if i.status != InvoiceStatus.CANCELLED:
            groups.setdefault(i.customer_id, []).append(i)
    return list(groups.values())


def _queue(db: Session, user: User | None, event: str, slip: DispatchSlip, group: list[Invoice], key: str) -> Notification | None:
    if db.scalar(select(Notification.id).where(Notification.dedupe_key == key)):
        return None
    cu = group[0].customer
    to = normalise_mobile(cu.whatsapp) or normalise_mobile(cu.contact_phone)
    prov = provider(db)
    n = Notification(event=event, dedupe_key=key, slip_id=slip.id, cfa_id=slip.cfa_id, customer_id=cu.id,
                     invoice_ids=[i.id for i in group], to_number=to, body=render(db, event, slip, group),
                     channel={"WHATSAPP_CLOUD": "WHATSAPP", "HTTP_SMS": "SMS"}.get(prov, "LOG"), provider=prov,
                     status=NotifyStatus.QUEUED if to else NotifyStatus.FAILED,
                     last_error=None if to else "No valid mobile / WhatsApp number in the customer master",
                     next_attempt_at=utcnow(), created_by_id=user.id if user else None)
    try:
        with db.begin_nested():  # another worker may have queued the same message a moment ago
            db.add(n)
            db.flush()
    except IntegrityError:
        return None
    return n


def on_gate_out(db: Session, user: User | None, slip: DispatchSlip, invs: list[Invoice]) -> int:
    if not enabled(db, NotifyEvent.GATE_OUT):
        return 0
    n = 0
    for group in _consignments(invs):
        if _queue(db, user, NotifyEvent.GATE_OUT, slip, group, f"GO:{slip.id}:{group[0].customer_id}"):
            n += 1
    return n


def on_lr(db: Session, user: User | None, invs: list[Invoice]) -> int:
    """After an LR is entered or corrected for a consignment (invoices of one customer on one gatepass)."""
    if not invs or not enabled(db, NotifyEvent.LR_ENTERED):
        return 0
    n = 0
    for group in _consignments(invs):
        first = group[0]
        if not first.lr_no or not first.dispatch_slip_id:
            continue
        slip = db.get(DispatchSlip, first.dispatch_slip_id)
        if slip is None or slip.is_void:
            continue
        if _queue(db, user, NotifyEvent.LR_ENTERED, slip, group, f"LR:{slip.id}:{first.customer_id}:{first.lr_no}"[:120]):
            n += 1
    return n


# ----------------------------------------------------------------------------- providers
class SendError(Exception):
    pass


def _whatsapp(db: Session, n: Notification, token: str) -> str:
    import httpx
    phone_id = cfg.get(db, "notify.whatsapp_phone_number_id")
    if not phone_id or not token:
        raise SendError("WhatsApp is not set up: phone-number ID (System Settings) and CTDMS_NOTIFY_TOKEN are needed")
    url = f"{cfg.get(db, 'notify.whatsapp_api_url').rstrip('/')}/{phone_id}/messages"
    payload = {"messaging_product": "whatsapp", "to": n.to_number, "type": "template",
               "template": {"name": cfg.get(db, "notify.whatsapp_template"),
                            "language": {"code": cfg.get(db, "notify.whatsapp_language") or "en"},
                            "components": [{"type": "body", "parameters": [{"type": "text", "text": n.body}]}]}}
    r = httpx.post(url, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    if r.status_code >= 300:
        raise SendError(f"WhatsApp API {r.status_code}: {r.text[:300]}")
    try:
        return str(r.json()["messages"][0]["id"])[:120]
    except (ValueError, KeyError, IndexError, TypeError):
        return "accepted"


def _sms(db: Session, n: Notification, token: str | None) -> str:
    import httpx
    url = cfg.get(db, "notify.http_url")
    if not url:
        raise SendError("SMS gateway URL is not set (System Settings › notify.http_url)")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = {"to": n.to_number, "text": n.body, "sender": cfg.get(db, "notify.sms_sender") or ""}
    if cfg.get(db, "notify.http_method") == "GET":
        r = httpx.get(url, params=data, headers=headers, timeout=20)
    else:
        r = httpx.post(url, json=data, headers=headers, timeout=20)
    if r.status_code >= 300:
        raise SendError(f"SMS gateway {r.status_code}: {r.text[:300]}")
    return (r.text or "accepted").strip()[:120]


def deliver(db: Session, n: Notification) -> str:
    """Send one message with the configured provider. Returns the provider's reference; raises SendError."""
    from app.config import get_settings
    prov = provider(db)
    if prov == "OFF":
        raise SendError("Notifications are switched off (System Settings › notify.provider)")
    if not n.to_number:
        raise SendError("No valid mobile / WhatsApp number in the customer master")
    if prov == "LOG":
        log.info("Notification #%s to %s: %s", n.id, n.to_number, n.body)
        return "LOG"
    token = get_settings().notify_token
    if prov == "WHATSAPP_CLOUD":
        return _whatsapp(db, n, token)
    if prov == "HTTP_SMS":
        return _sms(db, n, token)
    raise SendError(f"Unknown provider {prov}")


# ----------------------------------------------------------------------------- sending
def _claim(db: Session, n_id: int) -> bool:
    res = db.execute(update(Notification).where(Notification.id == n_id, Notification.status == NotifyStatus.QUEUED)
                     .values(status=NotifyStatus.SENDING))
    return res.rowcount == 1


def send_one(db: Session, n: Notification, user: User | None = None) -> bool:
    """Send a claimed (SENDING) message and record the outcome. Returns True when sent."""
    prov = provider(db)
    n.attempts = (n.attempts or 0) + 1
    n.provider = prov
    try:
        ref = deliver(db, n)
    except Exception as e:  # noqa: BLE001 - network errors, provider errors: record and retry later
        msg = str(e) if isinstance(e, SendError) else f"{type(e).__name__}: {e}"
        n.last_error = msg[:1000]
        max_attempts = int(cfg.get(db, "notify.max_attempts") or 3)
        if n.attempts < max_attempts and n.to_number and prov != "OFF":
            n.status = NotifyStatus.QUEUED
            n.next_attempt_at = utcnow() + timedelta(minutes=5 * n.attempts)
        else:
            n.status = NotifyStatus.FAILED
            n.next_attempt_at = None
        audit.log(db, user, "NOTIFY_FAILED", MODULE, record_type="Notification", record_id=n.id,
                  ref=f"{n.event} {n.to_number or ''}", reason=n.last_error[:300],
                  details={"attempt": n.attempts, "status": n.status, "provider": prov})
        return False
    n.status = NotifyStatus.SENT
    n.sent_at = utcnow()
    n.provider_ref = ref
    n.last_error = None
    n.next_attempt_at = None
    audit.log(db, user, "NOTIFY_SENT", MODULE, record_type="Notification", record_id=n.id,
              ref=f"{n.event} {n.to_number}", details={"provider": prov, "ref": ref, "attempt": n.attempts,
                                                       "customer_id": n.customer_id, "slip_id": n.slip_id})
    return True


def send_due(db: Session, limit: int = 50, user: User | None = None) -> dict:
    """Send queued messages whose time has come. Each message is committed on its own."""
    out = {"sent": 0, "failed": 0, "retry": 0}
    if provider(db) == "OFF":
        return out
    ids = list(db.scalars(select(Notification.id).where(
        Notification.status == NotifyStatus.QUEUED,
        (Notification.next_attempt_at.is_(None)) | (Notification.next_attempt_at <= utcnow()))
        .order_by(Notification.id).limit(limit)))
    for n_id in ids:
        if not _claim(db, n_id):
            continue  # another worker took it
        db.commit()
        n = db.get(Notification, n_id)
        ok = send_one(db, n, user)
        db.commit()
        if ok:
            out["sent"] += 1
        elif n.status == NotifyStatus.QUEUED:
            out["retry"] += 1
        else:
            out["failed"] += 1
    return out


def retry(db: Session, user: User, n: Notification) -> None:
    """A person asks for a FAILED (or waiting) message to go again now."""
    from app.services.errors import BusinessError
    if n.status == NotifyStatus.SENT:
        raise BusinessError("This message was already sent")
    if n.status == NotifyStatus.SENDING:
        raise BusinessError("This message is being sent right now")
    if not n.to_number:
        cu = n.customer
        n.to_number = (normalise_mobile(cu.whatsapp) or normalise_mobile(cu.contact_phone)) if cu else None
        if not n.to_number:
            raise BusinessError("The customer has no valid mobile / WhatsApp number - fix the customer master first")
    old = n.status
    n.status = NotifyStatus.QUEUED
    n.next_attempt_at = utcnow()
    n.attempts = min(n.attempts or 0, max(int(cfg.get(db, "notify.max_attempts") or 3) - 1, 0))
    audit.log(db, user, "NOTIFY_RETRY", MODULE, record_type="Notification", record_id=n.id,
              ref=f"{n.event} {n.to_number}", field="status", old=old, new=NotifyStatus.QUEUED)


# ----------------------------------------------------------------------------- background sender
_thread: threading.Thread | None = None
_stop = threading.Event()


def _loop() -> None:
    from app.db import SessionLocal
    while not _stop.is_set():
        wait = 60
        try:
            with SessionLocal() as db:
                wait = max(10, int(cfg.get(db, "notify.interval_seconds") or 60))
                if provider(db) != "OFF":
                    send_due(db)
                db.commit()
        except Exception:  # noqa: BLE001 - keep the sender alive
            log.exception("Notification sender failed")
        _stop.wait(wait)


def start_sender() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="ctdms-notify", daemon=True)
    _thread.start()


def stop_sender() -> None:
    _stop.set()
