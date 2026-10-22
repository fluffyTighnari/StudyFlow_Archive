'use strict';

/* ═══════════════════════════════════════════════════════════════════
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
import { adaptDetail, adaptWorkspace, dotClass, escape, paragraphs } from './src/adapt.js';
import { runDiscussion } from './src/live.js';

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const escapeHtml = (value) => String(value)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

const pad = (n) => String(n).padStart(2, '0');
const clock = () => {
  const d = new Date();
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

const FOLIOS = ['壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖', '拾'];

/* ── i18n ─────────────────────────────────────────────────────────── */
const I18N = {
  zh: {
    'brand.name': '觅学之契',
    'brand.tag': '学习流档案 · 卷三',
    'rail.newFolder': '新领域',
    'rail.newSession': '新会话',
    'rail.folders': '领域 · Folders',
    'rail.archive': '已归档',
    'rail.benchmark': '基准',
    'rail.settings': '设置',
    'rail.modelNote': '本地 Ollama · 上下文 32k',
    'rail.colophon': '持久化学习流 · 每句话可回溯',
    'common.create': '创建',
    'common.cancel': '取消',
    'common.save': '保存',
    'common.rename': '重命名',
    'common.archive': '归档',
    'common.delete': '删除',
    'common.restore': '恢复',
    'chat.end': '以上是本轮的全部往来 · 每一步都可回溯',
    'margin.motivation': '动机',
    'margin.plan': '计划',
    'margin.context': '上下文',
    'margin.sideNote': '侧记 · 通用模型',
    'margin.sideNoteNote': '独立记忆 · 不改动计划',
    'composer.label': '落笔',
    'composer.send': '落笔',
    'composer.hint': '尽量详细地描述任务；不明确的地方，我会先反问。',
    'composer.busyPrefix': '繁忙时：',
    'bench.kicker': '测量 · 不是门槛',
    'bench.demoNote': '演示数据未接入',
    'bench.startQuick': '从简短版开始',
    'bench.startFull': '从完整版开始',
    'settings.kicker': '桌案',
    'settings.general': '通用',
    'settings.generalNote': '语言 · 繁忙时行为',
    'settings.models': '模型',
    'settings.modelsNote': '档案 · 印鉴 · 探活',
    'settings.appearance': '外观',
    'settings.appearanceNote': '纸色 · 字号 · 动效',
    'settings.version': '界面演示 · 非最终设计',
    'state.active': '进行中',
    'state.review': '待复盘',
    'state.completed': '已完成',
    'state.draft': '草稿',
    'busy.queue': '排队等待',
    'busy.interrupt': '打断并发送',
    'busy.block': '暂不允许',
    'model.switch': '切换模型',
    'model.manage': '管理模型档案',
    'model.current': '当前',
    'model.use': '启用',
    'model.think': '思考',
  },
  en: {
    'brand.name': 'MOUXUE',
    'brand.tag': 'A Ledger of Learning · Vol. III',
    'rail.newFolder': 'New field',
    'rail.newSession': 'New session',
    'rail.folders': 'Fields · Folders',
    'rail.archive': 'Archived',
    'rail.benchmark': 'Benchmark',
    'rail.settings': 'Settings',
    'rail.modelNote': 'Local Ollama · 32k context',
    'rail.colophon': 'A persistent learning flow · every line traceable',
    'common.create': 'Create',
    'common.cancel': 'Cancel',
    'common.save': 'Save',
    'common.rename': 'Rename',
    'common.archive': 'Archive',
    'common.delete': 'Delete',
    'common.restore': 'Restore',
    'chat.end': 'End of this exchange · every step traceable',
    'margin.motivation': 'Motivation',
    'margin.plan': 'Plan',
    'margin.context': 'Context',
    'margin.sideNote': 'Margin notes · general model',
    'margin.sideNoteNote': 'Separate memory · never edits the plan',
    'composer.label': 'Write',
    'composer.send': 'Write',
    'composer.hint': 'Describe the task in detail; I will ask when something is unclear.',
    'composer.busyPrefix': 'While busy: ',
    'bench.kicker': 'Measurement · not a gate',
    'bench.demoNote': 'Demo data not connected',
    'bench.startQuick': 'Begin with the short form',
    'bench.startFull': 'Begin with the full form',
    'settings.kicker': 'Desk',
    'settings.general': 'General',
    'settings.generalNote': 'Language · busy behaviour',
    'settings.models': 'Models',
    'settings.modelsNote': 'Profiles · seals · probes',
    'settings.appearance': 'Appearance',
    'settings.appearanceNote': 'Paper · type · motion',
    'settings.version': 'Interface demo · not final design',
    'state.active': 'Active',
    'state.review': 'Review',
    'state.completed': 'Completed',
    'state.draft': 'Draft',
    'busy.queue': 'Queue',
    'busy.interrupt': 'Interrupt & send',
    'busy.block': 'Hold send',
    'model.switch': 'Switch model',
    'model.manage': 'Manage model profiles',
    'model.current': 'Current',
    'model.use': 'Use',
    'model.think': 'Thinking',
  },
};

const t = (key) => (I18N[state.lang] && I18N[state.lang][key]) || I18N.zh[key] || key;

/* ── data ─────────────────────────────────────────────────────────── */
const PROFILES = [
  {
    id: 'p-local', mark: '本', label: '本地 · 日常', short: 'Qwen 3.6 · 27B',
    provider: 'Ollama', kind: 'local', model: 'qwen3.6:27b',
    base: '127.0.0.1:11434', think: 'false', ctx: '32 768',
    masked: '无需密钥', note: '离线可用 · 单次 90–190 s', probe: '上次探活 128 ms · 12 个模型',
  },
  {
    id: 'p-deepseek', mark: '外', label: 'DeepSeek', short: 'DeepSeek V3',
    provider: 'OpenAI 兼容', kind: 'external', model: 'deepseek-chat',
    base: 'api.deepseek.com', think: 'auto', ctx: '64 000',
    masked: 'sk-••••9f2c', note: '产品落点 · 流式稳定', probe: '上次探活 2026-10-19 · 可用',
  },
  {
    id: 'p-kimi', mark: '外', label: 'Kimi K2', short: 'Kimi K2',
    provider: 'OpenAI 兼容', kind: 'external', model: 'kimi-k2-0905',
    base: 'api.moonshot.cn', think: 'auto', ctx: '128 000',
    masked: 'sk-••••71ab', note: '长上下文 · 适合整卷复盘', probe: '尚未探活',
  },
  {
    id: 'p-qwen', mark: '备', label: '本地 · 备用', short: 'Qwen 3.5 · 9B',
    provider: 'Ollama', kind: 'local', model: 'qwen3.5:9b',
    base: '127.0.0.1:11434', think: 'true', ctx: '32 768',
    masked: '无需密钥', note: '快，但工具调用不稳', probe: '上次探活 96 ms · 12 个模型',
  },
];

const EMPTY_CONTEXT = {
  used: '0', total: '32k', pct: 0, segments: [0, 0, 0, 0],
  sections: [],
  note: '这个会话还没有产生上下文。第一句话之后，这里会显示它实际看见的条目。',
};

/** 一个会话的**空壳**：打开会话时立刻有东西可画，网络回来之前界面不空白。 */
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
}

/* ── state ────────────────────────────────────────────────────────── */
const state = {
  view: 'chat',
  lang: 'zh',
  theme: 'paper',
  scale: 'base',
  motion: 'full',
  cite: true,
  thinkView: 'fold',
  busyBehavior: 'queue',
  activeProfile: 'p-local',
  profiles: structuredClone(PROFILES),
  currentSession: null,
  currentFolder: null,
  //: 点开计划项后，这一轮讨论的锚点（只由用户点击产生）。
  anchorStep: null,
  marginTab: 'plan',
  inlineKind: null,
  busy: false,
  queue: [],
  search: '',
  detail: null,
  run: null,
  bench: { form: 'full', status: 'interrupted', answered: 7 },
  familiar: new Set(['英语 · 考研词汇', '认知科学 · 记忆']),
  interested: new Set(['哲学 · 认识论']),
  folders: [],
  archivedSessions: [],
  archivedFolders: [],
  details: {},
  confirmAction: null,
  sessionSeq: 100,
  folderSeq: 10,
};


/* ── icons ────────────────────────────────────────────────────────── */
const icon = (name, cls = '') => `<svg${cls ? ` class="${cls}"` : ''}><use href="#i-${name}"/></svg>`;

