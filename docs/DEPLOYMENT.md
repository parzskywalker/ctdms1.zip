# Deployment

You own everything: source code, the PostgreSQL database, backups and exports. The stack runs on any Linux server
or cloud VM (AWS / Azure / GCP / DigitalOcean / on-prem). Suggested size for 3,000 invoices/month and ~20 users:
2 vCPU, 4 GB RAM, 40 GB disk.

## Option A — Docker Compose (recommended)

```bash
git clone <your repo> /opt/ctdms && cd /opt/ctdms
cp .env.example .env
#  edit .env:  CTDMS_SECRET_KEY (long random), POSTGRES_PASSWORD, CTDMS_DOMAIN,
#              CTDMS_SESSION_COOKIE_SECURE=true (with HTTPS), CTDMS_SHOW_DEMO_LOGINS=false
docker compose up -d --build                       # db + app + daily backup
docker compose --profile https up -d               # + Caddy with automatic Let's Encrypt HTTPS
docker compose --profile sync up -d                # + scheduled ERP pulls every SYNC_INTERVAL_SECONDS
docker compose exec app python -m app.cli create-admin --username youradmin --password 'StrongPass#2026'
```

* First start runs migrations automatically (`deploy/entrypoint.sh`). `CTDMS_SEED_DEMO=true` loads demo data only
  if the database is empty — use it for a demo instance, never for production.
* Backups land in `./backups` (daily, 30-day retention).
* Upgrade: `git pull && docker compose up -d --build` (migrations run on start).

## Option B — plain Linux server

```bash
sudo apt install python3.11-venv postgresql
sudo -u postgres createuser -P ctdms && sudo -u postgres createdb -O ctdms ctdms
sudo useradd -m ctdms && sudo mkdir /opt/ctdms && sudo chown ctdms /opt/ctdms   # copy code here
cd /opt/ctdms && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env    # set CTDMS_DATABASE_URL=postgresql+psycopg://ctdms:<pwd>@localhost/ctdms etc.
.venv/bin/python -m app.cli init-db
.venv/bin/python -m app.cli create-admin --username youradmin --password '...'
sudo cp deploy/ctdms.service /etc/systemd/system/ && sudo systemctl enable --now ctdms
# reverse proxy with TLS: deploy/nginx.conf (or Caddy); schedules: deploy/crontab.example
```

## Option C — single Windows / Linux PC (pilot)

SQLite, no server needed: `pip install -r requirements.txt`, `python -m app.cli init-db`,
`python -m app.cli create-admin …`, `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`. Other PCs on the
LAN open `http://<pc-ip>:8000`. Move to PostgreSQL before multi-site use (SQLite serialises writes).

## Locked out / forgot the admin password

`python -m app.cli create-admin --username admin --password '<new strong password>'` resets that administrator's
password, clears a lockout and **signs the account out everywhere** (all browser sessions and API tokens stop
working). To turn an existing non-admin user into an administrator add `--promote` — without it the command
refuses, so nobody is promoted by accident. Every recovery is written to the audit log (ADMIN_RECOVERY).

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `CTDMS_ENVIRONMENT` | development | `production` enables production warnings (`python -m app.cli check`) |
| `CTDMS_SECRET_KEY` | *(placeholder)* | signs session cookies and JWTs — **must** be set (production refuses to start with the placeholder or < 32 chars) |
| `CTDMS_DATABASE_URL` | SQLite file in `data/` | `postgresql+psycopg://user:pwd@host:5432/db` |
| `CTDMS_SESSION_COOKIE_SECURE` | false | true behind HTTPS |
| `CTDMS_SESSION_IDLE_MINUTES` | 480 | idle session timeout |
| `CTDMS_JWT_EXPIRE_MINUTES` | 720 | API token lifetime |
| `CTDMS_MAX_UPLOAD_MB` | 20 | size limit for every import (invoices, rates, transporter bills); max 100,000 rows |
| `CTDMS_SHOW_DEMO_LOGINS` | on in development, off in production | demo credential helper on the login page |
| `CTDMS_API_DOCS_PUBLIC` | on in development, off in production | Swagger UI at `/api/docs` |
| `FORWARDED_ALLOW_IPS` | 127.0.0.1 | proxies trusted to pass the client IP (audit log); with the Caddy profile use `172.16.0.0/12` |
| `CTDMS_SEED_DEMO` | false | Docker: seed demo data when DB is empty |
| `CTDMS_TIMEZONE` | Asia/Kolkata | display time zone |
| `GUNICORN_WORKERS` | 2×CPU+1 (≤8) | web workers |

Business controls (print-before-complete, approvals, tolerances, LR/vehicle mandatory, reprint reasons, dispatch
number prefix, organisation name on slips) are in *Administration › System Settings*, not env vars.

## Go-live checklist

- [ ] Strong `CTDMS_SECRET_KEY`, `CTDMS_SHOW_DEMO_LOGINS=false`, HTTPS + `CTDMS_SESSION_COOKIE_SECURE=true`
- [ ] Fresh database (no demo data); real admin created; demo users absent
- [ ] Masters loaded: companies, CFAs, locations, customers (bulk via import corrections or CSV), transporters
- [ ] Freight rate card uploaded (*Freight Rates › Bulk upload*) and approved by a checker
- [ ] ERP code mappings for each ERP; one import template per export layout; a test import per ERP
- [ ] Users with correct role and company/CFA scope
- [ ] Backups verified by a test restore; off-site copy configured
- [ ] Slip printer tested for A5/A4 (browser print dialog, margins "default")
