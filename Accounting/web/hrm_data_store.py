"""
AICC Human Resource Management (HRM) Data Store - PostgreSQL backend.
"""

import calendar
import logging
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from db import get_conn, get_tenant_cursor

logger = logging.getLogger(__name__)


def _num(v) -> Optional[float]:
    """'' → None, otherwise float — for NUMERIC columns."""
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dt(v):
    """'' → None — for DATE columns."""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return None
    return v


_SCHEMA = """
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

                        CREATE TABLE IF NOT EXISTS hrm_leave_types (
                            leave_type_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            name VARCHAR(100) NOT NULL,
                            days_per_year NUMERIC(6,2) DEFAULT 0,
                            description TEXT DEFAULT '',
                            active BOOLEAN DEFAULT TRUE,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS hrm_employee_terms (
                            company_id VARCHAR(64) NOT NULL,
                            employee_id VARCHAR(64) NOT NULL,
                            grade VARCHAR(50) DEFAULT '',
                            contract_type VARCHAR(30) DEFAULT 'permanent',
                            salary_structure TEXT DEFAULT '',
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY (company_id, employee_id)
                        );

                        CREATE TABLE IF NOT EXISTS hrm_kpi_records (
                            kpi_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            employee_id VARCHAR(64) NOT NULL,
                            period VARCHAR(30) NOT NULL,
                            objective TEXT DEFAULT '',
                            key_result TEXT DEFAULT '',
                            target NUMERIC(12,2),
                            actual NUMERIC(12,2),
                            score NUMERIC(6,2),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS hrm_disciplinary_records (
                            record_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            employee_id VARCHAR(64) NOT NULL,
                            incident_date DATE,
                            category VARCHAR(100) DEFAULT '',
                            description TEXT DEFAULT '',
                            action_taken TEXT DEFAULT '',
                            status VARCHAR(30) DEFAULT 'open',
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS hrm_promotions (
                            promotion_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) NOT NULL,
                            employee_id VARCHAR(64) NOT NULL,
                            effective_date DATE,
                            change_type VARCHAR(30) DEFAULT 'promotion',
                            from_grade VARCHAR(50) DEFAULT '',
                            to_grade VARCHAR(50) DEFAULT '',
                            from_salary NUMERIC(15,2),
                            to_salary NUMERIC(15,2),
                            notes TEXT DEFAULT '',
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );

                        CREATE INDEX IF NOT EXISTS idx_hrm_leave_types_company
                            ON hrm_leave_types(company_id);
                        CREATE INDEX IF NOT EXISTS idx_hrm_kpi_company_employee
                            ON hrm_kpi_records(company_id, employee_id);
                        CREATE INDEX IF NOT EXISTS idx_hrm_disciplinary_company_employee
                            ON hrm_disciplinary_records(company_id, employee_id);
                        CREATE INDEX IF NOT EXISTS idx_hrm_promotions_company_employee
                            ON hrm_promotions(company_id, employee_id);

                        -- ESS lets employees maintain an address; column is additive-safe
                        ALTER TABLE IF EXISTS employees
                            ADD COLUMN IF NOT EXISTS address TEXT DEFAULT '';
"""


