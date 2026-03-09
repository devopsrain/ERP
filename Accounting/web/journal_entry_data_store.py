"""
Journal Entry Data Store - PostgreSQL backend
"""

import uuid
import logging
import pandas as pd
from datetime import datetime, date
from typing import Dict, List, Any, Optional

from db import get_cursor, get_conn, get_tenant_cursor

logger = logging.getLogger(__name__)


class JournalEntryDataStore:
    """PostgreSQL-backed data storage for journal entries."""

    def __init__(self):
        pass

    def read_journal_entries(self, company_id: str = None,
                             start_date=None, end_date=None) -> pd.DataFrame:
        cid = company_id or 'default'
        try:
            conditions = ["is_active=TRUE", "company_id=%s"]
            params = [cid]
            if start_date:
                conditions.append("entry_date >= %s")
                params.append(str(start_date))
            if end_date:
                conditions.append("entry_date <= %s")
                params.append(str(end_date))
            where = " AND ".join(conditions)
            with get_tenant_cursor(cid) as cur:
                cur.execute(f"SELECT * FROM journal_entries WHERE {where} ORDER BY entry_date DESC", params)
                rows = cur.fetchall()
                return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()
        except Exception as e:
            logger.error("read_journal_entries failed: %s", e)
            return pd.DataFrame()

    def read_entry_lines(self, entry_id: str = None) -> pd.DataFrame:
        try:
            if entry_id:
                with get_cursor() as cur:
                    cur.execute(
                        "SELECT * FROM journal_entry_lines WHERE entry_id=%s AND is_active=TRUE ORDER BY line_number",
                        (entry_id,)
                    )
                    rows = cur.fetchall()
            else:
                with get_cursor() as cur:
                    cur.execute(
                        "SELECT * FROM journal_entry_lines WHERE is_active=TRUE ORDER BY entry_id, line_number"
                    )
                    rows = cur.fetchall()
            return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()
        except Exception as e:
            logger.error("read_entry_lines failed: %s", e)
            return pd.DataFrame()

    def save_journal_entry(self, entry_data: Dict[str, Any],
                           lines_data: List[Dict[str, Any]]) -> str:
        entry_id = entry_data.get('entry_id', str(uuid.uuid4()))
        today = str(date.today())
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO journal_entries
                           (entry_id,company_id,entry_date,description,reference_number,
                            total_debit,total_credit,created_by,created_date,status,is_active)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (entry_id) DO UPDATE SET
                             description=EXCLUDED.description,
                             reference_number=EXCLUDED.reference_number,
                             total_debit=EXCLUDED.total_debit,
                             total_credit=EXCLUDED.total_credit,
                             status=EXCLUDED.status""",
                        (entry_id,
                         entry_data.get('company_id', 'default'),
                         str(entry_data.get('entry_date', today)),
                         entry_data.get('description', ''),
                         entry_data.get('reference_number', ''),
                         float(entry_data.get('total_debit', 0)),
                         float(entry_data.get('total_credit', 0)),
                         entry_data.get('created_by', 'system'),
                         today, entry_data.get('status', 'posted'), True)
                    )
                    for i, line in enumerate(lines_data):
                        cur.execute(
                            """INSERT INTO journal_entry_lines
                               (line_id,entry_id,account_code,account_name,description,
                                debit_amount,credit_amount,line_number,created_date,is_active)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (str(uuid.uuid4()), entry_id,
                             line.get('account_code', ''),
                             line.get('account_name', ''),
                             line.get('description', ''),
                             float(line.get('debit_amount', 0)),
                             float(line.get('credit_amount', 0)),
                             i + 1, today, True)
                        )
        except Exception as e:
            logger.error("save_journal_entry failed: %s", e)
            raise
        return entry_id

    def bulk_import_entries(self, entries_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        result = {'success': False, 'imported_count': 0, 'errors': []}
        try:
            for i, entry in enumerate(entries_data):
                try:
                    if not self._validate_journal_entry(entry):
                        result['errors'].append(f'Invalid entry structure at row {i+1}')
                        continue
                    lines = entry.get('lines', [])
                    if not lines:
                        result['errors'].append(f'No lines found for entry at row {i+1}')
                        continue
                    self.save_journal_entry(entry, lines)
                    result['imported_count'] += 1
                except Exception as e:
                    result['errors'].append(f'Error importing entry {i+1}: {str(e)}')
            result['success'] = True
        except Exception as e:
            result['errors'].append(f'Bulk import error: {str(e)}')
        return result

    def _validate_journal_entry(self, entry: Dict[str, Any]) -> bool:
        for field in ('description', 'entry_date'):
            if not entry.get(field):
                return False
        lines = entry.get('lines', [])
        if not lines:
            return False
        total_debits = sum(float(l.get('debit_amount', 0)) for l in lines)
        total_credits = sum(float(l.get('credit_amount', 0)) for l in lines)
        return abs(total_debits - total_credits) < 0.01

    def export_to_excel(self, company_id: str = None, filename: str = None) -> str:
        from pathlib import Path
        import os
        data_dir = Path(__file__).parent / "data"
        data_dir.mkdir(exist_ok=True)
        if filename is None:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            cs = f'_{company_id}' if company_id else ''
            filename = f'journal_entries{cs}_{ts}.xlsx'
        filepath = str(data_dir / filename)
        entries_df = self.read_journal_entries(company_id)
        lines_df = self.read_entry_lines()
        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            if not entries_df.empty:
                entries_df.drop(columns=['company_id', 'is_active'], errors='ignore').to_excel(
                    writer, sheet_name='Journal Entries', index=False)
            if not lines_df.empty:
                lines_df.drop(columns=['is_active'], errors='ignore').to_excel(
                    writer, sheet_name='Entry Lines', index=False)
        return filepath

    def import_from_excel(self, filepath: str, company_id: str = None) -> Dict[str, Any]:
        result = {'success': False, 'imported_count': 0, 'errors': []}
        try:
            entries_df = pd.read_excel(filepath, sheet_name='Journal Entries')
            lines_df = pd.read_excel(filepath, sheet_name='Entry Lines')
            for _, entry_row in entries_df.iterrows():
                entry_id = str(entry_row.get('entry_id', str(uuid.uuid4())))
                entry_lines = lines_df[lines_df['entry_id'] == entry_id].to_dict('records')
                entry_dict = entry_row.to_dict()
                if company_id:
                    entry_dict['company_id'] = company_id
                try:
                    self.save_journal_entry(entry_dict, entry_lines)
                    result['imported_count'] += 1
                except Exception as e:
                    result['errors'].append(str(e))
            result['success'] = True
        except Exception as e:
            result['errors'].append(str(e))
        return result


# Singleton instance
journal_store = JournalEntryDataStore()
