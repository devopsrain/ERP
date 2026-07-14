# Ethiopian Business Management System — Architecture Map

## High-Level Architecture

```
┌────────────────────────────────────────────────────────────────────────────┐
│                              CLIENTS                                       │
│   Browser (Jinja2 SSR)  ·  Mobile App (REST API)  ·  3rd-Party Integrations│
└──────────────┬────────────────────┬────────────────────────┬───────────────┘
               │ :80/:443           │ :80/:443               │
               ▼                    ▼                        ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         NGINX  (reverse proxy)                            │
│   /* → web:8000 (SSR)    /api/* → api:8001 (JSON)    /nginx-health → 200  │
│   TLS termination · gzip · 50MB upload limit · security headers           │
└──────────┬──────────────────────────┬────────────────────────────────────┘
           │                          │
     ┌─────▼─────┐            ┌──────▼──────┐        ┌──────────────────┐
     │  WEB APP  │            │  API APP    │        │  EVENT WORKER    │
     │ uvicorn   │            │ uvicorn     │        │ Redis pub/sub    │
     │ :8000     │            │ :8001       │        │ listener         │
     │ 2 workers │            │ 2 workers   │        │                  │
     └─────┬─────┘            └──────┬──────┘        └────────┬─────────┘
           │                         │                        │
           ├─────────────────────────┼────────────────────────┤
           │                         │                        │
     ┌─────▼─────────────────────────▼────────────────────────▼─────────┐
     │                     SHARED  INFRASTRUCTURE                        │
     │                                                                   │
     │  ┌────────────────┐    ┌──────────────┐    ┌──────────────────┐  │
     │  │  PostgreSQL 16 │    │   Redis 7    │    │  Local Storage   │  │
     │  │  :5432         │    │   :6379      │    │  /app/web/data/  │  │
     │  │  82 tables     │    │   256 MB LRU │    │  bid_docs/       │  │
     │  │  RLS isolation │    │   cache +    │    │  letters/        │  │
     │  │  per company   │    │   pub/sub +  │    │  uploads/        │  │
     │  │                │    │   sessions   │    │  backups/        │  │
     │  └────────────────┘    └──────────────┘    └──────────────────┘  │
     └──────────────────────────────────────────────────────────────────┘
```

---

## Request Flow (Input → Processing → Output)

```
 REQUEST                    MIDDLEWARE CHAIN                       RESPONSE
 ───────                    ────────────────                       ────────
 Browser                                                          HTML page
 or API   ──►  Nginx  ──►  ┌─────────────────────────────┐  ──►  or JSON
 client                    │ 1. Session decode (cookie)   │
                           │ 2. CSRF validation           │
                           │ 3. Auth check (redirect/401) │
                           │ 4. Company context (tenant)  │
                           │ 5. Module license check      │
                           │ 6. Rate limiting (SlowAPI)   │
                           │ 7. Request logging (SIEM)    │
                           │ 8. Route handler             │
                           │    └─ Data Store ──► Postgres │
                           │    └─ Cache ──► Redis         │
                           │    └─ Events ──► Redis pubsub │
                           │ 9. Sliding session refresh    │
                           └─────────────────────────────┘
```

---

## Module Map (29 Routers → 30 Data Stores → 82 Tables)

```
┌───────────────────────────────────────────────────────────────────────────┐
│                         APPLICATION MODULES                               │
├───────────────────┬───────────────────────┬───────────────────────────────┤
│ ACCOUNTING &      │ OPERATIONS &          │ COLLABORATION &               │
│ FINANCE           │ ASSETS                │ ADMINISTRATION                │
│                   │                       │                               │
│ /accounts (1)     │ /payroll (3)          │ /comm (5)                     │
│ /journal (2)      │ /inventory (7)        │ /project (4)                  │
│ /income-expense(2)│ /cpo (2)              │ /procurement (8)              │
│ /transactions (3) │ /bid (2)              │ /ems (4)                      │
│ /vat (3)          │ /machinery (7)        │ /letters (json files)         │
│ /finance-mgmt (6) │ /lms (9)             │ /siem (2)                     │
│ /forecast (0*)    │ /hrm (5)             │ /auth (5)                     │
│                   │                       │ /company (3)                  │
│                   │                       │ /provider (3)                 │
│                   │                       │ /backup (1)                   │
│                   │                       │ /version (1)                  │
│ (n) = DB tables   │                       │ /notifications (1)            │
│ * forecast = reads│                       │ /audit (1)                    │
│   from other      │                       │                               │
│   modules         │                       │                               │
└───────────────────┴───────────────────────┴───────────────────────────────┘
```

