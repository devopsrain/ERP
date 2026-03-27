-- ============================================================================
-- EBMS Performance Indexes Migration
-- File: web/migrations/versions/add_performance_indexes.sql
-- Apply with: psql $DATABASE_URL -f add_performance_indexes.sql
-- Safe to run multiple times (all use CREATE INDEX IF NOT EXISTS)
-- ============================================================================

-- ── Transactions ─────────────────────────────────────────────────────────────
-- Most queries filter by company + sort by date
CREATE INDEX IF NOT EXISTS idx_transactions_company_date
    ON transactions (company_id, date DESC);

-- Flagged transactions filter
CREATE INDEX IF NOT EXISTS idx_transactions_flagged
    ON transactions (company_id, is_flagged)
    WHERE is_flagged = TRUE;

-- ── SIEM Events ──────────────────────────────────────────────────────────────
-- Dashboard alert count + event log page both hit these
CREATE INDEX IF NOT EXISTS idx_siem_events_company_created
    ON siem_events (company_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_siem_events_severity
    ON siem_events (severity, created_at DESC)
    WHERE severity IN ('HIGH', 'CRITICAL');

-- IP tracker page
CREATE INDEX IF NOT EXISTS idx_siem_events_ip
    ON siem_events (ip_address, created_at DESC);

-- ── Employees ────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_employees_company_active
    ON employees (company_id, is_active, name);

CREATE INDEX IF NOT EXISTS idx_employees_tin
    ON employees (tin_number);

-- ── Payroll Records ───────────────────────────────────────────────────────────
-- Payroll calculate + payslip lookups
CREATE INDEX IF NOT EXISTS idx_payroll_employee_period
    ON payroll_records (employee_id, period DESC);

CREATE INDEX IF NOT EXISTS idx_payroll_company_period
    ON payroll_records (company_id, period DESC);

-- ── Journal Entries ───────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_journal_entries_company_date
    ON journal_entries (company_id, entry_date DESC);

-- ── Chart of Accounts ─────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_accounts_company_code
    ON chart_of_accounts (company_id, account_code);

CREATE INDEX IF NOT EXISTS idx_accounts_company_type
    ON chart_of_accounts (company_id, account_type, is_active);

-- ── VAT Income / Expenses ─────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_vat_income_company_date
    ON vat_income (company_id, date DESC);

CREATE INDEX IF NOT EXISTS idx_vat_expenses_company_date
    ON vat_expenses (company_id, date DESC);

CREATE INDEX IF NOT EXISTS idx_vat_capital_company_date
    ON vat_capital (company_id, date DESC);

-- ── Inventory ────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_inventory_company_active
    ON inventory_items (company_id, is_active);

-- Low-stock alert query
CREATE INDEX IF NOT EXISTS idx_inventory_low_stock
    ON inventory_items (company_id, quantity, reorder_level)
    WHERE quantity <= reorder_level;

-- ── CPO ───────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_cpo_company_date
    ON cpo_records (company_id, date DESC);

CREATE INDEX IF NOT EXISTS idx_cpo_returned
    ON cpo_records (company_id, is_returned);

-- ── Bids ─────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_bids_company_status
    ON bids (company_id, status, deadline DESC);

-- ── Letters ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_letters_company_date
    ON letters (company_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_letters_status
    ON letters (company_id, status);

-- ── Users ─────────────────────────────────────────────────────────────────────
-- Login query always uses username or email
CREATE INDEX IF NOT EXISTS idx_users_username
    ON users (username);

CREATE INDEX IF NOT EXISTS idx_users_email
    ON users (email);

-- Login history for the auth portal page
CREATE INDEX IF NOT EXISTS idx_login_history_user_date
    ON login_history (user_id, logged_at DESC);

-- ── Income & Expense ──────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_income_expense_company_date
    ON income_expense (company_id, date DESC);

-- ── Analyse updated tables so the query planner uses these indexes ─────────────
ANALYZE transactions;
ANALYZE siem_events;
ANALYZE employees;
ANALYZE chart_of_accounts;
ANALYZE vat_income;
ANALYZE vat_expenses;
ANALYZE inventory_items;
ANALYZE bids;
ANALYZE letters;
ANALYZE users;
