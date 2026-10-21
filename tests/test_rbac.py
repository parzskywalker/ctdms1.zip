"""Role-based access (v1.6.1): menus, direct URLs, APIs, exports, final reports, depot isolation, permission management.

Seeded users: admin / management / accounts / cfa.raipur (Depot Manager, Guma) / operator.raipur / operator.bilaspur /
checker / viewer. The rules under test:
  * Master Settings (and administration, audit log, freight-rate master): Administrator only (fixed baseline).
  * Final Reports / Management MIS (mis.view): Administrator, Accounts, Management only - pages, exports, print, APIs.
  * Using a master in a dropdown (master.use) is separate from managing it (master.view / master.edit).
  * Every list, search, API and export is limited to the user's own depots.
"""
from __future__ import annotations

import re

import pytest
from sqlalchemy import select, text

from app.models import AuditLog, CFA, Role, User
from app.security.permissions import DEFAULT_ROLES, FIXED_BASELINE
from app.services.reports import REPORTS
from tests.conftest import WebClient, _test_session
from tests.helpers import make_invoice, roles_matrix

ROLE_USERS = {"ADMIN": "admin", "MANAGEMENT": "management", "ACCOUNTS": "accounts", "CFA_MANAGER": "cfa.raipur",
              "DISPATCH_OPERATOR": "operator.raipur", "CHECKER": "checker", "VIEWER": "viewer"}
MIS_ROLES = {"ADMIN", "MANAGEMENT", "ACCOUNTS"}
NON_ADMIN = [r for r in ROLE_USERS if r != "ADMIN"]
NON_MIS = [r for r in ROLE_USERS if r not in MIS_ROLES]
FINAL = sorted(c for c, rd in REPORTS.items() if rd.final)
OPERATIONAL = sorted(c for c, rd in REPORTS.items() if rd.perm == "report.view")
PW = "Rbac@2026xy"

_clients: dict[str, WebClient] = {}


def client(role: str) -> WebClient:
    if role not in _clients:
        _clients[role] = WebClient(ROLE_USERS[role])
    return _clients[role]


def hrefs(html: str) -> list[str]:
    return re.findall(r'href="([^"#]+)"', html)


def sidebar(html: str):
    aside = re.search(r'<aside class="side">(.*?)</aside>', html, re.S)
    assert aside, "no sidebar"
    groups = re.findall(r'<div class="group">\s*<div>([^<]+)</div>(.*?)</div>', aside.group(1), re.S)
    return [(g, hrefs(body)) for g, body in groups]


def make_user(username, role_code, cfa_codes=None, all_cfas=False):
    s = _test_session()
    try:
        rid = s.scalar(select(Role.id).where(Role.code == role_code))
        cids = [s.scalar(select(CFA.id).where(CFA.code == c)) for c in (cfa_codes or [])]
    finally:
        s.close()
    data = {"username": username, "full_name": username, "role_id": str(rid), "password": PW, "is_active": "1",
            "all_companies": "1", "cfas": [str(c) for c in cids]}
    if all_cfas:
        data["all_cfas"] = "1"
    WebClient("admin").post("/admin/users/save", data=data)
    return WebClient(username, PW)


# ------------------------------------------------------------------------------- the baseline itself
@pytest.mark.parametrize("code", list(DEFAULT_ROLES))
def test_fixed_baseline_per_role(code):
    role = Role(code=code, permissions=list(DEFAULT_ROLES[code]["permissions"]))
    for perm, allowed in FIXED_BASELINE.items():
        assert role.has(perm) == (code in allowed), (code, perm)
    assert role.has("master.use")  # everybody can USE masters in dropdowns


def test_stored_permission_list_cannot_widen_the_fixed_baseline():
    # even a wildcard or an edited list cannot make another role an Administrator of Master Settings / MIS
    for code in ("VIEWER", "DISPATCH_OPERATOR", "CFA_MANAGER", "CHECKER", "MADE_BY_MASTER"):
        r = Role(code=code, permissions=["*"])
        for perm in FIXED_BASELINE:
            assert not r.has(perm) or code in FIXED_BASELINE[perm], (code, perm)
        r = Role(code=code, permissions=list(FIXED_BASELINE))
        assert not any(r.has(p) for p in FIXED_BASELINE if code not in FIXED_BASELINE[p])
    assert Role(code="ACCOUNTS", permissions=["mis.view"]).has("mis.view")
    assert not Role(code="ACCOUNTS", permissions=["*"]).has("master.view")


def test_baseline_role_permissions_match_the_brief():
    d = {c: set(r["permissions"]) for c, r in DEFAULT_ROLES.items()}
    op, dm, ch, vw = d["DISPATCH_OPERATOR"], d["CFA_MANAGER"], d["CHECKER"], d["VIEWER"]
    for perms in (op, dm, ch, vw, d["MANAGEMENT"], d["ACCOUNTS"]):
        assert not perms & {"master.view", "master.edit", "rate.view", "rate.create", "audit.view", "admin.users",
                            "admin.roles", "admin.settings", "admin.integrations", "admin.backup"}
    assert not (op | dm | ch | vw) & {"mis.view"}
    assert d["MANAGEMENT"] & {"mis.view"} and d["ACCOUNTS"] & {"mis.view"}
    assert not op & {"recon.view", "recon.pay", "override.approve", "rate.approve", "report.view", "data.export"}
    assert not dm & {"recon.view", "recon.approve", "recon.pay", "rate.create", "rate.approve"}
    assert not ch & {"report.view", "data.export", "dashboard.view", "mis.view"}
    assert vw == {"dashboard.view", "invoice.view", "dispatch.view", "master.use"}
    assert not d["MANAGEMENT"] & {"dispatch.operate", "recon.edit", "recon.approve", "recon.pay", "invoice.import"}
    assert {"recon.view", "recon.edit", "recon.approve", "recon.pay"} <= d["ACCOUNTS"]
    assert {"override.approve", "rate.approve", "recon.approve"} <= ch


