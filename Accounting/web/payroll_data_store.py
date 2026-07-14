from psycopg2.extras import RealDictCursor, execute_values
from db import get_conn, get_cursor
import pandas as pd
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class PayrollDataStore:
    def get_payroll_data(self, month: int, year: int,
                         company_id: str = None) -> List[Dict]:
        try:
            with get_cursor(company_id) as cur:
                cur.execute(
                    "SELECT * FROM payroll_data WHERE month = %s AND year = %s",
                    (month, year))
                return cur.fetchall()
        except Exception as e:
            logger.error("get_payroll_data failed: %s", e)
            return []

    def get_employee_payroll_data(self,
                                  employee_id: str,
                                  month: int,
                                  year: int,
                                  company_id: str = None) -> Optional[Dict]:
        try:
            with get_cursor(company_id) as cur:
                cur.execute(
                    "SELECT * FROM payroll_data WHERE employee_id = %s AND month = %s AND year = %s",
                    (employee_id, month, year))
                return cur.fetchone()
        except Exception as e:
            logger.error("get_employee_payroll_data failed: %s", e)
            return None

    def write_payroll_data(self, df: pd.DataFrame,
                           company_id: str = None) -> None:
        """
        Writes a DataFrame of payroll data to the 'payroll_data' table.
        """
        if df.empty:
            return
        if 'company_id' not in df.columns:
            df = df.copy()
            df['company_id'] = 'default'

        # Define the columns to be written
        payroll_cols = [
            'employee_id', 'month', 'year', 'gross_salary', 'net_salary',
            'pension', 'income_tax', 'total_deductions', 'company_id'
        ]
        # Ensure all columns are present
        for col in payroll_cols:
            if col not in df.columns:
                df[col] = 0  # Or some other default

        # Prepare data for bulk insertion
        data_to_insert = [tuple(x) for x in df[payroll_cols].to_numpy()]

        try:
            with get_conn(company_id) as conn:
                with conn.cursor() as cur:
                    execute_values(
                        cur,
                        """INSERT INTO payroll_data (
                               employee_id, month, year, gross_salary, net_salary,
                               pension, income_tax, total_deductions, company_id
                           ) VALUES %s
                           ON CONFLICT (employee_id, month, year) DO UPDATE SET
                             gross_salary = EXCLUDED.gross_salary,
                             net_salary = EXCLUDED.net_salary,
                             pension = EXCLUDED.pension,
                             income_tax = EXCLUDED.income_tax,
                             total_deductions = EXCLUDED.total_deductions
                        """,
                        data_to_insert
                    )
                    conn.commit()
                    logger.info(f"Successfully wrote {len(data_to_insert)} payroll records.")
        except Exception as e:
            logger.error("Error writing payroll data: %s", e)
            raise
