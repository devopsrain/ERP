"""
Migration script: writes all new FastAPI infrastructure files to web/.
Run once from the repo root:  python write_fastapi_files.py
"""
import os
import textwrap

WEB = os.path.join(os.path.dirname(__file__), "web")


def write(rel, content):
    path = os.path.join(WEB, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content).lstrip("\n"))
    print(f"  wrote {path}")


# ──────────────────────────────────────────────────────────────────
# extensions.py  (framework-agnostic, removes Flask dependencies)
# ──────────────────────────────────────────────────────────────────
write("extensions.py", """
    \"\"\"
    Shared extensions -- framework-agnostic stubs.
    No Flask or FastAPI dependencies here so both legacy and new code can import freely.
    \"\"\"
    import logging as _logging
    import threading as _threading
    import time as _time

    # ── Rate-limiter ---------------------------------------------------
    class _NoLimiter:
        def limit(self, *args, **kwargs):
            def decorator(f):
                return f
            return decorator
        def init_app(self, app):
            pass

    try:
        from slowapi import Limiter
        from slowapi.util import get_remote_address
        limiter = Limiter(key_func=get_remote_address)
        LIMITER_AVAILABLE = True
    except ImportError:
        _logging.getLogger(__name__).warning(
            "slowapi not installed; rate-limiting disabled.  pip install slowapi"
        )
        limiter = _NoLimiter()
        LIMITER_AVAILABLE = False

    # ── In-memory TTL cache -------------------------------------------
    class _MemCache:
        def __init__(self):
            self._store: dict = {}
            self._lock = _threading.Lock()

        def get(self, key):
            with self._lock:
                entry = self._store.get(key)
                if entry is None:
                    return None
                value, expires = entry
                if expires is not None and _time.time() > expires:
                    del self._store[key]
                    return None
                return value

        def set(self, key, value, timeout=300):
            expires = _time.time() + timeout if timeout else None
            with self._lock:
                self._store[key] = (value, expires)

        def delete(self, key):
            with self._lock:
                self._store.pop(key, None)

        def cached(self, *args, **kwargs):
            def decorator(f):
                return f
            return decorator

        def init_app(self, app, config=None):
            pass

    cache = _MemCache()
    CACHE_AVAILABLE = True
""")

# ──────────────────────────────────────────────────────────────────
# app.py  (FastAPI application — replaces Flask app)
# ──────────────────────────────────────────────────────────────────
write("app.py", r'''
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

    # ── Logging setup ─────────────────────────────────────────────
    try:
        from pythonjsonlogger import jsonlogger as _jlog
        _h = logging.StreamHandler()
        _h.setFormatter(_jlog.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s"
        ))
        logging.root.handlers.clear()
        logging.root.addHandler(_h)
        logging.root.setLevel(logging.INFO)
    except ImportError:
        logging.basicConfig(
            level=logging.INFO,
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

    # ── Application ───────────────────────────────────────────────
    app = FastAPI(
        title="Ethiopian Business Management System",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
    if not os.environ.get("FLASK_SECRET_KEY"):
        logger.warning(
            "FLASK_SECRET_KEY not set — using ephemeral key "
            "(sessions reset on restart). Set it for production."
        )

    app.add_middleware(
        SessionMiddleware,
        secret_key=SECRET_KEY,
        session_cookie="session",
        same_site="lax",
        https_only=os.environ.get("SESSION_COOKIE_SECURE", "0").lower()
        in ("1", "true", "yes"),
    )

    # Static files
    _static = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(_static):
        app.mount("/static", StaticFiles(directory=_static), name="static")

    # Global ledger
    ledger = GeneralLedger()

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
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        if path in ("/health", "/favicon.ico") or any(
            path.startswith(p) for p in _PUBLIC
        ):
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
            except Exception:
                company_id = "default"

        request.state.company_id = company_id

        _ck = f"tenant:{company_id}"
        tenant = cache.get(_ck)
        if tenant is None:
            try:
                tenant = tenant_store.get_tenant(company_id)
            except Exception:
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
            except Exception:
                pass

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
            return Response(
                content=html,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type="text/html",
            )
        except Exception:
            return response

    # ── Exception handlers ────────────────────────────────────────
    from template_engine import templates

    @app.exception_handler(404)
    async def not_found(request: Request, exc: HTTPException):
        return templates.TemplateResponse(
            "errors/404.html", {"request": request}, status_code=404
        )

    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException):
        if exc.status_code in (301, 302, 303, 307, 308):
            return RedirectResponse(
                exc.headers.get("Location", "/"), status_code=exc.status_code
            )
        if "application/json" in request.headers.get("Accept", ""):
            return JSONResponse(
                {"error": exc.detail, "status": exc.status_code},
                status_code=exc.status_code,
            )
        return templates.TemplateResponse(
            "errors/404.html", {"request": request}, status_code=exc.status_code
        )

    # ── Health & root ─────────────────────────────────────────────
    @app.get("/health", name="health_check")
    async def health_check():
        from db import db
        pool = db._pool
        return {
            "status": "healthy",
            "service": "Ethiopian Business Management System",
            "pool": {
                "min": getattr(pool, "minconn", 0) if pool else 0,
                "max": getattr(pool, "maxconn", 0) if pool else 0,
            },
        }

    @app.get("/", name="index")
    async def index(request: Request):
        if request.session.get("logged_in"):
            return RedirectResponse("/auth/portal", status_code=302)
        return RedirectResponse("/auth/login", status_code=302)

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

    # Payroll uses add_payroll_routes(app, ledger) pattern
    try:
        from payroll_routes import add_payroll_routes
        add_payroll_routes(app, ledger)
        logger.info("Ethiopian payroll system integrated")
    except ImportError as e:
        logger.warning("Payroll system not available: %s", e)
    except Exception as e:
        logger.error("Error initializing payroll: %s", e, exc_info=True)
''')

print("Done writing infrastructure files.")
