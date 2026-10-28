"""v1.7 Phase 1: sidebar position script is served on every signed-in page; every report stays behind a scope helper
(so the Phase 2 area / customer scope only has to extend those helpers)."""
from __future__ import annotations

import inspect

from app.services import reports as R
from tests.conftest import WebClient

# the helpers every invoice / slip / bill query goes through (see docs/ITERATION_v1.7_DESIGN.md, section 2)
SCOPE_MARKERS = ("apply_invoice_scope", "apply_cfa_scope", "apply_strict_scope", "apply_scope_allow_null_cfa",
                 "apply_slip_scope", "apply_bill_scope", "apply_notification_scope",  # added in Phase 2
                 "_grouped(", "_bill_base(", "_consignment_rows(")
NOT_DATA_SCOPED = {"rpt_audit"}  # the audit log itself: Administrator only, not invoice data


def test_every_report_function_uses_a_scope_helper():
    fns = {n: f for n, f in inspect.getmembers(R, inspect.isfunction) if n.startswith("rpt_")}
    assert len(fns) >= 25
    unscoped = [n for n, f in fns.items() if n not in NOT_DATA_SCOPED
                and not any(m in inspect.getsource(f) for m in SCOPE_MARKERS)]
    assert not unscoped, f"report functions without a scope helper: {unscoped}"


def test_sidebar_keeps_its_scroll_position_script():
    w = WebClient("admin")
    html = w.get("/invoices").text
    assert "ctdms.sideTop" in html and "sessionStorage" in html
    assert html.index("</aside>") < html.index("ctdms.sideTop") < html.index('<main class="content">')  # before first paint
    # the sign-in page has no sidebar and clears a position left by another user in this tab
    from fastapi.testclient import TestClient
    from app.main import app
    page = TestClient(app).get("/login").text
    assert "removeItem('ctdms.sideTop')" in page and "class=\"side\"" not in page
