'use strict';

/* ═══════════════════════════════════════════════════════════════════
   学习流档案 · StudyFlow Archive —— 界面层

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
import { adaptContext, adaptDetail, adaptWorkspace, dotClass, escape, shortTime, traceRow, traceRows } from './src/adapt.js';
import { richText } from './src/rich.js';
import { drawContextRing } from './src/context-ring.js';
import { ThreadIndex } from './src/thread-index.js';
import { runDiscussion } from './src/live.js';
import { mountSelfAssessment } from './src/self-assessment.js';

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

/* ── i18n ─────────────────────────────────────────────────────────── */
const I18N = {
  zh: {
    'brand.name': '学习流档案',
    'brand.tag': 'StudyFlow Archive',
    'rail.newFolder': '新领域',
    'rail.newSession': '新会话',
    'rail.folders': '领域 · Folders',
    'rail.archive': '已归档',
    'sa.pageKicker': '认识自己 · 不是考试',
    'sa.pageKickerNote': 'SELF-ASSESSMENT',
    'sa.heroTitle': '先说说你',
    'sa.heroTitleEm': '平时怎么做',
    'sa.heroIntro': '没有对错，也不计时打分。你答得越具体，后面给你的计划就越贴合你自己的方式。',
    'sa.meta1': '随时可以停下',
    'sa.meta2': '答过的不会丢',
    'sa.meta3': '可以先做简短版',
    'sa.startShort': '做简短版 · 90 条',
    'sa.startFull': '做全面版 · 127 条',
    'sa.whatMark': '这些答案去哪了',
    'sa.whatTitle': '它不会给你一个分数',
    'sa.whatNote': '自评只回答"你认为自己怎么做"，所以它记录的是一条自述，不和任何实测混算。',
    'sa.note1Title': '它影响什么',
    'sa.note1Body': '影响我给你的建议用什么方式呈现——先讲例子还是先讲结构、给不给材料、要不要拆得更细。',
    'sa.note2Title': '它不影响什么',
    'sa.note2Body': '不会限制你能做什么，也不会被当成能力结论。想改随时改，改完以新的那条为准。',
    'sa.note3Title': '什么时候更新',
    'sa.note3Body': '不用刻意重做。每次会话归档时的复盘、以及你在侧记里的对话，都会带来新的认识。',
    'set.generalTitle': '语言与发送', 'set.generalIntro': '只影响界面与输入。',
    'set.langTitle': '界面语言', 'set.langDesc': '只改界面，不改内容。',
    'set.busyTitle': '繁忙时发送', 'set.busyDesc': '回答进行中又发了一句。',
    'set.queue': '排队等待', 'set.queueDesc': '排到当前回答之后。',
    'set.interrupt': '打断并发送', 'set.interruptDesc': '立刻停止并发送。',
    'set.block': '暂不允许', 'set.blockDesc': '这一轮结束前不能发。',
    'set.thinkTitle': '思考过程', 'set.thinkDesc': '默认是否展开。', 'set.fold': '折叠', 'set.open': '常显',
    'set.citeTitle': '依据标注', 'set.citeDesc': '在引用处留下墨点。',
    'set.modelsTitle': '模型设置', 'set.modelsIntro': '设置接下来用哪个模型。',
    'set.addProfile': '新增一份模型档案', 'set.addProfileNote': 'Ollama 或 OpenAI 兼容',
    'set.keyNote': '密钥只保存在本机，不会回显。探活会真实请求一次。',
    'set.appearanceTitle': '纸色与字面', 'set.appearanceIntro': '只换颜色，不改布局。',
    'set.paper': '宣纸', 'set.paperDesc': '暖白 · 墨字 · 朱砂',
    'set.ink': '夜墨', 'set.inkDesc': '深墨 · 米白字 · 暗朱',
    'set.scaleTitle': '正文字号', 'set.scaleDesc': '只影响正文。', 'set.small': '小', 'set.normal': '标准', 'set.large': '大',
    'set.motionTitle': '推演动效', 'set.motionDesc': '控制推演动画。', 'set.full': '完整', 'set.restrained': '克制',
    'sa.entryKicker': '自评 · 一次性',
    'sa.entryTitle': '先回答「你平时怎么做」',
    'sa.entryNote': '90 条或 127 条，按你愿意花的时间选。它不给人材料、也不判对错；产出的是自述通道的读数。',
    'sa.start': '开始自评',
    'sa.viewResult': '看上一次的结果',
    'cog.kicker': '全局认知',
    'cog.heroMark': '认知 · 其一',
    'cog.kickerNote': 'COGNITION · 跨会话',
    'cog.title': '你的认知',
    'cog.titleEm': '概览',
    'cog.intro': '偏好、基线、证据与假说构成你的全局认知；跨会话保留，可随时修订。',
    'cog.openSideNote': '打开侧记 · 讨论或提议修改',
    'cog.refresh': '重新读取',
    'cog.note': '归档时自动复盘，提案经确认后写入。',
    'cog.baseline': 'M1 基线',
    'cog.baselineNote': '每行一个变量；右侧三列为自述、经验、实测。',
    'cog.parallel': '偏好 · 证据 · 假说',
    'cog.parallelNote': '偏好：你确立的规范；证据：实践中验证过的事实；假说：待验证的猜想。',
    'cog.prefs': '偏好',
    'cog.evidence': '证据',
    'cog.hypotheses': '假说',
    'cog.hard': '硬',
    'cog.soft': '软',
    'cog.stated': '自述',
    'cog.experienced': '经验',
    'cog.measured': '实测',
    'cog.variable': '变量',
    'cog.hardCount': '硬约束',
    'cog.softCount': '软偏好',
    'cog.measuredCount': '已测通道',
    'cog.evidenceCount': '证据',
    'cog.hypothesisCount': '假说',
    'cog.priority': '优先级',
    'cog.unverified': '未验证',
    'rail.chat': '对话',
    'rail.cognition': '认知',
    'rail.benchmark': '自评',
    'rail.settings': '设置',
    'rail.modelNote': '本地 Ollama · 上下文 32k',
    'rail.colophon': '持久化学习流 · 每句话可回溯',
    'common.backToChat': '返回对话', 'set.kickerNote': 'SETTINGS · 模型与偏好', 'cog.chat': '和侧记聊聊', 'set.mark1': '其一', 'set.mark2': '其二', 'set.mark3': '其三',
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
    'composer.hint': '写明基础、目的与障碍；不明确处会先询问。',
    'composer.busyPrefix': '繁忙时：',
    'settings.kicker': '设置',
    'settings.general': '通用',
    'settings.generalNote': '语言 · 繁忙时行为',
    'settings.models': '模型',
    'settings.modelsNote': '档案 · 模型',
    'settings.appearance': '外观',
    'settings.appearanceNote': '纸色 · 字号 · 动效',
    'settings.version': '界面演示 · 非最终设计',
    'state.active': '进行中',
    'state.review': '待复盘',
    'state.completed': '已归档',
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
    'brand.name': 'StudyFlow',
    'brand.tag': 'Archive',
    'rail.newFolder': 'New field',
    'rail.newSession': 'New session',
    'rail.folders': 'Fields · Folders',
    'rail.archive': 'Archived',
    'sa.pageKicker': 'Getting to know you · not a test',
    'sa.pageKickerNote': 'SELF-ASSESSMENT',
    'sa.heroTitle': 'Tell me how',
    'sa.heroTitleEm': 'you actually study',
    'sa.heroIntro': 'No right answers, no timer, no score. The more concretely you answer, the better the plan fits the way you already work.',
    'sa.meta1': 'Stop any time',
    'sa.meta2': 'Your answers are kept',
    'sa.meta3': 'Start with the short one',
    'sa.startShort': 'Short version · 90 items',
    'sa.startFull': 'Full version · 127 items',
    'sa.whatMark': 'Where answers go',
    'sa.whatTitle': 'It will not give you a score',
    'sa.whatNote': 'It only records how you say you work — kept apart from anything measured.',
    'sa.note1Title': 'What it changes',
    'sa.note1Body': 'How advice is presented to you: examples first or structure first, materials or none, how finely steps are broken down.',
    'sa.note2Title': 'What it does not change',
    'sa.note2Body': 'It will not limit what you can do, and it is not treated as a verdict on ability. Change it whenever; the newest answer wins.',
    'sa.note3Title': 'When it updates',
    'sa.note3Body': 'You do not need to redo it. Each archived session review and each side-note conversation brings something new.',
    'set.generalTitle': 'Language & sending', 'set.generalIntro': 'Interface and input only.',
    'set.langTitle': 'Language', 'set.langDesc': 'Interface only; content unchanged.',
    'set.busyTitle': 'Sending while busy', 'set.busyDesc': 'A message sent while a reply is running.',
    'set.queue': 'Queue', 'set.queueDesc': 'Send after the current reply.',
    'set.interrupt': 'Interrupt & send', 'set.interruptDesc': 'Stop and send immediately.',
    'set.block': 'Hold', 'set.blockDesc': 'Disabled until this reply ends.',
    'set.thinkTitle': 'Reasoning', 'set.thinkDesc': 'Expanded by default.', 'set.fold': 'Collapsed', 'set.open': 'Always',
    'set.citeTitle': 'Citations', 'set.citeDesc': 'Mark citations with a dot.',
    'set.modelsTitle': 'Model settings', 'set.modelsIntro': 'Choose the model used next.',
    'set.addProfile': 'Add model profile', 'set.addProfileNote': 'Ollama or OpenAI-compatible',
    'set.keyNote': 'Keys stay on this machine and are never echoed. Probing makes one real request.',
    'set.appearanceTitle': 'Paper & type', 'set.appearanceIntro': 'Colors only; layout unchanged.',
    'set.paper': 'Paper', 'set.paperDesc': 'Warm white · ink · vermilion',
    'set.ink': 'Ink', 'set.inkDesc': 'Dark ink · cream · muted vermilion',
    'set.scaleTitle': 'Body size', 'set.scaleDesc': 'Body text only.', 'set.small': 'Small', 'set.normal': 'Normal', 'set.large': 'Large',
    'set.motionTitle': 'Motion', 'set.motionDesc': 'Controls reasoning animation.', 'set.full': 'Full', 'set.restrained': 'Restrained',
    'sa.entryKicker': 'Self-assessment · once',
    'sa.entryTitle': 'Start with how you actually work',
    'sa.entryNote': '90 or 127 statements, depending on how much time you want to spend. No materials, no right answers; it produces stated-channel readings.',
    'sa.start': 'Start self-assessment',
    'sa.viewResult': 'Last result',
    'cog.kicker': 'Cognition',
    'cog.heroMark': 'Cognition · 01',
    'cog.kickerNote': 'COGNITION · ACROSS SESSIONS',
    'cog.title': 'Your cognition',
    'cog.titleEm': 'overview',
    'cog.intro': 'Preferences, baselines, evidence and hypotheses form your global cognition. They persist across sessions and can be revised at any time.',
    'cog.openSideNote': 'Open margin notes',
    'cog.refresh': 'Refresh',
    'cog.note': 'Archiving triggers a review; proposals require your confirmation.',
    'cog.baseline': 'M1 Baseline',
    'cog.baselineNote': 'One variable per row; the three columns are stated, experienced, and measured.',
    'cog.parallel': 'Preferences · Evidence · Hypotheses',
    'cog.parallelNote': 'Preferences: norms you set. Evidence: facts verified in practice. Hypotheses: unverified conjectures.',
    'cog.prefs': 'Preferences',
    'cog.evidence': 'Evidence',
    'cog.hypotheses': 'Hypotheses',
    'cog.hard': 'Hard',
    'cog.soft': 'Soft',
    'cog.stated': 'Stated',
    'cog.experienced': 'Experienced',
    'cog.measured': 'Measured',
    'cog.variable': 'Variable',
    'cog.hardCount': 'Hard constraints',
    'cog.softCount': 'Soft preferences',
    'cog.measuredCount': 'Measured channels',
    'cog.evidenceCount': 'Evidence',
    'cog.hypothesisCount': 'Hypotheses',
    'cog.priority': 'priority',
    'cog.unverified': 'unverified',
    'rail.chat': 'Chat',
    'rail.cognition': 'Cognition',
    'rail.benchmark': 'Self-check',
    'rail.settings': 'Settings',
    'rail.modelNote': 'Local Ollama · 32k context',
    'rail.colophon': 'A persistent learning flow · every line traceable',
    'common.backToChat': 'Back to chat', 'set.kickerNote': 'SETTINGS · model & preferences', 'cog.chat': 'Discuss in margin notes', 'set.mark1': '01', 'set.mark2': '02', 'set.mark3': '03',
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
    'composer.hint': 'State your foundation, purpose, and obstacles; I will ask if anything is unclear.',
    'composer.busyPrefix': 'While busy: ',
    'settings.kicker': 'Settings',
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
const L = (zh, en) => (state.lang === 'en' ? en : zh);

/* ── data ─────────────────────────────────────────────────────────── */
// 模型档案**不再写死在界面里**（原来是四份手抄的假档案）。
// 真的那份在 `GET /api/llm/profiles`，由 `loadModels()` 拉进 `state.profiles`。

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
    failed: '',
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
  const [payload, meter] = await Promise.all([
    api.state(sessionId),
    // 计量与会话数据分开读：窗口上限来自**此刻生效的模型**，
    // 换模型之后这一栏要能单独刷新，而 `/state` 不该因此改变形状。
    api.contextMeter(sessionId).catch(() => null),
  ]);
  const previous = state.details[sessionId];
  const detail = adaptDetail(payload);
  // 会话状态可能被模型工具改过（例如“写入计划 → 自动执行中”）。
  // 侧栏那一行必须跟上，否则顶栏/列表还显示 draft。
  const row = findSessionRow(sessionId);
  if (row && payload?.session) {
    row.state = payload.session.state;
    row.dot = dotClass(payload.session.state);
  }
  if (meter) detail.context = adaptContext(meter);

  // 拿不到计量时，保留上一次**真的测到过**的那份，并写明它是哪一轮的 ——
  // 不把它抹成"没测到"，也不凭空补一个好看的数字。
  const measuredNow = detail.context && detail.context.measured;
  if (!measuredNow && previous && previous.context && previous.context.measured) {
    detail.context = {
      ...previous.context,
      note: '这一份是上一次推演结束时的数字；这一轮还没跑，所以不是最新的。',
    };
  }

  state.details[sessionId] = detail;
  if (render && state.currentSession === sessionId) {
    renderSession();
    // 打开会话应落在最新一条，而不是开头。
    requestAnimationFrame(() => jumpThreadBottom());
  }
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
  //: 生效中的档案 id（来自 `is_active`），没有档案时为 `null`。
  activeProfile: null,
  //: 真档案（`/api/llm/profiles` → `mapProfile`）。
  profiles: [],
  //: 服务商目录（`/api/llm/providers`），新建档案时的下拉框。
  providers: [],
  //: `GET /api/llm/config`：此刻实际用的是 profile 还是 `.env`。
  llmConfig: null,
  //: 探活结果按档案记（`probe.detail` 原话），不编造延迟。
  probeText: {},
  currentSession: null,
  currentFolder: null,
  //: 点开计划项后，这一轮讨论的锚点（只由用户点击产生）。
  anchorStep: null,
  marginTab: 'plan',
  inlineKind: null,
  inlineFolderId: null,
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
  //: 侧记（常驻通用模型）的会话状态。它**不属于任何会话**。
  sidebar: {
    loaded: false,
    loading: false,
    busy: false,
    messages: [],
    context: null,
    tools: [],
    sections: [],
    error: '',
  },
  //: 每个会话的资料清单（`GET /sessions/{id}/attachments`）。
  attachments: {},
  //: 每个档案拉到的候选模型（`POST /profiles/{id}/models`）。
  listedModels: {},
  //: 全局认知页的数据（按需读，读完缓存）。
  cognition: { loaded: false, loading: false, data: null, error: '' },
  //: 归档复盘弹出的待确认提案。
  proposalQueue: [],
  //: 自评（一次性单例）的入口状态。
  selfAssessment: { latest: null, loading: false, scales: {} },
  //: 侧页这次收起是不是因为视口变窄（而不是用户自己关的）。
  marginForced: false,
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
const threadIndexEl = $('#thread-index');
const threadIndexList = $('#thread-index-list');
const threadIndex = new ThreadIndex($('#thread-index-canvas'), {
  onSelect: (item) => {
    jumpToMessage(item.id);
    // 选完就收起：面板是"找一句话"的工具，不是常驻抽屉。
    threadIndexEl?.classList.remove('is-open');
  },
  onHover: (index) => {
    $$('.ti-item', threadIndexList).forEach((el, i) => el.classList.toggle('is-hover', i === index));
  },
  onToggle: () => {
    // 触屏与键盘没有 hover：点一下 canvas 空白处就把面板显式打开/收起。
    threadIndexEl?.classList.toggle('is-open');
  },
});

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
function activeProfile() {
  return state.profiles.find((p) => p.id === state.activeProfile) || null;
}


/* ── 英文界面的兜底翻译 ────────────────────────────────────────────
 *
 * `data-i18n` 只覆盖静态骨架；侧页、计划、空态、运行卡是**动态生成**的。
 * 这里对已知的界面短语做一次替换，并跳过用户/模型内容（消息正文、标题、
 * 表单里的值）。用户的话、session 名、模型名永远保持原文。
 */
const EN_UI = {
  '动机': 'Motivation',
  '计划': 'Plan',
  '上下文': 'Context',
  '未建立': 'Not set',
  '可随时改': 'Editable',
  '编辑': 'Edit',
  '填写': 'Fill in',
  '最近改动': 'Last change',
  '前置基础': 'Foundation',
  '目的': 'Purpose',
  '障碍': 'Obstacles',
  '填写基础、目的与障碍；它们参与计划取舍。': 'Fill in foundation, purpose and obstacles; they inform plan trade-offs.',
  '计划未建立。改动先以草案呈现。': 'No plan yet. Changes are shown as drafts first.',
  '生成计划草案': 'Generate plan draft',
  '无步骤': 'No steps',
  '检查点': 'Checkpoint',
  '通过标准': 'Pass criteria',
  '检查点未建立。': 'No checkpoint yet.',
  '进入复盘': 'Enter review',
  '回到执行': 'Back to active',
  '重新打开': 'Reopen',
  '下一步': 'Next step',
  '归档': 'Archive',
  '已归档': 'Archived',
  '执行中': 'Active',
  '未开始': 'Draft',
  '复盘中': 'In review',
  '已放弃': 'Abandoned',
  '已完成': 'Done',
  '进行中': 'In progress',
  '待开始': 'Pending',
  '已跳过': 'Skipped',
  '为什么这样建议': 'Why this suggestion',
  '标注一句': 'Annotate',
  '用量': 'Usage',
  '参与上下文的原文': 'Live messages',
  '已压成摘要': 'Compacted',
  '当前摘要段': 'Summary segments',
  '累计压缩': 'Total compactions',
  '对话索引': 'Conversation index',
  '按你说过的话': 'By your messages',
  '侧记': 'Margin notes',
  '侧记不绑定会话。可讨论方法，或提议修改全局认知；写入需确认。': 'Margin notes are session-independent. Discuss methods or propose global changes; writes need confirmation.',
  '从左边挑一个会话，或者新建一个。': 'Pick a session on the left, or start a new one.',
  '还没有会话': 'No sessions yet',
  '先在左侧建一个领域，再往里加会话。': 'Create a field on the left, then add sessions to it.',
  '还没有经验证据。': 'No evidence yet.',
  '没有待确认的提案。': 'No pending proposals.',
  '本次': 'This run',
  '上一次': 'Last run',
  '状态': 'Status',
  '已中断': 'Interrupted',
  '继续上次': 'Resume',
  '重新开始': 'Restart',
  '保存并退出': 'Save & exit',
  '勾选后写入。': 'Checked items will be written.',
  '已用': 'Used',
  '剩余': 'Remaining',
  '预算': 'Budget',
  '窗口': 'Window',
  '正常': 'Normal',
  '接近上限': 'Near limit',
  '已超线': 'Over limit',
  '服务商': 'Provider',
  '模型名': 'Model name',
  '档案名': 'Profile name',
  '端点': 'Endpoint',
  '上下文预算（留空用模型默认）': 'Context budget (blank = model default)',
  '密钥': 'API key',
  '清掉已存的密钥': 'Clear stored key',
  '保存': 'Save',
  '取消': 'Cancel',
  '这份档案里的模型（可多选）': 'Models in this profile (multi-select)',
  '拉取清单': 'Fetch list',
  '保存并拉取清单': 'Save & fetch list',
  '编辑模型档案': 'Edit model profile',
  '新增模型档案': 'New model profile',
  '当前': 'Current',
  '探活': 'Probe',
  '清单': 'Models',
  '删除': 'Delete',
  '已设置 · 留空表示不改': 'Set · leave blank to keep',
  '需要密钥的服务商才填': 'Only needed by providers that require a key',
  '未探活': 'Not probed',
  '正在使用 · .env 默认值': 'In use · .env default',
  '未配置档案': 'No profile configured',
  '已设置': 'Set',
  '未设置': 'Not set',
  '这一页还是空的': 'This page is still blank',
  '写明基础、目的与障碍；计划会先以草案呈现。': 'State foundation, purpose and obstacles. Plans appear as drafts first.',
  '还没有记录': 'Nothing recorded yet',
  '这个领域里还没有会话。先从一句你想弄清的事开始吧。': 'No sessions in this field yet. Start with something you want to figure out.',
  '在这个领域新建会话': 'New session in this field',
  '新建会话': 'New session',
  '还没有模型档案。现在用的是 .env 里配的那个。': 'No model profiles yet — using the one from .env.',
  '不绑定会话 · 提议需确认': 'Session-independent · proposals need confirmation',
  '独立记忆 · 只提议': 'Separate memory · proposals only',
  '侧记不绑定会话。可讨论方法，或提议修改全局认知；写入需确认。': 'Margin notes are session-independent. Discuss methods or propose global changes; writes need confirmation.',
  '已发出': 'Sent',
  '按配置预算': 'of configured budget',
  '条': 'items',
  '侧记自己的上下文': 'Margin notes context',
  '甲': 'A', '乙': 'B', '留白': 'Blank', '量': 'M1', '知': 'COG',
  'M1 · 自述轮优先': 'M1 · stated first',
  '一次任务观测四项变量；置信度上限': 'One task observes four variables; confidence capped at',
  '独立任务与真实闭环；可完整测量': 'Independent task and a real loop; fully measures',
  '通道。': 'channel.',
  '回答': 'Reply',
  '正在使用 · .env 默认值': 'In use · .env default',
  '和侧记聊聊': 'Discuss in margin notes',
  '返回对话': 'Back to chat',
  'GLOBAL · 认知': 'GLOBAL · cognition',
  '昨天': 'Yesterday',
  '读会话': 'Read session',
  '改计划': 'Update plan',
  '被拦下': 'Blocked',
  '已完成': 'Done',
  '英语 · 考研词汇': 'English · exam vocabulary',
  '认知科学 · 记忆': 'Cognitive science · memory',
  '写作 · 论证': 'Writing · argumentation',
  '统计学': 'Statistics',
  '编程 · 算法': 'Programming · algorithms',
  '日语 · N2': 'Japanese · N2',
  '哲学 · 认识论': 'Philosophy · epistemology',
  '音乐 · 和声学': 'Music · harmony',


  '一次任务观测四项变量；置信度上限 provisional。': 'One task observes four variables; confidence is capped at provisional.',
  '独立任务与真实闭环；可完整测量 measured 通道。': 'Independent task and a real loop; measured can be fully covered.',
  '自述轮 · 20 道以内': 'Stated round · up to 20 items',
  '复用行为任务 · 1 次': 'Reused behavioural task · once',
  '随时保存退出': 'Save and exit anytime',
  '自述轮 + 行为轮': 'Stated + behavioural rounds',
  '独立任务 · 互不污染': 'Independent tasks · no contamination',
  '产出可对账的读数': 'Auditable readings',
  '基准 · 其一': 'Benchmark · I',
  '读数 · 三通道': 'Readings · three channels',
  '领域 · 两段': 'Domains · two sections',
};

const EN_PATTERNS = [
  [/^计划 · 第 (\d+) 版$/, (m) => `Plan · v${m[1]}`],
  [/^第 (\d+) 步$/, (m) => `Step ${m[1]}`],
  [/^(\d+) \/ (\d+) 完成$/, (m) => `${m[1]} / ${m[2]} done`],
  [/^(\d+) 个工具动作$/, (m) => `${m[1]} tool actions`],
  [/^(\d+) 条$/, (m) => `${m[1]} items`],
  [/^(\d+) 段$/, (m) => `${m[1]} segments`],
  [/^(\d+) 次$/, (m) => `${m[1]} compactions`],
  [/^(\d+) 题 · 已答 (\d+)$/, (m) => `${m[1]} items · ${m[2]} answered`],
  [/^推演 (\d+) 步$/, (m) => `Reasoned ${m[1]} steps`],
];

const EN_SKIP = '.msg-prose, .sn-prose, .session-title, .folder-name, .mou-form, .mou-plan, .proposal-intent, .cog-col-list, .ti-text, .cog-bname';
const translatedText = new WeakMap();

function translateUi(root = document) {
  if (!root) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      if (node.parentElement && node.parentElement.closest(EN_SKIP)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    if (state.lang === 'en') {
      const key = node.nodeValue.trim();
      let en = EN_UI[key];
      if (!en) {
        const rule = EN_PATTERNS.find(([pattern]) => pattern.test(key));
        if (rule) {
          const match = key.match(rule[0]);
          if (match) en = rule[1](match);
        }
      }
      if (!en) continue;
      if (!translatedText.has(node)) translatedText.set(node, node.nodeValue);
      const lead = node.nodeValue.match(/^\s*/)[0];
      const trail = node.nodeValue.match(/\s*$/)[0];
      node.nodeValue = lead + en + trail;
    } else if (translatedText.has(node)) {
      node.nodeValue = translatedText.get(node);
      translatedText.delete(node);
    }
  }
}

/** 顶栏的侧页开关与 `is-margin-closed` 保持同一状态。 */
function syncMarginToggle() {
  const toggle = $('#margin-toggle');
  if (!toggle) return;
  const isClosed = workspace.classList.contains('is-margin-closed');
  toggle.classList.toggle('is-on', !isClosed);
  toggle.setAttribute('aria-pressed', String(!isClosed));
  syncComposerAxis();
}

/**
 * 把"侧页当前占多宽"交给输入区。
 *
 * `.composer` 横跨整个 `.stage`，而正文所在的 `.thread-wrap` 只在 workspace
 * 的第一列 —— 侧页那一列不在里面。输入区要让出这段宽度，才能和正文同轴
 * （1440 + 侧页展开时，不让出会差 116px）。
 *
 * 只有两种取值：侧页可见 = 它占的列宽；收起或覆盖模式 = 0
 * （覆盖模式下侧页浮在内容之上，不占列宽）。
 */
function syncComposerAxis() {
  const stage = $('#stage');
  if (!stage) return;
  const overlay = workspace.classList.contains('is-margin-overlay');
  const closed = workspace.classList.contains('is-margin-closed');
  if (overlay || closed) {
    stage.style.setProperty('--margin-current', '0px');
    return;
  }
  const width = parseFloat(
    getComputedStyle(document.documentElement).getPropertyValue('--margin-w'),
  ) || 348;
  const capped = Math.min(width, window.innerWidth * 0.42);
  stage.style.setProperty('--margin-current', `${Math.round(capped)}px`);
}



/* ── 断点 ────────────────────────────────────────────────────────────
 * 以前"窄屏收起侧页"是在 `boot()` 与 `selectSession()` 里各写一次的
 * `innerWidth <= 1180` —— 那是个**一次性副作用**：从 1024 拉宽到 1440 时
 * 没有任何代码再把侧页放回来（实测 `stillClosedAt1440: true`）。
 *
 * 现在只有一个判据 `applyBreakpoint()`，由 resize（rAF 合并）与启动共同调用。
 * 关键区分：**用户手动的开/关**与**视口强制的收起**是两件事。
 * - `state.marginForced` 记录"这次收起是视口干的，不是用户";
 * - 视口回到宽屏时，只有 `marginForced` 才恢复 —— 用户自己关掉的不会被擅自打开。
 */
const NARROW_MARGIN = 1280;
const NARROW_RAIL = 900;

function isNarrowMargin() { return window.innerWidth <= NARROW_MARGIN; }

function applyBreakpoint() {
  const narrow = isNarrowMargin();
  workspace.classList.toggle('is-margin-overlay', narrow);

  if (narrow) {
    if (!workspace.classList.contains('is-margin-closed')) {
      // 视口刚刚变窄：收起，并记下"这是被收起的"
      workspace.classList.add('is-margin-closed');
      state.marginForced = true;
    }
  } else if (state.marginForced) {
    // 回到宽屏：只恢复"被视口收起的"那一类
    workspace.classList.remove('is-margin-closed');
    state.marginForced = false;
  }

  syncMarginScrim();
  syncMarginToggle();
}

/** 抽屉打开时铺一层薄纱；点它即收起（覆盖模式下没有别的遮蔽物）。 */
function syncMarginScrim() {
  const scrim = $('#margin-scrim');
  if (!scrim) return;
  const overlayOpen = workspace.classList.contains('is-margin-overlay')
    && !workspace.classList.contains('is-margin-closed');
  scrim.hidden = !overlayOpen;
}

let breakpointFrame = 0;
function scheduleBreakpoint() {
  if (breakpointFrame) return;
  breakpointFrame = requestAnimationFrame(() => {
    breakpointFrame = 0;
    applyBreakpoint();
  });
}

/**
 * 顶栏/侧栏该显示哪个模型名。
 *
 * 没有档案时**不编一个名字**：这时真正在说话的是 `.env` 里那一个
 * （`/api/llm/config` 的 `settings`），那就如实显示它。
 */
function currentModelLabel() {
  const prof = activeProfile();
  // 顶栏那一格回答的是「下一句话由谁来说」—— 那是**模型名**，不是档案名。
  // 档案名（"DeepSeek（验收）"）放在左侧索引里，两者分开才看得出切换生效。
  if (prof) return prof.model;
  const model = state.llmConfig?.settings?.llm_model;
  return model ? `${model} · 来自 .env` : '未配置模型';
}

/** 左侧索引里显示档案名（人给它起的名字），与顶栏的模型名分工不同。 */
function currentProfileLabel() {
  const prof = activeProfile();
  if (prof) return prof.label;
  const model = state.llmConfig?.settings?.llm_model;
  return model ? `${model} · .env` : '未配置模型';
}

/** 后端的一行档案 → 界面用的视图模型（字段名照抄，方便对照接口）。 */
function mapProfile(row) {
  const meta = state.providers.find((x) => x.provider_id === row.provider_id);
  return {
    id: row.profile_id,
    label: row.label || row.profile_id,
    short: row.label || row.model,
    model: row.model,
    //: 这份档案里勾选过的模型（后端保证包含 `model`）。
    models: Array.isArray(row.models) && row.models.length ? row.models : [row.model],
    model2: row.model,
    providerId: row.provider_id,
    provider: meta ? meta.label : row.provider_id,
    kind: row.kind,
    base: row.base_url,
    numCtx: row.num_ctx,
    effectiveCtx: row.effective_num_ctx || row.num_ctx,
    ctx: row.effective_num_ctx ? String(row.effective_num_ctx) : (row.num_ctx ? String(row.num_ctx) : '—'),
    //: 三态：`auto` = 不发 think 字段（对不认识的模型更安全）。
    think: row.think === 'true' ? 'true' : row.think === 'false' ? 'false' : 'auto',
    note: row.note || '',
    active: !!row.is_active,
    hasKey: !!row.has_key,
    masked: row.has_key ? (row.key_masked || '已设置') : (meta?.needs_key ? '未设置' : '无需密钥'),
    mark: row.kind === 'local' ? '本' : '外',
  };
}

/**
 * 把一次写入的**响应行**并回界面。
 *
 * ★ 为什么不写完就立刻 `GET /profiles`：后端的提交发生在响应发出**之后**
 * （每请求一个事务，`session_scope` 在依赖退出时 commit）。立刻重读有可能
 * 读到上一版 —— 表现就是"点了启用，顶栏还是旧模型"：一次看起来没生效的点击。
 * 响应里的那一行就是写入结果，用它更新界面是确定的。
 */
function applyProfileRow(row) {
  if (!row) return null;
  const mapped = mapProfile(row);
  const index = state.profiles.findIndex((p) => p.id === mapped.id);
  if (index >= 0) state.profiles[index] = mapped;
  else state.profiles.push(mapped);
  if (mapped.active) {
    for (const p of state.profiles) if (p.id !== mapped.id) p.active = false;
    mapped.active = true;
    state.activeProfile = mapped.id;
    if (state.llmConfig) {
      state.llmConfig = { ...state.llmConfig, source: 'profile', profile: row };
    }
  }
  return mapped;
}

/**
 * 从后端读模型档案。**这是这一页唯一的数据来源。**
 *
 * 三件事一起读，因为它们互相解释：`profiles`（有哪些）、
 * `providers`（能选哪些服务商）、`config`（此刻实际在用哪一份）。
 * 只读 profiles 的话，"我配了怎么好像没生效"就没有答案来源。
 */
async function loadModels({ render = true } = {}) {
  try {
    const [profiles, config, providers] = await Promise.all([
      api.llmProfiles(),
      api.llmConfig(),
      api.llmProviders(),
    ]);
    state.providers = providers.items || [];
    state.llmConfig = config;
    state.profiles = (profiles.items || []).map(mapProfile);
    const active = state.profiles.find((p) => p.active);
    state.activeProfile = active ? active.id : null;
  } catch (err) {
    state.profiles = [];
    state.activeProfile = null;
    toast(`模型档案读不到：${err.message}`, 'danger');
  }
  if (render) {
    renderSettings();
    renderTopbar();
    renderRail();
  }
}

/**
 * 打开"新增/编辑一份档案"的表单。
 *
 * ★ 保存时**发整份**而不是只发改动的那几个字段：后端 `update_profile`
 * 是整份覆盖写（`note`/`num_ctx`/`base_url` 都会被赋成你给的值），
 * 只发一部分就会把它们悄悄抹掉 —— 那正是"我什么都没动，配置却变了"。
 */
function openProfileForm(existing, anchor = null) {
  if (!state.providers.length) {
    toast('服务商目录未加载', 'danger');
    return;
  }
  const p = existing || null;
  state.profileFormAnchor = anchor || $('#profile-list') || $('[data-action="add-profile"]') || document.body;
  const options = state.providers
    .map(
      (x) =>
        `<option value="${x.provider_id}"${p && p.providerId === x.provider_id ? ' selected' : ''}>${escapeHtml(x.label)}</option>`,
    )
    .join('');
  const listed = p ? (state.listedModels[p.id] || []) : [];
  const checked = p ? p.models : [];
  const candidates = Array.from(new Set([...checked, ...listed]));
  const checks = candidates.length
    ? `<div class="pf-model-checks">${candidates.map((m) => `
        <label class="pf-model-check">
          <input type="checkbox" value="${escapeHtml(m)}"${checked.includes(m) ? ' checked' : ''}>
          <span>${escapeHtml(m)}</span>
          ${p && m === p.model ? '<em>正在用</em>' : ''}
        </label>`).join('')}</div>`
    : `<p class="pf-hint">还没有候选清单。先填好端点与密钥，点「保存并拉取清单」——
        它会把这家服务列出的模型拿回来，你再勾要留下的那些。</p>`;
  const pop = openPopover(
    state.profileFormAnchor,
    `<div class="popover-label">${p ? '编辑模型档案' : '新增模型档案'}</div>
     <form class="profile-form">
       <label>服务商<select class="pf-provider">${options}</select></label>
       <label>模型名<input class="pf-model" value="${escapeHtml(p ? p.model : '')}" placeholder="例如 qwen3.6:27b"></label>
       <label>档案名<input class="pf-label" value="${escapeHtml(p ? p.label : '')}" placeholder="不填就用模型名"></label>
       <label>端点<input class="pf-base" value="${escapeHtml(p ? p.base : '')}" placeholder="留空用服务商默认"></label>
       <label>上下文预算（留空用模型默认）<input class="pf-ctx" type="number" min="0" step="512" value="${p && p.numCtx ? p.numCtx : ''}" placeholder="${p ? `模型默认 ${p.effectiveCtx}` : '留空 = 模型默认'}"></label>
       <label>密钥<input class="pf-key" type="password" autocomplete="off" placeholder="${p && p.hasKey ? '已设置 · 留空表示不改' : '需要密钥的服务商才填'}" /></label>
       ${p && p.hasKey ? '<label class="pf-row"><input type="checkbox" class="pf-clear-key"> 清掉已存的密钥</label>' : ''}
       <div class="pf-models">
         <div class="pf-models-head">
           <span>这份档案里的模型（可多选）</span>
           <button type="button" class="mini-btn" data-action="fetch-profile-models" data-id="${p ? p.id : ''}">
             ${p ? '拉取清单' : '保存并拉取清单'}
           </button>
         </div>
         ${checks}
       </div>
       <div class="inline-actions">
         <button type="submit" class="mini-btn solid">保存</button>
         <button type="button" class="mini-btn" data-action="close-popover">取消</button>
       </div>
     </form>`,
    'popover-form is-centered',
    { center: true },
  );
  pop.querySelector('form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const id = await saveProfileForm(pop, p);
      closePopover();
      renderSettings();
      renderTopbar();
      renderRail();
      updateBusyUI();
      toast(p ? '已保存' : '已创建');
    } catch (err) {
      toast(err.message, 'danger');
    }
  });
}

