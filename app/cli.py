"""Command-line tasks.

    python -m app.cli init-db            # run migrations (creates tables) + roles/settings
    python -m app.cli seed-demo --reset  # full demo dataset (DESTROYS existing data)
    python -m app.cli prepare-demo       # used by start_demo: demo data only if the database is EMPTY
    python -m app.cli create-admin --username admin --password '...' [--promote]
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


def prepare_demo(args) -> None:
    """Run by start_demo.bat / start_demo.sh on every start.

    The decision is based on what is INSIDE the database, not on whether the file exists (QA-09: an existing
    but empty ctdms.db used to be started as-is, so the demo logins shown on the login page failed with 401).
      * empty / brand-new database  -> load the demo data
      * database with data          -> never reset; only bring the table structure up to date
      * data but no active user     -> stop and explain how to create an administrator
    Exit codes: 0 ready, 2 needs attention (message printed); anything else is an unexpected error.
    """
    from sqlalchemy import func, inspect, select, text

    from app import seed
    from app.db import Base, SessionLocal, engine
    from app.models import Invoice, Role, User
    from app.security.auth import verify_password

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    # Rows in these tables do not count as business data (created automatically, or failed-login noise).
    housekeeping = {"roles", "app_settings", "settings", "audit_logs", "user_sessions", "alembic_version"}
    if {"users", "companies", "invoices"} <= tables:
        with engine.connect() as con:
            filled = [t.name for t in Base.metadata.sorted_tables if t.name in tables and t.name not in housekeeping
                      and con.execute(text(f'SELECT 1 FROM "{t.name}" LIMIT 1')).first() is not None]
        empty = not filled
    else:
        with engine.connect() as con:
            foreign = [t for t in tables - {"alembic_version", "sqlite_sequence"}
                       if con.execute(text(f'SELECT 1 FROM "{t}" LIMIT 1')).first() is not None]
        if foreign:
            print(f"This database contains other data ({', '.join(sorted(foreign)[:5])}) and does not look like a "
                  "CTDMS database. Nothing was changed. Check CTDMS_DATABASE_URL / the data folder.")
            sys.exit(2)
        empty = True

    if empty:
        print("Database is empty - loading the demo data (one time, takes about a minute)...")
        seed.run(reset=True)
        _stamp_head()
        print("Demo data loaded. Log in as admin / Admin@123 (see README for all demo users).")
        return

    # Existing data: keep it. FIRST bring the table structure up to date (no-op when already current) - the
    # checks below use the current models, e.g. v1.2 added users.tokens_valid_after.
    if "alembic_version" in tables:
        _alembic_upgrade()
    else:
        _stamp_head()  # created directly from the models by this version
    engine.dispose()
    db = SessionLocal()
    try:
        n_active = db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True)))
        n_inv = db.scalar(select(func.count()).select_from(Invoice))
        active = list(db.scalars(select(User).join(Role).where(User.is_active.is_(True))))
        admins = [u for u in active if u.can("admin.users")]
        demo_pw = any(u.username == "admin" and verify_password("Admin@123", u.password_hash) for u in admins)
    finally:
        db.close()
    if n_active == 0:
        print(f"The database has data ({n_inv:,} invoices) but no active user account, so nobody can log in.\n"
              "Nothing was changed. Create an administrator, then start again:\n"
              "    python -m app.cli create-admin --username admin --password <a new strong password>")
        sys.exit(2)
    if not admins:
        print("Note: no active administrator account - use 'python -m app.cli create-admin' to add one.")
    print(f"Database ready: {n_active} active users, {n_inv:,} invoices. Existing data kept as it is.")
    if demo_pw:
        print("Demo login: admin / Admin@123  (change this password before using real data)")


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
    from app.models import Role, User, utcnow
    from app.security.auth import hash_password, password_problems, sign_out_everywhere
    from app.seed import seed_core
    from app.services import audit
    username = args.username.strip().lower()
    probs = password_problems(args.password, username)
    if probs:
        print("Password needs " + ", ".join(probs))
        sys.exit(1)
    db = SessionLocal()
    try:
        seed_core(db)
        role = db.scalar(select(Role).where(Role.code == "ADMIN"))
        u = db.scalar(select(User).where(User.username == username))
        if u:
            # Account recovery. Promotion must be deliberate, and every existing login of the account (browser
            # sessions + API tokens) is ended, so whoever held an old session cannot keep - or gain - access.
            if (not u.role or u.role.code != "ADMIN") and not args.promote:
                print(f"User '{username}' already exists with role {u.role.code if u.role else '-'}. Nothing changed.\n"
                      f"To make this user an administrator, re-run with --promote.")
                sys.exit(1)
            promoted = not u.role or u.role.code != "ADMIN"
            u.password_hash = hash_password(args.password)
            u.password_changed_at = utcnow()
            u.role = role
            u.is_active = True
            u.failed_logins = 0
            u.locked_until = None
            sign_out_everywhere(db, u)  # all browser sessions + every API token issued before now
            audit.log(db, None, "ADMIN_RECOVERY", "SECURITY", record_type="User", record_id=u.id, ref=u.username,
                      details={"promoted": promoted, "via": "command line"})
            msg = f"Administrator '{username}' recovered: password reset, all existing sign-ins ended"
            print(msg + (", promoted to ADMIN." if promoted else "."))
        else:
            u = User(username=username, full_name=args.full_name, role=role,
                     password_hash=hash_password(args.password), all_companies=True, all_cfas=True)
            db.add(u)
            db.flush()
            audit.log(db, None, "USER_CREATED", "SECURITY", record_type="User", record_id=u.id, ref=u.username,
                      details={"role": "ADMIN", "via": "command line"})
            print(f"Administrator '{username}' created.")
        db.commit()
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


def inbox(args) -> None:
    """Import ERP exports waiting in the drop folder (data/inbox) - for cron / Task Scheduler setups."""
    from app.db import SessionLocal
    from app.services import inbox_service
    with SessionLocal() as db:
        results = inbox_service.scan(db)
    if not results:
        print(f"Nothing waiting in {inbox_service.inbox_dir()}")
    for r in results:
        print(("OK    " if r["ok"] else "FAILED ") + r["message"])


def notify(args) -> None:
    """Send queued WhatsApp / SMS dispatch alerts now - for cron / Task Scheduler setups."""
    from app.db import SessionLocal
    from app.services import notify_service
    with SessionLocal() as db:
        if notify_service.provider(db) == "OFF":
            print("Notifications are switched off (System Settings > notify.provider)")
            return
        res = notify_service.send_due(db, limit=args.limit)
    print(f"Sent {res['sent']}, will retry {res['retry']}, failed {res['failed']}")


def reports_mail(args) -> None:
    """E-mail the report subscriptions that are due - for cron / Task Scheduler setups."""
    from app.db import SessionLocal
    from app.services import subscription_service
    if not subscription_service.smtp_ready():
        print("E-mail is not set up (CTDMS_SMTP_HOST / CTDMS_SMTP_FROM in .env)")
        return
    with SessionLocal() as db:
        res = subscription_service.send_due(db)
    print(f"Sent {res['sent']}, failed {res['failed']}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="ctdms")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db").set_defaults(fn=init_db)
    sp = sub.add_parser("seed-demo")
    sp.add_argument("--reset", action="store_true")
    sp.add_argument("--if-empty", action="store_true", help="seed only when the database has no companies")
    sp.set_defaults(fn=seed_demo)
    sub.add_parser("prepare-demo", help="used by start_demo: demo data only into an EMPTY database").set_defaults(
        fn=prepare_demo)
    sp = sub.add_parser("create-admin")
    sp.add_argument("--username", required=True)
    sp.add_argument("--password", required=True)
    sp.add_argument("--full-name", default="Administrator")
    sp.add_argument("--promote", action="store_true", help="allow turning an existing non-admin user into an admin")
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
    sub.add_parser("inbox", help="import ERP exports waiting in data/inbox now").set_defaults(fn=inbox)
    sp = sub.add_parser("notify", help="send queued WhatsApp / SMS dispatch alerts now")
    sp.add_argument("--limit", type=int, default=200)
    sp.set_defaults(fn=notify)
    sub.add_parser("reports-mail", help="e-mail the report subscriptions that are due").set_defaults(fn=reports_mail)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
