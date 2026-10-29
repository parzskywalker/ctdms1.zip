# Architecture

## Stack (and why)

| Layer | Choice | Reason |
|---|---|---|
| Backend | Python 3.11, **FastAPI**, SQLAlchemy 2, Alembic | One codebase serves the HTML UI and the REST API; typed, fast, widely known; no vendor lock-in |
| Database | **PostgreSQL 16** in production; SQLite for demo / single PC | Relational, transactional, free; the same code runs on both (tested on both) |
| UI | Server-rendered Jinja2 + ~300 lines of vanilla JS, Chart.js vendored locally | Few moving parts, fast on low-end warehouse PCs, works on a LAN without internet, tablet-usable |
| PDF / Excel | ReportLab, openpyxl | Pure Python, no system libraries needed |
| Auth | bcrypt passwords, server-side sessions (signed cookie holds only a random token), JWT for the API, per-ERP API keys | |
| Deployment | Docker Compose (Postgres + app + backup + optional Caddy HTTPS / scheduler) or systemd | Runs on any Linux VM or on-prem server you own |

## Module map

```
                 ┌──────────── ERP sources ────────────┐
                 │ MARG  SAP  CBO  Intrect  other      │
                 └──┬──────┬──────┬──────────┬─────────┘
   Excel/CSV upload │ REST │ push │ adapters │ (API / OData / IDoc / DB / watched folder)
                    ▼      ▼      ▼          ▼
            ┌──────────────────────────────────────────┐
            │  import_service  (one pipeline for all)  │  map columns → resolve codes (ERP mappings)
            │  batches · rows · templates · errors     │  → validate → duplicates → commit / correct / retry
            └───────────────┬──────────────────────────┘
                            ▼
   rate_service ──► freight_engine ──► invoice_service ──► dispatch_service ──► recon_service
   (versions,       (priority lookup,   (status derivation,  (verify → slip →     (bills, matching,
    maker-checker)   RATE MISSING)       overrides, audit)    print → complete)    variance, approval)
                            │                                     │
                            └────────────► reports / dashboard ◄──┘  (SQL GROUP BY, scoped per user)
                                                 │
                                    web (HTML)   │   api/v1 (JSON)   exporters (xlsx/csv/pdf)
```

Everything that changes business data lives in `app/services/*`; both the HTML routes (`app/web`) and the REST
API (`app/api/v1.py`) call the same functions, so rules cannot be bypassed through one channel.

## Freight engine

`Transportation expense = boxes × applicable rate per box`, rounded half-up to paise.

Lookup (only APPROVED rates effective on the **invoice date**):

1. **Customer-specific** — Company + CFA + Customer + Location
2. **Standard** — Company + CFA + Location
3. **Company-wide** — Company + Location (rate defined for any CFA)
4. **Default** — only if explicitly configured (`is_default`), CFA-specific default before company default

No match → `RATE_MISSING`; two matches at the same level → `RATE_CONFLICT` (both block dispatch). The engine never
falls back to zero or a neighbouring location.

The rate id, level, rate and amount are **copied onto the invoice** when calculated. Later rate changes never
re-price an invoice; only `RATE_MISSING` invoices are re-priced automatically when a matching rate is approved, and
an authorised user can re-run the lookup on an open invoice (audited). Once the slip is generated the freight is
frozen.

Rate versioning: approved rates are never edited. A change is a new version with a later *Effective From*; when it
is approved the previous version is end-dated to the day before. A version that would start on/before an existing
approved version is refused. Maker-checker: the creator cannot approve (configurable).

## Dispatch state machine

```
             ┌── RATE_MISSING ──┐     (resolved by rate approval / authorised override)
 import ──►  PENDING ──verify──► VERIFIED ──generate──► SLIP_GENERATED ──print──► SLIP_PRINTED ──complete──► DISPATCHED
             └── EXCEPTION ─────┘ (boxes missing, override pending, rate conflict)      │              │
                                         ▲                                  void slip (reason)          │
                                         └───────────────────────────────────────┘                     │
 any open state ──cancel (reason)──► CANCELLED ◄──── (administrator only) ─────────────────────────────┘
```

* **Mandatory control**: the gatepass must be PRINTED - after its case labels - and every case scanned at the gate before
  the goods can leave (v1.7.2: no setting; the old `dispatch.complete_requires` is gone). Goods are marked gone out ONLY by
  `dispatch_service.gate_scan_out` (permission `gate.scan`): there is no complete / gate-out function or button. Enforced in
  the service layer; blocked attempts are audited (`GATE_OUT_BLOCKED`).
* The slip stores an immutable **snapshot**; reprints render the snapshot and are labelled "REPRINT #n".
  Every print (first and reprints, format, user, IP, reason) is in `slip_print_logs`.
* Dispatch number: `DS/<CFA>/<FY>/<5-digit sequence>` from a row-locked sequence table (safe under concurrency).
* Voiding a slip (reason required, authorised role) unlocks the details; regeneration issues a new number.
* Optimistic locking (`invoices.version`) prevents two operators overwriting each other.

## Security

* Role-based permissions (32 permission codes, 7 default roles, editable matrix) + **data scope** per user
  (companies / CFAs) applied to every invoice query, report and API call.
* bcrypt password hashes, lock-out after 5 failures, password policy, forced change on first login / reset.
* Server-side sessions (revocable, idle timeout); cookie is signed, `HttpOnly`, `SameSite=Lax`, `Secure` behind
  HTTPS. CSRF token on every form; session-authenticated API writes need `X-CSRF-Token`.
* JWT bearer tokens for integrations (invalidated when the password changes); per-ERP API keys stored as SHA-256.
* Security headers (HSTS behind HTTPS, nosniff, frame options, referrer policy), request IDs, rotating logs.
* Audit trail: every important action with user, timestamp, module, record, old → new value, reason, IP and
  session reference. The audit table is append-only from the application's point of view.

## Scalability

3,000 invoices/month ≈ 36,000/year — small for PostgreSQL. Reports aggregate in SQL (`GROUP BY`) with indexes on
status/date, company/CFA/date, dispatch date, LR, customer and location. The app is stateless (sessions in the DB),
so more Gunicorn workers or more containers can be added behind the proxy. New companies, CFAs, ERPs, locations
and transporters are data, not code.
