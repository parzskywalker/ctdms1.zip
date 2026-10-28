# CTDMS v1.7 — design checkpoint (Phase 1)

Status: design only for everything below Section 1; **Section 1 (sidebar) is implemented**. Nothing here changes data.
Decisions are the owner's answers to the plan (`B1`–`B14`, see the approved plan, Section 21).

## 0. Terms (used consistently in code, screens and reports)

| Term | Meaning |
|---|---|
| **Gatepass** (`DispatchSlip`) | the gate unit: one vehicle / one pickup. A **local-auto trip** is a gatepass with a local auto (B11). |
| **Consignment** | the goods of one **customer + delivery location** inside a gatepass (existing: one LR per customer). |
| **Allocation** | the part of an invoice line carried to one **receiving transporter** (new). Unit of freight, LR and Pawati. |
| **Case** | one physical carton with a unique barcode (new). Belongs to one invoice and one allocation. |
| **Garage Pawati** | handover slip, one per (**receiving transporter, customer, company**) on a trip (B1). Stays with the transporter. |
| **Local auto** | `OutsideAuto` — carries premises → receivers. **Receiving transporter** = `Transporter`. |

## 1. Sidebar scroll position — implemented

Cause: server-rendered pages + `.side` is its own scroll container (`position: sticky; height: 100vh; overflow-y: auto`), rebuilt on every page load.
Fix: inline script right after `</aside>` in `base.html` restores `scrollTop` from `sessionStorage['ctdms.sideTop']` before first paint, saves it on every scroll and on `pagehide`, then reveals the selected entry only if it is hidden (6 px margin). The login page clears the key. Verified with `tests/e2e_sidebar.py` (19 checks: click, repeated clicks, near-top entry, Back/Forward, keyboard Tab+Enter, F3, redirecting form post, reload, typed URL). Before the fix 5 of 18 passed.

## 2. Scope audit (current code) — result

Every report function takes its rows from one of seven helpers: `apply_invoice_scope` (invoices, most reports, dashboards), `apply_cfa_scope` (slips: gatepass register, local-auto trips, notifications), `apply_strict_scope` (bills, rates), `apply_scope_allow_null_cfa` (rate changes), `_bill_base`, `_consignment_rows`, `_grouped`. No report builds its own unscoped invoice query (only `rpt_audit`, Administrator-only). `tests/test_v17_phase1.py` is a **tripwire**: it fails if a new `rpt_*` function mentions none of the helpers (it proves the helper is used, not that every inner query is scoped).

**Still to audit in Phase 2 (not covered above):** dropdown sources (`filter_options`, `active_lists`), `/api/v1/masters`, quick search / tracking / delivery search, Approval History, recon screens, notifications, imports, `/dispatch/*` and `/gatepass/*` routes. Direct `role.has(...)` calls bypass per-user overrides and must move to `user.can(...)` / the effective set: `training/progress.py:61`, `web/admin.py` (user save guards), `templates/admin/roles.html` summary, `training/trainee.py` (practice user).

Consequence for Phase 2: adding area / customer-type / customer scope means extending **these helpers**, not every report. Three places need a rule beyond "add a WHERE":
* **Gatepasses** (`DispatchSlip` has only `cfa_id`): visible iff **all** its invoices are in scope (B6) → `apply_slip_scope` with `NOT EXISTS (line whose invoice is out of scope)`.
* **Bills** (`TransporterBill` has company / depot only): bills span customers → visible only to users whose area / customer-type / customer scope is "all" (recommended), otherwise hidden.
* **Dropdown sources** (`filter_options`, `active_lists`, customer / location lists): filter by the same scope.

## 3. Phase 2 — permissions and operational scope (design)

* Remove `FIXED_BASELINE` / `baseline_allows` (my v1.6.1 hard lock); `DEFAULT_ROLES` stay as editable templates. Migration `0009` already stripped wide permissions, so nothing re-opens automatically.
* `user_permissions(user_id, permission, effect GRANT|DENY, reason, granted_by, granted_at)`; effective = role ∪ GRANT − DENY (DENY wins). `User.can()` reads an effective set computed once per request. Training practice users receive the **effective** set.
* `user_scope(user_id, dimension AREA|CUSTOMER_TYPE|CUSTOMER, ref_id | ref_code)` (customer types are stored by their code in `ref_code`, areas and customers by id in `ref_id`) + `users.all_areas / all_customer_types / all_customers` (default true) + `users.responsibility`. AND across dimensions, OR within one.
* **Last-Administrator guard by effective permissions**: at least one active user must hold effective `admin.users` and `admin.roles`; checked on role save, user save, override save, deactivation.
* New permissions: `case.print`, `case.reprint`, `gate.scan`, `gate.exception`, `dispatch.group_cross_company`, `transporter.override_eligibility`, `pawati.generate/print/cancel/receive`, `lr_book.manage`.

## 4. Schema (all additive; migrations 0010–0014)

