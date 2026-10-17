"""v1.6 - Depots, customer types, Invoice Recognition Code, invoice markings, local auto charging, master upload

* cfas (shown as DEPOTS): locality, marking
* companies / customers: marking; customers: source ERP + source ERP customer code, wider customer type
* customer_types (new, seeded), invoice_markings (new), local_auto_rates (new), master_uploads (new)
* invoices: recognition_code, serial_no, recognition_note, marking_id (the ERP invoice number is untouched)
* outside_autos (shown as LOCAL AUTOS): allowed methods, suggested trip charge, default rate per box, vehicle no
* dispatch_slips: auto_method, trip_charge, vehicle_no
* role CFA_MANAGER is now called "Depot Manager"

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CUSTOMER_TYPES = [("RETAILER", "Retailer", "RET", 1), ("STOCKIST", "Stockist", "STK", 2),
                  ("DISTRIBUTOR", "Distributor", "DST", 3), ("HOSPITAL", "Hospital", "HSP", 4),
                  ("INSTITUTION", "Institution", "INS", 5), ("CHAIN_PHARMACY", "Chain Pharmacy", "CHN", 6),
                  ("OTHER", "Other", "OTH", 7)]


def _ts():
    return [sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp())]


def upgrade() -> None:
    with op.batch_alter_table("cfas") as b:
        b.add_column(sa.Column("locality", sa.String(120)))
        b.add_column(sa.Column("marking", sa.String(20)))
    with op.batch_alter_table("companies") as b:
        b.add_column(sa.Column("marking", sa.String(20)))
    with op.batch_alter_table("customers") as b:
        b.add_column(sa.Column("marking", sa.String(20)))
        b.add_column(sa.Column("source_erp_id", sa.Integer(),
                               sa.ForeignKey("erp_systems.id", name="fk_customers_source_erp")))
        b.add_column(sa.Column("source_erp_code", sa.String(80)))
        b.alter_column("party_type", existing_type=sa.String(12), type_=sa.String(30))

    op.create_table(
        "customer_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("marking", sa.String(20)),
        sa.Column("description", sa.String(200)),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_ts(),
    )
    op.create_index("ix_customer_types_code", "customer_types", ["code"], unique=True)
    ct = sa.table("customer_types", sa.column("code"), sa.column("name"), sa.column("marking"),
                  sa.column("sort_order"), sa.column("is_active"))
    op.bulk_insert(ct, [{"code": c, "name": n, "marking": m, "sort_order": o, "is_active": True}
                        for c, n, m, o in CUSTOMER_TYPES])

    op.create_table(
        "invoice_markings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("erp_system_id", sa.Integer(), sa.ForeignKey("erp_systems.id")),
        sa.Column("marking", sa.String(40), nullable=False),
        sa.Column("match_type", sa.String(10), nullable=False, server_default="PREFIX"),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("cfa_id", sa.Integer(), sa.ForeignKey("cfas.id")),
        sa.Column("customer_type", sa.String(30)),
        sa.Column("notes", sa.String(200)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_ts(),
    )
    op.create_index("ix_invoice_markings_code", "invoice_markings", ["code"], unique=True)

    with op.batch_alter_table("invoices") as b:
        b.add_column(sa.Column("recognition_code", sa.String(100)))
        b.add_column(sa.Column("serial_no", sa.String(30)))
        b.add_column(sa.Column("recognition_note", sa.String(255)))
        b.add_column(sa.Column("marking_id", sa.Integer(),
                               sa.ForeignKey("invoice_markings.id", name="fk_invoices_marking")))
        b.create_index("ix_invoices_recognition_code", ["recognition_code"])

    with op.batch_alter_table("outside_autos") as b:
        b.add_column(sa.Column("vehicle_no", sa.String(20)))
        b.add_column(sa.Column("allowed_methods", sa.String(10), nullable=False, server_default="PER_TRIP"))
        b.add_column(sa.Column("default_trip_charge", sa.Numeric(10, 2)))
        b.add_column(sa.Column("default_box_rate", sa.Numeric(10, 2)))
    op.execute("UPDATE outside_autos SET allowed_methods = charge_basis")
    op.execute("UPDATE outside_autos SET default_trip_charge = rate WHERE charge_basis = 'PER_TRIP'")
    op.execute("UPDATE outside_autos SET default_box_rate = rate WHERE charge_basis = 'PER_CASE'")

    op.create_table(
        "local_auto_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("outside_auto_id", sa.Integer(), sa.ForeignKey("outside_autos.id"), nullable=False),
        sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id")),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id")),
        sa.Column("cfa_id", sa.Integer(), sa.ForeignKey("cfas.id")),
        sa.Column("rate_per_box", sa.Numeric(10, 2), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date()),
        sa.Column("remarks", sa.String(200)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_ts(),
    )
    op.create_index("ix_local_auto_rates_code", "local_auto_rates", ["code"], unique=True)
    op.create_index("ix_local_auto_rates_outside_auto_id", "local_auto_rates", ["outside_auto_id"])

    with op.batch_alter_table("dispatch_slips") as b:
        b.add_column(sa.Column("auto_method", sa.String(10)))
        b.add_column(sa.Column("trip_charge", sa.Numeric(12, 2)))
        b.add_column(sa.Column("vehicle_no", sa.String(20)))
    # v1.5 slips: per-trip autos carried their fixed rate as the trip charge
    op.execute("UPDATE dispatch_slips SET auto_method = (SELECT charge_basis FROM outside_autos a "
               "WHERE a.id = dispatch_slips.outside_auto_id) WHERE outside_auto_id IS NOT NULL")
    op.execute("UPDATE dispatch_slips SET trip_charge = auto_charge WHERE auto_method = 'PER_TRIP' "
               "AND dispatch_mode <> 'OUTSIDE_AUTO'")
    op.execute("UPDATE dispatch_slips SET trip_charge = freight_total WHERE auto_method = 'PER_TRIP' "
               "AND dispatch_mode = 'OUTSIDE_AUTO'")

    op.create_table(
        "master_uploads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("file_name", sa.String(200), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="PREVIEW"),
        sa.Column("counts", sa.JSON()),
        sa.Column("rows", sa.JSON()),
        sa.Column("uploaded_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("committed_at", sa.DateTime()),
        sa.Column("committed_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("file_path", sa.String(255)),
    )
    op.create_index("ix_master_uploads_file_hash", "master_uploads", ["file_hash"])

    op.execute("UPDATE roles SET name = 'Depot Manager', description = 'Assigned depot data, imports, approvals "
               "and reports.' WHERE code = 'CFA_MANAGER'")


def downgrade() -> None:
    op.execute("UPDATE roles SET name = 'CFA Manager' WHERE code = 'CFA_MANAGER'")
    op.drop_table("master_uploads")
    with op.batch_alter_table("dispatch_slips") as b:
        b.drop_column("vehicle_no")
        b.drop_column("trip_charge")
        b.drop_column("auto_method")
    op.drop_table("local_auto_rates")
    with op.batch_alter_table("outside_autos") as b:
        b.drop_column("default_box_rate")
        b.drop_column("default_trip_charge")
        b.drop_column("allowed_methods")
        b.drop_column("vehicle_no")
    with op.batch_alter_table("invoices") as b:
        b.drop_index("ix_invoices_recognition_code")
        b.drop_column("marking_id")
        b.drop_column("recognition_note")
        b.drop_column("serial_no")
        b.drop_column("recognition_code")
    op.drop_table("invoice_markings")
    op.drop_table("customer_types")
    with op.batch_alter_table("customers") as b:
        b.drop_column("source_erp_code")
        b.drop_column("source_erp_id")
        b.drop_column("marking")
    with op.batch_alter_table("companies") as b:
        b.drop_column("marking")
    with op.batch_alter_table("cfas") as b:
        b.drop_column("marking")
        b.drop_column("locality")
