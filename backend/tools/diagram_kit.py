"""画图工具的底子：**放不下就报错，而不是悄悄溢出**。

我看不到生成的图（模型没有视觉输入），所以"排版好看"不能靠眼睛验收。
这个模块把每一条排版规则变成可执行检查：

- 文字宽度用**真实字体量**（`textlength`），不按字数估 —— 中文与西文的字宽差一倍；
- 一个框里放不下就**自动缩字号**，缩到下限还放不下就**报错**；
- 每段文字都必须落在画布内、且落在自己的框内；
- 相邻文字块**不许重叠**（重叠就是压字，肉眼看不出但我能算出来）。

它同时记录所有文字块的位置，供 `check_diagrams.py` 复核。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 900
INK = (17, 17, 17)
GRAY = (110, 110, 110)
LIGHT = (232, 232, 232)
FONTS = {"bold": "C:/Windows/Fonts/msyhbd.ttc", "regular": "C:/Windows/Fonts/msyh.ttc"}
MARGIN = 40


def font(size: int, kind: str = "regular") -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONTS[kind], size)


@dataclass
class TextBlock:
    x: float
    y: float
    width: float
    height: float
    text: str
    size: int
    parent: tuple[float, float, float, float] | None = None

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.x + self.width, self.y + self.height)


@dataclass
class Sheet:
    title: str
    subtitle: str = ""
    blocks: list[TextBlock] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.image = Image.new("RGB", (W, H), "white")
        self.draw = ImageDraw.Draw(self.image)
        self.draw.text((60, 40), self.title, font=font(34, "bold"), fill=INK)
        if self.subtitle:
            self.draw.text((60, 90), self.subtitle, font=font(19), fill=GRAY)
        self.draw.line([(60, 124), (W - 60, 124)], fill=LIGHT, width=2)

    # ── 量字 ──────────────────────────────────────────────────────────
    def _measure(self, text: str, size: int, kind: str = "regular") -> float:
        return self.draw.textlength(text, font=font(size, kind))

    def _wrap(self, text: str, size: int, max_width: float, kind: str = "regular") -> list[str]:
        """按**实测宽度**断行；显式 \n 保留。"""
        lines: list[str] = []
        for paragraph in text.split("\n"):
            current = ""
            for char in paragraph:
                candidate = current + char
                if self._measure(candidate, size, kind) <= max_width or not current:
                    current = candidate
                else:
                    lines.append(current)
                    current = char
            lines.append(current)
        return lines

    # ── 原语 ──────────────────────────────────────────────────────────
    def text(self, x: float, y: float, text: str, size: int = 20, bold: bool = False,
             fill=INK, anchor: str = "la", max_width: float | None = None) -> None:
        kind = "bold" if bold else "regular"
        width = self._measure(text, size, kind)
        if max_width and width > max_width:
            self.problems.append(f"文字超宽 {width:.0f}>{max_width:.0f}：{text[:24]}")
        self.draw.text((x, y), text, font=font(size, kind), fill=fill, anchor=anchor)
        left = x if anchor.startswith("l") else (x - width / 2 if anchor.startswith("m") else x - width)
        top = y if anchor[1] in "as" else y - size
        self.blocks.append(TextBlock(left, top, width, size * 1.35, text, size))

    def box(self, x: float, y: float, w: float, h: float, text: str = "", size: int = 20,
            bold: bool = False, fill=None, outline=INK, radius: int = 10,
            min_size: int = 13, pad: float = 14, wrap_at: int | None = None) -> None:
        self.draw.rounded_rectangle([x, y, x + w, y + h], radius=radius,
                                    fill=fill, outline=outline, width=2)
        if not text:
            return
        kind = "bold" if bold else "regular"
        inner = w - 2 * pad
        for attempt in range(size, min_size - 1, -1):
            lines = self._wrap(text, attempt, inner, kind)
            line_h = attempt * 1.45
            if len(lines) * line_h <= h - 10:
                break
        else:
            self.problems.append(
                f"框内放不下（最小字号 {min_size} 仍溢出）：{text[:26]}"
            )
            return
        total = len(lines) * line_h
        top = y + (h - total) / 2
        for index, line in enumerate(lines):
            width = self._measure(line, attempt, kind)
            if width > inner + 1:
                self.problems.append(f"行宽超框 {width:.0f}>{inner:.0f}：{line[:20]}")
            cx = x + w / 2
            self.draw.text((cx, top + index * line_h + line_h / 2), line,
                           font=font(attempt, kind), fill=INK, anchor="mm")
            self.blocks.append(
                TextBlock(cx - width / 2, top + index * line_h, width, attempt * 1.35,
                          line, attempt, parent=(x, y, w, h))
            )

    def arrow(self, x1: float, y1: float, x2: float, y2: float, width: int = 2,
              head: int = 12, dashed: bool = False) -> None:
        if dashed:
            length = math.hypot(x2 - x1, y2 - y1)
            steps = max(int(length / 14), 1)
            for step in range(0, steps, 2):
                self.draw.line([
                    (x1 + (x2 - x1) * step / steps, y1 + (y2 - y1) * step / steps),
                    (x1 + (x2 - x1) * (step + 1) / steps, y1 + (y2 - y1) * (step + 1) / steps),
                ], fill=INK, width=width)
        else:
            self.draw.line([(x1, y1), (x2, y2)], fill=INK, width=width)
        angle = math.atan2(y2 - y1, x2 - x1)
        for side in (0.45, -0.45):
            self.draw.line(
                [(x2, y2), (x2 - head * math.cos(angle + side), y2 - head * math.sin(angle + side))],
                fill=INK, width=width,
            )

    def table(self, x: float, y: float, widths: list[float], rows: list[list[str]],
              size: int = 18, row_h: int = 44, header: bool = True) -> float:
        cursor_y = y
        for row_index, row in enumerate(rows):
            is_header = header and row_index == 0
            self.draw.rectangle([x, cursor_y, x + sum(widths), cursor_y + row_h],
                                fill=LIGHT if is_header else None, outline=INK, width=1)
            cursor_x = x
            for width, cell in zip(widths, row):
                kind = "bold" if is_header else "regular"
                # 单元格内按实测宽度缩字号，最窄的情况下也绝不溢出格
                fit = size
                while fit > 11 and self._measure(cell, fit, kind) > width - 16:
                    fit -= 1
                if self._measure(cell, fit, kind) > width - 16:
                    self.problems.append(f"单元格放不下：{cell[:20]}")
                self.draw.text((cursor_x + 8, cursor_y + row_h / 2), cell,
                               font=font(fit, kind), fill=INK, anchor="lm")
                cursor_x += width
            cursor_y += row_h
        return cursor_y

    def note(self, text: str, y: float = 790, size: int = 17) -> None:
        self.draw.line([(60, y - 16), (W - 60, y - 16)], fill=LIGHT, width=2)
        for fit in range(size, 12, -1):
            lines = self._wrap(text, fit, W - 120)
            if y + len(lines) * (fit + 6) <= H - 20:
                break
        else:
            self.problems.append(f"脚注放不下：{text[:24]}")
            return
        for index, line in enumerate(lines):
            self.draw.text((60, y + index * (fit + 8)), line, font=font(fit), fill=GRAY)
            self.blocks.append(
                TextBlock(60, y + index * (fit + 8), self._measure(line, fit), fit * 1.35, line, fit)
            )

    # ── 收工检查 ──────────────────────────────────────────────────────
    def verify(self) -> list[str]:
        problems = list(self.problems)
        for block in self.blocks:
            x1, y1, x2, y2 = block.box
            if x1 < MARGIN - 24 or y1 < 20 or x2 > W - 20 or y2 > H - 10:
                problems.append(f"出界：{block.text[:20]} box={block.box}")
            if block.parent:
                px, py, pw, ph = block.parent
                if x1 < px - 1 or y1 < py - 1 or x2 > px + pw + 1 or y2 > py + ph + 1:
                    problems.append(f"超出所在框：{block.text[:20]}")
        # 压字：两个不同来源的文字块相交超过 40% 就算压字
        for index, first in enumerate(self.blocks):
            for second in self.blocks[index + 1:]:
                if first.text == second.text and abs(first.y - second.y) < 1:
                    continue
                ax1, ay1, ax2, ay2 = first.box
                bx1, by1, bx2, by2 = second.box
                overlap_w = min(ax2, bx2) - max(ax1, bx1)
                overlap_h = min(ay2, by2) - max(ay1, by1)
                if overlap_w > 6 and overlap_h > 6:
                    area = overlap_w * overlap_h
                    smaller = min(first.width * first.height, second.width * second.height)
                    if smaller and area / smaller > 0.40:
                        problems.append(
                            f"压字：「{first.text[:14]}」与「{second.text[:14]}」重叠 {area / smaller:.0%}"
                        )
        return problems

    def save(self, path: Path) -> None:
        problems = self.verify()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(path, optimize=True)
        size_kb = path.stat().st_size / 1024
        status = "OK" if not problems else f"{len(problems)} 处问题"
        print(f"  {path.name:18} {size_kb:6.0f}KB  文字块 {len(self.blocks):3}  {status}")
        for problem in problems:
            print(f"      ✗ {problem}")
        if problems:
            raise SystemExit(f"{path.name} 排版有问题，先修再往下")


#: 旧名（先写成 label 的调用处不用改）。
Sheet.label = Sheet.text  # type: ignore[attr-defined]
