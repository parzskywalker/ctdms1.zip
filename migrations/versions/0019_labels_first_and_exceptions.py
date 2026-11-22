"""v1.8 - labels printed before any gatepass, dispatch exceptions log, gate signature photo

- dispatch_cases.slip_id and label_print_log.slip_id may be empty: cartons are labelled per invoice before a vehicle is
  planned (dispatch_cases.label_first marks them) and are tied to the gatepass when it is made.
- label_print_log.invoice_id: which invoice a label batch printed before any gatepass belongs to.
- dispatch_exceptions: one row per exception (box count, urgent trip, eligibility, gate) - the exceptions log.
- dispatch_slips.urgent_reason, signature_photo(_at/_by_id): urgent single trip; photo of the signed gatepass.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("dispatch_cases") as b:
        b.alter_column("slip_id", existing_type=sa.Integer, nullable=True)
        b.add_column(sa.Column("label_first", sa.Boolean, nullable=False, server_default="0"))
    with op.batch_alter_table("label_print_log") as b:
        b.alter_column("slip_id", existing_type=sa.Integer, nullable=True)
        b.add_column(sa.Column("invoice_id", sa.Integer, nullable=True))  # plain column: SQLite batch needs named FKs
    with op.batch_alter_table("dispatch_slips") as b:
        b.add_column(sa.Column("urgent_reason", sa.Text, nullable=True))
        b.add_column(sa.Column("signature_photo", sa.String(200), nullable=True))
        b.add_column(sa.Column("signature_photo_at", sa.DateTime, nullable=True))
        b.add_column(sa.Column("signature_photo_by_id", sa.Integer, nullable=True))
    op.add_column("users", sa.Column("totp_secret", sa.String(64), nullable=True))
    op.create_table(
        "dispatch_exceptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("invoice_id", sa.Integer, sa.ForeignKey("invoices.id"), nullable=True, index=True),
        sa.Column("slip_id", sa.Integer, sa.ForeignKey("dispatch_slips.id"), nullable=True, index=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    # the Depot Manager plans vehicles and confirms exceptions (new v1.8 permissions, same as the new-install baseline)
    bind = op.get_bind()
    roles = sa.table("roles", sa.column("id", sa.Integer), sa.column("code", sa.String), sa.column("permissions", sa.JSON))
    for rid, code, perms in list(bind.execute(sa.select(roles.c.id, roles.c.code, roles.c.permissions))):
        if code == "CFA_MANAGER":
            add = [p for p in ("load.plan", "exception.approve") if p not in (perms or [])]
            if add:
                bind.execute(roles.update().where(roles.c.id == rid).values(permissions=list(perms or []) + add))


def downgrade() -> None:
    op.drop_table("dispatch_exceptions")
    with op.batch_alter_table("users") as b:
        b.drop_column("totp_secret")
    with op.batch_alter_table("dispatch_slips") as b:
        for c in ("signature_photo_by_id", "signature_photo_at", "signature_photo", "urgent_reason"):
            b.drop_column(c)
    with op.batch_alter_table("label_print_log") as b:
        b.drop_column("invoice_id")
    with op.batch_alter_table("dispatch_cases") as b:
        b.drop_column("label_first")
