"""
Standalone REST API service — pure JSON, no Jinja2/SSR.

This is the *API service* half of the split-app architecture (suggestion #2).
It runs on a separate port (8001) and can scale independently from the web UI.

Run directly:
    uvicorn api_app:api_app --host 0.0.0.0 --port 8001

Run via docker-compose:
    docker compose up api

Consumed by:
  - Mobile applications
  - Third-party ERP integrations
  - Single-Page Applications (React, Vue, etc.)
  - Power BI / reporting tools
  - Other microservices

Auth:
    All endpoints require an Authorization: Bearer <token> header.
    Tokens are issued from the web app's /auth/portal page.

CORS:
    Set CORS_ORIGINS env var (comma-separated) to allow specific origins.
    Default: http://localhost:3000,http://localhost:5173 (dev SPA servers)
"""
from __future__ import annotations

import logging
import os
import secrets
import time as _time
import uuid as _uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

# ── Logging ───────────────────────────────────────────────────────────────────
_LOG_LEVEL = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def _lifespan(app: FastAPI):
    logger.info("api_app_startup", extra={"service": "ebms-api"})
    yield
    logger.info("api_app_shutdown")
    try:
        from async_db import close_async_pool
        await close_async_pool()
    except Exception:
        pass


# ── FastAPI instance ──────────────────────────────────────────────────────────
api_app = FastAPI(
    title="EBMS REST API",
    description=(
        "Ethiopian Business Management System — Machine-to-machine REST API.\n\n"
        "This service handles all JSON API calls and can be scaled independently "
        "from the Jinja2 SSR web application."
    ),
    version="1.0.0",
    # Docs are served by the custom self-hosted routes below (docs_url/redoc_url
    # must be None so FastAPI doesn't register CDN-backed pages). All URLs keep
    # the /api prefix because nginx proxies "location /api/" with a bare
    # proxy_pass (no URI part), i.e. the /api prefix is NOT stripped.
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=_lifespan,
)

# ── API docs (self-hosted Swagger UI / ReDoc) ─────────────────────────────────
# FastAPI's default docs pages pull swagger-ui-bundle.js / swagger-ui.css /
# redoc.standalone.js from cdn.jsdelivr.net. On networks where that CDN is
# blocked, the docs HTML loads but the page body stays empty (the whole UI is
# rendered by the CDN script). Serve the assets locally instead, under /api/
# so nginx routes the requests to this service.
_SWAGGER_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "static", "vendor", "swagger-ui")
if os.path.isdir(_SWAGGER_STATIC):
    api_app.mount("/api/static/vendor/swagger-ui",
                  StaticFiles(directory=_SWAGGER_STATIC), name="swagger_static")

    @api_app.get("/api/docs", include_in_schema=False)
    async def swagger_ui_html():
        return get_swagger_ui_html(
            openapi_url="/api/openapi.json",
            title=f"{api_app.title} - Swagger UI",
            swagger_js_url="/api/static/vendor/swagger-ui/swagger-ui-bundle.js",
            swagger_css_url="/api/static/vendor/swagger-ui/swagger-ui.css",
        )

    @api_app.get("/api/redoc", include_in_schema=False)
    async def redoc_html():
        return get_redoc_html(
            openapi_url="/api/openapi.json",
            title=f"{api_app.title} - ReDoc",
            redoc_js_url="/api/static/vendor/swagger-ui/redoc.standalone.js",
            with_google_fonts=False,
        )
else:  # pragma: no cover — vendored assets missing; fall back to CDN docs
    logger.warning("swagger-ui vendor assets not found at %s — using CDN docs",
                   _SWAGGER_STATIC)

    @api_app.get("/api/docs", include_in_schema=False)
    async def swagger_ui_html():
        return get_swagger_ui_html(openapi_url="/api/openapi.json",
                                   title=f"{api_app.title} - Swagger UI")

    @api_app.get("/api/redoc", include_in_schema=False)
    async def redoc_html():
        return get_redoc_html(openapi_url="/api/openapi.json",
                              title=f"{api_app.title} - ReDoc")

# ── CORS ───────────────────────────────────────────────────────────────────────
_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS", "http://localhost:3000,http://localhost:5173"
    ).split(",")
    if o.strip()
]

api_app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-CSRFToken"],
    expose_headers=["X-Request-ID"],
)

# GZip — compress all responses ≥ 1 KB
api_app.add_middleware(GZipMiddleware, minimum_size=1000)

# Minimal session middleware — kept for backward compatibility with shared deps
# that read request.session; the API primarily uses Bearer tokens.
_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
api_app.add_middleware(
    SessionMiddleware,
    secret_key=_SECRET_KEY,
    session_cookie="api_session",
    same_site="lax",
    https_only=os.environ.get("SESSION_COOKIE_SECURE", "0").lower() in ("1", "true"),
)


