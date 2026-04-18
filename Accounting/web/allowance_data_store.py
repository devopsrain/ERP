from psycopg2.extras import RealDictCursor
from web.db import get_cursor
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class AllowanceDataStore:
    def get_allowance_definitions(self, company_id: str = None) -> List[Dict]:
        try:
            with get_cursor(company_id) as cur:
                cur.execute("SELECT * FROM allowance_definitions")
                return cur.fetchall()
        except Exception as e:
            logger.error("get_allowance_definitions failed: %s", e)
            return []

    def get_allowance_definition(self,
                                 allowance_name: str,
                                 company_id: str = None) -> Optional[Dict]:
        try:
            with get_cursor(company_id) as cur:
                cur.execute(
                    "SELECT * FROM allowance_definitions WHERE allowance_name = %s",
                    (allowance_name, ))
                return cur.fetchone()
        except Exception as e:
            logger.error("get_allowance_definition failed: %s", e)
            return None

    def add_allowance_definition(self,
                                 allowance_name: str,
                                 allowance_type: str,
                                 allowance_value: float,
                                 company_id: str = None) -> Optional[Dict]:
        try:
            with get_cursor(company_id) as cur:
                cur.execute(
                    """INSERT INTO allowance_definitions
                       (allowance_name, allowance_type, allowance_value)
                       VALUES (%s, %s, %s)
                       RETURNING *""",
                    (allowance_name, allowance_type, allowance_value))
                return cur.fetchone()
        except Exception as e:
            logger.error("add_allowance_definition failed: %s", e)
            return None

    def update_allowance_definition(self,
                                    allowance_name: str,
                                    allowance_type: str,
                                    allowance_value: float,
                                    company_id: str = None) -> Optional[Dict]:
        try:
            with get_cursor(company_id) as cur:
                cur.execute(
                    """UPDATE allowance_definitions SET
                         allowance_type=%s, allowance_value=%s
                       WHERE allowance_name=%s
                       RETURNING *""",
                    (allowance_type, allowance_value, allowance_name))
                return cur.fetchone()
        except Exception as e:
            logger.error("update_allowance_definition failed: %s", e)
            return None

    def delete_allowance_definition(self,
                                    allowance_name: str,
                                    company_id: str = None) -> bool:
        try:
            with get_cursor(company_id) as cur:
                cur.execute(
                    "DELETE FROM allowance_definitions WHERE allowance_name = %s",
                    (allowance_name, ))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("delete_allowance_definition failed: %s", e)
            return False

    def get_employee_allowances(self,
                                employee_id: str,
                                company_id: str = None) -> List[Dict]:
        try:
            with get_cursor(company_id) as cur:
                cur.execute(
                    "SELECT * FROM employee_allowances WHERE employee_id = %s",
                    (employee_id, ))
                return cur.fetchall()
        except Exception as e:
            logger.error("get_employee_allowances failed: %s", e)
            return []

    def add_employee_allowance(self,
                               employee_id: str,
                               allowance_name: str,
                               company_id: str = None) -> Optional[Dict]:
        try:
            with get_cursor(company_id) as cur:
                cur.execute(
                    """INSERT INTO employee_allowances (employee_id, allowance_name)
                       VALUES (%s, %s)
                       RETURNING *""", (employee_id, allowance_name))
                return cur.fetchone()
        except Exception as e:
            logger.error("add_employee_allowance failed: %s", e)
            return None

    def delete_employee_allowance(self,
                                  employee_id: str,
                                  allowance_name: str,
                                  company_id: str = None) -> bool:
        try:
            with get_cursor(company_id) as cur:
                cur.execute(
                    "DELETE FROM employee_allowances WHERE employee_id = %s AND allowance_name = %s",
                    (employee_id, allowance_name))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("delete_employee_allowance failed: %s", e)
            return False
