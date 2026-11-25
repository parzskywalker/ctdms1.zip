"""Builds app/static/geo/<state>.json (places, districts and PIN codes per state) from India Post's All India Pincode
Directory (Government of India open data, data.gov.in, GODL licence):
    https://data.gov.in/sites/default/files/dataurl31052019/Pincode_30052019.csv
usage: python deploy/build_geo.py <path to Pincode_30052019.csv>
Each file: {"state": name, "places": [[place, district, pin], ...]} sorted by place; districts are included as places."""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.geo import RAW_STATE, slug  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "app" / "static" / "geo"
SUFFIX = re.compile(r"\s+(B\.?O\.?|S\.?O\.?|H\.?O\.?|G\.?P\.?O\.?|BO|SO|HO|GPO)$", re.I)
DISTRICT_FIX = {  # spellings in the 2019 file -> the usual name
    "BILASPURCGH": "Bilaspur", "BIJAPUR(CGH)": "Bijapur", "JANJGIRCHAMPA": "Janjgir-Champa", "BALOD BAZER": "Baloda Bazar",
    "JASPUR": "Jashpur", "KAWARDHA": "Kabirdham (Kawardha)", "JAGDALPUR": "Bastar", "MAKDI": "Kondagaon",
}


def title(s: str) -> str:
    return " ".join(w if w.isupper() and len(w) <= 3 else w.capitalize() for w in s.split())


def district(raw: str) -> str:
    r = raw.strip()
    if r.upper() in DISTRICT_FIX:
        return DISTRICT_FIX[r.upper()]
    return title(re.sub(r"\s*\([^)]*\)$", "", r).lower())


def main(src: str) -> None:
    by_state: dict[str, set] = defaultdict(set)
    hq: dict[tuple, Counter] = defaultdict(Counter)
    with open(src, encoding="utf-8", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh):
            state = RAW_STATE.get(row["StateName"].strip())
            pin = (row["Pincode"] or "").strip()
            if not state or not re.fullmatch(r"\d{6}", pin):
                continue
            dist = district(row["District"])
            place = SUFFIX.sub("", row["Office Name"].strip()).strip()
            if not place:
                continue
            by_state[state].add((place, dist, pin))
            if row["OfficeType"].upper() in ("HO", "H.O", "SO", "S.O", "PO"):
                hq[(state, dist)][pin] += 3 if place.lower() == dist.lower() else 1
    OUT.mkdir(parents=True, exist_ok=True)
    for state, rows in sorted(by_state.items()):
        dists = {d for _, d, _ in rows}
        for d in dists:  # the district itself, with its head-office PIN
            pins = hq.get((state, d)) or Counter(p for _, dd, p in rows if dd == d)
            rows.add((d, d, pins.most_common(1)[0][0]))
        places = sorted(rows, key=lambda r: (r[0].lower(), r[1], r[2]))
        (OUT / f"{slug(state)}.json").write_text(json.dumps({"state": state, "places": places}, ensure_ascii=False,
                                                            separators=(",", ":")), encoding="utf-8")
        print(f"{state}: {len(places)} places, {len(dists)} districts")


if __name__ == "__main__":
    main(sys.argv[1])
