"""The real gate flow, in two calls, for everything that builds finished trips by code (demo seed, Guided Training, tests).

* `prepare`   - what the dispatch desk does after making a gatepass: print the case labels, then the gatepass, then the
                Garage Pawatis that travel with the auto.
* `scan_out`  - what the gate staff do: scan every case label, then the gatepass. Nothing else marks a trip gone out.

Both go through the same services the screens use (case_service, dispatch_service, pawati_service), so a trip built here
passes exactly the rules a trip made on the screens passes.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import CaseStatus, DispatchSlip, User
from app.services import case_service as cs
from app.services import dispatch_service as ds
from app.services import pawati_service as pws
from app.services.errors import BusinessError


def print_labels(db: Session, user: User, slip: DispatchSlip) -> int:
    """Print (and confirm) every case label that is not printed yet. Returns how many were printed."""
    ids = [c.id for c in cs.cases_of(db, slip) if c.status == CaseStatus.LABEL_PENDING]
    if ids:
        cs.record_print(db, user, slip, ids, result="PRINTED")
    return len(ids)


def print_pawatis(db: Session, user: User, slip: DispatchSlip) -> int:
    rows = pws.active(db, slip)
    if rows:
        pws.print_sheet(db, user, rows)
        db.flush()
    return len(rows)


def prepare(db: Session, user: User, slip: DispatchSlip, *, output: str = "PRINT_A5", pawatis: bool = True) -> DispatchSlip:
    """Labels printed -> gatepass printed -> Pawatis printed: the trip is ready to be scanned at the gate."""
    if cs.needs_conversion(slip):
        cs.convert_legacy(db, user, slip)
    print_labels(db, user, slip)
    ds.print_gatepass(db, user, slip, output)
    if pawatis:
        print_pawatis(db, user, slip)
    return slip


def scan_cases(db: Session, gate_user: User, slip: DispatchSlip, *, station: str = "gate") -> int:
    """Scan every labelled case that is not scanned yet; a refused scan is an error (the trip is not ready). Cases that
    are missing (a recorded gate exception), already dispatched or without a printed label are left as they are."""
    n = 0
    for c in cs.cases_of(db, slip):
        if c.status != CaseStatus.LABELLED:
            continue
        res = cs.scan(db, gate_user, c.case_code, slip, station=station)
        if not res["ok"]:
            raise BusinessError(f"Case {c.case_code} of {slip.dispatch_no} was refused at the gate: {res['message']}")
        n += 1
    return n


def scan_out(db: Session, gate_user: User, slip: DispatchSlip, *, station: str = "gate") -> int:
    """Every case, then the gatepass: the trip goes out. Returns how many invoices became DISPATCHED."""
    scan_cases(db, gate_user, slip, station=station)
    return ds.gate_scan_out(db, gate_user, slip)