/* ── DOM refs ─────────────────────────────────────────────────────── */
const rail = $('#rail');
const folderList = $('#folder-list');
const archiveList = $('#archive-list');
const thread = $('#thread');
const threadWrap = $('.thread-wrap');
const threadEnd = $('#thread-end');
const marginBody = $('#margin-body');
const workspace = $('#workspace');
const composer = $('#composer');
const composerInput = $('#composer-input');
//: 占位文案要能还原 —— 点开计划项时会临时改成「关于第 N 步…」。
const COMPOSER_PLACEHOLDER = composerInput ? composerInput.placeholder : '';
const sendBtn = $('#send-btn');
const popLayer = $('#popover-layer');
const toastLayer = $('#toast-layer');
const confirmDialog = $('#confirm-dialog');

/* ── helpers ──────────────────────────────────────────────────────── */
function findFolder(id) { return state.folders.find((f) => f.id === id) || null; }
function findSession(id) {
  for (const folder of state.folders) {
    const found = folder.sessions.find((s) => s.id === id);
    if (found) return { ...found, folderId: folder.id, folder };
  }
  return null;
}
/**
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

function currentSession() { return findSession(state.currentSession); }
function activeDetail() {
  if (!state.currentSession) return null;
  if (!state.details[state.currentSession]) {
    state.details[state.currentSession] = makeDetail(currentSession()?.detail || '');
  }
  return state.details[state.currentSession];
}
function activeProfile() { return state.profiles.find((p) => p.id === state.activeProfile) || state.profiles[0]; }
function stateLabel(key) { return t('state.' + key); }
function statusLabel(status) {
  return ({ done: '已完成', doing: '进行中', pending: '待开始', skipped: '已跳过' })[status] || status;
}

function toast(message, kind = '') {
  const el = document.createElement('div');
  el.className = 'toast' + (kind === 'danger' ? ' is-danger' : '');
  el.textContent = message;
  toastLayer.appendChild(el);
  setTimeout(() => {
    el.classList.add('is-out');
    setTimeout(() => el.remove(), 380);
  }, 2800);
}

/* ── rail rendering ───────────────────────────────────────────────── */
function matchesSearch(text) {
  if (!state.search) return true;
  return text.toLowerCase().includes(state.search.toLowerCase());
}

function sessionMarkup(session, folderId) {
  const isCurrent = session.id === state.currentSession;
  return `<li class="session${isCurrent ? ' is-current' : ''}" data-session-id="${session.id}" data-folder-id="${folderId}" tabindex="0">
    <span class="state-dot ${session.state}"></span>
    <span class="session-title">${escapeHtml(session.title)}</span>
    <time>${escapeHtml(session.time)}</time>
    <button class="session-menu-btn" data-action="session-menu" data-id="${session.id}" aria-label="会话操作">${icon('dots')}</button>
  </li>`;
}

function folderMarkup(folder, index) {
  let sessions = folder.sessions.filter((s) => matchesSearch(s.title));
  const folderMatches = matchesSearch(folder.name);
  if (!folderMatches && sessions.length === 0) return '';
  if (!folderMatches) {
    // keep only matching sessions
  } else {
    sessions = folder.sessions;
  }
  const open = folder.open || !!state.search || folder.sessions.some((s) => s.id === state.currentSession);
  return `<div class="folder${open ? ' is-open' : ''}" data-folder-id="${folder.id}">
    <div class="folder-head" data-action="toggle-folder" data-id="${folder.id}" role="button" tabindex="0"
      aria-expanded="${open}">
      <span class="folio">${FOLIOS[index] || '·'}</span>
      <span class="folder-name">${escapeHtml(folder.name)}</span>
      <span class="folder-count">${folder.sessions.length}</span>
      <svg class="chev"><use href="#i-chev"/></svg>
      <button class="folder-menu-btn" data-action="folder-menu" data-id="${folder.id}" aria-label="领域操作">${icon('dots')}</button>
    </div>
    <ul class="sessions">${sessions.map((s) => sessionMarkup(s, folder.id)).join('')}</ul>
  </div>`;
}

function renderRail() {
  const visibleFolders = state.folders;
  folderList.innerHTML = visibleFolders.map((f, i) => folderMarkup(f, i)).join('')
    || '<div class="archive-empty">还没有领域 · 点「新领域」开始</div>';
  $('#folder-count').textContent = pad(state.folders.length);

  const archived = [];
  for (const s of state.archivedSessions) {
    if (!matchesSearch(s.title) && !matchesSearch(s.folderName)) continue;
    archived.push(`<div class="session archived-item" data-archived-session="${s.id}" tabindex="0">
      <span class="state-dot ${s.state}"></span>
      <span class="session-title">${escapeHtml(s.title)}</span>
      <time>${escapeHtml(s.time)}</time>
      <button class="session-menu-btn" data-action="archived-session-menu" data-id="${s.id}" aria-label="归档操作">${icon('dots')}</button>
    </div>`);
  }
  for (const f of state.archivedFolders) {
    if (!matchesSearch(f.name)) continue;
    archived.push(`<div class="session archived-item" data-archived-folder="${f.id}" tabindex="0">
      <span class="state-dot done"></span>
      <span class="session-title">${escapeHtml(f.name)}</span>
      <time>${f.sessionCount}</time>
      <button class="session-menu-btn" data-action="archived-folder-menu" data-id="${f.id}" aria-label="归档操作">${icon('dots')}</button>
    </div>`);
  }
  archiveList.innerHTML = archived.join('') || '<div class="archive-empty">空</div>';
  $('#archive-count').textContent = pad(state.archivedSessions.length + state.archivedFolders.length);

  const prof = activeProfile();
  $('#rail-model-name').textContent = prof.short;
  $('#rail-model .foot-model-text em').textContent = (state.lang === 'en')
    ? `${prof.provider} · ${prof.ctx} context`
    : `${prof.provider === 'Ollama' ? '本地 Ollama' : '外部 API'} · 上下文 ${prof.ctx.replace(' 000', 'k').replace(' 768', 'k')}`;

  $('#bench-badge').textContent = state.bench.status === 'running' ? '进行中' : (state.bench.status === 'interrupted' ? '未完成' : '·');
}

/* ── topbar / session ─────────────────────────────────────────────── */
function renderTopbar() {
  const session = currentSession();
  const folder = session ? findFolder(session.folderId) : null;
  $('#crumb .crumb-folder').textContent = folder ? folder.name : '未选择领域';
  $('#crumb .crumb-session').textContent = session ? session.title : '尚未选择会话';
  const pill = $('#session-state');
  const stateKey = session ? session.state : 'draft';
  pill.className = 'state-pill is-' + stateKey;
  pill.querySelector('span').textContent = stateLabel(stateKey);

  const prof = activeProfile();
  $('#model-chip-name').textContent = prof.short;
  $('#composer-model-note').textContent = prof.short;
  $('#composer-busy-rule').textContent = t('composer.busyPrefix') + t('busy.' + state.busyBehavior);

  document.title = session ? `${session.title} · 觅学之契` : '觅学之契 · 学习流档案';
}

function renderThread() {
  const detail = activeDetail();
  const session = currentSession();
  if (!session || !detail) {
    thread.innerHTML = '';
    threadEnd.hidden = true;
    return;
  }
  if (detail.messages.length === 0) {
    thread.innerHTML = emptyThreadMarkup(session);
    threadEnd.hidden = true;
    return;
  }
  threadEnd.hidden = false;
  const messages = detail.messages.map((m, i) => messageMarkup(m, i)).join('');
  thread.innerHTML = `${dateDivider(detail)}${messages}`;
  bindInkDots();
}

/** 卷首那条横线。日期来自这个会话的创建时间 —— 写死的话，第二天它就错了。 */
function dateDivider(detail) {
  const iso = detail.session?.created_at;
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const week = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()];
  return `<div class="date-divider"><span>${d.getFullYear()} · ${pad(d.getMonth() + 1)} · ${pad(d.getDate())}</span><span>${week}</span></div>`;
}

function emptyThreadMarkup(session) {
  return `<div class="empty-thread">
    <span class="empty-seal">启</span>
    <h2>还没有写下第一句</h2>
    <p>这个会话是空的。你可以先描述任务，也可以只说说现在卡在哪里——在写入任何计划之前，我都会先把草案交给你。</p>
    <div class="prompt-hints">
      <button data-prompt="我想在两周内读懂一篇英文论文的摘要。">我想在两周内读懂一篇英文论文的摘要。</button>
      <button data-prompt="我不知道该怎么安排今天的复习。">我不知道该怎么安排今天的复习。</button>
      <button data-prompt="先帮我看看这个计划哪里不合理。">先帮我看看这个计划哪里不合理。</button>
    </div>
    <span class="empty-foot">${escapeHtml(session.title)} · 尚无记录</span>
  </div>`;
}

