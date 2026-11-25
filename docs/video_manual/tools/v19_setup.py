"""Off-camera set-up of the v1.9 manual videos (run with the project's .venv python; CTDMS_* env points at a SCRATCH copy of
the base practice database, set by the caller). Everything goes through the app's own pages over HTTP (as the e2e rehearsal
does), so the data is exactly what the screens would have produced.

  v19_setup.py <video: v1|v2|v3|v4> <port> <scratch folder of this video>

Writes <folder>/files/*.csv (the files uploaded ON camera) and <folder>/codes.json (codes the guard scans in video 4)."""
import csv
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

REPO = Path(r"D:\claude code\ctdms1.zip")
BASE = Path(r"C:\Users\Admin\AppData\Local\Temp\claude\b209c825a13a9dad\ac27c6fa-3f02-40da-8e4f-d3e721dd611c\scratchpad\manual_v19\base")
VIDEO, PORT, FOLDER = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
assert "data" not in FOLDER.parts and FOLDER != REPO
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
creds = json.loads((BASE / "creds.json").read_text(encoding="utf-8"))
(FOLDER / "files").mkdir(parents=True, exist_ok=True)
import httpx  # noqa: E402

TOK = re.compile(r'name="csrf_token" value="([^"]+)"')
today = date.today()
dmy = lambda d: d.strftime("%d-%m-%Y")  # noqa: E731
HEAD = ["company_code", "cfa_code", "invoice_no", "invoice_date", "customer_code", "customer_name", "location", "sales_value",
        "boxes", "source_invoice_id", "remarks"]


def csv_file(name: str, rows: list, when: date):
    p = FOLDER / "files" / name
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEAD)
        for co, no, cc, cn, loc, val, boxes in rows:
            w.writerow([co, "RPR", no, dmy(when), cc, cn, loc, f"{val:.2f}", boxes, "", ""])
    return p


# ------------------------------------------------------------------------------------------------ the invoices
# (company, invoice no, customer code, customer name, area, value, cartons)
A = [  # uploaded ON camera
    ("ALKEM", "GM/2627/0481", "P0004", "AARTI PHARMA", "Bilaspur", 61250, 6),
    ("TORRENT", "GM/2627/0482", "P0118", "KESHARWANI MEDICAL JANJGIR", "Janjgir", 48800, 5),
    ("ALKEM", "GM/2627/0483", "P0133", "MAA MANKA MEDICAL STORE", "Champa", 33900, 4),
    ("DRL", "GM/2627/0484", "P0003", "AAROHI PHARMA", "Raipur", 27450, 3),
]
B = [  # already in the system and labelled (yesterday)
    ("OZONE", "GM/2627/0471", "P0147", "MISHRA MEDICAL AGENCIES", "Dantewada", 72400, 8),
    ("AJANTA", "GM/2627/0472", "P0036", "BABA ENTERPRISES", "Kondagaon", 39650, 5),
    ("MBC", "GM/2627/0473", "P0005", "AARUSH MEDICOSE", "Manendragarh", 55100, 7),
    ("DRL", "GM/2627/0474", "P0013", "ALOK MEDICO SURGICO.", "Bilaspur", 29800, 4),
    ("MACLEODS", "GM/2627/0475", "P0012", "AKASH AGENCIES.", "Janjgir", 21700, 3),
    ("DRL", "GM/2627/0476", "P0015", "AMAR MEDICAL & SURGICAL", "Raipur", 18900, 2),
]
H = [  # history: already dispatched some days ago
    ("ALKEM", "GM/2627/0455", "P0031", "ASHOK MEDICAL STORES", "Bilaspur", 44200, 6),
    ("ALKEM", "GM/2627/0456", "P0025", "APURVA MEDICAL AGENCIES", "Bilaspur", 31800, 5),
    ("TORRENT", "GM/2627/0457", "P0073", "GOYAL MEDICAL AND SURGICAL", "Manendragarh", 26400, 4),
    ("ALKEM", "GM/2627/0458", "P0002", "AARAV AGENCY", "Korba", 38900, 6),
]
csv_file("Invoices_today.csv", A, today)
csv_file("Invoices_yesterday.csv", B, today - timedelta(days=1))
csv_file("Invoices_history.csv", H, today - timedelta(days=5))

