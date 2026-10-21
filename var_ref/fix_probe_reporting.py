"""探针两处调整：

1. **工具轨迹不再当成"必须有"**。`serveui --llm fake` 的替身按场景轮换脚本，
   有的场景会调 `propose_plan_change`、有的只回一句话。把"这次没调工具"判成失败，
   测的就是替身的剧本而不是界面。改成：**记下来**（给报告看），
   真正的判据是"回答真的出现在了页面上"与"消息条数长了"。
2. **把 toast 抓进报告**。侧栏动作失败时，界面上只闪一句 toast 就没了 ——
   而 `--dump-dom` 看不见它，于是"失败"在报告里表现成"卡住"。
   把 toast 文本收集起来，报告里就能直接看到后端说了什么。
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "redesign" / "src" / "selftest.js"
text = PATH.read_text(encoding="utf-8")

OLD_FLUSH = """function flush() {
  let pre = document.getElementById('probe-report');
  if (!pre) {
    pre = document.createElement('pre');
    pre.id = 'probe-report';
    document.body.appendChild(pre);
  }
  pre.textContent = JSON.stringify(report, null, 2);
}"""
NEW_FLUSH = """const toasts = [];
report.toasts = toasts;

// 侧栏动作失败时界面只闪一句 toast，而 --dump-dom 看不见它 ——
// 于是"失败"在报告里会表现成"卡住"。把 toast 收进报告，原因就跟着报告一起出来。
function watchToasts() {
  const layer = document.getElementById('toast-layer');
  if (!layer) return;
  new MutationObserver((records) => {
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (node.nodeType === 1) {
          const text = String(node.textContent || '').trim();
          if (text) toasts.push(text);
        }
      }
    }
  }).observe(layer, { childList: true, subtree: true });
}

function flush() {
  let pre = document.getElementById('probe-report');
  if (!pre) {
    pre = document.createElement('pre');
    pre.id = 'probe-report';
    document.body.appendChild(pre);
  }
  report.toasts = toasts;
  pre.textContent = JSON.stringify(report, null, 2);
}"""
assert text.count(OLD_FLUSH) == 1, "flush 锚点不对"
text = text.replace(OLD_FLUSH, NEW_FLUSH, 1)

OLD_TRACE = """  const prose = (($('.msg-prose', last) || {}).textContent || '').trim();
  mark('有回答正文', { ok: prose.length > 0, chars: prose.length, head: prose.slice(0, 30) });
  mark('有工具轨迹', { ok: traces.length > 0, traces });

  report.messages = $$('.thread .msg').length;
  report.planAfter = planVersion();
  report.planChanged = report.planBefore !== report.planAfter;"""
NEW_TRACE = """  const prose = (($('.msg-prose', last) || {}).textContent || '').trim();
  mark('有回答正文', { ok: prose.length > 0, chars: prose.length, head: prose.slice(0, 30) });
  // 工具轨迹只**记录**，不判失败：替身按场景轮换剧本，有的场景本来就不调工具
  mark('工具轨迹（仅供参考）', { ok: true, count: traces.length, traces });

  report.messages = $$('.thread .msg').length;
  report.messagesGrew = report.messages > (report.messagesBefore || 0);
  mark('消息条数增长', { ok: report.messagesGrew, from: report.messagesBefore, to: report.messages });
  report.planAfter = planVersion();
  report.planChanged = report.planBefore !== report.planAfter;
  mark('计划版本（仅供参考）', { ok: true, before: report.planBefore, after: report.planAfter });"""
assert text.count(OLD_TRACE) == 1, "轨迹锚点不对"
text = text.replace(OLD_TRACE, NEW_TRACE, 1)

OLD_SEND = """  const input = $('#composer-input');
  input.value = '这一步我想拆成两天，每天一半';"""
NEW_SEND = """  report.messagesBefore = $$('.thread .msg').length;
  const input = $('#composer-input');
  input.value = '这一步我想拆成两天，每天一半';"""
assert text.count(OLD_SEND) == 1, "发送锚点不对"
text = text.replace(OLD_SEND, NEW_SEND, 1)

OLD_RUN = """export async function run() {
  flush();
  try {"""
NEW_RUN = """export async function run() {
  watchToasts();
  flush();
  try {"""
assert text.count(OLD_RUN) == 1, "run 锚点不对"
text = text.replace(OLD_RUN, NEW_RUN, 1)

PATH.write_text(text, encoding="utf-8")
print("✅ 探针：轨迹改为参考项；toast 收进报告")
