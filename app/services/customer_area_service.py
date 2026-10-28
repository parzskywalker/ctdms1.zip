"""Delivery area of an invoice (v1.7): which of the customer's delivery locations the goods go to.

Area = the existing Location master (it already carries route and zone). A customer normally has ONE area
(`Customer.location_id`, the default). A customer that receives goods in several places lists them as delivery
locations (`customer_delivery_locations`, optional ship-to code from the ERP).

Resolution order, used identically by file import, API, e-invoice QR scan and manual entry:
  1. the file's ship-to code, when it matches one of the customer's delivery locations;
  2. the file's location, when it is one of the customer's locations (default or listed) - a location that is NOT one
     of them is a mismatch: until now the file silently won; setting `area.mismatch_action` decides (ACCEPT keeps
     that behaviour and only warns, EXCEPTION holds the invoice until someone confirms the area);
  3. no location in the file: the single / default delivery location; several and no default -> AREA_AMBIGUOUS,
     the operator chooses (audited).
Every choice is recorded on the invoice (`location_id` = the resolved area, `customer_location_id` = the delivery
location when the customer has a list) so it never has to be guessed again.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, CustomerDeliveryLocation, ExceptionCode, Invoice, InvoiceStatus, Location, User
from app.services import audit
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from app.services.utils import norm_code

MODULE = "CUSTOMER_AREA"


@dataclass
class AreaResult:
    location: Location | None
    delivery: CustomerDeliveryLocation | None = None
    code: str | None = None      # None = resolved; AREA_MISMATCH / AREA_AMBIGUOUS / MISSING_LOCATION
    message: str = ""

    @property
    def resolved(self) -> bool:
        return self.code is None


def delivery_locations(db: Session, customer: Customer, *, active_only: bool = True) -> list[CustomerDeliveryLocation]:
    stmt = select(CustomerDeliveryLocation).where(CustomerDeliveryLocation.customer_id == customer.id)
    if active_only:
        stmt = stmt.where(CustomerDeliveryLocation.is_active.is_(True))
    return list(db.scalars(stmt.order_by(CustomerDeliveryLocation.id)))


def allowed_location_ids(db: Session, customer: Customer) -> set[int]:
    ids = {r.location_id for r in delivery_locations(db, customer)}
    if customer.location_id:
        ids.add(customer.location_id)
    return ids


def mismatch_action(db: Session) -> str:
    return "EXCEPTION" if cfg.get(db, "area.mismatch_action") == "EXCEPTION" else "ACCEPT"


def resolve(db: Session, customer: Customer, file_location: Location | None = None,
            ship_to: str | None = None) -> AreaResult:
    rows = delivery_locations(db, customer)
    if ship_to and rows:
        key = norm_code(ship_to)
        for r in rows:
            if r.ship_to_code and norm_code(r.ship_to_code) == key:
                return AreaResult(r.location, r)
    default = customer.location
    if file_location is not None:
        allowed = allowed_location_ids(db, customer)
        if not allowed or file_location.id in allowed:
            same = [r for r in rows if r.location_id == file_location.id]
            return AreaResult(file_location, same[0] if len(same) == 1 else None)
        names = ", ".join(sorted({(r.location.name) for r in rows} | ({default.name} if default else set())))
        return AreaResult(file_location, None, ExceptionCode.AREA_MISMATCH,
                          f"The file says {file_location.name}, but {customer.name}'s delivery locations are {names}")
    if len(rows) >= 2:
        defaults = [r for r in rows if r.is_default]
        if len(defaults) == 1:
            return AreaResult(defaults[0].location, defaults[0])
        places = ", ".join(r.name for r in rows)
        return AreaResult(default or rows[0].location, None, ExceptionCode.AREA_AMBIGUOUS,
                          f"{customer.name} has several delivery locations ({places}) - choose one")
    if len(rows) == 1:
        return AreaResult(rows[0].location, rows[0])
    if default is not None:
        return AreaResult(default)
    return AreaResult(None, None, "MISSING_LOCATION", f"Customer {customer.code} has no delivery location")


def apply_to_invoice(inv: Invoice, res: AreaResult, *, mismatch_holds: bool) -> None:
    """Record the resolved area on a new invoice; ambiguous (or, when configured, mismatched) ones wait for a choice."""
    if res.location is not None:
        inv.location_id = res.location.id
    inv.customer_location_id = res.delivery.id if res.delivery else None
    if res.code == ExceptionCode.AREA_AMBIGUOUS or (res.code == ExceptionCode.AREA_MISMATCH and mismatch_holds):
        inv.exception_code = res.code
        inv.exception_reason = res.message[:255]
        inv.status = InvoiceStatus.EXCEPTION


# ----------------------------------------------------------------------------- the operator's choice (audited)
def choose_area(db: Session, user: User, inv: Invoice, *, delivery_id: int | None = None, location_id: int | None = None,
                reason: str | None = None) -> None:
    """Pick the delivery location of an invoice that has not been dispatched yet: one of the customer's delivery
    locations (`delivery_id`), or confirm another area for a mismatch (`location_id`, needs a reason)."""
    if not user.can("dispatch.operate"):
        raise BusinessError("You are not authorised to set the delivery area")
    if inv.status not in InvoiceStatus.PRE_SLIP:
        raise BusinessError("The delivery area cannot change once the invoice is on a gatepass")
    cust = inv.customer
    old_loc, old_dl = inv.location, inv.customer_location_id
    if delivery_id:
        dl = db.get(CustomerDeliveryLocation, delivery_id)
        if not dl or dl.customer_id != cust.id or not dl.is_active:
            raise BusinessError("That is not a delivery location of this customer")
        inv.location_id, inv.customer_location_id = dl.location_id, dl.id
        what = dl.name
    elif location_id:
        loc = db.get(Location, location_id)
        if not loc or not loc.is_active:
            raise BusinessError("Choose a delivery area")
        if location_id not in allowed_location_ids(db, cust) and not (reason or "").strip():
            raise BusinessError(f"{loc.name} is not one of {cust.name}'s delivery locations - give a reason to use it")
        inv.location_id, inv.customer_location_id = loc.id, None
        what = loc.name
    else:
        raise BusinessError("Choose a delivery location")
    if inv.exception_code in (ExceptionCode.AREA_AMBIGUOUS, ExceptionCode.AREA_MISMATCH):
        inv.exception_code = None
        inv.exception_reason = None
    from app.services.invoice_service import apply_freight  # re-derive status (and the company-card price) for the new area
    apply_freight(db, inv, user, reason="Delivery area chosen", audit_it=True, force_lookup=False)
    audit.log(db, user, "INVOICE_AREA_SET", MODULE, record_type="Invoice", record_id=inv.id, ref=inv.invoice_no,
              field="location", old=old_loc.name if old_loc else None, new=what, reason=reason,
              details={"customer": cust.code, "delivery_location_id": inv.customer_location_id, "was": old_dl})


# ----------------------------------------------------------------------------- master maintenance (Master Settings)
def add_location(db: Session, user: User, customer: Customer, *, location_id: int, label: str = "", address: str = "",
                 ship_to_code: str = "", is_default: bool = False) -> CustomerDeliveryLocation:
    if not user.can("master.edit"):
        raise BusinessError("You are not authorised to change customers")
    loc = db.get(Location, location_id)
    if not loc or not loc.is_active:
        raise BusinessError("Choose an active location")
    label = (label or "").strip()[:80]
    dup = db.scalar(select(CustomerDeliveryLocation).where(
        CustomerDeliveryLocation.customer_id == customer.id, CustomerDeliveryLocation.location_id == location_id,
        CustomerDeliveryLocation.label == label))
    if dup:
        if dup.is_active:
            raise BusinessError(f"{customer.name} already has {dup.name} as a delivery location")
        dup.is_active = True
        row = dup
    else:
        row = CustomerDeliveryLocation(customer_id=customer.id, location_id=location_id, label=label,
                                       address=(address or "").strip() or None,
                                       ship_to_code=(ship_to_code or "").strip() or None, is_default=False)
        db.add(row)
    if ship_to_code and ship_to_code.strip():
        other = db.scalar(select(CustomerDeliveryLocation).where(
            CustomerDeliveryLocation.customer_id == customer.id, CustomerDeliveryLocation.is_active.is_(True),
            CustomerDeliveryLocation.ship_to_code == ship_to_code.strip(), CustomerDeliveryLocation.id != row.id))
        if other:
            raise BusinessError(f"Ship-to code {ship_to_code.strip()} is already used by {other.name}")
    db.flush()
    if is_default:
        _set_default(db, customer, row)
    audit.log(db, user, "CUSTOMER_LOCATION_ADDED", MODULE, record_type="Customer", record_id=customer.id,
              ref=customer.code, field="delivery_location", new=row.name,
              details={"ship_to_code": row.ship_to_code, "default": row.is_default})
    return row


def _set_default(db: Session, customer: Customer, row: CustomerDeliveryLocation) -> None:
    for r in delivery_locations(db, customer):
        r.is_default = r.id == row.id
    db.flush()


def set_default(db: Session, user: User, customer: Customer, row_id: int) -> None:
    if not user.can("master.edit"):
        raise BusinessError("You are not authorised to change customers")
    row = db.get(CustomerDeliveryLocation, row_id)
    if not row or row.customer_id != customer.id or not row.is_active:
        raise BusinessError("Delivery location not found")
    _set_default(db, customer, row)
    audit.log(db, user, "CUSTOMER_LOCATION_DEFAULT", MODULE, record_type="Customer", record_id=customer.id,
              ref=customer.code, field="default_delivery_location", new=row.name)


def deactivate(db: Session, user: User, customer: Customer, row_id: int) -> None:
    if not user.can("master.edit"):
        raise BusinessError("You are not authorised to change customers")
    row = db.get(CustomerDeliveryLocation, row_id)
    if not row or row.customer_id != customer.id:
        raise BusinessError("Delivery location not found")
    row.is_active = False
    row.is_default = False
    audit.log(db, user, "CUSTOMER_LOCATION_REMOVED", MODULE, record_type="Customer", record_id=customer.id,
              ref=customer.code, field="delivery_location", old=row.name)