# ------------------------------------------------------------------------------------------------ http helpers
srv = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
                       stdout=open(FOLDER / "setup_server.log", "w"), stderr=subprocess.STDOUT, cwd=REPO)
base = f"http://127.0.0.1:{PORT}"
db_path = FOLDER / "demo.db"


def q(sql, args=()):
    con = sqlite3.connect(db_path, timeout=30)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def login(key):
    c = httpx.Client(base_url=base, follow_redirects=False, timeout=180)
    user, pw = creds[key]
    r = c.post("/login", data={"username": user, "password": pw, "next": "/", "csrf_token": TOK.search(c.get("/login").text).group(1)})
    assert r.status_code == 303, (key, r.status_code)
    return c


def tok(c, page="/"):
    return TOK.search(c.get(page).text).group(1)


def inv_id(no):
    return q("select id from invoices where invoice_no=?", (no,))[0][0]


def auto_id(name):
    return q("select id from outside_autos where name=?", (name,))[0][0]


def tr_id(name):
    return q("select id from transporters where name=?", (name,))[0][0]


def import_file(c, path):
    r = c.post("/imports/quick", data={"csrf_token": tok(c, "/imports")}, files={"file": (Path(path).name, Path(path).read_bytes(), "text/csv")},
               follow_redirects=True)
    assert "imported" in r.text.lower(), r.text[:500]


def label(c, no, count=None, reason=None):
    i = inv_id(no)
    boxes = q("select boxes from invoices where id=?", (i,))[0][0]
    t = tok(c, "/labels-desk")
    data = {"csrf_token": t, "count": str(count or boxes)}
    if reason:
        data["reason"] = reason
    r = c.post(f"/labels-desk/{i}", data=data)
    assert r.headers.get("location") == f"/labels-desk/{i}/print", (no, r.status_code, r.text[:300])
    c.post(f"/labels-desk/{i}/confirm", data={"csrf_token": t, "result": "PRINTED"})


def gatepass_fields(nos, auto, charge, recv, when=None, cases=None, vehicle="", transporter=None, mode="TRANSPORTER"):
    ids = [inv_id(n) for n in nos]
    d = {"mode": mode, "pickup_mode": "OUTSIDE_AUTO" if auto else "REGULAR", "outside_auto_id": str(auto_id(auto)) if auto else "",
         "auto_method": "PER_TRIP" if auto else "", "trip_charge": str(charge or ""), "vehicle_no": vehicle,
         "transporter_id": str(tr_id(transporter)) if transporter else "", "dispatch_date": (when or today).isoformat(),
         "remarks": "", "ids": [str(i) for i in ids]}
    for n, i in zip(nos, ids):
        d[f"cases_{i}"] = str((cases or {}).get(n) or q("select boxes from invoices where id=?", (i,))[0][0])
        if recv and recv.get(n):
            d[f"recv_{i}"] = str(tr_id(recv[n]))
    return d


def make_gatepass(c, fields):
    d = dict(fields)
    d["csrf_token"] = tok(c, "/gatepass/new")
    r = c.post("/gatepass/new", data=d)
    loc = r.headers.get("location", "")
    assert re.fullmatch(r"/gatepass/\d+", loc), (r.status_code, re.findall(r"<li>(.*?)</li>", r.text)[:5])
    return int(loc.split("/")[-1])


def print_all(c, slip_id):
    t = tok(c, f"/gatepass/{slip_id}")
    c.post(f"/gatepass/{slip_id}/print", data={"csrf_token": t, "mode": "print"})
    paw = [r[0] for r in q("select id from garage_pawatis where slip_id=? and status!='CANCELLED'", (slip_id,))]
    if paw:
        c.post("/pawati/print", data={"csrf_token": t, "ids": [str(p) for p in paw]})


