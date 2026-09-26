# User guide (day-to-day)

## Dispatch operator (warehouse)

1. **Pending Dispatch** opens after login. Scan or type the invoice number in the search box and press Enter — an
   exact match opens the dispatch screen directly.
2. Check boxes and freight (Boxes × Rate = Expense). If the physical count differs, use *Change boxes* with a
   reason — it goes to the checker for approval and the invoice waits. If boxes were not received from the ERP,
   enter them (no approval needed for a first entry).
3. Select **Transporter**, enter **Vehicle No** and **LR No** (date/time default to now) → **Verify**.
4. **Generate Dispatch Slip** → dispatch number is issued and details lock.
5. **Print Slip** (A5 or A4, or PDF). Give the copy to the driver and get signatures.
6. **Complete Dispatch**. The system refuses completion until the slip is printed.

Many invoices on one vehicle: tick them on Pending Dispatch → *Bulk dispatch* → one transporter/vehicle, LR per
invoice → *Verify & generate slips* → *Print N slips* → *Complete N dispatches*.

Reprint: allowed with a reason; the copy is marked "REPRINT #n (DUPLICATE)". Wrong vehicle after slip generation:
ask the checker to *Void slip*, correct, regenerate.

## CFA manager

* **Invoice Import**: upload the ERP export → check the column mapping (or pick the saved template) → *Apply
  mapping & validate* → *Import valid rows*. Fix rejected rows in *Needs attention*: map an unknown ERP customer code
  to the right customer, create a missing customer/location, or correct the row, then *Retry*.
* **Freight Exceptions**: add missing rates (*Add rate* pre-fills the combination). A checker approves; matching
  invoices re-price automatically.
* Propose rate changes with *Revise rate* (new Effective From date).

## Checker / approver

* **Approvals**: override requests, rate changes, transporter bills. You cannot approve your own requests.
* Void slips, cancel open dispatches (reason mandatory), re-run rate lookup on open invoices.

## Accounts

* **Reconciliation › Enter / import bill**: bill no, date, amount, period → *auto-link dispatches* for the period, or
  import the transporter's statement (LR No / Invoice No, Boxes, Amount). Lines are matched and flagged
  (Matched / Variance / Not found / Duplicate billing / Not dispatched / Other transporter).
* Save billed amounts, or *Allocate* a lump-sum bill. Another user approves (remarks required for variance); then
  *Mark paid* with the UTR.

## Management

* **Dashboard**: KPIs and charts with date/company/customer/CFA/location/transporter filters.
* **Transportation MIS**: expand Company → Customer → CFA → Location → Invoice → Dispatch.
* **Reports**: 18 reports, each exportable to Excel/CSV/PDF and printable.
