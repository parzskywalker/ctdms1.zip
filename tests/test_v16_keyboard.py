"""v1.6 keyboard-first operation: every daily screen carries the same function keys, searchable choice boxes get
codes, and the shared keyboard script is on every page. The keyboard behaviour itself is checked in a real browser
by tests/e2e_keyboard_dispatch.py (keyboard only, no mouse)."""
from __future__ import annotations

import re
from pathlib import Path

from tests.helpers import make_invoice

KEYS_JS = Path(__file__).parents[1] / "app" / "static" / "js" / "keys.js"


def _keys(html: str) -> set[str]:
    return set(re.findall(r'data-key="([^"]+)"', html))


def test_keyboard_script_and_key_bar_on_every_page(web):
    w = web("admin")
    for url in ("/", "/gatepass", "/invoices", "/masters/customers", "/reports"):
        html = w.get(url).text
        assert "/static/js/keys.js" in html and 'id="keybar"' in html, url
    assert 'id="kb-reports"' in w.get("/").text  # F10 = reports
    r = w.get("/static/js/keys.js")
    assert r.status_code == 200 and "F10" in r.text


def test_one_meaning_per_key_and_browser_keys_left_alone():
    js = KEYS_JS.read_text(encoding="utf-8")
    block = js[js.index("const KEYS = ["):js.index("];", js.index("const KEYS = ["))]
    table = dict(re.findall(r"\['(F\d+|Esc)', '([^']+)'", block))
    assert table == {"F1": "Help", "F2": "New", "F3": "Go to", "F4": "Search", "F6": "Save", "F7": "Edit",
                     "F8": "Gatepass / Print", "F10": "Reports", "Esc": "Back"}
    # F5 (refresh), F11 (full screen) and F12 stay with the browser
    rx = re.search(r"/\^F\(\[1-46-9\]\|10\)\$/", js)
    assert rx, "function-key filter changed - F5 / F11 / F12 must not be captured"
    assert "danger: b.classList.contains('btn-danger')" in (KEYS_JS.parent / "app.js").read_text(encoding="utf-8")
    assert "button.btn-danger, input.btn-danger" in js  # red buttons without a data-confirm still ask first


def test_gatepass_screen_keys(web):
    w = web("admin")
    html = w.get("/gatepass/new").text
    assert 'data-key="F8"' in html and 'data-why="#gp-why"' in html  # F8 generates; says why when not ready
    # admin sees several depots and none is chosen yet: the cursor starts in Depot, codes are searchable
    assert re.search(r'<select name="cfa_id" id="gp-cfa" autofocus', html)
    assert 'data-code="RPR"' in html or 'data-code="' in html
    assert 'id="gp-who"' in html and 'data-grid="#gp-open"' in html and 'data-empty-focus="#gp-scan"' in html
    assert re.search(r'id="gp-scan"[^>]*data-key="F4"', html)  # F4 = the invoice / scan box
    # who carries the goods comes first, date / time after it (ERP-style Enter order)
    who = html[html.index('id="gp-who"'):]
    assert who.index('name="mode"') < who.index('name="transporter_id"') < who.index('name="dispatch_date"')
    chosen = w.get("/gatepass/new?cfa_id=1").text
    assert re.search(r'id="gp-scan"[^>]*autofocus', chosen)


def test_dispatch_entry_and_gatepass_detail_keys(web, api):
    inv = make_invoice(api("admin"), boxes=3)
    w = web("admin")
    html = w.get(f"/dispatch/{inv['id']}").text
    assert {"F8"} <= _keys(html) and "F9" not in _keys(html)  # make the gatepass; nobody has a complete / gate-out key
    assert "Pending Dispatch" in html.split('class="crumbs"')[1][:200]  # Esc goes back to Pending Dispatch
    lst = w.get("/gatepass").text
    assert "F2" in _keys(lst)


def test_masters_and_forms_have_save_and_back_keys(web):
    w = web("admin")
    lst = w.get("/masters/depots").text
    assert "F2" in _keys(lst)
    form = w.get("/masters/customers/new").text
    assert "data-main-form" in form and {"F6", "Esc"} <= _keys(form)
    assert re.search(r'<option value="\d+" data-code="[^"]+"', form)  # company / depot / area options carry codes
    for url in ("/admin/users/new", "/rates/new", "/admin/settings", "/invoices/new"):
        assert "F6" in _keys(w.get(url).text), url


def test_filter_choice_boxes_search_by_code_and_recognition_code(web, db):
    from sqlalchemy import select
    from app.models import CFA
    depot = db.scalar(select(CFA).order_by(CFA.id))
    depot.marking = depot.marking or "TSTM"
    db.commit()
    html = web("admin").get("/reports/invoice-freight").text
    m = re.search(r'<option value="%d" data-code="%s" data-k="([^"]*)"' % (depot.id, re.escape(depot.code)), html)
    assert m and depot.marking in m.group(1)


def test_login_enter_submits_from_password(web):
    from fastapi.testclient import TestClient
    from app.main import app
    html = TestClient(app).get("/login").text
    assert 'data-enter-last="submit"' in html and "autofocus" in html
