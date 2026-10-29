# Changelog

## v1.7.2 — case control mandatory everywhere; gone out ONLY by the gate staff's scan

Migration 0017 (applied automatically). **Create your gate staff (role *Dispatch / Gate Staff*) before using this version for real
work: after it, only they - and administrators, who hold every permission - can send a trip out.**

Why: at Guma the gatepass DS/RPR/2627/00201 showed neither the "labels first" rule nor the gate scan, because case control was
off at every depot (the depot switch) and the gatepass was an old (`LEGACY`) one; operators still had a gone-out button; the
one-invoice slip and bulk dispatch still made single-transporter slips. All of that is closed:

- **Case control is mandatory at every depot.** The depot switch is gone (screen field, setting, `enabled_for`); migration 0017
  sets `cfas.case_control_enabled` to true (the column stays, nothing reads it). Every new gatepass gets its cases.
- **Old gatepasses still at the depot get their labels:** the gatepass screen shows *Make the case labels for this gatepass*
  (permission `case.print`, audited `CASES_GENERATED_LEGACY`). One barcode per carton still to leave (cartons of invoices
  already gone out are not labelled); then the labels are printed, then the gatepass (a reprint is allowed with a reason), then
  the gate scan clears it. Until then it cannot be printed or scanned out ("... has no case labels yet ...").
  Gatepasses that already went out stay as history.
- **Gone out ONLY by the gate staff's scan** (`dispatch_service.gate_scan_out`, permission `gate.scan`): every case, then the
  gatepass. Removed: `/gatepass/{id}/gate-out`, `/dispatch/{id}/complete`, `/dispatch/bulk/*`, `ds.complete`, `ds.gate_out`,
  the Gate Check "Confirm gate-out" form, the F9 key. The API `POST /invoices/{id}/complete` and `.../generate-slip` answer
  400 with a clear message. The manager exception still exists (cartons can be recorded as missing, with a reason); the gatepass
  is still scanned afterwards.
- **Permission `dispatch.complete` removed completely** (catalogue, role templates, every check). Migration 0017 takes it off every
  role and every user's own grants / denials and writes an audit row for each. Downgrade gives it back to Depot Managers and
  Dispatch Operators.
- **No back door to single-transporter slips:** `generate_slip`, the `legacy=True` exemption of `plan_gatepass`, the one-invoice
  Generate / Print / Complete buttons and bulk dispatch are retired. The invoice screen (`/dispatch/{id}`) now shows the status, the
  gatepass it is on and a *Make a gatepass with this invoice* link. Pending Dispatch posts only to *Make one gatepass*.
- The completion rule is fixed (gatepass PRINTED); the setting `dispatch.complete_requires` is removed.
- **Demo logins:** one list (`app/demo_users.py`) feeds the seed, the login page and `start_demo`. New: `gate.durg`; the login page
  now lists all gate users. `start_demo` on an existing **demo** database (administrator still has the demo password) adds the
  missing demo users (`seed.ensure_demo_users`, audited); it never changes an existing user or password and never runs on a
  production database.
- **Demo data and Guided Training go through the real flow** (`app/services/gate_service.py`: labels printed, gatepass printed,
  Pawatis printed; gate staff scan every case, then the gatepass). A fresh demo seed takes longer (about 63 s against 41 s on the
  development PC). Training: new demonstration user `demo.gate` ("Act as Demo Gate Staff"), shared lesson blocks (print the
  labels, act as gate staff, scan the cases - with a *Practice scanner* button on Gate Check in training only -, scan the
  gatepass) in the lessons Long distance, Local auto per trip / per box, Gate check and Garage Pawati; the Garage Pawati lesson
  teaches that scanning the gatepass before the Pawatis are printed is refused. Practice databases of older versions get the
  gate role and `demo.gate` automatically; checkpoints are rebuilt.
- Docs: USER_GUIDE, RELEASE_v1.7 (create the gate staff before go-live), README (demo users), ARCHITECTURE.
- Tests: updated to walk every trip through labels, print and the gate scan (`tests/helpers.walk_out`); new tests for
  `convert_legacy`, "no gone-out path anywhere", demo-user ensure on an existing database and the login list.
- Not part of the suite: `tests/e2e_training.py` / `tests/e2e_keyboard_dispatch.py` are manual browser scripts and still describe
  the old buttons; `e2e_training.py` already failed in the keyboard-basics lesson (step `combo`) before this change.

## v1.7.1 — transporter trips by local auto only, labels before gatepass, gone out by gate scan only

Migration 0016 (applied automatically): new role **Dispatch / Gate Staff** with the permission `gate.scan`. Nobody else is
given it automatically - Administrators already hold every permission; give it to the gate staff (Administration › Users).

- **Transporter trip: Local auto only; Direct pickup: Regular only.** On Scan & Gatepass, choosing *Transporter* shows only
  *Local auto* (Regular is gone); choosing *Direct pickup* shows only *Regular* (no local auto). The server refuses the other
  combinations on every new gatepass ("A transporter trip goes to the transporter by a local auto..."). Old Regular gatepasses
  remain readable. The v1.2 one-invoice slip screens and the API still make the old single-transporter slip.
- **Case labels first, then the gatepass.** On a case-control gatepass the gatepass cannot be printed until its labels are
  printed (the screen says "1. Print the case labels first ... 2. print the gatepass").
- **Gone out only by the gate scan.** On a case-control gatepass there is no gone-out button for anybody; the gatepass screen, the
  one-invoice screen, bulk complete and the API refuse it. The gate staff scan every case, then the gatepass: that marks it gone
  out (if every case is scanned or an exception is recorded, the gatepass is printed and its Garage Pawatis are printed). The
  operator's gatepass screen shows the scanned / total count and then "Gone out - confirmed at the gate by ..." (it refreshes
  itself). An exception (manager, reason) no longer sends the trip out by itself - the gate staff still scan the gatepass.
- Only users with `gate.scan` can scan at the gate; the demo data has `gate.raipur` and `gate.bilaspur`.
- Demo and training data follow the rule (local auto on every transporter trip; the long-distance lesson now teaches it, with the
  Garage Pawati print).
- Tests: `tests/test_v171_rules.py`, the gate / label-order tests in `tests/test_v17_phase5.py`; older tests were updated to
  make transporter trips by local auto.

## v1.7.0 — Guided Training brought up to date for v1.7

- **Garage Pawati lesson is now on every path that dispatches**: Master Administrator (16 lessons, last), Dispatch Operator and
  Depot Manager. It needs invoice 001250 to be waiting; if a trainee reaches it from a practice copy where that invoice
  carries a box-change request, the panel offers *Prepare practice data*.