# ------------------------------------------------------------------------------- menus
EXPECTED_GROUPS = {
    "ADMIN": ["Dashboard", "Dispatch desk", "Operations", "Approvals", "Accounts", "Master Settings", "Reports & MIS",
              "Administration", "Help"],
    "MANAGEMENT": ["Dashboard", "Dispatch Monitoring", "Operations", "Reports & MIS", "Help"],
    "ACCOUNTS": ["Dashboard", "Dispatch Monitoring", "Operations", "Approvals", "Accounts", "Reports & MIS", "Help"],
    "CFA_MANAGER": ["Dashboard", "Dispatch desk", "Operations", "Approvals", "Operational Reports", "Help"],
    "DISPATCH_OPERATOR": ["Dashboard", "Dispatch desk", "Operations", "Help"],
    "CHECKER": ["Dispatch Monitoring", "Operations", "Approvals", "Accounts", "Help"],
    "VIEWER": ["Dashboard", "Dispatch Monitoring", "Operations", "Help"],
}


@pytest.mark.parametrize("role", list(ROLE_USERS))
def test_menu_shows_only_what_the_role_needs(role):
    html = client(role).get("/invoices").text
    side = sidebar(html)
    assert [g.replace("&amp;", "&") for g, _ in side] == EXPECTED_GROUPS[role]
    assert all(links for _, links in side), "an empty menu group is shown"
    links = [h for _, ls in side for h in ls]
    if role != "ADMIN":
        assert not any(h.startswith(("/masters", "/admin", "/rates", "/rate-cards", "/reports/audit")) for h in links)
    if role not in MIS_ROLES:
        assert "/mis" not in links
        assert not any(h.startswith("/reports/") and h.split("/")[2] in FINAL for h in links), links


@pytest.mark.parametrize("role", list(ROLE_USERS))
def test_every_menu_entry_opens(role):
    """No inaccessible menu entry: each link in the sidebar is allowed for the role."""
    c = client(role)
    for _, links in sidebar(c.get("/invoices").text):
        for h in links:
            r = c.get(h)
            assert r.status_code == 200, (role, h, r.status_code)


def test_reports_shortcut_f10_only_for_roles_with_reports():
    for role in ROLE_USERS:
        html = client(role).get("/invoices").text
        has = 'id="kb-reports"' in html
        assert has == (role in MIS_ROLES or role == "CFA_MANAGER"), role


@pytest.mark.parametrize("role", ["DISPATCH_OPERATOR", "CFA_MANAGER", "CHECKER", "VIEWER", "MANAGEMENT", "ACCOUNTS"])
def test_no_inaccessible_buttons_or_links_on_working_screens(role, depots):
    """Links to Master Settings, administration, bills or final reports must not even be drawn for roles without them."""
    c = client(role)
    own = depots["RPR"]
    pages = ["/", "/dispatch/pending", "/dispatch/register", "/gatepass", "/invoices", "/exceptions", "/deliveries",
             "/track", f"/invoices/{own['id']}", f"/dispatch/{own['id']}", "/approvals", "/approvals/history",
             "/reports", "/recon"]
    banned = ["/masters", "/rates", "/rate-cards", "/admin"]
    if role not in MIS_ROLES:
        banned += ["/mis", "/reports/audit"] + [f"/reports/{c_}" for c_ in FINAL]
    if role not in ("ACCOUNTS", "CHECKER"):
        banned += ["/recon"]
    for url in pages:
        r = c.get(url)
        if r.status_code != 200:
            continue
        for h in hrefs(r.text):
            path = h.split("?")[0].rstrip("/")
            if role == "CHECKER" and re.fullmatch(r"/(rates|rate-cards)/\d+", path):
                continue  # approvers open the rate they verify (the lists stay closed)
            assert not any(path == b or path.startswith(b + "/") for b in banned), (role, url, h)


# ------------------------------------------------------------------------------- Master Settings: Administrator only
MASTER_GET = ["/masters/companies", "/masters/depots", "/masters/customers", "/masters/customer-types",
              "/masters/transporters", "/masters/locations", "/masters/autos", "/masters/local-auto-rates",
              "/masters/invoice-markings", "/masters/recognition", "/masters/erp-mappings", "/masters/upload",
              "/masters/upload/template.xlsx", "/masters/companies/new", "/masters/customers/new", "/rates",
              "/rates/finder", "/rates/new", "/rate-cards", "/rate-cards/template.csv", "/admin/users",
              "/admin/users/new", "/admin/roles", "/admin/settings", "/admin/integrations", "/admin/training",
              "/admin/export", "/reports/audit", "/reports/rate-changes"]