---

## Database Schema — 82 Tables by Domain

### Core & Auth (8 tables)
| Table | Key Columns | Input | Output |
|-------|-------------|-------|--------|
| `users` | id, username, password_hash, role, privilege_level, company_id | Registration, admin create | Login, session, API auth |
| `login_history` | user_id, ip, user_agent, success, timestamp | Every login attempt | SIEM dashboard, audit |
| `api_tokens` | token_hash, user_id, scopes, expires_at | Token generation UI | Bearer auth validation |
| `refresh_tokens` | token, user_id, expires_at, revoked | JWT login | Token rotation |
| `password_reset_tokens` | token, user_id, expires_at | Forgot-password form | Email link validation |
| `tenants` | company_id, name, subscription_plan, is_active | Company creation | Tenant resolution |
| `licenses` | company_id, module, tier, expires_at | Provider admin | Module gating |
| `license_audit` | company_id, action, changed_by, timestamp | Tier changes | Audit trail |

### Accounting & Finance (18 tables)
| Table | Key Columns | Input | Output |
|-------|-------------|-------|--------|
| `chart_of_accounts` | code, name, type, sub_type, company_id | Manual entry / import | Journal posting, reports |
| `journal_entries` | entry_id, date, reference, status, company_id | Manual / payroll event | Trial balance, ledger |
| `journal_entry_lines` | entry_id, account_code, debit, credit | Part of journal entry | Financial statements |
| `transactions` | id, date, description, amount, account, flagged | Excel import / manual | Transaction list, flagging |
| `flagged_accounts` | account_name, reason, company_id | Flagging rules | Review dashboard |
| `transaction_import_history` | filename, rows, status, timestamp | Excel upload | Import log |
| `income_records` | id, date, source, amount, company_id | Form entry | Income reports |
| `expense_records` | id, date, category, amount, company_id | Form entry | Expense reports |
| `vat_income` | id, tin, amount, vat_amount, date, company_id | VAT data entry | VAT summary |
| `vat_expenses` | id, tin, amount, vat_amount, date, company_id | VAT data entry | VAT summary |
| `vat_capital` | id, description, amount, date, company_id | VAT data entry | Capital report |
| `fin_gl_entries` | id, account, debit, credit, date, company_id | Finance module | GL reports |
| `fin_ar_ap` | id, type, party, amount, due_date, company_id | Invoice / bill entry | AR/AP aging |
| `fin_assets` | id, name, cost, depreciation, company_id | Asset registration | Asset register |
| `fin_budgets` | id, department, period, amount, company_id | Budget planning | Budget vs actuals |
| `fin_shareholders` | id, name, shares, company_id | Shareholder entry | Dividend calculation |
| `fin_dividends` | id, shareholder_id, amount, date, company_id | Dividend declaration | Payout reports |
| `cpo_records` | id, name, date, amount, bid_name, is_returned | Excel import / manual | CPO list, dashboard |

### HR, Payroll & Learning (20 tables)
| Table | Key Columns | Input | Output |
|-------|-------------|-------|--------|
| `employees` | employee_id, name, salary, tin, department, company_id | Add form / Excel import | Payroll calc, reports |
| `payroll_data` | employee_id, month, year, gross, tax, net, company_id | Payroll run | Payslips, bank file |
| `allowance_definitions` | id, name, type, amount/percent, company_id | Allowance config | Payroll calculation |
| `deduction_definitions` | id, name, type, amount/percent, company_id | Deduction config | Payroll calculation |
| `hrm_payroll_runs` | id, month, year, status, total, company_id | HRM payroll run | Payroll history |
| `hrm_leave_requests` | id, employee_id, type, start, end, status | Employee request | Leave calendar |
| `hrm_training_records` | id, employee_id, course, status, company_id | Training assignment | Training report |
| `hrm_performance_reviews` | id, employee_id, reviewer, score, period | Review submission | Performance dashboard |
| `hrm_grievances` | id, employee_id, subject, status, company_id | Grievance filing | Grievance tracker |
| `lms_courses` | id, title, category, duration, company_id | Course creation | Course catalog |
| `lms_learning_paths` | id, title, course_ids, company_id | Path creation | Learning roadmaps |
| `lms_enrollments` | id, user_id, course_id, progress, status | Auto-assign / manual | Progress tracking |
| `lms_certificates` | id, user_id, course_id, issued_at | Course completion | Certificate view |
| `lms_quizzes` | id, course_id, questions_json | Quiz creation | Quiz delivery |
| `lms_quiz_attempts` | id, user_id, quiz_id, score, passed | Quiz submission | Score reports |
| `lms_resources` | id, course_id, type, url, title | Resource upload | Course materials |
| `lms_gamification` | user_id, points, badges_json, level | Activity triggers | Leaderboard |
| `lms_skill_matrix` | user_id, skill, proficiency, company_id | Assessment | Skill gap analysis |