- **Corrected: "Transporter selection" (Depot Manager).** It still told the trainee to pick Vijay Transport Co and read "no rate
  for Durg". Since v1.7 only eligible transporters are offered, so the step now has the trainee read the eligibility line
  ("1 transporter can carry Durg - hidden: 1 without a rate for Durg") and choose Shree Roadlines; it explains the audited
  depot override.
- **Updated lessons**: Introduction (new menus, role-based sidebar), Masters (customer delivery locations, depots a transporter
  serves, builty-book flag, depot / company rate exceptions), Long distance (how freight was calculated, case barcodes at
  depots that use them), Local auto (what differs from the Garage Pawati trip), Users (editable roles, scope by company /
  depot / area / customer type / customer, individual exceptions), System settings (case control per depot, label size, area
  mismatch, Pawati numbering), Audit (new audited actions), Gate check (scanning cartons, gate exception, Pawati blocker),
  Depot monitoring (Pawati register, LR Books), Depot registers and Reports (Garage Pawati Register, Gate Exception Report),
  Rate approvals and Exceptions (rate exceptions, area choice, transporter override).
- Tests: the Depot Manager path test now drives the Pawati lesson; the Master path test covers 16 lessons.

## v1.7.0 — Guided Training: Garage Pawati lesson

- The lesson is now also the last lesson of the **Depot Manager** path (after *Depot registers and delivery status*); its test runs for both roles.
- **New lesson "Local-auto trip to transporters: Garage Pawati"** (Dispatch Operator path, after *Gate check*): invoice 001250
  (Korba, 8 cartons) goes by a local auto to two receiving transporters - 5 cartons to Shree Roadlines, 3 to Vijay Transport Co.
  The trainee splits the invoice in the builder, generates and prints the gatepass, sees the gate refuse the trip until the
  Garage Pawatis are printed, prints them, records gate-out, ticks one Pawati as received and receives the other by entering
  its LR. Every practical step is checked in the practice database.
