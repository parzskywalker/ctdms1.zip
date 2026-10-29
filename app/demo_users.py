"""The demonstration users - ONE list used by the demo data, the login page's demo box and the upgrade of an existing demo
database (so a user added here can never be missing from one of them)."""
from __future__ import annotations

USERS = [  # username, full name, role, depot codes (None = all), password
    ("admin", "System Administrator", "ADMIN", None, "Admin@123"),
    ("management", "Arvind Kumar", "MANAGEMENT", None, "Demo@123"),
    ("accounts", "Kavita Joshi", "ACCOUNTS", None, "Demo@123"),
    ("cfa.raipur", "Rohit Mishra", "CFA_MANAGER", ["RPR"], "Demo@123"),
    ("operator.raipur", "Sunil Dewangan", "DISPATCH_OPERATOR", ["RPR"], "Demo@123"),
    ("operator.bilaspur", "Ajay Kaushik", "DISPATCH_OPERATOR", ["BSP"], "Demo@123"),
    ("operator.durg", "Tarun Sinha", "DISPATCH_OPERATOR", ["DRG"], "Demo@123"),
    ("gate.raipur", "Mohan Sahu", "GATE_STAFF", ["RPR"], "Demo@123"),
    ("gate.bilaspur", "Ravi Tandon", "GATE_STAFF", ["BSP"], "Demo@123"),
    ("gate.durg", "Dinesh Verma", "GATE_STAFF", ["DRG"], "Demo@123"),
    ("checker", "Meena Gupta", "CHECKER", None, "Demo@123"),
    ("viewer", "Audit Viewer", "VIEWER", None, "Demo@123"),
]

CITY = {"RPR": "Raipur", "BSP": "Bilaspur", "DRG": "Durg"}


def login_rows() -> list[tuple[str, str, str]]:
    """(username, password, label) for the demo box on the login page."""
    from app.security.permissions import DEFAULT_ROLES
    out = []
    for uname, _full, role, cfas, pwd in USERS:
        name = "Administrator" if role == "ADMIN" else DEFAULT_ROLES.get(role, {}).get("name", role)
        out.append((uname, pwd, name + (" · " + ", ".join(CITY.get(c, c) for c in cfas) if cfas else "")))
    return out
