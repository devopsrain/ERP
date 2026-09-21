"""
Telegram Bot Data Store — PostgreSQL backend.

Tables (all created idempotently by ensure_schema()):
  telegram_links        chat_id  -> EBMS user + company
  telegram_link_codes   6-char one-time codes generated on /telegram/link
  telegram_subscriptions (chat_id, topic) pairs
  telegram_outbox       queued messages, flushed every minute by telegram_jobs
  telegram_updates_log  every webhook update (dedupe on update_id)

Also hosts the READ-ONLY query helpers the bot commands rely on
(/today, /month, /bids, /cpo, /stock, ...). Those queries touch tables owned
by other modules, so every one of them is guarded by information_schema
(see _table_columns) and swallows errors into empty results — a missing
table or column must never break the bot.
"""
from __future__ import annotations

import logging
import secrets
from datetime import date, datetime, timedelta
from typing import List, Optional

from db import get_conn, get_cursor

logger = logging.getLogger(__name__)

TOPICS = ("daily_digest", "bid_deadlines", "approvals", "payments",
          "security_alerts", "siem_critical")

TOPIC_LABELS = {
    "daily_digest":    "Daily digest (07:30) — ዕለታዊ ማጠቃለያ",
    "bid_deadlines":   "Bid deadlines — የጨረታ ቀነ-ገደብ",
    "approvals":       "Approvals — ማጽደቅ",
    "payments":        "Payments — ክፍያዎች",
    "security_alerts": "Security alerts — የደህንነት ማስጠንቀቂያ",
    "siem_critical":   "SIEM critical events",
}

LINK_CODE_TTL_MINUTES = 15
OUTBOX_MAX_ATTEMPTS = 5

