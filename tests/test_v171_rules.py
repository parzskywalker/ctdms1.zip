"""v1.7.1: a transporter trip goes by local auto only, a direct pickup is Regular only."""
from __future__ import annotations

from tests.conftest import WebClient
from tests.test_rbac import _token_client
from tests.test_v17_phase3 import TODAY, _slip_of, world  # noqa: F401
from tests.test_v17_phase4 import db  # noqa: F401
from tests.test_v17_phase6 import _auto, _inv, _local


def _post(c, inv, **fields):
    data = {"ids": [str(inv["id"])], "dispatch_date": TODAY.isoformat(), f"cases_{inv['id']}": "4", **fields}
    return c.post("/gatepass/new", data=data)


def test_a_transporter_trip_cannot_be_regular_and_a_direct_pickup_cannot_use_a_local_auto(db, world):  # noqa: F811
    api = _token_client("admin")
    admin = WebClient("admin")
    t = str(world["tr"].id)
    a = _inv(api, "P3-TOWN", 4)
    r = _post(admin, a, mode="TRANSPORTER", transporter_id=t, pickup_mode="REGULAR")
    assert _slip_of(r) is None and "by a local auto" in r.text
    r = _post(admin, a, mode="DIRECT", transporter_id=t, pickup_mode="OUTSIDE_AUTO", outside_auto_id=str(_auto(db)),
              auto_method="PER_TRIP", trip_charge="300")
    assert _slip_of(r) is None and "no local auto" in r.text
    # the two valid shapes
    ok = _post(admin, a, mode="DIRECT", transporter_id=t, pickup_mode="REGULAR")
    assert _slip_of(ok)
    b = _inv(api, "P3-TOWN", 4)
    assert _slip_of(_local(admin, [b], {0: t}, db))

