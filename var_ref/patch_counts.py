"""`passed = sum(...)` 在报告渲染与控制台汇总里各出现一次 —— 两处都要换成三态计数。"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent / "fix_behaviorcheck3.py"
text = PATH.read_text(encoding="utf-8")

old = '''swap(
    """    passed = sum(1 for row in rows if row.ok)""",
    """    passed = sum(1 for row in rows if row.ok is True)
    skipped = sum(1 for row in rows if row.ok is None)
    failed = len(rows) - passed - skipped""",
    "计数：通过/跳过/偏离",
)'''
new = '''#: 这一行在「渲染报告」和「控制台汇总」各出现一次，两处都要换成三态计数。
count_old = "    passed = sum(1 for row in rows if row.ok)"
count_new = (
    "    passed = sum(1 for row in rows if row.ok is True)\\n"
    "    skipped = sum(1 for row in rows if row.ok is None)\\n"
    "    failed = len(rows) - passed - skipped"
)
assert text.count(count_old) == 2, text.count(count_old)
text = text.replace(count_old, count_new)
print("✅ 计数：通过/跳过/偏离（两处）")
swap(
    """        f"- 结果：**{passed} 通过 / {len(rows) - passed} 偏离**",""",
    """        f"- 结果：**{passed} 通过 / {failed} 偏离 / {skipped} 跳过**",""",
    "报告头部的结果行也要三态",
)'''
assert old in text
PATH.write_text(text.replace(old, new), encoding="utf-8")
compile(PATH.read_text(encoding="utf-8"), str(PATH), "exec")
print("✅ 补丁脚本语法 OK")
