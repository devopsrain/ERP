"""
Customer & Supplier Portal — authentication helpers.

Everything under /portal/ is exempt from the staff login middleware and the
staff CSRF middleware (see app.py), so this module supplies the portal's own:

  - session identity        request.session["portal_user"]   (JSON-safe dict)
  - CSRF token              request.session["portal_csrf"]   + hidden field
  - flash messages          request.session["portal_flash"]
  - login rate limiting     in-memory, per IP and per e-mail (5 / 15 min)
  - account lockout         10 failed attempts -> locked_until (30 min)
  - invite / reset tokens   secrets.token_urlsafe(32); 72 h invite, 1 h reset
  - password policy/hash    reuses auth_data_store (bcrypt) when importable

The session keys are deliberately distinct from the staff keys
(logged_in / user_id / username / full_name / privilege_level /
current_company_id / _csrf / _flash) so a staff member and a portal user can
never be confused, even when both identities live in one browser session.

This module imports no database code at import time so it is unit-testable
without DATABASE_URL.
"""
from __future__ import annotations

import hmac
import logging
import re
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Callable, Deque, Dict, Optional
from urllib.parse import urlencode

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# ── Session keys ─────────────────────────────────────────────────
SESSION_USER_KEY = "portal_user"
SESSION_CSRF_KEY = "portal_csrf"
SESSION_FLASH_KEY = "portal_flash"

# ── Policy constants ─────────────────────────────────────────────
LOGIN_URL = "/portal/login"
RATE_LIMIT_ATTEMPTS = 5            # per IP and per e-mail ...
RATE_LIMIT_WINDOW_SECONDS = 15 * 60  # ... within this window
ACCOUNT_LOCK_FAILURES = 10         # lock the account after N bad passwords
ACCOUNT_LOCK_MINUTES = 30
INVITE_TOKEN_HOURS = 72
RESET_TOKEN_HOURS = 1
MIN_PASSWORD_LENGTH = 10

VALID_KINDS = ("customer", "supplier")


# ── Password policy & hashing (reuse the staff implementation) ────

