"""v1.5 - proof of delivery & tracking, e-way bill, e-invoice QR signature, e-mailed report subscriptions

* invoices: delivery status / date / received by / note / photo (POD), e-way bill number + validity,
  result of the e-invoice QR signature check
* report_subscriptions (new)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("invoices") as b:
        b.add_column(sa.Column("qr_signature", sa.String(12)))
        b.add_column(sa.Column("eway_bill_no", sa.String(20)))
        b.add_column(sa.Column("eway_valid_until", sa.Date()))
        b.add_column(sa.Column("delivery_status", sa.String(12)))
        b.add_column(sa.Column("delivered_on", sa.Date()))
        b.add_column(sa.Column("received_by", sa.String(120)))
        b.add_column(sa.Column("pod_note", sa.Text()))
        b.add_column(sa.Column("pod_file", sa.String(255)))
        b.add_column(sa.Column("pod_source", sa.String(10)))
        b.add_column(sa.Column("pod_at", sa.DateTime()))
        b.add_column(sa.Column("pod_by_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_invoices_pod_by")))
        b.create_index("ix_invoices_eway_bill_no", ["eway_bill_no"])
        b.create_index("ix_invoices_delivery_status", ["delivery_status"])

    op.create_table(
        "report_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("report_code", sa.String(40), nullable=False),
        sa.Column("frequency", sa.String(10), nullable=False),
        sa.Column("params", sa.JSON()),
        sa.Column("recipients", sa.Text(), nullable=False),
        sa.Column("fmt", sa.String(5), nullable=False, server_default="xlsx"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("next_run_at", sa.DateTime()),
        sa.Column("last_sent_at", sa.DateTime()),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_report_subscriptions_next_run_at", "report_subscriptions", ["next_run_at"])


def downgrade() -> None:
    op.drop_table("report_subscriptions")
    with op.batch_alter_table("invoices") as b:
        b.drop_index("ix_invoices_delivery_status")
        b.drop_index("ix_invoices_eway_bill_no")
        for c in ("pod_by_id", "pod_at", "pod_source", "pod_file", "pod_note", "received_by", "delivered_on",
                  "delivery_status", "eway_valid_until", "eway_bill_no", "qr_signature"):
            b.drop_column(c)
