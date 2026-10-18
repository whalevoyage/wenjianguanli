"""相似图片 dHash 评估（手动跑，不被 pytest 收集：文件名不匹配 test_*）。

目的：用手搓的「真值集」给 dHash 算法 + 当前分桶匹配 Logic 量一遍，
给出 precision / recall 数据，决定下一步到底要不要加「旋转容忍」或「缓存」，
以及确认分桶匹配是否有召回漏洞。

真值集构造（用 Pillow 现画，不依赖任何外部图片）：
- 6 张结构化的"原图"（梯度 / 圆形 / 棋盘 / 条纹 …），互不相同，且都有明暗起伏
  （保证 dHash 有信息量，不会被 _informative 当成纯色扔掉）。
- 每张原图的「内容变体」：jpeg q90 / jpeg q50 / 缩放 0.6x / 缩放 1.8x / 中心裁切 80%。
  这些理论上应该被判相似（正样本）。
- 每张原图的「旋转变体」：90° / 180° / 270°。用来量 dHash 对旋转的无能为力。
- 跨原图之间算负样本（应该判不相似），量 precision。

输出：
- 各变换相对原图的汉明距离分布（是否 ≤ 阈值 6）
- 分桶匹配的召回漏洞：纯暴力两两比能抓住的正样本，分桶逻辑漏掉了多少
- 旋转容忍方案（对 4 个朝向各算一次 dHash 取最小汉明）能救回多少旋转变体
"""
from __future__ import annotations

import io
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from backend.services import similar


THRESHOLD = similar.DEFAULT_THRESHOLD  # 6


