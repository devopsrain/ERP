"""
Telegram scheduled jobs — registered on the shared APScheduler from app.py
via register_jobs(scheduler).

  * flush_outbox        every minute  — deliver queued telegram_outbox rows,
                                        exponential-backoff retry, max 5 attempts
  * send_daily_digest   07:30 daily   — yesterday's income/expenses, bids due in
                                        3 days and pending approvals to every
                                        chat subscribed to 'daily_digest'

Both are no-ops when TELEGRAM_BOT_TOKEN is unset and never raise. They can be
run by hand:

    docker compose exec web python -c "from telegram_jobs import flush_outbox; print(flush_outbox())"
    docker compose exec web python -c "from telegram_jobs import send_daily_digest; print(send_daily_digest())"
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

DIGEST_HOUR = 7
DIGEST_MINUTE = 30
DIGEST_BID_DAYS = 3
OUTBOX_BATCH = 50


def _company_name(company_id: str) -> str:
    """Best-effort display name; falls back to the id. info_schema-guarded."""
    try:
        from telegram_data_store import _table_columns
        from db import get_cursor
        cols = _table_columns("companies")
        key = next((c for c in ("company_id", "id") if c in cols), None)
        if not key or "name" not in cols:
            return company_id
        with get_cursor() as cur:
            cur.execute(f"SELECT name FROM companies WHERE {key}=%s", (company_id,))
            row = cur.fetchone()
            return (row or {}).get("name") or company_id
    except Exception:
        return company_id


def flush_outbox(limit: int = OUTBOX_BATCH) -> dict:
    """Deliver pending outbox rows. Returns {'sent': n, 'failed': n, 'skipped': n}."""
    result = {"sent": 0, "failed": 0, "skipped": 0}
    try:
        import telegram_bot
        from telegram_data_store import telegram_store as store
    except Exception as e:
        logger.error("telegram_jobs imports failed: %s", e)
        return result
    if not telegram_bot.is_configured():
        return result
    try:
        rows = store.pending_outbox(limit)
    except Exception as e:
        logger.error("flush_outbox query failed: %s", e)
        return result
    for row in rows:
        try:
            res = telegram_bot.send_message(row["chat_id"], row["text"], row.get("parse_mode") or "HTML")
        except Exception as e:                       # send_message shouldn't raise, but be safe
            res = {"ok": False, "description": str(e), "transport_error": True}
        if res.get("ok"):
            store.mark_sent(row["id"])
            result["sent"] += 1
            continue
        desc = str(res.get("description") or "unknown error")
        # 400 (bad chat / malformed HTML) and 403 (bot blocked by user) never
        # succeed on retry — fail them immediately. Transport errors retry.
        code = res.get("error_code")
        permanent = code in (400, 403) or "chat not found" in desc.lower() or "blocked" in desc.lower()
        store.mark_failed(row["id"], desc, int(row.get("attempts") or 0), permanent=permanent)
        result["failed"] += 1
    if rows:
        logger.info("telegram flush_outbox: %s", result)
    return result


def send_daily_digest(today: date = None) -> int:
    """Queue the morning digest for every 'daily_digest' subscriber.
    Returns the number of messages queued. Never raises."""
    try:
        import telegram_bot
        from telegram_data_store import telegram_store as store
    except Exception as e:
        logger.error("telegram digest imports failed: %s", e)
        return 0
    if not telegram_bot.is_configured():
        return 0
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    try:
        subscribers = store.all_chats_for_topic("daily_digest")
    except Exception as e:
        logger.error("digest subscriber query failed: %s", e)
        return 0

    per_company: dict = {}          # company_id -> (totals, bids, name, employees)
    queued = 0
    for link in subscribers:
        cid = link.get("company_id") or "default"
        try:
            if cid not in per_company:
                per_company[cid] = (
                    store.income_expense_totals(cid, yesterday, yesterday),
                    store.bids_due(cid, DIGEST_BID_DAYS, today),
                    _company_name(cid),
                    store.employee_count(cid),
                )
            totals, bids, name, emp = per_company[cid]
            approvals = telegram_bot.pending_approvals(
                cid, link["username"], store.user_roles(link["username"]))
            text = telegram_bot.format_digest(name, today, totals, bids, approvals, emp)
            if store.enqueue(link["chat_id"], text):
                queued += 1
        except Exception as e:
            logger.error("digest for chat %s failed: %s", link.get("chat_id"), e)
    logger.info("telegram daily digest queued for %d chat(s)", queued)
    return queued


def register_jobs(scheduler) -> None:
    """Hook both jobs onto the shared BackgroundScheduler (called from app.py)."""
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler.add_job(
        flush_outbox, trigger=IntervalTrigger(minutes=1),
        id="telegram_outbox_flush", replace_existing=True,
        max_instances=1, coalesce=True, misfire_grace_time=30,
    )
    scheduler.add_job(
        send_daily_digest, trigger=CronTrigger(hour=DIGEST_HOUR, minute=DIGEST_MINUTE),
        id="telegram_daily_digest", replace_existing=True,
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    logger.info("telegram jobs registered (outbox every minute, digest %02d:%02d)",
                DIGEST_HOUR, DIGEST_MINUTE)
