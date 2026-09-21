"""
Report Builder Data Store — PostgreSQL backend.
Tables: report_definitions, report_schedules, report_runs
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Set

from db import get_conn, get_cursor

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS report_definitions (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL DEFAULT 'default',
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    source_key   TEXT NOT NULL,
    columns      JSONB NOT NULL DEFAULT '[]'::jsonb,
    filters      JSONB NOT NULL DEFAULT '[]'::jsonb,
    group_by     JSONB NOT NULL DEFAULT '[]'::jsonb,
    aggregates   JSONB NOT NULL DEFAULT '[]'::jsonb,
    sort         JSONB NOT NULL DEFAULT '[]'::jsonb,
    date_column  TEXT,
    date_preset  TEXT NOT NULL DEFAULT 'all',
    "limit"      INT  NOT NULL DEFAULT 5000,
    chart        JSONB,
    is_shared    BOOLEAN NOT NULL DEFAULT TRUE,
    is_template  BOOLEAN NOT NULL DEFAULT FALSE,
    created_by   TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_report_definitions_company ON report_definitions(company_id);

CREATE TABLE IF NOT EXISTS report_schedules (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    report_id     TEXT NOT NULL,
    frequency     TEXT NOT NULL DEFAULT 'daily',     -- daily|weekly|monthly
    hour          INT  NOT NULL DEFAULT 7,
    minute        INT  NOT NULL DEFAULT 0,
    weekday       INT,                                -- 0=Mon .. 6=Sun (weekly)
    day_of_month  INT,                                -- 1..31 (monthly, clamped)
    format        TEXT NOT NULL DEFAULT 'pdf',       -- pdf|xlsx|csv
    recipients    TEXT NOT NULL DEFAULT '',          -- comma-separated emails
    subject       TEXT NOT NULL DEFAULT '',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    last_run_at   TIMESTAMP,
    next_run_at   TIMESTAMP,
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_report_schedules_company ON report_schedules(company_id);
CREATE INDEX IF NOT EXISTS idx_report_schedules_next ON report_schedules(is_active, next_run_at);

CREATE TABLE IF NOT EXISTS report_runs (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    report_id     TEXT NOT NULL,
    schedule_id   TEXT,
    started_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMP,
    status        TEXT NOT NULL DEFAULT 'running',   -- running|ok|error
    row_count     INT  NOT NULL DEFAULT 0,
    error         TEXT,
    output_path   TEXT,
    output_format TEXT,
    triggered_by  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_report_runs_company ON report_runs(company_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_report_runs_report ON report_runs(report_id);
"""

_JSON_FIELDS = ("columns", "filters", "group_by", "aggregates", "sort", "chart")


def _opt(value):
    """'' → None (Postgres rejects '' for INT/TIMESTAMP)."""
    return value if value not in ("", None) else None


def _int(value, default=None):
    try:
        return int(value) if value not in ("", None) else default
    except (TypeError, ValueError):
        return default


def _jsonb(value, default="[]"):
    if value is None:
        return default if default is not None else None
    return json.dumps(value)


def _row(r) -> Optional[dict]:
    if not r:
        return None
    d = dict(r)
    for k in _JSON_FIELDS:
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("reports schema ready")
    except Exception as e:
        logger.error("reports schema init failed: %s", e)


