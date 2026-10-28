"""Which transporters may carry a gatepass (v1.7): only the eligible ones are offered, and the server re-checks.

A transporter is eligible for a set of invoices when
  * it is active,
  * it serves the depot (`transporter_depots`; NO rows = serves every depot, so a v1.6.1 installation is unchanged),
  * and - when freight comes from the transporter rate card - it has an approved rate, effective on the dispatch date,
    for EVERY delivery area on the gatepass (an exception for the invoice's depot / company counts).
With invoices for several areas the eligible set is therefore the intersection of the transporters of each area.
The override (a transporter that does not serve the depot) needs `transporter.override_eligibility`, a reason, and is
audited by `dispatch_service.create_gatepass`; a missing rate is never overridden here (add the rate, or use the
audited manual-freight request on the invoice).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.models import CFA, Invoice, Transporter, User, transporter_depots
from app.services import audit
from app.services import transporter_rate_service as trs
from app.services.errors import BusinessError

MODULE = "TRANSPORTER_ELIGIBILITY"


def depot_ids(db: Session, transporter_id: int) -> set[int]:
    return set(db.scalars(select(transporter_depots.c.cfa_id).where(transporter_depots.c.transporter_id == transporter_id)))


def serves_depot(db: Session, transporter_id: int, cfa_id: int | None) -> bool:
    ids = depot_ids(db, transporter_id)
    return not ids or cfa_id is None or cfa_id in ids


@dataclass
class Candidate:
    transporter: Transporter
    why: list[str] = field(default_factory=list)  # empty = eligible
    serves_depot: bool = True
    missing_areas: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.why


def evaluate(db: Session, invs: list[Invoice], on_date: date, *, require_rate: bool = True,
             include_inactive: bool = False) -> list[Candidate]:
    """Every transporter judged for these invoices (they must share one depot)."""
    cfa_ids = {i.cfa_id for i in invs}
    cfa = db.get(CFA, next(iter(cfa_ids))) if len(cfa_ids) == 1 else None
    stmt = select(Transporter).order_by(Transporter.name)
    if not include_inactive:
        stmt = stmt.where(Transporter.is_active.is_(True))
    out: list[Candidate] = []
    for t in db.scalars(stmt):
        c = Candidate(t)
        if cfa is not None and not serves_depot(db, t.id, cfa.id):
            c.serves_depot = False
            c.why.append(f"does not serve {cfa.name}")
        if require_rate and invs:
            missing: dict[int, str] = {}
            for inv in invs:
                if not trs.find(db, t.id, inv.location_id, on_date, cfa_id=inv.cfa_id, company_id=inv.company_id).found:
                    missing[inv.location_id] = inv.location.name
            if missing:
                c.missing_areas = sorted(missing.values())
                c.why.append("no approved rate for " + ", ".join(c.missing_areas))
        out.append(c)
    return out


def eligible(db: Session, invs: list[Invoice], on_date: date, *, require_rate: bool = True) -> list[Transporter]:
    return [c.transporter for c in evaluate(db, invs, on_date, require_rate=require_rate) if c.ok]


def explain_none(db: Session, invs: list[Invoice], on_date: date, *, require_rate: bool = True) -> str:
    """Why nobody is eligible, in words for the operator (named areas and depot, what to do next)."""
    cands = evaluate(db, invs, on_date, require_rate=require_rate)
    if not cands:
        return "There is no active transporter - ask for a Master update."
    cfa = invs[0].cfa if invs else None
    areas = sorted({i.location.name for i in invs})
    per_area_ok = []
    if len(areas) > 1 and require_rate:  # is it the mix of areas that leaves nobody?
        for name in areas:
            sub = [i for i in invs if i.location.name == name]
            per_area_ok.append((name, [c.transporter.name for c in evaluate(db, sub, on_date, require_rate=True) if c.ok]))
        if all(ok for _, ok in per_area_ok):
            return ("No single transporter serves all of " + ", ".join(areas) + ". Split into separate gatepasses - "
                    + "; ".join(f"{n}: {', '.join(ok[:3])}" for n, ok in per_area_ok))
    return (f"No transporter is eligible for {', '.join(areas)} from {cfa.name if cfa else 'this depot'}. Ask for a "
            "Master update (rate card / depots served)" + (", or a Manager can override a depot restriction." if
                                                           any(not c.serves_depot for c in cands) else "."))


# ----------------------------------------------------------------------------- master maintenance
def set_depots(db: Session, user: User, transporter: Transporter, cfa_ids: list[int]) -> None:
    """The depots this transporter serves. Empty = every depot."""
    if not user.can("master.edit"):
        raise BusinessError("You are not authorised to change transporters")
    wanted = {c.id for c in db.scalars(select(CFA).where(CFA.id.in_(cfa_ids)))} if cfa_ids else set()
    old = depot_ids(db, transporter.id)
    if wanted == old:
        return
    db.execute(delete(transporter_depots).where(transporter_depots.c.transporter_id == transporter.id))
    if wanted:
        db.execute(insert(transporter_depots), [{"transporter_id": transporter.id, "cfa_id": i} for i in sorted(wanted)])
    names = lambda ids: ", ".join(sorted(c.code for c in db.scalars(select(CFA).where(CFA.id.in_(ids))))) or "all depots"  # noqa: E731
    audit.log(db, user, "TRANSPORTER_DEPOTS_SET", MODULE, record_type="Transporter", record_id=transporter.id,
              ref=transporter.code, field="depots_served", old=names(old) if old else "all depots", new=names(wanted))
