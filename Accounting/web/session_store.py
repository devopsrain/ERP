"""
Redis-backed Server-Side Session Middleware

Drop-in replacement for Starlette's SessionMiddleware that keeps session
data in Redis instead of the encrypted cookie.

Benefits over cookie sessions:
- Cookie only contains a random session ID (not the full payload) → smaller,
  leaks no user data even if the cookie is stolen.
- Sessions can be invalidated server-side (revoke by deleting the Redis key).
- Session data is never exposed to the client.
- Works across multiple worker processes / pods without shared state.

Fallback:
  If Redis is unavailable (connection refused / timeout), the middleware
  falls back transparently to Starlette's standard cookie-based SessionMiddleware
  so the app keeps running.

Usage in app.py — REPLACE the existing SessionMiddleware block with:

    from session_store import make_session_middleware
    app.add_middleware(make_session_middleware(SECRET_KEY))

    # Then remove the old:
    # app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, ...)
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from typing import Any, MutableMapping

logger = logging.getLogger(__name__)

_SESSION_TTL = 28800          # 8-hour session expiry (seconds)
_COOKIE_NAME = "sid"          # Server-side session ID cookie name
_KEY_PREFIX  = "acct:sess:"   # Redis key prefix for sessions


# ── Redis client (shared with extensions.py cache) ────────────────

def _get_redis():
    """Return a redis client or None if unavailable."""
    try:
        import redis as _redis_lib
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        r = _redis_lib.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=2,  # TCP handshake timeout
            socket_timeout=2,          # per-operation timeout (e.g. ping)
        )
        r.ping()
        return r
    except Exception as e:
        logger.debug("Redis session backend unavailable: %s", e)
        return None


# ── Session object ──────────────────────────────────────────────

class _RedisSession(MutableMapping):
    """
    Dict-like session object backed by Redis.

    Starlette's SessionMiddleware expects request.session to be a
    MutableMapping. This class satisfies that interface while storing
    all values in Redis.
    """

    def __init__(self, redis_client, session_id: str):
        self._r = redis_client
        self._sid = session_id
        self._key = f"{_KEY_PREFIX}{session_id}"
        self._data: dict = self._load()
        self._modified = False

    def _load(self) -> dict:
        try:
            raw = self._r.get(self._key)
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def save(self):
        """Persist to Redis (called by middleware after response)."""
        if self._modified:
            try:
                self._r.setex(self._key, _SESSION_TTL, json.dumps(self._data))
            except Exception as e:
                logger.error("Session save failed: %s", e)

    def destroy(self):
        """Delete the session from Redis (used on logout)."""
        try:
            self._r.delete(self._key)
            self._data.clear()
        except Exception:
            pass

    # MutableMapping interface
    def __getitem__(self, key):         return self._data[key]
    def __setitem__(self, key, value):  self._data[key] = value;  self._modified = True
    def __delitem__(self, key):         del self._data[key];       self._modified = True
    def __iter__(self):                 return iter(self._data)
    def __len__(self):                  return len(self._data)
    def __contains__(self, key):        return key in self._data
    def get(self, key, default=None):   return self._data.get(key, default)
    def pop(self, key, *args):
        val = self._data.pop(key, *args)
        self._modified = True
        return val
    def setdefault(self, key, default=None):
        if key not in self._data:
            self._data[key] = default
            self._modified = True
        return self._data[key]


# ── ASGI Middleware ───────────────────────────────────────────────

class RedisSessionMiddleware:
    """
    ASGI middleware that assigns each visitor a random session ID cookie
    (``sid``) and stores all session data in Redis.

    Falls back to Starlette SessionMiddleware if Redis is down.
    """

    def __init__(self, app, secret_key: str, https_only: bool = False):
        self._app = app
        self._https_only = https_only
        self._redis = _get_redis()

        if self._redis is None:
            logger.warning(
                "Redis unavailable — falling back to cookie-based sessions "
                "(data stored in signed cookie, not server-side)"
            )
            from starlette.middleware.sessions import SessionMiddleware as _SM
            self._fallback = _SM(app, secret_key=secret_key, https_only=https_only)
        else:
            self._fallback = None
            logger.info("Redis session backend active")

    async def __call__(self, scope, receive, send):
        if self._fallback is not None:
            await self._fallback(scope, receive, send)
            return

        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return

        # Extract or generate session ID from cookie
        from starlette.datastructures import MutableHeaders
        from starlette.requests import HTTPConnection

        conn = HTTPConnection(scope)
        sid = conn.cookies.get(_COOKIE_NAME)
        if not sid:
            sid = secrets.token_hex(32)

        session = _RedisSession(self._redis, sid)
        scope["session"] = session

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                session.save()
                headers = MutableHeaders(scope=message)
                cookie_parts = [
                    f"{_COOKIE_NAME}={sid}",
                    "HttpOnly",
                    "SameSite=Lax",
                    f"Max-Age={_SESSION_TTL}",
                    "Path=/",
                ]
                if self._https_only:
                    cookie_parts.append("Secure")
                headers.append("Set-Cookie", "; ".join(cookie_parts))
            await send(message)

        await self._app(scope, receive, send_wrapper)


def make_session_middleware(secret_key: str, https_only: bool = False):
    """
    Factory function — returns the appropriate session middleware class.

    Usage:
        app.add_middleware(make_session_middleware(SECRET_KEY))
    """
    redis = _get_redis()
    if redis is not None:
        def _redis_mw(app):
            return RedisSessionMiddleware(app, secret_key=secret_key, https_only=https_only)
        _redis_mw.__name__ = "RedisSessionMiddleware"
        return _redis_mw

    # Fallback to Starlette's cookie sessions
    from starlette.middleware.sessions import SessionMiddleware
    def _cookie_mw(app):
        return SessionMiddleware(app, secret_key=secret_key, same_site="lax",
                                 https_only=https_only, session_cookie="session")
    _cookie_mw.__name__ = "SessionMiddleware"
    return _cookie_mw
