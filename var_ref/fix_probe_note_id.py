"""修探针里那条正则：笔记编号是 `N-0001`，不是 `NT-…`。

原来的写法是猜的（`/NT[-_]?\\d|NT-/`），于是**真的写进去了**也被判成失败 ——
报告里那条 `ok:false` 与实际的 `已存为笔记 N-0001` 对不上。判据要照后端的
真实格式写：`<字母>-<数字>`。
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "redesign" / "src" / "selftest.js"
text = PATH.read_text(encoding="utf-8")

OLD = """  mark('侧记回执带笔记编号', { ok: /NT[-_]?\\d|NT-/.test(text), text: text.slice(0, 60) });"""
NEW = """  // 后端的笔记编号形如 `N-0001`：判据照真实格式写，别猜前缀
  const noteId = (text.match(/已存为笔记\\s*([A-Z]+-\\d+)/) || [])[1] || null;
  mark('侧记回执带笔记编号', { ok: Boolean(noteId), noteId, text: text.slice(0, 60) });"""

assert text.count(OLD) == 1, f"锚点出现 {text.count(OLD)} 次"
PATH.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
print("✅ 探针的笔记编号判据改成真实格式")
