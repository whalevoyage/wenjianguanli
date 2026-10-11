"""按品牌规格生成 Atlas 的窗口/任务栏/安装包图标。

规格直接抄自 frontend/src/components/AppSidebar.vue 的品牌方章：
  方章 40×40 · border-radius 12px（= 30%）· linear-gradient(145deg, #35a1ff, #0071e3)
  山脊 22×22 居中（= 方章的 55%）：后峰 M 17.5 29 L 26 16.5 L 34.5 29 Z（白 55%）
                                     前峰 M 5.5 29 L 16 12 L 26.5 29 Z（纯白）
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "atlas.ico"
PREVIEW = Path(__file__).resolve().parent / "atlas-256.png"

BOARD = 40.0            # 品牌 SVG 的坐标空间
SS = 2048               # 超采样尺寸（先大后缩，边缘才平滑）
BASE = 1024             # ICO 主图尺寸
RADIUS_RATIO = 12 / 40  # 方章圆角 = 30%
FROM = (0x35, 0xA1, 0xFF)
TO = (0x00, 0x71, 0xE3)

# CSS 里 145deg：0deg 指向上，顺时针为正 → 方向向量 (sin, -cos)
_theta = math.radians(145)
DX, DY = math.sin(_theta), -math.cos(_theta)

BACK_PEAK = [(17.5, 29), (26, 16.5), (34.5, 29)]
FRONT_PEAK = [(5.5, 29), (16, 12), (26.5, 29)]


def _lerp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))


def _gradient(size: int) -> Image.Image:
    """沿 145deg 的线性渐变。CSS 的渐变线长度 = |w·dx| + |h·dy|。"""
    img = Image.new("RGB", (size, size))
    px = img.load()
    length = abs(size * DX) + abs(size * DY)
    cx = cy = size / 2
    inv = 1.0 / length
    for y in range(size):
        for x in range(size):
            t = ((x - cx) * DX + (y - cy) * DY) * inv + 0.5
            t = 0.0 if t < 0 else (1.0 if t > 1 else t)
            px[x, y] = (_lerp(FROM[0], TO[0], t), _lerp(FROM[1], TO[1], t), _lerp(FROM[2], TO[2], t))
    return img


def build() -> Image.Image:
    # 渐变在超采样尺寸上算完再缩，避免小图出现色带
    grad = _gradient(BASE).resize((SS, SS), Image.LANCZOS)

    # 圆角遮罩
    mask = Image.new("L", (SS, SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SS - 1, SS - 1], radius=int(SS * RADIUS_RATIO), fill=255)

    canvas = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    canvas.paste(grad.convert("RGBA"), (0, 0), mask)

    scale = SS / BOARD
    layer = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    pen = ImageDraw.Draw(layer)
    # 后峰先画（白 55%），前峰后画（纯白）—— 与品牌 SVG 的叠放顺序一致
    pen.polygon([(x * scale, y * scale) for x, y in BACK_PEAK], fill=(255, 255, 255, 140))
    pen.polygon([(x * scale, y * scale) for x, y in FRONT_PEAK], fill=(255, 255, 255, 255))
    canvas = Image.alpha_composite(canvas, layer)

    return canvas.resize((BASE, BASE), Image.LANCZOS)


def main() -> None:
    art = build()
    art.save(PREVIEW, format="PNG")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    art.save(OUT, format="ICO", sizes=sizes)
    print("已生成:", OUT)
    print("  文件大小: %.1f KB" % (OUT.stat().st_size / 1024))
    print("  内含尺寸:", ", ".join(f"{w}x{h}" for w, h in sizes))
    print("预览图:", PREVIEW)


if __name__ == "__main__":
    main()
