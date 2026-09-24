# Commercial Module — Sales Order & Marketing Management

EBMS module for the cable-manufacturing tender (Belayab Cable Manufacturing PLC).
Mounted at **`/commercial`**; sidebar entry *Commercial*. Works stand-alone and
links to Manufacturing, Quality, Inventory, ERCA, VAT, Payments and the Approval
engine when those modules are present.

## 1. What the module does (mapped to the tender list)

| Tender requirement | Where in EBMS |
|---|---|
| Customer master with credit limits, terms, segment, territory, sales rep | `/commercial/customers` — code, TIN, contact, segment, territory, rep, credit limit & days; balance = invoiced − paid − credited; over-limit customers highlighted; optional link to a portal user |
| Product catalogue (cable code, size mm², colour, unit, packaging, price, VAT) | `/commercial/products` — manual entry or **Import from manufacturing catalog** (`mfg_products`) |
| Price lists (validity, tiers by minimum quantity) and discounts | `/commercial/pricing` — price lists with per-item tiers; discount rules by customer / segment / product / order value with validity dates; best single rule wins |
| Sales territories & representatives with commission % | `/commercial/territories` |
| Proforma invoice (ref no, item code, size, description, qty, colour, unit, price, total; payment method / info; validity) | `/commercial/proformas` — PFI-YYYY-NNNNNN, statuses draft → sent → accepted / expired → converted; **Convert to Sales Order** |
| Sales order with verification (starting / ending / difference), prepared / checked / approved, distribution copies (finance, sales, first copy) | `/commercial/orders` — SO-YYYY-NNNNNN with all header fields, line items, totals with 15 % VAT |
| **Three authorised approvers** on a sales order | Settings → three approver steps (username or role). Submission creates three sequential approval rows; the order becomes *approved* only when all three approve; any rejection rejects. `/commercial/orders/approvals` lists what awaits the current user. When an approval-engine workflow for `sales_order` exists the request is also routed there and its decision is synchronised |
| Credit-limit check on credit sales | Blocked (or warned, per settings) at submission and at invoicing; managers may override, and the override is recorded on the order |
| Inventory availability & allocation | Order page shows on-hand from `inventory_items` (when the inventory module exists), active reservations, shortfall; **Allocate / reserve stock** per line (`commercial_reservations`) |
| Manufacturing order request (product code, description, size, colour, unit, quantity, packing, cutting length, delivery date, comment, to factory, prepared / checked / approved / manager) | **Create Manufacturing Order** per line or for the whole order → `manufacturing_data_store.create_order_from_sales_order`; when the module is absent a `commercial_mo_requests` row is kept and the user is told it was *sent to Planning & Engineering*. `/commercial/mo-requests` prints the request form and follows production status |
| Delivery instruction for inventory (product code, description, size, colour, unit, packaging, quantity; prepared / checked / approved) | **Delivery instruction** from an approved order → DI-YYYY-NNNNNN with the quantities to release |
| Dispatch note, delivery confirmation, customer acceptance | Dispatch (vehicle, driver, dispatched at) → *delivered* (received by) → *accepted* / *rejected*. Delivery posts `delivered_qty` to the order which moves to partially delivered / delivered automatically |
| Sales invoice, credit sales tracking, receipts (cash, bank, Telebirr, CBE Birr, M-Pesa, cheque) | Invoice from an order (defaults to delivered-not-invoiced quantities) or a direct invoice. Numbering via the ERCA series when the ERCA module has an active series, else INV-YYYY-NNNNNN. Issue creates a VAT income record and the rep's commission. Receipts update *partially paid* / *paid*; **Link mobile-money payments** matches inbound rows of the payments module by invoice number |
| Sales return & credit note | From an invoice → RN-YYYY-NNNNNN; manager approval issues CN-YYYY-NNNNNN and reduces the customer balance |
| Sales forecasting | `/commercial/forecasts` — per item 3- and 6-month moving averages and a linear trend from invoiced history (average of the three), refreshed on the 1st of every month; manual forecasts / overrides by item and territory |
| Sales commissions | `/commercial/commissions` — accrued on the net (VAT-exclusive) invoice value; summary per rep & period; mark paid |
| Marketing: campaigns, budget vs spend vs revenue, events, leads, competitor analysis, segmentation, acquisition / retention, KPIs, executive dashboard | `/commercial/marketing` dashboard, `/marketing/campaigns`, `/marketing/events`, `/marketing/leads` (convert a lead into a customer), `/marketing/competitors` |
| Reports (filter by date, customer, product, rep, territory; Excel export) | `/commercial/reports` — 20 reports, see §3 |
| Document header / footer / ISO document number and signature blocks | Company defaults in Settings, overridable per document; every print view, PDF and Excel export carries them |
| Sales dashboard KPIs | `/commercial/` — revenue MTD / YTD, receivables, overdue, open orders, pending approvals, backlog value, on-time delivery %, top customers / products, pipeline by status, 12-month revenue chart, approvals waiting for me |

