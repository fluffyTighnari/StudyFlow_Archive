"""把 9 号补丁的最后一条（标注）改成"已经手工改好，跳过"。"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent / "fix_behaviorcheck9.py"
text = PATH.read_text(encoding="utf-8")

start = text.index("# 4) 标注：引文多试几段")
end = text.index('PATH.write_text(text, encoding="utf-8")')
text = text[:start] + "# 4) 标注那条已经手工改在文件里了（候选引文逐个试），这里不再重复。\n\n" + text[end:]
PATH.write_text(text, encoding="utf-8")
compile(text, str(PATH), "exec")
print("✅ 9 号补丁已收口")
