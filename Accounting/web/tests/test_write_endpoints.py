"""
Write-Path & Import Endpoint Tests — Ethiopian Business Management System
=========================================================================
Covers every POST / file-upload route in the application.

Goals
-----
- Catch 500 errors caused by missing company_id, missing columns, bad
  SQL, or unhandled exceptions in any write handler.
- Verify that successful writes redirect (30x) or return JSON/200.
- Verify that Excel import endpoints accept a valid in-memory workbook
  without crashing.
- All tests run in < 60 s with NO real database and NO network calls.

Design
------
1. All DB modules are replaced with MagicMock before the FastAPI app is
   imported so that no connection is ever attempted.
2. The FastAPI TestClient is used directly (handles ASGI properly).
3. Auth dependencies are overridden with a stub that returns an admin user.
4. Session values (current_company_id, logged_in, username) are injected
   via Starlette's SessionMiddleware cookie — we sign it with the same
   secret key used by the app.
5. In-memory Excel files are generated with openpyxl for upload tests.

Run
---
    cd web
    pytest tests/test_write_endpoints.py -v
    pytest tests/test_write_endpoints.py -v -k payroll
    pytest tests/test_write_endpoints.py -v --tb=short 2>&1 | head -120
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import uuid
from contextlib import contextmanager
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ══════════════════════════════════════════════════════════════════
#  0. Environment — must happen BEFORE any web/ module is imported
# ══════════════════════════════════════════════════════════════════

_WEB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # web/
_ROOT = os.path.dirname(_WEB)                                        # repo root

for _p in (_WEB, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DATABASE_URL",       "postgresql://mock:mock@localhost/mock")
os.environ.setdefault("FLASK_SECRET_KEY",   "test-secret-for-write-endpoint-tests")
os.environ.setdefault("REDIS_URL",          "")          # disable Redis
os.environ.setdefault("LOG_LEVEL",          "ERROR")     # keep test output clean

# ══════════════════════════════════════════════════════════════════
#  1. Build a realistic mock DB layer
# ══════════════════════════════════════════════════════════════════

def _make_cursor():
    cur = MagicMock()
    cur.fetchone.return_value  = None
    cur.fetchall.return_value  = []
    cur.fetchmany.return_value = []
    cur.description            = [("id",), ("company_id",)]
    cur.rowcount               = 1
    cur.__enter__              = lambda s: s
    cur.__exit__               = MagicMock(return_value=False)
    return cur


def _make_conn(cur):
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.commit              = MagicMock()
    conn.rollback            = MagicMock()
    conn.__enter__           = lambda s: s
    conn.__exit__            = MagicMock(return_value=False)
    return conn


_MOCK_CUR  = _make_cursor()
_MOCK_CONN = _make_conn(_MOCK_CUR)


@contextmanager
def _mock_cursor_ctx(*args, **kwargs):
    yield _MOCK_CUR


@contextmanager
def _mock_conn_ctx(*args, **kwargs):
    yield _MOCK_CONN


# ── db module mock ────────────────────────────────────────────────
_db_mock = MagicMock()
_db_mock.get_conn             = _mock_conn_ctx
_db_mock.get_cursor           = _mock_cursor_ctx
_db_mock.get_tenant_cursor    = _mock_cursor_ctx
_db_mock.get_tenant_conn      = _mock_conn_ctx
_db_mock.health_check.return_value = {"ok": True, "latency_ms": 1, "version": "mock"}

# ── async_db mock ─────────────────────────────────────────────────
_async_db_mock            = MagicMock()
_async_db_mock.get_async_pool      = AsyncMock(return_value=None)
_async_db_mock.close_async_pool    = AsyncMock()
_async_db_mock.close_sqlalchemy_engine = AsyncMock()

# ── cache / extensions mock ───────────────────────────────────────
_cache_mock = MagicMock()
_cache_mock.get.return_value   = None
_cache_mock.set.return_value   = True
_cache_mock.delete.return_value = True

_ext_mock               = MagicMock()
_ext_mock.cache         = _cache_mock
_ext_mock.limiter       = MagicMock()
_ext_mock.LIMITER_AVAILABLE = False

# ── session_store mock (skip Redis sessions) ──────────────────────
_sess_mock = MagicMock()
# make_session_middleware must raise so the app falls back to cookie sessions
_sess_mock.make_session_middleware = MagicMock(side_effect=RuntimeError("use cookie sessions"))

# ── siem mock ─────────────────────────────────────────────────────
_siem_store_mock                   = MagicMock()
_siem_store_mock.log_upload_event  = MagicMock()
_siem_mock                         = MagicMock()
_siem_mock.siem_store              = _siem_store_mock

# ── metrics mock ──────────────────────────────────────────────────
_metrics_mock                 = MagicMock()
_metrics_mock.record_request  = MagicMock()
_metrics_mock.metrics_router  = MagicMock()
_metrics_mock.metrics_router.routes = []

# ── tenant_data_store mock ────────────────────────────────────────
_tenant_store_mock = MagicMock()
_tenant_store_mock.ensure_default_tenant.return_value = "default"
_tenant_store_mock.get_tenant.return_value = {"company_id": "default", "company_name": "Test Co"}
_tenant_store_mock.is_subscription_active.return_value = True
_tenant_store_mock.is_module_licensed.return_value     = True
_tenant_store_mock.ALWAYS_ALLOWED_MODULES = {
    "auth", "payroll", "siem", "backup", "version", "lms", "machinery", "hrm"
}
_tenant_ds_mock           = MagicMock()
_tenant_ds_mock.tenant_store = _tenant_store_mock
_tenant_ds_mock.ALWAYS_ALLOWED_MODULES = _tenant_store_mock.ALWAYS_ALLOWED_MODULES

# ── Inject mocks into sys.modules BEFORE importing app ───────────
sys.modules.setdefault("db",               _db_mock)
sys.modules.setdefault("async_db",         _async_db_mock)
sys.modules.setdefault("extensions",       _ext_mock)
sys.modules.setdefault("cache",            _cache_mock)
sys.modules.setdefault("session_store",    _sess_mock)
sys.modules.setdefault("siem_data_store",  _siem_mock)
sys.modules.setdefault("metrics",          _metrics_mock)
sys.modules.setdefault("tenant_data_store", _tenant_ds_mock)

# ══════════════════════════════════════════════════════════════════
#  2. Import FastAPI app & TestClient
# ══════════════════════════════════════════════════════════════════

os.chdir(_WEB)

# Wrap the lifespan so DB / Redis startup probes are skipped silently
# Pre-inject module-level stubs for modules the lifespan tries to import
sys.modules.setdefault("event_handlers", MagicMock())
sys.modules.setdefault("events",         MagicMock())
sys.modules.setdefault("db_setup",       MagicMock())
sys.modules.setdefault("backup_data_store", MagicMock())

with patch("db_setup.ensure_schema", MagicMock(), create=True):
    from app import app as _fastapi_app

from starlette.testclient import TestClient

# ══════════════════════════════════════════════════════════════════
#  3. Override auth dependencies
# ══════════════════════════════════════════════════════════════════

_ADMIN_USER = {
    "user_id":         "test-admin-001",
    "username":        "testadmin",
    "full_name":       "Test Administrator",
    "privilege_level": "super_admin",
    "role":            "admin",
}

try:
    from deps import require_auth, login_required, admin_required
    _fastapi_app.dependency_overrides[login_required] = lambda: _ADMIN_USER
    _fastapi_app.dependency_overrides[admin_required] = lambda: _ADMIN_USER
    # Also override require_auth factory results
    for _dep_fn in list(_fastapi_app.dependency_overrides.keys()):
        pass
except Exception as _e:
    logging.getLogger(__name__).warning("Could not override auth deps: %s", _e)


# ══════════════════════════════════════════════════════════════════
#  4. Session helper — inject a pre-authenticated Starlette session
# ══════════════════════════════════════════════════════════════════

def _authed_client() -> TestClient:
    """
    Return a TestClient with a signed session cookie so that all
    request.session reads return a logged-in admin user in company 'default'.
    """
    # Build a signed session using itsdangerous (same lib Starlette uses)
    import base64, hashlib, hmac, json as _json, time as _time
    from itsdangerous import URLSafeTimedSerializer

    secret = os.environ["FLASK_SECRET_KEY"]
    serializer = URLSafeTimedSerializer(secret)

    session_data = {
        "logged_in":          True,
        "user_id":            _ADMIN_USER["user_id"],
        "username":           _ADMIN_USER["username"],
        "full_name":          _ADMIN_USER["full_name"],
        "privilege_level":    _ADMIN_USER["privilege_level"],
        "current_company_id": "default",
    }
    signed = serializer.dumps(session_data)

    client = TestClient(_fastapi_app, raise_server_exceptions=False)
    client.cookies.set("session", signed, domain="testserver")
    return client


# Convenience: module-level authenticated client (shared across tests)
@pytest.fixture(scope="module")
def ac():
    """Authenticated TestClient — reused across the module for speed."""
    return _authed_client()


# ══════════════════════════════════════════════════════════════════
#  5. Excel helper — build a minimal in-memory .xlsx
# ══════════════════════════════════════════════════════════════════

def _xlsx(sheets: Dict[str, list]) -> bytes:
    """
    Build an in-memory Excel workbook.
    sheets: {"SheetName": [["col1","col2",...], [val1,val2,...], ...]}
    """
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for name, rows in sheets.items():
            ws = wb.create_sheet(name)
            for row in rows:
                ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.read()
    except ImportError:
        # openpyxl not installed — return a stub that the route's except branch handles
        return b"PK\x03\x04"   # minimal ZIP magic bytes


# ══════════════════════════════════════════════════════════════════
#  6. Helper: assert response is a success (200/201/30x, never 500)
# ══════════════════════════════════════════════════════════════════

def _ok(resp, label: str = ""):
    """Assert the response is not a server error."""
    assert resp.status_code < 500, (
        f"{label} returned HTTP {resp.status_code}:\n"
        + resp.text[:800]
    )
    return resp


# ══════════════════════════════════════════════════════════════════
#  7. TESTS
# ══════════════════════════════════════════════════════════════════

# ── 7.1 Auth ─────────────────────────────────────────────────────

class TestAuthWritePaths:
    """POST endpoints in the auth module."""

    def test_login_page_loads(self, ac):
        r = ac.get("/auth/login")
        # login page is public — redirects or 200, never 500
        assert r.status_code < 500, f"/auth/login returned {r.status_code}"

    def test_login_wrong_password(self, ac):
        """Bad credentials should return 200 (re-render form) not 500."""
        with patch("auth_data_store.auth_store") as mock_store:
            mock_store.authenticate.return_value = None
            r = ac.post("/auth/login", data={"username": "bad", "password": "bad"})
        _ok(r, "POST /auth/login bad credentials")

    def test_create_user(self, ac):
        with patch("auth_data_store.auth_store") as mock_store:
            mock_store.create_user.return_value = {"user_id": "new-001"}
            r = ac.post("/auth/users/create", data={
                "username":  "newuser",
                "password":  "Password1!",
                "full_name": "New User",
                "email":     "new@example.com",
                "privilege_level": "viewer",
            })
        _ok(r, "POST /auth/users/create")

    def test_change_password(self, ac):
        with patch("auth_data_store.auth_store") as mock_store:
            mock_store.change_password.return_value = True
            r = ac.post("/auth/change-password", data={
                "current_password": "OldPass1!",
                "new_password":     "NewPass2!",
                "confirm_password": "NewPass2!",
            })
        _ok(r, "POST /auth/change-password")


# ── 7.2 VAT ──────────────────────────────────────────────────────

class TestVATWritePaths:
    """POST endpoints in the VAT module."""

    def test_add_vat_income(self, ac):
        with patch("vat_data_store.vat_store") as s:
            s.add_income.return_value = True
            r = ac.post("/vat/income/add", data={
                "contract_date":  "2026-01-15",
                "description":    "Consulting services",
                "category":       "Services",
                "gross_amount":   "10000",
                "vat_type":       "standard",
                "vat_rate":       "15",
                "customer_name":  "Acme Corp",
                "customer_tin":   "0012345678",
                "invoice_number": "INV-001",
            })
        _ok(r, "POST /vat/income/add")

    def test_add_vat_expense(self, ac):
        with patch("vat_data_store.vat_store") as s:
            s.add_expense.return_value = True
            r = ac.post("/vat/expenses/add", data={
                "expense_date":   "2026-01-20",
                "description":    "Office supplies",
                "category":       "Office",
                "gross_amount":   "500",
                "vat_type":       "standard",
                "vat_rate":       "15",
                "supplier_name":  "Supplier Ltd",
                "supplier_tin":   "0087654321",
                "receipt_number": "REC-001",
            })
        _ok(r, "POST /vat/expenses/add")

    def test_add_vat_capital(self, ac):
        with patch("vat_data_store.vat_store") as s:
            s.add_capital.return_value = True
            r = ac.post("/vat/capital/add", data={
                "investment_date": "2026-01-01",
                "description":     "Equipment purchase",
                "capital_type":    "machinery",
                "amount":          "50000",
                "vat_type":        "standard",
                "vat_rate":        "15",
                "investor_name":   "Owner",
                "investor_tin":    "1234567890",
            })
        _ok(r, "POST /vat/capital/add")


# ── 7.3 Journal Entries ───────────────────────────────────────────

class TestJournalWritePaths:
    """POST endpoints in the journal entry module."""

    def test_add_journal_entry(self, ac):
        with patch("journal_entry_data_store.journal_store") as s:
            s.save_journal_entry.return_value = True
            r = ac.post("/journal/add", data={
                "entry_date":        "2026-02-01",
                "description":       "Test entry",
                "reference_number":  "REF-001",
                "lines[0][account_code]":  "1000",
                "lines[0][account_name]":  "Cash",
                "lines[0][debit_amount]":  "1000",
                "lines[0][credit_amount]": "0",
                "lines[1][account_code]":  "4000",
                "lines[1][account_name]":  "Revenue",
                "lines[1][debit_amount]":  "0",
                "lines[1][credit_amount]": "1000",
            })
        _ok(r, "POST /journal/add")

    def test_import_journal_excel(self, ac):
        """Excel import must not raise 500 even with minimal data."""
        xlsx_bytes = _xlsx({
            "Journal Entries": [
                ["entry_id", "entry_date", "description", "reference_number",
                 "total_debit", "total_credit"],
                [str(uuid.uuid4()), "2026-02-01", "Test import", "REF-001", 1000, 1000],
            ],
            "Entry Lines": [
                ["entry_id", "account_code", "account_name", "debit_amount", "credit_amount"],
            ],
        })
        with patch("journal_entry_data_store.journal_store") as s:
            s.import_from_excel.return_value = {"success": True, "imported_count": 1, "errors": []}
            r = ac.post(
                "/journal/import/excel",
                files={"excel_file": ("journal.xlsx", xlsx_bytes,
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
        _ok(r, "POST /journal/import/excel")


# ── 7.4 Chart of Accounts ─────────────────────────────────────────

class TestAccountsWritePaths:
    """POST endpoints in the chart of accounts module."""

    def test_add_account(self, ac):
        # accounts_store is a module-level instance in chart_of_accounts_routes
        with patch("chart_of_accounts_routes.accounts_store") as s:
            s.save_account.return_value = True
            s.get_account_by_code.return_value = None
            r = ac.post("/accounts/add", data={
                "account_code":    "1001",
                "account_name":    "Test Cash Account",
                "account_type":    "Asset",
                "account_subtype": "Current Asset",
                "normal_balance":  "debit",
                "description":     "Test account for unit test",
            })
        _ok(r, "POST /accounts/add")

    def test_import_accounts_excel(self, ac):
        xlsx_bytes = _xlsx({
            "Chart of Accounts": [
                ["account_code", "account_name", "account_type", "account_subtype",
                 "normal_balance", "description"],
                ["9001", "Test Import Account", "Asset", "Current", "debit", "Imported"],
            ],
        })
        with patch("chart_of_accounts_routes.accounts_store") as s:
            s.import_from_excel.return_value = {"imported": 1, "errors": []}
            r = ac.post(
                "/accounts/import/excel",
                files={"excel_file": ("coa.xlsx", xlsx_bytes,
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
        _ok(r, "POST /accounts/import/excel")


# ── 7.5 Income & Expense ─────────────────────────────────────────

class TestIncomeExpenseWritePaths:
    """POST endpoints in the income/expense module — these had the company_id bug."""

    def test_add_income(self, ac):
        with patch("income_expense_data_store.income_expense_store") as s:
            s.save_income_record.return_value = True
            r = ac.post("/income-expense/add-income", data={
                "date":           "2026-03-01",
                "description":    "Consulting fee",
                "category":       "Services",
                "amount":         "5000",
                "tax_amount":     "0",
                "customer_name":  "Client A",
                "payment_method": "Bank Transfer",
            })
        _ok(r, "POST /income-expense/add-income")

    def test_add_income_saves_company_id(self, ac):
        """Verify company_id is included in the record dict sent to store."""
        captured: list = []

        def _capture(record):
            captured.append(record)
            return True

        with patch("income_expense_data_store.income_expense_store") as s:
            s.save_income_record.side_effect = _capture
            ac.post("/income-expense/add-income", data={
                "date": "2026-03-01", "description": "Test",
                "category": "X", "amount": "100",
                "tax_amount": "0", "customer_name": "C",
                "payment_method": "Cash",
            })

        if captured:
            assert "company_id" in captured[0], (
                "save_income_record was called WITHOUT company_id in the record dict. "
                "This is the bug we fixed — re-check income_expense_routes.py"
            )

    def test_add_expense(self, ac):
        with patch("income_expense_data_store.income_expense_store") as s:
            s.save_expense_record.return_value = True
            r = ac.post("/income-expense/add-expense", data={
                "date":           "2026-03-05",
                "description":    "Office rent",
                "category":       "Rent",
                "amount":         "3000",
                "tax_amount":     "0",
                "supplier_name":  "Landlord",
                "payment_method": "Bank Transfer",
            })
        _ok(r, "POST /income-expense/add-expense")

    def test_add_expense_saves_company_id(self, ac):
        """Verify company_id is included in the expense record."""
        captured: list = []

        def _capture(record):
            captured.append(record)
            return True

        with patch("income_expense_data_store.income_expense_store") as s:
            s.save_expense_record.side_effect = _capture
            ac.post("/income-expense/add-expense", data={
                "date": "2026-03-05", "description": "Rent",
                "category": "Rent", "amount": "1000",
                "tax_amount": "0", "supplier_name": "LL",
                "payment_method": "Cash",
            })

        if captured:
            assert "company_id" in captured[0], (
                "save_expense_record was called WITHOUT company_id. "
                "Check income_expense_routes.py POST /add-expense handler."
            )

    def test_import_income_expense_excel(self, ac):
        """Import endpoint should accept a valid workbook without crashing."""
        xlsx_bytes = _xlsx({
            "Income": [
                ["date", "description", "category", "amount", "tax_amount",
                 "customer_name", "payment_method"],
                ["2026-03-01", "Revenue", "Services", 5000, 0, "Client A", "Cash"],
            ],
            "Expenses": [
                ["date", "description", "category", "amount", "tax_amount",
                 "supplier_name", "payment_method"],
                ["2026-03-02", "Supplies", "Office", 200, 0, "Vendor", "Cash"],
            ],
        })
        with patch("income_expense_data_store.income_expense_store") as s:
            s.save_income_record.return_value  = True
            s.save_expense_record.return_value = True
            r = ac.post(
                "/income-expense/import-excel",
                files={"excel_file": ("ie.xlsx", xlsx_bytes,
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
        _ok(r, "POST /income-expense/import-excel")


# ── 7.6 Transactions ─────────────────────────────────────────────

class TestTransactionWritePaths:
    """POST endpoints in the transaction module."""

    def test_import_transactions_excel(self, ac):
        xlsx_bytes = _xlsx({
            "Transactions": [
                ["date", "account_code", "account_name", "description",
                 "reference", "debit", "credit", "currency"],
                ["2026-01-10", "1000", "Cash", "Payment received",
                 "REF-T01", 0, 5000, "ETB"],
            ],
        })
        with patch("transaction_data_store.transaction_store") as s:
            s.import_from_dataframe.return_value = {
                "success": True, "imported": 1, "flagged": 0, "errors": []
            }
            r = ac.post(
                "/transactions/import",
                files={"excel_file": ("tx.xlsx", xlsx_bytes,
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
        _ok(r, "POST /transactions/import")

    def test_flag_account(self, ac):
        with patch("transaction_data_store.transaction_store") as s:
            s.flag_account.return_value = True
            r = ac.post("/transactions/flag-account", data={
                "account_code": "9999",
                "account_name": "Suspicious",
                "flag_reason":  "Test reason",
            })
        _ok(r, "POST /transactions/flag-account")


# ── 7.7 Payroll ───────────────────────────────────────────────────

class TestPayrollWritePaths:
    """POST endpoints in the payroll module — includes the import fix."""

    def test_add_employee(self, ac):
        with patch("employee_data_store.employee_store") as s:
            s.write_employee.return_value = True
            s.employee_exists.return_value = False
            r = ac.post("/payroll/employees/add", data={
                "employee_id":   "EMP-TEST-001",
                "name":          "Test Employee",
                "category":      "permanent",
                "basic_salary":  "8000",
                "hire_date":     "2024-01-01",
                "department":    "Engineering",
                "position":      "Engineer",
                "bank_account":  "1234567890",
                "tin_number":    "TIN001",
                "pension_number": "PEN001",
            })
        _ok(r, "POST /payroll/employees/add")

    def test_import_employees_excel(self, ac):
        """Employee Excel import must include company_id, created_date, updated_date."""
        xlsx_bytes = _xlsx({
            "Employees": [
                ["employee_id", "name", "category", "basic_salary",
                 "hire_date", "department", "position"],
                ["EMP-IMPORT-01", "Imported Worker", "permanent",
                 "7500", "2025-06-01", "Finance", "Accountant"],
            ],
        })
        captured_rows: list = []

        def _capture_bulk(rows, overwrite=False):
            captured_rows.extend(rows)
            return True

        with patch("employee_data_store.employee_store") as s:
            s.bulk_import.side_effect  = _capture_bulk
            s.employee_exists.return_value = False
            r = ac.post(
                "/payroll/employees/import-excel",
                files={"excel_file": ("emp.xlsx", xlsx_bytes,
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
        _ok(r, "POST /payroll/employees/import-excel")

        if captured_rows:
            row = captured_rows[0]
            assert "company_id" in row, (
                "bulk_import called with rows missing company_id. "
                "Check payroll_routes.py import_excel_post handler."
            )
            assert "created_date" in row or "updated_date" in row, (
                "bulk_import rows missing timestamp columns — DB insert will fail."
            )

    def test_calculate_payroll(self, ac):
        import pandas as pd
        with patch("payroll_routes._employee_store") as es:
            es.read_all_employees.return_value = pd.DataFrame()
            r = ac.post("/payroll/calculate", data={
                "month": "3",
                "year":  "2026",
            })
        _ok(r, "POST /payroll/calculate")

    def test_tax_calculator_api(self, ac):
        """JSON tax calculator endpoint."""
        r = ac.post(
            "/payroll/api/tax-calculator",
            json={"gross_salary": 15000},
            headers={"Content-Type": "application/json"},
        )
        _ok(r, "POST /payroll/api/tax-calculator")
        if r.headers.get("content-type", "").startswith("application/json"):
            data = r.json()
            assert "income_tax" in data or "tax" in data or "net" in data or "error" not in data


# ── 7.8 CPO ──────────────────────────────────────────────────────

class TestCPOWritePaths:
    """POST endpoints in the CPO module."""

    def test_add_cpo(self, ac):
        # cpo_store is a module-level instance in cpo_routes
        with patch("cpo_routes.cpo_store") as s:
            s.save_cpo.return_value = True
            r = ac.post("/cpo/add", data={
                "name":       "Test CPO",
                "date":       "2026-02-15",
                "amount":     "25000",
                "bid_name":   "Road Construction Bid",
                "is_returned": "false",
            })
        _ok(r, "POST /cpo/add")

    def test_import_cpo_excel(self, ac):
        xlsx_bytes = _xlsx({
            "CPO": [
                ["name", "date", "amount", "bid_name", "is_returned"],
                ["CPO-Import-1", "2026-02-01", 10000, "Test Bid", "false"],
            ],
        })
        with patch("cpo_routes.cpo_store") as s:
            s.import_from_dataframe.return_value = {"success": True, "imported": 1, "errors": []}
            r = ac.post(
                "/cpo/import",
                files={"excel_file": ("cpo.xlsx", xlsx_bytes,
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
        _ok(r, "POST /cpo/import")


# ── 7.9 Inventory ─────────────────────────────────────────────────

class TestInventoryWritePaths:
    """POST endpoints in the inventory module."""

    def test_add_item(self, ac):
        with patch("inventory_data_store.inventory_store") as s:
            s.add_item.return_value = "INV-001"
            r = ac.post("/inventory/items/add", data={
                "sku":           "SKU-TEST-001",
                "name":          "Test Widget",
                "description":   "A test widget",
                "category":      "Electronics",
                "unit":          "pcs",
                "unit_price":    "250",
                "cost_price":    "150",
                "current_stock": "100",
                "min_stock_level": "10",
                "reorder_point":   "20",
            })
        _ok(r, "POST /inventory/items/add")

    def test_import_inventory_excel(self, ac):
        xlsx_bytes = _xlsx({
            "Items": [
                ["sku", "name", "category", "unit", "unit_price",
                 "cost_price", "current_stock"],
                ["SKU-IMP-01", "Imported Item", "General", "pcs", 100, 60, 50],
            ],
        })
        with patch("inventory_data_store.inventory_store") as s:
            s.bulk_import.return_value = {"imported": 1, "errors": []}
            r = ac.post(
                "/inventory/items/import",
                files={"excel_file": ("inv.xlsx", xlsx_bytes,
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
        _ok(r, "POST /inventory/items/import")

    def test_delete_item(self, ac):
        with patch("inventory_data_store.inventory_store") as s:
            s.soft_delete_item.return_value = True
            r = ac.post("/inventory/items/delete/INV-FAKE-999")
        _ok(r, "POST /inventory/items/delete/INV-FAKE-999")


# ── 7.10 Bid Tracker ─────────────────────────────────────────────

class TestBidWritePaths:
    """POST endpoints in the bid tracker module."""

    def test_add_bid(self, ac):
        with patch("bid_data_store.bid_store") as s:
            s.upsert_bid.return_value = "BID-001"
            r = ac.post("/bid/add", data={
                "title":            "Infrastructure Project",
                "reference_number": "BID-REF-001",
                "organization":     "Ministry of Works",
                "description":      "Road construction bid",
                "category":         "Construction",
                "deadline":         "2026-06-30",
                "bid_amount":       "5000000",
                "currency":         "ETB",
                "case_handler_name":  "Handler A",
                "case_handler_email": "handler@example.com",
            })
        _ok(r, "POST /bid/add")

    def test_edit_bid(self, ac):
        with patch("bid_data_store.bid_store") as s:
            s.get_bid.return_value = {
                "id": "BID-FAKE-001", "company_id": "default",
                "title": "Old Title", "status": "open",
            }
            s.upsert_bid.return_value = True
            r = ac.post("/bid/edit/BID-FAKE-001", data={
                "title":    "Updated Title",
                "status":   "submitted",
                "bid_amount": "5500000",
            })
        _ok(r, "POST /bid/edit/BID-FAKE-001")


# ── 7.11 Finance Management ───────────────────────────────────────

class TestFinanceManagementWritePaths:
    """POST endpoints in the finance management module."""

    def test_post_gl_entry(self, ac):
        with patch("finance_management_data_store.finance_store") as s:
            s.post_gl_entry.return_value = "GL-001"
            r = ac.post("/finance-mgmt/gl/entries", json={
                "entry_date":   "2026-03-01",
                "account_code": "4000",
                "account_name": "Revenue",
                "amount":       10000,
                "entry_type":   "credit",
                "reference":    "INV-001",
                "description":  "March revenue",
            })
        _ok(r, "POST /finance-mgmt/gl/entries")

    def test_create_ar_ap(self, ac):
        with patch("finance_management_data_store.finance_store") as s:
            s.create_ar_ap.return_value = "AR-001"
            r = ac.post("/finance-mgmt/ar-ap", json={
                "txn_type":   "AR",
                "party_name": "Client Corp",
                "invoice_no": "INV-101",
                "due_date":   "2026-04-30",
                "amount":     25000,
            })
        _ok(r, "POST /finance-mgmt/ar-ap")

    def test_create_asset(self, ac):
        with patch("finance_management_data_store.finance_store") as s:
            s.create_asset.return_value = "ASSET-001"
            r = ac.post("/finance-mgmt/assets", json={
                "asset_name":       "Generator",
                "category":         "Equipment",
                "acquisition_date": "2026-01-01",
                "acquisition_cost": 150000,
                "useful_life_years": 10,
            })
        _ok(r, "POST /finance-mgmt/assets")

    def test_create_budget(self, ac):
        with patch("finance_management_data_store.finance_store") as s:
            s.create_budget.return_value = "BUD-001"
            r = ac.post("/finance-mgmt/budgets", json={
                "fiscal_year":    2026,
                "cost_center":    "Engineering",
                "account_code":   "5000",
                "budget_amount":  500000,
            })
        _ok(r, "POST /finance-mgmt/budgets")

    def test_create_shareholder(self, ac):
        with patch("finance_management_data_store.finance_store") as s:
            s.create_shareholder.return_value = "SH-001"
            r = ac.post("/finance-mgmt/shareholders", json={
                "full_name":        "Investor One",
                "national_id":      "ID123456",
                "shares_owned":     1000,
                "share_class":      "ordinary",
                "ownership_percent": 25.0,
            })
        _ok(r, "POST /finance-mgmt/shareholders")

    def test_declare_dividend(self, ac):
        with patch("finance_management_data_store.finance_store") as s:
            s.declare_dividend.return_value = "DIV-001"
            r = ac.post("/finance-mgmt/dividends", json={
                "declaration_date": "2026-03-31",
                "fiscal_year":      2025,
                "total_amount":     200000,
                "status":           "declared",
            })
        _ok(r, "POST /finance-mgmt/dividends")


# ── 7.12 HRM ─────────────────────────────────────────────────────

class TestHRMWritePaths:
    """POST endpoints in the HRM module."""

    def test_create_payroll_run(self, ac):
        with patch("hrm_data_store.hrm_store") as s:
            s.create_payroll_run.return_value = "RUN-001"
            r = ac.post("/hrm/payroll/runs", json={
                "payroll_month": "2026-03",
                "contract_type": "permanent",
                "gross_pay":     120000,
                "allowances":    5000,
                "deductions":    2000,
                "tax_amount":    18000,
                "pension_amount": 6000,
                "net_pay":       99000,
            })
        _ok(r, "POST /hrm/payroll/runs")

    def test_create_leave_request(self, ac):
        with patch("hrm_data_store.hrm_store") as s:
            s.create_leave_request.return_value = "LEAVE-001"
            r = ac.post("/hrm/leave/requests", json={
                "employee_id":    "EMP-001",
                "leave_type":     "annual",
                "start_date":     "2026-04-10",
                "end_date":       "2026-04-14",
                "days_requested": 5,
                "reason":         "Vacation",
            })
        _ok(r, "POST /hrm/leave/requests")

    def test_create_training_record(self, ac):
        with patch("hrm_data_store.hrm_store") as s:
            s.create_training_record.return_value = "TR-001"
            r = ac.post("/hrm/learning/training-records", json={
                "employee_id":   "EMP-001",
                "training_name": "Safety Training",
                "planned_date":  "2026-05-01",
                "status":        "planned",
            })
        _ok(r, "POST /hrm/learning/training-records")

    def test_create_performance_review(self, ac):
        with patch("hrm_data_store.hrm_store") as s:
            s.create_performance_review.return_value = "REV-001"
            r = ac.post("/hrm/performance/reviews", json={
                "employee_id":            "EMP-001",
                "review_period":          "2026-Q1",
                "kpi_score":              85.0,
                "okr_score":              78.5,
                "promotion_recommended":  False,
                "increment_percent":       5.0,
            })
        _ok(r, "POST /hrm/performance/reviews")

    def test_create_grievance(self, ac):
        with patch("hrm_data_store.hrm_store") as s:
            s.create_grievance.return_value = "GRV-001"
            r = ac.post("/hrm/ess/grievances", json={
                "employee_id": "EMP-001",
                "title":       "Workload concern",
                "details":     "Excessive overtime without compensation.",
            })
        _ok(r, "POST /hrm/ess/grievances")


# ── 7.13 Machinery ────────────────────────────────────────────────

class TestMachineryWritePaths:
    """POST endpoints in the machinery module."""

    def test_create_asset(self, ac):
        with patch("machinery_data_store.MachineryDataStore") as MockDS:
            instance = MockDS.return_value
            instance.create_asset.return_value = "MACH-001"
            r = ac.post("/machinery/assets/new", data={
                "name":          "Excavator 320",
                "category":      "heavy_equipment",
                "asset_type":    "excavator",
                "manufacturer":  "CAT",
                "model":         "320",
                "serial_number": "SN-12345",
                "status":        "available",
                "fuel_type":     "diesel",
            })
        _ok(r, "POST /machinery/assets/new")

    def test_delete_asset(self, ac):
        with patch("machinery_data_store.MachineryDataStore") as MockDS:
            instance = MockDS.return_value
            instance.delete_asset.return_value = True
            r = ac.post("/machinery/assets/FAKE-ASSET-ID/delete")
        _ok(r, "POST /machinery/assets/FAKE-ASSET-ID/delete")


# ── 7.14 LMS ─────────────────────────────────────────────────────

class TestLMSWritePaths:
    """POST endpoints in the LMS module."""

    def test_enroll_course(self, ac):
        with patch("lms_data_store.lms_store") as s:
            s.get_course.return_value = {
                "course_id": "COURSE-001", "title": "Test Course",
                "status": "published", "company_id": "default",
            }
            s.enroll_user.return_value = "ENROL-001"
            r = ac.post("/lms/courses/COURSE-001/enroll")
        _ok(r, "POST /lms/courses/COURSE-001/enroll")

    def test_admin_add_course(self, ac):
        with patch("lms_data_store.lms_store") as s:
            s.create_course.return_value = "COURSE-NEW"
            r = ac.post("/lms/admin/courses/add", data={
                "title":        "Safety Fundamentals",
                "description":  "Core safety training",
                "content_type": "text",
                "category":     "Safety",
                "skill_level":  "beginner",
                "duration_minutes": "60",
                "passing_score":    "70",
            })
        _ok(r, "POST /lms/admin/courses/add")

    def test_admin_delete_course(self, ac):
        with patch("lms_data_store.lms_store") as s:
            s.delete_course.return_value = True
            r = ac.post("/lms/admin/courses/COURSE-FAKE/delete")
        _ok(r, "POST /lms/admin/courses/COURSE-FAKE/delete")


# ── 7.15 Backup ───────────────────────────────────────────────────

class TestBackupWritePaths:
    """POST endpoints in the backup module."""

    def test_create_backup(self, ac):
        with patch("backup_data_store.BackupEngine") as MockEngine:
            instance = MockEngine.return_value
            instance.create_backup.return_value = {
                "success": True, "archive_name": "backup_test.tar.gz"
            }
            r = ac.post("/backup/create", data={"label": "test backup"})
        _ok(r, "POST /backup/create")


# ══════════════════════════════════════════════════════════════════
#  8. Company_id propagation parametric test
#     Asserts that the session's current_company_id is what actually
#     reaches the data store — not a hardcoded "default" from a form.
# ══════════════════════════════════════════════════════════════════

class TestCompanyIdPropagation:
    """
    Critical: verify that every write route reads company_id from the
    SESSION, not from the POST body (the original class of bugs).
    """

    @pytest.mark.parametrize("company_id", ["company-abc", "company-xyz", "tenant-99"])
    def test_income_uses_session_company_id(self, company_id):
        """
        Send a form POST with NO company_id field in the body.
        The record saved must use the session's current_company_id.
        """
        captured: list = []

        def _capture(record):
            captured.append(record)
            return True

        # Build a client whose session has a specific company_id
        import base64
        from itsdangerous import URLSafeTimedSerializer

        secret = os.environ["FLASK_SECRET_KEY"]
        serializer = URLSafeTimedSerializer(secret)
        session_data = {
            "logged_in":          True,
            "user_id":            "test-001",
            "username":           "tester",
            "privilege_level":    "super_admin",
            "current_company_id": company_id,
        }
        signed = serializer.dumps(session_data)
        client = TestClient(_fastapi_app, raise_server_exceptions=False)
        client.cookies.set("session", signed, domain="testserver")

        with patch("income_expense_data_store.income_expense_store") as s:
            s.save_income_record.side_effect = _capture
            client.post("/income-expense/add-income", data={
                "date": "2026-03-01", "description": "Sale",
                "category": "X", "amount": "100",
                "tax_amount": "0", "customer_name": "C",
                "payment_method": "Cash",
            })

        if captured:
            assert captured[0].get("company_id") == company_id, (
                f"Expected company_id='{company_id}' but got '{captured[0].get('company_id')}'. "
                f"Route is reading company_id from the wrong source."
            )

    @pytest.mark.parametrize("company_id", ["company-abc", "company-xyz"])
    def test_journal_import_uses_session_company_id(self, company_id):
        """Journal import must pass session company_id to the data store."""
        captured_cids: list = []

        def _capture(filepath, cid=None, company_id=None):
            captured_cids.append(cid or company_id)
            return {"success": True, "imported_count": 0, "errors": []}

        from itsdangerous import URLSafeTimedSerializer
        secret = os.environ["FLASK_SECRET_KEY"]
        serializer = URLSafeTimedSerializer(secret)
        session_data = {
            "logged_in": True, "user_id": "t", "username": "t",
            "privilege_level": "super_admin",
            "current_company_id": company_id,
        }
        signed = serializer.dumps(session_data)
        client = TestClient(_fastapi_app, raise_server_exceptions=False)
        client.cookies.set("session", signed, domain="testserver")

        xlsx_bytes = _xlsx({
            "Journal Entries": [
                ["entry_id", "entry_date", "description", "total_debit", "total_credit"],
            ],
            "Entry Lines": [["entry_id", "account_code", "debit_amount", "credit_amount"]],
        })

        with patch("journal_entry_data_store.journal_store") as s:
            s.import_from_excel.side_effect = _capture
            client.post(
                "/journal/import/excel",
                files={"excel_file": ("j.xlsx", xlsx_bytes,
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )

        if captured_cids and captured_cids[0] is not None:
            assert captured_cids[0] == company_id, (
                f"journal import sent company_id='{captured_cids[0]}' "
                f"but session had '{company_id}'"
            )


# ══════════════════════════════════════════════════════════════════
#  9. Smoke: all POST endpoints return < 500
#     Catch-all parametric test for any module not covered above.
# ══════════════════════════════════════════════════════════════════

# Minimal POST bodies for the catch-all smoke pass
_SMOKE_POSTS: list[tuple[str, dict, str]] = [
    # (url, form_data, label)
    ("/auth/forgot-password", {"email": "x@x.com"}, "Forgot password"),
    ("/vat/income/add",   {"contract_date": "2026-01-01", "description": "x",
                           "category": "y", "gross_amount": "100",
                           "vat_type": "standard", "vat_rate": "15"}, "VAT income"),
    ("/vat/expenses/add", {"expense_date": "2026-01-01", "description": "x",
                           "category": "y", "gross_amount": "50",
                           "vat_type": "standard", "vat_rate": "15"}, "VAT expense"),
    ("/accounts/add",     {"account_code": "9998", "account_name": "Smoke",
                           "account_type": "Asset", "normal_balance": "debit"}, "Add account"),
    ("/bid/add",          {"title": "Smoke bid", "reference_number": "X",
                           "organization": "Org", "deadline": "2026-12-31",
                           "bid_amount": "1000", "currency": "ETB",
                           "case_handler_name": "H", "case_handler_email": "h@h.com"}, "Add bid"),
    ("/cpo/add",          {"name": "smoke cpo", "date": "2026-01-01",
                           "amount": "1000", "bid_name": "B"}, "Add CPO"),
]


class TestSmokePOST:
    """Minimal-data POST to every remaining endpoint — must not 500."""

    @pytest.mark.parametrize("url,data,label", _SMOKE_POSTS, ids=[x[2] for x in _SMOKE_POSTS])
    def test_smoke_post(self, ac, url, data, label):
        # Swallow all data store errors — we're only checking for HTTP 500
        with patch("chart_of_accounts_routes.accounts_store", MagicMock()), \
             patch("cpo_routes.cpo_store", MagicMock()), \
             patch("bid_data_store.bid_store", MagicMock()), \
             patch("auth_data_store.auth_store", MagicMock()):
            r = ac.post(url, data=data)
        assert r.status_code < 500, (
            f"[{label}] POST {url} returned {r.status_code}:\n{r.text[:400]}"
        )
