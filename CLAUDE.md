# CTDMS — notes for Claude Code

Central Transport & Dispatch Management System for a pharma CFA (Tulsyan). FastAPI + SQLAlchemy + Jinja2,
SQLite locally / PostgreSQL in production. Runs on the office's internal server; users sign in with
username / password managed under Administration › Users.

## Run and test (Windows)
- Start: `start_demo.bat` (creates `.venv`, installs packages, upgrades the database, opens http://127.0.0.1:8000).
- Tests: `.venv\Scripts\python -m pytest -q` — all must pass before committing.
- Database changes need an Alembic migration in `migrations/versions/` (next number after the highest one) and the
  head number in `tests/test_v12_fixes.py::test_upgrade_from_v11_database_keeps_data`.

## Rules
- No paid services. AI bill reading and WhatsApp / SMS are locked behind `CTDMS_PAID_SERVICES` (default false);
  do not add features that need a paid API or key.
- No Google / Microsoft sign-in.
- Every business change is audited (`app/services/audit.py`); keep the company / CFA scope checks
  (`app/services/scope.py`) on new queries and screens.
- Never commit `.env`, `data/` or `.venv/` (already in `.gitignore`).

## After every iteration
1. Run the tests.
2. Add a short entry at the top of `CHANGELOG.md`.
3. Commit with a clear message and push: `git add -A`, `git commit -m "..."`, `git push`.
