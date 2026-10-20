"""从 DSH 的插件包里找 SVG 图标（`d:` 路径 + viewBox），供复刻时对齐。

★ 只读：这些图标属于 DSH 自己的包。我们提取是为了让复刻的界面图标一致。
"""

from __future__ import annotations

import pathlib
import re

REF = pathlib.Path(r"D:\Workspace\Code\phy\personal_learning_flow\var_ref")


def scan(name: str) -> None:
    path = REF / name
    if not path.is_file():
        print(f"{name}: 没有这个文件")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    print(f"=== {name} ===")
    # 图标组件通常是：{ viewBox: "0 0 16 16", children: [ ... { d: "M..." } ] }
    for vb in sorted(set(re.findall(r'"0 0 (\d+) (\d+)"', text))):
        print(f"  viewBox 0 0 {vb[0]} {vb[1]}")
    paths = re.findall(r'd:\s*"([Mm][^"]{8,400})"', text)
    print(f"  路径数 {len(paths)}")
    return


if __name__ == "__main__":
    for n in ("chat.js", "sidebar.js", "app-entry.js", "conversation.js"):
        scan(n)
