"""
Employee Data Store - PostgreSQL backend
"""

import uuid
import logging
import pandas as pd
from datetime import datetime, date
from typing import Dict, List, Any, Optional
from psycopg2.extras import RealDictCursor, execute_values

from db import get_cursor, get_conn, get_tenant_cursor

logger = logging.getLogger(__name__)


def _resolve_company_id(company_id=None):
    """Return company_id, falling back to 'default'.  No Flask dependency."""
    return company_id or 'default'


class EmployeeDataStore:
    """PostgreSQL-backed data storage for employee records."""

    def __init__(self):
        self._ensure_new_columns()  # safe migration on startup

    def _ensure_new_columns(self):
        """Add new columns to the employees table if they don't already exist."""
        new_cols = [
            ("date_of_birth", "DATE"),
            ("phone_number",  "VARCHAR(50)  DEFAULT ''"),
            ("manager",       "VARCHAR(200) DEFAULT ''"),
        ]
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for col, col_type in new_cols:
                        cur.execute(
                            "SELECT 1 FROM information_schema.columns "
                            "WHERE table_name='employees' AND column_name=%s",
                            (col,)
                        )
                        if not cur.fetchone():
                            cur.execute(
                                f"ALTER TABLE employees ADD COLUMN IF NOT EXISTS {col} {col_type}"
                            )
        except Exception as e:
            logger.warning("_ensure_new_columns: %s", e)

    #  Read 

    def read_all_employees(self, company_id: str = None) -> pd.DataFrame:
        """Read all active employees. Cached per company for 2 minutes."""
        from extensions import cache
        cid = _resolve_company_id(company_id)
        ck  = f"employees:{cid}"
        cached = cache.get(ck)
        if cached is not None:
            return pd.DataFrame(cached)
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM employees WHERE company_id=%s AND is_active=TRUE "
                    "ORDER BY name LIMIT 500",
                    (cid,)
                )
                rows = cur.fetchall()
                result = [dict(r) for r in rows] if rows else []
                cache.set(ck, result, timeout=120)
                return pd.DataFrame(result) if result else pd.DataFrame()
        except Exception as e:
            logger.error("read_all_employees failed: %s", e)
            return pd.DataFrame()

    def _read_all_employees_unfiltered(self) -> pd.DataFrame:
        """DEPRECATED: This method exposes all tenant data. Only use for admin/migration."""
        logger.warning("_read_all_employees_unfiltered called — bypasses tenant isolation")
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM employees ORDER BY name LIMIT 500")
                rows = cur.fetchall()
                return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()
        except Exception as e:
            logger.error("_read_all_employees_unfiltered failed: %s", e)
            return pd.DataFrame()

    def get_active_employees(self, company_id: str = None) -> pd.DataFrame:
        return self.read_all_employees(company_id)

    def get_employee(self, employee_id: str, company_id: str = None) -> Optional[dict]:
        cid = _resolve_company_id(company_id)
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM employees WHERE employee_id=%s AND company_id=%s",
                    (employee_id, cid)
                )
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_employee failed: %s", e)
            return None

    def employee_exists(self, employee_id: str, company_id: str = None) -> bool:
        return self.get_employee(employee_id, company_id) is not None

    #  Write 

    def write_employees(self, df: pd.DataFrame):
        """Upsert a DataFrame of employees into PostgreSQL using execute_values for bulk insertion."""
        if df.empty:
            return
        if 'company_id' not in df.columns:
            df = df.copy()
            df['company_id'] = 'default'
        
        # Ensure all columns are present, filling missing ones with defaults
        cols = [
            'employee_id', 'company_id', 'name', 'category', 'basic_salary',
            'hire_date', 'department', 'position', 'bank_account', 'tin_number',
            'pension_number', 'work_days_per_month', 'work_hours_per_day',
            'is_active', 'created_date', 'updated_date',
            'date_of_birth', 'phone_number', 'manager'
        ]
        for col in cols:
            if col not in df.columns:
                df[col] = None

        # Convert to list of tuples for execute_values
        data = [tuple(row) for row in df[cols].to_numpy()]

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    execute_values(
                        cur,
                        """INSERT INTO employees (
                               employee_id, company_id, name, category, basic_salary,
                               hire_date, department, position, bank_account, tin_number,
                               pension_number, work_days_per_month, work_hours_per_day,
                               is_active, created_date, updated_date,
                               date_of_birth, phone_number, manager
                           ) VALUES %s
                           ON CONFLICT (employee_id) DO UPDATE SET
                             name=EXCLUDED.name, category=EXCLUDED.category,
                             basic_salary=EXCLUDED.basic_salary,
                             hire_date=EXCLUDED.hire_date,
                             department=EXCLUDED.department,
                             position=EXCLUDED.position,
                             bank_account=EXCLUDED.bank_account,
                             tin_number=EXCLUDED.tin_number,
                             pension_number=EXCLUDED.pension_number,
                             work_days_per_month=EXCLUDED.work_days_per_month,
                             work_hours_per_day=EXCLUDED.work_hours_per_day,
                             is_active=EXCLUDED.is_active,
                             updated_date=EXCLUDED.updated_date,
                             date_of_birth=EXCLUDED.date_of_birth,
                             phone_number=EXCLUDED.phone_number,
                             manager=EXCLUDED.manager
                        """,
                        data
                    )
                    conn.commit()
                    logger.info(f"Successfully upserted {len(data)} employee records.")
        except Exception as e:
            logger.error("Error bulk writing employees: %s", e)
            # Optionally re-raise or handle the error
            raise

    def _invalidate_cache(self, company_id: str):
        """Bust the per-company employee + dashboard cache after any mutation."""
        try:
            from extensions import cache
            cache.delete(f"employees:{company_id}")
            cache.delete(f"dashboard_stats:{company_id}")
        except Exception:
            pass

    def update_employee(self, employee_id: str, updates: dict,
                        company_id: str = None) -> bool:
        cid = _resolve_company_id(company_id)
        protected = {'employee_id', 'company_id', 'created_date'}
        clean = {k: v for k, v in updates.items() if k not in protected}
        if not clean:
            return True
        clean['updated_date'] = datetime.now()
        cols = ', '.join(f"{k}=%s" for k in clean)
        vals = list(clean.values()) + [employee_id, cid]
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    f"UPDATE employees SET {cols} WHERE employee_id=%s AND company_id=%s",
                    vals
                )
                ok = cur.rowcount > 0
            if ok:
                self._invalidate_cache(cid)
            return ok
        except Exception as e:
            logger.error("update_employee failed: %s", e)
            return False

    def delete_employee(self, employee_id: str, company_id: str = None) -> bool:
        """Soft delete  sets is_active=False."""
        return self.update_employee(employee_id, {'is_active': False}, company_id)

    #  Validation 

    def validate_employee_data(self, employee_data: dict,
                                employee_id_to_exclude: str = None) -> List[str]:
        errors = []
        if not str(employee_data.get('employee_id', '')).strip():
            errors.append('Employee ID is required')
        if not str(employee_data.get('name', '')).strip():
            errors.append('Name is required')
        if not str(employee_data.get('tin_number', '')).strip():
            errors.append('TIN Number is required')
        if not employee_data.get('category'):
            errors.append('Category is required')
        if not employee_data.get('basic_salary'):
            errors.append('Basic Salary is required')
        if not employee_data.get('hire_date'):
            errors.append('Hire Date is required')

        employee_id = str(employee_data.get('employee_id', '')).strip()
        name = str(employee_data.get('name', '')).strip().lower()
        tin_number = str(employee_data.get('tin_number', '')).strip()

        if employee_id or name or tin_number:
            try:
                df = self.read_all_employees()
                if not df.empty:
                    check = df.copy()
                    if employee_id_to_exclude:
                        check = check[check['employee_id'] != employee_id_to_exclude]
                    if employee_id and employee_id in check['employee_id'].values:
                        errors.append(f'Employee ID {employee_id} already exists')
                    if tin_number and len(check[check['tin_number'] == tin_number]) > 0:
                        errors.append(f'TIN Number {tin_number} already exists')
            except Exception:
                pass

        return errors


    def add_employee(self, employee_data: dict, company_id: str = None) -> bool:
        """Insert a single new employee record."""
        cid = _resolve_company_id(company_id)
        data = dict(employee_data)
        data.setdefault('company_id', cid)
        result = self.bulk_import([data])
        if result['error_count'] == 0:
            self._invalidate_cache(cid)
        return result['error_count'] == 0

    def bulk_import(self, employees_data: list, overwrite: bool = False) -> dict:
        """Upsert a list of employee dicts. Returns {success_count, error_count, errors}."""
        import pandas as _pd
        success = 0
        errors = []
        try:
            df = _pd.DataFrame(employees_data)
            if 'company_id' not in df.columns:
                df['company_id'] = 'default'
            self.write_employees(df)
            success = len(df)
        except Exception as e:
            logger.error("bulk_import failed: %s", e)
            errors.append(str(e))
        return {'success_count': success, 'error_count': len(errors), 'errors': errors}


# Singleton instance
employee_store = EmployeeDataStore()