def _make_photos(size=96):
    """画 6 张结构化的"照片"，返回 [Image]。互不相同且有明暗结构。"""
    imgs = []

    # 1. 对角渐变 + 彩色方块
    im = Image.new("RGB", (size, size))
    px = im.load()
    for y in range(size):
        for x in range(size):
            r = int(255 * x / size)
            g = int(255 * y / size)
            b = 128
            px[x, y] = (r, g, b)
    d = ImageDraw.Draw(im)
    d.rectangle([10, 10, 40, 40], fill=(255, 0, 0))
    d.rectangle([60, 50, 90, 85], fill=(0, 255, 0))
    imgs.append(im)

    # 2. 径向渐变 + 圆
    im = Image.new("RGB", (size, size))
    px = im.load()
    cx = cy = size / 2
    maxd = (cx ** 2 + cy ** 2) ** 0.5
    for y in range(size):
        for x in range(size):
            dd = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            v = int(255 * (1 - dd / maxd))
            px[x, y] = (v, v // 2, 255 - v)
    d = ImageDraw.Draw(im)
    d.ellipse([20, 20, 70, 70], outline=(255, 255, 0), width=3)
    imgs.append(im)

    # 3. 大块棋盘
    im = Image.new("RGB", (size, size))
    px = im.load()
    block = size // 8
    for y in range(size):
        for x in range(size):
            on = ((x // block) + (y // block)) % 2 == 0
            px[x, y] = (255, 255, 255) if on else (20, 20, 20)
    imgs.append(im)

    # 4. 竖条纹
    im = Image.new("RGB", (size, size))
    px = im.load()
    for y in range(size):
        for x in range(size):
            on = (x // 8) % 2 == 0
            px[x, y] = (0, 120, 200) if on else (200, 120, 0)
    imgs.append(im)

    # 5. 同心方块
    im = Image.new("RGB", (size, size), (240, 240, 240))
    d = ImageDraw.Draw(im)
    for i in range(0, size // 2, 8):
        d.rectangle([i, i, size - i, size - i], outline=(30, 30, 30))
    imgs.append(im)

    # 6. 斜向条纹
    im = Image.new("RGB", (size, size))
    px = im.load()
    for y in range(size):
        for x in range(size):
            on = ((x + y) // 8) % 2 == 0
            px[x, y] = (60, 200, 90) if on else (90, 60, 200)
    imgs.append(im)
    return imgs


def _save(im, path, fmt="PNG"):
    im.save(path, fmt)


def _save_jpeg(im, path, quality):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    with open(path, "wb") as fh:
        fh.write(buf.read())


def _hash_of(path):
    return similar._dhash(path, 8)


# ----- 复刻生产里的分桶匹配逻辑（只为量召回漏洞，不动生产代码）-----
def _bucketed_match(hashes, threshold):
    """与 backend.services.similar.find_similar 里的分桶逻辑一致：
    按 value>>56 分桶，只比同桶 + 相邻整数桶（±1）。返回被判定为相似的对集合。"""
    usable = [(i, v) for i, v in enumerate(hashes) if similar._informative(v)]
    buckets = {}
    for slot, (i, v) in enumerate(usable):
        buckets.setdefault(v >> 56, []).append(slot)
    pairs = set()
    # 桶内
    for members in buckets.values():
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                li, ri = usable[members[a]][0], usable[members[b]][0]
                if similar._hamming(usable[members[a]][1], usable[members[b]][1]) <= threshold:
                    pairs.add((min(li, ri), max(li, ri)))
    # 相邻桶
    keys = sorted(buckets)
    for pos, key in enumerate(keys):
        for nb in keys[pos + 1:]:
            if nb - key > 1:
                break
            for la in buckets[key]:
                for lb in buckets[nb]:
                    li, ri = usable[la][0], usable[lb][0]
                    if similar._hamming(usable[la][1], usable[lb][1]) <= threshold:
                        pairs.add((min(li, ri), max(li, ri)))
    return pairs


def _brute_match(hashes, threshold):
    pairs = set()
    for i in range(len(hashes)):
        if not similar._informative(hashes[i]):
            continue
        for j in range(i + 1, len(hashes)):
            if not similar._informative(hashes[j]):
                continue
            if similar._hamming(hashes[i], hashes[j]) <= threshold:
                pairs.add((i, j))
    return pairs


def main():
    tmp = Path(tempfile.mkdtemp(prefix="fp_sim_"))
    photos = _make_photos()

    # 构造真值集：每个 base 一组；记录的 index 顺序固定，方便后面按组判正负
    # 每组变体：0=原图, 1=jpeg90, 2=jpeg50, 3=resize06, 4=resize18, 5=crop80
    # 另加旋转组：6=rot90, 7=rot180, 8=rot270（单独统计，dHash 本来就不认旋转）
    variant_paths: list[list[str]] = []
    rotation_paths: list[list[str]] = []
    for bi, base in enumerate(photos):
        grp = []
        p = tmp / f"b{bi}_orig.png"
        _save(base, p)
        grp.append(str(p))
        p = tmp / f"b{bi}_j90.jpg"
        _save_jpeg(base, p, 90)
        grp.append(str(p))
        p = tmp / f"b{bi}_j50.jpg"
        _save_jpeg(base, p, 50)
        grp.append(str(p))
        small = base.resize((int(base.width * 0.6), int(base.height * 0.6)))
        p = tmp / f"b{bi}_r06.png"
        _save(small, p)
        grp.append(str(p))
        big = base.resize((int(base.width * 1.8), int(base.height * 1.8)))
        p = tmp / f"b{bi}_r18.png"
        _save(big, p)
        grp.append(str(p))
        w, h = base.size
        crop = base.crop((int(w * 0.1), int(h * 0.1), int(w * 0.9), int(h * 0.9)))
        p = tmp / f"b{bi}_crop.png"
        _save(crop, p)
        grp.append(str(p))
        variant_paths.append(grp)

        rot = []
        for ang in (90, 180, 270):
            p = tmp / f"b{bi}_rot{ang}.png"
            _save(base.rotate(ang, expand=True), p)
            rot.append(str(p))
        rotation_paths.append(rot)

    # 算哈希：把所有图摊平成一个大列表，记 index -> (base, variant_kind)
    flat: list[str] = []
    meta: list[tuple[int, str]] = []  # (base_index, kind)
    for bi, grp in enumerate(variant_paths):
        for kind, path in enumerate(grp):
            flat.append(path)
            meta.append((bi, f"v{kind}"))
    for bi, rot in enumerate(rotation_paths):
        for ang_i, path in enumerate(rot):
            flat.append(path)
            meta.append((bi, f"rot{90 * (ang_i + 1)}"))

    hashes = [_hash_of(p) for p in flat]

    print(f"图片总数: {len(flat)}，能解出指纹: {sum(1 for h in hashes if h is not None)}，阈值: {THRESHOLD}")
    print()

    # 1) 各"内容变体"相对原图的汉明距离（应 ≤ 阈值）
    print("== 内容变体相对原图的汉明距离（期望 ≤%d 即判相似）==" % THRESHOLD)
    kinds = ["orig", "jpeg90", "jpeg50", "resize06", "resize18", "crop80"]
    dist_by_kind = {k: [] for k in kinds[1:]}
    for bi, grp in enumerate(variant_paths):
        h0 = hashes[meta.index((bi, "v0"))]
        for kind_i, kind in enumerate(kinds[1:], start=1):
            idx = meta.index((bi, f"v{kind_i}"))
            h = hashes[idx]
            if h is not None and h0 is not None:
                dist_by_kind[kind].append(similar._hamming(h0, h))
    for kind in kinds[1:]:
        ds = dist_by_kind[kind]
        if ds:
            matched = sum(1 for d in ds if d <= THRESHOLD)
            print(f"  {kind:9s}: 距离={ds}，匹配率 {matched}/{len(ds)}")
    print()

    # 2) 旋转变体相对原图（期望不相似，量化旋转缺口）
    print("== 旋转变体相对原图的汉明距离（dHash 不认旋转，期望 >%d）==" % THRESHOLD)
    rot_dists = []
    for bi, rot in enumerate(rotation_paths):
        h0 = hashes[meta.index((bi, "v0"))]
        for path in rot:
            idx = flat.index(path)
            h = hashes[idx]
            if h is not None and h0 is not None:
                rot_dists.append(similar._hamming(h0, h))
    print(f"  旋转距离分布: {rot_dists}")
    print(f"  其中 ≤{THRESHOLD}（被误判相似，属于假阳性）: {sum(1 for d in rot_dists if d <= THRESHOLD)}/{len(rot_dists)}")
    print()

    # 3) 精度：跨原图负样本里有多少被误判相似
    print("== 跨原图负样本（不同图之间，期望不相似）==")
    neg_total = 0
    neg_false = 0
    for i in range(len(flat)):
        for j in range(i + 1, len(flat)):
            if meta[i][0] == meta[j][0]:
                continue  # 同原图不算负样本
            if hashes[i] is None or hashes[j] is None:
                continue
            neg_total += 1
            if similar._hamming(hashes[i], hashes[j]) <= THRESHOLD:
                neg_false += 1
    precision = 1 - neg_false / neg_total if neg_total else 1.0
    print(f"  负样本对: {neg_total}，误判相似: {neg_false}，precision={precision:.3f}")
    print()

    # 4) 分桶召回漏洞：正样本（同原图的内容变体对）被分桶逻辑漏掉多少
    print("== 分桶匹配召回漏洞（旧逻辑，仅用于对照）==")
    # 正样本对：同原图、且都是内容变体（排除旋转）
    pos_pairs = set()
    for bi in range(len(variant_paths)):
        idxs = [k for k, m in enumerate(meta) if m[0] == bi and m[1].startswith("v")]
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                pos_pairs.add((idxs[a], idxs[b]))
    brute = _brute_match(hashes, THRESHOLD)
    bucketed = _bucketed_match(hashes, THRESHOLD)
    pos_brute = pos_pairs & brute
    pos_bucketed = pos_pairs & bucketed
    missed = pos_brute - pos_bucketed
    print(f"  正样本对总数: {len(pos_pairs)}")
    print(f"  暴力匹配抓住: {len(pos_brute)}")
    print(f"  旧分桶逻辑抓住: {len(pos_bucketed)}")
    print(f"  ★旧分桶漏掉（应相似却没被分到一组）: {len(missed)}  ← 召回漏洞")
    if missed:
        sample = list(missed)[:5]
        for (i, j) in sample:
            print(f"     漏掉示例: base{meta[i][0]}/{meta[i][1]} vs base{meta[j][0]}/{meta[j][1]} "
                  f"hamming={similar._hamming(hashes[i], hashes[j])} "
                  f"topbyte=({hashes[i] >> 56}, {hashes[j] >> 56})")
    print()

    # 4b) 新多索引匹配召回：应 100% 抓住暴力匹配的结果
    print("== 新多索引匹配召回 ==")
    usable_real = [(k, h) for k, h in enumerate(hashes) if similar._informative(h)]
    new_pairs = set(similar._match_pairs(usable_real, THRESHOLD))
    new_pos = pos_pairs & new_pairs
    new_missed = pos_brute - new_pos
    print(f"  新匹配器抓住正样本: {len(new_pos)}/{len(pos_brute)}")
    print(f"  ★新匹配器漏掉: {len(new_missed)}  ← 应为 0")
    print()

    # 5) 旋转容忍方案：对 4 个朝向各算一次 dHash，取最小汉明
    print("== 旋转容忍方案（4 朝向取最小汉明）能救回多少旋转变体 ==")
    def _dhash_rot(im, angle):
        buf = io.BytesIO()
        im.rotate(angle, expand=True).save(buf, "PNG")
        buf.seek(0)
        import tempfile as _t
        pp = Path(_t.mkdtemp()) / "r.png"
        pp.write_bytes(buf.read())
        return similar._dhash(str(pp), 8)

    saved = 0
    for bi, base in enumerate(photos):
        h0 = _dhash_rot(base, 0)
        for ang in (90, 180, 270):
            h_rot = _dhash_rot(base, ang)
            # 旋转容忍：与 4 个朝向的原图哈希取最小汉明
            best = min(
                similar._hamming(h0, h_rot),
                similar._hamming(_dhash_rot(base, 90), h_rot),
                similar._hamming(_dhash_rot(base, 180), h_rot),
                similar._hamming(_dhash_rot(base, 270), h_rot),
            )
            if best <= THRESHOLD:
                saved += 1
    print(f"  旋转变体总数: {len(rot_dists)}，旋转容忍后能匹配: {saved}")
    print()

    print("== 结论速览 ==")
    print("  - 压缩/缩放/裁切：dHash 基本能抓（见上方匹配率）")
    print(f"  - 旋转：dHash 完全不认（{sum(1 for d in rot_dists if d <= THRESHOLD)}/{len(rot_dists)} 误判，实为缺口）")
    print(f"  - 精度：跨图负样本 precision={precision:.3f}")
    print(f"  - 旧分桶召回漏洞：漏掉 {len(missed)}/{len(pos_brute)} 个本应相似的对（已用多索引哈希修复）")
    print(f"  - 新多索引匹配召回：漏掉 {len(new_missed)}/{len(pos_brute)}（应为 0）")
    print(f"  - 旋转容忍方案可救回 {saved}/{len(rot_dists)} 个旋转变体（代价：哈希算 4 倍）")


if __name__ == "__main__":
    main()
