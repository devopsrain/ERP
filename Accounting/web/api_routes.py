"""
REST API v1 — JSON endpoints for the Ethiopian Business Management System

All routes live under the /api/v1/ prefix and return JSON exclusively.
Authentication uses the same Bearer token mechanism as the HTML routes.

These endpoints allow:
  - Mobile app integration
  - Third-party ERP integrations
  - Programmatic data access (scripts, Power BI, etc.)

Auth:
    All endpoints require a valid Bearer token (issued from /auth/portal)
    or an active session cookie.

    Authorization: Bearer <token>

Versioning:
    The /api/v1/ prefix means future breaking changes become /api/v2/,
    allowing old integrations to keep working.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from deps import login_required, admin_required, template_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["API v1"])


# ── Helpers ───────────────────────────────────────────────────────

def _company(request: Request) -> str:
    return getattr(request.state, "company_id", None) or request.session.get("current_company_id", "default")


def _ok(data, **meta) -> dict:
    return {"status": "ok", "data": data, **meta}


def _err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"status": "error", "error": msg}, status_code=code)


# ── Health ────────────────────────────────────────────────────────

@router.get("/health", name="api_health", include_in_schema=True)
async def api_health(request: Request):
    """
    Detailed health check — verifies DB connectivity.

    Returns 200 with {"status": "ok"} when all services are reachable.
    Returns 503 with {"status": "degraded"} when DB is unreachable.
    """
    checks: dict = {"app": "ok"}
    status_code = 200

    # DB check
    try:
        from db import get_cursor
        with get_cursor() as cur:
            cur.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
        status_code = 503

    # Cache check
    try:
        from extensions import cache
        cache.set("_health", "1", timeout=5)
        checks["cache"] = "ok"
    except Exception:
        checks["cache"] = "unavailable"

    overall = "ok" if status_code == 200 else "degraded"
    return JSONResponse({"status": overall, "checks": checks, "ts": datetime.utcnow().isoformat()},
                        status_code=status_code)


# ── Accounts ──────────────────────────────────────────────────────

@router.get("/accounts", name="api_list_accounts")
async def list_accounts(
    request: Request,
    account_type: Optional[str] = Query(None, description="Filter by account type"),
    user=Depends(login_required),
):
    """
    List all accounts in the current company's chart of accounts.

    Query params:
        account_type: Filter by type (ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE)
    """
    try:
        from chart_of_accounts_data_store import chart_store
        company_id = _company(request)
        df = chart_store.read_all_accounts(company_id=company_id)
        accounts = df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
        if account_type:
            accounts = [a for a in accounts if str(a.get("account_type", "")).upper() == account_type.upper()]
        return _ok(accounts, count=len(accounts))
    except Exception as e:
        logger.error("api list_accounts: %s", e)
        return _err(str(e), 500)


@router.get("/accounts/{account_id}", name="api_get_account")
async def get_account(account_id: str, request: Request, user=Depends(login_required)):
    """Get a single account by ID."""
    try:
        from chart_of_accounts_data_store import chart_store
        account = chart_store.get_account_by_code(account_id, _company(request))
        if not account:
            return _err("Account not found", 404)
        return _ok(account)
    except Exception as e:
        logger.error("api get_account: %s", e)
        return _err(str(e), 500)


# ── Transactions ──────────────────────────────────────────────────

@router.get("/transactions", name="api_list_transactions")
async def list_transactions(
    request: Request,
    limit: int = Query(50, ge=1, le=500, description="Number of records to return"),
    offset: int = Query(0, ge=0),
    flagged: bool = Query(False, description="Return only flagged transactions"),
    user=Depends(login_required),
):
    """
    List transactions for the current company.

    Query params:
        limit:   Max records (default 50, max 500)
        offset:  Pagination offset
        flagged: If true, return only flagged transactions
    """
    try:
        from transaction_data_store import transaction_store
        company_id = _company(request)
        raw = transaction_store.get_transactions(company_id=company_id)
        transactions = raw.to_dict(orient="records") if hasattr(raw, "to_dict") else list(raw)
        if flagged:
            transactions = [t for t in transactions if t.get("is_flagged")]
        total = len(transactions)
        page = transactions[offset:offset + limit]
        return _ok(page, total=total, limit=limit, offset=offset)
    except Exception as e:
        logger.error("api list_transactions: %s", e)
        return _err(str(e), 500)


# ── Journal Entries ───────────────────────────────────────────────

@router.get("/journal", name="api_list_journal")
async def list_journal_entries(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user=Depends(login_required),
):
    """List journal entries for the current company."""
    try:
        from journal_entry_data_store import journal_store
        company_id = _company(request)
        df = journal_store.read_journal_entries(company_id=company_id)
        entries = df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
        total = len(entries)
        return _ok(entries[offset:offset + limit], total=total, limit=limit, offset=offset)
    except Exception as e:
        logger.error("api list_journal: %s", e)
        return _err(str(e), 500)


# ── VAT ───────────────────────────────────────────────────────────

@router.get("/vat/summary", name="api_vat_summary")
async def vat_summary(request: Request, user=Depends(login_required)):
    """Return the current VAT financial summary."""
    try:
        from vat_data_store import vat_store
        company_id = _company(request)
        summary = vat_store.get_vat_summary(company_id=company_id)
        return _ok(summary)
    except Exception as e:
        logger.error("api vat_summary: %s", e)
        return _err(str(e), 500)


# ── Payroll ───────────────────────────────────────────────────────

@router.get("/payroll/employees", name="api_list_employees")
async def list_employees(request: Request, user=Depends(login_required)):
    """List all employees for the current company."""
    try:
        from services.payroll_service import payroll_service
        company_id = _company(request)
        employees = payroll_service.list_employees(company_id)
        return _ok(employees, count=len(employees))
    except Exception as e:
        logger.error("api list_employees: %s", e)
        return _err(str(e), 500)


# ── Inventory ─────────────────────────────────────────────────────

@router.get("/inventory/items", name="api_list_inventory")
async def list_inventory(
    request: Request,
    low_stock: bool = Query(False, description="Return only low-stock items"),
    user=Depends(login_required),
):
    """List inventory items. Use ?low_stock=true for reorder alerts."""
    try:
        from inventory_data_store import inventory_store
        company_id = _company(request)
        raw = inventory_store.get_all_items(company_id=company_id)
        items = raw.to_dict(orient="records") if hasattr(raw, "to_dict") else list(raw or [])
        if low_stock:
            items = [i for i in items if (i.get("quantity") or 0) <= (i.get("reorder_level") or 0)]
        return _ok(items, count=len(items))
    except Exception as e:
        logger.error("api list_inventory: %s", e)
        return _err(str(e), 500)


# ── SIEM / Security (admin only) ──────────────────────────────────

@router.get("/siem/events", name="api_siem_events")
async def siem_events(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    severity: Optional[str] = Query(None),
    user=Depends(admin_required),
):
    """List recent SIEM security events (admin only)."""
    try:
        from siem_data_store import siem_store
        events = siem_store.get_all_events(limit=limit)
        if not isinstance(events, list):
            events = list(events) if events is not None else []
        if severity:
            events = [e for e in events if isinstance(e, dict) and e.get("severity", "").lower() == severity.lower()]
        return _ok(events, count=len(events))
    except Exception as e:
        logger.error("api siem_events: %s", e)
        return _err(str(e), 500)


# ── Dashboard Stats ───────────────────────────────────────────────

@router.get("/dashboard/stats", name="api_dashboard_stats")
async def dashboard_stats(request: Request, user=Depends(login_required)):
    """
    Aggregate stats for the main dashboard.

    Returns counts for accounts, transactions, employees, inventory items.
    Used by mobile apps or external BI tools.
    """
    company_id = _company(request)
    stats = {}

    _sources = [
        ("accounts",     "chart_of_accounts_data_store", "chart_store",       "read_all_accounts"),
        ("transactions", "transaction_data_store",        "transaction_store", "get_transactions"),
    ]
    for key, module_name, store_name, method_name in _sources:
        try:
            import importlib
            mod = importlib.import_module(module_name)
            store = getattr(mod, store_name)
            result = getattr(store, method_name)(company_id=company_id)
            if hasattr(result, "__len__"):
                stats[key] = len(result)
            else:
                stats[key] = 0
        except Exception:
            stats[key] = 0

    return _ok(stats)
