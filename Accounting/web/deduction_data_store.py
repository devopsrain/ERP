from psycopg2.extras import RealDictCursor
from db import get_cursor
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class DeductionDataStore:
    def get_deduction_definitions(self,
                                  company_id: str = None) -> List[Dict]:
        try:
            with get_cursor(company_id) as cur:
                cur.execute("SELECT * FROM deduction_definitions")
                return cur.fetchall()
        except Exception as e:
            logger.error("get_deduction_definitions failed: %s", e)
            return []

    def get_deduction_definition(self,
                                 deduction_name: str,
                                 company_id: str = None) -> Optional[Dict]:
        try:
            with get_cursor(company_id) as cur:
                cur.execute(
                    "SELECT * FROM deduction_definitions WHERE deduction_name = %s",
                    (deduction_name, ))
                return cur.fetchone()
        except Exception as e:
            logger.error("get_deduction_definition failed: %s", e)
            return None

    def add_deduction_definition(self,
                                 deduction_name: str,
                                 deduction_type: str,
                                 deduction_value: float,
                                 company_id: str = None) -> Optional[Dict]:
        try:
            with get_cursor(company_id) as cur:
                cur.execute(
                    """INSERT INTO deduction_definitions
                       (deduction_name, deduction_type, deduction_value)
                       VALUES (%s, %s, %s)
                       RETURNING *""",
                    (deduction_name, deduction_type, deduction_value))
                return cur.fetchone()
        except Exception as e:
            logger.error("add_deduction_definition failed: %s", e)
            return None

    def update_deduction_definition(self,
                                    deduction_name: str,
                                    deduction_type: str,
                                    deduction_value: float,
                                    company_id: str = None) -> Optional[Dict]:
        try:
            with get_cursor(company_id) as cur:
                cur.execute(
                    """UPDATE deduction_definitions SET
                         deduction_type=%s, deduction_value=%s
                       WHERE deduction_name=%s
                       RETURNING *""",
                    (deduction_type, deduction_value, deduction_name))
                return cur.fetchone()
        except Exception as e:
            logger.error("update_deduction_definition failed: %s", e)
            return None

    def delete_deduction_definition(self,
                                    deduction_name: str,
                                    company_id: str = None) -> bool:
        try:
            with get_cursor(company_id) as cur:
                cur.execute(
                    "DELETE FROM deduction_definitions WHERE deduction_name = %s",
                    (deduction_name, ))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("delete_deduction_definition failed: %s", e)
            return False

    def get_employee_deductions(self,
                                employee_id: str,
                                company_id: str = None) -> List[Dict]:
        try:
            with get_cursor(company_id) as cur:
                cur.execute(
                    "SELECT * FROM employee_deductions WHERE employee_id = %s",
                    (employee_id, ))
                return cur.fetchall()
        except Exception as e:
            logger.error("get_employee_deductions failed: %s", e)
            return []

    def add_employee_deduction(self,
                               employee_id: str,
                               deduction_name: str,
                               company_id: str = None) -> Optional[Dict]:
        try:
            with get_cursor(company_id) as cur:
                cur.execute(
                    """INSERT INTO employee_deductions (employee_id, deduction_name)
                       VALUES (%s, %s)
                       RETURNING *""", (employee_id, deduction_name))
                return cur.fetchone()
        except Exception as e:
            logger.error("add_employee_deduction failed: %s", e)
            return None

    def delete_employee_deduction(self,
                                  employee_id: str,
                                  deduction_name: str,
                                  company_id: str = None) -> bool:
        try:
            with get_cursor(company_id) as cur:
                cur.execute(
                    "DELETE FROM employee_deductions WHERE employee_id = %s AND deduction_name = %s",
                    (employee_id, deduction_name))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("delete_employee_deduction failed: %s", e)
            return False
