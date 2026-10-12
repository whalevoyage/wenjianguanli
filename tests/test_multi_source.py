"""B8 多来源选择：后端单元测试。

不依赖运行中的服务器，用桩 JobContext 直接调四个服务，并断言三道护栏：
  1. 嵌套根自动跳过（不报错，写进 notes）
  2. 坏路径整体失败
  3. 跨目录重复（查重）能被识别并排到前面
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import fsutil
from backend.services import duplicates, index, junk, projects, scan, similar


class StubCtx:
    """最小 JobContext：服务用到的接口全 stub 掉，便于脱离 JobManager 跑。"""

    def __init__(self, params: dict) -> None:
        self._params = params
        self._cancel = threading.Event()

    @property
    def params(self):
        return self._params

    def cancelled(self):
        return self._cancel.is_set()

    def check_cancel(self):
        if self._cancel.is_set():
            raise RuntimeError("cancelled")

    def phase(self, index: int, message: str = ""):
        pass

    def progress(self, message: str, current, total=None, path: str = ""):
        pass

    def tick(self, message: str, current, total=None, path: str = ""):
        pass

    def sleep(self, seconds: float = 0.02):
        pass


def _ctx(params: dict) -> StubCtx:
    return StubCtx(params)


# ---------------------------------------------------------------------------
# resolve_sources 护栏
# ---------------------------------------------------------------------------

def test_resolve_sources_path_fallback(tmp_path: Path):
    r = tmp_path / "a"
    r.mkdir()
    roots, notes = fsutil.resolve_sources({"path": str(r)})
    assert [str(x) for x in roots] == [str(r)]
    assert notes == []


def test_resolve_sources_paths_list(tmp_path: Path):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    roots, notes = fsutil.resolve_sources({"paths": [str(a), str(b)]})
    assert len(roots) == 2


def test_resolve_sources_nested_skipped(tmp_path: Path):
    a = tmp_path / "a"; a.mkdir()
    sub = a / "sub"; sub.mkdir()
    (sub / "x.txt").write_text("hi")
    roots, notes = fsutil.resolve_sources({"paths": [str(a), str(sub)]})
    # sub 被包含在 a 内，应该被跳过，只保留 a
    assert [str(x) for x in roots] == [str(a)]
    assert any("已包含" in n or "合并" in n for n in notes)


def test_resolve_sources_bad_path_fails(tmp_path: Path):
    import pytest
    with pytest.raises(FileNotFoundError):
        fsutil.resolve_sources({"paths": [str(tmp_path / "nope")]})


def test_resolve_sources_empty_fails():
    import pytest
    with pytest.raises(ValueError):
        fsutil.resolve_sources({})


# ---------------------------------------------------------------------------
# iter_entries_multi：产出 (root, entry) 且前缀正确
# ---------------------------------------------------------------------------

def test_iter_entries_multi_roots(tmp_path: Path):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    (a / "f1.txt").write_text("1")
    (b / "f2.txt").write_text("2")
    seen = {}
    for root, entry in fsutil.iter_entries_multi([a, b]):
        seen[entry.path] = str(root)
    assert set(seen) == {str(a / "f1.txt"), str(b / "f2.txt")}
    assert seen[str(a / "f1.txt")] == str(a)
    assert seen[str(b / "f2.txt")] == str(b)


# ---------------------------------------------------------------------------
# 四个服务实跑（多源）
# ---------------------------------------------------------------------------

def test_junk_multi(tmp_path: Path):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    (a / "empty.log").write_bytes(b"")          # zero_byte
    (b / "empty.tmp").write_bytes(b"")          # zero_byte
    res = junk.run_junk_scan(_ctx({"paths": [str(a), str(b)]}))
    assert res["source_count"] == 2
    assert {str(a), str(b)} <= set(res["sources"])
    total = sum(item["size"] for lv in res["levels"] for item in lv["items"])
    assert res["total_count"] >= 2


def test_scan_multi(tmp_path: Path):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    (a / "big.iso").write_bytes(b"x" * (20 * 1024 * 1024))
    (b / "note.txt").write_text("hello")
    res = scan.run_scan(_ctx({"paths": [str(a), str(b)]}))
    assert res["source_count"] == 2
    assert res["total_files"] == 2
    assert res["total_size"] >= 20 * 1024 * 1024


def test_duplicate_cross_source(tmp_path: Path):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    content = "identical payload for cross-source test\n"
    (a / "dup.txt").write_text(content)
    (b / "dup.txt").write_text(content)
    (a / "uniq.txt").write_text("only in a")
    res = duplicates.run_duplicate_scan(_ctx({"paths": [str(a), str(b)]}))
    assert res["source_count"] == 2
    cross = [g for g in res["groups"] if g["cross_source"]]
    assert cross, "跨目录重复必须被识别"
    assert cross[0]["cross_source"] is True
    # 跨目录的组排在最前面
    assert res["groups"][0]["cross_source"] is True
    # 单源里的 uniq 不该造出重复
    assert res["redundant_file_count"] == 1


def test_similar_multi_handles_pillow_absence(tmp_path: Path):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    # 即使没装 Pillow，多源解析本身也不能崩；available=False 时直接返回人话
    res = similar.run_similar_scan(_ctx({"paths": [str(a), str(b)]}))
    assert res["available"] in (True, False)
    if res["available"]:
        assert res["source_count"] == 2


# ---------------------------------------------------------------------------
# API 层：端点能接收前端只发 `paths`（不发 `path`）的请求
#   不真正发起任务 —— 把 _launch 打成桩，只校验「入参解析 + 标题/目标 + 不 422」。
# ---------------------------------------------------------------------------

def _stub_launch():
    """把 main._launch 换成桩，返回最后一次调用收到的 (job_type, title, target, params)。

    直接重新绑定模块级全局：端点函数体里的 `return _launch(...)` 会在调用时
    查 main 模块的全局名，所以替换 main._launch 即可生效，无需改端点代码。
    """
    from backend import main
    captured = {}

    def fake(job_type, title, target, params, worker):
        captured.update(job_type=job_type, title=title, target=target, params=params)
        return {"id": "stub", "title": title, "target": target}

    main._launch = fake
    return captured


def test_endpoint_scan_paths_only(tmp_path: Path):
    captured = _stub_launch()
    from backend import main
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    resp = main.start_scan(main.ScanRequest(paths=[str(a), str(b)]))
    # 多源：标题带「个来源」后缀，目标取首个来源
    assert "个来源" in resp["title"]
    assert captured["target"] == str(a)
    # 下发给服务的参数确实带 paths
    assert captured["params"].get("paths") == [str(a), str(b)]


def test_endpoint_scan_single_path_backcompat(tmp_path: Path):
    captured = _stub_launch()
    from backend import main
    a = tmp_path / "a"; a.mkdir()
    # 老旧调用方仍只发 path：resolve_sources 回退到 path，不应带「个来源」
    main.start_scan(main.ScanRequest(path=str(a)))
    assert "个来源" not in captured["title"]
    assert captured["target"] == str(a)


def test_endpoint_scan_bad_path_400(tmp_path: Path):
    from backend import main
    from fastapi import HTTPException
    main._launch = lambda *a, **k: {"id": "x"}
    try:
        # 不存在的目录：必须 400 友好错误，绝不能 422（校验失败）或 500
        main.start_scan(main.ScanRequest(paths=[str(tmp_path / "nope")]))
    except HTTPException as exc:
        assert exc.status_code == 400
        return
    raise AssertionError("坏路径没有触发 400 友好错误")


def test_endpoint_other_three_paths_only(tmp_path: Path):
    captured = _stub_launch()
    from backend import main
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    for fn, req in (
        (main.start_duplicate_scan, main.DuplicateRequest),
        (main.start_junk_scan, main.JunkRequest),
        (main.start_similar_scan, main.SimilarRequest),
    ):
        captured.clear()
        fn(req(paths=[str(a), str(b)]))
        assert "个来源" in captured["title"], fn.__name__
        assert captured["target"] == str(a), fn.__name__
        assert captured["params"].get("paths") == [str(a), str(b)], fn.__name__


# ---------------------------------------------------------------------------
# 第二批：项目雷达 / 全文检索 接多来源
# ---------------------------------------------------------------------------

def test_projects_multi(tmp_path: Path):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    for name, base in (("A", a), ("B", b)):
        proj = base / f"proj_{name}"
        proj.mkdir()
        (proj / "package.json").write_text("{}", encoding="utf-8")
        (proj / "code.py").write_text("print(1)", encoding="utf-8")
    res = projects.run_project_scan(_ctx({"paths": [str(a), str(b)]}))
    assert res["source_count"] == 2
    assert set(res["roots"]) == {str(a), str(b)}
    assert res["project_count"] == 2
    # 每条项目都带来源标记，且两个来源各出一个
    assert {row["source"] for row in res["projects"]} == {str(a), str(b)}


def test_index_multi(tmp_path: Path):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    (a / "note.txt").write_text("hello index", encoding="utf-8")
    (b / "doc.md").write_text("# title", encoding="utf-8")
    res = index.run_index(_ctx({"paths": [str(a), str(b)]}))
    assert res["source_count"] == 2
    assert set(res["roots"]) == {str(a), str(b)}
    assert res["total_files"] == 2
    assert len(res.get("per_root", [])) == 2


def test_endpoint_projects_and_search_paths_only(tmp_path: Path):
    captured = _stub_launch()
    from backend import main
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    for fn, req in (
        (main.start_project_scan, main.ProjectRequest),
        (main.start_search_index, main.SearchIndexRequest),
    ):
        captured.clear()
        fn(req(paths=[str(a), str(b)]))
        assert "个来源" in captured["title"], fn.__name__
        assert captured["target"] == str(a), fn.__name__
        assert captured["params"].get("paths") == [str(a), str(b)], fn.__name__


if __name__ == "__main__":
    import tempfile
    import shutil

    tmp = Path(tempfile.mkdtemp(prefix="fp_multisrc_"))
    fails = 0

    def _run(name, fn, *args):
        global fails
        d = tmp / name
        d.mkdir(parents=True, exist_ok=True)
        try:
            fn(d, *args)
            print("PASS", name)
        except Exception as exc:  # noqa
            fails += 1
            print("FAIL", name, "->", exc)

    _run("t1", test_resolve_sources_path_fallback)
    _run("t2", test_resolve_sources_paths_list)
    _run("t3", test_resolve_sources_nested_skipped)
    _run("t4", test_iter_entries_multi_roots)
    _run("t5", test_junk_multi)
    _run("t6", test_scan_multi)
    _run("t7", test_duplicate_cross_source)
    _run("t8", test_similar_multi_handles_pillow_absence)
    _run("t9", test_endpoint_scan_paths_only)
    _run("t10", test_endpoint_scan_single_path_backcompat)
    _run("t11", test_endpoint_scan_bad_path_400)
    _run("t12", test_endpoint_other_three_paths_only)
    _run("t13", test_projects_multi)
    _run("t14", test_index_multi)
    _run("t15", test_endpoint_projects_and_search_paths_only)
    shutil.rmtree(tmp, ignore_errors=True)
    print("FAILS:", fails)
    sys.exit(1 if fails else 0)

