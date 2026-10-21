"""收尾三件事：把最后几处"假动作"接上真后端，再把示例假数据删干净。

1. **侧记**：原来点了发送，界面自己造一条"记下了。我只把它放进侧记的记忆里…"
   的回复，还把上下文数字改成写死的 `6.5k / 16k`。这是最坏的一种假：
   用户以为笔记存下来了，其实什么都没写。改成真的 `POST /notes`，回复里带
   笔记编号；上下文数字用这个会话真实测到的那一份（没测到就说没测到）。
2. **上下文逐条来源**：原来 toast 里写死"偏好 4 · 方法卡 3 · 基线 6 · 策略库 2"。
   这四个数字没有任何来源，改成读真实 meter。
3. **示例会话的假数据**：`PLAN_V3/V4`、`MOTIVATION_S1/S2`、`CONTEXT_S1/S2`、
   `PLAN_S2/S3`、`BASE_MESSAGES_S1/S2/S3` 以及只服务于它们的 `replayDemo`。
   界面已经全部从 `/state` 读，这些只剩"哪天被人误用就又出现假计划"的风险。
"""

from __future__ import annotations

import pathlib

HERE = pathlib.Path(__file__).resolve().parent.parent
API = HERE / "frontend" / "redesign" / "src" / "api.js"
APP = HERE / "frontend" / "redesign" / "app.js"


def patch(path: pathlib.Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    assert count == 1, f"{label}：锚点出现 {count} 次"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"✅ {label}")


# ── 1. api：加一条写笔记 ──────────────────────────────────────────────
patch(
    API,
    """  // ── 任务定义 / 动机 ───────────────────────────────────────────────
  patchInput:""",
    """  // ── 笔记（侧记）─────────────────────────────────────────────────
  /** 写笔记。后端唯一的「终态也能写」的操作 —— 它不进上下文，也不改计划。 */
  addNote: (sessionId, content, tags) =>
    post(`/api/sessions/${seg(sessionId)}/notes`, { content, tags: tags || null }),

  // ── 任务定义 / 动机 ───────────────────────────────────────────────
  patchInput:""",
    "api：/notes",
)

# ── 2. 侧记：真的写库 ────────────────────────────────────────────────
OLD_SIDE = """function sendSideNote() {
  const input = $('#sn-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  const threadEl = $('#sn-thread');
  const mine = document.createElement('div');
  mine.className = 'sn-msg';
  mine.innerHTML = `<span class="sn-role">你</span><p>${escapeHtml(text)}</p>`;
  threadEl.appendChild(mine);
  threadEl.scrollTop = threadEl.scrollHeight;
  setTimeout(() => {
    const reply = document.createElement('div');
    reply.className = 'sn-msg from-bot';
    reply.innerHTML = `<span class="sn-role">记</span><p>记下了。我只把它放进侧记的记忆里，不改动计划。要落进会话，我会先给你一张提案。</p>`;
    threadEl.appendChild(reply);
    threadEl.scrollTop = threadEl.scrollHeight;
    const tokens = $('#sn-tokens');
    tokens.textContent = '6.5k / 16k';
  }, 900);
}"""

NEW_SIDE = """/**
 * 侧记：**真的写一条笔记**（`POST /sessions/{id}/notes`）。
 *
 * 原来是界面自己 setTimeout 造一条回复、再把上下文数字写成 `6.5k / 16k` ——
 * 用户会以为笔记存下来了。现在回复里带的是后端给的笔记编号：
 * 没写成功就没有编号，也就不会有这条回复。
 */
async function sendSideNote() {
  const input = $('#sn-input');
  const text = input.value.trim();
  if (!text) return;
  const detail = activeDetail();
  if (!detail || !state.currentSession) {
    toast('还没有选中会话，笔记没有地方可存');
    return;
  }
  input.value = '';
  const threadEl = $('#sn-thread');
  const mine = document.createElement('div');
  mine.className = 'sn-msg';
  mine.innerHTML = `<span class="sn-role">你</span><p>${escapeHtml(text)}</p>`;
  threadEl.appendChild(mine);
  threadEl.scrollTop = threadEl.scrollHeight;
  try {
    const note = await api.addNote(state.currentSession, text);
    detail.notes = detail.notes || [];
    detail.notes.push({ id: note.note_id, text: note.content, time: '刚刚' });
    const reply = document.createElement('div');
    reply.className = 'sn-msg from-bot';
    reply.innerHTML = `<span class="sn-role">记</span><p>已存为笔记 ${escapeHtml(note.note_id)}。笔记不进上下文、也不改动计划；要它影响计划，得在会话里明说。</p>`;
    threadEl.appendChild(reply);
    threadEl.scrollTop = threadEl.scrollHeight;
    refreshSideNoteMeter(detail);
    toast('已存为笔记');
  } catch (err) {
    toast(err.message, 'danger');
  }
}

/** 侧记顶上的上下文数字 —— 用这个会话**真实测到**的那一份，没测到就说没测到。 */
function refreshSideNoteMeter(detail) {
  const el = $('#sn-tokens');
  if (!el) return;
  const context = detail && detail.context;
  el.textContent = context && context.measured ? `${context.used} / ${context.total}` : '没测到';
}"""
patch(APP, OLD_SIDE, NEW_SIDE, "侧记：写真的笔记，数字用真的 meter")

