"""
Shared extensions -- framework-agnostic stubs.
No Flask or FastAPI dependencies here so both legacy and new code can import freely.
"""
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

# ── In-memory TTL cache (fallback when Redis is unavailable) ------
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


# ── Redis cache (preferred — shared across workers, survives restarts) --
import _pickle as _pickle_mod

class _RedisCache:
    """Redis-backed cache with pickle serialisation.

    All keys are namespaced under ``acct:`` to avoid collisions with
    other applications sharing the same Redis instance.
    """

    def __init__(self, client):
        self._r = client

    def _k(self, key: str) -> str:
        return f"acct:{key}"

    def get(self, key):
        try:
            raw = self._r.get(self._k(key))
            return _pickle_mod.loads(raw) if raw is not None else None
        except Exception:
            return None

    def set(self, key, value, timeout=300):
        try:
            packed = _pickle_mod.dumps(value)
            if timeout:
                self._r.setex(self._k(key), int(timeout), packed)
            else:
                self._r.set(self._k(key), packed)
        except Exception:
            pass

    def delete(self, key):
        try:
            self._r.delete(self._k(key))
        except Exception:
            pass

    def cached(self, *args, **kwargs):
        def decorator(f):
            return f
        return decorator

    def init_app(self, app, config=None):
        pass


try:
    import os as _os
    import redis as _redis_lib
    _redis_url = _os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    _r_client = _redis_lib.from_url(
        _redis_url,
        decode_responses=False,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    _r_client.ping()   # raises if unreachable
    cache = _RedisCache(_r_client)
    CACHE_AVAILABLE = True
    _logging.getLogger(__name__).info("Redis cache connected: %s", _redis_url)
except Exception as _cache_err:
    _logging.getLogger(__name__).warning(
        "Redis unavailable (%s) — using in-memory cache (single-process only)",
        _cache_err,
    )
    cache = _MemCache()
    CACHE_AVAILABLE = True


# ── Write-side cache invalidation helpers ─────────────────────────
# Route handlers that mutate data should call these after a successful
# write so that cached read results are flushed immediately.

def invalidate_company_cache(company_id: str, *modules: str) -> None:
    """
    Evict all per-company cache keys for the given modules.

    Usage in a route::
        from extensions import invalidate_company_cache
        # after successful write:
        invalidate_company_cache(company_id, "inventory", "dashboard")

    Supported module names:
        "inventory", "transactions", "journal", "accounts",
        "vat", "employees", "siem", "dashboard"
    """
    _PREFIX_MAP = {
        "inventory":    [
            "inventory:{cid}",
            "svc:inventory:items:{cid}",
            "svc:inventory:dash:{cid}",
            "api:inventory:{cid}:0:100:0",
            "api:inventory:{cid}:1:100:0",
        ],
        "transactions": [
            "api:transactions:{cid}:0:50:0",
            "api:transactions:{cid}:1:50:0",
        ],
        "journal":      ["api:journal:{cid}:50:0"],
        "accounts":     ["api:accounts:{cid}:all:200:0"],
        "vat":          ["api:vat_summary:{cid}"],
        "employees":    ["api:employees:{cid}:100:0"],
        "dashboard":    ["dashboard_stats:{cid}"],
        "siem":         ["api:siem_events:all:100:0"],
    }
    for module in modules:
        patterns = _PREFIX_MAP.get(module, [])
        for pattern in patterns:
            key = pattern.format(cid=company_id)
            try:
                cache.delete(key)
            except Exception:
                pass
