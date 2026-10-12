"""垃圾扫描的边界场景：空目录 / 多来源 / 来源根本身。

2026-10-12 修掉的真 bug：`run_junk_scan` 把主遍历的**循环变量** `root`
直接拿去给「空文件夹」那一趟用，于是
  ① 目录里一个条目都没有时（空目录 / 不可读）`root` 从未绑定，
     到那趟直接 `UnboundLocalError` 崩掉**整条 Agent 任务**（用户实机截图：扫桌面下的空目录失败）；
  ② 多来源时循环结束后 `root` 只是**最后一个**来源，
     前面几个来源的空目录被静默漏掉（B8 多来源埋下的隐患）。
"""
from pathlib import Path

from backend.services import junk
from backend.services.headless import HeadlessContext


def _paths_of(result: dict) -> set[str]:
    return {item["path"] for level in result["levels"] for item in level.get("items") or []}


def test_junk_scan_on_empty_directory_does_not_crash(tmp_path: Path):
    """空目录：主遍历零产出，以前这里直接 UnboundLocalError。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    result = junk.run_junk_scan(HeadlessContext({"path": str(empty)}))
    assert result["scanned_files"] == 0
    assert result["total_count"] == 0
    assert result["levels"]            # 三档结构仍然完整返回


def test_junk_scan_finds_empty_dirs_in_every_source(tmp_path: Path):
    """多来源：每个来源的空目录都要被发现（以前只看最后一个来源）。"""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "hole").mkdir()
    (b / "pit").mkdir()
    result = junk.run_junk_scan(HeadlessContext({"paths": [str(a), str(b)]}))
    found = _paths_of(result)
    assert str(a / "hole") in found
    assert str(b / "pit") in found


def test_junk_scan_skips_the_source_root_itself(tmp_path: Path):
    """来源根自己不算「空目录」——哪怕它真是空的。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    result = junk.run_junk_scan(HeadlessContext({"path": str(empty)}))
    assert str(empty) not in _paths_of(result)
