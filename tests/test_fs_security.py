import pytest
from backend import fsutil


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
