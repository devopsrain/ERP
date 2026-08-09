"""
Offline unit tests for the two production write-path fixes.

1. EmployeeDataStore.write_employees must never send SQL NULL into the
   NOT NULL employees columns (is_active, work_days_per_month,
   work_hours_per_day, text columns) when the caller — the add-employee and
   quick-add forms — does not supply them, and must not reference the
   optional `email` column. Verified with a fake connection + captured
   execute_values arguments; no database needed.

2. procurement_routes._pr_form_data / _parse_amount must map the PR form
   fields (item_description, quantity, estimated_cost, budget_line, ...)
   onto the proc_purchase_requisitions columns (title, department,
   total_amount, ...) with NUMERIC-safe parsing of empty strings.
"""
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import employee_data_store as eds  # noqa: E402
from procurement_routes import _parse_amount, _pr_form_data  # noqa: E402


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return ("x",)  # "column already exists" for _ensure_new_columns

    def close(self):
        pass


class _FakeConn:
    def cursor(self, *a, **k):
        return _FakeCursor()

    def commit(self):
        pass


@contextmanager
def _fake_get_conn():
    yield _FakeConn()


@pytest.fixture
def store_and_captured(monkeypatch):
    captured = {}

    def _fake_execute_values(cur, sql, data):
        captured["sql"] = sql
        captured["data"] = data

    monkeypatch.setattr(eds, "get_conn", _fake_get_conn)
    monkeypatch.setattr(eds, "execute_values", _fake_execute_values)
    return eds.EmployeeDataStore(), captured


# Exactly what add_employee_post builds (no is_active / work_days / work_hours)
_FORM_EMP = {
    "employee_id": "EMP-TEST1", "name": "Abebe Kebede",
    "category": "Regular Employee", "basic_salary": 12000.0,
    "hire_date": date(2026, 7, 1), "department": "Finance",
    "position": "Accountant", "bank_account": "1000123",
    "tin_number": "123456789", "pension_number": "PEN1",
    "phone_number": "+251-91-000-0000", "manager": "",
    "date_of_birth": None, "company_id": "default",
}

_COLS = [
    'employee_id', 'company_id', 'name', 'category', 'basic_salary',
    'hire_date', 'department', 'position', 'bank_account', 'tin_number',
    'pension_number', 'work_days_per_month', 'work_hours_per_day',
    'is_active', 'created_date', 'updated_date',
    'date_of_birth', 'phone_number', 'manager'
]


class TestWriteEmployeesNotNullDefaults:

    def test_missing_not_null_columns_get_defaults_not_null(self, store_and_captured):
        store, captured = store_and_captured
        store.write_employees(pd.DataFrame([dict(_FORM_EMP)]))
        row = dict(zip(_COLS, captured["data"][0]))
        assert row["is_active"] is True
        assert row["work_days_per_month"] == 22
        assert row["work_hours_per_day"] == 8
        assert row["employee_id"] == "EMP-TEST1"
        assert row["hire_date"] == date(2026, 7, 1)
        assert row["date_of_birth"] is None          # nullable stays NULL

    def test_write_path_does_not_reference_email_column(self, store_and_captured):
        store, captured = store_and_captured
        store.write_employees(pd.DataFrame([dict(_FORM_EMP)]))
        assert "email" not in captured["sql"].lower()

    def test_nan_in_partial_bulk_rows_becomes_default_or_none(self, store_and_captured):
        store, captured = store_and_captured
        full = dict(_FORM_EMP)
        full.update(is_active=False, work_days_per_month=26,
                    date_of_birth=date(1990, 1, 1))
        partial = dict(_FORM_EMP, employee_id="EMP-TEST2")
        store.write_employees(pd.DataFrame([full, partial]))
        r1 = dict(zip(_COLS, captured["data"][0]))
        r2 = dict(zip(_COLS, captured["data"][1]))
        assert r1["is_active"] is False and r1["work_days_per_month"] == 26
        assert r2["is_active"] is True and r2["work_days_per_month"] == 22
        assert r2["date_of_birth"] is None           # NaN/NaT → None, not NaT

    def test_add_employee_failure_sets_last_error(self, monkeypatch):
        def _boom(cur, sql, data):
            raise RuntimeError("null value in column violates not-null constraint")

        monkeypatch.setattr(eds, "get_conn", _fake_get_conn)
        monkeypatch.setattr(eds, "execute_values", _boom)
        store = eds.EmployeeDataStore()
        # employee_exists hits the real DB helper and fails offline → treated
        # as "does not exist", which is what we want here.
        ok = store.add_employee(dict(_FORM_EMP))
        assert ok is False
        assert "not-null constraint" in store.last_error

    def test_add_employee_success_clears_last_error(self, store_and_captured):
        store, _ = store_and_captured
        store.last_error = "stale"
        assert store.add_employee(dict(_FORM_EMP)) is True
        assert store.last_error == ""


class TestPrFormParsing:

    def test_parse_amount_empty_and_junk(self):
        assert _parse_amount("") == 0.0
        assert _parse_amount(None) == 0.0
        assert _parse_amount("   ") == 0.0
        assert _parse_amount("abc", 5.0) == 5.0
        assert _parse_amount("1,234.50") == 1234.5
        assert _parse_amount("", 7.5) == 7.5

    def test_pr_form_maps_template_fields_to_schema_columns(self):
        form = {
            "item_description": "10 boxes of A4 paper",
            "quantity": "10", "unit": "boxes",
            "estimated_cost": "250.00",
            "required_by_date": "2026-09-01",
            "budget_line": "Office Supplies",
            "justification": "Stock replenishment",
            "csrf_token": "x",
        }
        data = _pr_form_data(form, requested_by="fde")
        assert data["title"] == "10 boxes of A4 paper"
        assert data["department"] == "Office Supplies"   # budget_line fallback
        assert data["total_amount"] == pytest.approx(2500.0)  # qty * est_cost
        assert data["requested_by"] == "fde"
        assert "Required by: 2026-09-01" in data["description"]
        assert "Justification: Stock replenishment" in data["description"]

    def test_pr_form_empty_numeric_fields_do_not_break_numeric_insert(self):
        form = {"item_description": "Something", "quantity": "",
                "estimated_cost": "", "budget_line": ""}
        data = _pr_form_data(form)
        assert isinstance(data["total_amount"], float)
        assert data["total_amount"] == 0.0
        assert data["department"] == "General"           # NOT NULL default

    def test_pr_form_api_shape_still_supported(self):
        form = {"title": "Laptops", "department": "IT",
                "description": "5 laptops", "total_amount": "150000"}
        data = _pr_form_data(form, requested_by="api")
        assert data["title"] == "Laptops"
        assert data["department"] == "IT"
        assert data["description"] == "5 laptops"
        assert data["total_amount"] == 150000.0

    def test_pr_form_missing_title_is_flagged(self):
        data = _pr_form_data({"quantity": "3"})
        assert data["title"] == ""                       # route flashes error
