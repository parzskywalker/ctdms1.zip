"""Permission catalogue and default role definitions (role-based access control).

Roles are stored in the database and can be edited from Administration > Roles;
these are only the seeded defaults.
"""
from __future__ import annotations

PERMISSIONS: dict[str, str] = {
    # Dashboard / reports
    "dashboard.view": "View management dashboard",
    "report.view": "View reports & MIS",
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
    "rate.view": "View freight rate master",
    "rate.create": "Create / revise freight rates",
    "rate.approve": "Approve freight rate changes",
    # Masters
    "master.view": "View master data",
    "master.edit": "Create / edit master data",
    # Reconciliation
    "recon.view": "View transporter bills & reconciliation",
    "recon.edit": "Enter / import transporter bills",
    "recon.approve": "Approve transporter bills",
    "recon.pay": "Mark transporter bills paid",
    # Admin
    "audit.view": "View audit log",
    "admin.users": "Manage users",
    "admin.roles": "Manage roles & permissions",
    "admin.settings": "Manage system settings",
    "admin.integrations": "Manage ERP integrations",
    "admin.backup": "Download full data export / backups",
}

PERMISSION_GROUPS: list[tuple[str, list[str]]] = [
    ("Dashboard & Reports", ["dashboard.view", "report.view", "data.export"]),
    ("Invoices", ["invoice.view", "invoice.import", "invoice.recalculate"]),
    ("Dispatch", ["dispatch.view", "dispatch.operate", "dispatch.verify", "dispatch.complete",
                  "dispatch.reprint", "dispatch.void_slip", "dispatch.cancel", "dispatch.cancel_dispatched"]),
    ("Overrides", ["override.request", "override.approve", "override.direct"]),
    ("Freight Rates", ["rate.view", "rate.create", "rate.approve"]),
    ("Masters", ["master.view", "master.edit"]),
    ("Reconciliation", ["recon.view", "recon.edit", "recon.approve", "recon.pay"]),
    ("Administration", ["audit.view", "admin.users", "admin.roles", "admin.settings", "admin.integrations",
                        "admin.backup"]),
]

ALL = list(PERMISSIONS)

DEFAULT_ROLES: dict[str, dict] = {
    "ADMIN": {
        "name": "Administrator",
        "description": "Full access to every module and setting.",
        "permissions": ["*"],
        "all_scope": True,
    },
    "MANAGEMENT": {
        "name": "Management",
        "description": "Dashboard, reports and MIS across all companies and depots.",
        "permissions": ["dashboard.view", "report.view", "data.export", "invoice.view", "dispatch.view",
                        "rate.view", "master.view", "recon.view", "audit.view"],
        "all_scope": True,
    },
    "ACCOUNTS": {
        "name": "Accounts",
        "description": "Transporter bills, freight reconciliation and reports.",
        "permissions": ["dashboard.view", "report.view", "data.export", "invoice.view", "dispatch.view",
                        "rate.view", "master.view", "recon.view", "recon.edit", "recon.approve", "recon.pay"],
        "all_scope": True,
    },
    "CFA_MANAGER": {
        "name": "Depot Manager",
        "description": "Assigned depot data, imports, approvals and reports.",
        "permissions": ["dashboard.view", "report.view", "data.export", "invoice.view", "invoice.import",
                        "invoice.recalculate", "dispatch.view", "dispatch.operate", "dispatch.verify",
                        "dispatch.complete", "dispatch.reprint", "dispatch.void_slip", "dispatch.cancel",
                        "override.request", "override.approve", "rate.view", "rate.create", "master.view",
                        "recon.view", "recon.approve"],
        "all_scope": False,
    },
    "DISPATCH_OPERATOR": {
        "name": "Dispatch Operator",
        "description": "Pending dispatch, dispatch entry and slip printing.",
        "permissions": ["dispatch.view", "dispatch.operate", "dispatch.verify", "dispatch.complete",
                        "dispatch.reprint", "override.request", "invoice.view"],
        "all_scope": False,
    },
    "CHECKER": {
        "name": "Checker / Approver",
        "description": "Verification, overrides, rate approvals and exception resolution.",
        "permissions": ["dashboard.view", "report.view", "data.export", "invoice.view", "invoice.recalculate",
                        "dispatch.view", "dispatch.verify", "dispatch.void_slip", "dispatch.cancel",
                        "override.request", "override.approve", "override.direct", "rate.view",
                        "rate.approve", "master.view", "recon.view", "recon.approve", "audit.view"],
        "all_scope": True,
    },
    "VIEWER": {
        "name": "Read-only Viewer",
        "description": "Reports only. No exports, no changes.",
        "permissions": ["report.view"],
        "all_scope": True,
    },
}
