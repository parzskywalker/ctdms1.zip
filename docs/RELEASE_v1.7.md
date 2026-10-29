# CTDMS v1.7 — release guide

> **v1.7.1** (v1.7.2 below goes further): (1) a Transporter trip goes by Local auto only and a Direct pickup is Regular only; (2) the case labels are printed before the gatepass; (3) a case-control gatepass is marked gone out only by the gate staff's scan of every case and then the gatepass - no button for anybody. Migration **0016** adds the role *Dispatch / Gate Staff* (permission `gate.scan`); nobody else is given it, so **create the gate staff users (or give `gate.scan` to the right people) before switching a depot's case control on**. The one-invoice "slip" screens and the API still create the old Regular single-transporter slip (kept for older data); use Scan & Gatepass for real dispatch.

What is new, how to upgrade, how to go back, and how to bring each depot to the gate flow.

> **v1.7.2 (this guide applies): case control is MANDATORY at every depot and goods are marked gone out ONLY by the gate staff's scan** (every case label, then the gatepass). There is no depot switch, no gone-out button for anybody (the permission `dispatch.complete` no longer exists - migration **0017** takes it off every role and user, audited), no one-invoice slip, no bulk dispatch and no API call for it. **Create your gate staff (role *Dispatch / Gate Staff*, permission `gate.scan`) before go-live: after 0017 only they - and administrators, who hold every permission - can send a trip out.** Gatepasses made before v1.7 that are still at the depot get their labels from the gatepass screen (*Make the case labels for this gatepass*). Exception: a manager can record that cartons are missing (reason, audited), after which the gatepass is still scanned.

## 1. What changed (short)
| Area | v1.7 |
|---|---|
| Access | Roles + per-user grants/denials; scope by company, depot, area (location), customer type, customer (Administration › Users) |
| Areas | A customer has a default delivery area and may have more; mismatches are accepted (default) or held |
| Transporters | Serve chosen depots; only eligible transporters are offered on a gatepass (rate card + depot) |
| Freight | Rate exceptions per depot / company; the gatepass shows how freight was calculated |
| Cases | One barcode per carton (Code 128, digits + check digit); labels in any printer's print dialog |
| Gate | Guard scans every case; gate-out blocked until all scanned, or an authorised exception with a reason |
| Local-auto trip | One gatepass = one trip; each invoice goes to a receiving transporter (split allowed) |
| Garage Pawati | White slip per receiving transporter × customer × company, 4 per A4, printed before the auto leaves |
| LR books | Builty ranges per transporter; next LR assigned per consignment, changeable (logged) |
| Reports | Garage Pawati Register, Gate Exception Report (plus the earlier ones) |

## 2. Upgrade (do this in order)
1. **Backup** — `python -m app.cli backup` (PostgreSQL: also `pg_dump -Fc`). Keep it until the first week is over.
2. Stop the app, install the new version, start it. Migrations **0010 – 0017** run automatically and are additive.
3. Check — `python -m app.cli check`, open `/health` (shows `1.7.0`), sign in, open Gatepass › New.
4. Every old gatepass stays readable. An old gatepass that is **already gone out** is history and stays as it is; one that is
   **still at the depot** (`LEGACY`, not gone out) cannot leave until its labels are made: open it, press *Make the case labels
   for this gatepass*, print the labels, then print the gatepass. `start_demo` on a demo database also adds the demo gate users.

**v1.7.2 gates:** the full suite was run on SQLite only (no PostgreSQL server was available on the development PC) - run it once against PostgreSQL (`CTDMS_TEST_DATABASE_URL=postgresql+psycopg://...`, empty UTF8 database) and repeat the migration on a copy of the live database before the real upgrade.

Release gates run for v1.7.0: full test suite on SQLite **and on PostgreSQL 16**; migrations 0 → 0015, 0015 → 0012 → 0015 on
PostgreSQL; upgrade of a v1.6-style database with data. Repeat the PostgreSQL migration on a **copy of the live database**
before the real upgrade (`createdb ctdms_copy; pg_restore …; CTDMS_DATABASE_URL=… python -m alembic upgrade head`).

## 3. Rollback
* **Before anyone used v1.7 features:** `python -m alembic downgrade 0009` drops only the new, empty tables/columns
  (0015 → 0014 → 0013 → 0012 … in that order). Then start the old version.
