#!/usr/bin/env python3
"""Production entry point — start with: uvicorn run_production:app"""
import os
import sys
from pathlib import Path

# Load .env BEFORE importing the FastAPI app so all env vars are set
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        load_dotenv(env_path, override=True)
        print(f"Loaded environment from: {env_path}")
    else:
        print(f"Warning: .env file not found at {env_path}")
except ImportError:
    print("Warning: python-dotenv not installed, skipping .env loading")

# Put project root and web/ on sys.path so bare imports inside web/ resolve
project_root = Path(__file__).parent.absolute()
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / 'web'))

# Change working directory to web/ so relative data-store paths resolve correctly
os.chdir(str(project_root / 'web'))

# Import the FastAPI application object
from web.app import api as app  # noqa: E402  (web/app.py)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=5000, log_level='info')
