"""v1.3 - gatepasses with many invoices, transporter rate cards, outside autos, SKU lines, scanned bills

* customers: party_type (stockist / retailer / hospital), WhatsApp number
* transporters: payment mode (cash / TBB), direct-pickup extra charge
* outside_autos, transporter_rates, invoice_items, dispatch_slip_lines (new)
* dispatch_slips: header of a multi-invoice gatepass (mode, transporter / auto, totals, payment, gate-out)
* invoices: gatepass link, mode, extra charge, LR date + who changed the LR, e-invoice IRN, capture mode
* transporter_bills / lines: scanned-bill data and the cross-check results

Existing one-invoice slips are converted into one-line gatepasses, so history, reprints and reports keep working.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-15
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("customers") as b:
        b.add_column(sa.Column("party_type", sa.String(12), nullable=False, server_default="STOCKIST"))
        b.add_column(sa.Column("whatsapp", sa.String(20), nullable=True))
        b.create_index("ix_customers_party_type", ["party_type"])

    with op.batch_alter_table("transporters") as b:
        b.add_column(sa.Column("payment_mode", sa.String(10), nullable=False, server_default="TBB"))
        b.add_column(sa.Column("direct_pickup_charge", sa.Numeric(10, 2), nullable=True))
        b.add_column(sa.Column("direct_pickup_basis", sa.String(10), nullable=False, server_default="PER_TRIP"))

    op.create_table(
        "outside_autos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("mobile", sa.String(20)),
        sa.Column("charge_basis", sa.String(10), nullable=False, server_default="PER_TRIP"),
        sa.Column("rate", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_outside_autos_code", "outside_autos", ["code"], unique=True)

    op.create_table(
        "transporter_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transporter_id", sa.Integer(), sa.ForeignKey("transporters.id"), nullable=False),
        sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("rate_per_case", sa.Numeric(10, 2), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source", sa.String(10), nullable=False, server_default="MANUAL"),
        sa.Column("source_bill_id", sa.Integer(), sa.ForeignKey("transporter_bills.id")),
        sa.Column("change_reason", sa.Text()),
        sa.Column("previous_rate_id", sa.Integer(), sa.ForeignKey("transporter_rates.id")),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("approved_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("approved_at", sa.DateTime()),
        sa.Column("rejected_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_trate_lookup", "transporter_rates", ["transporter_id", "location_id", "status"])
    op.create_index("ix_transporter_rates_status", "transporter_rates", ["status"])

    op.create_table(
        "invoice_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sku_code", sa.String(60)),
        sa.Column("sku_name", sa.String(200)),
        sa.Column("hsn", sa.String(12)),
        sa.Column("batch_no", sa.String(40)),
        sa.Column("qty", sa.Numeric(12, 3)),
        sa.Column("free_qty", sa.Numeric(12, 3)),
        sa.Column("value", sa.Numeric(14, 2)),
    )
    op.create_index("ix_invoice_items_invoice_id", "invoice_items", ["invoice_id"])
    op.create_index("ix_item_sku", "invoice_items", ["sku_code"])

    with op.batch_alter_table("invoices") as b:
        b.add_column(sa.Column("lr_date", sa.Date()))
        b.add_column(sa.Column("lr_updated_at", sa.DateTime()))
        b.add_column(sa.Column("lr_updated_by_id", sa.Integer(), sa.ForeignKey("users.id",
                                                                                name="fk_invoices_lr_updated_by")))
        b.add_column(sa.Column("dispatch_slip_id", sa.Integer()))
        b.add_column(sa.Column("dispatch_mode", sa.String(15)))
        b.add_column(sa.Column("outside_auto_id", sa.Integer(), sa.ForeignKey("outside_autos.id",
                                                                                name="fk_invoices_outside_auto")))
        b.add_column(sa.Column("extra_charge", sa.Numeric(12, 2)))
        b.add_column(sa.Column("capture_mode", sa.String(10), nullable=False, server_default="IMPORT"))
        b.add_column(sa.Column("irn", sa.String(70)))
        b.add_column(sa.Column("item_count", sa.Integer()))
        b.create_index("ix_invoices_dispatch_slip_id", ["dispatch_slip_id"])
        b.create_index("ix_invoices_irn", ["irn"])

    with op.batch_alter_table("dispatch_slips") as b:
        b.alter_column("invoice_id", existing_type=sa.Integer(), nullable=True)
        b.add_column(sa.Column("cfa_id", sa.Integer(), sa.ForeignKey("cfas.id", name="fk_slips_cfa")))
        b.add_column(sa.Column("dispatch_mode", sa.String(15), nullable=False, server_default="TRANSPORTER"))
        b.add_column(sa.Column("pickup_mode", sa.String(15), nullable=False, server_default="REGULAR"))
        b.add_column(sa.Column("transporter_id", sa.Integer(), sa.ForeignKey("transporters.id",
                                                                               name="fk_slips_transporter")))
        b.add_column(sa.Column("outside_auto_id", sa.Integer(), sa.ForeignKey("outside_autos.id",
                                                                                name="fk_slips_outside_auto")))
        b.add_column(sa.Column("dispatch_date", sa.Date()))
        b.add_column(sa.Column("invoice_count", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("total_cases", sa.Integer(), nullable=False, server_default="0"))
        for col in ("freight_total", "pickup_charge", "auto_charge", "total_charge", "amount_paid"):
            b.add_column(sa.Column(col, sa.Numeric(12, 2), nullable=False, server_default="0"))
        b.add_column(sa.Column("payment_status", sa.String(10), nullable=False, server_default="TBB"))
        b.add_column(sa.Column("remarks", sa.Text()))
        b.add_column(sa.Column("gate_out_at", sa.DateTime()))
        b.add_column(sa.Column("gate_out_by_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_slips_gate_out_by")))
        b.create_index("ix_dispatch_slips_cfa_id", ["cfa_id"])
        b.create_index("ix_dispatch_slips_transporter_id", ["transporter_id"])
        b.create_index("ix_dispatch_slips_dispatch_date", ["dispatch_date"])

    op.create_table(
        "dispatch_slip_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slip_id", sa.Integer(), sa.ForeignKey("dispatch_slips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id")),
        sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id")),
        sa.Column("cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rate_per_case", sa.Numeric(10, 2)),
        sa.Column("freight", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("extra_charge", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )
    op.create_index("ix_dispatch_slip_lines_slip_id", "dispatch_slip_lines", ["slip_id"])
    op.create_index("ix_dispatch_slip_lines_invoice_id", "dispatch_slip_lines", ["invoice_id"])

    with op.batch_alter_table("transporter_bills") as b:
        b.add_column(sa.Column("source", sa.String(10), nullable=False, server_default="MANUAL"))
        b.add_column(sa.Column("scan_files", sa.JSON()))
        b.add_column(sa.Column("extraction", sa.JSON()))
        b.add_column(sa.Column("total_cases", sa.Integer()))
        b.add_column(sa.Column("rates_reviewed_at", sa.DateTime()))
        b.add_column(sa.Column("rates_reviewed_by_id", sa.Integer(), sa.ForeignKey("users.id",
                                                                                     name="fk_bills_rates_reviewed_by")))
        b.add_column(sa.Column("rates_review_note", sa.Text()))

    with op.batch_alter_table("transporter_bill_lines") as b:
        b.add_column(sa.Column("lr_date", sa.Date()))
        b.add_column(sa.Column("party_name", sa.String(160)))
        b.add_column(sa.Column("station", sa.String(120)))
        b.add_column(sa.Column("billed_rate", sa.Numeric(10, 2)))
        b.add_column(sa.Column("invoice_ids", sa.JSON()))
        b.add_column(sa.Column("slip_id", sa.Integer(), sa.ForeignKey("dispatch_slips.id", name="fk_bill_lines_slip")))
        b.add_column(sa.Column("system_cases", sa.Integer()))
        b.add_column(sa.Column("card_rate", sa.Numeric(10, 2)))
        b.add_column(sa.Column("issues", sa.JSON()))
        b.add_column(sa.Column("suggestions", sa.JSON()))

    _convert_existing_slips()
    _settings()


def _settings() -> None:
    """LR and vehicle numbers are no longer needed to print a gatepass. Installations that already price invoices
    with the company rate card keep doing so until an administrator loads transporter rate cards and switches
    'freight.source' to TRANSPORTER (System Settings)."""
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE settings SET value = 'false' WHERE key IN ('dispatch.lr_mandatory', "
                         "'dispatch.vehicle_mandatory')"))
    has_company_rates = conn.execute(sa.text("SELECT COUNT(*) FROM freight_rates")).scalar() or 0
    exists = conn.execute(sa.text("SELECT COUNT(*) FROM settings WHERE key = 'freight.source'")).scalar() or 0
    if has_company_rates and not exists:
        conn.execute(sa.text(
            "INSERT INTO settings (key, value, value_type, category, description, choices, updated_at) VALUES "
            "('freight.source', 'COMPANY', 'choice', 'Freight rates', 'Where the freight per case comes from: "
            "TRANSPORTER = transporter rate card / COMPANY = principal rate card', 'TRANSPORTER,COMPANY', :now)"),
            {"now": datetime.now(timezone.utc).replace(tzinfo=None)})


def _convert_existing_slips() -> None:
    """Turn every v1.2 one-invoice slip into a one-line gatepass (portable SQL, SQLite and PostgreSQL)."""
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT s.id, s.invoice_id, s.is_void, i.cfa_id, i.transporter_id, i.dispatch_date, i.boxes, "
        "i.rate_per_box, i.freight_amount, i.customer_id, i.location_id, i.status, i.completed_at, "
        "i.completed_by_id, i.dispatch_no, s.dispatch_no AS slip_no "
        "FROM dispatch_slips s JOIN invoices i ON i.id = s.invoice_id")).mappings().all()
    for r in rows:
        freight = r["freight_amount"] or 0
        conn.execute(sa.text(
            "UPDATE dispatch_slips SET cfa_id = :cfa, transporter_id = :tr, dispatch_date = :dd, invoice_count = 1, "
            "total_cases = :cases, freight_total = :fr, total_charge = :fr, gate_out_at = :go, "
            "gate_out_by_id = :gob WHERE id = :id"),
            {"cfa": r["cfa_id"], "tr": r["transporter_id"], "dd": r["dispatch_date"], "cases": r["boxes"] or 0,
             "fr": freight, "id": r["id"],
             "go": r["completed_at"] if (r["status"] == "DISPATCHED" and not r["is_void"]) else None,
             "gob": r["completed_by_id"] if (r["status"] == "DISPATCHED" and not r["is_void"]) else None})
        conn.execute(sa.text(
            "INSERT INTO dispatch_slip_lines (slip_id, invoice_id, customer_id, location_id, cases, rate_per_case, "
            "freight, extra_charge) VALUES (:s, :i, :c, :l, :cases, :rate, :fr, 0)"),
            {"s": r["id"], "i": r["invoice_id"], "c": r["customer_id"], "l": r["location_id"],
             "cases": r["boxes"] or 0, "rate": r["rate_per_box"], "fr": freight})
        if not r["is_void"] and r["dispatch_no"] == r["slip_no"]:
            conn.execute(sa.text("UPDATE invoices SET dispatch_slip_id = :s WHERE id = :i"),
                         {"s": r["id"], "i": r["invoice_id"]})
    conn.execute(sa.text("UPDATE invoices SET dispatch_mode = 'TRANSPORTER' WHERE transporter_id IS NOT NULL "
                         "AND dispatch_no IS NOT NULL"))


def downgrade() -> None:
    with op.batch_alter_table("transporter_bill_lines") as b:
        for c in ("suggestions", "issues", "card_rate", "system_cases", "slip_id", "invoice_ids", "billed_rate",
                  "station", "party_name", "lr_date"):
            b.drop_column(c)
    with op.batch_alter_table("transporter_bills") as b:
        for c in ("rates_review_note", "rates_reviewed_by_id", "rates_reviewed_at", "total_cases", "extraction",
                  "scan_files", "source"):
            b.drop_column(c)
    op.drop_table("dispatch_slip_lines")
    with op.batch_alter_table("dispatch_slips") as b:
        b.drop_index("ix_dispatch_slips_dispatch_date")
        b.drop_index("ix_dispatch_slips_transporter_id")
        b.drop_index("ix_dispatch_slips_cfa_id")
        for c in ("gate_out_by_id", "gate_out_at", "remarks", "payment_status", "amount_paid", "total_charge",
                  "auto_charge", "pickup_charge", "freight_total", "total_cases", "invoice_count", "dispatch_date",
                  "outside_auto_id", "transporter_id", "pickup_mode", "dispatch_mode", "cfa_id"):
            b.drop_column(c)
    with op.batch_alter_table("invoices") as b:
        b.drop_index("ix_invoices_irn")
        b.drop_index("ix_invoices_dispatch_slip_id")
        for c in ("item_count", "irn", "capture_mode", "extra_charge", "outside_auto_id", "dispatch_mode",
                  "dispatch_slip_id", "lr_updated_by_id", "lr_updated_at", "lr_date"):
            b.drop_column(c)
    op.drop_table("invoice_items")
    op.drop_table("transporter_rates")
    op.drop_table("outside_autos")
    with op.batch_alter_table("transporters") as b:
        for c in ("direct_pickup_basis", "direct_pickup_charge", "payment_mode"):
            b.drop_column(c)
    with op.batch_alter_table("customers") as b:
        b.drop_index("ix_customers_party_type")
        b.drop_column("whatsapp")
        b.drop_column("party_type")