* **After go-live at a depot** (cases, Pawatis, LR books exist): do **not** downgrade — the downgrade would delete that data.
  Restore the backup taken in step 1 (work done since is re-entered from the gatepass print-outs), start the old version.
* There is no "switch case control off" any more (v1.7.2). Going back to v1.7.1 (`alembic downgrade 0016`) returns the
  permission `dispatch.complete` to Depot Managers and Dispatch Operators; it does not undo the labels already made.

## 4. Bringing each depot to the gate flow (before its first day on v1.7.2)
1. Check the depot has its label printer and scanner (section 5). Case labels are not optional.
2. Administration › Pawati Numbering: set the **starting number** (the first slip number for the depot) *before* the first trip.
2b. Administration › Users: create the gate staff (role *Dispatch / Gate Staff*) for the depot **before go-live** - they are the only ones (besides administrators) who can scan and mark trips gone out.
3. Transporters that give their own builty book: tick "uses builty book", import the range (Dispatch desk › LR Books).
4. Walk the gate staff through the Guided Training lessons "Gate check, LR pending and common errors" and "Local-auto trip to transporters: Garage Pawati".
5. Hardware trial (section 5), then the first real day with a supervisor at the gate.

## 5. Hardware trial at Guma (half a day, any printer / scanner)
Needs: the office printer(s) used for labels and for A4, one USB scanner (keyboard-wedge — it "types" the code and presses Enter).
| # | Check | Pass when |
|---|---|---|
| 1 | Print one gatepass label sheet (Labels screen, preview first) at the label size in System Settings | Barcode is crisp, not clipped, scales 100 % |
| 2 | Scan every printed code into Gate Check's scan box | Every scan is OK; the cursor stays in the box |
| 3 | Scan a code twice; a code from another gatepass; a torn/short code | Red STOP with the reason each time |
| 4 | Scan from 30–40 cm, under the loading-dock light, on the glossy and the plain label stock | ≥ 99 % first-time reads; note the stock that fails |
| 5 | Print a Garage Pawati sheet on plain white A4 (4 slips) | Slips align to the quarters; cut lines fall on the page; signature lines visible |
| 6 | One real trip, supervised: gatepass → labels → gatepass printed → Pawatis → scan every case → scan the gatepass → LR | No step needs the mouse; total time recorded |
Record: printer model, label stock, scanner model, any code that failed to read, time per carton. If the scanner adds a prefix/suffix
or types in the wrong layout, set it to "Code 128, Enter suffix, no prefix".

## 6. Acceptance checklist (tick before go-live)
- [ ] Sidebar and permissions: a custom role sees only its screens; a depot-restricted user sees only that depot.
- [ ] Gatepass of one customer in one area: transporters offered are only eligible ones; a missing rate says why.
- [ ] Two companies on one gatepass need the explicit tick; every invoice keeps its own company and number.
- [ ] Case labels: counts match; reprint asks for a reason; a void gatepass voids its labels.
- [ ] Gate: there is no gone-out button or API call anywhere; an unscanned case blocks the gatepass scan; an exception needs permission and reason and the gatepass is still scanned.
- [ ] Local-auto trip: receivers per invoice and a split; Pawati print is required before the gatepass scan; reprint needs a reason.
- [ ] Receipt: ticked by hand, and by an LR entered after the trip went out; the Pawati Register shows days open.
- [ ] LR book: next number given per consignment, change is logged, empty book warns, void releases the numbers.
- [ ] Audit log shows each of the above. Reports: Garage Pawati Register, Gate Exception Report export to CSV/Excel.
- [ ] Backup restored once onto a spare machine and the app opened.

## 7. Still to be confirmed by the owner
1. A consignment with invoices of **two companies**: does it need one Pawati per company (current) or one combined slip?
2. On the first printed slip: **Amount** = receiver's share of the invoice value, **Freight** = freight of that receiver's cases.
3. Whether the ERP file will carry the ship-to code (would remove manual area choice).
4. Printer and scanner models bought for the trial (section 5 results decide label stock and size).
5. The Guided-Training lesson for the Pawati flow (Dispatch Operator path, `d03-pawati`) teaches the slip as it is now; if item 2 changes the slip's Amount / Freight wording, update the lesson text.
