"""v1.7 Phase 3 - freight rate exceptions, freight breakdown, grouping records, two new permissions

* transporter_rates.cfa_id / company_id: explicit exceptions only (NULL = the general rate, as every existing row).
* dispatch_slip_lines.rate_id / calc_note / consignment_no: how a line's freight was found; the consignment it belongs
  to. Existing lines stay as they are (NULL) - frozen rates and amounts are never recomputed.
* dispatch_slips.cross_company / eligibility_override_reason: invoices of different companies grouped on purpose,
  and a transporter chosen although it does not serve the depot (both audited).
* roles: dispatch.group_cross_company is added to every role that can dispatch (dispatch.operate), and
  transporter.override_eligibility to every role that can approve overrides (override.approve), so nobody who could do
  the work before is blocked after the upgrade. Each change is written to the audit log; the Administrator can
  take either away on Administration > Roles.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-28
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GRANTS = (("dispatch.operate", "dispatch.group_cross_company"), ("override.approve", "transporter.override_eligibility"))


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def upgrade() -> None:
    with op.batch_alter_table("transporter_rates") as b:
        b.add_column(sa.Column("cfa_id", sa.Integer(), sa.ForeignKey("cfas.id", name="fk_trate_cfa")))
        b.add_column(sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", name="fk_trate_company")))
    with op.batch_alter_table("dispatch_slip_lines") as b:
        b.add_column(sa.Column("rate_id", sa.Integer(), sa.ForeignKey("transporter_rates.id", name="fk_slipline_rate")))
        b.add_column(sa.Column("calc_note", sa.JSON()))
        b.add_column(sa.Column("consignment_no", sa.Integer()))
    with op.batch_alter_table("dispatch_slips") as b:
        b.add_column(sa.Column("cross_company", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("eligibility_override_reason", sa.Text()))

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
            reason="v1.7: keeps what this role could already do (grouping across companies / choosing a transporter "
                   "outside its depots now needs an explicit permission)"))


def downgrade() -> None:
    with op.batch_alter_table("dispatch_slips") as b:
        b.drop_column("eligibility_override_reason")
        b.drop_column("cross_company")
    with op.batch_alter_table("dispatch_slip_lines") as b:
        b.drop_column("consignment_no")
        b.drop_column("calc_note")
        b.drop_column("rate_id")
    with op.batch_alter_table("transporter_rates") as b:
        b.drop_column("company_id")
        b.drop_column("cfa_id")
