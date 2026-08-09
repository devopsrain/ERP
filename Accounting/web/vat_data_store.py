"""
VAT Data Store - PostgreSQL backend
"""

import logging
import uuid
import pandas as pd
from datetime import datetime, date
from typing import Dict, List, Any, Optional

from db import get_cursor, get_conn, get_tenant_cursor

logger = logging.getLogger(__name__)


class VATDataStore:
    """PostgreSQL-backed VAT data store for income, expenses, and capital."""

    def __init__(self):
        self._columns_cache: Dict[str, set] = {}

    def _table_columns(self, table_name: str) -> set:
        """Actual columns of a table (cached). Lets writes/filters tolerate
        schema drift — e.g. a new column whose ALTER hasn't applied yet."""
        cols = self._columns_cache.get(table_name)
        if cols:
            return cols
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name=%s",
                    (table_name,)
                )
                cols = {r['column_name'] for r in cur.fetchall()}
        except Exception as e:
            logger.error("_table_columns(%s) failed: %s", table_name, e)
            cols = set()
        if cols:
            self._columns_cache[table_name] = cols
        return cols

    # ------------------------------------------------------------------
    # income
    # ------------------------------------------------------------------
    def add_income(self, data: dict) -> bool:
        cid = data.get('company_id', 'default')
        result = self._add_income_impl(data, cid)
        if result:
            self._invalidate_cache(cid)
        return result

    def _add_income_impl(self, data: dict, cid: str) -> bool:
        try:
            with get_tenant_cursor(cid) as cur:
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

    def _invalidate_cache(self, company_id: str = 'default') -> None:
        """Invalidate all VAT caches for a company (called after any mutation)."""
        from extensions import cache
        for key in (f"vat_income:{company_id}", f"vat_expenses:{company_id}",
                    f"vat_capital:{company_id}", f"dashboard_stats:{company_id}"):
            cache.delete(key)

    def get_income(self, company_id: str = None, tax_period: str = None) -> List[dict]:
        from extensions import cache
        cid = company_id or 'default'
        ck  = f"vat_income:{cid}"
        cached = cache.get(ck)
        if cached is not None:
            return cached
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM vat_income WHERE company_id=%s "
                    "ORDER BY created_date DESC LIMIT 500",
                    (cid,)
                )
                result = [dict(r) for r in cur.fetchall()]
                cache.set(ck, result, timeout=120)
                return result
        except Exception as e:
            logger.error("get_income failed: %s", e)
            return []

    def get_income_record(self, company_id: str, income_id: str) -> Optional[dict]:
        """Fetch a single income row as a plain dict (or None if not found)."""
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM vat_income WHERE income_id=%s AND company_id=%s",
                    (income_id, cid)
                )
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_income_record failed: %s", e)
            return None

    def update_income_record(self, company_id: str, income_id: str, updates: dict) -> bool:
        """Update a single income row. Unknown columns are dropped (schema drift)."""
        cid = company_id or 'default'
        data = {k: v for k, v in updates.items() if k and isinstance(k, str)}
        data.pop('income_id', None)
        data.pop('company_id', None)
        available = self._table_columns('vat_income')
        if available:
            unknown = [k for k in data if k not in available]
            if unknown:
                logger.warning("update_income_record: dropping unknown columns %s", unknown)
                data = {k: v for k, v in data.items() if k in available}
        if not data:
            return False
        sets = ', '.join(f"{k}=%s" for k in data)
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    f"UPDATE vat_income SET {sets} WHERE income_id=%s AND company_id=%s",
                    list(data.values()) + [income_id, cid]
                )
                updated = cur.rowcount > 0
            if updated:
                self._invalidate_cache(cid)
            return updated
        except Exception as e:
            logger.error("update_income_record failed: %s", e)
            return False

    def delete_income(self, record_id: str, company_id: str = None) -> bool:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "DELETE FROM vat_income WHERE income_id=%s AND company_id=%s",
                    (record_id, cid)
                )
            self._invalidate_cache(cid)
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
            with get_tenant_cursor(cid) as cur:
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
            self._invalidate_cache(cid)
            return True
        except Exception as e:
            logger.error("add_expense failed: %s", e)
            return False

    def get_expenses(self, company_id: str = None, tax_period: str = None) -> List[dict]:
        from extensions import cache
        cid = company_id or 'default'
        ck  = f"vat_expenses:{cid}"
        cached = cache.get(ck)
        if cached is not None:
            return cached
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM vat_expenses WHERE company_id=%s "
                    "ORDER BY created_date DESC LIMIT 500",
                    (cid,)
                )
                result = [dict(r) for r in cur.fetchall()]
                cache.set(ck, result, timeout=120)
                return result
        except Exception as e:
            logger.error("get_expenses failed: %s", e)
            return []

    def delete_expense(self, record_id: str, company_id: str = None) -> bool:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "DELETE FROM vat_expenses WHERE expense_id=%s AND company_id=%s",
                    (record_id, cid)
                )
            self._invalidate_cache(cid)
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
            with get_tenant_cursor(cid) as cur:
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
            self._invalidate_cache(cid)
            return True
        except Exception as e:
            logger.error("add_capital failed: %s", e)
            return False

    def get_capital(self, company_id: str = None, tax_period: str = None) -> List[dict]:
        from extensions import cache
        cid = company_id or 'default'
        ck  = f"vat_capital:{cid}"
        cached = cache.get(ck)
        if cached is not None:
            return cached
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM vat_capital WHERE company_id=%s "
                    "ORDER BY created_date DESC LIMIT 500",
                    (cid,)
                )
                result = [dict(r) for r in cur.fetchall()]
                cache.set(ck, result, timeout=120)
                return result
        except Exception as e:
            logger.error("get_capital failed: %s", e)
            return []

    def delete_capital(self, record_id: str, company_id: str = None) -> bool:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "DELETE FROM vat_capital WHERE capital_id=%s AND company_id=%s",
                    (record_id, cid)
                )
            self._invalidate_cache(cid)
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
        # Drop keys the live table doesn't have — an INSERT naming a missing
        # column fails whole, which silently lost records after schema drift.
        available = self._table_columns(table_name)
        if available:
            unknown = [k for k in data if k not in available]
            if unknown:
                logger.warning("add_record(%s): dropping unknown columns %s", table_name, unknown)
                data = {k: v for k, v in data.items() if k in available}
        cols = ', '.join(data.keys())
        placeholders = ', '.join(['%s'] * len(data))
        cid = str(data.get('company_id', 'default'))
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    f"INSERT INTO {table_name} ({cols}) VALUES ({placeholders})",
                    list(data.values())
                )
            self._invalidate_cache(cid)
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
        # vat_income filters on the date revenue was RECEIVED (payment /
        # income date), not the agreement date — but only once the column
        # actually exists; otherwise fall back so queries never break.
        if table_name == 'vat_income' and 'income_date' in self._table_columns('vat_income'):
            date_col = 'COALESCE(income_date, contract_date)'
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
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
            with get_tenant_cursor(cid) as cur:
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
            with get_tenant_cursor(cid) as cur:
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
