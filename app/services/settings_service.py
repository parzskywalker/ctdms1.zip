"""Configurable business rules (stored in the `settings` table, editable by admins)."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting

# key: (default, type, category, description, choices)
DEFAULTS: dict[str, tuple[str, str, str, str, str | None]] = {
    "org.name": ("Demo Pharma Distributors", "str", "Organisation", "Organisation name printed on slips/reports", None),
    "org.address": ("Ring Road No. 1, Raipur, Chhattisgarh", "str", "Organisation", "Organisation address line", None),
    "dispatch.complete_requires": ("PRINTED", "choice", "Dispatch controls",
                                   "Dispatch cannot be COMPLETED unless the slip is: PRINTED / GENERATED / NONE (no control)",
                                   "PRINTED,GENERATED,NONE"),
    "dispatch.require_verify_before_slip": ("true", "bool", "Dispatch controls",
                                            "Invoice must be Verified before a Dispatch Slip can be generated", None),
    "dispatch.lr_mandatory": ("false", "bool", "Dispatch controls",
                              "LR / docket number mandatory for slip generation (transporters usually give it later)", None),
    "dispatch.vehicle_mandatory": ("false", "bool", "Dispatch controls", "Vehicle number mandatory for slip generation", None),
    "freight.source": ("TRANSPORTER", "choice", "Freight rates",
                       "Where the freight per case comes from: TRANSPORTER = transporter rate card (per area, applied on the "
                       "gatepass) / COMPANY = principal rate card (company + depot + location, applied at import)",
                       "TRANSPORTER,COMPANY"),
    "gatepass.cash_paid_default": ("true", "bool", "Dispatch controls",
                                   "Local autos and CASH transporters are marked paid in full when the gatepass is made", None),
    "inbox.enabled": ("true", "bool", "Import",
                      "Watch the drop folder (data/inbox) and import ERP exports placed there automatically", None),
    "inbox.interval_minutes": ("5", "int", "Import", "How often the drop folder is checked (minutes)", None),
    "ai.bill_reader": ("CLAUDE", "choice", "Reconciliation",
                       "Read photographed / scanned transporter bills with Claude (PAID - locked unless CTDMS_PAID_SERVICES=true "
                       "and CTDMS_ANTHROPIC_API_KEY are set) or OFF (type the lines next to the photo)", "CLAUDE,OFF"),
    "recon.match_window_days": ("3", "int", "Reconciliation",
                                "Days either side of the LR date searched when a billed LR is not in our records", None),
    "recon.rate_tolerance": ("0.50", "decimal", "Reconciliation",
                             "Billed rate per case may differ from the rate card by this much (₹) without a flag", None),
    "dispatch.allow_future_date": ("false", "bool", "Dispatch controls", "Allow dispatch date later than today", None),
    "dispatch.reprint_reason_required": ("true", "bool", "Dispatch controls", "Reason mandatory for every slip reprint", None),
    "dispatch.default_slip_size": ("A5", "choice", "Dispatch controls", "Default slip paper size", "A4,A5"),
    "dispatch.number_prefix": ("DS", "str", "Dispatch controls", "Dispatch number prefix (DS/Depot/FY/sequence)", None),
    "override.boxes_requires_approval": ("true", "bool", "Overrides",
                                         "Box-count changes by users without 'override.direct' need approval", None),
    "override.allow_self_approval": ("false", "bool", "Overrides", "Allow the requester to approve their own override", None),
    "rate.require_approval": ("true", "bool", "Freight rates", "New / revised freight rates need approval before use", None),
    "rate.allow_self_approval": ("false", "bool", "Freight rates", "Allow the creator of a rate to approve it", None),
    "rate.auto_resolve_missing": ("true", "bool", "Freight rates",
                                  "When a rate is approved, automatically re-price matching RATE MISSING invoices", None),
    "import.allow_blank_boxes": ("true", "bool", "Import",
                                 "Accept invoices without box count (they land as EXCEPTION - boxes missing)", None),
    "recon.tolerance_amount": ("10.00", "decimal", "Reconciliation", "Variance tolerance per line (₹) to auto-MATCH", None),
    "recon.tolerance_pct": ("1.0", "decimal", "Reconciliation", "Variance tolerance per bill (%) to auto-MATCH", None),
    "recon.allow_self_approval": ("false", "bool", "Reconciliation", "Allow the bill creator to approve it", None),
    "notify.provider": ("OFF", "choice", "Customer notifications",
                        "Dispatch alerts to customers: OFF (nothing is queued or sent) / LOG (queued and marked sent "
                        "without sending - for trying it out) / WHATSAPP_CLOUD (WhatsApp Business Cloud API template) "
                        "/ HTTP_SMS (your SMS gateway). WhatsApp and SMS are PAID and stay locked unless CTDMS_PAID_SERVICES=true; "
                        "the API token goes in CTDMS_NOTIFY_TOKEN (.env).",
                        "OFF,LOG,WHATSAPP_CLOUD,HTTP_SMS"),
    "notify.on_gate_out": ("true", "bool", "Customer notifications", "Tell the customer when their goods leave the gate",
                           None),
    "notify.on_lr": ("true", "bool", "Customer notifications", "Tell the customer the LR / bilty number when it is entered",
                     None),
    "notify.template_gate_out": (
        "{org}: your order is on the way. Invoice {invoices} ({cases} cases) left on {date} with {carrier}{carrier_mobile}. "
        "Gatepass {gatepass}. The LR number will follow.{track}", "str", "Customer notifications",
        "Message on gate-out. Placeholders: {org} {customer} {invoices} {companies} {cases} {carrier} "
        "{carrier_mobile} {gatepass} {date} {lr} {lr_date} {track} (' Track: <link>' when org.public_url is set) "
        "{track_url}", None),
    "notify.template_lr": (
        "{org}: LR / bilty no {lr}{lr_date} for invoice {invoices} ({cases} cases) with {carrier}{carrier_mobile}. "
        "Gatepass {gatepass}.{track}", "str", "Customer notifications",
        "Message when the LR number is entered (same placeholders)", None),
    "notify.whatsapp_api_url": ("https://graph.facebook.com/v21.0", "str", "Customer notifications",
                                "WhatsApp Cloud API base URL", None),
    "notify.whatsapp_phone_number_id": ("", "str", "Customer notifications",
                                        "WhatsApp Business phone-number ID (Meta business manager)", None),
    "notify.whatsapp_template": ("dispatch_update", "str", "Customer notifications",
                                 "Approved WhatsApp template whose body is a single {{1}} - the message text is "
                                 "sent as that parameter", None),
    "notify.whatsapp_language": ("en", "str", "Customer notifications", "Language code of the WhatsApp template", None),
    "notify.http_url": ("", "str", "Customer notifications",
                        "SMS gateway URL (HTTP_SMS). POST sends JSON {to, text, sender}; GET adds ?to=&text=&sender=",
                        None),
    "notify.http_method": ("POST", "choice", "Customer notifications", "SMS gateway HTTP method", "POST,GET"),
    "notify.sms_sender": ("CTDMS", "str", "Customer notifications", "SMS sender ID (DLT header) passed to the gateway",
                          None),
    "notify.max_attempts": ("3", "int", "Customer notifications",
                            "Sending attempts before a message is marked FAILED (retries wait 5, 10, 15 ... minutes)",
                            None),
    "notify.interval_seconds": ("60", "int", "Customer notifications", "How often queued messages are sent (seconds)",
                                None),
    "org.public_url": ("", "str", "Organisation",
                       "Address customers open for tracking links, e.g. https://dispatch.example.com (blank = no "
                       "tracking link in messages)", None),
    "delivery.expected_days": ("4", "int", "Delivery & tracking",
                               "Days from gate-out to delivery counted as on time (transit-time report, overdue list)",
                               None),
    "delivery.customer_confirm": ("true", "bool", "Delivery & tracking",
                                  "Customers may confirm receipt (received / short / damaged) on their tracking link",
                                  None),
    "delivery.link_days": ("60", "int", "Delivery & tracking", "How many days a customer tracking link keeps working",
                           None),
    "gate.eway_required_above": ("0", "decimal", "Dispatch controls",
                                 "Gate-out needs an e-way bill number for every invoice above this amount (₹). "
                                 "0 = no check. The GST limit is usually 50000.", None),
    "einvoice.signature_check": ("WARN", "choice", "Import",
                                 "E-invoice QR signature (needs the IRP public key, CTDMS_IRP_PUBLIC_KEY_FILE): "
                                 "OFF / WARN (flag invalid QRs) / BLOCK (refuse invalid QRs)", "OFF,WARN,BLOCK"),
    # v1.6 - Invoice Recognition Code (a provision; the ERP invoice number is never changed)
    "invoice_code.enabled": ("true", "bool", "Invoice Recognition Code",
                             "Make an Invoice Recognition Code for every incoming invoice - an extra internal reference "
                             "beside the original ERP invoice number, which is never changed. Off = no new codes.", None),
    "invoice_code.format": ("{company}-{ctype}-{customer}-{depot}-{serial}", "str", "Invoice Recognition Code",
                            "Pattern of the code. Parts: {company} {ctype} (customer type) {customer} {depot} "
                            "{serial} (numeric part of the ERP invoice number) {erp} {fy}. Any separator, any order.",
                            None),
    "invoice_code.serial_digits": ("6", "int", "Invoice Recognition Code",
                                   "Numeric part padded with zeros to at least this many digits (0 = as in the ERP "
                                   "number)", None),
    "invoice_code.serial_source": ("LAST_DIGITS", "choice", "Invoice Recognition Code",
                                   "Numeric part taken from the ERP invoice number: LAST_DIGITS (last group of digits, "
                                   "MB/26/001245 -> 001245) / ALL_DIGITS (every digit) / FULL (whole number)",
                                   "LAST_DIGITS,ALL_DIGITS,FULL"),
    "invoice_code.use_master_code": ("true", "bool", "Invoice Recognition Code",
                                     "When a company / depot / customer has no recognition code of its own, use its "
                                     "master code (off = show 'code not generated' until the code is filled in)", None),
    "features.sku_lines": ("false", "bool", "Import",
                           "Keep product / SKU lines from item-wise ERP exports. Postponed in v1.6: off = item-wise "
                           "exports are still accepted (rows combined into one invoice) but lines are not stored or "
                           "shown", None),
    "slip.footer_note": ("Goods dispatched in good condition. Please check seals and box count on receipt.",
                         "str", "Dispatch slip", "Footer note printed on each dispatch slip", None),
}


def _convert(value: str | None, vtype: str) -> Any:
    if value is None:
        return None
    if vtype == "bool":
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if vtype == "int":
        return int(value)
    if vtype == "decimal":
        return Decimal(str(value))
    return value


def get(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    if row is not None:
        return _convert(row.value, row.value_type)
    if key in DEFAULTS:
        d = DEFAULTS[key]
        return _convert(d[0], d[1])
    raise KeyError(key)


def set_value(db: Session, key: str, value: Any, user_id: int | None = None) -> None:
    row = db.get(Setting, key)
    if row is None:
        d = DEFAULTS.get(key, (None, "str", "General", None, None))
        row = Setting(key=key, value_type=d[1], category=d[2], description=d[3], choices=d[4])
        db.add(row)
    if isinstance(value, bool):
        value = "true" if value else "false"
    row.value = None if value is None else str(value)
    row.updated_by_id = user_id


def ensure_defaults(db: Session) -> None:
    existing = {s.key: s for s in db.scalars(select(Setting))}
    for key, (val, vtype, cat, desc, choices) in DEFAULTS.items():
        row = existing.get(key)
        if row is None:
            db.add(Setting(key=key, value=val, value_type=vtype, category=cat, description=desc, choices=choices))
        elif (row.description, row.category, row.choices) != (desc, cat, choices):
            # wording / grouping follow the application version (values are never touched)
            row.description, row.category, row.choices = desc, cat, choices
    db.flush()


def all_settings(db: Session) -> list[Setting]:
    ensure_defaults(db)
    return list(db.scalars(select(Setting).order_by(Setting.category, Setting.key)))
