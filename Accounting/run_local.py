#!/usr/bin/env python3
"""
Local dev server (FastAPI / uvicorn) - runs the app on http://localhost:5000/

Usage:
    cd C:/Users/fde/AzureDevops/Accounting
    .venv-1/Scripts/Activate.ps1
    python run_local.py

Configure database by editing web/.env (see web/.env.example).
Without DATABASE_URL the app starts but DB-dependent pages will error.
"""

import os
import re
import sys

# -- Resolve paths
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR   = os.path.join(REPO_ROOT, "web")

# -- Load credentials
try:
    from dotenv import load_dotenv
    for _f in [os.path.join(WEB_DIR, ".env"), os.path.join(REPO_ROOT, ".env")]:
        if os.path.exists(_f):
            load_dotenv(_f, override=False)
            print(f"  Loaded {_f}")
            break
except ImportError:
    print("  dotenv not installed -- skipping .env load")

# -- Safe defaults for local dev
os.environ.setdefault("FLASK_SECRET_KEY", "local-dev-only-not-for-production")

# -- Ensure "web" is importable
sys.path.insert(0, WEB_DIR)
os.chdir(WEB_DIR)

if __name__ == "__main__":
    import uvicorn

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("\n  WARNING: DATABASE_URL not set.")
        print("  Pages that hit the database will return errors.")
        print("  Set it in web/.env -- see web/.env.example for format.\n")
    else:
        safe = re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", db_url)
        print(f"\n  DATABASE_URL: {safe}")

    print("\n  Starting FastAPI dev server on http://localhost:5000/")
    print("  API docs available at http://localhost:5000/api/docs")
    print("  Press Ctrl+C to stop.\n")

    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=5000,
        reload=False,
        log_level="info",
    )
