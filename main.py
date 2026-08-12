#!/usr/bin/env python3
"""
Root entry point for FastAPI verification and app servers.
Imports app from ui.server.
"""
import sys, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "ui"))

from ui.server import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=7842, reload=True)
