"""v1.8.4 - vehicle plans: the Depot Manager plans, a Dispatch Operator makes the gatepass from the plan

Only a new table (no change to existing tables).

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "load_plans",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("cfa_id", sa.Integer, sa.ForeignKey("cfas.id"), nullable=False, index=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="OPEN", index=True),
        sa.Column("invoice_ids", sa.JSON, nullable=False),
        sa.Column("fields", sa.JSON, nullable=False),
        sa.Column("planned_by_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("planned_at", sa.DateTime, nullable=False),
        sa.Column("slip_id", sa.Integer, sa.ForeignKey("dispatch_slips.id"), nullable=True),
        sa.Column("done_by_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("done_at", sa.DateTime, nullable=True),
        sa.Column("cancel_reason", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("load_plans")
