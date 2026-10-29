"""Dispatch workflow (state machine) with the mandatory gatepass controls.

PENDING -> VERIFIED -> SLIP_GENERATED -> SLIP_PRINTED -> DISPATCHED
(RATE_MISSING / EXCEPTION block verification; CANCELLED is terminal unless reinstated.)

v1.6: LOCAL AUTOS (local delivery vehicles) are charged PER TRIP (one amount for the whole trip, entered on the
gatepass, shared over the invoices by boxes - never multiplied by the number of invoices) or PER BOX (boxes x rate
from Local Auto Box Rates). Long-distance transporters keep the v1.3-v1.5 rate-card workflow unchanged.

v1.3: a dispatch slip is a **gatepass** - one pickup (transporter, transporter direct pickup, local auto or
self pickup) carrying one or more invoices for one or more customers. The charge per invoice is frozen on the
gatepass (transporter rate card per case per area, or the principal rate card when `freight.source` = COMPANY).
LR numbers are not needed to print it; they are entered later (audited) until the transporter's bill is approved.
Gate-out (scan at the gate) completes every invoice of the gatepass - nothing is DISPATCHED without one.
The one-invoice screens of v1.2 still work: they create a one-line gatepass.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, time
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CFA, DispatchAllocation, DispatchMode, DispatchSlip, DispatchSlipLine, ExceptionCode, Invoice, InvoiceStatus, OutsideAuto, PaymentMode,
    PickupMode, RateLevel, SlipPrintLog, Transporter, TransporterBill, User, utcnow,
)
from app.models.masters import ChargeBasis
from app.models.recon import BillStatus
from app.services import audit, notify_service
from app.services import local_auto_service as las
from app.services import settings_service as cfg
from app.services import case_service as cs
from app.services import lr_book_service as lrb
from app.services import pawati_service as pws
from app.services import eligibility_service as es
from app.services import transporter_rate_service as trs
from app.services.errors import BusinessError
from app.services.invoice_service import refresh_status
from app.services.scope import can_see_invoice
from app.services.utils import (
    fiscal_year, local_now, local_today, money, next_sequence, norm_vehicle, parse_date, parse_decimal, parse_int,
    parse_time, safe_int, vehicle_looks_valid,
)
from app.services import context

MODULE = "DISPATCH"
S = InvoiceStatus
ZERO = Decimal("0")
GATEPASS_LEVELS = (RateLevel.TRANSPORTER, RateLevel.SELF, RateLevel.AUTO)  # rates set when the gatepass is made


def _perm(user: User, perm: str) -> None:
    if not user.can(perm):
        raise BusinessError(f"You are not authorised for this action ({perm})")


def freight_source(db: Session) -> str:
    return "COMPANY" if cfg.get(db, "freight.source") == "COMPANY" else "TRANSPORTER"


def active_slip(db: Session, inv: Invoice) -> DispatchSlip | None:
    if inv.dispatch_slip_id:
        slip = db.get(DispatchSlip, inv.dispatch_slip_id)
        if slip and not slip.is_void:
            return slip
    return db.scalar(select(DispatchSlip).join(DispatchSlipLine, DispatchSlipLine.slip_id == DispatchSlip.id)
                     .where(DispatchSlipLine.invoice_id == inv.id, DispatchSlip.is_void.is_(False))
                     .order_by(DispatchSlip.id.desc()))


def slip_invoices(db: Session, slip: DispatchSlip) -> list[Invoice]:
    ids = [ln.invoice_id for ln in slip.lines]
    if not ids:
        return []
    by_id = {i.id: i for i in db.scalars(select(Invoice).where(Invoice.id.in_(ids)))}
    return [by_id[i] for i in ids if i in by_id]


# ----------------------------------------------------------------------------- details (one-invoice screen)
def save_details(db: Session, user: User, inv: Invoice, *, transporter_id=None, vehicle_no=None, lr_no=None,
                 dispatch_date=None, dispatch_time=None, remarks=None) -> list[str]:
    """Save draft dispatch details. Returns list of changed fields."""
    _perm(user, "dispatch.operate")
    if inv.status in (S.DISPATCHED, S.CANCELLED):
        raise BusinessError(f"Invoice is {inv.status_label}; dispatch details are locked")
    if inv.status in (S.SLIP_GENERATED, S.SLIP_PRINTED):
        raise BusinessError("Dispatch slip already generated - details are locked. Void the slip to correct them "
                            "(the LR number can still be added on the gatepass page).")

    try:
        d = parse_date(dispatch_date) if dispatch_date not in (None, "") else None
        t = parse_time(dispatch_time) if dispatch_time not in (None, "") else None
    except ValueError as e:
        raise BusinessError(str(e)) from e
    tid = None
    if transporter_id not in (None, "", "0"):
        tid = safe_int(transporter_id, minimum=1)
        if tid is None:
            raise BusinessError("Select an active transporter")
    if tid:
        tr = db.get(Transporter, tid)
        if not tr or not tr.is_active:
            raise BusinessError("Select an active transporter")
    values = {
        "transporter_id": tid,
        "vehicle_no": norm_vehicle(vehicle_no),
        "lr_no": (str(lr_no).strip().upper() or None) if lr_no is not None else None,
        "dispatch_date": d,
        "dispatch_time": t,
        "remarks": (remarks or "").strip() or None,
    }
    changes = audit.diff(inv, values)
    if not changes:
        return []
    for k, v in values.items():
        setattr(inv, k, v)
    tracked = {k: v for k, v in changes.items() if k in ("transporter_id", "vehicle_no", "lr_no")}
    if tracked and any(o is not None for o, _ in tracked.values()):
        audit.log_changes(db, user, "DISPATCH_DETAILS_CHANGED", MODULE, tracked, record_type="Invoice",
                          record_id=inv.id, ref=inv.invoice_no)
    else:
        audit.log(db, user, "DISPATCH_DRAFT_SAVED", MODULE, record_type="Invoice", record_id=inv.id,
                  ref=inv.invoice_no, details={k: audit._s(v[1]) for k, v in changes.items()})
    return list(changes)


# ----------------------------------------------------------------------------- verify
def verify(db: Session, user: User, inv: Invoice) -> None:
    _perm(user, "dispatch.verify")
    if inv.status == S.VERIFIED:
        return
    if inv.status == S.RATE_MISSING:
        raise BusinessError("Cannot verify: freight rate missing. Resolve the rate exception first.")
    if inv.status == S.EXCEPTION:
        raise BusinessError(f"Cannot verify: {inv.exception_reason or 'invoice has an open exception'}")
    if inv.status != S.PENDING:
        raise BusinessError(f"Cannot verify an invoice in status {inv.status_label}")
    company = freight_source(db) == "COMPANY"
    if not inv.boxes or inv.boxes <= 0 or (company and inv.freight_amount is None):
        raise BusinessError("Boxes and freight must be available before verification" if company else
                            "Number of cases must be available before verification")
    inv.verified_at = utcnow()
    inv.verified_by_id = user.id
    inv.status = S.VERIFIED
    audit.log(db, user, "DISPATCH_VERIFIED", MODULE, record_type="Invoice", record_id=inv.id, ref=inv.invoice_no,
              field="status", old=S.PENDING, new=S.VERIFIED,
              details={"boxes": inv.boxes, "rate": str(inv.rate_per_box), "freight": str(inv.freight_amount)})


# ----------------------------------------------------------------------------- gatepass
@dataclass
class GatepassRequest:
    mode: str = DispatchMode.TRANSPORTER
    transporter_id: int | None = None
    outside_auto_id: int | None = None
    pickup_mode: str = PickupMode.REGULAR
    dispatch_date: date | None = None
    dispatch_time: time | None = None
    cases: dict | None = None            # {invoice_id: cases} entered on the gatepass
    amount_paid: Decimal | None = None   # None -> default from payment mode
    remarks: str | None = None
    auto_method: str | None = None       # v1.6 local auto: PER_TRIP / PER_CASE (None = the auto's default)
    trip_charge: Decimal | None = None   # v1.6 per-trip amount for this trip (None = the auto's suggestion)
    vehicle_no: str | None = None
    group_cross_company: bool = False    # v1.7: invoices of different companies on one gatepass, on purpose
    eligibility_override_reason: str | None = None  # v1.7: transporter that does not serve this depot (permission + reason)
    # v1.7 local-auto path: {invoice_id: [(receiving_transporter_id, cases), ...]} - one or several receivers per invoice
    receivers: dict | None = None


@dataclass
class PlannedAlloc:
    transporter: Transporter
    cases: int
    rate: Decimal | None
    freight: Decimal
    rate_id: int | None = None
    note: dict | None = None


@dataclass
class PlannedLine:
    inv: Invoice
    cases: int
    rate: Decimal | None
    freight: Decimal
    level: str
    extra: Decimal = ZERO
    rate_id: int | None = None   # v1.7: the transporter-rate row that priced the line
    note: dict | None = None     # v1.7: how the freight was found (stored on the gatepass line)
    allocs: list = field(default_factory=list)  # v1.7: receiving transporters of this line (PlannedAlloc)


def _alloc(total: Decimal, weights: list[int]) -> list[Decimal]:
    """Split money over lines in proportion to cases; the shares add up exactly to the total (paise)."""
    total = money(total)
    if not weights or total == 0:
        return [ZERO for _ in weights]
    w_sum = sum(weights) or len(weights)
    ws = weights if sum(weights) else [1] * len(weights)
    shares = [(total * w / w_sum).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for w in ws]
    shares[-1] += total - sum(shares)
    return shares


def _trip_amount(auto: OutsideAuto | None, req: "GatepassRequest") -> Decimal | None:
    if auto is None:
        return None
    if req.trip_charge is not None:
        return money(req.trip_charge)
    return money(auto.default_trip_charge) if auto.default_trip_charge else None


def _pickup_charge(tr: Transporter | None, cases: int) -> Decimal:
    if not tr or not tr.direct_pickup_charge:
        return ZERO
    if tr.direct_pickup_basis == ChargeBasis.PER_CASE:
        return money(Decimal(cases) * tr.direct_pickup_charge)
    return money(tr.direct_pickup_charge)


def plan_gatepass(db: Session, user: User | None, invs: list[Invoice], req: GatepassRequest,
                  ) -> tuple[list[PlannedLine], dict, list[str]]:
    """Validate and price a gatepass without saving anything. Returns (lines, totals, problems)."""
    problems: list[str] = []
    if not invs:
        return [], {}, ["Add at least one invoice"]
    mode = req.mode if req.mode in DispatchMode.ALL else None
    if not mode:
        problems.append("Choose how the goods leave (transporter / direct pickup / local auto / self)")
        mode = DispatchMode.TRANSPORTER
    tr = db.get(Transporter, req.transporter_id) if req.transporter_id else None
    auto = db.get(OutsideAuto, req.outside_auto_id) if req.outside_auto_id else None
    local_path = mode == DispatchMode.TRANSPORTER and req.pickup_mode == PickupMode.OUTSIDE_AUTO
    covered = bool(req.receivers) and all(i.id in req.receivers for i in invs)
    if req.receivers and not local_path:
        problems.append("Several receiving transporters are only possible when a local auto takes the goods to them")
    if mode == DispatchMode.TRANSPORTER and not local_path:
        problems.append("A transporter trip goes to the transporter by a local auto: choose Local auto, the auto, and "
                        "the receiving transporter(s)")
    if mode == DispatchMode.DIRECT and req.pickup_mode == PickupMode.OUTSIDE_AUTO:
        problems.append("Direct pickup: the transporter collects from our depot - no local auto on this gatepass")
    if req.receivers and freight_source(db) == "COMPANY":
        problems.append("Receiving transporters per invoice need the transporter rate card (System Settings > freight.source)")
    if mode in (DispatchMode.TRANSPORTER, DispatchMode.DIRECT):
        if (not tr or not tr.is_active) and not covered:
            problems.append("Transporter not selected")
        if req.pickup_mode == PickupMode.OUTSIDE_AUTO and (not auto or not auto.is_active):
            problems.append("Select the local auto that takes the goods to the transporter")
    if mode == DispatchMode.OUTSIDE_AUTO and (not auto or not auto.is_active):
        problems.append("Select the local auto")
    uses_auto = auto is not None and (mode == DispatchMode.OUTSIDE_AUTO or (
        mode in (DispatchMode.TRANSPORTER, DispatchMode.DIRECT) and req.pickup_mode == PickupMode.OUTSIDE_AUTO))
    auto_method, method_problem = las.method_for(auto, req.auto_method) if uses_auto else (None, None)
    if method_problem:
        problems.append(method_problem)
    trip = _trip_amount(auto, req) if auto_method == ChargeBasis.PER_TRIP else None
    if auto_method == ChargeBasis.PER_TRIP and (trip is None or trip <= 0):
        problems.append(f"Enter the trip charge for {auto.name} (one amount for the whole trip)")
    elif trip is not None and trip > Decimal("100000"):
        problems.append(f"Trip charge ₹{trip:,.2f} looks wrong")
    d = req.dispatch_date or local_today()
    if d > local_today() and not cfg.get(db, "dispatch.allow_future_date"):
        problems.append("Dispatch date cannot be in the future")
    cfa_ids = {i.cfa_id for i in invs}
    if len(cfa_ids) > 1:
        problems.append("All invoices on one gatepass must be from the same depot")
    if len({i.id for i in invs}) != len(invs):
        problems.append("The same invoice is listed twice")
    company_mode = freight_source(db) == "COMPANY"
    # v1.7 grouping rule: different companies only by an explicit, permitted choice (the companies stay on every line)
    company_codes = sorted({i.company.code for i in invs})
    cross_company = len(company_codes) > 1
    if cross_company and not req.group_cross_company:
        problems.append(f"These invoices belong to different companies ({', '.join(company_codes)}). Tick \"Group across "
                        "companies\" to put them on one gatepass on purpose, or make one gatepass per company")
    elif cross_company and user is not None and not user.can("dispatch.group_cross_company"):
        problems.append("You are not authorised to put invoices of different companies on one gatepass")
    # v1.7 eligibility: every receiving transporter must serve this depot (no depot list = serves every depot)
    override_used = False
    cfa0 = next(iter(cfa_ids)) if len(cfa_ids) == 1 else None
    depot_checked: set[int] = set()

    def depot_check(t: Transporter) -> None:
        nonlocal override_used
        if t.id in depot_checked or cfa0 is None or es.serves_depot(db, t.id, cfa0):
            return
        depot_checked.add(t.id)
        depot = db.get(CFA, cfa0)
        reason = (req.eligibility_override_reason or "").strip()
        if reason and (user is None or user.can("transporter.override_eligibility")):
            if len(reason) < 5:
                problems.append("Give a reason (at least 5 characters) for choosing a transporter that does not "
                                "serve this depot")
            override_used = True
        elif reason:
            problems.append("You are not authorised to choose a transporter that does not serve this depot")
        else:
            problems.append(f"{t.name} does not serve {depot.name if depot else 'this depot'}. Choose a transporter "
                            "that does, or ask a Manager to override it (a reason is recorded)")

    if mode in (DispatchMode.TRANSPORTER, DispatchMode.DIRECT) and tr and tr.is_active and not covered:
        depot_check(tr)

    planned: list[PlannedLine] = []
    for inv in invs:
        tag = f"Invoice {inv.invoice_no}"
        if user is not None and not can_see_invoice(user, inv):
            problems.append(f"{tag}: not in your company / depot / area / customer scope")
            continue
        if inv.status == S.CANCELLED:
            problems.append(f"{tag}: cancelled")
            continue
        if inv.status not in S.PRE_SLIP:
            problems.append(f"{tag}: already on gatepass {inv.dispatch_no or ''} ({inv.status_label})")
            continue
        if inv.exception_code == ExceptionCode.OVERRIDE_PENDING:
            problems.append(f"{tag}: a box / freight change is awaiting approval")
        if inv.exception_code == ExceptionCode.RATE_CONFLICT:
            problems.append(f"{tag}: {inv.exception_reason or 'rate conflict'}")
        if inv.exception_code in (ExceptionCode.AREA_AMBIGUOUS, ExceptionCode.AREA_MISMATCH):
            problems.append(f"{tag}: {inv.exception_reason or ExceptionCode.LABELS[inv.exception_code]} "
                            "- choose the delivery location on the invoice first")
        raw = (req.cases or {}).get(inv.id)
        try:
            cases = parse_int(raw) if raw not in (None, "") else inv.boxes
        except ValueError:
            problems.append(f"{tag}: cases must be a whole number")
            continue
        if not cases or cases <= 0:
            problems.append(f"{tag}: number of cases missing")
            continue
        if cases > 5000:
            problems.append(f"{tag}: {cases} cases looks wrong")
            continue
        if inv.boxes and cases != inv.boxes and not (user and user.can("override.direct")):
            problems.append(f"{tag}: {cases} cases entered but the invoice says {inv.boxes}. Request a box change "
                            "(approval) first, or ask a checker to make the gatepass.")
        if d < inv.invoice_date:
            problems.append(f"{tag}: dispatch date is before the invoice date")
        rate: Decimal | None
        rate_id, note, allocs = None, None, []
        if mode == DispatchMode.SELF:
            rate, freight, level = ZERO, ZERO, RateLevel.SELF
            note = {"source": "SELF", "cases": cases}
        elif mode == DispatchMode.OUTSIDE_AUTO:
            rate, freight, level = None, ZERO, RateLevel.AUTO  # per trip: the trip amount is shared out below
            if auto and auto_method == ChargeBasis.PER_CASE:
                br = las.box_rate(db, auto, location_id=inv.location_id, company_id=inv.company_id,
                                  cfa_id=inv.cfa_id, on=d)
                if br.rate is None:
                    problems.append(f"{tag}: no per-box rate for {auto.name} ({inv.location.name}, {inv.company.code})"
                                    " - add it under Master Settings > Local Auto Box Rates")
                    continue
                rate = br.rate
                freight = money(Decimal(cases) * rate)
                note = {"source": "AUTO_PER_CASE", "cases": cases, "rate": str(rate), "amount": str(freight)}
            else:
                note = {"source": "AUTO_PER_TRIP", "cases": cases}
        elif company_mode or inv.freight_basis in ("MANUAL_RATE", "MANUAL_AMOUNT"):
            if inv.freight_amount is None:
                problems.append(f"{tag}: {inv.exception_reason or 'freight not calculated'}")
                continue
            rate = inv.rate_per_box
            freight = inv.freight_amount if cases == inv.boxes else \
                money(Decimal(cases) * (inv.rate_per_box or ZERO))
            level = inv.rate_level or RateLevel.MANUAL
            note = {"source": "COMPANY_CARD" if inv.freight_basis not in ("MANUAL_RATE", "MANUAL_AMOUNT") else inv.freight_basis,
                    "cases": cases, "rate": str(rate) if rate is not None else None, "amount": str(freight)}
        else:
            splits = (req.receivers or {}).get(inv.id) or ([(tr.id, cases)] if tr else [])
            if not splits:
                continue
            if sum(n for _, n in splits) != cases:
                problems.append(f"{tag}: the cases given to the receiving transporters ({sum(n for _, n in splits)}) "
                                f"do not add up to {cases}")
                continue
            if len({t for t, _ in splits}) != len(splits) or any(n <= 0 for _, n in splits):
                problems.append(f"{tag}: each receiving transporter once, with at least one case")
                continue
            allocs, ok = [], True
            for tid, n in splits:
                rt = db.get(Transporter, tid)
                if not rt or not rt.is_active:
                    problems.append(f"{tag}: receiving transporter not found / inactive")
                    ok = False
                    continue
                depot_check(rt)
                lk = trs.find(db, rt.id, inv.location_id, d, cfa_id=inv.cfa_id, company_id=inv.company_id)
                if not lk.found:
                    problems.append(f"{tag}: {lk.message} - add it to the rate card")
                    ok = False
                    continue
                fr = money(Decimal(n) * lk.rate_per_case)
                allocs.append(PlannedAlloc(rt, n, lk.rate_per_case, fr, lk.rate.id, {
                    "source": "TRANSPORTER_CARD", "rule": lk.rule, "scope": lk.rate.scope_label, "rate_id": lk.rate.id,
                    "area": inv.location.name, "cases": n, "rate": str(lk.rate_per_case), "amount": str(fr),
                    "effective_from": lk.rate.effective_from.isoformat()}))
            if not ok:
                continue
            freight = sum((a.freight for a in allocs), ZERO)
            rate = allocs[0].rate if len(allocs) == 1 else (freight / cases).quantize(Decimal("0.01"))
            level = RateLevel.TRANSPORTER
            rate_id = allocs[0].rate_id
            note = dict(allocs[0].note)
            if len(allocs) > 1:
                note = {"source": "TRANSPORTER_CARD", "rule": "Split across receiving transporters", "cases": cases,
                        "rate": str(rate), "amount": str(freight), "area": inv.location.name,
                        "allocations": [{"transporter": a.transporter.code, "cases": a.cases, "rate": str(a.rate),
                                         "amount": str(a.freight)} for a in allocs]}
        if not allocs and mode in (DispatchMode.TRANSPORTER, DispatchMode.DIRECT) and tr:
            allocs = [PlannedAlloc(tr, cases, rate, freight, rate_id, note)]  # one receiver: the gatepass's transporter
        planned.append(PlannedLine(inv, cases, rate, freight, level, rate_id=rate_id, note=note, allocs=allocs))

    total_cases = sum(p.cases for p in planned)
    weights = [p.cases for p in planned]
    pickup = auto_ch = ZERO
    if mode == DispatchMode.OUTSIDE_AUTO and auto and auto_method == ChargeBasis.PER_TRIP and trip:
        # ONE amount for the trip, however many invoices: shared by boxes for MIS (the shares add up to the trip)
        for p, share in zip(planned, _alloc(trip, weights)):
            p.freight = share
            p.rate = (share / p.cases).quantize(Decimal("0.01")) if p.cases else None
            p.note = {"source": "AUTO_PER_TRIP", "cases": p.cases, "trip_charge": str(trip), "share": str(share),
                      "trip_cases": total_cases}
    if mode == DispatchMode.DIRECT:
        pickup = _pickup_charge(tr, total_cases)
    if mode in (DispatchMode.TRANSPORTER, DispatchMode.DIRECT) and req.pickup_mode == PickupMode.OUTSIDE_AUTO and auto:
        if auto_method == ChargeBasis.PER_TRIP:
            auto_ch = trip or ZERO
        else:
            for p in planned:
                br = las.box_rate(db, auto, location_id=p.inv.location_id, company_id=p.inv.company_id,
                                  cfa_id=p.inv.cfa_id, on=d)
                if br.rate is None:
                    problems.append(f"Invoice {p.inv.invoice_no}: no per-box rate for {auto.name}")
                else:
                    auto_ch += money(Decimal(p.cases) * br.rate)
    for p, share in zip(planned, _alloc(pickup + auto_ch, weights)):
        p.extra = share
    freight_total = sum((p.freight for p in planned), ZERO)
    total = money(freight_total + pickup + auto_ch)

    # who is paid now: outside autos and CASH transporters at dispatch; TBB transporters bill later
    if req.amount_paid is not None:
        paid = money(req.amount_paid)
        if paid < 0 or paid > total:
            problems.append(f"Amount paid must be between ₹0 and the total ₹{total}")
    elif not cfg.get(db, "gatepass.cash_paid_default"):
        paid = ZERO
    elif mode == DispatchMode.OUTSIDE_AUTO:
        paid = total
    elif mode in (DispatchMode.TRANSPORTER, DispatchMode.DIRECT) and tr and tr.payment_mode == PaymentMode.CASH:
        paid = total
    else:
        paid = auto_ch
    status = "NIL" if total == 0 else ("PAID" if paid >= total else ("PARTIAL" if paid > 0 else "TBB"))
    receivers_used = {a.transporter.id: a.transporter for p in planned for a in p.allocs}
    if mode in (DispatchMode.TRANSPORTER, DispatchMode.DIRECT) and len(receivers_used) == 1:
        tr = next(iter(receivers_used.values()))
    elif len(receivers_used) > 1:
        tr = None  # several receiving transporters: the gatepass itself has none (see the allocations / Pawatis)
    totals = {"receivers": list(receivers_used.values()), "invoices": len(planned), "cases": total_cases, "freight": freight_total, "pickup": pickup,
              "auto": auto_ch, "total": total, "paid": paid, "balance": total - paid, "payment_status": status,
              "mode": mode, "transporter": tr, "auto_obj": auto, "date": d,
              "auto_method": auto_method, "trip_charge": trip, "cross_company": cross_company,
              "eligibility_override": override_used}
    return planned, totals, problems


def _gatepass_snapshot(db: Session, slip: DispatchSlip, planned: list[PlannedLine], totals: dict, user: User) -> dict:
    f = planned[0].inv.cfa
    tr, auto = totals["transporter"], totals["auto_obj"]
    groups: OrderedDict = OrderedDict()
    for p in planned:
        inv, cu, lo = p.inv, p.inv.customer, p.inv.location
        g = groups.setdefault(cu.id, {
            "customer": {"code": cu.code, "name": cu.name, "type": cu.type_label,
                         "address": cu.delivery_address or cu.billing_address, "city": cu.city,
                         "contact": cu.contact_person, "phone": cu.contact_phone, "whatsapp": cu.whatsapp,
                         "gstin": cu.gstin},
            "location": {"code": lo.code, "name": lo.name, "route": lo.route, "zone": lo.zone},
            "invoices": [], "cases": 0, "freight": ZERO, "extra": ZERO})
        g["invoices"].append({"invoice_no": inv.invoice_no, "recognition_code": inv.recognition_code,
                              "invoice_date": inv.invoice_date.strftime("%d-%m-%Y"),
                              "company_code": inv.company.code, "company": inv.company.name,
                              "sales_value": str(inv.sales_value), "cases": p.cases,
                              "rate": str(p.rate) if p.rate is not None else None, "freight": str(p.freight),
                              "extra": str(p.extra)})
        g["cases"] += p.cases
        g["freight"] += p.freight
        g["extra"] += p.extra
    group_list = []
    for g in groups.values():
        g["freight"] = str(g["freight"])
        g["extra"] = str(g["extra"])
        group_list.append(g)
    first = planned[0].inv
    return {
        "v": 2,
        "org_name": cfg.get(db, "org.name"),
        "org_address": cfg.get(db, "org.address"),
        "footer": cfg.get(db, "slip.footer_note"),
        "dispatch_no": slip.dispatch_no,
        "generated_at": local_now().strftime("%d-%m-%Y %H:%M"),
        "dispatch_date": totals["date"].strftime("%d-%m-%Y"),
        "dispatch_time": None,  # filled in by create_gatepass
        "cfa": {"code": f.code, "name": f.name, "address": f.address, "city": f.city, "state": f.state,
                "phone": f.contact_phone, "locality": f.locality},
        "mode": totals["mode"], "mode_label": slip.mode_label, "pickup_mode": slip.pickup_mode,
        "transporter": {"code": tr.code, "name": tr.name, "contact": tr.contact_person, "mobile": tr.mobile,
                        "gstin": tr.gstin, "address": tr.address, "city": tr.city,
                        "payment_mode": tr.payment_mode, "payment_label": tr.payment_label} if tr else None,
        "outside_auto": {"code": auto.code, "name": auto.name, "mobile": auto.mobile,
                         "vehicle_no": slip.vehicle_no or auto.vehicle_no,
                         "method": totals.get("auto_method"),
                         "trip_charge": str(totals["trip_charge"]) if totals.get("trip_charge") is not None else None,
                         "charge_basis": totals.get("auto_method") or auto.charge_basis,
                         "rate": str(totals["trip_charge"] if totals.get("trip_charge") is not None
                                     else auto.default_box_rate or auto.rate)} if auto else None,
        "rate_basis": freight_source(db),
        "receivers": [{"code": t.code, "name": t.name} for t in totals.get("receivers", [])],
        "groups": group_list,
        "totals": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in totals.items()
                   if k in ("invoices", "cases", "freight", "pickup", "auto", "total", "paid", "balance",
                            "payment_status")},
        "remarks": slip.remarks,
        "prepared_by": user.full_name,
        "checked_by": first.verified_by.full_name if (len(planned) == 1 and first.verified_by) else None,
        # one-invoice slips also carry the v1.2 keys (API consumers, single-invoice print)
        "invoice_no": first.invoice_no if len(planned) == 1 else None,
        "central_id": first.central_id if len(planned) == 1 else f"{len(planned)} invoices",
    }


def create_gatepass(db: Session, user: User, invs: list[Invoice], req: GatepassRequest) -> DispatchSlip:
    _perm(user, "dispatch.operate")
    planned, totals, problems = plan_gatepass(db, user, invs, req)
    if problems:
        raise BusinessError("Cannot generate gatepass: " + "; ".join(problems))
    if not planned:
        raise BusinessError("Cannot generate gatepass: no invoice could be added")
    d = totals["date"]
    cfa = planned[0].inv.cfa
    prefix = cfg.get(db, "dispatch.number_prefix") or "DS"
    fy = fiscal_year(d)
    seq = next_sequence(db, f"DISPATCH:{cfa.code}:{fy}")
    dispatch_no = f"{prefix}/{cfa.code}/{fy.replace('-', '')[2:]}/{seq:05d}"
    tr, auto = totals["transporter"], totals["auto_obj"]
    mode = totals["mode"]
    slip = DispatchSlip(
        dispatch_no=dispatch_no, invoice_id=planned[0].inv.id if len(planned) == 1 else None, cfa_id=cfa.id,
        dispatch_mode=mode,
        pickup_mode=req.pickup_mode if mode in (DispatchMode.TRANSPORTER, DispatchMode.DIRECT) else PickupMode.REGULAR,
        transporter_id=tr.id if tr and mode in (DispatchMode.TRANSPORTER, DispatchMode.DIRECT) else None,
        outside_auto_id=auto.id if auto and (mode == DispatchMode.OUTSIDE_AUTO or
                                            req.pickup_mode == PickupMode.OUTSIDE_AUTO) else None,
        dispatch_date=d, invoice_count=len(planned), total_cases=totals["cases"], freight_total=totals["freight"],
        pickup_charge=totals["pickup"], auto_charge=totals["auto"], total_charge=totals["total"],
        amount_paid=totals["paid"], payment_status=totals["payment_status"],
        remarks=(req.remarks or "").strip() or None, generated_by_id=user.id)
    if slip.outside_auto_id is not None:
        slip.auto_method = totals.get("auto_method")
        slip.trip_charge = totals.get("trip_charge")
        slip.vehicle_no = norm_vehicle(req.vehicle_no) or auto.vehicle_no
    if slip.outside_auto_id is None:
        totals["auto_obj"] = None
        totals["auto_method"] = totals["trip_charge"] = None
    if slip.transporter_id is None:
        totals["transporter"] = None
    slip.cross_company = bool(totals.get("cross_company"))
    if totals.get("eligibility_override"):
        slip.eligibility_override_reason = (req.eligibility_override_reason or "").strip()
    db.add(slip)
    db.flush()
    t = req.dispatch_time or local_now().time().replace(second=0, microsecond=0)
    consignments: dict[tuple, int] = {}  # v1.7: one consignment = one customer + delivery location on this gatepass
    for p in planned:
        inv = p.inv
        old_status = inv.status
        if inv.boxes != p.cases:
            audit.log(db, user, "BOXES_ENTERED" if inv.boxes is None else "OVERRIDE_APPLIED", "OVERRIDE",
                      record_type="Invoice", record_id=inv.id, ref=inv.invoice_no, field="BOXES", old=inv.boxes,
                      new=p.cases, reason=f"Cases counted on gatepass {dispatch_no}")
            inv.boxes_source = "MANUAL" if inv.boxes is None else "OVERRIDE"
            inv.boxes = p.cases
        if inv.exception_code == ExceptionCode.BOXES_MISSING:
            inv.exception_code = None
            inv.exception_reason = None
        if p.level in GATEPASS_LEVELS:  # priced on the gatepass (company-card freight stays as it was)
            inv.rate_per_box = p.rate
            inv.rate_level = p.level
            inv.freight_rate_id = None
            inv.freight_basis = "CARD" if p.level == RateLevel.TRANSPORTER else "AUTO"
        inv.freight_amount = p.freight
        inv.extra_charge = p.extra
        inv.freight_calculated_at = utcnow()
        inv.dispatch_mode = mode
        inv.transporter_id = (max(p.allocs, key=lambda a: a.cases).transporter.id
                              if p.allocs and mode in (DispatchMode.TRANSPORTER, DispatchMode.DIRECT)
                              else slip.transporter_id)  # v1.7: the receiver with most cases when an invoice is split
        inv.outside_auto_id = slip.outside_auto_id
        inv.dispatch_date = d
        inv.dispatch_time = t
        if not inv.verified_at:  # the cases were counted and confirmed on the gatepass
            inv.verified_at = utcnow()
            inv.verified_by_id = user.id
        inv.dispatch_no = dispatch_no
        inv.dispatch_slip_id = slip.id
        inv.slip_generated_at = utcnow()
        inv.slip_generated_by_id = user.id
        inv.print_count = 0
        inv.status = S.SLIP_GENERATED
        cno = consignments.setdefault((inv.customer_id, inv.location_id), len(consignments) + 1)
        if p.note is not None and p.extra:
            p.note = {**p.note, "extra": str(p.extra)}
        slip.lines.append(DispatchSlipLine(invoice_id=inv.id, customer_id=inv.customer_id, location_id=inv.location_id,
                                           cases=p.cases, rate_per_case=p.rate, freight=p.freight,
                                           extra_charge=p.extra, rate_id=p.rate_id, calc_note=p.note,
                                           consignment_no=cno))
        audit.log(db, user, "SLIP_GENERATED", MODULE, record_type="Invoice", record_id=inv.id, ref=inv.invoice_no,
                  field="status", old=old_status, new=S.SLIP_GENERATED,
                  details={"dispatch_no": dispatch_no, "cases": p.cases, "rate": audit._s(p.rate),
                           "freight": str(p.freight), "extra": str(p.extra)})
    db.flush()
    line_of = {ln.invoice_id: ln for ln in slip.lines}
    for p in planned:  # v1.7 allocations: who carries how many cases of each invoice, at what rate
        for a in p.allocs:
            db.add(DispatchAllocation(slip_id=slip.id, line_id=line_of[p.inv.id].id, invoice_id=p.inv.id,
                                      receiving_transporter_id=a.transporter.id, cases=a.cases, rate_per_case=a.rate,
                                      freight=a.freight, calc_note=a.note))
    db.flush()
    slip.snapshot = _gatepass_snapshot(db, slip, planned, totals, user)
    slip.snapshot["dispatch_time"] = t.strftime("%H:%M")
    audit.log(db, user, "GATEPASS_GENERATED", MODULE, record_type="DispatchSlip", record_id=slip.id, ref=dispatch_no,
              details={"invoices": len(planned), "cases": totals["cases"], "mode": mode,
                       "transporter": tr.code if slip.transporter_id else None,
                       "auto": auto.code if slip.outside_auto_id else None, "total": str(totals["total"]),
                       "auto_method": slip.auto_method, "trip_charge": audit._s(slip.trip_charge),
                       "paid": str(totals["paid"]), "consignments": len(consignments),
                       "cross_company": slip.cross_company})
    cs.generate(db, user, slip)  # case control is mandatory: one barcode per carton, scanned at the gate
    pws.generate(db, user, slip)                    # v1.7 Garage Pawati (local-auto path only)
    slip.lr_warnings = lrb.assign_for_slip(db, user, slip)  # v1.7 LR books (transporters that give their own book)
    if slip.cross_company:
        audit.log(db, user, "GATEPASS_CROSS_COMPANY", MODULE, record_type="DispatchSlip", record_id=slip.id,
                  ref=dispatch_no, reason="Invoices of different companies grouped on purpose",
                  details={"companies": sorted({p.inv.company.code for p in planned})})
    if slip.eligibility_override_reason:
        audit.log(db, user, "ELIGIBILITY_OVERRIDE", MODULE, record_type="DispatchSlip", record_id=slip.id,
                  ref=dispatch_no, reason=slip.eligibility_override_reason,
                  details={"transporter": tr.code if tr else None, "depot": cfa.code,
                           "why": "Transporter does not serve this depot"})
    return slip


# ----------------------------------------------------------------------------- data checks
def data_warnings(db: Session, inv: Invoice) -> list[str]:
    """Non-blocking sanity checks shown to the operator (the slip can still be generated)."""
    out = []
    if inv.vehicle_no and not vehicle_looks_valid(inv.vehicle_no):
        out.append(f"Vehicle number '{inv.vehicle_no}' does not look like a registration number "
                   "(e.g. CG04AB1234 / 22BH1234AA). Please double-check it.")
    if inv.lr_no and inv.transporter_id:
        dup = db.scalar(select(Invoice.invoice_no).where(
            Invoice.lr_no == inv.lr_no, Invoice.transporter_id == inv.transporter_id, Invoice.id != inv.id,
            Invoice.status != S.CANCELLED).limit(1))
        if dup:
            out.append(f"LR {inv.lr_no} is also used on invoice {dup} (same transporter). Fine for a shared "
                       "consignment - otherwise check for a typo.")
    return out


def _lr_conflict(db: Session, lr_no: str, transporter_id: int | None, invs: list[Invoice]) -> Invoice | None:
    """An LR of a transporter belongs to one consignment (one customer). Returns a clash on another customer."""
    if not lr_no or not transporter_id:
        return None
    ids = [i.id for i in invs]
    customers = {i.customer_id for i in invs}
    return db.scalar(select(Invoice).where(Invoice.lr_no == lr_no, Invoice.transporter_id == transporter_id,
                                           Invoice.id.notin_(ids), Invoice.status != S.CANCELLED,
                                           Invoice.customer_id.notin_(customers)).limit(1))


# ----------------------------------------------------------------------------- printing
def _print_slip(db: Session, user: User, slip: DispatchSlip, output: str, reason: str | None) -> list[SlipPrintLog]:
    if output not in ("PRINT_A4", "PRINT_A5", "PDF_A4", "PDF_A5"):
        raise BusinessError("Unknown print format")
    if slip.is_void:
        raise BusinessError(f"Gatepass {slip.dispatch_no} is void")
    if cs.needs_conversion(slip):  # made before case labels were mandatory: make the labels first
        raise BusinessError(f"Gatepass {slip.dispatch_no} has no case labels yet. On the gatepass screen choose "
                            "Make the case labels for this gatepass, print the labels - then print the gatepass")
    left = cs.labels_pending(db, slip)
    if left:  # the case labels come first, then the gatepass
        raise BusinessError(f"Print the case labels first ({left} of {slip.total_cases} not printed): gatepass screen > "
                            "Print the labels - then print the gatepass")
    is_reprint = slip.print_count > 0
    if is_reprint:
        if not user.can("dispatch.reprint"):
            raise BusinessError("Not authorised to reprint dispatch slips")
        if cfg.get(db, "dispatch.reprint_reason_required") and (not reason or len(reason.strip()) < 5):
            raise BusinessError("Reason is mandatory for reprinting a dispatch slip (at least 5 characters)")
    else:
        _perm(user, "dispatch.operate")
    slip.print_count += 1
    logs = []
    for inv in slip_invoices(db, slip):
        inv.print_count = (inv.print_count or 0) + 1
        log = SlipPrintLog(slip_id=slip.id, invoice_id=inv.id, dispatch_no=slip.dispatch_no,
                           print_no=slip.print_count, is_reprint=is_reprint, output=output,
                           reason=(reason or "").strip() or None, printed_by_id=user.id, ip=context.request_ip.get())
        db.add(log)
        logs.append(log)
        if not is_reprint:
            inv.first_printed_at = utcnow()
            inv.first_printed_by_id = user.id
            if inv.status == S.SLIP_GENERATED:
                inv.status = S.SLIP_PRINTED
        audit.log(db, user, "SLIP_REPRINTED" if is_reprint else "SLIP_PRINTED", MODULE, record_type="Invoice",
                  record_id=inv.id, ref=inv.invoice_no, field="print_count", old=slip.print_count - 1,
                  new=slip.print_count, reason=reason, details={"dispatch_no": slip.dispatch_no, "output": output})
    db.flush()
    return logs


def record_print(db: Session, user: User, inv: Invoice, output: str, reason: str | None = None) -> tuple[DispatchSlip, SlipPrintLog]:
    slip = active_slip(db, inv)
    if not slip:
        raise BusinessError("Generate the dispatch slip first")
    logs = _print_slip(db, user, slip, output, reason)
    log = next((lg for lg in logs if lg.invoice_id == inv.id), logs[0])
    return slip, log


def print_gatepass(db: Session, user: User, slip: DispatchSlip, output: str, reason: str | None = None) -> SlipPrintLog:
    return _print_slip(db, user, slip, output, reason)[0]


# ----------------------------------------------------------------------------- gate-out (only by the gate staff's scan)
def completion_blockers(db: Session, inv: Invoice) -> list[str]:
    """What still stops this invoice from leaving: the gatepass printed (after its case labels), every case scanned (or an
    exception), the Garage Pawatis printed and, above the limit, a valid e-way bill."""
    if inv.status == S.DISPATCHED:
        return ["Already dispatched"]
    if inv.status == S.CANCELLED:
        return ["Invoice cancelled"]
    slip = active_slip(db, inv)
    if slip is not None and cs.needs_conversion(slip):  # made before case labels were mandatory: the labels come first
        return cs.gate_clear(db, slip)
    if inv.status != S.SLIP_PRINTED:
        return ["The gatepass must be printed (after its case labels) before the goods can leave"]
    if slip is not None:
        return cs.gate_clear(db, slip) or pws.gate_clear(db, slip) or eway_blockers(db, inv)
    return eway_blockers(db, inv)


def eway_blockers(db: Session, inv: Invoice) -> list[str]:
    """Setting gate.eway_required_above: invoices above that value need a valid e-way bill before gate-out."""
    limit = cfg.get(db, "gate.eway_required_above") or 0
    if not limit or (inv.sales_value or 0) <= limit:
        return []
    if not inv.eway_bill_no:
        return [f"E-way bill number missing for invoice {inv.invoice_no} (value above ₹{limit:,.0f})"]
    if inv.eway_valid_until and inv.eway_valid_until < local_today():
        return [f"E-way bill {inv.eway_bill_no} of invoice {inv.invoice_no} expired on {inv.eway_valid_until:%d-%m-%Y}"]
    return []


def _complete(db: Session, user: User, inv: Invoice) -> None:
    old = inv.status
    inv.status = S.DISPATCHED
    inv.completed_at = utcnow()
    inv.completed_by_id = user.id
    inv.erp_sync_status = "PENDING"
    if inv.dispatch_slip_id:
        cs.mark_dispatched(db, inv.dispatch_slip_id, inv.id)
    audit.log(db, user, "DISPATCH_COMPLETED", MODULE, record_type="Invoice", record_id=inv.id, ref=inv.invoice_no,
              field="status", old=old, new=S.DISPATCHED, details={"dispatch_no": inv.dispatch_no})


def gate_scan_out(db: Session, user: User, slip: DispatchSlip) -> int:
    """The gate staff scan the gatepass after its last case label: every invoice on it becomes DISPATCHED and the gatepass
    is gone out. This is the ONLY way goods are recorded as gone out - there is no button and no other path. Everything
    that must be true first (case labels made and printed, the gatepass printed, every case scanned or an exception,
    Pawatis printed, e-way bill) is checked here."""
    _perm(user, "gate.scan")
    if slip.is_void:
        raise BusinessError(f"Gatepass {slip.dispatch_no} is VOID - the goods must not leave on it")
    if slip.gate_out_at:
        raise BusinessError(f"Gatepass {slip.dispatch_no} already went out on {slip.gate_out_at:%d-%m-%Y %H:%M} UTC")
    invs = [i for i in slip_invoices(db, slip) if i.status != S.CANCELLED]
    problems = sorted({p for i in invs for p in completion_blockers(db, i) if p != "Already dispatched"})
    if not problems and not any(i.status != S.DISPATCHED for i in invs):
        problems = [f"Gatepass {slip.dispatch_no} has no invoice left to send"]
    if problems:
        audit.log(db, user, "GATE_OUT_BLOCKED", MODULE, record_type="DispatchSlip", record_id=slip.id,
                  ref=slip.dispatch_no, reason="; ".join(problems))
        raise BusinessError("Gate-out refused: " + "; ".join(problems))
    n = 0
    for inv in invs:
        if inv.status != S.DISPATCHED:
            _complete(db, user, inv)
            n += 1
    slip.gate_out_at = utcnow()
    slip.gate_out_by_id = user.id
    audit.log(db, user, "GATE_OUT", MODULE, record_type="DispatchSlip", record_id=slip.id, ref=slip.dispatch_no,
              details={"invoices": n, "cases": slip.total_cases, "via": "gate scan"})
    notify_service.on_gate_out(db, user, slip, invs)  # "your order is on the way" (when alerts are switched on)
    return n


# ----------------------------------------------------------------------------- LR (arrives after dispatch)
def lr_locked_reason(db: Session, inv: Invoice) -> str | None:
    if inv.status == S.CANCELLED:
        return "invoice cancelled"
    if inv.status in S.PRE_SLIP:
        return None
    if inv.transporter_bill_id:
        bill = db.get(TransporterBill, inv.transporter_bill_id)
        if bill and bill.status in (BillStatus.APPROVED, BillStatus.PAID):
            return f"on approved transporter bill {bill.bill_no}"
    return None


def set_lr(db: Session, user: User, invs: list[Invoice], lr_no: str | None, lr_date=None,
           reason: str | None = None, *, from_bill: bool = False) -> int:
    """Enter / correct the LR (bilty / GR / docket) number of a consignment - any time until the bill is approved.
    from_bill: accounts staff accepting a bill line as this consignment (they hold recon.edit, not dispatch rights)."""
    if not (from_bill and user.can("recon.edit")):
        _perm(user, "dispatch.operate")
    lr = (str(lr_no or "").strip().upper()) or None
    if lr and len(lr) > 40:
        raise BusinessError("LR number is too long")
    try:
        d = parse_date(lr_date) if lr_date not in (None, "") else None
    except ValueError as e:
        raise BusinessError(f"LR date: {e}") from e
    if not invs:
        raise BusinessError("No invoices selected")
    for inv in invs:
        why = lr_locked_reason(db, inv)
        if why:
            raise BusinessError(f"LR of invoice {inv.invoice_no} cannot be changed: {why}")
        if inv.status not in (S.SLIP_GENERATED, S.SLIP_PRINTED, S.DISPATCHED):
            raise BusinessError(f"Invoice {inv.invoice_no} is not on a gatepass yet")
    trs_ids = {i.transporter_id for i in invs}
    if lr and len(trs_ids) == 1:
        dup = _lr_conflict(db, lr, next(iter(trs_ids)), invs)
        if dup:
            raise BusinessError(f"LR {lr} of this transporter is already used for {dup.customer.name} (invoice "
                                f"{dup.invoice_no}). One LR belongs to one consignee - check the number.")
    if d and any(d < (i.dispatch_date or i.invoice_date) for i in invs):
        raise BusinessError("LR date cannot be before the dispatch date")
    n = 0
    for inv in invs:
        if inv.lr_no == lr and inv.lr_date == (d or inv.lr_date):
            continue
        changed = inv.lr_no is not None and inv.lr_no != lr
        if changed and (not reason or len(reason.strip()) < 3) and inv.lr_updated_at:
            raise BusinessError(f"Invoice {inv.invoice_no} already has LR {inv.lr_no}; give a reason for changing it")
        audit.log(db, user, "LR_CHANGED" if changed else "LR_ENTERED", MODULE, record_type="Invoice", record_id=inv.id,
                  ref=inv.invoice_no, field="lr_no", old=inv.lr_no, new=lr, reason=reason,
                  details={"lr_date": d.isoformat() if d else None, "dispatch_no": inv.dispatch_no})
        old_lr = inv.lr_no
        inv.lr_no = lr
        lrb.on_lr_changed(db, user, inv, old_lr, lr, reason)
        if d or not lr:
            inv.lr_date = d
        inv.lr_updated_at = utcnow()
        inv.lr_updated_by_id = user.id
        n += 1
    if n and lr:
        for sl in {i.dispatch_slip_id for i in invs if i.dispatch_slip_id}:  # v1.7: an LR after gate-out = Pawati received
            slip_obj = db.get(DispatchSlip, sl)
            pws.receive_by_lr(db, user, slip_obj, {i.id for i in invs}, invs[0].transporter_id)
        notify_service.on_lr(db, user, invs)  # tell the customer the LR number (when alerts are switched on)
    if n and invs[0].transporter_bill_id:  # re-check the bill this consignment is on
        bill = db.get(TransporterBill, invs[0].transporter_bill_id)
        if bill and bill.status in (BillStatus.PENDING, BillStatus.MATCHED, BillStatus.VARIANCE):
            from app.services import recon_service
            recon_service.rematch(db, bill)
    return n


def set_alloc_lr(db: Session, user: User, slip: DispatchSlip, allocs: list[DispatchAllocation], lr_no: str | None,
                 lr_date=None, reason: str | None = None) -> int:
    """LR of a receiving transporter's PART of a split invoice (the invoice itself carries the main receiver's LR)."""
    _perm(user, "dispatch.operate")
    if slip.is_void:
        raise BusinessError("This gatepass is void")
    lr = (str(lr_no or "").strip().upper()) or None
    if lr and len(lr) > 40:
        raise BusinessError("LR number is too long")
    try:
        d = parse_date(lr_date) if lr_date not in (None, "") else None
    except ValueError as e:
        raise BusinessError(f"LR date: {e}") from e
    if not allocs:
        raise BusinessError("Nothing selected")
    t_id = allocs[0].receiving_transporter_id
    if lr:
        other = db.scalar(select(DispatchAllocation).join(Invoice, Invoice.id == DispatchAllocation.invoice_id).where(
            DispatchAllocation.receiving_transporter_id == t_id, DispatchAllocation.lr_no == lr,
            Invoice.customer_id != allocs[0].invoice.customer_id).limit(1))
        if other:
            raise BusinessError(f"LR {lr} of this transporter is already used for {other.invoice.customer.name}. "
                                "One LR belongs to one consignee - check the number.")
    if d and d < (slip.dispatch_date or d):
        raise BusinessError("LR date cannot be before the dispatch date")
    n = 0
    for a in allocs:
        if a.lr_no == lr and (not d or a.lr_date == d):
            continue
        changed = a.lr_no is not None and a.lr_no != lr
        if changed and (not reason or len(reason.strip()) < 3):
            raise BusinessError(f"{a.transporter.name} already has LR {a.lr_no} here; give a reason for changing it")
        audit.log(db, user, "LR_CHANGED" if changed else "LR_ENTERED", MODULE, record_type="DispatchAllocation",
                  record_id=a.id, ref=a.invoice.invoice_no, field="lr_no", old=a.lr_no, new=lr, reason=reason,
                  details={"lr_date": d.isoformat() if d else None, "dispatch_no": slip.dispatch_no,
                           "receiver": a.transporter.code, "part_of_split_invoice": True})
        old_lr = a.lr_no
        a.lr_no = lr
        lrb.on_lr_changed_t(db, user, t_id, old_lr, lr, reason)
        if d or not lr:
            a.lr_date = d
        n += 1
    if n and lr:
        pws.receive_by_lr(db, user, slip, {a.invoice_id for a in allocs}, t_id)
    return n


# ----------------------------------------------------------------------------- void / cancel
def _release(db: Session, inv: Invoice) -> None:
    """Take an invoice off its gatepass: back to the dispatch queue; card-priced freight is worked out again next time."""
    inv.dispatch_no = None
    inv.dispatch_slip_id = None
    inv.slip_generated_at = None
    inv.print_count = 0
    inv.first_printed_at = None
    if inv.rate_level in GATEPASS_LEVELS:
        inv.rate_per_box = None
        inv.rate_level = None
        inv.freight_amount = None
        inv.freight_basis = "AUTO"
    inv.extra_charge = None


def void_gatepass(db: Session, user: User, slip: DispatchSlip, reason: str) -> int:
    _perm(user, "dispatch.void_slip")
    if slip.is_void:
        raise BusinessError("Gatepass is already void")
    if not reason or len(reason.strip()) < 5:
        raise BusinessError("Reason (min 5 characters) is required to void a gatepass")
    invs = slip_invoices(db, slip)
    gone = [i.invoice_no for i in invs if i.status == S.DISPATCHED]
    if gone:
        raise BusinessError(f"Invoices {', '.join(gone[:5])} already left the gate; cancel them individually "
                            "(administrator) instead of voiding the gatepass")
    slip.is_void = True
    slip.voided_at = utcnow()
    slip.voided_by_id = user.id
    slip.void_reason = reason.strip()
    cs.void_for_slip(db, slip)
    pws.cancel_for_slip(db, user, slip, f"Gatepass voided: {reason.strip()}")
    lrb.release_for_slip(db, slip)
    n = 0
    for inv in invs:
        if inv.status not in (S.SLIP_GENERATED, S.SLIP_PRINTED) or inv.dispatch_slip_id not in (None, slip.id):
            continue
        old = inv.status
        _release(db, inv)
        inv.status = S.VERIFIED if inv.verified_at else S.PENDING
        refresh_status(db, inv)
        audit.log(db, user, "SLIP_VOIDED", MODULE, record_type="Invoice", record_id=inv.id, ref=inv.invoice_no,
                  field="status", old=old, new=inv.status, reason=reason, details={"dispatch_no": slip.dispatch_no})
        n += 1
    audit.log(db, user, "GATEPASS_VOIDED", MODULE, record_type="DispatchSlip", record_id=slip.id,
              ref=slip.dispatch_no, reason=reason, details={"invoices_released": n})
    return n


def void_slip(db: Session, user: User, inv: Invoice, reason: str) -> None:
    _perm(user, "dispatch.void_slip")
    if inv.status not in (S.SLIP_GENERATED, S.SLIP_PRINTED):
        raise BusinessError("Only a generated/printed slip of an open dispatch can be voided")
    if not reason or len(reason.strip()) < 5:
        raise BusinessError("Reason (min 5 characters) is required to void a slip")
    slip = active_slip(db, inv)
    if slip:
        void_gatepass(db, user, slip, reason)
        return
    old = inv.status
    _release(db, inv)
    inv.status = S.VERIFIED if inv.verified_at else S.PENDING
    audit.log(db, user, "SLIP_VOIDED", MODULE, record_type="Invoice", record_id=inv.id, ref=inv.invoice_no,
              field="status", old=old, new=inv.status, reason=reason)
    refresh_status(db, inv)


def cancel(db: Session, user: User, inv: Invoice, reason: str) -> None:
    _perm(user, "dispatch.cancel")
    if inv.status == S.CANCELLED:
        raise BusinessError("Already cancelled")
    if inv.status == S.DISPATCHED and not user.can("dispatch.cancel_dispatched"):
        raise BusinessError("Only an administrator can cancel a completed dispatch")
    if not reason or len(reason.strip()) < 5:
        raise BusinessError("Cancellation reason (min 5 characters) is mandatory")
    if inv.transporter_bill_id:
        bill = db.get(TransporterBill, inv.transporter_bill_id)
        if bill and bill.status in (BillStatus.APPROVED, BillStatus.PAID):
            raise BusinessError(f"Invoice is on approved/paid transporter bill {bill.bill_no}; cannot cancel")
    slip = active_slip(db, inv)
    if slip and inv.status in (S.SLIP_GENERATED, S.SLIP_PRINTED) and len(slip.lines) > 1:
        raise BusinessError(f"Invoice is on gatepass {slip.dispatch_no} with {len(slip.lines) - 1} other invoice(s). "
                            "Void the gatepass first (it can then be made again without this invoice).")
    if slip and len(slip.lines) <= 1:
        slip.is_void = True
        slip.voided_at = utcnow()
        slip.voided_by_id = user.id
        slip.void_reason = f"Dispatch cancelled: {reason}"
    if slip:
        cs.void_for_invoice(db, slip.id, inv.id)
    old = inv.status
    inv.status = S.CANCELLED
    inv.cancelled_at = utcnow()
    inv.cancelled_by_id = user.id
    inv.cancel_reason = reason.strip()
    audit.log(db, user, "DISPATCH_CANCELLED", MODULE, record_type="Invoice", record_id=inv.id, ref=inv.invoice_no,
              field="status", old=old, new=S.CANCELLED, reason=reason,
              details={"dispatch_no": slip.dispatch_no if slip else None})


def reinstate(db: Session, user: User, inv: Invoice, reason: str) -> None:
    _perm(user, "dispatch.cancel")
    if not user.can("override.approve"):
        raise BusinessError("Only an approver can reinstate a cancelled invoice")
    if inv.status != S.CANCELLED:
        raise BusinessError("Only cancelled invoices can be reinstated")
    if not reason or len(reason.strip()) < 5:
        raise BusinessError("Reason is mandatory")
    inv.status = S.PENDING
    _release(db, inv)
    inv.completed_at = None
    inv.verified_at = None
    inv.cancelled_at = None
    refresh_status(db, inv)
    audit.log(db, user, "DISPATCH_REINSTATED", MODULE, record_type="Invoice", record_id=inv.id, ref=inv.invoice_no,
              field="status", old=S.CANCELLED, new=inv.status, reason=reason)


# ----------------------------------------------------------------------------- UI helpers
@dataclass
class Actions:
    can_edit: bool       # boxes / rate can still be changed (not on a gatepass yet)
    can_verify: bool
    can_void: bool
    can_cancel: bool
    can_reinstate: bool
    can_override: bool
    complete_blockers: list[str]   # what still stops the goods from leaving (shown, never actionable here)


def actions_for(db: Session, user: User, inv: Invoice) -> Actions:
    pre = inv.status in S.PRE_SLIP
    slipped = inv.status in (S.SLIP_GENERATED, S.SLIP_PRINTED)
    cb = completion_blockers(db, inv) if inv.status not in (S.DISPATCHED, S.CANCELLED) and not pre else []
    return Actions(
        can_edit=pre and user.can("dispatch.operate"),
        can_verify=inv.status == S.PENDING and user.can("dispatch.verify"),
        can_void=slipped and user.can("dispatch.void_slip"),
        can_cancel=(inv.status != S.CANCELLED and user.can("dispatch.cancel")
                    and (inv.status != S.DISPATCHED or user.can("dispatch.cancel_dispatched"))),
        can_reinstate=inv.status == S.CANCELLED and user.can("dispatch.cancel") and user.can("override.approve"),
        can_override=pre and (user.can("override.request") or user.can("override.direct")),
        complete_blockers=cb,
    )


def parse_money(v) -> Decimal | None:
    try:
        d = parse_decimal(v)
    except ValueError as e:
        raise BusinessError(f"Amount: {e}") from e
    return money(d) if d is not None else None
