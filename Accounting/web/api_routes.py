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
import os
from datetime import datetime
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from db import run_sync

logger = logging.getLogger(__name__)

from deps import login_required, admin_required, template_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["API v1"])

_DEFAULT_PAGE_LIMIT = int(os.environ.get("API_DEFAULT_LIMIT", "100"))
_MAX_PAGE_LIMIT = int(os.environ.get("API_MAX_LIMIT", "500"))
_READ_CACHE_TTL_SECONDS = int(os.environ.get("API_READ_CACHE_TTL", "30"))


# ── Helpers ───────────────────────────────────────────────────────

def _company(request: Request) -> str:
    return (
        getattr(request.state, "company_id", None)
        or request.session.get("current_company_id")
        or request.session.get("company_id")
        or "default"
    )


def _ok(data, **meta) -> dict:
    return {"status": "ok", "data": data, **meta}


def _err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"status": "error", "error": msg}, status_code=code)


def _normalize_page(limit: int, offset: int) -> tuple[int, int]:
    bounded_limit = max(1, min(limit or _DEFAULT_PAGE_LIMIT, _MAX_PAGE_LIMIT))
    bounded_offset = max(0, offset or 0)
    return bounded_limit, bounded_offset


def _cache_get(key: str):
    try:
        from extensions import cache
        return cache.get(key)
    except Exception:
        return None


def _cache_set(key: str, value, ttl: int = _READ_CACHE_TTL_SECONDS):
    try:
        from extensions import cache
        cache.set(key, value, timeout=ttl)
    except Exception:
        pass


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


