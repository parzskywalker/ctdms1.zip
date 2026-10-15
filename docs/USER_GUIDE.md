# User guide (day-to-day)

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
5. **Generate gatepass** → one number for the whole pickup. **Print** it (A5/A4/PDF): it carries a barcode, every
   customer with its invoices, cases and charge, the transporter's name/contact/GST/payment mode — no vehicle number.
6. **Gate check**: the guard scans the gatepass barcode, counts the cases and presses **Confirm gate-out**. All its
   invoices become DISPATCHED. A void, unprinted or already-used gatepass shows a red STOP.
7. **LR numbers** come later from the transporter: type them on the gatepass page (one per customer) or on
   **LR Pending** in bulk. They can be corrected (with a reason) until the transporter's bill is approved.

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
* New reports: *Gatepass Register*, *Transporter × Area cost per case*, *SKU-wise Dispatch*, *Stockists / Retailers /
  Hospitals*, *Mode of Dispatch*, *Outside Auto Expense*, *Transporter Bill Irregularities* — all exportable.
