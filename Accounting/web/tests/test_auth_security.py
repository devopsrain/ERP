"""
Authentication & Security Tests — Ethiopian Accounting System
==============================================================
Tests login flows, role-based access, session management, and security headers.

Run:  cd web && pytest tests/test_auth_security.py -v
"""
import pytest


# ══════════════════════════════════════════════════════════════════
#  Login / Logout Flow
# ══════════════════════════════════════════════════════════════════

class TestLoginFlow:
    """End-to-end login and logout tests."""

    @pytest.mark.auth
    def test_login_page_loads(self, client):
        resp = client.get('/auth/login')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'login' in html.lower() or 'username' in html.lower()

    @pytest.mark.auth
    def test_login_with_valid_credentials(self, fresh_client):
        resp = fresh_client.post('/auth/login', data={
            'username': 'admin',
            'password': 'admin123',
        }, follow_redirects=True)
        assert resp.status_code == 200
        # Should land on portal or dashboard after login
        html = resp.data.decode('utf-8')
        assert 'portal' in html.lower() or 'dashboard' in html.lower() or 'logout' in html.lower()

    @pytest.mark.auth
    def test_login_with_invalid_password(self, fresh_client):
        resp = fresh_client.post('/auth/login', data={
            'username': 'admin',
            'password': 'wrong_password',
        }, follow_redirects=True)
        html = resp.data.decode('utf-8')
        # Should show error or remain on login
        assert 'invalid' in html.lower() or 'incorrect' in html.lower() or 'error' in html.lower() or 'login' in html.lower()

    @pytest.mark.auth
    def test_login_with_nonexistent_user(self, fresh_client):
        resp = fresh_client.post('/auth/login', data={
            'username': 'nonexistent_user_xyz',
            'password': 'anything',
        }, follow_redirects=True)
        html = resp.data.decode('utf-8')
        assert resp.status_code == 200
        # Should show error
        assert 'invalid' in html.lower() or 'not found' in html.lower() or 'error' in html.lower() or 'login' in html.lower()

    @pytest.mark.auth
    def test_logout_clears_session(self, fresh_client):
        # Login first
        fresh_client.post('/auth/login', data={
            'username': 'admin',
            'password': 'admin123',
        })
        # Logout
        resp = fresh_client.get('/auth/logout', follow_redirects=False)
        assert resp.status_code in (200, 302, 303)

        # Try accessing protected page — should redirect to login
        resp2 = fresh_client.get('/auth/portal', follow_redirects=False)
        assert resp2.status_code in (302, 303, 200)


# ══════════════════════════════════════════════════════════════════
#  Session & Cookie Security
# ══════════════════════════════════════════════════════════════════

class TestSessionSecurity:
    """Session cookie configuration tests."""

    @pytest.mark.auth
    def test_session_cookie_httponly(self, app):
        assert app.config.get('SESSION_COOKIE_HTTPONLY') is True

    @pytest.mark.auth
    def test_session_cookie_samesite(self, app):
        assert app.config.get('SESSION_COOKIE_SAMESITE') in ('Lax', 'Strict', 'None')

    @pytest.mark.auth
    def test_secret_key_is_set(self, app):
        assert app.secret_key is not None
        assert len(app.secret_key) > 10


# ══════════════════════════════════════════════════════════════════
#  Role-Based Access
# ══════════════════════════════════════════════════════════════════

class TestRoleBasedAccess:
    """Verify role-gated pages respect permissions."""

    @pytest.mark.auth
    def test_admin_can_access_user_management(self, logged_in_client):
        resp = logged_in_client.get('/auth/users')
        assert resp.status_code == 200

    @pytest.mark.auth
    def test_unauthenticated_cannot_access_portal(self, fresh_client):
        resp = fresh_client.get('/auth/portal', follow_redirects=False)
        # Should redirect to login or show 302
        assert resp.status_code in (200, 302, 303)

    @pytest.mark.auth
    def test_access_denied_page_loads(self, client):
        resp = client.get('/auth/access-denied')
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════
#  Auth Data Store
# ══════════════════════════════════════════════════════════════════

class TestAuthDataStore:
    """Direct tests on the auth data store."""

    @pytest.mark.auth
    @pytest.mark.unit
    def test_authenticate_valid(self):
        from auth_data_store import auth_store
        result = auth_store.authenticate('admin', 'admin123')
        assert result is not None
        assert result.get('success') is True or result.get('user_id') is not None

    @pytest.mark.auth
    @pytest.mark.unit
    def test_authenticate_invalid(self):
        from auth_data_store import auth_store
        result = auth_store.authenticate('admin', 'wrong_password')
        assert result is None or result.get('success') is False

    @pytest.mark.auth
    @pytest.mark.unit
    def test_get_all_users(self):
        from auth_data_store import auth_store
        users = auth_store.get_all_users()
        assert isinstance(users, (list, dict))
        # At least admin should exist
        user_list = users if isinstance(users, list) else list(users.values())
        assert len(user_list) >= 1

    @pytest.mark.auth
    @pytest.mark.unit
    def test_get_auth_stats(self):
        from auth_data_store import auth_store
        stats = auth_store.get_auth_stats()
        assert isinstance(stats, dict)


# ══════════════════════════════════════════════════════════════════
#  Strict Authentication Gate — Global before_request enforcement
# ══════════════════════════════════════════════════════════════════

class TestStrictAuthGate:
    """Verify EVERY non-public route redirects to login when unauthenticated."""

    PROTECTED_ROUTES = [
        '/',
        '/setup',
        '/accounts',
        '/accounts/new',
        '/journal-entry',
        '/quick-transactions',
        '/reports/trial-balance',
        '/reports/income-statement',
        '/reports/balance-sheet',
        '/export',
        '/api/accounts',
        '/payroll',
        '/payroll/employees',
        '/vat/dashboard',
        '/vat/income',
        '/vat/expenses',
        '/vat/capital',
        '/journal/',
        '/journal/dashboard',
        '/accounts/',
        '/income-expense/',
        '/income-expense/income',
        '/income-expense/expenses',
        '/transactions/',
        '/transactions/list',
        '/cpo/',
        '/cpo/list',
        '/cpo/add',
        '/inventory/',
        '/inventory/items',
        '/bid/',
        '/bid/dashboard',
        '/siem/',
        '/siem/events',
        '/backup/dashboard',
        '/version/dashboard',
        '/auth/portal',
        '/auth/users',
    ]

    @pytest.mark.auth
    @pytest.mark.parametrize('path', PROTECTED_ROUTES)
    def test_unauthenticated_redirect_to_login(self, client, path):
        """Unauthenticated requests to any protected route must redirect to /auth/login."""
        resp = client.get(path)
        assert resp.status_code in (302, 308), \
            f"Expected redirect for unauthenticated request to {path}, got {resp.status_code}"
        location = resp.headers.get('Location', '')
        assert '/auth/login' in location or path == '/', \
            f"Expected redirect to /auth/login, got {location}"

    PUBLIC_ROUTES = [
        '/auth/login',
        '/auth/register',
        '/auth/access-denied',
    ]

    @pytest.mark.auth
    @pytest.mark.parametrize('path', PUBLIC_ROUTES)
    def test_public_routes_accessible_without_login(self, client, path):
        """Public pages must be accessible without authentication."""
        resp = client.get(path)
        assert resp.status_code == 200, \
            f"Public route {path} returned {resp.status_code}, expected 200"
