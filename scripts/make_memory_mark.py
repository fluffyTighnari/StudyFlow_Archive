"""把「记忆.png」抠成透明底品牌标识（GrabCut 分割）。

背景是暖米色（≈ RGB 247,232,213），主体是偏白的纸页与红绳。纸面与背景颜色
接近，纯颜色阈值会把纸面白化，因此用 GrabCut：
1. 主体大致落在画面中部，先用矩形先验跑 GrabCut；
2. 四边泛洪把确定背景连通区压成透明；
3. 前景 mask 做形态学清理 + 羽化，得到平滑 alpha；
4. 裁掉右下角水印与多余边距，居中回方形画布，输出多尺寸透明 PNG / ico。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageFilter

SRC = Path(r"C:\Users\lilex\Downloads\记忆.png")
OUT_DIR = Path(r"D:\Workspace\Code\phy\personal_learning_flow\frontend\redesign\assets")

BG = np.array([247.2, 232.5, 212.8], dtype=np.float32)
SOFT = 12.0


def flood_edge(mask_fg: np.ndarray) -> np.ndarray:
    """从四边泛洪：与边缘连通的非前景像素判为确定背景。"""
    h, w = mask_fg.shape
    sure_bg = ~mask_fg
    visited = np.zeros_like(mask_fg, dtype=bool)
    stack: list[tuple[int, int]] = []
    for x in range(w):
        for y in (0, h - 1):
            if sure_bg[y, x] and not visited[y, x]:
                visited[y, x] = True
                stack.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if sure_bg[y, x] and not visited[y, x]:
                visited[y, x] = True
                stack.append((y, x))
    while stack:
        y, x = stack.pop()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and sure_bg[ny, nx] and not visited[ny, nx]:
                visited[ny, nx] = True
                stack.append((ny, nx))
    return visited


def main() -> None:
    pil = Image.open(SRC).convert("RGB")
    arr = np.asarray(pil)
    h, w, _ = arr.shape

    # ── GrabCut：矩形先验（主体居中，四周留边）──
    mask = np.zeros((h, w), np.uint8)
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    rect = (int(w * 0.08), int(h * 0.06), int(w * 0.84), int(h * 0.82))
    cv2.grabCut(arr, mask, rect, bgd, fgd, 6, cv2.GC_INIT_WITH_RECT)
    gc_fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)

    # ── 边缘泛洪：连通到边界的非前景 → 确定背景 ──
    dist = np.linalg.norm(arr.astype(np.float32) - BG, axis=2)
    close_bg = dist <= SOFT
    edge_bg = flood_edge(gc_fg > 0)
    sure_bg = edge_bg & close_bg

    alpha = gc_fg.astype(np.float32) / 255.0
    alpha[sure_bg] = 0.0

    # 形态学清理小孔洞、平滑边缘
    a8 = (alpha * 255).astype(np.uint8)
    kernel = np.ones((5, 5), np.uint8)
    a8 = cv2.morphologyEx(a8, cv2.MORPH_CLOSE, kernel, iterations=2)
    a8 = cv2.morphologyEx(a8, cv2.MORPH_OPEN, kernel, iterations=1)
    a8 = cv2.GaussianBlur(a8, (0, 0), 1.4)

    alpha_img = Image.fromarray(a8, mode="L")
    rgba = pil.convert("RGBA")
    rgba.putalpha(alpha_img)

    # 裁掉底部水印（约 10% 高度）
    rgba = rgba.crop((0, 0, w, int(h * 0.90)))

    bbox = rgba.getbbox()
    if bbox:
        rgba = rgba.crop(bbox)
    cw, ch = rgba.size
    pad = int(max(cw, ch) * 0.06)
    side = max(cw, ch) + pad * 2
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(rgba, ((side - cw) // 2, (side - ch) // 2), rgba)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    canvas.resize((512, 512), Image.LANCZOS).save(OUT_DIR / "memory-mark.png", optimize=True)
    canvas.resize((256, 256), Image.LANCZOS).save(OUT_DIR / "memory-mark@256.png", optimize=True)
    canvas.resize((64, 64), Image.LANCZOS).save(OUT_DIR / "memory-mark@64.png", optimize=True)
    canvas.resize((64, 64), Image.LANCZOS).save(
        OUT_DIR / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)]
    )
    print("saved", OUT_DIR / "memory-mark.png", (OUT_DIR / "memory-mark.png").stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
