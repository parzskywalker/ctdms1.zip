"""v1.7 Phase 3 - customer delivery locations, depots a transporter serves, LR-book flag

* customer_delivery_locations: one row per place a customer receives goods (area = Location master). A customer
  with a single location needs no row: customers.location_id stays the default, so nothing changes for them.
* invoices.customer_location_id: which delivery location an invoice goes to (NULL = the single / default one).
* transporter_depots: the depots a transporter serves. NO rows = serves every depot, so nobody loses a transporter
  after the upgrade.
* transporters.uses_lr_book: this transporter gives us its own LR (builty) book (used from Phase 6; default false).

Nothing existing is changed or removed.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer_delivery_locations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("label", sa.String(80), nullable=False, server_default=""),
        sa.Column("address", sa.Text()),
        sa.Column("ship_to_code", sa.String(60)),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("customer_id", "location_id", "label", name="uq_customer_delivery_location"),
    )
    op.create_index("ix_customer_delivery_locations_customer_id", "customer_delivery_locations", ["customer_id"])
    op.create_index("ix_customer_delivery_locations_location_id", "customer_delivery_locations", ["location_id"])
    op.create_index("ix_customer_delivery_locations_ship_to_code", "customer_delivery_locations", ["ship_to_code"])
    op.create_table(
        "transporter_depots",
        sa.Column("transporter_id", sa.Integer(), sa.ForeignKey("transporters.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("cfa_id", sa.Integer(), sa.ForeignKey("cfas.id", ondelete="CASCADE"), primary_key=True),
    )
    with op.batch_alter_table("invoices") as b:
        b.add_column(sa.Column("customer_location_id", sa.Integer(),
                               sa.ForeignKey("customer_delivery_locations.id", name="fk_invoices_customer_location")))
    with op.batch_alter_table("transporters") as b:
        b.add_column(sa.Column("uses_lr_book", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("transporters") as b:
        b.drop_column("uses_lr_book")
    with op.batch_alter_table("invoices") as b:
        b.drop_column("customer_location_id")
    op.drop_table("transporter_depots")
    op.drop_index("ix_customer_delivery_locations_ship_to_code", table_name="customer_delivery_locations")
    op.drop_index("ix_customer_delivery_locations_location_id", table_name="customer_delivery_locations")
    op.drop_index("ix_customer_delivery_locations_customer_id", table_name="customer_delivery_locations")
    op.drop_table("customer_delivery_locations")
