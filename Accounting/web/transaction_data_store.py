"""
Transaction Data Store - PostgreSQL backend
"""

import logging
import pandas as pd
from datetime import datetime, date
from typing import Dict, List, Any, Optional

from db import get_cursor, get_conn, get_tenant_cursor

logger = logging.getLogger(__name__)


class TransactionDataStore:
    """PostgreSQL-backed transaction store."""

    def __init__(self, data_dir=None):
        pass

    # ------------------------------------------------------------------
    # Transactions
    # ------------------------------------------------------------------
    def add_transaction(self, data: dict) -> bool:
        cid = data.get('company_id', 'default')
        try:
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO transactions
                       (company_id, date, description, debit_account, credit_account,
                        amount, reference, category, status, created_by, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (cid,
                     data.get('date', str(date.today())),
                     data.get('description', ''),
                     data.get('debit_account', ''),
                     data.get('credit_account', ''),
                     float(data.get('amount', 0)),
                     data.get('reference', ''),
                     data.get('category', ''),
                     data.get('status', 'active'),
                     data.get('created_by', ''),
                     datetime.utcnow().isoformat())
                )
            return True
        except Exception as e:
            logger.error("add_transaction failed: %s", e)
            return False

    def get_transactions(self, company_id: str = None, start_date: str = None,
                         end_date: str = None) -> pd.DataFrame:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                sql = "SELECT * FROM transactions WHERE company_id=%s"
                params = [cid]
                if start_date:
                    sql += " AND date >= %s"
                    params.append(start_date)
                if end_date:
                    sql += " AND date <= %s"
                    params.append(end_date)
                sql += " ORDER BY date DESC LIMIT 1000"
                cur.execute(sql, params)
                rows = cur.fetchall()
                return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()
        except Exception as e:
            logger.error("get_transactions failed: %s", e)
            return pd.DataFrame()

    def delete_transaction(self, record_id: int, company_id: str = None) -> bool:
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute(
                    "DELETE FROM transactions WHERE id=%s AND company_id=%s",
                    (record_id, cid)
                )
            return True
        except Exception as e:
            logger.error("delete_transaction failed: %s", e)
            return False

    def bulk_import(self, records: List[Dict], company_id: str = None) -> dict:
        result = {'imported': 0, 'errors': []}
        cid = company_id or 'default'
        imported_at = datetime.utcnow().isoformat()
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for r in records:
                        r['company_id'] = cid
                        try:
                            cur.execute(
                                """INSERT INTO transactions
                                   (company_id, date, description, debit_account,
                                    credit_account, amount, reference, category,
                                    status, created_by, created_at)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                (cid,
                                 r.get('date', str(date.today())),
                                 r.get('description', ''),
                                 r.get('debit_account', ''),
                                 r.get('credit_account', ''),
                                 float(r.get('amount', 0)),
                                 r.get('reference', ''),
                                 r.get('category', ''),
                                 'active',
                                 r.get('created_by', ''),
                                 imported_at)
                            )
                            result['imported'] += 1
                        except Exception as e:
                            result['errors'].append(str(e))
                    # log import history
                    cur.execute(
                        """INSERT INTO transaction_import_history
                           (company_id, imported_at, record_count, status)
                           VALUES (%s,%s,%s,%s)""",
                        (cid, imported_at, result['imported'], 'completed')
                    )
        except Exception as e:
            result['errors'].append(str(e))
        return result

    # ------------------------------------------------------------------
    # Flagged accounts
    # ------------------------------------------------------------------
    def flag_account(self, data: dict) -> bool:
        cid = data.get('company_id', 'default')
        try:
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO flagged_accounts
                       (company_id, account_code, account_name, reason,
                        flagged_by, flagged_at)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (company_id, account_code) DO UPDATE
                       SET reason=%s, flagged_by=%s, flagged_at=%s""",
                    (cid,
                     data.get('account_code', ''),
                     data.get('account_name', ''),
                     data.get('reason', ''),
                     data.get('flagged_by', ''),
                     datetime.utcnow().isoformat(),
                     data.get('reason', ''),
                     data.get('flagged_by', ''),
                     datetime.utcnow().isoformat())
                )
            return True
        except Exception as e:
            logger.error("flag_account failed: %s", e)
            return False

    def get_flagged_accounts(self, company_id: str = None) -> pd.DataFrame:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM flagged_accounts WHERE company_id=%s ORDER BY flagged_at DESC",
                    (cid,)
                )
                rows = cur.fetchall()
                return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()
        except Exception as e:
            logger.error("get_flagged_accounts failed: %s", e)
            return pd.DataFrame()

    def unflag_account(self, account_code: str, company_id: str = None) -> bool:
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute(
                    "DELETE FROM flagged_accounts WHERE company_id=%s AND account_code=%s",
                    (cid, account_code)
                )
            return True
        except Exception as e:
            logger.error("unflag_account failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Import history
    # ------------------------------------------------------------------
    def get_import_history(self, company_id: str = None) -> List[dict]:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM transaction_import_history WHERE company_id=%s "
                    "ORDER BY imported_at DESC LIMIT 100",
                    (cid,)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_import_history failed: %s", e)
            return []


    def get_summary_statistics(self, company_id: str = None) -> dict:
        """Return summary counts and totals for the transaction dashboard."""
        try:
            df = self.get_transactions(company_id=company_id)
            flagged = self.get_flagged_accounts(company_id=company_id)
            flagged_count = 0 if flagged is None or (hasattr(flagged, 'empty') and flagged.empty) else len(flagged)
            if df is None or (hasattr(df, 'empty') and df.empty):
                total, debit, credit = 0, 0.0, 0.0
            else:
                total = len(df)
                debit  = float(df.loc[df['amount'] > 0, 'amount'].sum()) if 'amount' in df.columns else 0.0
                credit = float(df.loc[df['amount'] < 0, 'amount'].abs().sum()) if 'amount' in df.columns else 0.0
            return {
                'total_transactions':    total,
                'flagged_count':         flagged_count,
                'individual_name_count': 0,
                'pending_review':        0,
                'total_debit':           debit,
                'total_credit':          credit,
                'net_balance':           debit - credit,
            }
        except Exception as e:
            logger.error("get_summary_statistics failed: %s", e)
            return {
                'total_transactions': 0, 'flagged_count': 0,
                'individual_name_count': 0, 'pending_review': 0,
                'total_debit': 0.0, 'total_credit': 0.0, 'net_balance': 0.0,
            }

    # ------------------------------------------------------------------
    # Methods required by transaction_routes.py
    # ------------------------------------------------------------------
    def get_all_transactions(self, company_id: str = None) -> List[dict]:
        """Return all transactions as a list of dicts (not DataFrame)."""
        df = self.get_transactions(company_id=company_id)
        if df is None or df.empty:
            return []
        return df.to_dict('records')

    def get_transaction_by_id(self, txn_id: str, company_id: str = None) -> Optional[dict]:
        """Get a single transaction by ID."""
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute("SELECT * FROM transactions WHERE id=%s", (txn_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_transaction_by_id failed: %s", e)
            return None

    def update_review_status(self, txn_id: str, status: str, notes: str = '',
                             company_id: str = None) -> bool:
        """Update the review status of a transaction."""
        try:
            with get_cursor() as cur:
                cur.execute(
                    """UPDATE transactions 
                       SET status=%s, description=CONCAT(description, ' [Review: ', %s, ']')
                       WHERE id=%s""",
                    (status, notes, txn_id)
                )
            return True
        except Exception as e:
            logger.error("update_review_status failed: %s", e)
            return False

    def add_flagged_account(self, account_code: str, account_name: str = '',
                            reason: str = '', auto: bool = False,
                            company_id: str = None) -> bool:
        """Add an account to the flagged list."""
        return self.flag_account({
            'company_id': company_id or 'default',
            'account_code': account_code,
            'account_name': account_name,
            'reason': reason,
            'flagged_by': 'auto' if auto else 'manual',
        })

    def remove_flagged_account(self, flag_id: str, company_id: str = None) -> bool:
        """Remove a flagged account by flag_id (which is the account_code)."""
        return self.unflag_account(flag_id, company_id)

    def generate_sample_excel(self) -> Optional[str]:
        """Generate a sample Excel template for transaction imports."""
        import tempfile
        import os
        try:
            sample_data = {
                'date': ['2024-01-15', '2024-01-16', '2024-01-17'],
                'description': ['Office supplies purchase', 'Client payment received', 'Utility bill payment'],
                'debit_account': ['6100 - Office Expenses', '1100 - Cash', '6200 - Utilities'],
                'credit_account': ['1100 - Cash', '4100 - Sales Revenue', '1100 - Cash'],
                'amount': [1500.00, 25000.00, 3200.00],
                'reference': ['INV-001', 'REC-042', 'UTIL-012'],
                'category': ['Expense', 'Income', 'Expense'],
            }
            df = pd.DataFrame(sample_data)
            
            # Create temp file
            fd, filepath = tempfile.mkstemp(suffix='.xlsx')
            os.close(fd)
            df.to_excel(filepath, index=False, sheet_name='Transactions')
            return filepath
        except Exception as e:
            logger.error("generate_sample_excel failed: %s", e)
            return None

    def export_to_excel(self, company_id: str = None) -> Optional[str]:
        """Export all transactions to an Excel file."""
        import tempfile
        import os
        try:
            df = self.get_transactions(company_id=company_id)
            if df is None or df.empty:
                # Create empty template
                df = pd.DataFrame(columns=['date', 'description', 'debit_account',
                                           'credit_account', 'amount', 'reference', 'category'])
            
            fd, filepath = tempfile.mkstemp(suffix='.xlsx')
            os.close(fd)
            df.to_excel(filepath, index=False, sheet_name='Transactions')
            return filepath
        except Exception as e:
            logger.error("export_to_excel failed: %s", e)
            return None

    def import_from_dataframe(self, df: pd.DataFrame, filename: str = '',
                              company_id: str = None) -> dict:
        """Import transactions from a pandas DataFrame."""
        result = {'success': False, 'imported': 0, 'errors': [], 'message': ''}
        cid = company_id or 'default'
        
        if df is None or df.empty:
            result['message'] = 'No data to import'
            return result
        
        # Normalize column names
        df.columns = [str(c).lower().strip().replace(' ', '_') for c in df.columns]
        
        # Map common column variations
        col_map = {
            'transaction_date': 'date',
            'trans_date': 'date',
            'desc': 'description',
            'debit': 'debit_account',
            'credit': 'credit_account',
            'ref': 'reference',
            'ref_no': 'reference',
            'reference_no': 'reference',
            'cat': 'category',
            'type': 'category',
        }
        df.rename(columns={k: v for k, v in col_map.items() if k in df.columns}, inplace=True)
        
        records = df.to_dict('records')
        for r in records:
            r['created_by'] = f'import:{filename}'
        
        import_result = self.bulk_import(records, company_id=cid)
        result['success'] = import_result['imported'] > 0
        result['imported'] = import_result['imported']
        result['errors'] = import_result.get('errors', [])
        result['message'] = f"Successfully imported {import_result['imported']} transactions"
        if import_result.get('errors'):
            result['message'] += f" with {len(import_result['errors'])} errors"
        
        return result


# Singleton
transaction_store = TransactionDataStore()
