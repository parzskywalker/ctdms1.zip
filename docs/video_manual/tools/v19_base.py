"""Builds the BASE practice database for the v1.9 manual videos on a SCRATCH folder (never the real data folder):
init-db -> create-admin -> pilot-v18 -> Guma master workbook through the upload screen -> Depot Manager, Dispatch Operator,
Gate Staff users (generated passwords, written to creds.json in the scratch folder only) -> Depot Manager approves the rates.
usage (project .venv python):  v19_base.py <base folder>"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(r"D:\claude code\ctdms1.zip")
BASE = Path(sys.argv[1])
WORKBOOK = Path(r"D:\claude code\pilot_inputs\Guma_Master_Workbook.xlsx")
PORT = 8039
assert "data" not in BASE.parts and BASE != REPO, "scratch folder only"
if BASE.exists():
    shutil.rmtree(BASE)
BASE.mkdir(parents=True)
for k in [k for k in os.environ if k.startswith("CTDMS_")]:
    del os.environ[k]
os.environ.update({"CTDMS_DATABASE_URL": "sqlite:///" + (BASE / "demo.db").as_posix(), "CTDMS_DATA_DIR": str(BASE / "appdata"),
                   "CTDMS_UPLOAD_DIR": str(BASE / "appdata" / "uploads"), "CTDMS_BACKUP_DIR": str(BASE / "appdata" / "backups"),
                   "CTDMS_LOG_DIR": str(BASE / "appdata" / "logs"), "CTDMS_ENVIRONMENT": "development",
                   "CTDMS_SHOW_DEMO_LOGINS": "false", "PYTHONUTF8": "1"})
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
os.chdir(REPO)
sys.path.insert(0, str(REPO))


def pw():
    return "Cf" + secrets.token_urlsafe(9) + "#7"


creds = {"admin": ("scratch.admin", pw()), "dm": ("suresh.dm", pw()), "op": ("ramesh.op", pw()), "guard": ("mahesh.guard", pw())}
(BASE / "creds.json").write_text(json.dumps(creds), encoding="utf-8")


def cli(*a):
    r = subprocess.run([PY, "-m", "app.cli", *a], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


cli("init-db")
cli("create-admin", "--username", creds["admin"][0], "--password", creds["admin"][1])
cli("pilot-v18")
import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import CFA, Role, User  # noqa: E402
from app.security.auth import hash_password  # noqa: E402

srv = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
                       stdout=open(BASE / "server.log", "w"), stderr=subprocess.STDOUT, cwd=REPO)
TOK = re.compile(r'name="csrf_token" value="([^"]+)"')
try:
    base = f"http://127.0.0.1:{PORT}"
    for _ in range(80):
        try:
            if httpx.get(base + "/health", timeout=3).status_code == 200:
                break
        except Exception:
            time.sleep(0.5)

    def login(user, p):
        c = httpx.Client(base_url=base, follow_redirects=False, timeout=180)
        r = c.post("/login", data={"username": user, "password": p, "next": "/", "csrf_token": TOK.search(c.get("/login").text).group(1)})
        assert r.status_code == 303, (user, r.status_code, r.headers)
        return c

    def tok(c, page):
        return TOK.search(c.get(page).text).group(1)

    adm = login(*creds["admin"])
    r = adm.get("/masters/upload")
    print("master upload page", r.status_code, r.headers.get("location"))
    r = adm.post("/masters/upload", data={"csrf_token": tok(adm, "/masters/upload")},
                 files={"file": (WORKBOOK.name, WORKBOOK.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    up = r.headers.get("location", "")
    r = adm.post(up + "/commit", data={"csrf_token": tok(adm, up)})
    print("commit", r.status_code)
    with SessionLocal() as db:
        cfa = db.scalar(select(CFA).where(CFA.code == "RPR"))
        for key, role in (("dm", "CFA_MANAGER"), ("op", "DISPATCH_OPERATOR"), ("guard", "GATE_STAFF")):
            un, p = creds[key]
            names = {"dm": "Suresh Sharma", "op": "Ramesh Sahu", "guard": "Mahesh Verma"}
            u = User(username=un, full_name=names[key], role=db.scalar(select(Role).where(Role.code == role)),
                     password_hash=hash_password(p), all_companies=True)
            u.cfas = [cfa]
            db.add(u)
        # The stock Dispatch Operator role has no invoice.import. The owner wants the operators to import and add invoices,
        # so the practice database gives that permission to the role (an Administrator does the same under Administration > Roles).
        opr = db.scalar(select(Role).where(Role.code == "DISPATCH_OPERATOR"))
        if "invoice.import" not in (opr.permissions or []):
            opr.permissions = list(opr.permissions or []) + ["invoice.import"]
        db.commit()
    dm = login(*creds["dm"])
    r = dm.post("/rate-cards/approve-all", data={"csrf_token": tok(dm, "/rate-cards"), "remarks": "approved after checking the Guma rate list"})
    print("approve", r.status_code)
finally:
    srv.terminate()
    time.sleep(1.5)
print("base ready")
