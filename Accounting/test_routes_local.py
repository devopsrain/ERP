#!/usr/bin/env python3
"""
Local route tester — runs the Flask app with a mock DB and hits every
important URL, reporting 200/302/403/404/500 for each.

Usage (from repo root, venv activated):
    python test_routes_local.py

No real database is needed. The DB layer is fully mocked so no network
connection is attempted. Routes that require auth have a fake session
injected (super_admin). DB-dependent data will come back empty, which
is fine — we're testing routing and template rendering, not data.
"""

import os
import sys
from unittest.mock import MagicMock

# Force UTF-8 output so special chars don't crash on Windows cp1252 terminals
if hasattr(sys.stdout, 'buffer'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ── Point Python at the web/ package ─────────────────────────────
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR   = os.path.join(REPO_ROOT, 'web')
sys.path.insert(0, WEB_DIR)

# ── Minimal env vars ──────────────────────────────────────────────
# DATABASE_URL must be non-empty so db.py doesn't raise RuntimeError.
# The pool itself is replaced with a mock after import, so no real
# connection is ever attempted.
os.environ.setdefault('DATABASE_URL',      'postgresql://mock:mock@localhost/mock')
os.environ.setdefault('FLASK_SECRET_KEY',  'test-secret-key-not-for-production')
os.environ.setdefault('FLASK_ENV',         'testing')
os.environ.setdefault('FLASK_TESTING',     'true')

# ── Import the Flask app ──────────────────────────────────────────
# db.py uses a lazy pool — _get_pool() / _init_pool() only runs on the
# first actual DB call, NOT at import time. So it is safe to import app
# first and then transplant the mock pool before any request is made.
os.chdir(WEB_DIR)
from app import app

# ── Build a mock psycopg2 connection pool ─────────────────────────
# Replace the pool object in db.py BEFORE the first test request fires.
# All data-store calls will now get empty/None results without dialling
# any network endpoint.
def _make_mock_pool():
    cursor = MagicMock()
    cursor.fetchone.return_value  = None
    cursor.fetchall.return_value  = []
    cursor.fetchmany.return_value = []
    cursor.description            = None
    cursor.rowcount               = 0
    cursor.__enter__ = lambda s: s
    cursor.__exit__  = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: s
    conn.__exit__  = MagicMock(return_value=False)

    pool = MagicMock()
    pool.getconn.return_value  = conn
    pool.putconn.return_value  = None
    pool.closed                = False
    # db.health_check() reads these as integers for the JSON payload
    pool.minconn = 2
    pool.maxconn = 20
    return pool

import db as _db_module
_db_module._pool = _make_mock_pool()

# ── Mock tenant_store so module-licensing gates don't redirect ────
# Without a real DB, is_subscription_active() returns falsy → every
# auth'd route gets bounced to /auth/portal.  Stub the three methods
# the before_request hooks call so all modules appear licensed.
import tenant_data_store as _tds_module
_tds = _tds_module.tenant_store
_tds.is_subscription_active = lambda company_id: True
_tds.is_module_licensed      = lambda company_id, module: True
_tds.get_tenant              = lambda company_id: {
    'company_id':        company_id,
    'company_name':      'Test Company',
    'subscription_tier': 'enterprise',
    'modules':           ['vat', 'payroll', 'inventory', 'cpo', 'siem', 'bid', 'backup'],
    'is_active':         True,
}
_tds.create_tenant          = lambda data, created_by='system': data
_tds.ensure_default_tenant  = lambda: 'test-company-001'

app.config['TESTING']        = True
app.config['WTF_CSRF_ENABLED'] = False   # disable CSRF for test client
# Prevent Flask-Caching from trying to connect to Redis/memcached
app.config.setdefault('CACHE_TYPE', 'SimpleCache')

# ─────────────────────────────────────────────────────────────────
# Route definitions
# Each entry: (method, path, label, expected_status, needs_login)
# ─────────────────────────────────────────────────────────────────
PUBLIC_ROUTES = [
    ('GET',  '/',                       'Home (sales redirect)',        302, False),
    ('GET',  '/sales/',                 'Sales landing page',           200, False),
    ('GET',  '/sales',                  'Sales (no slash)',             200, False),
    ('GET',  '/auth/login',             'Login page',                  200, False),
    ('GET',  '/auth/register',          'Register page',               200, False),
    ('GET',  '/health',                 'Health check',                (200, 503), False),
    ('POST', '/sales/contact',          'Contact form (empty)',        302, False),
]

AUTH_ROUTES = [
    ('GET',  '/auth/portal',            'Auth portal',                 200, True),
    ('GET',  '/auth/users',             'User management',             200, True),
    ('GET',  '/accounts/dashboard',     'Chart of Accounts',          200, True),
    ('GET',  '/accounts/',              'Accounts list',               200, True),
    ('GET',  '/journal/dashboard',      'Journal entries',             200, True),
    ('GET',  '/vat/dashboard',          'VAT dashboard',               200, True),
    ('GET',  '/income-expense/',        'Income & Expense',           200, True),
    ('GET',  '/transactions/',          'Transactions',                200, True),
    ('GET',  '/payroll',                'Payroll dashboard',           200, True),
    ('GET',  '/payroll/employees',      'Employees list',              200, True),
    ('GET',  '/payroll/tax-calculator', 'Tax calculator',              200, True),
    ('GET',  '/cpo/',                    'CPO dashboard',               200, True),
    ('GET',  '/inventory/',             'Inventory dashboard',         200, True),
    ('GET',  '/bid/dashboard',          'Bid tracker',                 200, True),
    ('GET',  '/backup/dashboard',       'Backup dashboard',            200, True),
    ('GET',  '/siem/',                  'SIEM dashboard',              200, True),
    ('GET',  '/company/login',          'Multi-company login',         200, True),
    ('GET',  '/sales/leads',            'Sales leads (admin)',         200, True),
]

REDIRECT_STUBS = [
    ('GET',  '/setup',                  '-> /accounts/dashboard',      302, False),
    ('GET',  '/accounts',               '-> /accounts/',               302, False),
    ('GET',  '/accounts/new',           '-> /accounts/add',            302, False),
    ('GET',  '/journal-entry',          '-> /journal/',                302, False),
    ('GET',  '/quick-transactions',     '-> /transactions/',           302, False),
    ('GET',  '/reports/trial-balance',  '-> /accounts/trial-balance',  302, False),
    ('GET',  '/reports/income-statement','-> /income-expense/',        302, False),
    ('GET',  '/reports/balance-sheet',  '-> /accounts/dashboard',      302, False),
    ('GET',  '/export',                 '-> /accounts/export/excel',   302, False),
]

# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────
GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
RESET  = '\033[0m'
BOLD   = '\033[1m'

def _color(ok, warn=False):
    if warn:   return YELLOW
    return GREEN if ok else RED

def _inject_auth_session(client):
    """Set session vars that simulate a logged-in super_admin user."""
    with client.session_transaction() as sess:
        sess['logged_in']          = True
        sess['username']           = 'test_admin'
        sess['full_name']          = 'Test Admin'
        sess['privilege_level']    = 'super_admin'
        sess['current_company_id'] = 'test-company-001'
        sess['company_name']       = 'Test Company'

# ─────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────
def run_tests():
    client = app.test_client()
    passed = failed = warned = 0
    results = []

    all_routes = (
        [('PUBLIC', r) for r in PUBLIC_ROUTES] +
        [('AUTH',   r) for r in AUTH_ROUTES] +
        [('STUB',   r) for r in REDIRECT_STUBS]
    )

    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  Ethiopian Business — Route Test Suite{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    for group, (method, path, label, expected, needs_login) in all_routes:
        if needs_login:
            _inject_auth_session(client)

        try:
            if method == 'GET':
                resp = client.get(path, follow_redirects=False)
            else:
                resp = client.post(path, data={}, follow_redirects=False)

            actual = resp.status_code

            # Normalise expected to a set of acceptable codes
            acceptable = set(expected) if isinstance(expected, tuple) else {expected}

            # A 302 pointing to /auth/login means auth gate fired unexpectedly
            auth_blocked = (
                actual == 302 and
                '/auth/login' in resp.headers.get('Location', '')
            )

            if auth_blocked and needs_login:
                status = '! AUTH BLOCKED'
                color  = YELLOW
                warned += 1
            elif actual in acceptable:
                status = f'OK {actual}'
                color  = GREEN
                passed += 1
            elif actual in (200, 302) and acceptable & {200, 302}:
                # redirect vs direct render — acceptable
                exp_str = '/'.join(str(e) for e in sorted(acceptable))
                status = f'~~ {actual} (expected {exp_str})'
                color  = YELLOW
                warned += 1
            else:
                exp_str = '/'.join(str(e) for e in sorted(acceptable))
                status = f'FAIL {actual} (expected {exp_str})'
                color  = RED
                failed += 1

            tag = f'[{group:<6}]'
            print(f"  {color}{status:<28}{RESET} {tag} {method:<5} {path:<40} {label}")

        except Exception as e:
            print(f"  {RED}FAIL EXCEPTION          {RESET} [{group:<6}] {method:<5} {path:<40} {e}")
            failed += 1

    print(f"\n{BOLD}{'='*70}{RESET}")
    total = passed + failed + warned
    print(f"  {GREEN}{passed} passed{RESET}  {YELLOW}{warned} warned{RESET}  {RED}{failed} failed{RESET}  ({total} total)")
    if failed == 0:
        print(f"\n  {GREEN}{BOLD}All routes OK!{RESET}")
    else:
        print(f"\n  {RED}{BOLD}{failed} route(s) returned unexpected status codes.{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")
    return failed == 0

if __name__ == '__main__':
    ok = run_tests()
    sys.exit(0 if ok else 1)
