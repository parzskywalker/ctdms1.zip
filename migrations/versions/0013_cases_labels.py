"""v1.7 Phase 4 - case identity (one barcode per case), label print log, case control per depot

* dispatch_cases: one row per physical carton of a gatepass with its own 10-digit code (Code 128).
* case_scans: append-only record of every scan at the gate (used from Phase 5).
* label_print_log: every label printed / failed / reprinted.
* dispatch_slips.case_control: LEGACY for every existing gatepass (count-and-confirm as before) or REQUIRED.
* cfas.case_control_enabled: off for every depot - a depot is switched on when it is ready (Guma first).
* roles: case.print goes to every role that can dispatch, case.reprint to every role that can reprint a slip.

Nothing existing is changed; no historical gatepass gets cases.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-29
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GRANTS = (("dispatch.operate", "case.print"), ("dispatch.reprint", "case.reprint"))


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def upgrade() -> None:
    op.create_table(
        "dispatch_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_code", sa.String(12), nullable=False),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("slip_id", sa.Integer(), sa.ForeignKey("dispatch_slips.id"), nullable=False),
        sa.Column("line_id", sa.Integer(), sa.ForeignKey("dispatch_slip_lines.id")),
        sa.Column("allocation_id", sa.Integer()),
        sa.Column("seq_in_invoice", sa.Integer(), nullable=False),
        sa.Column("seq_in_consignment", sa.Integer(), nullable=False),
        sa.Column("consignment_total", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(14), nullable=False, server_default="LABEL_PENDING"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("voided_at", sa.DateTime()),
        sa.UniqueConstraint("case_code", name="uq_dispatch_case_code"),
        sa.UniqueConstraint("slip_id", "invoice_id", "seq_in_invoice", name="uq_dispatch_case_seq"),
    )
    op.create_index("ix_dispatch_cases_slip_status", "dispatch_cases", ["slip_id", "status"])
    op.create_index("ix_dispatch_cases_invoice_id", "dispatch_cases", ["invoice_id"])
    op.create_table(
        "case_scans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slip_id", sa.Integer(), sa.ForeignKey("dispatch_slips.id")),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("dispatch_cases.id")),
        sa.Column("scanned_code", sa.String(60), nullable=False),
        sa.Column("result", sa.String(20), nullable=False),
        sa.Column("scanned_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("scanned_at", sa.DateTime(), nullable=False),
        sa.Column("station", sa.String(40)),
    )
    op.create_index("ix_case_scans_slip_at", "case_scans", ["slip_id", "scanned_at"])
    op.create_table(
        "label_print_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slip_id", sa.Integer(), sa.ForeignKey("dispatch_slips.id"), nullable=False),
        sa.Column("batch_no", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("dispatch_cases.id"), nullable=False),
        sa.Column("is_reprint", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text()),
        sa.Column("output", sa.String(20)),
        sa.Column("status", sa.String(8), nullable=False, server_default="PRINTED"),
        sa.Column("printed_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("printed_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_label_print_log_slip_id", "label_print_log", ["slip_id"])
    with op.batch_alter_table("dispatch_slips") as b:
        b.add_column(sa.Column("case_control", sa.String(10), nullable=False, server_default="LEGACY"))
        b.add_column(sa.Column("verified_cases", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("gate_exception_reason", sa.Text()))
        b.add_column(sa.Column("gate_exception_by_id", sa.Integer(),
                               sa.ForeignKey("users.id", name="fk_slips_gate_exception_by")))
        b.add_column(sa.Column("gate_exception_at", sa.DateTime()))
    with op.batch_alter_table("cfas") as b:
        b.add_column(sa.Column("case_control_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))

    bind = op.get_bind()
    roles = sa.table("roles", sa.column("id", sa.Integer), sa.column("code", sa.String), sa.column("permissions", sa.JSON))
    audit = sa.table("audit_logs", sa.column("ts", sa.DateTime), sa.column("username", sa.String),
                     sa.column("action", sa.String), sa.column("module", sa.String), sa.column("record_type", sa.String),
                     sa.column("record_id", sa.String), sa.column("ref", sa.String), sa.column("new_value", sa.Text),
                     sa.column("reason", sa.Text))
    for rid, code, perms in list(bind.execute(sa.select(roles.c.id, roles.c.code, roles.c.permissions))):
        perms = list(perms or [])
        if "*" in perms:
            continue
        added = [new for need, new in GRANTS if need in perms and new not in perms]
        if not added:
            continue
        bind.execute(roles.update().where(roles.c.id == rid).values(permissions=perms + added))
        bind.execute(audit.insert().values(
            ts=_now(), username="system", action="ROLE_PERMISSIONS_CHANGED", module="ADMIN", record_type="Role",
            record_id=str(rid), ref=code, new_value="added: " + ", ".join(added),
            reason="v1.7: printing / reprinting case labels now has its own permission; kept for roles that could dispatch / reprint"))


def downgrade() -> None:
    with op.batch_alter_table("cfas") as b:
        b.drop_column("case_control_enabled")
    with op.batch_alter_table("dispatch_slips") as b:
        b.drop_column("gate_exception_at")
        b.drop_column("gate_exception_by_id")
        b.drop_column("gate_exception_reason")
        b.drop_column("verified_cases")
        b.drop_column("case_control")
    op.drop_index("ix_label_print_log_slip_id", table_name="label_print_log")
    op.drop_table("label_print_log")
    op.drop_index("ix_case_scans_slip_at", table_name="case_scans")
    op.drop_table("case_scans")
    op.drop_index("ix_dispatch_cases_invoice_id", table_name="dispatch_cases")
    op.drop_index("ix_dispatch_cases_slip_status", table_name="dispatch_cases")
    op.drop_table("dispatch_cases")
