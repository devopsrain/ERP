"""
Chart of Accounts Data Store - PostgreSQL backend
"""

import uuid
import logging
import pandas as pd
from datetime import datetime, date
from typing import Dict, List, Any, Optional

from db import get_cursor, get_conn, get_tenant_cursor

logger = logging.getLogger(__name__)

ETHIOPIAN_DEFAULT_ACCOUNTS = [
    ('1000','Cash and Cash Equivalents','Asset','Current Asset','','',True,'debit',0.0),
    ('1001','Cash on Hand','Asset','Current Asset','1000','',True,'debit',0.0),
    ('1010','Bank - Commercial Bank of Ethiopia','Asset','Current Asset','1000','',True,'debit',0.0),
    ('1100','Accounts Receivable','Asset','Current Asset','','',True,'debit',0.0),
    ('1200','Inventory','Asset','Current Asset','','',True,'debit',0.0),
    ('1500','Property Plant and Equipment','Asset','Fixed Asset','','',True,'debit',0.0),
    ('2000','Accounts Payable','Liability','Current Liability','','',True,'credit',0.0),
    ('2100','VAT Payable','Liability','Current Liability','','',True,'credit',0.0),
    ('2200','Income Tax Payable','Liability','Current Liability','','',True,'credit',0.0),
    ('2300','Pension Payable','Liability','Current Liability','','',True,'credit',0.0),
    ('3000','Share Capital','Equity','Capital','','',True,'credit',0.0),
    ('3100','Retained Earnings','Equity','Capital','','',True,'credit',0.0),
    ('4000','Sales Revenue','Revenue','Operating Revenue','','',True,'credit',0.0),
    ('4100','Service Revenue','Revenue','Operating Revenue','','',True,'credit',0.0),
    ('5000','Cost of Goods Sold','Expense','Cost of Sales','','',True,'debit',0.0),
    ('6000','Salaries and Wages','Expense','Operating Expense','','',True,'debit',0.0),
    ('6100','Rent Expense','Expense','Operating Expense','','',True,'debit',0.0),
    ('6200','Utilities','Expense','Operating Expense','','',True,'debit',0.0),
    ('6300','Depreciation Expense','Expense','Operating Expense','','',True,'debit',0.0),
    ('7000','Income Tax Expense','Expense','Tax Expense','','',True,'debit',0.0),
]


class ChartOfAccountsDataStore:
    """PostgreSQL-backed chart of accounts."""

    def __init__(self):
        pass

    def read_all_accounts(self, company_id: str = None) -> pd.DataFrame:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM chart_of_accounts WHERE company_id=%s AND is_active=TRUE "
                    "ORDER BY account_code",
                    (cid,)
                )
                rows = cur.fetchall()
                if not rows:
                    self._load_default_accounts(cid)
                    cur.execute(
                        "SELECT * FROM chart_of_accounts WHERE company_id=%s AND is_active=TRUE "
                        "ORDER BY account_code",
                        (cid,)
                    )
                    rows = cur.fetchall()
                return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()
        except Exception as e:
            logger.error("read_all_accounts failed: %s", e)
            return pd.DataFrame()

    def get_account_by_code(self, account_code: str, company_id: str = None) -> Optional[dict]:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM chart_of_accounts WHERE account_code=%s AND company_id=%s",
                    (account_code, cid)
                )
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_account_by_code failed: %s", e)
            return None

    def save_account(self, account_data: dict) -> bool:
        cid = account_data.get('company_id', 'default')
        today = str(date.today())
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM chart_of_accounts WHERE account_code=%s AND company_id=%s",
                    (account_data['account_code'], cid)
                )
                exists = cur.fetchone()
                if exists:
                    cur.execute(
                        """UPDATE chart_of_accounts SET
                           account_name=%s, account_type=%s, account_subtype=%s,
                           parent_account=%s, description=%s, is_active=%s,
                           normal_balance=%s, current_balance=%s, modified_date=%s
                           WHERE account_code=%s AND company_id=%s""",
                        (account_data.get('account_name',''),
                         account_data.get('account_type',''),
                         account_data.get('account_subtype',''),
                         account_data.get('parent_account',''),
                         account_data.get('description',''),
                         bool(account_data.get('is_active',True)),
                         account_data.get('normal_balance','debit'),
                         float(account_data.get('current_balance',0)),
                         today, account_data['account_code'], cid)
                    )
                else:
                    cur.execute(
                        """INSERT INTO chart_of_accounts
                           (account_code,company_id,account_name,account_type,account_subtype,
                            parent_account,description,is_active,normal_balance,
                            current_balance,created_date,modified_date)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (account_data['account_code'], cid,
                         account_data.get('account_name',''),
                         account_data.get('account_type',''),
                         account_data.get('account_subtype',''),
                         account_data.get('parent_account',''),
                         account_data.get('description',''),
                         bool(account_data.get('is_active',True)),
                         account_data.get('normal_balance','debit'),
                         float(account_data.get('current_balance',0)),
                         today, today)
                    )
            return True
        except Exception as e:
            logger.error("save_account failed: %s", e)
            return False

    def bulk_import_accounts(self, accounts_data: List[Dict], company_id: str = None) -> dict:
        result = {'success': False, 'imported': 0, 'errors': []}
        cid = company_id or 'default'
        for a in accounts_data:
            a['company_id'] = cid
            try:
                self.save_account(a)
                result['imported'] += 1
            except Exception as e:
                result['errors'].append(str(e))
        result['success'] = True
        return result

    def _load_default_accounts(self, company_id: str = 'default'):
        today = str(date.today())
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for code, name, atype, subtype, parent, desc, active, balance, curr_bal in ETHIOPIAN_DEFAULT_ACCOUNTS:
                        cur.execute(
                            """INSERT INTO chart_of_accounts
                               (account_code,company_id,account_name,account_type,account_subtype,
                                parent_account,description,is_active,normal_balance,
                                current_balance,created_date,modified_date)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (account_code, company_id) DO NOTHING""",
                            (code, company_id, name, atype, subtype, parent, desc,
                             active, balance, curr_bal, today, today)
                        )
        except Exception as e:
            logger.error("_load_default_accounts failed: %s", e)

    def export_to_excel(self, company_id: str = None, filename: str = None) -> str:
        from pathlib import Path
        data_dir = Path(__file__).parent / "data"
        data_dir.mkdir(exist_ok=True)
        if filename is None:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'chart_of_accounts_{ts}.xlsx'
        filepath = str(data_dir / filename)
        df = self.read_all_accounts(company_id)
        df.to_excel(filepath, index=False)
        return filepath

    def import_from_excel(self, filepath: str, company_id: str = None) -> dict:
        result = {'success': False, 'imported': 0, 'errors': []}
        try:
            df = pd.read_excel(filepath)
            for _, row in df.iterrows():
                acc = row.to_dict()
                if company_id:
                    acc['company_id'] = company_id
                try:
                    self.save_account(acc)
                    result['imported'] += 1
                except Exception as e:
                    result['errors'].append(str(e))
            result['success'] = True
        except Exception as e:
            result['errors'].append(str(e))
        return result


# Singleton instance
chart_store = ChartOfAccountsDataStore()
