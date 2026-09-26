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

## Assumptions made (change via settings / masters where noted)

1. One invoice = one dispatch = one dispatch slip. Several invoices on one truck are handled by bulk dispatch
   (one slip per invoice, same vehicle). A consolidated trip-sheet is a possible extension.
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
* Optional: consolidated trip sheet (multiple invoices per vehicle slip), e-way bill / LR number integration with
  transporters, SMS/WhatsApp dispatch notification to customers, barcode label printing, per-transporter rate
  cards for comparing transporter quotes, e-mail report subscriptions, SSO (Azure AD / Google).

## Known limitations

* ERP adapters other than file/demo have not been run against real MARG/SAP/CBO/Intrect systems (no credentials yet).
* The Docker image was validated (compose config, entrypoint, gunicorn, migrations, backups) but not built in the
  development sandbox, because Docker Hub was not reachable there. Build it once on the target server.
* SQLite serialises writes — fine for a single site or demo; use PostgreSQL for multi-user production.
* Print confirmation is software-level (see assumption 6).
* Report screens show up to 2,000 rows (exports contain everything); invoice-level drill shows up to 1,000 rows per node.
* UI is English only; desktop-first and tablet-usable, not optimised for phones.