- **Fix found by the lesson:** an invoice split between two receiving transporters could only take one LR (the main
  receiver's). The gatepass now shows one LR row per customer and receiver, with that receiver's own cases, rate and freight;
  the other part's LR is kept on its allocation, audited, needs a reason to change, and marks that receiver's Pawati received.
- Tests: `tests/test_training_pawati.py` (the lesson, end to end) and a split-LR test in `tests/test_v17_phase6.py`.

## v1.7.0 — Phase 7: release readiness

- **Garage Pawati is white** (plain white A4, 4 slips; no coloured stock needed).
- **Two new reports**: *Garage Pawati Register* (receiving transporter, customer, cartons, bills, amount, freight, receipt state,
  days open) and *Gate Exception Report* (gatepasses that left with unscanned cases: who, when, why). Both are scoped like the
  gatepass they belong to and export to CSV / Excel / PDF.
- **PostgreSQL release gate passed**: the full test suite on PostgreSQL 16, migrations 0 -> 0015, 0015 -> 0009 -> 0015.
- **End-to-end test** of the whole workflow (case control + receivers + Pawati + LR book + audit trail + report scope).
- **docs/RELEASE_v1.7.md**: upgrade steps, rollback, depot-by-depot switch-on (Guma first), hardware trial sheet, acceptance
  checklist and the points still to be confirmed by the owner. Version is now 1.7.0.

## v1.7 (in progress) — Phase 6: receiving transporters, Garage Pawati, LR books

Migration 0015 (applied automatically, additive): `dispatch_allocations`, `garage_pawatis` (+ lines, print log), `lr_books`,
`lr_numbers`. Roles that could operate dispatch get `pawati.print`, `pawati.receive`, `lr_book.manage`; roles that could void a
slip get `pawati.cancel` (audited). A depot's training/practice database is upgraded in place.

- **Local-auto trip with several receiving transporters** (builder: Transporter + *Local auto*): each invoice names its receiving
  transporter, and an invoice may be **split** (some cases to a second transporter). Freight is priced per receiver from its
  rate card; the allocation (invoice line x receiver) is the unit of freight, LR and Pawati. One gatepass = one local-auto trip;
  *Direct pickup* + local auto is refused. Regular transporter gatepasses are unchanged.
- **Garage Pawati** (Dispatch desk): one white slip (printed on plain white A4) per receiving transporter x customer x company (No., Date, Garage, To,
  Cartons, Bill No., Amount, Freight, signatures), 4 per A4. Numbered per depot; the Administrator sets the starting number
  (Administration > Pawati Numbering). **The auto cannot go out until every Pawati is printed.** Reprint = reason + permission.
  Receipt is recorded by hand or automatically when the LR is entered after gate-out; the Pawati stays with the transporter.
  Voiding the gatepass cancels its Pawatis; a cancelled Pawati is re-issued under a new number.
- **LR books** (Dispatch desk > LR Books): import a range for a transporter flagged "uses builty book"; CTDMS gives the next LR to
  each consignment at gatepass creation, staff can change it (logged), an empty book warns and is audited, a void gatepass
  releases the numbers.
- Fixed in the builder: the split box shadowed the Cases box when a split was open.
- Tests: `tests/test_v17_phase6.py` (9) and `tests/e2e_pawati.py` (browser); the full suite passes (383).

## v1.7 (in progress) — Phase 5: the guard scans every case before gate-out

Migration 0014 (applied automatically): roles that could void a gatepass receive `gate.exception` (audited). No table changes.

- **Gate check scan loop** (Dispatch desk › Gate Check): for a gatepass with case control, the guard scans each case label; the
  cursor stays in the scan box, every scan answers at once - green tick + short beep, or a red STOP with the reason (not a
  CTDMS code / misread, training label, unknown, void label, belongs to another gatepass, same case twice, gatepass void or
  already gone out). A running "scanned / total", scanned count per invoice and the list of cases still missing are shown.
  Scanning a case in the top box opens its gatepass and counts that scan.
- **Gate-out is blocked until every case is scanned** - in the service, so all four ways of completing a dispatch are covered:
  the gatepass screen, the one-invoice screen, bulk complete and the API. Gate-out marks the cases DISPATCHED.
- **Exception**: a person holding `gate.exception` can let a gatepass leave with unscanned cases - a reason is required, the
  cases are marked MISSING, and it is written to the audit log and shown on the screen.
- **Every scan is recorded** (`case_scans`: code, result, who, when, station), including misreads and rejected scans.
  Practice labels are refused on the live gate. Gatepasses without case control are untouched.
- Tests: `tests/test_v17_phase5.py` (6 checks: every scan rule and the scan log, all four completion paths blocked and then
  allowed, exception permission / reason / audit, gate screen, old gatepasses unaffected, migration grants).

## v1.7 (in progress) — Phase 4: one barcode per case, case labels

Migration 0013 (applied automatically, additive): `dispatch_cases`, `case_scans`, `label_print_log`,
`dispatch_slips.case_control` (LEGACY for every existing gatepass) and gate-exception columns, `cfas.case_control_enabled`
(off everywhere). Roles that could dispatch get `case.print`, roles that could reprint a slip get `case.reprint` (audited).

- **Case control is per depot, switched on when the depot is ready** (Master Settings › Depots › *Case barcode labels and gate
  scanning*; Guma first). A gatepass from such a depot gets one row per physical carton, each with its own code. Every other
  depot, and every gatepass made before, behaves exactly as in v1.6.1 (gatepass barcode on the label, count at the gate).
- **Code**: 9-digit number + check digit, digits only, Code 128 - any keyboard-wedge scanner types it correctly, and a
  mistyped or misread digit is always caught. The invoice number, company and customer are stored on the case and printed
  as text; the code never replaces them. Numbers of one gatepass are reserved in one step (consecutive, no write-lock
  storm). Practice (Training) labels start at 900,000,000, so they can never be mistaken for real ones.
- **Numbering**: "case k / n" counts inside the consignment (customer + delivery area) across its invoices.
- **Labels screen** (gatepass › *Print the labels*): preview of exactly what prints, label size from System Settings
  (`labels.width_mm` / `labels.height_mm`, default 100 x 50), the normal Windows print dialog (any printer model), then
  *Did all labels print?* - **printed** labels the cases, **failed** is logged and they stay to print. Printing labelled cases
  again is a **reprint**: same codes, permission `case.reprint`, a reason, audited. Range pickers for a torn label.
- Voiding a gatepass or cancelling an invoice voids its cases; a re-made gatepass gets new codes. Scanning any case code
  in Gate Check finds its gatepass. The gatepass page lists the cases, their status and the label prints.
- Tests: `tests/test_v17_phase4.py` (8 checks: code and check digit, per-depot generation and numbering, labels page and size
  setting, failed / printed / reprint rules and log, void / cancel / new codes, gate lookup, depot switch and grants, migration).
  Scanning at the gate and blocking gate-out until every case is scanned is Phase 5.

## v1.7 (in progress) — Phase 3: freight by area, customer delivery areas, eligible transporters, grouping rules

Migrations 0011 and 0012 (applied automatically, additive): `customer_delivery_locations`, `transporter_depots`,
`transporters.uses_lr_book`, `invoices.customer_location_id`, `transporter_rates.cfa_id / company_id`,
`dispatch_slip_lines.rate_id / calc_note / consignment_no`, `dispatch_slips.cross_company /
eligibility_override_reason`. Roles that can dispatch keep what they could do: they receive `dispatch.group_cross_company`,
roles that approve overrides receive `transporter.override_eligibility` (each written to the audit log).

- **Customer delivery areas.** A customer may list several delivery locations (area = the Location master, label,
  ship-to code, default). Resolution is the same for file import, API, e-invoice QR and manual entry: ship-to code ->
  the file's area if it is one of the customer's -> the single / default location -> otherwise the invoice is held as
  *AREA_AMBIGUOUS* until the operator chooses (recorded on the invoice, audited). A file area that is not one of the
  customer's was silently used before; `area.mismatch_action` (default ACCEPT = unchanged) can hold such invoices.
- **Only eligible transporters are offered** - on the new gatepass screen and on the quick dispatch screens: the
  transporter serves the depot (Transporters › *Depots served*; none ticked = every depot, so nothing changes by
  default) and has an approved rate for every area (intersection for mixed areas, with a plain explanation when nobody
  qualifies). The server re-checks every gatepass, so a forged request is refused. Choosing a transporter that does not
  serve the depot needs `transporter.override_eligibility` and a reason; it is audited and shown on the gatepass.
- **Rate exceptions.** A transporter rate may be an explicit exception for one depot and / or company; the most specific
  wins (depot + company > depot > company > general), versions are kept per scope and never end each other. Every existing
  rate is general, so every existing price is identical (a test prices them all before / after).
- **Freight breakdown.** Each gatepass line freezes how its freight was found (rate row, rule, area, cases x rate,
  or the share of a per-trip charge) and its consignment number; the gatepass shows *How freight was calculated*. A
  per-trip auto is still paid once. Slabs are not part of this version (kept for a later update, as agreed).
- **Grouping.** Invoices of different companies go on one gatepass only by an explicit tick (*Group across companies*,
  permission `dispatch.group_cross_company`), recorded on the gatepass and audited; every invoice keeps its own company,
  number and cases. The screen shows one consignment per customer + area. The Guided Training lessons whose demonstration
  trips mix two companies have the new step.
- Tests: `tests/test_v17_phase3.py` (20 checks: area resolution, import / mismatch setting, ambiguous area flow, customer
  screen, merge, depots served, eligibility and intersection, forged transporter, audited override, exception ranking and
  scope-wise versions, all existing rates price identically, frozen breakdown, rate-card screen, cross-company rules and
  permission, consignments, builder controls, quick screen, migrations 0011 / 0012 with role grants); browser test
  `tests/e2e_gatepass.py` (20 checks).

## v1.7 (in progress) — Phase 2: configurable permissions and operational scope per employee

Migration 0010 (applied automatically, additive): `user_permissions`, `user_scope`, and on users `all_areas`,
`all_customer_types`, `all_customers` (all default "All", so nobody sees less after the upgrade) and `responsibility`.

- **Roles are editable templates - nothing is hard-locked any more.** The v1.6.1 fixed baseline is removed: the
  Administrator can tick any permission for any role, rename a role, copy a role into a new one, or reset a system role
  to its template (audited: ROLE_RENAMED / ROLE_PERMISSIONS_CHANGED / ROLE_CREATED / ROLE_RESET). Defaults are unchanged.
- **Individual exceptions per user**: *Give* or *Take away* one permission for one person on top of their role (a
  reason is required, DENY wins). The user screen shows a live **Effective access** preview (permissions, menu, what
  the exception changes) before anything is saved.
- **Operational scope per employee**: companies, depots, **delivery areas** (Location master), **customer types** and
  **customers** - AND between kinds, OR inside a kind; types and customers together show both. Enforced in every list,
  search, register, report, export, dashboard, API call, dropdown and import: `app/services/scope.py` is the single
  place (a test fails if any report skips it). A gatepass is visible only if **all** its invoices are in scope (rule B6);
  transporter bills (which span many customers) are hidden from area / customer-restricted users; customer alerts follow
  the gatepass. Importing an invoice outside your area or customers is refused.
- **Guard rails instead of locks**: only holders of Manage users / Manage roles change access; nobody changes their own
  role, permissions, scope or status; nobody gives (or edits someone holding) more than they hold themselves - also through *Reset to template* -, or a wider
  company / depot / area / customer scope than their own; at least one active user must always hold both Manage users and
  Manage roles (checked by effective permissions, not by role name); changing a role or deactivating signs the user out.
- **User screen**: identity and role, scope pickers (type to search areas / customers, ↑↓ Enter, × to remove - fast
  typing never picks a stale entry), customer-type list, individual permissions that follow the role, F6 to save.
- **Upgrade safety found by the tests**: a Guided Training practice copy made by an older version is now brought up to
  date (missing tables / columns added, rows kept) before it is used; users load their exceptions and scope with them,
  so error pages and Training Mode work for any user.
- Tests: `tests/test_v17_phase2.py` (30 checks: effective permissions, scope matrix by page / search / register / export /
  API / dropdown / forged post, gatepass rule, bills, import, guard rails, last-administrator, audit, role edit / reset /
  copy, migration 0010 up and down, practice-copy upgrade), the v1.6.1 "fixed baseline" tests rewritten for editable
  roles, browser test `tests/e2e_access.py` (27 checks). All browser suites pass: sidebar 19/19, role-based 257/257,
  training 19/19.

## v1.7 (in progress) — Phase 1: sidebar position, scope audit, design checkpoint

- **Sidebar keeps its scroll position.** Cause: every screen is a new page load and the scrollable sidebar restarted at
  the top. Now restored before first paint from the tab's session storage, saved on scroll / page hide, and the
  selected entry is revealed only if hidden. Covers click, keyboard, F3, redirecting form posts, Back / Forward, reload.
  Browser test `tests/e2e_sidebar.py`: 5 of 18 checks passed before the fix, 19 of 19 after.
- **Scope audit**: every report goes through one of seven scope helpers (tripwire test `tests/test_v17_phase1.py`);
  the helpers are the extension point for area / customer scope in Phase 2.
- **Design checkpoint** `docs/ITERATION_v1.7_DESIGN.md`: terms, schema for migrations 0010-0014 (slabs deferred),
  state machines, services, rollback. No data or behaviour changed apart from the sidebar.

## v1.6.1 — Role-based access: every role sees only its own work (21 Oct 2026)

Migration 0009 (applied automatically): re-aligns the stored roles to the new baseline (every changed role is written
to the audit log as ROLE_BASELINE_APPLIED), pauses e-mailed reports whose owner lost access, moves a Viewer off the
Management training path. No business data is touched.

- **Master Settings = Administrator only** (fixed baseline in `app/security/permissions.py`, enforced in `Role.has`, so an
  edited role list, a wildcard or a role made by the Master cannot widen it): Companies, Depots, Customers, Customer
  Types, Transporters, Locations, Local Autos, Freight Rates and Transporter Rate Cards, Invoice Recognition Code,
  ERP Mappings, Initial Master Upload, System Settings, Users, Roles, Integrations, Guided Training administration, audit
  log. *Using* a master (new permission `master.use`: transporter / customer / depot / location dropdowns and lookups)
  is separate from *managing* it - operators keep their searchable dropdowns.
- **Final Reports / Management MIS = Administrator, Accounts, Management only** (new `mis.view`, fixed baseline). Every
  consolidated, financial, variance or management report - page, Excel / CSV / PDF export, print view, the e-mailed
  reports, `/mis`, `/api/v1/reports*` - checks it on the server. Operational registers (dispatch, gatepass, delivery,
  pending, exceptions, a new money-free Invoice Register export) stay on `report.view`; audit and rate-change reports stay
  Administrator-only. `report.view` no longer opens final reports.
- **Role baselines** (Administration › Roles shows them, locked where fixed): Management and Accounts lose Master access;
  Depot Manager loses freight-rate creation and transporter bills; Checker loses dashboard / reports / audit and keeps
  approvals; Viewer is read-only operational (no reports, no exports) and is no longer on the Management path.
- **Menus per role** (no empty groups, no inaccessible entries; F3 "go to", Tab order and the F10 key follow the menu):
  new groups Approvals (+ **Approval History**) and Accounts; Dispatch desk reads "Dispatch Monitoring" for read-only
  roles; Reports & MIS reads "Operational Reports" for roles without final reports. Esc goes to the role's own home.
- **Dashboards**: roles with MIS see the financial dashboard (Management / Accounts titles, Accounts also bill counts);
  everyone else gets a counts-only Dispatch Desk / Depot Operations Dashboard - the financial figures are not even
  computed for them (page and `/api/v1/dashboard/summary`). The Invoice Register totals and invoice-level billed /
  variance data (reconciliation card, API `actual_freight`) are shown only to roles with bills or MIS.
- **Depot restrictions**: a user with several depots switches between them in the top bar (Alt+D, or F3 and type the depot name); the choice narrows
  every list, search, report, export and API call through `User.cfa_ids`, never widens it. Isolation is tested across
  lists, details, tracking, bulk actions, exports and APIs.
- **API**: `/api/v1/masters/*` needs only `master.use` and returns lookup fields; full records (GSTIN, PAN, bank details)
  need Master access. Reports / dashboard API follow the report rules. 403 pages no longer name the permission; denials
  are logged.
- **Permission management**: Roles page shows fixed permissions locked, "what each role sees" and every user with role
  and depots; a forged form cannot grant fixed permissions (ROLE_PERMISSION_REFUSED is audited); nobody can change their
  own role / companies / depots, and only an Administrator hands out or edits the Administrator role. The two-person
  rules (overrides, rates, bills) apply to the Administrator too - tested.
- **Guided Training**: each role gets its own tutorial (new Read-only Viewer path; Depot Manager's last lesson is now
  Depot registers and delivery status instead of final reports); the Master can only assign a tutorial that fits the
  user's role; "Act as" in Training Mode offers only the demonstration checker; practice screens use exactly the live
  role's permissions (Master Settings and final reports stay closed in Training Mode).
- Tests: `tests/test_rbac.py` (88 tests: baseline, menus, direct URLs, final reports / exports / print / API per role,
  dashboards, billed data, approvals, depot isolation and switching, role screen, upgrade of a v1.6.0 database);
  `tests/e2e_rbac.py` - 257 browser checks (keyboard sign-in, menu, F3, Tab order, F10, depot switch, direct URLs, training) on
  Chromium. Older tests updated where they relied on the previous, wider roles.

## v1.6.0 — Guided Training and first-time onboarding (21 Oct 2026)

Migration 0008 (applied automatically): `training_progress` - one row per user in the live database.

- **Guided Training** (Help › Guided Training / Practice Mode): new staff learn by doing the real work in the real
  screens, on the v1.6 demonstration data, step by step: explain → highlight → do it → CTDMS checks → result → next.
  Practical steps are checked on the server in the trainee's own data (a workbook really imported, a gatepass really
  generated, printed and gone out, a bill really approved by a second person); clicking through never completes a lesson.
