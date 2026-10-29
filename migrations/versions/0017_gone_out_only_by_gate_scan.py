"""v1.7.2 - case control is mandatory; gone out only by the gate scan

* The permission dispatch.complete ("Complete dispatch") no longer exists: nothing is marked gone out by a button. It is
  removed from every role and from every individual GRANT / DENY (each role change is written to the audit log).
* cfas.case_control_enabled is set on for every depot (the switch itself is no longer used).
No table changes.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-30
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERM = "dispatch.complete"


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    roles = sa.table("roles", sa.column("id", sa.Integer), sa.column("code", sa.String), sa.column("permissions", sa.JSON))
    audit = sa.table("audit_logs", sa.column("ts", sa.DateTime), sa.column("username", sa.String),
                     sa.column("action", sa.String), sa.column("module", sa.String), sa.column("record_type", sa.String),
                     sa.column("record_id", sa.String), sa.column("ref", sa.String), sa.column("new_value", sa.Text),
                     sa.column("reason", sa.Text))
    for rid, code, perms in list(bind.execute(sa.select(roles.c.id, roles.c.code, roles.c.permissions))):
        perms = list(perms or [])
        if PERM not in perms:
            continue
        bind.execute(roles.update().where(roles.c.id == rid).values(permissions=[p for p in perms if p != PERM]))
        bind.execute(audit.insert().values(
            ts=now, username="system", action="ROLE_PERMISSIONS_CHANGED", module="ADMIN", record_type="Role",
            record_id=str(rid), ref=code, new_value="removed: " + PERM,
            reason="v1.7.2: nothing is marked gone out by a button any more - only the gate staff's scan of every case and "
                   "then the gatepass does it (permission gate.scan)"))
    up = sa.table("user_permissions", sa.column("permission", sa.String))
    bind.execute(up.delete().where(up.c.permission == PERM))
    cfas = sa.table("cfas", sa.column("case_control_enabled", sa.Boolean))
    bind.execute(cfas.update().values(case_control_enabled=True))


def downgrade() -> None:
    pass
