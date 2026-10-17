# CTDMS — Central Transportation Expense & Dispatch Management System

A web application that sits **above** MARG, SAP, CBO, Intrect (and any other billing system) as a central
transportation and dispatch layer for a pharmaceutical distribution / CFA business.

```
ERP invoice ─► central invoice DB ─► Company ─► Customer ─► Depot ─► Location ─► Boxes × Rate/Box
           ─► Transportation expense ─► Pending Dispatch ─► Transporter / LR / Vehicle
           ─► Dispatch Slip (generate + print, mandatory) ─► Complete Dispatch ─► MIS & reconciliation
```

**v1.6** (see CHANGELOG): one-time **Initial Master Upload** workbook, **Depots** instead of CFA (several per city),
configurable **Customer Types**, the **Invoice Recognition Code** beside the unchanged ERP invoice number
(e.g. `ABC-RET-C025-GUMA-001245`), and **Local Autos** charged per trip (one amount per trip, shared by boxes) or per box.
The v1.6 guide (video, transcript, manual) is in `docs/video_manual/v1.6/`.

It does not replace the ERPs. Invoices come in by Excel/CSV import, REST push or ERP adapters, are normalised into one
schema, priced with an effective-dated freight-rate engine, dispatched through a controlled workflow and reported in
consolidated MIS.

---

## Quick start (local demo, ~2 minutes)

Requirements: Python 3.11+. Easiest: double-click `start_demo.bat` (Windows) or run `./start_demo.sh`
(macOS/Linux) — it installs everything, loads the demo data **only into an empty database** (existing data is
never reset) and starts the app. If something is missing (Python, packages) it stops with a clear message. Manually:

```bash
cd ctdms
python3 -m pip install -r requirements.txt
python3 -m app.cli seed-demo --reset          # SQLite demo DB with ~300 invoices over 3 months
python3 -m uvicorn app.main:app --port 8000   # open http://127.0.0.1:8000
```

With Docker (PostgreSQL + app + daily backups):

```bash
cp .env.example .env          # set CTDMS_SECRET_KEY, POSTGRES_PASSWORD; CTDMS_SEED_DEMO=true for demo data
docker compose up -d --build  # http://localhost:8000
docker compose --profile https up -d   # + automatic HTTPS for CTDMS_DOMAIN (Caddy)
```

### Demo users

| Username | Password | Role | Data scope |
|---|---|---|---|
| `admin` | `Admin@123` | Administrator | all |
| `management` | `Demo@123` | Management | all companies / CFAs |
| `accounts` | `Demo@123` | Accounts | all |
| `cfa.raipur` | `Demo@123` | CFA Manager | Raipur CFA |
| `operator.raipur` | `Demo@123` | Dispatch Operator | Raipur CFA |
| `operator.bilaspur` | `Demo@123` | Dispatch Operator | Bilaspur CFA |
| `operator.durg` | `Demo@123` | Dispatch Operator | Durg CFA |
| `checker` | `Demo@123` | Checker / Approver | all |
| `viewer` | `Demo@123` | Read-only Viewer (reports, no export) | all |

The login page lists them (click to fill) while `CTDMS_SHOW_DEMO_LOGINS=true`. **Change or remove them before go-live.**

---

## What is in the demo data

Generated through the real services (import pipeline, freight engine, dispatch workflow) with a simulated clock, so
it looks like three months of operation (ending this evening, or yesterday evening if you seed before 19:30 — no demo
event is ever timestamped in the future):

* 4 principals — Company A (SAP), Company B (MARG), Company C (CBO), Own Distribution (Intrect); 3 CFAs (Raipur,
  Bilaspur, Durg); 8 locations (Raipur, Bilaspur, Durg, Bhilai, Korba, Nagpur, Rajnandgaon, Raigarh);
  10 customers, each with a **different code in every ERP** mapped to one central record; 4 transporters.
* ~40 freight rates: the brief's examples (Company A / Raipur / Bilaspur ₹8, Durg ₹6, Nagpur ₹12; Company B ₹9 / ₹7 /
  ₹14), a customer-specific rate (ABC Pharma ₹7 instead of ₹8), a company-wide Korba rate, an explicit default rate,
  a **rate revision** (Company A / Raipur / Durg ₹6.00 → ₹6.50 from the 1st of this month — last month's invoices keep
  ₹6.00) and a pending rate proposal awaiting approval.
* Daily import batches from each ERP's own file layout; today's MARG file contains deliberate problems (unknown
  customer, code typo, unknown location, blank boxes, a duplicate) to demonstrate validation and correction.
* ~260 completed dispatches with slips, prints and reprints; open invoices in every stage; Company C → Korba
  invoices stuck as **RATE MISSING**; override requests (approved, pending, applied directly); cancellations; a voided
  and regenerated slip.
* Transporter bills: paid, approved, matched, and one with variances + a line the system cannot find.

Sample files for trying the import yourself are in `sample_data/` (also downloadable from *Operations › Invoice
Import*).

---

## Screenshots