def _fallback_validate_password(password: str) -> tuple[bool, str]:
    """Identical rules to auth_data_store.validate_password (AICC 6.5.2)."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if not re.search(r"[A-Z]", password):
        return False, "Password must include at least one uppercase letter"
    if not re.search(r"[a-z]", password):
        return False, "Password must include at least one lowercase letter"
    if not re.search(r"\d", password):
        return False, "Password must include at least one number"
    return True, ""


def validate_password(password: str) -> tuple[bool, str]:
    """Central password policy — the staff rules, imported lazily so this
    module stays importable without a database."""
    try:
        from auth_data_store import validate_password as _staff_validate
        return _staff_validate(password)
    except Exception:
        return _fallback_validate_password(password)


def hash_password(password: str) -> str:
    """bcrypt hash — same helper the staff users table uses."""
    try:
        from auth_data_store import _hash_password
        return _hash_password(password)
    except Exception:
        import bcrypt
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    try:
        from auth_data_store import _verify_password
        return bool(_verify_password(password, password_hash))
    except Exception:
        try:
            import bcrypt
            return bcrypt.checkpw(password.encode(), password_hash.encode())
        except Exception:
            return False


# ── Tokens ───────────────────────────────────────────────────────

def generate_token() -> str:
    return secrets.token_urlsafe(32)


def token_expiry(hours: float, now: Optional[datetime] = None) -> datetime:
    return (now or datetime.now()) + timedelta(hours=hours)


def invite_expiry(now: Optional[datetime] = None) -> datetime:
    return token_expiry(INVITE_TOKEN_HOURS, now)


def reset_expiry(now: Optional[datetime] = None) -> datetime:
    return token_expiry(RESET_TOKEN_HOURS, now)


def token_is_valid(expires_at, now: Optional[datetime] = None) -> bool:
    """True when `expires_at` (datetime or ISO string) is still in the future."""
    if not expires_at:
        return False
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            return False
    return (now or datetime.now()) < expires_at


def tokens_match(a: Optional[str], b: Optional[str]) -> bool:
    """Constant-time comparison that tolerates None/empty."""
    if not a or not b:
        return False
    return hmac.compare_digest(str(a), str(b))


# ── Rate limiter (in-memory, per process) ─────────────────────────

class LoginRateLimiter:
    """Sliding-window counter keyed by an arbitrary string (IP or e-mail).

    `clock` is injectable for tests. Thread-safe; memory is bounded because
    stale keys are pruned on every call.
    """

    def __init__(self, max_attempts: int = RATE_LIMIT_ATTEMPTS,
                 window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._clock = clock
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> Deque[float]:
        q = self._hits.get(key)
        if q is None:
            q = deque()
            self._hits[key] = q
        cutoff = now - self.window
        while q and q[0] <= cutoff:
            q.popleft()
        if not q and key in self._hits and len(self._hits) > 5000:
            # opportunistic global prune when the table gets large
            for k in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
                self._hits.pop(k, None)
            q = self._hits.setdefault(key, deque())
        return q

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, self._clock())) >= self.max_attempts

    def retry_after(self, key: str) -> int:
        """Seconds until the oldest counted attempt leaves the window."""
        with self._lock:
            q = self._prune(key, self._clock())
            if len(q) < self.max_attempts:
                return 0
            return max(1, int(q[0] + self.window - self._clock()))

    def hit(self, key: str) -> None:
        with self._lock:
            self._prune(key, self._clock()).append(self._clock())

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


login_limiter = LoginRateLimiter()


def rate_limit_keys(ip: str, email: str) -> tuple[str, str]:
    return f"ip:{ip or '-'}", f"email:{(email or '').strip().lower() or '-'}"


# ── Account lockout helpers (pure) ────────────────────────────────

def next_failure_state(failed_attempts: int,
                       now: Optional[datetime] = None) -> tuple[int, Optional[datetime]]:
    """Given the current failure count, return (new_count, locked_until)."""
    failed = int(failed_attempts or 0) + 1
    if failed >= ACCOUNT_LOCK_FAILURES:
        return failed, (now or datetime.now()) + timedelta(minutes=ACCOUNT_LOCK_MINUTES)
    return failed, None


def is_locked(locked_until, now: Optional[datetime] = None) -> bool:
    return token_is_valid(locked_until, now)


# ── Request helpers ───────────────────────────────────────────────

def client_ip(request: Request) -> str:
    try:
        fwd = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        real = request.headers.get("X-Real-IP", "")
        host = getattr(getattr(request, "client", None), "host", None) or ""
        return fwd or real or host or "unknown"
    except Exception:
        return "unknown"


def user_agent(request: Request) -> str:
    try:
        return (request.headers.get("User-Agent", "") or "")[:300]
    except Exception:
        return ""


def external_base_url(request: Request) -> str:
    """Absolute origin for links in e-mails. PORTAL_BASE_URL / APP_BASE_URL
    win; otherwise trust the proxy's X-Forwarded-Proto/Host."""
    import os
    for var in ("PORTAL_BASE_URL", "APP_BASE_URL", "PUBLIC_BASE_URL"):
        v = (os.environ.get(var) or "").strip().rstrip("/")
        if v:
            return v
    try:
        proto = request.headers.get("X-Forwarded-Proto") or request.url.scheme or "https"
        host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host") \
            or request.url.netloc
        return f"{proto}://{host}"
    except Exception:
        return ""


# ── CSRF ─────────────────────────────────────────────────────────

def get_csrf_token(request: Request) -> str:
    tok = request.session.get(SESSION_CSRF_KEY)
    if not tok:
        tok = secrets.token_hex(32)
        request.session[SESSION_CSRF_KEY] = tok
    return tok


def csrf_ok(request: Request, submitted: Optional[str]) -> bool:
    return tokens_match(request.session.get(SESSION_CSRF_KEY), submitted)


def require_csrf(request: Request, form) -> None:
    """Raise 403 unless the hidden `csrf_token` (or X-CSRFToken header)
    matches the portal session token. Call on EVERY portal POST."""
    submitted = None
    try:
        submitted = form.get("csrf_token") if form is not None else None
    except Exception:
        submitted = None
    if not submitted:
        submitted = request.headers.get("X-CSRFToken")
    if not csrf_ok(request, submitted):
        logger.warning("portal CSRF rejected path=%s ip=%s",
                       request.url.path, client_ip(request))
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")


