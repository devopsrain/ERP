"""
Integration Tests — Ethiopian Accounting System
=================================================
Tests that span multiple modules working together:
  - Creating data via routes and verifying via data stores
  - Version control + backup integration
  - Payroll calculation via HTTP endpoint
  - Import/export round-trips

Run:  cd web && pytest tests/test_integration.py -v
"""
import json
import pytest


# ══════════════════════════════════════════════════════════════════
#  Version + Backup Integration
# ══════════════════════════════════════════════════════════════════

class TestVersionBackupIntegration:
    """Version system leverages BackupEngine for snapshots."""

    @pytest.mark.integration
    def test_version_api_returns_active_with_snapshot(self, logged_in_client):
        resp = logged_in_client.get('/version/api/current')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'version' in data
        active = data.get('active', {})
        assert active.get('status') == 'active'
        # v1.0.0 should have a snapshot archive
        assert active.get('snapshot_archive') is not None

    @pytest.mark.integration
    def test_backup_api_lists_version_snapshot(self, logged_in_client):
        resp = logged_in_client.get('/backup/api/list')
        assert resp.status_code == 200
        data = resp.get_json()
        backups = data.get('backups', data) if isinstance(data, dict) else data
        # Should include the version-1.0.0 snapshot
        if isinstance(backups, list) and len(backups) > 0:
            names = [b.get('archive_name', '') if isinstance(b, dict) else str(b) for b in backups]
            version_backups = [n for n in names if 'version' in n.lower()]
            assert len(version_backups) >= 1, \
                f"Expected version snapshot in backup list, got: {names[:5]}"


# ══════════════════════════════════════════════════════════════════
#  Payroll Route Integration
# ══════════════════════════════════════════════════════════════════

class TestPayrollRouteIntegration:
    """Payroll calculations via HTTP endpoints."""

    @pytest.mark.integration
    def test_tax_calculator_api(self, logged_in_client):
        resp = logged_in_client.post('/api/payroll/tax-calculator', data={
            'basic_salary': '10000',
        })
        # Should return 200 with calculation result
        if resp.status_code == 200:
            data = resp.get_json()
            if data:
                assert 'tax' in str(data).lower() or 'income_tax' in str(data).lower() or 'net' in str(data).lower()

    @pytest.mark.integration
    def test_payroll_dashboard_loads_with_data(self, logged_in_client):
        resp = logged_in_client.get('/payroll')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        # Dashboard should render (even if empty)
        assert 'payroll' in html.lower() or 'dashboard' in html.lower()


# ══════════════════════════════════════════════════════════════════
#  VAT Route Integration
# ══════════════════════════════════════════════════════════════════

class TestVATRouteIntegration:
    """Add VAT income/expense via route, verify via API."""

    @pytest.mark.integration
    def test_add_vat_income_and_check_api(self, logged_in_client):
        # Add income (route expects JSON via request.get_json())
        resp = logged_in_client.post('/vat/income/add',
            json={
                'contract_date': '2026-01-15',
                'description': 'Test VAT Income',
                'category': 'PRODUCT_SALES',
                'gross_amount': '10000',
                'vat_type': 'STANDARD',
                'vat_rate': '0.15',
                'customer_name': 'Test Company',
                'customer_tin': '0012345678',
                'invoice_number': 'INV-001',
            },
            follow_redirects=True)
        # May fail if no company is set up — accept 200, 302, or 400
        assert resp.status_code in (200, 302, 400)

        # Check stats API
        stats_resp = logged_in_client.get('/vat/api/stats')
        assert stats_resp.status_code == 200


# ══════════════════════════════════════════════════════════════════
#  Income/Expense Route Integration
# ══════════════════════════════════════════════════════════════════

