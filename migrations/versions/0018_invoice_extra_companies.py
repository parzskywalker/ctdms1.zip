"""v1.7.2 pilot - one invoice may carry stock of more than one company

New table invoice_extra_companies (invoice_id, company_id): the OTHER companies named on an invoice. invoices.company_id stays
the main company (scope, numbering, freight and the company-wise reports). Nothing else changes; existing invoices have none.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoice_extra_companies",
        sa.Column("invoice_id", sa.Integer, sa.ForeignKey("invoices.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("company_id", sa.Integer, sa.ForeignKey("companies.id"), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("invoice_extra_companies")
