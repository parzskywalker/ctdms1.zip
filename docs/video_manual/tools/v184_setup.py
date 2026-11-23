"""Off-camera set-up for the v1.8.4 training videos (run with the project's .venv python, pointed at a SCRATCH
database by the caller). dm: 4 invoices labelled and ready to load. op: 2 labelled invoices in two vehicle plans
made by the Depot Manager; the other invoices still need labels."""
from __future__ import annotations

import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Invoice, InvoiceStatus, LoadPlan, OutsideAuto, User
from app.services import case_service as cs
from app.services import dispatch_service as ds
from app.services.scope import apply_invoice_scope

mode = sys.argv[1]
OPEN = [s for s in InvoiceStatus.PRE_SLIP if s != InvoiceStatus.CANCELLED]
with SessionLocal() as db:
    dm = db.scalar(select(User).where(User.username == "cfa.raipur"))
    invs = list(db.scalars(apply_invoice_scope(select(Invoice), dm).where(Invoice.status.in_(OPEN), Invoice.boxes > 0,
                                                                          Invoice.boxes < 40)
                           .order_by(Invoice.invoice_date, Invoice.id).limit(12)))
    n = 4 if mode == "dm" else 2
    for inv in invs[:n]:
        cs.label_invoice(db, dm, inv, inv.boxes)
        cs.record_invoice_print(db, dm, inv, result="PRINTED")
    if mode == "op":
        autos = {a.name.split()[0]: a for a in db.scalars(select(OutsideAuto).where(OutsideAuto.is_active.is_(True)))}
        for inv, (auto, charge) in zip(invs[:2], (("Kishore", "600"), ("Ramesh", "250"))):
            a = autos[auto]
            fields = {"mode": "OUTSIDE_AUTO", "outside_auto_id": str(a.id), "auto_method": "PER_TRIP",
                      "trip_charge": charge, "vehicle_no": a.vehicle_no or "", f"cases_{inv.id}": str(inv.boxes)}
            req = ds.GatepassRequest(mode="OUTSIDE_AUTO", outside_auto_id=a.id, auto_method="PER_TRIP",
                                     trip_charge=ds.parse_money(charge), cases={inv.id: str(inv.boxes)})
            _, _, problems = ds.plan_gatepass(db, dm, [inv], req)
            assert not problems, problems
            db.add(LoadPlan(cfa_id=inv.cfa_id, invoice_ids=[inv.id], fields=fields, planned_by_id=dm.id))
    db.commit()
    print("prepared", mode, [i.invoice_no for i in invs[:n]])
