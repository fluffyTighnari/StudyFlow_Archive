"""把 behaviorcheck 里的两处问题修掉：ASCII 引号（中文串里）与 ctx["note_error"]。"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "backend" / "tools" / "behaviorcheck.py"
text = PATH.read_text(encoding="utf-8")

# 1) 中文句子里的 ASCII 双引号会把 Python 字符串截断 —— 一律换成「」
pairs = [
    ('必须能指认"哪一步没成"。', "必须能指认「哪一步没成」。"),
    ('要的是"链路通不通"，不是"模型聪不聪明"', "要的是「链路通不通」，不是「模型聪不聪明」"),
    ('"两轮的话不一样"', "「两轮的话不一样」"),
    ('只验"有没有答、答得一样不一样"，不验"答得对不对"', "只验「有没有答、答得一样不一样」，不验「答得对不对」"),
    ('"- **长时间运行**', '"- **长时间运行**'),
]

for old, new in pairs:
    if old in text:
        text = text.replace(old, new)

# 2) ctx["note_error"] → ctx.note_error（RunContext 上是个方法，不是字典键）
text = text.replace('ctx["note_error"](', "ctx.note_error(")

PATH.write_text(text, encoding="utf-8")

# 3) 立刻编译一遍，语法错在这里就露出来
compile(PATH.read_text(encoding="utf-8"), str(PATH), "exec")
print("✅ behaviorcheck.py 语法 OK")
