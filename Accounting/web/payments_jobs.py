"""
Scheduled jobs for the mobile-money payments module.

app.py picks this up automatically via ``register_jobs(scheduler)`` on the
shared APScheduler BackgroundScheduler:

  * every 5 minutes — turn unprocessed provider notifications logged in
    ``webhook_inbound_log`` (telebirr / cbebirr / mpesa) into payments
  * nightly 02:30   — auto-match unreconciled payments against income /
    expense records (only unambiguous high-confidence candidates)

Both jobs are no-ops when there is nothing to do and never raise.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

INBOUND_INTERVAL_MINUTES = 5
AUTO_MATCH_HOUR = 2
AUTO_MATCH_MINUTE = 30


def process_inbound_job() -> dict:
    try:
        from payments_data_store import payments_store
        summary = payments_store.process_inbound_notifications()
        if summary.get("scanned"):
            logger.info("payments inbound: %s", "; ".join(summary.get("messages", [])))
        return summary
    except Exception as e:
        logger.warning("payments inbound job failed: %s", e)
        return {"error": str(e)}


def auto_match_job() -> dict:
    totals = {"companies": 0, "scanned": 0, "linked": 0, "skipped": 0}
    try:
        from payments_data_store import payments_store
        for cid in payments_store.companies_with_unmatched():
            res = payments_store.auto_match(cid, by="auto-match")
            totals["companies"] += 1
            for k in ("scanned", "linked", "skipped"):
                totals[k] += res.get(k, 0)
        if totals["companies"]:
            logger.info("payments auto-match: %s", totals)
    except Exception as e:
        logger.warning("payments auto-match job failed: %s", e)
        totals["error"] = str(e)
    return totals


def register_jobs(scheduler) -> None:
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler.add_job(process_inbound_job, trigger=IntervalTrigger(minutes=INBOUND_INTERVAL_MINUTES),
                      id="payments_process_inbound", replace_existing=True, coalesce=True, max_instances=1)
    scheduler.add_job(auto_match_job, trigger=CronTrigger(hour=AUTO_MATCH_HOUR, minute=AUTO_MATCH_MINUTE),
                      id="payments_auto_match", replace_existing=True, coalesce=True, max_instances=1)
