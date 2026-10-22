"""Compatibility wrapper for the historical ``python packaging\\launcher.py`` command."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from atlas_app.launcher import _fatal, main  # noqa: E402


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        _fatal(f"{type(exc).__name__}: {exc}")
        raise SystemExit(1)
