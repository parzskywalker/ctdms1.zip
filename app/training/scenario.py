"""The v1.6 demonstration, replayed through the application's own services into prepared starting points.

Same workbook, same SAP / MARG / CBO files, same transporters, amounts and invoices as the v1.6 guide video
(docs/video_manual/v1.6). Each checkpoint is a small SQLite file; a trainee's practice database is a copy of one.

    clean       a brand-new CTDMS: roles, settings, the training's own demonstration users
    uploaded    + DEMO_v1.6_Master_Workbook.xlsx imported by the demo master (freight rates wait for a checker)
    masters     + the freight rates approved by the demo checker (two-person rule)
    sap         + ABC Pharma's SAP file imported, Government Supply given the code GOV
    invoices    + XYZ (MARG) and PQR (CBO) imported - 13 invoices waiting
    dispatched  + Shree Roadlines (Bilaspur), Sharma per trip ₹500, Ravi Tempo per box ₹432, Kishore ₹550 (printed)
    billed      + Shree Roadlines' bill for the Bilaspur trip entered by the demo accounts user
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import __version__
from app.config import BASE_DIR
from app.training import runtime

DEMO_FILES = BASE_DIR / "docs" / "video_manual" / "v1.6" / "demo_files"
WORKBOOK = DEMO_FILES / "DEMO_v1.6_Master_Workbook.xlsx"
SAP_FILE = DEMO_FILES / "DEMO_v1.6_SAP_billing_ABC.xlsx"
MARG_FILE = DEMO_FILES / "DEMO_v1.6_MARG_sales_XYZ.csv"
CBO_FILE = DEMO_FILES / "DEMO_v1.6_CBO_invoices_PQR.csv"
FILES = {f.name: f for f in (WORKBOOK, SAP_FILE, MARG_FILE, CBO_FILE)}

CHECKPOINTS = ["clean", "uploaded", "masters", "sap", "invoices", "dispatched", "billed"]
SCENARIO_VERSION = "4"
DEMO_USERS = [("demo.master", "Demo Master (training)", "ADMIN"),
              ("demo.checker", "Demo Checker (training)", "CHECKER"),
              ("demo.accounts", "Demo Accounts (training)", "ACCOUNTS"),
              ("demo.operator", "Demo Operator (training)", "DISPATCH_OPERATOR")]

# the v1.6 video's trips
LONG = ["001248", "001249", "PQR/B/0313"]                    # Bilaspur -> Shree Roadlines
LONG_LRS = {"DM-C071": "SRL-5501", "DM-C080": "SRL-5502"}
TRIP = ["001245", "001246", "001247", "XY/G/2601"]          # Guma -> Sharma Local Auto, ₹500 per trip
BOX = ["XY/K/2603", "PQR/K/0311"]                           # Kamal Vihar -> Ravi Tempo per box (15x20 + 6x22)
BOTH = ["XY/G/2602"]                                         # Guma -> Kishore Mini Truck, ₹550 per trip
BILL_NO = "SRL/OCT/26-01"

_lock = threading.Lock()


def templates_dir() -> Path:
    p = runtime.root() / "templates"
    p.mkdir(parents=True, exist_ok=True)
    return p


def template_path(name: str) -> Path:
    if name not in CHECKPOINTS:
        raise ValueError(f"unknown training checkpoint {name}")
    return templates_dir() / f"{name}.db"


def _stamp() -> str:
    h = hashlib.sha256()
    for f in sorted(FILES.values()):
        h.update(f.read_bytes() if f.exists() else b"missing")
    from app.db import Base
    h.update(",".join(sorted(Base.metadata.tables)).encode())
    return f"{__version__}|{SCENARIO_VERSION}|{h.hexdigest()[:16]}"


def ready() -> bool:
    meta = templates_dir() / "meta.json"
    try:
        ok = json.loads(meta.read_text(encoding="utf-8")).get("stamp") == _stamp()
    except (OSError, ValueError):
        return False
    return ok and all(template_path(n).exists() for n in CHECKPOINTS)


def ensure() -> None:
    """Build the starting points once (about 10-20 seconds), or again after an upgrade / a changed demo file."""
    with _lock:
        if not ready():
            build_all()


def missing_files() -> list[str]:
    return [n for n, f in FILES.items() if not f.exists()]


# ----------------------------------------------------------------------------- building
def build_all() -> None:
    gone = missing_files()
    if gone:
        raise RuntimeError("Guided Training needs the v1.6 demonstration files in docs/video_manual/v1.6/demo_files: "
                           + ", ".join(gone))
    import app.models  # noqa: F401 - every table registered before create_all
    from app.db import Base, _make_engine
    work = templates_dir() / "build"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    path = work / "build.db"
    eng = _make_engine("sqlite:///" + path.as_posix())
    Base.metadata.create_all(eng)
    ctx = {"uid": 0, "dir": work, "uploads": work / "uploads", "inbox": work / "inbox", "as": None}
    db = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)()
    try:
        with runtime.use(ctx):
            for name, step in (("clean", _clean), ("uploaded", _uploaded), ("masters", _masters), ("sap", _sap),
                               ("invoices", _invoices),
                               ("dispatched", _dispatched), ("billed", _billed)):
                step(db)
                db.commit()
                _snapshot(eng, work, name)
    finally:
        db.close()
        eng.dispose()
    (templates_dir() / "meta.json").write_text(json.dumps({"stamp": _stamp()}), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)


def _snapshot(eng, work: Path, name: str) -> None:
    eng.dispose()
    src = sqlite3.connect(work / "build.db")
    tmp = templates_dir() / f"{name}.db.tmp"
    if tmp.exists():
        tmp.unlink()
    out = sqlite3.connect(tmp)
    src.backup(out)
    out.execute("PRAGMA journal_mode=DELETE")
    out.close()
    src.close()
    tmp.replace(template_path(name))
    up = templates_dir() / f"{name}_uploads"
    shutil.rmtree(up, ignore_errors=True)
    if (work / "uploads").exists():
        shutil.copytree(work / "uploads", up)


def _user(db: Session, username: str):
    from app.models import User
    return db.scalar(select(User).where(User.username == username))


def _clean(db: Session) -> None:
    from app.models import Role, User
    from app.seed import seed_core
    from app.services import settings_service as cfg
    seed_core(db)
    db.flush()
    for username, name, role in DEMO_USERS:
        db.add(User(username=username, full_name=name, password_hash="!training-only", is_active=True,
                    role=db.scalar(select(Role).where(Role.code == role)), all_companies=True, all_cfas=True,
                    must_change_password=False))
    for key, val in (("org.name", "Tulsyan (Training)"), ("notify.provider", "OFF"), ("inbox.enabled", "false")):
        cfg.set_value(db, key, val)


def _uploaded(db: Session) -> None:
    from app.services import master_upload_service as mus
    master = _user(db, "demo.master")
    up = mus.preview(db, master, WORKBOOK.read_bytes(), WORKBOOK.name)
    db.commit()
    mus.commit(db, master, up)


def _masters(db: Session) -> None:
    from app.models import RateStatus, TransporterRate
    from app.services import transporter_rate_service as trs
    checker = _user(db, "demo.checker")
    for r in db.scalars(select(TransporterRate).where(TransporterRate.status == RateStatus.PENDING_APPROVAL)):
        trs.approve(db, checker, r, "Approved together (approve all)")


def _import(db: Session, f: Path, erp_code: str, company_code: str | None = None) -> None:
    from app.models import Company, ERPSystem
    from app.services import import_service as imp
    master = _user(db, "demo.master")
    erp = db.scalar(select(ERPSystem).where(ERPSystem.code == erp_code))
    co = db.scalar(select(Company).where(Company.code == company_code)) if company_code else None
    batch, _ = imp.create_batch_from_file(db, master, f.read_bytes(), f.name, erp_system_id=erp.id,
                                          company_id=co.id if co else None)
    imp.validate_batch(db, batch, master)
    db.commit()
    imp.commit_batch(db, master, batch)


def _sap(db: Session) -> None:
    _import(db, SAP_FILE, "SAP", "DM-ABC")
    db.commit()
    fix_government_supply(db)


def fix_government_supply(db: Session) -> None:
    from app.models import CustomerTypeMaster
    from app.services import recognition
    ct = db.scalar(select(CustomerTypeMaster).where(CustomerTypeMaster.code == "GOVT_SUPPLY"))
    if ct and not ct.marking:
        ct.marking = "GOV"
        db.flush()
        recognition.after_master_change(db, CustomerTypeMaster, ct)


def _invoices(db: Session) -> None:
    _import(db, MARG_FILE, "MARG")
    db.commit()
    _import(db, CBO_FILE, "CBO")


def invoices_by_no(db: Session, numbers: list[str]):
    from app.models import Invoice
    found = {i.invoice_no: i for i in db.scalars(select(Invoice).where(Invoice.invoice_no.in_(numbers)))}
    return [found[n] for n in numbers if n in found]


def _gatepass(db: Session, numbers: list[str], remarks: str, *, transporter=None, auto=None, method=None,
              trip=None, gate_out=True):
    from app.models import DispatchMode, OutsideAuto, Transporter
    from app.services import dispatch_service as ds
    master = _user(db, "demo.master")
    invs = invoices_by_no(db, numbers)
    req = ds.GatepassRequest(cases={i.id: str(i.boxes) for i in invs}, remarks=remarks, group_cross_company=True)
    if transporter:
        req.mode = DispatchMode.TRANSPORTER
        req.transporter_id = db.scalar(select(Transporter.id).where(Transporter.code == transporter))
    else:
        req.mode = DispatchMode.OUTSIDE_AUTO
        req.outside_auto_id = db.scalar(select(OutsideAuto.id).where(OutsideAuto.code == auto))
        req.auto_method = method
        req.trip_charge = Decimal(trip) if trip else None
    slip = ds.create_gatepass(db, master, invs, req)
    db.commit()
    ds.print_gatepass(db, master, slip, "PRINT_A5")
    if gate_out:
        ds.gate_out(db, master, slip)
    db.commit()
    return slip, invs


def _dispatched(db: Session) -> None:
    from app.services import dispatch_service as ds
    from app.services.utils import local_today
    master = _user(db, "demo.master")
    _, invs = _gatepass(db, LONG, "Demonstration - long distance", transporter="DM-SRL")
    for code, lr in LONG_LRS.items():
        ds.set_lr(db, master, [i for i in invs if i.customer.code == code], lr, local_today())
    db.commit()
    _gatepass(db, TRIP, "Demonstration - per trip, four invoices", auto="DM-LA1", method="PER_TRIP", trip="500")
    _gatepass(db, BOX, "Demonstration - per box", auto="DM-LA2", method="PER_CASE")
    _gatepass(db, BOTH, "Demonstration - per trip or per box", auto="DM-LA3", method="PER_TRIP", trip="550",
              gate_out=False)
    # a box-change request waiting for a checker (the operator counted 9 cases, the ERP says 8)
    from app.services import invoice_service
    inv = invoices_by_no(db, ["001250"])[0]
    invoice_service.request_override(db, _user(db, "demo.operator"), inv, "BOXES", 9,
                                     "Physical count at the dock shows 9 cases (demonstration)")


def _billed(db: Session) -> None:
    from app.models import Transporter
    from app.services import recon_service as rs
    from app.services.utils import local_today
    acc = _user(db, "demo.accounts")
    srl = db.scalar(select(Transporter).where(Transporter.code == "DM-SRL"))
    today = local_today()
    bill = rs.create_bill(db, acc, transporter_id=srl.id, bill_no=BILL_NO, bill_date=today, bill_amount="924",
                          period_from=today - timedelta(days=30), period_to=today,
                          remarks="Demonstration bill (training)")
    rs.link_dispatches(db, acc, bill)
    rs.set_line_amounts(db, acc, bill, {ln.id: str(ln.calculated_amount or 0) for ln in bill.lines})
    rs.apply_rate_review(db, acc, bill, [], "Card rates checked - no changes (demonstration)")
