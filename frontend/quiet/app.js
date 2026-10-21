'use strict';

/* ═══════════════════════════════════════════════════════════════════
   觅学之契 · 静室 —— static interaction prototype
   Local, fictional data. No network, no backend.
   ═══════════════════════════════════════════════════════════════════ */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const escapeHtml = (value) => String(value ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

const pad = (n) => String(n).padStart(2, '0');
const clock = () => {
  const d = new Date();
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const icon = (name, cls = '') => `<svg${cls ? ` class="${cls}"` : ''}><use href="#i-${name}"/></svg>`;

/* ── i18n ─────────────────────────────────────────────────────────── */
const I18N = {
  zh: {
    'brand.name': '觅学之契',
    'brand.sub': '静室 · 学习流档案',
    'sb.newChat': '新对话',
    'sb.searchPlaceholder': '搜索会话与领域',
    'sb.fields': '领域',
    'sb.archived': '已归档',
    'sb.benchmark': '基准测验',
    'sb.settings': '设置',
    'sb.colophon': '持久化学习流 · 每句话可回溯',
    'common.create': '创建',
    'common.cancel': '取消',
    'common.save': '保存',
    'common.rename': '重命名',
    'common.archive': '归档',
    'common.delete': '删除',
    'common.restore': '恢复',
    'state.active': '进行中',
    'state.review': '待复盘',
    'state.completed': '已完成',
    'state.draft': '草稿',
    'state.abandoned': '已放弃',
    'chat.end': '以上是本轮的全部往来 · 每一步都可回溯',
    'margin.motivation': '动机',
    'margin.plan': '计划',
    'margin.context': '上下文',
    'margin.sidenote': '侧记',
    'margin.sidenoteNote': '独立记忆 · 不改动计划',
    'composer.stop': '停止',
    'composer.sendHint': '发送',
    'composer.newlineHint': '换行',
    'composer.placeholder': '说说你正在学什么，或今天卡在哪里。',
    'composer.busyPrefix': '繁忙时 · ',
    'composer.hint': '尽量具体；不明确的地方，我会先反问。',
    'composer.attachNote': '附件尚未接入这一版界面',
    'busy.queue': '排队等待',
    'busy.queueP': '当前回答写完后，依次发送。',
    'busy.queueTag': '最不打断思路',
    'busy.interrupt': '打断并发送',
    'busy.interruptP': '立即停止生成，未完成部分标记为「已中断」。',
    'busy.interruptTag': '最快转向',
    'busy.block': '暂不允许',
    'busy.blockP': '发送键禁用，直到这一轮结束。',
    'busy.blockTag': '最不易丢上下文',
    'bench.kicker': '测量',
    'bench.pageTitle': '基准',
    'bench.demoNote': '演示数据未接入',
    'bench.title': '先量一次，<br><em>你站在哪里</em>',
    'bench.lede': '基准是推荐的路，不是准入的门。你可以先做一次简短的自述，也可以直接去对话——两条路都算数。',
    'bench.meta1': '可中断保存',
    'bench.meta2': '可继续或重开',
    'bench.meta3': '两种卷不分优劣',
    'bench.figureTitle': '三通道',
    'bench.stated': '自述',
    'bench.experienced': '经验',
    'bench.measured': '实测',
    'bench.figureNote': '没有读数时，柱子保持空白——空白本身也是答案。',
    'bench.quickTitle': '简短版',
    'bench.quickP': '一次复用任务同时观测四个变量。省时，代价是变量之间会互相沾染——置信封顶 provisional。',
    'bench.quick1': '自述轮 · 20 道以内',
    'bench.quick2': '复用行为任务 · 1 次',
    'bench.quick3': '随时保存退出',
    'bench.startQuick': '从简短版开始',
    'bench.fullTitle': '完整版',
    'bench.fullP': '独立任务加一个真实学习闭环。更慢，也更干净——measured 通道只有在它这里才可能被认真填满。',
    'bench.full1': '自述轮 + 行为轮',
    'bench.full2': '独立任务 · 互不污染',
    'bench.full3': '产出可对账的读数',
    'bench.startFull': '从完整版开始',
    'bench.domainsKicker': '领域 · 两段',
    'bench.domainsTitle': '你熟悉什么，和你想碰什么',
    'bench.domainsP': '第二段是独立的兴趣区，可以跳过，也可以随时改。唯一一条硬规矩：兴趣不能落在熟悉集合里。',
    'bench.familiar': '我熟悉的',
    'bench.interested': '我不熟悉、但想碰的',
    'bench.familiarNote': '勾选后产生一个新版本；旧版本仍然可查。',
    'bench.skip': '跳过第二段',
    'bench.skipNote': '随时回来再补。',
    'bench.readingsKicker': '读数 · 三通道',
    'bench.readingsTitle': '互不换算，也不取平均',
    'bench.readingsP': '自述、经验、实测是三种证据，各自独立。没有测到的通道保持空白，而不是给一个看起来合理的中位数。',
    'bench.reading1': '首轮自述基线已建立，逐参数读数暂未公开。',
    'bench.reading1Foot': '20 题 · 已答 7',
    'bench.reading2': '需要在真实学习回合里累积证据，基线尚未接通。',
    'bench.reading2Foot': '0 条经验证据',
    'bench.reading3': '未做行为任务，因此这里没有读数——空白本身就是答案。',
    'bench.reading3Foot': '未执行行为轮',
    'bench.emptyMark': '留白',
    'bench.emptyNote': '完整测试数据尚未暴露到这一版界面。此处刻意保留版式，不填入演示分数——一个编出来的读数，比没有读数更坏。',
    'settings.kicker': '偏好',
    'settings.title': '设置',
    'settings.back': '返回对话',
    'settings.general': '通用',
    'settings.generalNote': '语言 · 发送 · 思考',
    'settings.models': '模型',
    'settings.modelsNote': '供应商 · 探活',
    'settings.appearance': '外观',
    'settings.appearanceNote': '主题 · 字号 · 动效',
    'settings.version': '界面演示 · 非最终设计',
    'settings.langHead': '语言与发送',
    'settings.langHeadP': '这些选项只影响界面与输入行为，不会翻译你的偏好原文，也不会改写已经写下的笔记。',
    'settings.language': '界面语言',
    'settings.languageP': '切换后立即生效。偏好、笔记与对话内容保持原文——它们是你的话，不该被自动翻译。',
    'settings.busy': '繁忙时发送',
    'settings.busyP': '模型正在回答时，你又写了一句。三种处理方式各有代价，没有默认的正确。',
    'settings.reasoning': '思考过程',
    'settings.reasoningP': '思考与回答始终分开存储、分开走帧。这里只决定它默认是否展开。',
    'settings.fold': '折叠',
    'settings.open': '常显',
    'settings.citations': '依据标注',
    'settings.citationsP': '回答里引用偏好或高分参数时，在句尾留下一个可点击的标记。',
    'settings.modelsHead': '模型档案',
    'settings.modelsHeadP': '模型不是认知状态的一部分。切换模型不会改写基线、偏好或计划，只改变接下来谁来说话。',
    'settings.addProfile': '新增一份模型档案',
    'settings.addProfileNote': '支持 Ollama 与任意 OpenAI 兼容端点',
    'settings.keyNote': '密钥只写进本机的密钥文件，回显永远是掩码。探活会真实消耗一次外部请求额度——所以它是主动动作，不是自动轮询。',
    'settings.appearanceHead': '主题与动效',
    'settings.appearanceHeadP': '主题只换值，不换结构。日光与夜读共用同一套模板——换纸色，等于换一段文本。',
    'settings.day': '日光',
    'settings.dayNote': '纸白 · 墨字 · 苔绿',
    'settings.night': '夜读',
    'settings.nightNote': '深炭 · 米白字 · 浅苔',
    'settings.size': '正文字号',
    'settings.sizeP': '只影响阅读区的正文；界面标签与数据保持原尺寸。',
    'settings.small': '小',
    'settings.standard': '标准',
    'settings.large': '大',
    'settings.motion': '推演动效',
    'settings.motionP': '侧栏游线、状态词轮换与段落浮现。克制模式下只保留一次淡入，不播放持续动画。',
    'settings.full': '完整',
    'settings.restrained': '克制',
    'settings.preview': '预览 · 正文与依据',
    'settings.previewNote': '依据 · 偏好 2026-10-12',
    'sn.sub': '通用模型 · 独立记忆 · 不改动计划',
    'sn.you': '你',
    'sn.note': '记',
    'sn.msg1': '最近晚上总是开始不了，坐下就想刷手机。',
    'sn.msg2': '这更像「启动阻力」，不是动机不足。要不要把它写进当前会话的障碍里？我可以先起草，等你点头。',
    'sn.placeholder': '只聊，不改动任何东西…',
  },
  en: {
    'brand.name': 'MOUXUE',
    'brand.sub': 'The Quiet Room',
    'sb.newChat': 'New chat',
    'sb.searchPlaceholder': 'Search chats and fields',
    'sb.fields': 'Fields',
    'sb.archived': 'Archived',
    'sb.benchmark': 'Benchmark',
    'sb.settings': 'Settings',
    'sb.colophon': 'Every line traceable · persistent flow',
    'common.create': 'Create',
    'common.cancel': 'Cancel',
    'common.save': 'Save',
    'common.rename': 'Rename',
    'common.archive': 'Archive',
    'common.delete': 'Delete',
    'common.restore': 'Restore',
    'state.active': 'Active',
    'state.review': 'Review',
    'state.completed': 'Done',
    'state.draft': 'Draft',
    'state.abandoned': 'Abandoned',
    'chat.end': 'End of this exchange · every step traceable',
    'margin.motivation': 'Motivation',
    'margin.plan': 'Plan',
    'margin.context': 'Context',
    'margin.sidenote': 'Margin note',
    'margin.sidenoteNote': 'Separate memory · never edits the plan',
    'composer.stop': 'Stop',
    'composer.sendHint': 'send',
    'composer.newlineHint': 'newline',
    'composer.placeholder': 'Tell me what you are studying, or where you are stuck.',
    'composer.busyPrefix': 'While busy · ',
    'composer.hint': 'Be specific; I will ask when something is unclear.',
    'composer.attachNote': 'Attachments are not wired into this prototype',
    'busy.queue': 'Queue',
    'busy.queueP': 'Send in order once the current answer finishes.',
    'busy.queueTag': 'Least disruptive',
    'busy.interrupt': 'Interrupt & send',
    'busy.interruptP': 'Stop generating now; the unfinished part is marked interrupted.',
    'busy.interruptTag': 'Fastest turn',
    'busy.block': 'Hold send',
    'busy.blockP': 'Sending stays disabled until this round ends.',
    'busy.blockTag': 'Least context loss',
    'bench.kicker': 'Measurement',
    'bench.pageTitle': 'Benchmark',
    'bench.demoNote': 'Demo data not connected',
    'bench.title': 'Measure once,<br><em>know where you stand</em>',
    'bench.lede': 'The baseline is a suggested path, not a gate. Start with a short self-report, or go straight to a conversation — both count.',
    'bench.meta1': 'Resumable anytime',
    'bench.meta2': 'Continue or restart',
    'bench.meta3': 'Neither form is better',
    'bench.figureTitle': 'Three channels',
    'bench.stated': 'Stated',
    'bench.experienced': 'Experienced',
    'bench.measured': 'Measured',
    'bench.figureNote': 'When there is no reading, the bar stays empty — the blank is an answer too.',
    'bench.quickTitle': 'Short form',
    'bench.quickP': 'One reuse task observes four variables at once. Fast, at the cost of variables bleeding into each other — confidence capped at provisional.',
    'bench.quick1': 'Self-report · 20 items max',
    'bench.quick2': 'Reuse task · once',
    'bench.quick3': 'Save and exit anytime',
    'bench.startQuick': 'Start the short form',
    'bench.fullTitle': 'Full form',
    'bench.fullP': 'Independent tasks plus one real learning loop. Slower and cleaner — only here can the measured channel be filled honestly.',
    'bench.full1': 'Self-report + behavioural round',
    'bench.full2': 'Independent tasks · no bleed',
    'bench.full3': 'Readings you can reconcile',
    'bench.startFull': 'Start the full form',
    'bench.domainsKicker': 'Fields · two sets',
    'bench.domainsTitle': 'What you know, and what you want to touch',
    'bench.domainsP': 'The second set is an independent interest area. Skip it or change it anytime. One hard rule: interest cannot sit inside the familiar set.',
    'bench.familiar': 'Familiar to me',
    'bench.interested': 'Unfamiliar, but curious',
    'bench.familiarNote': 'Toggling creates a new version; older versions stay readable.',
    'bench.skip': 'Skip the second set',
    'bench.skipNote': 'Come back whenever.',
    'bench.readingsKicker': 'Readings · three channels',
    'bench.readingsTitle': 'Never converted, never averaged',
    'bench.readingsP': 'Stated, experienced and measured are three kinds of evidence, each independent. A channel with no evidence stays blank rather than receiving a plausible median.',
    'bench.reading1': 'A first self-report baseline exists; per-parameter readings are not published yet.',
    'bench.reading1Foot': '20 items · 7 answered',
    'bench.reading2': 'Evidence has to accumulate in real learning rounds; the baseline is not connected yet.',
    'bench.reading2Foot': '0 items of evidence',
    'bench.reading3': 'No behavioural task was run, so there is no reading here — the blank is the answer.',
    'bench.reading3Foot': 'Behavioural round not run',
    'bench.emptyMark': 'Blank',
    'bench.emptyNote': 'The full benchmark data is not exposed to this interface yet. The layout is kept, and demonstration scores are deliberately omitted — an invented reading is worse than none.',
    'settings.kicker': 'Preferences',
    'settings.title': 'Settings',
    'settings.back': 'Back to chat',
    'settings.general': 'General',
    'settings.generalNote': 'Language · sending · reasoning',
    'settings.models': 'Models',
    'settings.modelsNote': 'Providers · probes',
    'settings.appearance': 'Appearance',
    'settings.appearanceNote': 'Theme · size · motion',
    'settings.version': 'Interface prototype · not final',
    'settings.langHead': 'Language & sending',
    'settings.langHeadP': 'These options only affect the interface and input behaviour. They never translate your preferences, nor rewrite notes you have written.',
    'settings.language': 'Interface language',
    'settings.languageP': 'Applies immediately. Preferences, notes and conversations keep their original words — they are yours, and should not be translated for you.',
    'settings.busy': 'Sending while busy',
    'settings.busyP': 'The model is answering and you write another line. Each of the three behaviours has a cost; none is the default right answer.',
    'settings.reasoning': 'Reasoning',
    'settings.reasoningP': 'Thoughts and answers are stored and streamed separately. This only decides whether they start expanded.',
    'settings.fold': 'Collapsed',
    'settings.open': 'Expanded',
    'settings.citations': 'Citations',
    'settings.citationsP': 'When an answer refers to a preference or a high-scoring parameter, leave a clickable mark at the end of the sentence.',
    'settings.modelsHead': 'Model profiles',
    'settings.modelsHeadP': 'A model is not part of your cognitive state. Switching models never rewrites baselines, preferences or plans — it only changes who speaks next.',
    'settings.addProfile': 'Add a model profile',
    'settings.addProfileNote': 'Ollama or any OpenAI-compatible endpoint',
    'settings.keyNote': 'Keys are written only to the local key file, and are always echoed masked. A probe spends a real request — it is a deliberate action, not a poll.',
    'settings.appearanceHead': 'Theme & motion',
    'settings.appearanceHeadP': 'Themes change values, never structure. Day and Night share one template — changing the paper is changing one block of text.',
    'settings.day': 'Day',
    'settings.dayNote': 'Chalk · ink · moss',
    'settings.night': 'Night',
    'settings.nightNote': 'Charcoal · bone · pale moss',
    'settings.size': 'Reading size',
    'settings.sizeP': 'Affects the reading column only; interface labels and data keep their size.',
    'settings.small': 'Small',
    'settings.standard': 'Standard',
    'settings.large': 'Large',
    'settings.motion': 'Reasoning motion',
    'settings.motionP': 'Rail trace, rotating status words and paragraph reveals. Restrained keeps a single fade and disables ambient loops.',
    'settings.full': 'Full',
    'settings.restrained': 'Restrained',
    'settings.preview': 'Preview · body & citation',
    'settings.previewNote': 'Basis · preference 2026-10-12',
    'sn.sub': 'General model · separate memory · never edits the plan',
    'sn.you': 'You',
    'sn.note': 'N',
    'sn.msg1': 'I keep failing to start in the evening. I sit down and reach for my phone.',
    'sn.msg2': 'That reads as starting friction, not a lack of motivation. Want it written into this session\'s obstacles? I can draft it and wait for your nod.',
    'sn.placeholder': 'Just talk; nothing changes here…',
  },
};

const t = (key) => (I18N[state.lang] && I18N[state.lang][key]) || I18N.zh[key] || key;
const L = (zh, en) => (state.lang === 'en' ? en : zh);

/* ── data ─────────────────────────────────────────────────────────── */
const PROFILES = [
  {
    id: 'p-local', mark: '本', label: '本地 · 日常', short: 'Qwen 3.6 · 27B',
    provider: 'Ollama', kind: 'local', model: 'qwen3.6:27b',
    base: '127.0.0.1:11434', think: 'auto', ctx: '32k',
    masked: '无需密钥', note: '离线可用 · 单次 90–190 s', probe: '上次探活 128 ms · 12 个模型',
  },
  {
    id: 'p-deepseek', mark: '外', label: 'DeepSeek', short: 'DeepSeek V3',
    provider: 'OpenAI 兼容', kind: 'external', model: 'deepseek-chat',
    base: 'api.deepseek.com', think: 'auto', ctx: '64k',
    masked: 'sk-••••9f2c', note: '产品落点 · 流式稳定', probe: '上次探活 2026-10-19 · 可用',
  },
  {
    id: 'p-kimi', mark: '外', label: 'Kimi K2', short: 'Kimi K2',
    provider: 'OpenAI 兼容', kind: 'external', model: 'kimi-k2-0905',
    base: 'api.moonshot.cn', think: 'auto', ctx: '128k',
    masked: 'sk-••••71ab', note: '长上下文 · 适合整卷复盘', probe: '尚未探活',
  },
  {
    id: 'p-qwen', mark: '备', label: '本地 · 备用', short: 'Qwen 3.5 · 9B',
    provider: 'Ollama', kind: 'local', model: 'qwen3.5:9b',
    base: '127.0.0.1:11434', think: 'true', ctx: '32k',
    masked: '无需密钥', note: '快，但工具调用不稳', probe: '上次探活 96 ms · 12 个模型',
  },
];

const PLAN_V3 = {
  version: 3,
  steps: [
    { id: 'ST-001', action: '建立词根地图：-spect / -tract / -fer', status: 'done', meta: '产出 · 一张词根草图' },
    { id: 'ST-002', action: '精读 unit 1 例句，标注 25 个词', status: 'doing', meta: '进行中 · 方法 M-SE-03' },
    { id: 'ST-003', action: '自测：词根 → 词义反向回忆', status: 'pending', meta: '检查点 CP-01' },
    { id: 'ST-004', action: '第 2 天：间隔复习 + 错词重写', status: 'pending', meta: '间隔 · 18 h' },
  ],
  checkpoint: {
    title: '检查点 CP-01',
    text: '第 3 步完成后，不看书复述 20 / 25 个词根。',
    criteria: '通过标准 · 20 / 25；未通过则回到第 2 步重读例句。',
  },
};

const MOTIVATION_S1 = {
  type: '内在 · 主题兴趣',
  typeNote: 'INTRINSIC · TOPIC',
  strength: 3,
  drivers: [
    '三十天内读懂考研阅读的核心词',
    '把词根串成网络，而不是逐词抄写',
    '每天一个可完成的短回合',
  ],
  obstacles: [
    { title: '启动阻力', note: '晚上坐下后难以开始；常以刷手机替代第一句。' },
    { title: '前置知识缺口', note: '词缀含义记不牢，遇到生词容易退回逐字查。' },
  ],
  revision: '10-16 · 你把「每天 50 词」改成「每天 25 词」 · 由你写入',
};

const CONTEXT_S1 = {
  used: '27.4k',
  total: '32k',
  pct: 86,
  segments: [46, 18, 12, 10],
  sections: [
    { name: '偏好', value: '4 条 · 其中硬约束 2', meta: 'hard' },
    { name: '方法卡', value: '3 张 · 按环节筛选', meta: 'cards' },
    { name: '基线 / 证据', value: '6 条 · 仅 stated 通道', meta: 'stated' },
    { name: '策略库', value: '2 条', meta: 'library' },
    { name: '当前计划', value: '第 3 版 · 4 步', meta: 'plan' },
  ],
  note: '侧记使用独立记忆与独立计量。它可以看到全局，但写入只能是提案。',
};

const MOTIVATION_S2 = {
  type: '外在 · 截止时间',
  typeNote: 'EXTRINSIC · DEADLINE',
  strength: 2,
  drivers: ['下周要交一份英文摘要', '想在会议前把例句读顺'],
  obstacles: [{ title: '时间碎片化', note: '通勤时段只能做单句，不适合长回合。' }],
  revision: '昨天 · 你把计划从「每天 40 分钟」改成「通勤两段」 · 由你写入',
};

const CONTEXT_S2 = {
  used: '18.2k',
  total: '32k',
  pct: 57,
  segments: [35, 20, 10, 12],
  sections: [
    { name: '偏好', value: '4 条 · 其中硬约束 2', meta: 'hard' },
    { name: '方法卡', value: '2 张', meta: 'cards' },
    { name: '基线 / 证据', value: '4 条', meta: 'stated' },
    { name: '策略库', value: '1 条', meta: 'library' },
    { name: '当前计划', value: '第 2 版 · 3 步', meta: 'plan' },
  ],
  note: '这一轮只带入与「例句」相关的上下文；其余条目被检索维度排除。',
};

const PLAN_S2 = {
  version: 2,
  steps: [
    { id: 'ST-101', action: '挑出 -tract 的三条例句', status: 'done', meta: '产出 · 3 句' },
    { id: 'ST-102', action: '读顺三句，各造一句自己的话', status: 'done', meta: '方法 · 产出式复述' },
    { id: 'ST-103', action: '通勤时做单句跟读', status: 'doing', meta: '间隔 · 20 h' },
  ],
  checkpoint: {
    title: '检查点 CP-02',
    text: '明早不看笔记，说出 -tract 的三个变体。',
    criteria: '通过标准 · 3 / 3；未通过则保留在通勤清单里。',
  },
};

const PLAN_S3 = {
  version: 5,
  steps: [
    { id: 'ST-201', action: '整理三轮遗忘曲线记录', status: 'done', meta: '产出 · 一张曲线' },
    { id: 'ST-202', action: '标出三次以上遗忘的词', status: 'done', meta: '17 个词' },
    { id: 'ST-203', action: '把错词并入第 3 周计划', status: 'done', meta: '已完成' },
  ],
  checkpoint: {
    title: '检查点 CP-03',
    text: '复盘时能说出哪三类词最容易在 48 小时节点滑落。',
    criteria: '通过标准 · 命中两类；未通过则在复盘里补一次提取练习。',
  },
};

const BASE_MESSAGES_S1 = [
  {
    role: 'assistant',
    time: '20:58',
    thinking: '先不动计划：核对这句话是在改「词数」，还是在改「什么时候提取」。',
    traces: [
      { label: '读取会话', note: '目标 / 额外要求' },
      { label: '检索偏好', note: '2 条 · 1 条硬约束' },
    ],
    html: '<p class="lede">你把这三十天的目标写成了「能读懂考研阅读里的核心词」，而不是「背完 unit 1」。这两件事的差别，决定了今天该做的是一张词根网络，而不是 50 次抄写。</p>'
      + '<p>unit 1 里反复出现的只有三条词根：<strong>-spect</strong>（看）、<strong>-tract</strong>（拉）、<strong>-fer</strong>（带）。先让它们互相咬合，再往例句里回收。</p>'
      + '<p>今天的回合只做两件事：认出词根，在句子里看见它。先从你最熟悉的那一类例句开始。<span class="cite" data-basis="你在偏好里写过：「我喜欢直接跟模型一边交流一边学习记笔记。」（2026-10-12 · 由你写入）"></span></p>',
    foot: true,
    basis: '依据是当前会话目标、你的偏好原话，以及 unit 1 的词频分布。',
  },
  {
    role: 'user',
    time: '21:04',
    text: '第一单元太长了，我想拆成两天，每天 25 个词。',
  },
];

const BASE_MESSAGES_S2 = [
  {
    role: 'assistant',
    time: '昨天 22:10',
    thinking: '先核对计划第 2 周：这一步是补新词，还是把旧词读顺。',
    traces: [{ label: '检索计划', note: '第 2 周 · 3 步' }],
    html: '<p class="lede">上一周你卡在 -tract 的三个变体上。今天不补新词，只把三条例句读顺，再各造一句。</p>'
      + '<p>如果造不出来，就说明你记住的是中文释义，而不是这个词根的「拉」这个动作。</p>',
    foot: true,
  },
  {
    role: 'user',
    time: '昨天 22:18',
    text: '明天只做两段可以吗？',
  },
  {
    role: 'assistant',
    time: '昨天 22:18',
    thinking: '间隔从 14 小时推到 20 小时；仍在有效区间，只需记一笔。',
    traces: [{ label: '核对间隔', note: '14 h → 20 h' }],
    html: '<p>可以。把第 2 段挪到明早，间隔从 14 小时变成 20 小时——仍在有效区间内。我会按这个顺序记一笔，不动计划主体。</p>',
    foot: true,
  },
];

const BASE_MESSAGES_S3 = [
  {
    role: 'assistant',
    time: '10-18',
    thinking: '计划 3 / 3 完成；复盘由你点，不自动触发。',
    traces: [{ label: '核对计划', note: '3 / 3 已完成' }],
    html: '<p class="lede">这一轮的计划已经全部落定。复盘由你点，不自动触发。</p>'
      + '<p>写执行时的感受就好——尤其是哪一步你想跳过、为什么。感想会整份进入模型，用来校准偏好与基线；它不会直接改写任何读数。</p>'
      + '<p class="muted">如果你觉得没有什么可写，也可以直接点「完成会话」。空白同样是一条信息。</p>',
    foot: true,
  },
];

const EMPTY_CONTEXT = {
  used: '0', total: '32k', pct: 0, segments: [0, 0, 0, 0],
  sections: [],
  note: '这个会话还没有产生上下文。第一句话之后，这里会显示它实际看见的条目。',
};

function makeDetail(key) {
  const base = { replyCount: 0, lastVariant: '' };
  if (key === 's1') {
    return {
      ...base,
      messages: structuredClone(BASE_MESSAGES_S1),
      plan: structuredClone(PLAN_V3),
      motivation: structuredClone(MOTIVATION_S1),
      context: structuredClone(CONTEXT_S1),
    };
  }
  if (key === 's2') {
    return {
      ...base,
      messages: structuredClone(BASE_MESSAGES_S2),
      plan: structuredClone(PLAN_S2),
      motivation: structuredClone(MOTIVATION_S2),
      context: structuredClone(CONTEXT_S2),
    };
  }
  if (key === 's3') {
    return {
      ...base,
      messages: structuredClone(BASE_MESSAGES_S3),
      plan: structuredClone(PLAN_S3),
      motivation: structuredClone(MOTIVATION_S1),
      context: structuredClone(CONTEXT_S1),
    };
  }
  return {
    ...base,
    messages: [], plan: null, motivation: null, context: structuredClone(EMPTY_CONTEXT),
  };
}

/* ── state ────────────────────────────────────────────────────────── */
const PREFS_KEY = 'mouxue.quiet.prefs.v1';
function readPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch { return {}; }
}
const prefs = readPrefs();

const state = {
  view: 'chat',
  lang: prefs.lang === 'en' ? 'en' : 'zh',
  theme: prefs.theme === 'night' ? 'night' : 'day',
  size: ['small', 'base', 'large'].includes(prefs.size) ? prefs.size : 'base',
  motion: prefs.motion === 'restrained' ? 'restrained' : 'full',
  cite: prefs.cite !== false,
  thinkView: prefs.thinkView === 'open' ? 'open' : 'fold',
  busyBehavior: ['queue', 'interrupt', 'block'].includes(prefs.busy) ? prefs.busy : 'queue',
  activeProfile: 'p-local',
  profiles: structuredClone(PROFILES),
  currentSession: 'S-0001',
  currentFolder: 'F-01',
  marginTab: 'plan',
  inlineKind: null,
  busy: false,
  queue: [],
  search: '',
  archiveOpen: false,
  details: {},
  run: null,
  autoPlayed: false,
  bench: { form: 'full', status: 'interrupted', answered: 7 },
  familiar: new Set(['英语 · 考研词汇', '认知科学 · 记忆']),
  interested: new Set(['哲学 · 认识论']),
  folders: [
    {
      id: 'F-01', name: '英语 · 考研词汇', open: true,
      sessions: [
        { id: 'S-0001', title: '词根网络 · unit 1', state: 'active', time: '21:04', detail: 's1' },
        { id: 'S-0002', title: '例句精读 · 第 2 周', state: 'active', time: '昨天', detail: 's2' },
        { id: 'S-0003', title: '复盘：遗忘曲线校准', state: 'review', time: '10-18', detail: 's3' },
      ],
    },
    {
      id: 'F-02', name: '认知科学 · 记忆机制', open: false,
      sessions: [
        { id: 'S-0004', title: '间隔重复的边界条件', state: 'active', time: '10-17' },
        { id: 'S-0005', title: '提取练习 vs 重读', state: 'draft', time: '10-15' },
      ],
    },
    {
      id: 'F-03', name: '写作 · 论证结构', open: false,
      sessions: [
        { id: 'S-0006', title: '议论文的让步段', state: 'completed', time: '09-30' },
      ],
    },
  ],
  archivedSessions: [
    { id: 'S-0091', title: '暑期数学复盘', folderId: 'F-03', folderName: '写作 · 论证结构', state: 'completed', time: '09-28' },
    { id: 'S-0092', title: '旧版 · 单词听写实验', folderId: 'F-01', folderName: '英语 · 考研词汇', state: 'abandoned', time: '08-12' },
  ],
  archivedFolders: [
    {
      id: 'F-A1', name: '日语 · N2 听力', sessionCount: 4, sessions: [
        { id: 'S-0081', title: 'N2 听力 · 第 1 回', state: 'completed', time: '08-02' },
        { id: 'S-0082', title: 'N2 听力 · 第 2 回', state: 'completed', time: '08-09' },
        { id: 'S-0083', title: '敬语听辨', state: 'completed', time: '08-16' },
        { id: 'S-0084', title: '听写复盘的停顿点', state: 'completed', time: '08-23' },
      ],
    },
  ],
  confirmAction: null,
  sessionSeq: 100,
  folderSeq: 10,
};

state.details['S-0001'] = makeDetail('s1');
state.details['S-0002'] = makeDetail('s2');
state.details['S-0003'] = makeDetail('s3');

function savePrefs() {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({
      lang: state.lang, theme: state.theme, size: state.size, motion: state.motion,
      cite: state.cite, thinkView: state.thinkView, busy: state.busyBehavior,
    }));
  } catch { /* storage may be unavailable */ }
}

