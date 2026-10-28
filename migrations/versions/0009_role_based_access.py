"""v1.6.1 Role-based access - fixed baseline for Master Settings and Final Reports / MIS

* roles: the seven system roles get the new baseline (Master Settings: Administrator only; Final Reports / MIS:
  Administrator, Accounts, Management only; "use a master" = master.use is separate from "manage a master");
  roles made by the Master lose the fixed-baseline permissions and keep everything else (they get master.use where
  they work with invoices / dispatch / bills, so dropdowns keep working)
* every changed role is written to the audit log (ROLE_BASELINE_APPLIED) with the permissions before and after
* e-mailed report subscriptions whose owner may no longer open the report are paused
* Guided Training: a Viewer is no longer on the Management path (that path needs the final reports)

Nothing else is touched. Downgrade does not bring back the old (too wide) permissions.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-21
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# frozen copy of the v1.6.1 baseline (a migration must not change when app code changes later)
BASELINE = {
    "MANAGEMENT": ["dashboard.view", "mis.view", "report.view", "data.export", "invoice.view", "dispatch.view",
                   "master.use"],
    "ACCOUNTS": ["dashboard.view", "mis.view", "report.view", "data.export", "invoice.view", "dispatch.view",
                 "master.use", "recon.view", "recon.edit", "recon.approve", "recon.pay"],
    "CFA_MANAGER": ["dashboard.view", "report.view", "data.export", "invoice.view", "invoice.import",
                    "invoice.recalculate", "dispatch.view", "dispatch.operate", "dispatch.verify", "dispatch.complete",
                    "dispatch.reprint", "dispatch.void_slip", "dispatch.cancel", "override.request",
                    "override.approve", "master.use"],
    "DISPATCH_OPERATOR": ["dashboard.view", "dispatch.view", "dispatch.operate", "dispatch.verify",
                          "dispatch.complete", "dispatch.reprint", "override.request", "invoice.view", "master.use"],
    "CHECKER": ["invoice.view", "invoice.recalculate", "dispatch.view", "dispatch.verify", "dispatch.void_slip",
                "dispatch.cancel", "override.request", "override.approve", "override.direct", "rate.approve",
                "recon.view", "recon.approve", "master.use"],
    "VIEWER": ["dashboard.view", "invoice.view", "dispatch.view", "master.use"],
}
ADMIN_ONLY = {"master.view", "master.edit", "rate.view", "rate.create", "audit.view", "admin.users", "admin.roles",
              "admin.settings", "admin.integrations", "admin.backup"}
MIS_ROLES = {"ADMIN", "MANAGEMENT", "ACCOUNTS"}
WORKS_WITH_DATA = {"invoice.view", "invoice.import", "dispatch.view", "dispatch.operate", "recon.view", "recon.edit",
                   "report.view"}
# reports a role without Final Reports / MIS may still be subscribed to (operational registers)
OPERATIONAL_REPORTS = {"gatepass-register", "delivery-register", "dispatch-register", "pending-dispatch",
                       "dispatch-exceptions", "rate-missing", "overrides", "reprints", "cancelled", "invoice-register"}
ADMIN_REPORTS = {"audit", "rate-changes"}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def upgrade() -> None:
    bind = op.get_bind()
    roles = sa.table("roles", sa.column("id", sa.Integer), sa.column("code", sa.String),
                     sa.column("description", sa.Text), sa.column("permissions", sa.JSON))
    audit = sa.table("audit_logs", sa.column("ts", sa.DateTime), sa.column("username", sa.String),
                     sa.column("action", sa.String), sa.column("module", sa.String), sa.column("record_type", sa.String),
                     sa.column("record_id", sa.String), sa.column("ref", sa.String), sa.column("old_value", sa.Text),
                     sa.column("new_value", sa.Text), sa.column("reason", sa.Text), sa.column("details", sa.JSON))

    for rid, code, old in list(bind.execute(sa.select(roles.c.id, roles.c.code, roles.c.permissions))):
        old = list(old or [])
        if code == "ADMIN" or "*" in old:
            continue
        if code in BASELINE:
            new = list(BASELINE[code])
        else:  # a role made by the Master: drop the fixed-baseline permissions, keep the rest
            new = [p for p in old if p not in ADMIN_ONLY and p != "mis.view"]
            if "master.use" not in new and any(p in old for p in WORKS_WITH_DATA):
                new.append("master.use")
        if sorted(new) == sorted(old):
            continue
        bind.execute(roles.update().where(roles.c.id == rid).values(permissions=new))
        removed, added = sorted(set(old) - set(new)), sorted(set(new) - set(old))
        bind.execute(audit.insert().values(
            ts=_now(), username="system", action="ROLE_BASELINE_APPLIED", module="ADMIN", record_type="Role",
            record_id=str(rid), ref=code, old_value="removed: " + ", ".join(removed) if removed else None,
            new_value="added: " + ", ".join(added) if added else None,
            reason="v1.6.1 role-based access: Master Settings Administrator-only; Final Reports / MIS for "
                   "Administrator, Accounts and Management only",
            details={"before": sorted(old), "after": sorted(new)}))

    # e-mailed reports: pause what the owner may no longer open (they would otherwise fail silently every day)
    subs, users = sa.table("report_subscriptions", sa.column("id", sa.Integer), sa.column("owner_id", sa.Integer),
                           sa.column("report_code", sa.String), sa.column("is_active", sa.Boolean)), \
        sa.table("users", sa.column("id", sa.Integer), sa.column("role_id", sa.Integer))
    rcode = {rid: code for rid, code in bind.execute(sa.select(roles.c.id, roles.c.code))}
    paused = []
    for sid, owner, report, role_id in list(bind.execute(
            sa.select(subs.c.id, subs.c.owner_id, subs.c.report_code, users.c.role_id)
            .select_from(subs.join(users, users.c.id == subs.c.owner_id)).where(subs.c.is_active.is_(True)))):
        role = rcode.get(role_id)
        ok = (role == "ADMIN" or (report in OPERATIONAL_REPORTS) or
              (role in MIS_ROLES and report not in ADMIN_REPORTS))
        if not ok:
            paused.append(sid)
    if paused:
        bind.execute(subs.update().where(subs.c.id.in_(paused)).values(is_active=False))
        bind.execute(audit.insert().values(
            ts=_now(), username="system", action="SUBSCRIPTIONS_PAUSED", module="ADMIN", ref="report_subscriptions",
            reason="Owners no longer have access to these reports (Final Reports / MIS baseline)",
            details={"subscription_ids": paused}))

    # Guided Training: Viewer left the Management path
    tp = sa.table("training_progress", sa.column("user_id", sa.Integer), sa.column("path", sa.String),
                  sa.column("status", sa.String), sa.column("current_lesson", sa.String),
                  sa.column("current_step", sa.Integer), sa.column("lessons", sa.JSON),
                  sa.column("completed_at", sa.DateTime))
    viewer_ids = [i for (i,) in bind.execute(sa.select(users.c.id).where(
        users.c.role_id.in_([rid for rid, c in rcode.items() if c == "VIEWER"]))) if i]
    if viewer_ids:
        for uid, status, completed_at in list(bind.execute(sa.select(tp.c.user_id, tp.c.status, tp.c.completed_at).where(
                tp.c.user_id.in_(viewer_ids), tp.c.path == "MANAGEMENT"))):
            if status == "COMPLETED":
                lessons = {"k00-keys": {"done": (completed_at or _now()).isoformat(), "attempts": 1}}
                bind.execute(tp.update().where(tp.c.user_id == uid).values(path="VIEWER", lessons=lessons,
                                                                          current_lesson=None, current_step=0))
            else:
                bind.execute(tp.update().where(tp.c.user_id == uid).values(
                    path="VIEWER", lessons={}, current_lesson=None, current_step=0, status="NOT_STARTED"))


def downgrade() -> None:
    pass  # the old, wider permissions are deliberately not restored
