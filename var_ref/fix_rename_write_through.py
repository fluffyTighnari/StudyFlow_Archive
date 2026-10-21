"""修一个**真的会让人以为改名没生效**的缺陷：`findSession()` 返回的是副本。

`findSession` 为了让调用方顺手拿到 `folderId` 与 `folder`，用的是
`{ ...found, folderId, folder }` —— 一个展开出来的**浅拷贝**。
于是 `session.title = name` 改的是那个副本，紧接着的 `renderRail()`
又从 `state.folders` 重新渲染，标题当然还是旧的。
（原型里也是这样：月改名前后的截图对比看不出来，因为它改的那份马上就被丢了。）

修法：加一个 `findSessionRow()`，只用来**写**，返回列表里那个真对象。
读还是走 `findSession()`（它顺带给了 folder，很方便）。
"""

from __future__ import annotations

import pathlib

APP = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "redesign" / "app.js"
text = APP.read_text(encoding="utf-8")

OLD_HELPER = """function currentSession() { return findSession(state.currentSession); }"""
NEW_HELPER = """/**
 * 侧栏列表里**那一行本身**（用来写）。
 *
 * 与 `findSession()` 的区别是它有意的：那个函数为了顺手，返回的是展开出来的
 * 副本（多了 `folderId` 与 `folder`），改它不会改到列表 —— 于是"改名之后界面没变"。
 * 要改数据就走这个函数。
 */
function findSessionRow(id) {
  for (const folder of state.folders) {
    const found = folder.sessions.find((s) => s.id === id);
    if (found) return found;
  }
  return null;
}

function currentSession() { return findSession(state.currentSession); }"""
assert text.count(OLD_HELPER) == 1, "currentSession 锚点不对"
text = text.replace(OLD_HELPER, NEW_HELPER, 1)

OLD_RENAME = """        openRename(el, '重命名会话', session.title, async (name) => {
          try {
            await api.renameSession(id, name);
            session.title = name;
            renderRail();
            renderTopbar();
            toast('会话已重命名');
          } catch (err) {
            toast(err.message, 'danger');
          }
        });"""
NEW_RENAME = """        openRename(el, '重命名会话', session.title, async (name) => {
          try {
            await api.renameSession(id, name);
            // 改**列表里那一行**，不是 `findSession` 给的副本
            const row = findSessionRow(id);
            if (row) row.title = name;
            renderRail();
            renderTopbar();
            toast('会话已重命名');
          } catch (err) {
            toast(err.message, 'danger');
          }
        });"""
assert text.count(OLD_RENAME) == 1, "rename-session 锚点不对"
text = text.replace(OLD_RENAME, NEW_RENAME, 1)

APP.write_text(text, encoding="utf-8")
print("✅ 新增 findSessionRow；会话改名改到真对象上")