/** 从表单里收一份**完整**的档案字段（后端 update 是覆盖写，只发一部分会抹掉其余）。 */
function collectProfileBody(pop) {
  const providerId = pop.querySelector('.pf-provider').value;
  const meta = state.providers.find((x) => x.provider_id === providerId);
  const typedModel = pop.querySelector('.pf-model').value.trim();
  const checked = $$('.pf-model-check input:checked', pop).map((el) => el.value);
  const model = typedModel || checked[0] || meta?.default_model || '';
  const models = checked.length ? checked : (model ? [model] : []);
  const body = {
    label: pop.querySelector('.pf-label').value.trim() || model,
    provider_id: providerId,
    model,
    models,
    base_url: pop.querySelector('.pf-base').value.trim() || null,
    num_ctx: Number(pop.querySelector('.pf-ctx').value) || null,
    note: '',
  };
  const keyInput = pop.querySelector('.pf-key');
  const clearKey = pop.querySelector('.pf-clear-key');
  if (clearKey && clearKey.checked) body.api_key = '';
  else if (keyInput.value) body.api_key = keyInput.value;
  return body;
}

async function saveProfileForm(pop, existing) {
  const body = collectProfileBody(pop);
  if (existing) {
    body.note = existing.note;
    const row = await api.llmUpdateProfile(existing.id, body);
    applyProfileRow(row);
    return existing.id;
  }
  const created = await api.llmCreateProfile(body);
  applyProfileRow(created);
  return created.profile_id;
}