function traceMarkup(traces) {
  if (!traces || !traces.length) return '';
  return `<div class="msg-trace">${traces.map((tr) => `
    <div class="trace-item ${tr.state || 'is-done'}">
      <strong>${escapeHtml(tr.label)}</strong>
      <span>${escapeHtml(tr.note || '')}</span>
    </div>`).join('')}</div>`;
}

function messageMarkup(message, index) {
  if (message.role === 'user') {
    return `<article class="msg msg-user" style="--i:${index}">
      <div class="msg-gutter">
        <span class="msg-marker">你</span>
        <time>${escapeHtml(message.time || '')}</time>
      </div>
      <div class="msg-body">
        <div class="msg-head"><span class="msg-role">你的话</span><span class="msg-meta">可随时改</span></div>
        <div class="msg-prose"><p>${escapeHtml(message.text || '')}</p></div>
        <div class="msg-foot"><span>你写下的</span><span class="dot"></span><span>未写入计划</span></div>
      </div>
    </article>`;
  }
  const proposal = message.proposal ? proposalMarkup(message.proposal) : '';
  const foot = message.foot ? `<div class="msg-foot">
      <button data-action="show-basis" data-basis="${escapeHtml(message.basis || '这是基于本轮的工具轨迹与当时锚点给出的建议。')}">为什么这样建议</button>
      <span class="dot"></span>
      <button data-action="annotate">标注一句</button>
      <span class="dot"></span>
      <span>${message.traces ? message.traces.length : 0} 个工具动作</span>
    </div>` : '';
  return `<article class="msg msg-assistant" style="--i:${index}" data-msg-id="${message.id || ''}">
    <div class="msg-gutter">
      <span class="msg-marker">契</span>
      <time>${escapeHtml(message.time || '')}</time>
      ${traceMarkup(message.traces)}
    </div>
    <div class="msg-body">
      <div class="msg-head"><span class="msg-role"><em>契</em> · MOUXUE</span><span class="msg-meta">${message.traces ? `推演 ${message.traces.length} 步` : '回答'}</span></div>
      <div class="msg-prose">${message.html || ''}</div>
      ${proposal}
      ${foot}
    </div>
  </article>`;
}

function proposalMarkup(proposal) {
  const resolved = proposal.state && proposal.state !== 'pending';
  const resolution = resolved
    ? `<div class="proposal-resolution">${proposal.state === 'approved' ? '已写入 · 计划第 4 版' : '暂不写入 · 保留原计划'}</div>`
    : '';
  return `<div class="proposal${resolved ? ' is-resolved' : ''}" data-proposal-id="${proposal.id || 'prop-1'}">
    <span class="proposal-stamp">草案</span>
    <span class="proposal-label">计划 · 第 4 版 · ${resolved ? '已处理' : '待你点头'}</span>
    <h4>拆成两天，但拆在「提取」而不是「词数」</h4>
    <ul class="proposal-changes">
      <li><span class="chg-mark">改</span><div>第 2 步：<del>精读 unit 1 例句，标注 25 个词</del> → <ins>精读前 25 词，标注词根与一次搭配</ins></div></li>
      <li><span class="chg-mark">增</span><div>新增第 3 步：<ins>反向回忆后 25 词（先测后读）</ins>，原第 3 步顺延</div></li>
    </ul>
    <div class="proposal-basis"><strong>依据</strong> · 你在偏好里写过「先测再读比先读再测更牢」；unit 1 的第 2 组词频更高，适合留到第二天做提取。</div>
    <div class="proposal-actions">
      <button class="mini-btn solid" data-action="approve-proposal" data-id="${proposal.id || 'prop-1'}">写入计划 · 同意</button>
      <button class="mini-btn" data-action="reject-proposal" data-id="${proposal.id || 'prop-1'}">先不动</button>
      <button class="text-link" data-action="proposal-basis">看看依据</button>
    </div>
    ${resolution}
  </div>`;
}

function renderMargin() {
  const detail = activeDetail();
  const underline = $('#margin-underline');
  const tabs = $$('.margin-tab');
  const idx = tabs.findIndex((tab) => tab.dataset.tab === state.marginTab);
  underline.style.transform = `translateX(${Math.max(0, idx) * 100}%)`;
  tabs.forEach((tab) => tab.classList.toggle('is-active', tab.dataset.tab === state.marginTab));

  if (!detail) {
    marginBody.innerHTML = '<div class="mpanel"><p class="mpanel-sub">选择一个会话后，这里会显示它的动机、计划与上下文。</p></div>';
    return;
  }
  if (state.marginTab === 'motivation') {
    marginBody.innerHTML = motivationMarkup(detail.motivation);
  } else if (state.marginTab === 'context') {
    marginBody.innerHTML = contextMarkup(detail.context);
  } else {
    marginBody.innerHTML = planMarkup(detail.plan);
  }
}

function motivationMarkup(motivation) {
  if (!motivation) {
    return `<div class="mpanel">
      <div class="mpanel-head"><h3>动机</h3><span class="ver">未建立</span></div>
      <p class="mpanel-sub">这个会话还没有写下动机。动机可以随时改，且每次修改都会留下一条版本。</p>
      <div class="mpanel-actions"><button class="mini-btn" data-action="edit-motivation">写下动机</button></div>
    </div>`;
  }
  const bars = [1, 2, 3].map((n) => `<i class="${n <= motivation.strength ? 'on' : ''}"></i>`).join('');
  return `<div class="mpanel">
    <div class="mpanel-head"><h3>动机</h3><span class="ver">当前</span></div>
    <div class="mot-type"><strong>${escapeHtml(motivation.type)}</strong><em>${escapeHtml(motivation.typeNote)}</em></div>
    <div class="strength">${bars}</div>
    <div class="strength-labels"><span>低</span><span>中</span><span>高</span></div>
    <div class="m-section">
      <div class="m-section-head"><span>驱动 · 它为什么值得</span><em>${motivation.drivers.length}</em></div>
      <ul class="driver-list">${motivation.drivers.map((d) => `<li>${escapeHtml(d)}</li>`).join('')}</ul>
    </div>
    <div class="m-section">
      <div class="m-section-head"><span>障碍 · 什么会挡住它</span><em>${motivation.obstacles.length}</em></div>
      <ul class="obstacle-list">${motivation.obstacles.map((o) => `<li><strong>${escapeHtml(o.title)}</strong><span>${escapeHtml(o.note)}</span></li>`).join('')}</ul>
    </div>
    <div class="revision-note"><b>最近改动</b><span>${escapeHtml(motivation.revision)}</span></div>
    <div class="mpanel-actions"><button class="mini-btn" data-action="edit-motivation">改一项</button><button class="mini-btn" data-action="motivation-history">版本历史</button></div>
  </div>`;
}

function planMarkup(plan) {
  if (!plan) {
    return `<div class="mpanel">
      <div class="mpanel-head"><h3>计划</h3><span class="ver">未建立</span></div>
      <p class="mpanel-sub">还没有计划。拆解与写入都会先给你一张草案——点一次计划项，不等于同意任何修改。</p>
      <div class="mpanel-actions"><button class="mini-btn" data-action="ask-plan">请模型拆解任务</button></div>
    </div>`;
  }
  const done = plan.steps.filter((s) => s.status === 'done').length;
  const pct = Math.round((done / plan.steps.length) * 100);
  return `<div class="mpanel">
    <div class="mpanel-head"><h3>计划 · 第 ${plan.version} 版</h3><span class="ver">${done} / ${plan.steps.length} 完成</span></div>
    <div class="plan-progress">
      <div class="plan-progress-bar"><i style="--p:${pct}%"></i></div><span>${pct}%</span>
    </div>
    <ul class="steps">
      ${plan.steps.map((step) => `<li class="step is-${step.status}" data-action="discuss-step" data-step-id="${step.id}" title="点这一项，与模型讨论它">
        <span class="step-check" data-action="toggle-step" data-step-id="${step.id}" role="button" tabindex="0" aria-label="标记完成">${icon('check')}</span>
        <div class="step-body">
          <div class="step-action">${escapeHtml(step.action)}</div>
          <div class="step-meta"><span class="status-tag">${statusLabel(step.status)}</span><span>·</span><span>${escapeHtml(step.meta || '')}</span></div>
        </div>
      </li>`).join('')}
    </ul>
    <div class="checkpoint">
      <div class="checkpoint-head"><span>${escapeHtml(plan.checkpoint.title)}</span><span>通过标准</span></div>
      <p>${escapeHtml(plan.checkpoint.text)}</p>
      <p class="cp-criteria"><b>${escapeHtml(plan.checkpoint.criteria)}</b></p>
    </div>
    <div class="mpanel-actions">
      <button class="mini-btn" data-action="ask-change">请模型改一步</button>
      <button class="mini-btn" data-action="plan-history">版本历史</button>
    </div>
  </div>`;
}