Conventions: no historical row rewritten; new columns nullable or defaulted so 1.6.1 behaves unchanged; SQLite and PostgreSQL (portable SQLAlchemy Core); every table has `created_at`.

**0010 permissions** — `user_permissions`, `user_scope`, `users.all_areas/all_customer_types/all_customers` (default true), `users.responsibility`.

**0011 areas & eligibility** — `customer_delivery_locations(id, customer_id, location_id, label, address, ship_to_code NULL, is_default, is_active)` UNIQUE(customer_id, location_id, label); `invoices.customer_location_id NULL`; `transporter_depots(transporter_id, cfa_id)` (no rows = serves all depots); `transporters.uses_lr_book` (default false); exception codes `AREA_MISMATCH`, `AREA_AMBIGUOUS`.

**0012 freight (slabs deferred, B4)** — `transporter_rates.cfa_id NULL`, `.company_id NULL` (explicit exceptions only; ranking: depot+company > depot > company > general; equal-rank overlap refused); `dispatch_slip_lines.rate_id`, `.calc_note JSON` (rule chosen, quantity, rate, amount), `.consignment_no`.

**0013 cases & verification**
* `dispatch_cases(id, case_code UNIQUE, invoice_id, slip_id, line_id, allocation_id NULL→set at generation, seq_in_invoice, seq_in_consignment, consignment_total, status LABEL_PENDING|LABELLED|SCANNED|DISPATCHED|MISSING|VOID, created_at, voided_at)`; UNIQUE(slip_id, invoice_id, seq_in_invoice); index (slip_id, status).
* `case_scans(id, slip_id NULL, case_id NULL, scanned_code, result, scanned_by_id, scanned_at, station)` append-only; index (slip_id, scanned_at).
* `label_print_log(id, slip_id, batch_no, case_id, is_reprint, reason, output, status PRINTED|FAILED, printed_by_id, printed_at)`.
* `dispatch_slips.case_control` (`LEGACY` for every existing row | `REQUIRED`), `.verified_cases`, `.gate_exception_reason/by/at`; `cfas.case_control_enabled` (default false → Guma first, B10).
* **Training codes cannot hit the live gate**: practice databases have their own sequences, so codes generated in Training Mode start with the digit `9` (live sequence stays below 900,000,000); the live scan endpoint rejects them as "training label".
* **Block reservation**: a gatepass reserves its N case numbers with one UPDATE of the sequence row (not N `next_sequence` calls), so a 200-case gatepass does not hold SQLite's write lock.
* **Scan any case to open its gatepass (kept from 1.6.1)**: Gate Check resolves a **case code first**, then the gatepass number (today `_find_slip` treats all-digit input as a gatepass suffix, so the case lookup runs before that branch).
* **Case code**: digits only, 9-digit global sequence + 1 check digit (10 digits), Code 128. Digits-only because keyboard-wedge scanners mis-type letters under some layouts / Caps Lock. Invoice identity is printed as text on the label and stored in the DB; the code never replaces the invoice number.

**0014 allocations, Pawati, LR books**
* `dispatch_allocations(id, slip_id, line_id, invoice_id, receiving_transporter_id, cases, rate_per_case, freight, lr_no NULL, lr_date NULL, calc_note)` UNIQUE(line_id, receiving_transporter_id). Per-line sum of `cases` = `DispatchSlipLine.cases` (service assert + integrity query). Pre-1.7 gatepasses have no rows and are read as one implicit allocation.
* `garage_pawatis(id, cfa_id, pawati_no INT, slip_id, receiving_transporter_id, customer_id, location_id, company_id, status DRAFT|GENERATED|PRINTED|CANCELLED, print_count, cartons, bill_nos TEXT, amount, freight, snapshot JSON, supersedes_id NULL, cancel_reason, received_at NULL, received_by_id NULL, received_via LR|MANUAL NULL, generated_by_id, generated_at)` UNIQUE(cfa_id, pawati_no). `garage_pawati_lines(pawati_id, allocation_id, invoice_id, cases)`; `pawati_print_log`.
* **Numbering (B3)**: per depot, plain integers from `sequences` key `PAWATI:<depot>`; Administration › Setup lets the Administrator set the **starting number** (e.g. 191) — allowed until the first Pawati of that depot exists, afterwards only with an audited reason and never below the highest issued number.
* **Unit of a Pawati (B1)**: one slip per (receiving transporter, customer, company), matching the owner's yellow slip (No., Date, Garage, To, Cartons, Bill No., Amount, Freight, Signature), 4 per A4 yellow sheet. Header company line = that company, so cross-company groups print separate slips. **Owner to confirm** (B1 said per customer per transporter): splitting by company can print more slips. The whole header is built from masters — firm name from settings, principal from the company, address and phones from the **depot** (Kamal Vihar's differs from Guma's) — not copied from the photo. *Assumption to confirm on the first printed sample:* Amount = invoice value, Freight = calculated freight of that consignment.
* `dispatch_cases.allocation_id` is the only link from a case to its receiver — no second copy of the transporter id, so a case cannot be in two Pawatis.
* **Receipt (B14)**: `received_at` is set (a) manually by `pawati.receive`, or (b) automatically when the transporter's LR is entered **after** the goods have left. **Rule:** an LR assigned automatically from an LR book at gatepass generation does **not** count as receipt (it exists before the auto leaves); only a later LR entry/confirmation does.
* **LR books** — `lr_books(id, transporter_id, label, received_on, status)`, `lr_numbers(id, transporter_id, lr_no, book_id, status AVAILABLE|USED|VOID, allocation_id NULL, void_reason, used_at)` UNIQUE(transporter_id, lr_no). Only for `transporters.uses_lr_book`; next free number is assigned **inside the gatepass transaction with a row lock / conditional UPDATE** so two operators cannot take the same number. LR key = (slip, receiving transporter, customer, location).

