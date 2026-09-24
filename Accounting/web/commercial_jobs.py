"""
Commercial module — scheduled jobs (registered on app.py's shared APScheduler).

* ``commercial_daily_credit_check``  — daily 06:30: mark overdue invoices, then
  record credit-limit breaches and overdue credit-sales reminders (audit trail +
  notifications module when available).
* ``commercial_monthly_forecast``    — 1st of the month 06:30: refresh the
  automatic sales forecast (3/6-month moving average + linear trend) for the
  new month, per company.

``register_jobs(scheduler)`` only adds jobs; nothing touches the database at
import time.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

DAILY_JOB_ID = "commercial_daily_credit_check"
MONTHLY_JOB_ID = "commercial_monthly_forecast"


def _notify(company_id: str, title: str, body: str) -> None:
    """Best-effort: push to the notifications module if it exists."""
    try:
        import notifications_data_store as nds
        fn = getattr(nds, "notify", None) or getattr(getattr(nds, "notification_store", None), "create", None)
        if callable(fn):
            fn(company_id=company_id, title=title, message=body, category="commercial")
    except Exception as e:  # pragma: no cover — optional integration
        logger.debug("commercial notify skipped: %s", e)


def run_daily_credit_check(today=None) -> dict:
    from commercial_data_store import commercial_store as store
    summary = {"companies": {}}
    for cid in store.companies():
        try:
            marked = store.mark_overdue(cid)
            breaches = store.credit_breaches(cid)
            overdue = store.overdue_invoices(cid)
            for c in breaches:
                store.add_event(cid, "customer", c["id"], "credit_breach",
                                f"Balance {c['balance']:,} exceeds credit limit {c['credit_limit']:,}", "scheduler")
            for inv in overdue:
                store.add_event(cid, "invoice", inv["id"], "overdue_reminder",
                                f"Invoice {inv['invoice_no']} overdue — outstanding {inv['outstanding']:,}", "scheduler")
            if breaches or overdue:
                _notify(cid, "Commercial: credit control",
                        f"{len(breaches)} customer(s) over credit limit; {len(overdue)} overdue credit invoice(s)")
            summary["companies"][cid] = {"marked_overdue": marked, "breaches": len(breaches), "overdue": len(overdue)}
            logger.info("commercial daily: company=%s overdue=%s breaches=%s", cid, len(overdue), len(breaches))
        except Exception as e:  # one bad company must not stop the others
            logger.error("commercial daily job: company=%s failed: %s", cid, e)
            summary["companies"][cid] = {"error": str(e)}
    return summary


def run_monthly_forecast(period: Optional[str] = None) -> dict:
    from commercial_data_store import commercial_store as store
    import commercial_logic as L
    from datetime import date
    period = period or L.next_period(L.period_of(date.today()))
    summary = {"period": period, "companies": {}}
    for cid in store.companies():
        try:
            n = store.refresh_forecasts(cid, period, actor="scheduler")
            summary["companies"][cid] = {"rows": n}
            logger.info("commercial forecast: company=%s period=%s rows=%s", cid, period, n)
        except Exception as e:
            logger.error("commercial forecast: company=%s failed: %s", cid, e)
            summary["companies"][cid] = {"error": str(e)}
    return summary


def register_jobs(scheduler) -> None:
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(run_daily_credit_check, CronTrigger(hour=6, minute=30), id=DAILY_JOB_ID,
                      replace_existing=True, misfire_grace_time=6 * 3600, coalesce=True)
    scheduler.add_job(run_monthly_forecast, CronTrigger(day=1, hour=6, minute=30), id=MONTHLY_JOB_ID,
                      replace_existing=True, misfire_grace_time=24 * 3600, coalesce=True)
    logger.info("commercial: daily credit check (06:30) and monthly forecast (1st 06:30) jobs registered")