def require_staff_csrf(request: Request, form) -> None:
    """Staff-side admin pages under /portal/admin are skipped by the global
    CSRF middleware, so verify the STAFF session token (deps._csrf) here."""
    submitted = None
    try:
        submitted = form.get("csrf_token") if form is not None else None
    except Exception:
        submitted = None
    if not submitted:
        submitted = request.headers.get("X-CSRFToken")
    if not tokens_match(request.session.get("_csrf"), submitted):
        logger.warning("portal admin CSRF rejected path=%s ip=%s",
                       request.url.path, client_ip(request))
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")


# ── Flash ────────────────────────────────────────────────────────

def flash(request: Request, message: str, category: str = "info") -> None:
    msgs = list(request.session.get(SESSION_FLASH_KEY) or [])
    msgs.append({"message": message, "category": category})
    request.session[SESSION_FLASH_KEY] = msgs   # reassign so the store sees it


def pop_flashes(request: Request) -> list[tuple[str, str]]:
    msgs = request.session.pop(SESSION_FLASH_KEY, None) or []
    return [(m.get("category", "info"), m.get("message", "")) for m in msgs]


# ── Session identity ─────────────────────────────────────────────

def session_payload(user: dict) -> dict:
    """The JSON-safe subset of a portal_users row kept in the session."""
    return {
        "id": user.get("id"),
        "company_id": user.get("company_id") or "default",
        "kind": user.get("kind"),
        "email": user.get("email"),
        "full_name": user.get("full_name") or "",
        "org_name": user.get("org_name") or "",
        "party_key": user.get("party_key") or "",
        "login_time": int(time.time()),
    }


def login_session(request: Request, user: dict) -> None:
    request.session["_rotate"] = True            # session-fixation defense
    request.session[SESSION_USER_KEY] = session_payload(user)
    request.session.pop(SESSION_CSRF_KEY, None)  # fresh CSRF token post-login


def logout_session(request: Request) -> None:
    for k in (SESSION_USER_KEY, SESSION_CSRF_KEY, SESSION_FLASH_KEY):
        request.session.pop(k, None)


def current_portal_user(request: Request) -> Optional[dict]:
    u = request.session.get(SESSION_USER_KEY)
    if isinstance(u, dict) and u.get("id") and u.get("kind") in VALID_KINDS:
        return u
    return None


def _redirect_to_login(request: Request) -> HTTPException:
    nxt = request.url.path
    if request.url.query:
        nxt += f"?{request.url.query}"
    return HTTPException(status_code=302,
                         headers={"Location": f"{LOGIN_URL}?{urlencode({'next': nxt})}",
                                  "Cache-Control": "no-store"})


def portal_user(request: Request) -> dict:
    """FastAPI dependency: the logged-in portal user or a redirect to login."""
    u = current_portal_user(request)
    if not u:
        raise _redirect_to_login(request)
    return u


def portal_customer(request: Request) -> dict:
    u = portal_user(request)
    if u.get("kind") != "customer":
        raise HTTPException(status_code=303, headers={"Location": "/portal/"})
    return u


def portal_supplier(request: Request) -> dict:
    u = portal_user(request)
    if u.get("kind") != "supplier":
        raise HTTPException(status_code=303, headers={"Location": "/portal/"})
    return u


def safe_next(next_url: Optional[str]) -> str:
    """Only allow same-site portal paths as post-login targets."""
    n = (next_url or "").strip()
    if n.startswith("/portal/") and not n.startswith("//") and "\\" not in n:
        return n
    return "/portal/"


__all__ = [
    "SESSION_USER_KEY", "SESSION_CSRF_KEY", "SESSION_FLASH_KEY",
    "RATE_LIMIT_ATTEMPTS", "RATE_LIMIT_WINDOW_SECONDS",
    "ACCOUNT_LOCK_FAILURES", "ACCOUNT_LOCK_MINUTES",
    "INVITE_TOKEN_HOURS", "RESET_TOKEN_HOURS", "VALID_KINDS",
    "validate_password", "hash_password", "verify_password",
    "generate_token", "token_expiry", "invite_expiry", "reset_expiry",
    "token_is_valid", "tokens_match",
    "LoginRateLimiter", "login_limiter", "rate_limit_keys",
    "next_failure_state", "is_locked",
    "client_ip", "user_agent", "external_base_url",
    "get_csrf_token", "csrf_ok", "require_csrf", "require_staff_csrf",
    "flash", "pop_flashes",
    "session_payload", "login_session", "logout_session",
    "current_portal_user", "portal_user", "portal_customer", "portal_supplier",
    "safe_next",
]
