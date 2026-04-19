"""
User Authentication & Authorization Data Store — PostgreSQL backend

Persistent PostgreSQL-backed user management with:
- Username/password authentication (bcrypt, with legacy SHA-256 upgrade)
- Role-based privilege levels
- Login history tracking
- Session management helpers
- SIEM integration (auto-logs auth events)
"""

import uuid
import os
import hashlib
import logging
import re
import bcrypt
from datetime import datetime
from typing import Optional

from db import get_cursor, get_conn

logger = logging.getLogger(__name__)

# ── Security Constants ──────────────────────────────────────────
MAX_FAILED_LOGIN_ATTEMPTS = 5      # lock account after N failures
ACCOUNT_LOCKOUT_MINUTES = 30       # how long the account stays locked
MIN_PASSWORD_LENGTH = 12           # minimum password characters (NIST SP 800-63B)


def _validate_password_policy(password: str, username: str = "") -> tuple[bool, str]:
    """Validate password strength policy."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if not re.search(r"[A-Z]", password):
        return False, "Password must include at least one uppercase letter"
    if not re.search(r"[a-z]", password):
        return False, "Password must include at least one lowercase letter"
    if not re.search(r"\d", password):
        return False, "Password must include at least one number"
    if not re.search(r"[^A-Za-z0-9]", password):
        return False, "Password must include at least one special character"
    if username and username.lower() in password.lower():
        return False, "Password must not contain your username"
    return True, ""

# ── Privilege Levels ──────────────────────────────────────────────
# Higher number = more privileges
PRIVILEGE_LEVELS = {
    'viewer':       10,   # Read-only access
    'data_entry':   20,   # Can enter data (add records)
    'operator':     30,   # Can import/export, run reports
    'manager':      40,   # Can manage employees, approve
    'admin':        50,   # Full access to a module
    'super_admin':  99,   # Full access to everything
}

PRIVILEGE_DESCRIPTIONS = {
    'viewer':       'View dashboards and reports only',
    'data_entry':   'Add and edit records',
    'operator':     'Import/export data, run reports',
    'manager':      'Manage employees, approve actions',
    'admin':        'Full module access',
    'super_admin':  'Full system access',
}

# Module permission requirements
MODULE_MIN_PRIVILEGE = {
    'vat':            'data_entry',
    'payroll':        'operator',
    'accounts':       'data_entry',
    'journal':        'data_entry',
    'income_expense': 'data_entry',
    'transaction':    'operator',
    'cpo':            'operator',
    'inventory':      'operator',
    'multicompany':   'viewer',
    'siem':           'admin',     # Only admins can view security logs
}


def _hash_password(password: str) -> str:
    """Hash password with bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, password_hash: str) -> bool:
    """Verify password against stored hash.

    Supports both bcrypt (new) and legacy SHA-256 hashes.
    If a legacy hash matches, return True so the caller can
    transparently re-hash with bcrypt.
    """
    # Try bcrypt first (hashes start with '$2b$')
    if password_hash.startswith('$2b$') or password_hash.startswith('$2a$'):
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    # Fall back to legacy SHA-256 for migration
    return hashlib.sha256(password.encode()).hexdigest() == password_hash


def _is_legacy_hash(password_hash: str) -> bool:
    """Check if the hash is a legacy SHA-256 that should be upgraded."""
    return not (password_hash.startswith('$2b$') or password_hash.startswith('$2a$'))


