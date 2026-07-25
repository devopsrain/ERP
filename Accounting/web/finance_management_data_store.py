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

                        CREATE TABLE IF NOT EXISTS fin_cost_centers (
                            id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            code VARCHAR(50) NOT NULL,
                            name VARCHAR(255) NOT NULL,
                            budget_amount NUMERIC(15,2) DEFAULT 0,
                            is_active BOOLEAN DEFAULT TRUE,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS fin_receivables (
                            id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            party VARCHAR(255) NOT NULL,
                            description TEXT DEFAULT '',
                            amount NUMERIC(15,2) DEFAULT 0,
                            due_date DATE,
                            status VARCHAR(20) DEFAULT 'open',
                            paid_amount NUMERIC(15,2) DEFAULT 0,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS fin_payables (
                            id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            party VARCHAR(255) NOT NULL,
                            description TEXT DEFAULT '',
                            amount NUMERIC(15,2) DEFAULT 0,
                            due_date DATE,
                            status VARCHAR(20) DEFAULT 'open',
                            paid_amount NUMERIC(15,2) DEFAULT 0,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE INDEX IF NOT EXISTS idx_fin_cost_centers_company
                            ON fin_cost_centers(company_id, code);
                        CREATE INDEX IF NOT EXISTS idx_fin_receivables_company
                            ON fin_receivables(company_id, status);
                        CREATE INDEX IF NOT EXISTS idx_fin_payables_company
                            ON fin_payables(company_id, status);

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

    # ── Financial statements (chart_of_accounts / journal aggregates) ──

    _TYPE_INCOME = ("income", "revenue")

    def balance_sheet(self, company_id: str = "default") -> Dict[str, Any]:
        """Assets / Liabilities / Equity from chart_of_accounts balances."""
        report: Dict[str, Any] = {
            "assets": [], "liabilities": [], "equity": [],
            "total_assets": 0.0, "total_liabilities": 0.0, "total_equity": 0.0,
            "balanced": True, "difference": 0.0,
        }
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    """
                    SELECT account_code, account_name, account_type,
                           COALESCE(account_subtype, '') AS account_subtype,
                           COALESCE(current_balance, 0) AS current_balance
                    FROM chart_of_accounts
                    WHERE company_id = %s AND is_active = TRUE
                      AND LOWER(account_type) IN ('asset', 'liability', 'equity')
                    ORDER BY account_code
                    """,
                    (company_id,),
                )
                for row in cur.fetchall():
                    acc = dict(row)
                    acc["current_balance"] = float(acc.get("current_balance") or 0)
                    kind = (acc.get("account_type") or "").lower()
                    if kind == "asset":
                        report["assets"].append(acc)
                        report["total_assets"] += acc["current_balance"]
                    elif kind == "liability":
                        report["liabilities"].append(acc)
                        report["total_liabilities"] += acc["current_balance"]
                    else:
                        report["equity"].append(acc)
                        report["total_equity"] += acc["current_balance"]
            report["difference"] = report["total_assets"] - (
                report["total_liabilities"] + report["total_equity"]
            )
            report["balanced"] = abs(report["difference"]) < 0.01
            return report
        except Exception as e:
            logger.error("balance_sheet failed: %s", e)
            return report

    def profit_loss(self, company_id: str = "default") -> Dict[str, Any]:
        """Income vs expenses grouped by account subtype (category)."""
        report: Dict[str, Any] = {
            "income_by_category": {}, "expense_by_category": {},
            "income_accounts": [], "expense_accounts": [],
            "total_income": 0.0, "total_expenses": 0.0, "net_profit": 0.0,
        }
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    """
                    SELECT account_code, account_name, account_type,
                           COALESCE(NULLIF(account_subtype, ''), 'Uncategorized') AS category,
                           COALESCE(current_balance, 0) AS current_balance
                    FROM chart_of_accounts
                    WHERE company_id = %s AND is_active = TRUE
                      AND LOWER(account_type) IN ('income', 'revenue', 'expense')
                    ORDER BY account_code
                    """,
                    (company_id,),
                )
                for row in cur.fetchall():
                    acc = dict(row)
                    bal = float(acc.get("current_balance") or 0)
                    acc["current_balance"] = bal
                    cat = acc["category"]
                    if (acc.get("account_type") or "").lower() in self._TYPE_INCOME:
                        report["income_accounts"].append(acc)
                        report["income_by_category"][cat] = \
                            report["income_by_category"].get(cat, 0.0) + bal
                        report["total_income"] += bal
                    else:
                        report["expense_accounts"].append(acc)
                        report["expense_by_category"][cat] = \
                            report["expense_by_category"].get(cat, 0.0) + bal
                        report["total_expenses"] += bal
            report["net_profit"] = report["total_income"] - report["total_expenses"]
            return report
        except Exception as e:
            logger.error("profit_loss failed: %s", e)
            return report

    def _cash_account_codes(self, cur, company_id: str) -> List[str]:
        cur.execute(
            """
            SELECT account_code FROM chart_of_accounts
            WHERE company_id = %s AND is_active = TRUE
              AND LOWER(account_type) = 'asset'
              AND (LOWER(account_name) LIKE '%%cash%%'
                   OR LOWER(account_name) LIKE '%%bank%%')
            """,
            (company_id,),
        )
        return [r["account_code"] for r in cur.fetchall()]

    def cash_flow(self, year: int, company_id: str = "default") -> Dict[str, Any]:
        """
        Monthly cash inflows/outflows for a year from journal entry lines.

        Primary approach: debits/credits against cash-type accounts
        (asset accounts whose name contains 'cash' or 'bank').
        Fallback when no cash accounts exist: monthly income (credits on
        income/revenue accounts) vs expenses (debits on expense accounts).
        """
        report: Dict[str, Any] = {
            "year": year, "months": [], "approach": "cash_accounts",
            "total_inflow": 0.0, "total_outflow": 0.0, "net_cash_flow": 0.0,
        }
        monthly = {m: {"inflow": 0.0, "outflow": 0.0} for m in range(1, 13)}
        try:
            with get_tenant_cursor(company_id) as cur:
                cash_codes = self._cash_account_codes(cur, company_id)
                if cash_codes:
                    cur.execute(
                        """
                        SELECT EXTRACT(MONTH FROM je.entry_date)::int AS month,
                               COALESCE(SUM(jel.debit_amount), 0)  AS inflow,
                               COALESCE(SUM(jel.credit_amount), 0) AS outflow
                        FROM journal_entry_lines jel
                        JOIN journal_entries je ON jel.entry_id = je.entry_id
                        WHERE je.company_id = %s AND je.is_active = TRUE
                          AND jel.is_active = TRUE
                          AND EXTRACT(YEAR FROM je.entry_date) = %s
                          AND jel.account_code = ANY(%s)
                        GROUP BY 1 ORDER BY 1
                        """,
                        (company_id, year, cash_codes),
                    )
                else:
                    report["approach"] = "income_vs_expense"
                    cur.execute(
                        """
                        SELECT EXTRACT(MONTH FROM je.entry_date)::int AS month,
                               COALESCE(SUM(CASE WHEN LOWER(coa.account_type) IN ('income','revenue')
                                                 THEN jel.credit_amount - jel.debit_amount
                                                 ELSE 0 END), 0) AS inflow,
                               COALESCE(SUM(CASE WHEN LOWER(coa.account_type) = 'expense'
                                                 THEN jel.debit_amount - jel.credit_amount
                                                 ELSE 0 END), 0) AS outflow
                        FROM journal_entry_lines jel
                        JOIN journal_entries je ON jel.entry_id = je.entry_id
                        JOIN chart_of_accounts coa
                          ON coa.account_code = jel.account_code
                         AND coa.company_id = je.company_id
                        WHERE je.company_id = %s AND je.is_active = TRUE
                          AND jel.is_active = TRUE
                          AND EXTRACT(YEAR FROM je.entry_date) = %s
                        GROUP BY 1 ORDER BY 1
                        """,
                        (company_id, year),
                    )
                for row in cur.fetchall():
                    m = int(row["month"])
                    if m in monthly:
                        monthly[m]["inflow"] = float(row["inflow"] or 0)
                        monthly[m]["outflow"] = float(row["outflow"] or 0)
        except Exception as e:
            logger.error("cash_flow failed: %s", e)
        month_names = ["January", "February", "March", "April", "May", "June",
                       "July", "August", "September", "October", "November", "December"]
        for m in range(1, 13):
            inflow, outflow = monthly[m]["inflow"], monthly[m]["outflow"]
            report["months"].append({
                "month": m, "month_name": month_names[m - 1],
                "inflow": inflow, "outflow": outflow, "net": inflow - outflow,
            })
            report["total_inflow"] += inflow
            report["total_outflow"] += outflow
        report["net_cash_flow"] = report["total_inflow"] - report["total_outflow"]
        return report

    # ── Cost centers ──────────────────────────────────────────────

    def list_cost_centers(self, company_id: str = "default",
                          include_inactive: bool = True) -> List[Dict[str, Any]]:
        """Cost centers with actual spend from fin_gl_entries.cost_center."""
        try:
            with get_tenant_cursor(company_id) as cur:
                active_filter = "" if include_inactive else "AND cc.is_active = TRUE"
                cur.execute(
                    f"""
                    SELECT cc.id, cc.company_id, cc.code, cc.name,
                           COALESCE(cc.budget_amount, 0) AS budget_amount,
                           cc.is_active, cc.created_at,
                           COALESCE(SUM(CASE WHEN g.entry_type = 'debit'
                                             THEN g.amount ELSE -g.amount END), 0) AS spend
                    FROM fin_cost_centers cc
                    LEFT JOIN fin_gl_entries g
                      ON g.company_id = cc.company_id AND g.cost_center = cc.code
                    WHERE cc.company_id = %s {active_filter}
                    GROUP BY cc.id, cc.company_id, cc.code, cc.name,
                             cc.budget_amount, cc.is_active, cc.created_at
                    ORDER BY cc.code
                    """,
                    (company_id,),
                )
                rows = []
                for r in cur.fetchall():
                    row = dict(r)
                    row["budget_amount"] = float(row.get("budget_amount") or 0)
                    row["spend"] = float(row.get("spend") or 0)
                    row["variance"] = row["budget_amount"] - row["spend"]
                    rows.append(row)
                return rows
        except Exception as e:
            logger.error("list_cost_centers failed: %s", e)
            return []

    def get_cost_center(self, cc_id: str, company_id: str = "default") -> Optional[Dict[str, Any]]:
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    "SELECT * FROM fin_cost_centers WHERE id = %s AND company_id = %s",
                    (cc_id, company_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_cost_center failed: %s", e)
            return None

    def create_cost_center(self, data: Dict[str, Any]) -> Optional[str]:
        cc_id = data.get("id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO fin_cost_centers (id, company_id, code, name, budget_amount, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        cc_id, cid,
                        data.get("code", ""),
                        data.get("name", ""),
                        float(data.get("budget_amount", 0) or 0),
                        bool(data.get("is_active", True)),
                    ),
                )
                return cc_id
        except Exception as e:
            logger.error("create_cost_center failed: %s", e)
            return None

    def update_cost_center(self, cc_id: str, data: Dict[str, Any],
                           company_id: str = "default") -> bool:
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    """
                    UPDATE fin_cost_centers
                    SET code = %s, name = %s, budget_amount = %s, is_active = %s
                    WHERE id = %s AND company_id = %s
                    """,
                    (
                        data.get("code", ""),
                        data.get("name", ""),
                        float(data.get("budget_amount", 0) or 0),
                        bool(data.get("is_active", True)),
                        cc_id, company_id,
                    ),
                )
                return cur.rowcount > 0
        except Exception as e:
            logger.error("update_cost_center failed: %s", e)
            return False

    # ── AR / AP registers (fin_receivables / fin_payables) ───────

    _REGISTER_TABLES = {"receivable": "fin_receivables", "payable": "fin_payables"}

    @staticmethod
    def _aging_bucket(due_date, today) -> str:
        if not due_date or due_date >= today:
            return "current"
        days = (today - due_date).days
        if days <= 30:
            return "1-30"
        if days <= 60:
            return "31-60"
        if days <= 90:
            return "61-90"
        return "90+"

    def list_register(self, kind: str, company_id: str = "default") -> Dict[str, Any]:
        """List AR or AP records with outstanding amounts and aging buckets."""
        table = self._REGISTER_TABLES[kind]
        today = date.today()
        buckets = {"current": 0.0, "1-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
        result: Dict[str, Any] = {
            "records": [], "aging": buckets,
            "total_amount": 0.0, "total_paid": 0.0, "total_outstanding": 0.0,
        }
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    f"""
                    SELECT * FROM {table}
                    WHERE company_id = %s
                    ORDER BY due_date NULLS LAST, created_at DESC
                    """,
                    (company_id,),
                )
                for r in cur.fetchall():
                    row = dict(r)
                    amount = float(row.get("amount") or 0)
                    paid = float(row.get("paid_amount") or 0)
                    outstanding = amount - paid
                    row["amount"], row["paid_amount"] = amount, paid
                    row["outstanding"] = outstanding
                    row["aging_bucket"] = self._aging_bucket(row.get("due_date"), today)
                    result["records"].append(row)
                    result["total_amount"] += amount
                    result["total_paid"] += paid
                    if row.get("status") != "paid" and outstanding > 0:
                        result["total_outstanding"] += outstanding
                        buckets[row["aging_bucket"]] += outstanding
            return result
        except Exception as e:
            logger.error("list_register(%s) failed: %s", kind, e)
            return result

    def create_register_record(self, kind: str, data: Dict[str, Any]) -> Optional[str]:
        table = self._REGISTER_TABLES[kind]
        rec_id = data.get("id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    f"""
                    INSERT INTO {table}
                    (id, company_id, party, description, amount, due_date, status, paid_amount)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        rec_id, cid,
                        data.get("party", ""),
                        data.get("description", ""),
                        float(data.get("amount", 0) or 0),
                        data.get("due_date"),
                        data.get("status", "open"),
                        float(data.get("paid_amount", 0) or 0),
                    ),
                )
                return rec_id
        except Exception as e:
            logger.error("create_register_record(%s) failed: %s", kind, e)
            return None

    def record_register_payment(self, kind: str, rec_id: str, payment: float,
                                company_id: str = "default") -> bool:
        """Apply a payment; status becomes paid/partial/open accordingly."""
        table = self._REGISTER_TABLES[kind]
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    f"SELECT amount, paid_amount FROM {table} WHERE id = %s AND company_id = %s",
                    (rec_id, company_id),
                )
                row = cur.fetchone()
                if not row:
                    return False
                amount = float(row["amount"] or 0)
                new_paid = float(row["paid_amount"] or 0) + float(payment or 0)
                if new_paid >= amount - 0.005:
                    status = "paid"
                elif new_paid > 0:
                    status = "partial"
                else:
                    status = "open"
                cur.execute(
                    f"UPDATE {table} SET paid_amount = %s, status = %s "
                    f"WHERE id = %s AND company_id = %s",
                    (new_paid, status, rec_id, company_id),
                )
                return cur.rowcount > 0
        except Exception as e:
            logger.error("record_register_payment(%s) failed: %s", kind, e)
            return False

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