### Operations (22 tables)
| Table | Key Columns | Input | Output |
|-------|-------------|-------|--------|
| `inventory_items` | id, sku, name, quantity, unit_cost, category_id | Add form / import | Stock list, valuation |
| `inventory_categories` | id, name, company_id | Category setup | Item classification |
| `inventory_movements` | id, item_id, type, quantity, date | Stock in/out | Movement report |
| `inventory_allocations` | id, item_id, department, quantity | Allocation request | Allocation report |
| `inventory_maintenance` | id, item_id, type, date, cost | Maintenance log | Maintenance schedule |
| `inventory_requisitions` | id, item_id, quantity, status, requester | Requisition form | Approval workflow |
| `inventory_import_history` | id, filename, rows, status | Excel upload | Import log |
| `bid_records` | id, title, client, amount, status, deadline | Bid form entry | Bid pipeline |
| `bid_documents_meta` | id, bid_id, filename, storage_path, size | File upload | Document viewer |
| `machinery_assets` | id, name, type, serial, site_id, status | Asset registration | Fleet dashboard |
| `machinery_sites` | id, name, location, company_id | Site setup | Site management |
| `machinery_transfers` | id, asset_id, from_site, to_site, date | Transfer request | Transfer history |
| `machinery_maintenance` | id, asset_id, type, date, cost, status | Work order entry | Maintenance schedule |
| `machinery_shift_logs` | id, asset_id, operator, hours, date | Shift logging | Utilization report |
| `machinery_fuel_logs` | id, asset_id, liters, cost, date | Fuel entry | Fuel consumption |
| `machinery_certifications` | id, asset_id, cert_type, expiry | Cert registration | Expiry alerts |
| `proc_vendors` | id, name, tin, category, status, company_id | Vendor registration | Vendor directory |
| `proc_purchase_requisitions` | id, requester, department, status | PR form | Approval workflow |
| `proc_purchase_orders` | id, vendor_id, total, status, company_id | PO creation | PO tracking |
| `proc_po_lines` | id, po_id, item, quantity, unit_price | PO line items | PO detail |
| `proc_grn` | id, po_id, received_date, inspector | Goods receipt | GRN report |
| `proc_invoices` | id, vendor_id, po_id, amount, status | Invoice entry | AP aging |

### Collaboration & Admin (18 tables)
| Table | Key Columns | Input | Output |
|-------|-------------|-------|--------|
| `comm_channels` | id, company_id, name, type, created_by | Channel creation | Channel list |
| `comm_messages` | id, channel_id, sender, content, parent_id | Message post | Chat thread |
| `comm_reactions` | id, message_id, user_id, emoji | Emoji reaction | Reaction display |
| `comm_user_status` | user_id, status_text, dnd_enabled | Status update | Presence indicator |
| `comm_file_metadata` | id, message_id, filename, storage_path | File attachment | File download |
| `pm_projects` | id, name, client, budget, status, company_id | Project creation | Project dashboard |
| `pm_wbs_elements` | id, project_id, code, name, parent_id | WBS setup | WBS tree |
| `pm_tasks` | id, project_id, wbs_id, assignee, status | Task creation | Kanban / Gantt |
| `pm_site_reports` | id, project_id, date, weather, progress | Daily report | Site log |
| `ems_venues` | id, name, capacity, location, company_id | Venue setup | Venue list |
| `ems_bookings` | id, venue_id, event_name, date, status | Booking form | Booking calendar |
| `ems_service_items` | id, name, unit_price, category | Service catalog | Booking add-ons |
| `ems_booking_services` | booking_id, service_id, quantity, total | Service selection | Invoice lines |
| `siem_events` | id, event_type, severity, source, company_id | Auto-logged | Security dashboard |
| `siem_alerts` | id, event_id, severity, acknowledged | Alert rules | Alert feed, CSV export |
| `audit_trail` | id, user_id, action, entity, timestamp | Auto-logged | Compliance audit |
| `notifications` | id, user_id, title, message, read, company_id | System events | Bell icon feed |
| `sales_contacts` | id, name, email, message, timestamp | Contact form | Lead management |

