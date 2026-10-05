"""Checks used by the desktop app before exposing destructive actions."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from . import config


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".filepilot_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def check() -> dict:
    state_ok = _writable(config.STATE_DIR)
    trash_ok = _writable(config.TRASH_DIR)
    temp_ok = _writable(Path(tempfile.gettempdir()))
    return {
        "ok": state_ok and trash_ok and temp_ok,
        "platform": os.name,
        "python": f"{os.sys.version_info.major}.{os.sys.version_info.minor}",
        "state_dir": str(config.STATE_DIR),
        "trash_dir": str(config.TRASH_DIR),
        "checks": {
            "state_writable": state_ok,
            "trash_writable": trash_ok,
            "temp_writable": temp_ok,
        },
    }