MASTER_POST = ["/masters/companies/save", "/masters/customers/save", "/masters/transporters/save",
               "/masters/locations/save", "/masters/customers/1/merge", "/masters/upload", "/masters/recognition",
               "/masters/recognition/regenerate", "/masters/erp-mappings", "/masters/erp-field-mappings/1", "/rates",
               "/rates/import", "/rates/1/revise", "/rate-cards/1/propose", "/rate-cards/1/upload", "/admin/users/save",
               "/admin/users/1/password", "/admin/roles/save", "/admin/settings", "/admin/integrations/new",
               "/admin/integrations/1/save", "/admin/integrations/1/sync", "/admin/training/switch", "/admin/training/1"]


@pytest.mark.parametrize("role", NON_ADMIN)
def test_master_settings_and_administration_are_denied_by_url(role):
    c = client(role)
    for url in MASTER_GET:
        r = c.get(url)
        assert r.status_code == 403, (role, url, r.status_code)
    for url in MASTER_POST:
        r = c.post(url, data={"x": "1"}, follow_redirects=False)
        assert r.status_code == 403, (role, url, r.status_code)


def test_administrator_still_opens_every_master_screen():
    c = client("ADMIN")
    for url in MASTER_GET:
        if url in ("/admin/export",):
            continue
        assert c.get(url).status_code == 200, url


@pytest.mark.parametrize("role", ["CHECKER"])
def test_checker_only_gets_the_rate_pages_needed_for_approval(role):
    c = client(role)
    assert c.get("/rates").status_code == 403 and c.get("/rate-cards").status_code == 403
    # the approval queue itself and the verification pages behind it
    assert c.get("/approvals").status_code == 200
    assert c.get("/approvals/history").status_code == 200


# ------------------------------------------------------------------------------- Final Reports / MIS
@pytest.mark.parametrize("role", NON_MIS)
def test_final_reports_denied_everywhere_for_other_roles(role):
    c = client(role)
    assert FINAL, "no final reports registered"
    for code in FINAL:
        for url in (f"/reports/{code}", f"/reports/{code}?print=1", f"/reports/{code}?date_from=2000-01-01",
                    f"/reports/{code}/export?fmt=xlsx", f"/reports/{code}/export?fmt=csv",
                    f"/reports/{code}/export?fmt=pdf"):
            r = c.get(url)
            assert r.status_code == 403, (role, url, r.status_code)
            assert "₹" not in r.text and "Freight" not in r.text.split("<main")[-1]
    for url in ("/mis", "/mis?date_from=2000-01-01", "/mis/data?level=company", "/mis/data?level=cfa&p_company=1"):
        assert c.get(url).status_code == 403, (role, url)


@pytest.mark.parametrize("role", sorted(MIS_ROLES))
def test_final_reports_open_and_export_for_admin_accounts_management(role):
    c = client(role)
    for code in FINAL:
        r = c.get(f"/reports/{code}?date_from=2000-01-01")
        assert r.status_code == 200, (role, code, r.status_code)
        if code == "audit":
            continue
        assert c.get(f"/reports/{code}/export?fmt=csv&date_from=2000-01-01").status_code == 200, (role, code)
    assert c.get("/reports/company-freight/export?fmt=xlsx").status_code == 200
    assert c.get("/reports/company-freight/export?fmt=pdf").status_code == 200
    assert c.get("/mis").status_code == 200
    assert c.get("/mis/data?level=company").status_code == 200


@pytest.mark.parametrize("role", ["MANAGEMENT", "ACCOUNTS"])
def test_audit_and_rate_master_reports_stay_administrator_only(role):
    c = client(role)
    assert c.get("/reports/audit").status_code == 403
    assert c.get("/reports/rate-changes").status_code == 403


def test_operational_registers_are_not_final_reports():
    assert OPERATIONAL and not set(OPERATIONAL) & set(FINAL)
    for code in ("dispatch-register", "gatepass-register", "delivery-register", "pending-dispatch",
                 "dispatch-exceptions", "invoice-register"):
        assert code in OPERATIONAL, code
    for code in ("company-freight", "cfa-freight", "customer-freight", "city-freight", "location-freight",
                 "transporter", "outside-auto", "freight-variance", "freight-vs-actual", "monthly-mis",
                 "invoice-freight", "customer-type", "transporter-area"):
        assert code in FINAL, code


@pytest.mark.parametrize("role", ["CFA_MANAGER", "MANAGEMENT", "ACCOUNTS", "ADMIN"])
def test_operational_registers_open_and_export_for_roles_with_report_view(role):
    c = client(role)
    for code in OPERATIONAL:
        assert c.get(f"/reports/{code}").status_code == 200, (role, code)
    assert c.get("/reports/dispatch-register/export?fmt=csv").status_code == 200


@pytest.mark.parametrize("role", ["DISPATCH_OPERATOR", "CHECKER", "VIEWER"])
def test_operational_report_pages_need_report_view(role):
    c = client(role)
    for code in OPERATIONAL:
        assert c.get(f"/reports/{code}").status_code == 403, (role, code)
        assert c.get(f"/reports/{code}/export?fmt=csv").status_code == 403, (role, code)
    # ... but the screens they use every day stay open
    assert c.get("/dispatch/register").status_code == 200 if role != "CHECKER" else True


