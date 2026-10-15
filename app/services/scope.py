"""Data-scope restrictions: users only see the companies / CFAs they are assigned to."""
from __future__ import annotations

from sqlalchemy import false
from sqlalchemy.orm import Session

from app.models import CFA, Company, Invoice, User


def apply_invoice_scope(stmt, user: User, model=Invoice):
    cids = user.company_ids
    fids = user.cfa_ids
    if cids is not None:
        stmt = stmt.where(model.company_id.in_(cids) if cids else false())
    if fids is not None:
        stmt = stmt.where(model.cfa_id.in_(fids) if fids else false())
    return stmt


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


def can_see_invoice(user: User, inv: Invoice) -> bool:
    cids, fids = user.company_ids, user.cfa_ids
    return (cids is None or inv.company_id in cids) and (fids is None or inv.cfa_id in fids)


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
