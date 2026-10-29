"""Case identity and labels (v1.7): every physical carton of a gatepass gets its own barcode.

* Code = 9-digit number + 1 check digit, digits only (Code 128). Digits only because keyboard-wedge scanners mis-type
  letters under some keyboard layouts / Caps Lock. The invoice, company and customer are stored on the case and printed
  as text - the code never replaces the invoice number.
* Codes of the live database stay below 900,000,000; a Guided Training practice database starts at 900,000,000, so a
  practice label can never be mistaken for a real one at the gate.
* A gatepass reserves all its numbers with ONE counter update (block reservation).
* Case control is mandatory (v1.7.2): every new gatepass gets its cases (`case_control = REQUIRED`). A gatepass made
  before v1.7 (`LEGACY`) that has not gone out yet cannot leave until `convert_legacy` has made its case labels.
* A reprint keeps the code. A voided gatepass / cancelled invoice voids its cases; a regenerated gatepass gets new codes.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CaseScan, CaseStatus, DispatchCase, DispatchSlip, Invoice, LabelPrintLog, User, utcnow
from app.services import audit
from app.services.errors import BusinessError
from app.services.utils import next_sequence_block

MODULE = "CASE"
TRAINING_BASE = 900_000_000
LIVE_LIMIT = TRAINING_BASE - 1


def check_digit(body: str) -> str:
    """Luhn (mod 10) check digit of a digit string."""
    total, double = 0, True
    for ch in reversed(body):
        d = int(ch)
        if double:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        double = not double
    return str((10 - total % 10) % 10)


def make_code(n: int, *, training: bool = False) -> str:
    if n < 1 or n > (TRAINING_BASE // 10 if training else LIVE_LIMIT):
        raise BusinessError("Case number range exhausted - contact support")
    body = f"{(TRAINING_BASE + n) if training else n:09d}"
    return body + check_digit(body)


def is_valid(code: str) -> bool:
    c = (code or "").strip()
    return len(c) == 10 and c.isdigit() and check_digit(c[:9]) == c[9]


def is_training(code: str) -> bool:
    return is_valid(code) and code.strip().startswith("9")


def _training() -> bool:
    from app.training import runtime
    return runtime.active() is not None


# ----------------------------------------------------------------------------- generation / voiding
def generate(db: Session, user: User, slip: DispatchSlip, *, lines: list | None = None, action: str = "CASES_GENERATED",
             reason: str | None = None) -> int:
    """Create the cases of a gatepass (one per carton), reserving their numbers in one step."""
    lines = [ln for ln in (slip.lines if lines is None else lines) if (ln.cases or 0) > 0]
    total = sum(ln.cases for ln in lines)
    if not total:
        return 0
    training = _training()
    first = next_sequence_block(db, "CASE:TRAINING" if training else "CASE:LIVE", total)
    per_cons: dict[int, int] = {}
    cons_total: dict[int, int] = {}
    for ln in lines:
        cons_total[ln.consignment_no or 0] = cons_total.get(ln.consignment_no or 0, 0) + ln.cases
    n = first
    from app.models import DispatchAllocation
    allocs: dict[int, list[int]] = {}  # line id -> one allocation id per case (receivers get their cases in order)
    for a in db.scalars(select(DispatchAllocation).where(DispatchAllocation.slip_id == slip.id).order_by(DispatchAllocation.id)):
        allocs.setdefault(a.line_id, []).extend([a.id] * a.cases)
    for ln in lines:
        key = ln.consignment_no or 0
        for k in range(1, ln.cases + 1):
            per_cons[key] = per_cons.get(key, 0) + 1
            owners = allocs.get(ln.id) or []
            db.add(DispatchCase(case_code=make_code(n, training=training), invoice_id=ln.invoice_id, slip_id=slip.id,
                                line_id=ln.id, seq_in_invoice=k, seq_in_consignment=per_cons[key],
                                consignment_total=cons_total[key], allocation_id=owners[k - 1] if k <= len(owners) else None))
            n += 1
    slip.case_control = "REQUIRED"
    db.flush()
    audit.log(db, user, action, MODULE, record_type="DispatchSlip", record_id=slip.id, ref=slip.dispatch_no, reason=reason,
              details={"cases": total, "first": make_code(first, training=training),
                       "last": make_code(first + total - 1, training=training)})
    return total


def convert_legacy(db: Session, user: User, slip: DispatchSlip) -> int:
    """A gatepass made before case labels were mandatory (LEGACY) that is still at the depot gets its case labels now:
    one barcode per carton still to leave. Nothing else about the gatepass changes; its labels are then printed and the
    gate staff scan them like any other gatepass."""
    if not user.can("case.print"):
        raise BusinessError("You are not authorised to make case labels")
    if slip.is_void:
        raise BusinessError(f"Gatepass {slip.dispatch_no} is void - no labels")
    if slip.gate_out_at:
        raise BusinessError(f"Gatepass {slip.dispatch_no} has already gone out")
    if slip.case_control == "REQUIRED" or cases_of(db, slip, include_void=True):
        raise BusinessError(f"Gatepass {slip.dispatch_no} already has its case labels")
    open_lines = [ln for ln in slip.lines if (ln.cases or 0) > 0 and ln.invoice is not None
                  and ln.invoice.status not in ("DISPATCHED", "CANCELLED")]
    n = generate(db, user, slip, lines=open_lines, action="CASES_GENERATED_LEGACY",
                 reason="Gatepass made before case labels were mandatory")
    if not n:
        raise BusinessError(f"Gatepass {slip.dispatch_no} has no cases to label - void it and make it again")
    return n


def void_for_slip(db: Session, slip: DispatchSlip) -> int:
    return _void(db, select(DispatchCase).where(DispatchCase.slip_id == slip.id))


def void_for_invoice(db: Session, slip_id: int | None, invoice_id: int) -> int:
    stmt = select(DispatchCase).where(DispatchCase.invoice_id == invoice_id)
    if slip_id:
        stmt = stmt.where(DispatchCase.slip_id == slip_id)
    return _void(db, stmt)


def _void(db: Session, stmt) -> int:
    n = 0
    for c in db.scalars(stmt.where(DispatchCase.status != CaseStatus.VOID, DispatchCase.status != CaseStatus.DISPATCHED)):
        c.status = CaseStatus.VOID
        c.voided_at = utcnow()
        n += 1
    return n


# ----------------------------------------------------------------------------- lookups
def find_case(db: Session, code: str) -> DispatchCase | None:
    c = (code or "").strip()
    if not is_valid(c):
        return None
    return db.scalar(select(DispatchCase).where(DispatchCase.case_code == c))


def cases_of(db: Session, slip: DispatchSlip, *, include_void: bool = False) -> list[DispatchCase]:
    stmt = select(DispatchCase).where(DispatchCase.slip_id == slip.id)
    if not include_void:
        stmt = stmt.where(DispatchCase.status != CaseStatus.VOID)
    return list(db.scalars(stmt.order_by(DispatchCase.id)))


def counts(db: Session, slip: DispatchSlip) -> dict[str, int]:
    out = {s: 0 for s in CaseStatus.LABELS}
    for st, n in db.execute(select(DispatchCase.status, func.count()).where(DispatchCase.slip_id == slip.id)
                            .group_by(DispatchCase.status)):
        out[st] = n
    return out


# ----------------------------------------------------------------------------- printing (printed / failed confirmation)
def record_print(db: Session, user: User, slip: DispatchSlip, case_ids: list[int], *, result: str,
                 reason: str | None = None, output: str = "THERMAL") -> int:
    """The operator confirms what came out of the printer. PRINTED labels the cases; FAILED is logged and the cases stay
    unlabelled (print them again - same code). Printing cases that were already labelled is a REPRINT: it needs
    `case.reprint` and a reason, and is audited."""
    if slip.is_void:
        raise BusinessError(f"Gatepass {slip.dispatch_no} is void - no labels")
    if result not in ("PRINTED", "FAILED"):
        raise BusinessError("Say whether the labels were printed or failed")
    cases = [c for c in cases_of(db, slip) if c.id in set(case_ids)]
    if not cases:
        raise BusinessError("No cases selected")
    reprint = any(c.status != CaseStatus.LABEL_PENDING for c in cases)
    if reprint:
        if not user.can("case.reprint"):
            raise BusinessError("You are not authorised to reprint case labels")
        if len((reason or "").strip()) < 5:
            raise BusinessError("Give a reason (at least 5 characters) for reprinting case labels")
    elif not user.can("case.print"):
        raise BusinessError("You are not authorised to print case labels")
    batch = (db.scalar(select(func.max(LabelPrintLog.batch_no)).where(LabelPrintLog.slip_id == slip.id)) or 0) + 1
    for c in cases:
        db.add(LabelPrintLog(slip_id=slip.id, batch_no=batch, case_id=c.id, is_reprint=reprint,
                             reason=(reason or "").strip() or None, output=output, status=result,
                             printed_by_id=user.id))
        if result == "PRINTED" and c.status == CaseStatus.LABEL_PENDING:
            c.status = CaseStatus.LABELLED
    audit.log(db, user, "LABELS_REPRINTED" if reprint else ("LABELS_PRINTED" if result == "PRINTED" else "LABELS_FAILED"),
              MODULE, record_type="DispatchSlip", record_id=slip.id, ref=slip.dispatch_no, reason=reason,
              details={"batch": batch, "cases": len(cases), "result": result})
    db.flush()
    return batch


def label_rows(db: Session, slip: DispatchSlip, cases: list[DispatchCase]) -> list[dict]:
    """Everything printed on one label, from the stored case + gatepass line (never recomputed)."""
    inv_cache: dict[int, Invoice] = {}
    rows = []
    for c in cases:
        inv = inv_cache.get(c.invoice_id) or db.get(Invoice, c.invoice_id)
        inv_cache[c.invoice_id] = inv
        rows.append({"case": c, "invoice": inv, "customer": inv.customer, "location": inv.location,
                     "company": inv.company})
    return rows


# ----------------------------------------------------------------------------- gate verification (Phase 5)
SCAN_OK, SCAN_DUP = "OK", "DUPLICATE"
SCAN_MESSAGES = {
    "INVALID": "Not a CTDMS case code (misread? scan again)",
    "TRAINING": "TRAINING label - not valid for real goods",
    "UNKNOWN": "Unknown case code - do not load",
    "VOID": "VOID label - this case was cancelled",
    "WRONG_GATEPASS": "Belongs to another gatepass",
    "GATE_CLOSED": "Gatepass is void - the goods must not leave",
    "ALREADY_OUT": "This gatepass has already gone out",
    "NO_CASES": "This gatepass has no case labels (count it by hand)",
}


def progress(db: Session, slip: DispatchSlip) -> dict:
    """Scanned / total for the gatepass and per invoice, and the cases still missing."""
    cases = cases_of(db, slip)
    done = (CaseStatus.SCANNED, CaseStatus.DISPATCHED)
    per: dict[int, dict] = {}
    for c in cases:
        row = per.setdefault(c.invoice_id, {"invoice": c.invoice, "total": 0, "scanned": 0})
        row["total"] += 1
        row["scanned"] += c.status in done
    scanned = sum(r["scanned"] for r in per.values())
    missing = [c for c in cases if c.status not in done]
    return {"total": len(cases), "scanned": scanned, "per_invoice": list(per.values()), "missing": missing,
            "complete": bool(cases) and not missing, "exception": bool(slip.gate_exception_reason)}


def needs_conversion(slip: DispatchSlip) -> bool:
    """An open gatepass made before case labels were mandatory: its labels must be made before anything else."""
    return slip.case_control != "REQUIRED" and not slip.is_void and not slip.gate_out_at


def labels_pending(db: Session, slip: DispatchSlip) -> int:
    """Cases whose label has not been printed (and confirmed) yet; an open LEGACY gatepass has none made at all, so all
    of its cartons count."""
    if needs_conversion(slip):
        return sum((ln.cases or 0) for ln in slip.lines if ln.invoice is not None
                   and ln.invoice.status not in ("DISPATCHED", "CANCELLED")) or (slip.total_cases or 0)
    if slip.case_control != "REQUIRED":
        return 0
    return sum(1 for c in cases_of(db, slip) if c.status == CaseStatus.LABEL_PENDING)


def gate_clear(db: Session, slip: DispatchSlip) -> list[str]:
    """What still stops a gatepass from leaving (empty = cleared)."""
    if needs_conversion(slip):
        return [f"Gatepass {slip.dispatch_no} has no case labels yet - make them first (gatepass screen > Make the case "
                "labels for this gatepass)"]
    if slip.case_control != "REQUIRED":
        return []
    p = progress(db, slip)
    if p["complete"] or slip.gate_exception_reason:
        return []
    return [f"Gatepass {slip.dispatch_no}: {len(p['missing'])} of {p['total']} cases not scanned at the gate - scan "
            "them, or have an authorised person record an exception"]


def scan(db: Session, user: User, code: str, slip: DispatchSlip | None = None, *, station: str | None = None) -> dict:
    """One scan at the gate. Always recorded (case_scans); returns {result, ok, message, slip_id, case}."""
    raw = (code or "").strip()
    case = None
    result = None
    if not is_valid(raw):
        result = "INVALID"
    elif is_training(raw) and not _training():
        result = "TRAINING"
    else:
        case = find_case(db, raw)
        if case is None:
            result = "UNKNOWN"
    target = slip
    if result is None:
        if case.status == CaseStatus.VOID:
            result = "VOID"
        elif target is not None and case.slip_id != target.id:
            result = "WRONG_GATEPASS"
        else:
            target = target or db.get(DispatchSlip, case.slip_id)
            if target.is_void:
                result = "GATE_CLOSED"
            elif target.gate_out_at:
                result = "ALREADY_OUT"
            elif case.status in (CaseStatus.SCANNED, CaseStatus.DISPATCHED):
                result = SCAN_DUP
            else:
                case.status = CaseStatus.SCANNED
                target.verified_cases = (target.verified_cases or 0) + 1
                result = SCAN_OK
    db.add(CaseScan(slip_id=(target.id if target else (case.slip_id if case else None)), case_id=case.id if case else None,
                    scanned_code=raw[:60], result=result, scanned_by_id=user.id, station=station))
    db.flush()
    msg = SCAN_MESSAGES.get(result, "")
    if result == "WRONG_GATEPASS":
        other = db.get(DispatchSlip, case.slip_id)
        msg = f"Belongs to gatepass {other.dispatch_no} - not this one"
    elif result == SCAN_DUP:
        msg = "Already scanned - same case twice"
    elif result == SCAN_OK:
        msg = f"Case {case.seq_in_consignment}/{case.consignment_total} of invoice {case.invoice.invoice_no}"
    return {"result": result, "ok": result == SCAN_OK, "message": msg, "case": case,
            "slip_id": target.id if target else (case.slip_id if case else None)}


def record_exception(db: Session, user: User, slip: DispatchSlip, reason: str) -> int:
    """An authorised person lets the gatepass go with cases not scanned: reason mandatory, the cases are marked MISSING."""
    if not user.can("gate.exception"):
        raise BusinessError("You are not authorised to record a gate exception")
    if slip.is_void or slip.gate_out_at:
        raise BusinessError("This gatepass is closed")
    if len((reason or "").strip()) < 5:
        raise BusinessError("Give the reason (at least 5 characters) - it is written to the audit log")
    p = progress(db, slip)
    if not p["missing"]:
        raise BusinessError("Every case is scanned - no exception needed")
    for c in p["missing"]:
        c.status = CaseStatus.MISSING
    slip.gate_exception_reason = reason.strip()
    slip.gate_exception_by_id = user.id
    slip.gate_exception_at = utcnow()
    audit.log(db, user, "GATE_EXCEPTION", MODULE, record_type="DispatchSlip", record_id=slip.id, ref=slip.dispatch_no,
              reason=reason, details={"missing": [c.case_code for c in p["missing"]], "scanned": p["scanned"],
                                      "total": p["total"]})
    return len(p["missing"])


def mark_dispatched(db: Session, slip_id: int, invoice_id: int | None = None) -> None:
    stmt = select(DispatchCase).where(DispatchCase.slip_id == slip_id, DispatchCase.status == CaseStatus.SCANNED)
    if invoice_id:
        stmt = stmt.where(DispatchCase.invoice_id == invoice_id)
    for c in db.scalars(stmt):
        c.status = CaseStatus.DISPATCHED
