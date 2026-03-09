"""
Shared pytest fixtures for the Ethiopian Accounting System test suite.

Provides:
  - Flask test client with CSRF disabled
  - Isolated temporary data directories for data stores
  - Pre-configured data store instances
  - Helper functions for login simulation
"""
import os
import sys
import shutil
import tempfile

import pytest

# ── Ensure web/ is on the import path ─────────────────────────────
WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # web/
PROJECT_ROOT = os.path.dirname(WEB_DIR)                                  # Accounting/
for p in (WEB_DIR, PROJECT_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)


# ══════════════════════════════════════════════════════════════════
#  Flask Application Fixture
# ══════════════════════════════════════════════════════════════════

@pytest.fixture(scope='session')
def app():
    """Create the Flask application once per test session."""
    # Set required env vars BEFORE importing app
    os.environ.setdefault('FLASK_SECRET_KEY', 'test-secret-key-for-pytest')
    os.environ.setdefault('DEFAULT_ADMIN_PASSWORD', 'admin123')
    os.environ.setdefault('DEFAULT_HR_PASSWORD', 'hr123')
    os.environ.setdefault('DEFAULT_ACCOUNTANT_PASSWORD', 'acc123')
    os.environ.setdefault('DEFAULT_EMPLOYEE_PASSWORD', 'emp123')
    os.environ.setdefault('DEFAULT_DATA_ENTRY_PASSWORD', 'data123')

    # Ensure cwd is web/ so relative imports in routes resolve
    original_cwd = os.getcwd()
    os.chdir(WEB_DIR)

    from app import app as flask_app

    flask_app.config.update({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,          # Disable CSRF for tests
        'SERVER_NAME': 'localhost:5000',
    })

    yield flask_app

    os.chdir(original_cwd)


@pytest.fixture(scope='session')
def client(app):
    """Flask test client — reused across the session for speed."""
    return app.test_client()


@pytest.fixture()
def fresh_client(app):
    """Per-test Flask test client (clean session state)."""
    return app.test_client()


# ══════════════════════════════════════════════════════════════════
#  Temporary Data Directory (isolated per test)
# ══════════════════════════════════════════════════════════════════

@pytest.fixture()
def tmp_data_dir(tmp_path):
    """
    Provide a clean temporary directory for data stores.
    Avoids modifying production data/. Cleaned up automatically.
    """
    data_dir = str(tmp_path / 'data')
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


# ══════════════════════════════════════════════════════════════════
#  Auth Helpers
# ══════════════════════════════════════════════════════════════════

@pytest.fixture()
def logged_in_client(app):
    """A test client already logged in as admin."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_id'] = 'admin'
        sess['username'] = 'admin'
        sess['full_name'] = 'Admin User'
        sess['role'] = 'admin'
        sess['privilege_level'] = 'admin'
    return client


@pytest.fixture()
def logged_in_hr(app):
    """A test client logged in as HR manager."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_id'] = 'hr_manager'
        sess['username'] = 'hr_manager'
        sess['full_name'] = 'HR Manager'
        sess['role'] = 'hr'
        sess['privilege_level'] = 'operator'
    return client


# ══════════════════════════════════════════════════════════════════
#  Data Store Fixtures (isolated)
# ══════════════════════════════════════════════════════════════════

@pytest.fixture()
def income_expense_store(tmp_data_dir):
    from income_expense_data_store import IncomeExpenseDataStore
    return IncomeExpenseDataStore(data_dir=tmp_data_dir)


@pytest.fixture()
def transaction_store(tmp_data_dir):
    from transaction_data_store import TransactionDataStore
    return TransactionDataStore(data_dir=tmp_data_dir)


@pytest.fixture()
def cpo_store(tmp_data_dir):
    from cpo_data_store import CPODataStore
    return CPODataStore(data_dir=tmp_data_dir)


@pytest.fixture()
def inventory_store(tmp_data_dir):
    from inventory_data_store import InventoryDataStore
    return InventoryDataStore(data_dir=tmp_data_dir)


@pytest.fixture()
def bid_store(tmp_data_dir):
    from bid_data_store import BidDataStore
    return BidDataStore(data_dir=tmp_data_dir)


@pytest.fixture()
def backup_engine(tmp_data_dir):
    from backup_data_store import BackupEngine
    backup_dir = os.path.join(tmp_data_dir, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    return BackupEngine(data_dir=tmp_data_dir, backup_dir=backup_dir)