def test_depot_manager_cannot_download_final_reports_but_can_export_the_invoice_register(depots):
    c = client("CFA_MANAGER")
    assert c.get("/reports/invoice-freight/export?fmt=csv").status_code == 403
    r = c.get("/reports/invoice-register/export?fmt=csv&date_from=2000-01-01")
    assert r.status_code == 200
    assert depots["RPR"]["invoice_no"] in r.text
    for other in ("BSP", "DRG", "KVR"):
        assert depots[other]["invoice_no"] not in r.text, other
    head = r.text.splitlines()[0].lower()
    assert "freight" not in head and "sales" not in head and "rate" not in head  # operational columns only
    page = c.get("/invoices").text
    assert "/reports/invoice-freight/export" not in page and "/reports/invoice-register/export" in page


def test_invoice_register_totals_hide_money_for_roles_without_mis(depots):
    page = client("DISPATCH_OPERATOR").get("/invoices").text
    foot = page.split("<tfoot>")[-1].split("</tfoot>")[0]
    assert "₹" not in foot
    assert "₹" in client("ACCOUNTS").get("/invoices").text.split("<tfoot>")[-1].split("</tfoot>")[0]


# ------------------------------------------------------------------------------- dashboards
@pytest.mark.parametrize("role,title", [("MANAGEMENT", "Management Dashboard"), ("ACCOUNTS", "Accounts Dashboard"),
                                        ("ADMIN", "Dashboard")])
def test_financial_dashboard_for_mis_roles(role, title):
    html = client(role).get("/").text
    assert f"<h1>{title}</h1>" in html
    assert "Calculated freight" in html and "Sales value" in html and "Company-wise expense" in html


@pytest.mark.parametrize("role,title", [("CFA_MANAGER", "Depot Operations Dashboard"),
                                        ("DISPATCH_OPERATOR", "Dispatch Desk"), ("VIEWER", "Operations Dashboard")])
def test_operations_dashboard_has_no_financial_figures(role, title):
    c = client(role)
    html = c.get("/").text
    assert f"<h1>{title}</h1>" in html
    for word in ("Calculated freight", "Sales value", "Variance", "Freight per box", "Company-wise", "₹"):
        assert word not in html, (role, word)
    assert "Ready to dispatch" in html and "Pending dispatch ageing" in html


def test_dashboard_api_gives_counts_only_to_roles_without_mis(api):
    def keys(o):
        out = set()
        if isinstance(o, dict):
            for k, v in o.items():
                out.add(k)
                out |= keys(v)
        elif isinstance(o, list):
            for v in o:
                out |= keys(v)
        return out
    ops = api("cfa.raipur").get("/api/v1/dashboard/summary")
    assert ops.status_code == 200
    k = keys(ops.json())
    assert not k & {"freight", "sales", "actual", "variance", "billed_calc", "freight_per_box", "freight_pct"}, k
    full = api("management").get("/api/v1/dashboard/summary").json()
    assert {"freight", "sales"} <= keys(full)
    assert api("checker").get("/api/v1/dashboard/summary").status_code == 403


def test_accounts_dashboard_shows_bill_counts():
    assert "Bills waiting for approval" in client("ACCOUNTS").get("/").text
    assert "Bills waiting for approval" not in client("MANAGEMENT").get("/").text


def test_home_screen_per_role():
    for role, expect in (("CHECKER", "/approvals"),):
        r = client(role).get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == expect, role


# ------------------------------------------------------------------------------- APIs
def test_reporting_api_follows_the_same_rules(api):
    for role in ("operator.raipur", "checker", "viewer"):
        a = api(role)
        assert a.get("/api/v1/reports").status_code == 403
        assert a.get("/api/v1/reports/company-freight").status_code == 403
        assert a.get("/api/v1/reports/dispatch-register").status_code == 403
    dm = api("cfa.raipur")
    codes = {r["code"] for r in dm.get("/api/v1/reports").json()}
    assert "dispatch-register" in codes and not codes & set(FINAL)
    assert dm.get("/api/v1/reports/company-freight").status_code == 403
    assert dm.get("/api/v1/reports/dispatch-register").status_code == 200
    for role in ("management", "accounts", "admin"):
        a = api(role)
        assert {"company-freight", "freight-variance"} <= {r["code"] for r in a.get("/api/v1/reports").json()}
        assert a.get("/api/v1/reports/company-freight").status_code == 200


def test_masters_api_separates_using_from_managing(api):
    op = api("operator.raipur").get("/api/v1/masters/transporters")
    assert op.status_code == 200 and op.json()
    row = op.json()[0]
    assert {"id", "code", "name"} <= set(row)
    assert not set(row) & {"gstin", "pan", "bank_details", "payment_terms", "mobile", "contact_person"}
    adm = api("admin").get("/api/v1/masters/transporters").json()[0]
    assert {"gstin", "pan", "bank_details"} <= set(adm)
    for entity in ("customers", "locations", "companies", "depots"):
        assert api("viewer").get(f"/api/v1/masters/{entity}").status_code == 200
    cust = api("operator.raipur").get("/api/v1/masters/customers").json()[0]
    assert not set(cust) & {"gstin", "drug_license_no", "billing_address", "contact_phone", "email"}
    assert api("operator.raipur").get("/api/v1/freight/quote?company_code=COA&location=RPR").status_code == 403


