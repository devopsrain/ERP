"""
Notifications Data Store — bell-icon notifications.
Inserts on PR pending, booking conflicts, mentions, etc.
"""
from __future__ import annotations
import logging, uuid
from typing import List
from db import get_conn

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL,
    user_id     TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL,
    message     TEXT NOT NULL DEFAULT '',
    icon        TEXT NOT NULL DEFAULT 'bell',
    link        TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT 'info',
    is_read     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(company_id, user_id, is_read, created_at DESC);
"""


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
    except Exception as e:
        logger.error("notifications schema init failed: %s", e)


class NotificationsDataStore:
    def ensure_schema(self):
        ensure_schema()

    def push(self, company_id: str, user_id: str, title: str,
             message: str = "", link: str = "", icon: str = "bell",
             category: str = "info") -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO notifications(id,company_id,user_id,title,message,icon,link,category)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (str(uuid.uuid4()), company_id, user_id, title, message, icon, link, category)
                    )
            return True
        except Exception as e:
            logger.error("push notification: %s", e); return False

    def broadcast(self, company_id: str, title: str, message: str = "",
                  link: str = "", icon: str = "bell", category: str = "info") -> bool:
        """Notification for all users in company (user_id = '')."""
        return self.push(company_id, "", title, message, link, icon, category)

    def get_recent(self, company_id: str, user_id: str, limit: int = 20) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT * FROM notifications
                           WHERE company_id=%s AND (user_id=%s OR user_id='')
                           ORDER BY created_at DESC LIMIT %s""",
                        (company_id, user_id, limit)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_recent notifications: %s", e); return []

    def unread_count(self, company_id: str, user_id: str) -> int:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT COUNT(*) AS n FROM notifications
                           WHERE company_id=%s AND (user_id=%s OR user_id='') AND NOT is_read""",
                        (company_id, user_id)
                    )
                    return int(cur.fetchone()["n"] or 0)
        except Exception:
            return 0

    def mark_read(self, notification_id: str, user_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE notifications SET is_read=TRUE WHERE id=%s AND (user_id=%s OR user_id='')",
                        (notification_id, user_id)
                    )
            return True
        except Exception as e:
            logger.error("mark_read: %s", e); return False

    def mark_all_read(self, company_id: str, user_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE notifications SET is_read=TRUE
                           WHERE company_id=%s AND (user_id=%s OR user_id='') AND NOT is_read""",
                        (company_id, user_id)
                    )
            return True
        except Exception as e:
            logger.error("mark_all_read: %s", e); return False


notifications_store = NotificationsDataStore()
