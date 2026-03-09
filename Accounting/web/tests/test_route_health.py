"""
Route Health / Smoke Tests — Ethiopian Accounting System
=========================================================
Verifies every GET route returns 200 (or a valid redirect/login).
These are non-destructive; they don't POST any data.

Run:  cd web && pytest tests/test_route_health.py -v
"""
import pytest

# ── All GET routes grouped by module ──────────────────────────────

# Routes that should return 200 without any authentication
PUBLIC_ROUTES = [
    '/auth/login',
    '/auth/access-denied',
]

# Routes that redirect to login when not authenticated (302/303)
AUTH_REDIRECT_ROUTES = [
    '/',
]

# Routes that require login session — tested with logged_in_client
AUTHENTICATED_GET_ROUTES = [
    # ── App direct routes ──
    ('/setup', 'Setup'),
    ('/accounts', 'Chart of Accounts (legacy)'),
    ('/accounts/new', 'New Account (legacy)'),
    ('/journal-entry', 'Journal Entry Form'),
    ('/quick-transactions', 'Quick Transactions'),
    ('/reports/trial-balance', 'Trial Balance'),
    ('/reports/income-statement', 'Income Statement'),
    ('/reports/balance-sheet', 'Balance Sheet'),
    ('/api/accounts', 'API: Accounts'),
    # ── Auth ──
    ('/auth/portal', 'Auth Portal'),
    ('/auth/users', 'User Management'),
    ('/auth/api/login-history', 'API: Login History'),
    ('/auth/api/stats', 'API: Auth Stats'),
    # ── Payroll ──
    ('/payroll', 'Payroll Dashboard'),
    ('/payroll/employees', 'Employee List'),
    ('/payroll/employees/add', 'Add Employee'),
    ('/payroll/calculate', 'Calculate Payroll'),
    ('/payroll/tax-calculator', 'Tax Calculator'),
    ('/payroll/reports', 'Payroll Reports'),
    # ── VAT ──
    ('/vat/dashboard', 'VAT Dashboard'),
    ('/vat/income', 'VAT Income List'),
    ('/vat/income/add', 'VAT Add Income'),
    ('/vat/expenses', 'VAT Expense List'),
    ('/vat/expenses/add', 'VAT Add Expense'),
    ('/vat/capital', 'VAT Capital List'),
    ('/vat/capital/add', 'VAT Add Capital'),
    ('/vat/api/stats', 'VAT API Stats'),
    # ── Journal Entries ──
    ('/journal/', 'Journal List'),
    ('/journal/add', 'Journal Add'),
    ('/journal/dashboard', 'Journal Dashboard'),
    # ── Chart of Accounts (Blueprint) ──
    ('/accounts/', 'Accounts List (BP)'),
    ('/accounts/add', 'Add Account (BP)'),
    ('/accounts/dashboard', 'Accounts Dashboard (BP)'),
    ('/accounts/trial-balance', 'Accounts Trial Balance (BP)'),
    # ── Income & Expense ──
    ('/income-expense/', 'Income & Expense Dashboard'),
    ('/income-expense/income', 'Income List'),
    ('/income-expense/income/add', 'Add Income'),
    ('/income-expense/expenses', 'Expense List'),
    ('/income-expense/expenses/add', 'Add Expense'),
    ('/income-expense/reports', 'Reports'),
    ('/income-expense/api/stats', 'IE API Stats'),
    ('/income-expense/api/income', 'IE API Income'),
    ('/income-expense/api/expenses', 'IE API Expenses'),
    # ── Transactions ──
    ('/transactions/', 'Transaction Dashboard'),
    ('/transactions/dashboard', 'Transaction Dashboard (alt)'),
    ('/transactions/list', 'Transaction List'),
    ('/transactions/flagged-accounts', 'Flagged Accounts'),
    ('/transactions/import-history', 'Import History'),
    ('/transactions/api/stats', 'Transaction API Stats'),
    # ── CPO ──
    ('/cpo/', 'CPO Dashboard'),
    ('/cpo/list', 'CPO List'),
    ('/cpo/add', 'Add CPO'),
    # ── Inventory ──
    ('/inventory/', 'Inventory Dashboard'),
    ('/inventory/items', 'Inventory Items'),
    ('/inventory/items/add', 'Add Inventory Item'),
    ('/inventory/movements', 'Inventory Movements'),
    ('/inventory/movements/add', 'Add Movement'),
    ('/inventory/valuation', 'Inventory Valuation'),
    ('/inventory/replenishment', 'Replenishment'),
    ('/inventory/replenishment/add', 'Add Requisition'),
    ('/inventory/allocations', 'Allocations'),
    ('/inventory/allocations/add', 'Add Allocation'),
    ('/inventory/maintenance', 'Maintenance'),
    ('/inventory/maintenance/add', 'Add Maintenance'),
    ('/inventory/reports', 'Inventory Reports'),
    ('/inventory/reports/stock', 'Stock Report'),
    ('/inventory/reports/valuation', 'Valuation Report'),
    ('/inventory/reports/movements', 'Movement Report'),
    # ── Bid Tracker ──
    ('/bid/', 'Bid Dashboard'),
    ('/bid/dashboard', 'Bid Dashboard (alt)'),
    ('/bid/add', 'Add Bid'),
    ('/bid/api/stats', 'Bid API Stats'),
    # ── SIEM ──
    ('/siem/', 'SIEM Dashboard'),
    ('/siem/events', 'SIEM Event Log'),
    ('/siem/ips', 'SIEM IP Tracker'),
    ('/siem/alerts', 'SIEM Alerts'),
    # ── Backup ──
    ('/backup/dashboard', 'Backup Dashboard'),
    ('/backup/api/stats', 'Backup API Stats'),
    ('/backup/api/list', 'Backup API List'),
    # ── Version ──
    ('/version/', 'Version Dashboard'),
    ('/version/dashboard', 'Version Dashboard (alt)'),
    ('/version/create', 'Create Version'),
    ('/version/api/current', 'Version API Current'),
    ('/version/api/list', 'Version API List'),
]

