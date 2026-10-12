"""MCP server 安全边界单测（见 docs/MCP_SECURITY.md）。

不启动真实 stdio 进程，直接测 AllowList / TOOLS 元数据 / parse_args。
锁住三条硬约束：
- 默认空白名单 → 路径类工具全拒
- 白名单只放行子树，白名单外一律拒
- 不注册任何写类工具；overview / explain 两个"白名单外"工具的位置被固定
"""
from __future__ import annotations

import pytest

from backend import mcp_server as mcp

# 刻意豁免白名单的两个工具（见文档第三节）——改动它们要同步改文档
ALLOWLIST_BYPASS = {"atlas_overview", "atlas_explain_item"}


def test_empty_allowlist_rejects_all(tmp_path):
    allow = mcp.AllowList([])
    assert allow.empty() is True
    with pytest.raises(ValueError):
        allow.check(str(tmp_path))


def test_allowlist_permits_subtree(tmp_path):
    allow = mcp.AllowList([str(tmp_path)])
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert allow.check(str(sub)) == sub.resolve()
    # 根目录自身也放行
    assert allow.check(str(tmp_path)) == tmp_path.resolve()


def test_allowlist_rejects_outside(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    allow = mcp.AllowList([str(allowed)])
    with pytest.raises(ValueError):
        allow.check(str(outside))


def test_allowlist_rejects_file(tmp_path):
    allow = mcp.AllowList([str(tmp_path)])
    f = tmp_path / "not_a_dir.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        allow.check(str(f))


def test_path_tools_require_allowlist():
    flags = {t["name"]: t["needs_allow"] for t in mcp.TOOLS}
    for name, needs in flags.items():
        if name in ALLOWLIST_BYPASS:
            assert needs is False, f"{name} 应豁免白名单（若改了，请同步 docs/MCP_SECURITY.md）"
        else:
            assert needs is True, f"{name} 必须走白名单"


def test_no_write_tools_exposed():
    names = [t["name"].lower() for t in mcp.TOOLS]
    forbidden = ("delete", "remove", "move", "write", "clean", "purge", "restore", "rollback")
    for name in names:
        for token in forbidden:
            assert token not in name, f"不该暴露疑似写类工具：{name}"


def test_scan_rejected_without_allow(tmp_path):
    """空白名单时，真正调 handler 也必须在扫描前就被挡下。"""
    allow = mcp.AllowList([])
    with pytest.raises(ValueError):
        mcp.tool_scan({"path": str(tmp_path)}, allow)


def test_parse_args():
    roots, help_ = mcp.parse_args(["--mcp", "--allow", "D:\\X", "--allow=D:\\Y"])
    assert roots == ["D:\\X", "D:\\Y"]
    assert help_ is False
    assert mcp.parse_args(["-h"])[1] is True


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp(prefix="fp_mcp_"))
    fails = 0

    def _run(name, fn):
        global fails
        try:
            fn()
            print("PASS", name)
        except Exception as exc:  # noqa
            fails += 1
            print("FAIL", name, "->", exc)

    _run("t1", lambda: test_empty_allowlist_rejects_all(tmp))
    _run("t2", lambda: test_allowlist_permits_subtree(tmp))
    _run("t3", lambda: test_allowlist_rejects_outside(tmp))
    _run("t4", lambda: test_allowlist_rejects_file(tmp))
    _run("t5", test_path_tools_require_allowlist)
    _run("t6", test_no_write_tools_exposed)
    _run("t7", lambda: test_scan_rejected_without_allow(tmp))
    _run("t8", test_parse_args)
    print("FAILS:", fails)