class HRMDataStore:
    def __init__(self):
        self._schema_ok = False
        self._ensure_tables()

    def _ensure_tables(self):
        self.ensure_schema()

    def ensure_schema(self):
        """Idempotent CREATE TABLE IF NOT EXISTS — safe to call from routes."""
        if self._schema_ok:
            return
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(_SCHEMA)
                conn.commit()
            self._schema_ok = True
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

    # ── Employees (read helpers for HRM screens) ─────────────────────────

    def list_employees(self, company_id: str = "default") -> List[Dict[str, Any]]:
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    """
                    SELECT employee_id, name, department, position, email,
                           phone_number, basic_salary, hire_date, is_active
                    FROM employees
                    WHERE company_id=%s AND is_active=TRUE
                    ORDER BY name
                    """,
                    (company_id,),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_employees failed: %s", e)
            return []

    def get_employee(self, employee_id: str, company_id: str = "default") -> Optional[Dict[str, Any]]:
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    "SELECT * FROM employees WHERE employee_id=%s AND company_id=%s",
                    (employee_id, company_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_employee failed: %s", e)
            return None

    def get_employee_for_user(self, company_id: str, username: str = "", email: str = "") -> Optional[Dict[str, Any]]:
        """Match the logged-in user to an employee record by email or name."""
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    """
                    SELECT * FROM employees
                    WHERE company_id=%s
                      AND (LOWER(email)=LOWER(%s) OR LOWER(email)=LOWER(%s)
                           OR LOWER(name)=LOWER(%s))
                    ORDER BY is_active DESC
                    LIMIT 1
                    """,
                    (company_id, username or "-", email or "-", username or "-"),
                )
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_employee_for_user failed: %s", e)
            return None

    def update_employee_contact(self, employee_id: str, company_id: str, data: Dict[str, Any]) -> bool:
        """ESS self-update — restricted to contact-style fields only."""
        allowed = ("phone_number", "email", "address", "bank_account")
        fields = {k: (data.get(k) or "") for k in allowed if k in data}
        if not fields:
            return False
        try:
            with get_tenant_cursor(company_id) as cur:
                sets = ", ".join(f"{k}=%s" for k in fields)
                cur.execute(
                    f"UPDATE employees SET {sets}, updated_date=CURRENT_TIMESTAMP "
                    "WHERE employee_id=%s AND company_id=%s",
                    (*fields.values(), employee_id, company_id),
                )
                return cur.rowcount > 0
        except Exception as e:
            logger.error("update_employee_contact failed: %s", e)
            return False

    # ── Leave administration ─────────────────────────────────────────────

    def list_leave_types(self, company_id: str = "default", active_only: bool = False) -> List[Dict[str, Any]]:
        try:
            with get_tenant_cursor(company_id) as cur:
                sql = "SELECT * FROM hrm_leave_types WHERE company_id=%s"
                if active_only:
                    sql += " AND active=TRUE"
                cur.execute(sql + " ORDER BY name", (company_id,))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_leave_types failed: %s", e)
            return []

    def create_leave_type(self, data: Dict[str, Any]) -> Optional[str]:
        leave_type_id = data.get("leave_type_id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO hrm_leave_types
                    (leave_type_id, company_id, name, days_per_year, description, active)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        leave_type_id, cid,
                        data.get("name", ""),
                        _num(data.get("days_per_year")) or 0,
                        data.get("description", ""),
                        bool(data.get("active", True)),
                    ),
                )
                return leave_type_id
        except Exception as e:
            logger.error("create_leave_type failed: %s", e)
            return None

    def update_leave_type(self, leave_type_id: str, company_id: str, data: Dict[str, Any]) -> bool:
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    """
                    UPDATE hrm_leave_types
                    SET name=%s, days_per_year=%s, description=%s, active=%s
                    WHERE leave_type_id=%s AND company_id=%s
                    """,
                    (
                        data.get("name", ""),
                        _num(data.get("days_per_year")) or 0,
                        data.get("description", ""),
                        str(data.get("active", "on")).lower() in ("on", "true", "1", "yes"),
                        leave_type_id, company_id,
                    ),
                )
                return cur.rowcount > 0
        except Exception as e:
            logger.error("update_leave_type failed: %s", e)
            return False

    def list_leave_requests(self, company_id: str = "default", status: str = "",
                            employee_id: str = "") -> List[Dict[str, Any]]:
        try:
            with get_tenant_cursor(company_id) as cur:
                sql = """
                    SELECT lr.*, COALESCE(e.name, lr.employee_id) AS employee_name,
                           COALESCE(e.department, '') AS department
                    FROM hrm_leave_requests lr
                    LEFT JOIN employees e
                      ON e.employee_id = lr.employee_id AND e.company_id = lr.company_id
                    WHERE lr.company_id=%s
                """
                params: List[Any] = [company_id]
                if status:
                    sql += " AND lr.status=%s"
                    params.append(status)
                if employee_id:
                    sql += " AND lr.employee_id=%s"
                    params.append(employee_id)
                sql += " ORDER BY lr.created_at DESC"
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_leave_requests failed: %s", e)
            return []

    def get_leave_balances(self, company_id: str = "default", employee_id: str = "",
                           year: Optional[int] = None) -> List[Dict[str, Any]]:
        """Entitlement (days/year per type) minus approved days for the year."""
        year = year or date.today().year
        try:
            with get_tenant_cursor(company_id) as cur:
                sql = """
                    SELECT e.employee_id, e.name AS employee_name,
                           COALESCE(e.department,'') AS department,
                           t.name AS leave_type, t.days_per_year AS entitlement,
                           COALESCE(u.used, 0) AS used,
                           t.days_per_year - COALESCE(u.used, 0) AS balance
                    FROM employees e
                    CROSS JOIN hrm_leave_types t
                    LEFT JOIN (
                        SELECT employee_id, LOWER(leave_type) AS lt,
                               SUM(days_requested) AS used
                        FROM hrm_leave_requests
                        WHERE company_id=%s AND status='approved'
                          AND EXTRACT(YEAR FROM start_date)=%s
                        GROUP BY employee_id, LOWER(leave_type)
                    ) u ON u.employee_id=e.employee_id AND u.lt=LOWER(t.name)
                    WHERE e.company_id=%s AND t.company_id=%s
                      AND e.is_active=TRUE AND t.active=TRUE
                """
                params: List[Any] = [company_id, year, company_id, company_id]
                if employee_id:
                    sql += " AND e.employee_id=%s"
                    params.append(employee_id)
                sql += " ORDER BY e.name, t.name"
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_leave_balances failed: %s", e)
            return []

    def get_leave_calendar(self, company_id: str = "default", month: str = "") -> List[Dict[str, Any]]:
        """All leave requests overlapping the given 'YYYY-MM' month."""
        try:
            try:
                y, m = (int(x) for x in (month or "").split("-")[:2])
            except (ValueError, TypeError):
                today = date.today()
                y, m = today.year, today.month
            first = date(y, m, 1)
            last = date(y, m, calendar.monthrange(y, m)[1])
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    """
                    SELECT lr.*, COALESCE(e.name, lr.employee_id) AS employee_name,
                           COALESCE(e.department,'') AS department
                    FROM hrm_leave_requests lr
                    LEFT JOIN employees e
                      ON e.employee_id = lr.employee_id AND e.company_id = lr.company_id
                    WHERE lr.company_id=%s AND lr.start_date <= %s AND lr.end_date >= %s
                    ORDER BY lr.start_date, employee_name
                    """,
                    (company_id, last, first),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_leave_calendar failed: %s", e)
            return []

    def get_leave_report(self, company_id: str = "default", year: Optional[int] = None) -> List[Dict[str, Any]]:
        """Per-type totals: requests, approved/pending/rejected, approved days."""
        year = year or date.today().year
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    """
                    SELECT leave_type,
                           COUNT(*) AS total_requests,
                           COUNT(*) FILTER (WHERE status='approved') AS approved,
                           COUNT(*) FILTER (WHERE status='pending')  AS pending,
                           COUNT(*) FILTER (WHERE status='rejected') AS rejected,
                           COALESCE(SUM(days_requested) FILTER (WHERE status='approved'), 0) AS approved_days
                    FROM hrm_leave_requests
                    WHERE company_id=%s AND EXTRACT(YEAR FROM start_date)=%s
                    GROUP BY leave_type
                    ORDER BY leave_type
                    """,
                    (company_id, year),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_leave_report failed: %s", e)
            return []

    # ── Grievances ───────────────────────────────────────────────────────

    def list_grievances(self, company_id: str = "default", status: str = "",
                        employee_id: str = "") -> List[Dict[str, Any]]:
        try:
            with get_tenant_cursor(company_id) as cur:
                sql = """
                    SELECT g.*, COALESCE(e.name, g.employee_id) AS employee_name
                    FROM hrm_grievances g
                    LEFT JOIN employees e
                      ON e.employee_id = g.employee_id AND e.company_id = g.company_id
                    WHERE g.company_id=%s
                """
                params: List[Any] = [company_id]
                if status:
                    sql += " AND g.status=%s"
                    params.append(status)
                if employee_id:
                    sql += " AND g.employee_id=%s"
                    params.append(employee_id)
                sql += " ORDER BY g.created_at DESC"
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_grievances failed: %s", e)
            return []

    def update_grievance_status(self, grievance_id: str, company_id: str,
                                status: str, resolution_note: str = "") -> bool:
        if status not in ("open", "in_review", "resolved"):
            return False
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    """
                    UPDATE hrm_grievances
                    SET status=%s, resolution_note=%s,
                        resolved_at=CASE WHEN %s='resolved' THEN CURRENT_TIMESTAMP ELSE NULL END
                    WHERE grievance_id=%s AND company_id=%s
                    """,
                    (status, resolution_note, status, grievance_id, company_id),
                )
                return cur.rowcount > 0
        except Exception as e:
            logger.error("update_grievance_status failed: %s", e)
            return False

    # ── Employee terms (grade / contract / salary structure) ────────────

    def get_employee_terms(self, employee_id: str, company_id: str = "default") -> Optional[Dict[str, Any]]:
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    "SELECT * FROM hrm_employee_terms WHERE company_id=%s AND employee_id=%s",
                    (company_id, employee_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_employee_terms failed: %s", e)
            return None

    def list_employee_terms(self, company_id: str = "default") -> List[Dict[str, Any]]:
        """Active employees with their terms (terms may be NULL)."""
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    """
                    SELECT e.employee_id, e.name, COALESCE(e.department,'') AS department,
                           COALESCE(e.position,'') AS position, e.basic_salary,
                           COALESCE(t.grade,'') AS grade,
                           COALESCE(t.contract_type,'') AS contract_type,
                           COALESCE(t.salary_structure,'') AS salary_structure
                    FROM employees e
                    LEFT JOIN hrm_employee_terms t
                      ON t.employee_id = e.employee_id AND t.company_id = e.company_id
                    WHERE e.company_id=%s AND e.is_active=TRUE
                    ORDER BY e.name
                    """,
                    (company_id,),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_employee_terms failed: %s", e)
            return []

    def upsert_employee_terms(self, data: Dict[str, Any]) -> bool:
        cid = data.get("company_id", "default")
        contract_type = data.get("contract_type", "permanent")
        if contract_type not in ("permanent", "contract", "temporary"):
            contract_type = "permanent"
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO hrm_employee_terms
                    (company_id, employee_id, grade, contract_type, salary_structure)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (company_id, employee_id) DO UPDATE
                    SET grade=EXCLUDED.grade,
                        contract_type=EXCLUDED.contract_type,
                        salary_structure=EXCLUDED.salary_structure,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        cid,
                        data.get("employee_id", ""),
                        data.get("grade", ""),
                        contract_type,
                        data.get("salary_structure", ""),
                    ),
                )
                return True
        except Exception as e:
            logger.error("upsert_employee_terms failed: %s", e)
            return False

    # ── KPI / OKR records ────────────────────────────────────────────────

    def list_kpi_records(self, company_id: str = "default", employee_id: str = "") -> List[Dict[str, Any]]:
        try:
            with get_tenant_cursor(company_id) as cur:
                sql = """
                    SELECT k.*, COALESCE(e.name, k.employee_id) AS employee_name
                    FROM hrm_kpi_records k
                    LEFT JOIN employees e
                      ON e.employee_id = k.employee_id AND e.company_id = k.company_id
                    WHERE k.company_id=%s
                """
                params: List[Any] = [company_id]
                if employee_id:
                    sql += " AND k.employee_id=%s"
                    params.append(employee_id)
                sql += " ORDER BY k.period DESC, employee_name"
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_kpi_records failed: %s", e)
            return []

    def create_kpi_record(self, data: Dict[str, Any]) -> Optional[str]:
        kpi_id = data.get("kpi_id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO hrm_kpi_records
                    (kpi_id, company_id, employee_id, period, objective, key_result,
                     target, actual, score)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        kpi_id, cid,
                        data.get("employee_id", ""),
                        data.get("period", ""),
                        data.get("objective", ""),
                        data.get("key_result", ""),
                        _num(data.get("target")),
                        _num(data.get("actual")),
                        _num(data.get("score")),
                    ),
                )
                return kpi_id
        except Exception as e:
            logger.error("create_kpi_record failed: %s", e)
            return None

    # ── Disciplinary records ─────────────────────────────────────────────

    def list_disciplinary_records(self, company_id: str = "default", employee_id: str = "") -> List[Dict[str, Any]]:
        try:
            with get_tenant_cursor(company_id) as cur:
                sql = """
                    SELECT d.*, COALESCE(e.name, d.employee_id) AS employee_name
                    FROM hrm_disciplinary_records d
                    LEFT JOIN employees e
                      ON e.employee_id = d.employee_id AND e.company_id = d.company_id
                    WHERE d.company_id=%s
                """
                params: List[Any] = [company_id]
                if employee_id:
                    sql += " AND d.employee_id=%s"
                    params.append(employee_id)
                sql += " ORDER BY d.incident_date DESC NULLS LAST, d.created_at DESC"
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_disciplinary_records failed: %s", e)
            return []

    def create_disciplinary_record(self, data: Dict[str, Any]) -> Optional[str]:
        record_id = data.get("record_id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO hrm_disciplinary_records
                    (record_id, company_id, employee_id, incident_date, category,
                     description, action_taken, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record_id, cid,
                        data.get("employee_id", ""),
                        _dt(data.get("incident_date")),
                        data.get("category", ""),
                        data.get("description", ""),
                        data.get("action_taken", ""),
                        data.get("status", "open"),
                    ),
                )
                return record_id
        except Exception as e:
            logger.error("create_disciplinary_record failed: %s", e)
            return None

    # ── Promotions / increments ──────────────────────────────────────────

    def list_promotions(self, company_id: str = "default", employee_id: str = "",
                        limit: int = 0) -> List[Dict[str, Any]]:
        try:
            with get_tenant_cursor(company_id) as cur:
                sql = """
                    SELECT p.*, COALESCE(e.name, p.employee_id) AS employee_name
                    FROM hrm_promotions p
                    LEFT JOIN employees e
                      ON e.employee_id = p.employee_id AND e.company_id = p.company_id
                    WHERE p.company_id=%s
                """
                params: List[Any] = [company_id]
                if employee_id:
                    sql += " AND p.employee_id=%s"
                    params.append(employee_id)
                sql += " ORDER BY p.effective_date DESC NULLS LAST, p.created_at DESC"
                if limit:
                    sql += " LIMIT %s"
                    params.append(limit)
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_promotions failed: %s", e)
            return []

    def create_promotion(self, data: Dict[str, Any]) -> Optional[str]:
        promotion_id = data.get("promotion_id") or str(uuid.uuid4())
        cid = data.get("company_id", "default")
        change_type = data.get("change_type", "promotion")
        if change_type not in ("promotion", "increment"):
            change_type = "promotion"
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """
                    INSERT INTO hrm_promotions
                    (promotion_id, company_id, employee_id, effective_date, change_type,
                     from_grade, to_grade, from_salary, to_salary, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        promotion_id, cid,
                        data.get("employee_id", ""),
                        _dt(data.get("effective_date")),
                        change_type,
                        data.get("from_grade", ""),
                        data.get("to_grade", ""),
                        _num(data.get("from_salary")),
                        _num(data.get("to_salary")),
                        data.get("notes", ""),
                    ),
                )
                return promotion_id
        except Exception as e:
            logger.error("create_promotion failed: %s", e)
            return None

    # ── HR analytics dashboard ───────────────────────────────────────────

    def get_hr_dashboard(self, company_id: str = "default") -> Dict[str, Any]:
        """Aggregates for the /hrm/analytics HTML dashboard."""
        result: Dict[str, Any] = {
            "headcount": 0,
            "headcount_by_department": [],
            "leave_entitlement_days": 0.0,
            "leave_used_days": 0.0,
            "leave_utilization_pct": 0.0,
            "pending_leave_requests": 0,
            "open_grievances": 0,
            "in_review_grievances": 0,
            "kpi_avg_score": None,
            "recent_promotions": [],
        }
        year = date.today().year
        try:
            with get_tenant_cursor(company_id) as cur:
                cur.execute(
                    """
                    SELECT COALESCE(NULLIF(department, ''), 'Unassigned') AS department,
                           COUNT(*) AS headcount
                    FROM employees
                    WHERE company_id=%s AND is_active=TRUE
                    GROUP BY 1 ORDER BY 2 DESC, 1
                    """,
                    (company_id,),
                )
                result["headcount_by_department"] = [dict(r) for r in cur.fetchall()]
                result["headcount"] = sum(int(r["headcount"]) for r in result["headcount_by_department"])

                cur.execute(
                    "SELECT COALESCE(SUM(days_per_year),0) AS days FROM hrm_leave_types "
                    "WHERE company_id=%s AND active=TRUE",
                    (company_id,),
                )
                per_head = float((cur.fetchone() or {}).get("days", 0) or 0)
                result["leave_entitlement_days"] = per_head * result["headcount"]

                cur.execute(
                    """
                    SELECT COALESCE(SUM(days_requested),0) AS days
                    FROM hrm_leave_requests
                    WHERE company_id=%s AND status='approved'
                      AND EXTRACT(YEAR FROM start_date)=%s
                    """,
                    (company_id, year),
                )
                result["leave_used_days"] = float((cur.fetchone() or {}).get("days", 0) or 0)
                if result["leave_entitlement_days"]:
                    result["leave_utilization_pct"] = round(
                        100.0 * result["leave_used_days"] / result["leave_entitlement_days"], 1
                    )

                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM hrm_leave_requests "
                    "WHERE company_id=%s AND status='pending'",
                    (company_id,),
                )
                result["pending_leave_requests"] = (cur.fetchone() or {}).get("cnt", 0)

                cur.execute(
                    "SELECT COUNT(*) FILTER (WHERE status='open') AS open_cnt, "
                    "       COUNT(*) FILTER (WHERE status='in_review') AS review_cnt "
                    "FROM hrm_grievances WHERE company_id=%s",
                    (company_id,),
                )
                row = cur.fetchone() or {}
                result["open_grievances"] = row.get("open_cnt", 0) or 0
                result["in_review_grievances"] = row.get("review_cnt", 0) or 0

                cur.execute(
                    "SELECT AVG(score) AS avg_score FROM hrm_kpi_records "
                    "WHERE company_id=%s AND score IS NOT NULL",
                    (company_id,),
                )
                avg = (cur.fetchone() or {}).get("avg_score")
                result["kpi_avg_score"] = round(float(avg), 2) if avg is not None else None
        except Exception as e:
            logger.error("get_hr_dashboard failed: %s", e)
        result["recent_promotions"] = self.list_promotions(company_id, limit=5)
        return result


hrm_store = HRMDataStore()
