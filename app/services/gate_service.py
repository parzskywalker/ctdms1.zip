"""Walking a gatepass through the real gate steps in code (v1.7.2).

Gone out is recorded only by the gate staff's scan: every case label, then the gatepass. The demonstration data, the
Guided Training scenario and the automated tests need many gatepasses that have really gone out; this does exactly
what the people do - confirm the labels, print the gatepass and the Garage Pawatis, scan every case, scan the gatepass -
through the same services and the same checks, so nothing is bypassed. It needs a user who may print (operator) and a
user who may scan (gate staff); it is never called by a screen.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import CaseStatus, DispatchSlip, User
from app.services import case_service as cs
from app.services import dispatch_service as ds
from app.services import pawati_service as pws


def prepare(db: Session, operator: User, slip: DispatchSlip, *, output: str = "PRINT_A5") -> None:
    """Labels printed, then the gatepass, then the Garage Pawatis (what the operator does before the trip is at the gate)."""
    pending = [c.id for c in cs.cases_of(db, slip) if c.status == CaseStatus.LABEL_PENDING]
    if pending:
        cs.record_print(db, operator, slip, pending, result="PRINTED")
    if not slip.print_count:
        ds.print_gatepass(db, operator, slip, output)
    rows = [p for p in pws.active(db, slip) if not p.print_count]
    if rows:
        pws.print_sheet(db, operator, rows)
    db.flush()


def scan_out(db: Session, gate: User, slip: DispatchSlip, *, station: str = "gate") -> int:
    """The gate staff scan every case, then the gatepass. Returns the number of invoices that left."""
    for case in cs.cases_of(db, slip):
        if case.status in (CaseStatus.SCANNED, CaseStatus.DISPATCHED):
            continue
        res = cs.scan(db, gate, case.case_code, slip, station=station)
        if not res["ok"]:
            raise ds.BusinessError(f"Scan of case {case.case_code} refused: {res['message']}")
    return ds.gate_scan_out(db, gate, slip)


def leave(db: Session, operator: User, gate: User, slip: DispatchSlip) -> int:
    """prepare() then scan_out(): a gatepass that has really gone out."""
    prepare(db, operator, slip)
    return scan_out(db, gate, slip)
