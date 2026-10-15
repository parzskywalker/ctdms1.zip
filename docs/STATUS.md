# Status, assumptions and limitations

## Completed (MVP, all six phases)

| Phase | Delivered |
|---|---|
| 1 Foundation | FastAPI app, 30-table schema + Alembic migration, authentication, 7 roles / 32 permissions, company/CFA data scope, masters for companies, CFAs, customers, locations, transporters, freight rates |
| 2 Invoice & freight | Normalised invoice model, Excel/CSV/XLS import with preview, column mapping, reusable templates, validation, duplicate detection (DB, in-file, same file, same source id), missing customer/location/rate detection, correction & retry, import log; effective-dated rate engine with 4-level priority, RATE MISSING / RATE CONFLICT exceptions |
| 3 Dispatch | Pending queue (scan-to-open, tabs, bulk), dispatch entry, transporter / vehicle / LR / date / time, status flow, dispatch slip with unique number, HTML print A4/A5 + PDF, configurable print-before-complete control, print & reprint history, void & regenerate |
| 4 Reporting | Management dashboard (12 KPIs, 7 charts + summary table), drill-down Transportation MIS, 18 reports with filters, Excel / CSV / PDF / print |
| 5 Advanced controls | Audit trail (user, time, module, record, old/new, reason, IP, session), manual override workflow with approval, rate maker-checker, cancellation workflow, transporter bill reconciliation with auto-matching, tolerance, duplicate-billing detection, approval & payment, variance analysis |
| 6 ERP framework | Adapter interface + registry; MARG, SAP OData, SAP IDoc, CBO, Intrect, generic REST, DB and watched-folder adapters; per-ERP field mapping and code mapping; push API with API keys; dispatch write-back API; sync scheduler; demo adapter |
| Other | REST API (OpenAPI at `/api/docs`), CLI (migrate, seed, admin, sync, backup, check), Docker Compose with backups and HTTPS, 26 automated tests passing on SQLite and PostgreSQL, full data export (ZIP) |

## v1.3 (management feedback) — delivered

E-invoice QR capture, drop-folder auto import with layout recognition, item-wise (SKU) import, multi-invoice
gatepasses with Transporter / Direct pickup / Outside auto / Self modes, LR entered later, gate check, transporter
rate cards, outside-auto master, customer types, scanned transporter bills with AI reading, consignment-level
cross-check with irregularity report and rate review, seven new reports. See `CHANGELOG.md`.

### v1.3 assumptions (please confirm)

* **Modes**: *Transporter* = we hand the goods to the transporter (regular, or via an outside auto whose charge is
  added); *Direct* = the transporter collects at our warehouse (its direct-pickup extra applies); *Outside auto* =
  an auto delivers straight to the customer; *Self* = the customer collects (no charge).
* **One gatepass per pickup**, all invoices from one CFA; it may carry several customers. One LR per consignee.
* Freight per case comes from the **transporter's** rate card for the invoice's area on the dispatch date
  (`freight.source = TRANSPORTER`); the principal rate card stays available (`COMPANY`).
* CASH transporters and outside autos are recorded as **paid at dispatch** (editable on the gatepass); a bill
  line for a consignment already paid in cash is blocked.
* The e-invoice QR signature is not verified (needs the IRP public key); the payload is trusted like the paper.
* Bill photos are sent to the Anthropic API only when `CTDMS_ANTHROPIC_API_KEY` is set; extracted lines are always
  checked by a person before they count.

## v1.4.1 / v1.5 — delivered

Add Invoice screen with taxable amount and planned mode / transporter; cost per trip. Proof of delivery, Track
Shipment, customer tracking link, Deliveries Pending, delivery performance reports, e-way bill gate rule, e-invoice
QR signature check, case labels, e-mailed reports, single sign-on, tracking API. See `CHANGELOG.md`.

### v1.5 assumptions (please confirm)

* A consignment (customer on a gatepass) is delivered as a whole; one POD for all its invoices.
* On time = delivered within `delivery.expected_days` (default 4) of gate-out, same for every area.
* The e-way bill rule is off until `gate.eway_required_above` is set; CTDMS records the number, it does not
  generate e-way bills (that needs NIC e-way bill API credentials).
* SSO never creates users; the e-mail on the CTDMS user must match the company account.

## Assumptions made (change via settings / masters where noted)

1. *(v1.0–v1.2)* One invoice = one dispatch = one dispatch slip. **Superseded in v1.3**: a gatepass carries many
   invoices; the one-invoice screens still work and create a one-line gatepass.
2. Invoice uniqueness = company + CFA + financial year (April–March) + invoice number.
3. The rate is chosen by **invoice date** (not dispatch date).
4. Customer-specific rates must name the CFA; a "default" rate is only used if explicitly created.
5. Freight is frozen at slip generation; completed dispatches are never re-priced.
6. "Printed" means the slip was rendered for printing / PDF by the system (a browser cannot confirm the paper
   left the printer). Every such event is logged.
7. Invoices without a box count are accepted as exceptions (setting `import.allow_blank_boxes`).
8. Box changes by operators need checker approval; checkers/admins apply changes directly (still audited).
9. Transporter bill tolerance ₹10 per line and 1 % per bill (settings).
10. Indian number formatting and IST; GST/PAN fields are informational (no GST computation on freight).
11. ERP export layouts in `sample_data/` are representative guesses until real files are provided.
12. The demo organisation name "Demo Pharma Distributors & CFA" on slips is a setting (`org.name`).

## Pending / next steps

* Connect live ERPs (see [ERP_INTEGRATION.md](ERP_INTEGRATION.md) for what each needs) and confirm export layouts.
* Load real masters and the real rate card; train users.
* For v1.5 features: put the mail server, IRP public key and (if wanted) SSO credentials in `.env`; set
  `org.public_url` once the server is reachable from outside.
* Optional next: e-way bill generation through the NIC API, transporter portal / API for LR and POD updates,
  per-area expected transit days, mobile app for the gate / POD photos.

## Known limitations

* ERP adapters other than file/demo have not been run against real MARG/SAP/CBO/Intrect systems (no credentials yet).
* The Docker image's Python part was built and run (v1.5: pip install from the lock file, migrations, gunicorn,
  /health). The OS-package layer (postgresql-client, curl) could not be fetched in the build sandbox - build the
  image once on the target server.
* SQLite serialises writes — fine for a single site or demo; use PostgreSQL for multi-user production.
* Print confirmation is software-level (see assumption 6).
* Report screens show up to 2,000 rows (exports contain everything); invoice-level drill shows up to 1,000 rows per node.
* UI is English only; desktop-first and tablet-usable, not optimised for phones.
