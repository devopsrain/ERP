"""
Communication Platform Data Store — PostgreSQL backend.
Tables: comm_channels, comm_messages, comm_reactions, comm_user_status, comm_file_metadata
"""
from __future__ import annotations
import logging, uuid
from datetime import datetime
from typing import List, Optional
from db import get_conn

logger = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS comm_channels (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL,
    name         TEXT NOT NULL,
    type         TEXT NOT NULL DEFAULT 'group',  -- direct | group
    created_by   TEXT NOT NULL,
    created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    is_archived  BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_comm_channels_company ON comm_channels(company_id);

CREATE TABLE IF NOT EXISTS comm_messages (
    id           TEXT PRIMARY KEY,
    channel_id   TEXT NOT NULL,
    sender_id    TEXT NOT NULL,
    sender_name  TEXT NOT NULL,
    content      TEXT NOT NULL DEFAULT '',
    type         TEXT NOT NULL DEFAULT 'text',   -- text | file
    parent_id    TEXT,                            -- thread reply
    is_pinned    BOOLEAN NOT NULL DEFAULT FALSE,
    is_edited    BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at   TIMESTAMP,
    created_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_comm_messages_channel ON comm_messages(channel_id);
CREATE INDEX IF NOT EXISTS idx_comm_messages_parent  ON comm_messages(parent_id);

CREATE TABLE IF NOT EXISTS comm_reactions (
    id          TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    emoji       TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (message_id, user_id, emoji)
);
CREATE INDEX IF NOT EXISTS idx_comm_reactions_msg ON comm_reactions(message_id);

CREATE TABLE IF NOT EXISTS comm_user_status (
    user_id      TEXT PRIMARY KEY,
    status_text  TEXT NOT NULL DEFAULT '',
    dnd_enabled  BOOLEAN NOT NULL DEFAULT FALSE,
    last_seen    TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS comm_file_metadata (
    id           TEXT PRIMARY KEY,
    message_id   TEXT NOT NULL,
    filename     TEXT NOT NULL,
    original_name TEXT NOT NULL,
    file_size    BIGINT NOT NULL DEFAULT 0,
    mime_type    TEXT NOT NULL DEFAULT '',
    storage_path TEXT NOT NULL DEFAULT '',
    uploaded_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_comm_files_msg ON comm_file_metadata(message_id);
"""


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("comm schema ready")
    except Exception as e:
        logger.error("comm schema init failed: %s", e)


# ── Channels ──────────────────────────────────────────────────────────────────

class CommunicationDataStore:

    def ensure_schema(self):
        ensure_schema()

    # Channels
    def get_channels(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM comm_channels WHERE company_id=%s AND NOT is_archived ORDER BY name",
                        (company_id,)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_channels: %s", e); return []

    def create_channel(self, company_id: str, name: str, ctype: str, created_by: str) -> Optional[dict]:
        try:
            cid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO comm_channels(id,company_id,name,type,created_by) VALUES(%s,%s,%s,%s,%s) RETURNING *",
                        (cid, company_id, name, ctype, created_by)
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_channel: %s", e); return None

    def delete_channel(self, channel_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE comm_channels SET is_archived=TRUE WHERE id=%s AND company_id=%s",
                        (channel_id, company_id)
                    )
            return True
        except Exception as e:
            logger.error("delete_channel: %s", e); return False

    # Messages
    def get_messages(self, channel_id: str, limit: int = 100) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT * FROM comm_messages
                           WHERE channel_id=%s AND deleted_at IS NULL AND parent_id IS NULL
                           ORDER BY created_at DESC LIMIT %s""",
                        (channel_id, limit)
                    )
                    return list(reversed([dict(r) for r in cur.fetchall()]))
        except Exception as e:
            logger.error("get_messages: %s", e); return []

    def get_thread(self, parent_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM comm_messages WHERE parent_id=%s AND deleted_at IS NULL ORDER BY created_at",
                        (parent_id,)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_thread: %s", e); return []

    def post_message(self, channel_id: str, sender_id: str, sender_name: str,
                     content: str, mtype: str = "text", parent_id: str = None) -> Optional[dict]:
        try:
            mid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO comm_messages(id,channel_id,sender_id,sender_name,content,type,parent_id)
                           VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (mid, channel_id, sender_id, sender_name, content, mtype, parent_id)
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("post_message: %s", e); return None

    def edit_message(self, message_id: str, user_id: str, new_content: str) -> bool:
        """Edit own message; marks it is_edited."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE comm_messages SET content=%s, is_edited=TRUE "
                        "WHERE id=%s AND sender_id=%s AND deleted_at IS NULL",
                        (new_content, message_id, user_id)
                    )
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("edit_message: %s", e); return False

    def search_messages(self, company_id: str, query: str, limit: int = 100) -> List[dict]:
        """Search message text and file names across all channels of a company."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT m.*, c.name AS channel_name
                           FROM comm_messages m
                           JOIN comm_channels c ON c.id = m.channel_id
                           WHERE c.company_id=%s AND m.deleted_at IS NULL
                             AND m.content ILIKE %s
                           ORDER BY m.created_at DESC LIMIT %s""",
                        (company_id, f"%{query}%", limit)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("search_messages: %s", e); return []

    def save_file_message(self, channel_id: str, sender_id: str, sender_name: str,
                          original_name: str, stored_name: str, storage_path: str,
                          file_size: int, mime_type: str) -> Optional[dict]:
        """Post a file-type message and its metadata row in one transaction."""
        try:
            mid = str(uuid.uuid4())
            fid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO comm_messages(id,channel_id,sender_id,sender_name,content,type)
                           VALUES(%s,%s,%s,%s,%s,'file') RETURNING *""",
                        (mid, channel_id, sender_id, sender_name, original_name)
                    )
                    msg = dict(cur.fetchone())
                    cur.execute(
                        """INSERT INTO comm_file_metadata
                           (id,message_id,filename,original_name,file_size,mime_type,storage_path)
                           VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                        (fid, mid, stored_name, original_name, file_size, mime_type, storage_path)
                    )
                    msg["file_id"] = fid
                    return msg
        except Exception as e:
            logger.error("save_file_message: %s", e); return None

    def get_file(self, file_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM comm_file_metadata WHERE id=%s", (file_id,))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("get_file: %s", e); return None

    def get_files_for_messages(self, message_ids: List[str]) -> dict:
        """Map message_id -> file metadata for file-type messages."""
        if not message_ids:
            return {}
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM comm_file_metadata WHERE message_id = ANY(%s)",
                        (list(message_ids),)
                    )
                    return {r["message_id"]: dict(r) for r in cur.fetchall()}
        except Exception as e:
            logger.error("get_files_for_messages: %s", e); return {}

    def soft_delete_message(self, message_id: str, user_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE comm_messages SET deleted_at=NOW() WHERE id=%s AND sender_id=%s",
                        (message_id, user_id)
                    )
            return True
        except Exception as e:
            logger.error("soft_delete_message: %s", e); return False

    def pin_message(self, message_id: str, pinned: bool) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE comm_messages SET is_pinned=%s WHERE id=%s", (pinned, message_id))
            return True
        except Exception as e:
            logger.error("pin_message: %s", e); return False

    def get_pinned(self, channel_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM comm_messages WHERE channel_id=%s AND is_pinned=TRUE AND deleted_at IS NULL ORDER BY created_at DESC",
                        (channel_id,)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_pinned: %s", e); return []

    # Reactions
    def add_reaction(self, message_id: str, user_id: str, emoji: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO comm_reactions(id,message_id,user_id,emoji) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (str(uuid.uuid4()), message_id, user_id, emoji)
                    )
            return True
        except Exception as e:
            logger.error("add_reaction: %s", e); return False

    def remove_reaction(self, message_id: str, user_id: str, emoji: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM comm_reactions WHERE message_id=%s AND user_id=%s AND emoji=%s",
                        (message_id, user_id, emoji)
                    )
            return True
        except Exception as e:
            logger.error("remove_reaction: %s", e); return False

    def get_reactions(self, channel_id: str) -> dict:
        """Returns {message_id: [{emoji, count, users}]}"""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT r.message_id, r.emoji, COUNT(*) AS cnt
                           FROM comm_reactions r
                           JOIN comm_messages m ON r.message_id = m.id
                           WHERE m.channel_id=%s
                           GROUP BY r.message_id, r.emoji""",
                        (channel_id,)
                    )
                    result: dict = {}
                    for row in cur.fetchall():
                        result.setdefault(row["message_id"], []).append(
                            {"emoji": row["emoji"], "count": row["cnt"]}
                        )
                    return result
        except Exception as e:
            logger.error("get_reactions: %s", e); return {}

    # User status
    def set_status(self, user_id: str, status_text: str, dnd: bool) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO comm_user_status(user_id,status_text,dnd_enabled,last_seen)
                           VALUES(%s,%s,%s,NOW())
                           ON CONFLICT(user_id) DO UPDATE
                           SET status_text=EXCLUDED.status_text, dnd_enabled=EXCLUDED.dnd_enabled, last_seen=NOW()""",
                        (user_id, status_text, dnd)
                    )
            return True
        except Exception as e:
            logger.error("set_status: %s", e); return False

    def touch_last_seen(self, user_id: str) -> None:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO comm_user_status(user_id,last_seen) VALUES(%s,NOW())
                           ON CONFLICT(user_id) DO UPDATE SET last_seen=NOW()""",
                        (user_id,)
                    )
        except Exception as e:
            logger.error("touch_last_seen: %s", e)

    def get_channel_stats(self, company_id: str) -> dict:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS c FROM comm_channels WHERE company_id=%s AND NOT is_archived", (company_id,))
                    channels = cur.fetchone()["c"]
                    cur.execute(
                        """SELECT COUNT(*) AS c FROM comm_messages m
                           JOIN comm_channels ch ON m.channel_id=ch.id
                           WHERE ch.company_id=%s AND m.deleted_at IS NULL""",
                        (company_id,)
                    )
                    messages = cur.fetchone()["c"]
                    return {"channels": channels, "messages": messages}
        except Exception as e:
            logger.error("get_channel_stats: %s", e); return {"channels": 0, "messages": 0}


comm_store = CommunicationDataStore()
