"""
Async PostgreSQL database layer — asyncpg

Drop-in async alternative to `db.py` (psycopg2 ThreadedConnectionPool).
Use in FastAPI `async def` route handlers to avoid blocking the event loop
with synchronous psycopg2 I/O.

Usage — raw queries:
    from async_db import get_async_conn

    @router.get("/items")
    async def list_items(request: Request):
        async with get_async_conn() as conn:
            rows = await conn.fetch(
                "SELECT * FROM accounts WHERE company_id=$1", company_id
            )
            return [dict(r) for r in rows]

Usage — explicit transaction:
    from async_db import get_async_transaction

    async with get_async_transaction() as conn:
        await conn.execute("INSERT INTO journal_entries ...", ...)
        await conn.execute("UPDATE accounts SET balance=...", ...)

Usage — SQLAlchemy async ORM session (with orm_models.py):
    from async_db import get_async_session
    from orm_models import User
    from sqlalchemy import select

    async with get_async_session() as session:
        result = await session.execute(select(User).where(User.is_active == True))
        users = result.scalars().all()

Shutdown:
    Call `await close_async_pool()` from the FastAPI lifespan handler.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

logger = logging.getLogger(__name__)

# ── asyncpg (raw async pool) ──────────────────────────────────────
try:
    import asyncpg
    _ASYNCPG_AVAILABLE = True
except ImportError:
    asyncpg = None
    _ASYNCPG_AVAILABLE = False
    logger.warning(
        "asyncpg not installed — async DB layer unavailable.  "
        "Run: pip install asyncpg"
    )

# ── SQLAlchemy async (ORM sessions) ──────────────────────────────
try:
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    _SQLA_ASYNC_AVAILABLE = True
except ImportError:
    AsyncSession = Any
    async_sessionmaker = None
    create_async_engine = None
    _SQLA_ASYNC_AVAILABLE = False
    logger.warning(
        "SQLAlchemy async drivers unavailable.  "
        "Run: pip install sqlalchemy asyncpg"
    )

_pool: Any | None = None
_pool_lock = asyncio.Lock()

_async_engine = None
_async_session_factory = None

_STATEMENT_TIMEOUT_MS = int(os.environ.get("DB_STATEMENT_TIMEOUT_MS", "30000"))
_LOCK_TIMEOUT_MS = int(os.environ.get("DB_LOCK_TIMEOUT_MS", "5000"))
_IDLE_TX_TIMEOUT_MS = int(os.environ.get("DB_IDLE_TX_TIMEOUT_MS", "60000"))


def _server_settings() -> dict[str, str]:
    return {
        "statement_timeout": str(_STATEMENT_TIMEOUT_MS),
        "lock_timeout": str(_LOCK_TIMEOUT_MS),
        "idle_in_transaction_session_timeout": str(_IDLE_TX_TIMEOUT_MS),
    }


# ── Pool lifecycle ─────────────────────────────────────────────────

def _get_dsn() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Export it before starting the app."
        )
    # asyncpg requires postgresql:// not postgres://
    return url.replace("postgres://", "postgresql://")


async def _init_asyncpg_pool() -> Any:
    global _pool
    if asyncpg is None:
        raise RuntimeError("asyncpg is not installed. Run: pip install asyncpg")
    dsn = _get_dsn()
    min_size = int(os.environ.get("DB_POOL_MIN_SIZE", 5))
    max_size = int(os.environ.get("DB_POOL_MAX_SIZE", 15))

    # Log the target (host/dbname only — never the password)
    _safe_target = dsn.split("@")[-1] if "@" in dsn else "(redacted)"
    logger.info(
        "asyncpg connecting to: %s (pool_size: %d-%d)",
        _safe_target,
        min_size,
        max_size,
    )
    try:
        _pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=min_size,
            max_size=max_size,
            command_timeout=30,
            statement_cache_size=0,   # required for PgBouncer compatibility
            server_settings=_server_settings(),
        )
        logger.info(
            "asyncpg connection pool initialised (min=%d, max=%d, statement_timeout_ms=%d, lock_timeout_ms=%d)",
            min_size,
            max_size,
            _STATEMENT_TIMEOUT_MS,
            _LOCK_TIMEOUT_MS,
        )
    except Exception as e:
        logger.error(
            "asyncpg connection FAILED (target=%s): %s",
            _safe_target, e,
            exc_info=True,
        )
        raise
    return _pool


async def get_async_pool() -> Any:
    """Return (or lazily create) the shared asyncpg pool."""
    if not _ASYNCPG_AVAILABLE:
        raise RuntimeError("asyncpg is not installed. Run: pip install asyncpg")
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                await _init_asyncpg_pool()
    assert _pool is not None
    return _pool


async def close_async_pool() -> None:
    """Gracefully drain and close the asyncpg pool (call on app shutdown)."""
    global _pool
    if _pool and _ASYNCPG_AVAILABLE:
        await _pool.close()
        _pool = None
        logger.info("asyncpg pool closed")


# ── Context managers — asyncpg ─────────────────────────────────────

@asynccontextmanager
async def get_async_conn() -> "AsyncIterator[Any]":
    """
    Borrow a connection from the asyncpg pool.
    Auto-released back to the pool on exit.

    Example::
        async with get_async_conn() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE username=$1", "alice"
            )
    """
    pool = await get_async_pool()
    async with pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def get_async_transaction() -> "AsyncIterator[Any]":
    """
    Borrow a connection and wrap it in an explicit transaction.
    Commits on clean exit; rolls back on any exception.

    Example::
        async with get_async_transaction() as conn:
            await conn.execute("INSERT INTO accounts ...", ...)
            await conn.execute("UPDATE balances SET ...", ...)
    """
    pool = await get_async_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn


@asynccontextmanager
async def get_async_tenant_conn(company_id: str) -> "AsyncIterator[Any]":
    """
    Borrow a connection from the asyncpg pool and set the PostgreSQL
    session variable ``app.current_company_id`` so that RLS policies
    automatically filter every subsequent query to this tenant's rows.

    The variable is set with ``set_config(..., true)`` (transaction-local),
    so it cannot leak to the next request that borrows the same physical
    connection from the pool.

    Always use this instead of ``get_async_conn()`` in authenticated route
    handlers.  Never pass company_id from the request body — always obtain
    it from the validated JWT/session via ``request.state.company_id``.

    Example::
        async with get_async_tenant_conn(request.state.company_id) as conn:
            rows = await conn.fetch("SELECT * FROM invoices ORDER BY created_at DESC")
            return [dict(r) for r in rows]
    """
    if not company_id:
        raise ValueError("company_id must not be empty — cannot set tenant context")
    pool = await get_async_pool()
    async with pool.acquire() as conn:
        # TRUE = transaction-local scope; resets on connection return to pool
        await conn.execute(
            "SELECT set_config('app.current_company_id', $1, TRUE)",
            company_id,
        )
        yield conn


@asynccontextmanager
async def get_async_tenant_transaction(company_id: str) -> "AsyncIterator[Any]":
    """
    Like ``get_async_tenant_conn`` but also wraps the work in an explicit
    transaction.  Use for multi-statement writes that must be atomic.

    Example::
        async with get_async_tenant_transaction(request.state.company_id) as conn:
            await conn.execute("INSERT INTO journal_entries ...", ...)
            await conn.execute("UPDATE accounts SET balance=...", ...)
    """
    if not company_id:
        raise ValueError("company_id must not be empty — cannot set tenant context")
    pool = await get_async_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_company_id', $1, TRUE)",
                company_id,
            )
            yield conn


# ── SQLAlchemy ORM session context managers ────────────────────────

def _get_async_engine():
    """Return (or lazily create) the shared SQLAlchemy async engine."""
    global _async_engine
    if not _SQLA_ASYNC_AVAILABLE:
        raise RuntimeError("SQLAlchemy async drivers not installed")
    assert create_async_engine is not None
    if _async_engine is None:
        dsn = _get_dsn()
        sa_pool_size = int(os.environ.get("DB_SQLA_POOL_SIZE", "5"))
        sa_max_overflow = int(os.environ.get("DB_SQLA_MAX_OVERFLOW", "10"))
        sa_pool_timeout = int(os.environ.get("DB_SQLA_POOL_TIMEOUT", "30"))
        sa_pool_recycle = int(os.environ.get("DB_SQLA_POOL_RECYCLE", "1800"))
        _async_engine = create_async_engine(
            dsn,
            pool_size=sa_pool_size,
            max_overflow=sa_max_overflow,
            pool_timeout=sa_pool_timeout,
            pool_recycle=sa_pool_recycle,
            json_serializer=lambda d: d,  # use asyncpg's native JSON handling
            json_deserializer=lambda d: d,
            connect_args={"server_settings": _server_settings()},
        )
        logger.info(
            "SQLAlchemy async engine initialised (pool_size=%d, max_overflow=%d)",
            sa_pool_size,
            sa_max_overflow,
        )
    return _async_engine


def get_async_session_factory() -> Any:
    """Return (or lazily create) the shared SQLAlchemy session factory."""
    global _async_session_factory
    if not _SQLA_ASYNC_AVAILABLE:
        raise RuntimeError("SQLAlchemy async drivers not installed")
    assert async_sessionmaker is not None
    if _async_session_factory is None:
        engine = _get_async_engine()
        _async_session_factory = async_sessionmaker(
            bind=engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _async_session_factory


@asynccontextmanager
async def get_async_session() -> "AsyncIterator[Any]":
    """
    Provide a transactional SQLAlchemy AsyncSession.
    Commits on clean exit, rolls back on exception.

    Example::
        from sqlalchemy import select
        from orm_models import User

        async with get_async_session() as session:
            result = await session.execute(select(User).where(User.id == 1))
            user = result.scalar_one_or_none()
    """
    factory = get_async_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_sqlalchemy_engine():
    """Gracefully dispose of the SQLAlchemy engine's connection pool."""
    global _async_engine
    if _async_engine:
        await _async_engine.dispose()
        _async_engine = None
        logger.info("SQLAlchemy async engine disposed")