def test_dropdown_masters_still_reach_operational_screens():
    """The operator edits nothing in Master Settings, yet transporters / customers / locations fill the screens."""
    c = client("DISPATCH_OPERATOR")
    page = c.get("/gatepass/new").text
    assert "<option" in page
    pend = c.get("/dispatch/pending").text
    assert 'name="transporter_id"' in pend or "transporter" in pend.lower()


# ------------------------------------------------------------------------------- transporter-bill data
def test_billed_amounts_are_not_shown_to_dispatch_roles(depots, api, db):
    inv_id = depots["RPR"]["id"]
    s = _test_session()
    try:
        s.execute(text("update invoices set actual_freight = 777.77 where id = :i"), {"i": inv_id})
        s.commit()
        assert api("operator.raipur").get(f"/api/v1/invoices/{inv_id}").json()["actual_freight"] is None
        assert api("cfa.raipur").get(f"/api/v1/invoices/{inv_id}").json()["actual_freight"] is None
        assert api("accounts").get(f"/api/v1/invoices/{inv_id}").json()["actual_freight"] == "777.77"
        assert "Reconciliation" not in client("DISPATCH_OPERATOR").get(f"/invoices/{inv_id}").text
        assert "Reconciliation" in client("ACCOUNTS").get(f"/invoices/{inv_id}").text
    finally:
        s.execute(text("update invoices set actual_freight = NULL where id = :i"), {"i": inv_id})
        s.commit()
        s.close()


@pytest.mark.parametrize("role", ["DISPATCH_OPERATOR", "CFA_MANAGER", "VIEWER", "MANAGEMENT"])
def test_transporter_bills_closed_to_roles_without_recon(role):
    c = client(role)
    assert c.get("/recon").status_code == 403
    assert c.get("/recon/new").status_code == 403
    assert c.post("/recon/1/approve", data={"remarks": "x"}, follow_redirects=False).status_code == 403
    assert c.post("/recon/1/pay", data={"payment_ref": "x"}, follow_redirects=False).status_code == 403


# ------------------------------------------------------------------------------- approvals
def test_approval_screens_only_for_approvers():
    for role in ("CHECKER", "CFA_MANAGER", "ACCOUNTS", "ADMIN"):
        assert client(role).get("/approvals").status_code == 200, role
        assert client(role).get("/approvals/history").status_code == 200, role
    for role in ("DISPATCH_OPERATOR", "VIEWER", "MANAGEMENT"):
        assert client(role).get("/approvals").status_code == 403, role
        assert client(role).get("/approvals/history").status_code == 403, role
        assert client(role).post("/overrides/1/decide", data={"decision": "approve"},
                                 follow_redirects=False).status_code == 403
        assert client(role).post("/rates/1/decide", data={"decision": "approve"},
                                 follow_redirects=False).status_code == 403
        assert client(role).post("/rate-cards/approve-all", data={}, follow_redirects=False).status_code == 403


def test_approval_history_shows_only_what_the_role_may_approve():
    page = client("CFA_MANAGER").get("/approvals/history").text
    body = page.split("<tbody>")[-1]
    assert "Transporter bill" not in body and "Freight rate" not in body and "Transporter rate" not in body


def test_administrator_does_not_bypass_the_two_person_rule(web, db):
    w = web("admin")
    s = _test_session()
    try:
        co = s.scalar(select(text("id")).select_from(text("companies")).where(text("code = 'COA'")))
        loc = s.scalar(select(text("id")).select_from(text("locations")).where(text("code = 'KRB'")))
    finally:
        s.close()
    r = w.post("/rates", data={"company_id": str(co), "location_id": str(loc), "rate_per_box": "31.50",
                               "effective_from": "2031-04-01", "remarks": "rbac test"})
    m = re.search(r"/rates/(\d+)$", str(r.url))
    assert m, r.text[:300]
    rid = int(m.group(1))
    status = _test_session().scalar(text("select status from freight_rates where id = :i").bindparams(i=rid))
    if status != "PENDING_APPROVAL":
        pytest.skip("approval not required in this configuration")
    w.post(f"/rates/{rid}/decide", data={"decision": "approve", "remarks": "self"})
    assert _test_session().scalar(text("select status from freight_rates where id = :i").bindparams(i=rid)) \
        == "PENDING_APPROVAL"
    # a different approver (the checker) can approve it - the page for it opens for him
    assert client("CHECKER").get(f"/rates/{rid}").status_code == 200
    client("CHECKER").post(f"/rates/{rid}/decide", data={"decision": "approve", "remarks": "ok"})
    assert _test_session().scalar(text("select status from freight_rates where id = :i").bindparams(i=rid)) == "APPROVED"


# ------------------------------------------------------------------------------- depot isolation
def _token_client(username: str, password: str | None = None):
    from fastapi.testclient import TestClient
    from app.main import app
    password = password or ("Admin@123" if username == "admin" else "Demo@123")
    c = TestClient(app)
    r = c.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return c


@pytest.fixture(scope="module")
def depots():
    a = _token_client("admin")
    out = {}
    for cfa, comp in (("RPR", "COA"), ("BSP", "COA"), ("DRG", "COA"), ("KVR", "COB")):
        out[cfa] = make_invoice(a, company=comp, cfa=cfa, customer="C-VDP01", boxes=4)
    return out


