"""
Fixed Assets — scheduled jobs.

``register_jobs(scheduler)`` is picked up by app.py's shared APScheduler
BackgroundScheduler. The monthly job runs on the 1st at 02:00 and records
the previous month's depreciation for every active asset of every company.
It is idempotent: fixed_asset_depreciation has UNIQUE(asset_id, period) and
the store inserts with ON CONFLICT DO NOTHING, so re-running a month (or the
job firing after a manual run) never duplicates a charge.

Units-of-production assets are skipped by the job — they need the period's
actual units, entered on the depreciation run page.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

JOB_ID = "fixed_assets_monthly_depreciation"


def run_monthly_depreciation(period: Optional[str] = None, today: Optional[date] = None) -> dict:
    """Compute and store depreciation for ``period`` (default: last month)
    across all companies. Returns a per-company summary."""
    from fixed_assets_data_store import fixed_asset_store, previous_period
    period = period or previous_period(today)
    summary = {"period": period, "companies": {}}
    for cid in fixed_asset_store.companies_with_active_assets():
        try:
            res = fixed_asset_store.run_period(cid, period)
            summary["companies"][cid] = res
            if res.get("error"):
                logger.error("fixed_assets job: company=%s period=%s error=%s", cid, period, res["error"])
            else:
                logger.info("fixed_assets job: company=%s period=%s inserted=%s skipped=%s total=%s",
                            cid, period, res["inserted"], res["skipped"], res["total"])
        except Exception as e:   # one bad company must not stop the others
            logger.error("fixed_assets job: company=%s failed: %s", cid, e)
            summary["companies"][cid] = {"error": str(e)}
    return summary


def register_jobs(scheduler) -> None:
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(
        run_monthly_depreciation,
        CronTrigger(day=1, hour=2, minute=0),
        id=JOB_ID,
        replace_existing=True,
        misfire_grace_time=6 * 3600,   # still run if the app was down at 02:00
        coalesce=True,
    )
    logger.info("fixed_assets: monthly depreciation job registered (day 1, 02:00)")
