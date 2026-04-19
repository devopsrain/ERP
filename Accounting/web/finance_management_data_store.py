"""
AICC Finance Management Data Store - PostgreSQL backend.
"""

import logging
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from db import get_conn, get_tenant_cursor

logger = logging.getLogger(__name__)


class FinanceManagementDataStore:
    def __init__(self):
        self._ensure_tables()

    def _ensure_tables(self):
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS fin_gl_entries (
                            entry_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            entry_date DATE NOT NULL,
                            account_code VARCHAR(50) NOT NULL,
                            account_name VARCHAR(255) DEFAULT '',
                            cost_center VARCHAR(100) DEFAULT '',
                            amount NUMERIC(15,2) DEFAULT 0,
                            entry_type VARCHAR(10) NOT NULL,
                            reference VARCHAR(100) DEFAULT '',
                            description TEXT,
                            created_by VARCHAR(100),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS fin_ar_ap (
                            txn_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            txn_type VARCHAR(10) NOT NULL,
                            party_name VARCHAR(255) NOT NULL,
                            invoice_no VARCHAR(100) DEFAULT '',
                            due_date DATE,
                            amount NUMERIC(15,2) DEFAULT 0,
                            paid_amount NUMERIC(15,2) DEFAULT 0,
                            status VARCHAR(30) DEFAULT 'open',
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS fin_assets (
                            asset_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            asset_name VARCHAR(255) NOT NULL,
                            category VARCHAR(100) DEFAULT '',
                            acquisition_date DATE,
                            acquisition_cost NUMERIC(15,2) DEFAULT 0,
                            useful_life_years INT DEFAULT 1,
                            depreciation_method VARCHAR(30) DEFAULT 'straight_line',
                            accumulated_depreciation NUMERIC(15,2) DEFAULT 0,
                            book_value NUMERIC(15,2) DEFAULT 0,
                            status VARCHAR(30) DEFAULT 'active',
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS fin_budgets (
                            budget_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            fiscal_year INT NOT NULL,
                            cost_center VARCHAR(100) NOT NULL,
                            account_code VARCHAR(50) NOT NULL,
                            budget_amount NUMERIC(15,2) DEFAULT 0,
                            forecast_amount NUMERIC(15,2) DEFAULT 0,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS fin_shareholders (
                            shareholder_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            full_name VARCHAR(255) NOT NULL,
                            national_id VARCHAR(100) DEFAULT '',
                            shares_owned NUMERIC(15,2) DEFAULT 0,
                            share_class VARCHAR(50) DEFAULT 'ordinary',
                            ownership_percent NUMERIC(8,4) DEFAULT 0,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS fin_dividends (
                            dividend_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            declaration_date DATE NOT NULL,
                            fiscal_year INT NOT NULL,
                            total_amount NUMERIC(15,2) DEFAULT 0,
                            status VARCHAR(30) DEFAULT 'declared',
                            notes TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE INDEX IF NOT EXISTS idx_fin_gl_company_date
                            ON fin_gl_entries(company_id, entry_date);
                        CREATE INDEX IF NOT EXISTS idx_fin_ar_ap_company
                            ON fin_ar_ap(company_id, txn_type, status);
                        CREATE INDEX IF NOT EXISTS idx_fin_assets_company
                            ON fin_assets(company_id, status);
                        CREATE INDEX IF NOT EXISTS idx_fin_budget_company_year
                            ON fin_budgets(company_id, fiscal_year);
                        CREATE INDEX IF NOT EXISTS idx_fin_shareholder_company
                            ON fin_shareholders(company_id);
                        """
                    )
                    conn.commit()
        except Exception as e:
            logger.warning("Finance tables check failed: %s", e)

    def post_gl_entry(self, data: Dict[str, Any]) -> Optional[str]:
        entry_id = data.get("entry_id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO fin_gl_entries
                    (entry_id, company_id, entry_date, account_code, account_name, cost_center,
                     amount, entry_type, reference, description, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        entry_id, cid,
                        data.get("entry_date", date.today()),
                        data.get("account_code", ""),
                        data.get("account_name", ""),
                        data.get("cost_center", ""),
                        float(data.get("amount", 0) or 0),
                        data.get("entry_type", "debit"),
                        data.get("reference", ""),
                        data.get("description", ""),
                        data.get("created_by", ""),
                    ),
                )
                return entry_id
        except Exception as e:
            logger.error("post_gl_entry failed: %s", e)
            return None

    def create_ar_ap(self, data: Dict[str, Any]) -> Optional[str]:
        txn_id = data.get("txn_id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO fin_ar_ap
                    (txn_id, company_id, txn_type, party_name, invoice_no, due_date,
                     amount, paid_amount, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        txn_id, cid,
                        data.get("txn_type", "AR"),
                        data.get("party_name", ""),
                        data.get("invoice_no", ""),
                        data.get("due_date"),
                        float(data.get("amount", 0) or 0),
                        float(data.get("paid_amount", 0) or 0),
                        data.get("status", "open"),
                    ),
                )
                return txn_id
        except Exception as e:
            logger.error("create_ar_ap failed: %s", e)
            return None

    def create_asset(self, data: Dict[str, Any]) -> Optional[str]:
        asset_id = data.get("asset_id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        cost = float(data.get("acquisition_cost", 0) or 0)
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO fin_assets
                    (asset_id, company_id, asset_name, category, acquisition_date,
                     acquisition_cost, useful_life_years, depreciation_method,
                     accumulated_depreciation, book_value, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        asset_id, cid,
                        data.get("asset_name", ""),
                        data.get("category", ""),
                        data.get("acquisition_date"),
                        cost,
                        int(data.get("useful_life_years", 1) or 1),
                        data.get("depreciation_method", "straight_line"),
                        float(data.get("accumulated_depreciation", 0) or 0),
                        float(data.get("book_value", cost) or cost),
                        data.get("status", "active"),
                    ),
                )
                return asset_id
        except Exception as e:
            logger.error("create_asset failed: %s", e)
            return None

    def create_budget(self, data: Dict[str, Any]) -> Optional[str]:
        budget_id = data.get("budget_id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO fin_budgets
                    (budget_id, company_id, fiscal_year, cost_center, account_code,
                     budget_amount, forecast_amount)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        budget_id, cid,
                        int(data.get("fiscal_year", date.today().year) or date.today().year),
                        data.get("cost_center", ""),
                        data.get("account_code", ""),
                        float(data.get("budget_amount", 0) or 0),
                        float(data.get("forecast_amount", 0) or 0),
                    ),
                )
                return budget_id
        except Exception as e:
            logger.error("create_budget failed: %s", e)
            return None

    def create_shareholder(self, data: Dict[str, Any]) -> Optional[str]:
        shareholder_id = data.get("shareholder_id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO fin_shareholders
                    (shareholder_id, company_id, full_name, national_id, shares_owned,
                     share_class, ownership_percent)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        shareholder_id, cid,
                        data.get("full_name", ""),
                        data.get("national_id", ""),
                        float(data.get("shares_owned", 0) or 0),
                        data.get("share_class", "ordinary"),
                        float(data.get("ownership_percent", 0) or 0),
                    ),
                )
                return shareholder_id
        except Exception as e:
            logger.error("create_shareholder failed: %s", e)
            return None

    def declare_dividend(self, data: Dict[str, Any]) -> Optional[str]:
        dividend_id = data.get("dividend_id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO fin_dividends
                    (dividend_id, company_id, declaration_date, fiscal_year, total_amount, status, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        dividend_id, cid,
                        data.get("declaration_date", date.today()),
                        int(data.get("fiscal_year", date.today().year) or date.today().year),
                        float(data.get("total_amount", 0) or 0),
                        data.get("status", "declared"),
                        data.get("notes", ""),
                    ),
                )
                return dividend_id
        except Exception as e:
            logger.error("declare_dividend failed: %s", e)
            return None

    def budget_vs_actual(self, fiscal_year: int, company_id: str = "default") -> List[Dict[str, Any]]:
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    """
                    SELECT
                        b.cost_center,
                        b.account_code,
                        b.budget_amount,
                        COALESCE(SUM(CASE WHEN g.entry_type='debit' THEN g.amount ELSE -g.amount END), 0) AS actual_amount,
                        b.budget_amount - COALESCE(SUM(CASE WHEN g.entry_type='debit' THEN g.amount ELSE -g.amount END), 0) AS variance
                    FROM fin_budgets b
                    LEFT JOIN fin_gl_entries g
                      ON b.company_id = g.company_id
                     AND b.account_code = g.account_code
                     AND EXTRACT(YEAR FROM g.entry_date) = %s
                    WHERE b.company_id = %s AND b.fiscal_year = %s
                    GROUP BY b.cost_center, b.account_code, b.budget_amount
                    ORDER BY b.cost_center, b.account_code
                    """,
                    (fiscal_year, company_id, fiscal_year),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("budget_vs_actual failed: %s", e)
            return []

    def finance_dashboard(self, company_id: str = "default") -> Dict[str, Any]:
        data = {
            "open_ar": 0.0,
            "open_ap": 0.0,
            "asset_book_value": 0.0,
            "shareholders": 0,
            "declared_dividends": 0.0,
            "gl_entries": 0,
        }
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute("SELECT COALESCE(SUM(amount - paid_amount),0) AS v FROM fin_ar_ap WHERE company_id=%s AND txn_type='AR' AND status='open'", (company_id,))
                data["open_ar"] = float((cur.fetchone() or {}).get("v", 0) or 0)

                cur.execute("SELECT COALESCE(SUM(amount - paid_amount),0) AS v FROM fin_ar_ap WHERE company_id=%s AND txn_type='AP' AND status='open'", (company_id,))
                data["open_ap"] = float((cur.fetchone() or {}).get("v", 0) or 0)

                cur.execute("SELECT COALESCE(SUM(book_value),0) AS v FROM fin_assets WHERE company_id=%s AND status='active'", (company_id,))
                data["asset_book_value"] = float((cur.fetchone() or {}).get("v", 0) or 0)

                cur.execute("SELECT COUNT(*) AS v FROM fin_shareholders WHERE company_id=%s", (company_id,))
                data["shareholders"] = int((cur.fetchone() or {}).get("v", 0) or 0)

                cur.execute("SELECT COALESCE(SUM(total_amount),0) AS v FROM fin_dividends WHERE company_id=%s", (company_id,))
                data["declared_dividends"] = float((cur.fetchone() or {}).get("v", 0) or 0)

                cur.execute("SELECT COUNT(*) AS v FROM fin_gl_entries WHERE company_id=%s", (company_id,))
                data["gl_entries"] = int((cur.fetchone() or {}).get("v", 0) or 0)
            return data
        except Exception as e:
            logger.error("finance_dashboard failed: %s", e)
            return data


finance_store = FinanceManagementDataStore()
