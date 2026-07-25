"""
Project Management Data Store — PostgreSQL backend.
Tables: pm_projects, pm_wbs_elements, pm_tasks, pm_site_reports
"""
from __future__ import annotations
import logging, uuid
from datetime import datetime
from typing import List, Optional
from db import get_conn

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pm_projects (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    name            TEXT NOT NULL,
    classification  TEXT NOT NULL DEFAULT 'internal',
    status          TEXT NOT NULL DEFAULT 'planning',  -- planning|active|on_hold|completed|cancelled
    start_date      DATE,
    end_date        DATE,
    total_budget    NUMERIC(18,2) NOT NULL DEFAULT 0,
    material_costs  NUMERIC(18,2) NOT NULL DEFAULT 0,
    consultant_fees NUMERIC(18,2) NOT NULL DEFAULT 0,
    internal_labor  NUMERIC(18,2) NOT NULL DEFAULT 0,
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pm_projects_company ON pm_projects(company_id);

CREATE TABLE IF NOT EXISTS pm_wbs_elements (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    parent_id   TEXT,
    title       TEXT NOT NULL,
    sequence    INT  NOT NULL DEFAULT 0,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pm_wbs_project ON pm_wbs_elements(project_id);

CREATE TABLE IF NOT EXISTS pm_tasks (
    id              TEXT PRIMARY KEY,
    wbs_element_id  TEXT NOT NULL,
    project_id      TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    assigned_to     TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'not_started',  -- not_started|in_progress|completed|blocked
    priority        TEXT NOT NULL DEFAULT 'medium',        -- low|medium|high|critical
    depends_on      TEXT,  -- task id prerequisite
    est_hours       NUMERIC(8,2) NOT NULL DEFAULT 0,
    actual_hours    NUMERIC(8,2) NOT NULL DEFAULT 0,
    due_date        DATE,
    completed_at    TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pm_tasks_wbs     ON pm_tasks(wbs_element_id);
CREATE INDEX IF NOT EXISTS idx_pm_tasks_project ON pm_tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_pm_tasks_status  ON pm_tasks(status);

CREATE TABLE IF NOT EXISTS pm_site_reports (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    report_date     DATE NOT NULL,
    weather         TEXT NOT NULL DEFAULT '',
    progress_notes  TEXT NOT NULL DEFAULT '',
    issues_logged   TEXT NOT NULL DEFAULT '',
    submitted_by    TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pm_site_reports_project ON pm_site_reports(project_id);

CREATE TABLE IF NOT EXISTS pm_contractors (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'contractor',  -- contractor|consultant
    specialty   TEXT NOT NULL DEFAULT '',
    phone       TEXT NOT NULL DEFAULT '',
    email       TEXT NOT NULL DEFAULT '',
    tin         TEXT NOT NULL DEFAULT '',
    rating      INT  NOT NULL DEFAULT 0,
    notes       TEXT NOT NULL DEFAULT '',
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pm_contractors_company ON pm_contractors(company_id);

CREATE TABLE IF NOT EXISTS pm_payments (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    company_id    TEXT NOT NULL,
    payment_date  DATE,
    amount        NUMERIC(18,2) NOT NULL DEFAULT 0,
    payee         TEXT NOT NULL DEFAULT '',
    payment_type  TEXT NOT NULL DEFAULT 'other',  -- material|consultant|labor|other
    reference     TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pm_payments_project ON pm_payments(project_id);
CREATE INDEX IF NOT EXISTS idx_pm_payments_company ON pm_payments(company_id);
"""


def _opt(value):
    """Empty form fields arrive as '' — Postgres rejects '' for DATE/NUMERIC."""
    return value if value not in ("", None) else None


def _num(value):
    return value if value not in ("", None) else 0


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("project_mgmt schema ready")
    except Exception as e:
        logger.error("project_mgmt schema init failed: %s", e)


class ProjectDataStore:

    def ensure_schema(self):
        ensure_schema()

    # Projects
    def get_projects(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM pm_projects WHERE company_id=%s ORDER BY created_at DESC",
                        (company_id,)
                    )
                    rows = [dict(r) for r in cur.fetchall()]
                    for p in rows:
                        p["budget_remaining"] = float(p["total_budget"]) - (
                            float(p["material_costs"]) + float(p["consultant_fees"]) + float(p["internal_labor"])
                        )
                        p["budget_remaining"] = round(p["budget_remaining"], 2)
                    return rows
        except Exception as e:
            logger.error("get_projects: %s", e); return []

    def get_project(self, project_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM pm_projects WHERE id=%s AND company_id=%s", (project_id, company_id))
                    row = cur.fetchone()
                    if not row:
                        return None
                    p = dict(row)
                    p["budget_remaining"] = round(
                        float(p["total_budget"]) - float(p["material_costs"]) - float(p["consultant_fees"]) - float(p["internal_labor"]), 2
                    )
                    return p
        except Exception as e:
            logger.error("get_project: %s", e); return None

    def create_project(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            pid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO pm_projects(id,company_id,name,classification,status,start_date,end_date,total_budget,created_by)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (pid, company_id, data["name"], data.get("classification","internal"),
                         data.get("status","planning"), _opt(data.get("start_date")), _opt(data.get("end_date")),
                         _num(data.get("total_budget")), data.get("created_by",""))
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_project: %s", e); return None

    def update_project(self, project_id: str, company_id: str, data: dict) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE pm_projects SET name=%s,classification=%s,status=%s,start_date=%s,end_date=%s,
                           total_budget=%s,material_costs=%s,consultant_fees=%s,internal_labor=%s,updated_at=NOW()
                           WHERE id=%s AND company_id=%s""",
                        (data["name"], data.get("classification","internal"), data.get("status","planning"),
                         _opt(data.get("start_date")), _opt(data.get("end_date")), _num(data.get("total_budget")),
                         _num(data.get("material_costs")), _num(data.get("consultant_fees")), _num(data.get("internal_labor")),
                         project_id, company_id)
                    )
            return True
        except Exception as e:
            logger.error("update_project: %s", e); return False

    def delete_project(self, project_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM pm_tasks WHERE project_id=%s", (project_id,))
                    cur.execute("DELETE FROM pm_wbs_elements WHERE project_id=%s", (project_id,))
                    cur.execute("DELETE FROM pm_site_reports WHERE project_id=%s", (project_id,))
                    cur.execute("DELETE FROM pm_projects WHERE id=%s AND company_id=%s", (project_id, company_id))
            return True
        except Exception as e:
            logger.error("delete_project: %s", e); return False

    # WBS
    def get_wbs(self, project_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM pm_wbs_elements WHERE project_id=%s ORDER BY sequence",
                        (project_id,)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_wbs: %s", e); return []

    def create_wbs_element(self, project_id: str, title: str, parent_id: str = None, sequence: int = 0) -> Optional[dict]:
        try:
            wid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO pm_wbs_elements(id,project_id,parent_id,title,sequence) VALUES(%s,%s,%s,%s,%s) RETURNING *",
                        (wid, project_id, parent_id, title, sequence)
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_wbs_element: %s", e); return None

    def delete_wbs_element(self, wbs_id: str, project_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM pm_tasks WHERE wbs_element_id=%s", (wbs_id,))
                    cur.execute("DELETE FROM pm_wbs_elements WHERE id=%s AND project_id=%s", (wbs_id, project_id))
            return True
        except Exception as e:
            logger.error("delete_wbs_element: %s", e); return False

    # Tasks
    def get_tasks(self, project_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM pm_tasks WHERE project_id=%s ORDER BY created_at",
                        (project_id,)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_tasks: %s", e); return []

    def get_task(self, task_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM pm_tasks WHERE id=%s", (task_id,))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("get_task: %s", e); return None

    def create_task(self, data: dict) -> Optional[dict]:
        try:
            tid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO pm_tasks(id,wbs_element_id,project_id,title,description,assigned_to,status,priority,depends_on,est_hours,due_date)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (tid, data["wbs_element_id"], data["project_id"], data["title"],
                         data.get("description",""), data.get("assigned_to",""),
                         data.get("status","not_started"), data.get("priority","medium"),
                         data.get("depends_on") or None, _num(data.get("est_hours")), _opt(data.get("due_date")))
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_task: %s", e); return None

    def update_task_status(self, task_id: str, new_status: str) -> dict:
        """Returns {ok, error} — enforces dependency rule."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM pm_tasks WHERE id=%s", (task_id,))
                    task = cur.fetchone()
                    if not task:
                        return {"ok": False, "error": "Task not found"}
                    if new_status == "in_progress" and task["depends_on"]:
                        cur.execute("SELECT status FROM pm_tasks WHERE id=%s", (task["depends_on"],))
                        dep = cur.fetchone()
                        if dep and dep["status"] != "completed":
                            return {"ok": False, "error": "Prerequisite task must be completed first"}
                    completed_at = "NOW()" if new_status == "completed" else "NULL"
                    cur.execute(
                        f"UPDATE pm_tasks SET status=%s, completed_at={'NOW()' if new_status=='completed' else 'NULL'}, updated_at=NOW() WHERE id=%s",
                        (new_status, task_id)
                    )
            return {"ok": True}
        except Exception as e:
            logger.error("update_task_status: %s", e); return {"ok": False, "error": str(e)}

    def update_task(self, task_id: str, data: dict) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE pm_tasks SET title=%s,description=%s,assigned_to=%s,status=%s,priority=%s,
                           depends_on=%s,est_hours=%s,actual_hours=%s,due_date=%s,updated_at=NOW()
                           WHERE id=%s""",
                        (data["title"], data.get("description",""), data.get("assigned_to",""),
                         data.get("status","not_started"), data.get("priority","medium"),
                         data.get("depends_on") or None, _num(data.get("est_hours")), _num(data.get("actual_hours")),
                         _opt(data.get("due_date")), task_id)
                    )
            return True
        except Exception as e:
            logger.error("update_task: %s", e); return False

    def delete_task(self, task_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM pm_tasks WHERE id=%s", (task_id,))
            return True
        except Exception as e:
            logger.error("delete_task: %s", e); return False

    def get_wbs_progress(self, wbs_id: str) -> dict:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS total FROM pm_tasks WHERE wbs_element_id=%s", (wbs_id,))
                    total = cur.fetchone()["total"]
                    cur.execute("SELECT COUNT(*) AS done FROM pm_tasks WHERE wbs_element_id=%s AND status='completed'", (wbs_id,))
                    done = cur.fetchone()["done"]
                    pct = round((done / total * 100) if total else 0, 1)
                    return {"total": total, "completed": done, "pct": pct}
        except Exception as e:
            logger.error("get_wbs_progress: %s", e); return {"total": 0, "completed": 0, "pct": 0}

    # Site Reports
    def get_site_reports(self, project_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM pm_site_reports WHERE project_id=%s ORDER BY report_date DESC",
                        (project_id,)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_site_reports: %s", e); return []

    def create_site_report(self, project_id: str, data: dict) -> Optional[dict]:
        try:
            rid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO pm_site_reports(id,project_id,report_date,weather,progress_notes,issues_logged,submitted_by)
                           VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (rid, project_id, data["report_date"], data.get("weather",""),
                         data.get("progress_notes",""), data.get("issues_logged",""), data.get("submitted_by",""))
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_site_report: %s", e); return None

    # Contractors & Consultants
    def get_contractors(self, company_id: str, type_filter: str = None,
                        include_inactive: bool = True) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    sql = "SELECT * FROM pm_contractors WHERE company_id=%s"
                    params = [company_id]
                    if type_filter in ("contractor", "consultant"):
                        sql += " AND type=%s"
                        params.append(type_filter)
                    if not include_inactive:
                        sql += " AND is_active=TRUE"
                    sql += " ORDER BY name"
                    cur.execute(sql, tuple(params))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_contractors: %s", e); return []

    def get_contractor(self, contractor_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM pm_contractors WHERE id=%s AND company_id=%s",
                                (contractor_id, company_id))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("get_contractor: %s", e); return None

    def create_contractor(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            cid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO pm_contractors(id,company_id,name,type,specialty,phone,email,tin,rating,notes)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (cid, company_id, data["name"],
                         data.get("type", "contractor") if data.get("type") in ("contractor", "consultant") else "contractor",
                         data.get("specialty", ""), data.get("phone", ""), data.get("email", ""),
                         data.get("tin", ""), int(_num(data.get("rating"))), data.get("notes", ""))
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_contractor: %s", e); return None

    def update_contractor(self, contractor_id: str, company_id: str, data: dict) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE pm_contractors SET name=%s,type=%s,specialty=%s,phone=%s,email=%s,
                           tin=%s,rating=%s,notes=%s WHERE id=%s AND company_id=%s""",
                        (data["name"],
                         data.get("type", "contractor") if data.get("type") in ("contractor", "consultant") else "contractor",
                         data.get("specialty", ""), data.get("phone", ""), data.get("email", ""),
                         data.get("tin", ""), int(_num(data.get("rating"))), data.get("notes", ""),
                         contractor_id, company_id)
                    )
            return True
        except Exception as e:
            logger.error("update_contractor: %s", e); return False

    def deactivate_contractor(self, contractor_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE pm_contractors SET is_active=FALSE WHERE id=%s AND company_id=%s",
                                (contractor_id, company_id))
            return True
        except Exception as e:
            logger.error("deactivate_contractor: %s", e); return False

    # Payments
    def get_payments(self, project_id: str, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT * FROM pm_payments WHERE project_id=%s AND company_id=%s
                           ORDER BY payment_date DESC NULLS LAST, created_at DESC""",
                        (project_id, company_id)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_payments: %s", e); return []

    def create_payment(self, project_id: str, company_id: str, data: dict) -> Optional[dict]:
        try:
            pid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO pm_payments(id,project_id,company_id,payment_date,amount,payee,payment_type,reference,notes)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (pid, project_id, company_id, _opt(data.get("payment_date")),
                         _num(data.get("amount")), data.get("payee", ""),
                         data.get("payment_type", "other") if data.get("payment_type") in ("material", "consultant", "labor", "other") else "other",
                         data.get("reference", ""), data.get("notes", ""))
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_payment: %s", e); return None

    # Reports
    def budget_vs_actual(self, company_id: str) -> dict:
        """Per-project budget vs actual costs incl. payments, plus company totals."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT p.*, COALESCE(pay.paid, 0) AS paid_to_date
                           FROM pm_projects p
                           LEFT JOIN (SELECT project_id, SUM(amount) AS paid
                                      FROM pm_payments GROUP BY project_id) pay
                             ON pay.project_id = p.id
                           WHERE p.company_id=%s
                           ORDER BY p.name""",
                        (company_id,)
                    )
                    rows = []
                    totals = {"budget": 0.0, "actual": 0.0, "variance": 0.0, "paid_to_date": 0.0}
                    for r in cur.fetchall():
                        p = dict(r)
                        budget = float(p["total_budget"] or 0)
                        actual = (float(p["material_costs"] or 0) + float(p["consultant_fees"] or 0)
                                  + float(p["internal_labor"] or 0))
                        variance = round(budget - actual, 2)
                        pct_used = round(actual / budget * 100, 1) if budget else 0.0
                        p.update(budget=round(budget, 2), actual=round(actual, 2),
                                 variance=variance, pct_used=pct_used,
                                 over_budget=actual > budget,
                                 paid_to_date=round(float(p["paid_to_date"] or 0), 2))
                        rows.append(p)
                        totals["budget"] += budget
                        totals["actual"] += actual
                        totals["variance"] += variance
                        totals["paid_to_date"] += p["paid_to_date"]
                    totals = {k: round(v, 2) for k, v in totals.items()}
                    totals["pct_used"] = round(totals["actual"] / totals["budget"] * 100, 1) if totals["budget"] else 0.0
                    return {"rows": rows, "totals": totals}
        except Exception as e:
            logger.error("budget_vs_actual: %s", e)
            return {"rows": [], "totals": {"budget": 0, "actual": 0, "variance": 0,
                                           "paid_to_date": 0, "pct_used": 0}}

    def progress_report(self, company_id: str) -> List[dict]:
        """Per-project task counts by status, completion %, WBS progress, overdue count."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT p.id, p.name, p.status,
                                  COUNT(t.id) AS total_tasks,
                                  COUNT(t.id) FILTER (WHERE t.status='not_started') AS not_started,
                                  COUNT(t.id) FILTER (WHERE t.status='in_progress') AS in_progress,
                                  COUNT(t.id) FILTER (WHERE t.status='completed')   AS completed,
                                  COUNT(t.id) FILTER (WHERE t.status='blocked')     AS blocked,
                                  COUNT(t.id) FILTER (WHERE t.due_date IS NOT NULL
                                                        AND t.due_date < CURRENT_DATE
                                                        AND t.status <> 'completed') AS overdue
                           FROM pm_projects p
                           LEFT JOIN pm_tasks t ON t.project_id = p.id
                           WHERE p.company_id=%s
                           GROUP BY p.id, p.name, p.status
                           ORDER BY p.name""",
                        (company_id,)
                    )
                    projects = {r["id"]: dict(r) for r in cur.fetchall()}
                    # WBS progress per project
                    cur.execute(
                        """SELECT w.project_id, w.id,
                                  COUNT(t.id) AS total,
                                  COUNT(t.id) FILTER (WHERE t.status='completed') AS done
                           FROM pm_wbs_elements w
                           JOIN pm_projects p ON p.id = w.project_id
                           LEFT JOIN pm_tasks t ON t.wbs_element_id = w.id
                           WHERE p.company_id=%s
                           GROUP BY w.project_id, w.id""",
                        (company_id,)
                    )
                    wbs_by_project = {}
                    for r in cur.fetchall():
                        wbs_by_project.setdefault(r["project_id"], []).append(dict(r))
                    rows = []
                    for pid, p in projects.items():
                        total = p["total_tasks"] or 0
                        p["completion_pct"] = round(p["completed"] / total * 100, 1) if total else 0.0
                        wbs = wbs_by_project.get(pid, [])
                        p["wbs_total"] = len(wbs)
                        p["wbs_completed"] = sum(1 for w in wbs if w["total"] and w["done"] == w["total"])
                        p["wbs_pct"] = round(p["wbs_completed"] / len(wbs) * 100, 1) if wbs else 0.0
                        rows.append(p)
                    return rows
        except Exception as e:
            logger.error("progress_report: %s", e); return []

    def delay_analysis(self, company_id: str) -> dict:
        """Overdue projects (past end_date, not completed) and late tasks, with average delays."""
        result = {"overdue_projects": [], "late_tasks": [],
                  "avg_project_delay": 0.0, "avg_task_delay": 0.0}
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT id, name, status, end_date,
                                  (CURRENT_DATE - end_date) AS days_overdue
                           FROM pm_projects
                           WHERE company_id=%s
                             AND end_date IS NOT NULL
                             AND end_date < CURRENT_DATE
                             AND status NOT IN ('completed', 'cancelled')
                           ORDER BY days_overdue DESC""",
                        (company_id,)
                    )
                    result["overdue_projects"] = [dict(r) for r in cur.fetchall()]
                    cur.execute(
                        """SELECT t.id, t.title, t.assigned_to, t.status, t.due_date,
                                  p.name AS project_name, p.id AS project_id,
                                  (CURRENT_DATE - t.due_date) AS days_late
                           FROM pm_tasks t
                           JOIN pm_projects p ON p.id = t.project_id
                           WHERE p.company_id=%s
                             AND t.due_date IS NOT NULL
                             AND t.due_date < CURRENT_DATE
                             AND t.status <> 'completed'
                           ORDER BY days_late DESC""",
                        (company_id,)
                    )
                    result["late_tasks"] = [dict(r) for r in cur.fetchall()]
            if result["overdue_projects"]:
                result["avg_project_delay"] = round(
                    sum(p["days_overdue"] or 0 for p in result["overdue_projects"])
                    / len(result["overdue_projects"]), 1)
            if result["late_tasks"]:
                result["avg_task_delay"] = round(
                    sum(t["days_late"] or 0 for t in result["late_tasks"])
                    / len(result["late_tasks"]), 1)
            return result
        except Exception as e:
            logger.error("delay_analysis: %s", e); return result

    def get_stats(self, company_id: str) -> dict:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS c FROM pm_projects WHERE company_id=%s", (company_id,))
                    total_projects = cur.fetchone()["c"]
                    cur.execute("SELECT COUNT(*) AS c FROM pm_projects WHERE company_id=%s AND status='active'", (company_id,))
                    active = cur.fetchone()["c"]
                    cur.execute(
                        """SELECT COUNT(*) AS c FROM pm_tasks t
                           JOIN pm_projects p ON t.project_id=p.id
                           WHERE p.company_id=%s AND t.status='completed'""",
                        (company_id,)
                    )
                    completed_tasks = cur.fetchone()["c"]
                    cur.execute(
                        """SELECT COUNT(*) AS c FROM pm_tasks t
                           JOIN pm_projects p ON t.project_id=p.id
                           WHERE p.company_id=%s""",
                        (company_id,)
                    )
                    total_tasks = cur.fetchone()["c"]
                    return {
                        "total_projects": total_projects, "active_projects": active,
                        "total_tasks": total_tasks, "completed_tasks": completed_tasks
                    }
        except Exception as e:
            logger.error("get_stats: %s", e)
            return {"total_projects": 0, "active_projects": 0, "total_tasks": 0, "completed_tasks": 0}


project_store = ProjectDataStore()
