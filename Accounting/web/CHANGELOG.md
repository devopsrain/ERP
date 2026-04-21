# Changelog — Ethiopian Accounting System

All notable changes to this application will be documented in this file.
Format follows [Semantic Versioning](https://semver.org/): MAJOR.MINOR.PATCH

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
