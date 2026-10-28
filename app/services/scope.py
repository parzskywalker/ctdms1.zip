"""Data-scope restrictions: users only see what they are assigned to - companies, depots (CFAs) and, since v1.7,
delivery areas, customer types and customers (all configurable per employee).

Semantics: AND across dimensions (company, depot, area, customer); within a dimension OR. The customer dimension has
two optional lists - customer types and individual customers: when both are restricted a customer is visible if it
matches EITHER list ("all Hospitals, plus customer X"); when only one is restricted, that list decides.

Every invoice / gatepass / report / export / API / dropdown query goes through the helpers in this module, so a new
dimension only has to be added here.
"""
from __future__ import annotations

from sqlalchemy import and_, exists, false, not_, or_, select
from sqlalchemy.orm import Session

from app.models import CFA, Company, Customer, DispatchSlip, DispatchSlipLine, Invoice, Location, User


def _customer_clause(user: User, customer_id_col, party_type_col=None):
    """SQL for the customer dimension, or None when it is unrestricted. party_type_col: the customer's type column
    (pass Customer.party_type when the query already joins customers); otherwise a sub-select is used."""
    cids, codes = user.customer_ids, user.customer_type_codes
    if cids is None and codes is None:
        return None
    parts = []
    if cids is not None:
        parts.append(customer_id_col.in_(cids) if cids else false())
    if codes is not None:
        if not codes:
            parts.append(false())
        elif party_type_col is not None:
            parts.append(party_type_col.in_(codes))
        else:
            parts.append(customer_id_col.in_(select(Customer.id).where(Customer.party_type.in_(codes))))
    return or_(*parts) if len(parts) > 1 else parts[0]


def invoice_scope_clauses(user: User, model=Invoice) -> list:
    """Every restriction of this user as WHERE clauses on the invoice columns (empty list = sees everything)."""
    out = []
    cids, fids, aids = user.company_ids, user.cfa_ids, user.area_ids
    if cids is not None:
        out.append(model.company_id.in_(cids) if cids else false())
    if fids is not None:
        out.append(model.cfa_id.in_(fids) if fids else false())
    if aids is not None:
        out.append(model.location_id.in_(aids) if aids else false())
    cust = _customer_clause(user, model.customer_id)
    if cust is not None:
        out.append(cust)
    return out


def apply_invoice_scope(stmt, user: User, model=Invoice):
    for clause in invoice_scope_clauses(user, model):
        stmt = stmt.where(clause)
    return stmt


def apply_slip_scope(stmt, user: User, slip=DispatchSlip):
    """Gatepasses: the depot must be the user's, and (v1.7, rule B6) the gatepass is visible only if EVERY invoice on it
    is inside the user's company / depot / area / customer scope - one gatepass can carry several customers."""
    fids = user.cfa_ids
    if fids is not None:
        stmt = stmt.where(slip.cfa_id.in_(fids) if fids else false())
    clauses = invoice_scope_clauses(user, Invoice)
    if clauses:
        outside = (select(DispatchSlipLine.id).join(Invoice, Invoice.id == DispatchSlipLine.invoice_id)
                   .where(DispatchSlipLine.slip_id == slip.id, not_(and_(*clauses))))
        stmt = stmt.where(not_(exists(outside)))
    return stmt


def apply_bill_scope(stmt, user: User, model):
    """Transporter bills: company / depot as before; a bill spans many customers and areas, so a user restricted by
    area / customer type / customer sees no bills (they would otherwise see consignments outside their scope)."""
    if not user.scope_is_open:
        return stmt.where(false())
    return apply_strict_scope(stmt, user, model)


def apply_notification_scope(stmt, user: User, notification):
    """Customer alerts: through the gatepass they belong to (same rule as the gatepass itself)."""
    stmt = apply_cfa_scope(stmt, user, notification.cfa_id)
    if invoice_scope_clauses(user, Invoice):
        stmt = stmt.where(notification.slip_id.in_(apply_slip_scope(select(DispatchSlip.id), user)))
    return stmt


def visible_customers(db: Session, user: User) -> list[Customer]:
    stmt = select(Customer).order_by(Customer.name)
    clause = _customer_clause(user, Customer.id, Customer.party_type)
    if clause is not None:
        stmt = stmt.where(clause)
    return list(db.scalars(stmt))


