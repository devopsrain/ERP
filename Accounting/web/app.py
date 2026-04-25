"""
FastAPI Web Interface for the Ethiopian Business Management System.
Replaces the previous Flask application.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import sys
import uuid as _uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.gzip import GZipMiddleware
from contextlib import asynccontextmanager
import time as _time

# ── Logging setup ─────────────────────────────────────────────
# LOG_LEVEL env var controls verbosity: DEBUG | INFO | WARNING | ERROR
_LOG_LEVEL = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
try:
    from pythonjsonlogger import jsonlogger as _jlog
    _h = logging.StreamHandler()
    _h.setFormatter(_jlog.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s"
    ))
    logging.root.handlers.clear()
    logging.root.addHandler(_h)
    logging.root.setLevel(_LOG_LEVEL)
except ImportError:
    logging.basicConfig(
        level=_LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
logger = logging.getLogger(__name__)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# AWS secrets (no-op outside AWS)
try:
    from secrets_loader import load_secrets
    load_secrets()
except ImportError:
    pass

from models.account import Account, AccountType, AccountSubType
from models.journal_entry import JournalEntry, JournalEntryBuilder
from core.ledger import GeneralLedger

# ── Lifespan — start/stop background services ─────────────────
@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start the backup scheduler on startup; close async DB pool on shutdown."""
    # Capture unhandled asyncio exceptions into structured logs
    import asyncio as _asyncio
    def _async_exc_handler(loop, context):
        exc = context.get("exception")
        logger.error(
            "asyncio_unhandled: %s",
            context.get("message", "no message"),
            exc_info=exc,
        )
    try:
        _asyncio.get_event_loop().set_exception_handler(_async_exc_handler)
    except Exception:
        pass

    # Initialize Redis cache pool and store it in app state
    try:
        from cache import get_redis_pool
        app.state.redis_pool = get_redis_pool()
    except Exception as _cache_err:
        logger.warning("Redis cache pool not initialized: %s", _cache_err)
        app.state.redis_pool = None

    # Initialize async DB pool eagerly so failures surface at startup
    try:
        from async_db import get_async_pool
        await get_async_pool()
        logger.info("asyncpg pool initialized at startup")
    except Exception as _adb_err:
        logger.warning("asyncpg pool not initialized at startup: %s", _adb_err)

    try:
        from backup_data_store import BackupEngine, BackupScheduler
        _sched = BackupScheduler(BackupEngine(), hour=1)
        _sched.start()
        logger.info("Backup scheduler started (daily 01:00)")
    except Exception as _e:
        logger.warning("Backup scheduler not started: %s", _e)

    # ── Event bus (Architecture #3) ──────────────────────────────────
    # Load handlers so they register themselves on the bus, then start
    # the background Redis listener as a persistent asyncio task.
    _event_task = None
    try:
        import event_handlers  # noqa: F401 — registers @event_bus.on handlers
        from events import event_bus as _bus
        import asyncio as _asyncio_ev
        _event_task = _asyncio_ev.create_task(_bus.listen())
        logger.info("Event bus listener started")
    except Exception as _e:
        logger.warning("Event bus not started: %s", _e)

    # ── Startup DB connectivity probe ────────────────────────────
    # Verify database is reachable BEFORE accepting traffic.
    # Log the result clearly so AWS deployment failures are immediately visible.
    _db_url = os.environ.get("DATABASE_URL", "")
    _db_ok = False
    if _db_url:
        try:
            from db import health_check as _db_hc
            _hc = _db_hc()
            if _hc.get("ok"):
                logger.info(
                    "startup_db_ok",
                    extra={
                        "latency_ms": _hc.get("latency_ms"),
                        "version":    _hc.get("version"),
                        "pool_min":   _hc.get("pool_min"),
                        "pool_max":   _hc.get("pool_max"),
                    },
                )
                _db_ok = True
            else:
                logger.error(
                    "startup_db_FAIL: %s — app will start but DB queries will fail",
                    _hc.get("error", "unknown"),
                )
        except Exception as _db_err:
            logger.error(
                "startup_db_FAIL: %s — app will start but DB queries will fail",
                _db_err,
                exc_info=True,
            )
    else:
        logger.error(
            "startup_db_FAIL: DATABASE_URL is not set — "
            "all database operations will fail"
        )

    # ── DB schema auto-initialisation ────────────────────────────
    # Runs init_db.sql on every startup (idempotent CREATE TABLE IF NOT EXISTS).
    # This ensures a fresh deployment against an empty database works immediately
    # without a manual psql step.
    if _db_ok:
        try:
            from db_setup import ensure_schema
            ensure_schema()
        except Exception as _schema_err:
            logger.warning("DB schema init skipped: %s", _schema_err)

    # ── Redis connectivity probe ─────────────────────────────────
    _redis_ok = False
    try:
        from extensions import cache
        cache.set("_startup_probe", "1", timeout=10)
        _redis_ok = True
        logger.info("startup_redis_ok")
    except Exception as _redis_err:
        logger.warning("startup_redis_unavailable: %s", _redis_err)

    logger.info(
        "app_startup_complete",
        extra={
            "log_level":      os.environ.get("LOG_LEVEL", "INFO").upper(),
            "cdn":            os.environ.get("STATIC_CDN_URL") or "none",
            "redis_url":      (os.environ.get("REDIS_URL") or "not set").split("@")[-1],
            "db_configured":  bool(_db_url),
            "db_connected":   _db_ok,
            "redis_connected": _redis_ok,
            "session_secure": os.environ.get("SESSION_COOKIE_SECURE", "false"),
        },
    )
    yield
    logger.info("app_shutdown")
    # Stop event bus listener
    try:
        from events import event_bus as _bus
        _bus.stop()
        if _event_task is not None:
            _event_task.cancel()
    except Exception:
        pass
    # Close async DB pool
    try:
        from async_db import close_async_pool, close_sqlalchemy_engine
        await close_async_pool()
        await close_sqlalchemy_engine()
    except Exception:
        pass
    # Close sync DB pool
    try:
        from db import close_pool
        close_pool()
    except Exception:
        pass
    # Close Redis cache pool
    try:
        from cache import close_redis_pool
        await close_redis_pool()
    except Exception:
        pass

