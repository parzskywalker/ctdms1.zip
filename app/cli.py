"""Command-line tasks.

    python -m app.cli init-db            # run migrations (creates tables) + roles/settings
    python -m app.cli seed-demo --reset  # full demo dataset (DESTROYS existing data)
    python -m app.cli create-admin --username admin --password '...'
    python -m app.cli sync --all         # run ERP adapters flagged 'auto sync' (use from cron)
    python -m app.cli backup             # database backup to data/backups
    python -m app.cli check              # configuration sanity check
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from app.config import BASE_DIR, get_settings


def _alembic_upgrade() -> None:
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    command.upgrade(cfg, "head")


def init_db(args) -> None:
    _alembic_upgrade()
    from app.db import SessionLocal
    from app.seed import seed_core
    db = SessionLocal()
    try:
        seed_core(db)
        db.commit()
    finally:
        db.close()
    print("Database migrated and core roles/settings ensured.")


def seed_demo(args) -> None:
    from app import seed
    if args.if_empty:
        from sqlalchemy import inspect, select
        from app.db import SessionLocal, engine
        from app.models import Company
        if inspect(engine).has_table("companies"):
            db = SessionLocal()
            try:
                if db.scalar(select(Company.id).limit(1)) is not None:
                    print("Database already has data - demo seed skipped.")
                    return
            finally:
                db.close()
        args.reset = True
    if not args.reset:
        print("seed-demo rebuilds the demo database. Re-run with --reset to confirm (all data is deleted).")
        sys.exit(1)
    seed.run(reset=True)
    _stamp_head()
    print("Demo data loaded. Log in as admin / Admin@123 (see README for all demo users).")


def _stamp_head() -> None:
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    command.stamp(cfg, "head")


def create_admin(args) -> None:
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Role, User
    from app.security.auth import hash_password, password_problems
    from app.seed import seed_core
    probs = password_problems(args.password)
    if probs:
        print("Password needs " + ", ".join(probs))
        sys.exit(1)
    db = SessionLocal()
    try:
        seed_core(db)
        role = db.scalar(select(Role).where(Role.code == "ADMIN"))
        u = db.scalar(select(User).where(User.username == args.username.lower()))
        if u:
            u.password_hash = hash_password(args.password)
            u.role = role
            u.is_active = True
        else:
            db.add(User(username=args.username.lower(), full_name=args.full_name, role=role,
                        password_hash=hash_password(args.password), all_companies=True, all_cfas=True))
        db.commit()
        print(f"Administrator '{args.username}' ready.")
    finally:
        db.close()


def sync(args) -> None:
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.integrations.runner import run_sync
    from app.models import ERPSystem
    db = SessionLocal()
    try:
        stmt = select(ERPSystem).where(ERPSystem.is_active.is_(True))
        if args.erp:
            stmt = stmt.where(ERPSystem.code == args.erp.upper())
        elif args.all:
            stmt = stmt.where(ERPSystem.auto_sync.is_(True))
        else:
            print("Use --erp CODE or --all")
            sys.exit(1)
        for erp in db.scalars(stmt):
            run = run_sync(db, erp, None, trigger="SCHEDULED")
            db.commit()
            print(f"{erp.code}: {run.status} - {(run.message or '').splitlines()[0] if run.message else ''}")
    finally:
        db.close()


def backup(args) -> None:
    s = get_settings()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out or s.backup_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if s.is_sqlite:
        import sqlite3
        src_path = s.database_url.replace("sqlite:///", "")
        dest = out_dir / f"ctdms-{stamp}.sqlite3"
        src = sqlite3.connect(src_path)
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        print(f"SQLite backup written: {dest}")
    else:
        dest = out_dir / f"ctdms-{stamp}.dump"
        url = s.database_url.replace("postgresql+psycopg://", "postgresql://")
        if not shutil.which("pg_dump"):
            print("pg_dump not found on PATH")
            sys.exit(1)
        subprocess.run(["pg_dump", "--format=custom", "--no-owner", f"--file={dest}", url], check=True)
        print(f"PostgreSQL backup written: {dest}")
    keep = args.keep
    backups = sorted(out_dir.glob("ctdms-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[keep:]:
        old.unlink()
    print(f"Retention: kept latest {keep} backups")


def check(args) -> None:
    s = get_settings()
    problems = []
    if s.secret_key.startswith("change-me"):
        problems.append("CTDMS_SECRET_KEY is the default - set a long random value")
    if s.is_production and not s.session_cookie_secure:
        problems.append("CTDMS_SESSION_COOKIE_SECURE should be true behind HTTPS")
    if s.is_production and s.show_demo_logins:
        problems.append("CTDMS_SHOW_DEMO_LOGINS should be false in production")
    if s.is_production and s.is_sqlite:
        problems.append("Use PostgreSQL in production (CTDMS_DATABASE_URL)")
    print(f"Environment: {s.environment}  DB: {s.database_url.split('@')[-1]}")
    for p in problems:
        print(" - WARNING:", p)
    if not problems:
        print("Configuration OK")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="ctdms")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db").set_defaults(fn=init_db)
    sp = sub.add_parser("seed-demo")
    sp.add_argument("--reset", action="store_true")
    sp.add_argument("--if-empty", action="store_true", help="seed only when the database has no companies")
    sp.set_defaults(fn=seed_demo)
    sp = sub.add_parser("create-admin")
    sp.add_argument("--username", required=True)
    sp.add_argument("--password", required=True)
    sp.add_argument("--full-name", default="Administrator")
    sp.set_defaults(fn=create_admin)
    sp = sub.add_parser("sync")
    sp.add_argument("--erp")
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(fn=sync)
    sp = sub.add_parser("backup")
    sp.add_argument("--out")
    sp.add_argument("--keep", type=int, default=30)
    sp.set_defaults(fn=backup)
    sub.add_parser("check").set_defaults(fn=check)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
