"""
Manufacturing — scheduled jobs.

``register_jobs(scheduler)`` is picked up by app.py's shared APScheduler
BackgroundScheduler (import is side-effect free).

* daily 06:00  — compute yesterday's line/machine KPIs into ``mfg_daily_kpis``
                 (idempotent: UNIQUE(company, date, work centre, machine) + upsert)
* weekly Mon 05:30 — flag production orders past their delivery / planned end
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

KPI_JOB_ID = "manufacturing_daily_kpis"
PAST_DUE_JOB_ID = "manufacturing_weekly_past_due"


def run_daily_kpis(day: Optional[date] = None) -> dict:
    """Compute KPIs for ``day`` (default yesterday) for every company with manufacturing data."""
    from manufacturing_data_store import manufacturing_store
    day = day or (date.today() - timedelta(days=1))
    summary = {"date": day.isoformat(), "companies": {}}
    for cid in manufacturing_store.companies():
        try:
            n = manufacturing_store.compute_daily_kpis(cid, day)
            summary["companies"][cid] = n
            logger.info("manufacturing kpis: company=%s date=%s rows=%s", cid, day, n)
        except Exception as e:  # one bad company must not stop the others
            logger.error("manufacturing kpis: company=%s failed: %s", cid, e)
            summary["companies"][cid] = {"error": str(e)}
    return summary


def run_past_due_flags() -> dict:
    from manufacturing_data_store import manufacturing_store
    try:
        n = manufacturing_store.flag_past_due()
        logger.info("manufacturing past-due flags refreshed on %s orders", n)
        return {"updated": n}
    except Exception as e:
        logger.error("manufacturing past-due job failed: %s", e)
        return {"error": str(e)}


def register_jobs(scheduler) -> None:
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(run_daily_kpis, CronTrigger(hour=6, minute=0), id=KPI_JOB_ID,
                      replace_existing=True, misfire_grace_time=6 * 3600, coalesce=True)
    scheduler.add_job(run_past_due_flags, CronTrigger(day_of_week="mon", hour=5, minute=30), id=PAST_DUE_JOB_ID,
                      replace_existing=True, misfire_grace_time=24 * 3600, coalesce=True)
    logger.info("manufacturing: daily KPI (06:00) and weekly past-due (Mon 05:30) jobs registered")
