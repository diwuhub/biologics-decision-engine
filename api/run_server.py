"""
Run the Biologics Decision Engine API server.

Usage:
    python3 api/run_server.py
    # or equivalently:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""

import os
import sys

# Ensure project root is importable
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import uvicorn

if __name__ == "__main__":
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
