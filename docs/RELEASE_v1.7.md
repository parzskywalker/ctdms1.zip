# CTDMS v1.7 — release guide

What is new, how to upgrade, how to go back, and how to switch it on depot by depot.

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
2. Stop the app, install the new version, start it. Migrations **0010 – 0015** run automatically and are additive.
3. Check — `python -m app.cli check`, open `/health` (shows `1.7.0`), sign in, open Gatepass › New.
4. Nothing changes for users until a depot is switched on (section 4). Every old gatepass stays readable; old gatepasses
   behave as before (`LEGACY`: count and confirm at the gate).

Release gates already run: full test suite on SQLite **and on PostgreSQL 16**; migrations 0 → 0015, 0015 → 0012 → 0015 on
PostgreSQL; upgrade of a v1.6-style database with data. Repeat the PostgreSQL migration on a **copy of the live database**
before the real upgrade (`createdb ctdms_copy; pg_restore …; CTDMS_DATABASE_URL=… python -m alembic upgrade head`).

## 3. Rollback
* **Before anyone used v1.7 features:** `python -m alembic downgrade 0009` drops only the new, empty tables/columns
  (0015 → 0014 → 0013 → 0012 … in that order). Then start the old version.
* **After go-live at a depot** (cases, Pawatis, LR books exist): do **not** downgrade — the downgrade would delete that data.
  Restore the backup taken in step 1 (work done since is re-entered from the gatepass print-outs), start the old version.
* Safer than rollback: switch the depot's case control **off** (Master Settings › Depots). New gatepasses of that depot
  behave as in v1.6.1 again; Pawati only arise on local-auto trips with receiving transporters, so use *Regular* there.

## 4. Switching on, one depot at a time (Guma first)
1. Master Settings › Depots › Guma: tick **Case barcode label scanning**. (Other depots stay as they are.)
2. Administration › Pawati Numbering: set the **starting number** (the first slip number for the depot) *before* the first trip.
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
| 6 | One real trip, supervised: gatepass → labels → Pawatis → scan → gate-out → LR | No step needs the mouse; total time recorded |
Record: printer model, label stock, scanner model, any code that failed to read, time per carton. If the scanner adds a prefix/suffix
or types in the wrong layout, set it to "Code 128, Enter suffix, no prefix".

## 6. Acceptance checklist (tick before go-live)
- [ ] Sidebar and permissions: a custom role sees only its screens; a depot-restricted user sees only that depot.
- [ ] Gatepass of one customer in one area: transporters offered are only eligible ones; a missing rate says why.
- [ ] Two companies on one gatepass need the explicit tick; every invoice keeps its own company and number.
- [ ] Case labels: counts match; reprint asks for a reason; a void gatepass voids its labels.
- [ ] Gate: unscanned case blocks gate-out on all four paths (screen, one-invoice, bulk, API); exception needs permission and reason.
- [ ] Local-auto trip: receivers per invoice and a split; Pawati print is required before gate-out; reprint needs a reason.
- [ ] Receipt: ticked by hand, and by an LR entered after gate-out; the Pawati Register shows days open.
- [ ] LR book: next number given per consignment, change is logged, empty book warns, void releases the numbers.
- [ ] Audit log shows each of the above. Reports: Garage Pawati Register, Gate Exception Report export to CSV/Excel.
- [ ] Backup restored once onto a spare machine and the app opened.

## 7. Still to be confirmed by the owner
1. A consignment with invoices of **two companies**: does it need one Pawati per company (current) or one combined slip?
2. On the first printed slip: **Amount** = receiver's share of the invoice value, **Freight** = freight of that receiver's cases.
3. Whether the ERP file will carry the ship-to code (would remove manual area choice).
4. Printer and scanner models bought for the trial (section 5 results decide label stock and size).
5. The Guided-Training lesson for the Pawati flow (Dispatch Operator path, `d03-pawati`) teaches the slip as it is now; if item 2 changes the slip's Amount / Freight wording, update the lesson text.