def _sees(c, inv) -> bool:
    return f'/invoices/{inv["id"]}"' in c.get(f"/invoices?invoice_no={inv['invoice_no']}").text \
        or f'/dispatch/{inv["id"]}"' in c.get(f"/dispatch/pending?q={inv['invoice_no']}").text


@pytest.mark.parametrize("role,own", [("DISPATCH_OPERATOR", "RPR"), ("CFA_MANAGER", "RPR")])
def test_users_only_see_their_own_depot(role, own, depots, api):
    c = client(role)
    mine = depots[own]
    r = c.get(f"/dispatch/pending?q={mine['invoice_no']}")
    assert str(r.url).endswith(f"/dispatch/{mine['id']}"), r.url  # exact search finds it
    assert c.get(f"/invoices/{mine['id']}").status_code == 200
    token = api({"DISPATCH_OPERATOR": "operator.raipur", "CFA_MANAGER": "cfa.raipur"}[role])
    assert token.get(f"/api/v1/invoices?invoice_no={mine['invoice_no']}").json()["total"] == 1
    for cfa in ("BSP", "DRG", "KVR"):
        other = depots[cfa]
        assert c.get(f"/invoices/{other['id']}").status_code in (403, 404), (role, cfa)
        assert c.get(f"/dispatch/{other['id']}").status_code in (403, 404), (role, cfa)
        assert c.get(f"/track/{other['id']}").status_code in (403, 404), (role, cfa)
        assert not _sees(c, other), (role, cfa)
        assert token.get(f"/api/v1/invoices/{other['id']}").status_code == 404
        assert token.get(f"/api/v1/invoices?invoice_no={other['invoice_no']}").json()["total"] == 0
        assert token.get(f"/api/v1/dispatch/pending?invoice_no={other['invoice_no']}").json()["total"] == 0
        assert other["invoice_no"] not in c.get("/exceptions").text
        # a bulk / gatepass request that names another depot's invoice does nothing with it
        r = c.post("/dispatch/bulk/prepare", data={"ids": [str(other["id"])]})
        assert other["invoice_no"] not in r.text


def test_guma_depot_manager_does_not_see_kamal_vihar(depots):
    c = client("CFA_MANAGER")  # assigned to Guma (RPR) only
    for path in ("/dispatch/pending", "/dispatch/register", "/invoices", "/exceptions", "/gatepass", "/deliveries",
                 "/notifications"):
        text_ = c.get(path).text
        assert depots["KVR"]["invoice_no"] not in text_, path
    assert "Kamal Vihar" not in c.get("/").text.split('<main class="content">')[-1]
    page = c.get("/").text
    assert "Guma" in page
    # the depot filter offers only the assigned depot
    flt = c.get("/dispatch/pending").text
    assert "Kamal Vihar Depot" not in flt and "Bilaspur Depot" not in flt and "Durg Depot" not in flt
    assert "Guma Depot" in flt


def test_depot_filters_and_gatepass_screen_never_widen_the_scope(depots):
    kvr = _test_session().scalar(select(CFA.id).where(CFA.code == "KVR"))
    c = client("DISPATCH_OPERATOR")
    r = c.get(f"/dispatch/pending?cfa_id={kvr}")
    assert depots["KVR"]["invoice_no"] not in r.text
    r = c.get(f"/gatepass/new?cfa_id={kvr}")
    assert depots["KVR"]["invoice_no"] not in r.text


def test_user_with_several_depots_can_switch_between_them(depots, api):
    w = make_user("rbac.multi", "DISPATCH_OPERATOR", ["RPR", "BSP"])
    s = _test_session()
    try:
        ids = {c.code: c.id for c in s.scalars(select(CFA))}
    finally:
        s.close()

    def listed(inv):
        return w.get(f"/api/v1/invoices?invoice_no={inv['invoice_no']}").json()["total"] == 1

    assert 'id="depot-switch"' in w.get("/invoices").text
    assert listed(depots["RPR"]) and listed(depots["BSP"])
    assert not listed(depots["DRG"]) and not listed(depots["KVR"])
    w.post("/account/depot", data={"cfa_id": str(ids["RPR"])})
    assert listed(depots["RPR"]) and not listed(depots["BSP"])
    assert w.get(f"/invoices/{depots['BSP']['id']}").status_code in (403, 404)
    assert 'selected' in w.get("/invoices").text
    # a depot that is not assigned cannot be chosen, and nothing widens
    w.post("/account/depot", data={"cfa_id": str(ids["DRG"])})
    assert listed(depots["RPR"]) and not listed(depots["DRG"]) and not listed(depots["BSP"])
    w.post("/account/depot", data={"cfa_id": ""})
    assert listed(depots["RPR"]) and listed(depots["BSP"]) and not listed(depots["DRG"])
    # exports follow the switch too
    w.post("/account/depot", data={"cfa_id": str(ids["BSP"])})
    # (operator has no report.view; the Depot Manager export is covered by the single-depot test above)
    assert listed(depots["BSP"]) and not listed(depots["RPR"])


