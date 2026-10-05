"""Atlas Windows launcher: local-only API + bundled web UI."""
from __future__ import annotations
import uvicorn
import os
from backend.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("FILEPILOT_PORT", "8000")), log_level="info")
