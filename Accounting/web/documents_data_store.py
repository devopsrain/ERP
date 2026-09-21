"""
Documents Data Store — PostgreSQL metadata for the Nextcloud/local document
backend. The bytes live in Nextcloud (WebDAV) or on local disk; these tables
hold what/where/who.

Tables: documents, document_share_links, document_folders, document_audit
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import List, Optional

from psycopg2.extras import Json

from db import get_conn

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    module        TEXT NOT NULL DEFAULT 'general',   -- contracts|letters|projects|procurement|...
    entity_id     TEXT NOT NULL DEFAULT '',          -- id of the owning record in that module
    filename      TEXT NOT NULL,
    content_type  TEXT NOT NULL DEFAULT 'application/octet-stream',
    size          BIGINT NOT NULL DEFAULT 0,
    backend       TEXT NOT NULL DEFAULT 'local',     -- nextcloud|local
    remote_path   TEXT,                              -- ROOT-relative WebDAV path
    local_path    TEXT,                              -- absolute path on disk (local backend)
    sha256        TEXT,
    tags          JSONB NOT NULL DEFAULT '[]'::jsonb,
    uploaded_by   TEXT NOT NULL DEFAULT '',
    uploaded_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    deleted_at    TIMESTAMP,
    version       INT NOT NULL DEFAULT 1,
    previous_id   TEXT                               -- prior version of this document
);
CREATE INDEX IF NOT EXISTS idx_documents_company ON documents(company_id);
CREATE INDEX IF NOT EXISTS idx_documents_entity ON documents(company_id, module, entity_id);
CREATE INDEX IF NOT EXISTS idx_documents_remote ON documents(remote_path);
CREATE INDEX IF NOT EXISTS idx_documents_previous ON documents(previous_id);

CREATE TABLE IF NOT EXISTS document_share_links (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL,
    url          TEXT NOT NULL,
    expires_at   TIMESTAMP,
    created_by   TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_document_share_links_doc ON document_share_links(document_id);

CREATE TABLE IF NOT EXISTS document_folders (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL DEFAULT 'default',
    name         TEXT NOT NULL,
    module       TEXT,
    path         TEXT NOT NULL,                      -- ROOT-relative folder path
    created_by   TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_document_folders_company ON document_folders(company_id);

CREATE TABLE IF NOT EXISTS document_audit (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL DEFAULT 'default',
    document_id  TEXT,
    action       TEXT NOT NULL,                      -- upload|download|delete|share|sync
    actor        TEXT NOT NULL DEFAULT '',
    ip           TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_document_audit_company ON document_audit(company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_document_audit_doc ON document_audit(document_id);
"""

AUDIT_ACTIONS = ("upload", "download", "delete", "share", "sync")


def _opt(value):
    """'' → None (Postgres rejects '' for TIMESTAMP/INT)."""
    return value if value not in ("", None) else None


