"""旋转容忍基准（手动跑，不被 pytest 收集）。

量三件事的「改前(rotations=False) vs 改后(rotations=True)」：
1. 旋转召回：同一张图转 90° 的那份，能不能和原图分到一组
2. 精度：会不会把不同原图的图误并到一组
3. 代价：哈希耗时（每张图 ms）与端到端墙钟

数据全部现造：N 个原图，各出「原图 / jpeg q70 / 旋转 90°」三份，放同一张临时目录。
"""
from __future__ import annotations

import random
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw

from backend.services import similar
from backend.services.headless import HeadlessContext

N_BASES = 40
SIZE = 256


def _base_image(seed: int) -> Image.Image:
    im = Image.new("RGB", (SIZE, SIZE), (232, 232, 232))
    d = ImageDraw.Draw(im)
    rnd = random.Random(seed)
    for _ in range(7):
        x0 = rnd.randint(0, SIZE - 90)
        y0 = rnd.randint(0, SIZE - 90)
        w = rnd.randint(30, 90)
        h = rnd.randint(30, 90)
        col = (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
        if rnd.random() < 0.5:
            d.rectangle([x0, y0, x0 + w, y0 + h], fill=col)
        else:
            d.ellipse([x0, y0, x0 + w, y0 + h], fill=col)
    return im


def _make_set(root: Path) -> None:
    for i in range(N_BASES):
        base = _base_image(i)
        base.save(root / f"b{i}_orig.png")
        base.save(root / f"b{i}_comp.jpg", "JPEG", quality=70)
        base.rotate(90, expand=True).save(root / f"b{i}_rot90.png")


def _group_of(result: dict) -> dict[str, int]:
    """文件路径 → 组号（只统计进组且组内 ≥2 的）。"""
    mapping = {}
    for gi, group in enumerate(result.get("groups") or []):
        for f in group["files"]:
            mapping[f["path"]] = gi
    return mapping


def _metrics(result: dict) -> tuple[float, float]:
    """返回 (旋转召回率, 不纯度)。"""
    groups = result.get("groups") or []
    # 旋转召回：每个 base 的 orig 与 rot90 是否同组
    hit = 0
    mapping = _group_of(result)
    for i in range(N_BASES):
        o = next((p for p in mapping if Path(p).name == f"b{i}_orig.png"), None)
        r = next((p for p in mapping if Path(p).name == f"b{i}_rot90.png"), None)
        if o and r and mapping[o] == mapping[r]:
            hit += 1
    # 不纯度：一个组里含来自 ≥2 个 base 的文件，算 1 个"污染组"
    impure = 0
    for group in groups:
        bases = {Path(f["name"]).name.split("_")[0] for f in group["files"]}
        if len(bases) > 1:
            impure += 1
    return hit / N_BASES, impure


def _precision_check(n: int = 200) -> None:
    """精度压力：n 张互不相同的图，任何一组都是误报。比较开/关旋转的误报组数。"""
    root = Path(tempfile.mkdtemp(prefix="fp_prec_"))
    for i in range(n):
        _base_image(10_000 + i).save(root / f"u{i:04d}.png")
    print(f"精度压力：{n} 张互不相同的图（任何分组都是误报）")
    for rotations in (False, True):
        res = similar.find_similar(HeadlessContext({"path": str(root), "min_size": 1, "rotations": rotations}))
        label = "rotations=True " if rotations else "rotations=False"
        print(f"  {label:16s} 误报组数 = {res['group_count']}")
    print()


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="fp_rot_"))
    _make_set(root)
    total_files = len(list(root.glob("*")))
    print(f"样本：{N_BASES} 个原图 × 3 份 = {total_files} 个文件（原图 / jpeg / 旋转90°），{SIZE}×{SIZE}\n")

    # 微基准：单图哈希耗时
    one = str(root / "b0_orig.png")
    for label, fn in (("_dhash 单朝向", similar._dhash), ("_dhash_rotations 四朝向", similar._dhash_rotations)):
        fn(one)  # 预热
        t0 = time.perf_counter()
        for _ in range(60):
            fn(one)
        dt = (time.perf_counter() - t0) / 60 * 1000
        print(f"  {label:24s} 每张 {dt:6.2f} ms")

    print()
    rows = []
    for rotations in (False, True):
        t0 = time.perf_counter()
        res = similar.find_similar(HeadlessContext({"path": str(root), "min_size": 1, "rotations": rotations}))
        wall = time.perf_counter() - t0
        recall, impure = _metrics(res)
        rows.append((rotations, recall, impure, res["group_count"], wall))

    print("模式                 旋转召回      污染组   分组数   墙钟")
    for rotations, recall, impure, gcount, wall in rows:
        label = "rotations=True " if rotations else "rotations=False"
        print(f"  {label:16s} {recall*100:6.1f}%     {impure:4d}    {gcount:4d}   {wall:5.2f}s")

    print()
    print("说明：旋转召回 = orig 与 rot90 同组的比例；污染组 = 组内混了 2 个以上原图（越小越好）。")
    print()
    _precision_check()


if __name__ == "__main__":
    main()