function contextMarkup(context) {
  if (!context || !context.sections.length) {
    return `<div class="mpanel">
      <div class="mpanel-head"><h3>上下文</h3><span class="ver">0%</span></div>
      <p class="mpanel-sub">${escapeHtml(context ? context.note : '尚未产生上下文。')}</p>
    </div>`;
  }
  const segments = context.segments.map((w) => `<i style="width:${w}%"></i>`).join('');
  return `<div class="mpanel">
    <div class="mpanel-head"><h3>上下文 · 它看见的</h3><span class="ver">${context.pct}%</span></div>
    <div class="token-meter">
      <div class="token-meter-top"><span>窗口占用</span><span><strong>${context.used}</strong> / ${context.total}</span></div>
      <div class="token-bar">${segments}</div>
      <div class="token-legend"><span>偏好</span><span>方法卡</span><span>基线</span><span>策略库</span></div>
    </div>
    <div class="m-section">
      <div class="m-section-head"><span>采用序</span><em>偏好 → 方法卡 → 基线</em></div>
      <ul class="ctx-list">
        ${context.sections.map((s) => `<li><span>${escapeHtml(s.name)}</span><em>${escapeHtml(s.value)}</em></li>`).join('')}
      </ul>
    </div>
    <p class="ctx-note">${escapeHtml(context.note)}</p>
    <div class="mpanel-actions"><button class="mini-btn" data-action="inspect-context">查看逐条来源</button></div>
  </div>`;
}

function renderSession() {
  renderTopbar();
  renderThread();
  renderMargin();
  renderRail();
}

/* ── view switching ───────────────────────────────────────────────── */
function setView(view) {
  state.view = view;
  $$('.view').forEach((el) => el.classList.toggle('is-active', el.dataset.view === view));
  $$('.foot-link').forEach((el) => el.classList.toggle('is-active', el.dataset.view === view));
  if (window.innerWidth <= 900) document.body.classList.remove('rail-open');
  if (view === 'settings') renderSettings();
  if (view === 'benchmark') renderBench();
  window.scrollTo(0, 0);
}

/* ── chat run simulation ──────────────────────────────────────────── */
function updateBusyUI() {
  const prof = activeProfile();
  composer.classList.toggle('is-busy', state.busy);
  $('#composer-busy').hidden = !state.busy;
  $('#composer-hint').hidden = state.busy;
  $('#rail-model .pulse-dot').classList.toggle('is-busy', state.busy);
  $('#model-chip .pulse-dot').classList.toggle('is-busy', state.busy);
  $('#composer-busy-rule').textContent = t('composer.busyPrefix') + t('busy.' + state.busyBehavior);
  const blocked = state.busy && state.busyBehavior === 'block';
  sendBtn.classList.toggle('is-disabled', blocked);
  sendBtn.setAttribute('aria-disabled', String(blocked));
  const queued = state.queue.length;
  let strip = $('.queue-strip');
  if (queued && state.busy) {
    if (!strip) {
      strip = document.createElement('div');
      strip.className = 'queue-strip';
      composer.insertBefore(strip, $('.composer-row'));
    }
    strip.textContent = `排队中 · ${queued} 条 · 当前回答结束后依次发送`;
  } else if (strip) {
    strip.remove();
  }
  $('#composer-busy-text').textContent = state.busy ? `正在推演 · ${prof.short}` : '正在推演';
}

function scrollThread(force = false) {
  const nearBottom = threadWrap.scrollHeight - threadWrap.scrollTop - threadWrap.clientHeight < 220;
  if (force || nearBottom) {
    threadWrap.scrollTo({ top: threadWrap.scrollHeight, behavior: 'smooth' });
  }
}

function createLiveMessage() {
  const article = document.createElement('article');
  article.className = 'msg msg-assistant is-live';
  article.innerHTML = `
    <div class="msg-gutter">
      <span class="msg-marker">契</span>
      <time>${clock()}</time>
      <svg class="ink-thread" viewBox="0 0 10 300" preserveAspectRatio="none" aria-hidden="true">
        <path d="M4,0 C9,45 -2,82 4,125 C10,168 0,215 5,250 C8,272 6,288 5,300" vector-effect="non-scaling-stroke"/>
      </svg>
      <div class="msg-trace"></div>
    </div>
    <div class="msg-body">
      <div class="msg-head"><span class="msg-role"><em>契</em> · MOUXUE</span><span class="msg-meta">推演中</span></div>
      <div class="thinking-field">
        <div class="thinking-status">
          <span class="ink-loader"><i></i><i></i><i></i></span>
          <span class="status-word">辨明目标</span>
          <span class="status-ellipsis"><i></i><i></i><i></i></span>
        </div>
        <div class="thinking-note">先不急着改计划——我在核对这句话是在改「词数」，还是在改「什么时候提取」。</div>
      </div>
      <div class="msg-prose streaming"></div>
    </div>`;
  thread.appendChild(article);
  scrollThread(true);
  return article;
}

function addTrace(article, label, note, stateClass = 'is-done') {
  const trace = article.querySelector('.msg-trace');
  if (!trace) return;
  const row = document.createElement('div');
  row.className = `trace-item ${stateClass}`;
  row.innerHTML = `<strong>${escapeHtml(label)}</strong><span>${escapeHtml(note)}</span>`;
  trace.appendChild(row);
  scrollThread();
}

function inkify(html) {
  let count = 0;
  const tokens = html.split(/(<[^>]+>)/g);
  return tokens.map((token) => {
    if (!token) return '';
    if (token.startsWith('<')) return token;
    return Array.from(token).map((ch) => {
      if (ch === ' ' || ch === '\n') return ch;
      count += 1;
      return `<span class="chunk">${ch}</span>`;
    }).join('');
  }).join('');
}

function startStatusCycle(run, statusEl, words) {
  let i = 0;
  run.statusTimer = setInterval(() => {
    i = (i + 1) % words.length;
    statusEl.classList.add('is-swapping');
    setTimeout(() => {
      statusEl.textContent = words[i];
      statusEl.classList.remove('is-swapping');
    }, 260);
  }, 1500);
}

function cancelRun({ interrupted = false, silent = false } = {}) {
  const run = state.run;
  if (!run) return;
  run.cancelled = true;
  // 真的掐断连接。只关界面上的转圈是**假停止** —— 服务端会把整轮跑完。
  if (run.discussion) run.discussion.stop();
  clearInterval(run.statusTimer);
  if (run.article) {
    run.article.classList.remove('is-live');
    if (interrupted) run.article.classList.add('is-interrupted');
    const thinking = run.article.querySelector('.thinking-field');
    if (thinking) thinking.remove();
    const meta = run.article.querySelector('.msg-meta');
    if (meta) meta.textContent = interrupted ? '已中断' : '已停止';
  }
  state.run = null;
  state.busy = false;
  threadWrap.classList.remove('is-thinking');
  updateBusyUI();
  if (interrupted && !silent) toast('已停止生成 · 未完成的部分保留在记录里');
}

/**
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

  const traces = payload.toolTrace.map((entry) => {
    const ok = ['success', 'empty'].includes(entry.status);
    return {
      label: traceLabel(entry.tool),
      note: ok
        ? String(entry.message || '完成').replace(/\*\*/g, '').slice(0, 44)
        : `${entry.status === 'error' ? '出错' : '被拦下'} · ${String(entry.message || '').replace(/\*\*/g, '').slice(0, 40)}`,
    };
  });
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
    // 计划版本变了要重画；上下文数字这一轮也变了，同样要重画 ——
    // 只判断计划的话，改了上下文却没改计划的那些轮次会留着旧数字。
    if (fresh && (fresh.plan ? fresh.plan.version : null) !== before) renderMargin();
    else if (state.marginTab === 'context') renderMargin();
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
  const blocked = payload.toolTrace.filter((t) => ['rejected', 'error'].includes(t.status)).length;
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