- **Isolated practice copy per trainee** (`data/training/users/<id>/`): while a session is in Training Mode every screen
  works on the trainee's own SQLite practice database and upload folder; sign-in and progress stay on the live database.
  Starting points rebuilt from the v1.6 scenario through the application's own services (same workbook, SAP / MARG / CBO
  files, transporters and amounts): clean, uploaded, masters, sap, invoices, dispatched, billed. "Prepare practice data"
  copies one in under a second - no restart; several trainees practise at the same time without touching each other.
- **Nothing leaves CTDMS in training**: WhatsApp / SMS, e-mailed reports, ERP sync, the AI bill reader and the live
  import drop folder are blocked on the server; every training print, PDF, Excel and CSV export says
  "TRAINING - DEMONSTRATION ONLY - NOT VALID FOR BUSINESS USE"; a permanent orange TRAINING MODE bar.
- **Learning paths by role** (existing roles): Master Administrator (15 lessons, following the v1.6 video plus users &
  roles, settings & two-person rate approval, delivery / POD, bills / reconciliation / duplicates / payment, audit &
  exports from the Master manual), Dispatch Operator, Depot Manager, Checker / Approver, Accounts, Management / Viewer.
  The two-person rule is practised with the practice copy's demonstration checker ("Act as Demo Checker").
