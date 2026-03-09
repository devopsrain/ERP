"""
Test Runner — Ethiopian Accounting System
==========================================
Run the full test suite with reporting and summary.

Usage:
    cd web
    python run_tests.py              # Run all tests
    python run_tests.py --smoke      # Quick smoke tests only
    python run_tests.py --unit       # Unit tests only
    python run_tests.py --auth       # Auth tests only
    python run_tests.py --integration # Integration tests only
    python run_tests.py -k "payroll" # Filter by keyword
"""
import subprocess
import sys
import os
import argparse
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(description='Run Ethiopian Accounting System tests')
    parser.add_argument('--smoke', action='store_true', help='Run smoke tests only')
    parser.add_argument('--unit', action='store_true', help='Run unit tests only')
    parser.add_argument('--auth', action='store_true', help='Run auth tests only')
    parser.add_argument('--integration', action='store_true', help='Run integration tests only')
    parser.add_argument('--routes', action='store_true', help='Run route health tests only')
    parser.add_argument('-k', '--keyword', type=str, help='Filter tests by keyword')
    parser.add_argument('--html', action='store_true', help='Generate HTML report (requires pytest-html)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    args = parser.parse_args()

    # Ensure we're in the web/ directory
    web_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(web_dir)

    # Set required env vars
    env = os.environ.copy()
    env.setdefault('FLASK_SECRET_KEY', 'test-secret-key-for-pytest')
    env.setdefault('DEFAULT_ADMIN_PASSWORD', 'admin123')
    env.setdefault('DEFAULT_HR_PASSWORD', 'hr123')
    env.setdefault('DEFAULT_ACCOUNTANT_PASSWORD', 'acc123')
    env.setdefault('DEFAULT_EMPLOYEE_PASSWORD', 'emp123')
    env.setdefault('DEFAULT_DATA_ENTRY_PASSWORD', 'data123')

    # Build pytest command
    cmd = [sys.executable, '-m', 'pytest']

    # Marker filters
    markers = []
    if args.smoke:
        markers.append('smoke')
    if args.unit:
        markers.append('unit')
    if args.auth:
        markers.append('auth')
    if args.integration:
        markers.append('integration')
    if args.routes:
        markers.append('routes')

    if markers:
        cmd.extend(['-m', ' or '.join(markers)])

    if args.keyword:
        cmd.extend(['-k', args.keyword])

    # Output options
    cmd.extend(['-v', '--tb=short', '-ra'])

    # HTML report
    if args.html:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_path = os.path.join('tests', f'report_{timestamp}.html')
        cmd.extend([f'--html={report_path}', '--self-contained-html'])

    # Summary header
    print('=' * 70)
    print('  ETHIOPIAN ACCOUNTING SYSTEM — TEST SUITE')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'  Command: {" ".join(cmd)}')
    print('=' * 70)
    print()

    # Run
    result = subprocess.run(cmd, env=env, cwd=web_dir)

    print()
    print('=' * 70)
    if result.returncode == 0:
        print('  ALL TESTS PASSED')
    else:
        print(f'  SOME TESTS FAILED (exit code: {result.returncode})')
    print('=' * 70)

    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
