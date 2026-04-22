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
"""


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
                         data.get("status","planning"), data.get("start_date"), data.get("end_date"),
                         data.get("total_budget", 0), data.get("created_by",""))
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
                         data.get("start_date"), data.get("end_date"), data.get("total_budget",0),
                         data.get("material_costs",0), data.get("consultant_fees",0), data.get("internal_labor",0),
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
                         data.get("depends_on") or None, data.get("est_hours",0), data.get("due_date"))
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
                         data.get("depends_on") or None, data.get("est_hours",0), data.get("actual_hours",0),
                         data.get("due_date"), task_id)
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
