"""
Webhook background jobs — hooked onto the shared APScheduler in app.py via
``register_jobs(scheduler)``.

  retry_pending_deliveries   every 2 minutes — resend due pending/failed rows
                             (exponential backoff, up to MAX_ATTEMPTS)
  prune_webhook_logs         daily 03:30      — drop delivery + inbound rows
                             older than LOG_RETENTION_DAYS (30)
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

RETRY_INTERVAL_MINUTES = 2
PRUNE_HOUR, PRUNE_MINUTE = 3, 30


def retry_pending_deliveries() -> dict:
    try:
        from webhook_data_store import retry_due_deliveries
        counters = retry_due_deliveries(limit=200)
        if counters.get("claimed"):
            logger.info("webhook_retry: %s", counters)
        return counters
    except Exception as exc:
        logger.error("webhook_retry job failed: %s", exc)
        return {"claimed": 0, "success": 0, "failed": 0, "error": str(exc)}


def prune_webhook_logs() -> dict:
    try:
        from webhook_data_store import LOG_RETENTION_DAYS, webhook_store
        out = webhook_store.prune_old(LOG_RETENTION_DAYS)
        logger.info("webhook_prune: %s", out)
        return out
    except Exception as exc:
        logger.error("webhook_prune job failed: %s", exc)
        return {"deliveries": 0, "inbound": 0, "error": str(exc)}


def register_jobs(scheduler) -> None:
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler.add_job(
        retry_pending_deliveries,
        trigger=IntervalTrigger(minutes=RETRY_INTERVAL_MINUTES),
        id="webhook_retry_pending",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        prune_webhook_logs,
        trigger=CronTrigger(hour=PRUNE_HOUR, minute=PRUNE_MINUTE),
        id="webhook_prune_logs",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
