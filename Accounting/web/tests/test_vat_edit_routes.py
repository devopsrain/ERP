"""
Offline unit tests — VAT record edit endpoints (income / expenses / capital).

Exercises the full merge/rebuild path of
    GET  /vat/{income,expenses,capital}/{id}
    POST /vat/{income,expenses,capital}/{id}/edit
with realistic stored DB rows (dates as date objects, DOUBLE PRECISION as
floats, enums stored by VALUE) and submitted form dicts (all STRINGS, exactly
what the list-page modals post as JSON). No database required — the store's
single-record get/update methods are stubbed per test.

Guards the bugs that made the list-page Edit buttons crash:
  - enum round-trips (detail returns .name, edit accepts name OR value)
  - Decimal/float/None coercion and '' date handling
  - server-side recomputation of vat/net/penalty amounts
  - capital's DB column mapping (investment_date/investor_name)
  - parametric routes registered AFTER static /add, /import routes
"""
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi import FastAPI  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

import vat_routes  # noqa: E402
from deps import login_required  # noqa: E402


# ── Minimal app: real router, fake auth, no CSRF/auth middleware ──

@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")
    app.include_router(vat_routes.router)
    app.dependency_overrides[login_required] = lambda: {
        "username": "tester", "privilege_level": "admin"}
    return TestClient(app)


@pytest.fixture()
def store(monkeypatch):
    """Stub the single-record store methods; captures UPDATE payloads."""
    class _FakeStore:
        def __init__(self):
            self.rows = {}          # (kind, id) -> row dict
            self.updates = {}       # kind -> last updates dict
            self.update_ok = True

        def _get(self, kind, company_id, record_id):
            row = self.rows.get((kind, record_id))
            return dict(row) if row and row.get("company_id") == company_id else None

        def _update(self, kind, updates):
            self.updates[kind] = dict(updates)
            return self.update_ok

    fake = _FakeStore()
    ds = vat_routes.vat_data_store
    monkeypatch.setattr(ds, "get_income_record",
                        lambda c, i: fake._get("income", c, i))
    monkeypatch.setattr(ds, "update_income_record",
                        lambda c, i, u: fake._update("income", u))
    monkeypatch.setattr(ds, "get_expense_record",
                        lambda c, i: fake._get("expense", c, i))
    monkeypatch.setattr(ds, "update_expense_record",
                        lambda c, i, u: fake._update("expense", u))
    monkeypatch.setattr(ds, "get_capital_record",
                        lambda c, i: fake._get("capital", c, i))
    monkeypatch.setattr(ds, "update_capital_record",
                        lambda c, i, u: fake._update("capital", u))
    return fake


# ── Realistic stored rows (psycopg RealDictRow shapes) ────────────

def _income_row(**kw):
    row = {
        "income_id": "INC-1", "company_id": "default",
        "contract_date": date(2026, 7, 1), "income_date": date(2026, 7, 3),
        "description": "Consulting", "category": "Service Income",
        "gross_amount": 115000.0, "vat_type": "Standard VAT (15%)",
        "vat_rate": 0.15, "vat_amount": 17250.0, "net_amount": 97750.0,
        "customer_name": "Acme", "customer_tin": "123456789",
        "invoice_number": "INV-1", "tender_id": "", "payment_mode": "",
        "income_type": "", "penalty": "no", "penalty_fee": 0.0, "brand": "",
        "created_date": datetime(2026, 7, 1, 10),
        "updated_date": datetime(2026, 7, 1, 10),
        "created_by": "fde", "is_active": True,
    }
    row.update(kw)
    return row


def _expense_row(**kw):
    row = {
        "expense_id": "EXP-1", "company_id": "default",
        "expense_date": date(2026, 7, 2), "description": "Rent",
        "category": "Rent", "gross_amount": 300.0,
        "vat_type": "Standard VAT (15%)", "vat_rate": 0.15,
        "vat_amount": 45.0, "net_amount": 345.0,
        "supplier_name": "Landlord", "supplier_tin": "", "receipt_number": "R-9",
        "tender_id": "",
        "created_date": datetime(2026, 7, 2, 9),
        "updated_date": datetime(2026, 7, 2, 9),
        "created_by": "fde", "is_active": True,
    }
    row.update(kw)
    return row


def _capital_row(**kw):
    row = {
        "capital_id": "CAP-1", "company_id": "default",
        "investment_date": date(2026, 7, 3), "description": "Owner cash",
        "capital_type": "CASH", "transaction_type": "INJECTION",
        "amount": 500.0, "vat_type": "", "vat_rate": 0.0, "vat_amount": 0.0,
        "investor_name": "Owner", "investor_tin": "",
        "created_date": datetime(2026, 7, 3, 8),
        "updated_date": datetime(2026, 7, 3, 8),
        "created_by": "fde", "is_active": True,
    }
    row.update(kw)
    return row


# ── Route ordering: parametric AFTER static ───────────────────────