- **Compulsory training (Master switch)**: System Settings › Guided Training or Administration › Guided Training.
  ON: users created from then on must finish their path before any live screen opens (server-side gate); OFF: everyone
  works, training stays voluntary. Completions are never removed by the switch. The Master is never locked out of
  Administration; `python -m app.cli training-release --username <u>` (or `--all-off`) as a last resort.
- **Administration › Guided Training**: totals (required, not started, in training, completed), every user's path,
  progress, start / last activity / completion dates; reassign a path, require / release, reset progress (audited).
- **Keyboard-first panel**: Ctrl+Enter = Next / Check, Ctrl+/ = into the panel (Tab between Back, Check, Watch
  demonstration, Replay lesson, Save & exit; Esc back to the screen). Every step lists the CTDMS keys to use.
  "Watch demonstration" opens the v1.6 guide video at the lesson's chapter (range requests, Esc returns to practice).
- Progress per user: path, current lesson and step, lessons passed with their checks, %, start, last activity,
  completion; logging out and in resumes at the same step.
- Fixes found while building the lessons: the demonstration workbook gave Ravi Tempo a default ₹20 per box, so the
  Durg invoice was charged silently instead of showing the missing-rate warning the v1.6 video describes (workbook,
  video and demonstration database re-made); Esc on a top-level list now goes to the user's first menu screen (roles
  without the Dashboard got an error); after gate-out the cursor starts in the first empty LR box.
- `python -m app.cli training-build` prepares the starting points (otherwise done in the background at start-up).
- Tests: `test_training.py`, `test_training_master_path.py` (all 15 Master lessons with real actions),
  `test_training_role_paths.py` (Depot, Checker, Accounts); `tests/e2e_training.py` - the Dispatch Operator path
  completed in Edge with the keyboard only, compulsory switch, resume after logout, live database unchanged.
- Guide: supplementary video `CTDMS_v1.6_Guided_Training.mp4` (+ transcript, subtitles); manual chapter "Guided Training".

## v1.6.0 — keyboard-first operation (21 Oct 2026)

For staff used to MARG: every daily screen works from the keyboard, the mouse is optional. No database change.

- **Same function keys on every screen**: F1 help (all keys + the keys of this screen), F2 new (dashboard / gatepasses:
  new gatepass; Pending Dispatch: gatepass for the ticked invoices), F3 go to any screen by typing its name (Ctrl+K),
  F4 search box, F6 save the form you are in (Ctrl+S), F7 edit, F8 generate / print the gatepass or dispatch slip,
  F9 complete the dispatch (gate-out), F10 reports, Esc close / back. F5, F11, F12 are left to the browser.
  Buttons show their key (e.g. *Generate gatepass F8*); a key bar at the bottom of every screen lists what works there.
- **Enter = next field** in every data-entry form (ERP style); after the last field the cursor goes to the main button
  and a second Enter presses it. Shift+Enter goes back. Search / filter boxes: Enter applies. Login: Enter signs in.
- **Searchable choice boxes** for company, depot, customer, customer type, area, transporter, local auto, ERP (and any
  long list): type the code (`GUMA`, `C025`, `SRL`, recognition codes, ERP customer codes) or part of the name, ↓↑, Enter.
- **Lists**: ↓↑ move a highlighted row, Enter opens it (gatepass screen: adds the invoice), Space ticks, typing jumps
  to the search box, ↓ from a search box enters its table. Esc from a record returns to the same list, filters and row.
- **Auto-focus**: search box on lists, first field on forms, Depot (or the scan box) on New gatepass, Transporter on
  Dispatch Entry. Clear yellow focus on every field, button and row; focused fields never hide under the bars.
- **Dispatch desk**: Enter on the empty scan box jumps to *Who carries the goods*; the panel now asks carrier first,
  date / time after; a grey button explains why it cannot be pressed yet.
- **Safety**: confirmations are a keyboard Yes / No box (Y / N / Esc); for delete, void, cancel, reject and merge *No* is
  pre-selected, so Enter never destroys anything; a held-down key never answers. Esc on a screen with unsaved typing asks
  before leaving ("Stay here" pre-selected).
- **Fix - local auto trip charge**: emptying the trip-charge box refilled it at once with the usual amount, so what was
  typed next was added to the end (`500` became `500.00500`, saved as ₹500.01). The usual amount is now filled in only
  when the auto is chosen, and the server refuses a trip charge with more than 2 decimals. The v1.6 demonstration data
  and video had this error (₹500.01 / ₹600.01); both are re-made with ₹500 and ₹550.
- Every red (danger) button asks Yes / No first, even where a screen did not ask before (Reject on a freight rate).
- Tests: `tests/test_v16_keyboard.py` (keys on every screen); `tests/e2e_keyboard_dispatch.py` drives the whole dispatch
  in Edge with the keyboard only (58 checks: login, depot by code, invoices, transporter / local auto by code, generate,
  print, gate-out, back to the same row, per-invoice dispatch entry, unsaved-changes guard, master entry, F3 / F10 / F1 / F4).