# ── App Initialization ───────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="Comprehensive ERP System",
        description="""
        Enterprise Resource Planning system covering Accounting, HR, Sales,
        Inventory, and more.
        """,
        version="2.1.0",
        lifespan=_lifespan,
        redoc_url="/docs",
        docs_url=None,  # Disable default Swagger UI
    )

    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
    if not os.environ.get("FLASK_SECRET_KEY"):
        logger.warning(
            "FLASK_SECRET_KEY not set — using ephemeral key "
            "(sessions reset on restart). Set it for production."
        )

    # Static files — serve locally or via CDN (set STATIC_CDN_URL env var for CloudFront)
    _static = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(_static):
        app.mount("/static", StaticFiles(directory=_static), name="static")
    STATIC_CDN_URL = os.environ.get("STATIC_CDN_URL", "").rstrip("/")
    if STATIC_CDN_URL:
        logger.info("Static assets served via CDN: %s", STATIC_CDN_URL)

    # Per-company ledger registry — keyed by company_id.
    # GeneralLedger is in-memory; one instance must exist per tenant so that
    # one company's journal entries and account balances cannot bleed into another.
    # Access via get_company_ledger(company_id) — never use the old singleton directly.
    _ledger_registry: dict = {}
    _ledger_registry_lock = __import__("threading").Lock()


    def get_company_ledger(company_id: str) -> "GeneralLedger":
        """Return the GeneralLedger for *company_id*, creating it on first access."""
        cid = company_id or "default"
        if cid not in _ledger_registry:
            with _ledger_registry_lock:
                if cid not in _ledger_registry:
                    gl = GeneralLedger()
                    gl.create_standard_chart_of_accounts()
                    _ledger_registry[cid] = gl
        return _ledger_registry[cid]

    # Public URL prefixes — bypass auth gate
    _PUBLIC = (
        "/auth/login", "/auth/logout", "/auth/register", "/auth/access-denied",
        "/company/login", "/company/register",
        "/static/", "/provider/", "/sales/", "/health",
    )

    # ── Middleware ────────────────────────────────────────────────

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or _uuid.uuid4().hex[:12]
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


    @app.middleware("http")
    async def request_size_limit_middleware(request: Request, call_next):
        """Protect resources by rejecting oversized requests early."""
        if request.method in ("POST", "PUT", "PATCH"):
            max_mb = int(os.environ.get("MAX_REQUEST_MB", "25"))
            max_bytes = max_mb * 1024 * 1024
            cl = request.headers.get("content-length")
            if cl:
                try:
                    if int(cl) > max_bytes:
                        return JSONResponse(
                            {"error": f"Request too large (max {max_mb}MB)", "status": 413},
                            status_code=413,
                        )
                except ValueError:
                    pass
        return await call_next(request)


    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next):
        """Set secure HTTP headers for response hardening."""
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        # Keep CSP permissive enough for current CDN usage while blocking framing/injection sources.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self' https:; frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
        )
        if os.environ.get("SESSION_COOKIE_SECURE", "0").lower() in ("1", "true", "yes"):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


    @app.middleware("http")
    async def structured_log_middleware(request: Request, call_next):
        """Middleware 4: Emit one structured JSON log line per HTTP request."""
        t0 = _time.perf_counter()
        response = await call_next(request)
        duration_ms = round((_time.perf_counter() - t0) * 1000, 1)
        try:
            user = request.session.get("username", "-")
        except Exception:
            user = "-"
        logger.info(
            "request",
            extra={
                "method":     request.method,
                "path":       request.url.path,
                "status":     response.status_code,
                "ms":         duration_ms,
                "request_id": getattr(request.state, "request_id", "-"),
                "user":       user,
                "company":    str(getattr(request.state, "company_id", "-")),
                "ip":         request.client.host if request.client else "-",
            },
        )
        return response


    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        if path in ("/health", "/favicon.ico", "/") or any(
            path.startswith(p) for p in _PUBLIC
        ) or path in ("/api/v1/health", "/api/docs", "/api/redoc", "/openapi.json"):
            return await call_next(request)

        if not request.session.get("logged_in"):
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                try:
                    from auth_data_store import auth_store as _as
                    token_user = _as.validate_api_token(auth_header[7:].strip())
                    if token_user:
                        request.state.api_user = token_user
                        return await call_next(request)
                except Exception:
                    pass
            is_api = path.startswith("/api/") or "application/json" in request.headers.get(
                "Accept", ""
            )
            if is_api:
                return JSONResponse(
                    {"error": "Authentication required", "status": 401}, status_code=401
                )
            return RedirectResponse("/auth/login", status_code=302)

        return await call_next(request)

    @app.middleware("http")
    async def company_context_middleware(request: Request, call_next):
        if not request.session.get("logged_in") and not getattr(
            request.state, "api_user", None
        ):
            request.state.company_id = None
            request.state.tenant = None
            return await call_next(request)

        from tenant_data_store import tenant_store
        from extensions import cache

        company_id = request.session.get("current_company_id")
        if not company_id:
            try:
                company_id = tenant_store.ensure_default_tenant()
                request.session["current_company_id"] = company_id
            except Exception as _tenant_err:
                logger.warning("company_context: could not resolve tenant: %s", _tenant_err)
                company_id = "default"

        request.state.company_id = company_id

        _ck = f"tenant:{company_id}"
        tenant = cache.get(_ck)
        if tenant is None:
            try:
                tenant = tenant_store.get_tenant(company_id)
            except Exception as _t_err:
                logger.warning("company_context: could not fetch tenant %s: %s", company_id, _t_err)
                tenant = None
            if tenant:
                cache.set(_ck, tenant, timeout=60)

        if tenant is None:
            try:
                tenant_store.create_tenant(
                    {
                        "company_id": company_id,
                        "company_name": request.session.get("company_name", "My Company"),
                        "subscription_tier": "enterprise",
                    },
                    created_by=request.session.get("username", "system"),
                )
                tenant = tenant_store.get_tenant(company_id)
                if tenant:
                    cache.set(_ck, tenant, timeout=60)
            except Exception as _ct_err:
                logger.warning("company_context: could not auto-create tenant %s: %s", company_id, _ct_err)

        request.state.tenant = tenant
        return await call_next(request)

    @app.middleware("http")
    async def module_license_middleware(request: Request, call_next):
        if not request.session.get("logged_in"):
            return await call_next(request)

        path = request.url.path
        _exempt = (
            "/auth/", "/company/login", "/company/register",
            "/company/select", "/static/", "/provider/", "/sales/",
        )
        if any(path.startswith(p) for p in _exempt):
            return await call_next(request)

        MODULE_PATH = {
            "/vat/": "vat",
            "/journal/": "journal_entries",
            "/accounts/": "chart_of_accounts",
            "/income-expense/": "income_expense",
            "/transactions/": "transactions",
            "/inventory/": "inventory",
            "/bid/": "bid_tracker",
            "/cpo/": "cpo",
            "/siem/": "siem",
            "/backup/": "backup",
        }
        module = next(
            (m for p, m in MODULE_PATH.items() if path.startswith(p)), None
        )
        if not module:
            return await call_next(request)

        try:
            from tenant_data_store import tenant_store, ALWAYS_ALLOWED_MODULES
            if module in ALWAYS_ALLOWED_MODULES:
                return await call_next(request)
            company_id = getattr(request.state, "company_id", None)
            if company_id and not tenant_store.is_subscription_active(company_id):
                request.session.setdefault("_flash", []).append(
                    {"message": "Subscription expired.", "category": "danger"}
                )
                return RedirectResponse("/auth/portal", status_code=302)
            if company_id and not tenant_store.is_module_licensed(company_id, module):
                request.session.setdefault("_flash", []).append(
                    {
                        "message": f'Module "{module}" not in your subscription.',
                        "category": "warning",
                    }
                )
                return RedirectResponse("/auth/portal", status_code=302)
        except Exception as _lic_err:
            logger.warning("module_license_middleware: %s (path=%s)", _lic_err, path)

        return await call_next(request)

    # Paths exempt from CSRF header validation (public / static)
    _CSRF_SKIP = (
        "/static/", "/sales/", "/health", "/favicon.ico",
        "/auth/login", "/auth/logout", "/auth/register",
        "/company/login", "/company/register", "/provider/", "/api/",
    )


    @app.middleware("http")
    async def csrf_validate_middleware(request: Request, call_next):
        """
        Middleware 1: Validate X-CSRFToken header on AJAX / HTMX mutating requests.

        Strategy:
        - AJAX/HTMX (HX-Request, XMLHttpRequest, application/json) must carry the
          X-CSRFToken header matching the per-session token.
        - Regular HTML form submissions have no custom header; they are protected
          by SameSite=Lax cookie + the hidden csrf_token field injected by
          csrf_auto_inject middleware.
        - /api/* endpoints use Bearer token auth — exempt from CSRF.
        """
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            path = request.url.path
            is_public = any(path.startswith(p) for p in _CSRF_SKIP)
            if not is_public:
                is_ajax = (
                    request.headers.get("HX-Request") == "true"
                    or request.headers.get("X-Requested-With") == "XMLHttpRequest"
                    or "application/json" in request.headers.get("Content-Type", "")
                    or "application/json" in request.headers.get("Accept", "")
                )
                if is_ajax:
                    try:
                        session_token = request.session.get("_csrf", "")
                        submitted     = request.headers.get("X-CSRFToken", "")
                        if session_token and submitted and submitted != session_token:
                            logger.warning(
                                "CSRF mismatch: user=%s path=%s ip=%s",
                                request.session.get("username", "?"),
                                path,
                                request.client.host if request.client else "-",
                            )
                            return JSONResponse(
                                {"error": "CSRF token invalid", "status": 403},
                                status_code=403,
                            )
                    except Exception:
                        pass
        return await call_next(request)


    @app.middleware("http")
    async def csrf_auto_inject(request: Request, call_next):
        response = await call_next(request)
        ct = response.headers.get("content-type", "")
        if "text/html" not in ct:
            return response
        try:
            from deps import get_csrf_token
            token = get_csrf_token(request)
            hidden = f'<input type="hidden" name="csrf_token" value="{token}">'
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
            html = body.decode("utf-8", errors="replace")
            html = re.sub(
                r"(<form\b[^>]*method=[\"']?post[\"']?[^>]*>)",
                r"\1" + hidden,
                html,
                flags=re.IGNORECASE,
            )
            # Exclude content-length so Starlette recalculates it from the new content
            new_headers = {k: v for k, v in response.headers.items()
                           if k.lower() not in ("content-length",)}
            return Response(
                content=html,
                status_code=response.status_code,
                headers=new_headers,
                media_type="text/html",
            )
        except Exception:
            return response

    # ── SessionMiddleware must be added LAST so it is outermost ──────────────────
    # Starlette inserts each add_middleware call at position 0, making it the new
    # outermost layer. Adding SessionMiddleware last ensures it runs before any of
    # the http-middleware decorators above, so request.session is always populated.
    # Architecture 3: Server-side sessions via Redis (falls back to cookie sessions)
    # Replace SessionMiddleware with RedisSessionMiddleware when Redis is available.
    try:
        from session_store import make_session_middleware as _make_sess
        _https_only = os.environ.get("SESSION_COOKIE_SECURE", "0").lower() in ("1", "true", "yes")
        app.add_middleware(_make_sess(SECRET_KEY, https_only=_https_only))
    except Exception as _sess_err:
        logger.warning("Redis session setup failed (%s) — using cookie sessions", _sess_err)
        app.add_middleware(
            SessionMiddleware,
            secret_key=SECRET_KEY,
            session_cookie="session",
            same_site="lax",
            https_only=os.environ.get("SESSION_COOKIE_SECURE", "0").lower()
            in ("1", "true", "yes"),
        )

    # Middleware 3: GZip compression — added AFTER session middleware so it becomes
    # the outermost layer and compresses responses before they leave the server.
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # ── Audit-trail middleware — logs every data-change with the acting user ──────
    # Runs after SessionMiddleware (session already populated) and logs all
    # successful POST/PUT/PATCH/DELETE requests to the SIEM event table so that
    # the event log shows who made each change.
    _AUDIT_SKIP_PREFIXES = (
        "/static/", "/sales/", "/auth/login", "/auth/logout",
        "/auth/register", "/company/login", "/company/register",
        "/health", "/favicon.ico",
    )

    @app.middleware("http")
    async def audit_trail_middleware(request: Request, call_next):
        response = await call_next(request)
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return response
        path = request.url.path
        if any(path.startswith(p) for p in _AUDIT_SKIP_PREFIXES):
            return response
        # Only log successful mutations (2xx/3xx)
        if response.status_code >= 400:
            return response
        try:
            username = request.session.get("username", "unknown")
            module = path.strip("/").split("/")[0] if path.strip("/") else "unknown"
            from siem_data_store import siem_store
            siem_store.log_upload_event(
                request,
                module=module,
                endpoint=path,
                status="success",
                user=username,
                details=f"{request.method} {path}",
            )
        except Exception:
            pass
        return response

    # ── Sliding-session middleware ────────────────────────────────────────────────
    # Refreshes login_time on every authenticated GET so active users don't get
    # logged out mid-task. The 30-minute window only kicks in when the user is idle.
    _SLIDE_SKIP_PREFIXES = (
        "/static/", "/sales/", "/health", "/favicon.ico",
        "/api/session/", "/metrics",
    )

    @app.middleware("http")
    async def sliding_session_middleware(request: Request, call_next):
        try:
            if (request.method == "GET"
                    and request.session.get("logged_in")
                    and not any(request.url.path.startswith(p) for p in _SLIDE_SKIP_PREFIXES)):
                import time as _t
                last = request.session.get("login_time", 0)
                now = int(_t.time())
                # Only write if at least 60s passed (avoid cookie churn on every request)
                if now - last >= 60:
                    request.session["login_time"] = now
        except Exception:
            pass
        return await call_next(request)

    # ── Middleware 2: Rate-limiter wiring ────────────────────────────────────────────
    # SlowAPI reads app.state.limiter; the middleware enforces @limiter.limit() decorators.
    try:
        from extensions import limiter, LIMITER_AVAILABLE
        if LIMITER_AVAILABLE:
            from slowapi import _rate_limit_exceeded_handler
            from slowapi.errors import RateLimitExceeded
            from slowapi.middleware import SlowAPIMiddleware
            app.state.limiter = limiter
            app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
            app.add_middleware(SlowAPIMiddleware)
            logger.info("slowapi rate-limiting middleware enabled")
    except Exception as _rl_err:
        logger.warning("Rate-limiter not configured: %s", _rl_err)

    # ── Middleware: Prometheus request counter ───────────────────────────────────────
    # This must be added after GZip/Session so it is the outermost HTTP layer and
    # counts every request including those rejected by auth/rate-limiter.
    try:
        from starlette.middleware.base import BaseHTTPMiddleware
        from metrics import record_request as _record_request
        import time as _metrics_time

        class _PrometheusMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next: Callable) -> Response:
                if request.url.path == "/metrics":
                    return await call_next(request)
                t0 = _metrics_time.perf_counter()
                try:
                    response = await call_next(request)
                finally:
                    elapsed_ms = (_metrics_time.perf_counter() - t0) * 1000
                    _record_request(
                        request.method,
                        request.url.path,
                        getattr(response, "status_code", 500),
                        elapsed_ms,
                    )
                return response

        app.add_middleware(_PrometheusMiddleware)
        logger.info("Prometheus metrics middleware enabled")
    except Exception as _prom_err:
        logger.warning("Prometheus middleware not enabled: %s", _prom_err)

    # ── Exception handlers ────────────────────────────────────────
    from template_engine import templates

    @app.exception_handler(404)
    async def not_found(request: Request, exc: HTTPException):
        from deps import template_context
        ctx = template_context(request)
        ctx["detail"] = getattr(exc, "detail", "Page not found")
        return templates.TemplateResponse(
            "errors/404.html", ctx, status_code=404
        )

    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException):
        if exc.status_code in (301, 302, 303, 307, 308):
            return RedirectResponse(
                exc.headers.get("Location", "/"), status_code=exc.status_code
            )
        # Log server-side errors with request context for diagnosis
        if exc.status_code >= 500:
            logger.error(
                "http_5xx: %s %s -> %d  %s",
                request.method, request.url.path, exc.status_code, exc.detail,
                extra={"request_id": getattr(request.state, "request_id", "-")},
            )
        if "application/json" in request.headers.get("Accept", ""):
            return JSONResponse(
                {"error": exc.detail, "status": exc.status_code},
                status_code=exc.status_code,
            )
        from deps import template_context
        ctx = template_context(request)
        ctx["detail"] = getattr(exc, "detail", "An error occurred")
        return templates.TemplateResponse(
            "errors/404.html", ctx, status_code=exc.status_code
        )


    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        """Catch-all — log every unhandled exception with full traceback."""
        rid = getattr(request.state, "request_id", "-")
        logger.error(
            "unhandled_exception",
            exc_info=exc,
            extra={
                "request_id": rid,
                "method":     request.method,
                "path":       request.url.path,
                "ip":         request.client.host if request.client else "-",
            },
        )
        if "application/json" in request.headers.get("Accept", ""):
            return JSONResponse(
                {"error": "Internal server error", "request_id": rid, "status": 500},
                status_code=500,
            )
        try:
            from deps import template_context
            ctx = template_context(request)
            ctx["detail"] = "An unexpected error occurred. The error has been logged."
            ctx["request_id"] = rid
            return templates.TemplateResponse("errors/500.html", ctx, status_code=500)
        except Exception:
            return JSONResponse(
                {"error": "Internal server error", "request_id": rid}, status_code=500
            )

    # ── Health & root ─────────────────────────────────────────────
    @app.get("/health", name="health_check")
    async def health_check():
        """
        Liveness + readiness probe for ALB / Docker healthcheck / smoke tests.

        Returns 200 with DB status when everything is reachable.
        Returns 503 when the database is unreachable so ALB stops routing traffic.
        """
        checks: dict = {"app": "ok"}
        status_code = 200

        # DB check — critical for readiness
        try:
            from db import health_check as _db_health
            db = _db_health()
            if db.get("ok"):
                checks["database"] = "ok"
                checks["db_latency_ms"] = db.get("latency_ms")
                checks["db_version"] = db.get("version")
            else:
                checks["database"] = f"error: {db.get('error', 'unknown')}"
                status_code = 503
        except Exception as e:
            checks["database"] = f"error: {e}"
            status_code = 503

        # Cache check — non-critical (degraded, not down)
        try:
            from extensions import cache
            cache.set("_health", "1", timeout=5)
            checks["cache"] = "ok"
        except Exception:
            checks["cache"] = "unavailable"

        overall = "healthy" if status_code == 200 else "degraded"
        return JSONResponse(
            {
                "status": overall,
                "service": "Ethiopian Business Management System",
                "checks": checks,
            },
            status_code=status_code,
        )

    @app.get("/", name="index")
    async def index(request: Request):
        if request.session.get("logged_in"):
            return RedirectResponse("/auth/portal", status_code=302)
        return RedirectResponse("/sales/", status_code=302)

    # ── Module registration ───────────────────────────────────────

    def _reg(module: str, label: str):
        try:
            mod = __import__(module, fromlist=["router"])
            app.include_router(mod.router)
            logger.info("%s integrated", label)
        except ImportError as e:
            logger.warning("%s not available: %s", label, e)
        except AttributeError:
            logger.warning("%s: no 'router' export (skip)", label)
        except Exception as e:
            logger.error("Error initializing %s: %s", label, e, exc_info=True)

    _reg("auth_routes",              "Authentication system")
    _reg("provider_admin_routes",    "Provider admin dashboard")
    _reg("sales_routes",             "Sales marketing site")
    _reg("multicompany_routes",      "Multi-company portal")
    _reg("vat_routes",               "VAT portal")
    _reg("journal_entry_routes",     "Journal entry system")
    _reg("chart_of_accounts_routes", "Chart of accounts")
    _reg("income_expense_routes",    "Income & Expense system")
    _reg("transaction_routes",       "Transaction system")
    _reg("cpo_routes",               "CPO system")
    _reg("inventory_routes",         "Inventory system")
    _reg("bid_routes",               "Bid Tracker system")
    _reg("siem_routes",              "SIEM system")
    _reg("backup_routes",            "Backup & Archive system")
    _reg("version_routes",           "Version control system")
    _reg("api_routes",               "REST API v1")
    _reg("letter_routes",            "Letters & E-Signatures")
    _reg("lms_routes",               "Learning Management System")
    _reg("machinery_routes",         "Machinery & Equipment")
    _reg("hrm_routes",               "Human Resource Management")
    _reg("finance_management_routes", "Finance Management")
    _reg("communication_routes",      "Communication Platform")
    _reg("project_routes",            "Project Management")
    _reg("procurement_routes",        "Procurement")
    _reg("ems_routes",                "Event Management System")
    _reg("forecast_routes",           "Forecasting & Predictive Analytics")
    _reg("seamless_routes",           "Seamless UX (search/notifications/health)")

    try:
        from api_v2_routes import router as _api_v2_router
        app.include_router(_api_v2_router)
        logger.info("REST API v2 (mobile) mounted")
    except Exception as _e:
        logger.warning("REST API v2 not mounted: %s", _e)

    try:
        from payroll_routes import router as _payroll_router, set_ledger as _payroll_set_ledger
        _payroll_set_ledger(get_company_ledger)
        app.include_router(_payroll_router)
        logger.info("Ethiopian payroll system integrated")
    except ImportError as e:
        logger.warning("Payroll system not available: %s", e)
    except Exception as e:
        logger.error("Error initializing payroll: %s", e, exc_info=True)

    # ── Prometheus metrics ──────────────────────────────────────────
    try:
        from metrics import metrics_router as _metrics_router
        app.include_router(_metrics_router)
        logger.info("Prometheus /metrics endpoint enabled")
    except Exception as _m_err:
        logger.warning("Metrics not enabled: %s", _m_err)

    return app


# Module-level singleton — exposes `app` for `uvicorn run_production:app`
# and for `from app import app` in run_production.py / Supervisor config.
app = create_app()
