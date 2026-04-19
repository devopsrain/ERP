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

import logging
import os
import pathlib

logger = logging.getLogger(__name__)

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
        logger.warning(
            "DB init SQL not found — searched: %s",
            [str(p) for p in _SEARCH_PATHS[1:]],
        )
        return False

    logger.info("Running DB schema init from: %s", sql_path)
    sql = sql_path.read_text(encoding="utf-8")

    try:
        from db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
        logger.info("DB schema init complete (tables created/verified)")
        return True
    except Exception as exc:
        logger.error("DB schema init failed: %s", exc)
        return False