- Guide: the v1.6 video has a new *Step 9 - Keyboard-only dispatch*; the manual has a keyboard chapter.

## v1.6.0 — 18 Oct 2026 (management feedback: masters, depots, codification, local autos)

Migration 0007 (applied automatically; all data kept): depot locality / recognition code, customer types master,
customer / company recognition codes, source ERP + ERP customer code on customers, invoice markings, local-auto box
rates, local-auto charging fields, Invoice Recognition Code columns on invoices, master upload history.

- **Initial Master Upload** (Master Settings): one cleaned workbook for the first-day set-up — ERP_Systems, Locations,
  Companies, Depots, Customer_Types, Customers, Transporters (long-distance and local autos), Local_Auto_Rates,
  Freight_Rates, Invoice_Code_Mappings, ERP_Mappings, with an Instructions sheet. Validate → preview (new / already
  in CTDMS / rejected per sheet, every reason shown) → import valid rows → history + Excel error report. Existing
  codes are never duplicated or overwritten; freight rates go to the checker. Later additions: Master Settings.
- **CFA → Depot** on every screen, print, report and export (Depot Master, Depot Manager, Depot-wise report, depot
  selection). Several depots per city (Raipur: Guma, Kamal Vihar), each with its own code, locality and city.
  Database names unchanged. No business-type split.
- **Customer Types** are a master (Retailer, Stockist, Distributor, Hospital, Institution, Chain Pharmacy, Other —
  editable, each with a short code). Customers get Source ERP + Source ERP customer code (kept as an ERP mapping).
- **Invoice Recognition Code** (provision, switchable): Company + Customer Type + Customer + Depot + numeric part of
  the ERP number, e.g. `ABC-RET-C025-GUMA-001245`; pattern, padding and fallback configurable (Master Settings ›
  Invoice Recognition Code). Stored beside the **original ERP invoice number, which is never changed**; shown on the
  register, invoice, gatepass, dispatch slip, reports, exports and API; searchable. Missing master codes give a clear
  "code not generated" reason; dispatch never depends on it; Regenerate codes after master changes.
- **Invoice Code Mappings**: existing ERP invoice markings (e.g. `MB/` = Company B, optionally depot / customer type)
  recognise the company / depot of an incoming invoice when the file does not say.
- **Local Auto Transporters** (was Outside Autos): allowed charging Per Trip / Per Box / Both, usual trip charge,
  default rate per box, vehicle no. **Per trip** = one amount for the whole trip, entered / confirmed on the gatepass,
  shared over the invoices by boxes for MIS (4 invoices at ₹500 = ₹500, never ₹2,000). **Per box** = boxes × rate from
  the new **Local Auto Box Rates** (auto + location / company / depot + effective date; most specific wins).
  Long-distance transporter workflow unchanged.
- **Reports / search**: City-wise and Depot-wise freight, Local Auto Trips (amount owed vs share on invoices), filters
  for city, depot, customer type, recognition code and local-auto charging; reports show "Original ERP Invoice No" and
  "Invoice Recognition Code" separately. Approvals: **Approve all** rate-card changes (maker-checker kept).
- **Product / SKU lines postponed**: item-wise ERP exports are still accepted (rows combined into one invoice) but
  lines are not stored or shown; SKU report hidden. Setting `features.sku_lines` (off) keeps the provision.
- Demo seed: Guma / Bilaspur / Durg / Kamal Vihar depots, customer types with codes, a local auto charged either way.
- Tests: `tests/test_v16.py` (21); v1.5 tests updated for the new wording; date-dependent reconciliation test fixed.

## v1.5.1 — 15 Oct 2026 (office server, no paid services)

- **Paid services locked**: automatic bill reading (Anthropic API) and WhatsApp / SMS sending stay locked unless
  `CTDMS_PAID_SERVICES=true` (default false). The features remain in the code; screens say they are locked. Bills
  are typed next to the photo (all cross-checks unchanged); alerts can be previewed with provider LOG.
- **Single sign-on removed** (Google / Microsoft): users sign in with username and password only.
- **Users**: the administrator sets each user's password and decides whether it must be changed at first login
  (also when setting a new password). Audited.
- Tests: `tests/test_v151_locks.py` (4); SSO tests removed. Suite: 176 passed.

## v1.5.0 — 15 Oct 2026 (delivery & tracking, courier-style)

Migration 0006 (applied automatically; all data kept): delivery / POD, e-way bill and QR-signature columns on
invoices, `report_subscriptions` table. New packages: `cryptography` (via `pyjwt[crypto]`).

- **Proof of delivery**: delivered / short / damaged / refused, date, received by, note, photo of the signed LR.
  Recorded per consignment (customer on a gatepass), only after gate-out; changes need a reason; all audited.
  **Deliveries Pending** list (overdue after `delivery.expected_days`) with bulk "delivered on <date>".
- **Track Shipment**: one search for invoice / LR / gatepass / e-way bill / IRN → milestone timeline
  (order received → gatepass → printed → left warehouse → LR → delivered → bill checked → paid).
- **Customer tracking link** (signed, expires after `delivery.link_days`) in WhatsApp / SMS alerts via `{track}` /
  `{track_url}` when `org.public_url` is set; customers see the timeline without prices and can confirm receipt.
  A customer confirmation never overwrites a staff record.
- **E-way bill** number + validity per invoice (12 digits, one invoice per e-way bill); optional gate rule
  `gate.eway_required_above` blocks gate-out for bigger invoices without a valid e-way bill (default 0 = off).
- **E-invoice QR signature check** with the IRP public key (`CTDMS_IRP_PUBLIC_KEY_FILE`): VERIFIED / INVALID /
  UNVERIFIED; `einvoice.signature_check` OFF / WARN (default) / BLOCK. Shown on the invoice.
- **Case labels**: one per case (customer, area, invoices, carrier, "k / N", gatepass barcode) — 100×50 mm or A4.
- **Reports**: Delivery Performance (on-time %, average transit days per transporter × area), Delivery Register
  (POD). **E-mailed reports**: any report daily / weekly / monthly as Excel / CSV / PDF (SMTP in `.env`), sent by
  the app or `python -m app.cli reports-mail`; runs with the owner's data scope; never sent twice.
- **Single sign-on** (OpenID Connect: Google Workspace, Microsoft Entra ID, Okta …): user matched by e-mail, no
  automatic accounts; password login unchanged.
- **API**: `GET /api/v1/track?q=`, `POST /api/v1/invoices/{id}/delivery`; invoice JSON carries delivery, e-way bill
  and QR-signature fields.
