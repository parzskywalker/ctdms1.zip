# Acceptance checklist — demo script

Load the demo (`python3 -m app.cli seed-demo --reset`), start the server and follow the steps. Every item is also
covered by an automated test in `tests/test_acceptance.py` (`T…` = test function).

| # | Requirement | Where to see it (user) | Test |
|---|---|---|---|
| 1 | ≥ 20 imported sample invoices | *Operations › Invoice Import* — import log shows ~180 daily batches from 4 ERPs, ~300 invoices (admin) | `test_01_05_demo_data_breadth` |
| 2 | Multiple companies | *Masters › Companies*: Company A (SAP), B (MARG), C (CBO), Own Distribution (Intrect) | same |
| 3 | Multiple customers | *Masters › Customers* — 10 parties; open *ABC Pharma* to see its 4 ERP codes mapped to one record | same |
| 4 | Multiple CFAs | *Masters › CFAs*: Raipur, Bilaspur, Durg | same |
| 5 | Multiple locations | *Masters › Locations*: 8 locations with route / zone / distance | same |
| 6 | Different rates per company / location | *Masters › Freight Rates* (Current). Company A / Raipur: Bilaspur ₹8, Durg ₹6.50, Nagpur ₹12; Company B: ₹9 / ₹7 / ₹14 | `test_06_…` |
| 7 | Customer-specific rate | Rates list: *Company A / Raipur / ABC Pharma / Bilaspur ₹7* (level 1) beats the ₹8 standard. Try *Freight Rates › Rate finder* with and without customer | `test_07_customer_specific_rate_wins` |
| 8 | Automatic box-wise freight | Any invoice detail: **Boxes × Rate/box = Expense** with the rate record and priority level used | `test_08_automatic_boxwise_freight` |
| 9 | Missing-rate exception | *Operations › Freight Exceptions*: Company C / Raipur / Korba has no rate → invoices **RATE MISSING**, cannot be verified. "Add rate" → approve as `checker` → invoices re-price automatically | `test_09_…` |
| 10 | Pending Dispatch queue | `operator.raipur` lands on *Pending Dispatch* (only Raipur CFA data), tabs Ready / Slip / Exceptions, **OPEN DISPATCH** buttons | `test_10_pending_dispatch_queue` |
| 11 | Transporter selection | Dispatch entry → Transporter dropdown (active transporters with service area) | `test_11_16_…` |
| 12 | LR and vehicle entry | Dispatch entry → Vehicle No (auto upper-case/normalised), LR No, date, time, remarks → *Save draft* / *Verify* | same |
| 13 | Dispatch Slip generation | *Generate Dispatch Slip* → unique number `DS/<CFA>/<FY>/<seq>`; details lock | same |
| 14 | Slip printing / PDF | *Print Slip* (A5/A4 print view opens and prints) or *PDF*; reprints need a reason and are stamped "REPRINT #n (DUPLICATE)" | same |
| 15 | Completion blocked before slip print | Click *Complete Dispatch* before printing → "Dispatch Slip must be generated AND printed…" (attempt audited as COMPLETE_BLOCKED). Rule is configurable in *System Settings* (PRINTED / GENERATED / NONE) | same + `test_configurable_completion_rule` |
| 16 | Dispatch completion | After printing, *Complete Dispatch* → status DISPATCHED, user/time recorded, ERP sync = pending | same |
| 17 | Customer-wise MIS | *Reports › Customer-wise* | `test_17_20_…` |
| 18 | Company-wise MIS | *Reports › Company-wise* (freight / box, freight % of sales) | same |
| 19 | CFA-wise MIS | *Reports › CFA-wise* (calculated vs actual billed, variance) | same |
| 20 | Location-wise MIS | *Reports › Location-wise*; *Reports › Transportation MIS* drills Company → Customer → CFA → Location → Invoice → Dispatch | same |
| 21 | Rate change with Effective From | Rate *Company A / Raipur / Durg*: version history ₹6.00 (to last day of last month) → ₹6.50 (from the 1st). Created by `cfa.raipur`, approved by `checker`. Use *Revise rate* on any rate to try it | `test_21_22_…` |
| 22 | Old invoices retain old rates | *Reports › Invoice-wise*, filter Company A / Raipur CFA / Durg over two months: last month ₹6.00, this month ₹6.50. Invoice detail shows "Frozen rate retained" when the master has changed | same |
| 23 | Manual override with audit trail | Dispatch entry → *Change boxes / rate* (reason mandatory). Operator request → invoice on hold → `checker` approves in *Approvals*. *Reports › Manual Override* and the invoice's audit trail show old/new/user/time/reason | `test_23_…` |
| 24 | Excel / CSV export | Every report: Excel, CSV, PDF, Print buttons (users with *data.export*) | `test_24_excel_csv_pdf_export` |
| 25 | Role-based permissions | Log in as `viewer` (limited read-only screens, no reports / export), `operator.bilaspur` (cannot open Raipur invoices), `accounts`, `checker`. Matrix in *Administration › Roles* | `test_25_role_based_permissions` |
| 26 | Transporter bill reconciliation | *Operations › Reconciliation*: July bills paid; last month's Speed Cargo bill in VARIANCE (extra charges + unknown LR). *Enter / import bill* → link period dispatches or import `sample_data/transporter_bill_sample.csv` | `test_26_27_…` |
| 27 | Variance report | *Reports › Variance* (line level) and *Freight vs Actual* (bill level); dashboard KPI "Variance" | same |

## Additional controls demonstrated

| Control | Where | Test |
|---|---|---|
| Duplicate invoice prevention (DB + within file + same file re-uploaded) | Import batch rows marked DUPLICATE | `test_file_import_validation_duplicates_and_correction` |
| Missing customer / location detection, correction & retry, ERP code mapping from the import screen | Today's MARG batch (*Needs attention* tab) | same |
| Boxes not received → exception, first entry without approval | Invoice `MB/…/09904` | `test_boxes_missing_initial_entry_without_approval` |
| ERP push API with per-ERP key, SAP OData date format, write-back | *Integration Settings › SAP* | `test_erp_push_api_with_key_and_writeback` |
| Automated pull (demo adapter) | *Integration Settings › DEMO › Run sync now* | `test_demo_adapter_sync_run` |
| Unconfigured live adapters report exactly what is missing | *Integration Settings › CBO › Test connection* | `test_unconfigured_adapter_reports_missing_credentials` |
| Bulk dispatch with unique slip numbers, bulk print PDF, bulk complete | Pending Dispatch → select rows → *Bulk dispatch* | `test_dispatch_numbers_unique_and_bulk_dispatch` |
| Cancellation reason mandatory, slip voided, cancelled report | Dispatch entry › More actions | `test_cancel_requires_reason_and_voids_slip` |
| CSRF protection, session-API CSRF, login lock-out | — | `test_csrf_…`, `test_api_session_…`, `test_login_lockout_after_failures` |
| Customer merge (same party with two codes) | Customer › Possible duplicates › Merge | `test_customer_merge_redirects_future_imports` |
