"""把 redesign 原型接到真后端：**只换数据从哪来**，不动样式与结构。

为什么用一个补丁脚本而不是十几处手改：这个文件 2135 行，锚点必须唯一。
脚本里每一处都断言"锚点恰好出现一次"，改不到就报错 ——
比"以为改了、其实改到了另一处"安全得多。

改完之后 `frontend/redesign/app.js` 仍然是**一个可读的界面层**，
数据的三件事被拆到了 `src/` 下：`api.js`（怎么问）、`adapt.js`（怎么换算）、
`live.js`（一轮讨论的时序）。
"""

from __future__ import annotations

import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent.parent
APP = HERE / "frontend" / "redesign" / "app.js"
HTML = HERE / "frontend" / "redesign" / "index.html"

text = APP.read_text(encoding="utf-8")
_applied: list[str] = []


def replace(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    assert count == 1, f"{label}：锚点出现 {count} 次（应为 1）"
    text = text.replace(old, new, 1)
    _applied.append(label)
    print(f"✅ {label}")


def replace_between(start: str, end: str, new: str, label: str, *, keep_end: bool = True) -> None:
    """把 `start` 之后到 `end` 之前（不含 `end`）的整段换掉。"""
    global text
    i = text.find(start)
    assert i >= 0, f"{label}：找不到起点"
    assert text.count(start) == 1, f"{label}：起点出现多次"
    j = text.find(end, i + len(start))
    assert j >= 0, f"{label}：找不到终点"
    tail = text[j:] if keep_end else text[j + len(end):]
    text = text[:i] + new + tail
    _applied.append(label)
    print(f"✅ {label}")


# ─────────────────────────────────────────────────────────────── 1. 头部与 import
replace(
    """/* ═══════════════════════════════════════════════════════════════════
   觅学之契 · static interaction prototype
   All data is local and fictional. No network, no backend.
   ═══════════════════════════════════════════════════════════════════ */""",
    """/* ═══════════════════════════════════════════════════════════════════
   觅学之契 · 学习流档案 —— 界面层

   这个文件**原来是纯静态原型**（数据写死、没有网络）。现在它连真后端：
   侧栏读 /api/folders 与 /api/sessions，一个会话读 /api/sessions/{id}/state，
   对话走 POST /api/sessions/{id}/discuss/stream（SSE）。

   ★ 改动的边界很小心：**只换了数据从哪来**。类名、间距、动效一处没动。
     原型里的假数据（三份会话的样例消息 / 计划 / 动机 / 上下文）已经删掉 ——
     留着它们，接口坏掉时界面会"看起来正常"，而那正是最难查的故障。

   分层（为了稳定、清晰、可长期维护）：
     src/api.js     与后端说话的唯一起点（含 SSE 拆帧与错误形状）
     src/adapt.js   后端形状 → 界面形状（换算集中一处，不在渲染里散落 if）
     src/live.js    一轮讨论的时序（事件 → 动作，不碰 DOM）
     app.js         渲染与交互（本文件）
   ═══════════════════════════════════════════════════════════════════ */

import { api } from './src/api.js';
import { adaptDetail, adaptWorkspace, escape, paragraphs } from './src/adapt.js';
import { runDiscussion } from './src/live.js';""",
    "头部注释 + 三条 import",
)

# ─────────────────────────────────────────────────────── 2. state 里的写死数据
replace(
    "  currentSession: 'S-0001',\n  currentFolder: 'F-01',",
    "  currentSession: null,\n  currentFolder: null,\n  //: 点开计划项后，这一轮讨论的锚点（只由用户点击产生）。\n  anchorStep: null,",
    "state：当前会话/领域改为空，并记下讨论锚点",
)

pattern = re.compile(
    r"  folders: \[\n.*?\n  archivedFolders: \[\n.*?\n  \],\n  details: \{\},",
    re.S,
)
match = pattern.search(text)
assert match, "state：找不到写死的 folders/archived 数据块"
text = text[: match.start()] + (
    "  folders: [],\n"
    "  archivedSessions: [],\n"
    "  archivedFolders: [],\n"
    "  details: {},"
) + text[match.end():]
_applied.append("state：三份写死的领域/归档清空")
print("✅ state：三份写死的领域/归档清空")

replace(
    "state.details['S-0001'] = makeDetail('s1');\nstate.details['S-0002'] = makeDetail('s2');\nstate.details['S-0003'] = makeDetail('s3');\n",
    "",
    "state：去掉三份样例会话的预置",
)

# ──────────────────────────────────────────────────── 3. makeDetail → 真读后端
replace_between(
    "function makeDetail(key) {",
    "/* ── state ──",
    '''/** 一个会话的**空壳**：打开会话时立刻有东西可画，网络回来之前界面不空白。 */
function makeDetail() {
  return {
    session: null,
    loaded: false,
    messages: [],
    plan: null,
    motivation: null,
    input: null,
    context: structuredClone(EMPTY_CONTEXT),
    versions: null,
  };
}

/**
 * 把侧栏（领域 + 会话）从后端读回来。
 *
 * 归档的两类一起读：**「已归档」不是另一个页面**，它就是同一批数据里
 * `archived_at` 不为空的那几行。分成两次请求的话，两处状态一定会有对不上的时候。
 */
async function loadWorkspace() {
  const [foldersPayload, sessionsPayload] = await Promise.all([
    api.folders(true),
    api.sessions(true),
  ]);
  const { folders, archivedSessions, archivedFolders } =
    adaptWorkspace(foldersPayload, sessionsPayload);

  // 保住展开/收起状态：重新读一趟侧栏不该把用户展开的领域全收起来
  const opened = new Map(state.folders.map((f) => [f.id, f.open]));
  for (const folder of folders) {
    if (opened.has(folder.id)) folder.open = opened.get(folder.id);
    if (folder.sessions.some((s) => s.id === state.currentSession)) folder.open = true;
  }

  state.folders = folders;
  state.archivedSessions = archivedSessions;
  state.archivedFolders = archivedFolders;
  if (!findSession(state.currentSession)) state.currentSession = null;
}

/**
 * 读一个会话的全部状态。
 *
 * `render: false` 用在"后台对齐"上：刚结束的那一轮已经在界面上画完了，
 * 再整页重绘会把刚出现的字闪一下。后台只更新数据，界面按需再画。
 */
async function loadDetail(sessionId, { render = true } = {}) {
  if (!sessionId) return null;
  const payload = await api.state(sessionId);
  const detail = adaptDetail(payload);
  state.details[sessionId] = detail;
  if (render && state.currentSession === sessionId) renderSession();
  return detail;
}

''',
    "makeDetail：换成空壳 + loadWorkspace / loadDetail",
)

# ─────────────────────────────────────────────────── 4. DOM refs：记住占位文案
replace(
    "const composerInput = $('#composer-input');",
    "const composerInput = $('#composer-input');\n//: 占位文案要能还原 —— 点开计划项时会临时改成「关于第 N 步…」。\nconst COMPOSER_PLACEHOLDER = composerInput ? composerInput.placeholder : '';",
    "DOM：记下输入框原本的占位文案",
)

# ────────────────────────────────────────────────────────── 5. 卷首日期横线
replace(
    '  thread.innerHTML = `<div class="date-divider"><span>2026 · 10 · 20</span><span>周一 · 第 3 卷</span></div>${messages}`;',
    "  thread.innerHTML = `${dateDivider(detail)}${messages}`;",
    "renderThread：日期不再写死",
)

replace(
    "function emptyThreadMarkup(session) {",
    '''/** 卷首那条横线。日期来自这个会话的创建时间 —— 写死的话，第二天它就错了。 */
function dateDivider(detail) {
  const iso = detail.session?.created_at;
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const week = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()];
  return `<div class="date-divider"><span>${d.getFullYear()} · ${pad(d.getMonth() + 1)} · ${pad(d.getDate())}</span><span>${week}</span></div>`;
}

function emptyThreadMarkup(session) {''',
    "新增 dateDivider",
)

# ──────────────────────────────────────────────── 6. 计划项：点行=讨论，点圈=完成
replace(
    '''      ${plan.steps.map((step) => `<li class="step is-${step.status}" data-action="toggle-step" data-step-id="${step.id}">
        <span class="step-check">${icon('check')}</span>''',
    '''      ${plan.steps.map((step) => `<li class="step is-${step.status}" data-action="discuss-step" data-step-id="${step.id}" title="点这一项，与模型讨论它">
        <span class="step-check" data-action="toggle-step" data-step-id="${step.id}" role="button" tabindex="0" aria-label="标记完成">${icon('check')}</span>''',
    "计划项：整行点开讨论，圆圈标完成",
)

# ─────────────────────────────────────────────────────────────── 7. 选会话
replace_between(
    "function selectSession(id, { silent = false } = {}) {",
    "function ensureSelection() {",
    '''function selectSession(id) {
  const found = findSession(id);
  if (!found) return;
  if (state.busy) cancelRun({ interrupted: false, silent: true });
  state.currentSession = id;
  state.currentFolder = found.folderId;
  found.folder.open = true;
  if (!state.details[id]) state.details[id] = makeDetail();
  // 先用缓存画一次：点击必须立刻有反应，网络不能挡在点击和画面之间
  renderSession();
  if (window.innerWidth <= 1180) workspace.classList.add('is-margin-closed');
  void loadDetail(id).catch((err) => toast(err.message, 'danger'));
}

''',
    "selectSession：立即渲染 + 后台读 /state",
)

# ──────────────────────────────────────────────────────── 8. 停止 = 真断流
replace(
    "function cancelRun({ interrupted = false, silent = false } = {}) {\n  const run = state.run;\n  if (!run) return;\n  run.cancelled = true;",
    "function cancelRun({ interrupted = false, silent = false } = {}) {\n  const run = state.run;\n  if (!run) return;\n  run.cancelled = true;\n  // 真的掐断连接。只关界面上的转圈是**假停止** —— 服务端会把整轮跑完。\n  if (run.discussion) run.discussion.stop();",
    "cancelRun：abort 掉这一轮的连接",
)

# ────────────────────────────────────── 9. 模拟回答 → 真的 SSE 讨论
replace_between(
    "async function startAssistantRun(",
    "function sendFromComposer(",
    '''/**
 * 一轮真实讨论。界面上看到的三件事都来自服务端：
 * 状态词（`stage`）、逐字流（`token`）、工具动作与结果（`tool_call` / `tool_result`）。
 */
async function startLiveRun({ userText, stepId = null }) {
  if (state.busy) return;
  const detail = activeDetail();
  const sessionId = state.currentSession;
  if (!detail || !sessionId) return;

  const run = { cancelled: false, article: null, statusTimer: null, discussion: null };
  state.run = run;
  state.busy = true;
  updateBusyUI();

  const article = createLiveMessage();
  run.article = article;
  threadWrap.classList.add('is-thinking');
  const statusEl = article.querySelector('.status-word');
  const noteEl = article.querySelector('.thinking-note');
  startStatusCycle(run, statusEl, ['辨明目标', '检索上下文', '组织回答']);

  const prose = article.querySelector('.msg-prose');
  const meta = article.querySelector('.msg-meta');
  let streamed = 0;

  const view = {
    status(word, note) {
      if (statusEl.textContent !== word) statusEl.textContent = word;
      if (note !== undefined && noteEl) noteEl.textContent = note || '';
    },
    thinking(text) {
      if (noteEl && text) noteEl.textContent = String(text).slice(0, 160);
    },
    append(chunk) {
      if (!chunk) return;
      // 逐字拆成 span：原型里的"墨迹"动画就是这个类名 + 递延
      for (const ch of Array.from(chunk)) {
        const span = document.createElement('span');
        span.className = 'chunk';
        span.textContent = ch;
        prose.appendChild(span);
      }
      streamed += chunk.length;
      if (streamed > 80) scrollThread();
    },
    trace(row) {
      const el = document.createElement('div');
      el.className = `trace-item ${row.state || 'is-running'}`;
      el.dataset.call = row.callId || '';
      el.innerHTML = `<strong>${escape(row.label)}</strong><span>${escape(row.note || '')}</span>`;
      article.querySelector('.msg-trace').appendChild(el);
      scrollThread();
    },
    traceUpdate(callId, row) {
      const el = article.querySelector(`.trace-item[data-call="${callId}"]`);
      if (!el) {
        view.trace(row);
        return;
      }
      el.className = `trace-item ${row.state}`;
      el.innerHTML = `<strong>${escape(row.label)}</strong><span>${escape(row.note || '')}</span>`;
    },
    finish(payload) {
      void finalizeLive({ run, article, prose, meta, detail, sessionId, payload });
    },
    fail(message) {
      failLive({ run, article, message });
    },
  };

  const discussion = runDiscussion({ sessionId, text: userText, stepId, view });
  run.discussion = discussion;
  await discussion.settled;
}

/** 收官：先把界面上那条画完，再回头和后端对齐一次。 */
async function finalizeLive({ run, article, prose, meta, detail, sessionId, payload }) {
  if (run.cancelled) return;
  clearInterval(run.statusTimer);

  const thinking = article.querySelector('.thinking-field');
  if (thinking) {
    thinking.style.opacity = '0';
    setTimeout(() => thinking.remove(), 420);
  }

  const reply = payload.reply || (payload.stopReason === 'llm_error'
    ? '（模型这次没有返回内容）'
    : '');
  const html = paragraphs(reply);
  prose.innerHTML = inkify(html);
  $$('.chunk', prose).forEach((el, i) => { el.style.animationDelay = `${Math.min(i, 400) * 4}ms`; });
  prose.classList.remove('streaming');
  article.classList.remove('is-live');

  const traces = payload.toolTrace.map((entry) => ({
    label: traceLabel(entry.tool),
    note: entry.status === 'ok' ? '完成' : `被拦下 · ${String(entry.message || '').slice(0, 40)}`,
  }));
  if (meta) {
    meta.textContent = payload.toolTrace.length ? `回答 · 工具 ${payload.toolTrace.length}` : '回答';
  }

  const foot = document.createElement('div');
  foot.className = 'msg-foot';
  foot.innerHTML = `<button data-action="show-basis" data-basis="${escape(basisText(payload))}">为什么这样建议</button>
    <span class="dot"></span><button data-action="annotate">标注一句</button><span class="dot"></span>
    <span>${payload.toolTrace.length} 个工具动作</span>`;
  article.querySelector('.msg-body').appendChild(foot);

  detail.messages.push({
    id: 'M-' + Date.now(),
    role: 'assistant',
    time: clock(),
    html,
    traces: traces.length ? traces : null,
    thinking: payload.thinking,
    foot: true,
    basis: basisText(payload),
  });
  if (payload.context) detail.context = payload.context;

  state.run = null;
  state.busy = false;
  threadWrap.classList.remove('is-thinking');
  updateBusyUI();
  bindInkDots();
  scrollThread(true);

  // 与后端对齐但不重绘：这一轮可能改了计划版本，侧栏的时间也变了
  try {
    const before = detail.plan ? detail.plan.version : null;
    await loadDetail(sessionId, { render: false });
    renderRail();
    renderTopbar();
    const fresh = state.details[sessionId];
    if (fresh && (fresh.plan ? fresh.plan.version : null) !== before) renderMargin();
  } catch (err) {
    toast(err.message, 'danger');
  }

  if (state.queue.length && state.busyBehavior === 'queue' && state.currentSession) {
    const next = state.queue.shift();
    if (next.el) next.el.remove();
    updateBusyUI();
    await wait(350);
    beginUserTurn(next.text);
  }
}

function traceLabel(tool) {
  return ({
    propose_plan_change: '起草计划改动',
    update_task_plan: '改计划',
    read_context: '读取上下文',
    search_preferences: '检索偏好',
  })[tool] || tool;
}

/** 依据那句话：只写这一轮**真的发生了什么**，不预支结论。 */
function basisText(payload) {
  if (!payload.toolTrace.length) {
    return '这一轮没有调用工具：回答只基于这个会话里的目标、动机与你的这句话。';
  }
  const tools = Array.from(new Set(payload.toolTrace.map((t) => traceLabel(t.tool)))).join('、');
  const blocked = payload.toolTrace.filter((t) => t.status !== 'ok').length;
  return blocked
    ? `依据来自这一轮的 ${tools}。其中 ${blocked} 个动作被守卫拦下了 —— 拦下的原因写在工具轨迹里。`
    : `依据来自这一轮的 ${tools}；每一步的入参与返回都记在那条消息的工具轨迹里。`;
}

function failLive({ run, article, message }) {
  clearInterval(run.statusTimer);
  article.classList.remove('is-live');
  const thinking = article.querySelector('.thinking-field');
  if (thinking) thinking.remove();
  const meta = article.querySelector('.msg-meta');
  if (meta) meta.textContent = '中断了';
  const prose = article.querySelector('.msg-prose');
  prose.classList.remove('streaming');
  prose.insertAdjacentHTML('beforeend', `<p class="muted">${escape(message || '这一轮没有得出结论。')}</p>`);
  state.run = null;
  state.busy = false;
  threadWrap.classList.remove('is-thinking');
  updateBusyUI();
  toast(message || '这一轮中断了', 'danger');
  scrollThread(true);
}

function beginUserTurn(text, { fromQueue = false } = {}) {
  const detail = activeDetail();
  if (!detail || !state.currentSession) return;
  const message = { role: 'user', time: clock(), text, foot: true };
  detail.messages.push(message);

  if (detail.messages.length === 1) renderThread();
  else {
    const article = document.createElement('div');
    article.innerHTML = messageMarkup(message, detail.messages.length - 1);
    const node = article.firstElementChild;
    thread.appendChild(node);
    bindInkDots();
  }
  scrollThread(true);

  // 锚点只在这一句话上有效，发出去就清掉 —— 否则下一条消息会莫名带上它
  const stepId = state.anchorStep;
  state.anchorStep = null;
  if (composerInput) composerInput.placeholder = COMPOSER_PLACEHOLDER;
  void startLiveRun({ userText: text, stepId });
}

''',
    "模拟回答 → 真的 SSE 讨论（含收官、失败、用户发言）",
)

# ────────────────────────────────────────────────── 10. 侧栏动作 → 真请求
replace_between(
    "function archiveSession(id) {",
    "/* ── plan / proposal",
    '''/* 侧栏这些动作以前是**本地搬家**（把对象从一个数组挪到另一个）。
   现在它们都是一次真实请求 + 一次重新读侧栏 —— 因为"归档"在库里是一个
   时间戳，不是界面上的另一种状态。失败时把后端那句话原样报给用户。 */

async function runRailAction(label, action) {
  try {
    await action();
    await loadWorkspace();
    ensureSelection();
    renderRail();
    renderTopbar();
    toast(label);
  } catch (err) {
    toast(err.message || '这一步没成', 'danger');
  }
}

function archiveSession(id) {
  closePopover();
  void runRailAction('已归档 · 可在「已归档」中找回', () => api.archiveSession(id));
}

function deleteSession(id) {
  const found = findSession(id);
  if (!found) return;
  askConfirm({
    title: '删除这个会话？',
    body: `「${found.title}」的对话、计划版本与动机修订都会被删除。归档不会删除内容，删除会。`,
    ok: '删除会话',
    onOk: () => void runRailAction('会话已删除', async () => {
      await api.deleteSession(id);
      delete state.details[id];
      if (state.currentSession === id) state.currentSession = null;
    }),
  });
}

function archiveFolder(id) {
  closePopover();
  void runRailAction('领域已归档 · 会话一并收起', () => api.archiveFolder(id));
}

function deleteFolder(id) {
  const folder = findFolder(id);
  if (!folder) return;
  askConfirm({
    title: '删除这个领域？',
    body: `「${folder.name}」以及里面 ${folder.sessions.length} 个会话会被一并删除。这个动作无法撤销，也不会进入修订史。`,
    ok: '删除领域',
    onOk: () => void runRailAction('领域已删除', async () => {
      for (const s of folder.sessions) delete state.details[s.id];
      await api.deleteFolder(id);
      if (folder.sessions.some((s) => s.id === state.currentSession)) state.currentSession = null;
    }),
  });
}

function restoreSession(id) {
  closePopover();
  void runRailAction('已恢复到原领域', () => api.restoreSession(id));
}

function restoreFolder(id) {
  closePopover();
  void runRailAction('领域已恢复', () => api.restoreFolder(id));
}

''',
    "侧栏动作：归档/删除/恢复改为真请求",
)

# ─────────────────────────────────── 11. 步骤状态 + 点开某一步讨论（锚点）
replace_between(
    "function toggleStep(stepId) {",
    "function resolveProposal(",
    '''/** 标一步完成 / 取消完成。**服务端说了算**：把版本号带过去，撞车就报冲突。 */
async function toggleStep(stepId) {
  const detail = activeDetail();
  if (!detail || !detail.plan) return;
  const step = detail.plan.steps.find((s) => s.id === stepId);
  if (!step) return;
  const next = step.status === 'done' ? 'pending' : 'done';
  try {
    await api.markStep(state.currentSession, stepId, next, detail.plan.version);
    await loadDetail(state.currentSession, { render: false });
    renderMargin();
    renderRail();
    toast(next === 'done' ? '这一步记下了' : '已改回未完成');
  } catch (err) {
    toast(err.message, 'danger');
  }
}

/**
 * 点开某一步 → 它是这一轮讨论的锚点。
 *
 * ★ 锚点是**「用户点了这一项」的证据**，所以它只能由这一次点击产生 ——
 * 模型拿不到这个字段，只能被它限制（这正是计划守卫放行改动的依据）。
 */
function discussStep(stepId) {
  const detail = activeDetail();
  const step = detail && detail.plan ? detail.plan.steps.find((s) => s.id === stepId) : null;
  if (!step) return;
  state.anchorStep = stepId;
  if (composerInput) {
    composerInput.placeholder = `关于第 ${step.seq} 步「${String(step.action).slice(0, 18)}」…`;
  }
  composerInput.focus();
  autoGrow();
  scrollThread(true);
}

''',
    "toggleStep → 真写库；新增 discussStep（锚点来自点击）",
)

# ─────────────────────────────── 12. 提案：不再本地伪造第 4 版计划
replace(
    """  if (resolution === 'approved' && detail) {
    detail.plan = structuredClone(PLAN_V4);
    if (detail.context.sections[4]) detail.context.sections[4].value = '第 4 版 · 5 步';
    renderMargin();
  }""",
    """  // 计划的真实写入由服务端的工具 + 守卫完成（`propose_plan_change` 只在
  // 「用户点过那一项」时才被放行）。这里不本地伪造一个新版本 ——
  // 界面上的计划必须与库里的那一版是同一份。
  if (resolution === 'approved' && detail) void loadDetail(state.currentSession);""",
    "提案：去掉本地伪造的计划第 4 版",
)

# ───────────────────────────────────────────────── 13. handleAction 新增/改写
replace(
    """    case 'replay':
      replayDemo();
      break;""",
    """    case 'replay':
      void loadDetail(state.currentSession).catch((err) => toast(err.message, 'danger'));
      toast('已重新读取这个会话');
      break;""",
    "重播：改为重新读取会话",

)

replace(
    "    case 'send':\n      sendFromComposer();\n      break;",
    """    case 'toggle-step':
      void toggleStep(el.dataset.stepId);
      break;
    case 'discuss-step':
      discussStep(el.dataset.stepId);
      break;
    case 'send':
      sendFromComposer();
      break;""",
    "handleAction：接上 toggle-step 与 discuss-step",
)

replace(
    """    case 'delete-archived-session': {
      state.archivedSessions = state.archivedSessions.filter((s) => s.id !== id);
      closePopover();
      renderRail();
      toast('已从归档中删除', 'danger');
      break;
    }""",
    """    case 'delete-archived-session':
      closePopover();
      void runRailAction('已从归档中删除', () => api.deleteSession(id));
      break;""",
    "handleAction：归档会话的删除改走真请求",
)

replace(
    """    case 'delete-archived-folder': {
      state.archivedFolders = state.archivedFolders.filter((f) => f.id !== id);
      closePopover();
      renderRail();
      toast('已从归档中删除', 'danger');
      break;
    }""",
    """    case 'delete-archived-folder':
      closePopover();
      void runRailAction('已从归档中删除', () => api.deleteFolder(id));
      break;""",
    "handleAction：归档领域的删除改走真请求",
)

replace(
    """        openRename(el, '重命名会话', session.title, (name) => {
          session.title = name;
          renderRail();
          renderTopbar();
          toast('会话已重命名');
        });""",
    """        openRename(el, '重命名会话', session.title, async (name) => {
          try {
            await api.renameSession(id, name);
            session.title = name;
            renderRail();
            renderTopbar();
            toast('会话已重命名');
          } catch (err) {
            toast(err.message, 'danger');
          }
        });""",
    "重命名会话：真的写库",
)

replace(
    """        openRename(el, '重命名领域', folder.name, (name) => {
          folder.name = name;
          renderRail();
          renderTopbar();
          toast('领域已重命名');
        });""",
    """        openRename(el, '重命名领域', folder.name, async (name) => {
          try {
            await api.renameFolder(id, name);
            folder.name = name;
            renderRail();
            renderTopbar();
            toast('领域已重命名');
          } catch (err) {
            toast(err.message, 'danger');
          }
        });""",
    "重命名领域：真的写库",
)

# ───────────────────────────────────────────────── 14. 新建领域/会话 → 真请求
replace_between(
    "function commitInlineForm() {",
    "function switchSettingsTab(",
    '''async function commitInlineForm() {
  const value = $('#rail-form-input').value.trim();
  if (!value) { $('#rail-form-input').focus(); return; }
  try {
    if (state.inlineKind === 'folder') {
      await api.createFolder(value);
      await loadWorkspace();
      closeInlineForm();
      renderRail();
      toast('领域已创建');
      return;
    }

    // 会话必须落在某个领域里（库里有 NOT NULL 的 folder_id）。
    // 一个领域都没有时先建「未归类」—— 界面不假装存在一个不存在的归属。
    let folder = findFolder(state.currentFolder) || state.folders[0];
    if (!folder) {
      await api.createFolder('未归类');
      await loadWorkspace();
      folder = state.folders[0];
    }
    const created = await api.createSession(folder.id, value);
    await loadWorkspace();
    closeInlineForm();
    state.currentSession = created.session_id;
    state.currentFolder = created.folder_id;
    state.details[created.session_id] = makeDetail();
    renderSession();
    await loadDetail(created.session_id);
    toast('会话已创建 · 落笔即开始');
  } catch (err) {
    toast(err.message, 'danger');
  }
}

''',
    "新建领域/会话：真的写库并选中",
)

# ────────────────────────────────────────────────────────────── 15. 启动
replace_between(
    "function init() {",
    "init();",
    '''async function boot() {
  document.documentElement.dataset.cite = 'on';
  document.documentElement.dataset.motion = state.motion;
  if (window.innerWidth <= 1180) workspace.classList.add('is-margin-closed');

  // 先把壳画出来（侧栏空、对话区空），再从后端取数据 ——
  // 反过来做的话，首屏会有一段时间是白屏
  renderRail();
  renderTopbar();
  renderThread();
  renderMargin();
  renderSettings();
  renderBench();
  updateBusyUI();

  try {
    await loadWorkspace();
    if (!state.currentSession) {
      for (const folder of state.folders) {
        if (!folder.sessions.length) continue;
        state.currentSession = folder.sessions[0].id;
        state.currentFolder = folder.id;
        break;
      }
    }
    renderSession();
    if (state.currentSession) await loadDetail(state.currentSession);
    else ensureSelection();
  } catch (err) {
    // 后端没起来时**说清楚是哪一步没成**，而不是留一个空白界面让人以为"没有数据"
    thread.innerHTML = `<div class="empty-thread"><span class="empty-seal">未</span>
      <h2>连不上后端</h2>
      <p>${escape(err.message || String(err))}</p>
      <p class="muted">界面上的每一条数据都来自接口；接口不通时这里不会摆一份假数据。</p></div>`;
    threadEnd.hidden = true;
    toast(err.message || '连不上后端', 'danger');
  }
}

''',
    "init → boot：先画壳再取数据，连不上时说实话",
)

replace("\ninit();", "\nboot();", "启动函数改名")

APP.write_text(text, encoding="utf-8")
print(f"\n共 {len(_applied)} 处；app.js 现在 {len(text.splitlines())} 行")

html = HTML.read_text(encoding="utf-8")
assert html.count('<script src="app.js"></script>') == 1, "index.html：script 标签不对"
html = html.replace('<script src="app.js"></script>', '<script type="module" src="./app.js"></script>')
HTML.write_text(html, encoding="utf-8")
print("✅ index.html：以 module 方式加载（import 需要）")