class AuthDataStore:
    """PostgreSQL-backed user authentication and authorization store."""

    def __init__(self):
        self._ensure_default_users()

    def _ensure_default_users(self):
        """Create default admin users if the users table is empty."""
        try:
            with get_cursor() as cur:
                cur.execute("SELECT COUNT(*) AS cnt FROM users")
                row = cur.fetchone()
                if row and row['cnt'] > 0:
                    return
        except Exception as e:
            logger.warning("Could not check users table: %s", e)
            return

        import secrets

        def _default_pw(env_var: str, fallback_length: int = 16) -> str:
            return os.environ.get(env_var) or secrets.token_urlsafe(fallback_length)

        admin_pw      = _default_pw('DEFAULT_ADMIN_PASSWORD')
        hr_pw         = _default_pw('DEFAULT_HR_PASSWORD')
        accountant_pw = _default_pw('DEFAULT_ACCOUNTANT_PASSWORD')
        employee_pw   = _default_pw('DEFAULT_EMPLOYEE_PASSWORD')
        data_pw       = _default_pw('DEFAULT_DATA_ENTRY_PASSWORD')

        seed_users = [
            ('admin',      admin_pw,      'System Administrator', 'admin@system.et',           '+251-11-999-0001', 'super_admin'),
            ('hr_manager', hr_pw,         'Almaz Tadesse',        'hr.manager@addistech.et',   '+251-11-555-1001', 'manager'),
            ('accountant', accountant_pw, 'Dawit Mengistu',       'accountant@addistech.et',   '+251-11-555-1002', 'operator'),
            ('employee1',  employee_pw,   'Meron Haile',          'employee1@addistech.et',    '+251-11-555-2001', 'viewer'),
            ('data_entry', data_pw,       'Hanan Ahmed',          'data@addistech.et',         '+251-11-555-3001', 'data_entry'),
        ]
        now = datetime.now().isoformat()
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for uname, pw, full_name, email, phone, privilege in seed_users:
                        cur.execute(
                            """INSERT INTO users
                               (user_id,username,password_hash,full_name,email,phone,
                                privilege_level,is_active,created_at,last_login,
                                login_count,failed_login_count,locked_until)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (username) DO NOTHING""",
                            (str(uuid.uuid4()), uname, _hash_password(pw),
                             full_name, email, phone, privilege, True,
                             now, '', 0, 0, '')
                        )
        except Exception as e:
            logger.error("Failed to seed default users: %s", e)
            return

        logger.warning('=== DEFAULT CREDENTIALS (first run) ===')
        logger.warning('  admin      : %s', admin_pw)
        logger.warning('  hr_manager : %s', hr_pw)
        logger.warning('  accountant : %s', accountant_pw)
        logger.warning('  employee1  : %s', employee_pw)
        logger.warning('  data_entry : %s', data_pw)
        logger.warning('Change these immediately or set DEFAULT_*_PASSWORD env vars.')
        logger.warning('=======================================')

    def authenticate(self, username: str, password: str, request=None) -> dict:
        """Authenticate by username/email. Returns user dict or None.
        Pass `request` (FastAPI Request) so that IP and User-Agent are captured
        correctly in the login history.
        """
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM users WHERE username=%s OR email=%s",
                    (username, username)
                )
                user = cur.fetchone()
        except Exception as e:
            logger.error("DB error during authenticate: %s", e)
            return None

        if not user:
            self._log_auth_event(username, success=False, reason='User not found', request=request)
            return None

        user = dict(user)

        if user.get('locked_until'):
            try:
                locked_until = datetime.fromisoformat(user['locked_until'])
                if datetime.now() < locked_until:
                    self._log_auth_event(username, success=False, reason='Account locked', request=request)
                    return None
                else:
                    self._update_user_fields(user['user_id'], locked_until='', failed_login_count=0)
            except (ValueError, TypeError):
                pass

        if not user.get('is_active', True):
            self._log_auth_event(username, success=False, reason='Account disabled', request=request)
            return None

        if not _verify_password(password, user['password_hash']):
            failed = int(user.get('failed_login_count') or 0) + 1
            updates = {'failed_login_count': failed}
            if failed >= MAX_FAILED_LOGIN_ATTEMPTS:
                from datetime import timedelta
                updates['locked_until'] = (
                    datetime.now() + timedelta(minutes=ACCOUNT_LOCKOUT_MINUTES)
                ).isoformat()
            self._update_user_fields(user['user_id'], **updates)
            self._log_auth_event(username, success=False, reason='Invalid password', request=request)
            return None

        if _is_legacy_hash(user['password_hash']):
            self._update_user_fields(user['user_id'], password_hash=_hash_password(password))
            logger.info("Upgraded password hash to bcrypt for '%s'", username)

        self._update_user_fields(
            user['user_id'],
            last_login=datetime.now().isoformat(),
            login_count=int(user.get('login_count') or 0) + 1,
            failed_login_count=0,
            locked_until='',
        )

        self._log_auth_event(username, success=True, request=request)
        self._log_login_history(user, request=request)
        user.pop('password_hash', None)
        return user

    def _update_user_fields(self, user_id: str, **kwargs):
        """Generic helper to UPDATE one or more columns for a user."""
        if not kwargs:
            return
        cols = ', '.join(f"{k} = %s" for k in kwargs)
        vals = list(kwargs.values()) + [user_id]
        try:
            with get_cursor() as cur:
                cur.execute(f"UPDATE users SET {cols} WHERE user_id = %s", vals)
        except Exception as e:
            logger.error("Failed to update user fields %s: %s", list(kwargs.keys()), e)

    def _log_auth_event(self, username: str, success: bool, reason: str = '', request=None):
        try:
            from siem_data_store import siem_store
            req_obj = request
            if req_obj is None:
                # No request context available (e.g. background task)
                return
            siem_store.log_upload_event(
                req_obj, module='auth', endpoint='/auth/login',
                filename='', status='success' if success else 'failed',
                user=username,
                details=f"Login {'successful' if success else 'failed'}: {reason}" if reason
                        else f"Login {'successful' if success else 'failed'}"
            )
        except Exception as e:
            logger.warning("SIEM logging failed during auth event: %s", e)

    def _log_login_history(self, user: dict, request=None):
        ip = 'unknown'
        user_agent = ''
        device_name = 'Unknown'
        try:
            if request is not None:
                # FastAPI Request
                forwarded = request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
                real_ip   = request.headers.get('X-Real-IP', '')
                client_ip = getattr(getattr(request, 'client', None), 'host', None) or ''
                ip        = forwarded or real_ip or client_ip or 'unknown'
                user_agent = request.headers.get('User-Agent', '')
        except Exception:
            pass

        # Derive device name from User-Agent
        ua = user_agent.lower()
        if 'iphone' in ua or 'android' in ua and 'mobile' in ua:
            device_name = 'Mobile Phone'
        elif 'ipad' in ua or 'tablet' in ua:
            device_name = 'Tablet'
        elif 'android' in ua:
            device_name = 'Android Device'
        elif 'windows' in ua:
            device_name = 'Windows PC'
        elif 'macintosh' in ua or 'mac os x' in ua:
            device_name = 'Mac'
        elif 'linux' in ua:
            device_name = 'Linux'
        else:
            device_name = 'Desktop' if user_agent else 'Unknown'

        try:
            with get_cursor() as cur:
                # Try inserting with device_name column; fall back without if column doesn't exist yet
                try:
                    cur.execute(
                        """INSERT INTO login_history
                           (login_id,user_id,username,timestamp,ip_address,user_agent,device_name)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (str(uuid.uuid4()), user['user_id'], user['username'],
                         datetime.now().isoformat(), ip, user_agent, device_name)
                    )
                except Exception:
                    cur.execute(
                        """INSERT INTO login_history
                           (login_id,user_id,username,timestamp,ip_address,user_agent)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (str(uuid.uuid4()), user['user_id'], user['username'],
                         datetime.now().isoformat(), ip, user_agent)
                    )
        except Exception as e:
            logger.warning("Failed to log login history: %s", e)

    # ── Session Helpers ───────────────────────────────────────────

    def set_session(self, user: dict, session=None):
        """Set session after successful authentication.
        Always pass a Starlette session dict (request.session) in FastAPI context.
        """
        if session is None:
            logger.warning("set_session called without a session object — no-op")
            return
        session['user_id'] = user['user_id']
        session['username'] = user['username']
        session['full_name'] = user.get('full_name', user['username'])
        session['privilege_level'] = user.get('privilege_level', 'viewer')
        session['logged_in'] = True
        if user.get('company_id'):
            session['current_company_id'] = user['company_id']

    def clear_session(self, session=None):
        """Clear session on logout. Pass the Starlette session dict."""
        if session is not None:
            session.clear()
            return
        logger.warning("clear_session called without a session object — no-op")

    def get_current_user(self, session: dict = None) -> dict:
        """Get currently logged-in user from a Starlette session dict."""
        if not session or not session.get('logged_in'):
            return None
        return {
            'user_id': session.get('user_id'),
            'username': session.get('username'),
            'full_name': session.get('full_name'),
            'privilege_level': session.get('privilege_level', 'viewer'),
        }

    def get_current_username(self, session: dict = None) -> str:
        """Get current username or 'anonymous'."""
        if not session:
            return 'anonymous'
        return session.get('username', 'anonymous')

    # ── Privilege Checks ──────────────────────────────────────────

    def has_privilege(self, required_level: str, session: dict = None) -> bool:
        """Check if current session user meets the required privilege level."""
        user_level = (session or {}).get('privilege_level', 'viewer')
        return PRIVILEGE_LEVELS.get(user_level, 0) >= PRIVILEGE_LEVELS.get(required_level, 0)

    def can_access_module(self, module: str, session: dict = None) -> bool:
        """Check if current session user can access a module."""
        required = MODULE_MIN_PRIVILEGE.get(module, 'viewer')
        return self.has_privilege(required, session)

    # ── User Management (Admin) ───────────────────────────────────

    def get_all_users(self) -> list:
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT user_id,username,full_name,email,phone,privilege_level,"
                    "is_active,created_at,last_login,login_count,failed_login_count,"
                    "locked_until FROM users ORDER BY username"
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_all_users failed: %s", e)
            return []

    def get_user_by_id(self, user_id: str) -> dict:
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT user_id,username,full_name,email,phone,privilege_level,"
                    "is_active,created_at,last_login,login_count,failed_login_count,"
                    "locked_until FROM users WHERE user_id=%s",
                    (user_id,)
                )
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_user_by_id failed: %s", e)
            return None

    def create_user(self, username: str, password: str, full_name: str,
                    email: str, phone: str = '', privilege_level: str = 'viewer',
                    company_id: str = 'default') -> dict:
        try:
            ok, policy_error = _validate_password_policy(password, username)
            if not ok:
                return {'success': False, 'error': policy_error}

            with get_cursor() as cur:
                cur.execute("SELECT user_id FROM users WHERE username=%s", (username,))
                if cur.fetchone():
                    return {'success': False, 'error': 'Username already exists'}
                if email:
                    cur.execute("SELECT user_id FROM users WHERE email=%s", (email,))
                    if cur.fetchone():
                        return {'success': False, 'error': 'Email already exists'}

            user_id = str(uuid.uuid4())
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO users
                       (user_id,username,password_hash,full_name,email,phone,
                        privilege_level,is_active,created_at,last_login,
                        login_count,failed_login_count,locked_until)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (user_id, username, _hash_password(password), full_name,
                     email, phone, privilege_level, True,
                     datetime.now().isoformat(), '', 0, 0, '')
                )
            return {'success': True, 'user_id': user_id}
        except Exception as e:
            logger.error("create_user failed: %s", e)
            return {'success': False, 'error': str(e)}

    def update_user(self, user_id: str, **kwargs) -> bool:
        kwargs.pop('user_id', None)
        kwargs.pop('password_hash', None)
        if not kwargs:
            return True
        try:
            self._update_user_fields(user_id, **kwargs)
            return True
        except Exception as e:
            logger.error("update_user failed: %s", e)
            return False

    def change_password(self, user_id: str, current_password: str, new_password: str) -> dict:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT username, password_hash FROM users WHERE user_id=%s", (user_id,))
                row = cur.fetchone()
                if not row:
                    return {'success': False, 'error': 'User not found'}

            if not _verify_password(current_password, row['password_hash']):
                return {'success': False, 'error': 'Current password is incorrect'}

            ok, policy_error = _validate_password_policy(new_password, row['username'])
            if not ok:
                return {'success': False, 'error': policy_error}

            self._update_user_fields(
                user_id,
                password_hash=_hash_password(new_password),
                failed_login_count=0,
                locked_until=''
            )
            return {'success': True}
        except Exception as e:
            logger.error("change_password failed: %s", e)
            return {'success': False, 'error': str(e)}

    def reset_password(self, user_id: str, new_password: str) -> bool:
        """Admin reset password with policy enforcement."""
        try:
            with get_cursor() as cur:
                cur.execute("SELECT username FROM users WHERE user_id=%s", (user_id,))
                row = cur.fetchone()
                if not row:
                    return False

            ok, _ = _validate_password_policy(new_password, row['username'])
            if not ok:
                return False

            self._update_user_fields(
                user_id,
                password_hash=_hash_password(new_password),
                failed_login_count=0,
                locked_until=''
            )
            return True
        except Exception as e:
            logger.error("reset_password failed: %s", e)
            return False

    def toggle_user_active(self, user_id: str) -> bool:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT is_active FROM users WHERE user_id=%s", (user_id,))
                row = cur.fetchone()
                if not row:
                    return False
                cur.execute(
                    "UPDATE users SET is_active=%s WHERE user_id=%s",
                    (not row['is_active'], user_id)
                )
            return True
        except Exception as e:
            logger.error("toggle_user_active failed: %s", e)
            return False

    def delete_user(self, user_id: str) -> bool:
        try:
            with get_cursor() as cur:
                cur.execute("DELETE FROM users WHERE user_id=%s", (user_id,))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("delete_user failed: %s", e)
            return False

    # ── Login History ─────────────────────────────────────────────

    def get_login_history(self, limit: int = 100) -> list:
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM login_history ORDER BY timestamp DESC LIMIT %s",
                    (limit,)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_login_history failed: %s", e)
            return []

    def get_user_login_history(self, user_id: str, limit: int = 50) -> list:
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM login_history WHERE user_id=%s "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (user_id, limit)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_user_login_history failed: %s", e)
            return []

    # ── Statistics ────────────────────────────────────────────────

    def get_auth_stats(self) -> dict:
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS total, "
                    "SUM(CASE WHEN is_active THEN 1 ELSE 0 END) AS active, "
                    "privilege_level FROM users GROUP BY privilege_level"
                )
                rows = cur.fetchall()

            total = sum(r['total'] for r in rows)
            active = sum(r['active'] or 0 for r in rows)
            priv = {r['privilege_level']: r['total'] for r in rows}

            with get_cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM login_history WHERE timestamp >= %s",
                    (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),)
                )
                recent = cur.fetchone()['cnt']

            return {
                'total_users': total,
                'active_users': active,
                'locked_users': 0,
                'privilege_breakdown': priv,
                'recent_logins': recent,
                'privilege_viewer': priv.get('viewer', 0),
                'privilege_data_entry': priv.get('data_entry', 0),
                'privilege_operator': priv.get('operator', 0),
                'privilege_manager': priv.get('manager', 0),
                'privilege_admin': priv.get('admin', 0),
                'privilege_super_admin': priv.get('super_admin', 0),
            }
        except Exception as e:
            logger.error("get_auth_stats failed: %s", e)
            return {
                'total_users': 0, 'active_users': 0, 'locked_users': 0,
                'privilege_breakdown': {}, 'recent_logins': 0,
                'privilege_viewer': 0, 'privilege_data_entry': 0,
                'privilege_operator': 0, 'privilege_manager': 0,
                'privilege_admin': 0, 'privilege_super_admin': 0,
            }

    # ── API Token Management ───────────────────────────────────────

    def create_api_token(self, user_id: str, label: str, expires_days: int = None) -> dict:
        """
        Generate a new API token for a user.

        Token format: "<token_id>.<secret>" where token_id (16 hex) is the
        lookup key and secret (64 hex) is hashed with SHA-256 for storage.
        Returns the raw token string (show once); None on failure.
        """
        import secrets as _sec
        import hashlib
        token_id    = _sec.token_hex(8)    # 16-char public lookup ID
        secret      = _sec.token_hex(32)   # 64-char secret
        raw_token   = f"{token_id}.{secret}"
        secret_hash = hashlib.sha256(secret.encode()).hexdigest()
        now = datetime.utcnow().isoformat()
        try:
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO api_tokens
                       (id, user_id, secret_hash, label, created_at, last_used_at, is_active)
                       VALUES (%s,%s,%s,%s,%s,'',TRUE)""",
                    (token_id, user_id, secret_hash, label, now)
                )
            logger.info("API token created: id=%s user_id=%s label=%s", token_id, user_id, label)
            return {"success": True, "token": raw_token, "token_id": token_id}
        except Exception as e:
            logger.error("create_api_token failed: %s", e)
            return {"success": False, "error": str(e)}

    def validate_api_token(self, raw_token: str) -> Optional[dict]:
        """
        Validate a Bearer token.  O(1) DB lookup by token_id + SHA-256 match.
        Returns the owning user dict (without password hash) or None.
        """
        import hashlib
        if not raw_token or '.' not in raw_token:
            return None
        token_id, _, secret = raw_token.partition('.')
        if not secret:
            return None
        secret_hash = hashlib.sha256(secret.encode()).hexdigest()
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT t.id AS token_id, t.secret_hash, t.is_active AS token_active,
                              u.user_id, u.username, u.full_name, u.privilege_level,
                              u.is_active AS user_active
                       FROM api_tokens t
                       JOIN users u ON u.user_id = t.user_id
                       WHERE t.id = %s AND t.is_active = TRUE AND u.is_active = TRUE""",
                    (token_id,)
                )
                row = cur.fetchone()
        except Exception as e:
            logger.error("validate_api_token DB error: %s", e)
            return None

        if not row or row['secret_hash'] != secret_hash:
            return None

        # Update last_used_at (best-effort, non-blocking)
        try:
            with get_cursor() as cur:
                cur.execute(
                    "UPDATE api_tokens SET last_used_at=%s WHERE id=%s",
                    (datetime.utcnow().isoformat(), token_id)
                )
        except Exception:
            pass

        return {
            'user_id':        row['user_id'],
            'username':       row['username'],
            'full_name':      row['full_name'],
            'privilege_level': row['privilege_level'],
        }

    # ── JWT helpers (mobile / SPA auth) ──────────────────────────

    @staticmethod
    def _jwt_secret() -> str:
        """Return the HS256 signing secret.  Requires JWT_SECRET env var in production."""
        s = os.environ.get("JWT_SECRET", "")
        if not s:
            # Development fallback — warn loudly so it's never silently used in prod.
            logger.warning(
                "JWT_SECRET env var not set — using insecure fallback. "
                "Set JWT_SECRET to a 32-byte random value in production."
            )
            s = "dev-only-insecure-jwt-secret-change-me"
        return s

    def issue_jwt_pair(self, user: dict, device_hint: str = "") -> dict:
        """
        Issue an access + refresh token pair for the given user.

        Access token:  JWT, signed HS256, expires in 15 minutes.
        Refresh token: Opaque random bytes, stored hashed in DB, expires in 30 days.

        Args:
            user: Dict containing at least user_id, username, privilege_level.
            device_hint: Optional description (e.g. "iPhone 15 / iOS 17").

        Returns:
            {access_token, refresh_token, token_type, expires_in}
        """
        from jose import jwt as _jwt
        import secrets as _sec
        from datetime import timedelta

        now = datetime.utcnow()
        access_exp = now + timedelta(minutes=15)
        refresh_exp = now + timedelta(days=30)

        access_payload = {
            "sub":             user["user_id"],
            "username":        user.get("username", ""),
            "privilege_level": user.get("privilege_level", "viewer"),
            "company_id":      user.get("company_id", "default"),
            "iat":             int(now.timestamp()),
            "exp":             int(access_exp.timestamp()),
            "type":            "access",
        }
        access_token = _jwt.encode(access_payload, self._jwt_secret(), algorithm="HS256")

        # Refresh token: opaque random bytes stored hashed in DB
        raw_refresh = _sec.token_hex(48)   # 96 hex chars, 48 bytes entropy
        token_hash  = hashlib.sha256(raw_refresh.encode()).hexdigest()
        token_id    = _sec.token_hex(16)   # used as primary key

        try:
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO refresh_tokens
                       (token_id, user_id, token_hash, issued_at, expires_at, device_hint)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (token_id, user["user_id"], token_hash,
                     now.isoformat(), refresh_exp.isoformat(), device_hint or ""),
                )
        except Exception as e:
            logger.error("issue_jwt_pair: failed to store refresh token: %s", e)
            raise

        # Surface format: token_id.raw_secret  (mirrors API token convention)
        refresh_token = f"{token_id}.{raw_refresh}"
        return {
            "access_token":  access_token,
            "refresh_token": refresh_token,
            "token_type":    "bearer",
            "expires_in":    900,   # seconds (15 min)
        }

    def refresh_access_token(self, raw_refresh: str) -> Optional[dict]:
        """
        Validate a refresh token and issue a new access + refresh pair (rotation).

        The old refresh token is revoked on success — one-time use enforced.

        Returns the same shape as issue_jwt_pair, or None on failure.
        """
        import hashlib as _hl
        if not raw_refresh or '.' not in raw_refresh:
            return None
        token_id, _, secret = raw_refresh.partition('.')
        if not secret:
            return None
        token_hash = _hl.sha256(secret.encode()).hexdigest()

        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT rt.token_id, rt.user_id, rt.token_hash, rt.expires_at,
                              rt.revoked_at, rt.device_hint,
                              u.username, u.privilege_level, u.is_active, u.company_id
                       FROM refresh_tokens rt
                       JOIN users u ON u.user_id = rt.user_id
                       WHERE rt.token_id = %s""",
                    (token_id,)
                )
                row = cur.fetchone()
        except Exception as e:
            logger.error("refresh_access_token DB error: %s", e)
            return None

        if not row:
            return None
        row = dict(row)

        # Integrity checks
        if row["token_hash"] != token_hash:
            return None
        if row["revoked_at"]:
            logger.warning("refresh_access_token: attempted reuse of revoked token %s", token_id)
            return None
        if not row["is_active"]:
            return None
        try:
            if datetime.fromisoformat(row["expires_at"]) < datetime.utcnow():
                return None
        except Exception:
            return None

        # Revoke the used token (rotation — prevents replay)
        try:
            with get_cursor() as cur:
                cur.execute(
                    "UPDATE refresh_tokens SET revoked_at=%s WHERE token_id=%s",
                    (datetime.utcnow().isoformat(), token_id)
                )
        except Exception as e:
            logger.error("refresh_access_token: revoke failed: %s", e)
            return None

        user = {
            "user_id":        row["user_id"],
            "username":       row["username"],
            "privilege_level": row["privilege_level"],
            "company_id":     row.get("company_id", "default"),
        }
        return self.issue_jwt_pair(user, device_hint=row.get("device_hint", ""))

    def revoke_refresh_token(self, raw_refresh: str, user_id: str) -> bool:
        """Revoke a specific refresh token.  user_id guards against cross-user revocation."""
        if not raw_refresh or '.' not in raw_refresh:
            return False
        token_id, _, secret = raw_refresh.partition('.')
        if not secret:
            return False
        try:
            with get_cursor() as cur:
                cur.execute(
                    """UPDATE refresh_tokens SET revoked_at=%s
                       WHERE token_id=%s AND user_id=%s AND revoked_at IS NULL""",
                    (datetime.utcnow().isoformat(), token_id, user_id)
                )
            return True
        except Exception as e:
            logger.error("revoke_refresh_token failed: %s", e)
            return False

    def validate_jwt(self, token: str) -> Optional[dict]:
        """
        Verify a JWT access token.  Returns the payload dict or None.
        Does NOT hit the database — rely on signature + expiry only.
        """
        from jose import jwt as _jwt, JWTError
        try:
            payload = _jwt.decode(token, self._jwt_secret(), algorithms=["HS256"])
            if payload.get("type") != "access":
                return None
            return payload
        except JWTError:
            return None

    def list_api_tokens(self, user_id: str) -> list:
        """List all API tokens for a user (without secret hashes)."""
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT id, label, created_at, last_used_at, is_active "
                    "FROM api_tokens WHERE user_id=%s ORDER BY created_at DESC",
                    (user_id,)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_api_tokens failed: %s", e)
            return []

    def revoke_api_token(self, token_id: str, user_id: str) -> bool:
        """Deactivate a token.  user_id prevents revoking another user's token."""
        try:
            with get_cursor() as cur:
                cur.execute(
                    "UPDATE api_tokens SET is_active=FALSE WHERE id=%s AND user_id=%s",
                    (token_id, user_id)
                )
            return True
        except Exception as e:
            logger.error("revoke_api_token failed: %s", e)
            return False