# Unambiguous alphabet for the 6-char link code (no 0/O, 1/I/L).
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_links (
    id                SERIAL PRIMARY KEY,
    company_id        TEXT NOT NULL DEFAULT 'default',
    username          TEXT NOT NULL,
    chat_id           BIGINT NOT NULL UNIQUE,
    telegram_username TEXT NOT NULL DEFAULT '',
    first_name        TEXT NOT NULL DEFAULT '',
    linked_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active         BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_telegram_links_user ON telegram_links(username);
CREATE INDEX IF NOT EXISTS idx_telegram_links_company ON telegram_links(company_id);

CREATE TABLE IF NOT EXISTS telegram_link_codes (
    code        TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    company_id  TEXT NOT NULL DEFAULT 'default',
    expires_at  TIMESTAMP NOT NULL,
    used_at     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS telegram_subscriptions (
    chat_id     BIGINT NOT NULL,
    topic       TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chat_id, topic)
);

CREATE TABLE IF NOT EXISTS telegram_outbox (
    id              SERIAL PRIMARY KEY,
    chat_id         BIGINT NOT NULL,
    text            TEXT NOT NULL,
    parse_mode      TEXT NOT NULL DEFAULT 'HTML',
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending|sent|failed
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT NOT NULL DEFAULT '',
    next_attempt_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    sent_at         TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_telegram_outbox_status ON telegram_outbox(status, next_attempt_at);

CREATE TABLE IF NOT EXISTS telegram_updates_log (
    id          SERIAL PRIMARY KEY,
    update_id   BIGINT UNIQUE,
    chat_id     BIGINT,
    text        TEXT NOT NULL DEFAULT '',
    handled     BOOLEAN NOT NULL DEFAULT FALSE,
    response    TEXT NOT NULL DEFAULT '',
    received_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_telegram_updates_received ON telegram_updates_log(received_at DESC);
"""

# Bid statuses that still count as "open" for deadline listings (same set as
# reminder_job.REMINDABLE_STATUSES, duplicated so this module has no import
# dependency on the email job).
_OPEN_BID_STATUSES = ("open", "submitted", "pending", "in_progress", "in progress", "draft")

_columns_cache: dict = {}


def _table_columns(table_name: str) -> set:
    """Actual columns of a table (cached). Empty set when the table is missing."""
    cols = _columns_cache.get(table_name)
    if cols:
        return cols
    try:
        with get_cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name=%s",
                (table_name,),
            )
            cols = {r["column_name"] for r in cur.fetchall()}
    except Exception as e:
        logger.error("_table_columns(%s) failed: %s", table_name, e)
        cols = set()
    if cols:
        _columns_cache[table_name] = cols
    return cols


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("telegram schema ready")
    except Exception as e:
        logger.error("telegram schema init failed: %s", e)


def _generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


class TelegramDataStore:

    def ensure_schema(self):
        ensure_schema()

    # ── Link codes ────────────────────────────────────────────────
    def create_link_code(self, username: str, company_id: str,
                         ttl_minutes: int = LINK_CODE_TTL_MINUTES) -> Optional[dict]:
        """Generate a fresh one-time code for a logged-in EBMS user."""
        try:
            expires = datetime.now() + timedelta(minutes=ttl_minutes)
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for _ in range(5):                      # retry on the (rare) collision
                        code = _generate_code()
                        cur.execute(
                            """INSERT INTO telegram_link_codes(code, username, company_id, expires_at)
                               VALUES (%s,%s,%s,%s) ON CONFLICT (code) DO NOTHING RETURNING *""",
                            (code, username, company_id or "default", expires),
                        )
                        row = cur.fetchone()
                        if row:
                            return dict(row)
            return None
        except Exception as e:
            logger.error("create_link_code: %s", e); return None

    def consume_link_code(self, code: str) -> Optional[dict]:
        """Mark a code used and return {username, company_id}; None if invalid/expired."""
        code = (code or "").strip().upper()
        if len(code) != 6:
            return None
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE telegram_link_codes SET used_at=NOW()
                           WHERE code=%s AND used_at IS NULL AND expires_at > NOW()
                           RETURNING username, company_id""",
                        (code,),
                    )
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("consume_link_code: %s", e); return None

    def active_codes_for(self, username: str) -> List[dict]:
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT code, company_id, expires_at FROM telegram_link_codes
                       WHERE username=%s AND used_at IS NULL AND expires_at > NOW()
                       ORDER BY expires_at DESC""",
                    (username,),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("active_codes_for: %s", e); return []

    # ── Links ─────────────────────────────────────────────────────
    def link_chat(self, chat_id: int, username: str, company_id: str,
                  telegram_username: str = "", first_name: str = "") -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO telegram_links
                               (company_id, username, chat_id, telegram_username, first_name)
                           VALUES (%s,%s,%s,%s,%s)
                           ON CONFLICT (chat_id) DO UPDATE SET
                               company_id=EXCLUDED.company_id, username=EXCLUDED.username,
                               telegram_username=EXCLUDED.telegram_username,
                               first_name=EXCLUDED.first_name,
                               linked_at=NOW(), is_active=TRUE""",
                        (company_id or "default", username, int(chat_id),
                         (telegram_username or "")[:64], (first_name or "")[:128]),
                    )
            return True
        except Exception as e:
            logger.error("link_chat: %s", e); return False

    def get_link_by_chat(self, chat_id: int) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM telegram_links WHERE chat_id=%s AND is_active=TRUE",
                    (int(chat_id),),
                )
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_link_by_chat: %s", e); return None

    def links_for_user(self, username: str) -> List[dict]:
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT * FROM telegram_links WHERE username=%s AND is_active=TRUE
                       ORDER BY linked_at DESC""",
                    (username,),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("links_for_user: %s", e); return []

    def links_for_company(self, company_id: str) -> List[dict]:
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT * FROM telegram_links WHERE company_id=%s AND is_active=TRUE
                       ORDER BY linked_at DESC""",
                    (company_id,),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("links_for_company: %s", e); return []

    def unlink_chat(self, chat_id: int, username: str = None) -> bool:
        """Deactivate a link. When `username` is given the row must belong to
        that user (ownership check for the staff page)."""
        try:
            sql = "UPDATE telegram_links SET is_active=FALSE WHERE chat_id=%s"
            params = [int(chat_id)]
            if username:
                sql += " AND username=%s"; params.append(username)
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    ok = cur.rowcount > 0
                    if ok:
                        cur.execute("DELETE FROM telegram_subscriptions WHERE chat_id=%s",
                                    (int(chat_id),))
            return ok
        except Exception as e:
            logger.error("unlink_chat: %s", e); return False

    def user_roles(self, username: str) -> List[str]:
        """Privilege level of an EBMS user as a one-element role list."""
        if "privilege_level" not in _table_columns("users"):
            return []
        try:
            with get_cursor() as cur:
                cur.execute("SELECT privilege_level FROM users WHERE username=%s", (username,))
                row = cur.fetchone()
                return [row["privilege_level"]] if row and row.get("privilege_level") else []
        except Exception as e:
            logger.error("user_roles: %s", e); return []

    # ── Subscriptions ─────────────────────────────────────────────
    def get_subscriptions(self, chat_id: int) -> List[str]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT topic FROM telegram_subscriptions WHERE chat_id=%s ORDER BY topic",
                            (int(chat_id),))
                return [r["topic"] for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_subscriptions: %s", e); return []

    def subscribe(self, chat_id: int, topic: str) -> bool:
        if topic not in TOPICS:
            return False
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO telegram_subscriptions(chat_id, topic) VALUES (%s,%s)
                           ON CONFLICT DO NOTHING""",
                        (int(chat_id), topic),
                    )
            return True
        except Exception as e:
            logger.error("subscribe: %s", e); return False

    def unsubscribe(self, chat_id: int, topic: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM telegram_subscriptions WHERE chat_id=%s AND topic=%s",
                                (int(chat_id), topic))
            return True
        except Exception as e:
            logger.error("unsubscribe: %s", e); return False

    def set_subscriptions(self, chat_id: int, topics) -> bool:
        wanted = [t for t in (topics or []) if t in TOPICS]
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM telegram_subscriptions WHERE chat_id=%s", (int(chat_id),))
                    for t in wanted:
                        cur.execute("INSERT INTO telegram_subscriptions(chat_id, topic) VALUES (%s,%s)",
                                    (int(chat_id), t))
            return True
        except Exception as e:
            logger.error("set_subscriptions: %s", e); return False

    def chats_for_topic(self, company_id: str, topic: str) -> List[dict]:
        """Active links in a company subscribed to `topic`."""
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT l.* FROM telegram_links l
                       JOIN telegram_subscriptions s ON s.chat_id = l.chat_id
                       WHERE l.company_id=%s AND l.is_active=TRUE AND s.topic=%s""",
                    (company_id, topic),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("chats_for_topic: %s", e); return []

    def all_chats_for_topic(self, topic: str) -> List[dict]:
        """Active links (all companies) subscribed to `topic` — used by the digest."""
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT l.* FROM telegram_links l
                       JOIN telegram_subscriptions s ON s.chat_id = l.chat_id
                       WHERE l.is_active=TRUE AND s.topic=%s ORDER BY l.company_id""",
                    (topic,),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("all_chats_for_topic: %s", e); return []

    # ── Outbox ────────────────────────────────────────────────────
    def enqueue(self, chat_id: int, text: str, parse_mode: str = "HTML") -> Optional[int]:
        if not text:
            return None
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO telegram_outbox(chat_id, text, parse_mode)
                           VALUES (%s,%s,%s) RETURNING id""",
                        (int(chat_id), text[:4096], parse_mode or "HTML"),
                    )
                    return cur.fetchone()["id"]
        except Exception as e:
            logger.error("enqueue: %s", e); return None

    def pending_outbox(self, limit: int = 50) -> List[dict]:
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT * FROM telegram_outbox
                       WHERE status='pending' AND next_attempt_at <= NOW()
                       ORDER BY id LIMIT %s""",
                    (limit,),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("pending_outbox: %s", e); return []

    def mark_sent(self, outbox_id: int) -> None:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE telegram_outbox SET status='sent', sent_at=NOW(),
                           attempts=attempts+1, last_error='' WHERE id=%s""",
                        (outbox_id,),
                    )
        except Exception as e:
            logger.error("mark_sent: %s", e)

    def mark_failed(self, outbox_id: int, error: str, attempts: int, permanent: bool = False) -> None:
        """Record a failed attempt. Exponential backoff (2, 4, 8, 16 min);
        after OUTBOX_MAX_ATTEMPTS the row is marked failed for good."""
        n = attempts + 1
        final = permanent or n >= OUTBOX_MAX_ATTEMPTS
        delay = timedelta(minutes=min(2 ** n, 60))
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE telegram_outbox SET status=%s, attempts=%s, last_error=%s,
                           next_attempt_at=%s WHERE id=%s""",
                        ("failed" if final else "pending", n, (error or "")[:500],
                         datetime.now() + delay, outbox_id),
                    )
        except Exception as e:
            logger.error("mark_failed: %s", e)

    def recent_outbox(self, limit: int = 50) -> List[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM telegram_outbox ORDER BY id DESC LIMIT %s", (limit,))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("recent_outbox: %s", e); return []

    # ── Updates log ───────────────────────────────────────────────
    def log_update(self, update_id, chat_id, text: str) -> bool:
        """Insert an update. Returns False when update_id was already seen."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO telegram_updates_log(update_id, chat_id, text)
                           VALUES (%s,%s,%s) ON CONFLICT (update_id) DO NOTHING RETURNING id""",
                        (int(update_id) if update_id is not None else None,
                         int(chat_id) if chat_id is not None else None,
                         (text or "")[:1000]),
                    )
                    return cur.fetchone() is not None
        except Exception as e:
            logger.error("log_update: %s", e)
            return True         # never drop an update because logging failed

    def mark_handled(self, update_id, response: str) -> None:
        if update_id is None:
            return
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE telegram_updates_log SET handled=TRUE, response=%s WHERE update_id=%s",
                        ((response or "")[:1000], int(update_id)),
                    )
        except Exception as e:
            logger.error("mark_handled: %s", e)

    def recent_updates(self, limit: int = 50) -> List[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM telegram_updates_log ORDER BY id DESC LIMIT %s", (limit,))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("recent_updates: %s", e); return []

    # ── Dashboard stats ───────────────────────────────────────────
    def get_stats(self) -> dict:
        stats = {"links": 0, "subscriptions": 0, "outbox_pending": 0, "outbox_sent": 0,
                 "outbox_failed": 0, "updates": 0, "updates_24h": 0}
        try:
            with get_cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM telegram_links WHERE is_active=TRUE")
                stats["links"] = cur.fetchone()["n"]
                cur.execute("SELECT COUNT(*) AS n FROM telegram_subscriptions")
                stats["subscriptions"] = cur.fetchone()["n"]
                cur.execute("SELECT status, COUNT(*) AS n FROM telegram_outbox GROUP BY status")
                for r in cur.fetchall():
                    stats[f"outbox_{r['status']}"] = r["n"]
                cur.execute("SELECT COUNT(*) AS n FROM telegram_updates_log")
                stats["updates"] = cur.fetchone()["n"]
                cur.execute("SELECT COUNT(*) AS n FROM telegram_updates_log "
                            "WHERE received_at > NOW() - INTERVAL '24 hours'")
                stats["updates_24h"] = cur.fetchone()["n"]
        except Exception as e:
            logger.error("telegram get_stats: %s", e)
        return stats

    # ── Read-only business queries used by bot commands ───────────
    def income_expense_totals(self, company_id: str, start: date, end: date) -> dict:
        """Gross income / expenses between start and end (inclusive)."""
        out = {"income": 0.0, "expenses": 0.0, "income_count": 0, "expense_count": 0,
               "start": start, "end": end}
        for table, date_col, key in (("vat_income", "contract_date", "income"),
                                     ("vat_expenses", "expense_date", "expenses")):
            cols = _table_columns(table)
            if not {date_col, "gross_amount", "company_id"} <= cols:
                continue
            active = " AND is_active=TRUE" if "is_active" in cols else ""
            try:
                with get_cursor() as cur:
                    cur.execute(
                        f"SELECT COALESCE(SUM(gross_amount),0) AS total, COUNT(*) AS n "
                        f"FROM {table} WHERE company_id=%s{active} "
                        f"AND {date_col} >= %s AND {date_col} <= %s",
                        (company_id, start, end),
                    )
                    row = cur.fetchone() or {}
                out[key] = _f(row.get("total"))
                out[f"{'income' if key == 'income' else 'expense'}_count"] = int(row.get("n") or 0)
            except Exception as e:
                logger.error("income_expense_totals(%s): %s", table, e)
        return out

    def bids_due(self, company_id: str, days: int = 7, today: date = None, limit: int = 10) -> List[dict]:
        """Open bids whose deadline falls within the next `days` days."""
        cols = _table_columns("bid_records")
        if not {"deadline", "company_id"} <= cols:
            return []
        try:
            from reminder_job import parse_deadline
        except Exception:                        # pragma: no cover — defensive
            parse_deadline = lambda v: None      # noqa: E731
        want = [c for c in ("id", "title", "reference_number", "organization", "status",
                            "deadline", "bid_amount", "currency") if c in cols]
        sql = f"SELECT {', '.join(want)} FROM bid_records WHERE company_id=%s"
        params = [company_id]
        if "status" in cols:
            sql += " AND LOWER(COALESCE(status,'')) = ANY(%s)"
            params.append(list(_OPEN_BID_STATUSES))
        try:
            with get_cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("bids_due: %s", e); return []
        today = today or date.today()
        out = []
        for r in rows:
            d = parse_deadline(r.get("deadline"))
            if d is None:
                continue
            delta = (d - today).days
            if 0 <= delta <= days:
                r["deadline_date"] = d
                r["days_left"] = delta
                out.append(r)
        out.sort(key=lambda r: r["deadline_date"])
        return out[:limit]

    def recent_cpos(self, company_id: str, limit: int = 5) -> List[dict]:
        cols = _table_columns("cpo_records")
        if not {"company_id", "amount"} <= cols:
            return []
        want = [c for c in ("id", "name", "date", "amount", "bid_name", "is_returned", "created_at")
                if c in cols]
        order = "created_at DESC" if "created_at" in cols else "id DESC"
        try:
            with get_cursor() as cur:
                cur.execute(
                    f"SELECT {', '.join(want)} FROM cpo_records WHERE company_id=%s "
                    f"ORDER BY {order} LIMIT %s",
                    (company_id, limit),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("recent_cpos: %s", e); return []

    def cpo_outstanding(self, company_id: str) -> dict:
        """Count + total of CPOs not yet returned."""
        cols = _table_columns("cpo_records")
        if not {"company_id", "amount", "is_returned"} <= cols:
            return {"count": 0, "total": 0.0}
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n, COALESCE(SUM(amount),0) AS total FROM cpo_records "
                    "WHERE company_id=%s AND LOWER(COALESCE(is_returned,'false')) <> 'true'",
                    (company_id,),
                )
                row = cur.fetchone() or {}
                return {"count": int(row.get("n") or 0), "total": _f(row.get("total"))}
        except Exception as e:
            logger.error("cpo_outstanding: %s", e); return {"count": 0, "total": 0.0}

    def employee_count(self, company_id: str) -> int:
        cols = _table_columns("employees")
        if "company_id" not in cols:
            return 0
        active = " AND is_active=TRUE" if "is_active" in cols else ""
        try:
            with get_cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS n FROM employees WHERE company_id=%s{active}",
                            (company_id,))
                return int(cur.fetchone()["n"])
        except Exception as e:
            logger.error("employee_count: %s", e); return 0

    def low_stock(self, company_id: str, limit: int = 10) -> List[dict]:
        """Active inventory items at or below their minimum / reorder level."""
        cols = _table_columns("inventory_items")
        if not {"company_id", "current_stock"} <= cols:
            return []
        thresholds = [c for c in ("min_stock_level", "reorder_point") if c in cols]
        if not thresholds:
            return []
        thr = f"GREATEST({', '.join(thresholds)})" if len(thresholds) > 1 else thresholds[0]
        want = [c for c in ("id", "sku", "name", "unit", "current_stock", "min_stock_level",
                            "reorder_point", "location") if c in cols]
        status = " AND status='active'" if "status" in cols else ""
        try:
            with get_cursor() as cur:
                cur.execute(
                    f"SELECT {', '.join(want)}, {thr} AS threshold FROM inventory_items "
                    f"WHERE company_id=%s{status} AND {thr} > 0 AND current_stock <= {thr} "
                    f"ORDER BY (current_stock - {thr}) ASC, name LIMIT %s",
                    (company_id, limit),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("low_stock: %s", e); return []

    def payments_today(self, company_id: str, today: date = None) -> Optional[dict]:
        """Today's mobile-money summary if the payments module is present.
        Returns None when the module is not installed."""
        try:
            import importlib
            mod = importlib.import_module("payments_data_store")
        except Exception:
            return None
        today = today or date.today()
        obj = getattr(mod, "payments_store", None) or getattr(mod, "store", None) or mod
        for name in ("today_summary", "get_today_summary", "daily_summary", "summary_for_day"):
            fn = getattr(obj, name, None)
            if not callable(fn):
                continue
            for args in ((company_id, today), (company_id,)):
                try:
                    res = fn(*args)
                    if isinstance(res, dict):
                        return res
                except TypeError:
                    continue
                except Exception as e:
                    logger.error("payments_today via %s: %s", name, e)
                    return {}
        return {}


telegram_store = TelegramDataStore()
