"""The demonstration data set (single source of truth for the video, the PDF manual and the import files).

EVERYTHING here is a DEMONSTRATION ENTRY: codes start with DM-, names end with "(Demo)", invoice numbers start
with DEMO-. Remove them before live use (see the manual, chapter "Removing the demonstration data")."""
from __future__ import annotations

ERPS = [  # code, name, adapter, notes, active, api_key
    ("DM-SAP", "SAP S/4HANA - Company A (Demo)", "file",
     "Billing documents are exported to Excel every evening and uploaded here. Status: WORKING.", True, False),
    ("DM-MARG", "MARG ERP 9+ - Company B (Demo)", "marg",
     "Sales register exported to CSV. Direct database link not configured yet. Status: FILE IMPORT ONLY.", True, False),
    ("DM-CBO", "CBO - Company C (Demo)", "rest",
     "Company C's IT team will push invoices to CTDMS through the API. Status: API KEY ISSUED.", True, True),
    ("DM-INTRECT", "Intrect - own distribution (Demo)", "file",
     "Planned, not connected. Status: INACTIVE.", False, False),
]

CFA = dict(code="DM-RPR", name="Raipur CFA (Demo)", warehouse="Main warehouse", city="Raipur", state="Chhattisgarh",
           pincode="492001", gstin="22DEMOR0001R1Z1", contact_person="Warehouse Manager (Demo)",
           contact_phone="9000000001", erp="DM-SAP")

LOCATIONS = [  # code, name, city, route, zone, km
    ("DM-RAIPUR", "Raipur", "Raipur", "Local", "Central", 0),
    ("DM-BILASPUR", "Bilaspur", "Bilaspur", "North", "North", 115),
    ("DM-DURG", "Durg", "Durg", "West", "West", 40),
    ("DM-BHILAI", "Bhilai", "Bhilai", "West", "West", 35),
    ("DM-KORBA", "Korba", "Korba", "North", "North", 200),
]

COMPANIES = [  # code, name, erp, gstin
    ("DM-COA", "Company A Pharma (Demo)", "DM-SAP", "22DEMOA0001A1Z1"),
    ("DM-COB", "Company B Healthcare (Demo)", "DM-MARG", "22DEMOB0002B1Z2"),
    ("DM-COC", "Company C Lifesciences (Demo)", "DM-CBO", "22DEMOC0003C1Z3"),
]

CUSTOMERS = [  # code, name, type, location, gstin, mobile
    ("DM-C01", "Alpha Stockist (Demo)", "STOCKIST", "Raipur", "22DEMOX0001X1Z1", "9000000011"),
    ("DM-C02", "Beta Medical Agency (Demo)", "STOCKIST", "Bilaspur", "22DEMOX0002X1Z2", "9000000012"),
    ("DM-C03", "Gamma Pharma Distributors (Demo)", "STOCKIST", "Durg", "22DEMOX0003X1Z3", "9000000013"),
    ("DM-C04", "Delta Chemists (Demo)", "RETAILER", "Bhilai", "22DEMOX0004X1Z4", "9000000014"),
    ("DM-C05", "Epsilon City Hospital (Demo)", "HOSPITAL", "Korba", "22DEMOX0005X1Z5", "9000000015"),
    ("DM-C06", "Zeta Medical Hall (Demo)", "RETAILER", "Raipur", "22DEMOX0006X1Z6", "9000000016"),
    ("DM-C07", "Eta Healthcare Agency (Demo)", "STOCKIST", "Bilaspur", "22DEMOX0007X1Z7", "9000000017"),
    ("DM-C08", "Theta Pharma Mart (Demo)", "RETAILER", "Durg", "22DEMOX0008X1Z8", "9000000018"),
]

# ERP-specific party codes (what each ERP calls the customer) - mapped on the customer screen
ERP_CODES = {"DM-C01": [("DM-MARG", "ALP001")], "DM-C02": [("DM-MARG", "BET002")], "DM-C04": [("DM-MARG", "DEL004")],
             "DM-C06": [("DM-MARG", "ZET006")], "DM-C07": [("DM-MARG", "ETA007")]}

TRANSPORTERS = [  # code, name, mode, contact, mobile, gst, extra
    ("DM-T1", "Alpha Roadlines (Demo)", "TBB", "Mr. Singh (Demo)", "9000000101", "22DEMOT0001T1Z1", None),
    ("DM-T2", "Beta Cargo Movers (Demo)", "TBB", "Mr. Verma (Demo)", "9000000102", "22DEMOT0002T1Z2", None),
    ("DM-T3", "Gamma Express Parcel (Demo)", "CASH", "Mr. Khan (Demo)", "9000000103", "22DEMOT0003T1Z3", None),
    ("DM-T4", "Delta Logistics (Demo)", "TBB", "Mr. Das (Demo)", "9000000104", "22DEMOT0004T1Z4", ("200", "PER_TRIP")),
    ("DM-T5", "Epsilon Transport Co (Demo)", "TBB", "Mr. Rao (Demo)", "9000000105", "22DEMOT0005T1Z5", None),
]

