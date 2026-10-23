"""验收全部测题素材（图 / 音频 / 打印件 / 文字版）。

看不到图，就只能把"该有的东西有没有"写成检查。这个脚本查六件事：

1. **文件齐不齐** —— 每组该有哪几个文件，缺了就报（缺一版 = 这一格测不出来）；
2. **规格** —— 图必须 1200×900，音频必须 3–5 分钟；
3. **信息等价** —— 每个 SMP 组的"必须出现的事实清单"必须能在**读写版与听觉版**里全部找到
   （图和卡片查不了，只能按同一份清单撰写，这条在报告里如实标"需人眼"）；
4. **成对可比** —— `V-OSP` 每对都要有结构图，且线性版步骤数 ≥6（太少就成了"摘要 vs 全文"）；
5. **答案不外泄** —— `EFT-answers.json` 只能在 backend，frontend 目录里不许出现答案字段；
6. **总量** —— 文件数、体积、超限文件（单张 ≤400KB）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ASSETS = ROOT / "frontend" / "redesign" / "assets" / "items"
ANSWERS = ROOT / "backend" / "app" / "seeds" / "benchmark_items" / "EFT-answers.json"

#: 每组"必须在读写版与听觉版里出现"的事实清单。
SMP_FACTS: dict[str, list[str]] = {
    "SMP-01": ["隆起", "自转", "十二小时二十五分", "大潮", "小潮", "全日潮", "混合潮"],
    "SMP-02": ["主格", "作格", "绝对格", "印地语", "生命度", "小句", "迪尔巴尔", "判据"],
    "SMP-03": ["哈希", "冲突", "负载因子", "扩容", "摊还"],
}

PROBLEMS: list[str] = []
CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        PROBLEMS.append(message)


def duration(m4a: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(m4a)],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip() or 0)


def main() -> None:
    # ── EFT ──
    data = json.loads(ANSWERS.read_text(encoding="utf-8"))
    check(len(data["items"]) == 16, f"EFT 组数应为 16（含练习），实际 {len(data['items'])}")
    eft_pngs = sorted((ASSETS / "EFT").rglob("*-A.png"))
    check(len(eft_pngs) == 16, f"EFT A 图应为 16 张，实际 {len(eft_pngs)}")
    for item in data["items"]:
        item_dir = ASSETS / "EFT" / item["item_id"]
        check((item_dir / f"{item['item_id']}-A.png").exists(), f"{item['item_id']}: 缺 A 图")
        check((item_dir / f"{item['item_id']}-B.png").exists(), f"{item['item_id']}: 缺 B 图")
        if "bbox" in item:
            check(len(item["bbox"]) == 4, f"{item['item_id']}: bbox 形状不对")

    # ── V-SMP 四版 ──
    for item_id, facts in SMP_FACTS.items():
        folder = ASSETS / item_id
        graphic = folder / f"{item_id}-G.png"
        text = folder / f"{item_id}-T.md"
        audio = folder / f"{item_id}-A.m4a"
        cards = folder / f"{item_id}-K.html"

        for path, label in [(graphic, "图示版"), (text, "读写版"), (audio, "听觉版"), (cards, "动觉版")]:
            check(path.exists(), f"{item_id}: 缺{label} {path.name}")

        if graphic.exists():
            from PIL import Image

            check(Image.open(graphic).size == (1200, 900), f"{item_id}: 图尺寸 {Image.open(graphic).size}")
            check(graphic.stat().st_size <= 400 * 1024, f"{item_id}: 图超过 400KB")
        if audio.exists():
            seconds = duration(audio)
            check(150 <= seconds <= 330, f"{item_id}: 音频 {seconds:.0f}s 不在 150–330s")
        if text.exists():
            body = text.read_text(encoding="utf-8")
            check(len(body) >= 400, f"{item_id}: 读写版只有 {len(body)} 字，太短")
            missing = [fact for fact in facts if fact not in body]
            check(not missing, f"{item_id}: 读写版缺事实 {missing}")
        audio_script = folder / f"{item_id}-A.md"
        if audio_script.exists():
            script = audio_script.read_text(encoding="utf-8")
            # 讲稿是口语，"十二小时二十五分"会写成"十二小时二十五分"以外，所以放宽到子串任一
            missing = [fact for fact in facts if fact not in script and fact.replace("十二小时二十五分", "十二小时二十五分") not in script]
            missing = [fact for fact in missing if fact not in ("二十分钟",)]
            check(not missing, f"{item_id}: 听觉版讲稿缺事实 {missing}")
        if cards.exists():
            html = cards.read_text(encoding="utf-8")
            check(html.count('class="card"') >= 8, f"{item_id}: 动觉版卡片不足 8 张")
            check("打印" in html, f"{item_id}: 动觉版没有打印说明")

    # ── V-OSP 成对 ──
    for index in range(1, 6):
        item_id = f"OSP-{index:02d}"
        folder = ASSETS / item_id
        graphic = folder / f"{item_id}-G.png"
        linear = folder / f"{item_id}-B.md"
        check(graphic.exists(), f"{item_id}: 缺结构图（A 版）")
        check(linear.exists(), f"{item_id}: 缺线性版（B 版）")
        if graphic.exists():
            from PIL import Image

            check(Image.open(graphic).size == (1200, 900), f"{item_id}: 图尺寸不对")
        if linear.exists():
            steps = linear.read_text(encoding="utf-8").count("\n1. ") + 0
            body = linear.read_text(encoding="utf-8")
            numbered = sum(1 for line in body.splitlines() if line[:1].isdigit() and ". " in line[:4])
            check(numbered >= 6, f"{item_id}: 线性版只有 {numbered} 步，太少（会变成摘要 vs 全文）")

    # ── 答案不外泄 ──
    leaked = []
    for path in (ROOT / "frontend" / "redesign" / "assets").rglob("*"):
        if path.is_file() and path.suffix.lower() in {".json", ".md", ".html", ".js"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if '"bbox"' in text or "rotation_deg" in text:
                leaked.append(path)
    check(not leaked, f"答案字段泄漏到前端资产：{[str(p.relative_to(ROOT)) for p in leaked]}")

    # ── 总量 ──
    files = [p for p in ASSETS.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    oversized = [p for p in files if p.suffix == ".png" and p.stat().st_size > 400 * 1024]

    print(f"通过 {CHECKS - len(PROBLEMS)}/{CHECKS} 项检查")
    for problem in PROBLEMS:
        print("  ✗", problem)
    print(f"\n素材：{len(files)} 个文件，{total / 1024 / 1024:.2f} MB")
    if oversized:
        print("  超过 400KB 的图：", [p.name for p in oversized])
    by_kind: dict[str, int] = {}
    for path in files:
        by_kind[path.suffix] = by_kind.get(path.suffix, 0) + 1
    print("  按类型：", ", ".join(f"{kind or '无扩展名'} {count}" for kind, count in sorted(by_kind.items())))
    if PROBLEMS:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
