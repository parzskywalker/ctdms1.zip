"""v1.6 DEMONSTRATION data set - every record is a demonstration entry.

Codes start with DM- and names end with "(Demo)"; the short recognition codes (ABC, GUMA, C025 ...) are what appear in
the Invoice Recognition Code, e.g. ABC-RET-C025-GUMA-001245."""
from __future__ import annotations

ERPS = [  # code, name, how data arrives, active, notes
    ("SAP", "SAP S/4HANA - ABC Pharma (Demo)", "FILE", "Yes", "Billing documents exported to Excel every evening"),
    ("MARG", "MARG ERP 9+ - XYZ Healthcare (Demo)", "FILE", "Yes", "Sales register exported to CSV"),
    ("CBO", "CBO - PQR Lifesciences (Demo)", "API", "Yes", "PQR's IT team can push invoices by API"),
    ("INTRECT", "Intrect - own distribution (Demo)", "FILE", "No", "Planned - not connected yet"),
]

LOCATIONS = [  # code, name, city, route, zone, km
    ("DM-RPR", "Raipur City", "Raipur", "Local", "Central", 0),
    ("DM-BHL", "Bhilai", "Bhilai", "West", "West", 30),
    ("DM-DRG", "Durg", "Durg", "West", "West", 40),
    ("DM-BSP", "Bilaspur City", "Bilaspur", "North", "North", 0),
    ("DM-KRB", "Korba", "Korba", "North", "North", 90),
]

COMPANIES = [  # code, name, recognition code, ERP, relationship
    ("DM-ABC", "ABC Pharma Ltd (Demo)", "ABC", "SAP", "PRINCIPAL"),
    ("DM-XYZ", "XYZ Healthcare Ltd (Demo)", "XYZ", "MARG", "PRINCIPAL"),
    ("DM-PQR", "PQR Lifesciences Ltd (Demo)", "PQR", "CBO", "PRINCIPAL"),
    ("DM-OWN", "Tulsyan Distribution (Demo)", "TDL", "INTRECT", "OWN"),
]

DEPOTS = [  # code, name, locality, city, PIN, address, recognition code, companies, ERP
    ("DM-GUMA", "Guma Depot (Demo)", "Guma", "Raipur", "493111", "Plot 7, Guma Industrial Area", "GUMA",
     "DM-ABC, DM-XYZ, DM-OWN", "SAP"),
    ("DM-KVR", "Kamal Vihar Depot (Demo)", "Kamal Vihar", "Raipur", "492001", "Sector 4, Kamal Vihar", "KVR",
     "DM-XYZ, DM-PQR", "MARG"),
    ("DM-BSPD", "Bilaspur Depot (Demo)", "Vyapar Vihar", "Bilaspur", "495001", "Vyapar Vihar, Link Road", "BSP",
     "DM-ABC, DM-PQR", "SAP"),
]

CUSTOMER_TYPES = [  # key, name, recognition code
    ("RETAILER", "Retailer", "RET"),              # already built in -> "already in CTDMS"
    ("SUPER_STOCKIST", "Super Stockist", "SST"),
    ("GOVT_SUPPLY", "Government Supply", ""),     # no code on purpose -> "code not generated" demo
]

CUSTOMERS = [  # code, name, type, recognition, location, companies, depots, source ERP, ERP code, mobile
    ("DM-C025", "Sharma Medical Store (Demo)", "RETAILER", "C025", "DM-RPR", "DM-ABC, DM-XYZ", "DM-GUMA", "SAP", "100025", "9000001025"),
    ("DM-C031", "Raipur Medical Agencies (Demo)", "STOCKIST", "C031", "DM-RPR", "DM-ABC, DM-XYZ, DM-PQR", "DM-GUMA, DM-KVR", "SAP", "100031", "9000001031"),
    ("DM-C040", "City Care Hospital (Demo)", "HOSPITAL", "C040", "DM-RPR", "DM-ABC", "DM-GUMA", "SAP", "100040", "9000001040"),
    ("DM-C052", "Bhilai Chemists (Demo)", "RETAILER", "C052", "DM-BHL", "DM-XYZ, DM-PQR", "DM-GUMA, DM-KVR", "MARG", "BH052", "9000001052"),
    ("DM-C063", "Durg Distributors (Demo)", "DISTRIBUTOR", "C063", "DM-DRG", "DM-XYZ", "DM-KVR", "MARG", "DD063", "9000001063"),
    ("DM-C071", "WellCare Pharmacy Chain (Demo)", "CHAIN_PHARMACY", "C071", "DM-BSP", "DM-ABC, DM-PQR", "DM-BSPD", "SAP", "100071", "9000001071"),
    ("DM-C080", "Bilaspur Super Stockist (Demo)", "SUPER_STOCKIST", "C080", "DM-BSP", "DM-ABC", "DM-BSPD", "SAP", "100080", "9000001080"),
    ("DM-C090", "District Govt Store Korba (Demo)", "GOVT_SUPPLY", "C090", "DM-KRB", "DM-ABC", "DM-BSPD", "SAP", "100090", "9000001090"),
]
BAD_CUSTOMERS = [  # deliberate workbook problems
    ("DM-C025", "Sharma Medical Store - typed twice (Demo)", "RETAILER", "", "DM-RPR", "DM-ABC", "", "", "", ""),
    ("DM-C099", "Unknown Town Medical (Demo)", "RETAILER", "C099", "Nowhere", "DM-ABC", "", "", "", ""),
]

