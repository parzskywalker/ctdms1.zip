"""v1.7 Phase 5 - gate verification: permission to let a gatepass leave with cases not scanned

Every role that can void a gatepass (dispatch.void_slip - depot managers and checkers) receives gate.exception, so the
people who could already take decisions at the gate keep that ability; each change is written to the audit log. The
Administrator can take it away on Administration > Roles. No table changes (the verification columns came with 0013).

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-29
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    roles = sa.table("roles", sa.column("id", sa.Integer), sa.column("code", sa.String), sa.column("permissions", sa.JSON))
    audit = sa.table("audit_logs", sa.column("ts", sa.DateTime), sa.column("username", sa.String),
                     sa.column("action", sa.String), sa.column("module", sa.String), sa.column("record_type", sa.String),
                     sa.column("record_id", sa.String), sa.column("ref", sa.String), sa.column("new_value", sa.Text),
                     sa.column("reason", sa.Text))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for rid, code, perms in list(bind.execute(sa.select(roles.c.id, roles.c.code, roles.c.permissions))):
        perms = list(perms or [])
        if "*" in perms or "dispatch.void_slip" not in perms or "gate.exception" in perms:
            continue
        bind.execute(roles.update().where(roles.c.id == rid).values(permissions=perms + ["gate.exception"]))
        bind.execute(audit.insert().values(
            ts=now, username="system", action="ROLE_PERMISSIONS_CHANGED", module="ADMIN", record_type="Role",
            record_id=str(rid), ref=code, new_value="added: gate.exception",
            reason="v1.7: letting a gatepass leave with unscanned cases needs its own permission; kept for roles that could void a gatepass"))


def downgrade() -> None:
    pass