def test_param_routes_registered_after_static():
    """/expenses/add etc. must never be shadowed by /{id} routes."""
    paths = [r.path for r in vat_routes.router.routes]
    for static, param in [
        ("/vat/income/add", "/vat/income/{income_id}"),
        ("/vat/income/import", "/vat/income/{income_id}"),
        ("/vat/income/import/template", "/vat/income/{income_id}"),
        ("/vat/expenses/add", "/vat/expenses/{expense_id}"),
        ("/vat/capital/add", "/vat/capital/{capital_id}"),
    ]:
        assert paths.index(static) < paths.index(param), (
            f"{param} is registered before {static} and would shadow it")


# ── Income ────────────────────────────────────────────────────────

def test_income_detail_serializes_row(client, store):
    store.rows[("income", "INC-1")] = _income_row()
    r = client.get("/vat/income/INC-1")
    assert r.status_code == 200
    inc = r.json()["income"]
    assert inc["category"] == "SERVICE_INCOME"          # enum NAME
    assert inc["category_value"] == "Service Income"    # display value
    assert inc["vat_type"] == "STANDARD"
    assert inc["contract_date"] == "2026-07-01"
    assert inc["gross_amount"] == 115000.0
    assert inc["client_name"] == "Acme"                 # template alias


def test_income_detail_404(client, store):
    r = client.get("/vat/income/NOPE")
    assert r.status_code == 404


def test_income_edit_recomputes_amounts_from_string_form(client, store):
    store.rows[("income", "INC-1")] = _income_row()
    # exactly what the modal posts: every value a string
    payload = {
        "contract_date": "2026-07-01", "income_date": "2026-07-05",
        "description": "Consulting v2", "category": "SERVICE_INCOME",
        "vat_type": "STANDARD", "customer_name": "Acme", "customer_tin": "",
        "gross_amount": "200000", "invoice_number": "INV-1", "tender_id": "",
        "payment_mode": "advance", "income_type": "service",
        "brand": "Cisco", "penalty": "yes", "csrf_token": "tok",
    }
    r = client.post("/vat/income/INC-1/edit", json=payload)
    assert r.status_code == 200, r.text
    u = store.updates["income"]
    assert u["gross_amount"] == 200000.0
    assert u["vat_amount"] == pytest.approx(30000.0)    # 15% recomputed
    assert u["net_amount"] == pytest.approx(170000.0)
    assert u["penalty_fee"] == pytest.approx(20000.0)   # 10% of gross
    assert u["income_date"] == date(2026, 7, 5)
    assert u["category"] == "Service Income"            # stored by VALUE
    assert u["vat_type"] == "Standard VAT (15%)"


def test_income_edit_accepts_enum_values_too(client, store):
    """JSON API clients may send display values instead of names."""
    store.rows[("income", "INC-1")] = _income_row()
    payload = {"category": "Sales Revenue", "vat_type": "Standard VAT (15%)",
               "gross_amount": "1000"}
    r = client.post("/vat/income/INC-1/edit", json=payload)
    assert r.status_code == 200, r.text
    assert store.updates["income"]["category"] == "Sales Revenue"


def test_income_edit_legacy_row_and_empty_strings(client, store):
    """Legacy row: NULL dates, '' category, lowercase vat_type, missing
    new columns entirely — plus a submission of all-empty strings."""
    legacy = {
        "income_id": "INC-L", "company_id": "default",
        "contract_date": None, "description": None, "category": "",
        "gross_amount": 1000.0, "vat_type": "standard", "vat_rate": 0.15,
        "vat_amount": 150.0, "net_amount": 850.0,
        "customer_name": None, "customer_tin": None, "invoice_number": None,
        "created_date": None, "updated_date": None, "created_by": None,
        "is_active": True,
    }
    store.rows[("income", "INC-L")] = legacy
    r = client.get("/vat/income/INC-L")
    assert r.status_code == 200
    payload = {k: "" for k in (
        "contract_date", "income_date", "description", "category", "vat_type",
        "customer_name", "customer_tin", "gross_amount", "invoice_number",
        "tender_id", "payment_mode", "income_type", "brand")}
    payload["penalty"] = "no"
    r = client.post("/vat/income/INC-L/edit", json=payload)
    assert r.status_code == 200, r.text
    u = store.updates["income"]
    assert u["gross_amount"] == 1000.0                  # kept stored amount
    assert u["penalty_fee"] == 0.0


def test_income_edit_preserves_zero_rate_for_exempt(client, store):
    """A stored 0 vat_rate (EXEMPT) must not silently become 0.15."""
    store.rows[("income", "INC-1")] = _income_row(
        vat_type="Exempt", vat_rate=0.0, vat_amount=0.0, net_amount=115000.0)
    r = client.post("/vat/income/INC-1/edit",
                    json={"vat_type": "EXEMPT", "gross_amount": "115000"})
    assert r.status_code == 200, r.text
    u = store.updates["income"]
    assert u["vat_rate"] == 0.0
    assert u["vat_amount"] == 0.0
    assert u["net_amount"] == 115000.0


