"""相似图片查重（感知哈希）。

精确查重（`duplicates.py`）只能找**字节完全一样**的文件：同一张图存了两份、
其中一份被聊天软件压过或缩放过，SHA256 就完全不同，查不出来。
这是查重最常被抱怨的缺口，也是同类工具（czkawka 等）的招牌功能。

这里用 dHash（差值哈希）：把图缩成 9×8 灰度，逐行比较相邻像素的明暗，
得到 64 位指纹。视觉上相似的图，指纹的汉明距离就小。

★ 两条刻意的原则：

1. **相似 ≠ 相同，绝不并进「完全重复」** —— 相似组独立成组，
   界面会明确写「删哪个请自己看图确认」。自动合并的后果是误删。
2. **没有 Pillow 就明说，不装作支持** —— 依赖缺失时返回 `available: False` 与一句人话，
   而不是抛异常或返回空结果（空结果会被读成"没有相似图片"）。

Pillow 只在**真正要解码图片**时才 import，所以没装它也不影响其它功能。
"""
from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path

from .. import config, fsutil
from ..jobs import JobContext

# 能解码的图片类型。不做 HEIC/SVG（Pillow 默认不支持，硬塞会得到一堆错误）
IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".jfif",
}

# 指纹比对默认阈值：64 位里允许差 6 位。调大更宽松（误报变多），调小更严格
DEFAULT_THRESHOLD = 6
# 太小的图（图标、缩略图）缩到 9×8 后几乎没有细节，容易互相误判，直接跳过
DEFAULT_MIN_SIZE = 4 * 1024
# 单次最多比对多少张，防止在几十万张图的目录里跑成无底洞
MAX_IMAGES = 20000


def available() -> bool:
    """能不能算感知哈希 —— 取决于有没有 Pillow。"""
    try:
        import PIL

        return bool(PIL.__version__)
    except Exception:  # noqa: BLE001
        return False


def _pixels(image, size: int) -> list[int]:
    """把一张已打开的图缩成 size+1 × size 的灰度像素序列（相邻两列比一次，得 size×size 位）。"""
    from PIL import Image

    small = image.convert("L").resize((size + 1, size), Image.LANCZOS)
    # Pillow 12 起 getdata() 弃用（预计 14 移除），新接口是 get_flattened_data()；
    # 老 Pillow 没有新接口，所以做个兼容取用，别把运行环境直接框死。
    try:
        return list(small.get_flattened_data())
    except AttributeError:
        return list(small.getdata())


def _bits_from_pixels(pixels: list[int], size: int) -> int:
    """逐行比较左右相邻像素的明暗，得到 64 位指纹。"""
    bits = 0
    width = size + 1
    for row in range(size):
        base = row * width
        for col in range(size):
            bits = (bits << 1) | (1 if pixels[base + col] > pixels[base + col + 1] else 0)
    return bits


def _dhash(path: str, size: int = 8) -> int | None:
    """算一张图的 dHash。读不了或解不了码返回 None（调用方跳过，不中断整轮）。"""
    try:
        from PIL import Image

        with Image.open(path) as image:
            pixels = _pixels(image, size)
    except Exception:  # noqa: BLE001  图片损坏 / 格式不支持 / 权限不足，都只是"这张跳过"
        return None
    return _bits_from_pixels(pixels, size)


# 旋转容忍要试的朝向（度）。Pillow 的 rotate 是逆时针，90 的整数倍都是无损的。
ROTATIONS = (0, 90, 180, 270)


def _dhash_rotations(path: str, size: int = 8) -> list[int]:
    """一次解码，返回 0/90/180/270 四个朝向的 dHash（顺序同 ROTATIONS）。

    ★ **只为「旋转容忍」开的那条路**：普通匹配仍走 `_dhash`（只算一个朝向）。
      这里**只解码一次**，另外三个朝向在内存里旋转得到 —— 绝不为每个朝向各开一次文件、
      各解一次码（那才是 4× 的真实代价所在）。解不了码返回 []（调用方当作"这张跳过"）。
    """
    try:
        from PIL import Image

        with Image.open(path) as image:
            base = image.convert("L")  # 先转一次灰度，四个朝向都基于它旋转
            out: list[int] = []
            for angle in ROTATIONS:
                turned = base if angle == 0 else base.rotate(angle, expand=True)
                out.append(_bits_from_pixels(_pixels(turned, size), size))
            return out
    except Exception:  # noqa: BLE001
        return []


def _hamming(left: int, right: int) -> int:
    return bin(left ^ right).count("1")


def _hamming_neighbors(value: int, width: int) -> list[int]:
    """返回与 16 位 value 汉明距离 ≤ width 的所有 16 位值（含自身）。

    多索引哈希里用来「按段取值的邻域」找候选：width=ceil(threshold/4)，
    抽屉原理保证任意 Hamming≤threshold 的对必在某一 16 位段上落在邻域内。
    """
    result = [value]
    if width <= 0:
        return result
    from itertools import combinations

    for k in range(1, width + 1):
        for combo in combinations(range(16), k):
            v = value
            for bit in combo:
                v ^= 1 << bit
            result.append(v)
    return result


