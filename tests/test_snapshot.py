"""B-ScanDiff 扫描快照：保存 / 列出 / 两次对比。

不依赖运行中的服务器，直接调 backend.services.snapshot。
覆盖：目录级 新增/消失/变大/变小 分类、总量差、root 归一化同桶、
排除规则不同标 scope_mismatch、缺快照抛错。
"""
from __future__ import annotations

from backend import config
from backend.services import snapshot


def _result(root, dir_sizes, *, total_size=0, total_files=0, total_dirs=None,
            excludes=None, max_depth=0):
    # total_size 默认取目录体积之和，省得每处都手算
    if not total_size:
        total_size = sum(dir_sizes.values())
    return {
        "root": root,
        "sources": [root],
        "excludes": excludes or [],
        "max_depth": max_depth,
        "total_size": total_size,
        "total_files": total_files,
        "total_dirs": total_dirs if total_dirs is not None else len(dir_sizes),
        "dir_sizes": dir_sizes,
    }


def test_save_and_list(tmp_path):
    config.SNAPSHOT_FILE = tmp_path / "snap.json"
    meta = snapshot.save_snapshot(_result("D:/A", {"D:/A": 100, "D:/A/sub": 50}))
    assert meta["root"] == snapshot._normalize_root("D:/A")
    assert meta["total_size"] == 150
    listed = snapshot.list_snapshots("D:/A")
    assert len(listed) == 1
    assert listed[0]["id"] == meta["id"]


def test_normalize_root_same_bucket(tmp_path):
    config.SNAPSHOT_FILE = tmp_path / "snap.json"
    snapshot.save_snapshot(_result("D:/A", {"D:/A": 10}))
    # 大小写 / 结尾斜杠差异应落到同一个归档桶
    assert len(snapshot.list_snapshots("d:/a/")) == 1


def test_compare_classification(tmp_path):
    config.SNAPSHOT_FILE = tmp_path / "snap.json"
    base = snapshot.save_snapshot(_result("D:/A", {"D:/A": 100, "D:/A/grew": 10, "D:/A/gone": 20}))
    target = snapshot.save_snapshot(_result("D:/A", {"D:/A": 100, "D:/A/grew": 40, "D:/A/new": 5}))
    diff = snapshot.compare_snapshots("D:/A", base["id"], target["id"])
    assert {x["path"] for x in diff["added"]} == {"D:/A/new"}
    assert {x["path"] for x in diff["removed"]} == {"D:/A/gone"}
    assert {x["path"] for x in diff["grown"]} == {"D:/A/grew"}
    # 总量差：target(100+40+5=145) - base(100+10+20=130) = +15
    assert diff["total_size_delta"] == 15
    assert diff["scope_mismatch"] is False


def test_compare_scope_mismatch(tmp_path):
    config.SNAPSHOT_FILE = tmp_path / "snap.json"
    base = snapshot.save_snapshot(_result("D:/A", {"D:/A": 100}, excludes=["node_modules"]))
    target = snapshot.save_snapshot(_result("D:/A", {"D:/A": 100}, excludes=[]))
    diff = snapshot.compare_snapshots("D:/A", base["id"], target["id"])
    # 排除规则不同 → 标 scope_mismatch，不让不同范围的两次扫描硬比
    assert diff["scope_mismatch"] is True


def test_compare_missing(tmp_path):
    config.SNAPSHOT_FILE = tmp_path / "snap.json"
    try:
        snapshot.compare_snapshots("D:/A", "nope", "also")
    except ValueError:
        return
    raise AssertionError("缺少快照时应抛 ValueError")


if __name__ == "__main__":
    import shutil
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp(prefix="fp_snap_"))
    fails = 0

    def _run(name, fn):
        global fails
        d = tmp / name
        d.mkdir(parents=True, exist_ok=True)
        try:
            fn(d)
            print("PASS", name)
        except Exception as exc:  # noqa
            fails += 1
            print("FAIL", name, "->", exc)

    _run("t1", test_save_and_list)
    _run("t2", test_normalize_root_same_bucket)
    _run("t3", test_compare_classification)
    _run("t4", test_compare_scope_mismatch)
    _run("t5", test_compare_missing)
    shutil.rmtree(tmp, ignore_errors=True)
    print("FAILS:", fails)