AUTO = ("DM-AUTO1", "Local Auto - Ramesh (Demo)", "9000000201", "PER_TRIP", "150")

# transporter -> {area: rate per case}
RATES = {
    "DM-T1": {"Raipur": 6, "Bilaspur": 22, "Durg": 14, "Bhilai": 15, "Korba": 28},
    "DM-T2": {"Raipur": 7, "Bilaspur": 20, "Durg": 13, "Bhilai": 14, "Korba": 30},
    "DM-T3": {"Korba": 26, "Bilaspur": 21},
    "DM-T4": {"Raipur": 8, "Bilaspur": 24, "Durg": 15, "Bhilai": 16, "Korba": 32},
    "DM-T5": {"Raipur": 5, "Bilaspur": 19, "Bhilai": 13},
}

USERS = [  # username, full name, role label, scope note
    ("demo.checker", "Demo Checker", "Checker / Approver"),
    ("demo.operator", "Demo Dispatch Operator", "Dispatch Operator"),
    ("demo.accounts", "Demo Accounts", "Accounts"),
]

SKUS = [("DM-PCM650", "Paracetamol 650 mg Tab 15s"), ("DM-AZI500", "Azithromycin 500 mg Tab 3s"),
        ("DM-PAN40", "Pantoprazole 40 mg Tab 15s"), ("DM-ORS", "ORS Sachet 21 g"),
        ("DM-MVT", "Multivitamin Syrup 200 ml"), ("DM-AMX625", "Amoxicillin + Clav 625 Tab 6s")]

# invoices: no, date(dd-mm-yyyy), company, customer code, customer name, location, value, cases, erp-source
SAP_INV = [
    ("DEMO-SAP-1001", "14-10-2026", "DM-C01", 48500, 12, [("DM-PCM650", 40), ("DM-AZI500", 30), ("DM-PAN40", 20)]),
    ("DEMO-SAP-1002", "14-10-2026", "DM-C02", 36240, 9, [("DM-ORS", 60), ("DM-MVT", 25)]),
    ("DEMO-SAP-1003", "15-10-2026", "DM-C03", 52800, 14, [("DM-AMX625", 30), ("DM-PCM650", 50), ("DM-AZI500", 20)]),
    ("DEMO-SAP-1004", "16-10-2026", "DM-C05", 71300, 18, [("DM-AMX625", 45), ("DM-PAN40", 35), ("DM-MVT", 15)]),
    ("DEMO-SAP-1005", "17-10-2026", "DM-C06", 8750, 3, [("DM-ORS", 40), ("DM-PCM650", 10)]),
]
MARG_INV = [  # no, date, party code, party name, station, value, cases
    ("DEMO-MRG-2001", "15-10-2026", "ALP001", "ALPHA STOCKIST (DEMO)", "RAIPUR", 22400, 6),
    ("DEMO-MRG-2002", "15-10-2026", "DEL004", "DELTA CHEMISTS (DEMO)", "BHILAI", 18900, 5),
    ("DEMO-MRG-2003", "16-10-2026", "ZET006", "ZETA MEDICAL HALL (DEMO)", "RAIPUR", 9600, 3),
    ("DEMO-MRG-2004", "16-10-2026", "ETA007", "ETA HEALTHCARE AGENCY (DEMO)", "BILASPUR", 31750, 8),
    ("DEMO-MRG-2005", "16-10-2026", "TH-08", "THETA PHARMA MART", "DURG", 15000, 4),
    ("DEMO-MRG-2006", "17-10-2026", "BET002", "BETA MEDICAL AGENCY (DEMO)", "BILASPUR", 27300, ""),
    ("DEMO-MRG-2003", "16-10-2026", "ZET006", "ZETA MEDICAL HALL (DEMO)", "RAIPUR", 9600, 3),  # duplicate row on purpose
]
CBO_INV = [  # no, date, customer code, name, city, value, cases, gstin
    ("DEMO-CBO-3001", "16/10/2026", "CB-GAMMA", "Gamma Pharma Distributors (Demo)", "Durg", 41000, 10, "22DEMOX0003X1Z3"),
    ("DEMO-CBO-3002", "16/10/2026", "CB-EPS", "Epsilon City Hospital (Demo)", "Korba", 64500, 16, "22DEMOX0005X1Z5"),
    ("DEMO-CBO-3003", "17/10/2026", "CB-THETA", "Theta Pharma Mart (Demo)", "Durg", 12800, 4, "22DEMOX0008X1Z8"),
]
MANUAL = ("DEMO-MAN-4001", "17-10-2026", "DM-C04", "Company A Pharma (Demo)", 19200, 6, 16300)
MANUAL_WRONG = ("DEMO-MAN-4002", "17-10-2026", "DM-C06", "Company B Healthcare (Demo)", 5000, 2, 4200)
QR_INV = ("DEMO-QR-5001", "17-10-2026", "DM-COA", "DM-C06", 14400, 4)
