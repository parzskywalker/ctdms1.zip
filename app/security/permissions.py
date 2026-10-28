"""Permission catalogue and default role definitions (role-based access control).

Roles are stored in the database and are edited by the Administrator under Administration > Roles; DEFAULT_ROLES are
only the TEMPLATES a new installation starts from (and "Reset to template" returns to). Nothing here is locked: any
permission can be given to any role, and individually to a user (user_permissions GRANT / DENY, see User.can()). The
guard rails are elsewhere: only holders of admin.users / admin.roles change access, nobody changes their own access,
at least one active user must always hold both (app.services.access), every change is audited.
"""
from __future__ import annotations

PERMISSIONS: dict[str, str] = {
    # Dashboard / reports
    "dashboard.view": "Open the Dashboard (counts only; money figures need Final Reports / MIS)",
    "report.view": "Operational registers & control reports (dispatch, gatepass, delivery, exceptions)",
    "mis.view": "Final Reports / Management MIS (freight, expense, variance, financial dashboard)",
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
    "pawati.print": "Print / reprint Garage Pawati slips (reprint needs a reason)",
    "pawati.cancel": "Cancel a Garage Pawati and make it again (reason required)",
    "pawati.receive": "Record that the receiving transporter received a Garage Pawati",
    "lr_book.manage": "Import LR (builty) books of transporters that give their own book",
    "gate.exception": "Let a gatepass leave with cases not scanned (reason required, audited)",
    "case.print": "Print case labels (one barcode per case)",
    "case.reprint": "Reprint case labels (same code, reason required)",
    "dispatch.group_cross_company": "Put invoices of different companies on one gatepass (explicit choice, audited)",
    "transporter.override_eligibility": "Choose a transporter that does not serve the depot / area (reason required, audited)",
    # Overrides
    "override.request": "Request manual change of boxes / rate / freight",
    "override.approve": "Approve / reject override requests",
    "override.direct": "Apply overrides directly (still audited)",
    # Freight rates
    "rate.view": "View freight rate master",
    "rate.create": "Create / revise freight rates",
    "rate.approve": "Approve freight rate changes",
    # Masters
    "master.use": "Use master records in dropdowns and searches (read-only lookup, no Master Settings)",
    "master.view": "Open Master Settings (view master data)",
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
    ("Dashboard & Reports", ["dashboard.view", "report.view", "mis.view", "data.export"]),
    ("Invoices", ["invoice.view", "invoice.import", "invoice.recalculate"]),
    ("Dispatch", ["dispatch.view", "dispatch.operate", "dispatch.verify", "dispatch.complete",
                  "dispatch.reprint", "dispatch.void_slip", "dispatch.cancel", "dispatch.cancel_dispatched",
                  "dispatch.group_cross_company", "transporter.override_eligibility", "case.print", "case.reprint",
                  "gate.exception", "pawati.print", "pawati.cancel", "pawati.receive", "lr_book.manage"]),
    ("Overrides", ["override.request", "override.approve", "override.direct"]),
    ("Freight Rates", ["rate.view", "rate.create", "rate.approve"]),
    ("Masters", ["master.use", "master.view", "master.edit"]),
    ("Reconciliation", ["recon.view", "recon.edit", "recon.approve", "recon.pay"]),
    ("Administration", ["audit.view", "admin.users", "admin.roles", "admin.settings", "admin.integrations",
                        "admin.backup"]),
]

ALL = list(PERMISSIONS)

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
                        "dispatch.group_cross_company", "transporter.override_eligibility", "case.print",
                        "case.reprint", "gate.exception", "pawati.print", "pawati.cancel", "pawati.receive",
                        "lr_book.manage", "override.request", "override.approve", "master.use"],
        "all_scope": False,
    },
    "DISPATCH_OPERATOR": {
        "name": "Dispatch Operator",
        "description": "Pending dispatch, dispatch entry, gatepass / slip printing and the dispatch register of the "
                       "assigned depot. Uses masters in dropdowns only.",
        "permissions": ["dashboard.view", "dispatch.view", "dispatch.operate", "dispatch.verify",
                        "dispatch.complete", "dispatch.reprint", "dispatch.group_cross_company", "case.print",
                        "case.reprint", "pawati.print", "pawati.receive", "lr_book.manage", "override.request",
                        "invoice.view", "master.use"],
        "all_scope": False,
    },
    "CHECKER": {
        "name": "Checker / Approver",
        "description": "Approval queues (overrides, freight rates, transporter bills), verification and exception "
                       "handling. No final reports or Master Settings.",
        "permissions": ["invoice.view", "invoice.recalculate", "dispatch.view", "dispatch.verify",
                        "dispatch.void_slip", "dispatch.cancel", "transporter.override_eligibility", "gate.exception",
                        "pawati.cancel",
                        "override.request",
                        "override.approve", "override.direct", "rate.approve", "recon.view", "recon.approve",
                        "master.use"],
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
