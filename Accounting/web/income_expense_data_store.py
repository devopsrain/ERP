"""
Income and Expense Data Store - PostgreSQL backend
"""

import logging
import pandas as pd
from datetime import datetime, date
from typing import Dict, List, Any, Optional

from db import get_cursor, get_conn, get_tenant_cursor

logger = logging.getLogger(__name__)


class IncomeExpenseDataStore:
    """PostgreSQL-backed income and expense data store."""

    def __init__(self, data_dir=None):
        # data_dir kept for backward compat but not used
        pass

    # ------------------------------------------------------------------
    # Income
    # ------------------------------------------------------------------
    def add_income(self, data: dict) -> bool:
        cid = data.get('company_id', 'default')
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """INSERT INTO income_records
                       (company_id, date, category, description, amount,
                        payment_method, reference, created_by, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (cid,
                     data.get('date', str(date.today())),
                     data.get('category', ''),
                     data.get('description', ''),
                     float(data.get('amount', 0)),
                     data.get('payment_method', ''),
                     data.get('reference', ''),
                     data.get('created_by', ''),
                     datetime.utcnow().isoformat())
                )
            return True
        except Exception as e:
            logger.error("add_income failed: %s", e)
            return False

    def get_income(self, company_id: str = None, start_date: str = None,
                   end_date: str = None) -> List[dict]:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                sql = "SELECT * FROM income_records WHERE company_id=%s"
                params = [cid]
                if start_date:
                    sql += " AND date >= %s"
                    params.append(start_date)
                if end_date:
                    sql += " AND date <= %s"
                    params.append(end_date)
                sql += " ORDER BY date DESC LIMIT 1000"
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_income failed: %s", e)
            return []

    def get_income_df(self, company_id: str = None) -> pd.DataFrame:
        rows = self.get_income(company_id)
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def update_income(self, record_id: int, data: dict, company_id: str = None) -> bool:
        cid = company_id or data.get('company_id', 'default')
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """UPDATE income_records SET
                       date=%s, category=%s, description=%s, amount=%s,
                       payment_method=%s, reference=%s
                       WHERE id=%s AND company_id=%s""",
                    (data.get('date', str(date.today())),
                     data.get('category', ''),
                     data.get('description', ''),
                     float(data.get('amount', 0)),
                     data.get('payment_method', ''),
                     data.get('reference', ''),
                     record_id, cid)
                )
            return True
        except Exception as e:
            logger.error("update_income failed: %s", e)
            return False

    def delete_income(self, record_id: int, company_id: str = None) -> bool:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "DELETE FROM income_records WHERE id=%s AND company_id=%s",
                    (record_id, cid)
                )
            return True
        except Exception as e:
            logger.error("delete_income failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Expenses
    # ------------------------------------------------------------------
    def add_expense(self, data: dict) -> bool:
        cid = data.get('company_id', 'default')
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """INSERT INTO expense_records
                       (company_id, date, category, description, amount,
                        payment_method, reference, created_by, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (cid,
                     data.get('date', str(date.today())),
                     data.get('category', ''),
                     data.get('description', ''),
                     float(data.get('amount', 0)),
                     data.get('payment_method', ''),
                     data.get('reference', ''),
                     data.get('created_by', ''),
                     datetime.utcnow().isoformat())
                )
            return True
        except Exception as e:
            logger.error("add_expense failed: %s", e)
            return False

    def get_expenses(self, company_id: str = None, start_date: str = None,
                     end_date: str = None) -> List[dict]:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                sql = "SELECT * FROM expense_records WHERE company_id=%s"
                params = [cid]
                if start_date:
                    sql += " AND date >= %s"
                    params.append(start_date)
                if end_date:
                    sql += " AND date <= %s"
                    params.append(end_date)
                sql += " ORDER BY date DESC LIMIT 1000"
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_expenses failed: %s", e)
            return []

    def get_expenses_df(self, company_id: str = None) -> pd.DataFrame:
        rows = self.get_expenses(company_id)
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def update_expense(self, record_id: int, data: dict, company_id: str = None) -> bool:
        cid = company_id or data.get('company_id', 'default')
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """UPDATE expense_records SET
                       date=%s, category=%s, description=%s, amount=%s,
                       payment_method=%s, reference=%s
                       WHERE id=%s AND company_id=%s""",
                    (data.get('date', str(date.today())),
                     data.get('category', ''),
                     data.get('description', ''),
                     float(data.get('amount', 0)),
                     data.get('payment_method', ''),
                     data.get('reference', ''),
                     record_id, cid)
                )
            return True
        except Exception as e:
            logger.error("update_expense failed: %s", e)
            return False

    def delete_expense(self, record_id: int, company_id: str = None) -> bool:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "DELETE FROM expense_records WHERE id=%s AND company_id=%s",
                    (record_id, cid)
                )
            return True
        except Exception as e:
            logger.error("delete_expense failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    def get_summary(self, company_id: str = None, start_date: str = None,
                    end_date: str = None) -> dict:
        cid = company_id or 'default'
        inc = self.get_income(cid, start_date, end_date)
        exp = self.get_expenses(cid, start_date, end_date)
        total_income = sum(float(r.get('amount', 0)) for r in inc)
        total_expense = sum(float(r.get('amount', 0)) for r in exp)
        total_income_tax = sum(float(r.get('tax_amount', 0)) for r in inc)
        total_expense_tax = sum(float(r.get('tax_amount', 0)) for r in exp)
        return {
            'total_income': total_income,
            'total_expense': total_expense,
            'total_expenses': total_expense,
            'net_profit': total_income - total_expense,
            'income_count': len(inc),
            'expense_count': len(exp),
            'total_income_tax': total_income_tax,
            'total_expense_tax': total_expense_tax,
        }


    def get_summary_statistics(self, date_from=None, date_to=None) -> dict:
        """Alias for get_summary() matching the route's expected method name."""
        return self.get_summary(start_date=date_from, end_date=date_to)

    def get_all_income_records(self, company_id: str = None) -> list:
        """Return all income records as a list of dicts."""
        return self.get_income(company_id=company_id)

    def get_all_expense_records(self, company_id: str = None) -> list:
        """Return all expense records as a list of dicts."""
        return self.get_expenses(company_id=company_id)

    def get_date_range(self, company_id: str = None) -> dict:
        """Return the min/max date across all income and expense records."""
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT MIN(date) AS min_d, MAX(date) AS max_d "
                    "FROM (SELECT date FROM vat_income WHERE company_id=%s"
                    "      UNION ALL SELECT date FROM vat_expenses WHERE company_id=%s) t",
                    (cid, cid)
                )
                row = cur.fetchone()
            if row and row['min_d']:
                return {
                    'min_date': str(row['min_d']),
                    'max_date': str(row['max_d']),
                }
        except Exception:
            pass
        return {'min_date': None, 'max_date': None}

    # ------------------------------------------------------------------
    # Alias methods for route compatibility
    # ------------------------------------------------------------------
    def save_income_record(self, data: dict) -> bool:
        """Alias for add_income() - used by routes."""
        return self.add_income(data)

    def save_expense_record(self, data: dict) -> bool:
        """Alias for add_expense() - used by routes."""
        return self.add_expense(data)

    def export_to_excel(self, company_id: str = None) -> str:
        """Export all income and expense records to an Excel file."""
        import tempfile
        import os
        import pandas as pd
        
        cid = company_id or 'default'
        income_records = self.get_all_income_records(cid)
        expense_records = self.get_all_expense_records(cid)
        
        fd, filepath = tempfile.mkstemp(suffix='.xlsx')
        os.close(fd)
        
        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            income_df = pd.DataFrame(income_records) if income_records else pd.DataFrame(
                columns=['date', 'description', 'category', 'amount', 'tax_amount'])
            expense_df = pd.DataFrame(expense_records) if expense_records else pd.DataFrame(
                columns=['date', 'description', 'category', 'amount', 'tax_amount'])
            income_df.to_excel(writer, sheet_name='Income', index=False)
            expense_df.to_excel(writer, sheet_name='Expenses', index=False)
        
        return filepath


# Singleton instance
income_expense_store = IncomeExpenseDataStore()
