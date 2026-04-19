"""
AICC Human Resource Management (HRM) Data Store - PostgreSQL backend.
"""

import logging
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from db import get_conn, get_tenant_cursor

logger = logging.getLogger(__name__)


class HRMDataStore:
    def __init__(self):
        self._ensure_tables()

    def _ensure_tables(self):
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS hrm_payroll_runs (
                            run_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            payroll_month VARCHAR(7) NOT NULL,
                            contract_type VARCHAR(50) DEFAULT '',
                            grade VARCHAR(50) DEFAULT '',
                            gross_pay NUMERIC(15,2) DEFAULT 0,
                            allowances NUMERIC(15,2) DEFAULT 0,
                            deductions NUMERIC(15,2) DEFAULT 0,
                            overtime_pay NUMERIC(15,2) DEFAULT 0,
                            tax_amount NUMERIC(15,2) DEFAULT 0,
                            pension_amount NUMERIC(15,2) DEFAULT 0,
                            net_pay NUMERIC(15,2) DEFAULT 0,
                            status VARCHAR(30) DEFAULT 'draft',
                            approved_by VARCHAR(100),
                            created_by VARCHAR(100),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS hrm_leave_requests (
                            leave_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            employee_id VARCHAR(64) NOT NULL,
                            leave_type VARCHAR(50) NOT NULL,
                            start_date DATE NOT NULL,
                            end_date DATE NOT NULL,
                            days_requested INT DEFAULT 0,
                            reason TEXT,
                            status VARCHAR(30) DEFAULT 'pending',
                            approver_id VARCHAR(64),
                            approver_note TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS hrm_training_records (
                            training_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            employee_id VARCHAR(64) NOT NULL,
                            training_name VARCHAR(255) NOT NULL,
                            planned_date DATE,
                            completion_date DATE,
                            result VARCHAR(100) DEFAULT '',
                            score NUMERIC(6,2) DEFAULT 0,
                            status VARCHAR(30) DEFAULT 'planned',
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS hrm_performance_reviews (
                            review_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            employee_id VARCHAR(64) NOT NULL,
                            review_period VARCHAR(30) NOT NULL,
                            kpi_score NUMERIC(6,2) DEFAULT 0,
                            okr_score NUMERIC(6,2) DEFAULT 0,
                            disciplinary_note TEXT,
                            promotion_recommended BOOLEAN DEFAULT FALSE,
                            increment_percent NUMERIC(6,2) DEFAULT 0,
                            reviewer_id VARCHAR(64),
                            status VARCHAR(30) DEFAULT 'draft',
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS hrm_grievances (
                            grievance_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            employee_id VARCHAR(64) NOT NULL,
                            title VARCHAR(255) NOT NULL,
                            details TEXT,
                            status VARCHAR(30) DEFAULT 'open',
                            resolution_note TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            resolved_at TIMESTAMP
                        );

                        CREATE INDEX IF NOT EXISTS idx_hrm_payroll_company_month
                            ON hrm_payroll_runs(company_id, payroll_month);
                        CREATE INDEX IF NOT EXISTS idx_hrm_leave_company_employee
                            ON hrm_leave_requests(company_id, employee_id);
                        CREATE INDEX IF NOT EXISTS idx_hrm_training_company_employee
                            ON hrm_training_records(company_id, employee_id);
                        CREATE INDEX IF NOT EXISTS idx_hrm_perf_company_employee
                            ON hrm_performance_reviews(company_id, employee_id);
                        CREATE INDEX IF NOT EXISTS idx_hrm_grievance_company_employee
                            ON hrm_grievances(company_id, employee_id);
                        """
                    )
                    conn.commit()
        except Exception as e:
            logger.warning("HRM tables check failed: %s", e)

    def create_payroll_run(self, data: Dict[str, Any]) -> Optional[str]:
        run_id = data.get("run_id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO hrm_payroll_runs
                    (run_id, company_id, payroll_month, contract_type, grade,
                     gross_pay, allowances, deductions, overtime_pay, tax_amount,
                     pension_amount, net_pay, status, approved_by, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id, cid,
                        data.get("payroll_month", ""),
                        data.get("contract_type", ""),
                        data.get("grade", ""),
                        float(data.get("gross_pay", 0) or 0),
                        float(data.get("allowances", 0) or 0),
                        float(data.get("deductions", 0) or 0),
                        float(data.get("overtime_pay", 0) or 0),
                        float(data.get("tax_amount", 0) or 0),
                        float(data.get("pension_amount", 0) or 0),
                        float(data.get("net_pay", 0) or 0),
                        data.get("status", "draft"),
                        data.get("approved_by", ""),
                        data.get("created_by", ""),
                    ),
                )
                return run_id
        except Exception as e:
            logger.error("create_payroll_run failed: %s", e)
            return None

    def create_leave_request(self, data: Dict[str, Any]) -> Optional[str]:
        leave_id = data.get("leave_id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO hrm_leave_requests
                    (leave_id, company_id, employee_id, leave_type, start_date, end_date,
                     days_requested, reason, status, approver_id, approver_note)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        leave_id, cid,
                        data.get("employee_id", ""),
                        data.get("leave_type", "annual"),
                        data.get("start_date", date.today()),
                        data.get("end_date", date.today()),
                        int(data.get("days_requested", 0) or 0),
                        data.get("reason", ""),
                        data.get("status", "pending"),
                        data.get("approver_id", ""),
                        data.get("approver_note", ""),
                    ),
                )
                return leave_id
        except Exception as e:
            logger.error("create_leave_request failed: %s", e)
            return None

    def update_leave_status(self, leave_id: str, status: str, approver_id: str = "", approver_note: str = "", company_id: str = "default") -> bool:
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    """
                    UPDATE hrm_leave_requests
                    SET status=%s, approver_id=%s, approver_note=%s, updated_at=CURRENT_TIMESTAMP
                    WHERE leave_id=%s AND company_id=%s
                    """,
                    (status, approver_id, approver_note, leave_id, company_id),
                )
                return cur.rowcount > 0
        except Exception as e:
            logger.error("update_leave_status failed: %s", e)
            return False

    def create_training_record(self, data: Dict[str, Any]) -> Optional[str]:
        training_id = data.get("training_id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO hrm_training_records
                    (training_id, company_id, employee_id, training_name, planned_date,
                     completion_date, result, score, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        training_id, cid,
                        data.get("employee_id", ""),
                        data.get("training_name", ""),
                        data.get("planned_date"),
                        data.get("completion_date"),
                        data.get("result", ""),
                        float(data.get("score", 0) or 0),
                        data.get("status", "planned"),
                    ),
                )
                return training_id
        except Exception as e:
            logger.error("create_training_record failed: %s", e)
            return None

    def create_performance_review(self, data: Dict[str, Any]) -> Optional[str]:
        review_id = data.get("review_id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO hrm_performance_reviews
                    (review_id, company_id, employee_id, review_period, kpi_score, okr_score,
                     disciplinary_note, promotion_recommended, increment_percent, reviewer_id, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        review_id, cid,
                        data.get("employee_id", ""),
                        data.get("review_period", ""),
                        float(data.get("kpi_score", 0) or 0),
                        float(data.get("okr_score", 0) or 0),
                        data.get("disciplinary_note", ""),
                        bool(data.get("promotion_recommended", False)),
                        float(data.get("increment_percent", 0) or 0),
                        data.get("reviewer_id", ""),
                        data.get("status", "draft"),
                    ),
                )
                return review_id
        except Exception as e:
            logger.error("create_performance_review failed: %s", e)
            return None

    def create_grievance(self, data: Dict[str, Any]) -> Optional[str]:
        grievance_id = data.get("grievance_id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO hrm_grievances
                    (grievance_id, company_id, employee_id, title, details, status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        grievance_id, cid,
                        data.get("employee_id", ""),
                        data.get("title", ""),
                        data.get("details", ""),
                        data.get("status", "open"),
                    ),
                )
                return grievance_id
        except Exception as e:
            logger.error("create_grievance failed: %s", e)
            return None

    def get_hr_analytics(self, company_id: str = "default") -> Dict[str, Any]:
        result = {
            "open_grievances": 0,
            "pending_leave_requests": 0,
            "completed_trainings": 0,
            "approved_promotions": 0,
            "payroll_runs": 0,
        }
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute("SELECT COUNT(*) AS cnt FROM hrm_grievances WHERE company_id=%s AND status='open'", (company_id,))
                result["open_grievances"] = (cur.fetchone() or {}).get("cnt", 0)

                cur.execute("SELECT COUNT(*) AS cnt FROM hrm_leave_requests WHERE company_id=%s AND status='pending'", (company_id,))
                result["pending_leave_requests"] = (cur.fetchone() or {}).get("cnt", 0)

                cur.execute("SELECT COUNT(*) AS cnt FROM hrm_training_records WHERE company_id=%s AND status='completed'", (company_id,))
                result["completed_trainings"] = (cur.fetchone() or {}).get("cnt", 0)

                cur.execute("SELECT COUNT(*) AS cnt FROM hrm_performance_reviews WHERE company_id=%s AND promotion_recommended=TRUE", (company_id,))
                result["approved_promotions"] = (cur.fetchone() or {}).get("cnt", 0)

                cur.execute("SELECT COUNT(*) AS cnt FROM hrm_payroll_runs WHERE company_id=%s", (company_id,))
                result["payroll_runs"] = (cur.fetchone() or {}).get("cnt", 0)
            return result
        except Exception as e:
            logger.error("get_hr_analytics failed: %s", e)
            return result


hrm_store = HRMDataStore()
