# ERP integration

The system is not built around any one ERP. Every source goes through the same path:

```
source → adapter.fetch() → adapter.transform() (field map) → import pipeline → invoices
```

The import pipeline (`app/services/import_service.py`) is shared by Excel/CSV upload, REST push and adapters, so
validation, code mapping, duplicate detection, rate lookup and exception handling are identical for every ERP, and
every record is traceable to an import batch and row.

## Central invoice fields

| Field | Required | Notes |
|---|---|---|
| `invoice_no` | yes | unique per company + CFA + financial year |
| `invoice_date` | yes | `YYYY-MM-DD`, `DD-MM-YYYY`, `DD/MM/YYYY`, `DD.MM.YYYY`, Excel dates, SAP `/Date(ms)/` |
| `customer_code` | yes | ERP's own code → mapped via *ERP Mappings* (or equal to the central code) |
| `sales_value` | yes | net invoice value |
| `company_code` | if not defaulted | ERP code (e.g. SAP sales org `1000`) → mapping |
| `cfa_code` | if not defaulted | plant / depot / branch → mapping |
| `boxes` | optional* | blank → invoice lands as EXCEPTION "boxes missing"; entered at dispatch (*configurable) |
| `location` | optional | delivery city / station; defaults to the customer's delivery location |
| `customer_name`, `source_invoice_id`, `transporter_code`, `lr_no`, `vehicle_no`, `remarks` | optional | |

## Ways in

1. **Excel / CSV upload** (*Operations › Invoice Import*) — available today for every ERP. Save one template per
   export layout. Samples of each ERP's layout are in `sample_data/`.
2. **Push API** — the ERP (or a small middleware job) POSTs invoices:
   ```http
   POST /api/v1/integrations/{ERP_CODE}/invoices
   X-API-Key: <key from Integration Settings>
   Content-Type: application/json

   {"invoices": [{"BillingDocument": "90012345", "BillingDocumentDate": "2026-09-25", "SoldToParty": "100245",
                  "TotalNetAmount": 56000, "SalesOrganization": "1000", "cfa_code": "RPR", "boxes": 36}]}
   ```
   Field names are the ERP's own; they are translated with the ERP's *invoice-field mapping* (Integration
   Settings) or the adapter default. The response lists each row's status, errors and the new invoice id.
   A user-token variant exists for central field names: `POST /api/v1/invoices/ingest`.
3. **Pull adapters** — *Integration Settings › Run sync now*, or scheduled (`python -m app.cli sync --all`, the
   `scheduler` compose profile, or cron). Runs are logged in `integration_runs`.

## Dispatch write-back (CTDMS → ERP)

When a dispatch is completed the invoice gets `erp_sync_status = PENDING`. The ERP pulls
`GET /api/v1/integrations/{ERP}/dispatch-updates` (dispatch no, transporter, vehicle, LR, boxes, date/time, freight)
and confirms with `POST …/dispatch-updates/ack {"invoice_ids": [...]}` → `SYNCED`. Adapters can also implement
`push_dispatch()` for direct write-back.

## Adapters provided (`app/integrations/adapters.py`)

| Key | For | Status |
|---|---|---|
| `file` | Manual Excel/CSV | **working** |
| `file_drop` | Any ERP writing scheduled exports into a folder | working (needs folder path) |
| `rest` | Generic JSON REST (bearer / basic / API-key auth, date range, pagination, records path) | working, untested against a live ERP |
| `database` | Read-only SQL on the ERP database (SQL Server / MySQL / PostgreSQL / Oracle via SQLAlchemy URL) | working, untested against a live ERP |
| `marg` | MARG ERP 9+ — FILE (watched folder) or DB mode, MARG sales-register column defaults | placeholder defaults to confirm |
| `sap_odata` | SAP S/4HANA `API_BILLING_DOCUMENT_SRV` (OData v2, `$filter` on billing date, paging, `sap-client`) | ready, needs credentials |
| `sap_idoc` | SAP INVOIC02 IDoc XML via file port | ready, needs port folder |
| `cbo` | CBO REST (default field names to confirm) | placeholder |
| `intrect` | Intrect REST (default field names to confirm) | placeholder |
| `demo` | Simulated ERP API — demonstrates the automated pull end-to-end | working |

Adding a new ERP: *Integration Settings › Add a source system* (choose an adapter), set its config and field
mapping, add code mappings. A completely new protocol = one subclass of `ERPAdapter` with `fetch()` (and optionally
`transform()` / `push_dispatch()`), registered with `@register`.

## What is needed from each ERP to go live

| ERP | Information / access required | Our assumption until then |
|---|---|---|
| **MARG** (Company B) | Which MARG edition/version; can it schedule a daily *Sales Register / Bill-wise* export to a shared folder? Or read-only DB access (server, DB name, credentials, table/view names for bills, parties, stations, cases). Sample export file. Party code ↔ central customer list | Daily CSV export with Bill No, Bill Date, Branch, Party Code/Name, Station, Net Amount, Cases (template “MARG – Sales register”) |
| **SAP** (Company A) | OData service URL (`API_BILLING_DOCUMENT_SRV` or a custom CDS view), communication user + password, `sap-client`, sales-org(s) and plant ↔ CFA list, **where the number of packages/boxes comes from** (delivery handling units or custom field), network route (VPN / IP allow-list) | Billing doc no/date, sold-to, net value, sales org, plant; boxes from the export column “No. of Packages” |
| **CBO** (Company C) | API documentation (endpoint, auth method, date filter, pagination), test credentials, field names for invoice no/date, customer, depot, division, amount, cases | JSON with InvoiceNo, InvoiceDate, CustomerCode, DepotCode, DivisionCode, NetAmount, NoOfCases |
| **Intrect** (own distribution) | API key and base URL, or export format; branch codes | JSON with inv_no, inv_date, party_id, branch, principal, net_value, boxes |
| **All** | Master code lists (customers, locations/stations, branches/plants) to pre-load *ERP Mappings*; whether ERP should receive dispatch details back (LR, vehicle) and how | Mappings entered from the import correction screen as they appear |

Credentials are stored in `erp_systems.config` (masked in the UI). For production, restrict DB access to the app
server and back the database up (the config holds secrets); or inject secrets via environment variables in a custom
adapter subclass.
