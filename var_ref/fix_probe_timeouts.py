"""给探针的 `waitDom` 加真实超时。

没有超时的话，一次"等不到"在报告里表现成"停在上一步、ok 一直是 null" ——
而真正的原因（比如菜单没开、接口 422）一个字都不剩。加了超时之后，
它会带着"等的是哪一步"抛出来，报告里就有具体位置了。
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "redesign" / "src" / "selftest.js"
text = PATH.read_text(encoding="utf-8")

OLD = """/** 等 DOM 出现某个东西 —— 用 MutationObserver，不用定时器（见文件头）。 */
function waitDom(check) {
  return new Promise((resolve) => {
    const first = check();
    if (first) {
      resolve(first);
      return;
    }
    const observer = new MutationObserver(() => {
      const value = check();
      if (value) {
        observer.disconnect();
        resolve(value);
      }
    });
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
    });
  });
}"""

NEW = """/**
 * 等 DOM 出现某个东西 —— 用 MutationObserver 而不是轮询。
 *
 * ★ **必须带超时**：等不到的时候如果只是"永远挂着"，报告里只会看到
 *   "停在上一步、ok 还是 null"，真正的原因一个字都不剩。带上 `label`
 *   与真实超时，失败会指到具体的那个等待上。
 */
function waitDom(check, label = '等一个界面变化', timeoutMs = 15_000) {
  return new Promise((resolve, reject) => {
    const first = check();
    if (first) {
      resolve(first);
      return;
    }
    let timer = null;
    const observer = new MutationObserver(() => {
      const value = check();
      if (value) {
        observer.disconnect();
        if (timer) clearTimeout(timer);
        resolve(value);
      }
    });
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
    });
    timer = setTimeout(() => {
      observer.disconnect();
      reject(new Error(`等不到：${label}`));
    }, timeoutMs);
  });
}"""

assert text.count(OLD) == 1, "waitDom 锚点不对"
text = text.replace(OLD, NEW, 1)

# 每一处等待都带一句话 —— 失败时报告里能直接看出卡在哪一步
LABELS = [
    ("await waitDom(() => $$('.session[data-session-id]').length > 0);",
     "await waitDom(() => $$('.session[data-session-id]').length > 0, '侧栏出现会话');"),
    ("const step = await waitDom(() => $('.steps .step[data-step-id]'));",
     "const step = await waitDom(() => $('.steps .step[data-step-id]'), '计划里出现步骤');"),
    ("""  await waitDom(() => {
    const live = $('article.msg-assistant.is-live');
    const last = $$('article.msg-assistant').pop();
    return !live && last && last.querySelector('.msg-foot');
  });""",
     """  await waitDom(() => {
    const live = $('article.msg-assistant.is-live');
    const last = $$('article.msg-assistant').pop();
    return !live && last && last.querySelector('.msg-foot');
  }, '这一轮讨论结束');"""),
    ("const form = await waitDom(() => ($('#rail-form').hidden ? null : $('#rail-form')));",
     "const form = await waitDom(() => ($('#rail-form').hidden ? null : $('#rail-form')), '新建表单出现');"),
    ("await waitDom(() => sessionTitled(title));",
     "await waitDom(() => sessionTitled(title), `新会话出现在侧栏：${title}`);"),
    ("const renameInput = await waitDom(() => $('#popover-layer .rename-input'));",
     "const renameInput = await waitDom(() => $('#popover-layer .rename-input'), '重命名小窗出现');"),
    ("await waitDom(() => sessionTitled(renamed));",
     "await waitDom(() => sessionTitled(renamed), `改名后的标题出现在侧栏：${renamed}`);"),
    ("await waitDom(() => !sessionTitled(renamed) && archivedTitled(renamed));",
     "await waitDom(() => !sessionTitled(renamed) && archivedTitled(renamed), '会话移进「已归档」');"),
    ("await waitDom(() => sessionTitled(renamed) && !archivedTitled(renamed));",
     "await waitDom(() => sessionTitled(renamed) && !archivedTitled(renamed), '会话从「已归档」回到侧栏');"),
    ("await waitDom(() => $('#confirm-dialog[open]'));",
     "await waitDom(() => $('#confirm-dialog[open]'), '删除确认框打开');"),
    ("await waitDom(() => !sessionTitled(renamed));",
     "await waitDom(() => !sessionTitled(renamed), '删除后会话从侧栏消失');"),
]

for old, new in LABELS:
    count = text.count(old)
    assert count == 1, f"锚点出现 {count} 次：{old[:40]}"
    text = text.replace(old, new, 1)

PATH.write_text(text, encoding="utf-8")
print("✅ waitDom 带超时与位置说明；11 处等待都写清了在等什么")
