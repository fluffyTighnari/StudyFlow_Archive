"""接完真后端之后，把三处"看着对、其实不对"的地方补齐。

1. **工具步数是假的**：`推演 3 步` 是原型写死的字，而真实一轮可能是 1 步、可能是 5 步。
2. **依据那句话是假的**：重开一个会话时，每条旧消息的「为什么这样建议」都挂着
   同一句写死的文案。理由必须来自**那条消息真的执行过的工具轨迹**。
3. **「被拦下」看不出来**：工具轨迹只有 `is-running` / `is-done` 两个状态有颜色。
   `rejected`（模型想做、被守卫拦住）是这一层最重要的一种状态，
   它必须和"做成了"长得不一样 —— 否则用户会以为改动已经写进去了。
"""

from __future__ import annotations

import pathlib

HERE = pathlib.Path(__file__).resolve().parent.parent
APP = HERE / "frontend" / "redesign" / "app.js"
ADAPT = HERE / "frontend" / "redesign" / "src" / "adapt.js"
CSS = HERE / "frontend" / "redesign" / "styles.css"


def patch(path: pathlib.Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    assert count == 1, f"{label}：锚点出现 {count} 次"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"✅ {label}")


# 1. 工具步数
patch(
    APP,
    """<span class="msg-meta">${message.traces ? '推演 3 步' : '回答'}</span>""",
    """<span class="msg-meta">${message.traces ? `推演 ${message.traces.length} 步` : '回答'}</span>""",
    "消息头：步数按真实的工具轨迹数",
)

# 2. 依据来自轨迹
patch(
    ADAPT,
    """export function traceRow(entry) {
  const tool = entry.tool || '工具';
  const status = entry.status || 'ok';
  const note = STATUS_LABEL[status] || status;
  return {
    label: toolLabel(tool),
    note: status === 'ok' ? note : `${note} · ${String(entry.message || '').slice(0, 40)}`,
    state: status === 'ok' ? 'is-done' : 'is-rejected',
    callId: entry.call_id || null,
  };
}""",
    """export function traceRow(entry) {
  const tool = entry.tool || '工具';
  const status = entry.status || 'ok';
  const note = STATUS_LABEL[status] || status;
  return {
    label: toolLabel(tool),
    note: status === 'ok' ? note : `${note} · ${shortText(entry.message, 40)}`,
    state: status === 'ok' ? 'is-done' : 'is-rejected',
    callId: entry.call_id || null,
  };
}

/** 后端那句拦截理由里有 `**加粗**` 的 markdown，摘进一行小字前先去掉记号。 */
function shortText(value, limit) {
  const flat = String(value ?? '').replace(/\\*\\*/g, '').replace(/\\s+/g, ' ').trim();
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat;
}

/**
 * 「为什么这样建议」的答案 —— **只写这条回复真的做过什么**。
 *
 * 不写"基于偏好与基线"这种听起来对、实际无法核对的句子：这条回复到底读过
 * 什么，库里记着的是工具轨迹，那就只说工具轨迹。没有工具调用时就说没有。
 */
export function basisFromTrace(toolTrace) {
  if (!Array.isArray(toolTrace) || !toolTrace.length) {
    return '这一轮没有调用工具：回答只基于这个会话里的目标、动机与你的那句话。';
  }
  const labels = Array.from(new Set(toolTrace.map((entry) => toolLabel(entry.tool || '工具'))));
  const blocked = toolTrace.filter((entry) => (entry.status || 'ok') !== 'ok').length;
  return blocked
    ? `依据来自这一轮的 ${labels.join('、')}。其中 ${blocked} 个动作被守卫拦下了 —— 拦下的原因写在工具轨迹里。`
    : `依据来自这一轮的 ${labels.join('、')}；每一步的入参与返回都记在这条消息的工具轨迹里。`;
}""",
    "adapt：依据来自工具轨迹（去掉 markdown 记号）",
)

patch(
    ADAPT,
    """      traces: traceRows(row.tool_trace),
      foot: true,
      basis: null,""",
    """      traces: traceRows(row.tool_trace),
      foot: true,
      basis: basisFromTrace(row.tool_trace),""",
    "adapt：旧消息带上真实依据",
)

# 3. 「被拦下」要有颜色
patch(
    CSS,
    """.trace-item.is-done::before { background: var(--jade); }""",
    """.trace-item.is-done::before { background: var(--jade); }
/* 「被拦下」—— 模型想做、守卫不允许。它必须和"做成了"长得不一样：
   把一次被拒绝的写入画成成功的绿点，是这个界面最不能犯的错。 */
.trace-item.is-rejected::before { background: var(--gold); }""",
    "样式：被拦下的工具动作用金色点（沿用原有令牌）",
)

print("\n三处都补完了")
