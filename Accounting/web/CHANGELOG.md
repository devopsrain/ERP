# Changelog — Ethiopian Accounting System

All notable changes to this application will be documented in this file.
Format follows [Semantic Versioning](https://semver.org/): MAJOR.MINOR.PATCH

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