def _tags(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [t.strip() for t in value.split(",")]
    return [str(t).strip() for t in value if str(t).strip()]


def _row(r) -> Optional[dict]:
    if r is None:
        return None
    d = dict(r)
    if "tags" in d:
        d["tags"] = _tags(d.get("tags"))
    return d


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("documents schema ready")
    except Exception as e:
        logger.error("documents schema init failed: %s", e)


class DocumentsDataStore:

    def ensure_schema(self):
        ensure_schema()

    # ── documents ───────────────────────────────────────────────────────────

    def create_document(self, doc: dict) -> Optional[dict]:
        """Insert a metadata row. `doc` keys mirror the table columns."""
        try:
            doc_id = doc.get("id") or str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO documents(id,company_id,module,entity_id,filename,content_type,size,
                               backend,remote_path,local_path,sha256,tags,uploaded_by,version,previous_id)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (doc_id, doc.get("company_id") or "default", doc.get("module") or "general",
                         doc.get("entity_id") or "", doc["filename"],
                         doc.get("content_type") or "application/octet-stream", int(doc.get("size") or 0),
                         doc.get("backend") or "local", _opt(doc.get("remote_path")),
                         _opt(doc.get("local_path")), _opt(doc.get("sha256")),
                         Json(_tags(doc.get("tags"))), doc.get("uploaded_by") or "",
                         int(doc.get("version") or 1), _opt(doc.get("previous_id")))
                    )
                    return _row(cur.fetchone())
        except Exception as e:
            logger.error("create_document: %s", e)
            return None

    def get_document(self, doc_id: str, company_id: str = None,
                     include_deleted: bool = True) -> Optional[dict]:
        try:
            sql = "SELECT * FROM documents WHERE id=%s"
            params: list = [doc_id]
            if company_id:
                sql += " AND company_id=%s"; params.append(company_id)
            if not include_deleted:
                sql += " AND deleted_at IS NULL"
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    return _row(cur.fetchone())
        except Exception as e:
            logger.error("get_document: %s", e)
            return None

    def get_by_remote_path(self, company_id: str, remote_path: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM documents WHERE company_id=%s AND remote_path=%s "
                                "ORDER BY uploaded_at DESC LIMIT 1", (company_id, remote_path))
                    return _row(cur.fetchone())
        except Exception as e:
            logger.error("get_by_remote_path: %s", e)
            return None

    def documents_for(self, company_id: str, module: str, entity_id: str) -> List[dict]:
        """Live (non-deleted) documents of one record — latest version of each chain."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT d.* FROM documents d
                           WHERE d.company_id=%s AND d.module=%s AND d.entity_id=%s AND d.deleted_at IS NULL
                             AND NOT EXISTS (SELECT 1 FROM documents n
                                             WHERE n.previous_id=d.id AND n.deleted_at IS NULL)
                           ORDER BY d.uploaded_at DESC""",
                        (company_id, module, entity_id))
                    return [_row(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("documents_for: %s", e)
            return []

    def list_documents(self, company_id: str, module: str = None, entity_id: str = None,
                       include_deleted: bool = False, limit: int = 500) -> List[dict]:
        try:
            sql = "SELECT * FROM documents WHERE company_id=%s"
            params: list = [company_id]
            if module:
                sql += " AND module=%s"; params.append(module)
            if entity_id:
                sql += " AND entity_id=%s"; params.append(entity_id)
            if not include_deleted:
                sql += " AND deleted_at IS NULL"
            sql += " ORDER BY uploaded_at DESC LIMIT %s"; params.append(int(limit))
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    return [_row(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_documents: %s", e)
            return []

    def search_documents(self, company_id: str, q: str = None, module: str = None,
                         tag: str = None, date_from: str = None, date_to: str = None,
                         backend: str = None, limit: int = 200) -> List[dict]:
        try:
            sql = "SELECT * FROM documents WHERE company_id=%s AND deleted_at IS NULL"
            params: list = [company_id]
            if q:
                sql += " AND (filename ILIKE %s OR entity_id ILIKE %s OR tags::text ILIKE %s)"
                like = f"%{q}%"; params += [like, like, like]
            if module:
                sql += " AND module=%s"; params.append(module)
            if tag:
                sql += " AND tags @> %s::jsonb"; params.append(json.dumps([tag]))
            if _opt(date_from):
                sql += " AND uploaded_at >= %s::date"; params.append(date_from)
            if _opt(date_to):
                sql += " AND uploaded_at < (%s::date + INTERVAL '1 day')"; params.append(date_to)
            if backend:
                sql += " AND backend=%s"; params.append(backend)
            sql += " ORDER BY uploaded_at DESC LIMIT %s"; params.append(int(limit))
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    return [_row(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("search_documents: %s", e)
            return []

    def soft_delete(self, doc_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE documents SET deleted_at=NOW() WHERE id=%s AND company_id=%s "
                                "AND deleted_at IS NULL", (doc_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("soft_delete: %s", e)
            return False

    def restore(self, doc_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE documents SET deleted_at=NULL WHERE id=%s AND company_id=%s",
                                (doc_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("restore: %s", e)
            return False

    def update_tags(self, doc_id: str, company_id: str, tags: list) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE documents SET tags=%s WHERE id=%s AND company_id=%s",
                                (Json(_tags(tags)), doc_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("update_tags: %s", e)
            return False

    def get_versions(self, doc_id: str, company_id: str) -> List[dict]:
        """Whole version chain (ancestors + descendants) ordered by version."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """WITH RECURSIVE up AS (
                               SELECT d.*, 0 AS depth FROM documents d WHERE d.id=%s AND d.company_id=%s
                               UNION ALL
                               SELECT d.*, up.depth + 1 FROM documents d JOIN up ON d.id = up.previous_id
                               WHERE up.depth < 200
                           ), down AS (
                               SELECT d.*, 0 AS depth FROM documents d WHERE d.id=%s AND d.company_id=%s
                               UNION ALL
                               SELECT d.*, down.depth + 1 FROM documents d JOIN down ON d.previous_id = down.id
                               WHERE down.depth < 200
                           )
                           SELECT * FROM up UNION SELECT * FROM down ORDER BY version, uploaded_at""",
                        (doc_id, company_id, doc_id, company_id))
                    seen, out = set(), []
                    for r in cur.fetchall():
                        d = _row(r)
                        d.pop("depth", None)
                        if d["id"] not in seen:
                            seen.add(d["id"]); out.append(d)
                    return out
        except Exception as e:
            logger.error("get_versions: %s", e)
            return []

    def known_remote_paths(self, company_id: str) -> set:
        """All remote paths ever recorded (deleted included) — used by the sync view."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT remote_path FROM documents WHERE company_id=%s "
                                "AND remote_path IS NOT NULL", (company_id,))
                    return {r["remote_path"] for r in cur.fetchall()}
        except Exception as e:
            logger.error("known_remote_paths: %s", e)
            return set()

    # ── aggregates ──────────────────────────────────────────────────────────

    def module_summary(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT module, COUNT(*) AS count, COALESCE(SUM(size),0) AS bytes,
                                  COUNT(DISTINCT entity_id) AS entities, MAX(uploaded_at) AS last_upload
                           FROM documents WHERE company_id=%s AND deleted_at IS NULL
                           GROUP BY module ORDER BY module""", (company_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("module_summary: %s", e)
            return []

    def entity_summary(self, company_id: str, module: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT entity_id, COUNT(*) AS count, COALESCE(SUM(size),0) AS bytes,
                                  MAX(uploaded_at) AS last_upload
                           FROM documents WHERE company_id=%s AND module=%s AND deleted_at IS NULL
                           GROUP BY entity_id ORDER BY last_upload DESC""", (company_id, module))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("entity_summary: %s", e)
            return []

    def recent_uploads(self, company_id: str, limit: int = 10) -> List[dict]:
        return self.list_documents(company_id, limit=limit)

    def get_stats(self, company_id: str) -> dict:
        empty = {"total": 0, "total_bytes": 0, "deleted": 0, "by_module": [],
                 "by_backend": {"nextcloud": 0, "local": 0}, "shares": 0, "modules": 0}
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT COUNT(*) FILTER (WHERE deleted_at IS NULL) AS total,
                                  COUNT(*) FILTER (WHERE deleted_at IS NOT NULL) AS deleted,
                                  COALESCE(SUM(size) FILTER (WHERE deleted_at IS NULL),0) AS total_bytes
                           FROM documents WHERE company_id=%s""", (company_id,))
                    r = cur.fetchone() or {}
                    cur.execute(
                        """SELECT backend, COUNT(*) AS c FROM documents
                           WHERE company_id=%s AND deleted_at IS NULL GROUP BY backend""", (company_id,))
                    by_backend = {"nextcloud": 0, "local": 0}
                    for b in cur.fetchall():
                        by_backend[b["backend"]] = b["c"]
                    cur.execute(
                        """SELECT COUNT(*) AS c FROM document_share_links s
                           JOIN documents d ON d.id = s.document_id
                           WHERE d.company_id=%s AND (s.expires_at IS NULL OR s.expires_at > NOW())""",
                        (company_id,))
                    shares = (cur.fetchone() or {}).get("c", 0)
            by_module = self.module_summary(company_id)
            return {"total": int(r.get("total") or 0), "deleted": int(r.get("deleted") or 0),
                    "total_bytes": int(r.get("total_bytes") or 0), "by_module": by_module,
                    "by_backend": by_backend, "shares": int(shares or 0), "modules": len(by_module)}
        except Exception as e:
            logger.error("get_stats: %s", e)
            return empty

    # ── share links ─────────────────────────────────────────────────────────

    def add_share_link(self, document_id: str, url: str, expires_at: Optional[datetime],
                       created_by: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO document_share_links(id,document_id,url,expires_at,created_by)
                           VALUES(%s,%s,%s,%s,%s) RETURNING *""",
                        (str(uuid.uuid4()), document_id, url, _opt(expires_at), created_by or ""))
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("add_share_link: %s", e)
            return None

    def get_share_links(self, document_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT *, (expires_at IS NOT NULL AND expires_at < NOW()) AS expired
                           FROM document_share_links WHERE document_id=%s ORDER BY created_at DESC""",
                        (document_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_share_links: %s", e)
            return []

    # ── folders ─────────────────────────────────────────────────────────────

    def create_folder(self, company_id: str, name: str, module: Optional[str], path: str,
                      created_by: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO document_folders(id,company_id,name,module,path,created_by)
                           VALUES(%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (str(uuid.uuid4()), company_id, name, _opt(module), path, created_by or ""))
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_folder: %s", e)
            return None

    def list_folders(self, company_id: str, module: str = None) -> List[dict]:
        try:
            sql = "SELECT * FROM document_folders WHERE company_id=%s"
            params: list = [company_id]
            if module:
                sql += " AND module=%s"; params.append(module)
            sql += " ORDER BY module NULLS LAST, name"
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_folders: %s", e)
            return []

    def get_folder(self, folder_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM document_folders WHERE id=%s AND company_id=%s",
                                (folder_id, company_id))
                    r = cur.fetchone()
                    return dict(r) if r else None
        except Exception as e:
            logger.error("get_folder: %s", e)
            return None

    def delete_folder(self, folder_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM document_folders WHERE id=%s AND company_id=%s",
                                (folder_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("delete_folder: %s", e)
            return False

    # ── audit ───────────────────────────────────────────────────────────────

    def add_audit(self, company_id: str, document_id: Optional[str], action: str,
                  actor: str = "", ip: str = "") -> None:
        if action not in AUDIT_ACTIONS:
            action = "sync"
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO document_audit(id,company_id,document_id,action,actor,ip)
                           VALUES(%s,%s,%s,%s,%s,%s)""",
                        (str(uuid.uuid4()), company_id or "default", _opt(document_id), action,
                         actor or "", (ip or "")[:64]))
        except Exception as e:
            logger.error("add_audit: %s", e)

    def recent_audit(self, company_id: str, limit: int = 20) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT a.*, d.filename FROM document_audit a
                           LEFT JOIN documents d ON d.id = a.document_id
                           WHERE a.company_id=%s ORDER BY a.created_at DESC LIMIT %s""",
                        (company_id, int(limit)))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("recent_audit: %s", e)
            return []

    def audit_for(self, document_id: str, limit: int = 50) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM document_audit WHERE document_id=%s "
                                "ORDER BY created_at DESC LIMIT %s", (document_id, int(limit)))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("audit_for: %s", e)
            return []


doc_store = DocumentsDataStore()
