"""v1.7.1 - gate staff: the people who scan the cases and then the gatepass

A case-control gatepass is marked gone out only when the gate staff scan every case and then the gatepass; nobody gets a
"gone out" button any more. This adds the permission gate.scan and a role "Dispatch / Gate Staff" that holds it (and
dispatch.view). Nobody else is given gate.scan automatically - the Administrator gives it to the right people on
Administration > Users / Roles (Administrators already hold every permission). No table changes.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-30
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERMS = ["dispatch.view", "gate.scan", "master.use"]


def upgrade() -> None:
    bind = op.get_bind()
    roles = sa.table("roles", sa.column("id", sa.Integer), sa.column("code", sa.String), sa.column("name", sa.String),
                     sa.column("description", sa.Text), sa.column("permissions", sa.JSON),
                     sa.column("is_system", sa.Boolean), sa.column("created_at", sa.DateTime),
                     sa.column("updated_at", sa.DateTime))
    if bind.execute(sa.select(roles.c.id).where(roles.c.code == "GATE_STAFF")).first():
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    bind.execute(roles.insert().values(
        code="GATE_STAFF", name="Dispatch / Gate Staff", is_system=True, permissions=PERMS, created_at=now, updated_at=now,
        description="The people at the gate: scan every case label and then the gatepass, which marks the trip gone out. "
                    "Nothing else - they do not make or print gatepasses."))
    audit = sa.table("audit_logs", sa.column("ts", sa.DateTime), sa.column("username", sa.String),
                     sa.column("action", sa.String), sa.column("module", sa.String), sa.column("record_type", sa.String),
                     sa.column("ref", sa.String), sa.column("new_value", sa.Text), sa.column("reason", sa.Text))
    bind.execute(audit.insert().values(
        ts=now, username="system", action="ROLE_CREATED", module="ADMIN", record_type="Role", ref="GATE_STAFF",
        new_value="permissions: " + ", ".join(PERMS),
        reason="v1.7.1: gone out for a case-control gatepass is recorded only by the gate staff's scan"))


def downgrade() -> None:
    bind = op.get_bind()
    roles = sa.table("roles", sa.column("id", sa.Integer), sa.column("code", sa.String))
    users = sa.table("users", sa.column("role_id", sa.Integer))
    rid = bind.execute(sa.select(roles.c.id).where(roles.c.code == "GATE_STAFF")).scalar()
    if rid and not bind.execute(sa.select(users.c.role_id).where(users.c.role_id == rid)).first():
        bind.execute(roles.delete().where(roles.c.id == rid))
