"""
db_setup.py — Idempotent DB schema initialiser.

Called once during app lifespan startup.  Runs init_db.sql so that a
fresh deployment against an empty database automatically creates all
tables.  Safe to run on every restart because every statement in the
SQL file uses CREATE TABLE IF NOT EXISTS / CREATE EXTENSION IF NOT EXISTS.

The SQL file is looked for in these locations (first match wins):
  1. $DB_INIT_SQL env var (explicit override)
  2. <this file's directory>/../aws-deployment/init_db.sql
  3. <project root>/aws-deployment/init_db.sql
  4. <this file's directory>/../../Deployed/aws-deployment/init_db.sql
"""
from __future__ import annotations

import datetime
import logging
import os
import pathlib

logger = logging.getLogger(__name__)

# ── Last-run status (exposed via /health → schema_init) ──────────────
# Set by ensure_schema() on every call.  psycopg2 error messages include
# the failing statement context, so LAST_SCHEMA_ERROR pinpoints exactly
# which statement rolled the whole init transaction back.
LAST_SCHEMA_OK: bool | None = None      # None = not run yet
LAST_SCHEMA_ERROR: str | None = None
LAST_SCHEMA_TS: str | None = None       # ISO-8601 UTC of last attempt


def _record(ok: bool, error: str | None) -> None:
    global LAST_SCHEMA_OK, LAST_SCHEMA_ERROR, LAST_SCHEMA_TS
    LAST_SCHEMA_OK = ok
    LAST_SCHEMA_ERROR = error
    LAST_SCHEMA_TS = datetime.datetime.now(datetime.timezone.utc).isoformat()

_HERE = pathlib.Path(__file__).parent.resolve()   # web/

_SEARCH_PATHS = [
    pathlib.Path(os.environ.get("DB_INIT_SQL", "/__nonexistent__")),
    _HERE.parent / "aws-deployment" / "init_db.sql",
    _HERE.parent / "Deployed" / "aws-deployment" / "init_db.sql",
    _HERE.parent.parent / "Deployed" / "aws-deployment" / "init_db.sql",
]


def _find_sql() -> pathlib.Path | None:
    for p in _SEARCH_PATHS:
        if p.exists():
            return p
    return None


def ensure_schema() -> bool:
    """
    Run init_db.sql against the configured database.

    Returns True if the schema was applied successfully (or already existed),
    False if the SQL file was not found or the connection failed.
    """
    sql_path = _find_sql()
    if sql_path is None:
        msg = "DB init SQL not found — searched: %s" % [
            str(p) for p in _SEARCH_PATHS[1:]
        ]
        logger.warning(msg)
        _record(False, msg)
        return False

    logger.info("Running DB schema init from: %s", sql_path)
    sql = sql_path.read_text(encoding="utf-8")

    try:
        from db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
        logger.info("DB schema init complete (tables created/verified)")
        _record(True, None)
        return True
    except Exception as exc:
        # NOTE: the whole file runs in ONE transaction, so a single bad
        # statement silently strands every later statement.  Keep the full
        # exception text — psycopg2 includes the failing statement context.
        msg = f"{type(exc).__name__}: {exc}"
        logger.error("DB schema init failed (entire transaction rolled back): %s", msg)
        _record(False, msg)
        return False