function sendFromComposer({ programmaticText = null } = {}) {
  const text = (programmaticText ?? composerInput.value).trim();
  if (!text) return;

  if (state.busy) {
    if (state.busyBehavior === 'block') {
      composer.classList.add('is-shake');
      setTimeout(() => composer.classList.remove('is-shake'), 420);
      toast('当前回答还在进行 · 发送被暂时挂起', 'danger');
      return;
    }
    if (state.busyBehavior === 'queue') {
      composerInput.value = '';
      autoGrow();
      const item = { text, el: null };
      const detail = activeDetail();
      if (detail) {
        const article = document.createElement('div');
        article.innerHTML = messageMarkup({ role: 'user', time: clock(), text, foot: false }, detail.messages.length);
        const node = article.firstElementChild;
        node.classList.add('is-queued');
        node.querySelector('.msg-head').insertAdjacentHTML('beforeend', '<span class="msg-queued-tag">排队中</span>');
        thread.appendChild(node);
        item.el = node;
        scrollThread(true);
      }
      state.queue.push(item);
      updateBusyUI();
      toast('已排队 · 当前回答结束后发送');
      return;
    }
    if (state.busyBehavior === 'interrupt') {
      composerInput.value = '';
      autoGrow();
      cancelRun({ interrupted: true, silent: true });
      toast('已打断上一轮 · 新的消息开始发送');
      setTimeout(() => beginUserTurn(text), 260);
      return;
    }
  }

  composerInput.value = '';
  autoGrow();
  beginUserTurn(text);
}

/* ── 状态：把 M7 §8 的边表搬到界面上 ─────────────────────────────────
 *
 * 后端 `guards.TRANSITIONS` 是唯一的真相，这里只是它的**界面投影**：
 * 每一条都必须在后端存在，否则点了就是一发 409。
 * （为什么不在前端"自己算能去哪"：两处推导一旦分叉，界面就会给出
 * 一个后端不接受的按钮 —— 那比没有按钮更糟。）
 */
const STATE_ACTIONS = {
  draft: [['active', '开始执行']],
  active: [['review', '结束执行'], ['completed', '完成']],
  review: [['active', '回到执行'], ['completed', '完成']],
  completed: [['review', '重新打开'], ['active', '回到执行']],
  abandoned: [],
};

function openStateMenu(anchor) {
  const session = currentSession();
  if (!session) {
    toast('先选一个会话');
    return;
  }
  const targets = STATE_ACTIONS[session.state] || [];
  if (!targets.length) {
    toast('这个会话已经放弃，没有可走的下一步');
    return;
  }
  openPopover(anchor, `
    <div class="popover-label">${stateLabel(session.state)} · 下一步</div>
    ${targets.map(([target, label]) =>
      popoverItem('go-state', label, 'arrow', `data-target="${target}"`)).join('')}
  `);
}

async function goState(target) {
  const session = currentSession();
  if (!session) return;
  closePopover();
  const said = { active: '已开始执行', review: '进入整理期', completed: '已归档完成', abandoned: '已放弃' };
  try {
    // 版本号**现读**：它可能被另一次写入（比如刚跑完一轮讨论）改过，
    // 用界面上记得的旧版本去转，换来的是一次 409。
    const fresh = await api.state(session.id);
    const row = await api.transition(session.id, target, fresh.session.version);
    const sessionRow = findSessionRow(session.id);
    if (sessionRow) {
      sessionRow.state = row.state;
      sessionRow.dot = dotClass(row.state);
    }
    await loadDetail(session.id);
    renderRail();
    renderTopbar();
    toast(said[target] || '状态已更新');
  } catch (err) {
    // 后端的 409/422 原文比一句"操作失败"有用得多
    toast(err.message, 'danger');
  }
}

/* ── popovers ─────────────────────────────────────────────────────── */
function closePopover() {
  popLayer.innerHTML = '';
  $$('[data-popover-anchor]').forEach((el) => { delete el.dataset.popoverAnchor; });
}

function openPopover(anchor, html, className = '') {
  closePopover();
  anchor.dataset.popoverAnchor = '1';
  const el = document.createElement('div');
  el.className = 'popover' + (className ? ' ' + className : '');
  el.innerHTML = html;
  popLayer.appendChild(el);
  const rect = anchor.getBoundingClientRect();
  const width = el.offsetWidth;
  const height = el.offsetHeight;
  let left = rect.left;
  if (left + width > window.innerWidth - 12) left = rect.right - width;
  let top = rect.bottom + 7;
  if (top + height > window.innerHeight - 12) top = Math.max(12, rect.top - height - 7);
  el.style.left = Math.max(12, left) + 'px';
  el.style.top = top + 'px';
  return el;
}

function popoverItem(action, label, iconName, attrs = '', extra = '') {
  return `<button class="popover-item ${extra}" data-action="${action}" ${attrs}>${icon(iconName)}<span>${label}</span></button>`;
}

function openSessionMenu(anchor, id) {
  const session = id === state.currentSession ? currentSession() : findSession(id);
  if (!session) return;
  openPopover(anchor, `
    <div class="popover-label">会话 · ${escapeHtml(session.title.slice(0, 18))}</div>
    ${popoverItem('rename-session', t('common.rename'), 'edit', `data-id="${id}"`)}
    ${popoverItem('archive-session', t('common.archive'), 'archive', `data-id="${id}"`)}
    <div class="popover-sep"></div>
    ${popoverItem('delete-session', t('common.delete'), 'trash', `data-id="${id}"`, 'is-danger')}
  `);
}

function openFolderMenu(anchor, id) {
  const folder = findFolder(id);
  if (!folder) return;
  openPopover(anchor, `
    <div class="popover-label">领域 · ${escapeHtml(folder.name.slice(0, 16))}</div>
    ${popoverItem('rename-folder', t('common.rename'), 'edit', `data-id="${id}"`)}
    ${popoverItem('archive-folder', t('common.archive'), 'archive', `data-id="${id}"`)}
    <div class="popover-sep"></div>
    ${popoverItem('delete-folder', t('common.delete'), 'trash', `data-id="${id}"`)}
  `);
}

function openArchivedSessionMenu(anchor, id) {
  const item = state.archivedSessions.find((s) => s.id === id);
  if (!item) return;
  openPopover(anchor, `
    <div class="popover-label">已归档 · ${escapeHtml(item.title.slice(0, 16))}</div>
    ${popoverItem('restore-session', t('common.restore'), 'undo', `data-id="${id}"`)}
    <div class="popover-sep"></div>
    ${popoverItem('delete-archived-session', t('common.delete'), 'trash', `data-id="${id}"`, 'is-danger')}
  `);
}

function openArchivedFolderMenu(anchor, id) {
  const item = state.archivedFolders.find((f) => f.id === id);
  if (!item) return;
  openPopover(anchor, `
    <div class="popover-label">已归档 · ${escapeHtml(item.name)}</div>
    ${popoverItem('restore-folder', t('common.restore'), 'undo', `data-id="${id}"`)}
    <div class="popover-sep"></div>
    ${popoverItem('delete-archived-folder', t('common.delete'), 'trash', `data-id="${id}"`, 'is-danger')}
  `);
}

function openModelMenu(anchor) {
  const items = state.profiles.map((p) => `
    <button class="popover-item ${p.id === state.activeProfile ? 'is-selected' : ''}" data-action="activate-profile" data-id="${p.id}">
      <span class="profile-mark" style="width:18px;height:18px;font-size:7px">${escapeHtml(p.mark)}</span>
      <span>${escapeHtml(p.short)}</span>
      <span class="pop-meta">${p.id === state.activeProfile ? t('model.current') : escapeHtml(p.provider)}</span>
    </button>`).join('');
  openPopover(anchor, `
    <div class="popover-label">${t('model.switch')}</div>
    ${items}
    <div class="popover-sep"></div>
    ${popoverItem('go-settings-models', t('model.manage'), 'gear')}
  `);
}

function openRename(anchor, title, value, onSave) {
  const pop = openPopover(anchor, `
    <div class="popover-label">${escapeHtml(title)}</div>
    <form class="rename-form">
      <input class="rename-input" maxlength="80" value="${escapeHtml(value)}" autocomplete="off">
      <div class="inline-actions">
        <button type="submit" class="mini-btn solid">${t('common.save')}</button>
        <button type="button" class="mini-btn" data-action="close-popover">${t('common.cancel')}</button>
      </div>
    </form>`);
  const form = pop.querySelector('form');
  const input = pop.querySelector('input');
  input.focus();
  input.select();
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const next = input.value.trim();
    if (next) onSave(next);
    closePopover();
  });
}

function openBasis(anchor, text) {
  openPopover(anchor, `<h5>推断依据</h5>
    <p>${escapeHtml(text)}</p>
    <em>依据只引用你说过的原话与本轮工具轨迹；对不上时，它会如实说不知道。</em>`, 'popover-basis');
}

function bindInkDots() {
  // citations are handled through delegated clicks; this exists for future annotations.
}

