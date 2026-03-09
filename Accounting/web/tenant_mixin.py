"""
Tenant-Aware Data Store Mixin

Provides helper methods that any parquet-backed data store can use
to transparently scope reads/writes by company_id.

Usage:
    class MyCpoDataStore(TenantAwareMixin):
        def get_all_cpos(self, company_id=None):
            df = self._read_parquet(self.cpo_file)
            return self._filter_by_company(df, company_id).to_dict('records')
"""

import logging
import pandas as pd
from flask import g

logger = logging.getLogger(__name__)


class TenantAwareMixin:
    """Mixin that adds company_id scoping to any data store."""

    @staticmethod
    def _get_current_company_id() -> str:
        """Return the company_id from Flask's request-local `g`, or 'default'."""
        return getattr(g, 'company_id', None) or 'default'

    @staticmethod
    def _filter_by_company(df: pd.DataFrame, company_id: str = None) -> pd.DataFrame:
        """Filter a DataFrame down to rows belonging to one company.

        If company_id is None, attempts to resolve from Flask g.
        If the DataFrame has no 'company_id' column, returns it unchanged.
        """
        if df.empty or 'company_id' not in df.columns:
            return df

        if company_id is None:
            company_id = TenantAwareMixin._get_current_company_id()

        return df[df['company_id'] == company_id].copy()

    @staticmethod
    def _inject_company_id(record: dict, company_id: str = None) -> dict:
        """Ensure a record dict has a company_id field."""
        if company_id is None:
            company_id = TenantAwareMixin._get_current_company_id()
        record['company_id'] = company_id
        return record

    @staticmethod
    def _ensure_company_column(df: pd.DataFrame) -> pd.DataFrame:
        """Add company_id column with 'default' if it's missing (migration)."""
        if 'company_id' not in df.columns:
            df['company_id'] = 'default'
        return df