| | |
|---|---|
| ![Dashboard](docs/screenshots/00_dashboard.png) Management dashboard | ![Pending dispatch](docs/screenshots/03_pending_dispatch.png) Pending Dispatch (operator view) |
| ![Dispatch entry](docs/screenshots/04_dispatch_entry_print_required.png) Completion blocked until slip is printed | ![Slip](docs/screenshots/05_dispatch_slip_A5.png) Dispatch slip (A5 print view) |
| ![Import](docs/screenshots/02_invoice_import_corrections.png) Import validation & correction | ![MIS](docs/screenshots/09_transportation_mis_drilldown.png) Drill-down MIS |
| ![Rates](docs/screenshots/06_freight_rates.png) Freight rate master | ![Recon](docs/screenshots/07_reconciliation_bill.png) Transporter bill reconciliation |

---

## Main screens

| Menu | What it does |
|---|---|
| Dashboard | KPIs, company/CFA/customer/location charts, monthly trend, calculated vs actual, pending ageing |
| Operations › Invoice Import | Upload Excel/CSV → preview → map columns (reusable templates) → validate → import → correct & retry |
| Invoice Register | Central invoice database with filters, drill to invoice detail (freight trace, overrides, slips, audit) |
| Pending Dispatch | Fast warehouse queue (scan/enter invoice number → opens dispatch), bulk dispatch/print/complete |
| Dispatch entry | Verify → Generate Slip → Print (A4/A5/PDF) → Complete, with the mandatory print-before-complete control |
| Freight Exceptions | Rate-missing combinations (one click to add the rate) and every blocked invoice |
| Approvals | Maker-checker queue: overrides, rate changes, transporter bills |
| Reconciliation | Transporter bills: link dispatches / import statement, auto-match, variance, approve, pay |
| Masters | Companies, Customers (ERP codes, duplicates, merge), CFAs, Locations, Freight Rates (versions, rate finder), Transporters, ERP Mappings |
| Reports | 18 reports + drill-down Transportation MIS; Excel / CSV / PDF / print |
| Administration | Users (company/CFA scope), Roles (permission matrix), System Settings (controls), Audit Log, Integration Settings |

REST API documentation (OpenAPI) is served at `/api/docs`.

---

## Tests

```bash
python3 -m pytest -q                                   # SQLite (97 tests, ~2 min)
CTDMS_TEST_DATABASE_URL=postgresql+psycopg://user:pwd@localhost/ctdms_test python3 -m pytest -q   # PostgreSQL
```

`tests/test_acceptance.py` maps one-to-one to the 27-point acceptance list — see
[docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) for the matching click-through demo script.
`tests/test_qa_fixes.py` and `tests/test_v12_fixes.py` hold the regression tests for every issue found in the two QA rounds
(IDs match `docs/CTDMS_QA_Report.pdf`).

---

## Documentation

| Document | Contents |
|---|---|
| [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) | The 27 acceptance points: where to see each one in the UI and which test proves it |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Stack, modules, data flow, freight engine, dispatch state machine, security |
| [docs/DATABASE.md](docs/DATABASE.md) | Tables, keys, constraints, indexes, migrations, backup & restore |
| [docs/ERP_INTEGRATION.md](docs/ERP_INTEGRATION.md) | Adapter framework, push API, write-back, and what is needed from MARG / SAP / CBO / Intrect |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker, plain-server install, HTTPS, configuration, backups, scaling, go-live checklist |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | Day-to-day procedures for warehouse, CFA manager, checker and accounts |
| [docs/STATUS.md](docs/STATUS.md) | Assumptions, completed / pending functionality, known limitations |
| [CHANGELOG.md](CHANGELOG.md) | What changed in each version (v1.1 = QA fixes) |
| docs/CTDMS_QA_Report.pdf | v1.0 QA report: findings with severity, reproduction steps, screenshots and fixes |
| docs/CTDMS_User_Guide_v1.1.pdf | Illustrated user guide: update / fix steps first, then how to use every screen |

## Project layout

```
app/
  main.py            FastAPI app, middleware, error handling
  config.py          environment-based settings (CTDMS_* variables)
  models/            SQLAlchemy models (30 tables)
  security/          auth (sessions, JWT, bcrypt, CSRF), permission catalogue & default roles
  services/          business logic: freight_engine, rate_service, import_service, invoice_service,
                     dispatch_service, recon_service, reports, dashboard, pdf_service, exporters, audit
  integrations/      ERP adapter framework (MARG, SAP OData/IDoc, CBO, Intrect, REST, DB, file-drop, demo)
  web/               HTML routes (server-rendered Jinja2)
  api/v1.py          REST API
  templates/, static/  UI (no CDN dependencies - works offline in a warehouse LAN)
  seed.py, cli.py    demo data generator and command-line tasks
migrations/          Alembic migrations
deploy/              entrypoint, gunicorn, Caddy/nginx, systemd, backup, cron
sample_data/         sample ERP exports, rate template, transporter statement
tests/               pytest suite
```
