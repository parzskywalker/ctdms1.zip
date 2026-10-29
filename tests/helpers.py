from __future__ import annotations

import itertools
from datetime import date

from sqlalchemy import select

from app.models import Invoice
from app.services.utils import local_today

_seq = itertools.count(1)


def new_invoice_no(prefix: str = "T") -> str:
    return f"{prefix}{local_today():%y%m%d}-{next(_seq):04d}-{id(_seq) % 997}"


def ingest(api_client, rows: list[dict], erp_code: str | None = None, **extra) -> dict:
    body = {"invoices": rows, **extra}
    if erp_code:
        body["erp_code"] = erp_code
    r = api_client.post("/api/v1/invoices/ingest", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def make_invoice(api_client, *, company="COA", cfa="RPR", customer="C-VDP01", location=None, boxes=36,
                 sales="95000", inv_date: date | None = None, erp_code=None) -> dict:
    row = {"invoice_no": new_invoice_no(), "invoice_date": (inv_date or local_today()).isoformat(),
           "company_code": company, "cfa_code": cfa, "customer_code": customer, "sales_value": sales}
    if boxes is not None:
        row["boxes"] = boxes
    if location:
        row["location"] = location
    res = ingest(api_client, [row], erp_code)
    assert res["imported"] == 1, res
    return api_client.get(f"/api/v1/invoices/{res['rows'][0]['invoice_id']}").json()


def get_inv(invoice_id: int) -> Invoice:
    from tests.conftest import _test_session
    s = _test_session()
    try:
        inv = s.scalar(select(Invoice).where(Invoice.id == invoice_id))
        if inv is not None:  # load what assertions typically touch
            _ = (inv.customer, inv.company, inv.cfa, inv.location, inv.transporter)
        return inv
    finally:
        s.close()


def roles_matrix(db, extra: dict | None = None, drop: set | None = None) -> dict:
    """The full Roles & permissions form as the screen posts it: every role's ticked permissions (the form always carries
    all roles, so a partial post would clear the others). `extra` adds fields, `drop` removes some."""
    from app.models import Role
    from app.security.permissions import PERMISSIONS
    data = {}
    for r in db.scalars(select(Role)):
        if "*" in (r.permissions or []):
            continue
        for p in r.permissions or []:
            if p in PERMISSIONS:
                data[f"p_{r.id}_{p}"] = "1"
    data.update(extra or {})
    for k in drop or ():
        data.pop(k, None)
    return data


def local_auto(db=None) -> dict:
    """v1.7.1: a transporter trip always goes to the transporter by local auto - the fields every such request needs.
    (Read through a session of its own so the caller's transaction is not touched or locked.)"""
    from decimal import Decimal

    from app.models import OutsideAuto, PickupMode
    from tests.conftest import _test_session
    s = _test_session()
    try:
        aid = s.scalar(select(OutsideAuto.id).where(OutsideAuto.is_active.is_(True),
                                                    OutsideAuto.allowed_methods.in_(["PER_TRIP", "BOTH"])).limit(1))
    finally:
        s.close()
    return dict(pickup_mode=PickupMode.OUTSIDE_AUTO, outside_auto_id=aid, auto_method="PER_TRIP", trip_charge=Decimal("300"))


def print_pawatis(db, user, slip) -> None:
    """The Garage Pawatis of a local-auto trip travel with the auto: they are printed before it can leave."""
    from app.services import pawati_service as pws
    rows = pws.active(db, slip)
    if rows:
        pws.print_sheet(db, user, rows)
        db.flush()


# ----------------------------------------------------------------------------- v1.7.2: the real gate flow
def _user(db, username: str):
    from app.models import User
    return db.scalar(select(User).where(User.username == username))


def prepare_trip(db, slip, desk: str = "admin"):
    """What the dispatch desk does after making a gatepass: the case labels, then the gatepass, then the Garage Pawatis."""
    from app.services import gate_service
    return gate_service.prepare(db, _user(db, desk), slip)


def print_ready(db, slip, desk: str = "admin"):
    """The case labels printed, then the gatepass printed (not yet the Pawatis, not yet at the gate)."""
    from app.services import dispatch_service as ds
    from app.services import gate_service
    d = _user(db, desk)
    gate_service.print_labels(db, d, slip)
    ds.print_gatepass(db, d, slip, "PRINT_A5")
    return slip


def walk_out(db, slip, *, desk: str = "admin", gate: str = "admin") -> int:
    """The whole trip like the screens: labels printed, gatepass printed (when it is not yet), Pawatis printed; then the gate
    staff scan every case and the gatepass. Returns how many invoices became DISPATCHED. Nothing else marks a trip gone out."""
    from app.services import dispatch_service as ds
    from app.services import gate_service
    d = _user(db, desk)
    gate_service.print_labels(db, d, slip)
    if not slip.print_count:
        ds.print_gatepass(db, d, slip, "PRINT_A5")
    gate_service.print_pawatis(db, d, slip)
    return gate_service.scan_out(db, _user(db, gate), slip)


def _slip_facts(slip_id: int) -> dict:
    from sqlalchemy import select as _select

    from app.models import DispatchCase, DispatchSlip, GaragePawati
    from tests.conftest import _test_session
    s = _test_session()
    try:
        slip = s.get(DispatchSlip, slip_id)
        return {
            "pending": [str(i) for i in s.scalars(_select(DispatchCase.id).where(DispatchCase.slip_id == slip_id,
                                                                               DispatchCase.status == "LABEL_PENDING"))],
            "labelled": [c for c in s.scalars(_select(DispatchCase.case_code).where(
                DispatchCase.slip_id == slip_id, DispatchCase.status == "LABELLED").order_by(DispatchCase.id))],
            "pawatis": [str(i) for i in s.scalars(_select(GaragePawati.id).where(
                GaragePawati.slip_id == slip_id, GaragePawati.print_count == 0, GaragePawati.status != "CANCELLED"))],
            "printed": slip.print_count, "number": slip.dispatch_no}
    finally:
        s.close()


def confirm_labels(slip_id: int, desk: str = "admin") -> None:
    """The case labels are printed (and confirmed 'Yes, all printed') on the screen - the first thing the desk does."""
    from tests.conftest import WebClient
    ids = _slip_facts(slip_id)["pending"]
    if ids:
        WebClient(desk).post(f"/gatepass/{slip_id}/labels/confirm", data={"case_ids": ",".join(ids), "result": "PRINTED"})


def scan_cases_web(slip_id: int, gate: str = "admin") -> int:
    """The gate staff scan every labelled case that is not scanned yet (through the scan screen's own request)."""
    from tests.conftest import WebClient
    g = WebClient(gate)
    codes = _slip_facts(slip_id)["labelled"]
    for code in codes:
        assert g.post("/gatepass/scan-case", data={"code": code, "slip_id": str(slip_id)}).json()["ok"], code
    return len(codes)


def scan_gatepass_web(slip_id: int, gate: str = "admin") -> dict:
    """The gate staff scan the gatepass itself (after the last case): the JSON the gate screen shows."""
    from tests.conftest import WebClient
    return WebClient(gate).post("/gatepass/scan-case", data={"code": _slip_facts(slip_id)["number"],
                                                              "slip_id": str(slip_id)}).json()


def web_walk_out(slip_id: int, desk: str = "admin", gate: str = "admin") -> dict:
    """The same trip through the screens' own requests: labels printed, gatepass printed (when it is not yet), Garage Pawatis
    printed, every case scanned, then the gatepass scanned. Returns the JSON of the last scan."""
    from tests.conftest import WebClient
    confirm_labels(slip_id, desk)
    w = WebClient(desk)
    if not _slip_facts(slip_id)["printed"]:
        w.post(f"/gatepass/{slip_id}/print", data={"size": "A5", "mode": "pdf"})
    pawatis = _slip_facts(slip_id)["pawatis"]
    if pawatis:
        w.post("/pawati/print", data={"ids": pawatis})
    scan_cases_web(slip_id, gate)
    return scan_gatepass_web(slip_id, gate)
