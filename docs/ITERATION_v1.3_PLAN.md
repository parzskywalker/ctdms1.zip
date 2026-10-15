# CTDMS v1.3 — Management feedback: plan

Source: management review (flowchart "Transportation Expenses Management Software" + six real transporter bills:
Prakash Goods Carrier, Bhaiji Roadlines, Osho Express, Orient Logistics, Parakh Transport, Jai Bhole Garage).

Core objectives this iteration serves:

1. Keep a record of everything dispatched — nothing leaves the compound without a printed gatepass.
2. Prevent double entry and double payment.
3. Capture data that can be analysed and projected (company, stockist, area, SKU, transporter, cost per case).
4. Work like a courier-company dispatch desk (scan → gatepass → gate-out → reconcile).

---

**Customers** are of three kinds — **stockists, retailers and hospitals**. The customer master gets a *type*
field; gatepasses, filters and every customer-wise report show and split by it ("stockist" below means any customer).

## 1. Getting invoices in without re-typing (no ERP "back door")

The ERPs (MARG, SAP, CBO, Intrect …) do not give us direct access. Three ways in, none of which need ERP access:

| Way in | How it works | When to use |
|---|---|---|
| **Scan the e-invoice QR code** | Every GST e-invoice printed by any ERP carries a QR code signed by the GST portal. It already contains the invoice number, date, value, seller GSTIN (→ company) and buyer GSTIN (→ stockist). A ₹1,500 USB 2D barcode scanner types it into CTDMS like a keyboard. The operator only types the number of cases. | At the dispatch desk, for every invoice — zero typing. |
| **Drop-folder auto import** | Each ERP's daily export (most can schedule one) is saved into `data/inbox/`. CTDMS checks the folder every few minutes, **recognises the layout by its column headings**, imports it and moves the file to `processed/`. | End of day: brings in amounts, SKU lines and anything not scanned. |
| **One-click upload** | Any export file dropped on the Import page is recognised automatically (same layout memory) and committed in one click; only problem rows need attention. | Ad hoc. |

Item-wise exports (one row per SKU) are understood too: rows are grouped into one invoice with SKU lines. If the
invoice was already scanned at the desk, the SKU lines are attached to it instead of being rejected as a duplicate.

## 2. Gatepass (dispatch slip) — one slip per pickup, many invoices

* One gatepass per transporter pickup / auto trip, containing **many invoices for many stockists** (scan or tick).
* Printed slip shows: gatepass number + barcode, date, **mode** (Transporter / Transporter direct pickup /
  Outside auto / Self pickup), **transporter name, contact, GST, payment mode (Cash / TBB)** or the **outside
  auto** person, mobile, per-trip or per-case charge; then per stockist: name, area, mobile, invoice numbers,
  cases, rate per case, charge; totals: invoices, cases, freight, pickup / auto charges, **total charged**,
  amount paid now, balance (to be billed).
* **No vehicle number** (allotted later). **LR number is not needed to print**; it is entered later, per stockist
  consignment, whenever the transporter gives it — every change is audited and locked once the transporter's bill
  is approved. An "LR pending" list shows what is still missing.
* **Gate-out**: the guard scans the gatepass barcode at the gate → all its invoices become DISPATCHED. Invoices
  cannot be marked dispatched any other way, so nothing leaves without a gatepass.
* Charges come from the **transporter rate card (rate per case per area, effective-dated)**; outside autos are
  per trip or per case; transporters may have a direct-pickup extra. Per-invoice charge is frozen on the slip.

## 3. Data kept for analysis

Everything on the gatepass plus: invoice no / date / amount, company the goods belong to, stockist, area,
transporter / auto, mode, cases, rate, freight + allocated extra charges, LR no / date, and **SKU lines per
invoice** (company × SKU × quantity). New reports: gatepass register, LR pending, SKU-wise dispatch, outside-auto
expense, mode-of-dispatch, cost per case by transporter / area / company.

## 4. Transporter bill: scan → cross-check → irregularity report → rate review

1. **Scan**: photograph or scan the bill (handwritten Hindi bills included) and upload it. Claude (AI) reads it
   into rows: LR / bilty / GR no, date, party, station, cases (नग / डाग / TP / pkgs), rate, amount; plus bill
   no, date and total. Without an AI key the photo is shown beside an empty grid to type into. Excel / CSV
   statements still import directly.
2. **Review**: the operator corrects any misread cells next to the photo.
3. **Cross-check** against that transporter's gatepasses:
   * LR found → compare cases, rate (vs rate card for that area on that date), amount, station.
   * LR unknown → suggest the matching consignment by date ± 3 days, party/station and cases; one click links it
     (and fills the missing LR on our side).
   * Flags: LR not in our dispatches · billed twice (on this bill or an earlier one) · consignment already paid in
     cash at dispatch · more cases than dispatched · rate differs from rate card · arithmetic error (cases × rate ≠
     amount, lines ≠ bill total) · station / area mismatch · date outside bill period · our dispatches in the
     period that are not on the bill.
   * **Irregularity report** (screen / print / Excel) for the transporter conversation.
4. **Rate review**: once verified, CTDMS asks "Has the transporter changed any rates?" It lists each area with
   the card rate and the rate actually charged; accepted changes become new rate-card versions (approval by a
   second person, history kept).
5. Approve → mark paid (existing maker-checker and duplicate-payment blocks stay).

## 5. How it is built (technical)

* Same stack (FastAPI, SQLAlchemy, Jinja, SQLite/PostgreSQL). One Alembic migration `0003` adds: outside autos,
  transporter rate cards, gatepass header fields + gatepass lines, invoice SKU lines, e-invoice IRN, LR date /
  audit fields, scanned-bill fields and per-line irregularities. Existing data is kept.
* Setting `freight.source` = `TRANSPORTER` (rate card per transporter per area, the new default) or `COMPANY`
  (the v1.2 principal rate card, kept for anyone who needs it).
* AI bill reading uses the Anthropic API (`CTDMS_ANTHROPIC_API_KEY`); everything else works offline on the LAN.
* Demo data rebuilt around gatepasses, with the six real transporters as examples and scanned bills that show
  each kind of irregularity.
* Automated tests for each new rule; the existing suite kept green.
