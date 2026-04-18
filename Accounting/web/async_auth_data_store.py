"""
Async version of the Authentication & Authorization Data Store.

Uses asyncpg and SQLAlchemy for non-blocking database operations,
making it suitable for use in FastAPI's async route handlers.

Falls back to the synchronous auth_store when SQLAlchemy or asyncpg
are not available (e.g. the server hasn't been upgraded yet), so that
auth_routes.py always loads and /auth/login never 404s.
"""
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Optional async dependencies ────────────────────────────────
_ASYNC_AVAILABLE = False
try:
    from async_db import get_async_tenant_conn, get_async_session
    from orm_models import User, LoginHistory, Role, UserRole
    from sqlalchemy import select, func, delete
    _ASYNC_AVAILABLE = True
except Exception as _e:
    logger.warning(
        "async_auth_data_store: async drivers unavailable (%s). "
        "Falling back to synchronous auth_store. "
        "Run: pip install sqlalchemy[asyncio] asyncpg", _e
    )

from auth_data_store import (
    auth_store as _sync_store,
    _hash_password, _verify_password, _is_legacy_hash,
    MAX_FAILED_LOGIN_ATTEMPTS, ACCOUNT_LOCKOUT_MINUTES
)


class AsyncAuthDataStore:
    """
    Async PostgreSQL-backed user authentication and authorization store.
    Automatically falls back to the synchronous auth_store when SQLAlchemy
    async drivers are not available, ensuring the login route always works.
    """

    async def get_user_by_username(self, username: str) -> Optional[dict]:
        """Fetch a user by username, returning a dict or None."""
        if not _ASYNC_AVAILABLE:
            # Sync store doesn't have get_user, but this path is only used
            # internally when async IS available. Return None as safe fallback.
            return None
        async with get_async_session() as session:
            result = await session.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            return user.to_dict() if user else None

    async def validate_credentials(self, username: str, password: str) -> Optional[dict]:
        """
        Validate username/password. On success, return user dict. On fail, return None.
        Falls back to synchronous auth_store.authenticate() when async is unavailable.
        """
        if not _ASYNC_AVAILABLE:
            return _sync_store.authenticate(username, password)

        user = await self.get_user_by_username(username)
        if not user:
            return None

        # Check for account lockout
        if user.get('failed_login_attempts', 0) >= MAX_FAILED_LOGIN_ATTEMPTS:
            locked_until = user.get('account_locked_until')
            if locked_until and locked_until > datetime.utcnow():
                logger.warning(
                    "Login failed: account %s is locked until %s",
                    username, locked_until.isoformat()
                )
                return None  # Account is locked

        # Verify password
        password_ok = _verify_password(password, user['password_hash'])

        if password_ok:
            # Reset failed attempts on success
            if user.get('failed_login_attempts', 0) > 0:
                await self.reset_failed_logins(username)

            # Transparently upgrade legacy hash
            if _is_legacy_hash(user['password_hash']):
                new_hash = _hash_password(password)
                await self.update_password_hash(username, new_hash)
                logger.info("Upgraded password hash for user %s", username)

            return user
        else:
            # Increment failed attempts on failure
            await self.increment_failed_logins(username)
            return None

    async def log_login_event(self, username: str, ip_address: str, success: bool, company_id: str = "default"):
        """Log a login attempt. Falls back silently when async is unavailable."""
        if not _ASYNC_AVAILABLE:
            # Sync fallback: auth_store logs via _log_login_history internally
            # No separate call needed - logging happens inside authenticate()
            return
        try:
            async with get_async_session() as session:
                entry = LoginHistory(
                    id=str(uuid.uuid4()),
                    username=username,
                    ip_address=ip_address,
                    success=success,
                    company_id=company_id,
                    created_at=datetime.utcnow(),
                )
                session.add(entry)
                await session.commit()
        except Exception as e:
            logger.warning("log_login_event failed (non-critical): %s", e)

    async def update_password_hash(self, username: str, new_hash: str):
        """Update a user's password hash."""
        if not _ASYNC_AVAILABLE:
            return  # Sync store handles this internally
        async with get_async_session() as session:
            result = await session.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            if user:
                user.password_hash = new_hash
                await session.commit()

    async def increment_failed_logins(self, username: str):
        """Increment failed login counter and lock account if threshold is met."""
        if not _ASYNC_AVAILABLE:
            return  # Sync store handles this internally
        async with get_async_session() as session:
            result = await session.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            if not user:
                return

            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            if user.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
                user.account_locked_until = datetime.utcnow() + timedelta(minutes=ACCOUNT_LOCKOUT_MINUTES)
                logger.warning(
                    "Account %s locked due to %d failed login attempts",
                    username, user.failed_login_attempts
                )
            await session.commit()

    async def reset_failed_logins(self, username: str):
        """Reset failed login counter and unlock account."""
        if not _ASYNC_AVAILABLE:
            return  # Sync store handles this internally
        async with get_async_session() as session:
            result = await session.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            if user:
                user.failed_login_attempts = 0
                user.account_locked_until = None
                await session.commit()

    async def create_api_token(self, user_id: str, company_id: str, expires_in_days: int = 30) -> dict:
        """Create and store a new API token."""
        if not _ASYNC_AVAILABLE:
            raise NotImplementedError("API tokens require async database support")
        async with get_async_tenant_conn(company_id) as conn:
            token = str(uuid.uuid4())
            expires_at = datetime.utcnow() + timedelta(days=expires_in_days)
            await conn.execute(
                """
                INSERT INTO api_tokens (token, user_id, company_id, expires_at)
                VALUES ($1, $2, $3, $4)
                """,
                token, user_id, company_id, expires_at
            )
            return {"token": token, "expires_at": expires_at.isoformat()}

    async def validate_api_token(self, token: str) -> Optional[dict]:
        """Validate an API token and return the associated user and tenant info."""
        if not _ASYNC_AVAILABLE:
            return None  # API tokens require async support
        async with get_async_tenant_conn("default") as conn:
            row = await conn.fetchrow(
                """
                SELECT t.user_id, t.company_id, u.username, u.privilege_level
                FROM api_tokens t
                JOIN users u ON t.user_id = u.user_id
                WHERE t.token = $1 AND t.expires_at > NOW() AND t.is_revoked = FALSE
                """,
                token
            )
            return dict(row) if row else None


# Instantiate a singleton for use across the application
async_auth_store = AsyncAuthDataStore()
