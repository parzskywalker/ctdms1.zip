"""States of India for the address boxes (State -> City -> PIN). The places and PIN codes of each state are static files
in app/static/geo/<slug>.json, built by deploy/build_geo.py from India Post's open-data PIN code directory."""
from __future__ import annotations

import re

# state name in the India Post 2019 file -> the name shown and stored
RAW_STATE = {
    "Andaman and Nico.In.": "Andaman and Nicobar Islands", "Andhra Pradesh": "Andhra Pradesh",
    "Arunachal Pradesh": "Arunachal Pradesh", "Assam": "Assam", "Bihar": "Bihar", "Chandigarh": "Chandigarh",
    "Chattisgarh": "Chhattisgarh", "Dadra and Nagar Hav.": "Dadra and Nagar Haveli and Daman and Diu",
    "Daman and Diu": "Dadra and Nagar Haveli and Daman and Diu", "Delhi": "Delhi", "Goa": "Goa", "Gujarat": "Gujarat",
    "Haryana": "Haryana", "Himachal Pradesh": "Himachal Pradesh", "Jammu and Kashmir": "Jammu and Kashmir",
    "Jharkhand": "Jharkhand", "Karnataka": "Karnataka", "Kerala": "Kerala", "Lakshadweep": "Lakshadweep",
    "Madhya Pradesh": "Madhya Pradesh", "Maharashtra": "Maharashtra", "Manipur": "Manipur", "Megalaya": "Meghalaya",
    "Mizoram": "Mizoram", "Nagaland": "Nagaland", "Odisha": "Odisha", "Pondicherry": "Puducherry", "Punjab": "Punjab",
    "Rajasthan": "Rajasthan", "Sikkim": "Sikkim", "Tamil Nadu": "Tamil Nadu", "Telangana": "Telangana",
    "Tripura": "Tripura", "Uttar Pradesh": "Uttar Pradesh", "Uttarakhand": "Uttarakhand", "West Bengal": "West Bengal",
}
STATES = sorted(set(RAW_STATE.values()))


def slug(state: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", state.lower()).strip("-")