def test_income_edit_update_failure_is_a_readable_400(client, store):
    store.rows[("income", "INC-1")] = _income_row()
    store.update_ok = False
    r = client.post("/vat/income/INC-1/edit", json={"gross_amount": "1"})
    assert r.status_code == 400
    assert "could not be updated" in r.json()["detail"]


# ── Expenses ──────────────────────────────────────────────────────

def test_expense_detail_serializes_row(client, store):
    store.rows[("expense", "EXP-1")] = _expense_row()
    r = client.get("/vat/expenses/EXP-1")
    assert r.status_code == 200
    exp = r.json()["expense"]
    assert exp["category"] == "RENT"
    assert exp["category_value"] == "Rent"
    assert exp["vat_type"] == "STANDARD"
    assert exp["expense_date"] == "2026-07-02"
    assert exp["total_amount"] == 345.0


def test_expense_edit_recomputes_net_as_gross_plus_vat(client, store):
    store.rows[("expense", "EXP-1")] = _expense_row()
    payload = {
        "expense_date": "2026-07-10", "description": "Office rent Q3",
        "category": "RENT", "vat_type": "STANDARD", "gross_amount": "1000",
        "supplier_name": "Landlord Ltd", "supplier_tin": "987654321",
        "receipt_number": "R-10", "tender_id": "BID-1", "csrf_token": "tok",
    }
    r = client.post("/vat/expenses/EXP-1/edit", json=payload)
    assert r.status_code == 200, r.text
    u = store.updates["expense"]
    assert u["gross_amount"] == 1000.0
    assert u["vat_amount"] == pytest.approx(150.0)
    assert u["net_amount"] == pytest.approx(1150.0)     # expenses ADD VAT
    assert u["expense_date"] == date(2026, 7, 10)
    assert u["category"] == "Rent"
    assert u["supplier_name"] == "Landlord Ltd"


def test_expense_edit_vat_type_change_resets_rate(client, store):
    store.rows[("expense", "EXP-1")] = _expense_row()
    r = client.post("/vat/expenses/EXP-1/edit",
                    json={"vat_type": "EXEMPT", "gross_amount": "1000"})
    assert r.status_code == 200, r.text
    u = store.updates["expense"]
    assert u["vat_rate"] == 0.0
    assert u["vat_amount"] == 0.0
    assert u["net_amount"] == 1000.0


def test_expense_detail_404(client, store):
    assert client.get("/vat/expenses/NOPE").status_code == 404
    assert client.post("/vat/expenses/NOPE/edit", json={}).status_code == 404


# ── Capital ───────────────────────────────────────────────────────

def test_capital_detail_maps_db_columns(client, store):
    store.rows[("capital", "CAP-1")] = _capital_row()
    r = client.get("/vat/capital/CAP-1")
    assert r.status_code == 200
    cap = r.json()["capital"]
    assert cap["transaction_date"] == "2026-07-03"      # from investment_date
    assert cap["source"] == "Owner"                     # from investor_name
    assert cap["source_destination"] == "Owner"
    assert cap["transaction_type"] == "INJECTION"
    assert cap["amount"] == 500.0


def test_capital_edit_maps_back_to_db_columns(client, store):
    store.rows[("capital", "CAP-1")] = _capital_row()
    payload = {
        "transaction_date": "2026-08-01", "description": "Owner withdrawal",
        "capital_type": "CASH", "transaction_type": "WITHDRAWAL",
        "amount": "750.50", "source_destination": "Owner account",
        "csrf_token": "tok",
    }
    r = client.post("/vat/capital/CAP-1/edit", json=payload)
    assert r.status_code == 200, r.text
    u = store.updates["capital"]
    assert u["investment_date"] == date(2026, 8, 1)     # model→DB mapping
    assert u["investor_name"] == "Owner account"
    assert u["transaction_type"] == "WITHDRAWAL"
    assert u["amount"] == 750.50
    assert "transaction_date" not in u                  # only DB column names
    assert "source" not in u


def test_capital_edit_rejects_bad_transaction_type(client, store):
    store.rows[("capital", "CAP-1")] = _capital_row()
    r = client.post("/vat/capital/CAP-1/edit",
                    json={"transaction_type": "SIDEWAYS"})
    assert r.status_code == 400
    assert "transaction type" in r.json()["detail"].lower()


def test_capital_edit_partial_update_keeps_stored_fields(client, store):
    store.rows[("capital", "CAP-1")] = _capital_row()
    r = client.post("/vat/capital/CAP-1/edit", json={"amount": "999"})
    assert r.status_code == 200, r.text
    u = store.updates["capital"]
    assert u["amount"] == 999.0
    assert u["investment_date"] == date(2026, 7, 3)     # unchanged
    assert u["description"] == "Owner cash"
    assert u["investor_name"] == "Owner"
    assert u["transaction_type"] == "INJECTION"


def test_capital_detail_404(client, store):
    assert client.get("/vat/capital/NOPE").status_code == 404
