# User guide (day-to-day)

## What changed in v1.6

* **Depot** replaces "CFA" everywhere: a depot is the physical godown goods leave from. One city may have several
  depots (Raipur: Guma, Kamal Vihar); each has its own code. Reports work by City and by Depot.
* **First-day set-up**: Master Settings › **Initial Master Upload** — download the workbook, fill every sheet once
  (instructions inside), upload, read the preview, import. Afterwards add single records under Master Settings.
* **Customer Types** (Retailer, Stockist, Distributor, Hospital, Institution, Chain Pharmacy, Other) are a master with
  short codes; Company, Depot and Customer also have a short "Recognition code".
* **Invoice Recognition Code** (e.g. `ABC-RET-C025-GUMA-001245`) is made automatically beside the original ERP invoice
  number, which never changes. Pattern and switch: Master Settings › Invoice Recognition Code. A missing master code
  shows "code not generated" with the reason; fix the master and press *Regenerate codes*.
* **Local autos**: choose the local auto on the gatepass. *Per trip* — type or confirm the one amount for the whole
  trip (it is shared over the invoices by boxes, never multiplied). *Per box* — boxes × rate from Local Auto Box Rates.
  Long-distance transporters work as before.
* Product / SKU lines are not used in v1.6.

## Guided Training (first-time users) and Practice Mode

**Help › Guided Training / Practice Mode.** Press *Start* (or *Continue*): CTDMS gives you a private practice copy with
the v1.6 demonstration data, opens the first screen and a panel on the right. The panel explains each step (what it
does, why it matters, what to do, which keys to press, what you should see) and highlights the field. You do the work
on the real screen; CTDMS checks the result in your practice copy and moves on. Practical steps only pass when the
result is really there (a gatepass generated, printed and gone out; a bill approved by a second person ...).

