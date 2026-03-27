"""
RLS Tenant Isolation Tests (Gap 8)

Verifies that PostgreSQL Row-Level Security policies correctly prevent
one tenant from reading or modifying another tenant's data.

Prerequisites:
  1. RLS policies applied: psql $DATABASE_URL -f migrations/versions/add_rls_policies.sql
  2. DATABASE_URL env var set
  3. pip install pytest psycopg2-binary

Run from web/:
    pytest tests/test_rls_isolation.py -v
"""

from __future__ import annotations

import uuid
import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

COMPANY_A = str(uuid.uuid4())
COMPANY_B = str(uuid.uuid4())


@pytest.fixture(scope="module", autouse=True)
def seed_companies():
    """Insert two test companies into the tenants table, then clean up."""
    from db import get_cursor, get_conn

    # Insert test tenants
    try:
        with get_cursor() as cur:
            for cid, name in [(COMPANY_A, "_test_company_a"), (COMPANY_B, "_test_company_b")]:
                cur.execute(
                    """INSERT INTO tenants (company_id, company_name, subscription_tier, is_active, created_at)
                       VALUES (%s, %s, 'starter', TRUE, NOW())
                       ON CONFLICT (company_id) DO NOTHING""",
                    (cid, name),
                )
    except Exception:
        # tenants table may have different schema; tests still run without it
        pass

    yield

    # Teardown — remove all test data for both companies
    with get_conn() as conn:
        with conn.cursor() as cur:
            for table in (
                "transactions", "employees", "journal_entries",
                "inventory_items", "cpo_records", "bid_records",
                "vat_income", "vat_expenses",
            ):
                try:
                    cur.execute(
                        f"DELETE FROM {table} WHERE company_id IN (%s, %s)",
                        (COMPANY_A, COMPANY_B),
                    )
                except Exception:
                    pass
    try:
        with get_cursor() as cur:
            cur.execute(
                "DELETE FROM tenants WHERE company_id IN (%s, %s)",
                (COMPANY_A, COMPANY_B),
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _insert_as(company_id: str, table: str, row: dict):
    """Insert *row* using get_tenant_cursor so RLS context is set."""
    from db import get_tenant_cursor
    with get_tenant_cursor(company_id) as cur:
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f"%s" for _ in row)
        cur.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
            list(row.values()),
        )


def _count_as(company_id: str, table: str, row_id: str, id_col: str = "id") -> int:
    """Return row count visible to *company_id* for a specific row id."""
    from db import get_tenant_cursor
    with get_tenant_cursor(company_id) as cur:
        cur.execute(
            f"SELECT COUNT(*) AS cnt FROM {table} WHERE {id_col} = %s",
            (row_id,),
        )
        row = cur.fetchone()
        return int(row["cnt"]) if row else 0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTransactionIsolation:
    def test_company_b_cannot_see_company_a_transaction(self):
        row_id = str(uuid.uuid4())
        _insert_as(COMPANY_A, "transactions", {
            "id": row_id,
            "company_id": COMPANY_A,
            "date": "2026-01-01",
            "description": "RLS test transaction",
            "amount": 100.00,
            "transaction_type": "income",
            "reference": "RLS-TEST",
            "is_flagged": False,
        })

        visible_to_b = _count_as(COMPANY_B, "transactions", row_id)
        assert visible_to_b == 0, (
            f"CRITICAL RLS BREACH: Company B can see Company A's transaction {row_id}"
        )

    def test_company_a_can_see_own_transaction(self):
        row_id = str(uuid.uuid4())
        _insert_as(COMPANY_A, "transactions", {
            "id": row_id,
            "company_id": COMPANY_A,
            "date": "2026-01-01",
            "description": "RLS own-access test",
            "amount": 50.00,
            "transaction_type": "expense",
            "reference": "RLS-OWN",
            "is_flagged": False,
        })

        visible_to_a = _count_as(COMPANY_A, "transactions", row_id)
        assert visible_to_a == 1, "Company A should see its own transaction"


