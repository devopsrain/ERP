#!/usr/bin/env python3
"""
RDS Schema Verification Test
Connects to the live AWS RDS instance and verifies every expected table
exists in the ethiopian_business database.

Usage:
    # Option A — set env var directly:
    $env:DATABASE_URL = "postgresql://admin:SecurePassword123!@<RDS_ENDPOINT>:5432/ethiopian_business"
    python test_rds_tables.py

    # Option B — write a .env.rds file (never commit it):
    # DATABASE_URL=postgresql://admin:SecurePassword123!@<RDS_ENDPOINT>:5432/ethiopian_business
    python test_rds_tables.py

    # Option C — pass endpoint as CLI arg (password from env/file):
    python test_rds_tables.py --endpoint <RDS_ENDPOINT>

    # Get the endpoint from Terraform:
    # cd Deployed/aws-deployment && terraform output database_endpoint

Requirements:
    pip install psycopg2-binary python-dotenv
"""

import os
import sys
import argparse
import time

# ── Load .env.rds if present (add to .gitignore!) ────────────────
_env_file = os.path.join(os.path.dirname(__file__), '.env.rds')
try:
    from dotenv import load_dotenv
    if os.path.exists(_env_file):
        load_dotenv(_env_file)
        print(f"  Loaded credentials from .env.rds")
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────────
# Expected Tables
# Group → [table_name, ...]
# Add new tables here as they are scaffolded.
# ─────────────────────────────────────────────────────────────────
EXPECTED_TABLES = {
    'Auth / Users': [
        'users',
        'login_history',
    ],
    'Multitenancy': [
        'tenants',
        'licenses',
        'license_audit',
    ],
    'VAT / Accounting': [
        'vat_income',
        'vat_expenses',
        'vat_capital',
        'chart_of_accounts',
        'journal_entries',
        'transactions',
        'flagged_accounts',
    ],
    'CPO': [
        'cpo_records',
    ],
    'Inventory': [
        'inventory_items',
        'inventory_categories',
        'inventory_movements',
        'inventory_requisitions',
    ],
    'Sales / CRM': [
        'sales_contacts',   # added this session
    ],
}

# ─────────────────────────────────────────────────────────────────
# Optional extra checks
# ─────────────────────────────────────────────────────────────────
COLUMN_CHECKS = {
    # table_name → expected_columns (subset — not exhaustive)
    'sales_contacts': ['contact_id', 'full_name', 'email', 'tier_interest', 'submitted_at', 'is_read'],
    'users':          ['id', 'username', 'email', 'privilege_level'],
    'tenants':        ['tenant_id', 'company_name'],
    'licenses':       ['license_id', 'tenant_id', 'tier'],
}

# ─────────────────────────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────────────────────────
GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
CYAN   = '\033[96m'
RESET  = '\033[0m'
BOLD   = '\033[1m'

def ok(msg):   print(f"  {GREEN}✓{RESET}  {msg}")
def fail(msg): print(f"  {RED}✗{RESET}  {msg}")
def warn(msg): print(f"  {YELLOW}!{RESET}  {msg}")
def info(msg): print(f"  {CYAN}·{RESET}  {msg}")

# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description='Verify RDS tables for Ethiopian Business app')
    p.add_argument('--endpoint', help='RDS endpoint host (overrides DATABASE_URL host)')
    p.add_argument('--db',       default='ethiopian_business', help='Database name')
    p.add_argument('--user',     default='admin',  help='DB user')
    p.add_argument('--password', default=None,     help='DB password (defaults to env DB_PASSWORD or prompts)')
    p.add_argument('--port',     default=5432, type=int)
    p.add_argument('--timeout',  default=10,   type=int, help='Connection timeout seconds')
    return p.parse_args()


def build_dsn(args):
    """Resolve connection string: DATABASE_URL env var wins, else args."""
    url = os.environ.get('DATABASE_URL')
    if url:
        return url, '(from DATABASE_URL env var)'

    endpoint = args.endpoint
    if not endpoint:
        print(f"\n{RED}ERROR{RESET}: No DATABASE_URL set and --endpoint not provided.\n")
        print("  Get your RDS endpoint:")
        print("    cd Deployed/aws-deployment")
        print("    terraform output database_endpoint\n")
        print("  Then either:")
        print("    $env:DATABASE_URL = \"postgresql://admin:SecurePassword123!@<endpoint>:5432/ethiopian_business\"")
        print("    python test_rds_tables.py\n")
        print("  Or:  python test_rds_tables.py --endpoint <endpoint>\n")
        sys.exit(1)

    password = args.password or os.environ.get('DB_PASSWORD') or 'SecurePassword123!'
    dsn = f"postgresql://{args.user}:{password}@{endpoint}:{args.port}/{args.db}"
    return dsn, f'(built from --endpoint {endpoint})'


