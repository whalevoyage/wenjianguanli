"""执行层（`backend.execution`）的门禁测试。

这组测试守的是 Phase 3 的验收标准：

  1. readonly 档下**任何**用户文件写操作都失败；
  2. 不存在通过 HTTP / Agent / rollback / restore / purge / virtual 绕过门禁的路径；
  3. 将来没人能直接写 `shutil.move` / `.unlink()` 绕过这一层。

第 3 条用静态扫描守（`test_no_module_bypasses_the_execution_layer`），
因为它防的是「以后新增代码忘了走 execution」，运行时测试抓不到。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend import execution, permissions
from backend.services import clean


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

@pytest.fixture
def readonly(monkeypatch):
    """把当前档位钉在只读。monkeypatch 会在用例结束后自动还原。"""
    monkeypatch.setattr(permissions, "current", lambda: "readonly")
    return "readonly"


@pytest.fixture
def standard(monkeypatch):
    monkeypatch.setattr(permissions, "current", lambda: "standard")
    return "standard"


class _Ctx:
    """够用的 JobContext 替身：不落库、不推 SSE。"""

    def __init__(self, params: dict):
        self.params = params
        self.job_id = "test-job"
        self.actions: list[dict] = []

    def phase(self, *args, **kwargs):
        pass

    def progress(self, *args, **kwargs):
        pass

    def check_cancel(self):
        pass

    def sleep(self, *args, **kwargs):
        pass

    def add_actions(self, actions):
        self.actions.extend(actions or [])


def _file(tmp_path: Path, name: str = "a.txt") -> Path:
    path = tmp_path / name
    path.write_text("hello", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 一、原语级：每个操作在只读档都必须拒绝，且不能碰到文件
# ---------------------------------------------------------------------------

def test_assert_can_write_rejects_in_readonly(readonly, tmp_path):
    with pytest.raises(ValueError) as info:
        execution.assert_can_write(tmp_path / "x.txt", action="测试写")
    assert "只读" in str(info.value)


def test_move_does_nothing_in_readonly(readonly, tmp_path):
    src = _file(tmp_path)
    dst = tmp_path / "sub" / "a.txt"

    with pytest.raises(ValueError):
        execution.move(src, dst)

    assert src.exists(), "只读档下源文件被动了"
    assert not dst.exists(), "只读档下目标被创建了"


def test_remove_does_nothing_in_readonly(readonly, tmp_path):
    target = _file(tmp_path)
    with pytest.raises(ValueError):
        execution.remove(target)
    assert target.exists(), "只读档下文件被删了"


def test_remove_tree_does_nothing_in_readonly(readonly, tmp_path):
    folder = tmp_path / "dir"
    folder.mkdir()
    _file(folder, "inner.txt")

    with pytest.raises(ValueError):
        execution.remove(folder, recursive=True)

    assert folder.exists() and (folder / "inner.txt").exists()


def test_remove_empty_dir_does_nothing_in_readonly(readonly, tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    with pytest.raises(ValueError):
        execution.remove_empty_dir(folder)
    assert folder.exists()


def test_mkdir_does_nothing_in_readonly(readonly, tmp_path):
    target = tmp_path / "new"
    with pytest.raises(ValueError):
        execution.mkdir(target)
    assert not target.exists()


def test_recycle_does_nothing_in_readonly(readonly, tmp_path):
    target = _file(tmp_path)
    with pytest.raises(ValueError):
        execution.recycle(target)
    assert target.exists()


# ---------------------------------------------------------------------------
# 二、服务级：readonly 下真实入口全都写不进去
# ---------------------------------------------------------------------------

def test_clean_cannot_isolate_in_readonly(readonly, tmp_path, monkeypatch):
    target = _file(tmp_path)
    monkeypatch.setattr(clean, "_trash_root_for", lambda path: tmp_path / "_trash")

    ctx = _Ctx({"items": [{"path": str(target), "size": 5}], "mode": "isolate"})
    result = clean.run_clean(ctx)

    assert target.exists(), "只读档下 clean 把文件搬走了"
    assert result["cleaned_count"] == 0
    assert result["errors"], "只读档下清理应当逐条报错，而不是静默成功"


def test_clean_cannot_recycle_in_readonly(readonly, tmp_path):
    target = _file(tmp_path)
    ctx = _Ctx({"items": [{"path": str(target), "size": 5}], "mode": "recycle"})
    result = clean.run_clean(ctx)

    assert target.exists()
    assert result["cleaned_count"] == 0


def test_clean_cannot_remove_empty_dir_in_readonly(readonly, tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    ctx = _Ctx({"items": [{"path": str(folder), "is_dir": True, "rule": "empty_dir"}]})
    result = clean.run_clean(ctx)

    assert folder.exists(), "只读档下空目录被删了"
    assert result["cleaned_count"] == 0


def test_restore_trash_rejects_in_readonly(readonly, monkeypatch):
    """回滚/还原是 2026-10-08 审计里真实漏过的那条路径。"""
    with pytest.raises(ValueError) as info:
        clean.restore_trash(["any-id"])
    assert "只读" in str(info.value)


def test_purge_trash_rejects_in_readonly(readonly):
    """物理删除口在只读档必须拒 —— 不可恢复，没有例外。"""
    with pytest.raises(ValueError) as info:
        clean.purge_trash(["any-id"])
    assert "只读" in str(info.value)


def test_job_rollback_rejects_in_readonly(readonly):
    """回滚会把文件搬回原位 —— 只读档下同样是写操作。"""
    from backend.jobs import manager

    with pytest.raises(ValueError) as info:
        manager.rollback("no-such-job")
    assert "只读" in str(info.value)


# ---------------------------------------------------------------------------
# 二·补：以前**只有路由层**拦着、服务层裸着的三个写入口
# ---------------------------------------------------------------------------

def test_migrate_execute_requires_write(readonly, tmp_path):
    """迁移真执行会删源。以前门禁只在 `_launch()`，绕过去就是裸的。"""
    from backend.services import migrate

    ctx = _Ctx({
        "source_dir": str(tmp_path),
        "target_dir": str(tmp_path / "target"),
        "dry_run": False,
    })
    with pytest.raises(ValueError) as info:
        migrate.run_migrate(ctx)
    assert "只读" in str(info.value)


def test_virtual_organize_requires_write(readonly, tmp_path):
    """`virtual` 这个 job 类型既做预览又做执行，按 job type 判权限区分不了。"""
    from backend.services import virtual

    ctx = _Ctx({
        "path": str(tmp_path),
        "items": [{"source": str(tmp_path / "a.txt"), "target": str(tmp_path / "sub" / "a.txt")}],
    })
    with pytest.raises(ValueError) as info:
        virtual.run_organize(ctx)
    assert "只读" in str(info.value)


def test_backup_requires_write(readonly, tmp_path):
    """备份会往磁盘写 ZIP，只读档一律拒绝。"""
    from backend.services import backup

    ctx = _Ctx({"source_dir": str(tmp_path)})
    with pytest.raises(ValueError) as info:
        backup.run_backup(ctx)
    assert "只读" in str(info.value)


# ---------------------------------------------------------------------------
# 三、写保护：目标路径而不是源路径决定能不能写
# ---------------------------------------------------------------------------

def test_cannot_move_into_self_protected_dir(standard, tmp_path):
    """工具自身的运行数据 / 隔离区账本：任何档位都不能被当成普通目标。"""
    from backend import config

    src = _file(tmp_path)
    with pytest.raises(ValueError) as info:
        execution.move(src, config.STATE_DIR / "should-not-land.txt")
    assert "保护目录" in str(info.value) or "禁止" in str(info.value)
    assert src.exists(), "被拒绝时不该先把源搬走"


def test_cannot_move_into_desktop_in_standard_mode(standard, tmp_path):
    """桌面是软保护（只限写）—— standard 下不许写，full 才放开。"""
    from backend import config

    desktop = next((p for p in config.SOFT_PROTECTED_DIRS if p.exists()), None)
    if desktop is None:
        pytest.skip("当前环境没有桌面目录")

    src = _file(tmp_path)
    with pytest.raises(ValueError):
        execution.move(src, desktop / "atlas-guard-test.txt")
    assert src.exists()


def test_dest_root_blocks_escape(standard, tmp_path):
    """就地整理：目标必须留在整理根内。"""
    root = tmp_path / "root"
    root.mkdir()
    src = _file(root)
    outside = _file(tmp_path, "outside.txt")

    with pytest.raises(ValueError) as info:
        execution.move(src, outside, dest_root=root)
    assert "越出" in str(info.value) or "允许目录" in str(info.value)
    assert src.exists()


def test_move_within_root_is_allowed(standard, tmp_path):
    """正例：根内移动必须照常工作，别把门禁写成全面禁止。"""
    root = tmp_path / "root"
    goal = root / "sub"
    goal.mkdir(parents=True)
    src = _file(root)

    execution.move(src, goal / "a.txt", dest_root=root)
    assert (goal / "a.txt").exists() and not src.exists()


# ---------------------------------------------------------------------------
# 四、静态守卫：不许绕过执行层
# ---------------------------------------------------------------------------

# 这些文件里的裸写操作是「工具自己的产物」，不属于用户文件操作：
#   - fsutil：复制被取消时删掉没写完的目标文件（半成品清理）
#   - backup：ZIP 的临时目录与 partial 文件
#   - store / database / diagnostics：状态库与探针文件的原子写
_ALLOWED_RAW_WRITE = {
    "backend/execution.py",
    "backend/fsutil.py",
    "backend/services/backup.py",
    "backend/store.py",
    "backend/database.py",
    "backend/diagnostics.py",
}

_RAW_WRITE_PATTERNS = (
    "shutil.move(",
    "shutil.rmtree(",
    "shutil.copy2(",
    ".unlink()",
    "os.remove(",
    "os.unlink(",
)


# ---------------------------------------------------------------------------
# 五、功能没被收口弄坏：完整走一遍「隔离 → 还原 → 彻底删除」
# ---------------------------------------------------------------------------

@pytest.fixture
def memory_trash(monkeypatch, tmp_path):
    """把隔离区账本放进内存、把桶重定向到 tmp_path。

    不碰真实状态目录，也不在用户盘的根上建 `_filepilot_trash`。
    """
    from backend import store

    entries: list[dict] = []

    monkeypatch.setattr(store, "read_trash", lambda: list(entries))

    def save(items):
        entries[:] = list(items)

    monkeypatch.setattr(store, "save_trash", save)
    monkeypatch.setattr(clean, "_trash_root_for", lambda path: tmp_path / "_trash")
    monkeypatch.setattr(clean, "_prune_empty_buckets", lambda: None)
    return entries


def test_clean_restore_purge_round_trip(standard, tmp_path, memory_trash):
    target = _file(tmp_path, "round-trip.txt")

    # 1) 隔离
    result = clean.run_clean(_Ctx({"items": [{"path": str(target), "size": 5}], "mode": "isolate"}))
    assert result["cleaned_count"] == 1, result["errors"]
    assert not target.exists(), "隔离后源文件还在"
    assert len(memory_trash) == 1

    entry = memory_trash[0]
    trash_path = Path(entry["trash_path"])
    assert trash_path.exists(), "隔离区里没有这个文件"

    # 2) 还原
    restored = clean.restore_trash([entry["id"]])
    assert restored["restored_count"] == 1, restored["errors"]
    assert target.exists(), "还原后文件没回到原位"
    assert not trash_path.exists(), "还原后隔离区里还留着副本"

    # 3) 再隔离一次，然后彻底删除
    clean.run_clean(_Ctx({"items": [{"path": str(target), "size": 5}], "mode": "isolate"}))
    again = memory_trash[0]
    purged = clean.purge_trash([again["id"]])
    assert purged["purged_count"] == 1, purged["errors"]
    assert not Path(again["trash_path"]).exists(), "彻底删除后隔离区文件还在"


def test_recycle_mode_still_works(standard, tmp_path, memory_trash):
    """送系统回收站这条路不能因为收口而失效。"""
    target = _file(tmp_path, "recycle-me.txt")
    result = clean.run_clean(_Ctx({"items": [{"path": str(target), "size": 5}], "mode": "recycle"}))

    # 沙箱里系统回收站 API 可能不可用 —— 那就必须如实报错，而不是假装成功
    if result["cleaned_count"] == 1:
        assert not target.exists()
        assert not memory_trash, "送回收站不该进隔离区账本"
    else:
        assert result["errors"], "送回收站失败必须报错，不能静默"
        assert target.exists()


def test_no_module_bypasses_the_execution_layer():
    """扫描源码：用户文件操作只能出现在 execution 层或已知豁免文件里。

    这条测试防的是「以后新增一个服务，图省事直接 shutil.move」——
    运行时测试抓不到，静态扫描可以。
    """
    root = Path(__file__).resolve().parent.parent
    offenders: list[str] = []

    for path in sorted((root / "backend").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        if rel in _ALLOWED_RAW_WRITE:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pattern in _RAW_WRITE_PATTERNS:
                if pattern in line:
                    offenders.append(f"{rel}:{lineno} → {stripped}")
                    break

    assert offenders == [], (
        "这些地方直接写了底层文件操作，应当改走 backend.execution：\n"
        + "\n".join(offenders)
    )
