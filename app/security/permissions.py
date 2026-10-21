"""Permission catalogue and default role definitions (role-based access control).

Roles are stored in the database and can be edited from Administration > Roles; DEFAULT_ROLES are the seeded
baseline. FIXED_BASELINE permissions (Master Settings, administration, Final Reports / MIS) cannot be given to other
roles at all - see Role.has().
"""
from __future__ import annotations

PERMISSIONS: dict[str, str] = {
    # Dashboard / reports
    "dashboard.view": "Open the Dashboard (counts only; money figures need Final Reports / MIS)",
    "report.view": "Operational registers & control reports (dispatch, gatepass, delivery, exceptions)",
    "mis.view": "Final Reports / Management MIS (freight, expense, variance, financial dashboard) - fixed baseline",
    "data.export": "Export data (Excel / CSV / PDF)",
    # Invoices
    "invoice.view": "View invoice register",
    "invoice.import": "Import invoices (Excel/CSV/API)",
    "invoice.recalculate": "Re-run freight calculation on open invoices",
    # Dispatch
    "dispatch.view": "View pending dispatch & dispatch register",
    "dispatch.operate": "Enter dispatch details, generate & print slips",
    "dispatch.verify": "Verify boxes / freight calculation",
    "dispatch.complete": "Complete dispatch",
    "dispatch.reprint": "Reprint dispatch slip",
    "dispatch.void_slip": "Void a generated slip (unlock for correction)",
    "dispatch.cancel": "Cancel an open dispatch",
    "dispatch.cancel_dispatched": "Cancel an already completed dispatch",
    # Overrides
    "override.request": "Request manual change of boxes / rate / freight",
    "override.approve": "Approve / reject override requests",
    "override.direct": "Apply overrides directly (still audited)",
    # Freight rates
    "rate.view": "View freight rate master - fixed baseline",
    "rate.create": "Create / revise freight rates - fixed baseline",
    "rate.approve": "Approve freight rate changes",
    # Masters
    "master.use": "Use master records in dropdowns and searches (read-only lookup, no Master Settings)",
    "master.view": "Open Master Settings (view master data) - fixed baseline",
    "master.edit": "Create / edit master data - fixed baseline",
    # Reconciliation
    "recon.view": "View transporter bills & reconciliation",
    "recon.edit": "Enter / import transporter bills",
    "recon.approve": "Approve transporter bills",
    "recon.pay": "Mark transporter bills paid",
    # Admin
    "audit.view": "View audit log - fixed baseline",
    "admin.users": "Manage users - fixed baseline",
    "admin.roles": "Manage roles & permissions - fixed baseline",
    "admin.settings": "Manage system settings - fixed baseline",
    "admin.integrations": "Manage ERP integrations - fixed baseline",
    "admin.backup": "Download full data export / backups - fixed baseline",
}

PERMISSION_GROUPS: list[tuple[str, list[str]]] = [
    ("Dashboard & Reports", ["dashboard.view", "report.view", "mis.view", "data.export"]),
    ("Invoices", ["invoice.view", "invoice.import", "invoice.recalculate"]),
    ("Dispatch", ["dispatch.view", "dispatch.operate", "dispatch.verify", "dispatch.complete",
                  "dispatch.reprint", "dispatch.void_slip", "dispatch.cancel", "dispatch.cancel_dispatched"]),
    ("Overrides", ["override.request", "override.approve", "override.direct"]),
    ("Freight Rates", ["rate.view", "rate.create", "rate.approve"]),
    ("Masters", ["master.use", "master.view", "master.edit"]),
    ("Reconciliation", ["recon.view", "recon.edit", "recon.approve", "recon.pay"]),
    ("Administration", ["audit.view", "admin.users", "admin.roles", "admin.settings", "admin.integrations",
                        "admin.backup"]),
]

ALL = list(PERMISSIONS)

ADMIN, MANAGEMENT, ACCOUNTS = "ADMIN", "MANAGEMENT", "ACCOUNTS"

