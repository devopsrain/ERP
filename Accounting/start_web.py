#!/usr/bin/env python3
"""
Direct Flask app runner for testing
"""
import os
import sys

# Add the project root to the Python path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

if __name__ == "__main__":
    print("Starting Flask application...")
    try:
        from web.app import app
        print("Flask app imported successfully!")
        print("Starting server on http://localhost:5000")
        app.run(debug=True, host='localhost', port=5000)
    except Exception as e:
        print(f"Error starting Flask app: {e}")
        import traceback
        traceback.print_exc()