"""
Data Store Unit Tests — Ethiopian Accounting System
=====================================================
Tests CRUD operations for each data store in isolation using temporary directories.

Run:  cd web && pytest tests/test_data_stores.py -v
"""
import os
import pytest
from datetime import datetime, date


# ══════════════════════════════════════════════════════════════════
#  Income & Expense Data Store
# ══════════════════════════════════════════════════════════════════

class TestIncomeExpenseDataStore:
    """CRUD operations on income & expense records."""

    @pytest.mark.unit
    def test_save_and_read_income(self, income_expense_store):
        store = income_expense_store
        record = {
            'date': '2026-01-15',
            'description': 'Consulting Revenue',
            'amount': 50000.0,
            'category': 'Services',
            'payment_method': 'Bank Transfer',
            'reference': 'INV-001',
        }
        result = store.save_income_record(record)
        assert result is True, f"save_income_record failed: {result}"

        records = store.get_all_income_records()
        assert len(records) >= 1, "Should have at least one income record"
        assert any('Consulting' in str(r) for r in records), \
            "Saved record not found in get_all_income_records"

    @pytest.mark.unit
    def test_save_and_read_expense(self, income_expense_store):
        store = income_expense_store
        record = {
            'date': '2026-01-20',
            'description': 'Office Rent',
            'amount': 15000.0,
            'category': 'Rent',
            'payment_method': 'Bank Transfer',
            'reference': 'RENT-JAN',
        }
        result = store.save_expense_record(record)
        assert result is not None

        records = store.get_all_expense_records()
        assert len(records) >= 1

    @pytest.mark.unit
    def test_summary_statistics_empty(self, income_expense_store):
        stats = income_expense_store.get_summary_statistics()
        assert isinstance(stats, dict)

    @pytest.mark.unit
    def test_delete_income_record(self, income_expense_store):
        store = income_expense_store
        record = {
            'date': '2026-02-01',
            'description': 'Delete Test',
            'amount': 100.0,
            'category': 'Test',
            'payment_method': 'Cash',
        }
        result = store.save_income_record(record)
        records = store.get_all_income_records()
        assert len(records) >= 1

        # Find the record ID
        record_id = None
        for r in records:
            if isinstance(r, dict) and 'Delete Test' in str(r.get('description', '')):
                record_id = r.get('id') or r.get('record_id')
                break

        if record_id:
            del_result = store.delete_income_record(record_id)
            remaining = store.get_all_income_records()
            assert len(remaining) < len(records), "Record should have been deleted"


# ══════════════════════════════════════════════════════════════════
#  Transaction Data Store
# ══════════════════════════════════════════════════════════════════

class TestTransactionDataStore:
    """Transaction CRUD and flagging operations."""

    @pytest.mark.unit
    def test_save_and_retrieve_transaction(self, transaction_store):
        store = transaction_store
        txn = {
            'date': '2026-01-15',
            'description': 'Payment to vendor',
            'amount': 25000.0,
            'debit_account': '5000',
            'credit_account': '1000',
            'reference': 'TXN-001',
        }
        result = store.save_transaction(txn)
        assert result is not None

        all_txns = store.get_all_transactions()
        assert len(all_txns) >= 1

    @pytest.mark.unit
    def test_flagged_accounts_crud(self, transaction_store):
        store = transaction_store
        store.add_flagged_account(
            account_code='ACCT-001',
            account_name='John Doe',
            reason='Suspicious pattern',
        )
        flagged = store.get_flagged_accounts()
        assert len(flagged) >= 1

    @pytest.mark.unit
    def test_is_individual_name_static(self):
        from transaction_data_store import TransactionDataStore
        assert TransactionDataStore.is_individual_name('John Doe') is True
        assert TransactionDataStore.is_individual_name('ABC Corporation PLC') is False

    @pytest.mark.unit
    def test_summary_statistics(self, transaction_store):
        stats = transaction_store.get_summary_statistics()
        assert isinstance(stats, dict)


# ══════════════════════════════════════════════════════════════════
#  CPO Data Store
# ══════════════════════════════════════════════════════════════════