/**
 * 「保存并拉取清单」：先把端点/密钥存下来，再去问这家服务有哪些模型。
 *
 * 顺序不能反：列模型要用到刚填的密钥与地址，而它们只在保存之后才落到后端。
 * 拉不到也**不算失败**（后端会给人话解释），这时只提示可以手动填模型名。
 */
async function fetchProfileModels(button) {
  const pop = button.closest('.popover');
  if (!pop) return;
  const existingId = button.dataset.id || '';
  const existing = state.profiles.find((x) => x.id === existingId) || null;
  button.disabled = true;
  try {
    const id = await saveProfileForm(pop, existing);
    const got = await api.llmListModels(id);
    state.listedModels[id] = got.models || [];
    closePopover();
    const fresh = state.profiles.find((x) => x.id === id) || null;
    openProfileForm(fresh, state.profileFormAnchor);
    if (got.models && got.models.length) {
      toast('已拉取模型清单');
    } else {
      toast(got.note || '无法获取模型清单', 'danger');
    }
  } catch (err) {
    toast(err.message, 'danger');
  } finally {
    button.disabled = false;
  }
}
function openMotivationForm(anchor) {
  const detail = activeDetail();
  if (!detail) return;
  const m = detail.motivation || { foundation: '', value: '', obstacles: [] };
  const pop = openPopover(
    anchor || $('.mpanel') || document.body,
    `<div class="popover-label">前置基础 · 目的 · 障碍</div>
     <form class="profile-form motivation-form">
       <label>前置基础
         <textarea class="pf-foundation" rows="3" placeholder="已经会什么、缺什么、卡在哪一步">${escapeHtml(m.foundation || '')}</textarea>
       </label>
       <label>目的
         <textarea class="pf-value" rows="3" placeholder="学会之后会改变什么">${escapeHtml(m.value || '')}</textarea>
       </label>
       <label>障碍 · 一行一条
         <textarea class="pf-obstacles" rows="3" placeholder="时间碎片化&#10;晚上开始不了">${escapeHtml((m.obstacles || []).join('\n'))}</textarea>
       </label>
       <div class="inline-actions">
         <button type="submit" class="mini-btn solid">保存</button>
         <button type="button" class="mini-btn" data-action="close-popover">取消</button>
       </div>
     </form>`,
    'popover-form',
  );
  pop.querySelector('form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const obstacles = pop.querySelector('.pf-obstacles').value
      .split('\n').map((line) => line.trim()).filter(Boolean);
    const version = activeDetail()?.versions?.session
      ?? activeDetail()?.session?.version
      ?? 1;
    try {
      await api.patchMotivation(state.currentSession, {
        expected_version: version,
        change: '手动编辑动机：前置基础 / 目的 / 障碍',
        foundation: pop.querySelector('.pf-foundation').value.trim(),
        value: pop.querySelector('.pf-value').value.trim(),
        obstacles,
        basis: 'stated',
      });
      closePopover();
      await loadDetail(state.currentSession);
      toast('已保存');
    } catch (err) {
      toast(err.message, 'danger');
    }
  });
}

function stateLabel(key) { return t('state.' + key); }
function statusLabel(status) {
  return ({ done: '已完成', doing: '进行中', pending: '待开始', skipped: '已跳过' })[status] || status;
}

function toast(message, kind = '') {
  const el = document.createElement('div');
  el.className = 'toast' + (kind === 'danger' ? ' is-danger' : '');
  const text = String(message ?? '').replace(/\s+/g, ' ').trim();
  el.textContent = text.length > 140 ? `${text.slice(0, 140)}…` : text;
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

function folderMarkup(folder) {
  let sessions = folder.sessions.filter((s) => matchesSearch(s.title));
  const folderMatches = matchesSearch(folder.name);
  if (!folderMatches && sessions.length === 0) return '';
  if (!folderMatches) {
    // keep only matching sessions
  } else {
    sessions = folder.sessions;
  }
  const open = folder.open || !!state.search;
  const current = folder.id === state.currentFolder;
  return `<div class="folder${open ? ' is-open' : ''}${current ? ' is-current' : ''}" data-folder-id="${folder.id}">
    <div class="folder-head" data-action="toggle-folder" data-id="${folder.id}" role="button" tabindex="0"
      aria-expanded="${open}">
      <span class="folio">${icon(open ? 'folder-open' : 'folder')}</span>
      <span class="folder-name">${escapeHtml(folder.name)}</span>
      <span class="folder-count">${folder.sessions.length}</span>
      <button class="folder-add-btn" data-action="new-session-in-folder" data-id="${folder.id}" aria-label="在这个领域新建会话">${icon('plus')}</button>
      <svg class="chev"><use href="#i-chev"/></svg>
      <button class="folder-menu-btn" data-action="folder-menu" data-id="${folder.id}" aria-label="领域操作">${icon('dots')}</button>
    </div>
    <ul class="sessions">${sessions.map((s) => sessionMarkup(s, folder.id)).join('')}</ul>
  </div>`;
}

function renderRail() {
  const visibleFolders = state.folders;
  folderList.innerHTML = visibleFolders.map((f) => folderMarkup(f)).join('')
    || '<div class="archive-empty">还没有领域。点上面「新领域」建第一个。</div>';
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
  $('#rail-model-name').textContent = currentProfileLabel();
  $('#rail-model .foot-model-text em').textContent = prof
    ? `${prof.model} · ${prof.provider} · ${L('上下文', 'context')} ${prof.ctx}`
    : (state.llmConfig?.settings ? '设置 → 模型：加一份档案就能换' : '读不到模型配置');

  translateUi();
}

/* ── topbar / session ─────────────────────────────────────────────── */
function renderTopbar() {
  const session = currentSession();
  // 没有会话但选中了领域时，面包屑仍然显示那个领域 —— 否则会出现
  // “刚点了领域，上面却写着未选择领域”。
  const folder = session ? findFolder(session.folderId) : findFolder(state.currentFolder);
  $('#crumb .crumb-folder').textContent = folder ? folder.name : '还没选领域';
  $('#crumb .crumb-session').textContent = session ? session.title : '还没选会话';
  const pill = $('#session-state');
  const stateKey = session ? session.state : 'draft';
  pill.className = 'state-pill is-' + stateKey;
  pill.querySelector('span').textContent = stateLabel(stateKey);

  $('#model-chip-name').textContent = currentModelLabel();
  const prof = activeProfile();
  $('#composer-model-note').textContent = prof ? prof.model : (state.llmConfig?.settings?.llm_model || '未配置模型');
  $('#composer-busy-rule').textContent = t('composer.busyPrefix') + t('busy.' + state.busyBehavior);

  const brandTitle = state.lang === 'en' ? 'StudyFlow Archive' : '学习流档案';
  document.title = session ? `${session.title} · ${brandTitle}` : brandTitle;
}

function renderThread({ animate = true } = {}) {
  const detail = activeDetail();
  const session = currentSession();
  if (!session || !detail) {
    thread.innerHTML = '';
    threadEnd.hidden = true;
    return;
  }
  // 数据还没到 ≠ 没有数据。`makeDetail()` 先塞一份空的给点击用，
  // 那份的 `loaded` 是 false —— 直接画空态会让"正在读"看起来像"确实没聊过"。
  if (!detail.loaded) {
    thread.innerHTML = threadSkeletonMarkup();
    threadEnd.hidden = true;
    threadIndexEl.hidden = true;
    return;
  }
  if (detail.failed) {
    thread.innerHTML = emptyStateMarkup({
      icon: 'close',
      title: '这个会话读不到',
      body: detail.failed,
      note: '界面不摆假数据：读不到就是读不到。',
    });
    threadEnd.hidden = true;
    threadIndexEl.hidden = true;
    return;
  }
  if (detail.messages.length === 0) {
    thread.innerHTML = emptyThreadMarkup(session);
    threadEnd.hidden = true;
    renderThreadIndex();
    return;
  }
  threadEnd.hidden = false;
  const messages = detail.messages.map((m, i) => messageMarkup(m, i)).join('');
  thread.innerHTML = `${dateDivider(detail)}${messages}`;
  // 重绘的**原因**决定要不要重播"墨落"动画：
  // 切会话/首次进入该有；换语言、换主题只该换字，不该把整屏再演一遍。
  if (!animate) thread.classList.add('is-settled');
  else thread.classList.remove('is-settled');
  bindInkDots();
  renderThreadIndex();
  markInteractionStates(thread, '.msg-assistant', '.msg-user');
}

/**
 * 按**用户说过的话**重建索引轨。
 *
 * 位置用「这条消息在整段对话里的相对位置」而不是像素：窗口大小、字号、
 * 消息长短都会变，相对位置不会因为一次重排就全部漂掉。
 */
function renderThreadIndex() {
  if (!threadIndexEl) return;
  const marks = $$('.msg-user[data-msg-id]', thread);
  const total = Math.max(1, thread.scrollHeight);
  const items = marks.map((el, index) => {
    const prose = el.querySelector('.msg-prose');
    return {
      id: el.dataset.msgId,
      label: (prose?.textContent || '').replace(/\s+/g, ' ').trim(),
      fraction: Math.min(1, Math.max(0, el.offsetTop / total)),
      index,
    };
  });
  threadIndexEl.hidden = items.length === 0;
  if (threadIndexList) {
    threadIndexList.innerHTML = items.map((item, i) => `
      <button type="button" class="ti-item" data-thread-jump="${escapeHtml(item.id)}">
        <span class="ti-no">${String(i + 1).padStart(2, '0')}</span>
        <span class="ti-text">${escapeHtml(item.label.slice(0, 46) || '（这句话没有正文）')}</span>
      </button>`).join('');
  }
  threadIndex.update(items);
  updateThreadIndexViewport();
  const canvas = $('#thread-index-canvas');
  if (canvas) canvas.setAttribute('aria-label', `对话索引：${items.length} 条你说过的话`);
}

/** 视口指示 + 当前条：滚动时轻量更新（`scroll` 事件用 rAF 合并）。 */
let indexFrame = 0;
function updateThreadIndexViewport() {
  if (!threadWrap || !threadIndexEl) return;
  const total = Math.max(1, threadWrap.scrollHeight);
  const start = threadWrap.scrollTop / total;
  const end = (threadWrap.scrollTop + threadWrap.clientHeight) / total;
  threadIndex.setViewport(start, end);
  const marks = $$('.msg-user[data-msg-id]', thread);
  let active = -1;
  for (let i = 0; i < marks.length; i += 1) {
    if (marks[i].offsetTop <= threadWrap.scrollTop + 120) active = i;
  }
  threadIndex.setActive(active);
}

function scheduleThreadIndexViewport() {
  if (indexFrame) return;
  indexFrame = requestAnimationFrame(() => {
    indexFrame = 0;
    updateThreadIndexViewport();
  });
}

/** 窗口尺寸变了，每条消息的**相对位置**也跟着变：索引轨要按新高度重建。 */
let indexRebuildFrame = 0;
function scheduleThreadIndexRebuild() {
  if (indexRebuildFrame) return;
  indexRebuildFrame = requestAnimationFrame(() => {
    indexRebuildFrame = 0;
    renderThreadIndex();
  });
}

function jumpToMessage(msgId) {
  const node = thread.querySelector(`.msg[data-msg-id="${CSS.escape(String(msgId))}"]`);
  if (!node) return;
  const top = Math.max(0, node.offsetTop - 72);
  threadWrap.scrollTo({ top, behavior: 'smooth' });
  node.classList.remove('is-jump');
  // 强制重启动画：同一个节点连续点两次也要有反馈。
  void node.offsetWidth;
  node.classList.add('is-jump');
  setTimeout(() => node.classList.remove('is-jump'), 1400);
}

/** 卷首那条横线。日期来自这个会话的创建时间 —— 写死的话，第二天它就错了。 */
function dateDivider(detail) {
  const iso = detail.session?.created_at;
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const week = (state.lang === 'en'
    ? ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    : ['周日', '周一', '周二', '周三', '周四', '周五', '周六'])[d.getDay()];
  return `<div class="date-divider"><span>${d.getFullYear()} · ${pad(d.getMonth() + 1)} · ${pad(d.getDate())}</span><span>${week}</span></div>`;
}

/**
 * 会话正在读：画**稿纸骨架**，不画空态。
 *
 * 三件事必须同时成立，否则它在说谎：
 * 1. 形状像一段回答（抬头 + 几行正文），不是通用灰块；
 * 2. 有 `aria-busy` + 一句给读屏听的"正在读取"；
 * 3. 不出现任何示例提问 —— 那是"确实还没聊过"才该有的东西。
 */
/**
 * 主对话区的空态 —— 一个形状，六种来由。
 *
 * 原先六个调用点各写一遍（读不到 / 没聊过 / 画不出来 / 领域空 / 无会话 / 连不上），
 * 结构相同但措辞与动作散在各处，改一处样式要改六遍。
 * 现在统一成：**印章 + 一句结论 + 一句解释 + 一个可选的去处**。
 *
 * `tone` 只影响印章的图标语义（错误用叉、空用档案、目录用文件夹），
 * 不改变结构 —— 空态与错误态不该长得像两种组件。
 */
function emptyStateMarkup({ icon: iconName = 'archive-mark', title, body = '', note = '', action = null }) {
  const actionHtml = action
    ? `<div class="prompt-hints"><button ${action.action || ''}>${escapeHtml(action.label)}</button></div>`
    : '';
  const noteHtml = note ? `<p class="muted">${escapeHtml(note)}</p>` : '';
  return `<div class="empty-thread">
    <span class="empty-seal">${icon(iconName)}</span>
    <h2>${escapeHtml(title)}</h2>
    ${body ? `<p>${escapeHtml(body)}</p>` : ''}
    ${noteHtml}
    ${actionHtml}
  </div>`;
}

function threadSkeletonMarkup() {
  const line = (w) => `<i class="sk-line" style="--w:${w}%"></i>`;
  return `<div class="thread-skeleton" aria-busy="true">
    <span class="sr-only">正在读取这个会话…</span>
    ${[0, 1].map(() => `<div class="sk-msg">
      <div class="sk-gutter"><span class="sk-seal"></span><span class="sk-time"></span></div>
      <div class="sk-body">
        <span class="sk-role"></span>
        ${line(96)}${line(88)}${line(72)}
      </div>
    </div>`).join('')}
    <div class="sk-status"><span class="ink-loader"><i></i><i></i><i></i></span><span>正在读取会话</span></div>
  </div>`;
}

function emptyThreadMarkup(session) {
  return `<div class="empty-thread">
    <span class="empty-seal">${icon('archive-mark')}</span>
    <h2>这一页还是空的</h2>
    <p>说一句你现在卡在哪儿就行——基础、想达到什么、哪一步不顺。
      我会先给一个草案，你看着改。</p>
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
    return `<article class="msg msg-user" style="--i:${index}" data-msg-id="${escapeHtml(message.id || '')}">
      <div class="msg-gutter">
        <span class="msg-marker">${L('你', 'Y')}</span>
        <time>${escapeHtml(message.time || '')}</time>
      </div>
      <div class="msg-body">
        <div class="msg-prose"><p>${escapeHtml(message.text || '')}</p></div>
      </div>
    </article>`;
  }
  const proposal = message.proposal ? proposalMarkup(message.proposal) : '';
  const stop = message.stopReason || 'completed';
  const stopLabel = ({
    completed: '',
    llm_error: '模型错误',
    internal_error: '出错',
    max_steps: '步数用尽',
    client_disconnected: '中断了',
  })[stop] || '';
  const metaClass = stop === 'llm_error' || stop === 'internal_error'
    ? ' is-error'
    : stop === 'max_steps' ? ' is-warn' : '';
  const metaText = stopLabel
    || (message.traces ? `推演 ${message.traces.length} 步` : '回答');
  const foot = message.foot ? `<div class="msg-foot">
      <button data-action="show-basis" data-basis="${escapeHtml(message.basis || '这是基于本轮的工具轨迹与当时锚点给出的建议。')}">为什么这样建议</button>
      <span class="dot"></span>
      <button data-action="annotate">标注一句</button>
      <span class="dot"></span>
      <span>${message.traces ? message.traces.length : 0} 个工具动作</span>
    </div>` : '';
  return `<article class="msg msg-assistant" style="--i:${index}" data-msg-id="${message.id || ''}">
    <div class="msg-gutter">
      <span class="msg-marker is-mark">${icon('archive-mark')}</span>
      <time>${escapeHtml(message.time || '')}</time>
      ${traceMarkup(message.traces)}
    </div>
    <div class="msg-body">
      <div class="msg-head"><span class="msg-role"><em>${L('档', 'SF')}</em> · STUDYFLOW</span>${message.review ? '<span class="msg-review">归档复盘</span>' : ''}<span class="msg-meta${metaClass}">${metaText}</span></div>
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
    marginBody.innerHTML = '<div class="mpanel"><p class="mpanel-sub">从左边挑一个会话，这里就会显示它的动机、计划与上下文。</p></div>';
    renderContextRings();
    return;
  }
  if (state.marginTab === 'motivation') {
    marginBody.innerHTML = motivationMarkup(detail.motivation);
  } else if (state.marginTab === 'context') {
    marginBody.innerHTML = contextMarkup(detail.context);
  } else {
    marginBody.innerHTML = planMarkup(detail.plan);
  }
  renderContextRings();
  translateUi(marginBody);
}