class TestInventoryIsolation:
    def test_company_b_cannot_see_company_a_item(self):
        item_id = "RLSITEM" + uuid.uuid4().hex[:4].upper()
        _insert_as(COMPANY_A, "inventory_items", {
            "item_id": item_id,
            "company_id": COMPANY_A,
            "name": "RLS Test Item",
            "sku": "RLS-SKU-A",
            "category": "test",
            "description": "",
            "unit_of_measure": "pcs",
            "unit_cost": 10.00,
            "quantity_on_hand": 5.0,
            "reorder_point": 1.0,
            "reorder_quantity": 10.0,
            "location": "",
            "status": "active",
        })

        visible_to_b = _count_as(COMPANY_B, "inventory_items", item_id, "item_id")
        assert visible_to_b == 0, (
            f"CRITICAL RLS BREACH: Company B can see Company A's inventory item {item_id}"
        )


class TestBidIsolation:
    def test_company_b_cannot_see_company_a_bid(self):
        bid_id = str(uuid.uuid4())
        _insert_as(COMPANY_A, "bid_records", {
            "id": bid_id,
            "company_id": COMPANY_A,
            "title": "RLS Test Bid",
            "reference_number": "RLS-BID-001",
            "organization": "Test Org",
            "description": "",
            "category": "test",
            "status": "open",
            "deadline": "2026-12-31",
            "bid_amount": 999.00,
            "currency": "ETB",
            "notes": "",
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
            "reminder_days_before": 3,
            "reminder_sent": False,
        })

        visible_to_b = _count_as(COMPANY_B, "bid_records", bid_id)
        assert visible_to_b == 0, (
            f"CRITICAL RLS BREACH: Company B can see Company A's bid {bid_id}"
        )


class TestCrossWriteRejection:
    def test_company_b_cannot_update_company_a_record(self):
        """RLS WITH CHECK should block Company B from updating Company A's row."""
        from db import get_tenant_cursor

        row_id = str(uuid.uuid4())
        _insert_as(COMPANY_A, "transactions", {
            "id": row_id,
            "company_id": COMPANY_A,
            "date": "2026-01-01",
            "description": "Write protection test",
            "amount": 1.00,
            "transaction_type": "income",
            "reference": "RLS-WRITE",
            "is_flagged": False,
        })

        # Attempt an UPDATE as Company B — should not affect any row
        with get_tenant_cursor(COMPANY_B) as cur:
            cur.execute(
                "UPDATE transactions SET description='BREACHED' WHERE id=%s",
                (row_id,),
            )
            affected = cur.rowcount

        assert affected == 0, (
            f"CRITICAL RLS BREACH: Company B updated Company A's transaction {row_id}"
        )

        # Confirm the original record is intact
        with get_tenant_cursor(COMPANY_A) as cur:
            cur.execute(
                "SELECT description FROM transactions WHERE id=%s",
                (row_id,),
            )
            row = cur.fetchone()
        assert row is not None
        assert row["description"] == "Write protection test", (
            "Original record was modified by cross-tenant UPDATE"
        )


class TestEmployeeIsolation:
    def test_company_b_cannot_see_company_a_employee(self):
        emp_id = "EMP-RLS-" + uuid.uuid4().hex[:4].upper()
        _insert_as(COMPANY_A, "employees", {
            "employee_id": emp_id,
            "company_id": COMPANY_A,
            "name": "RLS Test Employee",
            "category": "permanent",
            "basic_salary": 5000.00,
            "hire_date": "2026-01-01",
            "tin_number": "RLS" + uuid.uuid4().hex[:6],
            "is_active": True,
            "work_days_per_month": 26,
            "work_hours_per_day": 8,
        })

        visible_to_b = _count_as(COMPANY_B, "employees", emp_id, "employee_id")
        assert visible_to_b == 0, (
            f"CRITICAL RLS BREACH: Company B can see Company A's employee {emp_id}"
        )
