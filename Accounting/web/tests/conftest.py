"""
Shared pytest fixtures for the Ethiopian Accounting System test suite.

Migrated to FastAPI/Starlette `TestClient`. The legacy Flask-style helpers
(``client.session_transaction()``, ``app.config.update``) no longer apply;
this module exposes equivalent fixtures for the FastAPI app:

  - ``app``                FastAPI application (session-scoped)
  - ``client``             ``starlette.testclient.TestClient``
  - ``fresh_client``       per-test client (clean cookie jar)
  - ``logged_in_client``   client with a pre-signed session cookie (admin)
  - ``logged_in_hr``       client with a pre-signed session cookie (HR)
  - Data-store fixtures unchanged
"""
import os
import sys
import json
import time
import base64

import pytest

# ── Ensure web/ is on the import path ─────────────────────────────
WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # web/
PROJECT_ROOT = os.path.dirname(WEB_DIR)                                  # Accounting/
for p in (WEB_DIR, PROJECT_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

# Use a stable secret across the test session so we can sign session cookies
# the same way Starlette's SessionMiddleware does.
_TEST_SECRET = "test-secret-key-for-pytest"


# ══════════════════════════════════════════════════════════════════
#  FastAPI Application Fixture
# ══════════════════════════════════════════════════════════════════

@pytest.fixture(scope='session')
def app():
    """Create the FastAPI application once per test session."""
    os.environ.setdefault('FLASK_SECRET_KEY', _TEST_SECRET)
    os.environ.setdefault('DEFAULT_ADMIN_PASSWORD', 'admin123')
    os.environ.setdefault('DEFAULT_HR_PASSWORD', 'hr123')
    os.environ.setdefault('DEFAULT_ACCOUNTANT_PASSWORD', 'acc123')
    os.environ.setdefault('DEFAULT_EMPLOYEE_PASSWORD', 'emp123')
    os.environ.setdefault('DEFAULT_DATA_ENTRY_PASSWORD', 'data123')

    original_cwd = os.getcwd()
    os.chdir(WEB_DIR)
    from app import app as fastapi_app
    yield fastapi_app
    os.chdir(original_cwd)


def _make_session_cookie(payload: dict) -> str:
    """Replicate Starlette ``SessionMiddleware`` cookie format.

    Starlette signs ``base64(json(payload))`` with ``itsdangerous.TimestampSigner``
    using the same SECRET_KEY used by the app. Reproducing this lets us inject
    a pre-authenticated session without going through the login flow.
    """
    import itsdangerous
    signer = itsdangerous.TimestampSigner(str(_TEST_SECRET))
    data = base64.b64encode(json.dumps(payload).encode("utf-8"))
    return signer.sign(data).decode("utf-8")


def _client_with_session(app, session_payload: dict):
    """Return a TestClient with a signed ``session`` cookie pre-set."""
    from starlette.testclient import TestClient
    c = TestClient(app)
    c.cookies.set("session", _make_session_cookie(session_payload))
    return c


@pytest.fixture(scope='session')
def client(app):
    """Anonymous TestClient — reused across the session for speed."""
    from starlette.testclient import TestClient
    return TestClient(app)


@pytest.fixture()
def fresh_client(app):
    """Per-test TestClient (clean cookie jar)."""
    from starlette.testclient import TestClient
    return TestClient(app)


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
    return _client_with_session(app, {
        'logged_in': True,
        'user_id': 'admin',
        'username': 'admin',
        'full_name': 'Admin User',
        'role': 'admin',
        'privilege_level': 'admin',
        'current_company_id': 'default',
        'login_time': int(time.time()),
    })


@pytest.fixture()
def logged_in_hr(app):
    """A test client logged in as HR manager."""
    return _client_with_session(app, {
        'logged_in': True,
        'user_id': 'hr_manager',
        'username': 'hr_manager',
        'full_name': 'HR Manager',
        'role': 'hr',
        'privilege_level': 'operator',
        'current_company_id': 'default',
        'login_time': int(time.time()),
    })


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