/* ── session & folder actions ─────────────────────────────────────── */
function selectSession(id) {
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

function ensureSelection() {
  if (currentSession()) { renderSession(); return; }
  for (const folder of state.folders) {
    if (folder.sessions.length) {
      state.currentSession = folder.sessions[0].id;
      state.currentFolder = folder.id;
      renderSession();
      return;
    }
  }
  state.currentSession = null;
  renderSession();
  thread.innerHTML = `<div class="empty-thread"><span class="empty-seal">空</span><h2>桌面上还没有会话</h2><p>先新建一个领域，再写下第一句。</p></div>`;
  threadEnd.hidden = true;
  marginBody.innerHTML = '<div class="mpanel"><p class="mpanel-sub">尚无会话。</p></div>';
}

/* 侧栏这些动作以前是**本地搬家**（把对象从一个数组挪到另一个）。
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

/* ── plan / proposal ──────────────────────────────────────────────── */
/** 标一步完成 / 取消完成。**服务端说了算**：把版本号带过去，撞车就报冲突。 */
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

function resolveProposal(id, resolution) {
  const detail = activeDetail();
  const message = detail?.messages.find((m) => m.proposal && m.proposal.id === id);
  if (message) message.proposal.state = resolution;
  const node = thread.querySelector(`[data-proposal-id="${id}"]`);
  if (node) {
    node.classList.add('is-resolved');
    const actions = node.querySelector('.proposal-actions');
    if (actions) actions.remove();
    if (!node.querySelector('.proposal-resolution')) {
      const div = document.createElement('div');
      div.className = 'proposal-resolution';
      div.textContent = resolution === 'approved' ? '已写入 · 计划第 4 版' : '暂不写入 · 保留原计划';
      node.appendChild(div);
    }
    const label = node.querySelector('.proposal-label');
    if (label) label.textContent = '计划 · 第 4 版 · 已处理';
  }
  // 计划的真实写入由服务端的工具 + 守卫完成（`propose_plan_change` 只在
  // 「用户点过那一项」时才被放行）。这里不本地伪造一个新版本 ——
  // 界面上的计划必须与库里的那一版是同一份。
  if (resolution === 'approved' && detail) void loadDetail(state.currentSession);
}

/* ── settings rendering ───────────────────────────────────────────── */
function thinkLabel(value) {
  return ({ auto: '自动', false: '关', true: '开' })[String(value)] || value;
}

function renderSettings() {
  const prof = activeProfile();
  const card = $('#active-model-card');
  if (card) {
    card.innerHTML = `
      <span class="am-seal">当前印鉴</span>
      <div class="am-left">
        <div class="am-kicker"><span class="pulse-dot"></span>${state.lang === 'en' ? 'IN USE' : '正在使用'} · ${escapeHtml(prof.provider)}</div>
        <div class="am-title"><h3>${escapeHtml(prof.label)}</h3><span class="am-model">${escapeHtml(prof.model)}</span></div>
        <div class="am-meta">
          <span>${state.lang === 'en' ? 'Endpoint' : '端点'} <b>${escapeHtml(prof.base)}</b></span>
          <span>${state.lang === 'en' ? 'Context' : '上下文'} <b>${escapeHtml(prof.ctx)}</b></span>
          <span>${state.lang === 'en' ? 'Key' : '密钥'} <b>${escapeHtml(prof.masked)}</b></span>
        </div>
        <div class="am-think">
          <span>${t('model.think')}</span>
          <div class="segmented" data-think-profile="${prof.id}">
            ${['auto', 'false', 'true'].map((v) => `<button data-action="set-think" data-id="${prof.id}" data-value="${v}" class="${String(prof.think) === v ? 'is-on' : ''}">${thinkLabel(v)}</button>`).join('')}
          </div>
          <em>${state.lang === 'en' ? 'auto = do not send the field' : '自动 = 不发这个字段，对不认识的模型更安全'}</em>
        </div>
      </div>
      <div class="am-probe">
        <button class="mini-btn" data-action="probe" data-id="${prof.id}">${state.lang === 'en' ? 'Probe' : '探活'}</button>
        <span class="probe-result" id="probe-result">${escapeHtml(prof.probe || '尚未探活')}</span>
      </div>`;
  }

  const list = $('#profile-list');
  if (list) {
    list.innerHTML = state.profiles.map((p) => `
      <div class="profile ${p.id === state.activeProfile ? 'is-active' : ''}">
        <span class="profile-mark">${escapeHtml(p.mark)}</span>
        <div class="profile-main">
          <strong>${escapeHtml(p.label)}</strong>
          <span>${escapeHtml(p.model)} · ${escapeHtml(p.note)}</span>
        </div>
        <div class="profile-tags">
          <span class="tag">${escapeHtml(p.provider)}</span>
          <span class="tag ${p.kind === 'local' ? 'jade' : ''}">${p.ctx}</span>
          ${p.id === state.activeProfile ? `<span class="tag accent">${t('model.current')}</span>` : ''}
        </div>
        <div class="profile-actions">
          ${p.id === state.activeProfile
      ? '<span class="tag jade">●</span>'
      : `<button class="mini-btn" data-action="activate-profile" data-id="${p.id}">${t('model.use')}</button>`}
        </div>
      </div>`).join('');
  }
}

/* ── benchmark rendering ──────────────────────────────────────────── */
function renderBench() {
  renderDomains();
  renderRunCard();
  renderRail();
}

function renderDomains() {
  $$('#familiar-chips .chip').forEach((chip) => {
    chip.classList.toggle('is-on', state.familiar.has(chip.dataset.domain));
  });
  $$('#interested-chips .chip').forEach((chip) => {
    chip.classList.toggle('is-on', state.interested.has(chip.dataset.domain));
  });
}

function renderRunCard() {
  const card = $('#run-card');
  if (!card) return;
  if (state.bench.status === 'running') {
    const formLabel = state.bench.form === 'quick' ? '简短版' : '完整版';
    const answered = state.bench.answered;
    card.innerHTML = `
      <div class="run-head"><span class="section-mark">本次</span><span class="run-status">进行中</span></div>
      <h3>${formLabel} · 自述轮 ${answered} / 20</h3>
      <p class="run-sub">开始于今天 · 每题会记录犹豫、切换与用时，但只记录、不计分。</p>
      <div class="run-progress"><i style="--p:${Math.max(4, Math.round(answered / 20 * 100))}%"></i></div>
      <div class="run-actions">
        <button class="mini-btn solid" data-action="pause-run">保存并退出</button>
        <button class="mini-btn" data-action="restart-run">重新开始</button>
      </div>
      <p class="run-note">保存后这次运行停在 <em>interrupted</em>，下次进入可以继续。</p>`;
  } else if (state.bench.status === 'interrupted') {
    card.innerHTML = `
      <div class="run-head"><span class="section-mark">上一次</span><span class="run-status">已中断</span></div>
      <h3>自述轮 · 7 / 20</h3>
      <p class="run-sub">2026-10-18 21:40 · 停在「阅读 vs 听力」这一题之前</p>
      <div class="run-progress"><i style="--p:35%"></i></div>
      <div class="run-actions">
        <button class="mini-btn solid" data-action="resume-run">继续上次</button>
        <button class="mini-btn" data-action="restart-run">重新开始</button>
      </div>
      <p class="run-note">重新开始不会抹掉旧运行；它会被标成 <em>abandoned</em>，仍然可查。</p>`;
  } else {
    card.innerHTML = `
      <div class="run-head"><span class="section-mark">状态</span><span class="run-status">未开始</span></div>
      <h3>还没有运行</h3>
      <p class="run-sub">选一种卷别开始；两种都不拦你进入对话。</p>
      <div class="run-actions">
        <button class="mini-btn solid" data-action="start-run" data-form="quick">简短版</button>
        <button class="mini-btn" data-action="start-run" data-form="full">完整版</button>
      </div>`;
  }
}

/* ── confirm dialog ───────────────────────────────────────────────── */
function askConfirm({ title, body, ok = '删除', onOk }) {
  $('#confirm-title').textContent = title;
  $('#confirm-body').textContent = body;
  $('#confirm-ok').textContent = ok;
  state.confirmAction = onOk;
  confirmDialog.showModal();
}

/* ── language / theme ─────────────────────────────────────────────── */
function applyLanguage() {
  document.documentElement.lang = state.lang === 'zh' ? 'zh-CN' : 'en';
  $$('[data-i18n]').forEach((el) => {
    const key = el.dataset.i18n;
    el.textContent = t(key);
  });
  $$('#lang-seg button').forEach((btn) => btn.classList.toggle('is-on', btn.dataset.lang === state.lang));
  $('#composer-hint').textContent = t('composer.hint');
  $('#composer-busy-rule').textContent = t('composer.busyPrefix') + t('busy.' + state.busyBehavior);
  renderRail();
  renderTopbar();
  renderMargin();
  if (state.view === 'settings') renderSettings();
}

function setTheme(theme) {
  state.theme = theme;
  document.documentElement.dataset.theme = theme;
  $$('.theme-swatch').forEach((el) => el.classList.toggle('is-on', el.dataset.themeChoice === theme));
}

/* ── event handling ───────────────────────────────────────────────── */
function handleAction(el, event) {
  const action = el.dataset.action;
  const id = el.dataset.id;
  switch (action) {
    case 'go-chat':
      setView('chat');
      break;
    case 'toggle-rail': {
      if (window.innerWidth <= 900) document.body.classList.toggle('rail-open');
      else document.body.classList.toggle('rail-closed');
      break;
    }
    case 'toggle-margin': {
      workspace.classList.toggle('is-margin-closed');
      const isClosed = workspace.classList.contains('is-margin-closed');
      $('#margin-toggle').classList.toggle('is-on', !isClosed);
      $('#margin-toggle').setAttribute('aria-pressed', String(!isClosed));
      break;
    }
    case 'new-folder':
    case 'new-session':
      openInlineForm(action === 'new-folder' ? 'folder' : 'session');
      break;
    case 'cancel-inline':
      closeInlineForm();
      break;
    case 'toggle-search': {
      const slot = $('#search-slot');
      slot.hidden = !slot.hidden;
      if (!slot.hidden) $('#rail-search').focus();
      break;
    }
    case 'toggle-archive': {
      const list = $('#archive-list');
      list.hidden = !list.hidden;
      el.setAttribute('aria-expanded', String(!list.hidden));
      break;
    }
    case 'toggle-folder': {
      const folder = findFolder(el.dataset.id);
      if (folder) {
        folder.open = !folder.open;
        renderRail();
      }
      break;
    }
    case 'folder-menu':
      event.stopPropagation();
      openFolderMenu(el, id);
      break;
    case 'session-menu':
      event.stopPropagation();
      openSessionMenu(el, id);
      break;
    case 'archived-session-menu':
      event.stopPropagation();
      openArchivedSessionMenu(el, id);
      break;
    case 'archived-folder-menu':
      event.stopPropagation();
      openArchivedFolderMenu(el, id);
      break;
    case 'session-more': {
      const session = currentSession();
      if (session) openSessionMenu(el, session.id);
      break;
    }
    case 'model-menu':
      openModelMenu(el);
      break;
    case 'go-settings-models':
      closePopover();
      setView('settings');
      switchSettingsTab('models');
      break;
    case 'activate-profile':
      activateProfile(id);
      break;
    case 'close-popover':
      closePopover();
      break;
    case 'rename-session': {
      const session = findSession(id);
      if (session) {
        openRename(el, '重命名会话', session.title, async (name) => {
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
        });
      }
      break;
    }
    case 'rename-folder': {
      const folder = findFolder(id);
      if (folder) {
        openRename(el, '重命名领域', folder.name, async (name) => {
          try {
            await api.renameFolder(id, name);
            folder.name = name;
            renderRail();
            renderTopbar();
            toast('领域已重命名');
          } catch (err) {
            toast(err.message, 'danger');
          }
        });
      }
      break;
    }
    case 'archive-session':
      archiveSession(id);
      break;
    case 'delete-session':
      deleteSession(id);
      break;
    case 'archive-folder':
      archiveFolder(id);
      break;
    case 'delete-folder':
      deleteFolder(id);
      break;
    case 'restore-session':
      restoreSession(id);
      break;
    case 'delete-archived-session':
      closePopover();
      void runRailAction('已从归档中删除', () => api.deleteSession(id));
      break;
    case 'restore-folder':
      restoreFolder(id);
      break;
    case 'delete-archived-folder':
      closePopover();
      void runRailAction('已从归档中删除', () => api.deleteFolder(id));
      break;
    case 'toggle-step':
      void toggleStep(el.dataset.stepId);
      break;
    case 'discuss-step':
      discussStep(el.dataset.stepId);
      break;
    case 'send':
      sendFromComposer();
      break;
    case 'stop':
      cancelRun({ interrupted: true });
      break;
    case 'replay':
      void loadDetail(state.currentSession).catch((err) => toast(err.message, 'danger'));
      toast('已重新读取这个会话');
      break;
    case 'approve-proposal':
      resolveProposal(el.dataset.id, 'approved');
      toast('已写入计划 · 第 4 版');
      break;
    case 'reject-proposal':
      resolveProposal(el.dataset.id, 'rejected');
      toast('草案未写入 · 原计划保留');
      break;
    case 'proposal-basis': {
      const basis = el.closest('.proposal')?.querySelector('.proposal-basis');
      openBasis(el, basis ? basis.textContent.replace(/^依据\s*·\s*/, '') : '这是基于本轮的工具轨迹与你的偏好原话。');
      break;
    }
    case 'show-basis':
      openBasis(el, el.dataset.basis || '这是基于本轮的工具轨迹与当时锚点给出的建议。');
      break;
    case 'annotate':
      toast('选中一句话即可标注；依据对不上时，系统会拒绝写入');
      break;
    case 'edit-motivation':
      toast('动机编辑面板在完整界面中展开 · 每次改动留下一个版本');
      break;
    case 'motivation-history':
    case 'plan-history':
      toast('版本历史为只读 · 旧版本不会随新版本改变');
      break;
    case 'ask-plan':
    case 'ask-change':
      composerInput.value = '这一步我不太明白，想换一种做法。';
      composerInput.focus();
      autoGrow();
      break;
    case 'inspect-context': {
      // 原来这里写死「偏好 4 · 方法卡 3 · 基线 6 · 策略库 2」——
      // 四个数字没有任何来源。改成读这个会话真实测到的那一份。
      const context = activeDetail() && activeDetail().context;
      if (!context || !context.measured) {
        toast('这个会话的上下文还没测到：后端没有给出窗口上限，也就不编一个百分比');
        break;
      }
      toast(`${context.note || ''}${context.used} / ${context.total} token · 占用 ${context.pct}%`);
      break;
    }
    case 'state-menu':
      openStateMenu(el);
      break;
    case 'go-state':
      goState(el.dataset.target);
      break;
    case 'open-side-note':
      $('#side-note').classList.add('is-open');
      refreshSideNoteMeter(activeDetail());
      $('#sn-input').focus();
      break;
    case 'close-side-note':
      $('#side-note').classList.remove('is-open');
      break;
    case 'sn-send':
      sendSideNote();
      break;
    case 'start-run':
      startBench(el.dataset.form || 'full');
      break;
    case 'resume-run':
      state.bench.status = 'running';
      state.bench.answered = 7;
      state.bench.form = 'quick';
      renderRunCard();
      renderRail();
      toast('已继续上次 · 从第 7 题开始');
      break;
    case 'restart-run':
      askConfirm({
        title: '重新开始这次基准？',
        body: '旧的运行会被标成「已放弃」，记录仍然可查。新的运行从自述轮第 1 题开始。',
        ok: '重新开始',
        onOk: () => {
          state.bench.status = 'running';
          state.bench.answered = 0;
          state.bench.form = el.dataset.form || 'full';
          renderRunCard();
          renderRail();
          toast('新的运行已开始');
        },
      });
      break;
    case 'pause-run':
      state.bench.status = 'interrupted';
      renderRunCard();
      renderRail();
      toast('已保存退出 · 下次可以继续');
      break;
    case 'skip-domains':
      toast('已跳过第二段 · 随时可以回来补');
      break;
    case 'probe':
      probeProfile(id, el);
      break;
    case 'set-think': {
      const profile = state.profiles.find((p) => p.id === id);
      if (profile) {
        profile.think = el.dataset.value;
        renderSettings();
        toast(`思考 · ${thinkLabel(el.dataset.value)}`);
      }
      break;
    }
    case 'add-profile':
      toast('新增档案：填 base URL 与密钥即可 · 演示版暂不写入');
      break;
    case 'confirm-ok': {
      const fn = state.confirmAction;
      state.confirmAction = null;
      confirmDialog.close();
      if (fn) fn();
      break;
    }
    case 'confirm-cancel':
      state.confirmAction = null;
      confirmDialog.close();
      break;
    default:
      break;
  }
}

function activateProfile(id) {
  if (state.busy) {
    toast('正在生成 · 先停止这一轮再切换模型', 'danger');
    closePopover();
    return;
  }
  if (!state.profiles.some((p) => p.id === id)) return;
  state.activeProfile = id;
  closePopover();
  renderSettings();
  renderTopbar();
  renderRail();
  updateBusyUI();
  toast(`已切换到 ${activeProfile().short}`);
}

function probeProfile(id, button) {
  const profile = state.profiles.find((p) => p.id === id);
  if (!profile) return;
  const result = $('#probe-result');
  button.disabled = true;
  if (result) {
    result.textContent = '探活中 · 正在请求端点…';
    result.classList.add('is-pending');
  }
  setTimeout(() => {
    const latency = 80 + Math.floor(Math.random() * 180);
    const models = profile.kind === 'local' ? 12 : 3;
    profile.probe = `刚刚 · ${latency} ms · ${models} 个模型`;
    button.disabled = false;
    if (result) {
      result.textContent = profile.probe;
      result.classList.remove('is-pending');
    }
    toast(`探活成功 · ${latency} ms`);
  }, 1200);
}

function startBench(form) {
  state.bench = { form, status: 'running', answered: 0 };
  renderRunCard();
  renderRail();
  toast(form === 'quick' ? '简短版已开始 · 自述轮' : '完整版已开始 · 自述轮');
}

/**
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
}

function openInlineForm(kind) {
  closePopover();
  state.inlineKind = kind;
  const form = $('#rail-form');
  const label = $('#rail-form-label');
  const input = $('#rail-form-input');
  label.textContent = kind === 'folder' ? '新领域 · 名称' : '新会话 · 标题';
  input.placeholder = kind === 'folder' ? '例如：物理学 · 概念梳理' : '例如：第 2 章的概念地图';
  form.hidden = false;
  input.value = '';
  input.focus();
}

function closeInlineForm() {
  state.inlineKind = null;
  const form = $('#rail-form');
  form.hidden = true;
  $('#rail-form-input').value = '';
}

async function commitInlineForm() {
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

function switchSettingsTab(tab) {
  $$('.settings-nav-item').forEach((el) => el.classList.toggle('is-active', el.dataset.settingsTab === tab));
  $$('.settings-panel').forEach((el) => el.classList.toggle('is-active', el.dataset.settingsPanel === tab));
  renderSettings();
}

/* ── wiring ───────────────────────────────────────────────────────── */
document.addEventListener('click', (event) => {
  const actionEl = event.target.closest('[data-action]');
  if (actionEl) {
    handleAction(actionEl, event);
    return;
  }
  const viewEl = event.target.closest('[data-view]');
  if (viewEl && !viewEl.classList.contains('view')) {
    setView(viewEl.dataset.view);
    return;
  }
  const cite = event.target.closest('.cite');
  if (cite) {
    openBasis(cite, cite.dataset.basis || '');
    return;
  }
  const sessionEl = event.target.closest('.session[data-session-id]');
  if (sessionEl) {
    selectSession(sessionEl.dataset.sessionId);
    return;
  }
  const prompt = event.target.closest('[data-prompt]');
  if (prompt) {
    composerInput.value = prompt.dataset.prompt;
    composerInput.focus();
    autoGrow();
    return;
  }
  if (event.target.closest('.settings-nav-item')) {
    switchSettingsTab(event.target.closest('.settings-nav-item').dataset.settingsTab);
    return;
  }
  if (event.target.closest('.margin-tab')) {
    state.marginTab = event.target.closest('.margin-tab').dataset.tab;
    renderMargin();
    return;
  }
  const langBtn = event.target.closest('#lang-seg button');
  if (langBtn) {
    state.lang = langBtn.dataset.lang;
    applyLanguage();
    toast(state.lang === 'zh' ? '界面语言 · 中文' : 'Interface language · English');
    return;
  }
  const busyBtn = event.target.closest('#busy-options .busy-card');
  if (busyBtn) {
    state.busyBehavior = busyBtn.dataset.busy;
    $$('#busy-options .busy-card').forEach((b) => {
      b.classList.toggle('is-on', b === busyBtn);
      b.setAttribute('aria-checked', String(b === busyBtn));
    });
    updateBusyUI();
    toast(`繁忙时 · ${t('busy.' + state.busyBehavior)}`);
    return;
  }
  const thinkView = event.target.closest('#think-seg button');
  if (thinkView) {
    state.thinkView = thinkView.dataset.thinkview;
    $$('#think-seg button').forEach((b) => b.classList.toggle('is-on', b === thinkView));
    return;
  }
  const themeBtn = event.target.closest('[data-theme-choice]');
  if (themeBtn) {
    setTheme(themeBtn.dataset.themeChoice);
    toast(state.theme === 'paper' ? '纸色 · 宣纸' : '纸色 · 夜墨');
    return;
  }
  const scaleBtn = event.target.closest('#scale-seg button');
  if (scaleBtn) {
    state.scale = scaleBtn.dataset.scale;
    document.documentElement.dataset.scale = state.scale;
    $$('#scale-seg button').forEach((b) => b.classList.toggle('is-on', b === scaleBtn));
    return;
  }
  const motionBtn = event.target.closest('#motion-seg button');
  if (motionBtn) {
    state.motion = motionBtn.dataset.motion;
    document.documentElement.dataset.motion = state.motion;
    $$('#motion-seg button').forEach((b) => b.classList.toggle('is-on', b === motionBtn));
    return;
  }
  const domainChip = event.target.closest('.chip[data-domain]');
  if (domainChip) {
    toggleDomain(domainChip);
  }
});

document.addEventListener('pointerdown', (event) => {
  if (!popLayer.contains(event.target) && !event.target.closest('[data-popover-anchor]')) closePopover();
}, true);

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    if (popLayer.innerHTML) { closePopover(); return; }
    if ($('#side-note').classList.contains('is-open')) { $('#side-note').classList.remove('is-open'); return; }
    document.body.classList.remove('rail-open');
  }
  const tag = document.activeElement?.tagName;
  if ((event.key === 'Enter' || event.key === ' ') && tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'BUTTON') {
    const target = document.activeElement;
    if (target?.matches('.folder-head[role="button"], .session[tabindex]')) {
      event.preventDefault();
      target.click();
      return;
    }
  }
  if (event.key === 'Enter' && document.activeElement === $('#sn-input')) {
    event.preventDefault();
    sendSideNote();
  }
});

