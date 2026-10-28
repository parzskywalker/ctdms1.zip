"""v1.7 Phase 6 - allocations per receiving transporter, Garage Pawati slips, LR books

* dispatch_allocations: the part of an invoice line carried to one receiving transporter (unit of freight, LR, Pawati).
  Gatepasses made before have none and are read as one implicit allocation.
* garage_pawatis (+ lines, print log): one handover slip per receiving transporter x customer x company on a local-auto trip,
  numbered per depot (sequences key PAWATI:<depot id>; the Administrator may set the starting number).
* lr_books / lr_numbers: LR (builty) numbers of the few transporters that give their own book, imported per book.
* roles: pawati.print / pawati.receive / lr_book.manage go to every role that can dispatch, pawati.cancel to every role that
  can void a gatepass (audited), so nobody who could work before is blocked.

Nothing existing is changed.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-29
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GRANTS = (("dispatch.operate", ["pawati.print", "pawati.receive", "lr_book.manage"]),
          ("dispatch.void_slip", ["pawati.cancel"]))


def upgrade() -> None:
    op.create_table(
        "dispatch_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slip_id", sa.Integer(), sa.ForeignKey("dispatch_slips.id"), nullable=False),
        sa.Column("line_id", sa.Integer(), sa.ForeignKey("dispatch_slip_lines.id"), nullable=False),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("receiving_transporter_id", sa.Integer(), sa.ForeignKey("transporters.id"), nullable=False),
        sa.Column("cases", sa.Integer(), nullable=False),
        sa.Column("rate_per_case", sa.Numeric(10, 2)),
        sa.Column("freight", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("lr_no", sa.String(40)),
        sa.Column("lr_date", sa.Date()),
        sa.Column("calc_note", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("line_id", "receiving_transporter_id", name="uq_alloc_line_receiver"),
    )
    op.create_index("ix_dispatch_allocations_slip_id", "dispatch_allocations", ["slip_id"])
    op.create_index("ix_dispatch_allocations_invoice_id", "dispatch_allocations", ["invoice_id"])
    op.create_table(
        "garage_pawatis",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cfa_id", sa.Integer(), sa.ForeignKey("cfas.id"), nullable=False),
        sa.Column("pawati_no", sa.Integer(), nullable=False),
        sa.Column("slip_id", sa.Integer(), sa.ForeignKey("dispatch_slips.id"), nullable=False),
        sa.Column("receiving_transporter_id", sa.Integer(), sa.ForeignKey("transporters.id"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id")),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="GENERATED"),
        sa.Column("print_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cartons", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bill_nos", sa.Text()),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("freight", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("snapshot", sa.JSON()),
        sa.Column("supersedes_id", sa.Integer(), sa.ForeignKey("garage_pawatis.id")),
        sa.Column("cancel_reason", sa.Text()),
        sa.Column("cancelled_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("cancelled_at", sa.DateTime()),
        sa.Column("received_at", sa.DateTime()),
        sa.Column("received_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("received_via", sa.String(8)),
        sa.Column("received_note", sa.Text()),
        sa.Column("generated_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("cfa_id", "pawati_no", name="uq_pawati_depot_no"),
    )
    op.create_index("ix_garage_pawatis_slip_id", "garage_pawatis", ["slip_id"])
    op.create_table(
        "garage_pawati_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pawati_id", sa.Integer(), sa.ForeignKey("garage_pawatis.id"), nullable=False),
        sa.Column("allocation_id", sa.Integer(), sa.ForeignKey("dispatch_allocations.id"), nullable=False),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("cases", sa.Integer(), nullable=False),
    )
    op.create_index("ix_garage_pawati_lines_pawati_id", "garage_pawati_lines", ["pawati_id"])
    op.create_table(
        "pawati_print_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pawati_id", sa.Integer(), sa.ForeignKey("garage_pawatis.id"), nullable=False),
        sa.Column("print_no", sa.Integer(), nullable=False),
        sa.Column("is_reprint", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text()),
        sa.Column("printed_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("printed_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_pawati_print_log_pawati_id", "pawati_print_log", ["pawati_id"])
    op.create_table(
        "lr_books",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transporter_id", sa.Integer(), sa.ForeignKey("transporters.id"), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("first_no", sa.String(40), nullable=False),
        sa.Column("last_no", sa.String(40), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("received_on", sa.Date()),
        sa.Column("status", sa.String(8), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_lr_books_transporter_id", "lr_books", ["transporter_id"])
    op.create_table(
        "lr_numbers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transporter_id", sa.Integer(), sa.ForeignKey("transporters.id"), nullable=False),
        sa.Column("lr_no", sa.String(40), nullable=False),
        sa.Column("book_id", sa.Integer(), sa.ForeignKey("lr_books.id"), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="AVAILABLE"),
        sa.Column("allocation_id", sa.Integer()),
        sa.Column("void_reason", sa.Text()),
        sa.Column("used_at", sa.DateTime()),
        sa.UniqueConstraint("transporter_id", "lr_no", name="uq_lr_number"),
    )
    op.create_index("ix_lr_numbers_book_status", "lr_numbers", ["book_id", "status"])

    bind = op.get_bind()
    roles = sa.table("roles", sa.column("id", sa.Integer), sa.column("code", sa.String), sa.column("permissions", sa.JSON))
    audit = sa.table("audit_logs", sa.column("ts", sa.DateTime), sa.column("username", sa.String),
                     sa.column("action", sa.String), sa.column("module", sa.String), sa.column("record_type", sa.String),
                     sa.column("record_id", sa.String), sa.column("ref", sa.String), sa.column("new_value", sa.Text),
                     sa.column("reason", sa.Text))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for rid, code, perms in list(bind.execute(sa.select(roles.c.id, roles.c.code, roles.c.permissions))):
        perms = list(perms or [])
        if "*" in perms:
            continue
        added = [p for need, new in GRANTS if need in perms for p in new if p not in perms]
        if not added:
            continue
        bind.execute(roles.update().where(roles.c.id == rid).values(permissions=perms + added))
        bind.execute(audit.insert().values(
            ts=now, username="system", action="ROLE_PERMISSIONS_CHANGED", module="ADMIN", record_type="Role",
            record_id=str(rid), ref=code, new_value="added: " + ", ".join(added),
            reason="v1.7: Garage Pawati and LR books have their own permissions; kept for the roles that dispatch / void gatepasses"))


def downgrade() -> None:
    for name in ("lr_numbers", "lr_books", "pawati_print_log", "garage_pawati_lines", "garage_pawatis", "dispatch_allocations"):
        op.drop_table(name)
