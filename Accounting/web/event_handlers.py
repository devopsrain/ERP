"""
EBMS Event Handlers — cross-module side-effects triggered by business events.

All handlers self-register on the module-level event_bus via the @event_bus.on
decorator at import time.  They are loaded once in the FastAPI lifespan
(app.py) and again in the standalone event-worker process (python -m events).

Registered events
─────────────────
payroll.completed
    → Post summary journal entry (Salary Expense Dr / Payables Cr)
    → Log to SIEM audit trail
    → Trigger incremental backup

account.created
    → Invalidate chart-of-accounts cache for the company

user.login_failed
    → Log MEDIUM-severity SIEM event for brute-force detection
"""
from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace

from events import event_bus

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_request(company_id: str = "default") -> SimpleNamespace:
    """
    Fabricate a minimal request-like object for SIEM methods that expect
    a FastAPI Request (used only in background event handlers with no real request).
    """
    return SimpleNamespace(
        headers={},
        client=None,
        session={"company_id": company_id},
    )


# ─────────────────────────────────────────────────────────────────────────────
# payroll.completed
#
# Expected payload keys:
#   company_id          str
#   period              str   "YYYY-MM"
#   total_employees     int
#   total_gross_pay     float
#   total_net_pay       float
#   total_income_tax    float
#   total_employee_pension  float
#   total_employer_pension  float
#   created_by          str
# ─────────────────────────────────────────────────────────────────────────────

@event_bus.on("payroll.completed")
async def _post_payroll_journal(payload: dict) -> None:
    """
    Automatically post a summary journal entry when a payroll run completes.

    Double-entry:
      Dr 6000  Salaries & Wages Expense        gross_pay + employer_pension
      Cr 2200  Income Tax Payable              total_income_tax
      Cr 2300  Pension Payable                 employee_pension + employer_pension
      Cr 2000  Wages Payable (net to staff)    net_pay
    """
    company_id   = payload.get("company_id", "default")
    period       = payload.get("period", datetime.utcnow().strftime("%Y-%m"))
    gross        = float(payload.get("total_gross_pay", 0))
    net          = float(payload.get("total_net_pay", 0))
    tax          = float(payload.get("total_income_tax", 0))
    emp_pens     = float(payload.get("total_employee_pension", 0))
    emp_r_pens   = float(payload.get("total_employer_pension", 0))
    created_by   = payload.get("created_by", "system")

    if gross <= 0:
        logger.info("payroll_journal_skipped: zero gross for %s/%s", company_id, period)
        return

    total_dr = gross + emp_r_pens
    total_cr = tax + (emp_pens + emp_r_pens) + net

    entry_data = {
        "company_id":       company_id,
        "entry_date":       datetime.utcnow().date(),
        "description":      f"Payroll — {period}",
        "reference_number": f"PAYROLL-{period}",
        "total_debit":      round(total_dr, 2),
        "total_credit":     round(total_cr, 2),
    }
    lines = [
        {
            "account_code": "6000", "account_name": "Salaries and Wages",
            "description": f"Gross pay — {period}",
            "debit_amount": round(gross, 2), "credit_amount": 0.0,
        },
        {
            "account_code": "6000", "account_name": "Employer Pension Contribution",
            "description": f"Employer pension (11%) — {period}",
            "debit_amount": round(emp_r_pens, 2), "credit_amount": 0.0,
        },
        {
            "account_code": "2200", "account_name": "Income Tax Payable",
            "description": f"PAYE — {period}",
            "debit_amount": 0.0, "credit_amount": round(tax, 2),
        },
        {
            "account_code": "2300", "account_name": "Pension Payable",
            "description": f"Employee + employer pension — {period}",
            "debit_amount": 0.0, "credit_amount": round(emp_pens + emp_r_pens, 2),
        },
        {
            "account_code": "2000", "account_name": "Wages Payable",
            "description": f"Net salaries — {period}",
            "debit_amount": 0.0, "credit_amount": round(net, 2),
        },
    ]

    try:
        from journal_entry_data_store import JournalEntryDataStore
        entry_id = JournalEntryDataStore().save_journal_entry(entry_data, lines)
        logger.info(
            "payroll_journal_posted: company=%s period=%s entry=%s gross=%.2f",
            company_id, period, entry_id, gross,
        )
    except Exception as exc:
        logger.error("payroll_journal_failed: %s", exc, exc_info=True)


@event_bus.on("payroll.completed")
async def _siem_log_payroll(payload: dict) -> None:
    """Log payroll completion to the SIEM audit trail."""
    company_id = payload.get("company_id", "default")
    period     = payload.get("period", "?")
    employees  = payload.get("total_employees", 0)
    gross      = float(payload.get("total_gross_pay", 0))
    created_by = payload.get("created_by", "system")

    try:
        from siem_data_store import siem_store
        siem_store.log_upload_event(
            _fake_request(company_id),
            module="payroll",
            endpoint="/payroll/calculate",
            status="success",
            user=created_by,
            details=(
                f"Payroll run completed: {employees} employees, "
                f"ETB {gross:,.2f} gross — period {period}"
            ),
        )
    except Exception as exc:
        logger.error("payroll_siem_log_failed: %s", exc)


@event_bus.on("payroll.completed")
async def _backup_after_payroll(payload: dict) -> None:
    """Trigger an incremental backup after each payroll run."""
    period = payload.get("period", "?")
    try:
        from backup_data_store import BackupEngine
        BackupEngine().create_backup(
            label=f"Post-payroll {period}",
            triggered_by="event:payroll.completed",
        )
        logger.info("payroll_backup_triggered: period=%s", period)
    except Exception as exc:
        logger.warning("payroll_backup_skipped: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# account.created — purge chart-of-accounts cache
# ─────────────────────────────────────────────────────────────────────────────

@event_bus.on("account.created")
async def _on_account_created(payload: dict) -> None:
    company_id = payload.get("company_id", "default")
    try:
        from extensions import cache
        cache.delete(f"accounts:{company_id}")
        cache.delete(f"dashboard_stats:{company_id}")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# user.login_failed — SIEM brute-force detection
# ─────────────────────────────────────────────────────────────────────────────

@event_bus.on("user.login_failed")
async def _on_login_failed(payload: dict) -> None:
    username   = payload.get("username", "?")
    ip         = payload.get("ip", "unknown")
    company_id = payload.get("company_id", "default")
    try:
        from siem_data_store import siem_store
        req = _fake_request(company_id)
        # Inject the real IP so SIEM records it correctly
        req.headers = {"X-Real-IP": ip}
        siem_store.log_upload_event(
            req,
            module="auth",
            endpoint="/auth/login",
            status="failure",
            user=username,
            details=f"Failed login attempt for '{username}' from {ip}",
        )
    except Exception as exc:
        logger.error("login_failed_siem_error: %s", exc)