- Demo data: proofs of delivery for most older consignments (some short / damaged / confirmed by the customer).
- Docker: `requirements.lock.txt` updated; image verified on Python 3.11 (pip install, migrations, gunicorn,
  `/health`) — the OS-package step could not be fetched in the build sandbox.
- Tests: `tests/test_v15_tracking.py` (17).

## v1.4.1 — 15 Oct 2026 (items from the management brief still missing)

Migration 0005: `invoices.taxable_value`.

- **Add Invoice** screen (flowchart "Add Invoices"): stockist (area filled in automatically), company, CFA,
  invoice no / date / amount / **taxable amount** / cases, planned **mode of dispatch** and **transporter**
  (pre-selected when the gatepass is made). Same checks as an import (duplicates, scope, customer area);
  latest invoices with their dispatch status listed below.
- Imports: "Taxable value" columns go to the new taxable amount (before, they could land in the invoice amount);
  a "Mode of dispatch" column is read as the planned mode. API ingest accepts `taxable_value` and `dispatch_mode`.
- Transporter-wise report: **trips (gatepasses), cost per case, cost per trip**. Invoice-wise report: taxable amount.
- Fix: totals on the Gatepass list were multiplied by the number of rows (204 gatepasses shown as 41,820).
- Tests: `tests/test_v141_gaps.py` (5) and a regression test for the totals.

## v1.4.0 — 15 Oct 2026 (customer dispatch alerts)

Migration 0004 adds the `notifications` table (applied automatically; data kept).

- WhatsApp / SMS alerts to customers, one per customer per gatepass: **on gate-out** (invoices, cases, carrier
  and its mobile, gatepass no) and **when the LR number is entered** (again if it is corrected). Never queued twice.
- Providers (System Settings › `notify.provider`): OFF (default, nothing queued), LOG (trial: marked sent, nothing
  leaves the server), WHATSAPP_CLOUD (approved template with one body parameter), HTTP_SMS (any HTTP SMS gateway).
  Token in `CTDMS_NOTIFY_TOKEN` (.env). Message templates are editable settings.
- Background sender with retries (5, 10, 15 … min, `notify.max_attempts`), safe with several workers;
  `python -m app.cli notify` for cron. Every send / failure / retry is audited.
- **Customer Alerts** screen (Dispatch desk): status tabs, search, retry; alerts listed on each gatepass.
- Mobile numbers normalised to +91; landlines (e.g. 0771-…) are refused, not texted.
- Tests: `tests/test_v14_notify.py` (12).

## v1.3.0 — 15 Oct 2026 (management feedback)

Plan and reasoning: `docs/ITERATION_v1.3_PLAN.md`. Day-to-day use: `docs/USER_GUIDE.md`.

**Database change** (migration 0003, applied by `start_demo.bat` / `init-db` / the Docker entrypoint; all data
kept). Every existing one-invoice slip becomes a one-line gatepass. Existing installations keep pricing from the
principal rate card (`freight.source = COMPANY`) until transporter rate cards are loaded and the setting is switched
to `TRANSPORTER` in System Settings; new installations start with `TRANSPORTER`. LR and vehicle numbers are no
longer required to print.

### Invoices in without re-typing (no ERP access needed)
- **E-invoice QR scanning**: the GST QR on every invoice (any ERP) is decoded at the dispatch desk — seller GSTIN →
  company, buyer GSTIN → customer, IRN prevents double capture. A USB 2D scanner or a phone camera works.
- **Drop folder** `data/inbox` (or `inbox/<ERP>`): exports are imported automatically every few minutes and moved
  to `processed/` or `failed/`; `python -m app.cli inbox` for cron / Task Scheduler.
- **One-click import** with **layout recognition** from the column headings.
- **Item-wise exports**: rows grouped per invoice into SKU lines; SKU lines are added to invoices already captured
  by QR instead of being rejected as duplicates. Customers can also be found by **GSTIN**.

### Gatepass (dispatch slip) — nothing leaves without it
- One gatepass per pickup with **many invoices for many customers**; modes Transporter / Direct pickup / Outside
  auto / Self pickup; outside auto to the transporter's office.
- Printed slip: barcode, carrier name, contact, GST, payment mode (Cash / TBB) or auto; per customer: invoices,
  cases, rate per case, charge; totals, paid now, to be billed. **No vehicle number.**
- **LR entered later** per consignment (gatepass page or *LR Pending*), audited, locked once the bill is approved;
  one LR cannot belong to two consignees.
- **Gate check**: scan the gatepass at the gate → DISPATCHED; void / unprinted / already-used gatepasses are stopped.

### Rates and masters
- **Transporter rate cards** (rate per case per area, effective-dated, maker-checker, history kept, CSV upload).
- **Outside autos** master (per trip / per case). Transporters: payment mode (Cash / TBB), direct-pickup extra.
- **Customer type**: stockist / retailer / hospital; WhatsApp number.

### Transporter bills
- **Scan a bill** (photo / PDF): read by Claude (`CTDMS_ANTHROPIC_API_KEY`) into lines, or typed next to the photo.
- Consignment-level cross-check against gatepasses with an **irregularity report** (screen / print / Excel): LR not
  ours (with suggested match that fills our LR), billed twice, **already paid in cash**, more cases, rate ≠ rate
  card, cases × rate ≠ amount, station / party mismatch, date outside period, lines ≠ bill total, our dispatches
  missing from the bill.
- **Rate review** after checking: "Has the transporter changed any rates?" → proposals to the rate card.

### Reports
- Gatepass Register, Transporter × Area cost per case, SKU-wise Dispatch, Stockists / Retailers / Hospitals,
  Mode of Dispatch, Outside Auto Expense, Transporter Bill Irregularities.

### Tests
- `tests/test_v13_gatepass.py` (42 tests). The v1.0–v1.2 suites run with `freight.source = COMPANY` (still
  supported); `test_rec02` no longer depends on test order; the upgrade test covers 0001 → 0003.
  Suite: 139 passed on SQLite (PostgreSQL not re-run for v1.3).

## v1.2.0 — 27 Sep 2026 (second QA round)

Fixes for the six issues from an independent QA / stress test that v1.1 had not covered, plus seven follow-ups
found by an independent code review of those fixes. Plain-language explanation with before/after results:
`docs/CTDMS_v1.2_Fix_Report.pdf`.

**One small database change** (migration 0002: `users.tokens_valid_after`). It is applied automatically by
`start_demo.bat` / `start_demo.sh` (and by `python -m app.cli init-db` / the Docker entrypoint). All data is kept.