# ── 3. 上下文逐条来源：读真 meter ────────────────────────────────────
patch(
    APP,
    """    case 'inspect-context':
      toast('逐条来源：偏好 4 · 方法卡 3 · 基线 6 · 策略库 2');
      break;""",
    """    case 'inspect-context': {
      // 原来这里写死「偏好 4 · 方法卡 3 · 基线 6 · 策略库 2」——
      // 四个数字没有任何来源。改成读这个会话真实测到的那一份。
      const context = activeDetail() && activeDetail().context;
      if (!context || !context.measured) {
        toast('这个会话的上下文还没测到：后端没有给出窗口上限，也就不编一个百分比');
        break;
      }
      toast(`${context.note || ''}${context.used} / ${context.total} token · 占用 ${context.pct}%`);
      break;
    }""",
    "上下文来源：改成真数字",
)

# 侧记打开时先把顶上的数字填对，而不是让它停在模板里的 6.2k / 16k
patch(
    APP,
    """    case 'open-side-note':
      $('#side-note').classList.add('is-open');
      $('#sn-input').focus();
      break;""",
    """    case 'open-side-note':
      $('#side-note').classList.add('is-open');
      refreshSideNoteMeter(activeDetail());
      $('#sn-input').focus();
      break;""",
    "打开侧记：先填真数字",
)

# ── 4. 删掉 replayDemo 与示例会话的假数据 ────────────────────────────
text = APP.read_text(encoding="utf-8")

OLD_REPLAY = """function replayDemo() {
  if (state.currentSession !== 'S-0001') {
    toast('重播只对示例会话「一 unit 1 的词根网络」可用');
    return;
  }
  if (state.busy) cancelRun({ silent: true });
  const detail = activeDetail();
  detail.messages = structuredClone(BASE_MESSAGES_S1);
  detail.plan = structuredClone(PLAN_V3);
  detail.context = structuredClone(CONTEXT_S1);
  detail.demoDone = false;
  state.marginTab = 'plan';
  renderSession();
  toast('推演将重播 · 计划回到第 3 版');
  setTimeout(() => startAssistantRun({ mode: 'demo' }), 600);
}

"""
assert text.count(OLD_REPLAY) == 1, "replayDemo 没找到"
text = text.replace(OLD_REPLAY, "", 1)

START = "const PLAN_V3 = {"
END = "const EMPTY_CONTEXT = {"
i = text.index(START)
j = text.index(END)
block = text[i:j]
DEMO_NAMES = [
    "PLAN_V3", "PLAN_V4", "MOTIVATION_S1", "CONTEXT_S1", "MOTIVATION_S2",
    "CONTEXT_S2", "PLAN_S2", "PLAN_S3", "BASE_MESSAGES_S1", "BASE_MESSAGES_S2",
    "BASE_MESSAGES_S3",
]
for name in DEMO_NAMES:
    assert block.count(f"const {name} = ") == 1, f"{name} 不在这一段里"
assert "function " not in block, "这一段里混进了函数，不敢整段删"
text = text[:i] + text[j:]

APP.write_text(text, encoding="utf-8")
print(f"✅ 删掉 replayDemo 与 {len(DEMO_NAMES)} 份示例假数据（{len(block.splitlines())} 行）")

# ── 5. 删完之后不许再有人引用它们 ────────────────────────────────────
after = APP.read_text(encoding="utf-8")
for name in DEMO_NAMES + ["replayDemo"]:
    hits = after.count(name)
    assert hits == 0, f"{name} 还有 {hits} 处引用"
print("✅ 复核：这些名字已经一处不剩")
