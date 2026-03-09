"""
PostgreSQL Database Connection Module

Provides a thread-safe connection pool for all data stores.
Reads DATABASE_URL from environment (set in .env or os.environ).

Usage:
    from db import get_conn, get_cursor

    # Single query
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()

    # Multiple queries in one transaction
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("INSERT INTO ...")
            cur.execute("UPDATE ...")
"""

import os
import logging
import threading
from contextlib import contextmanager

import psycopg2
import psycopg2.pool
import psycopg2.extras

logger = logging.getLogger(__name__)

_pool = None
_pool_lock = threading.Lock()


def _init_pool():
    """Initialize the connection pool (called lazily on first use)."""
    global _pool
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Add it to your .env file or set it in the environment."
        )
    _pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=2,
        maxconn=20,
        dsn=database_url,
        connect_timeout=10,
    )
    logger.info("PostgreSQL connection pool initialised (min=2, max=20)")


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Return (or lazily create) the connection pool."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:          # double-checked locking
                _init_pool()
    return _pool


@contextmanager
def get_conn():
    """
    Context manager that checks out a connection, commits on clean exit,
    rolls back on exception, then returns the connection to the pool.
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def get_cursor():
    """
    Convenience context manager that yields a RealDictCursor.
    Wraps get_conn(), so commit/rollback is handled automatically.
    """
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
        finally:
            cur.close()


def execute(sql: str, params=None):
    """
    Execute a single DML statement (INSERT / UPDATE / DELETE).
    Returns the cursor's rowcount.
    """
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def fetchone(sql: str, params=None) -> dict | None:
    """Run a SELECT and return the first row as a dict, or None."""
    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def fetchall(sql: str, params=None) -> list[dict]:
    """Run a SELECT and return all rows as a list of dicts."""
    with get_cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


@contextmanager
def get_tenant_cursor(company_id: str):
    """
    Like get_cursor(), but sets a transaction-local PostgreSQL variable for RLS.

    Use this in data stores for tables that have row-level security enabled.
    The variable 'app.current_company_id' is set for the duration of the
    transaction only (TRUE = local scope), so it can never leak across requests.

    Example:
        with get_tenant_cursor(g.company_id) as cur:
            cur.execute("SELECT * FROM bid_records")   # RLS filters by company_id
    """
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute(
                "SELECT set_config('app.current_company_id', %s, TRUE)",
                (company_id or '',)
            )
            yield cur
        finally:
            cur.close()


def health_check() -> dict:
    """
    Check the health of the PostgreSQL RDS connection.

    Returns a dict with:
      - ok (bool)          : True if the DB is reachable
      - latency_ms (float) : round-trip time for SELECT 1
      - version (str)      : PostgreSQL server version string
      - pool_min (int)     : minimum pool size configured
      - pool_max (int)     : maximum pool size configured
      - pool_available (int): connections currently idle in pool
      - error (str|None)   : exception message if not ok
    """
    import time
    result: dict = {
        'ok': False,
        'latency_ms': None,
        'version': None,
        'pool_min': None,
        'pool_max': None,
        'pool_available': None,
        'error': None,
    }
    try:
        pool = _get_pool()
        result['pool_min'] = pool.minconn
        result['pool_max'] = pool.maxconn
        # ThreadedConnectionPool tracks idle connections in _pool set
        result['pool_available'] = len(getattr(pool, '_pool', []))

        t0 = time.perf_counter()
        with get_cursor() as cur:
            cur.execute("SELECT 1")
            cur.execute("SELECT version()")
            row = cur.fetchone()
        result['latency_ms'] = round((time.perf_counter() - t0) * 1000, 2)
        if row:
            # row is a RealDictRow: key is 'version'
            version_str = row.get('version', '')
            # shorten: "PostgreSQL 15.4 on x86_64..." → "PostgreSQL 15.4"
            result['version'] = ' '.join(version_str.split()[:2])
        result['ok'] = True
    except Exception as e:
        logger.error("DB health check failed: %s", e)
        result['error'] = str(e)
    return result