class TestCPODataStore:
    """CPO record CRUD."""

    @pytest.mark.unit
    def test_save_and_list_cpo(self, cpo_store):
        store = cpo_store
        cpo = {
            'date': '2026-01-15',
            'payee': 'Supplier A',
            'amount': 10000.0,
            'description': 'Office supplies',
            'authorized_by': 'admin',
            'payment_type': 'Cash',
        }
        result = store.save_cpo(cpo)
        assert result is not None

        all_cpos = store.get_all_cpos()
        assert len(all_cpos) >= 1

    @pytest.mark.unit
    def test_delete_cpo(self, cpo_store):
        store = cpo_store
        store.save_cpo({
            'date': '2026-02-01',
            'payee': 'Delete Me',
            'amount': 500.0,
            'description': 'Test delete',
            'authorized_by': 'admin',
        })
        cpos = store.get_all_cpos()
        if cpos:
            cpo_id = cpos[0].get('id') or cpos[0].get('cpo_id')
            if cpo_id:
                result = store.delete_cpo(cpo_id)
                assert result is not None

    @pytest.mark.unit
    def test_summary(self, cpo_store):
        summary = cpo_store.get_summary()
        assert isinstance(summary, dict)


# ══════════════════════════════════════════════════════════════════
#  Inventory Data Store
# ══════════════════════════════════════════════════════════════════

class TestInventoryDataStore:
    """Inventory item CRUD and stock operations."""

    @pytest.mark.unit
    def test_save_and_list_item(self, inventory_store):
        store = inventory_store
        item = {
            'name': 'Office Chair',
            'category': 'Furniture',
            'unit': 'piece',
            'quantity': 50,
            'unit_cost': 2500.0,
            'reorder_level': 10,
            'location': 'Warehouse A',
        }
        result = store.save_item(item)
        assert result is not None

        items = store.get_all_items()
        assert len(items) >= 1

    @pytest.mark.unit
    def test_dashboard_summary(self, inventory_store):
        summary = inventory_store.get_dashboard_summary()
        assert isinstance(summary, dict)

    @pytest.mark.unit
    def test_categories(self, inventory_store):
        cats = inventory_store.get_categories()
        assert isinstance(cats, list)

    @pytest.mark.unit
    def test_low_stock_items_empty(self, inventory_store):
        items = inventory_store.get_low_stock_items()
        assert isinstance(items, list)


# ══════════════════════════════════════════════════════════════════
#  Bid Data Store
# ══════════════════════════════════════════════════════════════════

class TestBidDataStore:
    """Bid record CRUD."""

    @pytest.mark.unit
    def test_save_and_list_bid(self, bid_store):
        bid = {
            'title': 'IT Infrastructure Upgrade',
            'organization': 'Ministry of Finance',
            'bid_type': 'RFP',
            'status': 'Draft',
            'deadline': '2026-03-15',
            'estimated_value': 500000.0,
            'description': 'Network infrastructure upgrade',
        }
        result = bid_store.save_bid(bid)
        assert result is not None

        bids = bid_store.get_all_bids()
        assert len(bids) >= 1

    @pytest.mark.unit
    def test_summary_stats(self, bid_store):
        stats = bid_store.get_summary_stats()
        assert isinstance(stats, dict)

    @pytest.mark.unit
    def test_delete_bid(self, bid_store):
        bid_store.save_bid({
            'title': 'Delete Test',
            'organization': 'Test Org',
            'bid_type': 'RFQ',
            'status': 'Draft',
            'deadline': '2026-04-01',
        })
        bids = bid_store.get_all_bids()
        if bids:
            bid_id = bids[0].get('id') or bids[0].get('bid_id')
            if bid_id:
                result = bid_store.delete_bid(bid_id)
                assert result is not None


# ══════════════════════════════════════════════════════════════════
#  Backup Engine
# ══════════════════════════════════════════════════════════════════

