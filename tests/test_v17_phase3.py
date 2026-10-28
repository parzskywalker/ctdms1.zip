"""v1.7 Phase 3: customer delivery areas, eligible transporters, depot / company rate exceptions with a recorded
breakdown, and the grouping rules of a gatepass (consignments, companies only by explicit choice).

Own masters are created here (transporter P3-TR, areas P3-TOWN / P3-VILLAGE) so the seeded rate cards of the other
suites are never touched.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (
    AuditLog, CFA, Company, Customer, CustomerDeliveryLocation, DispatchSlip, ExceptionCode, Invoice, Location, Role,
    Transporter, TransporterRate, User, transporter_depots,
)
from app.services import customer_area_service as cas
from app.services import eligibility_service as es
from app.services import settings_service as cfg
from app.services import transporter_rate_service as trs
from app.services.errors import BusinessError
from app.services.utils import local_today
from tests.conftest import WebClient, _test_session
from tests.helpers import make_invoice, new_invoice_no
from tests.test_rbac import _token_client
from tests.test_v17_phase2 import PW, audit_actions, save_user

TODAY = local_today()
LONG_AGO = date(2020, 1, 1)


# ----------------------------------------------------------------------------- fixtures
def _admin(s) -> User:
    return s.scalar(select(User).where(User.username == "admin"))


def _loc(s, code, name) -> Location:
    row = s.scalar(select(Location).where(Location.code == code))
    if not row:
        row = Location(code=code, name=name, is_active=True)
        s.add(row)
        s.commit()
    return row


def _transporter(s, code, name) -> Transporter:
    row = s.scalar(select(Transporter).where(Transporter.code == code))
    if not row:
        row = Transporter(code=code, name=name, payment_mode="TBB", is_active=True)
        s.add(row)
        s.commit()
    return row


def _rate(s, t, loc, amount, *, cfa=None, company=None, start=LONG_AGO) -> TransporterRate:
    try:
        r = trs.propose(s, _admin(s), transporter_id=t.id, location_id=loc.id, rate_per_case=amount, effective_from=start,
                        reason="phase 3 test", auto_approve=True, cfa_id=cfa.id if cfa else None,
                        company_id=company.id if company else None)
    except BusinessError:  # the module fixture runs once per importing test module; the database is shared
        s.rollback()
        return None
    s.commit()
    return r


def _by_code(s, model, code):
    return s.scalar(select(model).where(model.code == code))


@pytest.fixture(scope="module")
def world():
    s = _test_session()
    try:
        w = {"town": _loc(s, "P3-TOWN", "P3 Town"), "village": _loc(s, "P3-VILLAGE", "P3 Village"),
             "tr": _transporter(s, "P3-TR", "P3 Transport"), "tr2": _transporter(s, "P3-TR2", "P3 Roadways"),
             "rpr": _by_code(s, CFA, "RPR"), "bsp": _by_code(s, CFA, "BSP"),
             "coa": _by_code(s, Company, "COA"), "cob": _by_code(s, Company, "COB")}
        # P3-TR: general 100 to the town, 120 to the village; P3-TR2: 110 to the town only
        _rate(s, w["tr"], w["town"], 100)
        _rate(s, w["tr"], w["village"], 120)
        _rate(s, w["tr2"], w["town"], 110)
        w["ids"] = {k: getattr(v, "id") for k, v in w.items() if hasattr(v, "id")}
        yield w
    finally:
        s.close()


@pytest.fixture()
def db():
    s = _test_session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _gatepass(c: WebClient, invs, **fields):
    data = {"ids": [str(i["id"]) for i in invs], "dispatch_date": TODAY.isoformat(), "pickup_mode": "REGULAR", **fields}
    for i in invs:
        data[f"cases_{i['id']}"] = str(i.get("boxes") or 4)
    return c.post("/gatepass/new", data=data)


def _slip_of(r) -> int | None:
    m = re.search(r"/gatepass/(\d+)$", str(r.url))
    return int(m.group(1)) if m else None


def _line_notes(db, slip_id):
    db.expire_all()
    slip = db.get(DispatchSlip, slip_id)
    return slip, list(slip.lines)


# ----------------------------------------------------------------------------- customer area
def _customer(db, code, loc_code="RPR", ptype="STOCKIST") -> Customer:
    c = db.scalar(select(Customer).where(Customer.code == code))
    if not c:
        c = Customer(code=code, name=f"{code} Traders", party_type=ptype, location_id=_by_code(db, Location, loc_code).id,
                     is_active=True)
        db.add(c)
        db.commit()
    return c


def _add_dl(db, cust, loc, label="", ship_to=None, default=False):
    row = CustomerDeliveryLocation(customer_id=cust.id, location_id=loc.id, label=label, ship_to_code=ship_to,
                                   is_default=default, is_active=True)
    db.add(row)
    db.commit()
    return row


def test_area_resolution_rules(db, world):
    plain = _customer(db, "P3-PLAIN")
    rpr, bsp = _by_code(db, Location, "RPR"), _by_code(db, Location, "BSP")
    # one location: the file's area may be that area; no area in the file -> the default; a different area -> mismatch
    assert cas.resolve(db, plain).location.id == rpr.id and cas.resolve(db, plain).resolved
    assert cas.resolve(db, plain, rpr).resolved
    r = cas.resolve(db, plain, bsp)
    assert r.code == ExceptionCode.AREA_MISMATCH and r.location.id == bsp.id and "RPR" in r.message or "Raipur" in r.message
    # several delivery locations
    multi = _customer(db, "P3-MULTI")
    a = _add_dl(db, multi, rpr, "Godown", ship_to="SHIP-1")
    b = _add_dl(db, multi, world["town"], "Branch", ship_to="SHIP-2")
    r = cas.resolve(db, multi)
    assert r.code == ExceptionCode.AREA_AMBIGUOUS and "Godown" in r.message and "Branch" in r.message
    assert cas.resolve(db, multi, ship_to="ship-2").delivery.id == b.id           # ship-to code (any case) decides
    assert cas.resolve(db, multi, world["town"]).delivery.id == b.id and cas.resolve(db, multi, world["town"]).resolved
    assert cas.resolve(db, multi, bsp).code == ExceptionCode.AREA_MISMATCH
    a.is_default = True
    db.commit()
    assert cas.resolve(db, multi).delivery.id == a.id and cas.resolve(db, multi).resolved   # a default settles it
    # a customer with no location anywhere
    nowhere = Customer(code="P3-NOWHERE", name="Nowhere", party_type="STOCKIST", is_active=True)
    db.add(nowhere)
    db.commit()
    assert cas.resolve(db, nowhere).code == "MISSING_LOCATION"


def _ingest_one(api, cust_code, *, location=None, company="COA", cfa="RPR", boxes=4):
    row = {"invoice_no": new_invoice_no("P3A"), "invoice_date": TODAY.isoformat(),
           "company_code": company, "cfa_code": cfa, "customer_code": cust_code, "sales_value": "5000", "boxes": boxes}
    if location:
        row["location"] = location
    res = api.post("/api/v1/invoices/ingest", json={"invoices": [row]}).json()
    assert res["imported"] == 1, res
    return res["rows"][0]["invoice_id"]


def _inv(db, iid) -> Invoice:
    db.expire_all()
    return db.get(Invoice, iid)


def test_import_with_several_delivery_locations_waits_for_the_operators_choice(db, world):
    multi = _customer(db, "P3-MULTI2")
    god = _add_dl(db, multi, _by_code(db, Location, "RPR"), "Godown")
    br = _add_dl(db, multi, world["town"], "Branch")
    api = _token_client("admin")
    iid = _ingest_one(api, "P3-MULTI2")
    inv = _inv(db, iid)
    assert inv.exception_code == ExceptionCode.AREA_AMBIGUOUS and inv.status == "EXCEPTION"
    # it cannot go on a gatepass yet - and the message says what to do
    c = WebClient("admin")
    r = _gatepass(c, [{"id": iid, "boxes": 4}], mode="SELF")
    assert _slip_of(r) is None and "choose the delivery location" in r.text
    # the operator picks one (audited), the invoice is released
    op = WebClient("operator.raipur")
    assert op.post(f"/invoices/{iid}/area", data={"delivery_id": str(br.id)}).status_code == 200
    inv = _inv(db, iid)
    assert inv.location_id == world["town"].id and inv.customer_location_id == br.id and inv.exception_code is None
    assert inv.status in ("PENDING", "VERIFIED")
    assert "INVOICE_AREA_SET" in audit_actions(db, inv.invoice_no)
    r = _gatepass(c, [{"id": iid, "boxes": 4}], mode="SELF")
    assert _slip_of(r), r.text[:500]
    # not after the gatepass
    r = op.post(f"/invoices/{iid}/area", data={"delivery_id": str(god.id)})
    assert "cannot change once" in r.text


def test_a_file_area_that_is_not_a_customer_location_follows_the_setting(db, world):
    from app.models import Setting
    cust = _customer(db, "P3-MISMATCH")
    api = _token_client("admin")
    # default setting ACCEPT = v1.6.1 behaviour: the file's area wins, the invoice is not held
    iid = _ingest_one(api, "P3-MISMATCH", location="BSP")
    inv = _inv(db, iid)
    assert inv.location.code == "BSP" and inv.exception_code is None
    # EXCEPTION: the invoice is held until the area is confirmed
    row = db.get(Setting, "area.mismatch_action") or Setting(key="area.mismatch_action", value_type="choice",
                                                              category="Delivery areas", choices="ACCEPT,EXCEPTION")
    row.value = "EXCEPTION"
    db.add(row)
    db.commit()
    try:
        iid2 = _ingest_one(api, "P3-MISMATCH", location="BSP")
        inv2 = _inv(db, iid2)
        assert inv2.exception_code == ExceptionCode.AREA_MISMATCH and inv2.status == "EXCEPTION"
        op = WebClient("operator.raipur")
        # confirming another area needs a reason
        r = op.post(f"/invoices/{iid2}/area", data={"location_id": str(inv2.location_id)})
        assert "give a reason" in r.text and _inv(db, iid2).exception_code == ExceptionCode.AREA_MISMATCH
        op.post(f"/invoices/{iid2}/area", data={"location_id": str(inv2.location_id), "reason": "customer moved godown"})
        assert _inv(db, iid2).exception_code is None
        # or choose the customer's own location
        iid3 = _ingest_one(api, "P3-MISMATCH", location="BSP")
        op.post(f"/invoices/{iid3}/area", data={"delivery_id": ""})
        assert _inv(db, iid3).exception_code == ExceptionCode.AREA_MISMATCH  # nothing chosen, nothing changed
    finally:
        row.value = "ACCEPT"
        db.commit()


def test_customer_screen_adds_defaults_and_removes_delivery_locations(db, world):
    cust = _customer(db, "P3-UI")
    admin = WebClient("admin")
    page = admin.get(f"/masters/customers/{cust.id}").text
    assert "Delivery locations" in page and "Add delivery location" in page
    admin.post(f"/masters/customers/{cust.id}/locations", data={
        "location_id": str(world["town"].id), "label": "Godown", "ship_to_code": "UI-1", "is_default": "1"})
    admin.post(f"/masters/customers/{cust.id}/locations", data={"location_id": str(world["village"].id), "label": "Depot 2",
                                                                "ship_to_code": "UI-2"})
    rows = cas.delivery_locations(db, cust)
    assert {r.ship_to_code for r in rows} == {"UI-1", "UI-2"} and [r.is_default for r in rows] == [True, False]
    # the same ship-to code twice is refused
    r = admin.post(f"/masters/customers/{cust.id}/locations", data={"location_id": str(world["town"].id), "label": "x",
                                                                    "ship_to_code": "UI-2"})
    assert "already used" in r.text
    second = rows[1]
    admin.post(f"/masters/customers/{cust.id}/locations/{second.id}/default")
    db.expire_all()
    assert [r.is_default for r in cas.delivery_locations(db, cust)] == [False, True]
    admin.post(f"/masters/customers/{cust.id}/locations/{second.id}/remove")
    db.expire_all()
    assert [r.ship_to_code for r in cas.delivery_locations(db, cust)] == ["UI-1"]
    assert {"CUSTOMER_LOCATION_ADDED", "CUSTOMER_LOCATION_DEFAULT", "CUSTOMER_LOCATION_REMOVED"} <= audit_actions(db, cust.code)
    # only people who may edit masters
    for name in ("operator.raipur", "viewer"):
        assert WebClient(name).post(f"/masters/customers/{cust.id}/locations", data={"location_id": "1"}).status_code == 403


def test_merging_customers_moves_their_delivery_locations(db, world):
    keep, gone = _customer(db, "P3-KEEP"), _customer(db, "P3-GONE")
    _add_dl(db, gone, world["village"], "Far")
    admin = WebClient("admin")
    admin.post(f"/masters/customers/{keep.id}/merge", data={"source_id": str(gone.id), "reason": "same party twice"})
    db.expire_all()
    assert [r.label for r in cas.delivery_locations(db, keep)] == ["Far"]
    assert cas.delivery_locations(db, gone) == []


# ----------------------------------------------------------------------------- eligibility
def test_depots_served_default_is_every_depot_and_a_list_restricts(db, world):
    t = world["tr2"]
    assert es.depot_ids(db, t.id) == set() and es.serves_depot(db, t.id, world["rpr"].id)
    es.set_depots(db, _admin(db), t, [world["bsp"].id])
    db.commit()
    try:
        assert es.serves_depot(db, t.id, world["bsp"].id) and not es.serves_depot(db, t.id, world["rpr"].id)
        assert "TRANSPORTER_DEPOTS_SET" in audit_actions(db, t.code)
        with pytest.raises(BusinessError):
            es.set_depots(db, db.scalar(select(User).where(User.username == "viewer")), t, [])
    finally:
        es.set_depots(db, _admin(db), t, [])
        db.commit()
    assert es.serves_depot(db, t.id, world["rpr"].id)


def test_only_eligible_transporters_are_offered_and_mixed_areas_intersect(db, world):
    api = _token_client("admin")
    town = make_invoice(api, company="COA", cfa="RPR", customer="C-VDP01", location="P3-TOWN", boxes=4)
    village = make_invoice(api, company="COA", cfa="RPR", customer="C-VDP01", location="P3-VILLAGE", boxes=4)
    invs = lambda *ids: [db.get(Invoice, i["id"]) for i in ids]  # noqa: E731
    names = lambda lst: {t.code for t in lst if t.code.startswith("P3-")}  # noqa: E731
    assert names(es.eligible(db, invs(town), TODAY)) == {"P3-TR", "P3-TR2"}
    assert names(es.eligible(db, invs(village), TODAY)) == {"P3-TR"}              # TR2 has no rate for the village
    assert names(es.eligible(db, invs(town, village), TODAY)) == {"P3-TR"}        # intersection
    why = {c.transporter.code: c.why for c in es.evaluate(db, invs(village), TODAY)}
    assert why["P3-TR2"] and "P3 Village" in why["P3-TR2"][0] and not why["P3-TR"]
    # not serving the depot: out, with the reason in words
    es.set_depots(db, _admin(db), world["tr"], [world["bsp"].id])
    db.commit()
    try:
        assert names(es.eligible(db, invs(town), TODAY)) == {"P3-TR2"}
        reasons = {c.transporter.code: c.why for c in es.evaluate(db, invs(town), TODAY)}
        assert "does not serve" in reasons["P3-TR"][0]
        # nobody for the village: the message names the area, the depot and the next step
        text = es.explain_none(db, invs(village), TODAY)
        assert "P3 Village" in text and "Master update" in text
    finally:
        es.set_depots(db, _admin(db), world["tr"], [])
        db.commit()
    # in company-rate mode a missing rate is not an eligibility matter
    assert names(es.eligible(db, invs(village), TODAY, require_rate=False)) >= {"P3-TR", "P3-TR2"}
    # the builder offers the same facts to the screen
    page = WebClient("admin").get(f"/gatepass/new?ids={town['id']}").text
    assert "const CARDS" in page and "P3 Transport" in page and "gp-elig-msg" in page


def test_the_server_refuses_an_ineligible_transporter_and_audits_the_override(db, world):
    api = _token_client("admin")
    inv = make_invoice(api, company="COA", cfa="RPR", customer="C-VDP01", location="P3-TOWN", boxes=4)
    es.set_depots(db, _admin(db), world["tr"], [world["bsp"].id])
    db.commit()
    try:
        # a forged POST names the transporter that is not offered
        op = WebClient("operator.raipur")
        r = _gatepass(op, [inv], mode="TRANSPORTER", transporter_id=str(world["tr"].id))
        assert _slip_of(r) is None and "does not serve" in r.text
        # an override needs the permission (the Dispatch Operator has not got it) ...
        r = _gatepass(op, [inv], mode="TRANSPORTER", transporter_id=str(world["tr"].id),
                      eligibility_override_reason="goods urgent, only vehicle")
        assert _slip_of(r) is None and "not authorised to choose a transporter" in r.text
        # ... and a reason
        mgr = WebClient("cfa.raipur")
        r = _gatepass(mgr, [inv], mode="TRANSPORTER", transporter_id=str(world["tr"].id))
        assert _slip_of(r) is None
        r = _gatepass(mgr, [inv], mode="TRANSPORTER", transporter_id=str(world["tr"].id),
                      eligibility_override_reason="ok")
        assert _slip_of(r) is None and "at least 5 characters" in r.text
        r = _gatepass(mgr, [inv], mode="TRANSPORTER", transporter_id=str(world["tr"].id),
                      eligibility_override_reason="only vehicle going that way today")
        slip_id = _slip_of(r)
        assert slip_id, r.text[:400]
        slip, _ = _line_notes(db, slip_id)
        assert slip.eligibility_override_reason == "only vehicle going that way today"
        assert "ELIGIBILITY_OVERRIDE" in audit_actions(db, slip.dispatch_no)
        assert "chosen although it does not serve this depot" in WebClient("cfa.raipur").get(f"/gatepass/{slip_id}").text
    finally:
        es.set_depots(db, _admin(db), world["tr"], [])
        db.commit()


def test_a_transporter_without_depot_list_and_with_a_rate_works_as_before(db, world):
    api = _token_client("admin")
    inv = make_invoice(api, company="COA", cfa="RPR", customer="C-VDP01", location="P3-TOWN", boxes=5)
    r = _gatepass(WebClient("operator.raipur"), [inv], mode="TRANSPORTER", transporter_id=str(world["tr"].id))
    slip_id = _slip_of(r)
    assert slip_id
    slip, lines = _line_notes(db, slip_id)
    assert slip.freight_total == Decimal("500.00") and not slip.cross_company and not slip.eligibility_override_reason
    assert lines[0].rate_per_case == Decimal("100.00")


def test_transporter_master_has_depots_served_and_the_lr_book_flag(db, world):
    page = WebClient("admin").get(f"/masters/transporters/{world['tr'].id}").text
    assert 'name="served_depots"' in page and 'name="uses_lr_book"' in page and "Depots served" in page


# ----------------------------------------------------------------------------- rate exceptions and the breakdown
def test_the_most_specific_rate_wins_and_versions_are_kept_per_scope(db, world):
    t = _transporter(db, "P3-EXC", "P3 Exceptions")
    loc = world["town"]
    rpr, bsp, coa, cob = world["rpr"], world["bsp"], world["coa"], world["cob"]
    _rate(db, t, loc, 100)                                   # general
    _rate(db, t, loc, 95, company=coa)                       # company exception
    _rate(db, t, loc, 90, cfa=rpr)                           # depot exception
    _rate(db, t, loc, 85, cfa=rpr, company=coa)              # depot + company exception
    f = lambda cfa, co: trs.find(db, t.id, loc.id, TODAY, cfa_id=cfa.id if cfa else None, company_id=co.id if co else None)  # noqa: E731
    assert f(rpr, coa).rate_per_case == 85 and f(rpr, coa).rule == "Depot + company exception"
    assert f(rpr, cob).rate_per_case == 90 and f(rpr, cob).rule == "Depot exception"
    assert f(bsp, coa).rate_per_case == 95 and f(bsp, coa).rule == "Company exception"
    assert f(bsp, cob).rate_per_case == 100 and f(bsp, cob).rule == "General rate"
    assert f(None, None).rate_per_case == 100                # callers that do not know the invoice get the general rate
    # a new version of ONE scope ends only that scope's previous version
    new = trs.propose(db, _admin(db), transporter_id=t.id, location_id=loc.id, rate_per_case=88,
                      effective_from=TODAY + timedelta(days=10), reason="x", auto_approve=True, cfa_id=rpr.id)
    db.commit()
    assert db.get(TransporterRate, new.previous_rate_id).effective_to == TODAY + timedelta(days=9)
    assert f(rpr, coa).rate_per_case == 85 and f(bsp, cob).rate_per_case == 100            # others untouched
    assert trs.find(db, t.id, loc.id, TODAY + timedelta(days=10), cfa_id=rpr.id, company_id=cob.id).rate_per_case == 88
    # a second proposal for the same scope before the latest version is refused; another scope is fine
    with pytest.raises(BusinessError, match="must start after the latest version"):
        trs.propose(db, _admin(db), transporter_id=t.id, location_id=loc.id, rate_per_case=80, effective_from=TODAY,
                    reason="x", auto_approve=True, cfa_id=rpr.id)
    db.rollback()
    trs.propose(db, _admin(db), transporter_id=t.id, location_id=loc.id, rate_per_case=80, effective_from=TODAY,
                reason="x", auto_approve=True, cfa_id=bsp.id, company_id=cob.id)
    db.commit()
    assert f(bsp, cob).rate_per_case == 80


def test_every_existing_general_rate_prices_exactly_as_before(db):
    """No exception exists in a v1.6.1 database, so find() with any depot / company must equal the plain card."""
    depots, companies = list(db.scalars(select(CFA))), list(db.scalars(select(Company)))
    checked = 0
    for t in db.scalars(select(Transporter).where(~Transporter.code.like("P3-%"))):
        for loc_id, rate in trs.card(db, t.id, TODAY).items():
            for d in depots:
                for c in companies:
                    got = trs.find(db, t.id, loc_id, TODAY, cfa_id=d.id, company_id=c.id)
                    assert got.found and got.rate_per_case == rate and got.rule == "General rate"
                    checked += 1
    assert checked > 100


def test_the_gatepass_prices_with_the_exception_and_freezes_how(db, world):
    t = _transporter(db, "P3-EXC2", "P3 Exceptions 2")
    loc = world["town"]
    _rate(db, t, loc, 100)
    exc = _rate(db, t, loc, 85, cfa=world["rpr"], company=world["coa"])
    api = _token_client("admin")
    inv = make_invoice(api, company="COA", cfa="RPR", customer="C-VDP01", location="P3-TOWN", boxes=4)
    other = make_invoice(api, company="COB", cfa="RPR", customer="C-VDP01", location="P3-TOWN", boxes=4)
    op = WebClient("operator.raipur")
    slip_id = _slip_of(_gatepass(op, [inv], mode="TRANSPORTER", transporter_id=str(t.id)))
    slip_other = _slip_of(_gatepass(op, [other], mode="TRANSPORTER", transporter_id=str(t.id)))
    slip, (line,) = _line_notes(db, slip_id)
    assert slip.freight_total == Decimal("340.00") and line.rate_id == exc.id and line.rate_per_case == Decimal("85.00")
    n = line.calc_note
    assert n["source"] == "TRANSPORTER_CARD" and n["rule"] == "Depot + company exception" and n["cases"] == 4
    assert Decimal(n["rate"]) == 85 and Decimal(n["amount"]) == 340 and n["area"] == "P3 Town" and n["scope"] == "RPR + COA"
    _, (oline,) = _line_notes(db, slip_other)
    assert oline.rate_per_case == Decimal("100.00") and oline.calc_note["rule"] == "General rate"   # company B: general
    # a later rate change never touches what was frozen
    trs.propose(db, _admin(db), transporter_id=t.id, location_id=loc.id, rate_per_case=70, effective_from=TODAY + timedelta(days=3),
                reason="x", auto_approve=True, cfa_id=world["rpr"].id, company_id=world["coa"].id)
    db.commit()
    _, (line,) = _line_notes(db, slip_id)
    assert line.rate_per_case == Decimal("85.00") and line.calc_note["rate"] == "85.00"
    page = WebClient("operator.raipur").get(f"/gatepass/{slip_id}").text
    assert "How freight was calculated" in page and "Depot + company exception" in page and "exception" in page


def test_rate_card_screen_offers_exceptions_and_the_checker_approves_them(db, world):
    t = _transporter(db, "P3-CARD", "P3 Card")
    admin, checker = WebClient("admin"), WebClient("checker")
    page = admin.get(f"/rate-cards/{t.id}").text
    assert 'name="exc_location"' in page and 'name="exc_cfa"' in page and 'name="exc_company"' in page
    admin.post(f"/rate-cards/{t.id}/propose", data={"new_location": str(world["town"].id), "new_rate": "100",
                                                    "new_from": LONG_AGO.isoformat(), "reason": "letter"})
    pend = list(db.scalars(select(TransporterRate).where(TransporterRate.transporter_id == t.id)))
    assert len(pend) == 1 and pend[0].rank == 0
    # an exception needs a depot or a company
    r = admin.post(f"/rate-cards/{t.id}/propose", data={"exc_location": str(world["town"].id), "exc_rate": "90",
                                                        "exc_from": LONG_AGO.isoformat(), "reason": "letter"})
    assert "needs a depot and / or a company" in r.text
    admin.post(f"/rate-cards/{t.id}/propose", data={"exc_location": str(world["town"].id), "exc_cfa": str(world["rpr"].id),
                                                    "exc_rate": "90", "exc_from": LONG_AGO.isoformat(), "reason": "letter"})
    db.expire_all()
    rows = list(db.scalars(select(TransporterRate).where(TransporterRate.transporter_id == t.id).order_by(TransporterRate.id)))
    assert [(r.rank, r.cfa_id, r.status) for r in rows] == [(0, None, "PENDING_APPROVAL"), (2, world["rpr"].id, "PENDING_APPROVAL")]
    for r in rows:   # the maker cannot approve; the checker can
        assert "cannot approve" in admin.post(f"/rate-cards/rate/{r.id}/decide", data={"decision": "approve"}).text
        checker.post(f"/rate-cards/rate/{r.id}/decide", data={"decision": "approve", "remarks": "ok"})
    db.expire_all()
    assert all(db.get(TransporterRate, r.id).status == "APPROVED" for r in rows)
    page = admin.get(f"/rate-cards/{t.id}").text
    assert "RPR" in page and "all depots and companies" in page
    assert trs.find(db, t.id, world["town"].id, TODAY, cfa_id=world["rpr"].id).rate_per_case == Decimal("90.00")


# ----------------------------------------------------------------------------- grouping rules
def test_different_companies_only_by_explicit_choice(db, world):
    api = _token_client("admin")
    a = make_invoice(api, company="COA", cfa="RPR", customer="C-VDP01", location="P3-TOWN", boxes=4)
    b = make_invoice(api, company="COB", cfa="RPR", customer="C-VDP01", location="P3-TOWN", boxes=6)
    op = WebClient("operator.raipur")
    r = _gatepass(op, [a, b], mode="TRANSPORTER", transporter_id=str(world["tr"].id))
    assert _slip_of(r) is None and "different companies" in r.text and "Group across companies" in r.text
    r = _gatepass(op, [a, b], mode="TRANSPORTER", transporter_id=str(world["tr"].id), group_cross_company="1")
    slip_id = _slip_of(r)
    assert slip_id, r.text[:500]
    slip, lines = _line_notes(db, slip_id)
    assert slip.cross_company and slip.invoice_count == 2
    # identity is untouched: each invoice keeps its own company and number; one customer + area = one consignment
    assert {ln.invoice.company.code for ln in lines} == {"COA", "COB"} and {ln.consignment_no for ln in lines} == {1}
    assert {"GATEPASS_CROSS_COMPANY"} <= audit_actions(db, slip.dispatch_no)
    page = op.get(f"/gatepass/{slip_id}").text
    assert "different companies" in page and "C1" in page


def test_grouping_across_companies_needs_the_permission(db, world):
    save_user(WebClient("admin"), db, "p3.nogroup", "DISPATCH_OPERATOR", cfas=["RPR"], deny=["dispatch.group_cross_company"],
              reason="phase 3 test", companies=None)
    api = _token_client("admin")
    a = make_invoice(api, company="COA", cfa="RPR", customer="C-VDP01", location="P3-TOWN", boxes=4)
    b = make_invoice(api, company="COB", cfa="RPR", customer="C-VDP01", location="P3-TOWN", boxes=4)
    c = WebClient("p3.nogroup", PW)
    r = _gatepass(c, [a, b], mode="TRANSPORTER", transporter_id=str(world["tr"].id), group_cross_company="1")
    assert _slip_of(r) is None and "not authorised to put invoices of different companies" in r.text
    assert "disabled" in c.get(f"/gatepass/new?ids={a['id']}&ids={b['id']}").text  # the tick is offered disabled
    # one company at a time still works
    assert _slip_of(_gatepass(c, [a], mode="TRANSPORTER", transporter_id=str(world["tr"].id)))


def test_consignments_are_numbered_by_customer_and_area(db, world):
    api = _token_client("admin")
    a1 = make_invoice(api, company="COA", cfa="RPR", customer="C-VDP01", location="P3-TOWN", boxes=2)
    a2 = make_invoice(api, company="COA", cfa="RPR", customer="C-VDP01", location="P3-TOWN", boxes=3)
    b1 = make_invoice(api, company="COA", cfa="RPR", customer="C-MET01", location="P3-TOWN", boxes=4)
    c1 = make_invoice(api, company="COA", cfa="RPR", customer="C-MET01", location="P3-VILLAGE", boxes=5)
    slip_id = _slip_of(_gatepass(WebClient("admin"), [a1, b1, a2, c1], mode="TRANSPORTER", transporter_id=str(world["tr"].id)))
    assert slip_id
    _, lines = _line_notes(db, slip_id)
    by_inv = {ln.invoice_id: ln.consignment_no for ln in lines}
    assert by_inv[a1["id"]] == by_inv[a2["id"]] == 1 and by_inv[b1["id"]] == 2 and by_inv[c1["id"]] == 3   # same customer, other area = other consignment
    assert sum(ln.cases for ln in lines) == 14


def test_the_builder_screen_carries_the_new_controls(world):
    api = _token_client("admin")
    a = make_invoice(api, company="COA", cfa="RPR", customer="C-VDP01", location="P3-TOWN", boxes=4)
    page = WebClient("cfa.raipur").get(f"/gatepass/new?ids={a['id']}").text
    for marker in ("gp-cross-box", 'name="group_cross_company"', "gp-cons-list", "gp-override", "gp-elig-msg",
                   "function cardRate", "function eligibility"):
        assert marker in page, marker
    # a role without the override permission is not offered the override box
    assert 'id="gp-override"' not in WebClient("operator.raipur").get(f"/gatepass/new?ids={a['id']}").text


# ----------------------------------------------------------------------------- upgrade
def test_migrations_0011_0012_keep_data_and_keep_what_roles_could_do(tmp_path):
    import json
    import os
    import sqlite3
    import subprocess
    import sys

    from tests.test_v12_fixes import ROOT, _count, _run_prepare
    db_path = tmp_path / "ctdms.db"
    assert _run_prepare(db_path).returncode == 0
    env = {**os.environ, "CTDMS_DATABASE_URL": f"sqlite:///{db_path}", "CTDMS_TEST_DATABASE_URL": ""}
    down = subprocess.run([sys.executable, "-m", "alembic", "downgrade", "0010"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=600)
    assert down.returncode == 0, down.stdout + down.stderr
    con = sqlite3.connect(db_path)
    names = {x[0] for x in con.execute("select name from sqlite_master")}
    assert not {"customer_delivery_locations", "transporter_depots"} & names
    assert "cross_company" not in [c[1] for c in con.execute("pragma table_info(dispatch_slips)")]
    # a v1.7-Phase-2 role list: nobody has the two new permissions yet
    for code, perms in con.execute("select code, permissions from roles").fetchall():
        p = [x for x in json.loads(perms) if x not in ("dispatch.group_cross_company", "transporter.override_eligibility")]
        con.execute("update roles set permissions = ? where code = ?", (json.dumps(p), code))
    con.commit()
    invoices, rates, slips = (_count(db_path, "invoices"), _count(db_path, "transporter_rates"), _count(db_path, "dispatch_slips"))
    con.close()
    r = _run_prepare(db_path)
    assert r.returncode == 0, r.stdout + r.stderr
    con = sqlite3.connect(db_path)
    assert con.execute("select version_num from alembic_version").fetchone()[0] >= "0012"
    perms = {code: set(json.loads(p)) for code, p in con.execute("select code, permissions from roles")}
    for code, p in perms.items():
        if "*" in p:
            continue
        assert ("dispatch.group_cross_company" in p) == ("dispatch.operate" in p), code
        assert ("transporter.override_eligibility" in p) == ("override.approve" in p), code
    assert con.execute("select count(*) from audit_logs where action = 'ROLE_PERMISSIONS_CHANGED' and username = 'system'"
                       ).fetchone()[0] >= 3
    # nothing existing changed: every rate is still general, every slip has no breakdown, no cross-company flag
    assert con.execute("select count(*) from transporter_rates where cfa_id is not null or company_id is not null").fetchone()[0] == 0
    assert con.execute("select count(*) from dispatch_slips where cross_company = 1").fetchone()[0] == 0
    assert con.execute("select count(*) from invoices where customer_location_id is not null").fetchone()[0] == 0
    con.close()
    assert (_count(db_path, "invoices"), _count(db_path, "transporter_rates"), _count(db_path, "dispatch_slips")) == (invoices, rates, slips)


def test_the_quick_dispatch_screen_offers_only_eligible_transporters_too(db, world):
    api = _token_client("admin")
    inv = make_invoice(api, company="COA", cfa="RPR", customer="C-VDP01", location="P3-VILLAGE", boxes=4)
    page = WebClient("operator.raipur").get(f"/dispatch/{inv['id']}").text
    assert "P3 Transport" in page and "P3 Roadways" not in page        # P3-TR2 has no rate for the village
    town = make_invoice(api, company="COA", cfa="RPR", customer="C-VDP01", location="P3-TOWN", boxes=4)
    page = WebClient("operator.raipur").get(f"/dispatch/{town['id']}").text
    assert "P3 Transport" in page and "P3 Roadways" in page
    # a forged save of an ineligible transporter on the quick screen is still refused when the slip is made
    es.set_depots(db, _admin(db), world["tr"], [world["bsp"].id])
    db.commit()
    try:
        op = WebClient("operator.raipur")
        data = {"transporter_id": str(world["tr"].id), "dispatch_date": TODAY.isoformat(), "dispatch_time": "10:00"}
        op.post(f"/dispatch/{town['id']}/save", data=data)
        op.post(f"/dispatch/{town['id']}/verify", data=data)
        r = op.post(f"/dispatch/{town['id']}/generate", data=data)
        import re as _re
        assert "does not serve" in r.text, _re.findall(r'class="alert[^"]*"[^>]*>(.*?)</div>', r.text, _re.S)
    finally:
        es.set_depots(db, _admin(db), world["tr"], [])
        db.commit()


def test_a_practice_database_from_phase_2_gets_the_phase_3_columns(tmp_path):
    """Guided Training practice copies are upgraded in place: new columns arrive with their defaults, rows are kept."""
    import app.models  # noqa: F401
    from app.db import Base, _make_engine
    from app.training import runtime
    eng = _make_engine("sqlite:///" + (tmp_path / "practice.db").as_posix())
    Base.metadata.create_all(eng)
    with eng.begin() as c:
        c.exec_driver_sql("insert into transporters (code, name, payment_mode, direct_pickup_basis, is_active, created_at, updated_at, uses_lr_book) "
                          "values ('KEEP', 'Keep', 'TBB', 'PER_TRIP', 1, '2026-01-01', '2026-01-01', 0)")
        c.exec_driver_sql("alter table transporters drop column uses_lr_book")
        c.exec_driver_sql("alter table dispatch_slips drop column cross_company")
        c.exec_driver_sql("alter table dispatch_slips drop column eligibility_override_reason")
        c.exec_driver_sql("alter table dispatch_slip_lines drop column calc_note")
        c.exec_driver_sql("alter table dispatch_slip_lines drop column consignment_no")
        c.exec_driver_sql("drop table transporter_depots")
    runtime.sync_schema(eng)
    with eng.begin() as c:
        assert tuple(c.exec_driver_sql("select code, uses_lr_book from transporters").one()) == ("KEEP", 0)
        assert c.exec_driver_sql("select count(*) from transporter_depots").scalar() == 0
        cols = {r[1] for r in c.exec_driver_sql("pragma table_info(dispatch_slips)")}
        assert {"cross_company", "eligibility_override_reason"} <= cols
    eng.dispose()