All amounts are `Decimal` quantised to 2 dp; VAT defaults to 15 % (configurable
per company). Document numbers are gapless per company and calendar year
(`commercial_sequences`, allocated with `UPDATE … RETURNING` inside the same
transaction as the document insert).

## 2. Daily workflow

1. **Quote** — create a proforma, print / PDF it, mark *sent*; when the customer
   accepts, *Convert to Sales Order*.
2. **Order** — review the draft (lines, terms, readings, copies), *Submit for
   approval*. Credit sales over the limit are blocked unless a manager overrides.
3. **Approve** — the three designated approvers sign in sequence from *Order
   Approvals* or the order page.
4. **Produce / allocate** — reserve stock, or raise a manufacturing order request
   (per line or whole order). Completion of all requests moves the order to
   *ready*.
5. **Deliver** — issue a delivery instruction to inventory, create the dispatch
   note, mark delivered and record customer acceptance.
6. **Invoice & collect** — issue the invoice (ERCA number, VAT record and
   commission created automatically), record receipts or link mobile-money
   payments. Overdue credit invoices are flagged daily at 06:30.
7. **Returns** — raise a sales return from the invoice; manager approval issues
   the credit note.
8. **Close** — a manager closes the order once invoiced.

## 3. Reports (`/commercial/reports/<key>`, `…/export` for Excel)

`proformas`, `sales_orders`, `invoices`, `credit_sales`, `customers` (master,
credit limit & balance), `product_sales`, `pricing`, `discounts`, `dispatch`,
`returns`, `fulfilment` (ordered vs delivered vs invoiced), `forecast`,
`rep_performance`, `territory_performance`, `vat_summary`, `campaigns`,
`segmentation` (+ acquisition / retention), `competitors`, `events`,
`marketing_kpis` (+ revenue by segment).

## 4. Scheduled jobs (`commercial_jobs.py`)

* `commercial_daily_credit_check` — daily 06:30: marks overdue invoices, logs
  credit-limit breaches and overdue credit-sales reminders on the audit trail
  (and the notifications module when available).
* `commercial_monthly_forecast` — 1st of the month 06:30: recomputes the
  automatic forecast for the new month per company (manual rows are kept).

## 5. Public Python API (`commercial_data_store`)

```python
sales_order_by_no(company_id, so_no)                     # header + lines + approvals + linked docs
customer_balance(company_id, customer_id)                # Decimal: invoiced − paid − credited
record_payment(company_id, invoice_no, amount, method, reference, actor="api")
open_orders_summary(company_id)                          # {count, value, by_status, overdue_deliveries}
```

`record_payment` accepts the internal or the ERCA invoice number and returns the
receipt row (or `None`). None of these raise.

## 6. Permissions

* Any logged-in user: master data, documents, receipts, marketing, reports.
* `manager` and above: settings, discount rules, credit-limit changes and
  overrides, approval decisions, close / cancel an order, cancel an invoice,
  approve returns, mark commissions paid, delete territories.

## 7. Integration points (all optional, degrade gracefully)

| Module | Used for |
|---|---|
| `manufacturing_data_store` | `create_order_from_sales_order`, `order_status_summary`, `mfg_products` import |
| `inventory_items` | on-hand quantities on the order and delivery pages |
| `erca_data_store.issue_invoice` | ERCA e-invoice number for sales invoices |
| `vat_data_store.vat_store.add_income` | VAT income record on invoice issue |
| `payments` table | linking Telebirr / CBE Birr / M-Pesa / bank receipts by invoice reference |
| `approval_hooks` / `approval_data_store` | alternative approval path; decisions for `sales_order` requests are synchronised back |
| `portal_users` | optional customer ↔ portal user link |

## 8. Files

`web/commercial_data_store.py`, `web/commercial_routes.py`, `web/commercial_logic.py`,
`web/commercial_jobs.py`, `web/templates/commercial/*.html`,
`web/i18n_catalogue_commercial.py`, `web/tests/test_commercial_logic.py`,
`web/tests/test_commercial_templates.py`.