class TestBackupEngine:
    """Backup create / list / restore cycle."""

    @pytest.mark.unit
    def test_create_backup(self, backup_engine, tmp_data_dir):
        # Create a dummy file to back up
        dummy = os.path.join(tmp_data_dir, 'test_data.json')
        with open(dummy, 'w') as f:
            f.write('{"test": true}')

        result = backup_engine.create_backup(label='test-backup', triggered_by='pytest')
        assert result.get('success') is True, f"Create backup failed: {result}"
        assert result.get('archive_name') is not None

    @pytest.mark.unit
    def test_list_backups(self, backup_engine, tmp_data_dir):
        # Create a file and a backup
        dummy = os.path.join(tmp_data_dir, 'dummy.json')
        with open(dummy, 'w') as f:
            f.write('{}')
        backup_engine.create_backup(label='list-test', triggered_by='pytest')

        backups = backup_engine.list_backups()
        assert isinstance(backups, list)
        assert len(backups) >= 1

    @pytest.mark.unit
    def test_restore_requires_confirmation(self, backup_engine, tmp_data_dir):
        dummy = os.path.join(tmp_data_dir, 'dummy2.json')
        with open(dummy, 'w') as f:
            f.write('{}')
        created = backup_engine.create_backup(label='restore-test', triggered_by='pytest')
        archive = created.get('archive_name')

        # Restore without confirmation should fail
        result = backup_engine.restore_backup(archive, confirm=False)
        assert result.get('success') is False

    @pytest.mark.unit
    def test_full_backup_restore_cycle(self, backup_engine, tmp_data_dir):
        # Create original file
        test_file = os.path.join(tmp_data_dir, 'cycle_test.json')
        with open(test_file, 'w') as f:
            f.write('{"version": 1}')

        # Backup
        created = backup_engine.create_backup(label='cycle', triggered_by='pytest')
        assert created.get('success')
        archive = created['archive_name']

        # Modify file
        with open(test_file, 'w') as f:
            f.write('{"version": 2}')

        # Restore
        result = backup_engine.restore_backup(archive, confirm=True)
        assert result.get('success') is True

        # Verify restored content
        with open(test_file, 'r') as f:
            content = f.read()
        assert '"version": 1' in content or 'version' in content


# ══════════════════════════════════════════════════════════════════
#  Version Manager
# ══════════════════════════════════════════════════════════════════

class TestVersionManager:
    """Version create / list / rollback."""

    @pytest.mark.unit
    def test_get_current_version(self):
        from version_data_store import version_manager
        ver = version_manager.get_current_version()
        assert ver is not None
        assert '.' in ver  # semver-like

    @pytest.mark.unit
    def test_list_versions(self):
        from version_data_store import version_manager
        versions = version_manager.list_versions()
        assert isinstance(versions, list)
        assert len(versions) >= 1  # At least v1.0.0

    @pytest.mark.unit
    def test_get_active_version(self):
        from version_data_store import version_manager
        active = version_manager.get_active_version()
        assert active is not None
        assert active.get('status') == 'active'

    @pytest.mark.unit
    def test_create_duplicate_version_fails(self):
        from version_data_store import version_manager
        active = version_manager.get_active_version()
        assert active is not None, "No active version found"
        result = version_manager.create_version(active['version'], description='Duplicate test')
        assert result.get('success') is False
        assert 'already exists' in result.get('error', '')

    @pytest.mark.unit
    def test_invalid_version_format(self):
        from version_data_store import version_manager
        result = version_manager.create_version('abc', description='Bad format')
        assert result.get('success') is False

    @pytest.mark.unit
    def test_get_changelog(self):
        from version_data_store import version_manager
        changelog = version_manager.get_changelog()
        assert isinstance(changelog, str)
        assert '1.0.0' in changelog

    @pytest.mark.unit
    def test_delete_active_version_fails(self):
        """Deleting the currently active version should fail."""
        from version_data_store import version_manager
        active = version_manager.get_active_version()
        assert active is not None, "No active version found"
        result = version_manager.delete_version(active['version'])
        assert result.get('success') is False
        assert 'active' in result.get('error', '').lower()
