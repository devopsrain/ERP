"""
Audit Trail Data Store — record who changed what, when.
Pluggable from any module: audit_store.log(action, entity, entity_id, before, after, user)
"""
from __future__ import annotations
import json, logging, uuid
from typing import List, Optional
from db import get_conn

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_trail (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL,
    user_id      TEXT NOT NULL DEFAULT '',
    username     TEXT NOT NULL DEFAULT '',
    action       TEXT NOT NULL,            -- create|update|delete|view|approve|...
    entity_type  TEXT NOT NULL,            -- pm_projects, proc_purchase_orders, etc.
    entity_id    TEXT NOT NULL DEFAULT '',
    before_json  TEXT NOT NULL DEFAULT '',
    after_json   TEXT NOT NULL DEFAULT '',
    ip_address   TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_entity   ON audit_trail(entity_type, entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user     ON audit_trail(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_company  ON audit_trail(company_id, created_at DESC);
"""


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
    except Exception as e:
        logger.error("audit_trail schema init failed: %s", e)


class AuditDataStore:
    def ensure_schema(self):
        ensure_schema()

    def log(self, *, company_id: str, user_id: str = "", username: str = "",
            action: str, entity_type: str, entity_id: str = "",
            before: dict = None, after: dict = None, ip_address: str = "") -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO audit_trail(id,company_id,user_id,username,action,
                           entity_type,entity_id,before_json,after_json,ip_address)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (str(uuid.uuid4()), company_id, user_id, username, action,
                         entity_type, entity_id,
                         json.dumps(before, default=str) if before else "",
                         json.dumps(after, default=str) if after else "",
                         ip_address)
                    )
            return True
        except Exception as e:
            logger.error("audit log: %s", e); return False

    def history(self, entity_type: str, entity_id: str, limit: int = 50) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT * FROM audit_trail
                           WHERE entity_type=%s AND entity_id=%s
                           ORDER BY created_at DESC LIMIT %s""",
                        (entity_type, entity_id, limit)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("audit history: %s", e); return []

    def for_user(self, user_id: str, company_id: str, limit: int = 100) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT * FROM audit_trail
                           WHERE company_id=%s AND user_id=%s
                           ORDER BY created_at DESC LIMIT %s""",
                        (company_id, user_id, limit)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("audit for_user: %s", e); return []


audit_store = AuditDataStore()
