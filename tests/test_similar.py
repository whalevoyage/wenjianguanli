"""相似图片 dHash 的单元测试（B-SimilarEval 落地的回归测试）。

锁住几条关键行为：
- _informative 必须排掉纯色指纹（否则一堆纯色图会被误判相似）
- 压缩 / 缩放 对 dHash 鲁棒（hamming ≤ 阈值）
- 旋转 dHash 不认（hamming > 阈值）—— 这是已知缺口，测它"确实不认"，别哪天被改坏了
- ★_match_pairs 不能用旧的分桶逻辑：造一对"最高字节整数差>1 但汉明≤阈值"的指纹，
  旧逻辑会漏掉，新多索引逻辑必须抓住（召回回归测试）
"""
from __future__ import annotations

import io
import os

from PIL import Image

from backend.services import similar


def _structured_image(size=64):
    """结构化且不对称的图：既有明暗起伏（dHash 有信息量），又不对称（旋转后指纹会变）。

    别用纯线性梯度：它逐行单调，dHash 会退化成近乎全 0，旋转前后还可能一模一样，
    拿来做旋转测试会假通过。
    """
    from PIL import ImageDraw

    im = Image.new("RGB", (size, size), (240, 240, 240))
    d = ImageDraw.Draw(im)
    d.rectangle([4, 4, size // 2, size // 3], fill=(10, 10, 10))          # 左上黑块
    d.ellipse([size // 2, size // 2, size - 5, size - 5], fill=(20, 20, 20))  # 右下黑圆
    d.rectangle([size // 4, size - 12, size - size // 4, size - 5], fill=(60, 60, 60))  # 底部横条
    return im


def _hash_bytes(im):
    buf = io.BytesIO()
    im.save(buf, "PNG")
    buf.seek(0)
    import tempfile
    from pathlib import Path
    p = Path(tempfile.mkdtemp()) / "x.png"
    p.write_bytes(buf.read())
    return similar._dhash(str(p), 8)


def test_informative_rejects_solid():
    # 纯色图 → 全 0（或全 1）指纹，必须被排掉
    assert similar._informative(0) is False
    assert similar._informative((1 << 64) - 1) is False
    assert similar._informative(None) is False
    # 有明暗起伏的指纹才能用
    assert similar._informative(0b101010) is True


def test_compression_robust(tmp_path):
    im = _structured_image()
    p = tmp_path / "a.png"
    im.save(p, "PNG")
    h0 = similar._dhash(str(p), 8)
    # jpeg q90 压缩后应仍相似
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    buf.seek(0)
    pj = tmp_path / "a.jpg"
    pj.write_bytes(buf.read())
    hj = similar._dhash(str(pj), 8)
    assert similar._hamming(h0, hj) <= similar.DEFAULT_THRESHOLD


def test_resize_robust(tmp_path):
    im = _structured_image()
    p = tmp_path / "a.png"
    im.save(p, "PNG")
    h0 = similar._dhash(str(p), 8)
    big = im.resize((int(im.width * 1.8), int(im.height * 1.8)))
    pb = tmp_path / "b.png"
    big.save(pb, "PNG")
    hb = similar._dhash(str(pb), 8)
    assert similar._hamming(h0, hb) <= similar.DEFAULT_THRESHOLD


def test_rotation_not_recognized(tmp_path):
    im = _structured_image()
    p = tmp_path / "a.png"
    im.save(p, "PNG")
    h0 = similar._dhash(str(p), 8)
    # 旋转 90° 应被判"不相似"：dHash 不是旋转不变的，这是已知缺口
    rot = im.rotate(90, expand=True)
    pr = tmp_path / "r.png"
    rot.save(pr, "PNG")
    hr = similar._dhash(str(pr), 8)
    assert similar._hamming(h0, hr) > similar.DEFAULT_THRESHOLD


def test_match_pairs_no_recall_loss():
    """★ 回归测试：旧分桶逻辑会漏掉的"最高字节差>1 但汉明≤阈值"的对，新逻辑必须抓住。"""
    threshold = similar.DEFAULT_THRESHOLD
    # 最纯粹的例子：最高字节 0b0111=7 vs 0b1111=15 —— 只差 1 位（汉明=1），
    # 但整数差 = 8。旧「相邻桶 ±1」逻辑会漏；新多索引逻辑必须抓到。
    h0 = 0x07 << 56
    h1 = 0x0F << 56
    assert similar._hamming(h0, h1) == 1
    assert (h1 >> 56) - (h0 >> 56) == 8

    usable = [(0, h0), (1, h1)]
    pairs = similar._match_pairs(usable, threshold)
    assert (0, 1) in pairs

    # 顺带验证：距离远超阈值的对不会被硬塞进来（h0 全 0 会被 _informative 排掉，
    # 这里手动喂给 _match_pairs 只为测它的阈值判断本身）
    h2 = ~h0 & ((1 << 64) - 1)  # 与 h0 汉明 64
    usable2 = [(0, 0x0F << 56), (1, h2)]
    # 若两者汉明都 ≤ 阈值才会判定；这里 h2 与 0x0F<<56 的距离远大于阈值
    assert (0, 1) not in similar._match_pairs(usable2, threshold)


def test_dhash_rotations_returns_four(tmp_path):
    im = _structured_image(120)
    p = tmp_path / "a.png"
    im.save(p, "PNG")
    vals = similar._dhash_rotations(str(p), 8)
    assert len(vals) == 4
    assert all(isinstance(v, int) for v in vals)


def _rotated_pair(tmp_path):
    im = _structured_image(160)
    p0 = tmp_path / "orig.png"
    p1 = tmp_path / "rot90.png"
    im.save(p0, "PNG")
    im.rotate(90, expand=True).save(p1, "PNG")
    return p0, p1


def _group_name_sets(result):
    return [frozenset(os.path.basename(f["path"]) for f in g["files"]) for g in result.get("groups", [])]


def _run_similar(tmp_path, rotations):
    from backend.services.headless import HeadlessContext
    return similar.find_similar(HeadlessContext({"path": str(tmp_path), "min_size": 1, "rotations": rotations}))


def test_rotation_off_misses_rotated_copy(tmp_path):
    _rotated_pair(tmp_path)
    res = _run_similar(tmp_path, rotations=False)
    # 默认关：旋转过的副本认不出，orig 与 rot90 不该在同一组
    assert frozenset({"orig.png", "rot90.png"}) not in _group_name_sets(res)


def test_rotation_on_matches_rotated_copy(tmp_path):
    _rotated_pair(tmp_path)
    res = _run_similar(tmp_path, rotations=True)
    # 开启旋转容忍：orig 与 rot90 应被并入同一组
    assert frozenset({"orig.png", "rot90.png"}) in _group_name_sets(res)
    assert res["rotations"] is True


if __name__ == "__main__":
    import inspect
    import tempfile
    from pathlib import Path

    base = Path(tempfile.mkdtemp(prefix="fp_sim_t_"))
    fails = 0

    def _run(name, fn):
        global fails
        d = base / name
        d.mkdir(parents=True, exist_ok=True)
        try:
            # 每个用例给一个干净的目录；无参用例直接调
            fn(d) if inspect.signature(fn).parameters else fn()
            print("PASS", name)
        except Exception as exc:  # noqa
            fails += 1
            print("FAIL", name, "->", exc)

    _run("t1", test_informative_rejects_solid)
    _run("t2", test_compression_robust)
    _run("t3", test_resize_robust)
    _run("t4", test_rotation_not_recognized)
    _run("t5", test_match_pairs_no_recall_loss)
    _run("t6", test_dhash_rotations_returns_four)
    _run("t7", test_rotation_off_misses_rotated_copy)
    _run("t8", test_rotation_on_matches_rotated_copy)
    print("FAILS:", fails)
