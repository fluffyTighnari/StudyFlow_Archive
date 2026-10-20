"""把 DSH 应用入口包里的图标路径连同上下文一起导出，便于按名字认领。"""

from __future__ import annotations

import pathlib
import re

REF = pathlib.Path(r"D:\Workspace\Code\phy\personal_learning_flow\var_ref")
text = (REF / "app-entry.js").read_text(encoding="utf-8", errors="replace")

rows: list[str] = []
for match in re.finditer(r'd:\s*"([Mm][^"]{4,500})"', text):
    start = max(0, match.start() - 220)
    before = text[start : match.start()].replace("\n", " ")
    names = re.findall(r'([A-Za-z][A-Za-z0-9]{2,30})(?::|")', before)
    tail = " / ".join(names[-3:])
    rows.append(f"[{tail}]\n   d={match.group(1)}")

(REF / "icons.txt").write_text("\n".join(rows), encoding="utf-8")
print(f"导出 {len(rows)} 条 → var_ref/icons.txt")
