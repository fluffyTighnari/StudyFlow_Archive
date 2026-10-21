"""探针补一段：侧记要**真的写进库**。

原来那段是界面自己 setTimeout 造的回复，还顺手把上下文数字改成写死的
`6.5k / 16k`。现在改成 `POST /notes` 之后的判据是：
回复里带着后端给的笔记编号（`NT-…`），且 toast 说的是"已存为笔记"。
拿不到编号就说明没写进去 —— 那时候不该有回复。
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "redesign" / "src" / "selftest.js"
text = PATH.read_text(encoding="utf-8")

OLD = """/* ── 第二段：会话的生命周期（新建 → 改名 → 归档 → 恢复 → 删除）──────── */"""

NEW = """/* ── 第一段半：侧记写一条笔记 ─────────────────────────────────────── */
async function phaseSideNote() {
  click('[data-action="open-side-note"]');
  const input = await waitDom(() => $('#sn-input'), '侧记面板打开');
  const before = $$('#sn-thread .sn-msg').length;
  input.value = '词根 re- 我总记混，先记一笔。';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  click('#sn-thread ~ * [data-action="sn-send"], [data-action="sn-send"]');

  const reply = await waitDom(() => {
    const rows = $$('#sn-thread .sn-msg');
    const last = rows[rows.length - 1];
    return rows.length > before && last && last.classList.contains('from-bot') ? last : null;
  }, '侧记给出回执');
  const text = String(reply.textContent || '');
  mark('侧记回执带笔记编号', { ok: /NT[-_]?\\d|NT-/.test(text), text: text.slice(0, 60) });
  mark('侧记顶上的上下文数字', { ok: true, tokens: String(($('#sn-tokens') || {}).textContent || '') });
  click('[data-action="close-side-note"]');
}

/* ── 第二段：会话的生命周期（新建 → 改名 → 归档 → 恢复 → 删除）──────── */"""

assert text.count(OLD) == 1, "生命周期段锚点不对"
text = text.replace(OLD, NEW, 1)

OLD_RUN = """    await phaseChat();
    await phaseLifecycle();"""
NEW_RUN = """    await phaseChat();
    await phaseSideNote();
    await phaseLifecycle();"""
assert text.count(OLD_RUN) == 1, "run 锚点不对"
text = text.replace(OLD_RUN, NEW_RUN, 1)

PATH.write_text(text, encoding="utf-8")
print("✅ 探针加了侧记一段")
