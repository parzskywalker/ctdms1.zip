"""v1.4.1 - taxable amount of an invoice (kept apart from the invoice amount)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("invoices") as b:
        b.add_column(sa.Column("taxable_value", sa.Numeric(14, 2)))


def downgrade() -> None:
    with op.batch_alter_table("invoices") as b:
        b.drop_column("taxable_value")
