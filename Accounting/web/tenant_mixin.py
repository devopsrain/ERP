"""
Tenant-Aware Data Store Mixin

Provides helper methods that any data store can use to transparently
scope reads/writes by company_id.

Usage:
    class MyCpoDataStore(TenantAwareMixin):
        def get_all_cpos(self, company_id=None):
            df = self._read_parquet(self.cpo_file)
            return self._filter_by_company(df, company_id).to_dict('records')
"""

import logging
import pandas as pd

logger = logging.getLogger(__name__)


class TenantAwareMixin:
    """Mixin that adds company_id scoping to any data store."""

    @staticmethod
    def _get_current_company_id(company_id: str = None) -> str:
        """Return company_id, defaulting to 'default' when not provided.

        The Flask `g` usage was removed — callers must pass company_id
        explicitly (obtained from request.state.company_id in route handlers).
        """
        return company_id or 'default'

    @staticmethod
    def _filter_by_company(df: pd.DataFrame, company_id: str = None) -> pd.DataFrame:
        """Filter a DataFrame down to rows belonging to one company.

        If company_id is None, returns a copy of the full DataFrame unchanged
        (safe default; callers should always pass an explicit company_id).
        If the DataFrame has no 'company_id' column, returns it unchanged.
        """
        if df.empty or 'company_id' not in df.columns:
            return df

        cid = company_id or 'default'
        return df[df['company_id'] == cid].copy()

    @staticmethod
    def _inject_company_id(record: dict, company_id: str = None) -> dict:
        """Ensure a record dict has a company_id field."""
        record['company_id'] = company_id or 'default'
        return record

    @staticmethod
    def _ensure_company_column(df: pd.DataFrame) -> pd.DataFrame:
        """Add company_id column with 'default' if it's missing (migration)."""
        if 'company_id' not in df.columns:
            df['company_id'] = 'default'
        return df