# ── Middleware ────────────────────────────────────────────────────────────────

@api_app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or _uuid.uuid4().hex[:12]
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


@api_app.middleware("http")
async def structured_log_middleware(request: Request, call_next):
    t0 = _time.perf_counter()
    response = await call_next(request)
    logger.info(
        "api_request",
        extra={
            "method":     request.method,
            "path":       request.url.path,
            "status":     response.status_code,
            "ms":         round((_time.perf_counter() - t0) * 1000, 1),
            "request_id": getattr(request.state, "request_id", "-"),
            "ip":         request.client.host if request.client else "-",
        },
    )
    return response


@api_app.middleware("http")
async def bearer_auth_middleware(request: Request, call_next):
    """
    API-only authentication — Bearer token required on every non-health request.
    No session cookie / redirect; always returns 401 JSON on failure.
    """
    path = request.url.path
    # Public paths: health check, API docs, and v2 auth token/refresh (no creds yet)
    _open = ("/health", "/api/docs", "/api/redoc", "/api/openapi.json")
    _open_prefixes = ("/api/v2/auth/token", "/api/v2/auth/refresh",
                      "/api/static/")
    if path in _open or any(path.startswith(p) for p in _open_prefixes):
        return await call_next(request)

    auth = request.headers.get("Authorization", "")

    # v2 JWT tokens: let jwt_required dependency handle validation inside the route
    if auth.startswith("Bearer ") and path.startswith("/api/v2/"):
        token = auth[7:].strip()
        try:
            from auth_data_store import auth_store
            payload = auth_store.validate_jwt(token)
            if payload:
                request.state.api_user = payload
                request.state.company_id = payload.get("company_id", "default")
                request.session["username"]           = payload.get("username", "api")
                request.session["current_company_id"] = request.state.company_id
                request.session["logged_in"]           = True
                return await call_next(request)
        except Exception:
            pass
        # Fall through to opaque token check below

    if not auth.startswith("Bearer "):
        return JSONResponse(
            {"error": "Missing Authorization: Bearer <token> header", "status": 401},
            status_code=401,
        )

    token = auth[7:].strip()
    try:
        from auth_data_store import auth_store
        user = auth_store.validate_api_token(token)
        if not user:
            raise ValueError("token_invalid")
        request.state.api_user = user
        request.state.company_id = user.get("company_id", "default")
        # Populate session so shared deps (deps.py template_context) work
        request.session["username"]          = user.get("username", "api")
        request.session["current_company_id"] = request.state.company_id
        request.session["logged_in"]          = True
    except Exception:
        return JSONResponse(
            {"error": "Token invalid or expired", "status": 401},
            status_code=401,
        )

    return await call_next(request)


# ── Rate limiting ─────────────────────────────────────────────────────────────
try:
    from extensions import limiter, LIMITER_AVAILABLE
    if LIMITER_AVAILABLE:
        from slowapi import _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded
        from slowapi.middleware import SlowAPIMiddleware
        api_app.state.limiter = limiter
        api_app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        api_app.add_middleware(SlowAPIMiddleware)
        logger.info("rate-limiting enabled on api_app")
except Exception as _e:
    logger.warning("rate-limiting not configured on api_app: %s", _e)


# ── Exception handlers ────────────────────────────────────────────────────────

@api_app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        {
            "error":      exc.detail,
            "status":     exc.status_code,
            "request_id": getattr(request.state, "request_id", "-"),
        },
        status_code=exc.status_code,
    )


@api_app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    rid = getattr(request.state, "request_id", "-")
    logger.error("api_unhandled_exception", exc_info=exc, extra={"request_id": rid})
    return JSONResponse(
        {"error": "Internal server error", "request_id": rid, "status": 500},
        status_code=500,
    )


# ── Health ────────────────────────────────────────────────────────────────────

@api_app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "service": "ebms-api"}


# ── Mount shared API routes ───────────────────────────────────────────────────
# The same api_routes.router is used by both this standalone service and
# the full web app (app.py). The router itself is stateless — it only
# reads request.state / request.session which both apps populate.

try:
    from api_routes import router as _api_router
    api_app.include_router(_api_router)
    logger.info("api_routes mounted on api_app")
except Exception as _e:
    logger.error("Failed to mount api_routes on api_app: %s", _e)

try:
    from api_v2_routes import router as _api_v2_router
    api_app.include_router(_api_v2_router)
    logger.info("api_v2_routes mounted on api_app")
except Exception as _e:
    logger.error("Failed to mount api_v2_routes on api_app: %s", _e)


# ── Run directly (dev) ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_app:api_app", host="0.0.0.0", port=8001, reload=True)
