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
from typing import TYPE_CHECKING, AsyncIterator

logger = logging.getLogger(__name__)

# ── asyncpg (raw async pool) ──────────────────────────────────────
try:
    import asyncpg
    _ASYNCPG_AVAILABLE = True
except ImportError:
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
    _SQLA_ASYNC_AVAILABLE = False
    logger.warning(
        "SQLAlchemy async drivers unavailable.  "
        "Run: pip install sqlalchemy asyncpg"
    )

_pool: "asyncpg.Pool | None" = None
_pool_lock = asyncio.Lock()

_async_engine = None
_async_session_factory = None


# ── Pool lifecycle ─────────────────────────────────────────────────

def _get_dsn() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Export it before starting the app."
        )
    # asyncpg requires postgresql:// not postgres://
    return url.replace("postgres://", "postgresql://")


async def _init_asyncpg_pool() -> "asyncpg.Pool":
    global _pool
    dsn = _get_dsn()
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=2,
        max_size=20,
        command_timeout=30,
        statement_cache_size=0,   # required for PgBouncer compatibility
    )
    logger.info("asyncpg connection pool initialised (min=2, max=20)")
    return _pool


async def get_async_pool() -> "asyncpg.Pool":
    """Return (or lazily create) the shared asyncpg pool."""
    if not _ASYNCPG_AVAILABLE:
        raise RuntimeError("asyncpg is not installed. Run: pip install asyncpg")
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                await _init_asyncpg_pool()
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
async def get_async_conn() -> "AsyncIterator[asyncpg.Connection]":
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
async def get_async_transaction() -> "AsyncIterator[asyncpg.Connection]":
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


# ── Context managers — SQLAlchemy async ORM ───────────────────────

def _get_async_session_factory():
    global _async_engine, _async_session_factory
    if _async_session_factory is None:
        if not _SQLA_ASYNC_AVAILABLE:
            raise RuntimeError(
                "SQLAlchemy async not available. Run: pip install sqlalchemy asyncpg"
            )
        dsn = _get_dsn().replace("postgresql://", "postgresql+asyncpg://")
        _async_engine = create_async_engine(
            dsn,
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            echo=False,
        )
        _async_session_factory = async_sessionmaker(
            bind=_async_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _async_session_factory


@asynccontextmanager
async def get_async_session() -> "AsyncIterator[AsyncSession]":
    """
    Open a SQLAlchemy AsyncSession (ORM-level queries with orm_models.py).
    Commits on clean exit; rolls back and re-raises on exception.

    Example::
        from async_db import get_async_session
        from orm_models import User
        from sqlalchemy import select

        async with get_async_session() as session:
            result = await session.execute(
                select(User).where(User.username == "alice")
            )
            user = result.scalar_one_or_none()
    """
    factory = _get_async_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