def test_depot_switch_is_not_offered_where_it_makes_no_sense():
    assert 'id="depot-switch"' not in client("DISPATCH_OPERATOR").get("/invoices").text
    assert 'id="depot-switch"' not in client("ADMIN").get("/invoices").text
    r = client("ADMIN").post("/account/depot", data={"cfa_id": "1"})
    assert r.status_code == 200


# ------------------------------------------------------------------------------- permission management
def test_roles_screen_locks_fixed_permissions_and_audits_changes(web, db):
    w = web("admin")
    rid = db.scalar(select(Role.id).where(Role.code == "VIEWER"))
    all_before = {r.code: sorted(r.permissions) for r in db.scalars(select(Role))}
    page = w.get("/admin/roles").text
    assert "Fixed baseline" in page and "What each role sees in the menu" in page
    assert f'name="p_{rid}_mis.view"' not in page and f'name="p_{rid}_master.view"' not in page
    assert f'name="p_{rid}_invoice.view"' in page
    # a forged form that tries to hand the Viewer Master Settings, user administration and the final reports
    w.post("/admin/roles/save", data=roles_matrix(db, {f"p_{rid}_mis.view": "1", f"p_{rid}_master.view": "1",
                                                       f"p_{rid}_admin.users": "1", f"p_{rid}_audit.view": "1"}))
    db.expire_all()
    assert {r.code: sorted(r.permissions) for r in db.scalars(select(Role))} == all_before  # nothing changed
    assert db.scalar(select(AuditLog).where(AuditLog.action == "ROLE_PERMISSION_REFUSED", AuditLog.ref == "VIEWER"))
    # an ordinary change is saved and audited
    w.post("/admin/roles/save", data=roles_matrix(db, {f"p_{rid}_report.view": "1"}))
    db.expire_all()
    assert "report.view" in db.scalar(select(Role.permissions).where(Role.id == rid))
    assert db.scalar(select(AuditLog).where(AuditLog.action == "ROLE_PERMISSIONS_CHANGED", AuditLog.ref == "VIEWER"))
    w.post("/admin/roles/save", data=roles_matrix(db, drop={f"p_{rid}_report.view"}))  # put it back
    db.expire_all()
    assert {r.code: sorted(r.permissions) for r in db.scalars(select(Role))} == all_before


def test_edited_database_permissions_do_not_open_master_settings_or_reports(web):
    """Even if the stored permission list of a role is edited (database, old role), the baseline still decides."""
    s = _test_session()
    try:
        old = s.scalar(select(Role.permissions).where(Role.code == "VIEWER"))
        s.execute(text("update roles set permissions = :p where code = 'VIEWER'"),
                  {"p": '["*", "master.view", "master.edit", "mis.view", "admin.users", "audit.view", "report.view", "data.export"]'})
        s.commit()
        v = web("viewer")  # fresh sign-in: the role is read on every request
        for url in ("/masters/companies", "/admin/users", "/admin/roles", "/rates", "/reports/audit", "/mis",
                    "/reports/company-freight", "/reports/company-freight/export?fmt=csv"):
            assert v.get(url).status_code == 403, url
        assert "Master Settings" not in v.get("/invoices").text and "Administration" not in v.get("/invoices").text
    finally:
        import json
        s.execute(text("update roles set permissions = :p where code = 'VIEWER'"), {"p": json.dumps(old)})
        s.commit()
        s.close()


def test_users_cannot_elevate_themselves(web, db):
    w = web("admin")
    me = db.scalar(select(User).where(User.username == "admin"))
    op_role = db.scalar(select(Role.id).where(Role.code == "DISPATCH_OPERATOR"))
    w.post("/admin/users/save", data={"id": str(me.id), "username": "admin", "full_name": me.full_name,
                                      "role_id": str(op_role), "is_active": "1", "all_companies": "1", "all_cfas": "1"})
    db.expire_all()
    assert db.get(User, me.id).role.code == "ADMIN"  # refused (cannot change own role)
    # non-administrators cannot reach user management at all
    for role in NON_ADMIN:
        assert client(role).post("/admin/users/save", data={"id": "1", "username": "x"}).status_code == 403


def test_a_role_made_by_the_master_can_never_carry_fixed_permissions(web, db):
    """A role made by the Master cannot carry admin.users / mis.view / master.* (fixed), so it can never reach user
    management, Master Settings or the final reports - whatever the form says."""
    w = web("admin")
    w.post("/admin/roles/save", data=roles_matrix(db, {"new_code": "HR_TEST", "new_name": "HR test", "new_desc": "x"}))
    rid = db.scalar(select(Role.id).where(Role.code == "HR_TEST"))
    w.post("/admin/roles/save", data=roles_matrix(db, {f"p_{rid}_admin.users": "1", f"p_{rid}_mis.view": "1",
                                                       f"p_{rid}_master.edit": "1", f"p_{rid}_invoice.view": "1"}))
    db.expire_all()
    perms = db.scalar(select(Role.permissions).where(Role.code == "HR_TEST"))
    assert perms == ["invoice.view"]
    w.post("/admin/users/save", data={"username": "rbac.hr", "full_name": "HR", "role_id": str(rid), "password": PW,
                                      "is_active": "1", "all_companies": "1", "all_cfas": "1"})
    hr = WebClient("rbac.hr", PW)
    for url in ("/admin/users", "/masters/companies", "/mis", "/reports/company-freight", "/admin/roles"):
        assert hr.get(url).status_code == 403, url


