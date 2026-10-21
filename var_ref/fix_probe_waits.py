"""把探针里两处长等待从"定时器轮询"改成"MutationObserver"。

原因（值得写下来）：`--virtual-time-budget` 会把 `setTimeout` 当虚拟时间快进，
于是"每 120ms 看一眼、最多看 40 秒"在真实时间里只过了几十毫秒 ——
而一条 SSE 流需要真实的一秒。用定时器等，会赢在虚拟时间、输在真实网络，
报出来的错是「等太久了」，看起来像功能坏了。DOM 变化是真实发生的，改用它。
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "redesign" / "src" / "selftest.js"
text = PATH.read_text(encoding="utf-8")

PAIRS = [
    (
        "    await until(() => document.querySelectorAll('.session[data-session-id]').length > 0);",
        "    await waitDom(() => document.querySelectorAll('.session[data-session-id]').length > 0);",
    ),
    (
        "    const step = await until(() => document.querySelector('.steps .step[data-step-id]'));",
        "    const step = await waitDom(() => document.querySelector('.steps .step[data-step-id]'));",
    ),
    (
        """    await until(() => {
      const live = document.querySelector('article.msg-assistant.is-live');
      const last = Array.from(document.querySelectorAll('article.msg-assistant')).pop();
      return !live && last && last.querySelector('.msg-foot');
    });""",
        """    await waitDom(() => {
      const live = document.querySelector('article.msg-assistant.is-live');
      const last = Array.from(document.querySelectorAll('article.msg-assistant')).pop();
      return !live && last && last.querySelector('.msg-foot');
    });""",
    ),
]

for old, new in PAIRS:
    count = text.count(old)
    assert count == 1, f"锚点出现 {count} 次：{old[:50]}"
    text = text.replace(old, new, 1)

# `until` 已经没人用了，删掉它，免得留一个"看起来能用其实会误判"的函数
OLD_UNTIL = '''async function until(check, { timeout = 40000, step = 120 } = {}) {
  const started = Date.now();
  for (;;) {
    const value = check();
    if (value) return value;
    if (Date.now() - started > timeout) throw new Error('等太久了：' + report.steps.map((s) => s.name).join(' → '));
    await sleep(step);
  }
}

'''
assert text.count(OLD_UNTIL) == 1, "`until` 的定义没找到"
text = text.replace(OLD_UNTIL, "", 1)

PATH.write_text(text, encoding="utf-8")
print("✅ 探针改用 MutationObserver；未再使用的 until 已删除")
