"""上下文那只表：数字只在推演时给出，别让它在"对齐刷新"里被抹掉。

`/state` 里**没有**上下文计量（它只有 `snapshot`：精力 / 环境 / 压力）。
计量是 `done` 事件里的 `context`。于是出现一个很坑的时序：

1. 这一轮跑完，`finalizeLive` 把 meter 写进 `detail.context`，右栏显示 `3,353 / …`；
2. 紧接着为了对齐计划版本，`loadDetail()` 重读了 `/state`，
   把 `state.details[id]` **整个换掉** —— 新对象里的 `context` 是
   `adaptContext(null)`，也就是"没测到"；
3. 用户切一下右栏页签，刚看到的数字就没了。

修法不是"再编一个数字"，而是：重读时若这一轮没给新数字，
就把上一次**真的测到过**的那份留下来，并在旁边写清它是哪一轮的。
刷新页面之后当然还是"没测到" —— 那时候确实不知道，也不该猜。
"""

from __future__ import annotations

import pathlib

APP = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "redesign" / "app.js"
text = APP.read_text(encoding="utf-8")

OLD = """async function loadDetail(sessionId, { render = true } = {}) {
  if (!sessionId) return null;
  const payload = await api.state(sessionId);
  const detail = adaptDetail(payload);
  state.details[sessionId] = detail;
  if (render && state.currentSession === sessionId) renderSession();
  return detail;
}"""

NEW = """async function loadDetail(sessionId, { render = true } = {}) {
  if (!sessionId) return null;
  const payload = await api.state(sessionId);
  const previous = state.details[sessionId];
  const detail = adaptDetail(payload);

  // `/state` 里没有上下文计量（计量是 `done` 事件给的）。重读时若这一轮没给新数字，
  // 保留上一次**真的测到过**的那份，并写明它是哪一轮的 —— 而不是把它抹成"没测到"，
  // 也不是凭空补一个好看的数字。
  const measuredNow = detail.context && detail.context.measured;
  if (!measuredNow && previous && previous.context && previous.context.measured) {
    detail.context = {
      ...previous.context,
      note: '这一份是上一次推演结束时的数字；这一轮还没跑，所以不是最新的。',
    };
  }

  state.details[sessionId] = detail;
  if (render && state.currentSession === sessionId) renderSession();
  return detail;
}"""
assert text.count(OLD) == 1, "loadDetail 锚点不对"
text = text.replace(OLD, NEW, 1)

OLD_RENDER = """    const fresh = state.details[sessionId];
    if (fresh && (fresh.plan ? fresh.plan.version : null) !== before) renderMargin();"""
NEW_RENDER = """    const fresh = state.details[sessionId];
    // 计划版本变了要重画；上下文数字这一轮也变了，同样要重画 ——
    // 只判断计划的话，改了上下文却没改计划的那些轮次会留着旧数字。
    if (fresh && (fresh.plan ? fresh.plan.version : null) !== before) renderMargin();
    else if (state.marginTab === 'context') renderMargin();"""
assert text.count(OLD_RENDER) == 1, "对齐刷新锚点不对"
text = text.replace(OLD_RENDER, NEW_RENDER, 1)

APP.write_text(text, encoding="utf-8")
print("✅ 上下文计量不再被对齐刷新抹掉")
