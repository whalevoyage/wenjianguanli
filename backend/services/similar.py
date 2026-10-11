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


def _dhash(path: str, size: int = 8) -> int | None:
    """算一张图的 dHash。读不了或解不了码返回 None（调用方跳过，不中断整轮）。"""
    try:
        from PIL import Image

        with Image.open(path) as image:
            # 先缩到 size+1 × size 的灰度图：相邻两列比一次，正好得到 size×size 位
            small = image.convert("L").resize((size + 1, size), Image.LANCZOS)
            pixels = list(small.getdata())
    except Exception:  # noqa: BLE001  图片损坏 / 格式不支持 / 权限不足，都只是"这张跳过"
        return None

    bits = 0
    width = size + 1
    for row in range(size):
        base = row * width
        for col in range(size):
            bits = (bits << 1) | (1 if pixels[base + col] > pixels[base + col + 1] else 0)
    return bits


def _hamming(left: int, right: int) -> int:
    return bin(left ^ right).count("1")


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


def _collect(root: Path, excludes: list[str] | None, ctx: JobContext,
             min_size: int = DEFAULT_MIN_SIZE) -> list[tuple[str, int, float]]:
    """收集候选图片：(路径, 大小)。用项目统一的 iter_entries，别自己写 os.walk。"""
    found: list[tuple[str, int, float]] = []
    walked = 0

    def on_dir(_path: str, _depth: int, _dirs: list, _files: list) -> None:
        """每进一个目录报一次进度。签名是 iter_entries 定的：(路径, 深度, 目录名, 文件名)。"""
        nonlocal walked
        walked += 1
        if walked % 40 == 0:
            ctx.tick(f"已进入 {walked} 个目录，找到 {len(found)} 张图", None, None)

    for entry in fsutil.iter_entries(root, excludes=excludes, on_dir=on_dir, skip_self_protected=True):
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
    root = Path(params.get("path") or "").resolve()
    threshold = int(params.get("threshold") or DEFAULT_THRESHOLD)
    min_size = int(params.get("min_size") or DEFAULT_MIN_SIZE)

    if not root.is_dir():
        raise ValueError("这个目录不存在或不是文件夹")
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
    files = _collect(root, params.get("excludes"), ctx, min_size)
    if files:
        ctx.progress(f"找到 {len(files)} 张图片", len(files), len(files), files[0][0])
    if len(files) < 2:
        return {
            "available": True, "groups": [], "image_count": len(files), "group_count": 0,
            "redundant": 0, "redundant_text": fsutil.format_size(0),
            "scanned": len(files), "root": str(root), "threshold": threshold,
            "note": "图片太少，没什么可比的。",
        }

    ctx.phase(1, f"正在计算 {len(files)} 张图的指纹")
    hashes: list[int | None] = [None] * len(files)
    workers = max(1, min(6, int(getattr(config, "HASH_WORKERS", 8))))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_dhash, path): index for index, (path, _size, _mt) in enumerate(files)}
        done = 0
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            hashes[index] = future.result()
            done += 1
            if done % 25 == 0 or done == len(files):
                ctx.progress(f"正在计算指纹 {done}/{len(files)}", done, len(files), files[index][0])

    usable = [(index, value) for index, value in enumerate(hashes) if _informative(value)]
    if len(usable) < 2:
        return {
            "available": True, "groups": [], "image_count": len(files), "group_count": 0,
            "redundant": 0, "redundant_text": fsutil.format_size(0),
            "scanned": len(files), "root": str(root), "threshold": threshold,
            "note": "这些图片都解不出指纹（可能损坏或格式不支持）。",
        }

    # 两两比对。n² 在几千张的规模上还好（1 万张是 5000 万次异或，纯 Python 会慢），
    # 所以这里先做一次分桶：按指纹前 16 位分组，只跟邻近桶比 —— 汉明距离 ≤6 的指纹
    # 前 16 位最多差 6，按前 8 位分桶就够覆盖了。
    ctx.phase(2, "正在比对指纹")
    union = _Union(len(usable))
    buckets: dict[int, list[int]] = {}
    for slot, (index, value) in enumerate(usable):
        buckets.setdefault(value >> 56, []).append(slot)  # 前 8 位

    for members in buckets.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                left, right = members[i], members[j]
                if _hamming(usable[left][1], usable[right][1]) <= threshold:
                    union.union(left, right)

    # 相邻桶也要比：前 8 位差 1 的两张图，指纹可能只差 1 位
    keys = sorted(buckets)
    for position, key in enumerate(keys):
        for neighbour in keys[position + 1:]:
            if neighbour - key > 1:
                break
            for left in buckets[key]:
                for right in buckets[neighbour]:
                    if _hamming(usable[left][1], usable[right][1]) <= threshold:
                        union.union(left, right)

    grouped: dict[int, list[int]] = {}
    for slot in range(len(usable)):
        grouped.setdefault(union.find(slot), []).append(slot)

    groups = []
    for members in grouped.values():
        if len(members) < 2:
            continue
        items = []
        for slot in members:
            path, size, mtime = files[usable[slot][0]]
            items.append({
                "path": path,
                "name": os.path.basename(path),
                "size": size,
                "size_text": fsutil.format_size(size),
                # format_mtime 要的是时间戳，不是路径
                "mtime": fsutil.format_mtime(mtime),
                "hash": f"{usable[slot][1]:016x}",
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
        "root": str(root),
        "threshold": threshold,
        "image_count": len(files),
        "scanned": len(files),
        "group_count": len(groups),
        "redundant": sum(group["redundant"] for group in groups),
        "redundant_text": fsutil.format_size(sum(group["redundant"] for group in groups)),
        "groups": groups[:500],
        "note": "相似不等于相同：这里按画面相似度分组，删哪张请自己看图确认。",
    }


def run_similar_scan(ctx: JobContext) -> dict:
    return find_similar(ctx)
