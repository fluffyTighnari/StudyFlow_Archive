"""修掉补丁脚本里那两条写错锚点的 swap（检查点用例的真实代码形状与我以为的不同）。"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent / "fix_behaviorcheck3.py"
lines = PATH.read_text(encoding="utf-8").splitlines(keepends=True)

# 找到那两条错误 swap 的起止（从「检查点用计划版本」那条之前到它的 try 块结束）
text = "".join(lines)
start = text.index('swap(\n    """            "expected_version": ctx["version"],')
end = text.index('swap(\n    """                "expected_session_version": ctx["version"],')

replacement = (
    "swap(\n"
    '    \'"expected_version": ctx["version"], "result": "pass", "result_note": "讲清楚了"\',\n'
    '    \'"expected_version": ctx["plan_version"], "result": "pass", "result_note": "讲清楚了"\',\n'
    '    "检查点用计划版本",\n'
    ")\n"
)
text = text[:start] + replacement + text[end:]
PATH.write_text(text, encoding="utf-8")

compile(PATH.read_text(encoding="utf-8"), str(PATH), "exec")
print("✅ 补丁脚本语法 OK")
