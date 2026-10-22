"""无副作用检查已安装的桌面启动器参数。"""
from __future__ import annotations

import sys

import pytest

from atlas_app import launcher
from backend.config import APP_VERSION


def test_launcher_version_prints_and_exits_without_starting_service(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["atlas", "--version"])

    with pytest.raises(SystemExit) as info:
        launcher.main()

    assert info.value.code == 0
    assert capsys.readouterr().out.strip() == f"Atlas {APP_VERSION}"


def test_launcher_help_prints_and_exits_without_starting_service(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["atlas", "--help"])

    with pytest.raises(SystemExit) as info:
        launcher.main()

    assert info.value.code == 0
    assert "--version" in capsys.readouterr().out
