"""v1.7 Phase 2: roles are editable templates, individual GRANT / DENY per user, operational scope per employee
(companies, depots, areas, customer types, customers), last-Administrator guard, audit trail.

Scope matrix used below (all invoices are made once, by the Administrator):
  area matrix     (customer Metro Pharma):  a_in  = COA @ Guma, area Raipur   | a_co = COB @ Guma, area Raipur
                                            a_dep = COA @ Bilaspur depot, area Raipur | a_area = COA @ Guma, area Bilaspur
  customer matrix (all COA @ Guma):          c_stock = Vardaan (STOCKIST) | c_metro = Metro (RETAILER) | c_ccc = City Care (RETAILER)
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import select

from app.models import AuditLog, CFA, Company, Customer, DispatchSlip, Invoice, Location, Role, User, UserPermission
from app.security.permissions import DEFAULT_ROLES, PERMISSIONS
from app.services import access
from app.services.errors import BusinessError
from tests.conftest import WebClient, _test_session
from tests.helpers import make_invoice, roles_matrix
from tests.test_rbac import _token_client

PW = "Phase2@2026xy"
TODAY_RANGE = "date_from=2020-01-01&date_to=2099-12-31"


# ----------------------------------------------------------------------------- helpers
def _ids(db):
    return {
        "role": {r.code: r.id for r in db.scalars(select(Role))},
        "company": {c.code: c.id for c in db.scalars(select(Company))},
        "cfa": {c.code: c.id for c in db.scalars(select(CFA))},
        "area": {x.code: x.id for x in db.scalars(select(Location))},
        "customer": {c.code: c.id for c in db.scalars(select(Customer))},
    }


def save_user(admin: WebClient, db, username, role_code, *, companies=None, cfas=None, areas=None, types=None,
              customers=None, grant=(), deny=(), reason="", extra=None, uid=None):
    """Post the user screen the way the browser does. None = 'All' for that dimension; a list = exactly those."""
    ids = _ids(db)
    data = {"access_form": "1", "username": username, "full_name": username, "role_id": str(ids["role"][role_code]),
            "password": PW, "is_active": "1"}
    if uid:
        data["id"] = str(uid)
    for key, vals, names in (("companies", companies, "company"), ("cfas", cfas, "cfa")):
        if vals is None:
            data[f"all_{key}"] = "1"
        else:
            data[key] = [str(ids[names][c]) for c in vals]
    if areas is None:
        data["all_areas"] = "1"
    else:
        data["areas"] = [str(ids["area"][a]) for a in areas]
    if types is None:
        data["all_customer_types"] = "1"
    else:
        data["customer_types"] = list(types)
    if customers is None:
        data["all_customers"] = "1"
    else:
        data["customers"] = [str(ids["customer"][c]) for c in customers]
    for p in grant:
        data[f"perm_{p}"] = "GRANT"
    for p in deny:
        data[f"perm_{p}"] = "DENY"
    if reason:
        data["override_reason"] = reason
    data.update(extra or {})
    r = admin.post("/admin/users/save", data=data)
    return r


def user_row(db, username) -> User | None:
    db.expire_all()
    return db.scalar(select(User).where(User.username == username))


def audit_actions(db, ref) -> set[str]:
    db.expire_all()
    return {a.action for a in db.scalars(select(AuditLog).where(AuditLog.ref == ref))}


@pytest.fixture(scope="module")
def admin():
    return WebClient("admin")


@pytest.fixture(scope="module")
def world(admin):
    s = _test_session()
    try:
        a = _token_client("admin")
        inv = {
            "a_in": make_invoice(a, company="COA", cfa="RPR", customer="C-MET01", location="RPR", boxes=4),
            "a_co": make_invoice(a, company="COB", cfa="RPR", customer="C-MET01", location="RPR", boxes=4),
            "a_dep": make_invoice(a, company="COA", cfa="BSP", customer="C-MET01", location="RPR", boxes=4),
            "a_area": make_invoice(a, company="COA", cfa="RPR", customer="C-MET01", location="BSP", boxes=4),
            "c_stock": make_invoice(a, company="COA", cfa="RPR", customer="C-VDP01", boxes=4),
            "c_metro": make_invoice(a, company="COA", cfa="RPR", customer="C-MET01", boxes=4),
            "c_ccc": make_invoice(a, company="COA", cfa="RPR", customer="C-CCC01", boxes=4),
        }
        who = {
            # Company A + Raipur + Guma (the owner's example)
            "p2.s": dict(role="CFA_MANAGER", companies=["COA"], cfas=["RPR"], areas=["RPR"]),
            # all companies at Guma; every Stockist, plus Metro (a Retailer) by name
            "p2.t": dict(role="CFA_MANAGER", cfas=["RPR"], types=["STOCKIST"], customers=["C-MET01"]),
            # Stockists only
            "p2.u": dict(role="CFA_MANAGER", cfas=["RPR"], types=["STOCKIST"]),
            # one customer only
            "p2.v": dict(role="CFA_MANAGER", cfas=["RPR"], customers=["C-CCC01"]),
        }
        for name, kw in who.items():
            kw = dict(kw)
            save_user(admin, s, name, kw.pop("role"), **{"companies": None, **kw})
            assert user_row(s, name), name
        yield inv
    finally:
        s.close()


def _can_open(c, inv) -> bool:
    return c.get(f"/invoices/{inv['id']}").status_code == 200


def _in_search(c, inv) -> bool:
    return f'/invoices/{inv["id"]}"' in c.get(f"/invoices?invoice_no={inv['invoice_no']}").text \
        or f'/dispatch/{inv["id"]}"' in c.get(f"/dispatch/pending?q={inv['invoice_no']}").text


def _in_register(c, inv) -> bool:
    return inv["invoice_no"] in c.get(f"/reports/invoice-register?{TODAY_RANGE}").text


def _in_export(c, inv) -> bool:
    r = c.get(f"/reports/invoice-register/export?fmt=csv&{TODAY_RANGE}")
    assert r.status_code == 200
    return inv["invoice_no"] in r.text


def _in_api(tok, inv) -> bool:
    got = tok.get(f"/api/v1/invoices/{inv['id']}").status_code == 200
    listed = tok.get(f"/api/v1/invoices?invoice_no={inv['invoice_no']}").json()["total"] == 1
    assert got == listed, inv["invoice_no"]
    return got


EXPECT = {
    "p2.s": {"a_in": 1, "a_co": 0, "a_dep": 0, "a_area": 0, "c_stock": 0, "c_metro": 1, "c_ccc": 1},
    "p2.t": {"a_in": 1, "a_co": 1, "a_dep": 0, "a_area": 1, "c_stock": 1, "c_metro": 1, "c_ccc": 0},
    "p2.u": {"a_in": 0, "a_co": 0, "a_dep": 0, "a_area": 0, "c_stock": 1, "c_metro": 0, "c_ccc": 0},
    "p2.v": {"a_in": 0, "a_co": 0, "a_dep": 0, "a_area": 0, "c_stock": 0, "c_metro": 0, "c_ccc": 1},
}


# ----------------------------------------------------------------------------- effective permissions
def test_effective_permissions_are_role_plus_grants_minus_denies():
    from app.models import UserPermission as UP
    role = Role(code="X", name="X", permissions=["invoice.view", "dispatch.view"])
    u = User(username="u", full_name="u", role=role, is_active=True, permission_overrides=[
        UP(permission="mis.view", effect="GRANT"), UP(permission="invoice.view", effect="DENY")])
    assert u.can("mis.view") and u.can("dispatch.view") and not u.can("invoice.view") and not u.can("admin.users")
    assert u.effective_permissions() == {"mis.view", "dispatch.view"}
    # the full-access role narrowed by one DENY
    adm = User(username="a", full_name="a", role=Role(code="A", name="A", permissions=["*"]), is_active=True,
               permission_overrides=[UP(permission="admin.backup", effect="DENY")])
    assert adm.effective_permissions() == set(PERMISSIONS) - {"admin.backup"}
    assert adm.effective_permission_list() == sorted(set(PERMISSIONS) - {"admin.backup"})
    # inactive users hold nothing
    u.is_active = False
    assert not u.can("dispatch.view")
    assert access.effective_for(["*"], set(), {"mis.view"}) == set(PERMISSIONS) - {"mis.view"}
    assert access.effective_for(["invoice.view"], {"mis.view"}, set()) == {"invoice.view", "mis.view"}


def test_grant_needs_a_reason_takes_effect_at_once_and_is_audited(admin, db):
    r = save_user(admin, db, "p2.grant", "VIEWER", grant=["mis.view"], cfas=[], companies=[])
    assert user_row(db, "p2.grant") is None and "Give a reason" in r.text  # refused, nothing created
    save_user(admin, db, "p2.grant", "VIEWER", grant=["mis.view"], reason="covers Accounts leave", cfas=[], companies=[])
    u = user_row(db, "p2.grant")
    assert [(o.permission, o.effect, o.reason) for o in u.permission_overrides] == [("mis.view", "GRANT", "covers Accounts leave")]
    assert {"USER_CREATED"} <= audit_actions(db, "p2.grant")
    c = WebClient("p2.grant", PW)
    assert c.get("/mis").status_code == 200  # the Viewer role alone would be refused
    assert WebClient("viewer").get("/mis").status_code == 403
    # taking it away needs no reason and is audited
    save_user(admin, db, "p2.grant", "VIEWER", uid=u.id, cfas=[], companies=[])
    assert user_row(db, "p2.grant").permission_overrides == []
    assert c.get("/mis").status_code == 403
    assert "USER_PERMISSIONS_CHANGED" in audit_actions(db, "p2.grant")
    a = db.scalar(select(AuditLog).where(AuditLog.action == "USER_PERMISSIONS_CHANGED", AuditLog.ref == "p2.grant")
                  .order_by(AuditLog.id.desc()))
    assert "GRANT mis.view" in (a.old_value or "") and (a.new_value or "") == "none"


def test_deny_wins_over_the_role_and_hides_the_screen(admin, db):
    save_user(admin, db, "p2.deny", "CFA_MANAGER", deny=["dispatch.operate"], reason="no gatepasses for now",
              cfas=["RPR"], companies=None)
    c = WebClient("p2.deny", PW)
    assert WebClient("cfa.raipur").get("/gatepass/new").status_code == 200
    assert c.get("/gatepass/new").status_code == 403
    assert c.post("/gatepass/new", data={"ids": ["1"]}).status_code == 403
    # the rest of the Depot Manager role still works
    assert c.get("/invoices").status_code == 200 and c.get("/dispatch/pending").status_code == 200


def test_changes_that_would_do_nothing_are_not_stored(admin, db):
    # GRANT of something the role already has, DENY of something it lacks
    save_user(admin, db, "p2.noop", "VIEWER", grant=["invoice.view"], deny=["mis.view"], reason="x", cfas=[], companies=[])
    assert user_row(db, "p2.noop").permission_overrides == []


def test_preview_shows_the_effective_access_and_saves_nothing(admin, db):
    ids = _ids(db)
    before = db.query(UserPermission).count()
    r = admin.post("/admin/users/preview", data={"role_id": str(ids["role"]["VIEWER"]), "perm_mis.view": "GRANT",
                                                 "perm_invoice.view": "DENY", "perm_dispatch.view": "GRANT"})
    d = r.json()
    assert "mis.view" in d["permissions"] and "invoice.view" not in d["permissions"]
    assert d["granted"] == ["mis.view"] and d["denied"] == ["invoice.view"]  # dispatch.view: the role has it already
    assert any(m["group"].startswith("Reports") for m in d["menus"]) and d["can_manage_access"] is False
    assert db.query(UserPermission).count() == before
    assert admin.post("/admin/users/preview", data={"role_id": "99999"}).status_code == 400
    assert WebClient("operator.raipur").post("/admin/users/preview", data={"role_id": "1"}).status_code == 403


# ----------------------------------------------------------------------------- scope matrix
@pytest.mark.parametrize("name", list(EXPECT))
def test_scope_matrix_pages_search_registers_exports_and_api(name, world):
    c, tok = WebClient(name, PW), _token_client(name, PW)
    for key, want in EXPECT[name].items():
        inv = world[key]
        assert _can_open(c, inv) == bool(want), (name, key, "invoice page")
        assert _in_search(c, inv) == bool(want), (name, key, "search")
        assert _in_register(c, inv) == bool(want), (name, key, "report")
        assert _in_export(c, inv) == bool(want), (name, key, "export")
        assert _in_api(tok, inv) == bool(want), (name, key, "api")
        if not want:
            for url in (f"/dispatch/{inv['id']}", f"/track/{inv['id']}"):
                assert c.get(url).status_code in (403, 404), (name, key, url)


def test_forged_posts_cannot_reach_what_is_outside_the_scope(world):
    s = WebClient("p2.s", PW)
    out = world["a_co"]  # another company
    assert out["invoice_no"] not in s.post("/dispatch/bulk/prepare", data={"ids": [str(out["id"])]}).text
    r = s.post("/gatepass/new", data={"ids": [str(out["id"])], "mode": "SELF", f"cases_{out['id']}": "4"})
    assert "not available" in r.text
    sess = _test_session()
    try:
        assert sess.scalar(select(Invoice.dispatch_slip_id).where(Invoice.id == out["id"])) is None
    finally:
        sess.close()


def test_dropdown_lists_only_show_the_assigned_areas_and_customers(world):
    s, u, v = (_token_client(n, PW) for n in ("p2.s", "p2.u", "p2.v"))
    assert {x["code"] for x in s.get("/api/v1/masters/locations").json()} == {"RPR"}
    assert {x["party_type"] for x in u.get("/api/v1/masters/customers").json()} == {"STOCKIST"}
    assert {x["code"] for x in v.get("/api/v1/masters/customers").json()} == {"C-CCC01"}
    t = _token_client("p2.t", PW)
    got = {x["code"]: x["party_type"] for x in t.get("/api/v1/masters/customers").json()}
    assert "C-MET01" in got and "C-CCC01" not in got and all(p == "STOCKIST" or c == "C-MET01" for c, p in got.items())
    # the admin still sees everything
    adm = _token_client("admin")
    assert len(adm.get("/api/v1/masters/locations").json()) > 5 and len(adm.get("/api/v1/masters/customers").json()) > 10
    # filter drop-downs on the registers follow the same list
    page = WebClient("p2.v", PW).get("/invoices").text
    assert "City Care Chemists" in page and "Vardaan Pharma" not in page


def test_api_me_reports_the_scope_and_effective_permissions(world):
    me = _token_client("p2.s", PW).get("/api/v1/me").json()
    assert me["areas"] and me["customers"] is None and me["customer_types"] is None and me["companies"] == ["COA"]
    assert "mis.view" not in me["effective_permissions"] and "invoice.view" in me["effective_permissions"]
    me = _token_client("p2.deny", PW).get("/api/v1/me").json()
    assert "dispatch.operate" in me["permissions"] and "dispatch.operate" not in me["effective_permissions"]


def test_gatepass_is_visible_only_when_every_invoice_on_it_is(admin, world):
    """Rule B6: a gatepass may carry several customers; a restricted user sees it only if all of them are theirs."""
    a = _token_client("admin")
    g1 = make_invoice(a, company="COA", cfa="RPR", customer="C-MET01", boxes=4)
    g2 = make_invoice(a, company="COA", cfa="RPR", customer="C-CCC01", boxes=4)
    r = admin.post("/gatepass/new", data={"ids": [str(g1["id"]), str(g2["id"])], "mode": "SELF",
                                          f"cases_{g1['id']}": "4", f"cases_{g2['id']}": "4", "pickup_mode": "REGULAR"})
    m = re.search(r"/gatepass/(\d+)$", str(r.url))
    assert m, r.text[:400]
    slip_id = m.group(1)
    sess = _test_session()
    try:
        no = sess.scalar(select(DispatchSlip.dispatch_no).where(DispatchSlip.id == int(slip_id)))
    finally:
        sess.close()
    seen = {n: (WebClient(n, PW).get(f"/gatepass/{slip_id}").status_code == 200,
                no in WebClient(n, PW).get("/gatepass").text)
            for n in ("p2.s", "p2.t", "p2.u", "p2.v")}
    assert seen["p2.s"] == (True, True)    # Company A + Raipur: both customers are in the area
    assert seen["p2.t"] == (False, False)  # Metro yes, City Care no
    assert seen["p2.u"] == (False, False)  # Stockists only
    assert seen["p2.v"] == (False, False)  # City Care yes, Metro no
    assert WebClient("admin").get(f"/gatepass/{slip_id}").status_code == 200
    # nor does the customer alert of that gatepass leak
    assert no not in WebClient("p2.t", PW).get("/notifications").text


def test_bills_are_hidden_from_users_restricted_by_area_or_customer(admin, db):
    save_user(admin, db, "p2.acc_open", "ACCOUNTS", companies=None, cfas=None)
    save_user(admin, db, "p2.acc_area", "ACCOUNTS", companies=None, cfas=None, areas=["RPR"])
    save_user(admin, db, "p2.acc_cust", "ACCOUNTS", companies=None, cfas=None, customers=["C-MET01"])
    save_user(admin, db, "p2.acc_co", "ACCOUNTS", companies=["COA"], cfas=None)  # company only: bills stay visible
    o = WebClient("p2.acc_open", PW)
    page = o.get("/recon").text
    bills = re.findall(r'href="/recon/(\d+)"', page)
    assert bills, "the demo data has transporter bills"
    for name in ("p2.acc_area", "p2.acc_cust"):
        c = WebClient(name, PW)
        assert not re.findall(r'href="/recon/(\d+)"', c.get("/recon").text), name
        assert c.get(f"/recon/{bills[0]}").status_code in (403, 404), name
    assert re.findall(r'href="/recon/(\d+)"', WebClient("p2.acc_co", PW).get("/recon").text) is not None


def test_import_outside_the_users_area_or_customer_is_refused(admin, db):
    save_user(admin, db, "p2.imp", "CFA_MANAGER", companies=["COA"], cfas=["RPR"], areas=["RPR"])
    c = _token_client("p2.imp", PW)
    ok = {"invoice_no": "P2-IMP-OK", "invoice_date": "2026-10-01", "company_code": "COA", "cfa_code": "RPR",
          "customer_code": "C-MET01", "sales_value": "1000", "boxes": 2, "location": "RPR"}
    bad = {**ok, "invoice_no": "P2-IMP-BAD", "location": "BSP"}
    r = c.post("/api/v1/invoices/ingest", json={"invoices": [ok, bad]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] == 1
    errs = [e for row in body["rows"] for e in (row.get("errors") or [])]
    assert any(e.get("code") == "SCOPE" for e in errs), body


# ----------------------------------------------------------------------------- guard rails
def test_scope_screen_all_wins_over_selections(admin, db):
    save_user(admin, db, "p2.allwins", "VIEWER", companies=None, cfas=None, extra={
        "all_areas": "1", "areas": [str(_ids(db)["area"]["RPR"])]})
    u = user_row(db, "p2.allwins")
    assert u.all_areas and not [r for r in u.scope_rows if r.dimension == "AREA"]


def test_a_user_with_nothing_selected_sees_nothing_not_everything(admin, db, world):
    save_user(admin, db, "p2.none", "CFA_MANAGER", companies=None, cfas=["RPR"], areas=[])
    c = WebClient("p2.none", PW)
    for inv in world.values():
        assert not _can_open(c, inv)
    assert _token_client("p2.none", PW).get("/api/v1/invoices").json()["total"] == 0


def test_old_style_posts_keep_scope_and_individual_changes(admin, db):
    """A script or an old form without the access section must not widen or wipe an existing user."""
    save_user(admin, db, "p2.keep", "CFA_MANAGER", cfas=["RPR"], areas=["RPR"], types=["STOCKIST"],
              customers=["C-MET01"], deny=["dispatch.operate"], reason="test")
    u = user_row(db, "p2.keep")
    ids = _ids(db)
    r = admin.post("/admin/users/save", data={"id": str(u.id), "username": "p2.keep", "full_name": "Renamed",
                                              "role_id": str(ids["role"]["CFA_MANAGER"]), "is_active": "1",
                                              "all_companies": "1", "cfas": [str(ids["cfa"]["RPR"])]})
    u = user_row(db, "p2.keep")
    assert u.full_name == "Renamed" and not u.all_areas and not u.all_customer_types and not u.all_customers
    assert {(r.dimension, r.ref_id, r.ref_code) for r in u.scope_rows} == {
        ("AREA", ids["area"]["RPR"], None), ("CUSTOMER_TYPE", None, "STOCKIST"), ("CUSTOMER", ids["customer"]["C-MET01"], None)}
    assert [(o.permission, o.effect) for o in u.permission_overrides] == [("dispatch.operate", "DENY")]
    # and a new user made that way is unrestricted by area / customer, like every user before v1.7
    admin.post("/admin/users/save", data={"username": "p2.oldnew", "full_name": "x", "role_id": str(ids["role"]["VIEWER"]),
                                          "password": PW, "is_active": "1", "all_companies": "1", "all_cfas": "1"})
    n = user_row(db, "p2.oldnew")
    assert n.all_areas and n.all_customer_types and n.all_customers and n.scope_is_open


def test_nobody_changes_their_own_access_but_can_edit_their_details(admin, db):
    me = user_row(db, "admin")
    ids = _ids(db)
    base = {"id": str(me.id), "username": "admin", "full_name": me.full_name, "role_id": str(ids["role"]["ADMIN"]),
            "is_active": "1", "all_companies": "1", "all_cfas": "1", "all_areas": "1", "all_customer_types": "1",
            "all_customers": "1", "access_form": "1", "mobile": "99999 12345"}
    admin.post("/admin/users/save", data=base)
    assert user_row(db, "admin").mobile == "99999 12345"  # allowed: same access, new detail
    r = admin.post("/admin/users/save", data={**base, "perm_admin.backup": "DENY", "override_reason": "oops"})
    assert "cannot change your own" in r.text
    assert user_row(db, "admin").permission_overrides == []
    r = admin.post("/admin/users/save", data={k: v for k, v in base.items() if k != "all_areas"})
    assert "cannot change your own" in r.text and user_row(db, "admin").all_areas
    admin.post("/admin/users/save", data={**base, "mobile": ""})  # leave it as it was


def test_nobody_hands_out_more_than_they_hold(admin, db):
    # a user manager who is individually given user administration, but is limited to Company A and holds no MIS
    save_user(admin, db, "p2.um", "VIEWER", grant=["admin.users"], reason="HR clerk", companies=["COA"], cfas=["RPR"])
    um = WebClient("p2.um", PW)
    ids = _ids(db)

    def attempt(name, **kw):
        return save_user(um, db, name, kw.pop("role", "VIEWER"), **{"companies": ["COA"], "cfas": ["RPR"], **kw})

    r = attempt("p2.by_um1", role="ADMIN")
    assert user_row(db, "p2.by_um1") is None and "permissions you do not hold" in r.text
    r = attempt("p2.by_um2", grant=["mis.view"], reason="x")
    assert user_row(db, "p2.by_um2") is None
    r = attempt("p2.by_um3", companies=None)
    assert user_row(db, "p2.by_um3") is None and "outside your own scope" in r.text
    r = attempt("p2.by_um4", cfas=["BSP"])
    assert user_row(db, "p2.by_um4") is None and "outside your own scope" in r.text
    r = attempt("p2.by_um5", areas=["RPR"], customers=None)  # um has all areas: narrowing is always allowed
    assert user_row(db, "p2.by_um5") is not None
    # and cannot touch the Administrator
    me = user_row(db, "admin")
    r = um.post("/admin/users/save", data={"id": str(me.id), "username": "admin", "full_name": "hijack",
                                           "role_id": str(ids["role"]["ADMIN"]), "is_active": "1", "all_companies": "1",
                                           "all_cfas": "1", "access_form": "1", "all_areas": "1",
                                           "all_customer_types": "1", "all_customers": "1"})
    assert user_row(db, "admin").full_name != "hijack"
    # roles: p2.um holds no admin.roles
    assert um.get("/admin/roles").status_code == 403


def test_the_last_administrator_cannot_be_removed(db):
    """Administrator = any active user holding admin.users AND admin.roles, whatever the role is called."""
    s = _test_session()
    try:
        holders = access.administrators(s)
        assert holders
        for u in holders:
            u.is_active = False
        with pytest.raises(BusinessError, match="At least one Administrator"):
            access.ensure_admin_remains(s)
    finally:
        s.rollback()
        s.close()
    s = _test_session()
    try:  # taking admin.roles away from every role that carries it counts too
        for u in access.administrators(s):
            u.permission_overrides.append(UserPermission(permission="admin.roles", effect="DENY"))
        with pytest.raises(BusinessError):
            access.ensure_admin_remains(s)
    finally:
        s.rollback()
        s.close()


def test_a_second_administrator_is_a_user_who_holds_both_permissions(admin, db):
    save_user(admin, db, "p2.adm2", "VIEWER", grant=["admin.users", "admin.roles"], reason="deputy",
              companies=None, cfas=None)
    s = _test_session()
    try:
        assert "p2.adm2" in {u.username for u in access.administrators(s)}
    finally:
        s.close()
    # the original Administrator can now be demoted by the deputy... who must hold everything the target holds
    c = WebClient("p2.adm2", PW)
    me = user_row(db, "admin")
    r = c.post("/admin/users/save", data={"id": str(me.id), "username": "admin", "full_name": me.full_name,
                                          "role_id": str(_ids(db)["role"]["VIEWER"]), "is_active": "1",
                                          "all_companies": "1", "all_cfas": "1"})
    assert user_row(db, "admin").role.code == "ADMIN"  # refused: the deputy does not hold every permission of the Administrator


# ----------------------------------------------------------------------------- roles screen
def test_roles_can_be_renamed_reset_and_copied(admin, db):
    ids = _ids(db)
    rid = ids["role"]["CHECKER"]
    before = list(db.scalar(select(Role.permissions).where(Role.id == rid)))
    # rename
    admin.post("/admin/roles/save", data=roles_matrix(db, {f"name_{rid}": "Approver"}))
    db.expire_all()
    assert db.get(Role, rid).name == "Approver" and "ROLE_RENAMED" in audit_actions(db, "CHECKER")
    # edit, then reset to the template
    admin.post("/admin/roles/save", data=roles_matrix(db, {f"name_{rid}": "Approver", f"p_{rid}_mis.view": "1",
                                                           f"p_{rid}_admin.backup": "1"}))
    db.expire_all()
    assert {"mis.view", "admin.backup"} <= set(db.get(Role, rid).permissions)  # nothing is locked
    assert "Reset" in admin.get("/admin/roles").text
    admin.post(f"/admin/roles/{rid}/reset")
    db.expire_all()
    assert sorted(db.get(Role, rid).permissions) == sorted(DEFAULT_ROLES["CHECKER"]["permissions"])
    assert "ROLE_RESET" in audit_actions(db, "CHECKER")
    admin.post("/admin/roles/save", data=roles_matrix(db, {f"name_{rid}": "Checker / Approver"}))
    db.expire_all()
    assert sorted(db.get(Role, rid).permissions) == sorted(before)
    # a custom role has no template to go back to
    admin.post("/admin/roles/save", data=roles_matrix(db, {"new_code": "P2_CUSTOM", "new_name": "P2 custom"}))
    cid = _ids(db)["role"]["P2_CUSTOM"]
    assert admin.post(f"/admin/roles/{cid}/reset").status_code == 404


def test_only_role_managers_see_the_roles_and_picker_screens(world):
    for name in ("operator.raipur", "viewer", "checker"):
        c = WebClient(name)
        assert c.get("/admin/roles").status_code == 403
        assert c.get("/admin/picker?kind=area").status_code == 403
        assert c.post(f"/admin/roles/1/reset").status_code == 403


def test_picker_searches_areas_and_customers(admin):
    areas = admin.get("/admin/picker?kind=area&q=rai").json()
    assert {a["label"].split(" (")[0] for a in areas} >= {"Raipur", "Raigarh"} and "Bilaspur" not in str(areas)
    cust = admin.get("/admin/picker?kind=customer&q=metro").json()
    assert cust and all("Metro" in c["label"] for c in cust)
    assert len(admin.get("/admin/picker?kind=customer").json()) <= 30


def test_user_screens_render_with_their_selections(admin, db, world):
    u = user_row(db, "p2.t")
    page = admin.get(f"/admin/users/{u.id}").text
    assert 'name="access_form"' in page and "Metro Pharma" in page and 'value="STOCKIST" checked' in page
    assert 'id="role-perms"' in page and "user_access.js" in page
    lst = admin.get("/admin/users").text
    assert "p2.t" in lst and "customer(s)" in lst
    assert admin.get("/admin/users/new").status_code == 200
    assert "Roles &amp; permissions" in admin.get("/admin/roles").text
    assert admin.get("/static/js/user_access.js").status_code == 200


def test_user_changes_are_audited_with_their_scope(admin, db):
    save_user(admin, db, "p2.aud", "CFA_MANAGER", cfas=["RPR"], areas=["RPR"])
    u = user_row(db, "p2.aud")
    save_user(admin, db, "p2.aud", "CFA_MANAGER", uid=u.id, cfas=["RPR"], areas=["RPR", "BSP"], customers=["C-MET01"])
    db.expire_all()
    rows = list(db.scalars(select(AuditLog).where(AuditLog.action == "USER_UPDATED", AuditLog.ref == "p2.aud")))
    assert {"areas", "customers"} <= {r.field for r in rows}  # one audit row per changed field
    areas = next(r for r in rows if r.field == "areas")
    assert areas.old_value != areas.new_value


def test_deactivating_or_changing_the_role_signs_the_user_out(admin, db):
    save_user(admin, db, "p2.out", "VIEWER", cfas=["RPR"], companies=None)
    c = WebClient("p2.out", PW)
    assert c.get("/invoices").status_code == 200
    u = user_row(db, "p2.out")
    save_user(admin, db, "p2.out", "CHECKER", uid=u.id, cfas=["RPR"], companies=None)
    r = c.get("/invoices", follow_redirects=False)
    assert r.status_code in (302, 303, 401)


# ----------------------------------------------------------------------------- upgrade
def test_migration_0010_keeps_every_user_unrestricted(tmp_path):
    """v1.6.1 -> v1.7: new tables exist, every existing user is 'all areas / types / customers', nothing else moves."""
    from tests.test_v12_fixes import ROOT, _count, _run_prepare
    import os
    db_path = tmp_path / "ctdms.db"
    assert _run_prepare(db_path).returncode == 0
    env = {**os.environ, "CTDMS_DATABASE_URL": f"sqlite:///{db_path}", "CTDMS_TEST_DATABASE_URL": ""}
    down = subprocess.run([sys.executable, "-m", "alembic", "downgrade", "0009"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=600)
    assert down.returncode == 0, down.stdout + down.stderr
    con = sqlite3.connect(db_path)
    assert {"user_permissions", "user_scope"}.isdisjoint({r[0] for r in con.execute("select name from sqlite_master")})
    assert "all_areas" not in [c[1] for c in con.execute("pragma table_info(users)")]
    con.close()
    users, invoices = _count(db_path, "users"), _count(db_path, "invoices")
    r = _run_prepare(db_path)
    assert r.returncode == 0, r.stdout + r.stderr
    con = sqlite3.connect(db_path)
    names = {x[0] for x in con.execute("select name from sqlite_master")}
    assert {"user_permissions", "user_scope"} <= names
    cols = [c[1] for c in con.execute("pragma table_info(users)")]
    assert {"all_areas", "all_customer_types", "all_customers", "responsibility"} <= set(cols)
    assert con.execute("select count(*) from users where all_areas = 1 and all_customer_types = 1 and all_customers = 1"
                       ).fetchone()[0] == users
    assert con.execute("select version_num from alembic_version").fetchone()[0] >= "0010"
    con.close()
    assert _count(db_path, "users") == users and _count(db_path, "invoices") == invoices


def test_a_practice_database_from_an_older_version_is_brought_up_to_date(tmp_path):
    """Guided Training keeps a practice copy per trainee. One made before this version lacks the new tables and columns;
    it is brought up to date (rows kept) before it is used, instead of failing with 'no such column'."""
    import app.models  # noqa: F401
    from app.db import Base, _make_engine
    from app.training import runtime
    eng = _make_engine("sqlite:///" + (tmp_path / "practice.db").as_posix())
    Base.metadata.create_all(eng)
    with eng.begin() as c:
        c.exec_driver_sql("insert into roles (code, name, permissions, is_system, created_at, updated_at) "
                          "values ('R', 'R', '[]', 0, '2026-01-01', '2026-01-01')")
        c.exec_driver_sql("insert into users (username, full_name, password_hash, role_id, is_active, all_companies, "
                          "all_cfas, failed_logins, must_change_password, created_at, updated_at) values "
                          "('keep', 'Keep', 'x', 1, 1, 0, 0, 0, 0, '2026-01-01', '2026-01-01')")
        for col in ("all_areas", "all_customer_types", "all_customers", "responsibility"):
            c.exec_driver_sql(f"alter table users drop column {col}")
        c.exec_driver_sql("drop table user_scope")
        c.exec_driver_sql("drop table user_permissions")
    runtime.sync_schema(eng)
    runtime.sync_schema(eng)  # nothing left to do the second time
    with eng.begin() as c:
        row = c.exec_driver_sql("select username, all_areas, all_customer_types, all_customers from users").one()
        assert tuple(row) == ("keep", 1, 1, 1)  # unrestricted, like before the upgrade
        assert c.exec_driver_sql("select count(*) from user_scope").scalar() == 0
    eng.dispose()


def test_reset_to_template_cannot_hand_out_what_the_deputy_does_not_hold(admin, db):
    """Self-elevation through a role: a deputy who manages roles but lacks a permission must not get it back by pressing
    Reset on their own role."""
    vid = _ids(db)["role"]["VIEWER"]
    try:
        admin.post("/admin/roles/save", data=roles_matrix(db, drop={f"p_{vid}_dispatch.view"}))  # the Viewer loses it
        db.expire_all()
        assert "dispatch.view" not in db.get(Role, vid).permissions
        save_user(admin, db, "p2.deputy", "VIEWER", grant=["admin.roles", "admin.users"], reason="deputy",
                  companies=None, cfas=None)
        dep = WebClient("p2.deputy", PW)
        r = dep.post(f"/admin/roles/{vid}/reset")
        db.expire_all()
        assert "dispatch.view" not in db.get(Role, vid).permissions and "do not hold yourself" in r.text
        admin.post(f"/admin/roles/{vid}/reset")  # the Administrator holds everything: allowed
        db.expire_all()
        assert sorted(db.get(Role, vid).permissions) == sorted(DEFAULT_ROLES["VIEWER"]["permissions"])
    finally:
        admin.post(f"/admin/roles/{vid}/reset")
