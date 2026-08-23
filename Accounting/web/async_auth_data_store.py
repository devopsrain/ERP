"""
Async Authentication data store with safe transition support.

Strategy:
- Hot login path uses native async SQL via asyncpg.
- Lower-frequency admin operations are offloaded to a thread using run_sync.

This keeps behavior stable while removing event-loop blocking on the
highest-traffic authentication flows.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from auth_data_store import (
    ACCOUNT_LOCKOUT_MINUTES,
    MAX_FAILED_LOGIN_ATTEMPTS,
    _hash_password,
    _is_legacy_hash,
    _validate_password_policy,
    _verify_password,
    auth_store as _sync_store,
)
from db import run_sync

logger = logging.getLogger(__name__)

_ASYNC_AVAILABLE = False
try:
    from async_db import get_async_conn
    _ASYNC_AVAILABLE = True
except Exception as exc:
    logger.warning(
        "async_auth_data_store: async DB unavailable (%s). Falling back to run_sync wrappers.",
        exc,
    )


class AsyncAuthDataStore:
    async def _get_user_row(self, username_or_email: str) -> Optional[dict]:
        if not _ASYNC_AVAILABLE:
            return None
        async with get_async_conn() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE username = $1 OR email = $1",
                username_or_email,
            )
            return dict(row) if row else None

    @staticmethod
    def _parse_locked_until(value) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except Exception:
                return None
        return None

    async def validate_credentials(self, username: str, password: str) -> Optional[dict]:
        """
        Authenticate user with native async SQL on the hot login path.
        Falls back to sync auth through run_sync when async DB is unavailable.
        """
        if not _ASYNC_AVAILABLE:
            return await run_sync(_sync_store.authenticate, username, password)

        user = await self._get_user_row(username)
        if not user:
            return None

        if not user.get("is_active", True):
            return None

        locked_until = self._parse_locked_until(user.get("locked_until"))
        if locked_until and datetime.utcnow() < locked_until:
            logger.warning("Login blocked: account %s locked until %s", username, locked_until)
            return None

        if not _verify_password(password, user.get("password_hash", "")):
            failed = int(user.get("failed_login_count") or 0) + 1
            lock_value = user.get("locked_until") or ""
            if failed >= MAX_FAILED_LOGIN_ATTEMPTS:
                lock_value = (datetime.utcnow() + timedelta(minutes=ACCOUNT_LOCKOUT_MINUTES)).isoformat()
            async with get_async_conn() as conn:
                await conn.execute(
                    """
                    UPDATE users
                    SET failed_login_count = $1,
                        locked_until = $2
                    WHERE user_id = $3
                    """,
                    failed,
                    lock_value,
                    user["user_id"],
                )
            return None

        new_hash = None
        if _is_legacy_hash(user.get("password_hash", "")):
            new_hash = _hash_password(password)

        async with get_async_conn() as conn:
            if new_hash:
                await conn.execute(
                    """
                    UPDATE users
                    SET password_hash = $1,
                        last_login = $2,
                        login_count = COALESCE(login_count, 0) + 1,
                        failed_login_count = 0,
                        locked_until = ''
                    WHERE user_id = $3
                    """,
                    new_hash,
                    datetime.utcnow().isoformat(),
                    user["user_id"],
                )
            else:
                await conn.execute(
                    """
                    UPDATE users
                    SET last_login = $1,
                        login_count = COALESCE(login_count, 0) + 1,
                        failed_login_count = 0,
                        locked_until = ''
                    WHERE user_id = $2
                    """,
                    datetime.utcnow().isoformat(),
                    user["user_id"],
                )

        user.pop("password_hash", None)
        return user

    async def log_login_event(self, username: str, ip_address: str, success: bool, company_id: str = "default"):
        if not _ASYNC_AVAILABLE:
            return
        try:
            async with get_async_conn() as conn:
                urow = await conn.fetchrow("SELECT user_id FROM users WHERE username = $1", username)
                if not urow:
                    return
                user_id = urow["user_id"]
                now_iso = datetime.utcnow().isoformat()
                login_id = str(uuid.uuid4())
                ua = ""
                device_name = "Unknown"
                try:
                    await conn.execute(
                        """
                        INSERT INTO login_history
                        (login_id, user_id, username, timestamp, ip_address, user_agent, device_name)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        login_id,
                        user_id,
                        username,
                        now_iso,
                        ip_address or "unknown",
                        ua,
                        device_name,
                    )
                except Exception:
                    await conn.execute(
                        """
                        INSERT INTO login_history
                        (login_id, user_id, username, timestamp, ip_address, user_agent)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        """,
                        login_id,
                        user_id,
                        username,
                        now_iso,
                        ip_address or "unknown",
                        ua,
                    )
        except Exception as exc:
            logger.warning("log_login_event failed (non-critical): %s", exc)

    async def get_auth_stats(self) -> dict:
        return await run_sync(_sync_store.get_auth_stats)

    async def get_all_users(self) -> list:
        return await run_sync(_sync_store.get_all_users)

    async def get_login_history(self, limit: int = 100) -> list:
        return await run_sync(_sync_store.get_login_history, limit)

    async def create_user(
        self,
        username: str,
        password: str,
        full_name: str,
        email: str,
        phone: str = "",
        privilege_level: str = "viewer",
        company_id: str = "default",
    ) -> dict:
        return await run_sync(
            _sync_store.create_user,
            username,
            password,
            full_name,
            email,
            phone,
            privilege_level,
            company_id,
        )

    async def update_user(self, user_id: str, **kwargs) -> bool:
        return await run_sync(_sync_store.update_user, user_id, **kwargs)

    async def set_user_company(self, user_id: str, company_id: str) -> bool:
        return await run_sync(_sync_store.set_user_company, user_id, company_id)

    async def change_password(self, user_id: str, current_password: str, new_password: str) -> dict:
        return await run_sync(_sync_store.change_password, user_id, current_password, new_password)

    async def reset_password(self, user_id: str, new_password: str) -> bool:
        return await run_sync(_sync_store.reset_password, user_id, new_password)

    async def toggle_user_active(self, user_id: str) -> bool:
        return await run_sync(_sync_store.toggle_user_active, user_id)

    async def delete_user(self, user_id: str) -> bool:
        return await run_sync(_sync_store.delete_user, user_id)

    async def get_user_tokens(self, user_id: str) -> list:
        return await run_sync(_sync_store.list_api_tokens, user_id)

    async def create_api_token(self, user_id: str, label: str, expires_days: int = None) -> dict:
        # Current sync store ignores expires_days. Keep API compatibility.
        return await run_sync(_sync_store.create_api_token, user_id, label, expires_days)

    async def revoke_token(self, token_id: str, owner_id: str) -> bool:
        return await run_sync(_sync_store.revoke_api_token, token_id, owner_id)

    async def create_password_reset_token(self, email: str) -> Optional[str]:
        return await run_sync(_sync_store.create_password_reset_token, email)

    async def validate_reset_token(self, token: str) -> Optional[dict]:
        return await run_sync(_sync_store.validate_reset_token, token)

    async def reset_password_with_token(self, token: str, new_password: str) -> bool:
        ok, _ = _validate_password_policy(new_password)
        if not ok:
            return False
        return await run_sync(_sync_store.reset_password_with_token, token, new_password)


async_auth_store = AsyncAuthDataStore()
