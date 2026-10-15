"""v1.4 - WhatsApp / SMS dispatch alerts to customers (notifications queue)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("event", sa.String(15), nullable=False),
        sa.Column("dedupe_key", sa.String(120), nullable=False),
        sa.Column("slip_id", sa.Integer(), sa.ForeignKey("dispatch_slips.id")),
        sa.Column("cfa_id", sa.Integer(), sa.ForeignKey("cfas.id")),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id")),
        sa.Column("invoice_ids", sa.JSON()),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("to_number", sa.String(20)),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime()),
        sa.Column("last_error", sa.Text()),
        sa.Column("provider", sa.String(20)),
        sa.Column("provider_ref", sa.String(120)),
        sa.Column("sent_at", sa.DateTime()),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")),
    )
    op.create_index("ix_notifications_dedupe_key", "notifications", ["dedupe_key"], unique=True)
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])
    op.create_index("ix_notifications_slip_id", "notifications", ["slip_id"])
    op.create_index("ix_notifications_cfa_id", "notifications", ["cfa_id"])
    op.create_index("ix_notifications_customer_id", "notifications", ["customer_id"])
    op.create_index("ix_notifications_status", "notifications", ["status"])
    op.create_index("ix_notify_status_next", "notifications", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_table("notifications")
