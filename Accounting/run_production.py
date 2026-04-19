#!/usr/bin/env python3
"""Production entry point — start with: uvicorn run_production:app"""
import logging
import os
import sys
from pathlib import Path

# ── Bootstrap logging early so ALL startup messages are structured ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_log = logging.getLogger("run_production")

# Load .env BEFORE importing the FastAPI app so all env vars are set
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        load_dotenv(env_path, override=True)
        _log.info("Loaded environment from: %s", env_path)
    else:
        _log.warning(".env file not found at %s", env_path)
except ImportError:
    _log.warning("python-dotenv not installed, skipping .env loading")

# ── Pre-flight checks — log critical env vars ──
_db_url = os.environ.get("DATABASE_URL", "")
if _db_url:
    # Log host/dbname only (never the password)
    _safe = _db_url.split("@")[-1] if "@" in _db_url else "(set)"
    _log.info("DATABASE_URL target: %s", _safe)
else:
    _log.error("DATABASE_URL is NOT set — the app will fail on first DB query")

if not os.environ.get("FLASK_SECRET_KEY"):
    _log.warning("FLASK_SECRET_KEY not set — sessions will use an ephemeral key")

_redis = os.environ.get("REDIS_URL", "")
if _redis:
    _log.info("REDIS_URL target: %s", _redis.split("@")[-1] if "@" in _redis else "(set)")
else:
    _log.info("REDIS_URL not set — will use in-memory cache fallback")

# Put project root and web/ on sys.path so bare imports inside web/ resolve
project_root = Path(__file__).parent.absolute()
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / 'web'))

# Change working directory to web/ so relative data-store paths resolve correctly
os.chdir(str(project_root / 'web'))

# Import the FastAPI application object
_log.info("Importing FastAPI app from web/app.py ...")
try:
    from app import app  # noqa: E402  (web/app.py)
    _log.info("FastAPI app imported successfully")
except Exception as _import_err:
    _log.error("FATAL: Failed to import FastAPI app: %s", _import_err, exc_info=True)
    sys.exit(1)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=5000, log_level='info')
