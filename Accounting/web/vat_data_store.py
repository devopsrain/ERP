"""
VAT Data Store - PostgreSQL backend
"""

import logging
import uuid
import pandas as pd
from datetime import datetime, date
from typing import Dict, List, Any, Optional

from db import get_cursor, get_conn

logger = logging.getLogger(__name__)


class VATDataStore:
    """PostgreSQL-backed VAT data store for income, expenses, and capital."""

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # income
    # ------------------------------------------------------------------
    def add_income(self, data: dict) -> bool:
        cid = data.get('company_id', 'default')
        try:
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO vat_income
                       (income_id, company_id, contract_date, description,
                        category, gross_amount, vat_type, vat_rate, vat_amount,
                        net_amount, customer_name, customer_tin, invoice_number,
                        created_date, created_by, is_active)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (data.get('income_id') or str(uuid.uuid4()),
                     cid,
                     data.get('contract_date') or date.today(),
                     data.get('description', ''),
                     data.get('category', ''),
                     float(data.get('gross_amount', data.get('amount', 0))),
                     data.get('vat_type', 'standard'),
                     float(data.get('vat_rate', 15.0)),
                     float(data.get('vat_amount', 0)),
                     float(data.get('net_amount', 0)),
                     data.get('customer_name', ''),
                     data.get('customer_tin', ''),
                     data.get('invoice_number', ''),
                     datetime.utcnow(),
                     data.get('created_by', ''),
                     True)
                )
            return True
        except Exception as e:
            logger.error("add_income failed: %s", e)
            return False

    def get_income(self, company_id: str = None, tax_period: str = None) -> List[dict]:
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM vat_income WHERE company_id=%s "
                    "ORDER BY created_date DESC LIMIT 500",
                    (cid,)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_income failed: %s", e)
            return []

    def delete_income(self, record_id: str, company_id: str = None) -> bool:
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute(
                    "DELETE FROM vat_income WHERE income_id=%s AND company_id=%s",
                    (record_id, cid)
                )
            return True
        except Exception as e:
            logger.error("delete_income failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # expenses
    # ------------------------------------------------------------------
    def add_expense(self, data: dict) -> bool:
        cid = data.get('company_id', 'default')
        try:
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO vat_expenses
                       (expense_id, company_id, expense_date, description,
                        category, gross_amount, vat_type, vat_rate, vat_amount,
                        net_amount, supplier_name, supplier_tin, receipt_number,
                        created_date, created_by, is_active)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (data.get('expense_id') or str(uuid.uuid4()),
                     cid,
                     data.get('expense_date') or date.today(),
                     data.get('description', ''),
                     data.get('category', ''),
                     float(data.get('gross_amount', data.get('amount', 0))),
                     data.get('vat_type', 'standard'),
                     float(data.get('vat_rate', 15.0)),
                     float(data.get('vat_amount', 0)),
                     float(data.get('net_amount', 0)),
                     data.get('supplier_name', data.get('vendor_name', '')),
                     data.get('supplier_tin', ''),
                     data.get('receipt_number', data.get('invoice_number', '')),
                     datetime.utcnow(),
                     data.get('created_by', ''),
                     True)
                )
            return True
        except Exception as e:
            logger.error("add_expense failed: %s", e)
            return False

    def get_expenses(self, company_id: str = None, tax_period: str = None) -> List[dict]:
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM vat_expenses WHERE company_id=%s "
                    "ORDER BY created_date DESC LIMIT 500",
                    (cid,)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_expenses failed: %s", e)
            return []

    def delete_expense(self, record_id: str, company_id: str = None) -> bool:
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute(
                    "DELETE FROM vat_expenses WHERE expense_id=%s AND company_id=%s",
                    (record_id, cid)
                )
            return True
        except Exception as e:
            logger.error("delete_expense failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # capital
    # ------------------------------------------------------------------
    def add_capital(self, data: dict) -> bool:
        cid = data.get('company_id', 'default')
        try:
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO vat_capital
                       (capital_id, company_id, investment_date, description,
                        capital_type, amount, vat_type, vat_rate, vat_amount,
                        investor_name, investor_tin, created_date, created_by, is_active)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (data.get('capital_id') or str(uuid.uuid4()),
                     cid,
                     data.get('investment_date') or date.today(),
                     data.get('description', ''),
                     data.get('capital_type', data.get('asset_type', '')),
                     float(data.get('amount', 0)),
                     data.get('vat_type', 'standard'),
                     float(data.get('vat_rate', 15.0)),
                     float(data.get('vat_amount', 0)),
                     data.get('investor_name', ''),
                     data.get('investor_tin', ''),
                     datetime.utcnow(),
                     data.get('created_by', ''),
                     True)
                )
            return True
        except Exception as e:
            logger.error("add_capital failed: %s", e)
            return False

    def get_capital(self, company_id: str = None, tax_period: str = None) -> List[dict]:
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM vat_capital WHERE company_id=%s "
                    "ORDER BY created_date DESC LIMIT 500",
                    (cid,)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_capital failed: %s", e)
            return []

    def delete_capital(self, record_id: str, company_id: str = None) -> bool:
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute(
                    "DELETE FROM vat_capital WHERE capital_id=%s AND company_id=%s",
                    (record_id, cid)
                )
            return True
        except Exception as e:
            logger.error("delete_capital failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # methods required by models/vat_portal.py VATContextManager
    # ------------------------------------------------------------------
    def add_record(self, table_name: str, record_dict: dict) -> bool:
        """Generic insert called by VATContextManager for structured records."""
        allowed_tables = {'vat_income', 'vat_expenses', 'vat_capital'}
        if table_name not in allowed_tables:
            logger.warning("add_record: unknown table '%s'", table_name)
            return False
        data = {k: v for k, v in record_dict.items() if k and isinstance(k, str)}
        if not data:
            return False
        cols = ', '.join(data.keys())
        placeholders = ', '.join(['%s'] * len(data))
        try:
            with get_cursor() as cur:
                cur.execute(
                    f"INSERT INTO {table_name} ({cols}) VALUES ({placeholders})",
                    list(data.values())
                )
            return True
        except Exception as e:
            logger.error("add_record(%s) failed: %s", table_name, e)
            return False

    def get_company_records(self, table_name: str, company_id: str,
                             start_date=None, end_date=None) -> pd.DataFrame:
        """Return DataFrame of records for VATContextManager."""
        date_col_map = {
            'vat_income': 'contract_date',
            'vat_expenses': 'expense_date',
            'vat_capital': 'investment_date',
        }
        date_col = date_col_map.get(table_name)
        if not date_col:
            return pd.DataFrame()
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                if start_date and end_date:
                    cur.execute(
                        f"SELECT * FROM {table_name} WHERE company_id=%s "
                        f"AND {date_col}>=%s AND {date_col}<=%s ORDER BY {date_col} DESC",
                        (cid, start_date, end_date)
                    )
                elif start_date:
                    cur.execute(
                        f"SELECT * FROM {table_name} WHERE company_id=%s "
                        f"AND {date_col}>=%s ORDER BY {date_col} DESC",
                        (cid, start_date)
                    )
                else:
                    cur.execute(
                        f"SELECT * FROM {table_name} WHERE company_id=%s "
                        f"ORDER BY {date_col} DESC",
                        (cid,)
                    )
                rows = cur.fetchall()
            return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()
        except Exception as e:
            logger.error("get_company_records(%s) failed: %s", table_name, e)
            return pd.DataFrame()

    def get_statistics(self, company_id: str) -> dict:
        """Return row counts per VAT table for the dashboard."""
        cid = company_id or 'default'
        result = {'vat_income_count': 0, 'vat_expenses_count': 0, 'vat_capital_count': 0}
        try:
            with get_cursor() as cur:
                for key, table in [
                    ('vat_income_count', 'vat_income'),
                    ('vat_expenses_count', 'vat_expenses'),
                    ('vat_capital_count', 'vat_capital'),
                ]:
                    cur.execute(
                        f"SELECT COUNT(*) AS c FROM {table} WHERE company_id=%s", (cid,)
                    )
                    row = cur.fetchone()
                    result[key] = int(row['c']) if row else 0
        except Exception as e:
            logger.error("get_statistics failed: %s", e)
        return result

    # ------------------------------------------------------------------
    # summary
    # ------------------------------------------------------------------
    def get_vat_summary(self, company_id: str = None, tax_period: str = None) -> dict:
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                if tax_period:
                    cur.execute(
                        "SELECT COALESCE(SUM(vat_amount),0) FROM vat_income "
                        "WHERE company_id=%s AND tax_period=%s",
                        (cid, tax_period)
                    )
                else:
                    cur.execute(
                        "SELECT COALESCE(SUM(vat_amount),0) FROM vat_income WHERE company_id=%s",
                        (cid,)
                    )
                output_vat = float(cur.fetchone()['coalesce'])

                if tax_period:
                    cur.execute(
                        "SELECT COALESCE(SUM(vat_amount),0) FROM vat_expenses "
                        "WHERE company_id=%s AND tax_period=%s",
                        (cid, tax_period)
                    )
                else:
                    cur.execute(
                        "SELECT COALESCE(SUM(vat_amount),0) FROM vat_expenses WHERE company_id=%s",
                        (cid,)
                    )
                input_vat = float(cur.fetchone()['coalesce'])

                net = output_vat - input_vat
                return {
                    'output_vat': output_vat,
                    'input_vat': input_vat,
                    'net_vat': net,
                    'payable': max(0, net),
                    'refundable': max(0, -net),
                }
        except Exception as e:
            logger.error("get_vat_summary failed: %s", e)
            return {'output_vat': 0, 'input_vat': 0, 'net_vat': 0, 'payable': 0, 'refundable': 0}


# Singleton
vat_store = VATDataStore()