def _match_pairs(usable: list[tuple[int, int]], threshold: int) -> list[tuple[int, int]]:
    """返回所有汉明距离 ≤ threshold 的对 (slot_i, slot_j)，i<j（slot 是 usable 内的下标）。

    用多索引哈希（Norouzi 2008）：把 64 位指纹切成 4 段 16 位，分别建倒排；
    对每个 item 在「任意一段」上取 ≤ width=ceil(threshold/4) 位的邻域做候选，再验真汉明。
    抽屉原理保证 Hamming≤threshold 的对必然在某一段被捕获 —— **零漏召回**。

    旧实现只按最高 8 位分桶、只比相邻整数桶（±1），会漏掉最高字节整数差 >1 的相似对
    （实测丢 ~15% 真相似对）。这里用段内汉明邻域取代「整数相邻桶」，从根上消除该漏洞，
    复杂度仍是 O(n) 级候选而非 O(n²)。
    """
    n = len(usable)
    if n < 2:
        return []
    width = max(1, -(threshold // -4))  # ceil(threshold / 4)
    inverted: list[dict[int, list[int]]] = [{} for _ in range(4)]
    for slot, (_, value) in enumerate(usable):
        for c in range(4):
            seg = (value >> (c * 16)) & 0xFFFF
            inverted[c].setdefault(seg, []).append(slot)

    pairs: set[tuple[int, int]] = set()
    for slot, (_, value) in enumerate(usable):
        for c in range(4):
            seg = (value >> (c * 16)) & 0xFFFF
            for nb in _hamming_neighbors(seg, width):
                for other in inverted[c].get(nb, ()):
                    if other <= slot:
                        continue
                    if _hamming(usable[slot][1], usable[other][1]) <= threshold:
                        pairs.add((slot, other))
    return list(pairs)


def _informative(value: int | None) -> bool:
    """指纹有没有信息量。

    ★ 必须排掉全 0 / 全 1 的指纹：那样的图没有任何明暗起伏（纯色、极平滑），
      dHash 对它们无意义 —— 更要命的是**所有纯色图都会得到同一个全 0 指纹**，
      放进分组就会把一堆互不相干的纯色图判成"相似"。
      宁可漏掉（纯色图本来就没什么可查的），也不能误报。
    """
    if value is None:
        return False
    ones = bin(value).count("1")
    return 0 < ones < 64


class _Union:
    """并查集：把"互相相似"的图连成一组。"""

    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, index: int) -> int:
        while self.parent[index] != index:
            self.parent[index] = self.parent[self.parent[index]]
            index = self.parent[index]
        return index

    def union(self, left: int, right: int) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[b] = a


def _collect(roots: list[Path], excludes: list[str] | None, ctx: JobContext,
             min_size: int = DEFAULT_MIN_SIZE) -> list[tuple[str, int, float]]:
    """收集候选图片：(路径, 大小)。用项目统一的 iter_entries_multi，别自己写 os.walk。"""
    found: list[tuple[str, int, float]] = []
    walked = 0

    def on_dir(_path: str, _depth: int, _dirs: list, _files: list) -> None:
        """每进一个目录报一次进度。签名是 iter_entries 定的：(路径, 深度, 目录名, 文件名)。"""
        nonlocal walked
        walked += 1
        if walked % 40 == 0:
            ctx.tick(f"已进入 {walked} 个目录，找到 {len(found)} 张图", None, None)

    for _root, entry in fsutil.iter_entries_multi(roots, excludes=excludes, on_dir=on_dir, skip_self_protected=True):
        if ctx.cancelled():
            break
        suffix = os.path.splitext(entry.name)[1].lower()
        if suffix not in IMAGE_EXTS:
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if info.st_size < min_size:
            continue
        found.append((entry.path, info.st_size, info.st_mtime))
        if len(found) >= MAX_IMAGES:
            break
    return found


def find_similar(ctx: JobContext) -> dict:
    """主流程：收集图片 → 算指纹 → 按汉明距离聚类 → 出一份候选清单（只读）。"""
    params = ctx.params
    roots, source_notes = fsutil.resolve_sources(params)
    threshold = int(params.get("threshold") or DEFAULT_THRESHOLD)
    # 注意用 `is None` 而不是 `or`：`min_size=0` 是合法输入（比所有文件都大就全收），
    # 写成 `or DEFAULT` 会把 0 当成"没传"，静默退回默认阈值。
    _min = params.get("min_size")
    min_size = DEFAULT_MIN_SIZE if _min is None else int(_min)

    if not available():
        # 明确说清"缺什么"，而不是返回空结果（空结果会被读成"没有相似图片"）
        return {
            "available": False,
            "reason": "缺少 Pillow 依赖，无法解码图片做相似比对。运行 `pip install pillow` 后重试。",
            "groups": [],
            "image_count": 0,
            "scanned": 0,
        }

    ctx.phase(0, "正在收集图片")
    files = _collect(roots, params.get("excludes"), ctx, min_size)
    if files:
        ctx.progress(f"找到 {len(files)} 张图片", len(files), len(files), files[0][0])
    if len(files) < 2:
        return {
            "available": True, "groups": [], "image_count": len(files), "group_count": 0,
            "redundant": 0, "redundant_text": fsutil.format_size(0),
            "scanned": len(files), "root": str(roots[0]), "threshold": threshold,
            "sources": [str(r) for r in roots], "source_count": len(roots), "notes": source_notes,
            "rotations": bool(params.get("rotations")),
            "note": "图片太少，没什么可比的。",
        }

    rotations = bool(params.get("rotations"))
    ctx.phase(1, f"正在计算 {len(files)} 张图的指纹")
    workers = max(1, min(6, int(getattr(config, "HASH_WORKERS", 8))))

    # 匹配的基本单位是「条目 (图片下标, 指纹)」：
    #  · 普通模式：每张图 1 个条目；
    #  · 旋转容忍：每张图最多 4 个条目（四个朝向），任一朝向与对方某一朝向相近即算一组。
    # display_hash 只用于展示（取 0° 或第一个有信息量的朝向），不参与匹配。
    display_hash: dict[int, int] = {}
    entries: list[tuple[int, int]] = []
    hasher = _dhash_rotations if rotations else _dhash

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(hasher, path): index for index, (path, _size, _mt) in enumerate(files)}
        done = 0
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            raw = future.result()
            done += 1
            if done % 25 == 0 or done == len(files):
                ctx.progress(f"正在计算指纹 {done}/{len(files)}", done, len(files), files[index][0])
            if rotations:
                values = raw or []
                good = [v for v in values if _informative(v)]
                if not good:
                    continue
                display_hash[index] = values[0] if _informative(values[0]) else good[0]
                for value in good:
                    entries.append((index, value))
            elif _informative(raw):
                display_hash[index] = raw
                entries.append((index, raw))

    usable_images = sorted({index for index, _ in entries})
    if len(usable_images) < 2:
        return {
            "available": True, "groups": [], "image_count": len(files), "group_count": 0,
            "redundant": 0, "redundant_text": fsutil.format_size(0),
            "scanned": len(files), "root": str(roots[0]), "threshold": threshold,
            "sources": [str(r) for r in roots], "source_count": len(roots), "notes": source_notes,
            "rotations": rotations,
            "note": "这些图片都解不出指纹（可能损坏或格式不支持）。",
        }

    # 比对。直接 n² 在几千张以上会爆（1 万张是 5000 万次异或），所以用多索引哈希
    # （Norouzi 2008）做候选剪枝：64 位指纹切成 4 段 16 位分别建倒排，每个条目只要在
    # 「任意一段」上与候选相差 ≤ ceil(threshold/4) 位就列为候选再验真汉明。抽屉原理保证
    # 汉明 ≤ threshold 的对必在某段被捕获 → 零漏召回。旋转模式下条目最多 ×4，量级不变。
    ctx.phase(2, "正在比对指纹")
    match_pairs = _match_pairs(entries, threshold)
    union = _Union(len(files))  # 按「图片」维度合并，不是按条目
    for left, right in match_pairs:
        li, ri = entries[left][0], entries[right][0]
        if li != ri:
            union.union(li, ri)

    grouped: dict[int, list[int]] = {}
    for index in usable_images:
        grouped.setdefault(union.find(index), []).append(index)

    groups = []
    for members in grouped.values():
        if len(members) < 2:
            continue
        items = []
        for index in members:
            path, size, mtime = files[index]
            items.append({
                "path": path,
                "name": os.path.basename(path),
                "size": size,
                "size_text": fsutil.format_size(size),
                # format_mtime 要的是时间戳，不是路径
                "mtime": fsutil.format_mtime(mtime),
                "hash": f"{display_hash[index]:016x}",
                "source": fsutil.source_of(Path(path), roots),
            })
        # 大的排前面：相似图里大的那份细节通常最全，建议保留它，其余算"多余"
        items.sort(key=lambda item: item["size"], reverse=True)
        redundant = sum(item["size"] for item in items[1:])
        groups.append({
            "count": len(items),
            "size": sum(item["size"] for item in items),
            "size_text": fsutil.format_size(sum(item["size"] for item in items)),
            "redundant": redundant,
            "redundant_text": fsutil.format_size(redundant),
            "files": items,
            "keep": items[0]["path"],
        })

    groups.sort(key=lambda group: (-group["redundant"], -group["count"]))
    ctx.phase(3, "统计完成")
    return {
        "available": True,
        "root": str(roots[0]),
        "sources": [str(r) for r in roots],
        "source_count": len(roots),
        "notes": source_notes,
        "threshold": threshold,
        "rotations": rotations,
        "image_count": len(files),
        "scanned": len(files),
        "group_count": len(groups),
        "redundant": sum(group["redundant"] for group in groups),
        "redundant_text": fsutil.format_size(sum(group["redundant"] for group in groups)),
        "groups": groups[:500],
        "note": (
            "相似不等于相同：这里按画面相似度分组，删哪张请自己看图确认。"
            + ("（已开启旋转容忍：同一张图旋转 90°/180°/270° 也能匹配）" if rotations else "")
        ),
    }


def run_similar_scan(ctx: JobContext) -> dict:
    return find_similar(ctx)
