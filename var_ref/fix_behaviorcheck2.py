"""把误伤的那个 case 标题改回正常字符串。"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "backend" / "tools" / "behaviorcheck.py"
text = PATH.read_text(encoding="utf-8")

broken = '@case("讨论", 「两轮的话不一样」,'
fixed = '@case("讨论", "两轮的话不一样",'
assert text.count(broken) == 1, text.count(broken)
PATH.write_text(text.replace(broken, fixed), encoding="utf-8")

compile(PATH.read_text(encoding="utf-8"), str(PATH), "exec")
print("✅ 语法 OK")
