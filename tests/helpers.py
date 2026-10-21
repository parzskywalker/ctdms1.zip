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
    """The full Roles & permissions form as the screen posts it: every role's ticked, non-fixed permissions (the form
    always carries all roles, so a partial post would clear the others). `extra` adds fields, `drop` removes some."""
    from app.models import Role
    from app.security.permissions import FIXED_BASELINE, PERMISSIONS
    data = {}
    for r in db.scalars(select(Role)):
        if "*" in (r.permissions or []):
            continue
        for p in r.permissions or []:
            if p in PERMISSIONS and p not in FIXED_BASELINE:
                data[f"p_{r.id}_{p}"] = "1"
    data.update(extra or {})
    for k in drop or ():
        data.pop(k, None)
    return data
