"""
Async version of the Authentication & Authorization Data Store.

Uses asyncpg and SQLAlchemy for non-blocking database operations,
making it suitable for use in FastAPI's async route handlers.
"""
import uuid
import logging
from datetime import datetime
from typing import Optional

from async_db import get_async_tenant_conn, get_async_session
from orm_models import User, LoginHistory, Role, UserRole
from auth_data_store import (
    _hash_password, _verify_password, _is_legacy_hash,
    MAX_FAILED_LOGIN_ATTEMPTS, ACCOUNT_LOCKOUT_MINUTES
)
from sqlalchemy import select, func, delete

logger = logging.getLogger(__name__)


class AsyncAuthDataStore:
    """Async PostgreSQL-backed user authentication and authorization store."""

    async def get_user_by_username(self, username: str) -> Optional[dict]:
        """Fetch a user by username, returning a dict or None."""
        async with get_async_session() as session:
            result = await session.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            return user.to_dict() if user else None

    async def validate_credentials(self, username: str, password: str) -> Optional[dict]:
        """
        Validate username/password. On success, return user dict. On fail, return None.
        Handles account locking and transparently upgrades legacy password hashes.
        """
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

    async def update_password_hash(self, username: str, new_hash: str):
        """Update a user's password hash."""
        async with get_async_session() as session:
            result = await session.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            if user:
                user.password_hash = new_hash
                await session.commit()

    async def increment_failed_logins(self, username: str):
        """Increment failed login counter and lock account if threshold is met."""
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
        async with get_async_session() as session:
            result = await session.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            if user:
                user.failed_login_attempts = 0
                user.account_locked_until = None
                await session.commit()

    async def log_login_event(self, username: str, ip_address: str, success: bool, company_id: str):
        """Log a login attempt to the login_history table."""
        async with get_async_session() as session:
            event = LoginHistory(
                username=username,
                ip_address=ip_address,
                success=success,
                company_id=company_id,
                timestamp=datetime.utcnow()
            )
            session.add(event)
            await session.commit()

    async def create_api_token(self, user_id: str, company_id: str, expires_in_days: int = 30) -> dict:
        """Create and store a new API token."""
        # This is a simplified example. A real implementation would involve
        # creating and storing a secure token.
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
        # This is a simplified example.
        async with get_async_conn() as conn: # No tenant context needed for token lookup
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