def guard_out(g, slip_id, photo=True):
    dno = q("select dispatch_no from dispatch_slips where id=?", (slip_id,))[0][0]
    t = tok(g, "/gate/phone")
    hdr = {"X-CSRF-Token": t}
    s = g.post("/gate/phone/open", data={"code": dno, "csrf_token": t}, headers=hdr).json()
    assert s["ok"], s
    for (code,) in q("select case_code from dispatch_cases where slip_id=? and voided_at is null order by id", (slip_id,)):
        g.post("/gatepass/scan-case", data={"slip_id": slip_id, "code": code, "station": "phone", "csrf_token": t}, headers=hdr)
    for (pid,) in q("select id from garage_pawatis where slip_id=? and status!='CANCELLED'", (slip_id,)):
        g.post("/gate/phone/pawati", data={"slip_id": slip_id, "code": f"PW{pid}", "csrf_token": t}, headers=hdr)
    if q("select count(*) from dispatch_exceptions where slip_id=?", (slip_id,))[0][0] and photo:
        jpg = (BASE / "tiny_signed.jpg").read_bytes()
        g.post("/gate/phone/photo", data={"slip_id": slip_id, "csrf_token": t}, headers=hdr, files={"photo": ("signed.jpg", jpg, "image/jpeg")})
    r = g.post("/gatepass/scan-case", data={"slip_id": slip_id, "code": dno, "station": "phone", "csrf_token": t}, headers=hdr).json()
    assert r.get("gone_out"), r
    return dno


def save_plan(dm, fields):
    d = dict(fields)
    d["csrf_token"] = tok(dm, "/gatepass/new")
    r = dm.post("/load-plans/save", data=d)
    assert r.status_code == 303 and r.headers["location"] == "/load-plans", (r.status_code, re.findall(r"<li>(.*?)</li>", r.text)[:5])
    return q("select max(id) from load_plans")[0][0]


def set_lr(op, slip_id, customer_id, lr, when):
    t = tok(op, "/gatepass/lr-pending")
    op.post("/gatepass/lr-pending", data={"csrf_token": t, f"lr_{slip_id}_{customer_id}": lr, f"lrdate_{slip_id}_{customer_id}": when.isoformat()})


def cust_of(no):
    return q("select customer_id from invoices where invoice_no=?", (no,))[0][0]


def history(dm, op, g):
    """Two gate passes that left 3 and 5 days ago: LR pending for the Bilaspur ones, LR known for the others."""
    import_file(op, FOLDER / "files" / "Invoices_history.csv")
    # a tiny JPEG for the signed-gatepass photo
    if not (BASE / "tiny_signed.jpg").exists():
        from PIL import Image, ImageDraw  # noqa: E402
        im = Image.new("RGB", (320, 420), "white")
        dr = ImageDraw.Draw(im)
        dr.text((20, 20), "GATE PASS (signed)", fill="black")
        dr.line((30, 360, 200, 330), fill="blue", width=3)
        im.save(BASE / "tiny_signed.jpg", "JPEG")
    for n in ("GM/2627/0455", "GM/2627/0456"):
        label(dm, n)
    label(dm, "GM/2627/0457", count=5, reason="Party asked for one extra carton")  # carton-count exception
    label(dm, "GM/2627/0458")
    s1 = make_gatepass(dm, gatepass_fields(["GM/2627/0455", "GM/2627/0456"], "NANDKUMAR AUTO", 700, {}, today - timedelta(days=5),
                                           transporter="OSHO EXPRESS", vehicle=""))
    print_all(dm, s1)
    d1 = guard_out(g, s1)
    s2 = make_gatepass(dm, gatepass_fields(["GM/2627/0457", "GM/2627/0458"], "NANDKUMAR AUTO", 700,
                                           {"GM/2627/0457": "BHAI JI GARAGE", "GM/2627/0458": "AGRAWAL TRANSPORT"},
                                           today - timedelta(days=3), cases={"GM/2627/0457": 5}))
    print_all(dm, s2)
    d2 = guard_out(g, s2)
    set_lr(op, s2, cust_of("GM/2627/0457"), "BJG-44712", today - timedelta(days=3))
    set_lr(op, s2, cust_of("GM/2627/0458"), "AT-90817", today - timedelta(days=3))
    return d1, d2