* Keys: **Ctrl+Enter** = Next / Check · **Ctrl+/** = into the panel (Tab between its buttons, Esc back to the screen) ·
  all CTDMS keys work as usual. *Watch demonstration* plays the matching chapter of the v1.6 guide video.
* The orange **TRAINING MODE** bar is always on screen. Prints and exports say *TRAINING - NOT VALID FOR BUSINESS USE*.
  No message, e-mail or ERP sync leaves CTDMS. Live data is never touched - only your progress is saved.
* Log out at any time; *Continue* resumes at the same step. *Replay lesson* repeats a lesson; *Prepare practice data*
  resets your practice copy to the lesson's starting point. *Save & exit* returns to live work.
* Two-person steps (rate or bill approval): press *Act as Demo Checker* in the panel, then *Act as myself*.
* Learning paths: Master Administrator (15 lessons), Dispatch Operator, Depot Manager, Checker / Approver, Accounts,
  Management, Read-only Viewer - chosen from your role; the Master can only assign the tutorial that belongs to the
  user's role (a Dispatch Operator is not given the Master Administrator tutorial).

**For the Master - Administration › Guided Training:** switch compulsory training ON / OFF (also in System Settings ›
Guided Training). ON: users created from then on must finish their path before live screens open; OFF: everybody works,
training stays voluntary; completed training is never removed. Per user: path, required or voluntary, status,
progress, dates; *Assign* another path, *Require* / *Not required*, *Reset* (progress and practice copy). Every action
is in the audit log. The Master is never locked out of Administration; as a last resort
`python -m app.cli training-release --username <name>` (or `--all-off`).

## Keyboard (v1.6) - the mouse is optional

The same key does the same job on every screen; the dark bar at the bottom shows the keys of the screen, **F1** shows
all of them.

| Key | Meaning | | Key | Moving around |
|---|---|---|---|---|
| F1 | Help | | Enter | Next field; after the last one the cursor is on the main button - Enter again presses it |
| F2 | New / Add (new gatepass on the dispatch desk) | | Shift+Enter | Previous field |
| F3 | Go to a screen by typing its name (Ctrl+K) | | Type | In a choice box: code (GUMA, C025, SRL) or part of the name, then ↓↑ and Enter |
| F4 | Search box | | ↓ ↑ | Move in a list; Enter opens the row; ↓ from a search box goes into the list |
| F6 | Save (Ctrl+S) | | Space | Tick / untick |
| F7 | Edit | | Esc | Close the list / question, else back one screen (to the same list and row) |
| F8 | Generate / print the gatepass or slip | | / | Find an invoice, LR or gatepass from anywhere |
| F9 | Complete dispatch (gate-out) | | | |
| F10 | Reports | | | |

**A dispatch without the mouse:** F2 → type the depot code, Enter → type/scan invoice numbers, Enter (or ↓ into the
waiting list, Enter) → Enter on the empty box → arrows choose the mode, Enter → type the transporter or local-auto code,
Enter → Enter through the fields → Enter on *Generate gatepass* → F8 print → F9, Enter (gate-out) → Esc back to the list.
Delete, void, cancel and reject always ask first with **No** pre-selected.


## Dispatch desk (warehouse operator) — v1.3 gatepass flow

1. **Scan & Gatepass** (menu *Dispatch desk*). Point the 2D barcode scanner at the **e-invoice QR** printed on each
   invoice (or type the invoice number and press Enter). An invoice not yet in CTDMS is captured from the QR
   (company, customer, number, date, value); one already imported is simply added. Type the cases when asked.
   You can also tick invoices on *Pending Dispatch* and press **Make one gatepass**.
2. Check the **cases** per invoice. A count different from the ERP's needs a checker (or a box-change request).
3. Choose **who carries the goods**:
   * *Transporter* — booked with a transporter; charged per case from their **rate card** for each area.
     *Goods reach the transporter by*: Regular, or an **Outside auto** (its per-trip / per-case charge is added).
   * *Transporter – direct pickup* — the transporter collects from our warehouse; their direct-pickup extra is added.
   * *Outside auto* — a hired auto delivers straight to the customer (per trip or per case).
   * *Self pickup* — the customer collects; no charge.
4. The right-hand panel shows freight, extras, **total charged** and **amount paid now** (cash for autos and CASH
   transporters; TBB transporters bill later). Invoices whose area has no rate on that transporter's card are
   flagged — add the rate under *Transporter Rate Cards* (a second person approves it).
   **v1.7 – who is offered.** Only transporters that can carry these goods are listed: they serve the depot (Master
   Settings › Transporters › *Depots served*; nothing ticked = every depot) and have an approved rate for **every area**
   on the gatepass. With invoices for several areas, only a transporter that has all of them appears; if there is none
   the screen names the areas and says to split the gatepass or ask for a Master update. A manager who holds the
   permission can tick *Choose a transporter that does not serve this depot* and type a reason - it is audited and shown
   on the gatepass. A rate exception (see *Transporter Rate Cards*) for this depot / company is used automatically, and
   the gatepass keeps a note of *how the freight was calculated*.
   **Companies.** Invoices of different companies go on one gatepass only if you tick *Group across companies* (Enter
   on the empty scan box takes you to the tick box, Space ticks it). It is recorded; every invoice keeps its own company,
   number and cases. The *Consignments* panel shows one line per customer and delivery area.
   **Delivery area.** A customer with several delivery locations (Master Settings › Customers › *Delivery locations*)
   has the invoice held as *choose the delivery location* until you pick one on the invoice screen (recorded). If an
   import file names an area that is not one of the customer's, System Settings › `area.mismatch_action` decides:
   ACCEPT (use the file's area, as before) or EXCEPTION (hold the invoice until the area is confirmed).
5. **Generate gatepass** → one number for the whole pickup. **Print** it (A5/A4/PDF): it carries a barcode, every
   customer with its invoices, cases and charge, the transporter's name/contact/GST/payment mode — no vehicle number.
6. **Gate check**: the guard scans the gatepass barcode, counts the cases and presses **Confirm gate-out**. All its
   invoices become DISPATCHED. A void, unprinted or already-used gatepass shows a red STOP.
   **v1.7 – case control.** For a depot with case control the guard scans every case label on *Gate check* (the cursor
   stays in the scan box). A green tick means counted; a red STOP says why (wrong gatepass, void label, same case twice,
   training label...). *Confirm gate-out* appears only when every case is scanned. If cartons are genuinely missing, a manager
   records an *exception* with a reason; the cases are marked missing and it is audited.
7. **LR numbers** come later from the transporter: type them on the gatepass page (one per customer) or on
   **LR Pending** in bulk. They can be corrected (with a reason) until the transporter's bill is approved.

8. **Case labels with a barcode per case** (v1.7, depots with case control switched on): on the gatepass page press
   *Print the labels*. Every carton has its own code (shown under the barcode) and "case k / n" of its consignment. Print
   with the normal print dialog, then answer *Did all labels print?* - *No* keeps them in the to-print list. A torn label
   is reprinted from *Reprint* (same code, a reason is asked). The label size is a setting (System Settings › Case labels).
   **v1.7 – local auto, several transporters, Garage Pawati.** For a *Transporter + Local auto* trip, choose the receiving transporter on each
   invoice (use *split* to send some cases to another). After the gatepass is made, open *Garage Pawati* on it and print the
   white slips (4 per A4) - the auto cannot be gated out before that. When the garage signs, press *Received* (or just enter the LR
   later; that records it). A reprint needs a reason. *LR Books* (Dispatch desk) holds each transporter's builty numbers; CTDMS gives the next
   one to each consignment and you may change it. If one invoice is split between two transporters, each gets its own LR row. Practise it in Guided Training: lesson "Local-auto trip to transporters: Garage Pawati".
   (Older description, depots without case control:) **Case labels** (gatepass page): one label per case — customer, area, invoices, carrier, "case 3 / 12" and the
   gatepass barcode (scanning any case at the gate opens its gatepass). 100 × 50 mm label printer or A4 sheets.
9. **E-way bill**: on the consignment's *Track* page (link on the gatepass). When System Settings ›
   `gate.eway_required_above` is set (e.g. 50000), gate-out is refused for bigger invoices without a valid e-way bill.
10. **Delivery (POD)**: when the signed LR / receipt comes back, open **Deliveries Pending**, tick the consignments
    and *Mark ticked as delivered* — or open one (*Track Shipment*) to record short / damaged / refused with a note
    and a photo of the POD. Consignments past `delivery.expected_days` are highlighted as overdue.
11. **Track Shipment**: type or scan any invoice, LR, gatepass, e-way bill or IRN number → the full timeline
    (order received → gatepass → printed → left the warehouse → LR → delivered → bill checked → paid).

Typing an invoice by hand (no ERP export, no QR): **Add Invoice** (menu *Operations*) — stockist (area filled in),
company, invoice no / date / amount / taxable amount / cases, and optionally the planned mode and transporter, which
are pre-selected when the gatepass is made. The same duplicate and scope checks as an import apply.

Customers: when System Settings › `org.public_url` is set, the WhatsApp / SMS alerts carry a **tracking link**. The
customer sees the timeline (no prices) and can confirm receipt (received / short / damaged). Staff records always win.

Wrong gatepass before gate-out: a checker **voids** it (reason), the invoices return to the queue, make a new one.
Reprints need a reason and are marked "REPRINT #n (DUPLICATE)".

## CFA manager

* **Invoice Import** — three ways, none needing ERP access:
  * QR scanning at the dispatch desk (above);
  * **Drop folder**: set each ERP's daily export (or "Save as") to `data/inbox` (or `data/inbox/<ERP code>`). It is
    imported automatically every few minutes; the import log shows each batch;
  * **One-click import**: drop any export file on the Import page. The layout is recognised from the column
    headings. Item-wise exports (one row per SKU) become one invoice with SKU lines — or add the SKU lines to an
    invoice already captured by QR.
  Problem rows wait in the batch: map an unknown ERP customer code (or use the party GSTIN), create a missing
  customer/area, correct, *Retry*. A new layout is mapped once and saved as a template.
* **Transporter Rate Cards**: rate per case for each area per transporter; changes take an Effective From date and
  go to a checker. Upload a whole rate list as CSV/Excel.
* **Outside Autos** and **Customers** (type: stockist / retailer / hospital; mobile, WhatsApp, GSTIN) under Masters.

## Checker / approver

* **Approvals**: box / freight overrides, principal rate changes, **transporter rate-card changes**, transporter
  bills. You cannot approve your own requests.
* Void gatepasses, cancel open dispatches (reason mandatory).

## Accounts — transporter bills

1. **Scan a Bill**: choose the transporter, photograph each page (phone photo is fine; handwritten Hindi bills work)
   or upload the PDF. With the AI reader switched on, the lines (LR / bilty no, date, party, station, cases, rate,
   amount) are read for you; otherwise the photo is shown next to an empty table to type into. Excel/CSV statements
   can still be imported, and *Link period dispatches* builds a bill from our own gatepasses.
2. Check the lines against the photo and **Save lines & re-check**. Each line is cross-checked with our gatepasses:
   * LR not in our dispatches (with a **"Probably: …"** suggestion when a consignment without LR matches — *This one*
     links it and writes the LR onto our gatepass),
   * billed twice (this bill or an earlier one), **already paid in cash** at dispatch, not dispatched, another
     transporter's consignment — these cannot be approved;
   * more cases than dispatched, rate different from the rate card, cases × rate ≠ amount, station / party
     mismatch, date outside the period, lines not adding up to the bill total;
   * our consignments in the period that are not on the bill.
3. **Irregularity report** (screen / print / Excel) for the conversation with the transporter.
4. **Has the transporter changed any rates?** — per area: rate card vs rate charged. Tick to propose the new rate
   (a rate approver confirms it); save with nothing ticked to record "no rate changes".
5. Another user **approves** (remarks for variance) → *Mark paid* with the UTR.

## Management

* **Dashboard** and **Transportation MIS** as before.
* v1.5 reports: **Delivery Performance** (on-time %, average transit days, late / damaged per transporter and area),
  **Delivery Register (POD)**; *Transporter-wise* now shows trips, cost per case and **cost per trip**.
* **E-mailed Reports** (menu *Reports*): any report sent at 7 am daily / weekly (Mondays) / monthly (1st) as
  Excel, CSV or PDF — needs the mail server in `.env`.
* New reports: *Gatepass Register*, *Transporter × Area cost per case*, *SKU-wise Dispatch*, *Stockists / Retailers /
  Hospitals*, *Mode of Dispatch*, *Outside Auto Expense*, *Transporter Bill Irregularities* — all exportable.

## Administrator

* **Roles and access** (Administration › Roles): the permission matrix per role - tick what a role may do, rename it,
  copy it into a new role, or *Reset* a system role to the template it started from. Also shown: what each role sees
  in the menu, and every user with their role and scope. Nothing is locked, so give Master Settings, final reports or
  user administration only to people you trust. Every change is audited and applies on the user's next request. Nobody
  can change their own role, permissions or scope, nobody can hand out more than they hold themselves, and at least one
  active user must always be able to manage users and roles.
* **Users** (Administration › Users › *New user*): username, name, role (Admin, Management, Accounts, Depot
  Manager, Dispatch Operator, Checker, Viewer), the **scope** (companies, depots, delivery areas, customer types and
  individual customers - the person sees only those invoices, gatepasses, reports, exports and dropdown entries), optional
  **individual permission changes** (give or take away one permission, with a reason, on top of the role - the
  *Effective access* panel previews the result), and a password you choose.
  Untick *Must change it at first login* to hand over a fixed password. *Set new password* on a user unlocks the
  account and signs them out everywhere. Every change is in the audit log.
* **Freight and masters (v1.7)**: *Transporter Rate Cards* take an optional **exception** for one depot and / or one
  company (the most specific rate wins: depot + company, then depot, then company, then the general rate; a second
  person approves it like any rate). *Transporters* list the depots they serve and whether they give their own LR book.
  *Customers* list their delivery locations (area, label, ship-to code, a default).
* **Locked (paid) features**: automatic bill reading (AI) and WhatsApp / SMS alerts cost money and are locked while
  `CTDMS_PAID_SERVICES=false` (the default). Bills are typed next to the photo; alerts can be tried with
  `notify.provider = LOG`. Nothing else depends on them.

* **E-invoice QR signature**: download the IRP public key from the e-invoice portal, set
  `CTDMS_IRP_PUBLIC_KEY_FILE`, and choose `einvoice.signature_check` = WARN (flag) or BLOCK (refuse) in System
  Settings. Without the key QR scans are captured as "signature not checked".
* **Mail server** for e-mailed reports: `CTDMS_SMTP_HOST`, `CTDMS_SMTP_PORT`, `CTDMS_SMTP_USER`,
  `CTDMS_SMTP_PASSWORD`, `CTDMS_SMTP_FROM`, `CTDMS_SMTP_TLS` (starttls / ssl / none). `python -m app.cli reports-mail`
  sends what is due (for cron / Task Scheduler setups; the app also sends by itself every 5 minutes).
* **Tracking links**: `org.public_url` (System Settings) = the address customers can open; links expire after
  `delivery.link_days`.