def get_existing_tables(cursor):
    cursor.execute("""
        SELECT table_name
        FROM   information_schema.tables
        WHERE  table_schema = 'public'
          AND  table_type   = 'BASE TABLE'
        ORDER BY table_name;
    """)
    return {row[0] for row in cursor.fetchall()}


def get_columns(cursor, table_name):
    cursor.execute("""
        SELECT column_name
        FROM   information_schema.columns
        WHERE  table_schema = 'public'
          AND  table_name   = %s
        ORDER BY ordinal_position;
    """, (table_name,))
    return {row[0] for row in cursor.fetchall()}


def run_checks(cursor, existing_tables):
    total_passed = total_failed = 0

    print(f"\n{BOLD}  Table existence checks{RESET}")
    print(f"  {'─'*55}")

    for group, tables in EXPECTED_TABLES.items():
        print(f"\n  {CYAN}{group}{RESET}")
        for tbl in tables:
            if tbl in existing_tables:
                ok(f"{tbl:<40}")
                total_passed += 1
            else:
                fail(f"{tbl:<40}  ← MISSING")
                total_failed += 1

    # Column-level checks
    print(f"\n{BOLD}  Column checks (key tables){RESET}")
    print(f"  {'─'*55}")

    for tbl, expected_cols in COLUMN_CHECKS.items():
        if tbl not in existing_tables:
            warn(f"Skipping column check for {tbl} (table missing)")
            continue
        actual_cols = get_columns(cursor, tbl)
        missing_cols = [c for c in expected_cols if c not in actual_cols]
        if missing_cols:
            fail(f"{tbl}: missing columns → {', '.join(missing_cols)}")
            total_failed += 1
        else:
            ok(f"{tbl}: all expected columns present")
            total_passed += 1

    # Extra tables (not in our list — informational)
    all_expected_flat = {t for tables in EXPECTED_TABLES.values() for t in tables}
    extra = existing_tables - all_expected_flat - set(COLUMN_CHECKS.keys())
    if extra:
        print(f"\n  {YELLOW}Extra tables in DB (not in spec — OK){RESET}")
        for t in sorted(extra):
            info(t)

    return total_passed, total_failed


def main():
    args = parse_args()
    dsn, dsn_source = build_dsn(args)

    # Mask password in display
    import re
    display_dsn = re.sub(r'://([^:]+):([^@]+)@', r'://\1:****@', dsn)

    print(f"\n{BOLD}{'='*65}{RESET}")
    print(f"{BOLD}  RDS Schema Verification — Ethiopian Business{RESET}")
    print(f"{BOLD}{'='*65}{RESET}")
    print(f"\n  Connecting to: {display_dsn}")
    print(f"  Source:        {dsn_source}")

    try:
        import psycopg2
    except ImportError:
        print(f"\n{RED}psycopg2 not installed.{RESET}  Run:  pip install psycopg2-binary\n")
        sys.exit(1)

    start = time.time()
    try:
        conn = psycopg2.connect(dsn, connect_timeout=args.timeout)
        conn.autocommit = True
        cursor = conn.cursor()
        elapsed = time.time() - start
        print(f"  Connected in {elapsed:.2f}s")
    except psycopg2.OperationalError as e:
        print(f"\n{RED}Connection failed:{RESET} {e}")
        print("\n  Troubleshooting:")
        print("  1. Is the RDS instance running?  → AWS Console → RDS → Instances")
        print("  2. Is your IP in the security group inbound rules? (port 5432)")
        print("  3. Correct endpoint?  → terraform output database_endpoint")
        print("  4. Is the EC2 server running?  Route traffic through it or open RDS publicly.\n")
        sys.exit(2)

    # DB version info
    cursor.execute("SELECT version();")
    pg_version = cursor.fetchone()[0].split(',')[0]
    info(f"PostgreSQL: {pg_version}")

    # Collect existing tables
    existing_tables = get_existing_tables(cursor)
    info(f"Tables found in 'public' schema: {len(existing_tables)}")

    # Run checks
    passed, failed = run_checks(cursor, existing_tables)

    cursor.close()
    conn.close()

    # Summary
    print(f"\n{BOLD}{'='*65}{RESET}")
    total = passed + failed
    print(f"  {GREEN}{passed} passed{RESET}  {RED}{failed} failed{RESET}  ({total} checks)")

    if failed == 0:
        print(f"\n  {GREEN}{BOLD}All tables and columns verified OK!{RESET}")
        print(f"\n  The RDS database is ready for the Ethiopian Business app.")
    else:
        print(f"\n  {RED}{BOLD}{failed} check(s) failed.{RESET}")
        print(f"\n  To create missing tables, run init_db.sql on the server:")
        print(f"    SSH to EC2, then:")
        print(f"    PGPASSWORD=SecurePassword123! psql -h <RDS_ENDPOINT> \\")
        print(f"      -U admin -d ethiopian_business -f /opt/accounting/init_db.sql")
    print(f"{BOLD}{'='*65}{RESET}\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