## 5. State machines

* **Creation funnel (verified)**: every way of making a gatepass ends in `create_gatepass` — the new-gatepass screen directly; the single-invoice F8 dispatch entry, `bulk_apply` and the API `generate` endpoint through `generate_slip(...)` → `create_gatepass(legacy=True)`. Cases, allocations and `case_control` are therefore created in **one place**; no path can produce a slip without cases in a case-control depot. Invoice **cancel**, **cancel-dispatched**, **reinstate** and gatepass **void** all move the cases (`VOID`, or back to `LABEL_PENDING` on a re-generated gatepass).
* **Case**: LABEL_PENDING → LABELLED (print confirmed) → SCANNED (guard) → DISPATCHED (gate-out); MISSING (guard exception); VOID (gatepass voided). A VOID case scans as "voided"; regenerating issues new codes.
* **Gatepass verification** (`REQUIRED` only): OPEN → COMPLETE (all cases SCANNED) or EXCEPTION (authorised reason). `gate_out()` / `completion_blockers()` enforce it, so the four completion entry points (`/gatepass/{id}/gate-out`, `/dispatch/{id}/complete`, `/dispatch/bulk/complete`, API complete) are all covered. Invoices of a `REQUIRED` gatepass complete only through gate-out of the gatepass.
* **Pawati**: DRAFT → GENERATED → PRINTED → (REPRINTED = print_count>1) → CANCELLED (reason; regenerate creates a new row with `supersedes_id`); locked after gate-out. Local-auto path blocker: every case needs an allocation before gate-out (B14).

## 6. Services (new / changed)

`scope.py` (+area/customer helpers, `apply_slip_scope`); `permissions` (effective set); `transporter_rate_service` (+rank, exceptions); `eligibility_service` (eligible transporters, intersection, override audit); `customer_area_service` (resolution order: ship-to → file location if one of the customer's locations else `AREA_MISMATCH` → default → `AREA_AMBIGUOUS`); `dispatch_service` (+allocations, consignments, cross-company confirm, DIRECT never with local auto); `case_service` (generate, print log, reprint keeps code); `verification_service` (scan, reconcile, exception); `pawati_service`; `lr_book_service`. Existing signatures stay; `plan_gatepass` gains rules, never removes them.

## 7. Migration, rollback, release safety

Backup first (`python -m app.cli backup`); each migration additive; downgrade drops new empty tables/columns only — otherwise **restore the backup**. v1.6.1 data after upgrade: every gatepass `LEGACY` (count-and-confirm), every rate prices as before, every user keeps all areas/customers. One depot at a time (`cfas.case_control_enabled`), Guma first. Release gates: SQLite **and** PostgreSQL migration run, rehearsal on a copy of live data, first printer/scanner trial at Guma.

## 8. Phase 3 — as built (differences from the design above)

* Migrations **0011** (areas, depots served, LR-book flag) and **0012** (rate exceptions, line breakdown, grouping flags, role grants). `transporter_rates.charge_basis` and the slab table were **not** added (slabs deferred by the owner, B4/B5); the freight stays one rate per case per transporter / area.
* `customers.location_id` stays the default area; `customer_delivery_locations` lists the others. `area.mismatch_action` (ACCEPT default = v1.6.1 behaviour | EXCEPTION) controls what a file area outside the customer's locations does; `AREA_AMBIGUOUS` always holds the invoice until chosen.
* Eligibility = serves the depot (`transporter_depots`, no rows = all) AND (transporter-rate mode) an approved rate for every (area, depot, company) on the gatepass; override only for the depot rule. Enforced in `plan_gatepass`, offered in both gatepass screens.
* `dispatch.group_cross_company` and `transporter.override_eligibility` granted by migration 0012 to the roles that could already do the work. The seed data and the Guided Training scenario group across companies on purpose (they set the flag).
* A practice (Training) database made by an older version is upgraded in place before use (`app.training.runtime.sync_schema`): every new column must therefore be nullable or have a server default.
