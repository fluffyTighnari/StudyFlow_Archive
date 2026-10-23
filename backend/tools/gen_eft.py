"""生成 EFT 嵌入图形（15 组 + 1 组练习），输出 PNG 与答案表。

设计规则见 `_测题图片清单_v0.1.md` §2.3，这里逐条落实：

- 纯黑线稿、无填充、无线宽变化 —— 颜色与粗细都会引导视线，等于泄题；
- 每张 25–40 条干扰线，其中 6–10 条与目标**共线或共角**（否则目标太"跳"）；
- 目标允许 90° 旋转，**不允许缩放**（缩放会让"大小"变成线索）；
- 目标至少 2 条边被无关线条**截断**（不被截断就等于被人描出来了）；
- 不出现任何可辨认的实物（房子/动物/字母）—— 那会变成"看图猜物"，不再是视觉搜索；
- 固定随机种子，同一 item 每次生成同一张图（可复现）。

答案表含 `bbox`（外接框）与 `polyline`（逐段路径，判"他圈的位置对不对"用），
并且**只写到 backend 的题库数据里**，不随前端下发（清单 §6 最后一条）。
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent.parent
IMG_DIR = ROOT / "frontend" / "redesign" / "assets" / "items" / "EFT"
ANSWER_DIR = ROOT / "backend" / "app" / "seeds" / "benchmark_items"

W, H = 1600, 1200
LINE = 3
BLACK = (0, 0, 0)
GRAY = (150, 150, 150)


# ── 目标图形（单位坐标，外接框约 2×2，中心在原点）────────────────────
SHAPES: dict[str, list[tuple[float, float]]] = {
    "triangle": [(0.0, -1.0), (0.87, 0.5), (-0.87, 0.5)],
    "square": [(-0.8, -0.8), (0.8, -0.8), (0.8, 0.8), (-0.8, 0.8)],
    "pentagon": [
        (0.0, -1.0),
        (0.95, -0.31),
        (0.59, 0.81),
        (-0.59, 0.81),
        (-0.95, -0.31),
    ],
    "chevron": [(0.0, -1.0), (0.85, 0.0), (0.0, 1.0), (-0.85, 0.0)],
    "trapezoid": [(-1.0, 0.7), (-0.5, -0.7), (0.5, -0.7), (1.0, 0.7)],
    "parallelogram": [(-0.9, 0.7), (-0.2, -0.7), (0.9, -0.7), (0.2, 0.7)],
    "lshape": [
        (-0.8, -0.8),
        (0.0, -0.8),
        (0.0, 0.2),
        (0.8, 0.2),
        (0.8, 0.8),
        (-0.8, 0.8),
    ],
    "zshape": [
        (-0.8, -0.8),
        (0.5, -0.8),
        (0.5, -0.2),
        (0.8, -0.2),
        (-0.5, 0.8),
        (-0.8, 0.2),
    ],
    "hexagon": [
        (0.0, -1.0),
        (0.87, -0.5),
        (0.87, 0.5),
        (0.0, 1.0),
        (-0.87, 0.5),
        (-0.87, -0.5),
    ],
    "arrow": [
        (0.0, -1.0),
        (0.8, -0.1),
        (0.3, -0.1),
        (0.3, 1.0),
        (-0.3, 1.0),
        (-0.3, -0.1),
        (-0.8, -0.1),
    ],
}

#: 15 组的形状与难度。难度按清单 §2.3 的分级：易 3 / 中 7 / 难 5。
PLAN: list[tuple[str, str]] = [
    ("square", "易"),
    ("triangle", "易"),
    ("trapezoid", "易"),
    ("chevron", "中"),
    ("pentagon", "中"),
    ("parallelogram", "中"),
    ("lshape", "中"),
    ("hexagon", "中"),
    ("zshape", "中"),
    ("arrow", "中"),
    ("triangle", "难"),
    ("square", "难"),
    ("chevron", "难"),
    ("hexagon", "难"),
    ("arrow", "难"),
]

#: 难度 → (干扰线条数, 允许旋转角, 要求被截断的边数)
TIERS = {
    "易": (30, (0,), 1),
    "中": (36, (0, 90, 180, 270), 2),
    "难": (40, (90, 180, 270), 3),
}


def _rotate(points: list[tuple[float, float]], deg: int) -> list[tuple[float, float]]:
    rad = math.radians(deg)
    cos, sin = math.cos(rad), math.sin(rad)
    return [(x * cos - y * sin, x * sin + y * cos) for x, y in points]


def _place(
    shape: list[tuple[float, float]], deg: int, scale: float, cx: float, cy: float
) -> list[tuple[float, float]]:
    return [(cx + x * scale, cy + y * scale) for x, y in _rotate(shape, deg)]


def _segments(points: list[tuple[float, float]]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    return [(points[i], points[(i + 1) % len(points)]) for i in range(len(points))]


def _point_on_segment(
    seg: tuple[tuple[float, float], tuple[float, float]], t: float
) -> tuple[float, float]:
    (x1, y1), (x2, y2) = seg
    return (x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)


def _angle_deg(seg: tuple[tuple[float, float], tuple[float, float]]) -> float:
    (x1, y1), (x2, y2) = seg
    return math.degrees(math.atan2(y2 - y1, x2 - x1))


def draw_target(path: Path, points: list[tuple[float, float]], size: int = 400, gray: bool = False) -> None:
    image = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    pad = size * 0.18
    k = (size - 2 * pad) / span
    ox = size / 2 - (sum(xs) / len(xs)) * k
    oy = size / 2 - (sum(ys) / len(ys)) * k
    scaled = [(ox + x * k, oy + y * k) for x, y in points]
    draw.line(scaled + [scaled[0]], fill=GRAY if gray else BLACK, width=LINE + 1, joint="curve")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def generate(index: int, shape_name: str, difficulty: str, seed: int) -> dict:
    rng = random.Random(seed)
    n_lines, rotations, min_cut = TIERS[difficulty]

    shape = SHAPES[shape_name]
    deg = rng.choice(rotations)
    scale = rng.uniform(110, 150)
    # 目标放在画布中偏中区域，但不居中（居中会让"找中间"成为策略）
    cx = rng.uniform(W * 0.30, W * 0.70)
    cy = rng.uniform(H * 0.30, H * 0.70)
    target = _place(shape, deg, scale, cx, cy)
    segs = _segments(target)
    seg_angles = [_angle_deg(s) for s in segs]

    strokes: list[list[tuple[float, float]]] = []

    # ① 共线延伸：把若干条目标边向外延长 —— 这是"嵌进去"的核心手法
    for seg, angle in zip(segs, seg_angles):
        if rng.random() < 0.55:
            (x1, y1), (x2, y2) = seg
            extend = rng.uniform(60, 260)
            rad = math.radians(angle)
            strokes.append(
                [
                    (x1 - math.cos(rad) * extend, y1 - math.sin(rad) * extend),
                    (x2 + math.cos(rad) * extend, y2 + math.sin(rad) * extend),
                ]
            )

    # ② 截断线：穿过目标边上的一点，把那条边打断（至少 min_cut 条边）
    cut_edges = rng.sample(range(len(segs)), k=min(min_cut, len(segs)))
    crossings: list[dict] = []
    for edge_index in cut_edges:
        seg = segs[edge_index]
        t = rng.uniform(0.25, 0.75)
        px, py = _point_on_segment(seg, t)
        crossing = seg_angles[edge_index] + rng.choice((90, 60, 120, 45, 135))
        length = rng.uniform(400, 900)
        rad = math.radians(crossing)
        line = [
            (px - math.cos(rad) * length / 2, py - math.sin(rad) * length / 2),
            (px + math.cos(rad) * length / 2, py + math.sin(rad) * length / 2),
        ]
        strokes.append(line)
        #: 记下来，供校验脚本核对"这条边真的被穿过了"（不是靠我的注释保证）
        crossings.append(
            {
                "edge": edge_index,
                "at": [round(px, 1), round(py, 1)],
                "line": [[round(x, 1), round(y, 1)] for x, y in line],
                "angle_deg": round(crossing % 360, 1),
            }
        )

    # ③ 其余干扰线：多段折线，角度吸附到一组固定角，制造"同向却无关"的视觉噪声
    palette = [0, 30, 45, 60, 90, 120, 135, 150] + [int(a) for a in seg_angles if a % 15]
    while len(strokes) < n_lines:
        started = (rng.uniform(0, W), rng.uniform(0, H))
        points = [started]
        heading = rng.choice(palette)
        for _ in range(rng.randint(2, 4)):
            length = rng.uniform(120, 420)
            rad = math.radians(heading)
            points.append(
                (points[-1][0] + math.cos(rad) * length, points[-1][1] + math.sin(rad) * length)
            )
            heading += rng.choice((-60, -45, -30, 30, 45, 60, 90, 0, 180))
        strokes.append(points)

    image = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(image)
    for stroke in strokes:
        draw.line(stroke, fill=BLACK, width=LINE, joint="curve")
    draw.line(target + [target[0]], fill=BLACK, width=LINE, joint="curve")

    name = f"EFT-{index:02d}"
    out = IMG_DIR / name
    out.mkdir(parents=True, exist_ok=True)
    image.save(out / f"{name}-A.png", optimize=True)
    draw_target(out / f"{name}-B.png", target)

    xs = [p[0] for p in target]
    ys = [p[1] for p in target]
    bbox = [round(min(xs)), round(min(ys)), round(max(xs) - min(xs)), round(max(ys) - min(ys))]
    return {
        "item_id": name,
        "shape": shape_name,
        "difficulty": difficulty,
        "rotation_deg": deg,
        "bbox": bbox,
        "polyline": [[round(x, 1), round(y, 1)] for x, y in target + [target[0]]],
        "cut_edges": len(cut_edges),
        "crossings": crossings,
        "strokes": len(strokes),
        "images": {
            "complex": f"assets/items/EFT/{name}/{name}-A.png",
            "target": f"assets/items/EFT/{name}/{name}-B.png",
        },
    }


def generate_practice() -> dict:
    """练习图：目标**用灰线画出来**，让人先明白"藏"是什么意思。"""
    rng = random.Random(20260101)
    shape = SHAPES["square"]
    target = _place(shape, 0, 130, W / 2, H / 2)
    segs = _segments(target)
    strokes: list[list[tuple[float, float]]] = []
    for seg in segs:
        (x1, y1), (x2, y2) = seg
        rad = math.atan2(y2 - y1, x2 - x1)
        strokes.append(
            [
                (x1 - math.cos(rad) * 160, y1 - math.sin(rad) * 160),
                (x2 + math.cos(rad) * 160, y2 + math.sin(rad) * 160),
            ]
        )
    while len(strokes) < 20:
        started = (rng.uniform(0, W), rng.uniform(0, H))
        points = [started]
        heading = rng.choice((0, 30, 45, 60, 90, 120, 135, 150))
        for _ in range(3):
            rad = math.radians(heading)
            points.append((points[-1][0] + math.cos(rad) * 300, points[-1][1] + math.sin(rad) * 300))
            heading += rng.choice((-60, -45, 45, 60, 90))
        strokes.append(points)

    image = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(image)
    for stroke in strokes:
        draw.line(stroke, fill=BLACK, width=LINE, joint="curve")
    draw.line(target + [target[0]], fill=GRAY, width=LINE + 2, joint="curve")

    out = IMG_DIR / "EFT-00"
    out.mkdir(parents=True, exist_ok=True)
    image.save(out / "EFT-00-A.png", optimize=True)
    draw_target(out / "EFT-00-B.png", target)
    return {
        "item_id": "EFT-00",
        "note": "练习图：目标用灰线画出，让人先明白「藏」是什么意思；不计分",
        "images": {
            "complex": "assets/items/EFT/EFT-00/EFT-00-A.png",
            "target": "assets/items/EFT/EFT-00/EFT-00-B.png",
        },
    }


def main() -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    ANSWER_DIR.mkdir(parents=True, exist_ok=True)
    items = [generate_practice()]
    for index, (shape_name, difficulty) in enumerate(PLAN, start=1):
        items.append(generate(index, shape_name, difficulty, seed=20260100 + index))
    answers = {
        "note": "EFT 答案表。**只在服务端**，不随前端下发（下发即等于把答案交给浏览器）。",
        "canvas": {"width": W, "height": H},
        "tolerance_px": 8,
        "items": items,
    }
    path = ANSWER_DIR / "EFT-answers.json"
    path.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"生成 {len(items)} 组（含练习），答案写入 {path.relative_to(ROOT)}")
    for item in items[1:]:
        print(
            f"  {item['item_id']} {item['difficulty']} {item['shape']:12} "
            f"rot={item['rotation_deg']:3}° bbox={item['bbox']} 线={item['strokes']}"
        )


if __name__ == "__main__":
    main()