# ------------------------------------------------------------------------------- upgrade of an existing installation
OLD_ROLES = {  # the v1.6.0 baseline (too wide): what an installed database holds before migration 0009
    "MANAGEMENT": ["dashboard.view", "report.view", "data.export", "invoice.view", "dispatch.view", "rate.view",
                   "master.view", "recon.view", "audit.view"],
    "ACCOUNTS": ["dashboard.view", "report.view", "data.export", "invoice.view", "dispatch.view", "rate.view",
                 "master.view", "recon.view", "recon.edit", "recon.approve", "recon.pay"],
    "CFA_MANAGER": ["dashboard.view", "report.view", "data.export", "invoice.view", "invoice.import",
                    "invoice.recalculate", "dispatch.view", "dispatch.operate", "dispatch.verify", "dispatch.complete",
                    "dispatch.reprint", "dispatch.void_slip", "dispatch.cancel", "override.request", "override.approve",
                    "rate.view", "rate.create", "master.view", "recon.view", "recon.approve"],
    "DISPATCH_OPERATOR": ["dispatch.view", "dispatch.operate", "dispatch.verify", "dispatch.complete",
                          "dispatch.reprint", "override.request", "invoice.view"],
    "CHECKER": ["dashboard.view", "report.view", "data.export", "invoice.view", "invoice.recalculate", "dispatch.view",
                "dispatch.verify", "dispatch.void_slip", "dispatch.cancel", "override.request", "override.approve",
                "override.direct", "rate.view", "rate.approve", "master.view", "recon.view", "recon.approve",
                "audit.view"],
    "VIEWER": ["report.view"],
}


def test_upgrade_realigns_existing_roles_pauses_reports_and_keeps_everything_else(tmp_path):
    """Starting v1.6.1 on a v1.6.0 database (migration 0009): system roles get the new baseline, a role made by the
    Master loses only the fixed-baseline permissions, e-mailed final reports of users who lost them are paused, a Viewer
    leaves the Management training path, every role change is audited - and no business data changes."""
    import json
    import sqlite3

    from tests.test_v12_fixes import _count, _run_prepare

    db_path = tmp_path / "ctdms.db"
    assert _run_prepare(db_path).returncode == 0
    con = sqlite3.connect(db_path)
    for code, perms in OLD_ROLES.items():
        con.execute("update roles set permissions = ? where code = ?", (json.dumps(perms), code))
    con.execute("insert into roles (code, name, description, permissions, is_system, created_at, updated_at) values "
                "('OLD_CUSTOM', 'Old custom', 'made by the Master', ?, 0, '2026-09-01', '2026-09-01')",
                (json.dumps(["dashboard.view", "report.view", "data.export", "invoice.view", "recon.view", "master.view",
                             "audit.view", "rate.view", "rate.create", "admin.users"]),))
    uid = lambda name: con.execute("select id from users where username = ?", (name,)).fetchone()[0]  # noqa: E731
    for owner, report in (("viewer", "company-freight"), ("cfa.raipur", "dispatch-register"),
                          ("cfa.raipur", "cfa-freight"), ("management", "company-freight")):
        con.execute("insert into report_subscriptions (name, report_code, frequency, recipients, fmt, is_active, owner_id,"
                    " next_run_at, created_at) values (?, ?, 'DAILY', 'x@example.com', 'xlsx', 1, ?, '2027-01-01', '2026-09-01')",
                    (f"{owner}:{report}", report, uid(owner)))
    con.execute("insert into training_progress (user_id, path, required, status, current_step, lessons, assigned_at) "
                "values (?, 'MANAGEMENT', 0, 'IN_PROGRESS', 1, '{}', '2026-09-01')", (uid("viewer"),))
    con.execute("update alembic_version set version_num = '0008'")
    con.commit()
    invoices, users = _count(db_path, "invoices"), _count(db_path, "users")
    con.close()

    r = _run_prepare(db_path)
    assert r.returncode == 0, r.stdout + r.stderr
    con = sqlite3.connect(db_path)
    assert con.execute("select version_num from alembic_version").fetchone()[0] == "0009"
    perms = lambda code: set(json.loads(con.execute("select permissions from roles where code = ?", (code,)).fetchone()[0]))  # noqa: E731
    for code in OLD_ROLES:
        assert perms(code) == set(DEFAULT_ROLES[code]["permissions"]), code
    assert perms("ADMIN") == {"*"}
    assert perms("OLD_CUSTOM") == {"dashboard.view", "report.view", "data.export", "invoice.view", "recon.view",
                                   "master.use"}
    n = con.execute("select count(*) from audit_logs where action = 'ROLE_BASELINE_APPLIED'").fetchone()[0]
    assert n == len(OLD_ROLES) + 1
    active = {name: a for name, a in con.execute("select name, is_active from report_subscriptions")}
    assert active == {"viewer:company-freight": 0, "cfa.raipur:dispatch-register": 1, "cfa.raipur:cfa-freight": 0,
                      "management:company-freight": 1}
    assert con.execute("select path from training_progress where user_id = ?", (uid("viewer"),)).fetchone()[0] == "VIEWER"
    con.close()
    assert _count(db_path, "invoices") == invoices and _count(db_path, "users") == users