TRANSPORTERS = [  # code, name, type, payment, method, usual, trip, box, vehicle, mobile
    ("DM-SRL", "Shree Roadlines (Demo)", "LONG_DISTANCE", "TBB", "", "", "", "", "", "9000002001"),
    ("DM-VTC", "Vijay Transport Co (Demo)", "LONG_DISTANCE", "TBB", "", "", "", "", "", "9000002002"),
    ("DM-LA1", "Sharma Local Auto (Demo)", "LOCAL_AUTO", "", "PER_TRIP", "", "500", "", "CG04-AB-1001", "9000003001"),
    ("DM-LA2", "Ravi Tempo (Demo)", "LOCAL_AUTO", "", "PER_BOX", "", "", "20", "CG04-AB-1002", "9000003002"),
    ("DM-LA3", "Kishore Mini Truck (Demo)", "LOCAL_AUTO", "", "BOTH", "PER_TRIP", "600", "18", "CG04-AB-1003", "9000003003"),
]

LOCAL_AUTO_RATES = [  # code, auto, location, company, depot, rate
    ("DM-LA2-RPR", "DM-LA2", "DM-RPR", "", "", "20"),
    ("DM-LA2-BHL", "DM-LA2", "DM-BHL", "", "", "25"),
    ("DM-LA2-PQR-RPR", "DM-LA2", "DM-RPR", "DM-PQR", "", "22"),
    ("DM-LA3-RPR", "DM-LA3", "DM-RPR", "", "", "18"),
]

FREIGHT_RATES = [  # transporter, location, rate per case
    ("DM-SRL", "DM-BSP", "22"), ("DM-SRL", "DM-KRB", "30"), ("DM-SRL", "DM-DRG", "14"), ("DM-SRL", "DM-BHL", "15"),
    ("DM-VTC", "DM-BSP", "20"), ("DM-VTC", "DM-KRB", "28"),
]

MARKINGS = [  # code, ERP, marking, where, company, depot, customer type, notes
    ("DM-MK-XYG", "MARG", "XY/G/", "PREFIX", "DM-XYZ", "DM-GUMA", "", "XYZ invoices billed from Guma"),
    ("DM-MK-XYK", "MARG", "XY/K/", "PREFIX", "DM-XYZ", "DM-KVR", "", "XYZ invoices billed from Kamal Vihar"),
    ("DM-MK-PQK", "CBO", "PQR/K/", "PREFIX", "DM-PQR", "DM-KVR", "", "PQR invoices billed from Kamal Vihar"),
    ("DM-MK-PQB", "CBO", "PQR/B/", "PREFIX", "DM-PQR", "DM-BSPD", "", "PQR invoices billed from Bilaspur"),
]

ERP_MAPPINGS = [  # ERP, entity, ERP code, name in ERP, central code
    ("SAP", "DEPOT", "1101", "Plant Raipur-Guma", "DM-GUMA"),
    ("SAP", "DEPOT", "1102", "Plant Bilaspur", "DM-BSPD"),
    ("MARG", "CUSTOMER", "SH025", "SHARMA MEDICAL", "DM-C025"),
    ("MARG", "CUSTOMER", "RM031", "RAIPUR MED AGENCIES", "DM-C031"),
    ("CBO", "CUSTOMER", "CB-031", "Raipur Medical Agencies", "DM-C031"),
    ("CBO", "CUSTOMER", "CB-052", "Bhilai Chemists", "DM-C052"),
    ("CBO", "CUSTOMER", "CB-071", "WellCare Pharmacy", "DM-C071"),
]

# ---------------------------------------------------------------- transactions (invoice files)
# SAP (ABC Pharma): billing document, date, plant, sold-to (SAP code), name, net value, packages
SAP_INV = [
    ("001245", "16-10-2026", "1101", "100025", "SHARMA MEDICAL STORE", 18450, 5),
    ("001246", "16-10-2026", "1101", "100031", "RAIPUR MEDICAL AGENCIES", 52300, 15),
    ("001247", "16-10-2026", "1101", "100040", "CITY CARE HOSPITAL", 21600, 6),
    ("001248", "16-10-2026", "1102", "100071", "WELLCARE PHARMACY CHAIN", 39900, 12),
    ("001249", "17-10-2026", "1102", "100080", "BILASPUR SUPER STOCKIST", 68200, 20),
    ("001250", "17-10-2026", "1102", "100090", "DISTRICT GOVT STORE KORBA", 27750, 8),
]
# MARG (XYZ Healthcare): the marking XY/G/ or XY/K/ in the bill number tells company AND depot
MARG_INV = [
    ("XY/G/2601", "16-10-2026", "SH025", "SHARMA MEDICAL", 14200, 4),
    ("XY/G/2602", "17-10-2026", "BH052", "BHILAI CHEMISTS", 33500, 10),
    ("XY/K/2603", "17-10-2026", "RM031", "RAIPUR MED AGENCIES", 47800, 15),
    ("XY/K/2604", "17-10-2026", "DD063", "DURG DISTRIBUTORS", 29900, 9),
]
# CBO (PQR Lifesciences)
CBO_INV = [
    ("PQR/K/0311", "17-10-2026", "CB-031", "Raipur Medical Agencies", 19800, 6),
    ("PQR/K/0312", "17-10-2026", "CB-052", "Bhilai Chemists", 24100, 7),
    ("PQR/B/0313", "17-10-2026", "CB-071", "WellCare Pharmacy", 31200, 10),
]