class ReportsDataStore:
    _COLUMNS_TTL = 300  # seconds

    def __init__(self):
        self._columns_cache: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    def ensure_schema(self):
        ensure_schema()

    # ── runtime schema introspection ─────────────────────────────
    def table_columns(self, table_name: str) -> Set[str]:
        """Physical columns of a table (cached 5 min). Empty set = table missing."""
        with self._lock:
            hit = self._columns_cache.get(table_name)
            if hit and hit[1] > time.time():
                return hit[0]
        cols: Set[str] = set()
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name=%s AND table_schema = ANY(current_schemas(false))",
                    (table_name,))
                cols = {r["column_name"] for r in cur.fetchall()}
        except Exception as e:
            logger.error("table_columns(%s) failed: %s", table_name, e)
            return set()
        with self._lock:
            self._columns_cache[table_name] = (cols, time.time() + self._COLUMNS_TTL)
        return cols

    # ── definitions ──────────────────────────────────────────────
    def list_definitions(self, company_id: str, source_key: str = None) -> List[dict]:
        try:
            sql = "SELECT * FROM report_definitions WHERE company_id=%s"
            params = [company_id]
            if source_key:
                sql += " AND source_key=%s"; params.append(source_key)
            sql += " ORDER BY is_template DESC, name"
            with get_cursor() as cur:
                cur.execute(sql, tuple(params))
                return [_row(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_definitions: %s", e); return []

    def get_definition(self, report_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM report_definitions WHERE id=%s AND company_id=%s",
                            (report_id, company_id))
                return _row(cur.fetchone())
        except Exception as e:
            logger.error("get_definition: %s", e); return None

    def create_definition(self, company_id: str, d: dict) -> Optional[dict]:
        try:
            rid = str(uuid.uuid4())
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO report_definitions
                       (id,company_id,name,description,source_key,columns,filters,group_by,aggregates,
                        sort,date_column,date_preset,"limit",chart,is_shared,is_template,created_by)
                       VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,
                               %s,%s,%s,%s::jsonb,%s,%s,%s) RETURNING *""",
                    (rid, company_id, d.get("name") or "Untitled report", d.get("description") or "",
                     d["source_key"], _jsonb(d.get("columns") or []), _jsonb(d.get("filters") or []),
                     _jsonb(d.get("group_by") or []), _jsonb(d.get("aggregates") or []),
                     _jsonb(d.get("sort") or []), _opt(d.get("date_column")),
                     d.get("date_preset") or "all", _int(d.get("limit"), 5000),
                     _jsonb(d.get("chart"), None), bool(d.get("is_shared", True)),
                     bool(d.get("is_template", False)), d.get("created_by") or ""))
                return _row(cur.fetchone())
        except Exception as e:
            logger.error("create_definition: %s", e); return None

    def update_definition(self, report_id: str, company_id: str, d: dict) -> bool:
        try:
            with get_cursor() as cur:
                cur.execute(
                    """UPDATE report_definitions SET name=%s,description=%s,source_key=%s,
                       columns=%s::jsonb,filters=%s::jsonb,group_by=%s::jsonb,aggregates=%s::jsonb,
                       sort=%s::jsonb,date_column=%s,date_preset=%s,"limit"=%s,chart=%s::jsonb,
                       is_shared=%s,updated_at=NOW()
                       WHERE id=%s AND company_id=%s""",
                    (d.get("name") or "Untitled report", d.get("description") or "", d["source_key"],
                     _jsonb(d.get("columns") or []), _jsonb(d.get("filters") or []),
                     _jsonb(d.get("group_by") or []), _jsonb(d.get("aggregates") or []),
                     _jsonb(d.get("sort") or []), _opt(d.get("date_column")),
                     d.get("date_preset") or "all", _int(d.get("limit"), 5000),
                     _jsonb(d.get("chart"), None), bool(d.get("is_shared", True)),
                     report_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("update_definition: %s", e); return False

    def delete_definition(self, report_id: str, company_id: str) -> bool:
        try:
            with get_cursor() as cur:
                cur.execute("DELETE FROM report_schedules WHERE report_id=%s AND company_id=%s",
                            (report_id, company_id))
                cur.execute("DELETE FROM report_definitions WHERE id=%s AND company_id=%s",
                            (report_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("delete_definition: %s", e); return False

    def seed_templates(self, company_id: str) -> int:
        """Insert the prebuilt templates once per company (skips sources missing columns)."""
        try:
            from reports_catalog import PREBUILT_TEMPLATES, SOURCES, template_fits, validate_definition
            with get_cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM report_definitions WHERE company_id=%s AND is_template",
                            (company_id,))
                if (cur.fetchone() or {}).get("c", 0):
                    return 0
            created = 0
            for tpl in PREBUILT_TEMPLATES:
                source = SOURCES.get(tpl["source_key"])
                if not source:
                    continue
                available = self.table_columns(source.table)
                if not template_fits(tpl, available):
                    logger.info("seed_templates: skipping %r (columns missing)", tpl["name"])
                    continue
                try:
                    d = validate_definition(tpl)
                except Exception as e:
                    logger.warning("seed_templates: %r invalid: %s", tpl["name"], e)
                    continue
                d.update(is_template=True, created_by="system")
                if self.create_definition(company_id, d):
                    created += 1
            return created
        except Exception as e:
            logger.error("seed_templates: %s", e); return 0

    # ── schedules ────────────────────────────────────────────────
    def list_schedules(self, company_id: str) -> List[dict]:
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT s.*, d.name AS report_name FROM report_schedules s
                       LEFT JOIN report_definitions d ON d.id = s.report_id
                       WHERE s.company_id=%s ORDER BY s.is_active DESC, s.next_run_at NULLS LAST""",
                    (company_id,))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_schedules: %s", e); return []

    def get_schedule(self, schedule_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT s.*, d.name AS report_name FROM report_schedules s
                       LEFT JOIN report_definitions d ON d.id = s.report_id
                       WHERE s.id=%s AND s.company_id=%s""", (schedule_id, company_id))
                r = cur.fetchone()
                return dict(r) if r else None
        except Exception as e:
            logger.error("get_schedule: %s", e); return None

    def create_schedule(self, company_id: str, d: dict, next_run_at: datetime) -> Optional[dict]:
        try:
            sid = str(uuid.uuid4())
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO report_schedules
                       (id,company_id,report_id,frequency,hour,minute,weekday,day_of_month,format,
                        recipients,subject,is_active,next_run_at,created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (sid, company_id, d["report_id"], d.get("frequency") or "daily",
                     _int(d.get("hour"), 7), _int(d.get("minute"), 0), _int(d.get("weekday")),
                     _int(d.get("day_of_month")), d.get("format") or "pdf",
                     d.get("recipients") or "", d.get("subject") or "",
                     bool(d.get("is_active", True)), next_run_at, d.get("created_by") or ""))
                r = cur.fetchone()
                return dict(r) if r else None
        except Exception as e:
            logger.error("create_schedule: %s", e); return None

    def update_schedule(self, schedule_id: str, company_id: str, d: dict, next_run_at: datetime) -> bool:
        try:
            with get_cursor() as cur:
                cur.execute(
                    """UPDATE report_schedules SET report_id=%s,frequency=%s,hour=%s,minute=%s,weekday=%s,
                       day_of_month=%s,format=%s,recipients=%s,subject=%s,is_active=%s,next_run_at=%s,
                       updated_at=NOW() WHERE id=%s AND company_id=%s""",
                    (d["report_id"], d.get("frequency") or "daily", _int(d.get("hour"), 7),
                     _int(d.get("minute"), 0), _int(d.get("weekday")), _int(d.get("day_of_month")),
                     d.get("format") or "pdf", d.get("recipients") or "", d.get("subject") or "",
                     bool(d.get("is_active", True)), next_run_at, schedule_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("update_schedule: %s", e); return False

    def set_schedule_active(self, schedule_id: str, company_id: str, active: bool,
                            next_run_at: Optional[datetime] = None) -> bool:
        try:
            with get_cursor() as cur:
                if next_run_at is not None:
                    cur.execute("UPDATE report_schedules SET is_active=%s,next_run_at=%s,updated_at=NOW() "
                                "WHERE id=%s AND company_id=%s", (active, next_run_at, schedule_id, company_id))
                else:
                    cur.execute("UPDATE report_schedules SET is_active=%s,updated_at=NOW() "
                                "WHERE id=%s AND company_id=%s", (active, schedule_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("set_schedule_active: %s", e); return False

    def delete_schedule(self, schedule_id: str, company_id: str) -> bool:
        try:
            with get_cursor() as cur:
                cur.execute("DELETE FROM report_schedules WHERE id=%s AND company_id=%s",
                            (schedule_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("delete_schedule: %s", e); return False

    def due_schedules(self, now: datetime) -> List[dict]:
        """Active schedules across ALL companies whose next_run_at <= now (used by the job)."""
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT s.*, d.name AS report_name FROM report_schedules s
                       JOIN report_definitions d ON d.id = s.report_id AND d.company_id = s.company_id
                       WHERE s.is_active AND s.next_run_at IS NOT NULL AND s.next_run_at <= %s
                       ORDER BY s.next_run_at""", (now,))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("due_schedules: %s", e); return []

    def mark_schedule_run(self, schedule_id: str, last_run_at: datetime, next_run_at: datetime) -> bool:
        try:
            with get_cursor() as cur:
                cur.execute("UPDATE report_schedules SET last_run_at=%s,next_run_at=%s,updated_at=NOW() WHERE id=%s",
                            (last_run_at, next_run_at, schedule_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("mark_schedule_run: %s", e); return False

    # ── runs ─────────────────────────────────────────────────────
    def start_run(self, company_id: str, report_id: str, triggered_by: str,
                  schedule_id: str = None, output_format: str = None) -> Optional[str]:
        try:
            run_id = str(uuid.uuid4())
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO report_runs(id,company_id,report_id,schedule_id,status,triggered_by,output_format)
                       VALUES (%s,%s,%s,%s,'running',%s,%s)""",
                    (run_id, company_id, report_id, _opt(schedule_id), triggered_by, _opt(output_format)))
            return run_id
        except Exception as e:
            logger.error("start_run: %s", e); return None

    def finish_run(self, run_id: str, status: str, row_count: int = 0, error: str = None,
                   output_path: str = None) -> bool:
        if not run_id:
            return False
        try:
            with get_cursor() as cur:
                cur.execute(
                    """UPDATE report_runs SET finished_at=NOW(),status=%s,row_count=%s,error=%s,output_path=%s
                       WHERE id=%s""",
                    (status, int(row_count or 0), (error or None) and str(error)[:2000], _opt(output_path), run_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("finish_run: %s", e); return False

    def list_runs(self, company_id: str, report_id: str = None, limit: int = 100) -> List[dict]:
        try:
            sql = """SELECT r.*, d.name AS report_name FROM report_runs r
                     LEFT JOIN report_definitions d ON d.id = r.report_id
                     WHERE r.company_id=%s"""
            params: list = [company_id]
            if report_id:
                sql += " AND r.report_id=%s"; params.append(report_id)
            sql += " ORDER BY r.started_at DESC LIMIT %s"; params.append(int(limit))
            with get_cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_runs: %s", e); return []

    def get_run(self, run_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT r.*, d.name AS report_name FROM report_runs r
                       LEFT JOIN report_definitions d ON d.id = r.report_id
                       WHERE r.id=%s AND r.company_id=%s""", (run_id, company_id))
                r = cur.fetchone()
                return dict(r) if r else None
        except Exception as e:
            logger.error("get_run: %s", e); return None

    def get_stats(self, company_id: str) -> dict:
        stats = {"reports": 0, "templates": 0, "schedules": 0, "active_schedules": 0,
                 "runs_ok": 0, "runs_error": 0}
        try:
            with get_cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c, COUNT(*) FILTER (WHERE is_template) AS t "
                            "FROM report_definitions WHERE company_id=%s", (company_id,))
                r = cur.fetchone() or {}
                stats["reports"], stats["templates"] = r.get("c", 0), r.get("t", 0)
                cur.execute("SELECT COUNT(*) AS c, COUNT(*) FILTER (WHERE is_active) AS a "
                            "FROM report_schedules WHERE company_id=%s", (company_id,))
                r = cur.fetchone() or {}
                stats["schedules"], stats["active_schedules"] = r.get("c", 0), r.get("a", 0)
                cur.execute("SELECT COUNT(*) FILTER (WHERE status='ok') AS ok, "
                            "COUNT(*) FILTER (WHERE status='error') AS err "
                            "FROM report_runs WHERE company_id=%s AND started_at > NOW() - INTERVAL '30 days'",
                            (company_id,))
                r = cur.fetchone() or {}
                stats["runs_ok"], stats["runs_error"] = r.get("ok", 0), r.get("err", 0)
        except Exception as e:
            logger.error("get_stats: %s", e)
        return stats


report_store = ReportsDataStore()
