import pytest
from backend import fsutil
from backend.services import virtual


@pytest.mark.parametrize("group_by", ["../outside", r"..\outside", "type/../../outside", "unknown"])
def test_manifest_export_rejects_unknown_or_path_like_group(tmp_path, monkeypatch, group_by):
    monkeypatch.setattr(virtual.config, "STATE_DIR", tmp_path)
    with pytest.raises(ValueError, match="不支持的分组方式"):
        virtual.export_manifest("", group_by, [{"name": "a.txt", "source": "a.txt"}])
    assert not (tmp_path / "reports").exists()


def test_manifest_export_stays_in_reports_for_known_group(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(virtual.config, "STATE_DIR", tmp_path)
    result = virtual.export_manifest("", "type", [{"name": "a.txt", "source": "a.txt"}])
    exported = Path(result["path"])
    assert exported.parent == tmp_path / "reports"
    assert "a.txt" in exported.read_text(encoding="utf-8-sig")


def test_assert_within_rejects_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    with pytest.raises(fsutil.BlockedPathError):
        fsutil.assert_within(outside, root)


def test_assert_within_accepts_child(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    child = root / "child.txt"
    child.write_text("x")
    assert fsutil.assert_within(child, root) == child.resolve()


def test_assert_no_symlink_rejects_symlink(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "target.txt"
    target.write_text("x")
    link = root / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    # 某些沙箱/无特权环境下 symlink_to 不报错却静默不创建（is_symlink 为假），
    # 此时没有可测对象，跳过而非误判为"漏检"。
    if not link.is_symlink():
        pytest.skip("symlink creation no-op (sandbox/permission)")
    with pytest.raises(fsutil.BlockedPathError):
        fsutil.assert_no_symlink(link)
