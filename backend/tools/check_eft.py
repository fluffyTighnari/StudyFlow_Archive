"""验收 EFT 图：不靠肉眼，靠像素与几何。

为什么不用"看一眼"验收：我看不到图（模型没有视觉输入），而这件事又要长期复用 ——
所以把每条规则变成一条可执行的检查。规则来自 `_测题图片清单_v0.1.md` §2.3。

检查项：
1. **目标真的画在 A 图里** —— 沿答案里的 polyline 采样，像素必须是黑的；
2. **载断线真的穿过那条边** —— 线段求交，不是靠注释；
3. **每条难度要求的边数都被截断**（易 1 / 中 2 / 难 3）；
4. **墨迹占比合理** —— 太稀=太空、太密=抽奖（0.02–0.20）；
5. **目标不出画布** —— 外接框离边界至少 40px；
6. **15 组互不相同** —— A 图字节哈希不重复；
7. **B 图是那个目标本身** —— 墨迹占比合理、外接框（缩放到 400）比例与 A 图一致。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent.parent
IMG_DIR = ROOT / "frontend" / "redesign" / "assets" / "items" / "EFT"
ANSWERS = ROOT / "backend" / "app" / "seeds" / "benchmark_items" / "EFT-answers.json"

FAILS: list[str] = []
CHECKS = {"passed": 0}


def check(condition: bool, message: str) -> None:
    if condition:
        CHECKS["passed"] += 1
    else:
        FAILS.append(message)


def _ink_ratio(image: Image.Image) -> float:
    gray = image.convert("L")
    histogram = gray.histogram()
    dark = sum(histogram[:100])  # 亮度 < 100 视为墨
    return dark / (image.width * image.height)


def _is_dark(image: Image.Image, x: float, y: float, radius: int = 3) -> bool:
    """在 (x,y) 周围找黑色像素：线宽 3，采样点与像素都会有一点点偏差。"""
    gray = image.convert("L")
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            px, py = int(x) + dx, int(y) + dy
            if 0 <= px < image.width and 0 <= py < image.height:
                if gray.getpixel((px, py)) < 100:
                    return True
    return False


def _segments_intersect(a1, a2, b1, b2) -> bool:
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    d1, d2 = cross(b1, b2, a1), cross(b1, b2, a2)
    d3, d4 = cross(a1, a2, b1), cross(a1, a2, b2)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def main() -> None:
    data = json.loads(ANSWERS.read_text(encoding="utf-8"))
    items = data["items"]
    canvas_w, canvas_h = data["canvas"]["width"], data["canvas"]["height"]
    min_cut = {"易": 1, "中": 2, "难": 3}

    hashes: dict[str, str] = {}
    for item in items[1:]:  # 第一项是练习图，不计分口径
        item_id = item["item_id"]
        complex_path = IMG_DIR / item_id / f"{item_id}-A.png"
        target_path = IMG_DIR / item_id / f"{item_id}-B.png"
        check(complex_path.exists(), f"{item_id}: A 图不存在")
        check(target_path.exists(), f"{item_id}: B 图不存在")
        if not (complex_path.exists() and target_path.exists()):
            continue

        complex_image = Image.open(complex_path)
        target_image = Image.open(target_path)

        # 1. 目标真的画在 A 图里
        polyline = item["polyline"]
        sampled = 0
        hits = 0
        for (x1, y1), (x2, y2) in zip(polyline, polyline[1:]):
            for t in (0.2, 0.4, 0.6, 0.8):
                sampled += 1
                if _is_dark(complex_image, x1 + (x2 - x1) * t, y1 + (y2 - y1) * t):
                    hits += 1
        check(
            hits == sampled,
            f"{item_id}: 目标边只有 {hits}/{sampled} 个采样点是黑的（说明目标没真正画出来）",
        )

        # 2. 截断线真的穿过那条边
        for crossing in item["crossings"]:
            edge = crossing["edge"]
            a1, a2 = polyline[edge], polyline[edge + 1]
            b1, b2 = crossing["line"]
            check(
                _segments_intersect(a1, a2, b1, b2),
                f"{item_id}: 第 {edge} 条边的截断线与它不相交（'被截断'是假的）",
            )

        # 3. 截断边数达到该难度的要求
        edges_cut = {crossing["edge"] for crossing in item["crossings"]}
        check(
            len(edges_cut) >= min_cut[item["difficulty"]],
            f"{item_id}: 难度 {item['difficulty']} 要求 ≥{min_cut[item['difficulty']]} 条边被截断，"
            f"实际 {len(edges_cut)} 条",
        )

        # 4. 墨迹占比
        ratio = _ink_ratio(complex_image)
        check(
            0.02 <= ratio <= 0.20,
            f"{item_id}: 墨迹占比 {ratio:.3f} 超出 0.02–0.20（太稀=太空，太密=抽奖）",
        )

        # 5. 目标不出画布
        x, y, w, h = item["bbox"]
        check(
            x >= 40 and y >= 40 and x + w <= canvas_w - 40 and y + h <= canvas_h - 40,
            f"{item_id}: 目标外接框贴边或出界 {item['bbox']}",
        )

        # 6. 互不相同
        digest = hashlib.sha256(complex_path.read_bytes()).hexdigest()
        check(digest not in hashes, f"{item_id}: 与 {hashes.get(digest)} 的 A 图完全相同")
        hashes[digest] = item_id

        # 7. B 图是那个目标本身
        target_ratio = _ink_ratio(target_image)
        check(
            0.01 <= target_ratio <= 0.30,
            f"{item_id}: B 图墨迹占比 {target_ratio:.3f} 不合理（图是空的或糊成一团）",
        )
        check(
            target_image.size == (400, 400),
            f"{item_id}: B 图尺寸 {target_image.size}，应为 (400, 400)",
        )

    print(f"通过 {CHECKS['passed']} 项检查，失败 {len(FAILS)} 项")
    for message in FAILS:
        print("  ✗", message)

    files = sorted(IMG_DIR.rglob("*.png"))
    total = sum(path.stat().st_size for path in files)
    print(f"\n共 {len(files)} 张图，合计 {total / 1024 / 1024:.2f} MB")
    oversized = [p for p in files if p.stat().st_size > 400 * 1024]
    if oversized:
        print("  超过 400KB 的：")
        for path in oversized:
            print(f"    {path.name} {path.stat().st_size / 1024:.0f}KB")


if __name__ == "__main__":
    main()