function motivationMarkup(motivation) {
  if (!motivation) {
    return `<div class="mpanel">
      <div class="mpanel-head"><h3>动机</h3><span class="ver">未建立</span></div>
      <p class="mpanel-sub">填写基础、目的与障碍；它们参与计划取舍。</p>
      <div class="mpanel-actions"><button class="mini-btn" data-action="edit-motivation">填写</button></div>
    </div>`;
  }
  const foundation = motivation.foundation || '';
  const value = motivation.value || '';
  const obstacles = motivation.obstacles || [];
  return `<div class="mpanel">
    <div class="mpanel-head"><h3>动机</h3><span class="ver">可随时改</span></div>
    <div class="m-section">
      <div class="m-section-head"><span>前置基础</span></div>
      <p class="mot-text${foundation ? '' : ' is-empty'}">${escapeHtml(foundation || '—')}</p>
    </div>
    <div class="m-section">
      <div class="m-section-head"><span>目的</span></div>
      <p class="mot-text${value ? '' : ' is-empty'}">${escapeHtml(value || '—')}</p>
    </div>
    <div class="m-section">
      <div class="m-section-head"><span>障碍</span><em>${obstacles.length}</em></div>
      ${obstacles.length
        ? `<ul class="driver-list">${obstacles.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`
        : '<p class="mot-text is-empty">—</p>'}
    </div>
    <div class="revision-note"><b>最近改动</b><span>${escapeHtml(motivation.revision)}</span></div>
    <div class="mpanel-actions"><button class="mini-btn" data-action="edit-motivation">编辑</button></div>
  </div>`;
}

function planMarkup(plan) {
  if (!plan) {
    return `<div class="mpanel">
      <div class="mpanel-head"><h3>计划</h3><span class="ver">未建立</span></div>
      <p class="mpanel-sub">计划未建立。改动先以草案呈现。</p>
      <div class="mpanel-actions"><button class="mini-btn" data-action="ask-plan">生成计划草案</button></div>
    </div>`;
  }
  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  const done = steps.filter((s) => s.status === 'done').length;
  const pct = steps.length ? Math.round((done / steps.length) * 100) : 0;
  const version = plan.version ?? plan.currentVersion ?? '—';
  // 检查点是可空的：新会话、或有步骤但没有检查点的计划都会传 null。
  // 这里不能直接读 `plan.checkpoint.title` —— 那会让整个侧页抛错，
  // 而 renderSession 里 rail 排在 margin 后面时，左侧高亮就会停在旧会话。
  const checkpoint = plan.checkpoint || null;
  return `<div class="mpanel">
    <div class="mpanel-head"><h3>计划 · 第 ${escapeHtml(String(version))} 版</h3><span class="ver">${done} / ${steps.length} 完成</span></div>
    <div class="plan-progress">
      <div class="plan-progress-bar"><i style="--p:${pct}%"></i></div><span>${pct}%</span>
    </div>
    <ul class="steps">
      ${steps.length ? steps.map((step) => `<li class="step is-${step.status}${state.anchorStep === step.id ? ' is-anchor' : ''}" data-action="discuss-step" data-step-id="${step.id}" title="讨论此步骤">
        <span class="step-check" data-action="toggle-step" data-step-id="${step.id}" role="button" tabindex="0" aria-label="标记完成">${icon('check')}</span>
        <div class="step-body">
          <div class="step-action">${escapeHtml(step.action)}${state.anchorStep === step.id ? '<span class="anchor-tag">讨论锚点</span>' : ''}</div>
          <div class="step-meta"><span class="status-tag">${statusLabel(step.status)}</span><span>·</span><span>${escapeHtml(step.meta || '')}</span></div>
        </div>
      </li>`).join('') : '<li class="step is-empty"><div class="step-body"><div class="step-action">无步骤。</div></div></li>'}
    </ul>
    ${checkpoint ? `<div class="checkpoint">
      <div class="checkpoint-head"><span>${escapeHtml(checkpoint.title)}</span><span>通过标准</span></div>
      <p>${escapeHtml(checkpoint.text)}</p>
      <p class="cp-criteria"><b>${escapeHtml(checkpoint.criteria)}</b></p>
    </div>` : `<div class="checkpoint is-empty">
      <div class="checkpoint-head"><span>检查点</span><span>未建立</span></div>
      <p>检查点未建立。</p>
    </div>`}
  </div>`;
}

function contextMarkup(context) {
  const ctx = context || {
    measured: false, used: '—', total: '—', pct: 0, ratio: 0,
    pressure: 'unmeasured', limitSource: 'none', liveMessages: 0, compactedMessages: 0,
    summaries: 0, compactCount: 0, compactable: false,
    note: '',
  };
  const limitWord = ctx.limitSource === 'budget' ? '预算' : '窗口';
  const pressureText = {
    ok: '正常',
    near: '接近上限',
    over: '已超线',
    unmeasured: '—',
    unreported: '—',
  }[ctx.pressure] || ctx.pressure;
  const readingLabel = ctx.measured
    ? `${ctx.pct}%`
    : (ctx.hasReading ? `${ctx.used} tokens` : '—');
  return `<div class="mpanel">
    <div class="mpanel-head">
      <h3>上下文</h3>
      <div class="ctx-head-right">
        <span class="ver">${readingLabel}</span>
        <button class="icon-btn ctx-refresh" data-action="refresh-context" aria-label="刷新上下文">${icon('undo')}</button>
      </div>
    </div>
    <div class="ctx-ring-card">
      <div class="ctx-ring-wrap">
        <canvas class="ctx-ring" data-ctx-ring="margin" aria-label="上下文 ${readingLabel}"></canvas>
        <div class="ctx-ring-center">
          <strong>${ctx.measured ? ctx.pct + '%' : (ctx.hasReading ? ctx.usedShort : '—')}</strong>
          <em>${escapeHtml(pressureText)}</em>
        </div>
      </div>
      <div class="ctx-ring-facts">
        <div><span>已用</span><b>${escapeHtml(ctx.used)}</b></div>
        <div><span>${limitWord}</span><b>${escapeHtml(ctx.total)}</b></div>
        <div><span>剩余</span><b>${ctx.measured ? Number(ctx.remaining || 0).toLocaleString('zh-CN') : '—'}</b></div>
      </div>
    </div>
    <div class="m-section">
      <div class="m-section-head"><span>用量</span></div>
      <ul class="ctx-list">
        <li><span>参与上下文的原文</span><em>${ctx.liveMessages} 条</em></li>
        <li><span>已压成摘要</span><em>${ctx.compactedMessages} 条</em></li>
        <li><span>当前摘要段</span><em>${ctx.summaries} 段</em></li>
        <li><span>累计压缩</span><em>${ctx.compactCount} 次</em></li>
      </ul>
    </div>
    ${ctx.note ? `<p class="ctx-note">${escapeHtml(ctx.note)}</p>` : ''}
  </div>`;
}

/** 画所有上下文环（顶栏一个、侧页一个）。在 DOM 插入之后调用。 */
function renderContextRings() {
  const context = activeDetail() ? activeDetail().context : null;
  for (const canvas of $$('canvas[data-ctx-ring]')) {
    // 侧页收起时 `clientWidth` 是 0；给一个显式尺寸，避免把画布画成 1px 之后
    // 再打开侧页时看不见（关着的时候也算过一次）。
    const size = canvas.dataset.ctxRing === 'topbar' ? 22 : 118;
    drawContextRing(canvas, context || { measured: false }, { size });
  }
  const pct = $('#ctx-ring-pct');
  if (pct) {
    pct.textContent = context && context.measured
      ? `${context.pct}%`
      : (context && context.hasReading ? context.usedShort : '—');
  }
  const chip = $('#ctx-chip');
  if (chip) {
    const limitWord = context && context.limitSource === 'budget' ? L('预算', 'budget') : L('窗口', 'window');
    const label = context && context.measured
      ? `${L('上下文', 'Context')} ${context.used} / ${limitWord} ${context.total} (${context.pct}%)`
      : `${L('上下文', 'Context')} ${context && context.hasReading ? context.used : '—'} / ${limitWord} ${context ? context.total : '—'}`;
    chip.setAttribute('aria-label', label);
    chip.title = label;
  }
}

function renderSession() {
  renderTopbar();
  // 先同步侧栏：用户点了哪个会话，左侧高亮就必须立刻跟过去。
  // 后面任何一块渲染失败都不能把它留在旧会话上（这正是"聊天框跳了、标识没跳"）。
  renderRail();
  try {
    renderThread();
  } catch (err) {
    console.error(err);
    thread.innerHTML = emptyStateMarkup({
      icon: 'close',
      title: '对话区没画出来',
      body: err.message || String(err),
    });
    threadEnd.hidden = true;
  }
  try {
    renderMargin();
  } catch (err) {
    console.error(err);
    marginBody.innerHTML = `<div class="mpanel"><p class="mpanel-sub">侧页这一块没画出来：${escapeHtml(err.message || String(err))}</p></div>`;
  }
  renderContextRings();
  renderAttachmentStrip();
  renderAnchorChip();
  translateUi();
}

/* ── view switching ───────────────────────────────────────────────── */
function setView(view) {
  state.view = view;
  $$('.view').forEach((el) => el.classList.toggle('is-active', el.dataset.view === view));
  $$('.foot-link').forEach((el) => el.classList.toggle('is-active', el.dataset.view === view));
  if (window.innerWidth <= 900) document.body.classList.remove('rail-open');
  if (view === 'settings') renderSettings();
  if (view === 'benchmark') renderBench();
  if (view === 'cognition') {
    renderCognition();
    if (!state.cognition.loaded) void loadCognition();
  }
  window.scrollTo(0, 0);
  translateUi();
}

/* ── chat run simulation ──────────────────────────────────────────── */
function updateBusyUI() {
  const prof = activeProfile();
  // 没有数据库档案时，真正说话的是 `.env` 里的模型。不能在这里读 `prof.short`
  // 然后抛错 —— 那会让用户消息已经画出来、请求却没发出去，看起来就像"消息消失了"。
  const busyLabel = prof ? (prof.short || prof.model) : currentModelLabel();
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
    strip.textContent = `排队 ${queued} 条`;
  } else if (strip) {
    strip.remove();
  }
  $('#composer-busy-text').textContent = state.busy ? `正在推演 · ${busyLabel}` : '正在推演';
}

function scrollThread(force = false) {
  const nearBottom = threadWrap.scrollHeight - threadWrap.scrollTop - threadWrap.clientHeight < 220;
  if (force || nearBottom) {
    threadWrap.scrollTo({ top: threadWrap.scrollHeight, behavior: 'smooth' });
  }
}

function jumpThreadBottom() {
  threadWrap.scrollTop = threadWrap.scrollHeight;
}

function createLiveMessage() {
  const article = document.createElement('article');
  article.className = 'msg msg-assistant is-live';
  article.innerHTML = `
    <div class="msg-gutter">
      <span class="msg-marker is-mark">${icon('archive-mark')}</span>
      <time>${clock()}</time>
      <svg class="ink-thread" viewBox="0 0 10 300" preserveAspectRatio="none" aria-hidden="true">
        <path d="M4,0 C9,45 -2,82 4,125 C10,168 0,215 5,250 C8,272 6,288 5,300" vector-effect="non-scaling-stroke"/>
      </svg>
      <div class="msg-trace"></div>
    </div>
    <div class="msg-body">
      <div class="msg-head"><span class="msg-role"><em>${L('档', 'SF')}</em> · STUDYFLOW</span><span class="msg-meta">推演中</span></div>
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

/**
 * 动画散场：把 `<span class="chunk">` 拆回纯文本。
 *
 * 只在动画播完之后调用。视觉不变（字符顺序、空白、富文本标签都在），
 * 但节点数从 O(字数) 回到 O(段落数)。
 */
function settleInk(root, delayMs = 1600) {
  if (!root) return;
  const spans = $$('.chunk', root);
  if (!spans.length) return;
  const strip = () => {
    // **按父节点分组**：正文可能跨多个 <p>，把不同段落的字拼成一段会破坏排版。
    const byParent = new Map();
    for (const span of spans) {
      if (!span.isConnected) continue;
      const parent = span.parentNode;
      if (!parent) continue;
      if (!byParent.has(parent)) byParent.set(parent, []);
      byParent.get(parent).push(span);
    }
    for (const [parent, group] of byParent) {
      // 相邻的 span 合成一个文本节点，**原地**替换（先插后删，避免用到已脱离的节点）。
      let buffer = '';
      for (const span of group) buffer += span.textContent;
      const anchor = group[0];
      if (anchor && anchor.parentNode === parent) {
        parent.insertBefore(document.createTextNode(buffer), anchor);
      }
      for (const span of group) span.remove();
    }
  };
  if (delayMs <= 0) { strip(); return; }
  setTimeout(strip, delayMs);
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

/**
 * 往正文里追加一段流式文本 —— **只给最后几个字挂动画**。
 *
 * 原先是每个字符建一个 `<span class="chunk">`：一篇 1500 字的回答就是 1500 个节点，
 * 而且每个都要跑一次淡入。实测单次追加只要 1ms，但节点数是 O(字数) 的，
 * 长回答 + 滚动 + 索引重建叠在一起会掉帧。
 *
 * 现在：新到的字照旧逐个包 span（淡入只发生在"正在落的那几个字"上），
 * 早于窗口的 span 被折叠成一段纯文本节点 —— 视觉完全一致，节点数是 O(1)。
 * 折叠必须**保留原有顺序与空白**，所以是把 span 的 textContent 拼起来再插回原位。
 */
const INK_LIVE_SPANS = 24;
function appendInk(prose, chunk, liveSpans) {
  const frag = document.createDocumentFragment();
  for (const ch of Array.from(chunk)) {
    const span = document.createElement('span');
    span.className = 'chunk';
    span.textContent = ch;
    frag.appendChild(span);
    liveSpans.push(span);
  }
  prose.appendChild(frag);

  if (liveSpans.length > INK_LIVE_SPANS * 2) {
    // 把最老的一批折叠成纯文本：插在第一个"仍保留"的 span 之前。
    const keepFrom = liveSpans.length - INK_LIVE_SPANS;
    const fold = liveSpans.splice(0, keepFrom);
    const anchor = liveSpans[0];
    const text = fold.map((el) => el.textContent).join('');
    if (text && anchor && anchor.parentNode) {
      anchor.parentNode.insertBefore(document.createTextNode(text), anchor);
      for (const el of fold) el.remove();
    }
  }
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
  if (interrupted && !silent) toast('已停止');
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
  // 还挂着动画的 span（旧的会被折叠成纯文本，见 appendInk）。
  const liveSpans = [];

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
      appendInk(prose, chunk, liveSpans);
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
  const html = richText(reply);
  prose.innerHTML = inkify(html);
  $$('.chunk', prose).forEach((el, i) => { el.style.animationDelay = `${Math.min(i, 400) * 4}ms`; });
  prose.classList.remove('streaming');
  // 墨迹动画只演一遍。散场后把逐字符的 span 撤回纯文本 ——
  // 一篇 900 字的回答原本会永久留下 800+ 个节点，而这些节点除了当初那一次
  // 淡入之外不再有任何作用（滚动、索引重建、切语言都要陪着扫一遍）。
  settleInk(prose);
  article.classList.remove('is-live');

  // 复用 adapt 的 traceRow：状态→类名的映射只有那一处，
  // 在这里再写一遍就会出现"流式时一种颜色、重绘后另一种"。
  const traces = payload.toolTrace.map((entry) => traceRow(entry));

  // ★ 把轨迹连同**四种状态**写回这一条消息。
  // 以前只 push 进 `detail.messages`，不动 DOM —— 于是当轮看不到轨迹，
  // 只有刷新或切走再回来（从 detail 重绘）才出现。用户会说"工具调用没显示"。
  // 状态色必须跟着回来：被守卫拦下（rejected）不能画成成功。
  const traceBox = article.querySelector('.msg-trace');
  if (traceBox) {
    traceBox.innerHTML = traces.map((tr) => `
      <div class="trace-item ${tr.state || 'is-done'}">
        <strong>${escape(tr.label)}</strong><span>${escape(tr.note || '')}</span>
      </div>`).join('');
  }
  if (meta) {
    const stop = payload.stopReason || 'completed';
    const stopLabel = ({
      completed: '',
      llm_error: '模型错误',
      internal_error: '出错',
      max_steps: '步数用尽',
      client_disconnected: '中断了',
    })[stop] || '';
    meta.textContent = stopLabel
      || (payload.toolTrace.length ? `回答 · 工具 ${payload.toolTrace.length}` : '回答');
    meta.classList.toggle('is-error', stop === 'llm_error' || stop === 'internal_error');
    meta.classList.toggle('is-warn', stop === 'max_steps');
    // 「说改了」但没有对应的写操作。不改写模型原话 —— 只把它标出来，
    // 让用户知道这句话背后没有一次真的写入（判据在后端，前端只负责显示）。
    if (payload.unsupportedChangeClaim) {
      meta.classList.add('is-warn');
      meta.textContent = payload.toolTrace.length
        ? `回答 · 工具 ${payload.toolTrace.length}（未改计划）`
        : '回答（这一轮没有改动计划）';
    }
  }

  if (payload.unsupportedChangeClaim) {
    const warn = document.createElement('div');
    warn.className = 'msg-warning';
    warn.textContent = '这句话说改动发生了，但这一轮没有任何写操作 —— 计划没有真的改变。可以让它重做一次，或直接确认当前计划。';
    article.querySelector('.msg-body').insertBefore(warn, article.querySelector('.msg-body').firstChild);
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
    stopReason: payload.stopReason || 'completed',
  });
  if (payload.context) detail.context = adaptContext(payload.context);

  // 模型调用失败时，后端把原因写成一条正常的 assistant 消息（并带 stop:llm_error）。
  // 这里既把它画出来，也弹一次 toast —— 只靠在长文里读一句话太容易错过。
  if (['llm_error', 'internal_error'].includes(payload.stopReason)) {
    toast(String(reply || '模型这一轮没有返回结果').slice(0, 180), 'danger');
  }

  state.run = null;
  state.busy = false;
  threadWrap.classList.remove('is-thinking');
  updateBusyUI();
  bindInkDots();
  renderThreadIndex();
  scrollThread(true);

  // 与后端对齐但不重绘：这一轮可能改了计划版本，侧栏的时间也变了
  try {
    const before = detail.plan ? detail.plan.version : null;
    await loadDetail(sessionId, { render: false });
    // 讨论的事务在流结束后才提交；`done` 到达时立刻 GET /state 有可能读到
    // 上一版计划。计划类工具成功时再等一拍重读一次，避免“模型说改了，
    // 侧页还是旧版”。这不是防御性拦截，只是让读和写落在同一个事实上。
    if (payload.toolTrace.some(
      (t) => ['update_task_plan', 'propose_plan_change'].includes(t.tool)
        && ['success', 'empty'].includes(t.status),
    )) {
      await wait(350);
      await loadDetail(sessionId, { render: false });
    }
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
  return toolLabel(tool);
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
  if (meta) {
    meta.textContent = '出错';
    meta.classList.add('is-error');
  }
  const prose = article.querySelector('.msg-prose');
  prose.classList.remove('streaming');
  prose.insertAdjacentHTML('beforeend', `<p class="muted">${escape(message || '这一轮没有得出结论。')}</p>`);
  state.run = null;
  state.busy = false;
  threadWrap.classList.remove('is-thinking');
  updateBusyUI();
  toast(String(message || '这一轮中断了').slice(0, 180), 'danger');
  scrollThread(true);
}

function beginUserTurn(text, { fromQueue = false } = {}) {
  const detail = activeDetail();
  if (!detail || !state.currentSession) return;
  const message = { id: `local-u-${Date.now()}`, role: 'user', time: clock(), text, foot: true };
  detail.messages.push(message);

  if (detail.messages.length === 1) renderThread();
  else {
    const article = document.createElement('div');
    article.innerHTML = messageMarkup(message, detail.messages.length - 1);
    const node = article.firstElementChild;
    thread.appendChild(node);
    bindInkDots();
    renderThreadIndex();
  }
  scrollThread(true);

  // 锚点只在这一句话上有效，发出去就清掉 —— 否则下一条消息会莫名带上它
  const stepId = state.anchorStep;
  state.anchorStep = null;
  if (composerInput) composerInput.placeholder = COMPOSER_PLACEHOLDER;
  renderAnchorChip();
  void startLiveRun({ userText: text, stepId });
}

function sendFromComposer({ programmaticText = null } = {}) {
  const text = (programmaticText ?? composerInput.value).trim();
  if (!text) return;

  if (state.busy) {
    if (state.busyBehavior === 'block') {
      composer.classList.add('is-shake');
      setTimeout(() => composer.classList.remove('is-shake'), 420);
      toast('正在生成', 'danger');
      return;
    }
    if (state.busyBehavior === 'queue') {
      composerInput.value = '';
      autoGrow();
      const item = { text, el: null };
      const detail = activeDetail();
      if (detail) {
        const article = document.createElement('div');
        article.innerHTML = messageMarkup({ id: `local-q-${Date.now()}`, role: 'user', time: clock(), text, foot: false }, detail.messages.length);
        const node = article.firstElementChild;
        node.classList.add('is-queued');
        node.querySelector('.msg-head').insertAdjacentHTML('beforeend', '<span class="msg-queued-tag">排队中</span>');
        thread.appendChild(node);
        item.el = node;
        renderThreadIndex();
        scrollThread(true);
      }
      state.queue.push(item);
      updateBusyUI();
      toast('已排队');
      return;
    }
    if (state.busyBehavior === 'interrupt') {
      composerInput.value = '';
      autoGrow();
      cancelRun({ interrupted: true, silent: true });
      toast('已打断');
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
  // 起草期只用于“还没有计划”；计划写入后由后端自动进入执行中。
  draft: [['completed', '归档']],
  active: [['review', '进入复盘'], ['completed', '归档']],
  review: [['active', '回到执行'], ['completed', '归档']],
  completed: [['review', '重新打开'], ['active', '回到执行']],
  abandoned: [],
};

function openStateMenu(anchor) {
  const session = currentSession();
  if (!session) {
    toast('先从左边挑一个会话吧。');
    return;
  }
  const targets = STATE_ACTIONS[session.state] || [];
  if (!targets.length) {
    toast('无可执行操作');
    return;
  }
  const plan = activeDetail()?.plan;
  // 没有计划时没有“步骤未完成”这回事；有计划则要求全部勾选。
  const canReview = !plan || (plan.steps.length > 0
    && plan.steps.every((step) => step.status === 'done'));
  const items = targets.map(([target, label]) => {
    if (target === 'review' && session.state === 'active' && !canReview) {
      return `<button class="popover-item is-disabled" disabled>${icon('arrow')}<span>进入复盘</span>
        <span class="pop-meta">完成全部步骤后可用</span></button>`;
    }
    return popoverItem('go-state', label, 'arrow', `data-target="${target}"`);
  }).join('');
  const note = session.state === 'draft'
    ? '<p class="pop-note">写入计划后自动进入执行中。</p>'
    : '';
  openPopover(anchor, `
    <div class="popover-label">${stateLabel(session.state)} · 下一步</div>
    ${items}
    ${note}
  `);
}

async function goState(target) {
  const session = currentSession();
  if (!session) return;
  closePopover();
  const said = { active: '已开始执行', review: '已进入复盘', completed: '已归档', abandoned: '已放弃' };
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
    if (target === 'completed') void runArchiveReview(session.id);
  } catch (err) {
    // 后端的 409/422 原文比一句"操作失败"有用得多
    toast(err.message, 'danger');
  }
}

/**
 * 归档后的自动复盘。
 *
 * 它在后端跑一轮**不写用户消息**的讨论：模型尝试用 `submit_global_update`
 * 把这次会话里值得留下的东西提成提案。结果是：
 * - 一条带 `archive_review` 标签的助手消息（聊天里能看到它做了什么）；
 * - 0–N 条待确认提案 → 弹出确认框，用户勾选后才写入。
 */
let archiveReviewToken = 0;
async function runArchiveReview(sessionId) {
  const token = ++archiveReviewToken;
  const banner = $('#review-banner');
  if (banner) banner.hidden = false;
  toast('正在复盘');
  try {
    const result = await api.archiveReview(sessionId);
    if (token !== archiveReviewToken) return;
    // 后端在响应发出之后才提交；立刻重读可能读不到刚写的那条复盘消息。
    await wait(250);
    await loadDetail(sessionId);
    if (result.context) {
      const detail = state.details[sessionId];
      if (detail) detail.context = adaptContext(result.context);
    }
    renderSession();
    if (result.proposals && result.proposals.length) {
      openProposalDialog(result.proposals);
    } else {
      toast('复盘完成');
    }
  } catch (err) {
    toast(`归档复盘没成：${err.message}`, 'danger');
  } finally {
    if (token === archiveReviewToken && banner) banner.hidden = true;
  }
}

const INTENT_LABEL = {
  add_preference: '偏好',
  update_parameter: '参数',
  revise: '改写',
  archive: '归档',
  restore: '恢复',
  no_change: '无改动',
};

function intentValueText(intent) {
  const value = intent.value;
  if (!value || typeof value !== 'object') return String(value ?? '');
  if (value.statement) return String(value.statement);
  if (value.condition && value.effect) return `当 ${value.condition} 时，${value.effect}`;
  return Object.entries(value)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`)
    .join(' · ');
}

