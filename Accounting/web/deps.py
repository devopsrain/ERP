"""
FastAPI shared dependencies.

Provides:
  - Flash messages (session-backed)
  - CSRF token generation
  - Flask-compatible url_for shim for templates
  - Authentication dependency factory (require_auth / login_required)
  - Standard template context builder
"""
from __future__ import annotations

import logging
import secrets
from typing import Any, Callable, Optional

import os
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)


# ── Flash Messages ────────────────────────────────────────────────

def flash(request: Request, message: str, category: str = "info") -> None:
    """Store a flash message in the session (consumed once on next render)."""
    request.session.setdefault("_flash", []).append(
        {"message": message, "category": category}
    )


def get_flashed_messages(request: Request, with_categories: bool = False):
    """Pop and return all pending flash messages."""
    msgs = request.session.pop("_flash", [])
    if with_categories:
        return [(m["category"], m["message"]) for m in msgs]
    return [m["message"] for m in msgs]


# ── CSRF ──────────────────────────────────────────────────────────

def get_csrf_token(request: Request) -> str:
    """Return (creating if absent) a per-session CSRF token."""
    if "_csrf" not in request.session:
        request.session["_csrf"] = secrets.token_hex(32)
    return request.session["_csrf"]


# ── Company Resolution ────────────────────────────────────────────

def current_company(request: Request) -> str:
    """
    Single source of truth for the active company id.

    Every route module must resolve the tenant through this helper so
    that reads and writes always target the same company. Falls back to
    "default" when no company is selected in the session.
    """
    return request.session.get("current_company_id") or "default"


# ── URL Generation — Flask-compatible shim ────────────────────────

def make_url_for(request: Request) -> Callable:
    """
    Return a url_for function that accepts Flask 'blueprint.function' notation.

    Templates keep calling {{ url_for('auth.login') }} unchanged.
    Internally 'auth.login' is mapped to the FastAPI route named 'auth_login'.
    """
    def _relative(url: Any, extra_query: Optional[dict] = None) -> str:
        # Return a RELATIVE url. The absolute form carries the scheme
        # uvicorn saw (http behind the TLS-terminating proxy), which made
        # browsers warn "the information you're about to submit is not
        # secure" on every form whose action came from url_for.
        path = url.path if hasattr(url, "path") else str(url)
        query = getattr(url, "query", "")
        if extra_query:
            extra = urlencode(extra_query)
            query = f"{query}&{extra}" if query else extra
        return f"{path}?{query}" if query else path

    def url_for(endpoint: str, **params: Any) -> str:
        name = endpoint.replace(".", "_")
        try:
            return _relative(request.url_for(name, **params))
        except Exception:
            pass

        # Flask semantics: kwargs that are not path parameters belong in the
        # query string. Starlette's url_for raises NoMatchFound when it gets
        # kwargs the route path doesn't declare (e.g.
        # url_for('siem.event_log', ip=...) for the param-less /siem/events
        # route), which used to hit the guessed-URL fallback below and emit
        # dead links like /siem/event-log. Split the kwargs against the
        # route's actual path parameters and append the rest as a query.
        try:
            for route in request.app.routes:
                if getattr(route, "name", None) == name:
                    path_keys = set(getattr(route, "param_convertors", None) or ())
                    path_params = {k: v for k, v in params.items() if k in path_keys}
                    query_params = {k: v for k, v in params.items() if k not in path_keys}
                    return _relative(request.url_for(name, **path_params), query_params)
        except Exception:
            pass

        # Graceful fallback during incremental migration — the route name is
        # unknown to this app, so guess a Flask-era URL and log it.
        logger.warning("url_for: no route named %r — guessing URL", name)
        return "/" + endpoint.replace(".", "/").replace("_", "-")

    return url_for


# ── Standard Template Context ────────────────────────────────────

def template_context(request: Request) -> dict:
    """
    Build the context dict that every TemplateResponse needs.

    Injects Flask-compatible helpers so existing Jinja2 templates work
    without modification.
    """
    cdn = os.environ.get("STATIC_CDN_URL", "").rstrip("/")

    def static_url(path: str) -> str:
        """Return the URL for a static asset, using CDN when available."""
        p = path if path.startswith("/") else f"/{path}"
        return f"{cdn}{p}" if cdn else p

    return {
        "request": request,
        "url_for": make_url_for(request),
        "get_flashed_messages": lambda **kw: get_flashed_messages(request, **kw),
        "csrf_token": lambda: get_csrf_token(request),
        "session": request.session,
        "current_company_id": getattr(request.state, "company_id", None),
        "current_tenant": getattr(request.state, "tenant", None),
        # Provide app_version used in base.html footer
        "app_version": _get_app_version(),
        # Architecture 5: CDN helper — use {{ static_url('/static/file.css') }} in templates
        "static_cdn_url": cdn,
        "static_url": static_url,
    }


