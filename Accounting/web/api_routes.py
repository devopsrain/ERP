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
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

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
        from async_db import get_async_conn
        async with get_async_conn() as conn:
            await conn.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        logger.warning("API health check DB failed: %s", e)
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
    limit: int = Query(200, ge=1, le=1000, description="Max records to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    user=Depends(login_required),
):
    """
    List all accounts in the current company's chart of accounts.

    Query params:
        account_type: Filter by type (ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE)
        limit:        Max records (default 200, max 1000)
        offset:       Pagination offset
    """
    try:
        from chart_of_accounts_data_store import chart_store
        company_id = _company(request)
        df = chart_store.read_all_accounts(company_id=company_id)
        accounts = df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
        if account_type:
            accounts = [a for a in accounts if str(a.get("account_type", "")).upper() == account_type.upper()]
        total = len(accounts)
        return _ok(accounts[offset:offset + limit], total=total, limit=limit, offset=offset)
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
async def list_employees(
    request: Request,
    limit: int = Query(100, ge=1, le=500, description="Max records to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    user=Depends(login_required),
):
    """List employees for the current company."""
    try:
        from services.payroll_service import payroll_service
        company_id = _company(request)
        employees = payroll_service.list_employees(company_id)
        total = len(employees)
        return _ok(employees[offset:offset + limit], total=total, limit=limit, offset=offset)
    except Exception as e:
        logger.error("api list_employees: %s", e)
        return _err(str(e), 500)


# ── Inventory ─────────────────────────────────────────────────────

@router.get("/inventory/items", name="api_list_inventory")
async def list_inventory(
    request: Request,
    limit: int = Query(100, ge=1, le=500, description="Max records to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
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
        total = len(items)
        return _ok(items[offset:offset + limit], total=total, limit=limit, offset=offset)
    except Exception as e:
        logger.error("api list_inventory: %s", e)
        return _err(str(e), 500)


# ── SIEM / Security (admin only) ──────────────────────────────────

@router.get("/siem/events", name="api_siem_events")
async def siem_events(
    request: Request,
    limit: int = Query(100, ge=1, le=1000, description="Max records to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    severity: Optional[str] = Query(None, description="Filter by severity (LOW/MEDIUM/HIGH/CRITICAL)"),
    user=Depends(admin_required),
):
    """List recent SIEM security events (admin only)."""
    try:
        from siem_data_store import siem_store
        # Fetch enough to support offset — siem_store.get_all_events applies DB-level limit
        fetch_limit = limit + offset
        events = siem_store.get_all_events(limit=min(fetch_limit, 1000))
        if not isinstance(events, list):
            events = list(events) if events is not None else []
        if severity:
            events = [e for e in events if isinstance(e, dict) and e.get("severity", "").lower() == severity.lower()]
        total = len(events)
        return _ok(events[offset:offset + limit], total=total, limit=limit, offset=offset)
    except Exception as e:
        logger.error("api siem_events: %s", e)
        return _err(str(e), 500)


# ── Dashboard Stats ───────────────────────────────────────────────

async def _build_dashboard_stats(request: Request) -> dict:
    """Shared logic: build stats dict for the current company. Cached 60s."""
    from extensions import cache
    company_id = _company(request)
    cache_key  = f"dashboard_stats:{company_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    stats: dict = {}
    import importlib
    _sources = [
        ("accounts",     "chart_of_accounts_data_store", "chart_store",    "read_all_accounts"),
        ("transactions", "transaction_data_store",        "transaction_store", "get_transactions"),
        ("employees",    "employee_data_store",           "employee_store", "read_all_employees"),
        ("inventory",    "inventory_data_store",          "inventory_store","get_all_items"),
    ]
    for key, module_name, store_name, method_name in _sources:
        try:
            mod    = importlib.import_module(module_name)
            store  = getattr(mod, store_name)
            result = getattr(store, method_name)(company_id=company_id)
            stats[key] = len(result) if hasattr(result, "__len__") else 0
        except Exception:
            stats[key] = 0

    try:
        from inventory_data_store import inventory_store
        items = inventory_store.get_all_items(company_id=company_id)
        items = items.to_dict(orient="records") if hasattr(items, "to_dict") else list(items or [])
        stats["low_stock"] = sum(
            1 for i in items if (i.get("quantity") or 0) <= (i.get("reorder_level") or 0)
        )
    except Exception:
        stats["low_stock"] = 0

    try:
        from siem_data_store import siem_store
        events = siem_store.get_all_events(limit=200) or []
        stats["siem_alerts"] = sum(
            1 for e in events
            if isinstance(e, dict) and e.get("severity", "").upper() in ("HIGH", "CRITICAL")
        )
    except Exception:
        stats["siem_alerts"] = 0

    cache.set(cache_key, stats, timeout=60)
    return stats


@router.get("/dashboard/stats", name="api_dashboard_stats")
async def dashboard_stats(request: Request, user=Depends(login_required)):
    """
    Aggregate stats for the main dashboard (cached 60s per company).

    Includes: accounts, transactions, employees, inventory, low_stock, siem_alerts.
    Used by mobile apps, external BI tools, and the portal HTMX poller.
    """
    stats = await _build_dashboard_stats(request)
    return _ok(stats)


@router.get("/dashboard/stats/partial", name="api_dashboard_stats_partial", include_in_schema=False)
async def dashboard_stats_partial(request: Request, user=Depends(login_required)):
    """HTMX partial: returns the KPI quick-stats row as HTML. Polled every 60s by the portal."""
    from fastapi.responses import HTMLResponse
    stats = await _build_dashboard_stats(request)

    kpis = [
        ("bi-people-fill",          "text-primary", "#e3f2fd", "#bbdefb", stats.get("employees",    0), "Employees"),
        ("bi-arrow-left-right",     "text-success", "#e8f5e9", "#c8e6c9", stats.get("transactions", 0), "Transactions"),
        ("bi-box-seam",             "text-warning", "#fff3e0", "#ffe0b2", stats.get("inventory",    0), "Inventory Items"),
        ("bi-journal-text",         "text-info",    "#e0f7fa", "#b2ebf2", stats.get("accounts",     0), "Accounts"),
        ("bi-exclamation-triangle", "text-danger",  "#fce4ec", "#f8bbd0", stats.get("low_stock",    0), "Low Stock"),
        ("bi-shield-exclamation",   "text-danger",  "#ffebee", "#ffcdd2", stats.get("siem_alerts",  0), "SIEM Alerts"),
    ]
    cols = "".join(
        f'<div class="col-6 col-md-2">'
        f'<div class="quick-stat" style="background:linear-gradient(135deg,{g1},{g2})">'
        f'<div class="qs-icon {color}"><i class="bi {icon}"></i></div>'
        f'<div class="qs-value {color}">{val}</div>'
        f'<div class="qs-label">{label}</div>'
        f'</div></div>'
        for icon, color, g1, g2, val, label in kpis
    )
    return HTMLResponse(f'<div class="row g-3 mb-4">{cols}</div>')


# ── Data Export ───────────────────────────────────────────────────

_EXPORT_MODULES = {
    "employees":    ("employee_data_store", "employee_store", "read_all_employees"),
    "cpo":          ("cpo_data_store",      None,             None),   # handled below
    "transactions": ("transaction_data_store", "transaction_store", "get_transactions"),
    "vat_income":   ("vat_data_store",      "vat_store",      "get_all_income"),
    "vat_expenses": ("vat_data_store",      "vat_store",      "get_all_expenses"),
    "inventory":    ("inventory_data_store","inventory_store","get_all_items"),
}


@router.get("/export/{module}", name="api_export_module")
async def export_module(
    module: str,
    request: Request,
    fmt: str = Query("csv", alias="format", description="Output format: csv or xlsx"),
    user=Depends(login_required),
):
    """
    Export module data as a downloadable file.

    Supported modules: employees, cpo, transactions, vat_income, vat_expenses, inventory

    Query params:
        format: 'csv' (default) or 'xlsx'

    Returns:
        File download with Content-Disposition: attachment header.

    Example:
        GET /api/v1/export/employees?format=csv
        GET /api/v1/export/cpo?format=xlsx
    """
    if module not in _EXPORT_MODULES:
        return _err(
            f"Unknown module '{module}'. Choose from: {', '.join(_EXPORT_MODULES)}",
            404
        )
    if fmt not in ("csv", "xlsx"):
        return _err("format must be 'csv' or 'xlsx'", 400)

    company_id = _company(request)
    try:
        import importlib
        import io
        import pandas as pd
        from fastapi.responses import StreamingResponse

        # CPO uses its own data store instance
        if module == "cpo":
            from cpo_data_store import CPODataStore
            records = CPODataStore(data_dir="data").get_all_cpos(company_id)
            df = pd.DataFrame(records) if records else pd.DataFrame()
        else:
            mod_name, store_attr, method_name = _EXPORT_MODULES[module]
            mod = importlib.import_module(mod_name)
            store = getattr(mod, store_attr)
            raw = getattr(store, method_name)(company_id=company_id)
            if hasattr(raw, "to_dict"):
                df = raw
            elif isinstance(raw, list):
                df = pd.DataFrame(raw)
            else:
                df = pd.DataFrame()

        if df.empty:
            return _err(f"No data found for module '{module}'", 404)

        # Drop internal/sensitive fields
        sensitive = {"password", "password_hash", "hashed_password", "secret"}
        df = df[[c for c in df.columns if c.lower() not in sensitive]]

        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"{module}_export_{ts}.{fmt}"

        if fmt == "csv":
            buf = io.StringIO()
            df.to_csv(buf, index=False)
            buf.seek(0)
            return StreamingResponse(
                iter([buf.getvalue()]),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
        else:  # xlsx
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name=module[:31], index=False)
            buf.seek(0)
            return StreamingResponse(
                iter([buf.getvalue()]),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
    except Exception as e:
        logger.error("api_export_module %s: %s", module, e)
        return _err(str(e), 500)

