"""
Template render + unit tests — admin company assignment (registration→company gap).

Renders auth/users.html with route-accurate contexts (same harness pattern as
test_vat_templates.py) so the new per-user company dropdown fails the build
instead of 500ing in production, and unit-tests the pure company-options
builder used by the /auth/users routes. No database required.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from auth_routes import build_company_options  # noqa: E402

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))

PRIVILEGE_LEVELS = {"viewer": 10, "data_entry": 20, "operator": 30,
                    "manager": 40, "admin": 50, "super_admin": 99}
PRIVILEGE_DESCRIPTIONS = {k: k for k in PRIVILEGE_LEVELS}


def _base_ctx(path="/auth/users"):
    session = {"logged_in": True, "username": "admin", "full_name": "Admin",
               "privilege_level": "super_admin"}
    request = SimpleNamespace(
        url=SimpleNamespace(path=path, query=""),
        query_params=SimpleNamespace(get=lambda k, d=None: d),
        session=session, form=SimpleNamespace(),
    )
    return dict(
        request=request, session=session, url_for=lambda *a, **k: "#",
        csrf_token=lambda: "x", get_flashed_messages=lambda **k: [],
        static_url=lambda p: p, static_cdn_url="", app_version="1.0",
        current_company_id="default", current_tenant=None,
    )


def _user(username="alice", company_id="default", **kw):
    u = dict(user_id=f"uid-{username}", username=username, full_name=username.title(),
             email=f"{username}@example.et", phone="", privilege_level="viewer",
             is_active=True, created_at="2026-08-01T00:00:00",
             last_login="2026-08-20T09:00:00", login_count=3,
             failed_login_count=0, locked_until="", company_id=company_id)
    u.update(kw)
    return u


def _stats():
    return dict(total_users=2, active_users=2, locked_users=0, recent_logins=1)


def _history():
    return [dict(timestamp="2026-08-20T09:00:00", username="alice",
                 ip_address="10.0.0.1", user_agent="Mozilla/5.0 test agent")]


DEFAULT_ONLY = [{"company_id": "default", "company_name": "Default (unassigned)"}]
WITH_TENANTS = DEFAULT_ONLY + [
    {"company_id": "acme", "company_name": "Acme Trading PLC"},
    {"company_id": "beta", "company_name": "Beta Industries"},
]


def _users_ctx(users, companies):
    return dict(users=users, stats=_stats(), login_history=_history(),
                companies=companies, privilege_levels=PRIVILEGE_LEVELS,
                privilege_descriptions=PRIVILEGE_DESCRIPTIONS)


CASES = [
    # populated: tenants exist, users spread across companies (incl. unassigned)
    _users_ctx([_user("alice", "acme"), _user("bob", "default"),
                _user("carol", None)], WITH_TENANTS),
    # fresh install: no tenants — 'default' must still be offered
    _users_ctx([_user("alice")], DEFAULT_ONLY),
    # empty user table
    _users_ctx([], DEFAULT_ONLY),
]


@pytest.mark.parametrize("ctx", CASES, ids=["tenants", "default-only", "empty"])
def test_users_template_renders(ctx):
    html = env.get_template("auth/users.html").render(**_base_ctx(), **ctx)
    assert len(html) > 1000


def test_users_template_has_company_assignment_control():
    ctx = CASES[0]
    html = env.get_template("auth/users.html").render(**_base_ctx(), **ctx)
    # Column + per-row dropdown wired to the JSON endpoint
    for needle in ["<th>Company</th>", "company-select", "assignCompany(",
                   "/auth/users/' + userId + '/company",
                   "Acme Trading PLC", "Beta Industries",
                   "Default (unassigned)", "next login"]:
        assert needle in html, f"users.html missing {needle!r}"
    # user with company_id=None must fall back to 'default' being selected
    assert 'data-prev="default"' in html


def test_users_template_offers_default_without_tenants():
    html = env.get_template("auth/users.html").render(**_base_ctx(), **CASES[1])
    assert 'value="default"' in html
    assert "Default (unassigned)" in html


def test_users_template_keeps_stray_company_selectable():
    # A user assigned to a company that no longer exists in tenants must not
    # lose the assignment silently — the current value stays selected.
    ctx = _users_ctx([_user("dave", "ghost-co")], DEFAULT_ONLY)
    html = env.get_template("auth/users.html").render(**_base_ctx(), **ctx)
    assert 'value="ghost-co" selected' in html


# ── build_company_options: pure logic ─────────────────────────────

def test_options_empty_tenants_yields_default():
    assert build_company_options([]) == [
        {"company_id": "default", "company_name": "Default (unassigned)"}]


def test_options_none_tenants_yields_default():
    assert build_company_options(None) == [
        {"company_id": "default", "company_name": "Default (unassigned)"}]


def test_options_prepends_default_when_missing():
    opts = build_company_options([{"company_id": "acme", "company_name": "Acme"}])
    assert opts[0]["company_id"] == "default"
    assert opts[1] == {"company_id": "acme", "company_name": "Acme"}


def test_options_no_duplicate_default():
    opts = build_company_options([
        {"company_id": "default", "company_name": "Default Company"},
        {"company_id": "acme", "company_name": "Acme"},
    ])
    assert [o["company_id"] for o in opts] == ["default", "acme"]
    # tenant-provided display name wins over the synthetic one
    assert opts[0]["company_name"] == "Default Company"


def test_options_skips_blanks_and_dedupes():
    opts = build_company_options([
        {"company_id": "", "company_name": "Nameless"},
        {"company_id": "  ", "company_name": "Spaces"},
        {"company_id": "acme", "company_name": "Acme"},
        {"company_id": "acme", "company_name": "Acme Duplicate"},
        {"company_id": "beta", "company_name": None},
    ])
    assert [o["company_id"] for o in opts] == ["default", "acme", "beta"]
    # missing display name falls back to the id
    assert opts[-1]["company_name"] == "beta"
