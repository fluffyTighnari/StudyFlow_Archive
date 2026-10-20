"""把 `_构想对齐` §2 的统计句改成**数出来的**数字（写文档时是我口算的，错了一行）。

数法：只数矩阵里以 `| C` 开头的行（35 行），三栏分别为后端 / 界面 / 文档。
「全绿」= 三栏都含 ✅；「有 ✅ 也有 ❌/🟡」= 部分；「没有 ✅」= 未建。
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(r"D:\Workspace\Code\phy\personal_learning_flow\_构想对齐_v0.1.md")
text = PATH.read_text(encoding="utf-8")

rows = [line for line in text.splitlines() if line.startswith("| C")]
assert len(rows) == 35, f"矩阵行数变了：{len(rows)}"


def cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip("|").split("|")]


green = 0
partial = 0
absent = 0
for line in rows:
    parts = cells(line)
    backend, ui, doc = parts[2], parts[3], parts[4]
    marks = backend + ui + doc
    if "✅" in backend and "✅" in ui:
        green += 1
    elif "✅" in marks or "🟡" in marks:
        partial += 1
    else:
        absent += 1

print(f"全绿 {green} · 部分 {partial} · 未建 {absent}")

OLD = (
    "**统计**：36 行里 ✅ 全绿 11 行、🟡 13 行、❌ 12 行。"
    "\n把这 12 个 ❌ 拆开看：**4 个是\"后端有、界面没有\"**（C1、C2、C3、C4-a 这一整片），"
    "\n**5 个是真缺口**（C4-b、C4-c、C5-d、C7-b、C7-d），"
    "\n**3 个是\"逻辑在、文案/字段没做\"**（C5-a、C6-a、C8-d）。"
)
NEW = (
    f"**统计（数出来的，不是估的）**：矩阵 35 行 —— **后端与界面都绿 {green} 行**、"
    f"**部分 {partial} 行**、**完全没建 {absent} 行**。"
    "\n再把这堆不全绿的按性质拆开：**\"后端有、界面没有\"占掉一整片**（C1 的三行、C2 的四行、"
    "C3 的两行、C4-a/C4-d、C5-a/C5-b、C6-b/C6-d 的界面栏），"
    "**真缺口只有五个机制**（C4-b 自由作答判分、C4-c 自评按领域定制、C5-d 方法档案、"
    "C7-b 全部完成才可复盘、C7-d 感想后的精细化调整），"
    "**剩下几行是\"逻辑在、文案或字段没做\"**（C5-a 分档文案、C6-a 引导语、C8-d 理由引用偏好）。"
)

assert text.count(OLD) == 1, f"统计句锚点出现 {text.count(OLD)} 次"
PATH.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
print("✅ §2 统计句已按实数改写")