# ── Decorator ─────────────────────────────────────────────────────

def login_required(f=None, min_privilege='viewer'):
    """
    Decorator to require authentication and optionally a minimum privilege level.

    Usage:
        @login_required                         # any logged-in user
        @login_required(min_privilege='admin')   # admin+ only
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not session.get('logged_in'):
                flash('Please log in to continue.', 'warning')
                return redirect(url_for('auth.login'))

            if min_privilege != 'viewer':
                user_level = session.get('privilege_level', 'viewer')
                if PRIVILEGE_LEVELS.get(user_level, 0) < PRIVILEGE_LEVELS.get(min_privilege, 0):
                    flash(f'Access denied. Requires {min_privilege} privilege or higher.', 'error')
                    return redirect(url_for('auth.access_denied'))

            return func(*args, **kwargs)
        return wrapper

    if f is not None:
        # Called as @login_required without arguments
        return decorator(f)
    return decorator


    # ── Password Reset Token Management ───────────────────────────

    def create_password_reset_token(self, email: str) -> Optional[str]:
        """
        Generate a password reset token for a user by email.
        Returns the token string if successful, None if email not found.
        Token expires in 1 hour.
        """
        import secrets as _sec
        from datetime import timedelta
        
        try:
            with get_cursor() as cur:
                cur.execute("SELECT user_id, username FROM users WHERE email=%s", (email,))
                user = cur.fetchone()
                if not user:
                    logger.warning("Password reset requested for non-existent email: %s", email)
                    return None
                
                token = _sec.token_urlsafe(32)
                expires_at = (datetime.now() + timedelta(hours=1)).isoformat()
                
                # Store token (create table if needed)
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS password_reset_tokens (
                        id SERIAL PRIMARY KEY,
                        user_id VARCHAR(36),
                        token VARCHAR(255) UNIQUE,
                        expires_at TIMESTAMP,
                        used BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )"""
                )
                
                cur.execute(
                    """INSERT INTO password_reset_tokens (user_id, token, expires_at)
                       VALUES (%s, %s, %s)""",
                    (user['user_id'], token, expires_at)
                )
                
                logger.info("Password reset token created for user: %s", user['username'])
                return token
        except Exception as e:
            logger.error("create_password_reset_token failed: %s", e)
            return None

    def validate_reset_token(self, token: str) -> Optional[dict]:
        """
        Validate a password reset token.
        Returns user dict if valid, None if expired/invalid/already used.
        """
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT prt.user_id, prt.expires_at, prt.used, u.username, u.email
                       FROM password_reset_tokens prt
                       JOIN users u ON prt.user_id = u.user_id
                       WHERE prt.token = %s""",
                    (token,)
                )
                row = cur.fetchone()
                
                if not row:
                    return None
                
                if row['used']:
                    logger.warning("Attempted to reuse password reset token")
                    return None
                
                expires_at = datetime.fromisoformat(row['expires_at'])
                if datetime.now() > expires_at:
                    logger.warning("Expired password reset token attempted")
                    return None
                
                return dict(row)
        except Exception as e:
            logger.error("validate_reset_token failed: %s", e)
            return None

    def reset_password_with_token(self, token: str, new_password: str) -> bool:
        """
        Reset a user's password using a valid token.
        Marks the token as used and updates the password.
        """
        try:
            user = self.validate_reset_token(token)
            if not user:
                return False

            ok, _ = _validate_password_policy(new_password, user.get('username', ''))
            if not ok:
                return False
            
            with get_cursor() as cur:
                # Update password
                cur.execute(
                    "UPDATE users SET password_hash=%s, failed_login_count=0, locked_until='' WHERE user_id=%s",
                    (_hash_password(new_password), user['user_id'])
                )
                
                # Mark token as used
                cur.execute(
                    "UPDATE password_reset_tokens SET used=TRUE WHERE token=%s",
                    (token,)
                )
                
                logger.info("Password reset successful for user: %s", user['username'])
                return True
        except Exception as e:
            logger.error("reset_password_with_token failed: %s", e)
            return False


# Singleton instance
auth_store = AuthDataStore()

__all__ = [
    'auth_store',
    'login_required',
    'AuthDataStore',
    'PRIVILEGE_LEVELS',
    'PRIVILEGE_DESCRIPTIONS',
    'MODULE_MIN_PRIVILEGE',
    'MAX_FAILED_LOGIN_ATTEMPTS',
    'ACCOUNT_LOCKOUT_MINUTES',
    'MIN_PASSWORD_LENGTH',
]
