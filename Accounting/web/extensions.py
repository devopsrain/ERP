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