function openProposalDialog(proposals) {
  state.proposalQueue = proposals;
  const list = $('#proposal-list');
  if (!list) return;
  list.innerHTML = proposals.map((proposal) => `
    <section class="proposal-block" data-proposal-id="${escapeHtml(proposal.proposal_id)}">
      <header>
        <strong>${escapeHtml(proposal.proposal_id)}</strong>
        <em>${escapeHtml(proposal.trigger_type || 'R3')} · ${proposal.intents.length} 条</em>
      </header>
      ${proposal.intents.map((intent) => `
        <label class="proposal-intent">
          <input type="checkbox" checked data-intent-id="${escapeHtml(intent.intent_id)}">
          <div>
            <strong>${escapeHtml(INTENT_LABEL[intent.action] || intent.action)} · ${escapeHtml(intent.target)}</strong>
            <p>${escapeHtml(intentValueText(intent))}</p>
            ${intent.reasoning ? `<em>${escapeHtml(intent.reasoning)}</em>` : ''}
          </div>
        </label>`).join('')}
    </section>`).join('');
  const sub = $('#proposal-sub');
  if (sub) sub.textContent = `模型提议了 ${proposals.reduce((n, p) => n + p.intents.length, 0)} 条改动；勾选你同意的条目，写入才会发生。`;
  $('#proposal-dialog')?.showModal();
}

async function resolveArchiveProposals(approved) {
  const proposals = state.proposalQueue || [];
  const dialog = $('#proposal-dialog');
  const checks = new Map(
    $$('#proposal-list input[data-intent-id]').map((el) => [el.dataset.intentId, el.checked]),
  );
  const failures = [];
  for (const proposal of proposals) {
    const decisions = proposal.intents.map((intent) => ({
      intent_id: intent.intent_id,
      approved: approved && (checks.get(intent.intent_id) ?? true),
    }));
    try {
      await api.resolveProposal(proposal.proposal_id, decisions);
    } catch (err) {
      failures.push(err.message);
    }
  }
  state.proposalQueue = [];
  dialog?.close();
  // 提案可能改了偏好/基线/证据：重读认知页与当前会话。
  state.cognition.loaded = false;
  if (state.view === 'cognition') void loadCognition();
  if (state.currentSession) void loadDetail(state.currentSession);
  if (failures.length) toast(failures.join('；'), 'danger');
  else toast(approved ? '已写入' : '未写入');
}

/* ── popovers ─────────────────────────────────────────────────────── */
function closePopover() {
  popLayer.innerHTML = '';
  $$('[data-popover-anchor]').forEach((el) => { delete el.dataset.popoverAnchor; });
}