# Multi-company routes (separate auth system)
MULTICOMPANY_ROUTES = [
    ('/company/login', 'MC Login'),
]


# ══════════════════════════════════════════════════════════════════
#  TESTS
# ══════════════════════════════════════════════════════════════════

class TestPublicRoutes:
    """Routes accessible without login should return 200."""

    @pytest.mark.smoke
    @pytest.mark.routes
    @pytest.mark.parametrize('path', PUBLIC_ROUTES)
    def test_public_route_returns_200(self, client, path):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"


class TestAuthRedirects:
    """Unauthenticated requests to protected routes should redirect to login."""

    @pytest.mark.smoke
    @pytest.mark.routes
    @pytest.mark.parametrize('path', AUTH_REDIRECT_ROUTES)
    def test_unauthenticated_redirects(self, fresh_client, path):
        resp = fresh_client.get(path, follow_redirects=False)
        assert resp.status_code in (301, 302, 303, 308), \
            f"{path} should redirect when not logged in, got {resp.status_code}"


class TestAuthenticatedRoutes:
    """All protected GET routes should return 200 when logged in."""

    @pytest.mark.smoke
    @pytest.mark.routes
    @pytest.mark.parametrize('path,desc', AUTHENTICATED_GET_ROUTES,
                             ids=[r[1] for r in AUTHENTICATED_GET_ROUTES])
    def test_authenticated_route_returns_200(self, logged_in_client, path, desc):
        resp = logged_in_client.get(path)
        assert resp.status_code == 200, \
            f"[{desc}] {path} returned {resp.status_code}"

    @pytest.mark.smoke
    @pytest.mark.routes
    @pytest.mark.xfail(reason='Requires populated company financial data')
    def test_vat_financial_summary(self, logged_in_client):
        resp = logged_in_client.get('/vat/summary')
        assert resp.status_code == 200


class TestMultiCompanyRoutes:
    """Multi-company routes with their own auth system."""

    @pytest.mark.smoke
    @pytest.mark.routes
    @pytest.mark.parametrize('path,desc', MULTICOMPANY_ROUTES,
                             ids=[r[1] for r in MULTICOMPANY_ROUTES])
    def test_multicompany_route(self, client, path, desc):
        resp = client.get(path)
        assert resp.status_code in (200, 302), \
            f"[{desc}] {path} returned {resp.status_code}"


class TestAPIEndpointsReturnJSON:
    """API endpoints should return valid JSON."""

    API_ROUTES = [
        '/api/accounts',
        '/vat/api/stats',
        '/income-expense/api/stats',
        '/transactions/api/stats',
        '/bid/api/stats',
        '/backup/api/stats',
        '/backup/api/list',
        '/version/api/current',
        '/version/api/list',
        '/auth/api/stats',
        '/auth/api/login-history',
    ]

    @pytest.mark.smoke
    @pytest.mark.routes
    @pytest.mark.parametrize('path', API_ROUTES)
    def test_api_returns_json(self, logged_in_client, path):
        resp = logged_in_client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"
        assert resp.content_type.startswith('application/json'), \
            f"{path} content-type is {resp.content_type}, expected JSON"
        data = resp.get_json()
        assert data is not None, f"{path} returned empty/invalid JSON"


class TestVersionBadgeInFooter:
    """The version badge should appear on pages that use base templates."""

    def test_version_badge_on_login_page(self, client):
        """Login page is public and should show the version badge."""
        resp = client.get('/auth/login')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'bi-tag-fill' in html or 'app_version' in html or 'v1.' in html, \
            "Version badge not found on /auth/login"

    def test_version_badge_on_protected_page(self, logged_in_client):
        """Protected pages should also show the version badge once logged in."""
        resp = logged_in_client.get('/version/dashboard')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'bi-tag-fill' in html or 'app_version' in html or 'v1.' in html, \
            "Version badge not found on /version/dashboard"