def yesterday(dm, op):
    import_file(op, FOLDER / "files" / "Invoices_yesterday.csv")
    for row in B:
        label(dm, row[1])


try:
    for _ in range(80):
        try:
            if httpx.get(base + "/health", timeout=3).status_code == 200:
                break
        except Exception:
            time.sleep(0.5)
    dm, op, g = login("dm"), login("op"), login("guard")
    info = {}
    if VIDEO in ("v1", "v2", "v3", "v4"):
        info["history"] = history(dm, op, g)
    if VIDEO in ("v1", "v2", "v3"):
        yesterday(dm, op)
    if VIDEO == "v3":  # the operator's video: two vehicle plans waiting, made by the Depot Manager (from yesterday's labelled invoices)
        p1 = save_plan(dm, gatepass_fields(["GM/2627/0474"], "NANDKUMAR AUTO", 700, {}, transporter="OSHO EXPRESS"))
        p2 = save_plan(dm, gatepass_fields(["GM/2627/0472"], "PRADIP AUTO", 1200, {}, transporter="JAIN TRANSPORT"))
        info["plans"] = [p1, p2]
    if VIDEO == "v4":  # guard's video: one printed gate pass with an exception + 2 garages, one printed and then voided
        import_file(op, FOLDER / "files" / "Invoices_today.csv")
        label(dm, "GM/2627/0481")
        label(dm, "GM/2627/0482", count=6, reason="One extra carton packed at the party's request")
        label(dm, "GM/2627/0483")
        label(dm, "GM/2627/0484")
        s = make_gatepass(dm, gatepass_fields(["GM/2627/0481", "GM/2627/0482", "GM/2627/0483"], "NANDKUMAR AUTO", 700,
                                              {"GM/2627/0481": "OSHO EXPRESS", "GM/2627/0482": "ORIENT GOODS GARAGE",
                                               "GM/2627/0483": "ORIENT GOODS GARAGE"}, cases={"GM/2627/0482": 6}))
        print_all(dm, s)
        sv = make_gatepass(dm, gatepass_fields(["GM/2627/0484"], None, None, {}, mode="OUTSIDE_AUTO") |
                           {"mode": "OUTSIDE_AUTO", "pickup_mode": "REGULAR", "outside_auto_id": str(auto_id("SUNIL AUTO")),
                            "auto_method": "PER_CASE"})
        print_all(dm, sv)
        t = tok(dm, f"/gatepass/{sv}")
        r = dm.post(f"/gatepass/{sv}/void", data={"csrf_token": t, "reason": "Wrong vehicle - gate pass cancelled before loading"})
        info.update(slip=s, void_slip=sv)
        info["dispatch_no"] = q("select dispatch_no from dispatch_slips where id=?", (s,))[0][0]
        info["void_no"] = q("select dispatch_no from dispatch_slips where id=?", (sv,))[0][0]
        info["cases"] = [r[0] for r in q("select case_code from dispatch_cases where slip_id=? and voided_at is null order by id", (s,))]
        info["pawati"] = [f"PW{r[0]}" for r in q("select id from garage_pawatis where slip_id=? and status!='CANCELLED' order by id", (s,))]
    (FOLDER / "setup_info.json").write_text(json.dumps(info, default=str), encoding="utf-8")
    print("setup done", VIDEO, info)
finally:
    srv.terminate()
    time.sleep(1.5)
