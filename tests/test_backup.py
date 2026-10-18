"""备份还原核对（B-BackupVerify）：verify_backup_restore 的单元测试。

不依赖运行中的服务器，直接调 backend.services.backup.verify_backup_restore。
覆盖：
- 正常 ZIP：verify / restore_test 两种模式都能报 ok
- 缺少文件 → FileNotFoundError
- 损坏的字节 → ok=False（CRC 校验拦得住，不会假阳性）
- 非法 mode → ValueError
"""
from __future__ import annotations

import zipfile

from backend.services import backup


def _make_zip(path, tree):
    """tree: {相对名: 文件内容(bytes)}；目录用结尾斜杠的键占位。"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in tree.items():
            zf.writestr(name, content)


def _sample_tree():
    return {
        "doc/readme.txt": b"hello atlas backup\n" * 100,
        "doc/notes.txt": b"second file content here\n",
        "img/photo.bin": bytes(range(256)) * 50,
        "empty_dir/": b"",  # 目录项，verify 应跳过
    }


def test_verify_ok(tmp_path):
    zp = tmp_path / "good.zip"
    _make_zip(zp, _sample_tree())
    rep = backup.verify_backup_restore(str(zp), mode="verify")
    assert rep["ok"] is True
    assert rep["file_count"] == 3            # 三个文件，目录不算
    assert rep["verified_count"] == 3
    assert rep["member_count"] == 4          # +1 目录项
    assert rep["uncompressed_total"] > 0
    assert rep["mode"] == "verify"


def test_restore_test_ok(tmp_path):
    zp = tmp_path / "good.zip"
    _make_zip(zp, _sample_tree())
    rep = backup.verify_backup_restore(str(zp), mode="restore_test")
    # restore_test 真正走一遍还原：每个文件解到临时目录并核对大小
    assert rep["ok"] is True
    assert rep["verified_count"] == 3
    assert rep["mode"] == "restore_test"


def test_missing_file(tmp_path):
    try:
        backup.verify_backup_restore(str(tmp_path / "nope.zip"))
    except FileNotFoundError:
        return
    raise AssertionError("找不到文件时应抛 FileNotFoundError")


def test_corrupted_bytes(tmp_path):
    zp = tmp_path / "bad.zip"
    _make_zip(zp, _sample_tree())
    # 翻转某个真实文件的「压缩数据区」里一个字节：CRC 必然对不上，
    # 必须命中文件数据、避开文件尾的 central directory，否则会误报"截断"。
    with zipfile.ZipFile(zp) as zf:
        info = zf.infolist()[0]
        data_start = info.header_offset + 30 + len(info.filename.encode("utf-8")) + len(info.extra)
    with open(zp, "r+b") as fh:
        fh.seek(data_start + 5)
        b = fh.read(1)
        fh.seek(data_start + 5)
        fh.write(bytes([b[0] ^ 0xFF]))
    rep = backup.verify_backup_restore(str(zp), mode="verify")
    assert rep["ok"] is False
    assert rep["errors"]  # 必须把坏在哪说出来，不能静默 ok


def test_invalid_mode(tmp_path):
    zp = tmp_path / "good.zip"
    _make_zip(zp, _sample_tree())
    try:
        backup.verify_backup_restore(str(zp), mode="bogus")
    except ValueError:
        return
    raise AssertionError("非法 mode 应抛 ValueError")


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp(prefix="fp_backup_"))
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

    _run("t1", test_verify_ok)
    _run("t2", test_restore_test_ok)
    _run("t3", test_missing_file)
    _run("t4", test_corrupted_bytes)
    _run("t5", test_invalid_mode)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print("FAILS:", fails)