/* ── dom refs ─────────────────────────────────────────────────────── */
const sidebar = $('#sidebar');
const folderList = $('#folder-list');
const archiveList = $('#archive-list');
const thread = $('#thread');
const threadWrap = $('#thread-wrap');
const threadEnd = $('#thread-end');
const workline = $('#workline');
const marginBody = $('#margin-body');
const workspace = $('#workspace');
const composer = $('#composer');
const composerInput = $('#composer-input');
const sendBtn = $('#send-btn');
const popLayer = $('#popover-layer');
const toastLayer = $('#toast-layer');
const confirmDialog = $('#confirm-dialog');
const sideNote = $('#sidenote');
const snScrim = $('#sn-scrim');

/* ── helpers ──────────────────────────────────────────────────────── */
function findFolder(id) { return state.folders.find((f) => f.id === id) || null; }
function findSession(id) {
  for (const folder of state.folders) {
    const found = folder.sessions.find((s) => s.id === id);
    if (found) return { ...found, folderId: folder.id, folder };
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
  return ({
    done: L('已完成', 'Done'),
    doing: L('进行中', 'In progress'),
    pending: L('待开始', 'Queued'),
    skipped: L('已跳过', 'Skipped'),
  })[status] || status;
}
function thinkLabel(value) {
  return ({ auto: L('自动', 'Auto'), false: L('关', 'Off'), true: L('开', 'On') })[String(value)] || value;
}
function scrollThread(force = false) {
  const nearBottom = threadWrap.scrollHeight - threadWrap.scrollTop - threadWrap.clientHeight < 240;
  if (force || nearBottom) {
    threadWrap.scrollTo({ top: threadWrap.scrollHeight, behavior: 'smooth' });
  }
}
function toast(message, kind = '') {
  const el = document.createElement('div');
  el.className = 'toast' + (kind === 'danger' ? ' is-danger' : '');
  el.textContent = message;
  toastLayer.appendChild(el);
  setTimeout(() => {
    el.classList.add('is-out');
    setTimeout(() => el.remove(), 240);
  }, 2600);
}

/* ── conversation engine ───────────────────────────────────────────
   A small local pipeline: classify intent → assemble session context
   → pick a non-repeating reply structure → stream it → optionally
   draft a plan change. Everything is derived from the session, so the
   same input twice still produces a different, context-aware answer.
   ─────────────────────────────────────────────────────────────────── */
const CJK_RE = /[㐀-鿿]/;
const detectReplyLang = (text) => (CJK_RE.test(text) ? 'zh' : 'en');

const INTENT_RULES = [
  ['plan_review', /哪里不合理|看看(这个|我的)?计划|评估.*计划|检查.*计划|合理吗|值不值得|review (my|the) plan|what.?s (off|wrong)|critique/i],
  ['plan_change', /拆|分两次|两天|两段|调整|改成|改为|重新安排|换个顺序|提前|推迟|计划|安排|步骤|split|two days|two sessions|reschedule|rearrange|change the plan|make a plan|schedule/i],
  ['test_me', /考我|考考我|考一下|测我|测测我|测一下|问我|抽查|出题|检验|自测|quiz|test me|ask me|check me/i],
  ['stuck', /卡住|开始不了|不想|拖延|焦虑|迷茫|坚持不下去|启动不了|stuck|procrastinat|can'?t start|anxious|overwhelm|avoid/i],
  ['progress', /完成|做完|进度|复盘|检查点|复习什么|该复习|done|finished|progress|review|what should i (study|review)/i],
  ['explain', /为什么|是什么|怎么|如何|解释|区别|原理|why|what|how|explain|difference/i],
  ['smalltalk', /^(你好|嗨|在吗|谢谢|早上好|晚上好|hi|hello|hey|thanks|thank you)[\s!！。,.，?？]*$/i],
];

function classifyIntent(text) {
  const value = String(text || '').trim();
  for (const [intent, re] of INTENT_RULES) if (re.test(value)) return intent;
  return 'content';
}

function keyPhrase(text) {
  const clean = String(text || '').replace(/\s+/g, ' ').trim();
  const first = clean.split(/[。！？!?；;\n]/)[0] || clean;
  return first.length > 36 ? `${first.slice(0, 36)}…` : first;
}

function replyContext(detail, session, text, lang) {
  const plan = detail.plan;
  const steps = plan?.steps || [];
  const doingStep = steps.find((s) => s.status === 'doing') || steps.find((s) => s.status === 'pending') || null;
  const nextStep = steps.find((s) => s.status !== 'done') || null;
  const done = steps.filter((s) => s.status === 'done').length;
  const prevUser = [...detail.messages].reverse().find((m) => m.role === 'user' && m.text !== text);
  const fallback = lang === 'zh' ? '这一轮的入口' : 'this round’s entry';
  return {
    lang, text, key: keyPhrase(text),
    title: session?.title || (lang === 'zh' ? '这个会话' : 'this session'),
    folder: session?.folder?.name || '',
    plan, version: plan?.version || 0, steps, done, total: steps.length,
    doing: doingStep?.action || fallback,
    next: nextStep?.action || '',
    checkpoint: plan?.checkpoint?.text || '',
    obstacle: detail.motivation?.obstacles?.[0]?.title || (lang === 'zh' ? '启动阻力' : 'starting friction'),
    prevUser: prevUser?.text || '',
    firstAction: doingStep?.action || nextStep?.action || fallback,
    progress: plan ? `${done} / ${steps.length}` : '—',
  };
}

function pickReplyVariant(detail, key, text, count) {
  const seed = Array.from(String(text || '')).reduce((n, ch) => (n * 31 + ch.codePointAt(0)) % 100003, 7);
  const turn = (detail.replyCount || 0) + 1;
  let idx = (seed + turn) % count;
  if (detail.lastVariant === `${key}:${idx}` && count > 1) idx = (idx + 1) % count;
  detail.replyCount = turn;
  detail.lastVariant = `${key}:${idx}`;
  return idx;
}

/* reply bodies: each variant is a function of the assembled context */
const REPLY_VARIANTS = {
  zh: {
    plan_review: [
      (c) => ({
        lede: '先看结构，不急着改。',
        paras: [
          `当前计划第 ${c.version} 版，${c.done} / ${c.total} 步完成；正在进行的是「${c.doing}」。${c.next && c.next !== c.doing ? `下一处入口是「${c.next}」。` : ''}`,
          '我会盯三件事：一步的体量是否能在一次回合里完成、检查点是否可验证，以及有没有把「读」误当成「会」。',
        ],
        ask: '你最担心的是哪一件：时间不够、步骤太粗，还是做完之后说不清自己会不会？',
      }),
      (c) => ({
        lede: '有两点值得先核对。',
        paras: [
          `第一，第 ${c.version} 版里「${c.doing}」和它的检查点是否对得上：${c.checkpoint || '目前还没有写检查点。'}`,
          '第二，间隔是否被尊重。如果相邻两步都塞在同一天，计划看起来完整，实际只是在重复输入。',
        ],
        ask: '你希望我按「时间」还是按「提取强度」来重排这张表？',
      }),
    ],
    plan_change: [
      (c) => ({
        lede: '可以调整。先分清是改内容，还是改时机。',
        paras: [
          `当前计划第 ${c.version} 版，正在进行「${c.doing}」。你提的是「${c.key}」；如果直接加量，今天会变长，但下一次的提取会变弱。`,
          c.proposalMode === 'split'
            ? '我起草了一版改动：保留当前这一步的前半段，把后半段移到下一次的入口，用一次反向回忆开场。你点头之前，计划表不会变化。'
            : '这一步已经带着拆分标记，再拆一次只会叠词。我改为起草一条插入式调整：在当前步骤后面加一条新步骤，原步骤不动。',
        ],
        ask: c.proposalMode === 'split'
          ? '这样拆，下一次你愿意先做回忆，还是先读例句？'
          : '这条新增步骤，你希望它落在今天，还是下一次？',
      }),
      (c) => ({
        lede: c.proposalMode === 'split'
          ? '这个改动成立，但拆点最好落在「提取」上。'
          : '这一步已经拆过一次，继续保持结构比再拆一次更重要。',
        paras: [
          `在「${c.title}」里，你已经走到第 ${c.version} 版；当前入口是「${c.doing}」。把同样的内容读两遍，不如让第二天先找一次。`,
          c.proposalMode === 'split'
            ? '我把改动写成一张草案：只动当前这一步，并在它后面插一段先测后读。检查点会同步改成两段都完成后的标准。'
            : '我把改动写成一张插入式草案：当前步骤保持原样，在它后面补一条调整步骤。原来的检查点保留。',
        ],
        ask: c.proposalMode === 'split'
          ? '如果明天只有二十分钟，你想留下哪一半？'
          : '这条新步骤，你希望它先落在今天，还是下一次？',
      }),
    ],
    plan_change_new: [
      (c) => ({
        lede: '可以，但现在还没有一张能改的计划。',
        paras: [
          `与其把「${c.key}」直接塞进安排里，不如先立一个三次动作能完成的小计划。`,
          '我起了一张草案：目标、启动回合、检查点。先看，再决定要不要写入。',
        ],
        ask: '这一轮你希望在什么时候结束？',
      }),
      (c) => ({
        lede: '先把「计划」缩到能一次完成的尺寸。',
        paras: [
          `「${c.key}」听起来更像一个目标，而不是一步动作。没有计划时，先写下一个能在二十分钟内完成的入口。`,
          '草案里有三步：写下结束条件、做一次启动回合、用一个检查点验证。写入之后，侧页才会出现这张表。',
        ],
        ask: '第一次启动回合，你最多愿意给多少分钟？',
      }),
    ],
    test_me: [
      (c) => ({
        lede: '好，先不给你答案。',
        paras: [`按当前计划第 ${c.version} 版，我只从「${c.doing}」这一块出题。三道，都是提取，不是选择。`],
        list: [
          `不看笔记，用一句话说出「${c.title}」里你最近学到的一个概念。`,
          `它对应「${c.doing}」里的哪个动作？`,
          '举一个反例：它在什么情况下不成立？',
        ],
        ask: '先答第一题；我不提示对错。',
      }),
      (c) => ({
        lede: '三道题，按你现在的进度来。',
        paras: [`我会把题目压在你下一步的入口上：${c.next || c.doing}。答不出来也算信息。`],
        list: [
          '不看笔记，复述这一步在做什么。',
          '说出一个你还不能解释清楚的词或规则。',
          '给这个难点配一个例子，再配一个反例。',
        ],
        ask: '如果只能答一道，你选哪一道？',
      }),
    ],
    stuck: [
      (c) => ({
        lede: '那更像是启动阻力，不是动机不足。',
        paras: [
          `你在动机里记过的障碍是「${c.obstacle}」。如果它还在，今天不需要一个完整回合，只需要一个五分钟的入口。`,
          `把「${c.firstAction}」缩成一个动作：做完就停。完成一个小动作，比重新设计计划更能恢复推进。`,
        ],
        ask: `现在能不能只花五分钟，做「${c.firstAction}」的第一小步？`,
      }),
      (c) => ({
        lede: '先别把「开始不了」当成懒。',
        paras: [
          '你卡在的是入口，不是内容。把入口再切小一点：只打开材料，只读一段，只写一句。',
          `我会把下一步留在「${c.firstAction}」，但把完成标准降到「做了一小段」。`,
        ],
        ask: '今晚你愿意给这件事几分钟？三分钟也算。',
      }),
    ],
    progress: [
      (c) => ({
        lede: '先对一下账。',
        paras: [
          `第 ${c.version} 版计划里，${c.done} / ${c.total} 已完成；下一步是「${c.next || '尚未安排'}」。${c.checkpoint ? `检查点：${c.checkpoint}` : ''}`,
          '如果这一步已经实际完成，可以在侧页勾掉它；如果不确定，我们先找一个能验证它的动作。',
        ],
        ask: '今天这一步，你做到哪一小段了？',
      }),
      (c) => ({
        lede: '进度不是一个百分比，是下一步在哪里。',
        paras: [
          `你现在在「${c.title}」，计划第 ${c.version} 版，完成 ${c.done} 步，还剩 ${Math.max(0, c.total - c.done)} 步。`,
          `我建议先不追总量，只确认下一步的入口：「${c.next || c.firstAction}」。`,
        ],
        ask: '下一步，你希望我把完成标准写得更具体吗？',
      }),
    ],
    explain: [
      (c) => ({
        lede: '先把它放回你正在做的那一步。',
        paras: [
          `你问的是「${c.key}」。在「${c.title}」里，它和当前计划第 ${c.version} 版的「${c.doing}」直接相关。`,
          '解释这类问题，我通常先分清两层：它是什么（定义），和它在什么条件下成立（用法）。两层混在一起，解释会越读越长。',
        ],
        ask: '你想要的是一个人能听懂的定义，还是一个能用来做题的判断规则？',
      }),
      (c) => ({
        lede: '先别急着找一个标准答案。',
        paras: [
          `「${c.key}」这个问题，换一个问法会更可操作：它在什么例子下必须成立，什么例子下会失效？`,
          `当前这一步是「${c.doing}」。如果能把定义和一个反例同时说出来，你就不只是记住了词面。`,
        ],
        ask: '你先给我一个自己的例子，我来帮你挑它站不站得住。',
      }),
    ],
    smalltalk: [
      (c) => ({
        lede: '在。',
        paras: [`这一轮还停在「${c.doing}」。你想继续，还是先换一件更小的事？`],
        ask: '给我一句话就好。',
      }),
      (c) => ({
        lede: '收到。',
        paras: [`当前会话是「${c.title}」，下一步是「${c.next || c.firstAction}」。我们可以从那里接着走。`],
        ask: '要接着走，还是先把它拆小一点？',
      }),
    ],
    content: [
      (c) => ({
        lede: '先把你这句话放回当前这一轮。',
        paras: [
          `你提到「${c.key}」。当前会话是「${c.title}」，正在进行「${c.doing}」；进度 ${c.progress}。`,
          '如果要把这句话变成一个可执行的动作，它需要落到一个具体产出上——一句话、一道题，或一次复述。',
        ],
        ask: '你希望这一轮结束时，留下什么？',
      }),
      (c) => ({
        lede: '这句话可以变成一个动作，但先要落下锚点。',
        paras: [
          `在「${c.title}」里，你已经在处理「${c.doing}」。关于「${c.key}」，我暂时不替你下结论；先确认它属于哪一类：概念、例句，还是安排。`,
          '分类不同，下一步也不同：概念需要定义和反例，例句需要回读，安排需要一张草案。',
        ],
        ask: '它更像哪一种？',
      }),
    ],
    no_plan: [
      (c) => ({
        lede: '这里还没有一张计划。',
        paras: [
          '先不急着出题或对账——没有计划时，这些读数都会是空的。',
          '我们可以先写下一个能在二十分钟内完成的动作，再把它扩成三步。',
        ],
        ask: '这一轮你想先碰哪一件小事？',
      }),
      (c) => ({
        lede: '先立一个入口，再谈进度。',
        paras: [
          `当前会话「${c.title}」还没有计划。与其先评估，不如先确定这一轮结束时要留下什么。`,
          '一个具体的产出就够了：一句话、一道题，或一次复述。',
        ],
        ask: '你想要哪一种产出？',
      }),
    ],
  },
  en: {
    plan_review: [
      (c) => ({
        lede: 'Let’s look at the structure before changing it.',
        paras: [
          `The plan is at v${c.version}, with ${c.done} of ${c.total} steps done. The current entry is “${c.doing}”.`,
          'I would check three things: whether each step fits one sitting, whether the checkpoint can be verified, and whether “reading” is being mistaken for “knowing”.',
        ],
        ask: 'What worries you most: time, step size, or not being able to tell whether you actually learned it?',
      }),
      (c) => ({
        lede: 'Two things are worth checking first.',
        paras: [
          `First, whether “${c.doing}” and its checkpoint agree: ${c.checkpoint || 'there is no checkpoint yet.'}`,
          'Second, whether spacing is respected. If two adjacent steps both land today, the plan looks complete but is only repeated input.',
        ],
        ask: 'Should I re-order by time, or by retrieval strength?',
      }),
    ],
    plan_change: [
      (c) => ({
        lede: 'We can adjust it. First: are we changing the content, or the timing?',
        paras: [
          `At v${c.version}, the plan is on “${c.doing}”. You asked about “${c.key}”. Adding volume makes today longer, but makes the next retrieval weaker.`,
          c.proposalMode === 'split'
            ? 'I drafted a change: keep the first half in today’s round, move the second half to the next entry, and open with a recall. Nothing changes in the plan until you approve it.'
            : 'This step already carries a split marker; splitting it again would only nest labels. I drafted an inserted adjustment instead: a new step after the current one, leaving the original untouched.',
        ],
        ask: c.proposalMode === 'split'
          ? 'For the next entry, would you rather start with recall or with rereading?'
          : 'Should the inserted step land today, or at the next entry?',
      }),
      (c) => ({
        lede: c.proposalMode === 'split'
          ? 'That change works, but the split should land on retrieval.'
          : 'This step has already been split once; keeping the structure is better than splitting it again.',
        paras: [
          `In “${c.title}” you are at v${c.version}; the current entry is “${c.doing}”. Reading the same material twice is weaker than finding it once the next day.`,
          c.proposalMode === 'split'
            ? 'I put the change into a draft: only the current step moves, and a test-first segment is inserted after it. The checkpoint is updated to cover both halves.'
            : 'I put the change into a draft: the current step stays as it is, and one adjustment step is added after it. The existing checkpoint stays.',
        ],
        ask: c.proposalMode === 'split'
          ? 'If tomorrow only has twenty minutes, which half would you keep?'
          : 'Should the new step land today, or at the next entry?',
      }),
    ],
    plan_change_new: [
      (c) => ({
        lede: 'Yes — but there is no plan to change yet.',
        paras: [
          `Rather than dropping “${c.key}” straight into a schedule, let’s make a small plan that closes in three moves.`,
          'I drafted one: a goal, a starting round, and a checkpoint. Read it first; nothing gets written until you nod.',
        ],
        ask: 'When should this round end?',
      }),
      (c) => ({
        lede: 'Shrink the plan until it fits one sitting.',
        paras: [
          `“${c.key}” sounds more like a goal than an action. With no plan yet, start with one entry that fits in twenty minutes.`,
          'The draft has three steps: write the end condition, run one starting round, then verify it with a checkpoint.',
        ],
        ask: 'For the first starting round, how many minutes can you give it?',
      }),
    ],
    test_me: [
      (c) => ({
        lede: 'Alright — no answers yet.',
        paras: [`Three questions, all from “${c.doing}”. Retrieval, not multiple choice.`],
        list: [
          `Without notes, say in one sentence a concept you recently learned in “${c.title}”.`,
          `Which action in “${c.doing}” does it belong to?`,
          'Give one counterexample: when does it stop holding?',
        ],
        ask: 'Answer the first one; I won’t signal right or wrong.',
      }),
      (c) => ({
        lede: 'Three questions, aimed at your current step.',
        paras: [`They sit on your next entry: ${c.next || c.doing}. Not being able to answer is also information.`],
        list: [
          'Without notes, restate what this step is doing.',
          'Name one word or rule you still cannot explain clearly.',
          'Give it one example and one counterexample.',
        ],
        ask: 'If you can only answer one, which one?',
      }),
    ],
    stuck: [
      (c) => ({
        lede: 'That sounds like starting friction, not a lack of motivation.',
        paras: [
          `The obstacle you logged is “${c.obstacle}”. If it is still there, you do not need a full round today — only a five-minute entry.`,
          `Shrink “${c.firstAction}” to one move: do it, then stop. A finished small move restores momentum better than redesigning the plan.`,
        ],
        ask: `Could you spend five minutes on the first move of “${c.firstAction}” right now?`,
      }),
      (c) => ({
        lede: 'Do not read “I cannot start” as laziness.',
        paras: [
          'You are stuck at the entry, not in the content. Cut the entry smaller: open the material, read one section, write one line.',
          `I will keep the next step at “${c.firstAction}”, but lower the bar to “did a small piece”.`,
        ],
        ask: 'How many minutes can you give this tonight? Three counts too.',
      }),
    ],
    progress: [
      (c) => ({
        lede: 'Let’s reconcile first.',
        paras: [
          `In v${c.version}, ${c.done} of ${c.total} steps are done; the next entry is “${c.next || 'not set'}”. ${c.checkpoint ? `Checkpoint: ${c.checkpoint}` : ''}`,
          'If the step is actually finished, tick it in the margin. If you are unsure, let’s find one action that can verify it.',
        ],
        ask: 'How much of this step did you actually finish today?',
      }),
      (c) => ({
        lede: 'Progress is not a percentage; it is where the next step is.',
        paras: [
          `You are in “${c.title}”, plan v${c.version}, with ${c.done} steps done and ${Math.max(0, c.total - c.done)} left.`,
          `I would not chase the total. Confirm the next entry first: “${c.next || c.firstAction}”.`,
        ],
        ask: 'Should I write a clearer finish line for the next step?',
      }),
    ],
    explain: [
      (c) => ({
        lede: 'Put it back into the step you are on.',
        paras: [
          `You asked about “${c.key}”. In “${c.title}” it sits next to “${c.doing}” in plan v${c.version}.`,
          'For this kind of question I separate two layers: what it is (definition) and when it holds (use). Mixed together, explanations get longer without getting clearer.',
        ],
        ask: 'Do you want a definition a person can understand, or a rule you can apply to a question?',
      }),
      (c) => ({
        lede: 'Do not rush to a standard answer.',
        paras: [
          `A more workable version of “${c.key}”: in which examples must it hold, and in which examples does it fail?`,
          `The current step is “${c.doing}”. If you can state both a definition and a counterexample, you are not just remembering the surface.`,
        ],
        ask: 'Give me your own example first; I will help you test whether it holds.',
      }),
    ],
    smalltalk: [
      (c) => ({
        lede: 'Here.',
        paras: [`This round is still at “${c.doing}”. Do you want to continue, or pick something smaller?`],
        ask: 'One sentence is enough.',
      }),
      (c) => ({
        lede: 'Got it.',
        paras: [`The session is “${c.title}”, and the next step is “${c.next || c.firstAction}”. We can continue from there.`],
        ask: 'Continue, or cut it smaller first?',
      }),
    ],
    content: [
      (c) => ({
        lede: 'Let’s put that back into this round.',
        paras: [
          `You said “${c.key}”. The session is “${c.title}”, currently on “${c.doing}”; progress ${c.progress}.`,
          'For it to become an action, it needs a concrete output: a sentence, a question, or a recall.',
        ],
        ask: 'What should be left behind when this round ends?',
      }),
      (c) => ({
        lede: 'That can become an action, but it needs an anchor first.',
        paras: [
          `In “${c.title}” you are already handling “${c.doing}”. About “${c.key}”, I will not decide for you yet; first name what it is: a concept, an example, or a schedule.`,
          'Each needs a different next move: a concept needs a definition and a counterexample, an example needs rereading, a schedule needs a draft.',
        ],
        ask: 'Which one is it closer to?',
      }),
    ],
    no_plan: [
      (c) => ({
        lede: 'There is no plan here yet.',
        paras: [
          'Testing or reconciling would only produce empty readings. Let’s first write one action that fits in twenty minutes, then grow it into three steps.',
          'A plan can be small and still be real.',
        ],
        ask: 'What is the smallest thing you want to touch this round?',
      }),
      (c) => ({
        lede: 'Start with an entry, not with progress.',
        paras: [
          `“${c.title}” has no plan yet. Before evaluating anything, decide what this round should leave behind.`,
          'A concrete output is enough: one sentence, one question, one recall.',
        ],
        ask: 'Which output do you want?',
      }),
    ],
  },
};

const STATUS_WORDS = {
  zh: {
    plan_review: ['读取计划', '核对检查点', '组织评估'],
    plan_change: ['辨明目标', '核对计划', '起草改动'],
    plan_change_new: ['辨明目标', '检查当前状态', '起草计划'],
    test_me: ['读取检查点', '抽取题目', '等待作答'],
    stuck: ['读取动机', '检索策略', '缩小动作'],
    progress: ['核对计划', '统计进度', '定位下一步'],
    explain: ['检索上下文', '定位步骤', '组织解释'],
    smalltalk: ['读取会话', '简短回应'],
    content: ['读取上下文', '定位会话', '组织回答'],
    no_plan: ['读取会话', '检查计划', '组织回答'],
  },
  en: {
    plan_review: ['Read the plan', 'Check the checkpoint', 'Weigh the structure'],
    plan_change: ['Clarify intent', 'Check the plan', 'Draft the change'],
    plan_change_new: ['Clarify intent', 'Read the session', 'Draft a plan'],
    test_me: ['Read the checkpoint', 'Pick questions', 'Wait for an answer'],
    stuck: ['Read motivation', 'Search strategies', 'Shrink the move'],
    progress: ['Check the plan', 'Count progress', 'Find the next step'],
    explain: ['Search context', 'Locate the step', 'Compose an explanation'],
    smalltalk: ['Read the session', 'Keep it brief'],
    content: ['Read context', 'Locate the session', 'Compose a reply'],
    no_plan: ['Read the session', 'Check the plan', 'Compose a reply'],
  },
};

function buildTraces(key, lang, c) {
  const zh = {
    plan_review: [
      ['读取计划', `第 ${c.version} 版 · ${c.total} 步`],
      ['核对检查点', c.checkpoint ? '1 条' : '缺失'],
      ['比对间隔', '相邻步骤'],
    ],
    plan_change: [
      ['读取会话', '目标 / 额外要求'],
      ['检索偏好', '2 条 · 1 条硬约束'],
      ['核对计划', `第 ${c.version} 版 · ${c.total} 步`],
      ['起草改动', '1 张草案'],
    ],
    plan_change_new: [
      ['读取会话', c.title],
      ['确认现状', '尚无计划'],
      ['起草计划', '3 步草案'],
    ],
    test_me: [
      ['读取计划', `第 ${c.version} 版`],
      ['定位检查点', c.checkpoint ? 'CP-01' : '未建立'],
      ['抽取题目', '3 道 · 提取'],
    ],
    stuck: [
      ['读取动机', c.obstacle],
      ['检索策略库', '1 条 · 启动'],
      ['缩小动作', '5 分钟入口'],
    ],
    progress: [
      ['核对计划', `第 ${c.version} 版`],
      ['统计进度', `${c.done} / ${c.total}`],
      ['定位下一步', c.next || '未安排'],
    ],
    explain: [
      ['检索上下文', '偏好 / 方法卡'],
      ['定位相关步骤', c.doing],
      ['组织解释', '定义 / 用法'],
    ],
    smalltalk: [['读取会话', c.title]],
    content: [
      ['读取上下文', '目标 / 偏好'],
      ['定位会话', c.title],
      ['组织回答', '讨论'],
    ],
    no_plan: [
      ['读取会话', c.title],
      ['检查计划', '未建立'],
      ['组织回答', '先立入口'],
    ],
  };
  const en = {
    plan_review: [
      ['Read the plan', `v${c.version} · ${c.total} steps`],
      ['Check the checkpoint', c.checkpoint ? '1 found' : 'missing'],
      ['Compare spacing', 'adjacent steps'],
    ],
    plan_change: [
      ['Read the session', 'goal / constraints'],
      ['Search preferences', '2 found · 1 hard'],
      ['Check the plan', `v${c.version} · ${c.total} steps`],
      ['Draft the change', '1 draft'],
    ],
    plan_change_new: [
      ['Read the session', c.title],
      ['Check the state', 'no plan yet'],
      ['Draft a plan', '3-step draft'],
    ],
    test_me: [
      ['Read the plan', `v${c.version}`],
      ['Locate the checkpoint', c.checkpoint ? 'CP-01' : 'not set'],
      ['Pick questions', '3 · retrieval'],
    ],
    stuck: [
      ['Read motivation', c.obstacle],
      ['Search strategies', '1 · starting'],
      ['Shrink the move', '5-minute entry'],
    ],
    progress: [
      ['Check the plan', `v${c.version}`],
      ['Count progress', `${c.done} / ${c.total}`],
      ['Find the next step', c.next || 'not set'],
    ],
    explain: [
      ['Search context', 'prefs / methods'],
      ['Locate the step', c.doing],
      ['Compose an explanation', 'definition / use'],
    ],
    smalltalk: [['Read the session', c.title]],
    content: [
      ['Read context', 'goal / preferences'],
      ['Locate the session', c.title],
      ['Compose a reply', 'discussion'],
    ],
    no_plan: [
      ['Read the session', c.title],
      ['Check the plan', 'not set'],
      ['Compose a reply', 'start with an entry'],
    ],
  };
  return ((lang === 'zh' ? zh : en)[key] || (lang === 'zh' ? zh.content : en.content))
    .map(([label, note]) => ({ label, note }));
}

function buildThinking(key, lang, c) {
  const zh = {
    plan_review: ['先看结构，不急着改内容。', `检查点：${c.checkpoint || '尚未建立'}。`, '把担心落到一个可验证的动作上。'],
    plan_change: ['先不动计划：分辨这是内容改动，还是时机改动。', `当前入口是「${c.doing}」；只动这一步，并在它后面插一段。`, '草案会先放在你面前；点头之前计划不变。'],
    plan_change_new: ['先确认现状：这个会话还没有计划。', `把「${c.key}」缩成一个能一次完成的入口。`, '草案先给到，写入与否由你决定。'],
    test_me: ['先不给答案，只出提取题。', `题目围绕「${c.doing}」。`, '答不出来也是信息。'],
    stuck: ['把「开始不了」当作入口问题。', `障碍：${c.obstacle}。`, '把动作缩到五分钟能完成。'],
    progress: ['先对账，不追加计划。', `进度：${c.done} / ${c.total}。`, '下一步比百分比更重要。'],
    explain: ['先分清定义和用法。', `定位到「${c.doing}」。`, '用一个例子和一个反例收口。'],
    smalltalk: ['读一下当前会话，再简短回应。', `下一步：${c.next || '未安排'}。`],
    content: ['把原话放回当前会话。', `定位入口：${c.doing}。`, '让回答落到一个具体产出。'],
    no_plan: ['先确认：这个会话还没有计划。', '没有计划时，出题与对账都会得到空读数。', '先立一个入口。'],
  };
  const en = {
    plan_review: ['Read the structure before changing it.', `Checkpoint: ${c.checkpoint || 'not set'}.`, 'Turn the worry into a verifiable action.'],
    plan_change: ['Do not touch the plan yet: is this a content change or a timing change?', `The current entry is “${c.doing}”; change only that step and insert one after it.`, 'The draft comes first; the plan stays put until you nod.'],
    plan_change_new: ['Confirm the state: this session has no plan yet.', `Shrink “${c.key}” into one entry that fits a single sitting.`, 'The draft comes first; writing it is your call.'],
    test_me: ['No answers yet; retrieval only.', `Questions sit on “${c.doing}”.`, 'Not being able to answer is information.'],
    stuck: ['Treat “I cannot start” as an entry problem.', `Obstacle: ${c.obstacle}.`, 'Shrink the move until it fits five minutes.'],
    progress: ['Reconcile first; do not add to the plan.', `Progress: ${c.done} / ${c.total}.`, 'The next step matters more than the percentage.'],
    explain: ['Separate definition from use first.', `Locate it in “${c.doing}”.`, 'Close with one example and one counterexample.'],
    smalltalk: ['Read the session, then reply briefly.', `Next step: ${c.next || 'not set'}.`],
    content: ['Put the words back into this round.', `Locate the entry: ${c.doing}.`, 'Make the reply land on a concrete output.'],
    no_plan: ['Confirm: this session has no plan yet.', 'Testing or reconciling without a plan yields empty readings.', 'Start with an entry.'],
  };
  return (lang === 'zh' ? zh : en)[key] || (lang === 'zh' ? zh.content : en.content);
}

function buildBasis(key, lang, c) {
  const zh = {
    plan_review: `依据：当前计划第 ${c.version} 版、检查点「${c.checkpoint || '未建立'}」，以及你这一轮的原话「${c.key}」。`,
    plan_change: `依据：你这一轮的原话「${c.key}」，以及当前计划第 ${c.version} 版里正在进行的「${c.doing}」。`,
    plan_change_new: `依据：你这一轮的原话「${c.key}」，以及这个会话还没有计划这一事实。`,
    test_me: `依据：当前计划第 ${c.version} 版与检查点「${c.checkpoint || '未建立'}」；这一轮只做提取，不给答案。`,
    stuck: `依据：你在动机里记下的障碍「${c.obstacle}」，以及当前入口「${c.firstAction}」。`,
    progress: `依据：当前计划第 ${c.version} 版的 ${c.done} / ${c.total} 步完成情况，以及下一步「${c.next || '尚未安排'}」。`,
    explain: `依据：当前会话「${c.title}」与计划第 ${c.version} 版的「${c.doing}」；这里不替你补背景事实。`,
    smalltalk: `依据：当前会话「${c.title}」与下一步「${c.next || '未安排'}」。`,
    content: `依据：当前会话「${c.title}」、正在进行「${c.doing}」，以及你这一轮的原话「${c.key}」。`,
    no_plan: `依据：这个会话还没有计划；这里只给出入口，不编造读数。`,
  };
  const en = {
    plan_review: `Basis: plan v${c.version}, the checkpoint “${c.checkpoint || 'not set'}”, and your words “${c.key}”.`,
    plan_change: `Basis: your words “${c.key}”, and “${c.doing}”, which the plan is on at v${c.version}.`,
    plan_change_new: `Basis: your words “${c.key}”, and the fact that this session has no plan yet.`,
    test_me: `Basis: plan v${c.version} and the checkpoint “${c.checkpoint || 'not set'}”; this round is retrieval only.`,
    stuck: `Basis: the obstacle you logged (“${c.obstacle}”) and the current entry “${c.firstAction}”.`,
    progress: `Basis: plan v${c.version} at ${c.done} / ${c.total}, and the next step “${c.next || 'not set'}”.`,
    explain: `Basis: “${c.title}” and “${c.doing}” in plan v${c.version}; no background facts are invented here.`,
    smalltalk: `Basis: “${c.title}” and the next step “${c.next || 'not set'}”.`,
    content: `Basis: “${c.title}”, the current entry “${c.doing}”, and your words “${c.key}”.`,
    no_plan: 'Basis: this session has no plan; only an entry is offered, no invented readings.',
  };
  return (lang === 'zh' ? zh : en)[key] || (lang === 'zh' ? zh.content : en.content);
}

function composeReplyHTML(body) {
  const parts = [`<p class="lede">${escapeHtml(body.lede)}</p>`];
  (body.paras || []).forEach((p) => { if (p) parts.push(`<p>${escapeHtml(p)}</p>`); });
  if (body.list?.length) {
    parts.push(`<ol class="prose-list numbered">${body.list.map((q) => `<li>${escapeHtml(q)}</li>`).join('')}</ol>`);
  }
  if (body.ask) parts.push(`<p class="muted">${escapeHtml(body.ask)}</p>`);
  return parts.join('');
}

function nextStepId() {
  return 'ST-' + String(Date.now()).slice(-6);
}

function starterPlan(lang) {
  if (lang === 'zh') {
    return {
      version: 1,
      steps: [
        { id: nextStepId(), action: '写下这一轮的目标与结束条件', status: 'doing', meta: '产出 · 一句话' },
        { id: nextStepId() + 'a', action: '做一个 20 分钟的启动回合', status: 'pending', meta: '动作 · 最小入口' },
        { id: nextStepId() + 'b', action: '检查点：不看笔记复述一次', status: 'pending', meta: '验证 · 1 次' },
      ],
      checkpoint: {
        title: '检查点 CP-01',
        text: '一次回合结束后，能复述今天的一个要点。',
        criteria: '通过标准 · 1 / 1；未通过则把回合再切小一半。',
      },
    };
  }
  return {
    version: 1,
    steps: [
      { id: nextStepId(), action: 'Write the goal and the end condition for this round', status: 'doing', meta: 'Output · one sentence' },
      { id: nextStepId() + 'a', action: 'Run one 20-minute starting round', status: 'pending', meta: 'Action · smallest entry' },
      { id: nextStepId() + 'b', action: 'Checkpoint: recall it once without notes', status: 'pending', meta: 'Verify · once' },
    ],
    checkpoint: {
      title: 'Checkpoint CP-01',
      text: 'After one round, restate one point from today.',
      criteria: 'Pass · 1 / 1; if not, halve the round again.',
    },
  };
}

function planChangeMode(detail, text) {
  if (!detail.plan || !detail.plan.steps?.length) return 'starter';
  const steps = detail.plan.steps;
  let idx = steps.findIndex((s) => s.status === 'doing');
  if (idx < 0) idx = steps.findIndex((s) => s.status === 'pending');
  if (idx < 0) idx = steps.length - 1;
  const action = steps[idx]?.action || '';
  if (/^(前半|后半|Part A|Part B)/.test(action)) return 'insert';
  return /拆|分两次|两天|两段|split|two days|two sessions/i.test(text) ? 'split' : 'insert';
}

function buildProposal({ detail, text, lang, c }) {
  const zh = lang === 'zh';
  if (!detail.plan || !detail.plan.steps?.length) {
    const nextPlan = starterPlan(lang);
    return {
      id: 'prop-' + Date.now(),
      version: nextPlan.version,
      title: zh ? '先建一个能落地的三步行计划' : 'Start with a three-step plan that fits',
      changes: nextPlan.steps.map((step, i) => ({
        mark: zh ? '增' : 'add',
        text: zh
          ? `新增第 ${i + 1} 步：<ins>${escapeHtml(step.action)}</ins>`
          : `Add step ${i + 1}: <ins>${escapeHtml(step.action)}</ins>`,
      })),
      basis: zh
        ? `你这一轮的原话「${c.key}」，以及这个会话还没有计划。草案只写三步，不引入演示分数。`
        : `Your words “${c.key}”, and this session has no plan yet. The draft stays at three steps and invents no readings.`,
      nextPlan,
      state: 'pending',
    };
  }

  const nextPlan = structuredClone(detail.plan);
  if (!nextPlan.steps.length) {
    nextPlan.steps = starterPlan(lang).steps;
  }
  let idx = nextPlan.steps.findIndex((s) => s.status === 'doing');
  if (idx < 0) idx = nextPlan.steps.findIndex((s) => s.status === 'pending');
  if (idx < 0) idx = nextPlan.steps.length - 1;
  const target = nextPlan.steps[idx];
  const original = target.action;
  const originalCheckpoint = nextPlan.checkpoint?.text || '';
  const split = c.proposalMode === 'split';
  nextPlan.version = (detail.plan.version || 0) + 1;

  let title;
  let changes;
  if (split) {
    const first = zh ? `前半 · ${original}` : `Part A · ${original}`;
    const second = {
      id: nextStepId(),
      action: zh ? '后半 · 先测后读，再回看例句' : 'Part B · test first, then reread',
      status: 'pending',
      meta: zh ? '新增 · 提取优先' : 'Added · retrieval first',
    };
    target.action = first;
    nextPlan.steps.splice(idx + 1, 0, second);
    const newCheckpointText = zh
      ? '前半与后半都完成后，按原标准复述；未通过则回到后半的先测后读。'
      : 'After both halves, recall to the original standard; if it fails, return to the test-first half.';
    if (nextPlan.checkpoint) {
      nextPlan.checkpoint = { ...nextPlan.checkpoint, text: newCheckpointText };
    }
    changes = [
      {
        mark: zh ? '改' : 'edit',
        text: zh
          ? `第 ${idx + 1} 步：<del>${escapeHtml(original)}</del> → <ins>${escapeHtml(first)}</ins>`
          : `Step ${idx + 1}: <del>${escapeHtml(original)}</del> → <ins>${escapeHtml(first)}</ins>`,
      },
      {
        mark: zh ? '增' : 'add',
        text: zh
          ? `新增第 ${idx + 2} 步：<ins>${escapeHtml(second.action)}</ins>`
          : `Add step ${idx + 2}: <ins>${escapeHtml(second.action)}</ins>`,
      },
    ];
    if (originalCheckpoint && newCheckpointText !== originalCheckpoint) {
      changes.push({
        mark: zh ? '改' : 'edit',
        text: zh
          ? `检查点：<del>${escapeHtml(originalCheckpoint)}</del> → <ins>${escapeHtml(newCheckpointText)}</ins>`
          : `Checkpoint: <del>${escapeHtml(originalCheckpoint)}</del> → <ins>${escapeHtml(newCheckpointText)}</ins>`,
      });
    }
    title = zh ? '把当前这一步拆成两段' : 'Split the current step into two';
  } else {
    const added = {
      id: nextStepId(),
      action: zh ? `调整 · ${c.key}` : `Adjustment · ${c.key}`,
      status: 'pending',
      meta: zh ? '新增 · 按你的要求' : 'Added · per your request',
    };
    nextPlan.steps.splice(idx + 1, 0, added);
    changes = [{
      mark: zh ? '增' : 'add',
      text: zh
        ? `新增第 ${idx + 2} 步：<ins>${escapeHtml(added.action)}</ins>`
        : `Add step ${idx + 2}: <ins>${escapeHtml(added.action)}</ins>`,
    }];
    title = zh ? '按你的要求插入一步' : 'Insert a step, per your request';
  }

  return {
    id: 'prop-' + Date.now(),
    version: nextPlan.version,
    title,
    changes,
    basis: zh
      ? `你这一轮的原话「${c.key}」，以及当前计划第 ${detail.plan.version} 版里正在进行的「${original}」。`
      : `Your words “${c.key}” and “${original}”, which the plan is on at v${detail.plan.version}.`,
    nextPlan,
    state: 'pending',
  };
}

function buildReplyPlan(text, detail, session) {
  const lang = detectReplyLang(text);
  const intent = classifyIntent(text);
  const c = replyContext(detail, session, text, lang);
  const hasPlan = !!(detail.plan && detail.plan.steps?.length);
  let key = intent;
  if (!hasPlan && intent === 'plan_change') key = 'plan_change_new';
  else if (!hasPlan && ['plan_review', 'test_me', 'progress'].includes(intent)) key = 'no_plan';

  if (key === 'plan_change' || key === 'plan_change_new') {
    c.proposalMode = planChangeMode(detail, text);
  }

  const variants = REPLY_VARIANTS[lang]?.[key] || REPLY_VARIANTS[lang].content;
  const variant = pickReplyVariant(detail, key, text, variants.length);
  const body = variants[variant](c);
  const traces = buildTraces(key, lang, c);
  const notes = buildThinking(key, lang, c);
  const proposal = (key === 'plan_change' || key === 'plan_change_new')
    ? buildProposal({ detail, text, lang, c })
    : null;

  return {
    intent, lang, key, variant,
    html: composeReplyHTML(body),
    traces,
    notes,
    thinking: notes[notes.length - 1] || '',
    statusWords: STATUS_WORDS[lang]?.[key] || STATUS_WORDS[lang].content,
    proposal,
    basis: buildBasis(key, lang, c),
  };
}

/* ── sidebar ──────────────────────────────────────────────────────── */
function matchesSearch(text) {
  if (!state.search) return true;
  return String(text).toLowerCase().includes(state.search.toLowerCase());
}

function sessionMarkup(session, folderId) {
  const isCurrent = session.id === state.currentSession;
  return `<li class="session${isCurrent ? ' is-current' : ''}" data-session-id="${session.id}" data-folder-id="${folderId}" role="button" tabindex="0" aria-current="${isCurrent}">
    <span class="state-dot ${session.state}"></span>
    <span class="session-title">${escapeHtml(session.title)}</span>
    <time>${escapeHtml(session.time)}</time>
    <button class="row-menu" data-action="session-menu" data-id="${session.id}" aria-label="${t('common.rename')} / ${t('common.delete')}">${icon('dots')}</button>
  </li>`;
}

function folderMarkup(folder) {
  let sessions = folder.sessions.filter((s) => matchesSearch(s.title));
  if (!matchesSearch(folder.name) && sessions.length === 0) return '';
  if (matchesSearch(folder.name)) sessions = folder.sessions;
  const open = folder.open || !!state.search || folder.sessions.some((s) => s.id === state.currentSession);
  return `<div class="folder${open ? ' is-open' : ''}" data-folder-id="${folder.id}">
    <div class="folder-head" data-action="toggle-folder" data-id="${folder.id}" role="button" tabindex="0" aria-expanded="${open}">
      ${icon('chev', 'chev')}
      ${icon('folder', 'folder-glyph')}
      <span class="folder-name">${escapeHtml(folder.name)}</span>
      <span class="folder-count">${folder.sessions.length}</span>
      <button class="row-menu" data-action="folder-menu" data-id="${folder.id}" aria-label="${t('common.rename')} / ${t('common.delete')}">${icon('dots')}</button>
    </div>
    <ul class="sessions"${open ? '' : ' hidden'}>${sessions.map((s) => sessionMarkup(s, folder.id)).join('')}</ul>
  </div>`;
}

function renderRail() {
  folderList.innerHTML = state.folders.map(folderMarkup).join('')
    || `<div class="archived-empty">${L('还没有领域 · 点上方按钮新建', 'No fields yet · create one above')}</div>`;
  $('#folder-count').textContent = pad(state.folders.length);

  const archived = [];
  for (const s of state.archivedSessions) {
    if (!matchesSearch(s.title) && !matchesSearch(s.folderName)) continue;
    archived.push(`<li class="session archived-item" data-archived-session="${s.id}" role="button" tabindex="0">
      <span class="state-dot ${s.state}"></span>
      <span class="session-title">${escapeHtml(s.title)}</span>
      <time>${escapeHtml(s.time)}</time>
      <button class="row-menu" data-action="archived-session-menu" data-id="${s.id}" aria-label="归档操作">${icon('dots')}</button>
    </li>`);
  }
  for (const f of state.archivedFolders) {
    if (!matchesSearch(f.name)) continue;
    archived.push(`<li class="session archived-item" data-archived-folder="${f.id}" role="button" tabindex="0">
      <span class="state-dot completed"></span>
      <span class="session-title">${escapeHtml(f.name)}</span>
      <time>${f.sessionCount}</time>
      <button class="row-menu" data-action="archived-folder-menu" data-id="${f.id}" aria-label="归档操作">${icon('dots')}</button>
    </li>`);
  }
  archiveList.innerHTML = archived.join('') || `<div class="archived-empty">${L('归档是空的', 'Nothing archived')}</div>`;
  archiveList.hidden = !(state.archiveOpen || !!state.search);
  $('#archive-toggle').setAttribute('aria-expanded', String(state.archiveOpen));
  $('#archive-count').textContent = pad(state.archivedSessions.length + state.archivedFolders.length);

  const prof = activeProfile();
  $('#model-row-name').textContent = prof.short;
  $('#model-row-note').textContent = prof.provider === 'Ollama'
    ? L(`本地 Ollama · ${prof.ctx} 上下文`, `Local Ollama · ${prof.ctx} context`)
    : L(`外部 API · ${prof.ctx} 上下文`, `External API · ${prof.ctx} context`);

  const badge = $('#bench-badge');
  if (state.bench.status === 'running' || state.bench.status === 'interrupted') {
    badge.hidden = false;
    badge.textContent = state.bench.status === 'running' ? L('进行中', 'running') : L('未完成', 'unfinished');
  } else {
    badge.hidden = true;
  }
}

/* ── topbar ───────────────────────────────────────────────────────── */
function renderTopbar() {
  const session = currentSession();
  const folder = session ? findFolder(session.folderId) : null;
  $('#crumb-folder').textContent = folder ? folder.name : L('未选择领域', 'No field');
  $('#crumb-session').textContent = session ? session.title : L('尚未选择会话', 'No session selected');

  const pill = $('#session-state');
  const key = session ? session.state : 'draft';
  pill.className = 'state-chip is-' + key;
  pill.querySelector('span').textContent = stateLabel(key);

  const prof = activeProfile();
  $('#model-chip-name').textContent = prof.short;
  $('#compose-model-name').textContent = prof.short;
  $('#compose-busy-rule').textContent = t('composer.busyPrefix') + t('busy.' + state.busyBehavior);
  document.title = session ? `${session.title} · 觅学之契` : '觅学之契 · 静室';
}

/* ── thread ───────────────────────────────────────────────────────── */
function thinkMarkup(traces, thinking, open) {
  if ((!traces || !traces.length) && !thinking) return '';
  const lines = (traces || []).map((tr) => `<div class="think-line"><b>${escapeHtml(tr.label)}</b><span>${escapeHtml(tr.note || '')}</span></div>`).join('');
  return `<details class="think"${open ? ' open' : ''}>
    <summary>${icon('chev', 'chev')}<span>${L('思考', 'Reasoning')} · ${(traces || []).length} ${L('步', 'steps')}</span></summary>
    <div class="think-body">${lines}${thinking ? `<p class="think-note">${escapeHtml(thinking)}</p>` : ''}</div>
  </details>`;
}

function proposalMarkup(proposal) {
  const resolved = proposal.state && proposal.state !== 'pending';
  const verText = resolved
    ? L(`第 ${proposal.version} 版 · 已处理`, `v${proposal.version} · resolved`)
    : L(`第 ${proposal.version} 版 · 待确认`, `v${proposal.version} · awaiting`);
  const resolution = resolved
    ? `<div class="proposal-resolution">${proposal.state === 'approved'
      ? L(`已写入 · 计划第 ${proposal.version} 版`, `Written · plan v${proposal.version}`)
      : L('未写入 · 保留原计划', 'Declined · plan unchanged')}</div>`
    : '';
  const changes = proposal.changes.map((chg) => `<li><span class="chg-mark${chg.mark === '增' || chg.mark === 'add' ? ' add' : ''}">${escapeHtml(chg.mark)}</span><div>${chg.text}</div></li>`).join('');
  return `<div class="proposal${resolved ? ' is-resolved' : ''}" data-proposal-id="${escapeHtml(proposal.id)}">
    <div class="proposal-top">
      <span class="proposal-tag">${L('计划草案', 'Plan draft')}</span>
      <span class="proposal-ver">${verText}</span>
    </div>
    <h4>${escapeHtml(proposal.title)}</h4>
    <ul class="proposal-list">${changes}</ul>
    <div class="proposal-basis"><b>${L('依据', 'Basis')}</b> · ${escapeHtml(proposal.basis)}</div>
    <div class="proposal-actions">
      <button class="mini solid" data-action="approve-proposal" data-id="${escapeHtml(proposal.id)}">${L('写入计划 · 同意', 'Write to plan')}</button>
      <button class="mini" data-action="reject-proposal" data-id="${escapeHtml(proposal.id)}">${L('先不动', 'Not now')}</button>
      <button class="text-link" data-action="proposal-basis">${L('看看依据', 'See basis')}</button>
    </div>
    ${resolution}
  </div>`;
}

function messageMarkup(message, index, assistantNo) {
  if (message.role === 'user') {
    return `<article class="turn turn-user" style="--i:${index}">
      <div class="turn-body">
        <div class="user-card">
          <p>${escapeHtml(message.text || '')}</p>
          <div class="user-meta">
            <span>${L('你', 'You')}</span><span>·</span><time>${escapeHtml(message.time || '')}</time>
            ${message.queued ? `<span class="q-tag">${L('排队中', 'Queued')}</span>` : ''}
          </div>
        </div>
      </div>
    </article>`;
  }

  const traces = message.traces || [];
  const meta = message.interrupted
    ? `<span class="turn-meta is-warn">${L('已中断', 'Interrupted')}</span>`
    : `<span class="turn-meta">${traces.length ? `${traces.length} ${L('个动作', 'actions')}` : L('回答', 'Answer')}</span>`;
  const foot = message.foot
    ? `<div class="turn-foot">
        <button data-action="show-basis" data-basis="${escapeHtml(message.basis || L('依据是本轮的工具轨迹、你的偏好原话，以及当前计划版本。', 'Basis: this round\'s tool trace, your own words, and the current plan version.'))}">${L('为什么这样建议', 'Why this advice')}</button>
        <span class="dot"></span>
        <button data-action="annotate">${L('标注一句', 'Annotate')}</button>
        <span class="dot"></span>
        <span>${traces.length ? `${traces.length} ${L('个工具动作', 'tool actions')}` : L('未动工具', 'no tools')}</span>
      </div>`
    : '';

  return `<article class="turn turn-assistant" style="--i:${index}" data-msg-id="${escapeHtml(message.id || '')}">
    <div class="turn-gutter"><span class="turn-index">${pad(assistantNo)}</span></div>
    <div class="turn-body">
      <header class="turn-head">
        <span class="turn-role"><span class="role-mark">契</span><em>MOUXUE</em></span>
        <time>${escapeHtml(message.time || '')}</time>
        ${meta}
      </header>
      ${thinkMarkup(traces, message.thinking, state.thinkView === 'open')}
      <div class="prose">${message.html || ''}</div>
      ${message.proposal ? proposalMarkup(message.proposal) : ''}
      ${foot}
    </div>
  </article>`;
}

function emptyThreadMarkup(session) {
  const prompts = L(
    ['我想在两周内读懂一篇论文的摘要。', '我不知道今天该复习什么。', '先帮我看看这个计划哪里不合理。'],
    ['I want to read a paper abstract in two weeks.', 'I do not know what to review today.', 'Look at my plan and tell me what is off.'],
  );
  return `<div class="empty-thread">
    <span class="empty-mark" aria-hidden="true">
      <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
        <path d="M7 24.5c5.2 0 9.5-4.3 9.5-9.5S12.2 5.5 7 5.5"/>
        <path d="M17 26.5c5.2 0 9.5-4.3 9.5-9.5S22.2 7.5 17 7.5" opacity=".5"/>
        <circle cx="23.4" cy="22.4" r="2.3" fill="currentColor" stroke="none"/>
      </svg>
    </span>
    <h2>${L('从这里开始，把学习写成一条线。', 'Begin here, and keep learning on a line.')}</h2>
    <p>${L('第一句话之前，计划与偏好都不会被改写。任何写入，都会先把草案交给你。', 'Nothing in your plan or preferences changes before your first sentence. Any write arrives as a draft first.')}</p>
    <div class="prompt-hints">
      ${prompts.map((p) => `<button data-prompt="${escapeHtml(p)}">${escapeHtml(p)}</button>`).join('')}
    </div>
  </div>`;
}

function renderThread() {
  const session = currentSession();
  const detail = activeDetail();
  if (!session || !detail) {
    thread.innerHTML = `<div class="empty-thread">
      <span class="empty-mark" aria-hidden="true">${icon('book')}</span>
      <h2>${L('桌面上还没有会话', 'No sessions yet')}</h2>
      <p>${L('先新建一个领域，再写下第一句。', 'Create a field first, then write the first line.')}</p>
    </div>`;
    threadEnd.hidden = true;
    return;
  }
  if (detail.messages.length === 0) {
    thread.innerHTML = emptyThreadMarkup(session);
    threadEnd.hidden = true;
    return;
  }
  let assistantNo = 0;
  const html = detail.messages.map((m, i) => {
    if (m.role === 'assistant') assistantNo += 1;
    return messageMarkup(m, i, assistantNo);
  }).join('');
  thread.innerHTML = html;
  threadEnd.hidden = false;
}

/* ── margin ───────────────────────────────────────────────────────── */
function renderMargin() {
  const tabs = $$('.margin-tab');
  const idx = tabs.findIndex((tab) => tab.dataset.tab === state.marginTab);
  $('#margin-indicator').style.transform = `translateX(${Math.max(0, idx) * 100}%)`;
  tabs.forEach((tab) => {
    const on = tab.dataset.tab === state.marginTab;
    tab.classList.toggle('is-active', on);
    tab.setAttribute('aria-selected', String(on));
  });

  const detail = activeDetail();
  if (!detail) {
    marginBody.innerHTML = `<div class="mpanel"><p class="mp-sub">${L('选择一个会话后，这里会显示它的动机、计划与上下文。', 'Select a session to see its motivation, plan and context.')}</p></div>`;
    return;
  }
  if (state.marginTab === 'motivation') marginBody.innerHTML = motivationMarkup(detail.motivation);
  else if (state.marginTab === 'context') marginBody.innerHTML = contextMarkup(detail.context);
  else marginBody.innerHTML = planMarkup(detail.plan);
}

function motivationMarkup(motivation) {
  if (!motivation) {
    return `<div class="mpanel">
      <div class="mp-head"><h3>${t('margin.motivation')}</h3><span class="mp-ver">${L('未建立', 'none')}</span></div>
      <p class="mp-sub">${L('这个会话还没有写下动机。动机可以随时改，且每次修改都会留下一条版本。', 'No motivation has been written for this session yet. It can change anytime, and each edit leaves a version.')}</p>
      <div class="mp-actions"><button class="mini" data-action="edit-motivation">${L('写下动机', 'Write it down')}</button></div>
    </div>`;
  }
  const bars = [1, 2, 3].map((n) => `<i class="${n <= motivation.strength ? 'on' : ''}"></i>`).join('');
  return `<div class="mpanel">
    <div class="mp-head"><h3>${t('margin.motivation')}</h3><span class="mp-ver">${L('当前', 'current')}</span></div>
    <div class="mot-type"><strong>${escapeHtml(motivation.type)}</strong><em>${escapeHtml(motivation.typeNote)}</em></div>
    <div class="strength">${bars}</div>
    <div class="strength-labels"><span>${L('低', 'low')}</span><span>${L('中', 'mid')}</span><span>${L('高', 'high')}</span></div>
    <div class="m-section">
      <div class="m-section-head"><span>${L('驱动 · 它为什么值得', 'Drivers · why it matters')}</span><em>${motivation.drivers.length}</em></div>
      <ul class="driver-list">${motivation.drivers.map((d) => `<li>${escapeHtml(d)}</li>`).join('')}</ul>
    </div>
    <div class="m-section">
      <div class="m-section-head"><span>${L('障碍 · 什么会挡住它', 'Obstacles · what gets in the way')}</span><em>${motivation.obstacles.length}</em></div>
      <ul class="obstacle-list">${motivation.obstacles.map((o) => `<li><strong>${escapeHtml(o.title)}</strong><span>${escapeHtml(o.note)}</span></li>`).join('')}</ul>
    </div>
    <div class="revision-note"><b>${L('最近改动', 'Last change')}</b><span>${escapeHtml(motivation.revision)}</span></div>
    <div class="mp-actions">
      <button class="mini" data-action="edit-motivation">${L('改一项', 'Edit')}</button>
      <button class="mini" data-action="motivation-history">${L('版本历史', 'History')}</button>
    </div>
  </div>`;
}

function planMarkup(plan) {
  if (!plan) {
    return `<div class="mpanel">
      <div class="mp-head"><h3>${t('margin.plan')}</h3><span class="mp-ver">${L('未建立', 'none')}</span></div>
      <p class="mp-sub">${L('还没有计划。拆解与写入都会先给你一张草案——点一次计划项，不等于同意任何修改。', 'No plan yet. Breakdowns and writes arrive as drafts — ticking a step is not consent to change anything.')}</p>
      <div class="mp-actions"><button class="mini" data-action="ask-plan">${L('请模型拆解任务', 'Ask for a breakdown')}</button></div>
    </div>`;
  }
  const done = plan.steps.filter((s) => s.status === 'done').length;
  const pct = Math.round((done / plan.steps.length) * 100);
  return `<div class="mpanel">
    <div class="mp-head"><h3>${t('margin.plan')} · ${L(`第 ${plan.version} 版`, `v${plan.version}`)}</h3><span class="mp-ver">${done} / ${plan.steps.length} ${L('完成', 'done')}</span></div>
    <div class="plan-progress"><div class="plan-bar"><i style="--p:${pct}%"></i></div><span>${pct}%</span></div>
    <ul class="steps">
      ${plan.steps.map((step) => `<li class="step is-${step.status}" data-action="toggle-step" data-step-id="${step.id}" role="button" tabindex="0">
        <span class="step-check">${icon('check')}</span>
        <div class="step-body">
          <div class="step-action">${escapeHtml(step.action)}</div>
          <div class="step-meta"><span class="status-tag">${statusLabel(step.status)}</span><span>·</span><span>${escapeHtml(step.meta || '')}</span></div>
        </div>
      </li>`).join('')}
    </ul>
    <div class="checkpoint">
      <div class="checkpoint-head"><span>${escapeHtml(plan.checkpoint.title)}</span><span>${L('通过标准', 'pass mark')}</span></div>
      <p>${escapeHtml(plan.checkpoint.text)}</p>
      <p class="cp-criteria"><b>${escapeHtml(plan.checkpoint.criteria)}</b></p>
    </div>
    <div class="mp-actions">
      <button class="mini" data-action="ask-change">${L('请模型改一步', 'Ask for one change')}</button>
      <button class="mini" data-action="plan-history">${L('版本历史', 'History')}</button>
    </div>
  </div>`;
}

function contextMarkup(context) {
  if (!context || !context.sections.length) {
    return `<div class="mpanel">
      <div class="mp-head"><h3>${t('margin.context')}</h3><span class="mp-ver">0%</span></div>
      <p class="mp-sub">${escapeHtml(context ? context.note : L('尚未产生上下文。', 'No context yet.'))}</p>
    </div>`;
  }
  const segments = context.segments.map((w) => `<i style="width:${w}%"></i>`).join('');
  return `<div class="mpanel">
    <div class="mp-head"><h3>${L('上下文 · 它看见的', 'Context · what it sees')}</h3><span class="mp-ver">${context.pct}%</span></div>
    <div class="token-meter">
      <div class="token-meter-top"><span>${L('窗口占用', 'Window used')}</span><span><strong>${context.used}</strong> / ${context.total}</span></div>
      <div class="token-bar">${segments}</div>
      <div class="token-legend"><span>${L('偏好', 'Prefs')}</span><span>${L('方法卡', 'Methods')}</span><span>${L('基线', 'Baseline')}</span><span>${L('策略库', 'Strategy')}</span></div>
    </div>
    <div class="m-section">
      <div class="m-section-head"><span>${L('采用序', 'Assembly order')}</span><em>${L('偏好 → 方法卡 → 基线', 'prefs → methods → baseline')}</em></div>
      <ul class="ctx-list">
        ${context.sections.map((s) => `<li><span>${escapeHtml(s.name)}</span><em>${escapeHtml(s.value)}</em></li>`).join('')}
      </ul>
    </div>
    <p class="ctx-note">${escapeHtml(context.note)}</p>
    <div class="mp-actions"><button class="mini" data-action="inspect-context">${L('查看逐条来源', 'Inspect sources')}</button></div>
  </div>`;
}

function renderSession() {
  renderTopbar();
  renderThread();
  renderMargin();
  renderRail();
}

/* ── views ────────────────────────────────────────────────────────── */
function setView(view) {
  state.view = view;
  $$('.view').forEach((el) => el.classList.toggle('is-active', el.dataset.view === view));
  $$('.sb-link').forEach((el) => el.classList.toggle('is-active', el.dataset.view === view));
  if (window.innerWidth <= 980) closeRail();
  if (view === 'settings') renderSettings();
  if (view === 'benchmark') renderBench();
  if (view === 'chat') threadWrap.scrollTop = threadWrap.scrollHeight;
}

/* ── busy / run ───────────────────────────────────────────────────── */
function updateBusyUI() {
  const prof = activeProfile();
  composer.classList.toggle('is-busy', state.busy);
  $('#compose-status').hidden = !state.busy;
  $$('.live-dot').forEach((dot) => dot.classList.toggle('is-busy', state.busy));
  const blocked = state.busy && state.busyBehavior === 'block';
  sendBtn.classList.toggle('is-disabled', blocked);
  sendBtn.setAttribute('aria-disabled', String(blocked));
  $('#compose-busy-rule').textContent = t('composer.busyPrefix') + t('busy.' + state.busyBehavior);
  const statusNote = $('#compose-status-note');
  if (statusNote) statusNote.textContent = state.busy ? prof.short : '';
  const strip = $('#queue-strip');
  if (state.queue.length) {
    strip.hidden = false;
    strip.textContent = L(
      `排队中 · ${state.queue.length} 条 · 当前回答结束后依次发送`,
      `${state.queue.length} queued · sent after the current answer`,
    );
  } else {
    strip.hidden = true;
  }
}

function startStatusCycle(run, words) {
  const el = $('#compose-status-word');
  let i = 0;
  if (el) el.textContent = words[0];
  clearInterval(run.statusTimer);
  run.statusTimer = setInterval(() => {
    i = (i + 1) % words.length;
    if (el) el.textContent = words[i];
  }, 1700);
}

function createLiveTurn() {
  const detail = activeDetail();
  const no = detail.messages.filter((m) => m.role === 'assistant').length + 1;
  const article = document.createElement('article');
  article.className = 'turn turn-assistant is-live';
  article.innerHTML = `
    <div class="turn-gutter"><span class="turn-index">${pad(no)}</span></div>
    <div class="turn-body">
      <header class="turn-head">
        <span class="turn-role"><span class="role-mark">契</span><em>MOUXUE</em></span>
        <time>${clock()}</time>
        <span class="turn-meta is-live">${L('推演中', 'Working')}</span>
      </header>
      <div class="tool-row is-live"></div>
      <details class="think is-live" open>
        <summary>${icon('chev', 'chev')}<span class="think-summary">${L('思考', 'Reasoning')} · ${L('进行中', 'live')}</span></summary>
        <div class="think-body"><p class="think-note"></p></div>
      </details>
      <div class="ghost-lines" aria-hidden="true"><i></i><i></i><i></i></div>
      <div class="prose is-streaming"></div>
    </div>`;
  thread.appendChild(article);
  threadEnd.hidden = true;
  scrollThread(true);
  return article;
}

function addTrace(article, label, note) {
  const row = article.querySelector('.tool-row');
  if (!row) return;
  const chip = document.createElement('span');
  chip.className = 'tool-chip';
  chip.title = note;
  chip.innerHTML = `<i></i><span>${escapeHtml(label)}</span>`;
  row.appendChild(chip);
  scrollThread();
}

function setThinkNote(article, text) {
  const note = article.querySelector('.think-note');
  if (note) note.textContent = text;
}

function collectTraces(article) {
  return $$('.tool-chip', article).map((chip) => ({
    label: chip.querySelector('span')?.textContent.trim() || chip.textContent.trim(),
    note: chip.title || '',
  }));
}

async function startAssistantRun({ userText = '' } = {}) {
  if (state.busy) return;
  const detail = activeDetail();
  const session = currentSession();
  if (!detail || !session) return;

  const text = String(
    userText
    || [...detail.messages].reverse().find((m) => m.role === 'user')?.text
    || '',
  ).trim();

  let plan;
  try {
    plan = buildReplyPlan(text, detail, session);
  } catch (error) {
    console.error(error);
    toast(L('这一轮没能组织出回答，请再发一次。', 'This reply could not be composed. Try again.'), 'danger');
    return;
  }

  const run = {
    id: 'R-' + Date.now(),
    sessionId: session.id,
    detail,
    cancelled: false,
    article: null,
    statusTimer: null,
  };
  state.run = run;
  state.busy = true;
  updateBusyUI();

  const article = createLiveTurn();
  run.article = article;
  threadWrap.classList.add('is-thinking');
  startStatusCycle(run, plan.statusWords);

  const step = async (ms) => {
    await wait(ms);
    return !run.cancelled && state.run === run && state.currentSession === run.sessionId;
  };

  for (let i = 0; i < plan.traces.length; i += 1) {
    if (!(await step(i === 0 ? 780 : 680))) return;
    const trace = plan.traces[i];
    addTrace(article, trace.label, trace.note);
    setThinkNote(article, plan.notes[Math.min(i, plan.notes.length - 1)] || '');
  }
  if (!(await step(420))) return;

  const liveThink = article.querySelector('.think.is-live');
  const thinkText = plan.thinking;
  const traces = collectTraces(article);
  if (liveThink) liveThink.remove();
  article.querySelector('.tool-row')?.remove();

  const prose = article.querySelector('.prose');
  article.querySelector('.ghost-lines')?.remove();
  prose.insertAdjacentHTML('beforebegin', thinkMarkup(traces, thinkText, state.thinkView === 'open'));
  prose.innerHTML = plan.html;
  const lastNode = prose.lastElementChild;
  if (lastNode) lastNode.insertAdjacentHTML('beforeend', '<span class="stream-caret" aria-hidden="true"></span>');
  scrollThread();

  const readMs = state.motion === 'restrained' ? 900 : Math.min(2600, 800 + plan.html.length * 2.5);
  if (!(await step(readMs))) return;

  prose.classList.remove('is-streaming');
  prose.querySelector('.stream-caret')?.remove();
  article.classList.remove('is-live');
  const meta = article.querySelector('.turn-meta');
  if (meta) {
    meta.classList.remove('is-live');
    meta.textContent = L(`回答 · ${traces.length} 个动作`, `Answer · ${traces.length} actions`);
  }

  if (plan.proposal) {
    article.querySelector('.turn-body').insertAdjacentHTML('beforeend', proposalMarkup(plan.proposal));
  }

  const foot = document.createElement('div');
  foot.className = 'turn-foot';
  foot.innerHTML = `<button data-action="show-basis" data-basis="${escapeHtml(plan.basis)}">${L('为什么这样建议', 'Why this advice')}</button>
    <span class="dot"></span><button data-action="annotate">${L('标注一句', 'Annotate')}</button><span class="dot"></span>
    <span>${traces.length} ${L('个工具动作', 'tool actions')}</span>`;
  article.querySelector('.turn-body').appendChild(foot);

  detail.messages.push({
    id: 'M-' + Date.now(),
    role: 'assistant',
    time: clock(),
    html: plan.html,
    traces,
    thinking: thinkText,
    proposal: plan.proposal,
    foot: true,
    basis: plan.basis,
    intent: plan.intent,
    lang: plan.lang,
  });

  state.run = null;
  state.busy = false;
  threadWrap.classList.remove('is-thinking');
  clearInterval(run.statusTimer);
  threadEnd.hidden = false;
  updateBusyUI();
  scrollThread(true);

  if (state.queue.length) {
    const next = state.queue.shift();
    if (next.el) next.el.remove();
    updateBusyUI();
    await wait(320);
    beginUserTurn(next.text);
  }
}

function cancelRun({ interrupted = false, silent = false } = {}) {
  const run = state.run;
  if (!run) return;
  run.cancelled = true;
  clearInterval(run.statusTimer);
  const article = run.article;
  const detail = run.detail || activeDetail();
  const hadQueue = state.queue.length;
  const traces = article ? collectTraces(article) : [];
  const thinkText = article?.querySelector('.think-note')?.textContent || '';
  let persistedHtml = '';

  if (article) {
    article.classList.remove('is-live');
    const prose = article.querySelector('.prose');
    prose?.querySelector('.stream-caret')?.remove();
    persistedHtml = prose?.innerHTML.trim() || `<p class="muted">${L('这一轮在生成完成前被停止。', 'This round was stopped before the answer completed.')}</p>`;
    if (prose) prose.innerHTML = persistedHtml;
    article.querySelector('.ghost-lines')?.remove();
    article.querySelector('.tool-row')?.remove();
    article.querySelector('.think.is-live')?.remove();
    article.querySelector('.turn-meta')?.remove();
    const head = article.querySelector('.turn-head');
    if (head) {
      const meta = document.createElement('span');
      meta.className = 'turn-meta is-warn';
      meta.textContent = interrupted ? L('已中断', 'Interrupted') : L('已停止', 'Stopped');
      head.appendChild(meta);
    }
    if (thinkMarkup(traces, thinkText, state.thinkView === 'open')) {
      prose?.insertAdjacentHTML('beforebegin', thinkMarkup(traces, thinkText, state.thinkView === 'open'));
    }
    if (detail) {
      detail.messages.push({
        role: 'assistant', time: clock(), html: persistedHtml, traces,
        thinking: thinkText, interrupted: true, foot: false,
      });
    }
  }

  state.run = null;
  state.busy = false;
  threadWrap.classList.remove('is-thinking');
  state.queue.forEach((item) => item.el?.remove());
  state.queue = [];
  updateBusyUI();
  if (interrupted && !silent) {
    toast(hadQueue
      ? L('已停止生成 · 排队内容一并清空', 'Stopped · queued messages cleared')
      : L('已停止生成 · 未完成部分保留在记录里', 'Stopped · the unfinished part stays in the record'));
  }
}

function beginUserTurn(text) {
  const detail = activeDetail();
  if (!detail || !state.currentSession) return;
  const message = { role: 'user', time: clock(), text };
  detail.messages.push(message);

  if (detail.messages.filter((m) => m.role === 'user').length === 1 && thread.querySelector('.empty-thread')) {
    renderThread();
  } else {
    const holder = document.createElement('div');
    holder.innerHTML = messageMarkup(message, detail.messages.length - 1, 0);
    thread.appendChild(holder.firstElementChild);
  }
  threadEnd.hidden = false;
  scrollThread(true);

  startAssistantRun({ userText: text });
}

function sendFromComposer({ programmaticText = null } = {}) {
  const text = (programmaticText ?? composerInput.value).trim();
  if (!text) return;

  if (state.busy) {
    if (state.busyBehavior === 'block') {
      composer.classList.add('is-shake');
      setTimeout(() => composer.classList.remove('is-shake'), 420);
      toast(L('当前回答还在进行 · 发送被暂时挂起', 'The current answer is still running · send is on hold'), 'danger');
      return;
    }
    if (state.busyBehavior === 'queue') {
      composerInput.value = '';
      autoGrow();
      const detail = activeDetail();
      const item = { text, el: null };
      if (detail) {
        const holder = document.createElement('div');
        holder.innerHTML = messageMarkup({ role: 'user', time: clock(), text, queued: true }, detail.messages.length, 0);
        const node = holder.firstElementChild;
        node.classList.add('is-queued');
        thread.appendChild(node);
        item.el = node;
        scrollThread(true);
      }
      state.queue.push(item);
      updateBusyUI();
      toast(L('已排队 · 当前回答结束后发送', 'Queued · sent after the current answer'));
      return;
    }
    if (state.busyBehavior === 'interrupt') {
      composerInput.value = '';
      autoGrow();
      cancelRun({ interrupted: true, silent: true });
      toast(L('已打断上一轮 · 新的消息开始发送', 'Previous round interrupted · sending the new message'));
      setTimeout(() => beginUserTurn(text), 240);
      return;
    }
  }

  composerInput.value = '';
  autoGrow();
  beginUserTurn(text);
}

/* ── popovers ─────────────────────────────────────────────────────── */
function closePopover() {
  popLayer.innerHTML = '';
}

function openPopover(anchor, html, className = '') {
  closePopover();
  const rect = anchor instanceof Element ? anchor.getBoundingClientRect() : anchor;
  const el = document.createElement('div');
  el.className = 'popover' + (className ? ' ' + className : '');
  el.innerHTML = html;
  popLayer.appendChild(el);
  const width = el.offsetWidth;
  const height = el.offsetHeight;
  let left = rect.left;
  if (left + width > window.innerWidth - 10) left = rect.right - width;
  let top = rect.bottom + 6;
  if (top + height > window.innerHeight - 10) top = Math.max(10, rect.top - height - 6);
  el.style.left = Math.max(10, left) + 'px';
  el.style.top = Math.max(10, top) + 'px';
  return el;
}

function popoverItem(action, label, iconName, attrs = '', extra = '') {
  return `<button class="popover-item ${extra}" data-action="${action}" ${attrs}>${icon(iconName)}<span>${escapeHtml(label)}</span></button>`;
}

function openSessionMenu(anchor, id) {
  const session = findSession(id);
  if (!session) return;
  const rect = anchor instanceof Element ? anchor.getBoundingClientRect() : anchor;
  openPopover(rect, `
    <div class="popover-label">${L('会话', 'Session')} · ${escapeHtml(session.title.slice(0, 16))}</div>
    ${popoverItem('rename-session', t('common.rename'), 'edit', `data-id="${id}"`)}
    ${popoverItem('archive-session', t('common.archive'), 'archive', `data-id="${id}"`)}
    ${id === 'S-0001' ? popoverItem('replay-demo', L('重播推演动效', 'Replay the demo'), 'refresh') : ''}
    <div class="popover-sep"></div>
    ${popoverItem('delete-session', t('common.delete'), 'trash', `data-id="${id}"`, 'is-danger')}
  `);
}

function openFolderMenu(anchor, id) {
  const folder = findFolder(id);
  if (!folder) return;
  const rect = anchor instanceof Element ? anchor.getBoundingClientRect() : anchor;
  openPopover(rect, `
    <div class="popover-label">${L('领域', 'Field')} · ${escapeHtml(folder.name.slice(0, 16))}</div>
    ${popoverItem('new-session-in-folder', L('在此新建会话', 'New chat here'), 'plus', `data-id="${id}"`)}
    <div class="popover-sep"></div>
    ${popoverItem('rename-folder', t('common.rename'), 'edit', `data-id="${id}"`)}
    ${popoverItem('archive-folder', t('common.archive'), 'archive', `data-id="${id}"`)}
    <div class="popover-sep"></div>
    ${popoverItem('delete-folder', t('common.delete'), 'trash', `data-id="${id}"`, 'is-danger')}
  `);
}

function openArchivedSessionMenu(anchor, id) {
  const item = state.archivedSessions.find((s) => s.id === id);
  if (!item) return;
  const rect = anchor instanceof Element ? anchor.getBoundingClientRect() : anchor;
  openPopover(rect, `
    <div class="popover-label">${L('已归档', 'Archived')} · ${escapeHtml(item.title.slice(0, 16))}</div>
    ${popoverItem('restore-session', t('common.restore'), 'restore', `data-id="${id}"`)}
    <div class="popover-sep"></div>
    ${popoverItem('delete-archived-session', t('common.delete'), 'trash', `data-id="${id}"`, 'is-danger')}
  `);
}

function openArchivedFolderMenu(anchor, id) {
  const item = state.archivedFolders.find((f) => f.id === id);
  if (!item) return;
  const rect = anchor instanceof Element ? anchor.getBoundingClientRect() : anchor;
  openPopover(rect, `
    <div class="popover-label">${L('已归档', 'Archived')} · ${escapeHtml(item.name)}</div>
    ${popoverItem('restore-folder', t('common.restore'), 'restore', `data-id="${id}"`)}
    <div class="popover-sep"></div>
    ${popoverItem('delete-archived-folder', t('common.delete'), 'trash', `data-id="${id}"`, 'is-danger')}
  `);
}

function openModelMenu(anchor) {
  const rect = anchor instanceof Element ? anchor.getBoundingClientRect() : anchor;
  const items = state.profiles.map((p) => `
    <button class="popover-item ${p.id === state.activeProfile ? 'is-selected' : ''}" data-action="activate-profile" data-id="${p.id}">
      <span class="profile-mark" style="width:17px;height:17px;font-size:7px">${escapeHtml(p.mark)}</span>
      <span>${escapeHtml(p.short)}</span>
      <span class="pop-meta">${p.id === state.activeProfile ? L('当前', 'current') : escapeHtml(p.provider)}</span>
    </button>`).join('');
  openPopover(rect, `
    <div class="popover-label">${L('切换模型', 'Switch model')}</div>
    ${items}
    <div class="popover-sep"></div>
    ${popoverItem('go-settings-models', L('管理模型档案', 'Manage profiles'), 'gear')}
  `);
}

function openRename(rect, title, value, onSave) {
  const pop = openPopover(rect, `
    <div class="popover-label">${escapeHtml(title)}</div>
    <form class="rename-form">
      <input class="rename-input" maxlength="80" value="${escapeHtml(value)}" autocomplete="off">
      <div class="inline-actions">
        <button type="submit" class="mini solid">${t('common.save')}</button>
        <button type="button" class="mini" data-action="close-popover">${t('common.cancel')}</button>
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
  const rect = anchor instanceof Element ? anchor.getBoundingClientRect() : anchor;
  openPopover(rect, `<h5>${L('推断依据', 'Basis')}</h5>
    <p>${escapeHtml(text)}</p>
    <em>${L('依据只引用你说过的原话与本轮工具轨迹；对不上时，它会如实说不知道。', 'Basis cites only your own words and this round\'s tool trace. When it does not match, it says so.')}</em>`, 'popover-basis');
}

/* ── session & folder actions ─────────────────────────────────────── */
function selectSession(id) {
  const found = findSession(id);
  if (!found) return;
  if (state.busy) cancelRun({ interrupted: false, silent: true });
  state.currentSession = id;
  state.currentFolder = found.folderId;
  found.folder.open = true;
  if (!state.details[id]) state.details[id] = makeDetail(found.detail || '');
  renderSession();
  if (window.innerWidth <= 980) closeRail();
  if (window.innerWidth <= 1120) setMarginOpen(false);
  requestAnimationFrame(() => scrollThread(true));
}

function ensureSelection() {
  if (currentSession()) {
    renderSession();
    return;
  }
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
}

function createSession(folderId) {
  if (state.busy) cancelRun({ interrupted: true, silent: true });
  let folder = findFolder(folderId) || findFolder(state.currentFolder) || state.folders[0];
  if (!folder) {
    folder = { id: 'F-99', name: L('未归类', 'Unsorted'), open: true, sessions: [] };
    state.folders.push(folder);
  }
  state.sessionSeq += 1;
  const id = 'S-' + String(state.sessionSeq).padStart(4, '0');
  folder.sessions.unshift({
    id,
    title: L('新会话', 'New session') + ' · ' + clock(),
    state: 'draft',
    time: clock(),
  });
  folder.open = true;
  state.details[id] = makeDetail('');
  state.currentSession = id;
  state.currentFolder = folder.id;
  closeInlineForm();
  renderSession();
  toast(L('会话已创建 · 落笔即开始', 'Session created · start writing'));
  setTimeout(() => composerInput.focus(), 80);
}

function archiveSession(id) {
  const found = findSession(id);
  if (!found) return;
  if (state.busy && state.currentSession === id) cancelRun({ interrupted: true, silent: true });
  found.folder.sessions = found.folder.sessions.filter((s) => s.id !== id);
  state.archivedSessions.push({
    id, title: found.title, folderId: found.folderId, folderName: found.folder.name,
    state: found.state, time: found.time,
  });
  state.archiveOpen = true;
  if (state.currentSession === id) state.currentSession = found.folder.sessions[0]?.id || null;
  closePopover();
  ensureSelection();
  renderRail();
  toast(L('已归档 · 可在「已归档」中找回', 'Archived · find it under Archived'));
}

function deleteSession(id) {
  const found = findSession(id);
  if (!found) return;
  askConfirm({
    title: L('删除这个会话？', 'Delete this session?'),
    body: L(
      `「${found.title}」的对话、计划版本与动机修订都会被删除。归档不会删除内容，删除会。`,
      `“${found.title}” and all of its turns, plan versions and motivation edits will be removed. Archiving keeps them; deleting does not.`,
    ),
    ok: L('删除会话', 'Delete session'),
    mark: '删',
    onOk: () => {
      if (state.busy && state.currentSession === id) cancelRun({ interrupted: true, silent: true });
      found.folder.sessions = found.folder.sessions.filter((s) => s.id !== id);
      delete state.details[id];
      if (state.currentSession === id) state.currentSession = found.folder.sessions[0]?.id || null;
      closePopover();
      ensureSelection();
      renderRail();
      toast(L('会话已删除', 'Session deleted'), 'danger');
    },
  });
}

function archiveFolder(id) {
  const idx = state.folders.findIndex((f) => f.id === id);
  if (idx === -1) return;
  const folder = state.folders[idx];
  if (state.busy && folder.sessions.some((s) => s.id === state.currentSession)) {
    cancelRun({ interrupted: true, silent: true });
  }
  const [removed] = state.folders.splice(idx, 1);
  state.archivedFolders.push({
    id: removed.id, name: removed.name, sessionCount: removed.sessions.length, sessions: removed.sessions,
  });
  state.archiveOpen = true;
  if (!state.folders.length || findSession(state.currentSession) === null) {
    state.currentSession = null;
    ensureSelection();
  }
  closePopover();
  renderRail();
  toast(L('领域已归档 · 会话一并收起', 'Field archived · its sessions are folded in'));
}

function deleteFolder(id) {
  const folder = findFolder(id);
  if (!folder) return;
  askConfirm({
    title: L('删除这个领域？', 'Delete this field?'),
    body: L(
      `「${folder.name}」以及里面 ${folder.sessions.length} 个会话会被一并删除。这个动作无法撤销，也不会进入修订史。`,
      `“${folder.name}” and its ${folder.sessions.length} sessions will be deleted. This cannot be undone and does not enter the revision history.`,
    ),
    ok: L('删除领域', 'Delete field'),
    mark: '删',
    onOk: () => {
      if (state.busy && folder.sessions.some((s) => s.id === state.currentSession)) {
        cancelRun({ interrupted: true, silent: true });
      }
      for (const s of folder.sessions) delete state.details[s.id];
      state.folders = state.folders.filter((f) => f.id !== id);
      if (folder.sessions.some((s) => s.id === state.currentSession)) state.currentSession = null;
      closePopover();
      ensureSelection();
      renderRail();
      toast(L('领域已删除', 'Field deleted'), 'danger');
    },
  });
}

function restoreSession(id) {
  const idx = state.archivedSessions.findIndex((s) => s.id === id);
  if (idx === -1) return;
  const [item] = state.archivedSessions.splice(idx, 1);
  let folder = findFolder(item.folderId);
  if (!folder) {
    folder = { id: item.folderId || 'F-99', name: item.folderName || L('未归类', 'Unsorted'), open: true, sessions: [] };
    state.folders.push(folder);
  }
  folder.sessions.push({ id: item.id, title: item.title, state: item.state, time: item.time });
  folder.open = true;
  if (!state.details[item.id]) state.details[item.id] = makeDetail('');
  closePopover();
  renderRail();
  toast(L('已恢复到原领域', 'Restored to its field'));
}

function restoreFolder(id) {
  const idx = state.archivedFolders.findIndex((f) => f.id === id);
  if (idx === -1) return;
  const [item] = state.archivedFolders.splice(idx, 1);
  state.folders.push({ id: item.id, name: item.name, open: true, sessions: item.sessions || [] });
  closePopover();
  renderRail();
  toast(L('领域已恢复', 'Field restored'));
}

/* ── plan / proposal ──────────────────────────────────────────────── */
function toggleStep(stepId) {
  const detail = activeDetail();
  if (!detail || !detail.plan) return;
  const step = detail.plan.steps.find((s) => s.id === stepId);
  if (!step) return;
  step.status = step.status === 'done' ? 'pending' : 'done';
  renderMargin();
}

function resolveProposal(id, resolution) {
  const detail = activeDetail();
  const message = detail?.messages.find((m) => m.proposal && m.proposal.id === id);
  if (!detail || !message) return null;
  const prop = message.proposal;
  prop.state = resolution;

  const node = thread.querySelector(`[data-proposal-id="${id}"]`);
  if (node) {
    node.classList.add('is-resolved');
    node.querySelector('.proposal-actions')?.remove();
    if (!node.querySelector('.proposal-resolution')) {
      const div = document.createElement('div');
      div.className = 'proposal-resolution';
      div.textContent = resolution === 'approved'
        ? L(`已写入 · 计划第 ${prop.version} 版`, `Written · plan v${prop.version}`)
        : L('未写入 · 保留原计划', 'Declined · plan unchanged');
      node.appendChild(div);
    }
    const label = node.querySelector('.proposal-ver');
    if (label) label.textContent = L(`第 ${prop.version} 版 · 已处理`, `v${prop.version} · resolved`);
  }

  if (resolution === 'approved') {
    if (prop.nextPlan) detail.plan = structuredClone(prop.nextPlan);
    const section = detail.context?.sections?.find((s) => s.meta === 'plan');
    if (section && detail.plan) {
      section.value = `第 ${detail.plan.version} 版 · ${detail.plan.steps.length} 步`;
    }
    state.marginTab = 'plan';
    renderMargin();
  }
  return prop;
}

/* ── settings ─────────────────────────────────────────────────────── */
function renderSettings() {
  const prof = activeProfile();
  const card = $('#active-model-card');
  if (card) {
    card.innerHTML = `
      <div class="am-kicker"><span class="live-dot"></span>${L('正在使用', 'IN USE')} · ${escapeHtml(prof.provider)}</div>
      <div class="am-title"><h3>${escapeHtml(prof.label)}</h3><span class="am-model">${escapeHtml(prof.model)}</span></div>
      <div class="am-meta">
        <span>${L('端点', 'Endpoint')}<b>${escapeHtml(prof.base)}</b></span>
        <span>${L('上下文', 'Context')}<b>${escapeHtml(prof.ctx)}</b></span>
        <span>${L('密钥', 'Key')}<b>${escapeHtml(prof.masked)}</b></span>
      </div>
      <div class="am-think">
        <span>${L('思考', 'Thinking')}</span>
        <div class="segmented">
          ${['auto', 'false', 'true'].map((v) => `<button data-action="set-think" data-id="${prof.id}" data-value="${v}" class="${String(prof.think) === v ? 'is-on' : ''}">${thinkLabel(v)}</button>`).join('')}
        </div>
        <em>${L('自动 = 不发这个字段，对不认识的模型更安全', 'Auto = omit the field; safer for unknown models')}</em>
      </div>
      <div class="am-bottom">
        <button class="mini" data-action="probe" data-id="${prof.id}">${L('探活', 'Probe')}</button>
        <span class="probe-result" id="probe-result">${escapeHtml(prof.probe || L('尚未探活', 'Not probed yet'))}</span>
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
          <span class="tag">${escapeHtml(p.ctx)}</span>
          ${p.id === state.activeProfile ? `<span class="tag accent">${L('当前', 'current')}</span>` : ''}
        </div>
        <div class="profile-actions">
          ${p.id === state.activeProfile
            ? `<span class="tag accent">●</span>`
            : `<button class="mini" data-action="activate-profile" data-id="${p.id}">${L('使用', 'Use')}</button>`}
        </div>
      </div>`).join('');
  }
  syncSettingControls();
}

function syncSettingControls() {
  const setOn = (sel, match) => {
    $$(sel).forEach((btn) => {
      const on = match(btn);
      btn.classList.toggle('is-on', on);
      if (btn.hasAttribute('role')) btn.setAttribute('aria-checked', String(on));
    });
  };
  setOn('#lang-seg button', (b) => b.dataset.lang === state.lang);
  setOn('#think-seg button', (b) => b.dataset.thinkview === state.thinkView);
  setOn('#scale-seg button', (b) => b.dataset.scale === state.size);
  setOn('#motion-seg button', (b) => b.dataset.motion === state.motion);
  setOn('#busy-options .busy-card', (b) => b.dataset.busy === state.busyBehavior);
  setOn('.theme-swatch', (b) => b.dataset.themeChoice === state.theme);
  const cite = $('#cite-switch');
  if (cite) {
    cite.classList.toggle('is-on', state.cite);
    cite.setAttribute('aria-checked', String(state.cite));
  }
}

/* ── benchmark ────────────────────────────────────────────────────── */
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
    const formLabel = state.bench.form === 'quick' ? L('简短版', 'Short form') : L('完整版', 'Full form');
    const answered = state.bench.answered;
    card.innerHTML = `
      <div class="run-head"><span class="kicker">${L('本次', 'Current')}</span><span class="run-status is-running">${L('进行中', 'Running')}</span></div>
      <h3>${formLabel} · ${L('自述轮', 'Self-report')} ${answered} / 20</h3>
      <p class="run-sub">${L('开始于今天 · 每题会记录犹豫、切换与用时，但只记录、不计分。', 'Started today · hesitations, switches and timing are recorded, never scored.')}</p>
      <div class="run-progress"><i style="--p:${Math.max(4, Math.round((answered / 20) * 100))}%"></i></div>
      <div class="run-actions">
        <button class="mini solid" data-action="pause-run">${L('保存并退出', 'Save & exit')}</button>
        <button class="mini" data-action="restart-run">${L('重新开始', 'Restart')}</button>
      </div>
      <p class="run-note">${L('保存后这次运行停在', 'After saving it stays')} <em>interrupted</em>${L('，下次进入可以继续。', ' and can be resumed next time.')}</p>`;
  } else if (state.bench.status === 'interrupted') {
    card.innerHTML = `
      <div class="run-head"><span class="kicker">${L('上一次', 'Last run')}</span><span class="run-status">${L('已中断', 'Interrupted')}</span></div>
      <h3>${L('自述轮 · 7 / 20', 'Self-report · 7 / 20')}</h3>
      <p class="run-sub">${L('2026-10-18 21:40 · 停在「阅读 vs 听力」这一题之前', '2026-10-18 21:40 · paused before “reading vs listening”')}</p>
      <div class="run-progress"><i style="--p:35%"></i></div>
      <div class="run-actions">
        <button class="mini solid" data-action="resume-run">${L('继续上次', 'Resume')}</button>
        <button class="mini" data-action="restart-run">${L('重新开始', 'Restart')}</button>
      </div>
      <p class="run-note">${L('重新开始不会抹掉旧运行；它会被标成', 'Restarting does not erase the old run; it is marked')} <em>abandoned</em>${L('，仍然可查。', ' and stays readable.')}</p>`;
  } else {
    card.innerHTML = `
      <div class="run-head"><span class="kicker">${L('状态', 'Status')}</span><span class="run-status">${L('未开始', 'Not started')}</span></div>
      <h3>${L('还没有运行', 'No run yet')}</h3>
      <p class="run-sub">${L('选一种卷别开始；两种都不拦你进入对话。', 'Pick a form; neither blocks you from the conversation.')}</p>
      <div class="run-actions">
        <button class="mini solid" data-action="start-run" data-form="quick">${L('简短版', 'Short')}</button>
        <button class="mini" data-action="start-run" data-form="full">${L('完整版', 'Full')}</button>
      </div>`;
  }
}

function startBench(form) {
  state.bench = { form, status: 'running', answered: 0 };
  renderRunCard();
  renderRail();
  toast(form === 'quick' ? L('简短版已开始 · 自述轮', 'Short form started · self-report') : L('完整版已开始 · 自述轮', 'Full form started · self-report'));
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
        toast(L(`「${domain}」已从兴趣区移除 · 兴趣不能落在熟悉集合里`, `“${domain}” removed from interests · interest cannot sit in the familiar set`));
      }
    }
  } else {
    if (state.interested.has(domain)) state.interested.delete(domain);
    else {
      if (state.familiar.has(domain)) {
        chip.classList.add('is-conflict');
        setTimeout(() => chip.classList.remove('is-conflict'), 460);
        toast(L(`「${domain}」已在熟悉集合里 · 先移出熟悉再选`, `“${domain}” is in the familiar set · remove it there first`), 'danger');
        return;
      }
      state.interested.add(domain);
    }
  }
  renderDomains();
  toast(L('领域清单已产生新版本 · 旧版本仍可查', 'A new field version exists · older versions stay readable'));
}

/* ── confirm dialog ───────────────────────────────────────────────── */
function askConfirm({ title, body, ok = L('删除', 'Delete'), mark = '删', onOk }) {
  $('#confirm-title').textContent = title;
  $('#confirm-body').textContent = body;
  $('#confirm-ok').textContent = ok;
  $('#confirm-mark').textContent = mark;
  state.confirmAction = onOk;
  confirmDialog.showModal();
}

/* ── language / theme / appearance ────────────────────────────────── */
function applyLanguage() {
  document.documentElement.lang = state.lang === 'zh' ? 'zh-CN' : 'en';
  $$('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  $$('[data-i18n-html]').forEach((el) => { el.innerHTML = t(el.dataset.i18nHtml); });
  $$('[data-i18n-placeholder]').forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  composerInput.placeholder = t('composer.placeholder');
  $('#compose-hint').textContent = t('composer.hint');
  renderRail();
  renderTopbar();
  if (!state.busy) renderThread();
  renderMargin();
  if (state.view === 'settings') renderSettings();
  if (state.view === 'benchmark') renderBench();
  syncSettingControls();
  savePrefs();
}

function setTheme(theme, { silent = false } = {}) {
  state.theme = theme;
  document.documentElement.classList.add('theme-swapping');
  document.documentElement.dataset.theme = theme;
  const meta = $('#theme-color');
  if (meta) meta.setAttribute('content', theme === 'night' ? '#121513' : '#f7f6f2');
  setTimeout(() => document.documentElement.classList.remove('theme-swapping'), 320);
  $$('.theme-swatch').forEach((el) => el.classList.toggle('is-on', el.dataset.themeChoice === theme));
  savePrefs();
  if (!silent) toast(theme === 'day' ? L('纸色 · 日光', 'Paper · Day') : L('纸色 · 夜读', 'Paper · Night'));
}

/* ── side note ────────────────────────────────────────────────────── */
function openSideNote() {
  sideNote.classList.add('is-open');
  snScrim.hidden = false;
  setTimeout(() => $('#sn-input').focus(), 220);
}

function closeSideNote() {
  sideNote.classList.remove('is-open');
  snScrim.hidden = true;
}

function sendSideNote() {
  const input = $('#sn-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  const threadEl = $('#sn-thread');
  const mine = document.createElement('div');
  mine.className = 'sn-msg';
  mine.innerHTML = `<span class="sn-role">${t('sn.you')}</span><p>${escapeHtml(text)}</p>`;
  threadEl.appendChild(mine);
  threadEl.scrollTop = threadEl.scrollHeight;
  setTimeout(() => {
    const reply = document.createElement('div');
    reply.className = 'sn-msg from-bot';
    reply.innerHTML = `<span class="sn-role">${t('sn.note')}</span><p>${L('记下了。我只把它放进侧记的记忆里，不改动计划。要落进会话，我会先给你一张提案。', 'Noted. It goes into the margin note memory only; the plan stays untouched. If it should enter the session, I will draft a proposal first.')}</p>`;
    threadEl.appendChild(reply);
    threadEl.scrollTop = threadEl.scrollHeight;
    $('#sn-tokens').textContent = '6.5k / 16k';
  }, 850);
}

/* ── inline create / forms ────────────────────────────────────────── */
function openInlineForm(kind) {
  closePopover();
  state.inlineKind = kind;
  const form = $('#sb-inline');
  const label = $('#sb-inline-label');
  const input = $('#sb-inline-input');
  const isFolder = kind === 'folder';
  label.textContent = isFolder ? L('新领域 · 名称', 'New field · name') : L('新会话 · 标题', 'New session · title');
  input.placeholder = isFolder ? L('例如：物理学 · 概念梳理', 'e.g. Physics · concept map') : L('例如：第 2 章的概念地图', 'e.g. Chapter 2 concept map');
  form.hidden = false;
  input.value = '';
  input.focus();
}

function closeInlineForm() {
  state.inlineKind = null;
  $('#sb-inline').hidden = true;
  $('#sb-inline-input').value = '';
}

function commitInlineForm() {
  const value = $('#sb-inline-input').value.trim();
  if (!value) { $('#sb-inline-input').focus(); return; }
  if (state.inlineKind === 'folder') {
    state.folderSeq += 1;
    const id = `F-${pad(state.folderSeq)}`;
    state.folders.push({ id, name: value, open: true, sessions: [] });
    closeInlineForm();
    renderRail();
    toast(L('领域已创建', 'Field created'));
  } else {
    createSession(state.currentFolder);
  }
}

function openSearch() {
  if (state.view !== 'chat') setView('chat');
  if (window.innerWidth <= 980) openRail();
  const slot = $('#sb-search');
  slot.hidden = false;
  $('#sb-search-input').focus();
  state.search = '';
}

function closeSearch() {
  const slot = $('#sb-search');
  if (slot.hidden) return false;
  slot.hidden = true;
  $('#sb-search-input').value = '';
  state.search = '';
  renderRail();
  return true;
}

function openRail() {
  document.body.classList.add('rail-open');
  $('#scrim').hidden = false;
}

function closeRail() {
  document.body.classList.remove('rail-open');
  $('#scrim').hidden = true;
}

function setMarginOpen(open) {
  workspace.classList.toggle('is-margin-closed', !open);
  const btn = $('#margin-toggle');
  if (btn) {
    btn.classList.toggle('is-on', open);
    btn.setAttribute('aria-pressed', String(open));
  }
}

function toggleRail() {
  if (window.innerWidth <= 980) {
    if (document.body.classList.contains('rail-open')) closeRail();
    else openRail();
  } else {
    document.body.classList.toggle('rail-collapsed');
  }
}

function switchSettingsTab(tab) {
  $$('.settings-nav-item').forEach((el) => el.classList.toggle('is-active', el.dataset.settingsTab === tab));
  $$('.settings-panel').forEach((el) => el.classList.toggle('is-active', el.dataset.settingsPanel === tab));
  renderSettings();
}

function activateProfile(id) {
  if (state.busy) {
    toast(L('正在生成 · 先停止这一轮再切换模型', 'Generating · stop this round before switching models'), 'danger');
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
  toast(L(`已切换到 ${activeProfile().short}`, `Switched to ${activeProfile().short}`));
}

function probeProfile(id, button) {
  const profile = state.profiles.find((p) => p.id === id);
  if (!profile || !button) return;
  const result = $('#probe-result');
  button.disabled = true;
  if (result) {
    result.textContent = L('探活中 · 正在请求端点…', 'Probing · contacting the endpoint…');
    result.classList.add('is-pending');
    result.classList.remove('is-ok');
  }
  setTimeout(() => {
    const latency = 80 + Math.floor(Math.random() * 180);
    const models = profile.kind === 'local' ? 12 : 3;
    profile.probe = L(`刚刚 · ${latency} ms · ${models} 个模型`, `just now · ${latency} ms · ${models} models`);
    button.disabled = false;
    if (result) {
      result.textContent = profile.probe;
      result.classList.remove('is-pending');
      result.classList.add('is-ok');
    }
    toast(L(`探活成功 · ${latency} ms`, `Probe succeeded · ${latency} ms`));
  }, 1150);
}

function replayDemo() {
  if (state.currentSession !== 'S-0001') {
    toast(L('重播只对示例会话「词根网络 · unit 1」可用', 'Replay is only available in the demo session'));
    return;
  }
  if (state.busy) cancelRun({ silent: true });
  const detail = activeDetail();
  detail.messages = structuredClone(BASE_MESSAGES_S1);
  detail.plan = structuredClone(PLAN_V3);
  detail.context = structuredClone(CONTEXT_S1);
  detail.replyCount = 0;
  detail.lastVariant = '';
  state.marginTab = 'plan';
  closePopover();
  renderSession();
  toast(L('推演将重播 · 计划回到第 3 版', 'Replaying · the plan returns to v3'));
  const lastUser = [...detail.messages].reverse().find((m) => m.role === 'user');
  setTimeout(() => startAssistantRun({ userText: lastUser?.text || '' }), 650);
}

/* ── renames ──────────────────────────────────────────────────────── */
function renameSession(anchor, id) {
  const session = findSession(id);
  if (!session) return;
  const rect = anchor.getBoundingClientRect();
  openRename(rect, L('重命名会话', 'Rename session'), session.title, (name) => {
    session.title = name;
    renderRail();
    renderTopbar();
    toast(L('会话已重命名', 'Session renamed'));
  });
}

function renameFolder(anchor, id) {
  const folder = findFolder(id);
  if (!folder) return;
  const rect = anchor.getBoundingClientRect();
  openRename(rect, L('重命名领域', 'Rename field'), folder.name, (name) => {
    folder.name = name;
    renderRail();
    renderTopbar();
    toast(L('领域已重命名', 'Field renamed'));
  });
}

/* ── action dispatch ──────────────────────────────────────────────── */
function handleAction(el, event) {
  const action = el.dataset.action;
  const id = el.dataset.id;
  switch (action) {
    case 'go-chat':
      setView('chat');
      break;
    case 'toggle-sidebar':
      toggleRail();
      break;
    case 'close-sidebar':
      closeRail();
      break;
    case 'toggle-margin': {
      const open = workspace.classList.contains('is-margin-closed');
      setMarginOpen(open);
      break;
    }
    case 'new-session':
      createSession(state.currentFolder);
      break;
    case 'new-folder':
      openInlineForm('folder');
      break;
    case 'new-session-in-folder':
      closePopover();
      createSession(id);
      break;
    case 'cancel-inline':
      closeInlineForm();
      break;
    case 'toggle-search': {
      const slot = $('#sb-search');
      if (slot.hidden) openSearch();
      else closeSearch();
      break;
    }
    case 'toggle-archive':
      state.archiveOpen = !state.archiveOpen;
      renderRail();
      break;
    case 'toggle-folder': {
      if (event.target.closest('.row-menu')) return;
      const folder = findFolder(el.dataset.id);
      if (folder) {
        folder.open = !folder.open;
        renderRail();
      }
      break;
    }
    case 'toggle-step':
      toggleStep(el.dataset.stepId);
      break;
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
    case 'rename-session':
      renameSession(el, id);
      break;
    case 'rename-folder':
      renameFolder(el, id);
      break;
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
    case 'delete-archived-session': {
      const item = state.archivedSessions.find((s) => s.id === id);
      if (!item) break;
      askConfirm({
        title: L('从归档中删除这个会话？', 'Delete this session from the archive?'),
        body: L(`「${item.title}」的对话与版本记录会被永久删除。此动作无法撤销。`, `“${item.title}” and its version history will be permanently removed. This cannot be undone.`),
        ok: L('永久删除', 'Delete permanently'),
        mark: '删',
        onOk: () => {
          state.archivedSessions = state.archivedSessions.filter((s) => s.id !== id);
          delete state.details[id];
          closePopover();
          renderRail();
          toast(L('已从归档中删除', 'Removed from archive'), 'danger');
        },
      });
      break;
    }
    case 'restore-folder':
      restoreFolder(id);
      break;
    case 'delete-archived-folder': {
      const item = state.archivedFolders.find((f) => f.id === id);
      if (!item) break;
      askConfirm({
        title: L('从归档中删除这个领域？', 'Delete this field from the archive?'),
        body: L(`「${item.name}」与其中 ${item.sessionCount} 个会话会被永久删除。此动作无法撤销。`, `“${item.name}” and its ${item.sessionCount} sessions will be permanently removed. This cannot be undone.`),
        ok: L('永久删除', 'Delete permanently'),
        mark: '删',
        onOk: () => {
          (item.sessions || []).forEach((s) => delete state.details[s.id]);
          state.archivedFolders = state.archivedFolders.filter((f) => f.id !== id);
          closePopover();
          renderRail();
          toast(L('已从归档中删除', 'Removed from archive'), 'danger');
        },
      });
      break;
    }
    case 'replay-demo':
      replayDemo();
      break;
    case 'send':
      sendFromComposer();
      break;
    case 'stop':
      cancelRun({ interrupted: true });
      break;
    case 'attach':
      toast(t('composer.attachNote'));
      break;
    case 'approve-proposal': {
      const prop = resolveProposal(el.dataset.id, 'approved');
      toast(prop
        ? L(`已写入计划 · 第 ${prop.version} 版`, `Written to plan · v${prop.version}`)
        : L('已写入计划', 'Written to plan'));
      break;
    }
    case 'reject-proposal':
      resolveProposal(el.dataset.id, 'rejected');
      toast(L('草案未写入 · 原计划保留', 'Draft declined · plan unchanged'));
      break;
    case 'proposal-basis': {
      const basis = el.closest('.proposal')?.querySelector('.proposal-basis');
      openBasis(el, basis ? basis.textContent.replace(/^依据\s*·\s*/, '').replace(/^Basis\s*·\s*/, '') : '');
      break;
    }
    case 'show-basis':
      openBasis(el, el.dataset.basis || '');
      break;
    case 'annotate':
      toast(L('选中一句话即可标注；依据对不上时，系统会拒绝写入', 'Select a sentence to annotate; mismatched basis is refused'));
      break;
    case 'edit-motivation':
      toast(L('动机编辑面板在完整界面中展开 · 每次改动留下一个版本', 'The motivation editor opens in the full interface · each edit leaves a version'));
      break;
    case 'motivation-history':
    case 'plan-history':
      toast(L('版本历史为只读 · 旧版本不会随新版本改变', 'History is read-only · old versions never change'));
      break;
    case 'ask-plan':
    case 'ask-change':
      composerInput.value = L('这一步我不太明白，想换一种做法。', 'I do not quite follow this step; I would like another approach.');
      composerInput.focus();
      autoGrow();
      break;
    case 'inspect-context':
      toast(L('逐条来源：偏好 4 · 方法卡 3 · 基线 6 · 策略库 2', 'Sources: 4 preferences · 3 methods · 6 baseline · 2 strategies'));
      break;
    case 'open-sidenote':
      openSideNote();
      break;
    case 'close-sidenote':
      closeSideNote();
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
      toast(L('已继续上次 · 从第 7 题开始', 'Resumed · starting at item 7'));
      break;
    case 'restart-run':
      askConfirm({
        title: L('重新开始这次基准？', 'Restart this benchmark?'),
        body: L('旧的运行会被标成「已放弃」，记录仍然可查。新的运行从自述轮第 1 题开始。', 'The old run is marked abandoned and stays readable. The new run starts at item 1 of the self-report.'),
        ok: L('重新开始', 'Restart'),
        mark: '重',
        onOk: () => {
          state.bench = { form: el.dataset.form || state.bench.form || 'full', status: 'running', answered: 0 };
          renderRunCard();
          renderRail();
          toast(L('新的运行已开始', 'New run started'));
        },
      });
      break;
    case 'pause-run':
      state.bench.status = 'interrupted';
      renderRunCard();
      renderRail();
      toast(L('已保存退出 · 下次可以继续', 'Saved and exited · resume anytime'));
      break;
    case 'skip-domains':
      toast(L('已跳过第二段 · 随时可以回来补', 'Second set skipped · come back anytime'));
      break;
    case 'probe':
      probeProfile(id, el);
      break;
    case 'set-think': {
      const profile = state.profiles.find((p) => p.id === id);
      if (profile) {
        profile.think = el.dataset.value;
        renderSettings();
        toast(`${L('思考', 'Thinking')} · ${thinkLabel(el.dataset.value)}`);
      }
      break;
    }
    case 'add-profile':
      toast(L('新增档案：填 base URL 与密钥即可 · 演示版暂不写入', 'New profile: base URL and key are enough · not persisted in this prototype'));
      break;
    case 'quick-theme':
      setTheme(state.theme === 'day' ? 'night' : 'day');
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
    openBasis(cite.getBoundingClientRect(), cite.dataset.basis || '');
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

  const marginTab = event.target.closest('.margin-tab');
  if (marginTab) {
    state.marginTab = marginTab.dataset.tab;
    renderMargin();
    return;
  }

  const step = event.target.closest('.step[data-step-id]');
  if (step) {
    toggleStep(step.dataset.stepId);
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
    syncSettingControls();
    updateBusyUI();
    savePrefs();
    toast(`${t('composer.busyPrefix')}${t('busy.' + state.busyBehavior)}`);
    return;
  }

  const thinkBtn = event.target.closest('#think-seg button');
  if (thinkBtn) {
    state.thinkView = thinkBtn.dataset.thinkview;
    syncSettingControls();
    savePrefs();
    if (!state.busy) renderThread();
    return;
  }

  const themeBtn = event.target.closest('[data-theme-choice]');
  if (themeBtn) {
    setTheme(themeBtn.dataset.themeChoice);
    return;
  }

  const scaleBtn = event.target.closest('#scale-seg button');
  if (scaleBtn) {
    state.size = scaleBtn.dataset.scale;
    document.documentElement.dataset.size = state.size;
    syncSettingControls();
    savePrefs();
    return;
  }

  const motionBtn = event.target.closest('#motion-seg button');
  if (motionBtn) {
    state.motion = motionBtn.dataset.motion;
    document.documentElement.dataset.motion = state.motion;
    syncSettingControls();
    savePrefs();
    return;
  }

  const domainChip = event.target.closest('.chip[data-domain]');
  if (domainChip) {
    toggleDomain(domainChip);
  }
});

document.addEventListener('pointerdown', (event) => {
  if (!popLayer.contains(event.target)) closePopover();
}, true);

document.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    openSearch();
    return;
  }
  if (event.key === 'Escape') {
    if (popLayer.innerHTML) { closePopover(); return; }
    if (sideNote.classList.contains('is-open')) { closeSideNote(); return; }
    if (closeSearch()) return;
    if (document.body.classList.contains('rail-open')) { closeRail(); return; }
  }
  const tag = document.activeElement?.tagName;
  if ((event.key === 'Enter' || event.key === ' ') && !['INPUT', 'TEXTAREA', 'BUTTON', 'SELECT'].includes(tag)) {
    const target = document.activeElement;
    if (target?.matches('.folder-head[role="button"], .session[role="button"], .step[role="button"]')) {
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

let lastViewportWidth = window.innerWidth;
window.addEventListener('resize', () => {
  closePopover();
  if (window.innerWidth > 980 && document.body.classList.contains('rail-open')) closeRail();
  if (window.innerWidth <= 1120 && lastViewportWidth > 1120) setMarginOpen(false);
  lastViewportWidth = window.innerWidth;
});

window.addEventListener('scroll', () => closePopover(), true);

workspace.addEventListener('click', (event) => {
  if (window.innerWidth > 980) return;
  if (workspace.classList.contains('is-margin-closed')) return;
  if (event.target.closest('.margin')) return;
  setMarginOpen(false);
});

$('#sb-inline').addEventListener('submit', (event) => {
  event.preventDefault();
  commitInlineForm();
});

$('#sb-search-input').addEventListener('input', (event) => {
  state.search = event.target.value.trim();
  renderRail();
});

$('#cite-switch').addEventListener('click', (event) => {
  state.cite = !state.cite;
  document.documentElement.dataset.cite = state.cite ? 'on' : 'off';
  event.currentTarget.classList.toggle('is-on', state.cite);
  event.currentTarget.setAttribute('aria-checked', String(state.cite));
  savePrefs();
});

confirmDialog.addEventListener('close', () => { state.confirmAction = null; });

composerInput.addEventListener('input', autoGrow);
composerInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    sendFromComposer();
  }
});

snScrim.addEventListener('click', closeSideNote);
$('#scrim').addEventListener('click', closeRail);

function autoGrow() {
  composerInput.style.height = 'auto';
  composerInput.style.height = Math.min(180, composerInput.scrollHeight) + 'px';
}

/* ── init ─────────────────────────────────────────────────────────── */
function init() {
  document.documentElement.dataset.theme = state.theme;
  document.documentElement.dataset.size = state.size;
  document.documentElement.dataset.motion = state.motion;
  document.documentElement.dataset.cite = state.cite ? 'on' : 'off';
  const themeMeta = $('#theme-color');
  if (themeMeta) themeMeta.setAttribute('content', state.theme === 'night' ? '#121513' : '#f7f6f2');

  if (window.innerWidth <= 1120) {
    setMarginOpen(false);
  } else {
    setMarginOpen(true);
  }

  const btn = $('#theme-quick');
  if (btn) btn.setAttribute('aria-label', L('切换日夜', 'Toggle day and night'));

  applyLanguage();
  renderRail();
  renderTopbar();
  renderThread();
  renderMargin();
  renderSettings();
  renderBench();
  syncSettingControls();
  updateBusyUI();
  autoGrow();

  // show the non-bubble reasoning motion once on first load
  setTimeout(() => {
    const detail = activeDetail();
    const lastUser = detail
      ? [...detail.messages].reverse().find((m) => m.role === 'user')
      : null;
    if (!state.autoPlayed && lastUser && state.currentSession === 'S-0001' && !state.busy) {
      state.autoPlayed = true;
      startAssistantRun({ userText: lastUser.text });
    }
  }, 1000);
}

init();


