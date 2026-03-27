-- ============================================================================
-- Row-Level Security (RLS) Policies — Tenant Isolation
-- File: web/migrations/versions/add_rls_policies.sql
--
-- Apply with:
--   psql $DATABASE_URL -f add_rls_policies.sql
--
-- Prerequisites (run once, as superuser):
--   CREATE ROLE erp_app LOGIN PASSWORD '<strong-password>';
--   GRANT CONNECT ON DATABASE <dbname> TO erp_app;
--   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO erp_app;
--   ALTER DEFAULT PRIVILEGES IN SCHEMA public
--       GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO erp_app;
--   -- Confirm erp_app cannot bypass RLS:
--   -- SELECT rolbypassrls FROM pg_roles WHERE rolname = 'erp_app';
--   -- Must return: f
--
-- How it works:
--   Every authenticated route calls get_tenant_cursor(company_id) which executes:
--     SELECT set_config('app.current_company_id', '<uuid>', TRUE)
--   TRUE = transaction-local scope.  The DB rejects any row whose company_id
--   does not match that setting.  A superuser is never used by the application.
--
-- Safe to re-run — uses ALTER TABLE ... FORCE ROW LEVEL SECURITY and
-- CREATE POLICY ... IF NOT EXISTS where supported, or DROP POLICY IF EXISTS
-- followed by CREATE POLICY for idempotency.
-- ============================================================================

-- ── Helper: a small DO block so we can drop policies idempotently ─────────
-- (CREATE POLICY IF NOT EXISTS is not supported in PG < 16)

DO $$
DECLARE
    _tables TEXT[] := ARRAY[
        'transactions',
        'siem_events',
        'employees',
        'payroll_records',
        'journal_entries',
        'chart_of_accounts',
        'vat_income',
        'vat_expenses',
        'vat_capital',
        'inventory_items',
        'inventory_categories',
        'inventory_movements',
        'inventory_requisitions',
        'cpo_records',
        'bid_records',
        'letters',
        'income_expense',
        'users'
    ];
    _t TEXT;
BEGIN
    FOREACH _t IN ARRAY _tables LOOP
        -- Enable RLS (no-op if already enabled)
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', _t);
        -- FORCE RLS ensures even the table owner cannot bypass it
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', _t);
        -- Drop existing policy so we can replace it cleanly
        EXECUTE format(
            'DROP POLICY IF EXISTS tenant_isolation ON %I', _t
        );
        -- Create the isolation policy using the transaction-local config variable
        -- set by get_tenant_cursor() / get_async_tenant_conn() in the app layer
        EXECUTE format(
            $pol$
            CREATE POLICY tenant_isolation ON %I
                USING (
                    company_id = current_setting('app.current_company_id', TRUE)::TEXT
                )
                WITH CHECK (
                    company_id = current_setting('app.current_company_id', TRUE)::TEXT
                )
            $pol$,
            _t
        );
        RAISE NOTICE 'RLS policy applied to table: %', _t;
    END LOOP;
END;
$$;

-- ── Tables with no company_id (global / cross-tenant) — RLS disabled ────────
-- login_history   : system-wide security audit; no tenant scoping by design
-- journal_entry_lines : scoped via entry_id FK to journal_entries; no direct company_id
-- bid_documents_meta  : scoped via bid_id FK to bid_records; no direct company_id
-- cpo_import_history  : system import log; no company_id column
-- inventory_import_history : similar import log
-- tenants / companies     : global directory; never accessed by erp_app directly
-- refresh_tokens          : internal auth table

-- ── Bypass policy for server-side admin operations ────────────────────────
-- A separate POLICY can be added for the superuser role if needed:
--   CREATE POLICY admin_bypass ON invoices TO postgres USING (TRUE);
-- This bypasses RLS for the superuser when running migrations or admin queries.

-- ── Verification queries ──────────────────────────────────────────────────
-- Run these manually after applying to confirm RLS is active:
--
-- SELECT tablename, rowsecurity, forcerowaccessecurity
-- FROM pg_tables
-- WHERE schemaname = 'public'
-- ORDER BY tablename;
--
-- SELECT tablename, policyname, cmd, qual
-- FROM pg_policies
-- WHERE schemaname = 'public' AND policyname = 'tenant_isolation'
-- ORDER BY tablename;
