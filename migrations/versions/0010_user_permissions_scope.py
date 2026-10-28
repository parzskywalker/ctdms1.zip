"""v1.7 Phase 2 - configurable permissions and operational scope per employee

* user_permissions: individual GRANT / DENY of a permission for one user (on top of the role)
* user_scope: assigned delivery areas (Location), customer types and customers of a user
* users: all_areas / all_customer_types / all_customers (default TRUE = unrestricted, so nobody sees less after the
  upgrade), responsibility (a label such as "Guma dispatch desk")

Nothing existing is changed or removed. Roles keep the permissions migration 0009 left them with.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("all_areas", sa.Boolean(), nullable=False, server_default=sa.true()))
        b.add_column(sa.Column("all_customer_types", sa.Boolean(), nullable=False, server_default=sa.true()))
        b.add_column(sa.Column("all_customers", sa.Boolean(), nullable=False, server_default=sa.true()))
        b.add_column(sa.Column("responsibility", sa.String(120)))
    op.create_table(
        "user_permissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("permission", sa.String(60), nullable=False),
        sa.Column("effect", sa.String(5), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("granted_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "permission", name="uq_user_permission"),
    )
    op.create_index("ix_user_permissions_user_id", "user_permissions", ["user_id"])
    op.create_table(
        "user_scope",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dimension", sa.String(16), nullable=False),
        sa.Column("ref_id", sa.Integer()),
        sa.Column("ref_code", sa.String(40)),
        sa.UniqueConstraint("user_id", "dimension", "ref_id", "ref_code", name="uq_user_scope"),
    )
    op.create_index("ix_user_scope_user_id", "user_scope", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_scope_user_id", table_name="user_scope")
    op.drop_table("user_scope")
    op.drop_index("ix_user_permissions_user_id", table_name="user_permissions")
    op.drop_table("user_permissions")
    with op.batch_alter_table("users") as b:
        b.drop_column("responsibility")
        b.drop_column("all_customers")
        b.drop_column("all_customer_types")
        b.drop_column("all_areas")
