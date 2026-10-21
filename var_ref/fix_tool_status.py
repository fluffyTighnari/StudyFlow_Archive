"""工具轨迹的四种状态要对上 —— 后端说的是 success / empty / rejected / error。

原来只认 `ok`，于是**成功**的一次写入被画成"被拦下"（金色点）。
这不是配色问题：界面上那个点就是"改动到底写进去了没有"的答案。
把成功显示成被拦下，用户会以为自己的计划没变（而它变了）；
反过来把被拦下显示成成功，用户会以为变了（而它没变）。两种都危险，
所以状态映射要按后端枚举逐一对上，不能靠"不是 ok 就当失败"。

顺带：`empty` 是**正常结果**（"没有找到"），不是失败 —— `empty ≠ none` 这条
规矩在界面上也该成立。
"""

from __future__ import annotations

import pathlib

HERE = pathlib.Path(__file__).resolve().parent.parent
ADAPT = HERE / "frontend" / "redesign" / "src" / "adapt.js"
APP = HERE / "frontend" / "redesign" / "app.js"
CSS = HERE / "frontend" / "redesign" / "styles.css"


def patch(path: pathlib.Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    assert count == 1, f"{label}：锚点出现 {count} 次"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"✅ {label}")


patch(
    ADAPT,
    """const STATUS_LABEL = { ok: '完成', rejected: '被拦下', error: '出错', skipped: '跳过' };""",
    """/**
 * `ToolStatus` 的四个取值（`app/agent/tools.py`）：
 *
 * - `success`  做成了 —— 绿点，附上它自己那句话（"调整已应用（计划版本 3）"）
 * - `empty`    做成了，但没有东西可给（"没有找到"）—— 也是绿点。
 *              `empty ≠ none`：它是一次成功的检索，不是一次失败
 * - `rejected` 模型想做，被守卫拦住 —— 金点。**改动没有发生**
 * - `error`    工具自己出错 —— 朱点。**结果未知**
 *
 * 按枚举逐一写出来，而不是"不是 ok 就算失败"：后一种写法会把成功画成被拦下。
 */
const STATUS_STYLE = {
  success: { label: '完成', cls: 'is-done' },
  empty: { label: '没有找到', cls: 'is-done' },
  rejected: { label: '被拦下', cls: 'is-rejected' },
  error: { label: '出错', cls: 'is-error' },
  running: { label: '进行中', cls: 'is-running' },
};""",
    "adapt：四种工具状态逐一映射",
)

patch(
    ADAPT,
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
}""",
    """export function traceRow(entry) {
  const tool = entry.tool || '工具';
  const status = entry.status || 'success';
  const style = STATUS_STYLE[status] || { label: status, cls: 'is-rejected' };
  const detail = shortText(entry.message, 44);
  // 做成了就把工具自己那句话露出来（它带着"计划版本 3"这类可核对的事实）；
  // 被拦下 / 出错则先说结论，再说原因 —— 这两种状态不能只给原因。
  const note = style.cls === 'is-done'
    ? detail || style.label
    : `${style.label}${detail ? ' · ' + detail : ''}`;
  return {
    label: toolLabel(tool),
    note,
    state: style.cls,
    callId: entry.call_id || null,
  };
}""",
    "adapt：轨迹文案按状态分派",
)

patch(
    ADAPT,
    """  const labels = Array.from(new Set(toolTrace.map((entry) => toolLabel(entry.tool || '工具'))));
  const blocked = toolTrace.filter((entry) => (entry.status || 'ok') !== 'ok').length;""",
    """  const labels = Array.from(new Set(toolTrace.map((entry) => toolLabel(entry.tool || '工具'))));
  const blocked = toolTrace.filter((entry) => ['rejected', 'error'].includes(entry.status)).length;""",
    "adapt：依据里的「被拦下」计数改用枚举值",
)

patch(
    APP,
    """  const traces = payload.toolTrace.map((entry) => ({
    label: traceLabel(entry.tool),
    note: entry.status === 'ok' ? '完成' : `被拦下 · ${String(entry.message || '').slice(0, 40)}`,
  }));""",
    """  const traces = payload.toolTrace.map((entry) => {
    const ok = ['success', 'empty'].includes(entry.status);
    return {
      label: traceLabel(entry.tool),
      note: ok
        ? String(entry.message || '完成').replace(/\\*\\*/g, '').slice(0, 44)
        : `${entry.status === 'error' ? '出错' : '被拦下'} · ${String(entry.message || '').replace(/\\*\\*/g, '').slice(0, 40)}`,
    };
  });""",
    "app：这一轮的轨迹文案与状态对齐",
)

patch(
    APP,
    """  const blocked = payload.toolTrace.filter((t) => t.status !== 'ok').length;""",
    """  const blocked = payload.toolTrace.filter((t) => ['rejected', 'error'].includes(t.status)).length;""",
    "app：依据里的计数改用枚举值",
)

patch(
    CSS,
    """.trace-item.is-rejected::before { background: var(--gold); }""",
    """.trace-item.is-rejected::before { background: var(--gold); }
/* 「出错」比「被拦下」更重：被拦下是守卫按规则拒绝（结果确定），
   出错是工具自己坏了（结果未知）。两者不能同色。 */
.trace-item.is-error::before { background: var(--vermilion); }""",
    "样式：出错用朱点",
)

print("\n工具状态映射改完")