function openPopover(anchor, html, className = '', { center = false } = {}) {
  closePopover();
  anchor.dataset.popoverAnchor = '1';
  const el = document.createElement('div');
  el.className = 'popover' + (className ? ' ' + className : '');
  el.innerHTML = html;
  popLayer.appendChild(el);
  if (center) {
    // 表单类面板固定在视口中央：锚在按钮上时，窗口一小就会溢出/错位，
    // 而"编辑档案"是一个独立的操作，不依赖它从哪个按钮打开。
    el.style.left = '50%';
    el.style.top = '50%';
    el.style.transform = 'translate(-50%, -50%)';
    translateUi(el);
    return el;
  }
  const rect = anchor.getBoundingClientRect();
  const width = el.offsetWidth;
  const height = el.offsetHeight;
  let left = rect.left;
  if (left + width > window.innerWidth - 12) left = rect.right - width;
  let top = rect.bottom + 7;
  if (top + height > window.innerHeight - 12) top = Math.max(12, rect.top - height - 7);
  el.style.left = Math.max(12, left) + 'px';
  el.style.top = top + 'px';
  translateUi(el);
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
  if (!state.profiles.length) {
    openPopover(anchor, `
      <div class="popover-label">${t('model.switch')}</div>
      <p class="pop-note">还没有模型档案。现在用的是 .env 里的模型。</p>
      ${popoverItem('go-settings-models', t('model.manage'), 'gear')}
    `);
    return;
  }
  const groups = state.profiles.map((p) => `
    <div class="model-group">
      <div class="model-group-head">
        <span class="profile-mark" style="width:18px;height:18px;font-size:7px">${escapeHtml(p.mark)}</span>
        <span>${escapeHtml(p.short)}</span>
        <em>${escapeHtml(p.provider)}</em>
      </div>
      ${p.models.map((m) => {
        const on = p.active && p.model === m;
        return `<button class="popover-item model-item${on ? ' is-selected' : ''}"
          data-action="activate-profile" data-id="${p.id}" data-model="${escapeHtml(m)}">
          <span class="model-dot${on ? ' is-on' : ''}"></span>
          <span>${escapeHtml(m)}</span>
          <span class="pop-meta">${on ? t('model.current') : ''}</span>
        </button>`;
      }).join('')}
    </div>`).join('');
  openPopover(anchor, `
    <div class="popover-label">${t('model.switch')}</div>
    <div class="model-groups">${groups}</div>
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
  state.anchorStep = null;
  found.folder.open = true;
  if (!state.details[id]) state.details[id] = makeDetail();
  // 先用缓存画一次：点击必须立刻有反应，网络不能挡在点击和画面之间
  renderSession();
  requestAnimationFrame(() => jumpThreadBottom());
  applyBreakpoint();
  syncMarginToggle();
  renderAttachmentStrip();
  void loadAttachments(id);
  void loadDetail(id)
    .then(() => requestAnimationFrame(() => jumpThreadBottom()))
    .catch((err) => {
      // 读失败不能停在骨架屏上 —— 那会让"读不到"看起来像"一直在读"。
      const stale = state.details[id];
      if (stale) { stale.loaded = true; stale.failed = err.message || String(err); }
      if (state.currentSession === id) renderThread();
      toast(err.message, 'danger');
    });
}

/** 点到一个没有会话的领域：中间给出提示与一个明确的「新建会话」。 */
function showFolderEmpty(folder) {
  renderSession();
  thread.innerHTML = `<div class="empty-thread">
    <span class="empty-seal">${icon('folder')}</span>
    <h2>${escapeHtml(folder.name)}</h2>
    <p>该领域暂无会话。</p>
    <div class="prompt-hints">
      <button data-action="new-session-in-folder" data-id="${folder.id}">在这个领域新建会话</button>
    </div>
  </div>`;
  threadEnd.hidden = true;
  marginBody.innerHTML = `<div class="mpanel">
    <div class="mpanel-head"><h3>还没有会话</h3><span class="ver">${escapeHtml(folder.name)}</span></div>
    <p class="mpanel-sub">从左边挑一个会话，这里就会显示它的动机、计划与上下文。</p>
    <div class="mpanel-actions"><button class="mini-btn" data-action="new-session-in-folder" data-id="${folder.id}">新建会话</button></div>
  </div>`;
  renderThreadIndex();
}

function ensureSelection() {  if (currentSession()) { renderSession(); return; }
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
  thread.innerHTML = emptyStateMarkup({ icon: 'folder', title: '还没有会话', body: '先在左侧建一个领域，再往里加会话。' });
  threadEnd.hidden = true;
  marginBody.innerHTML = '<div class="mpanel"><p class="mpanel-sub">尚无会话。</p></div>';
}

/* 侧栏这些动作以前是**本地搬家**（把对象从一个数组挪到另一个）。
   现在它们都是一次真实请求 + 一次重新读侧栏 —— 因为"归档"在库里是一个
   时间戳，不是界面上的另一种状态。失败时把后端那句话原样报给用户。 */

async function runRailAction(label, action) {
  try {
    await action();
    // 提交发生在响应之后；立刻重读会看不到刚写的那一行。
    await wait(200);
    await loadWorkspace();
    ensureSelection();
    renderRail();
    renderTopbar();
    toast(label);
  } catch (err) {
    toast(err.message || '操作失败', 'danger');
  }
}

function archiveSession(id) {
  closePopover();
  void runRailAction('已归档', () => api.archiveSession(id));
}

function deleteSession(id) {
  const found = findSession(id);
  if (!found) return;
  askConfirm({
    title: '删除这个会话？',
    body: `归档保留内容；删除不可恢复。`,
    ok: '删除会话',
    onOk: () => void runRailAction('已删除', async () => {
      await api.deleteSession(id);
      delete state.details[id];
      if (state.currentSession === id) state.currentSession = null;
    }),
  });
}

function archiveFolder(id) {
  closePopover();
  void runRailAction('已归档', () => api.archiveFolder(id));
}

function deleteFolder(id) {
  const folder = findFolder(id);
  if (!folder) return;
  askConfirm({
    title: '删除这个领域？',
    body: `删除领域及其中 ${folder.sessions.length} 个会话，不可恢复。`,
    ok: '删除领域',
    onOk: () => void runRailAction('已删除', async () => {
      for (const s of folder.sessions) delete state.details[s.id];
      await api.deleteFolder(id);
      if (folder.sessions.some((s) => s.id === state.currentSession)) state.currentSession = null;
    }),
  });
}

function restoreSession(id) {
  closePopover();
  void runRailAction('已恢复', () => api.restoreSession(id));
}

function restoreFolder(id) {
  closePopover();
  void runRailAction('已恢复', () => api.restoreFolder(id));
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
    // `version_no` 是“第几版”，`lockVersion` 是每次写都 +1 的乐观锁；
    // PATCH 要的是后者。用错会让一次正常勾选 409，而提示看起来像“别人刚改过”。
    let expected = detail.plan.lockVersion ?? detail.versions?.plan ?? detail.plan.version;
    try {
      await api.markStep(state.currentSession, stepId, next, expected);
    } catch (err) {
      // 写后立刻读可能撞上提交窗口；409 时现读一次锁版本再重试一次。
      if (err.status !== 409) throw err;
      const fresh = await api.state(state.currentSession);
      expected = fresh.plan?.version ?? expected;
      await api.markStep(state.currentSession, stepId, next, expected);
    }
    // 提交发生在响应之后；不等待就回读会拿到旧的 lockVersion。
    await wait(200);
    await loadDetail(state.currentSession, { render: false });
    renderMargin();
    renderRail();
    toast(next === 'done' ? '已完成' : '已取消完成');
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
  renderAnchorChip();
  // 锚点要**看得见**：否则用户点了计划项，界面上只有输入框里的占位文案变了，
  // 而下一步（模型会不会按这一步回答）没有任何可见的承诺。
  renderMargin();
  composerInput.focus();
  autoGrow();
  scrollThread(true);
  composer.classList.add('is-anchored');
  setTimeout(() => composer.classList.remove('is-anchored'), 900);
}

function renderAnchorChip() {
  const chip = $('#composer-anchor');
  if (!chip) return;
  const detail = activeDetail();
  const step = state.anchorStep && detail?.plan
    ? detail.plan.steps.find((s) => s.id === state.anchorStep)
    : null;
  if (!step) {
    chip.hidden = true;
    if (composerInput) composerInput.placeholder = COMPOSER_PLACEHOLDER;
    return;
  }
  chip.hidden = false;
  const text = $('#composer-anchor-text');
  if (text) text.textContent = `讨论锚点 · 第 ${step.seq} 步 · ${step.id}`;
}

function clearAnchor() {
  state.anchorStep = null;
  renderAnchorChip();
  renderMargin();
  if (composerInput) composerInput.placeholder = COMPOSER_PLACEHOLDER;
  composerInput?.focus();
}

/**
 * 回答里的 `[ST-001]` → 跳到右侧计划并高亮那一步。
 *
 * 模型只有在这份计划里**真的存在**这个编号时才会这样写（上下文里有全量
 * 步骤表）；但界面仍然按"查不到就说查不到"处理 —— 不滚到第一个看起来像的
 * 元素上去。
 */
function jumpToStep(stepId) {
  if (!stepId) return;
  state.marginTab = 'plan';
  workspace.classList.remove('is-margin-closed');
  $('#margin-toggle')?.classList.add('is-on');
  $('#margin-toggle')?.setAttribute('aria-pressed', 'true');
  renderMargin();
  const node = marginBody.querySelector(`[data-action="discuss-step"][data-step-id="${stepId}"]`);
  if (!node) {
    toast(`这一版计划里没有 ${stepId}（可能已被改掉）`, 'danger');
    return;
  }
  node.scrollIntoView({ block: 'center', behavior: 'smooth' });
  node.classList.add('is-flash');
  setTimeout(() => node.classList.remove('is-flash'), 1600);
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
  const card = $('#active-model-card');
  const prof = activeProfile();
  if (card) {
    if (!prof) {
      // 一份档案都没有：这时真正在说话的是 `.env` 里那个模型。
      // **不编一个档案出来**，如实说"还没有档案、现在用的是环境变量里的那个"。
      const s = state.llmConfig?.settings;
      card.innerHTML = `
        <span class="am-seal">当前</span>
        <div class="am-left">
          <div class="am-kicker"><span class="pulse-dot"></span>正在使用 · .env 默认值</div>
          <div class="am-title"><h3>未配置档案</h3><span class="am-model">${escapeHtml(s ? s.llm_model : '—')}</span></div>
          <div class="am-meta">
            <span>服务商 <b>${escapeHtml(s ? s.llm_provider : '—')}</b></span>
            <span>端点 <b>${escapeHtml(s ? s.llm_base_url : '—')}</b></span>
            <span>上下文 <b>${s && s.llm_num_ctx ? escapeHtml(String(s.llm_num_ctx)) : '—'}</b></span>
            <span>密钥 <b>${s && s.has_api_key ? '已设置' : '未设置'}</b></span>
          </div>
          <div class="am-think">
            <em>当前实际使用的模型。</em>
          </div>
        </div>`;
    } else {
      card.innerHTML = `
        <span class="am-seal">当前</span>
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
            <em>自动：不发送该字段。</em>
          </div>
        </div>
        <div class="am-probe">
          <button class="mini-btn" data-action="probe" data-id="${prof.id}">${state.lang === 'en' ? 'Probe' : '探活'}</button>
          <span class="probe-result" id="probe-result">${escapeHtml(state.probeText[prof.id] || '未探活')}</span>
        </div>`;
    }
  }

  const list = $('#profile-list');
  if (list) {
    if (!state.profiles.length) {
      list.innerHTML = `<div class="profile-empty">暂无档案。</div>`;
    } else {
      list.innerHTML = state.profiles
        .map(
          (p) => `
      <div class="profile ${p.active ? 'is-active' : ''}">
        <span class="profile-mark">${escapeHtml(p.mark)}</span>
        <div class="profile-main">
          <strong>${escapeHtml(p.label)}</strong>
          <div class="profile-models">
            ${p.models.map((m) => `<button class="model-pill${p.active && p.model === m ? ' is-on' : ''}"
              data-action="activate-profile" data-id="${p.id}" data-model="${escapeHtml(m)}"
              title="用 ${escapeHtml(m)} 回答">${escapeHtml(m)}</button>`).join('')}
          </div>
        </div>
        <div class="profile-tags">
          <span class="tag">${escapeHtml(p.provider)}</span>
          ${p.active ? `<span class="tag accent">${t('model.current')}</span>` : ''}
        </div>
        <div class="profile-actions">
          <button class="mini-btn" data-action="list-models" data-id="${p.id}">清单</button>
          <button class="mini-btn" data-action="edit-profile" data-id="${p.id}">编辑</button>
          <button class="mini-btn" data-action="delete-profile" data-id="${p.id}">删除</button>
        </div>
      </div>`,
        )
        .join('');
    }
  }
  translateUi();
}

/* ── 全局认知页 ──────────────────────────────────────────────────── */
const CHANNEL_LABEL = { stated: '自述', experienced: '经验', measured: '实测' };

//: M1 变量的英文名。用户内容（偏好、证据、假说正文）不翻译；
//: 这里翻的是系统预置的变量表。
const M1_EN = {
  'V-SE': 'Signal extraction',
  'V-HSR': 'Help-seeking resistance',
  'V-DC': 'Decision caution',
  'V-IR': 'Initiation resistance',
  'V-PAR': 'Post-action reflection',
  'V-ENV': 'Environment sensitivity',
  'V-ATT': 'Typical attention span',
  'V-CP': 'Context material preference',
  'V-SMP': 'Sensory medium preference',
  'V-OSP': 'Organisation strategy preference',
  'V-ACT': 'Activity strategy repertoire',
};
const m1Name = (v) => (state.lang === 'en' ? (M1_EN[v.variable_id] || v.display_name) : (v.display_name || v.name));
const channelName = (c) => t(`cog.${c}`) || CHANNEL_LABEL[c] || c;

async function loadCognition({ render = true } = {}) {
  const cog = state.cognition;
  if (cog.loading) return;
  cog.loading = true;
  if (render) renderCognition();
  try {
    const [prefs, vars, evidence, hypotheses] = await Promise.all([
      api.preferences(),
      api.m1Variables(),
      api.evidence(),
      api.hypotheses(),
    ]);
    cog.data = {
      prefs: prefs.items || [],
      vars: vars.items || [],
      evidence: evidence.items || [],
      hypotheses: hypotheses.items || [],
    };
    cog.loaded = true;
    cog.error = '';
  } catch (err) {
    cog.error = err.message;
  } finally {
    cog.loading = false;
    if (render) renderCognition();
  }
}

function cognitionStat(label, value) {
  return `<span class="cog-stat"><b>${escapeHtml(String(value))}</b>${escapeHtml(label)}</span>`;
}

function renderCognition() {
  const body = $('#cognition-body');
  const stats = $('#cog-stats');
  if (!body) return;
  const cog = state.cognition;

  if (cog.loading && !cog.data) {
    if (stats) stats.innerHTML = '';
    body.innerHTML = '<div class="cog-empty reveal">正在读取全局认知…</div>';
    return;
  }
  if (cog.error && !cog.data) {
    if (stats) stats.innerHTML = '';
    body.innerHTML = `<div class="cog-empty is-error reveal">
      <strong>全局认知读不到</strong>
      <p>${escapeHtml(cog.error)}</p>
      <button class="mini-btn" data-action="refresh-cognition">再试一次</button>
    </div>`;
    return;
  }
  if (!cog.data) return;

  const { prefs, vars, evidence, hypotheses } = cog.data;
  const hard = prefs.filter((p) => p.is_constraint);
  const soft = prefs.filter((p) => !p.is_constraint);
  const measuredChannels = vars
    .flatMap((v) => v.channels || [])
    .filter((c) => c.current).length;

  if (stats) {
    stats.innerHTML = [
      cognitionStat(t('cog.hardCount'), hard.length),
      cognitionStat(t('cog.softCount'), soft.length),
      cognitionStat(t('cog.measuredCount'), measuredChannels),
      cognitionStat(t('cog.evidenceCount'), evidence.length),
      cognitionStat(t('cog.hypothesisCount'), hypotheses.length),
    ].join('');
  }

  const channelCell = (channel, coverage) => {
    const current = coverage?.current;
    return `<div class="cog-bcell${current ? ' is-on' : ''}${coverage && !coverage.designed ? ' is-na' : ''}">
      <span>${escapeHtml(channelName(channel))}</span>
      <b>${current ? escapeHtml(String(current.score ?? '—')) : '—'}</b>
      ${current && current.confidence ? `<em>${escapeHtml(current.confidence)}</em>` : ''}
    </div>`;
  };

  const baselineSection = `<section class="cog-section cog-baseline reveal">
    <header class="cog-section-head">
      <div><span class="section-mark">${state.lang === 'en' ? '01' : '其一'}</span><h2>${t('cog.baseline')}</h2></div>
      <p>${t('cog.baselineNote')}</p>
    </header>
    ${vars.length ? `<div class="cog-baseline-list">
      <div class="cog-bhead">
        <span>${t('cog.variable')}</span><span>${t('cog.stated')}</span><span>${t('cog.experienced')}</span><span>${t('cog.measured')}</span>
      </div>
      ${vars.map((v) => {
        const byChannel = new Map((v.channels || []).map((c) => [c.channel, c]));
        return `<div class="cog-brow">
          <div class="cog-bname"><strong>${escapeHtml(m1Name(v))}</strong><em>${escapeHtml(v.variable_id)}</em></div>
          ${['stated', 'experienced', 'measured'].map((channel) => channelCell(channel, byChannel.get(channel))).join('')}
        </div>`;
      }).join('')}
    </div>` : '<div class="cog-empty">变量表还是空的。</div>'}
  </section>`;

  const prefColumn = `<article class="cog-col">
    <header class="cog-col-head"><span>${t('cog.prefs')}</span><em>${prefs.length}</em></header>
    ${prefs.length ? `<ul class="cog-col-list">${prefs.map((p) => `
      <li>
        <span class="cog-badge ${p.is_constraint ? 'is-hard' : ''}">${p.is_constraint ? t('cog.hard') : t('cog.soft')}</span>
        <div><strong>${escapeHtml(p.statement)}</strong><em>${escapeHtml(p.pref_id)} · ${t('cog.priority')} ${p.priority}</em></div>
      </li>`).join('')}</ul>` : '<p class="cog-col-empty">—</p>'}
  </article>`;

  const evidenceColumn = `<article class="cog-col">
    <header class="cog-col-head"><span>${t('cog.evidence')}</span><em>${evidence.length}</em></header>
    ${evidence.length ? `<ul class="cog-col-list">${evidence.map((e) => `
      <li>
        <span class="cog-dot is-jade"></span>
        <div><strong>当 ${escapeHtml(e.condition)} 时，${escapeHtml(e.effect)}</strong>
        <em>${escapeHtml(e.evidence_id)} · ${escapeHtml(e.evidence_strength)}</em></div>
      </li>`).join('')}</ul>` : '<p class="cog-col-empty">—</p>'}
  </article>`;

  const hypothesisColumn = `<article class="cog-col">
    <header class="cog-col-head"><span>${t('cog.hypotheses')}</span><em>${hypotheses.length}</em></header>
    ${hypotheses.length ? `<ul class="cog-col-list">${hypotheses.map((h) => `
      <li>
        <span class="cog-dot"></span>
        <div><strong>当 ${escapeHtml(h.condition)} 时，${escapeHtml(h.effect)}</strong>
        <em>${escapeHtml(h.hypothesis_id)} · ${t('cog.unverified')}</em></div>
      </li>`).join('')}</ul>` : '<p class="cog-col-empty">—</p>'}
  </article>`;

  const parallelSection = `<section class="cog-section reveal">
    <header class="cog-section-head">
      <div><span class="section-mark">${state.lang === 'en' ? '02' : '其二'}</span><h2>${t('cog.parallel')}</h2></div>
      <p>${t('cog.parallelNote')}</p>
    </header>
    <div class="cog-parallel">${prefColumn}${evidenceColumn}${hypothesisColumn}</div>
  </section>`;

  body.innerHTML = baselineSection + parallelSection;
  translateUi();
}

/* ── 会话资料（附件）─────────────────────────────────────────────── */
function humanSize(bytes) {
  const n = Number(bytes || 0);
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

async function loadAttachments(sessionId) {
  if (!sessionId) return;
  try {
    const got = await api.attachments(sessionId);
    state.attachments[sessionId] = got.items || [];
  } catch (err) {
    // 资料清单读不到不该挡住对话：它只是"这个会话带了几份材料"。
    state.attachments[sessionId] = state.attachments[sessionId] || [];
    toast(`资料清单读不到：${err.message}`, 'danger');
  }
  renderAttachmentStrip();
}

function renderAttachmentStrip() {
  const strip = $('#attachment-strip');
  if (!strip) return;
  const items = state.attachments[state.currentSession] || [];
  strip.hidden = !items.length;
  if (!items.length) {
    strip.innerHTML = '';
    return;
  }
  strip.innerHTML = items.map((item) => `
    <span class="attach-chip${item.truncated ? ' is-truncated' : ''}" title="${escapeHtml(item.note || '')}">
      <span class="attach-kind">${escapeHtml((item.ext || '').replace('.', '') || item.kind)}</span>
      <span class="attach-name">${escapeHtml(item.filename)}</span>
      <em>${escapeHtml(humanSize(item.bytes_len))}</em>
      <button class="attach-x" data-action="delete-attachment" data-id="${item.attachment_id}" aria-label="移除这份资料">×</button>
    </span>`).join('');
}

async function uploadFiles(files) {
  const sessionId = state.currentSession;
  if (!sessionId) {
    toast('先从左边挑一个会话，或者新建一个。', 'danger');
    return;
  }
  const list = Array.from(files || []);
  if (!list.length) return;
  const failed = [];
  const added = [];
  for (const file of list) {
    try {
      const row = await api.uploadAttachment(sessionId, file);
      added.push(row);
    } catch (err) {
      failed.push(err.message || '不支持这种文件');
    }
  }
  // 响应行就是写入结果。先并进界面，再在提交完成后重读一次做对齐 ——
  // 反向做（传完立刻 GET）会撞上"提交在响应之后"的窗口，漏掉刚传的那份。
  const existing = state.attachments[sessionId] || [];
  for (const row of added) {
    if (!existing.some((item) => item.attachment_id === row.attachment_id)) existing.push(row);
  }
  state.attachments[sessionId] = existing;
  renderAttachmentStrip();
  setTimeout(() => { if (state.currentSession === sessionId) void loadAttachments(sessionId); }, 400);
  if (state.currentSession === sessionId) {
    // 资料会进入这个会话的上下文包，重新读一次让计数跟上。
    void loadDetail(sessionId, { render: false }).then(() => renderMargin());
  }
  if (failed.length) toast(Array.from(new Set(failed)).join('；'), 'danger');
  else toast(`已上传 ${added.length} 份`);
}

async function deleteAttachment(attachmentId) {
  const sessionId = state.currentSession;
  if (!sessionId) return;
  try {
    await api.deleteAttachment(sessionId, attachmentId);
    state.attachments[sessionId] = (state.attachments[sessionId] || [])
      .filter((item) => item.attachment_id !== attachmentId);
    renderAttachmentStrip();
    setTimeout(() => {
      if (state.currentSession === sessionId) void loadAttachments(sessionId);
    }, 400);
    toast('已移除这份资料');
  } catch (err) {
    toast(err.message, 'danger');
  }
}

/**
 * `mou-form` 提交：把控件里的 bool/string 拼成**一条完整消息**发回模型。
 *
 * 为什么不是本地直接改状态：表单回答是"用户此刻的选择"，它必须进入对话
 * 才能被模型看见并继续推理。前端只负责**结构化采集**，不替模型做决定。
 */
function submitMouForm(button) {
  const formEl = button.closest('.mou-form');
  if (!formEl || formEl.classList.contains('is-submitted')) return;
  let spec = {};
  try { spec = JSON.parse(formEl.dataset.mouForm || '{}'); } catch { spec = {}; }
  const fields = Array.isArray(spec.fields) ? spec.fields : [];
  const inputs = Array.from(formEl.querySelectorAll('[data-mou-key]'));
  const byKey = new Map(inputs.map((el) => [el.dataset.mouKey, el]));

  const lines = [];
  for (const field of fields) {
    const el = byKey.get(String(field.key));
    if (!el) continue;
    const label = String(field.label || field.key || '');
    if (field.type === 'bool') {
      lines.push(`- ${label}：${el.checked ? '是' : '否'}`);
    } else {
      const value = String(el.value || '').trim();
      lines.push(`- ${label}：${value || '（未填写）'}`);
    }
  }
  if (!lines.length) {
    toast('表单为空', 'danger');
    return;
  }
  const title = String(spec.title || '').trim();
  const text = `【表单回答${title ? ' · ' + title : ''}】\n${lines.join('\n')}\n`
    + '请根据这些回答继续；它们是我此刻的选择，不要再重复问同样的问题。';

  formEl.classList.add('is-submitted');
  inputs.forEach((el) => { el.disabled = true; });
  button.disabled = true;
  button.textContent = '已提交';
  formEl.insertAdjacentHTML('beforeend', '<div class="mou-resolved">已作为一条消息发回</div>');

  if (formEl.closest('#side-note')) {
    const input = $('#sn-input');
    if (input) input.value = text;
    void sendSideNote();
    return;
  }
  setView('chat');
  sendFromComposer({ programmaticText: text });
}

/**
 * `mou-plan` 确认：把草案原文作为**用户确认**发回模型，由它调用
 * `update_task_plan` 真正写入。
 *
 * 前端不直接写计划：计划的版本、步骤 id、检查点归属都由工具层负责；
 * 在这里复制一份写入逻辑，等于让同一条规矩有两个实现，迟早分叉。
 */
function submitMouPlan(button) {
  const card = button.closest('.mou-plan');
  if (!card || card.classList.contains('is-resolved')) return;
  const specText = card.dataset.mouPlan || '{}';
  const note = String(card.querySelector('[data-mou-note]')?.value || '').trim();
  const text = '我确认这份计划草案，请现在调用 `update_task_plan` 把它写入本会话：\n'
    + `\`\`\`mou-plan\n${specText}\n\`\`\`\n`
    + (note ? `补充要求：${note}\n` : '')
    + '这是我点「写入计划」确认的，不需要再问我。';

  card.classList.add('is-resolved');
  button.disabled = true;
  button.textContent = '已提交';
  card.insertAdjacentHTML('beforeend', '<div class="mou-resolved">已确认 · 等模型写入并回报版本</div>');

  if (card.closest('#side-note')) {
    toast('侧记不支持改计划', 'danger');
    return;
  }
  setView('chat');
  sendFromComposer({ programmaticText: text });
}

function dismissMouPlan(button) {
  const card = button.closest('.mou-plan');
  if (!card || card.classList.contains('is-resolved')) return;
  card.classList.add('is-dismissed');
  card.querySelector('.mou-plan-actions')?.remove();
  card.insertAdjacentHTML('beforeend', '<div class="mou-resolved">未采用 · 原计划不变</div>');
  rememberDismissedPlan(card);
  toast('未写入');
}

/* ── 刷新后保持「已提交 / 未采用」的状态 ────────────────────────────────
 *
 * 表单与草案是**消息内容**，刷新后会重新渲染成可交互的样子。
 * 判断依据放在消息流里：提交表单会产生一条 `【表单回答】` 的用户消息；
 * 确认草案会产生一条 `我确认这份计划草案` 的用户消息。
 * 「先不改」不发消息，所以把它的选择记在 localStorage（纯界面状态）。
 */
const DISMISSED_KEY = 'studyflow.dismissedPlans.v1';
const LEGACY_DISMISSED_KEY = 'mouxue.dismissedPlans.v1';

function dismissedSet() {
  try {
    return new Set(JSON.parse(
      localStorage.getItem(DISMISSED_KEY) || localStorage.getItem(LEGACY_DISMISSED_KEY) || '[]',
    ));
  } catch {
    return new Set();
  }
}

function rememberDismissedPlan(card) {
  const key = planCardKey(card);
  if (!key) return;
  try {
    const set = dismissedSet();
    set.add(key);
    localStorage.setItem(DISMISSED_KEY, JSON.stringify(Array.from(set)));
  } catch {
    /* storage may be unavailable */
  }
}

function planCardKey(card) {
  const msg = card.closest('[data-msg-id]');
  const title = card.querySelector('.mou-plan-head strong')?.textContent || '';
  if (!msg || !title) return '';
  const scope = card.closest('#side-note') ? 'sidebar' : (state.currentSession || 'chat');
  return `${scope}::${msg.dataset.msgId || ''}::${title}`;
}

function markFormSubmitted(formEl) {
  formEl.classList.add('is-submitted');
  formEl.querySelectorAll('[data-mou-key]').forEach((el) => { el.disabled = true; });
  const button = formEl.querySelector('[data-action="submit-mou-form"]');
  if (button) { button.disabled = true; button.textContent = '已提交'; }
  if (!formEl.querySelector('.mou-resolved')) {
    formEl.insertAdjacentHTML('beforeend', '<div class="mou-resolved">已作为一条消息发回</div>');
  }
}

function markPlanResolved(card, dismissed = false) {
  if (dismissed) {
    card.classList.add('is-dismissed');
    card.querySelector('.mou-plan-actions')?.remove();
    if (!card.querySelector('.mou-resolved')) {
      card.insertAdjacentHTML('beforeend', '<div class="mou-resolved">未采用 · 原计划不变</div>');
    }
    return;
  }
  card.classList.add('is-resolved');
  const button = card.querySelector('[data-action="submit-mou-plan"]');
  if (button) { button.disabled = true; button.textContent = '已提交'; }
  if (!card.querySelector('.mou-resolved')) {
    card.insertAdjacentHTML('beforeend', '<div class="mou-resolved">已确认 · 等模型写入并回报版本</div>');
  }
}

/** 在渲染完之后，按后续消息把已经处理过的表单/草案标回「已处理」。 */
function markInteractionStates(container, assistantSelector, userSelector) {
  if (!container) return;
  const nodes = Array.from(container.children);
  const dismissed = dismissedSet();
  nodes.forEach((node, index) => {
    if (!node.matches(assistantSelector)) return;
    const laterText = nodes
      .slice(index + 1)
      .filter((later) => later.matches(userSelector))
      .map((later) => later.textContent || '')
      .join('\n');
    for (const form of node.querySelectorAll('.mou-form:not(.is-submitted)')) {
      const title = form.querySelector('.mou-form-head strong')?.textContent || '';
      if (laterText.includes('【表单回答') && (!title || laterText.includes(title))) {
        markFormSubmitted(form);
      }
    }
    for (const card of node.querySelectorAll('.mou-plan:not(.is-resolved):not(.is-dismissed)')) {
      const title = card.querySelector('.mou-plan-head strong')?.textContent || '';
      const confirmed = laterText.includes('我确认这份计划草案')
        && (!title || laterText.includes(title));
      if (confirmed) markPlanResolved(card);
      else if (dismissed.has(planCardKey(card))) markPlanResolved(card, true);
    }
  });
}

/* ── 自评单例（全屏）────────────────────────────────────────────── */
async function loadSelfAssessment({ render = true } = {}) {
  try {
    const latest = await api.selfAssessmentLatest();
    state.selfAssessment.latest = latest || null;
  } catch (err) {
    state.selfAssessment.latest = null;
  }
  state.selfAssessment.loading = false;
  if (render) renderSelfAssessmentEntry();
}

/** 基准页顶部的自评入口：有结果就显示上一次的日期与条数。 */
function tierLabel(tier) {
  if (tier === 'short') return '简短版 · 90 条';
  if (tier === 'full') return '全面版 · 127 条';
  return '旧版 · 44 条';
}

function renderSelfAssessmentEntry() {
  const facts = $('#sa-entry-facts');
  const link = $('#sa-result-link');
  if (!facts) return;
  const latest = state.selfAssessment.latest;
  if (!latest) {
    facts.innerHTML = '<span class="sa-fact">尚未做过</span>';
    if (link) link.hidden = true;
    return;
  }
  const when = latest.finished_at ? shortTime(latest.finished_at) : '—';
  const flags = latest.flags || [];
  facts.innerHTML = [
    `<span class="sa-fact">最近一次 <b>${escapeHtml(when)}</b></span>`,
    `<span class="sa-fact">${escapeHtml(tierLabel(latest.tier))}</span>`,
    `<span class="sa-fact">${(latest.readings || []).length} 个变量</span>`,
    `<span class="sa-fact${flags.length ? ' is-warn' : ''}">${escapeHtml(flagSummary(flags))}</span>`,
  ].join('');
  if (link) link.hidden = false;
}

/* ── 全屏层 · 焦点纪律 ───────────────────────────────────────────────
 * `role="dialog" aria-modal="true"` 只是**声明**。声明之后浏览器不会替你做任何事：
 * 实测（2026-11-08）打开自评后 `activeElement` 仍是 BODY，连按三次 Tab
 * 焦点落到了侧栏的「新领域」—— 一个"模态"被自己的侧栏穿了过去。
 *
 * 这里补三件事，缺一不可：
 * 1. `inert` 把背景真正封住（焦点、点击、读屏都进不去）；
 * 2. 打开时把焦点送到层内（否则第一下 Tab 从文档开头数起）；
 * 3. 关闭时把焦点还给**打开它的那个按钮**（否则键盘用户被扔回页首）。
 */
let modalReturnFocus = null;

function openModalLayer(root, { focusSelector } = {}) {
  modalReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  for (const el of [$('#app'), $('#side-note')]) {
    if (el) el.inert = true;
  }
  root.inert = false;
  root.hidden = false;
  // 先让 DOM 生效再移焦：隐藏节点里的元素 `.focus()` 是空操作。
  requestAnimationFrame(() => {
    const target = (focusSelector && root.querySelector(focusSelector))
      || root.querySelector('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    target?.focus();
  });
}

function closeModalLayer(root) {
  root.hidden = true;
  for (const el of [$('#app'), $('#side-note')]) {
    if (el) el.inert = false;
  }
  const back = modalReturnFocus;
  modalReturnFocus = null;
  if (back && document.contains(back)) back.focus();
}

/** Tab 在层内循环：没有这一步，`inert` 之外仍可能把焦点送到浏览器地址栏后丢上下文。 */
function trapTabIn(root, event) {
  if (event.key !== 'Tab') return;
  const focusables = Array.from(
    root.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'),
  ).filter((el) => !el.disabled && el.getClientRects().length > 0);
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

async function openSelfAssessment(tier = 'full') {
  const root = $('#self-assessment');
  if (!root) return;
  if (!state.selfAssessment.scales[tier]) {
    try {
      state.selfAssessment.scales[tier] = await api.selfAssessmentScale(tier);
    } catch (err) {
      toast(err.message, 'danger');
      return;
    }
  }
  mountSelfAssessment({
    root,
    scale: state.selfAssessment.scales[tier],
    onSubmit: (payload) => api.submitSelfAssessment(payload),
    onError: (err) => toast(err.message, 'danger'),
    onClose: () => {
      document.body.classList.remove('is-assessing');
      closeModalLayer(root);
      void loadSelfAssessment();
      state.cognition.loaded = false;
      refreshCognitionIfVisible();
    },
  });
  document.body.classList.add('is-assessing');
  openModalLayer(root, { focusSelector: '[data-sa="close"]' });
}

/** 有效性标记 → 一句话。逐条列一遍会把入口撑成一段日志。 */
function flagSummary(flags) {
  if (!flags.length) return '无有效性标记';
  const straight = flags.includes('straight_lining');
  const reverse = flags.filter((f) => f.startsWith('acquiescence:')).length;
  const parts = [];
  if (straight) parts.push('直线作答');
  if (reverse) parts.push(`反向不一致 ${reverse} 项`);
  if (flags.includes('too_fast')) parts.push('过快');
  if (flags.includes('too_slow')) parts.push('过慢');
  return parts.length ? `置信下调：${parts.join(' · ')}` : flags.join('、');
}

function showSelfAssessmentResult() {
  const latest = state.selfAssessment.latest;
  if (!latest) {
    toast('你还没做过自评，先做一次就能看到结果了。');
    return;
  }
  const rows = (latest.readings || [])
    .map((row) => {
      const facets = Object.entries(row.facets || {});
      const tail = facets.length
        ? `\n    ${facets.map(([name, value]) => `${name} ${value}`).join(' · ')}`
        : '';
      return `${row.variable_name}（${row.variable_id}）${row.score}${tail}`;
    })
    .join('\n');
  openPopover($('#sa-result-link'), `
    <div class="popover-label">自评 · ${escapeHtml(latest.run_id)} · ${escapeHtml(tierLabel(latest.tier))}</div>
    <pre class="sa-result-pop">${escapeHtml(rows || '（没有读数）')}</pre>
    ${(latest.flags || []).length
      ? `<p class="pop-note">${escapeHtml(flagSummary(latest.flags))}</p>`
      : ''}
  `, 'popover-wide');
}

function refreshCognitionIfVisible() {
  if (state.view === 'cognition') { state.cognition.loaded = false; void loadCognition(); }
}

/* ── 自评页 ────────────────────────────────────────────────────────── */
/**
 * 这一页**只有自评**。基准测试（两套卷、领域勾选、三通道读数）已整体下线 ——
 * 给材料、计时、判对错的卷面，测到的是"做这套题的能力"；
 * 而这里要回答的是"你平时怎么学"，那是自评能回答的。
 *
 * 三通道读数的渲染也一并移除：没有 measured 这一列之后，
 * 在这个页面上再摆三个空格子只会让人以为"还有东西没填"。
 * 自述读数本身在「认知」页的变量表里看得到。
 */
function renderBench() {
  renderSelfAssessmentEntry();
  renderRail();
  translateUi();
  if (!state.selfAssessment.loading && !state.selfAssessment.latest) {
    state.selfAssessment.loading = true;
    void loadSelfAssessment({ render: true });
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
  if (composerInput) composerInput.placeholder = L('写明基础、目的与障碍。', 'State foundation, purpose and obstacles.');
  const snInput = $('#sn-input');
  if (snInput) snInput.placeholder = L('描述方法或障碍。', 'Describe a method or obstacle.');
  renderRail();
  renderTopbar();
  // 换语言只换字：不重播整屏的入场动画。
  renderThread({ animate: false });
  renderMargin();
  if (state.view === 'settings') renderSettings();
  if (state.view === 'cognition') renderCognition();
  if (state.view === 'benchmark') renderBench();
  translateUi();
}

function setTheme(theme) {
  state.theme = theme;
  document.documentElement.dataset.theme = theme;
  $$('.theme-swatch').forEach((el) => el.classList.toggle('is-on', el.dataset.themeChoice === theme));
  // Canvas 元素自己不会跟着 CSS 变量变 —— 换纸色后重画一次。
  requestAnimationFrame(() => {
    renderContextRings();
    renderThreadIndex();
  });
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
      // 用户亲手按了开关：这次的开关状态归用户，不再受视口回退影响。
      state.marginForced = false;
      syncMarginScrim();
      syncMarginToggle();
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
        // 有选中的会话时焦点留在那个会话；只有当前没有会话或点到空领域时
        // 才把这个领域设为当前，避免两处同时“亮着”。
        if (!state.currentSession || !folder.sessions.length) {
          state.currentFolder = folder.id;
        }
        if (!folder.sessions.length) {
          state.currentSession = null;
          showFolderEmpty(folder);
        }
        renderRail();
        renderTopbar();
      }
      break;
    }
    case 'new-session-in-folder': {
      const folder = findFolder(id);
      if (!folder) break;
      event.stopPropagation();
      folder.open = true;
      renderRail();
      openInlineForm('session', folder.id);
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
      activateProfile(id, el.dataset.model || null);
      break;
    case 'fetch-profile-models':
      fetchProfileModels(el);
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
            toast('已重命名');
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
            toast('已重命名');
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
      void runRailAction('已删除', () => api.deleteSession(id));
      break;
    case 'restore-folder':
      restoreFolder(id);
      break;
    case 'delete-archived-folder':
      closePopover();
      void runRailAction('已删除', () => api.deleteFolder(id));
      break;
    case 'toggle-step':
      void toggleStep(el.dataset.stepId);
      break;
    case 'discuss-step':
      discussStep(el.dataset.stepId);
      break;
    case 'clear-anchor':
      clearAnchor();
      break;
    case 'send':
      sendFromComposer();
      break;
    case 'stop':
      cancelRun({ interrupted: true });
      break;
    case 'replay':
      void loadDetail(state.currentSession).catch((err) => toast(err.message, 'danger'));
      toast('已刷新');
      break;
    case 'approve-proposal':
      resolveProposal(el.dataset.id, 'approved');
      toast('已写入');
      break;
    case 'reject-proposal':
      resolveProposal(el.dataset.id, 'rejected');
      toast('未写入');
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
      toast('选中一句话即可标注');
      break;
    case 'edit-motivation':
      openMotivationForm(el);
      break;
    case 'motivation-history':
      toast('只读');
      break;
    case 'ask-plan':
      composerInput.value = '这个目标我还没有拆过，先跟我确认一下从哪里开始。';
      composerInput.focus();
      autoGrow();
      break;
    case 'refresh-context':
      if (!state.currentSession) break;
      void loadDetail(state.currentSession)
        .then(() => toast('已刷新'))
        .catch((err) => toast(err.message, 'danger'));
      break;
    case 'open-context':
      state.marginTab = 'context';
      workspace.classList.remove('is-margin-closed');
      $('#margin-toggle')?.classList.add('is-on');
      $('#margin-toggle')?.setAttribute('aria-pressed', 'true');
      renderMargin();
      break;
    case 'attach-file':
      $('#attach-input')?.click();
      break;
    case 'delete-attachment':
      void deleteAttachment(id);
      break;
    case 'open-self-assessment':
      openSelfAssessment(el.dataset.tier === 'short' ? 'short' : 'full');
      break;
    case 'view-self-assessment-result':
      showSelfAssessmentResult();
      break;
    case 'refresh-cognition':
      state.cognition.loaded = false;
      void loadCognition();
      break;
    case 'state-menu':
      openStateMenu(el);
      break;
    case 'go-state':
      goState(el.dataset.target);
      break;
    case 'open-side-note':
      $('#side-note').classList.add('is-open');
      renderSideNote();
      if (!state.sidebar.loaded) void loadSidebar();
      setTimeout(() => $('#sn-input')?.focus(), 60);
      break;
    case 'close-side-note':
      $('#side-note').classList.remove('is-open');
      break;
    case 'refresh-sidebar':
      void loadSidebar();
      break;
    case 'sn-send':
      sendSideNote();
      break;
    case 'approve-proposals':
      void resolveArchiveProposals(true);
      break;
    case 'reject-proposals':
      void resolveArchiveProposals(false);
      break;
    case 'submit-mou-form':
      submitMouForm(el);
      break;
    case 'submit-mou-plan':
      submitMouPlan(el);
      break;
    case 'dismiss-mou-plan':
      dismissMouPlan(el);
      break;
    case 'probe':
      probeProfile(id, el);
      break;
    case 'set-think': {
      const profile = state.profiles.find((p) => p.id === id);
      if (!profile) break;
      const value = el.dataset.value;
      if (value === 'auto') {
        // 后端把 `think: null` 当"不改"，所以「自动」只能在新建时设定。
        // 不假装切过去了 —— 假装的话，下一轮会照旧发送 think 字段。
        toast('自动只能新建时设定', 'danger');
        break;
      }
      updateProfile(id, { think: value === 'true' });
      break;
    }
    case 'add-profile':
      openProfileForm(null, el);
      break;
    case 'edit-profile':
      openProfileForm(state.profiles.find((p) => p.id === id) || null, el);
      break;
    case 'delete-profile': {
      const target = state.profiles.find((p) => p.id === id);
      if (!target) break;
      askConfirm({
        title: '删除模型档案',
        body: `「${target.label}」（${target.model}）会被删掉${target.active ? '，它是当前生效的那一份 —— 删掉之后会自动接上另一份或退回 .env' : ''}。`,
        ok: '删除',
        onOk: async () => {
          try {
            const result = await api.llmDeleteProfile(id);
            state.profiles = state.profiles.filter((p) => p.id !== id);
            if (state.activeProfile === id) {
              state.activeProfile = result.active_profile_id || null;
              for (const p of state.profiles) p.active = p.id === state.activeProfile;
            }
            renderSettings();
            renderTopbar();
            renderRail();
            updateBusyUI();
            const next = result.active_profile_id
              ? state.profiles.find((p) => p.id === result.active_profile_id)
              : null;
            toast('已删除');
          } catch (err) {
            toast(err.message, 'danger');
          }
        },
      });
      break;
    }
    case 'list-models':
      // `handleAction` 是同步的，所以真正的异步动作放在单独的 helper 里 ——
      // 在这里 `await` 会让整个函数变成 async，而那会把调用方的同步语义
      // （事件绑定、渲染顺序）一起改掉。
      listProfileModels(id, el);
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

/**
 * 拉一份档案的模型清单（`POST /models`）。
 *
 * 后端说明：拉不到也**算成功**（`models` 为空 + `note` 是人话解释）——
 * 因为"这家服务问不出清单"不是用户的请求错了。所以这里把 `note` 原样说出来，
 * 而不是显示一句"失败"。
 */
async function listProfileModels(id, button) {
  if (button) button.disabled = true;
  try {
    const got = await api.llmListModels(id);
    const models = got.models || [];
    state.listedModels[id] = models;
    if (models.length) {
      toast('已拉取模型清单');
      const profile = state.profiles.find((p) => p.id === id) || null;
      openProfileForm(profile, button);
    } else {
      toast(got.note || '无法获取模型清单', 'danger');
    }
  } catch (err) {
    toast(err.message, 'danger');
  } finally {
    if (button) button.disabled = false;
  }
}

/**
 * 启用一份档案里的某个模型。**这是真的切换**：`POST /activate` 之后下一轮
 * 讨论就用它，所以这里必须等后端回话，不能只改界面上的一个变量。
 *
 * ★ `model` 与档案一起提交，一次点击就落库。分成两步（先启档案、再选模型）
 * 的界面会让人点两次，而中间那一下"启用了、但用的还是旧模型"看起来像没生效。
 */
async function activateProfile(id, model = null) {
  if (state.busy) {
    toast('生成中，无法切换', 'danger');
    closePopover();
    return;
  }
  const profile = state.profiles.find((p) => p.id === id);
  const target = model || (profile ? profile.model : null);
  if (profile && profile.active && target === profile.model) {
    closePopover();
    toast('已在使用');
    return;
  }
  try {
    const row = await api.llmActivateProfile(id, target);
    closePopover();
    // 用响应行更新，而不是写完立刻重读 —— 提交在响应之后，重读会撞上一版。
    applyProfileRow(row);
    renderSettings();
    renderTopbar();
    renderRail();
    updateBusyUI();
    // 窗口上限来自模型：换了模型，这个环的刻度也可能变。重读一次。
    if (state.currentSession) {
      void loadDetail(state.currentSession, { render: false }).then(() => renderContextRings());
    }
    toast('已切换');
  } catch (err) {
    toast(err.message, 'danger');
  }
}

/**
 * 改一份档案。**发整份**字段：后端 `update_profile` 是覆盖写，
 * 只发改动的那几个会把 `note`/`num_ctx`/`base_url` 一起抹掉。
 */
async function updateProfile(id, changes) {
  const current = state.profiles.find((p) => p.id === id);
  if (!current) return;
  try {
    const row = await api.llmUpdateProfile(id, {
      label: current.label,
      provider_id: current.providerId,
      model: current.model,
      models: current.models,
      base_url: current.base,
      num_ctx: current.numCtx || null,
      note: current.note,
      ...changes,
    });
    applyProfileRow(row);
    renderSettings();
  } catch (err) {
    toast(err.message, 'danger');
  }
}

/**
 * 探活：真的去问端点一次。
 *
 * 原来是 `setTimeout(1200)` 之后拿 `Math.random()` 编一个延迟和模型数，
 * 再 `toast('探活成功')` —— 那一整块代码里没有一个字来自后端。
 * 现在慢就是慢（本地大模型首次要进显存），失败就把后端那句话原样显示。
 */
async function probeProfile(id, button) {
  const result = $('#probe-result');
  button.disabled = true;
  if (result) {
    result.textContent = '探活中 · 真的发一次请求（本地模型首次加载可能要十几秒）…';
    result.classList.add('is-pending');
  }
  try {
    const probe = await api.llmProbe(id);
    state.probeText[id] = `${probe.ok ? '✓' : '✗'} ${probe.detail}`;
    if (result) result.textContent = state.probeText[id];
    toast(probe.ok ? '可用' : '不可用', probe.ok ? '' : 'danger');
  } catch (err) {
    state.probeText[id] = `✗ ${err.message}`;
    if (result) result.textContent = state.probeText[id];
    toast(err.message, 'danger');
  } finally {
    button.disabled = false;
    if (result) result.classList.remove('is-pending');
  }
}

/**
 * 侧记：**真的和常驻助手说话**（`POST /api/sidebar/discuss`）。
 *
 * 原来这里走的是 `POST /sessions/{id}/notes`，然后把后端的笔记编号
 * 包装成一句"已存为笔记"。那是把一次**对话**做成了归档动作 ——
 * 无论用户说什么，回答都一样，而侧记真正的用途（讨论方法、接住感受、
 * 提议整理进全局认知）一次都没发生过。
 *
 * 现在：没有当前会话也能聊；回复来自模型；工具轨迹、提案、上下文读数
 * 都按服务端返回的显示。只有模型**自己判断**值得留下时才提提案。
 */
function adaptSidebarMessage(row) {
  const time = shortTime(row.created_at);
  if (row.role === 'user') {
    return { id: row.msg_id, role: 'user', time, text: row.content };
  }
  const stopTag = (row.tags || []).find((tag) => String(tag).startsWith('stop:'));
  return {
    id: row.msg_id,
    role: 'assistant',
    time,
    html: richText(row.content),
    thinking: row.thinking || null,
    traces: traceRows(row.tool_trace),
    stopReason: stopTag ? String(stopTag).slice(5) : 'completed',
  };
}

async function loadSidebar({ render = true } = {}) {
  const bar = state.sidebar;
  if (bar.loading) return;
  bar.loading = true;
  if (render) renderSideNote();
  try {
    const payload = await api.sidebarState();
    bar.messages = (payload.messages || []).map(adaptSidebarMessage);
    bar.context = adaptContext(payload.context);
    bar.tools = payload.tools || [];
    bar.sections = payload.sections || [];
    bar.loaded = true;
    bar.error = '';
  } catch (err) {
    bar.error = err.message;
  } finally {
    bar.loading = false;
    if (render) renderSideNote();
  }
}

function sidebarMessageMarkup(message) {
  if (message.role === 'user') {
    return `<div class="sn-msg" data-msg-id="${escapeHtml(message.id || '')}">
      <span class="sn-role">${L('你', 'You')}</span><p>${escapeHtml(message.text || '')}</p>
    </div>`;
  }
  const stopLabel = ({
    completed: '',
    llm_error: '模型错误',
    internal_error: '出错',
    max_steps: '步数用尽',
    client_disconnected: '中断了',
  })[message.stopReason] || '';
  return `<div class="sn-msg from-bot${stopLabel ? ' is-error' : ''}" data-msg-id="${escapeHtml(message.id || '')}">
    <span class="sn-role">${L('档', 'SF')}</span>
    <div class="sn-prose">${message.html || ''}</div>
    ${stopLabel ? `<span class="sn-stop">${stopLabel}</span>` : ''}
    ${traceMarkup(message.traces)}
  </div>`;
}

function renderSideNote() {
  const bar = state.sidebar;
  const threadEl = $('#sn-thread');
  if (!threadEl) return;

  if (bar.loading && !bar.messages.length) {
    threadEl.innerHTML = '<div class="sn-empty">正在读侧记…</div>';
  } else if (bar.error && !bar.messages.length) {
    threadEl.innerHTML = `<div class="sn-empty is-error">侧记读不到：${escapeHtml(bar.error)}</div>`;
  } else if (!bar.messages.length) {
    threadEl.innerHTML = `<div class="sn-empty">
      <span class="sn-empty-seal">${icon('archive-mark')}</span>
      <p>侧记不绑定会话。可讨论方法，或提议修改全局认知；写入需确认。</p>
    </div>`;
  } else {
    threadEl.innerHTML = bar.messages.map(sidebarMessageMarkup).join('');
  }

  const meter = bar.context || adaptContext(null);
  const tokens = $('#sn-tokens');
  if (tokens) {
    tokens.textContent = meter.measured
      ? `${meter.used} / ${meter.total}`
      : (meter.hasReading ? `${meter.used} tokens` : `— / ${meter.total}`);
  }
  const barEl = $('#sn-meter-bar i');
  if (barEl) barEl.style.setProperty('--p', `${meter.measured ? meter.pct : 0}%`);
  const note = $('#sn-meter-note');
  if (note) {
    note.textContent = meter.measured
      ? L(
        `已发出 ${meter.liveMessages} 条 · ${meter.limitSource === 'budget' ? '按配置预算 ' : ''}${meter.pct}%`,
        `Sent ${meter.liveMessages} · ${meter.limitSource === 'budget' ? 'budget ' : ''}${meter.pct}%`,
      )
      : L(`已发出 ${meter.liveMessages} 条`, `Sent ${meter.liveMessages}`);
  }
  const input = $('#sn-input');
  const send = $('[data-action="sn-send"]');
  if (input) input.disabled = bar.busy;
  if (send) send.disabled = bar.busy;
  const busy = $('#sn-busy');
  if (busy) busy.hidden = !bar.busy;
  markInteractionStates(threadEl, '.sn-msg.from-bot', '.sn-msg:not(.from-bot)');
  threadEl.scrollTop = threadEl.scrollHeight;
  translateUi(threadEl);
  translateUi($('#side-note'));
}

async function sendSideNote() {
  const input = $('#sn-input');
  const text = input.value.trim();
  const bar = state.sidebar;
  if (!text || bar.busy) return;
  input.value = '';
  bar.busy = true;
  bar.messages.push({ id: `local-${Date.now()}`, role: 'user', time: clock(), text });
  renderSideNote();

  const pending = document.createElement('div');
  pending.className = 'sn-msg from-bot is-pending';
  pending.innerHTML = `<span class="sn-role">${L('档', 'SF')}</span><div class="sn-prose"><span class="ink-loader"><i></i><i></i><i></i></span></div>`;
  $('#sn-thread').appendChild(pending);
  $('#sn-thread').scrollTop = $('#sn-thread').scrollHeight;

  try {
    const result = await api.sidebarDiscuss(text);
    bar.messages.push(adaptSidebarMessage({
      msg_id: `local-a-${Date.now()}`,
      role: 'assistant',
      content: result.reply,
      tags: [`stop:${result.stop_reason}`],
      tool_trace: result.tool_trace,
      thinking: result.thinking,
      created_at: new Date().toISOString(),
    }));
    if (result.context) bar.context = adaptContext(result.context);
    bar.error = '';
  } catch (err) {
    pending.remove();
    bar.busy = false;
    renderSideNote();
    toast(err.message, 'danger');
    return;
  }
  bar.busy = false;
  renderSideNote();
}

function openInlineForm(kind, folderId = null) {
  closePopover();
  state.inlineKind = kind;
  state.inlineFolderId = kind === 'session' ? folderId : null;
  const form = $('#rail-form');
  const label = $('#rail-form-label');
  const input = $('#rail-form-input');
  const folder = state.inlineFolderId ? findFolder(state.inlineFolderId) : null;
  label.textContent = kind === 'folder'
    ? '新领域 · 名称'
    : `新会话${folder ? ' · ' + folder.name : ''}`;
  input.placeholder = kind === 'folder' ? '例如：物理学 · 概念梳理' : '例如：第 2 章的概念地图';
  form.hidden = false;
  input.value = '';
  input.focus();
}

function closeInlineForm() {
  state.inlineKind = null;
  state.inlineFolderId = null;
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
      await wait(200);
      await loadWorkspace();
      closeInlineForm();
      renderRail();
      toast('已创建');
      return;
    }

    // 会话必须落在某个领域里（库里有 NOT NULL 的 folder_id）。
    // 一个领域都没有时先建「未归类」—— 界面不假装存在一个不存在的归属。
    let folder = findFolder(state.inlineFolderId) || findFolder(state.currentFolder) || state.folders[0];
    if (!folder) {
      await api.createFolder('未归类');
      await wait(200);
      await loadWorkspace();
      folder = state.folders[0];
    }
    const created = await api.createSession(folder.id, value);
    await wait(200);
    await loadWorkspace();
    closeInlineForm();
    state.currentSession = created.session_id;
    state.currentFolder = created.folder_id;
    state.details[created.session_id] = makeDetail();
    renderSession();
    await loadDetail(created.session_id);
    toast('已创建');
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
  const quickAsk = event.target.closest('.quick-ask');
  if (quickAsk) {
    const prompt = quickAsk.dataset.prompt || quickAsk.textContent.trim();
    if (quickAsk.closest('#side-note')) {
      const input = $('#sn-input');
      if (input) input.value = prompt;
      sendSideNote();
    } else {
      composerInput.value = prompt;
      autoGrow();
      sendFromComposer();
    }
    return;
  }
  const stepRef = event.target.closest('.step-ref');
  if (stepRef) {
    jumpToStep(stepRef.dataset.stepId);
    return;
  }
  const threadJump = event.target.closest('[data-thread-jump]');
  if (threadJump) {
    jumpToMessage(threadJump.dataset.threadJump);
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
    toast(state.lang === 'zh' ? '中文' : 'English');
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
    toast(t('busy.' + state.busyBehavior));
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
    toast(state.theme === 'paper' ? '宣纸' : '夜墨');
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
});

document.addEventListener('pointerdown', (event) => {
  if (!popLayer.contains(event.target) && !event.target.closest('[data-popover-anchor]')) closePopover();
}, true);

document.addEventListener('keydown', (event) => {
  // 全屏层开着时，Tab 必须在层内循环，其余全局快捷键一律让位给层。
  const modal = $('#self-assessment').hidden ? null : $('#self-assessment');
  if (modal) {
    trapTabIn(modal, event);
    if (event.key === 'Escape') return; // 由挂载方自己处理（它要保存草稿）
    if (event.key !== 'Tab') return;
  }
  if (event.key === 'Escape') {
    if (popLayer.innerHTML) { closePopover(); return; }
    if ($('#side-note').classList.contains('is-open')) { $('#side-note').classList.remove('is-open'); return; }
    document.body.classList.remove('rail-open');
  }
  // 键盘激活：**不白名单**。任何被写成按钮的东西都必须能被 Enter/Space 按下。
  // 原先是 `.folder-head / .session` 两个选择器的白名单，于是
  // `#session-state`（role=button）与 `.step-check`（role=button 的 span）
  // 能聚焦、能 Tab 到，但按下没反应 —— 这是「看起来是按钮」最坏的一种。
  const tag = document.activeElement?.tagName;
  if ((event.key === 'Enter' || event.key === ' ') && tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'BUTTON') {
    const target = document.activeElement;
    if (target?.matches('[role="button"], [data-action], [tabindex="0"]')) {
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

// ── 资料上传：点按钮、拖进输入区、粘贴文件，三条路走同一个函数 ──────────
$('#attach-input')?.addEventListener('change', (event) => {
  const files = Array.from(event.target.files || []);
  event.target.value = '';
  void uploadFiles(files);
});

['dragenter', 'dragover'].forEach((type) => {
  composer.addEventListener(type, (event) => {
    if (!event.dataTransfer?.types?.includes('Files')) return;
    event.preventDefault();
    composer.classList.add('is-drop');
  });
});
['dragleave', 'drop'].forEach((type) => {
  composer.addEventListener(type, (event) => {
    if (type === 'dragleave' && composer.contains(event.relatedTarget)) return;
    composer.classList.remove('is-drop');
  });
});
composer.addEventListener('drop', (event) => {
  if (!event.dataTransfer?.files?.length) return;
  event.preventDefault();
  void uploadFiles(event.dataTransfer.files);
});
composerInput.addEventListener('paste', (event) => {
  const files = Array.from(event.clipboardData?.files || []);
  if (!files.length) return;
  event.preventDefault();
  void uploadFiles(files);
});

threadWrap.addEventListener('click', (event) => {
  const step = event.target.closest('[data-action="toggle-step"]');
  if (step) toggleStep(step.dataset.stepId);
});

threadWrap.addEventListener('scroll', scheduleThreadIndexViewport, { passive: true });
// resize 一次能做两件重活：重建索引（读一遍 offsetTop）与重算断点。
// 两者都用 rAF 合并 —— 拖动窗口时每个事件都跑一遍会强制同步布局。
window.addEventListener('resize', () => {
  scheduleBreakpoint();
  scheduleThreadIndexViewport();
  scheduleThreadIndexRebuild();
});

const SIZES_KEY = 'studyflow.sizes.v1';
const LEGACY_SIZES_KEY = 'mouxue.sizes.v1';
const clampSize = (value, min, max) => Math.min(max, Math.max(min, value));

function readSizes() {
  try {
    return JSON.parse(
      localStorage.getItem(SIZES_KEY) || localStorage.getItem(LEGACY_SIZES_KEY) || '{}',
    ) || {};
  } catch {
    return {};
  }
}

function applySavedSizes() {
  const sizes = readSizes();
  if (Number(sizes.rail) > 0) {
    document.documentElement.style.setProperty('--rail-w', `${sizes.rail}px`);
  }
  if (Number(sizes.margin) > 0) {
    document.documentElement.style.setProperty('--margin-w', `${sizes.margin}px`);
  }
  if (Number(sizes.composer) > 0) {
    composerInput.style.height = `${sizes.composer}px`;
    composerInput.dataset.manual = '1';
  }
}

function saveSizes(partial) {
  try {
    localStorage.setItem(SIZES_KEY, JSON.stringify({ ...readSizes(), ...partial }));
  } catch {
    /* storage may be unavailable */
  }
}

function wireResizers() {
  document.addEventListener('pointerdown', (event) => {
    const handle = event.target.closest('[data-resize]');
    if (!handle) return;
    const kind = handle.dataset.resize;
    const startX = event.clientX;
    const startY = event.clientY;
    const railStart = rail.getBoundingClientRect().width;
    const marginStart = $('#margin').getBoundingClientRect().width;
    const composerStart = composerInput.getBoundingClientRect().height;
    let railNow = railStart;
    let marginNow = marginStart;
    let composerNow = composerStart;
    event.preventDefault();
    handle.classList.add('is-dragging');
    document.body.classList.add('is-resizing');

    const onMove = (moveEvent) => {
      if (kind === 'rail') {
        const width = clampSize(railStart + (moveEvent.clientX - startX), 220, Math.min(460, window.innerWidth * 0.42));
        railNow = width;
        document.documentElement.style.setProperty('--rail-w', `${width}px`);
      } else if (kind === 'margin') {
        // 上限与显示时的 `min(var(--margin-w), 42vw)` 对齐：
        // 否则用户拖到超过可视宽度，画面却不再变化。
        const width = clampSize(marginStart - (moveEvent.clientX - startX), 240, Math.min(640, window.innerWidth * 0.42));
        marginNow = width;
        document.documentElement.style.setProperty('--margin-w', `${width}px`);
      } else if (kind === 'composer') {
        const height = clampSize(composerStart + (startY - moveEvent.clientY), 34, window.innerHeight * 0.5);
        composerNow = height;
        composerInput.style.height = `${height}px`;
        composerInput.dataset.manual = '1';
      }
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      handle.classList.remove('is-dragging');
      document.body.classList.remove('is-resizing');
      // 保存**拖动结束时的目标尺寸**，而不是网格动画播到一半的测量值。
      saveSizes({
        rail: Math.round(railNow),
        margin: Math.round(marginNow),
        composer: Math.round(composerNow),
      });
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp, { once: true });
  });

  // 双击输入框上沿：回到自动高度。
  document.addEventListener('dblclick', (event) => {
    if (!event.target.closest('[data-resize="composer"]')) return;
    delete composerInput.dataset.manual;
    composerInput.style.height = '';
    autoGrow();
    saveSizes({ composer: 0 });
  });
}

function autoGrow() {
  if (composerInput.dataset.manual === '1') return;
  composerInput.style.height = 'auto';
  composerInput.style.height = `${Math.min(window.innerHeight * 0.42, composerInput.scrollHeight)}px`;
}


/* ── init ─────────────────────────────────────────────────────────── */
/* ── 字体落地检查 ───────────────────────────────────────────────────
 * 起因（2026-11-08）：`--display` 曾指向 Fraunces，实测它的 6 个 face 永远
 * 停在 `unloaded`（`document.fonts.check` 为 false），于是所有大数字**静默**
 * 退回正文衬线，三级层级塌成两级，而且不会有任何报错。
 *
 * 处置：Fraunces 已从头文件移除，数字档改由字重/字距无条件成立（见 CSS）。
 * 这里保留一个**只做记录**的探针：把每个字族是否真的可用记在 `data-fonts` 上，
 * 供排查用 —— 但它**不再决定任何布局**。层级不能挂在"某个字体恰好在"上。
 */
function probeDisplayFont() {
  if (!document.fonts) return {};
  const families = ['Noto Serif SC', 'Noto Sans SC', 'Hanken Grotesk', 'IBM Plex Mono'];
  const state_ = {};
  for (const family of families) {
    try {
      state_[family] = document.fonts.check(`16px "${family}"`);
    } catch {
      state_[family] = false;
    }
  }
  const missing = families.filter((f) => !state_[f]);
  document.documentElement.dataset.fonts = missing.length ? `missing:${missing.join(',')}` : 'ok';
  return state_;
}

function watchDisplayFont() {
  probeDisplayFont();
  if (!document.fonts) return;
  document.fonts.ready.then(() => probeDisplayFont()).catch(() => {});
}

async function boot() {
  document.documentElement.dataset.cite = 'on';
  document.documentElement.dataset.motion = state.motion;
  watchDisplayFont();
  applyBreakpoint();

  // 先把壳画出来（侧栏空、对话区空），再从后端取数据 ——
  // 反过来做的话，首屏会有一段时间是白屏
  renderRail();
  renderTopbar();
  renderThread();
  renderMargin();
  renderSettings();
  renderBench();
  updateBusyUI();
  applySavedSizes();
  wireResizers();

  try {
    // 模型档案要在画侧栏之前就位（侧栏那行写的是"现在是谁在说话"）。
    // 它读的是 `/api/llm/*`，与会话数据互不依赖，失败也不该挡住会话。
    await loadModels({ render: false });
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
    renderSettings();
    renderTopbar();
    renderRail();
    if (state.currentSession) {
      await loadDetail(state.currentSession);
      await loadAttachments(state.currentSession);
    } else ensureSelection();
  } catch (err) {
    // 后端没起来时**说清楚是哪一步没成**，而不是留一个空白界面让人以为"没有数据"
    thread.innerHTML = emptyStateMarkup({
      icon: 'close',
      title: '连不上后端',
      body: err.message || String(err),
      note: '界面上的每一条数据都来自接口；接口不通时这里不会摆一份假数据。',
    });
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
