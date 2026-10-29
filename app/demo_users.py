"""The demonstration logins - ONE list, used by the demo seed, the login page and `prepare-demo`.

A new version that brings a new demo login (v1.7.1: the gate staff) only has to add it here: the seed creates it in a fresh
demo database, the login page lists it, and `seed.ensure_demo_users` adds it to a demo database that already has data.
"""
from __future__ import annotations

# username, full name, role code, depot codes (None = every depot), password, label shown on the login page
USERS: list[tuple[str, str, str, list[str] | None, str, str]] = [
    ("admin", "System Administrator", "ADMIN", None, "Admin@123", "Administrator"),
    ("management", "Arvind Kumar", "MANAGEMENT", None, "Demo@123", "Management"),
    ("accounts", "Kavita Joshi", "ACCOUNTS", None, "Demo@123", "Accounts"),
    ("cfa.raipur", "Rohit Mishra", "CFA_MANAGER", ["RPR"], "Demo@123", "Depot Manager · Raipur"),
    ("operator.raipur", "Sunil Dewangan", "DISPATCH_OPERATOR", ["RPR"], "Demo@123", "Dispatch Operator · Raipur"),
    ("operator.bilaspur", "Ajay Kaushik", "DISPATCH_OPERATOR", ["BSP"], "Demo@123", "Dispatch Operator · Bilaspur"),
    ("operator.durg", "Tarun Sinha", "DISPATCH_OPERATOR", ["DRG"], "Demo@123", "Dispatch Operator · Durg"),
    ("gate.raipur", "Mohan Sahu", "GATE_STAFF", ["RPR"], "Demo@123", "Dispatch / Gate Staff · Raipur"),
    ("gate.bilaspur", "Ravi Tandon", "GATE_STAFF", ["BSP"], "Demo@123", "Dispatch / Gate Staff · Bilaspur"),
    ("gate.durg", "Dinesh Verma", "GATE_STAFF", ["DRG"], "Demo@123", "Dispatch / Gate Staff · Durg"),
    ("checker", "Meena Gupta", "CHECKER", None, "Demo@123", "Checker / Approver"),
    ("viewer", "Audit Viewer", "VIEWER", None, "Demo@123", "Read-only Viewer"),
]


def seed_rows() -> list[tuple[str, str, str, list[str] | None, str]]:
    """What the seed creates: username, full name, role, depots, password."""
    return [(u, full, role, cfas, pw) for u, full, role, cfas, pw, _label in USERS]


def login_rows() -> list[tuple[str, str, str]]:
    """What the login page lists: username, password, label."""
    return [(u, pw, label) for u, _full, _role, _cfas, pw, label in USERS]
