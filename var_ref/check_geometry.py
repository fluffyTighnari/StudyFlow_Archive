"""把浏览器算出来的值，与 DSH 自己的数值对一遍。

为什么需要这一步：我看不了截图（当前模型不收图像），所以"看起来对不对"
只能换成"算出来是多少"。这里比的是**浏览器解析 `var()` 之后的最终值**，
不是我在 CSS 里写的那一行 —— 两者不同时，出问题的往往是后者。

期望值的出处：`var_ref/` 下从 DSH 运行时 CSS 里提取出来的片段
（`index.pretty.css` / `sidebar.css0.txt` / `chat.css*.txt` …）。
"""

from __future__ import annotations

import html
import json
import pathlib
import re

SRC = pathlib.Path(r"D:\Workspace\Code\phy\personal_learning_flow\var_ref\probe.html")

#: 令牌值 —— 来自 DSH 的三份色板（platform / alias / semantic）。
EXPECT_TOKENS = {
    "labelPrimary": "#0f1115",
    "bgBase": "#fff",
    "bubble": "#edf3fe",
    "selector": "#f5f6f7",
    "infoFill": "#4176e6",
    "deepseek500": "#4176e6",
    "sidebarWidth": "280px",
}

#: 几何 —— 来自 DSH 组件 CSS 的真实数字。
EXPECT_ELEMENTS: dict[str, dict[str, str]] = {
    "sidebar": {"padding": "6px 12px"},
    "newSession": {"height": "38px", "border-radius": "12px", "padding": "8px 16px"},
    "panelRow": {"min-height": "36px", "border-radius": "8px", "padding": "7px 8px"},
    "header": {
        "min-height": "76px",
        "padding-top": "10px",
        "padding-left": "20px",
        "padding-right": "28px",
    },
    "tab": {"font-size": "13px", "font-weight": "500"},
    "bubble": {
        "border-radius": "22px",
        "padding": "10px 16px",
        "background-color": "rgb(237, 243, 254)",
    },
    "body": {"font-size": "14px", "line-height": "24px"},
    "card": {"border-radius": "22px"},
    "input": {"min-height": "36px", "padding": "4px 8px 0px 14px"},
    "add": {"width": "28px", "height": "28px", "border-radius": "999px"},
    "send": {"width": "34px", "height": "34px", "border-radius": "999px"},
    "model": {
        "height": "28px",
        "font-size": "13px",
        "font-weight": "500",
        "border-radius": "8px",
    },
    "stats": {"font-size": "11px"},
}


def main() -> int:
    text = SRC.read_text(encoding="utf-8")
    match = re.search(r'<pre id="out">(.*?)</pre>', text, re.S)
    if match is None:
        print("没拿到输出 —— 页面脚本没跑完，或者 CSS 没加载")
        return 2
    raw = match.group(1)
    if raw.strip() == "":
        print("探针的 <pre> 是空的 —— 页面脚本抛异常了")
        return 2
    data = json.loads(html.unescape(raw))

    print("== 令牌（浏览器解析出来的值） ==")
    for key, value in data["tokens"].items():
        print(f"  {key:16} {value}")

    bad = 0
    print("\n== 令牌核对 ==")
    for key, want in EXPECT_TOKENS.items():
        got = str(data["tokens"].get(key, "")).strip()
        ok = got.lower() == want.lower()
        bad += 0 if ok else 1
        print(f"  {'OK ' if ok else '!! '} {key:16} 期望 {want:12} 实际 {got}")

    print("\n== 几何核对 ==")
    by_id = {item["id"]: item for item in data["elements"]}

    # 第一列（侧栏）固定 280；第二列随视口走，所以只比第一列。
    frame_cols = str(by_id.get("frame", {}).get("grid-template-columns", ""))
    ok = frame_cols.split(" ")[0] == "280px"
    bad += 0 if ok else 1
    print(f"  {'OK ' if ok else '!! '} frame.grid-template-columns  期望 280px …  实际 {frame_cols}")

    for element_id, props in EXPECT_ELEMENTS.items():
        got = by_id.get(element_id, {})
        if got.get("missing"):
            print(f"  !!  {element_id}: 页面上没有这个元素")
            bad += 1
            continue
        for prop, want in props.items():
            actual = str(got.get(prop, "<没有这个属性>")).strip()
            ok = actual == want
            bad += 0 if ok else 1
            print(f"  {'OK ' if ok else '!! '} {element_id}.{prop:20} 期望 {want:20} 实际 {actual}")

    print("\n== 其它 ==")
    print("  body 背景:", data["bodyBg"], " 文字:", data["bodyColor"])
    print("\n不合的项:", bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