def visible_locations(db: Session, user: User) -> list[Location]:
    stmt = select(Location).order_by(Location.name)
    aids = user.area_ids
    if aids is not None:
        stmt = stmt.where(Location.id.in_(aids) if aids else false())
    return list(db.scalars(stmt))


def apply_company_scope(stmt, user: User, column):
    cids = user.company_ids
    if cids is not None:
        stmt = stmt.where(column.in_(cids) if cids else false())
    return stmt


def apply_cfa_scope(stmt, user: User, column):
    fids = user.cfa_ids
    if fids is not None:
        stmt = stmt.where(column.in_(fids) if fids else false())
    return stmt


def customer_visible(user: User, customer_id: int | None, party_type: str | None) -> bool:
    cids, codes = user.customer_ids, user.customer_type_codes
    if cids is None and codes is None:
        return True
    return (cids is not None and customer_id in cids) or (codes is not None and party_type in codes)


def can_see_invoice(user: User, inv: Invoice) -> bool:
    cids, fids, aids = user.company_ids, user.cfa_ids, user.area_ids
    if not ((cids is None or inv.company_id in cids) and (fids is None or inv.cfa_id in fids)
            and (aids is None or inv.location_id in aids)):
        return False
    if user.customer_ids is None and user.customer_type_codes is None:
        return True
    return customer_visible(user, inv.customer_id, inv.customer.party_type if inv.customer else None)


def can_see_area_customer(user: User, location_id: int | None, customer) -> bool:
    """Area + customer part of the scope (the company / depot part is can_see): used by the import checks."""
    aids = user.area_ids
    if aids is not None and location_id not in aids:
        return False
    return customer is None or customer_visible(user, customer.id, customer.party_type)


def can_see(user: User, company_id: int | None = None, cfa_id: int | None = None) -> bool:
    cids, fids = user.company_ids, user.cfa_ids
    if company_id is not None and cids is not None and company_id not in cids:
        return False
    if cfa_id is not None and fids is not None and cfa_id not in fids:
        return False
    return True


def visible_companies(db: Session, user: User, active_only: bool = True) -> list[Company]:
    from sqlalchemy import select
    stmt = select(Company).order_by(Company.name)
    if active_only:
        stmt = stmt.where(Company.is_active.is_(True))
    stmt = apply_company_scope(stmt, user, Company.id)
    return list(db.scalars(stmt))


def visible_cfas(db: Session, user: User, active_only: bool = True) -> list[CFA]:
    from sqlalchemy import select
    stmt = select(CFA).order_by(CFA.name)
    if active_only:
        stmt = stmt.where(CFA.is_active.is_(True))
    stmt = apply_cfa_scope(stmt, user, CFA.id)
    return list(db.scalars(stmt))


# --------------------------------------------------------------------------- records that carry company/CFA ids
# (transporter bills, freight rates, import batches). A NULL company/CFA means "applies to all", so users with a
# restricted scope may *see* company-wide rows where noted, but may only *change* rows fully inside their scope.

def apply_strict_scope(stmt, user: User, model):
    """Rows whose company AND CFA are both inside the user's scope (NULLs only for all-scope users)."""
    cids, fids = user.company_ids, user.cfa_ids
    if cids is not None:
        stmt = stmt.where(model.company_id.in_(cids) if cids else false())
    if fids is not None:
        stmt = stmt.where(model.cfa_id.in_(fids) if fids else false())
    return stmt


def apply_scope_allow_null_cfa(stmt, user: User, model):
    """Company in scope; CFA in scope OR NULL (company-wide rows such as default freight rates stay visible)."""
    from sqlalchemy import or_
    cids, fids = user.company_ids, user.cfa_ids
    if cids is not None:
        stmt = stmt.where(model.company_id.in_(cids) if cids else false())
    if fids is not None:
        stmt = stmt.where(or_(model.cfa_id.is_(None), model.cfa_id.in_(fids)) if fids else model.cfa_id.is_(None))
    return stmt


def can_change(user: User, company_id: int | None, cfa_id: int | None) -> bool:
    """Write check: NULL company/CFA ('all') is only allowed for users with the matching all-scope."""
    if company_id is None and user.company_ids is not None:
        return False
    if cfa_id is None and user.cfa_ids is not None:
        return False
    return can_see(user, company_id, cfa_id)