# Fixed baseline rules. A permission listed here only ever works for the roles named - whatever is stored in the
# role's permission list (an edited database, an old role, a role made by the Master). The Roles screen shows them
# locked and refuses to save them for other roles.
#   Master Settings, freight-rate master, administration and the audit log: Administrator only.
#   Final Reports / Management MIS: Administrator, Accounts and Management only.
FIXED_BASELINE: dict[str, frozenset[str]] = {
    "mis.view": frozenset({ADMIN, ACCOUNTS, MANAGEMENT}),
    "master.view": frozenset({ADMIN}),
    "master.edit": frozenset({ADMIN}),
    "rate.view": frozenset({ADMIN}),
    "rate.create": frozenset({ADMIN}),
    "audit.view": frozenset({ADMIN}),
    "admin.users": frozenset({ADMIN}),
    "admin.roles": frozenset({ADMIN}),
    "admin.settings": frozenset({ADMIN}),
    "admin.integrations": frozenset({ADMIN}),
    "admin.backup": frozenset({ADMIN}),
}


def baseline_allows(role_code: str | None, perm: str) -> bool:
    """False when `perm` is a fixed-baseline permission this role may never hold."""
    allowed = FIXED_BASELINE.get(perm)
    return allowed is None or role_code in allowed


def locked_for(role_code: str | None) -> set[str]:
    """Permissions that cannot be granted to this role (shown locked on the Roles screen)."""
    return {p for p in FIXED_BASELINE if not baseline_allows(role_code, p)}


DEFAULT_ROLES: dict[str, dict] = {
    "ADMIN": {
        "name": "Administrator",
        "description": "Full access: Master Settings, administration, all operations and all reports. "
                       "Two-person approval rules still apply.",
        "permissions": ["*"],
        "all_scope": True,
    },
    "MANAGEMENT": {
        "name": "Management",
        "description": "Management dashboard, Final Reports / MIS and dispatch monitoring across all companies and "
                       "depots. Read-only: no Master Settings.",
        "permissions": ["dashboard.view", "mis.view", "report.view", "data.export", "invoice.view", "dispatch.view",
                        "master.use"],
        "all_scope": True,
    },
    "ACCOUNTS": {
        "name": "Accounts",
        "description": "Transporter bills, reconciliation, payment and Final Reports / MIS. No Master Settings.",
        "permissions": ["dashboard.view", "mis.view", "report.view", "data.export", "invoice.view", "dispatch.view",
                        "master.use", "recon.view", "recon.edit", "recon.approve", "recon.pay"],
        "all_scope": True,
    },
    "CFA_MANAGER": {
        "name": "Depot Manager",
        "description": "Assigned depots only: operations dashboard, invoices, dispatch, exceptions, gatepasses, "
                       "deliveries and depot overrides. No final financial reports, bills or Master Settings.",
        "permissions": ["dashboard.view", "report.view", "data.export", "invoice.view", "invoice.import",
                        "invoice.recalculate", "dispatch.view", "dispatch.operate", "dispatch.verify",
                        "dispatch.complete", "dispatch.reprint", "dispatch.void_slip", "dispatch.cancel",
                        "override.request", "override.approve", "master.use"],
        "all_scope": False,
    },
    "DISPATCH_OPERATOR": {
        "name": "Dispatch Operator",
        "description": "Pending dispatch, dispatch entry, gatepass / slip printing and the dispatch register of the "
                       "assigned depot. Uses masters in dropdowns only.",
        "permissions": ["dashboard.view", "dispatch.view", "dispatch.operate", "dispatch.verify",
                        "dispatch.complete", "dispatch.reprint", "override.request", "invoice.view", "master.use"],
        "all_scope": False,
    },
    "CHECKER": {
        "name": "Checker / Approver",
        "description": "Approval queues (overrides, freight rates, transporter bills), verification and exception "
                       "handling. No final reports or Master Settings.",
        "permissions": ["invoice.view", "invoice.recalculate", "dispatch.view", "dispatch.verify",
                        "dispatch.void_slip", "dispatch.cancel", "override.request", "override.approve",
                        "override.direct", "rate.approve", "recon.view", "recon.approve", "master.use"],
        "all_scope": True,
    },
    "VIEWER": {
        "name": "Read-only Viewer",
        "description": "Limited read-only operational screens. Not a Management user: no final reports, no exports, "
                       "no Master Settings.",
        "permissions": ["dashboard.view", "invoice.view", "dispatch.view", "master.use"],
        "all_scope": True,
    },
}
