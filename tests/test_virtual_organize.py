"""2026-10-14 虚拟整理「真执行」：移动文件 + 越界拒绝 + 重名加序号 + 可回滚动作。

安全语义：只移动不删除；每个文件记一条 op=move，任务页可整体回滚；
目标必须落在整理根目录内（防 `..` 越界）。
"""
from backend.services import virtual as virtual_svc


class _FakeCtx:
    def __init__(self, params):
        self.params = params
        self.actions: list[dict] = []

    def phase(self, *_a, **_k):
        pass

    def progress(self, *_a, **_k):
        pass

    def check_cancel(self):
        pass

    def sleep(self, *_a, **_k):
        pass

    def cancelled(self):
        return False

    def add_actions(self, actions):
        self.actions.extend(actions)


def test_organize_moves_files_records_rollback_and_rejects_escape(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x" * 10)
    (tmp_path / "b.mp4").write_bytes(b"y" * 20)
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "c.jpg").write_bytes(b"z" * 5)

    items = [
        {"source": str(tmp_path / "a.jpg"), "target": str(tmp_path / "按类型" / "图片" / "a.jpg"), "size": 10},
        {"source": str(tmp_path / "b.mp4"), "target": str(tmp_path / "按类型" / "视频" / "b.mp4"), "size": 20},
        {"source": str(sub / "c.jpg"), "target": str(tmp_path / "按类型" / "图片" / "c.jpg"), "size": 5},
        # 越界目标：必须被拒，且不产生动作
        {"source": str(tmp_path / "a.jpg"), "target": str(tmp_path.parent / "escape.jpg"), "size": 10},
    ]
    ctx = _FakeCtx({"path": str(tmp_path), "group_by": "type", "items": items})
    out = virtual_svc.run_organize(ctx)

    assert out["moved_count"] == 3
    assert out["readonly"] is False
    assert (tmp_path / "按类型" / "图片" / "a.jpg").read_bytes() == b"x" * 10
    assert (tmp_path / "按类型" / "视频" / "b.mp4").exists()
    assert (tmp_path / "按类型" / "图片" / "c.jpg").exists()
    # 源都搬走了
    assert not (tmp_path / "a.jpg").exists()
    assert not (sub / "c.jpg").exists()
    # 越界目标没有被创建
    assert not (tmp_path.parent / "escape.jpg").exists()
    # 每个搬移都留了可回滚动作
    assert len(ctx.actions) == 3
    assert all(a["op"] == "move" for a in ctx.actions)
    assert any("不在整理目录内" in e for e in out["errors"])


def test_organize_skips_files_already_in_place(tmp_path):
    target_dir = tmp_path / "按类型" / "图片"
    target_dir.mkdir(parents=True)
    (target_dir / "already.jpg").write_bytes(b"k")

    items = [{
        "source": str(target_dir / "already.jpg"),
        "target": str(target_dir / "already.jpg"),
        "size": 1,
    }]
    ctx = _FakeCtx({"path": str(tmp_path), "group_by": "type", "items": items})
    out = virtual_svc.run_organize(ctx)
    assert out["moved_count"] == 0
    assert out["skipped_count"] == 1
    assert ctx.actions == []


def test_organize_renames_on_collision_instead_of_overwriting(tmp_path):
    first = tmp_path / "one" / "same.txt"
    second = tmp_path / "two" / "same.txt"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    target = tmp_path / "按类型" / "文档" / "same.txt"

    items = [
        {"source": str(first), "target": str(target), "size": 5},
        {"source": str(second), "target": str(target), "size": 6},
    ]
    ctx = _FakeCtx({"path": str(tmp_path), "group_by": "type", "items": items})
    out = virtual_svc.run_organize(ctx)

    assert out["moved_count"] == 2
    dest = tmp_path / "按类型" / "文档"
    files = sorted(p.name for p in dest.iterdir())
    assert len(files) == 2                      # 第二个加了序号，没被覆盖
    assert "same.txt" in files
    assert any(name != "same.txt" for name in files)
    assert b"first" in [(dest / n).read_bytes() for n in files]
    assert b"second" in [(dest / n).read_bytes() for n in files]
