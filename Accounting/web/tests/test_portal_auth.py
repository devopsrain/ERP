"""
Pure unit tests for the portal authentication helpers (web/portal_auth.py).

No database, no app: token generation/expiry, CSRF verification, the login
rate limiter, account lockout arithmetic, password policy reuse and the
session identity helpers.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import portal_auth as pa  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def _request(session=None, path="/portal/customer/invoices", query="", headers=None):
    return SimpleNamespace(
        session=session if session is not None else {},
        url=SimpleNamespace(path=path, query=query, scheme="https", netloc="portal.example.com"),
        headers=headers or {},
        client=SimpleNamespace(host="203.0.113.7"),
    )


# ── tokens ────────────────────────────────────────────────────────

def test_generate_token_is_urlsafe_and_unique():
    a, b = pa.generate_token(), pa.generate_token()
    assert a != b
    assert len(a) >= 40                      # token_urlsafe(32) -> 43 chars
    assert all(c.isalnum() or c in "-_" for c in a)


def test_invite_and_reset_expiry_windows():
    now = datetime(2026, 9, 12, 10, 0, 0)
    assert pa.invite_expiry(now) == now + timedelta(hours=72)
    assert pa.reset_expiry(now) == now + timedelta(hours=1)


@pytest.mark.parametrize("expires,now,expected", [
    (datetime(2026, 1, 1, 12), datetime(2026, 1, 1, 11), True),
    (datetime(2026, 1, 1, 12), datetime(2026, 1, 1, 12), False),
    (datetime(2026, 1, 1, 12), datetime(2026, 1, 1, 13), False),
    ("2026-01-01T12:00:00", datetime(2026, 1, 1, 11, 59), True),
    ("not-a-date", datetime(2026, 1, 1), False),
    (None, datetime(2026, 1, 1), False),
    ("", datetime(2026, 1, 1), False),
])
def test_token_is_valid(expires, now, expected):
    assert pa.token_is_valid(expires, now) is expected


def test_tokens_match_constant_time_and_none_safe():
    assert pa.tokens_match("abc", "abc")
    assert not pa.tokens_match("abc", "abd")
    assert not pa.tokens_match(None, "abc")
    assert not pa.tokens_match("abc", None)
    assert not pa.tokens_match("", "")


# ── CSRF ─────────────────────────────────────────────────────────

def test_csrf_token_created_once_per_session_under_portal_key():
    req = _request()
    t1 = pa.get_csrf_token(req)
    t2 = pa.get_csrf_token(req)
    assert t1 == t2 and len(t1) == 64
    assert req.session[pa.SESSION_CSRF_KEY] == t1
    assert "_csrf" not in req.session          # never touches the staff key


def test_require_csrf_accepts_matching_hidden_field():
    req = _request()
    tok = pa.get_csrf_token(req)
    pa.require_csrf(req, {"csrf_token": tok})   # no raise


def test_require_csrf_accepts_header_fallback():
    req = _request()
    tok = pa.get_csrf_token(req)
    req.headers = {"X-CSRFToken": tok}
    pa.require_csrf(req, {})


@pytest.mark.parametrize("form", [{}, {"csrf_token": ""}, {"csrf_token": "wrong"}, None])
def test_require_csrf_rejects_missing_or_wrong(form):
    req = _request()
    pa.get_csrf_token(req)
    with pytest.raises(HTTPException) as ei:
        pa.require_csrf(req, form)
    assert ei.value.status_code == 403


def test_require_csrf_rejects_when_session_has_no_token():
    req = _request()
    with pytest.raises(HTTPException):
        pa.require_csrf(req, {"csrf_token": "anything"})


def test_staff_csrf_uses_staff_session_key():
    req = _request(session={"_csrf": "staff-token"})
    pa.require_staff_csrf(req, {"csrf_token": "staff-token"})
    with pytest.raises(HTTPException):
        pa.require_staff_csrf(req, {"csrf_token": "portal-token"})


# ── rate limiter ─────────────────────────────────────────────────

class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_rate_limiter_blocks_after_five_and_recovers_after_window():
    clock = _Clock()
    rl = pa.LoginRateLimiter(max_attempts=5, window_seconds=900, clock=clock)
    key = "ip:1.2.3.4"
    for _ in range(4):
        rl.hit(key)
    assert not rl.is_blocked(key)
    rl.hit(key)
    assert rl.is_blocked(key)
    assert 0 < rl.retry_after(key) <= 900
    clock.t += 899
    assert rl.is_blocked(key)
    clock.t += 2                      # oldest hit slides out of the window
    assert not rl.is_blocked(key)
    assert rl.retry_after(key) == 0


def test_rate_limiter_keys_are_independent_and_resettable():
    rl = pa.LoginRateLimiter(max_attempts=2, window_seconds=60, clock=_Clock())
    rl.hit("a"); rl.hit("a")
    assert rl.is_blocked("a") and not rl.is_blocked("b")
    rl.reset("a")
    assert not rl.is_blocked("a")


def test_rate_limit_keys_normalise_email():
    ip_key, email_key = pa.rate_limit_keys("10.0.0.1", "  Bob@Example.COM ")
    assert ip_key == "ip:10.0.0.1"
    assert email_key == "email:bob@example.com"
    assert pa.rate_limit_keys("", "") == ("ip:-", "email:-")


def test_module_defaults_match_policy():
    assert pa.RATE_LIMIT_ATTEMPTS == 5
    assert pa.RATE_LIMIT_WINDOW_SECONDS == 15 * 60
    assert pa.login_limiter.max_attempts == 5
    assert pa.login_limiter.window == 900


# ── account lockout ──────────────────────────────────────────────

def test_next_failure_state_locks_on_tenth_failure():
    now = datetime(2026, 9, 12, 9, 0)
    n, until = pa.next_failure_state(8, now)
    assert (n, until) == (9, None)
    n, until = pa.next_failure_state(9, now)
    assert n == 10
    assert until == now + timedelta(minutes=pa.ACCOUNT_LOCK_MINUTES)
    assert pa.is_locked(until, now)
    assert not pa.is_locked(until, until + timedelta(seconds=1))
    assert pa.next_failure_state(None, now)[0] == 1


# ── password policy / hashing reuse ──────────────────────────────

@pytest.mark.parametrize("pw,ok", [
    ("short1A", False),
    ("alllowercase1", False),
    ("ALLUPPERCASE1", False),
    ("NoDigitsHere", False),
    ("GoodPassw0rd", True),
])
def test_validate_password_policy(pw, ok):
    assert pa.validate_password(pw)[0] is ok


def test_fallback_policy_matches_staff_rules():
    for pw in ("short1A", "alllowercase1", "ALLUPPERCASE1", "NoDigitsHere", "GoodPassw0rd"):
        assert pa._fallback_validate_password(pw)[0] == pa.validate_password(pw)[0]


def test_hash_and_verify_roundtrip_is_bcrypt():
    h = pa.hash_password("GoodPassw0rd")
    assert h.startswith("$2")
    assert pa.verify_password("GoodPassw0rd", h)
    assert not pa.verify_password("GoodPassw0rd!", h)
    assert not pa.verify_password("", h)
    assert not pa.verify_password("x", "")


# ── session identity & dependency ────────────────────────────────

def _user_row():
    return {"id": "u1", "company_id": "acme", "kind": "supplier", "email": "s@x.com",
            "full_name": "Sue", "org_name": "Supply Co", "party_key": "Supply Co",
            "password_hash": "$2b$secret", "tin": "123"}


def test_login_session_stores_json_safe_subset_under_portal_key():
    req = _request(session={"logged_in": True, "username": "staff", "_csrf": "s"})
    pa.get_csrf_token(req)
    pa.login_session(req, _user_row())
    u = req.session[pa.SESSION_USER_KEY]
    assert u["id"] == "u1" and u["kind"] == "supplier" and u["party_key"] == "Supply Co"
    assert "password_hash" not in u and "tin" not in u
    assert req.session["_rotate"] is True
    assert pa.SESSION_CSRF_KEY not in req.session          # fresh token after login
    # staff keys are untouched
    assert req.session["logged_in"] is True and req.session["username"] == "staff"
    assert req.session["_csrf"] == "s"


def test_logout_only_removes_portal_keys():
    req = _request(session={"logged_in": True, "_csrf": "s"})
    pa.login_session(req, _user_row())
    pa.flash(req, "hi")
    pa.logout_session(req)
    for k in (pa.SESSION_USER_KEY, pa.SESSION_CSRF_KEY, pa.SESSION_FLASH_KEY):
        assert k not in req.session
    assert req.session["logged_in"] is True


def test_portal_user_dependency_redirects_anonymous_to_login_with_next():
    req = _request(path="/portal/supplier/orders", query="page=2")
    with pytest.raises(HTTPException) as ei:
        pa.portal_user(req)
    assert ei.value.status_code == 302
    loc = ei.value.headers["Location"]
    assert loc.startswith("/portal/login?next=")
    assert "supplier" in loc and "page" in loc


def test_portal_user_dependency_ignores_staff_session():
    req = _request(session={"logged_in": True, "user_id": "x", "username": "admin"})
    with pytest.raises(HTTPException):
        pa.portal_user(req)


def test_kind_specific_dependencies():
    req = _request()
    pa.login_session(req, _user_row())            # supplier
    assert pa.portal_supplier(req)["id"] == "u1"
    with pytest.raises(HTTPException) as ei:
        pa.portal_customer(req)
    assert ei.value.headers["Location"] == "/portal/"


def test_current_portal_user_rejects_malformed_payload():
    assert pa.current_portal_user(_request(session={pa.SESSION_USER_KEY: "junk"})) is None
    assert pa.current_portal_user(_request(session={pa.SESSION_USER_KEY: {"id": "1", "kind": "admin"}})) is None


@pytest.mark.parametrize("nxt,expected", [
    ("/portal/customer/invoices", "/portal/customer/invoices"),
    ("/auth/users", "/portal/"),
    ("https://evil.example/portal/", "/portal/"),
    ("//evil.example/portal/", "/portal/"),
    ("", "/portal/"),
    (None, "/portal/"),
])
def test_safe_next(nxt, expected):
    assert pa.safe_next(nxt) == expected


def test_flash_roundtrip_reassigns_key():
    req = _request()
    pa.flash(req, "Saved", "success")
    pa.flash(req, "Oops", "error")
    assert pa.pop_flashes(req) == [("success", "Saved"), ("error", "Oops")]
    assert pa.pop_flashes(req) == []
    assert "_flash" not in req.session


def test_client_ip_prefers_forwarded_header():
    req = _request(headers={"X-Forwarded-For": "198.51.100.9, 10.0.0.1"})
    assert pa.client_ip(req) == "198.51.100.9"
    assert pa.client_ip(_request()) == "203.0.113.7"


def test_external_base_url_env_override(monkeypatch):
    monkeypatch.setenv("PORTAL_BASE_URL", "https://portal.acme.et/")
    assert pa.external_base_url(_request()) == "https://portal.acme.et"
    monkeypatch.delenv("PORTAL_BASE_URL")
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    req = _request(headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "ebms.example.com"})
    assert pa.external_base_url(req) == "https://ebms.example.com"