def _get_app_version() -> str:
    try:
        from version_data_store import version_manager
        return version_manager.get_current_version()
    except Exception:
        return "1.0.0"


# ── Upload Validation (AICC 6.3) ─────────────────────────────────

# Extensions that are never accepted, regardless of any per-route whitelist
DANGEROUS_UPLOAD_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".sh", ".ps1", ".php", ".js",
    ".jar", ".msi", ".dll", ".scr", ".vbs",
}


def validate_upload(filename: str, content: bytes | None,
                    allowed_exts=None) -> tuple[bool, str]:
    """
    Central upload validation (AICC 6.3 — malicious upload controls).

    Args:
        filename:     the client-supplied file name.
        content:      the uploaded bytes (pass None to skip the empty check).
        allowed_exts: optional whitelist of extensions (with or without a
                      leading dot, case-insensitive). When given, anything
                      outside the whitelist is rejected.

    Returns (ok, error_message). Dangerous executable/script extensions are
    always rejected, even if present in a whitelist.
    """
    import posixpath

    name = (filename or "").strip()
    if not name:
        return False, "No file selected"

    # Use only the basename; look at the final extension
    base = posixpath.basename(name.replace("\\", "/"))
    dot = base.rfind(".")
    ext = base[dot:].lower() if dot > 0 else ""

    if ext in DANGEROUS_UPLOAD_EXTENSIONS:
        return False, f"File type '{ext}' is not allowed for security reasons"

    if allowed_exts is not None:
        allowed = {
            e.lower() if e.startswith(".") else f".{e.lower()}"
            for e in allowed_exts
        } - DANGEROUS_UPLOAD_EXTENSIONS
        if ext not in allowed:
            return False, f"File type '{ext or '(none)'}' is not allowed"

    if content is not None and len(content) == 0:
        return False, "Uploaded file is empty"

    return True, ""


# ── Authentication ────────────────────────────────────────────────

def get_current_user(request: Request) -> Optional[dict]:
    """Return the authenticated user from session or Bearer token."""
    if request.session.get("logged_in"):
        return {
            "user_id": request.session.get("user_id"),
            "username": request.session.get("username"),
            "full_name": request.session.get("full_name", ""),
            "privilege_level": request.session.get("privilege_level", "viewer"),
        }
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            from auth_data_store import auth_store
            return auth_store.validate_api_token(auth_header[7:].strip())
        except Exception:
            pass
    return None


def require_auth(min_privilege: str = "viewer") -> Callable:
    """
    FastAPI dependency factory.  Returns a *callable* suitable for Depends().

    Usage:
        @router.get("/path")
        async def view(request: Request, user=Depends(require_auth())):
            ...
        @router.get("/admin")
        async def admin(request: Request, user=Depends(require_auth("admin"))):
            ...
    """
    def _dep(request: Request) -> dict:
        user = get_current_user(request)
        if not user:
            is_api = (
                request.url.path.startswith("/api/")
                or "application/json" in request.headers.get("Accept", "")
            )
            if is_api:
                raise HTTPException(status_code=401, detail="Authentication required")
            # Include 'next' parameter so login can redirect back after auth
            from urllib.parse import urlencode
            next_url = str(request.url.path)
            if request.url.query:
                next_url += f"?{request.url.query}"
            redirect_url = f"/auth/login?{urlencode({'next': next_url})}"
            raise HTTPException(
                status_code=302, headers={"Location": redirect_url}
            )
        if min_privilege and min_privilege != "viewer":
            try:
                from auth_data_store import PRIVILEGE_LEVELS
                # PRIVILEGE_LEVELS is a dict {name: int_level} — use .get(), not .index()
                user_lvl = PRIVILEGE_LEVELS.get(user.get("privilege_level", "viewer"), 0)
                req_lvl  = PRIVILEGE_LEVELS.get(min_privilege, 0)
                if user_lvl < req_lvl:
                    raise HTTPException(
                        status_code=303,
                        headers={"Location": "/auth/access-denied"},
                    )
            except (KeyError, TypeError):
                pass
        return user

    return _dep


# ── Convenience aliases ───────────────────────────────────────────
# These are raw callables — routes must wrap them in Depends():
#   async def view(user=Depends(login_required)): ...

login_required      = require_auth()
admin_required      = require_auth("admin")
super_admin_required = require_auth("super_admin")