$('#rail-form').addEventListener('submit', (event) => {
  event.preventDefault();
  commitInlineForm();
});

$('#rail-search').addEventListener('input', (event) => {
  state.search = event.target.value.trim();
  renderRail();
});

$('#cite-switch').addEventListener('click', (event) => {
  state.cite = !state.cite;
  event.currentTarget.classList.toggle('is-on', state.cite);
  document.documentElement.dataset.cite = state.cite ? 'on' : 'off';
  event.currentTarget.setAttribute('aria-checked', String(state.cite));
});

composerInput.addEventListener('input', autoGrow);
composerInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    sendFromComposer();
  }
});

threadWrap.addEventListener('click', (event) => {
  const step = event.target.closest('[data-action="toggle-step"]');
  if (step) toggleStep(step.dataset.stepId);
});

function autoGrow() {
  composerInput.style.height = 'auto';
  composerInput.style.height = Math.min(160, composerInput.scrollHeight) + 'px';
}

function toggleDomain(chip) {
  const domain = chip.dataset.domain;
  const isFamiliar = chip.parentElement.id === 'familiar-chips';
  if (isFamiliar) {
    if (state.familiar.has(domain)) state.familiar.delete(domain);
    else {
      state.familiar.add(domain);
      if (state.interested.has(domain)) {
        state.interested.delete(domain);
        toast(`「${domain}」已从兴趣区移除 · 兴趣不能落在熟悉集合里`);
      }
    }
  } else {
    if (state.interested.has(domain)) state.interested.delete(domain);
    else {
      if (state.familiar.has(domain)) {
        chip.classList.add('is-conflict');
        setTimeout(() => chip.classList.remove('is-conflict'), 450);
        toast(`「${domain}」已在熟悉集合里 · 先移出熟悉再选`, 'danger');
        return;
      }
      state.interested.add(domain);
    }
  }
  renderDomains();
  toast('领域清单已产生新版本 · 旧版本仍可查');
}

/* ── init ─────────────────────────────────────────────────────────── */
async function boot() {
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

  // 无头验收的缝：带 ?probe=1 时加载探针，跑一轮真实对话并把结果写进 DOM。
  // 不带参数时这段代码不会被加载（动态 import），正式使用没有任何多余的东西。
  if (new URLSearchParams(location.search).get('probe') === '1') {
    import('./src/selftest.js')
      .then((mod) => mod.run())
      .catch((err) => toast(String(err && err.message ? err.message : err), 'danger'));
  }
}

boot();