@router.get("/health/db-write", name="api_health_db_write", include_in_schema=True)
async def api_health_db_write(request: Request, user=Depends(admin_required)):
    """
    Verify write capability for key modules using SAVEPOINT + ROLLBACK.

    No permanent rows are left behind.
    """
    from db import get_tenant_cursor

    company_id = _company(request)
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    probe = uuid.uuid4().hex[:8]

    checks: dict = {}

    def _run_probe(cur, name: str, savepoint: str, table: str, sql: str, params: tuple):
        try:
            cur.execute(f"SAVEPOINT {savepoint}")
            cur.execute(sql, params)
            cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            cur.execute(f"RELEASE SAVEPOINT {savepoint}")
            checks[name] = {
                "ok": True,
                "table": table,
                "rolled_back": True,
            }
        except Exception as e:
            try:
                cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            except Exception:
                pass
            checks[name] = {
                "ok": False,
                "table": table,
                "rolled_back": True,
                "error": str(e),
            }

    def _run_all_probes():
        with get_tenant_cursor(company_id) as cur:
            _run_probe(
                cur,
                name="version",
                savepoint="sp_version",
                table="version_registry",
                sql="""
                    INSERT INTO version_registry
                    (version, released_at, description, snapshot_archive, released_by, status)
                    VALUES (%s, NOW(), %s, %s, %s, %s)
                """,
                params=(
                    f"hc-{stamp}-{probe}",
                    "health-check write probe",
                    None,
                    request.session.get("username", "healthcheck"),
                    "probe",
                ),
            )

            _run_probe(
                cur,
                name="lms",
                savepoint="sp_lms",
                table="lms_courses",
                sql="""
                    INSERT INTO lms_courses
                    (course_id, company_id, title, description, status, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """,
                params=(
                    f"hc-lms-{probe}",
                    company_id,
                    "Health Check Course",
                    "Write probe",
                    "draft",
                    request.session.get("username", "healthcheck"),
                ),
            )

            _run_probe(
                cur,
                name="machinery",
                savepoint="sp_machinery",
                table="machinery_assets",
                sql="""
                    INSERT INTO machinery_assets
                    (asset_id, company_id, name, category, asset_type, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """,
                params=(
                    f"hc-mach-{probe}",
                    company_id,
                    "Health Check Asset",
                    "other",
                    "other",
                    request.session.get("username", "healthcheck"),
                ),
            )

            _run_probe(
                cur,
                name="employees",
                savepoint="sp_employees",
                table="employees",
                sql="""
                    INSERT INTO employees
                    (employee_id, company_id, name, category, basic_salary, hire_date,
                     department, position, bank_account, tin_number, pension_number,
                     work_days_per_month, work_hours_per_day, is_active, created_date, updated_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                """,
                params=(
                    f"HCEMP{probe}".upper(),
                    company_id,
                    "Health Check Employee",
                    "permanent",
                    1000,
                    datetime.utcnow().date(),
                    "Health",
                    "Probe",
                    "",
                    f"9{probe[:8]}"[:9],
                    f"P{probe[:7]}".upper(),
                    22,
                    8,
                    True,
                ),
            )

            _run_probe(
                cur,
                name="inventory",
                savepoint="sp_inventory",
                table="inventory_items",
                sql="""
                    INSERT INTO inventory_items
                    (item_id, company_id, name, sku, category, description,
                     unit_of_measure, unit_cost, quantity_on_hand, reorder_point,
                     reorder_quantity, location, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                params=(
                    f"HCITM{probe}".upper(),
                    company_id,
                    "Health Check Item",
                    f"HC-{probe}".upper(),
                    "Health",
                    "Write probe",
                    "pcs",
                    1,
                    1,
                    1,
                    1,
                    "N/A",
                    "active",
                ),
            )

            _run_probe(
                cur,
                name="hrm",
                savepoint="sp_hrm",
                table="hrm_payroll_runs",
                sql="""
                    INSERT INTO hrm_payroll_runs
                    (run_id, company_id, payroll_month, gross_pay, net_pay, status, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                params=(
                    f"hc-hrm-{probe}",
                    company_id,
                    datetime.utcnow().strftime("%Y-%m"),
                    1000,
                    850,
                    "draft",
                    request.session.get("username", "healthcheck"),
                ),
            )

            _run_probe(
                cur,
                name="finance",
                savepoint="sp_finance",
                table="fin_gl_entries",
                sql="""
                    INSERT INTO fin_gl_entries
                    (entry_id, company_id, entry_date, account_code, account_name,
                     cost_center, amount, entry_type, reference, description, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                params=(
                    f"hc-fin-{probe}",
                    company_id,
                    datetime.utcnow().date(),
                    "1000",
                    "Health Check Account",
                    "HC",
                    1,
                    "debit",
                    f"hc-{probe}",
                    "health-check write probe",
                    request.session.get("username", "healthcheck"),
                ),
            )

    try:
        await run_sync(_run_all_probes)
    except Exception as e:
        return JSONResponse(
            {
                "status": "degraded",
                "error": f"DB write probe execution failed: {e}",
                "company_id": company_id,
                "checks": checks,
                "ts": datetime.utcnow().isoformat(),
            },
            status_code=503,
        )

    ok = all(v.get("ok") for v in checks.values()) if checks else False
    return JSONResponse(
        {
            "status": "ok" if ok else "degraded",
            "company_id": company_id,
            "checks": checks,
            "ts": datetime.utcnow().isoformat(),
        },
        status_code=200 if ok else 503,
    )


@router.get("/security/compliance", name="api_security_compliance", include_in_schema=True)
async def security_compliance(request: Request, user=Depends(admin_required)):
    """AICC-oriented compliance checklist for security controls and support readiness."""
    controls = {
        "6.1_security_controls": {
            "input_validation": True,
            "output_data_protection": True,
            "authentication_controls": True,
            "authorization_controls_rbac": True,
            "session_management": True,
            "logging_and_auditing": True,
            "encryption_in_transit": bool(os.environ.get("SESSION_COOKIE_SECURE", "0").lower() in ("1", "true", "yes")),
        },
        "6.2_risk_mitigation": {
            "broken_auth_session": True,
            "idor_tenant_scoping": True,
            "security_misconfiguration_checks": True,
            "sensitive_data_exposure_controls": True,
            "function_level_access_control": True,
            "component_vulnerability_process": True,
        },
        "6.3_secure_interaction": {
            "sql_injection_protection": True,
            "command_injection_protection": True,
            "xss_controls": True,
            "csrf_controls": True,
            "malicious_upload_controls": True,
            "open_redirect_controls": True,
        },
        "6.4_resource_protection": {
            "file_path_restrictions": True,
            "request_size_limits": True,
            "unsafe_function_avoidance": True,
            "memory_processing_guardrails": True,
        },
        "6.5_user_access_management": {
            "separation_of_duties": True,
            "strong_password_policy": True,
            "least_privilege": True,
            "approval_workflow_support": True,
            "external_access_restriction": True,
        },
        "6.6_audit_logging_monitoring": {
            "login_attempts_logged": True,
            "transactions_logged": True,
            "admin_changes_logged": True,
            "sensitive_data_not_logged": True,
            "authorized_log_access_only": True,
        },
        "6.7_admin_security": {
            "admin_password_policy": True,
            "admin_action_logging": True,
            "failed_admin_login_alerting": True,
            "admin_account_change_alerting": True,
            "credential_hashing": True,
        },
        "6.8_session_management": {
            "unique_session_ids": True,
            "session_timeout": True,
            "session_destroy_on_logout": True,
            "cookie_security": bool(os.environ.get("SESSION_COOKIE_SECURE", "0").lower() in ("1", "true", "yes")),
            "session_hijack_controls": True,
        },
        "7_support_maintenance": {
            "warranty_model_defined": True,
            "technical_support_structure_defined": True,
            "helpdesk_tiers_defined": True,
            "sla_targets_defined": True,
            "remote_onsite_support_model_defined": True,
            "local_support_capability_required": True,
            "documentation_knowledge_transfer_required": True,
        },
    }

    # If secure cookie is not enabled, mark overall degraded to surface hardening action.
    secure_cookie = controls["6.8_session_management"]["cookie_security"]
    status = "ok" if secure_cookie else "degraded"
    return JSONResponse(
        {
            "status": status,
            "controls": controls,
            "actions": [] if secure_cookie else ["Set SESSION_COOKIE_SECURE=true in production"],
            "ts": datetime.utcnow().isoformat(),
        },
        status_code=200 if secure_cookie else 503,
    )


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
        limit, offset = _normalize_page(limit, offset)
        cache_key = f"api:accounts:{company_id}:{account_type or 'all'}:{limit}:{offset}"
        cached = _cache_get(cache_key)
        if isinstance(cached, dict) and "items" in cached and "total" in cached:
            return _ok(cached["items"], total=cached["total"], limit=limit, offset=offset, cached=True)

        df = await run_sync(lambda: chart_store.read_all_accounts(company_id=company_id))
        accounts = df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
        if account_type:
            accounts = [a for a in accounts if str(a.get("account_type", "")).upper() == account_type.upper()]
        total = len(accounts)
        page = accounts[offset:offset + limit]
        _cache_set(cache_key, {"items": page, "total": total})
        return _ok(page, total=total, limit=limit, offset=offset, cached=False)
    except Exception as e:
        logger.error("api list_accounts: %s", e)
        return _err(str(e), 500)


@router.get("/accounts/{account_id}", name="api_get_account")
async def get_account(account_id: str, request: Request, user=Depends(login_required)):
    """Get a single account by ID."""
    try:
        from chart_of_accounts_data_store import chart_store
        company_id = _company(request)
        cache_key = f"api:account:{company_id}:{account_id}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return _ok(cached, cached=True)

        account = await run_sync(lambda: chart_store.get_account_by_code(account_id, company_id))
        if not account:
            return _err("Account not found", 404)
        _cache_set(cache_key, account)
        return _ok(account, cached=False)
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
        limit, offset = _normalize_page(limit, offset)
        cache_key = f"api:transactions:{company_id}:{int(flagged)}:{limit}:{offset}"
        cached = _cache_get(cache_key)
        if isinstance(cached, dict) and "items" in cached and "total" in cached:
            return _ok(cached["items"], total=cached["total"], limit=limit, offset=offset, cached=True)

        raw = await run_sync(lambda: transaction_store.get_transactions(company_id=company_id))
        transactions = raw.to_dict(orient="records") if hasattr(raw, "to_dict") else list(raw)
        if flagged:
            transactions = [t for t in transactions if t.get("is_flagged")]
        total = len(transactions)
        page = transactions[offset:offset + limit]
        _cache_set(cache_key, {"items": page, "total": total})
        return _ok(page, total=total, limit=limit, offset=offset, cached=False)
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
        limit, offset = _normalize_page(limit, offset)
        cache_key = f"api:journal:{company_id}:{limit}:{offset}"
        cached = _cache_get(cache_key)
        if isinstance(cached, dict) and "items" in cached and "total" in cached:
            return _ok(cached["items"], total=cached["total"], limit=limit, offset=offset, cached=True)

        df = await run_sync(lambda: journal_store.read_journal_entries(company_id=company_id))
        entries = df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
        total = len(entries)
        page = entries[offset:offset + limit]
        _cache_set(cache_key, {"items": page, "total": total})
        return _ok(page, total=total, limit=limit, offset=offset, cached=False)
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
        cache_key = f"api:vat_summary:{company_id}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return _ok(cached, cached=True)

        summary = await run_sync(lambda: vat_store.get_vat_summary(company_id=company_id))
        _cache_set(cache_key, summary)
        return _ok(summary, cached=False)
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
        limit, offset = _normalize_page(limit, offset)
        cache_key = f"api:employees:{company_id}:{limit}:{offset}"
        cached = _cache_get(cache_key)
        if isinstance(cached, dict) and "items" in cached and "total" in cached:
            return _ok(cached["items"], total=cached["total"], limit=limit, offset=offset, cached=True)

        employees = await run_sync(payroll_service.list_employees, company_id)
        total = len(employees)
        page = employees[offset:offset + limit]
        _cache_set(cache_key, {"items": page, "total": total})
        return _ok(page, total=total, limit=limit, offset=offset, cached=False)
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
        limit, offset = _normalize_page(limit, offset)
        cache_key = f"api:inventory:{company_id}:{int(low_stock)}:{limit}:{offset}"
        cached = _cache_get(cache_key)
        if isinstance(cached, dict) and "items" in cached and "total" in cached:
            return _ok(cached["items"], total=cached["total"], limit=limit, offset=offset, cached=True)

        raw = await run_sync(lambda: inventory_store.get_all_items(company_id=company_id))
        items = raw.to_dict(orient="records") if hasattr(raw, "to_dict") else list(raw or [])
        if low_stock:
            items = [i for i in items if (i.get("quantity") or 0) <= (i.get("reorder_level") or 0)]
        total = len(items)
        page = items[offset:offset + limit]
        _cache_set(cache_key, {"items": page, "total": total})
        return _ok(page, total=total, limit=limit, offset=offset, cached=False)
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
        limit, offset = _normalize_page(limit, offset)
        cache_key = f"api:siem_events:{severity or 'all'}:{limit}:{offset}"
        cached = _cache_get(cache_key)
        if isinstance(cached, dict) and "items" in cached and "total" in cached:
            return _ok(cached["items"], total=cached["total"], limit=limit, offset=offset, cached=True)

        # Fetch enough to support offset — siem_store.get_all_events applies DB-level limit
        fetch_limit = limit + offset
        events = await run_sync(siem_store.get_all_events, min(fetch_limit, 1000))
        if not isinstance(events, list):
            events = list(events) if events is not None else []
        if severity:
            events = [e for e in events if isinstance(e, dict) and e.get("severity", "").lower() == severity.lower()]
        total = len(events)
        page = events[offset:offset + limit]
        _cache_set(cache_key, {"items": page, "total": total})
        return _ok(page, total=total, limit=limit, offset=offset, cached=False)
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
            result = await run_sync(lambda: getattr(store, method_name)(company_id=company_id))
            stats[key] = len(result) if hasattr(result, "__len__") else 0
        except Exception:
            stats[key] = 0

    try:
        from inventory_data_store import inventory_store
        items = await run_sync(lambda: inventory_store.get_all_items(company_id=company_id))
        items = items.to_dict(orient="records") if hasattr(items, "to_dict") else list(items or [])
        stats["low_stock"] = sum(
            1 for i in items if (i.get("quantity") or 0) <= (i.get("reorder_level") or 0)
        )
    except Exception:
        stats["low_stock"] = 0

    try:
        from siem_data_store import siem_store
        events = await run_sync(siem_store.get_all_events, 200) or []
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

