# Changelog — Ethiopian Accounting System

All notable changes to this application will be documented in this file.
Format follows [Semantic Versioning](https://semver.org/): MAJOR.MINOR.PATCH

---

## [2.3.0] — 2026-09-24

Manufacturing ERP release for cable / process manufacturers (Belayab tender §7.2, §7.7, §7.8).

### Production (`/manufacturing`)
- Plants, work centers, machines, product master, Technical Data Sheets (versioned, one approved per
  product), Bills of Material (versioned, per plant, semi-finished + finished), routings with per-operation
  process parameters (die, nipple, zone temperature, diameter, lay length, thickness), working calendar,
  holidays and shutdowns, shifts with shift leader / line supervisor.
- Periodic production plans (weekly to annual) per line, product variant and SKU; capacity planning
  (available vs planned hours); annual raw-material plan exploded from plan × BOM and submitted to
  Property Administration as store + purchase requisitions.
- Production orders for make-to-stock and make-to-order (from sales orders) with gapless MO numbers and
  the full Planning → Release → Confirmation → Closing cycle; operations per work center / machine;
  material issue, consumption, return and lot numbers; planned vs actual cost per order and product.
- Shop-floor logging per hour and shift: input, output, rolls, length, weight, scrap, rework, downtime by
  user-defined category and reason, machine and labour time incl. setup; yield and wastage; finished-goods
  transfer to store; consumption/output journals posted to the ledger.
- Reports: performance per machine, raw material converted to finished goods, finished goods delivered to
  store, raw-material status (issued / consumed / returned per material), scrap generated, machine
  utilisation and downtime, plan vs actual, line efficiency, BOM per order. Excel export on each.
- Process map page showing the 16-step customer-PO-to-delivery flow with live status per sales order.

### Quality Management (`/quality`)
- Inspection plans and specification sets (IEC / Ethiopian Standard / ISO values) with tolerances.
- Raw material inspection (with supplier disposition), cable in-process inspection, wire insulation
  inspection, final product inspection with Certificate of Analysis, wire packing summary, AAC/ABC
  conductor delivery reports. Spec vs actual evaluated automatically; failed inspections raise NCRs.
- Calibration management with valid / due-soon / expired status and reminders; customer complaints;
  CAPA with overdue tracking; non-conformance reports with disposition; internal audits with checklists.
- Reports: periodic mean and standard deviation per parameter, stability trends, supplier compliance,
  material yield, SPC control charts, defect rates, lot history card, non-conforming summaries, CAPA
  reminder reports, audit schedule / history / checklist results.

### Commercial — Sales & Marketing (`/commercial`)
- Customers with credit limits and terms, product catalog and price lists, discounts, territories and
  sales representatives with commissions.
- Proforma invoice → sales order (three designated approvers) → manufacturing order request →
  delivery instruction → dispatch → invoice (VAT income and ERCA e-invoice) → receipts; inventory
  availability check and reservation; returns and credit notes; sales forecast; marketing campaigns,
  events, leads and competitor notes.
- Reports: quotation / order / invoice / credit sales, customer balances, product revenue and pricing,
  delivery and fulfilment, forecast and trend, rep and territory, VAT and sales summary, dashboards.

### Also in this release
- Contracts: upload and view the signed contract and annexes (Nextcloud-backed document storage).
- Bid tracker: bid results table (bidder, price, rank, winner) and admin-only supplier confidential
  documents.
- risk-sim: pace test, quality heuristic, sector concentration and hit-day persistence analytics.

---

## [2.2.0] — 2026-09-12

### Ethiopian-native
- **Amharic UI** — `web/i18n.py` catalogue + per-area `web/i18n_catalogue_*.py`; `_()` global,
  language switcher in every layout (`/i18n/set/{lang}`), session + cookie persistence,
  Noto Sans Ethiopic loaded when Amharic is active.
- **Ethiopian calendar** — `web/ethiopian_calendar.py` (JDN conversion, fiscal year Hamle 1–Sene 30,
  Ge'ez numerals), `|et_date` / `|dual_date` filters, today's E.C. date in the navbar, and
  `static/js/ethiopian-calendar.js` adds an E.C. badge + picker under every `<input type="date">`.
- **Mobile money** (`/payments`) — Telebirr, CBE Birr, M-Pesa, bank, cash accounts; manual entry,
  statement import, provider notification processing via `/webhooks/inbound/{provider}`,
  reconciliation against VAT income/expenses with scored suggestions. Provider adapters are
  record-only until credentials are set (`PAYMENT_PROVIDERS.md`).
- **ERCA outputs** (`/erca`) — monthly VAT return computed from VAT income/expenses, withholding
  register + monthly return + receipts, gapless concurrency-safe invoice/receipt numbering with a
  tamper-evident hash chain, PDF/Excel exports, audit export.

### Open platform
- **Customer & supplier portal** (`/portal`) — invite-based accounts, own session/CSRF/rate limiting,
  customers see invoices/CPOs/projects/tickets, suppliers see POs/payments/RFQs/document upload;
  staff admin under `/portal/admin`.
- **Webhooks + API keys** (`/webhooks`) — signed outbound deliveries with retries/backoff and SSRF
  guard, per-tenant hashed API keys with scopes, generic signed inbound receiver, docs page.
- **Documents on Nextcloud** (`/documents`) — WebDAV client + OCS share links, `document_storage`
  facade with local-disk fallback, browse/sync/search/versioning.
- **Telegram bot** (`/telegram`) — link codes, `/today /month /bids /approvals /stock /payments`,
  subscriptions, 07:30 digest, outbox with retry; webhook secret is mandatory.

### Workflow depth
- **Approval engine** (`/approvals`) — amount-banded workflows per entity type, sequential/require-all
  steps, role/user/manager approvers, delegation, inbox, escalation reminders, Python API `submit()`.
- **Fixed assets** (`/assets`) — categories seeded with Ethiopian tax classes, SL/DB/SYD/UoP
  schedules, idempotent monthly depreciation run (job on day 1 02:00), GL posting, disposals, register export.
- **Report builder** (`/reports`) — whitelisted data-source catalogue, safe query compiler, HTML/Excel/CSV/PDF
  output, charts, saved reports, daily/weekly/monthly e-mail schedules.

### Platform
- `app.py` registers the new routers via `_reg` and imports each store's `ensure_schema()` and
  optional `register_jobs(scheduler)` lazily, so a missing module can never break startup.
- New env vars in `docker-compose.yml` (all optional): `TZ`, `TELEGRAM_*`, `PORTAL_BASE_URL`,
  `WEBHOOKS_ALLOW_PRIVATE`, `TELEBIRR_*`, `MPESA_*`, `CBEBIRR_*`, `NEXTCLOUD_*`, `APPROVAL_*`.

---

## [2.0.0] — 2026-04-21

### New Features
- **End-of-Year Forecast Tool** — Added `/finance-mgmt/forecast` and `/payroll/forecast` routes
  with a shared interactive dashboard. Extrapolates monthly finance and payroll data to
  end-of-year projections using linear regression (≥3 observed months) or monthly average fallback.
- **Forecast Service** (`web/services/forecast_service.py`) — `forecast_finance()` computes
  revenue (credits to 4xxx accounts) and expense (debits to 5xxx/6xxx); `forecast_payroll()`
  covers gross salary, net salary, income tax, and pension. Returns confidence score (0–1) and
  method label per run.
- **Forecast Dashboard** (`web/templates/forecast/dashboard.html`) — Shared by both finance and
  payroll modules. Chart.js line charts overlay actual (solid blue) vs. projected (dashed orange)
  per metric; summary cards show YTD actual, EOY projection, and remaining delta; full monthly
  breakdown table; year picker; JSON export link (`?format=json`).
- **Forecast sidebar links** — "Payroll Forecast" and "Finance Forecast" added to main sidebar
  under Operations & Assets in `base.html`.

### Layout & Responsive Fixes
- **Full-width content area** — Resolved blank right-side space on all non-sales pages where
  content was not expanding to fill the viewport minus sidebar width.
- **`multicompany/base.html`** — Replaced Bootstrap grid (`col-md-9 col-lg-10`) with CSS Flexbox
  (`portal-layout` / `portal-main`). Primary sidebar is `flex: 0 0 260px`; main content is
  `flex: 1 1 auto; min-width: 0` ensuring full-width expansion at all viewport sizes.
- **`auth/base.html`** — Added `@media (max-width: 768px)`: sidebar hidden, `.auth-main` expands
  to `width: 100%; margin-left: 0`.
- **`base.html`** — Mobile `.app-main` now sets `width: 100%` explicitly.
- **`siem/_sidebar.html`** — Added mobile media query: `.content-with-sidebar` collapses to
  `margin-left: 0; width: 100%` on small screens.
- **Module dashboards** — Fixed `cpo/dashboard.html`, `transaction/dashboard.html`, and
  `vat/dashboard.html` mobile overrides to include `width: 100%` in responsive blocks.
- **`vat/dashboard.html`** — Removed duplicate `{% block content %}` and orphaned CSS fragment
  injected above the real block.

### Session & Security
- **Idle timeout fixed** — The 60-second logout countdown was silently cancelled by any user
  activity (mouse move, scroll, click) because event listeners called `idleReset()` unconditionally
  while the warning was visible. Added `warningActive` flag: once the warning toast appears, all
  event-driven resets are blocked. Only clicking "Stay logged in" (passing `fromStayButton=true`)
  can dismiss the warning and restart the 5-minute idle clock. Applies to both `base.html` and
  `auth/base.html`.
- **Simplified timer logic** — Removed redundant two-timer pattern (`warnTimer` + `idleTimer`).
  Single timer fires `showWarning()` after 5 minutes of inactivity; countdown runs directly to
  logout with no further interruption.

### Bug Fixes
- Fixed `access-denied` redirect on read-only GET routes (HRM analytics and similar) —
  changed `admin_required` → `login_required`.
- Fixed empty space above content on `/cpo/`, `/auth/portal`, `/vat/dashboard` — removed
  phantom `top: 56px` offset (legacy navbar remnant) from `.module-sidebar`.

---

## [1.1.1] — 2026-03-11

### UI & Navigation
- **Sidebar redesign**: Reorganised entire navigation into 6 logical business-function sections —
  Main, Accounting & Finance, VAT & Tax Management, Operations & Assets, Administration, My Account
- Transactions sub-menu (All, Flagged Items, Flagged Accounts, Import, Export, Download Template) now
  indented under parent "Transactions" link
- VAT sub-menu (Add / List income, expense, capital + Financial Summary) grouped under "VAT Portal"
- Payroll, Inventory, CPO and Bid Tracker consolidated under "Operations & Assets"
- Multi-Company and SIEM grouped under "Administration"
- Removed duplicate "Balance Sheet" sidebar link (was pointing to Trial Balance route)
- "My Account" section (Profile, Change Password, Logout) visible only when logged in

### Sales
- Updated subscription pricing: Level 1 ETB 5,000 / Level 2 ETB 10,000 / Level 3 ETB 50,000 per month

### Security & Audit
- **IP Tracker**: Login IPs now captured from live FastAPI request (`request.client.host`); previously
  always logged as "unknown"
- **Device detection**: User-Agent string parsed and stored in `login_history.device_name`
  (Mobile / Tablet / Desktop + OS/browser hint)
- **Audit trail middleware**: Every POST/PUT/PATCH/DELETE request from an authenticated user is
  automatically recorded to SIEM events with username, method, and path
- **Event log user attribution**: `siem_data_store` resolves username from FastAPI session first,
  falls back to Flask session context; all events now carry correct actor

### Bug Fixes (from v1.0.0)
- Fixed logout crash (`clear_session()` signature mismatch in FastAPI context)
- Fixed VAT ExpenseCategory enum lookup (name-based, 8 missing members added)
- Fixed company dashboard crash (missing `user_role`, `company_summary`, `recent_payroll` context)
- Fixed S3 upload (`getattr(file, 'file', file).read()` — async/sync compatibility)
- Fixed mobile sidebar (hamburger button + overlay tap-to-close)

---

## [1.0.0] — 2026-02-18

### Initial Release

**Core Modules**
- General Ledger & Chart of Accounts (hierarchical, multi-level)
- Journal Entry system with Excel import
- Trial Balance, Income Statement, Balance Sheet reports
- VAT Portal (income, expense, capital, financial summary)
- Income & Expense Dashboard with time-frame filtering

**Payroll**
- Ethiopian Payroll with all tax brackets (Proclamation 1263/2023)
- Employee CRUD, salary calculation, payslip generation
- Monthly/annual payroll reports

**Multi-Company**
- Company registration, user management, role-based access
- Per-company dashboards, employees, payroll, settings

**Transactions & CPO**
- Quick Transactions (receipts, payments, transfers)
- CPO (Cash Payment Order) management

**Inventory**
- Stock items, categories, stock-in/stock-out
- Valuation reports, low-stock alerts

**Bid Tracker**
- Bid lifecycle management (Draft → Submitted → Won/Lost)
- RFP/RFQ tracking, bid analytics dashboard

**Security & Operations**
- Authentication system (role-based: Admin, HR, Accountant, Employee, Data Entry)
- SIEM — Security audit logging and monitoring
- Backup & Archive with scheduled daily backups at 01:00
- Dark/Light theme toggle across all interfaces

**UI/UX**
- Bootstrap 5 responsive design
- Chart.js infographic dashboards
- DevOpsRain Technologies CC branding
- 187 routes — all verified operational

---

*Maintained by DevOpsRain Technologies CC*