---

## Multi-Tenancy Isolation

```
                          REQUEST
                             │
                    ┌────────▼────────┐
                    │ Session Cookie   │  ← current_company_id
                    └────────┬────────┘
                             │
            ┌────────────────▼────────────────┐
            │ LAYER 1: App Middleware          │
            │ Sets request.state.company_id    │
            │ Auto-creates default tenant      │
            │ Caches tenant info in Redis      │
            └────────────────┬────────────────┘
                             │
            ┌────────────────▼────────────────┐
            │ LAYER 2: Module License Check    │
            │ Starter / Professional / Enterprise
            │ Blocks unlicensed module access  │
            └────────────────┬────────────────┘
                             │
            ┌────────────────▼────────────────┐
            │ LAYER 3: PostgreSQL RLS          │
            │ SET LOCAL app.current_company_id │
            │ WHERE company_id = current_setting()
            │ Applied to 18 core tables        │
            └─────────────────────────────────┘
```

---

## Data Flow Examples

### Payroll Run (end-to-end)
```
User clicks "Run Payroll"
    │
    ▼
POST /payroll/run (month, year)
    │
    ├──► Read: employees (filtered by company_id)
    ├──► Read: allowance_definitions
    ├──► Read: deduction_definitions
    ├──► Calculate: Ethiopian tax brackets, pension 7%/11%
    │
    ├──► Write: payroll_data (one row per employee)
    ├──► Event: payroll.completed → Redis pub/sub
    │       │
    │       ├──► Handler: auto-post journal_entries + journal_entry_lines
    │       ├──► Handler: log siem_events (payroll completion)
    │       └──► Handler: trigger backup_log entry
    │
    └──► Output: payroll summary page + downloadable payslips
```

### Transaction Import
```
User uploads Excel file
    │
    ▼
POST /transactions/import
    │
    ├──► Parse: openpyxl / pandas reads .xlsx
    ├──► Validate: required columns, data types
    ├──► Write: transactions (bulk upsert)
    ├──► Write: transaction_import_history (log)
    ├──► Invalidate: Redis cache (transactions:{company_id})
    │
    └──► Output: import result summary (success/error counts)
```

### Bid Document Upload
```
User uploads document to bid
    │
    ▼
POST /bid/upload/{bid_id}
    │
    ├──► If S3_BUCKET set:
    │       └──► boto3 → S3 put_object (bid_docs/{bid_id}/{filename})
    │       └──► Write: bid_documents_meta (storage_path = s3://...)
    │
    ├──► If no S3:
    │       └──► Save to: web/data/bid_docs/{bid_id}/{filename}
    │       └──► Write: bid_documents_meta (storage_path = local path)
    │
    └──► Output: redirect to bid detail page
```

---

## Cache Strategy

```
┌──────────────┐     cache hit     ┌──────────────┐
│  Route       │ ◄──────────────── │  Redis        │
│  Handler     │                   │  (256MB LRU)  │
│              │ ──── cache miss ──►│  namespace:   │
│              │      ↓            │  acct:*       │
│              │  ┌───▼────┐       │               │
│              │  │Postgres│       │  Key patterns: │
│              │  │ query  │       │  tenant:{cid} │
│              │  └───┬────┘       │  inv:{cid}    │
│              │      │            │  txn:{cid}:p* │
│              │  ◄───┘ set cache  │  dash:{cid}   │
└──────────────┘ ──────────────── ►└──────────────┘
                                         │
                 On mutation:            │
                 invalidate_company_cache()
                 deletes per-module keys
```

---

## Auth & Security

```
┌─────────────────────────────────────────────────────┐
│                  SECURITY LAYERS                     │
├─────────────────────────────────────────────────────┤
│ 1. TLS termination (Nginx)                          │
│ 2. Rate limiting (SlowAPI)                          │
│ 3. CSRF token validation (session + hidden field)   │
│ 4. Session auth (bcrypt passwords, 30min timeout)   │
│ 5. Bearer token auth (SHA-256 hashed API keys)      │
│ 6. Privilege levels (10=viewer → 99=super_admin)    │
│ 7. Account lockout (5 failures → 30min lock)        │
│ 8. Row-Level Security (Postgres per-company)        │
│ 9. SIEM audit logging (all security events)         │
│ 10. Security headers (CSP, HSTS, X-Frame-Options)   │
│ 11. Request size limit (25MB default)               │
│ 12. Password policy (12+ chars, complexity rules)   │
└─────────────────────────────────────────────────────┘
```