### Security
- QA-07 API writes from a browser session always need the CSRF token. Before, *any* `Authorization` or
  `X-API-Key` header (even rubbish) switched that check off. Now a request with `Authorization: Bearer …` is
  judged by the token alone (invalid token → 401, no fallback to the cookie; valid token → no CSRF needed) and
  everything else that uses the session cookie needs the CSRF token.
- QA-03 Signing out "everywhere" now really means everywhere. `create-admin` on an existing account (password
  recovery) resets the password, clears a lockout, ends all browser sessions **and** API tokens, and writes an
  ADMIN_RECOVERY audit entry; turning a non-admin into an admin needs `--promote`. Deactivating a user or
  changing their role (Admin → Users) does the same, and old API tokens stay invalid even if the account is
  re-activated later (new `tokens_valid_after` stamp).
- CSRF tokens with non-ASCII characters are rejected with 403 (was HTTP 500).

### Bugs
- QA-10 Report PDFs no longer crash (HTTP 500 / LayoutError) when a cell is very long, e.g. a transporter with
  hundreds of LR numbers. Lists with more than 10 items and text over 400 characters are shown as
  "N items – full list in note [k]" and printed in full in a Notes section after the table; rows still taller
  than a page continue on the next page. Excel exports cut cells at Excel's 32,767-character limit at an item
  boundary and say how many items are not shown (before: silently cut by Excel).
- QA-11 `GET /api/v1/dispatch/pending` is paged – **changed response shape**:
  `{total, page, page_size, has_more, next_cursor, items}` (was a bare list silently cut at 2,000). For complete
  reading use `?after=<next_cursor>` until `has_more` is false: every open invoice exactly once, even while
  others are dispatched meanwhile. `page` / `page_size` (max 1000) also work for browsing. Absurd page numbers
  now give 422 instead of 500 (also on `/api/v1/invoices`).
- QA-09 `start_demo.bat` / `start_demo.sh` decide from what is inside the database, not whether the file exists
  (new `python -m app.cli prepare-demo`): empty → demo data; any real data → never reset (structure brought up to
  date first); data but no active user → stops and explains `create-admin`. The launchers stop with a clear
  message — and the Windows window stays open — if Python 3.11+ is missing, packages fail to install or the app
  cannot start (e.g. already running).

### Performance (QA-13)
- Page views no longer take SQLite's single write lock (plain `BEGIN` for GET/HEAD, `BEGIN IMMEDIATE` for saves),
  so people reading never queue behind each other. "Last seen" bookkeeping waits at most 0.25 s and is skipped
  if busy. Exports read and build the file without the lock and take it only for the audit entry at the end.
- Safety net: a page view that still collides with a save is run once more — but never if it already saved
  something (no double audit entries) or was already waiting for the lock.
- Larger SQLite page cache; the pending API loads related records in bulk.
- Same 3,300-invoice data, one worker, 10 users for 60 s: failed requests 2 → 0, slowest 30.2 s → 2.9 s,
  95th percentile 5.8 s → 1.5 s, 634 → 811 requests. Readers + writers together: 693 → 1,327 requests in ~47 s
  with identical correctness (no duplicate invoices / slip numbers, integrity OK).

### Tests
- `tests/test_v12_fixes.py`: 26 new regression tests, incl. a v1.1 → v1.2 database upgrade test.
  Suite: 97 on SQLite; 95 + 2 SQLite-only skips on PostgreSQL 16.

## v1.1.0 — 26 Sep 2026 (QA fixes)

Result of an end-to-end QA round on v1.0 (live on the owner's laptop + automated security / edge-case testing).
Full details, reproduction steps and before/after screenshots: `docs/CTDMS_QA_Report.pdf`.
No database migration is needed.

### Security / data scope
- SEC-01 Transporter bills are now restricted to the user's company **and CFA** (list, detail, all actions, approvals, reports).
- SEC-02 Import batches are only visible to the uploader or users whose scope covers the batch CFA.
- SEC-03 Changing an existing ERP code mapping requires Masters-edit rights.
- SEC-04 NaN / Infinity / values >= 1e12 are rejected everywhere (one such row used to crash dashboard and reports).
- SEC-05 20 MB / 100,000-row limit on every importer (rates and bills had none); uploads are read with a bounded buffer.
- SEC-06 Excel / CSV exports neutralise formula injection (text starting with = + - @).
- SEC-07 Freight rates checked against the CFA scope (view: CFA or company-wide; change: fully in scope).
- SEC-08 "Must change password" is enforced on every page and on API token issue.
- SEC-09 Audit IP taken from the trusted-proxy setting (FORWARDED_ALLOW_IPS, default 127.0.0.1) - no more X-Forwarded-For spoofing.
- SEC-10 Login message states the lockout rule; identical text for unknown users.
- SEC-11 Content-Security-Policy header; /api/docs hidden in production (CTDMS_API_DOCS_PUBLIC).
- SEC-12 Common / demo passwords and passwords containing the username are refused.
- SEC-13 Company / CFA master pages and API lists respect scope.
- SEC-14 Demo logins default to OFF in production; production refuses to start with the default / short secret key.

### Bugs
- BUG-01 Damaged / fake / protected Excel files show a clear message instead of HTTP 500 (invoice, rate and bill import).
- BUG-02 Non-numeric or oversized ids / page numbers / filters no longer cause HTTP 500.
- BUG-03 API validation errors with raw bytes return 422 instead of 500.

### Reconciliation
- REC-01 System freight counts only valid lines; approval blocked while problem lines exist (double-payment risk).
- REC-02 A user who edited a bill cannot approve it; approved/paid bills cannot be edited.

### Usability
- Confirmation dialogs on Complete, bulk Complete, Void, Cancel, Reject, Mark paid, Merge, Cancel batch.
- No false "Dispatch date/time missing" warnings (fields are pre-filled).
- Bulk result: Complete enabled only after printing; grouped errors; stays on the Slip tab.
- Pending queue: blocked (rate missing / exception) invoices sorted last; printed / not-printed shown per slip.
- Read-only roles see the slip number and a "Read-only for your role" badge instead of "No active slip".
- No dead invoice links for roles without invoice access (reports, MIS).
- Import warns on implausible rows (> 2,000 boxes or freight > 10% of sales); dispatch warns on odd vehicle numbers and LR re-use.
- Mobile: collapsible filters, action bar only when something is selected; approvals decision column always visible.
- Labels linked to fields for screen readers; demo-login rows fully clickable; reprint reason 5 characters everywhere.

### Tests
- 43 new regression tests (`tests/test_qa_fixes.py`); 71/71 pass on SQLite and PostgreSQL 16.
