"""Server-side checks for Guided Training steps.

Each check looks at the trainee's OWN practice database and confirms that the real application action was done in
this attempt of the lesson (ctx.since = when the attempt started). Clicking through pages never passes a check.
A check returns (passed, message): the message says what was found, or what is still missing and how to fix it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog, CFA, Customer, CustomerTypeMaster, DispatchSlip, ImportBatch, Invoice, MasterUpload, OutsideAuto,
    RateStatus, Setting, TransporterBill, TransporterRate, User,
)


@dataclass
class Ctx:
    since: datetime
    user: User | None = None


CHECKS: dict = {}
NEEDS: dict = {}


def check(fn):
    CHECKS[fn.__name__] = fn
    return fn


def needs(fn):
    NEEDS[fn.__name__] = fn
    return fn


def _inv(db: Session, no: str) -> Invoice | None:
    return db.scalar(select(Invoice).where(Invoice.invoice_no == no))


def _audit(db: Session, ctx: Ctx, action: str, **where) -> AuditLog | None:
    q = select(AuditLog).where(AuditLog.action == action, AuditLog.ts >= ctx.since)
    for k, v in where.items():
        q = q.where(getattr(AuditLog, k) == v)
    return db.scalar(q.order_by(AuditLog.id.desc()).limit(1))


def _slip_with(db: Session, ctx: Ctx, numbers: list[str]) -> DispatchSlip | None:
    """The gatepass made in this attempt that carries all these invoices."""
    invs = [_inv(db, n) for n in numbers]
    if not all(invs) or len({i.dispatch_slip_id for i in invs}) != 1 or not invs[0].dispatch_slip_id:
        return None
    slip = db.get(DispatchSlip, invs[0].dispatch_slip_id)
    return slip if slip and slip.generated_at >= ctx.since else None


# ============================================================================ prerequisites (practice data state)
@needs
def is_clean(db, ctx):
    n = db.scalar(select(func.count()).select_from(MasterUpload).where(MasterUpload.status == "IMPORTED"))
    return n == 0, "a master workbook has already been imported in your practice data"


@needs
def has_masters(db, ctx):
    return db.scalar(select(CFA.id).where(CFA.code == "DM-GUMA")) is not None, "the demonstration masters are missing"


@needs
def masters_no_mohan(db, ctx):
    ok, msg = has_masters(db, ctx)
    if not ok:
        return ok, msg
    have = db.scalar(select(OutsideAuto.id).where(OutsideAuto.name.ilike("%mohan%")))
    return have is None, "Mohan's e-rickshaw is already in your practice data (from an earlier attempt)"


@needs
def masters_no_invoices(db, ctx):
    ok, msg = has_masters(db, ctx)
    if not ok:
        return ok, msg
    return db.scalar(select(func.count()).select_from(Invoice)) == 0, "invoices have already been imported"


@needs
def sap_only(db, ctx):
    sap = _inv(db, "001245") is not None
    marg = _inv(db, "XY/G/2601") is not None
    return sap and not marg, "this lesson starts after the SAP import and before the MARG / CBO imports"


def _pending(db, numbers):
    invs = [_inv(db, n) for n in numbers]
    return all(i is not None and i.dispatch_slip_id is None and i.status != "CANCELLED" for i in invs)


@needs
def long_waiting(db, ctx):
    return _pending(db, ["001248", "001249", "PQR/B/0313"]), "the Bilaspur invoices are not waiting for dispatch"


@needs
def trip_waiting(db, ctx):
    return _pending(db, ["001245", "001246", "001247", "XY/G/2601"]), "the four Guma invoices are not waiting"


@needs
def box_waiting(db, ctx):
    return _pending(db, ["XY/K/2603", "PQR/K/0311", "XY/K/2604"]), "the Kamal Vihar invoices are not waiting"


@needs
def has_dispatches(db, ctx):
    n = db.scalar(select(func.count()).select_from(DispatchSlip).where(DispatchSlip.gate_out_at.is_not(None)))
    return n >= 3, "there are no completed dispatches to report on yet"


@needs
def has_open_delivery(db, ctx):
    q = select(func.count()).select_from(Invoice).where(Invoice.status == "DISPATCHED", Invoice.delivery_status.is_(None))
    return db.scalar(q) > 0, "every dispatched consignment already has its delivery recorded"


@needs
def srl_unbilled(db, ctx):
    from app.models import Transporter
    srl = db.scalar(select(Transporter.id).where(Transporter.code == "DM-SRL"))
    lr = db.scalar(select(func.count()).select_from(Invoice).where(Invoice.lr_no.is_not(None),
                                                                    Invoice.transporter_id == srl))
    billed = db.scalar(select(func.count()).select_from(TransporterBill).where(TransporterBill.transporter_id == srl))
    return lr > 0 and billed == 0, "Shree Roadlines has no unbilled consignments with LR numbers"


@needs
def has_bill(db, ctx):
    b = db.scalar(select(TransporterBill).where(TransporterBill.status.in_(["MATCHED", "VARIANCE"])))
    return b is not None, "there is no transporter bill waiting for approval"


@needs
def anything(db, ctx):
    return True, ""


# ============================================================================ lesson 2 - master workbook
@check
def workbook_previewed(db, ctx):
    up = db.scalar(select(MasterUpload).where(MasterUpload.uploaded_at >= ctx.since).order_by(MasterUpload.id.desc()))
    if not up:
        return False, "No workbook has been checked yet. Choose DEMO_v1.6_Master_Workbook.xlsx and press Check workbook (F6)."
    if "DEMO_v1.6_Master_Workbook" not in up.file_name:
        return False, f"You checked {up.file_name}. For this exercise use DEMO_v1.6_Master_Workbook.xlsx."
    t = {"new": 0, "rejected": 0, "exists": 0}
    for c in (up.counts or {}).values():
        for k in t:
            t[k] += c.get(k, 0)
    return True, f"Checked: {t['new']} rows ready, {t['exists']} already in CTDMS, {t['rejected']} rejected. Nothing is saved yet."


@check
def workbook_imported(db, ctx):
    up = db.scalar(select(MasterUpload).where(MasterUpload.status == "IMPORTED", MasterUpload.committed_at >= ctx.since))
    if not up:
        return False, "The checked workbook is not imported yet. On the result screen press Import … valid records (F6), then Y."
    n = db.scalar(select(func.count()).select_from(Customer))
    return True, f"Imported. {n} customers, the depots, transporters and rates are now in your practice CTDMS."


# ============================================================================ lesson 3 - masters, rate approval
@check
def rates_approved_by_checker(db, ctx):
    pending = db.scalar(select(func.count()).select_from(TransporterRate)
                        .where(TransporterRate.status == RateStatus.PENDING_APPROVAL))
    if pending:
        return False, f"{pending} rate-card rate(s) still wait for approval. Act as the demo checker, open Approvals and press Approve all."
    a = _audit(db, ctx, "TRATE_APPROVED")
    if not a:
        return False, "No rate was approved in this lesson yet."
    return True, f"Approved by {a.username} - a different person from the one who uploaded them (two-person rule)."


# ============================================================================ lesson 4 - new record
@check
def mohan_added(db, ctx):
    a = db.scalar(select(OutsideAuto).where(OutsideAuto.name.ilike("%mohan%")))
    if not a:
        return False, "Mohan's e-rickshaw is not saved yet. Master Settings › Local Auto Transporters › F2 New, fill it in, F6."
    if not a.is_active:
        return False, "Mohan's e-rickshaw is saved but inactive - tick Active and save again."
    if "PER_TRIP" not in (a.allowed_methods or ""):
        return False, "Mohan charges per trip - set 'Charging allowed' to Per trip and save again."
    return True, f"Saved: {a.code} · {a.name}, per trip{f', usually ₹{a.default_trip_charge:,.0f}' if a.default_trip_charge else ''}."


# ============================================================================ lesson 5 - recognition codes (SAP)
@check
def sap_imported(db, ctx):
    b = db.scalar(select(ImportBatch).where(ImportBatch.committed_at >= ctx.since, ImportBatch.file_name.ilike("%SAP%")))
    inv = _inv(db, "001245")
    if not b or not inv:
        return False, "ABC Pharma's SAP file is not imported yet: Invoice Import › choose the SAP file, ERP SAP, company ABC Pharma, Upload & preview, then Import."
    return True, f"Imported batch {b.batch_no}. Invoice 001245 keeps its number; its code is {inv.recognition_code or '(none yet)'}."


@check
def gov_fixed(db, ctx):
    ct = db.scalar(select(CustomerTypeMaster).where(CustomerTypeMaster.code == "GOVT_SUPPLY"))
    inv = _inv(db, "001250")
    if not ct or not (ct.marking or "").strip():
        return False, "Government Supply still has no recognition code. Master Settings › Customer Types › GOVT_SUPPLY, Recognition code GOV, F6."
    if not inv or not inv.recognition_code:
        return False, "The code is saved but invoice 001250 has no code yet - open Invoice Recognition Code and press Regenerate."
    return True, f"Invoice 001250 now has the code {inv.recognition_code}; its ERP number 001250 is unchanged."


# ============================================================================ lesson 6 - MARG / CBO imports
@check
def marg_imported(db, ctx):
    inv = _inv(db, "XY/G/2601")
    b = db.scalar(select(ImportBatch).where(ImportBatch.committed_at >= ctx.since, ImportBatch.file_name.ilike("%MARG%")))
    if not b or not inv:
        return False, "XYZ Healthcare's MARG file is not imported yet (ERP MARG, no company needed - the bill number tells it)."
    return True, f"XY/G/2601 → company {inv.company.code}, depot {inv.cfa.code}, recognised from the marking XY/G/."


@check
def cbo_imported(db, ctx):
    inv = _inv(db, "PQR/K/0311")
    b = db.scalar(select(ImportBatch).where(ImportBatch.committed_at >= ctx.since, ImportBatch.file_name.ilike("%CBO%")))
    if not b or not inv:
        return False, "PQR Lifesciences' CBO file is not imported yet (ERP CBO)."
    return True, f"PQR/K/0311 → {inv.company.code}, depot {inv.cfa.code}. {db.scalar(select(func.count()).select_from(Invoice))} invoices in the register."


# ============================================================================ lessons 7-9 - dispatch
def _dispatch_check(db, ctx, numbers, *, gone=False, printed=False, transporter=None, auto=None, method=None,
                    total=None, trip=None, lr=False):
    slip = _slip_with(db, ctx, numbers)
    if not slip:
        return False, f"No gatepass with {', '.join(numbers)} has been generated in this lesson yet."
    if transporter and (not slip.transporter or slip.transporter.code != transporter):
        return False, f"Gatepass {slip.dispatch_no} is not with the expected transporter ({transporter})."
    if auto and (not slip.outside_auto or slip.outside_auto.code != auto):
        return False, f"Gatepass {slip.dispatch_no} is not with the expected local auto ({auto})."
    if method and slip.auto_method != method:
        return False, f"Gatepass {slip.dispatch_no} was charged {slip.auto_method or '-'}, the exercise is {method}."
    if trip is not None and Decimal(slip.trip_charge or 0) != Decimal(trip):
        return False, f"The trip charge on {slip.dispatch_no} is ₹{slip.trip_charge}; today's trip charge is ₹{trip}. Void it and make it again."
    if total is not None and Decimal(slip.total_charge) != Decimal(total):
        return False, f"{slip.dispatch_no} totals ₹{slip.total_charge}, expected ₹{total}."
    if printed and not slip.print_count:
        return False, f"{slip.dispatch_no} is not printed yet - press F8 on the gatepass."
    if gone and not slip.gate_out_at:
        return False, f"{slip.dispatch_no} has not gone out yet - press F9 (Record gate-out), then Enter."
    if lr:
        invs = list(db.scalars(select(Invoice).where(Invoice.dispatch_slip_id == slip.id)))
        missing = sorted({i.customer.name for i in invs if not i.lr_no})
        if missing:
            return False, "LR number still missing for: " + ", ".join(missing)
    return True, f"Gatepass {slip.dispatch_no}: {slip.invoice_count} invoice(s), {slip.total_cases} cases, ₹{slip.total_charge:,.2f}."


@check
def long_generated(db, ctx):
    return _dispatch_check(db, ctx, ["001248", "001249", "PQR/B/0313"], transporter="DM-SRL")


@check
def long_printed(db, ctx):
    return _dispatch_check(db, ctx, ["001248", "001249", "PQR/B/0313"], transporter="DM-SRL", printed=True)


@check
def long_gone(db, ctx):
    return _dispatch_check(db, ctx, ["001248", "001249", "PQR/B/0313"], transporter="DM-SRL", printed=True, gone=True)


@check
def long_lr(db, ctx):
    return _dispatch_check(db, ctx, ["001248", "001249", "PQR/B/0313"], transporter="DM-SRL", gone=True, lr=True)


TRIP_INVS = ["001245", "001246", "001247", "XY/G/2601"]


@check
def trip_generated(db, ctx):
    return _dispatch_check(db, ctx, TRIP_INVS, auto="DM-LA1", method="PER_TRIP", trip="500", total="500")


@check
def trip_gone(db, ctx):
    return _dispatch_check(db, ctx, TRIP_INVS, auto="DM-LA1", method="PER_TRIP", trip="500", printed=True, gone=True)


@check
def trip_allocation(db, ctx):
    slip = _slip_with(db, ctx, TRIP_INVS)
    if not slip:
        return False, "The per-trip gatepass is not made yet."
    invs = list(db.scalars(select(Invoice).where(Invoice.dispatch_slip_id == slip.id)))
    shares = sum((i.freight_amount or Decimal(0)) for i in invs)
    parts = ", ".join(f"{i.invoice_no} {i.boxes} boxes ₹{i.freight_amount:,.2f}" for i in invs)
    if abs(shares - Decimal(slip.trip_charge or 0)) > Decimal("0.01"):
        return False, f"Shares add up to ₹{shares}, not the trip charge ₹{slip.trip_charge}."
    return True, f"{parts} - together ₹{shares:,.2f}, exactly the one trip charge."


BOX_INVS = ["XY/K/2603", "PQR/K/0311"]


@check
def box_generated(db, ctx):
    ok, msg = _dispatch_check(db, ctx, BOX_INVS, auto="DM-LA2", method="PER_CASE", total="432")
    if ok and _inv(db, "XY/K/2604").dispatch_slip_id:
        return False, "XY/K/2604 (Durg) should not be on Ravi Tempo's trip - there is no per-box rate for Durg."
    return ok, msg


@check
def box_gone(db, ctx):
    return _dispatch_check(db, ctx, BOX_INVS, auto="DM-LA2", method="PER_CASE", total="432", printed=True, gone=True)


# ============================================================================ lesson 10 - reports
@check
def report_exported(db, ctx):
    a = _audit(db, ctx, "REPORT_EXPORTED")
    if not a:
        return False, "No report has been exported in this lesson yet. On a report press Excel, CSV or PDF."
    return True, f"Exported {a.ref} as {(a.details or {}).get('format', '').upper()} - marked TRAINING, not valid for business use."


# ============================================================================ lesson 11 - users
@check
def trainee_user_created(db, ctx):
    a = _audit(db, ctx, "USER_CREATED")
    if not a:
        return False, "No user has been created in your practice CTDMS yet: Administration › Users › F2 New user."
    u = db.scalar(select(User).where(User.username == a.ref))
    if not u:
        return False, "The new user was not found."
    if not u.role or u.role.code != "DISPATCH_OPERATOR":
        return False, f"{u.username} has the role {u.role.name if u.role else '-'}; the exercise is a Dispatch Operator."
    if u.all_cfas or not any(c.code == "DM-GUMA" for c in u.cfas):
        return False, f"{u.username} should see only the Guma depot - untick 'all depots' and tick DM-GUMA."
    return True, f"{u.username}: Dispatch Operator for Guma Depot only (practice user - no real login was created)."


# ============================================================================ lesson 12 - settings, rate approval
@check
def setting_changed(db, ctx):
    a = _audit(db, ctx, "SETTING_CHANGED")
    if not a:
        return False, "No setting has been changed yet: Administration › System Settings, change one value, give a reason, F6."
    return True, f"{a.ref}: {a.old_value} → {a.new_value} (practice settings only; recorded in the audit log)."


@check
def rate_proposed(db, ctx):
    r = db.scalar(select(TransporterRate).where(TransporterRate.created_at >= ctx.since,
                                                TransporterRate.status == RateStatus.PENDING_APPROVAL))
    if not r:
        return False, "No rate change is waiting yet: Transporter Rate Cards › Shree Roadlines, change one rate, give a reason, Send for approval."
    return True, f"Rate change waiting for approval (proposed by {r.created_by.username if r.created_by else '?'})."


@check
def rate_second_person(db, ctx):
    r = db.scalar(select(TransporterRate).where(TransporterRate.created_at >= ctx.since,
                                                TransporterRate.approved_by_id.is_not(None)))
    if not r:
        return False, "Your rate change is not approved yet. Act as the demo checker and approve it on Approvals."
    if r.approved_by_id == r.created_by_id:
        return False, "The same person proposed and approved - that is not allowed."
    return True, f"Proposed by {r.created_by.username}, approved by {r.approved_by.username}: two different people."


# ============================================================================ lesson 13 - delivery
@check
def delivery_recorded(db, ctx):
    inv = db.scalar(select(Invoice).where(Invoice.pod_at >= ctx.since))
    if not inv:
        return False, "No delivery has been recorded yet: Dispatch desk › Deliveries Pending, tick a consignment, Mark delivered."
    return True, f"{inv.invoice_no}: {inv.delivery_label} on {inv.delivered_on:%d-%m-%Y}."


# ============================================================================ lesson 14 - bills
@check
def bill_entered(db, ctx):
    b = db.scalar(select(TransporterBill).where(TransporterBill.created_at >= ctx.since).order_by(TransporterBill.id))
    if not b:
        return False, "No transporter bill entered yet: Operations › Transporter Bills › Enter / import bill."
    if not b.lines:
        return False, f"Bill {b.bill_no} has no lines yet - link the dispatches of the period."
    return True, f"Bill {b.bill_no}: {len(b.lines)} line(s), billed ₹{b.bill_amount:,.2f} vs calculated ₹{b.calculated_amount:,.2f} - {b.status}."


@check
def bill_matched(db, ctx):
    b = db.scalar(select(TransporterBill).where(TransporterBill.created_at >= ctx.since).order_by(TransporterBill.id))
    if not b:
        return False, "Enter the bill first."
    open_lines = [ln.lr_no or ln.invoice_no for ln in b.lines if ln.billed_amount is None]
    if open_lines:
        return False, "Billed amount still missing for: " + ", ".join(str(x) for x in open_lines)
    if b.status not in ("MATCHED", "VARIANCE"):
        return False, f"Bill {b.bill_no} is {b.status} - check the lines marked in red."
    return True, (f"Bill {b.bill_no}: billed ₹{b.bill_amount:,.2f}, calculated ₹{b.calculated_amount:,.2f}, "
                  f"variance ₹{b.variance:,.2f} - {b.status}.")


@check
def bill_rates_reviewed(db, ctx):
    b = db.scalar(select(TransporterBill).where(TransporterBill.status.in_(["MATCHED", "VARIANCE"]))
                  .order_by(TransporterBill.id.desc()))
    if not b:
        return False, "There is no bill waiting for its rate review."
    if not b.rates_reviewed_at:
        return False, f"Bill {b.bill_no}: press Save rate review on the bill first."
    return True, f"Bill {b.bill_no}: rates reviewed by {b.rates_reviewed_by.username if b.rates_reviewed_by else '?'}."


@check
def bill_approved_by_other(db, ctx):
    b = db.scalar(select(TransporterBill).where(TransporterBill.approved_at >= ctx.since))
    if not b:
        return False, "The bill is not approved yet. Approval needs a second person: act as the demo checker, review the rates, approve."
    if b.approved_by_id == b.created_by_id:
        return False, "The person who entered the bill approved it - not allowed."
    return True, f"Bill {b.bill_no} approved by {b.approved_by.username if b.approved_by else '?'} (entered by {b.created_by.username if b.created_by else '?'})."


@check
def bill_paid(db, ctx):
    b = db.scalar(select(TransporterBill).where(TransporterBill.paid_at >= ctx.since))
    if not b:
        return False, "The approved bill is not marked paid yet: give a payment reference (UTR / cheque) and press Mark paid."
    return True, f"Bill {b.bill_no} paid, reference {b.payment_ref} (practice only - no money moves)."


# ============================================================================ lesson 15 / checker / depot
@check
def audit_viewed(db, ctx):
    return True, "The audit log lists every change with who, when, old and new value."


@check
def override_decided(db, ctx):
    from app.models import InvoiceOverride
    o = db.scalar(select(InvoiceOverride).where(InvoiceOverride.decided_at >= ctx.since))
    if not o:
        return False, "The box-change request is not decided yet (Approvals › Approve or Reject, with remarks)."
    return True, (f"{o.invoice.invoice_no}: boxes {o.old_value} → {o.new_value} {o.status.lower()} by "
                  f"{o.decided_by.username if o.decided_by else '?'} (requested by "
                  f"{o.requested_by.username if o.requested_by else '?'}).")


@check
def setting_exists(db, ctx):
    return db.scalar(select(Setting.key).limit(1)) is not None, ""


@needs
def rates_waiting(db, ctx):
    n = db.scalar(select(func.count()).select_from(TransporterRate)
                  .where(TransporterRate.status == RateStatus.PENDING_APPROVAL))
    return n > 0, "no freight rate is waiting for approval"


@needs
def has_override(db, ctx):
    from app.models import InvoiceOverride
    o = db.scalar(select(InvoiceOverride.id).where(InvoiceOverride.status == "PENDING"))
    return o is not None, "no box-change request is waiting"


@needs
def kishore_waiting_at_gate(db, ctx):
    inv = _inv(db, "XY/G/2602")
    slip = db.get(DispatchSlip, inv.dispatch_slip_id) if inv and inv.dispatch_slip_id else None
    return bool(slip and slip.print_count and not slip.gate_out_at), "no printed gatepass is waiting at the gate"


@check
def kishore_gone(db, ctx):
    inv = _inv(db, "XY/G/2602")
    slip = db.get(DispatchSlip, inv.dispatch_slip_id) if inv and inv.dispatch_slip_id else None
    if not slip or not slip.gate_out_at or slip.gate_out_at < ctx.since:
        return False, "The Kishore Mini Truck gatepass has not been confirmed at the gate yet (Gate check, F9)."
    return True, f"{slip.dispatch_no} confirmed at the gate: {slip.total_cases} cases counted, invoices DISPATCHED."
