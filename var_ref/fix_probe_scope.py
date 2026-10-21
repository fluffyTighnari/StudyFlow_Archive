"""探针的一处查找要限定范围：归档行也带 `.session` 类。

`sessionTitled()` 原来在整页找 `.session .session-title`。归档之后，
同一个会话同时出现在「未归档」与「已归档」两处渲染里（归档行也是
`class="session archived-item"`），于是"它还在侧栏里吗"这个问题永远为真，
探针就会卡在"会话移进已归档"这一步 —— 而实际上归档是成功的。

这条值得写下来：`<div class="session ...">` 同时被两处复用，这是界面本身的设计
（同一套卡片样式），不是缺陷；**是测试的查询条件要限定到 `#folder-list`**。
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "redesign" / "src" / "selftest.js"
text = PATH.read_text(encoding="utf-8")

OLD = "  return $$('.session .session-title').find((el) => el.textContent.trim() === title) || null;"
NEW = """  // 只找「未归档」那一栏：归档行也带 `.session` 类（同一套卡片样式），
  // 不限定范围的话，"它还在侧栏吗"永远为真。
  return $$('#folder-list .session .session-title').find((el) => el.textContent.trim() === title) || null;"""

assert text.count(OLD) == 1, f"锚点出现 {text.count(OLD)} 次"
PATH.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
print("✅ sessionTitled 限定在 #folder-list 内")
