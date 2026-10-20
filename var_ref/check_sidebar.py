"""用无头 Edge 把页面真的渲染一遍，然后在 DOM 里核对"侧栏该有的东西都在"。

## 为什么不用截图

模型读不到图片。截图只能证明"渲染出了像素"，证明不了
"搜索框的 placeholder 是不是那句中文""文件夹那一行有没有子行"。

## 为什么不用 Playwright

这台机器上没有装（也不该为了一个检查装一个浏览器驱动）。
Edge 自带的 `--headless=new --dump-dom` 会在 JS 跑完之后把 DOM 打出来，
配合 `--virtual-time-budget` 等 React 与那几次 fetch 落地 —— 够用了。

## 检查什么

不是"页面能打开"，而是**这一版新增的每一个可交互件都在 DOM 里，
且它的语义属性是对的**（`aria-expanded`、`aria-label`、`placeholder`）。
缺一个就报 ❌ 并列表，不猜"大概是没渲染完"。
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

#: 渲染 + "等页面画到输入卡"的重试。两个脚本共用一份实现（见 `check_v3`）。
from check_v3 import dump_ready  # noqa: E402  （放在常量后面，读起来顺序更自然）

#: (名字, 判据) —— 判据是对 DOM 文本的正则。
#: 用正则在整份 DOM 上找，而不是解析成树：我们要回答的是
#: "这个属性/这段文字出现过吗"，而 `--dump-dom` 的输出是一行。
CHECKS: list[tuple[str, str]] = [
    ("搜索框在（placeholder 是那句中文）", r'placeholder="搜索文件夹与会话"'),
    ("搜索框有 aria-label", r'aria-label="搜索文件夹与会话"'),
    ("视图选项键在", r'aria-label="视图选项（分组与排序）"'),
    ("文件夹行存在", r'class="dsh-folderRow'),
    ("文件夹有独立的展开/收起键", r'class="dsh-folderToggle'),
    ("每个文件夹有一个「+」（在该文件夹下新建会话）", r'aria-label="在「'),
    ("每个文件夹有「⋯」", r'的更多操作"'),
    ("新建会话键在（不用先选文件夹）", r"新建会话"),
    ("侧栏有「工作区」那一行", r"工作区"),
    ("输入框在（可以直接说话）", r'aria-label="说点什么"'),
]

#: 只在新会话被聚焦/有资料时才出现，所以单独一串、允许缺席。
OPTIONAL: list[tuple[str, str]] = [
    ("资料卡片区（当前会话有资料时才出现）", r'class="dsh-attachRow"'),
    ("已归档分组（有归档条目时才出现）", r'class="dsh-archivedArea"'),
]


def find_edge() -> str:
    for path in EDGE_CANDIDATES:
        if pathlib.Path(path).exists():
            return path
    raise SystemExit("找不到 Edge。这台机器上必须有它才能做无头渲染检查。")


def dump(url: str) -> str:
    edge = find_edge()
    with tempfile.TemporaryDirectory(prefix="dsh-dom-") as profile:
        completed = subprocess.run(
            [
                edge,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={profile}",
                "--virtual-time-budget=9000",
                "--dump-dom",
                url,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    if completed.returncode != 0:
        print(f"⚠ Edge 退出码 {completed.returncode}（DOM 仍然可能可用）")
    return completed.stdout


def main(argv: list[str]) -> int:
    url = argv[1] if len(argv) > 1 else "http://127.0.0.1:8187/"
    # 渲染与"等页面画到输入卡"的重试都复用 `check_v3` 的 ——
    # 冷启动那一下（新 profile、所有接口冷读）会让 DOM 停在"还没选中会话"，
    # 而那份重试的注释只该有一份。
    dom = dump_ready(url)
    pathlib.Path("var_ref/sidebar_dom.html").write_text(dom, encoding="utf-8")
    print(f"DOM {len(dom)} 字符（已存 var_ref/sidebar_dom.html）\n")

    failures: list[str] = []
    for name, pattern in CHECKS:
        if re.search(pattern, dom):
            print(f"✅ {name}")
        else:
            print(f"❌ {name}")
            failures.append(name)

    print()
    for name, pattern in OPTIONAL:
        hit = re.search(pattern, dom) is not None
        print(f"{'✅' if hit else '·'} {name}{'' if hit else '（这一轮没有）'}")

    if "还没有文件夹" in dom and re.search(r'class="dsh-folderRow', dom) is None:
        print("\n⚠ 页面上写着「还没有文件夹」—— 可能后端没数据，也可能请求失败了。")

    if failures:
        print(f"\n❌ {len(failures)} 项缺失：")
        for name in failures:
            print(f"   · {name}")
        return 1
    print("\n✅ 侧栏该有的都在 DOM 里。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
