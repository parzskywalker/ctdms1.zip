"""v1.7.2 - case control is mandatory at every depot; gone out only by the gate staff's scan

* The permission dispatch.complete no longer exists (nobody has a "complete dispatch" / "gate-out" button any more). It is
  taken off every role and every individual user permission; each change is written to the audit log.
* cfas.case_control_enabled becomes true for every depot (the depot switch is gone; the column stays, nothing reads it).
* The setting dispatch.complete_requires is removed (the rule is fixed: the gatepass must be printed).
Gatepasses made before v1.7 that are still at the depot are given their case labels from the gatepass screen
("Make the case labels for this gatepass"). No other table changes.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-31
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GONE = "dispatch.complete"
REASON = "v1.7.2: gone out is recorded only by the gate staff's scan of every case and then the gatepass"


def _audit_table():
    return sa.table("audit_logs", sa.column("ts", sa.DateTime), sa.column("username", sa.String),
                    sa.column("action", sa.String), sa.column("module", sa.String), sa.column("record_type", sa.String),
                    sa.column("record_id", sa.String), sa.column("ref", sa.String), sa.column("old_value", sa.Text),
                    sa.column("new_value", sa.Text), sa.column("reason", sa.Text))


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    audit = _audit_table()
    roles = sa.table("roles", sa.column("id", sa.Integer), sa.column("code", sa.String), sa.column("permissions", sa.JSON))
    for rid, code, perms in list(bind.execute(sa.select(roles.c.id, roles.c.code, roles.c.permissions))):
        perms = list(perms or [])
        if GONE not in perms:
            continue
        bind.execute(roles.update().where(roles.c.id == rid).values(permissions=[p for p in perms if p != GONE]))
        bind.execute(audit.insert().values(
            ts=now, username="system", action="ROLE_PERMISSIONS_CHANGED", module="ADMIN", record_type="Role",
            record_id=str(rid), ref=code, old_value=GONE, new_value="removed: " + GONE, reason=REASON))

    users = sa.table("users", sa.column("id", sa.Integer), sa.column("username", sa.String))
    uperm = sa.table("user_permissions", sa.column("id", sa.Integer), sa.column("user_id", sa.Integer),
                     sa.column("permission", sa.String), sa.column("effect", sa.String))
    for pid, uid, effect in list(bind.execute(sa.select(uperm.c.id, uperm.c.user_id, uperm.c.effect).where(
            uperm.c.permission == GONE))):
        uname = bind.execute(sa.select(users.c.username).where(users.c.id == uid)).scalar()
        bind.execute(uperm.delete().where(uperm.c.id == pid))
        bind.execute(audit.insert().values(
            ts=now, username="system", action="USER_PERMISSION_REMOVED", module="ADMIN", record_type="User",
            record_id=str(uid), ref=uname, old_value=f"{effect} {GONE}", new_value="removed", reason=REASON))

    cfas = sa.table("cfas", sa.column("case_control_enabled", sa.Boolean))
    bind.execute(cfas.update().values(case_control_enabled=True))
    settings = sa.table("settings", sa.column("key", sa.String))
    bind.execute(settings.delete().where(settings.c.key == "dispatch.complete_requires"))


def downgrade() -> None:
    """Back to v1.7.1: the operators and depot managers get the permission back; the depot switch is off again."""
    bind = op.get_bind()
    roles = sa.table("roles", sa.column("id", sa.Integer), sa.column("code", sa.String), sa.column("permissions", sa.JSON))
    for rid, code, perms in list(bind.execute(sa.select(roles.c.id, roles.c.code, roles.c.permissions))):
        perms = list(perms or [])
        if code in ("CFA_MANAGER", "DISPATCH_OPERATOR") and GONE not in perms:
            bind.execute(roles.update().where(roles.c.id == rid).values(permissions=perms + [GONE]))
    cfas = sa.table("cfas", sa.column("case_control_enabled", sa.Boolean))
    bind.execute(cfas.update().values(case_control_enabled=False))