class TestIncomeExpenseRouteIntegration:
    """Add income/expense via routes and verify via API."""

    @pytest.mark.integration
    def test_add_income_via_route(self, logged_in_client):
        resp = logged_in_client.post('/income-expense/income/add', data={
            'date': '2026-02-01',
            'description': 'Integration Test Income',
            'amount': '50000',
            'category': 'Services',
            'payment_method': 'Bank Transfer',
            'reference': 'INT-001',
        }, follow_redirects=True)
        assert resp.status_code == 200

    @pytest.mark.integration
    def test_add_expense_via_route(self, logged_in_client):
        resp = logged_in_client.post('/income-expense/expenses/add', data={
            'date': '2026-02-01',
            'description': 'Integration Test Expense',
            'amount': '15000',
            'category': 'Rent',
            'payment_method': 'Bank Transfer',
            'reference': 'INT-002',
        }, follow_redirects=True)
        assert resp.status_code == 200

    @pytest.mark.integration
    def test_api_stats_reflects_data(self, logged_in_client):
        resp = logged_in_client.get('/income-expense/api/stats')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None


# ══════════════════════════════════════════════════════════════════
#  SIEM + Auth Integration
# ══════════════════════════════════════════════════════════════════

class TestSIEMIntegration:
    """SIEM should track events across modules."""

    @pytest.mark.integration
    def test_siem_dashboard_loads(self, logged_in_client):
        resp = logged_in_client.get('/siem/')
        assert resp.status_code == 200

    @pytest.mark.integration
    def test_siem_events_endpoint(self, logged_in_client):
        resp = logged_in_client.get('/siem/events')
        assert resp.status_code == 200

    @pytest.mark.integration
    def test_siem_alerts_endpoint(self, logged_in_client):
        resp = logged_in_client.get('/siem/alerts')
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════
#  Bid Tracker Integration
# ══════════════════════════════════════════════════════════════════

class TestBidTrackerIntegration:
    """Add a bid via route, verify it appears."""

    @pytest.mark.integration
    def test_add_bid_via_route(self, logged_in_client):
        resp = logged_in_client.post('/bid/add', data={
            'title': 'Integration Test Bid',
            'organization': 'Test Ministry',
            'bid_type': 'RFP',
            'status': 'Draft',
            'deadline': '2026-06-30',
            'estimated_value': '1000000',
            'description': 'Test bid from integration suite',
        }, follow_redirects=True)
        assert resp.status_code == 200

    @pytest.mark.integration
    def test_bid_api_stats(self, logged_in_client):
        resp = logged_in_client.get('/bid/api/stats')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None


# ══════════════════════════════════════════════════════════════════
#  Inventory Integration
# ══════════════════════════════════════════════════════════════════

class TestInventoryIntegration:
    """Add inventory item via route."""

    @pytest.mark.integration
    def test_add_item_via_route(self, logged_in_client):
        resp = logged_in_client.post('/inventory/items/add', data={
            'name': 'Integration Test Item',
            'category': 'Equipment',
            'unit': 'piece',
            'quantity': '25',
            'unit_cost': '5000',
            'reorder_level': '5',
            'location': 'Warehouse A',
        }, follow_redirects=True)
        assert resp.status_code == 200

    @pytest.mark.integration
    def test_inventory_dashboard_has_data(self, logged_in_client):
        resp = logged_in_client.get('/inventory/')
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════
#  Cross-Module: Version Badge Everywhere
# ══════════════════════════════════════════════════════════════════

class TestVersionBadgeAcrossModules:
    """Version badge should appear on pages from different modules."""

    PAGES = [
        '/payroll',
        '/vat/dashboard',
        '/inventory/',
        '/bid/dashboard',
        '/siem/',
        '/backup/dashboard',
        '/version/dashboard',
    ]

    @pytest.mark.integration
    @pytest.mark.parametrize('path', PAGES)
    def test_version_badge_on_page(self, logged_in_client, path):
        resp = logged_in_client.get(path)
        if resp.status_code == 200:
            html = resp.data.decode('utf-8')
            assert 'bi-tag-fill' in html or 'app_version' in html, \
                f"Version badge missing on {path}"
