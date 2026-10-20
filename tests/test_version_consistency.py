"""发行版本必须在运行时、Python 元数据和 Inno Setup 中一致。"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from backend.config import APP_VERSION


ROOT = Path(__file__).resolve().parents[1]


def test_release_version_sources_match():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project_version = tomllib.load(handle)["project"]["version"]

    installer = (ROOT / "packaging" / "Atlas.iss").read_text(encoding="utf-8")
    match = re.search(r'^\s*#define\s+MyAppVersion\s+"([^\"]+)"\s*$', installer, re.MULTILINE)
    assert match, "packaging/Atlas.iss must define MyAppVersion"

    assert project_version == APP_VERSION
    assert match.group(1) == APP_VERSION
