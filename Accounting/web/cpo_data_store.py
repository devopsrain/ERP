"""
CPO Data Store - PostgreSQL backend (schema matches init_db.sql cpo_records/cpo_import_history)
"""

import logging
import uuid
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from db import get_cursor, get_conn, get_tenant_cursor

logger = logging.getLogger(__name__)


class CPODataStore:
    """PostgreSQL-backed CPO (Cash Payment Order) management."""

    def __init__(self, data_dir=None):
        self._data_dir = Path(__file__).parent / (data_dir or 'data')
        self._data_dir.mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    def get_summary(self, company_id: str = None) -> dict:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """SELECT COUNT(*) AS total, COALESCE(SUM(amount),0) AS total_amount
                       FROM cpo_records WHERE company_id=%s""",
                    (cid,)
                )
                row = cur.fetchone()
                cur.execute(
                    "SELECT COUNT(*) AS ret FROM cpo_records "
                    "WHERE company_id=%s AND is_returned='Yes'",
                    (cid,)
                )
                ret = cur.fetchone()
                total = int(row['total']) if row else 0
                returned = int(ret['ret']) if ret else 0
                total_amount = float(row['total_amount']) if row else 0.0
                return {
                    'total': total,
                    'returned': returned,
                    'pending': total - returned,
                    'total_amount': total_amount,
                }
        except Exception as e:
            logger.error("get_summary failed: %s", e)
            return {'total': 0, 'returned': 0, 'pending': 0, 'total_amount': 0.0}

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def get_all_cpos(self, company_id: str = None) -> List[dict]:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM cpo_records WHERE company_id=%s ORDER BY created_at DESC LIMIT 500",
                    (cid,)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_all_cpos failed: %s", e)
            return []

    def get_cpo_by_id(self, cpo_id: str, company_id: str = None) -> Optional[dict]:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM cpo_records WHERE id=%s AND company_id=%s",
                    (cpo_id, cid)
                )
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_cpo_by_id failed: %s", e)
            return None

    def save_cpo(self, record: dict, company_id: str = None) -> bool:
        """Create or update a CPO record. Use record['id'] for updates."""
        cid = company_id or record.get('company_id', 'default')
        rid = record.get('id') or str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        try:
            with get_cursor() as cur:
                cur.execute("SELECT 1 FROM cpo_records WHERE id=%s", (rid,))
                exists = cur.fetchone()
                if exists:
                    cur.execute(
                        """UPDATE cpo_records SET
                           name=%s, date=%s, amount=%s, bid_name=%s,
                           is_returned=%s, returned_date=%s
                           WHERE id=%s""",
                        (record.get('name', ''),
                         record.get('date', ''),
                         float(record.get('amount', 0)),
                         record.get('bid_name', ''),
                         record.get('is_returned', 'No'),
                         record.get('returned_date', ''),
                         rid)
                    )
                else:
                    cur.execute(
                        """INSERT INTO cpo_records
                           (id, company_id, import_batch_id, name, date, amount,
                            bid_name, is_returned, returned_date, created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (rid, cid,
                         record.get('import_batch_id', ''),
                         record.get('name', ''),
                         record.get('date', ''),
                         float(record.get('amount', 0)),
                         record.get('bid_name', ''),
                         record.get('is_returned', 'No'),
                         record.get('returned_date', ''),
                         now)
                    )
            return True
        except Exception as e:
            logger.error("save_cpo failed: %s", e)
            return False

    def update_cpo(self, cpo_id: str, updates: dict, company_id: str = None) -> bool:
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute(
                    """UPDATE cpo_records SET
                       name=%s, date=%s, amount=%s, bid_name=%s,
                       is_returned=%s, returned_date=%s
                       WHERE id=%s AND company_id=%s""",
                    (updates.get('name', ''),
                     updates.get('date', ''),
                     float(updates.get('amount', 0)),
                     updates.get('bid_name', ''),
                     updates.get('is_returned', 'No'),
                     updates.get('returned_date', ''),
                     cpo_id, cid)
                )
            return True
        except Exception as e:
            logger.error("update_cpo failed: %s", e)
            return False

    def delete_cpo(self, cpo_id: str, company_id: str = None) -> bool:
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute(
                    "DELETE FROM cpo_records WHERE id=%s AND company_id=%s",
                    (cpo_id, cid)
                )
            return True
        except Exception as e:
            logger.error("delete_cpo failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Import / Export
    # ------------------------------------------------------------------
    def import_from_dataframe(self, df: pd.DataFrame, filename: str,
                               company_id: str = None) -> dict:
        cid = company_id or 'default'
        result = {'imported': 0, 'errors': [], 'skipped': 0}
        batch_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        # Normalise column names
        df.columns = [str(c).strip().lower().replace(' ', '_') for c in df.columns]

        for _, row in df.iterrows():
            try:
                name = str(row.get('name', row.get('payee_name', row.get('beneficiary', '')))).strip()
                if not name:
                    result['skipped'] += 1
                    continue
                record = {
                    'id': str(uuid.uuid4()),
                    'company_id': cid,
                    'import_batch_id': batch_id,
                    'name': name,
                    'date': str(row.get('date', row.get('payment_date', now[:10]))),
                    'amount': float(row.get('amount', row.get('payment_amount', 0))),
                    'bid_name': str(row.get('bid_name', row.get('bid', ''))).strip(),
                    'is_returned': (
                        'Yes' if str(row.get('is_returned', 'No')).strip().lower()
                        in ('yes', 'true', '1') else 'No'
                    ),
                    'returned_date': str(row.get('returned_date', '')).strip(),
                }
                self.save_cpo(record, cid)
                result['imported'] += 1
            except Exception as e:
                result['errors'].append(str(e))

        # Log import history
        try:
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO cpo_import_history
                       (id, filename, import_date, total_rows, imported_rows, errors, status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (batch_id, filename, now,
                     len(df), result['imported'], len(result['errors']), 'completed')
                )
        except Exception as e:
            logger.warning("cpo import history log failed: %s", e)

        return result

    def get_import_history(self, company_id: str = None) -> List[dict]:
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM cpo_import_history ORDER BY import_date DESC LIMIT 50"
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_import_history failed: %s", e)
            return []

    def export_to_excel(self, company_id: str = None) -> Optional[str]:
        try:
            records = self.get_all_cpos(company_id)
            if not records:
                return None
            df = pd.DataFrame(records)
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            filepath = str(self._data_dir / f"cpo_export_{ts}.xlsx")
            df.to_excel(filepath, index=False)
            return filepath
        except Exception as e:
            logger.error("export_to_excel failed: %s", e)
            return None

    def generate_sample_excel(self) -> Optional[str]:
        try:
            df = pd.DataFrame([{
                'name': 'Sample Payee',
                'date': '2026-01-01',
                'amount': 5000.00,
                'bid_name': 'Sample Bid',
                'is_returned': 'No',
                'returned_date': '',
            }])
            filepath = str(self._data_dir / "CPO_Import_Template.xlsx")
            df.to_excel(filepath, index=False)
            return filepath
        except Exception as e:
            logger.error("generate_sample_excel failed: %s", e)
            return None


# Note: cpo_routes.py creates its own instance via CPODataStore(data_dir='data')
