/**
 * 后端的形状 → 界面认的形状。
 *
 * 为什么要有这一层：界面认的是"渲染需要什么"，后端给的是"事实长什么样"。
 * 两者不会自动对齐 —— 后端说 `PlanStepStatus.pending`，界面要的是 `is-pending`
 * 这个类名；后端说 `strength: "medium"`，界面要的是点亮的第 2 格。
 * 把这个换算**集中在一处**，界面上就不会散落着 `if (x === 'medium')`。
 *
 * 一条纪律：**这里只做换算，不编造**。后端没给的（`limit: 0`、`drivers: null`）
 * 就如实留空，由渲染层显示「没测到」或「还没有」。补一个看起来合理的默认值，
 * 等于把"没测到"伪装成"测到了"。
 */

const pad = (n) => String(n).padStart(2, '0');

/** 时间怎么显示：今天给时刻，昨天给「昨天」，更早给月-日。 */
export function shortTime(iso) {
  if (!iso) return '';
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return '';
  const now = new Date();
  const sameDay = then.toDateString() === now.toDateString();
  if (sameDay) return `${pad(then.getHours())}:${pad(then.getMinutes())}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (then.toDateString() === yesterday.toDateString()) return '昨天';
  return `${pad(then.getMonth() + 1)}-${pad(then.getDate())}`;
}

/** 纯文本 → 段落 HTML。**先转义再拼标签**，顺序反了就是注入。 */
export function paragraphs(text) {
  const body = String(text ?? '').trim();
  if (!body) return '';
  return body
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean)
    .map((block) => {
      const lines = block.split('\n').map((line) => escape(line)).join('<br>');
      return `<p>${lines}</p>`;
    })
    .join('');
}

export function escape(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** 后端状态 → 界面用的短类名（CSS 里只有 review / done / draft 三种点）。 */
const DOT_CLASS = { draft: 'draft', active: '', review: 'review', completed: 'done', abandoned: '' };

const STATE_TEXT = {
  draft: '未开始',
  active: '执行中',
  review: '复盘中',
  completed: '已完成',
  abandoned: '已放弃',
};

export const stateText = (value) => STATE_TEXT[value] || value;
export const dotClass = (value) => DOT_CLASS[value] ?? '';

/* ── 侧栏 ──────────────────────────────────────────────────────────── */

/**
 * 两个列表 → 侧栏要的三样东西。
 *
 * 归档的会话**仍然是会话**（有自己的状态），所以它们从 `sessions` 里按
 * `archived_at` 分流出来，而不是另做一种数据。归档的领域同理。
 */
export function adaptWorkspace(folderPayload, sessionPayload) {
  const folderItems = folderPayload?.items || [];
  const sessionItems = sessionPayload?.items || [];

  const folders = folderItems
    .filter((f) => !f.archived_at)
    .map((f) => ({
      id: f.folder_id,
      name: f.name,
      goal: f.goal || '',
      open: true,
      sessions: [],
    }));
  const byId = new Map(folders.map((f) => [f.id, f]));
  const archivedFolderItems = folderItems.filter((f) => f.archived_at);

  const archivedSessions = [];
  for (const s of sessionItems) {
    const folder = byId.get(s.folder_id);
    const row = {
      id: s.session_id,
      title: s.title,
      //: 后端原值，用来查文案（`stateLabel`）。
      state: s.state,
      //: CSS 认的短类名（`.state-dot.review / .done / .draft`）。
      dot: dotClass(s.state),
      time: shortTime(s.updated_at),
      detail: null,
    };
    if (s.archived_at) {
      archivedSessions.push({
        id: s.session_id,
        title: s.title,
        folderId: s.folder_id,
        folderName: folder ? folder.name : '（已归档的领域）',
        state: s.state,
        time: shortTime(s.updated_at),
      });
      continue;
    }
    if (folder) folder.sessions.push(row);
    else if (archivedFolderItems.some((f) => f.folder_id === s.folder_id)) {
      archivedSessions.push({
        id: s.session_id,
        title: s.title,
        folderId: s.folder_id,
        folderName: '（已归档的领域）',
        state: s.state,
        time: shortTime(s.updated_at),
      });
    }
  }

  const archivedFolders = archivedFolderItems.map((f) => ({
    id: f.folder_id,
    name: f.name,
    sessionCount: f.session_count || 0,
  }));

  return { folders, archivedSessions, archivedFolders };
}

/* ── 一个会话 ──────────────────────────────────────────────────────── */

const TOOL_LABEL = {
  propose_plan_change: '起草计划改动',
  update_task_plan: '改计划',
  read_context: '读取上下文',
  search_preferences: '检索偏好',
  search_evidence: '检索证据',
  search_methods: '检索方法',
  propose_preference: '起草偏好',
  propose_evidence: '起草证据',
  ask_user: '向你提问',
  save_summary: '写下总结',
  web_search: '联网搜索',
};

/** 工具名 → 界面上的中文。查不到就**原样显示**，不猜一个更好听的名字。 */
export const toolLabel = (tool) => TOOL_LABEL[tool] || tool;

/**
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
};

/**
 * 工具轨迹 → 消息底下那几行小字。
 *
 * `status` 不是装饰：`rejected` 意味着**模型想做但被守卫拦住了**，
 * 界面上必须能区分"做成了"与"被拦下"，否则用户会把一次被拒绝的写入
 * 当成已经发生的改动。
 */
export function traceRows(toolTrace) {
  if (!Array.isArray(toolTrace) || !toolTrace.length) return null;
  return toolTrace.map((entry) => traceRow(entry));
}

export function traceRow(entry) {
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
}

/** 后端那句拦截理由里有 `**加粗**` 的 markdown，摘进一行小字前先去掉记号。 */
function shortText(value, limit) {
  const flat = String(value ?? '').replace(/\*\*/g, '').replace(/\s+/g, ' ').trim();
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
  const blocked = toolTrace.filter((entry) => ['rejected', 'error'].includes(entry.status)).length;
  return blocked
    ? `依据来自这一轮的 ${labels.join('、')}。其中 ${blocked} 个动作被守卫拦下了 —— 拦下的原因写在工具轨迹里。`
    : `依据来自这一轮的 ${labels.join('、')}；每一步的入参与返回都记在这条消息的工具轨迹里。`;
}

function adaptMessages(chat) {
  const messages = [];
  for (const row of chat || []) {
    const time = shortTime(row.created_at);
    if (row.role === 'user') {
      messages.push({ id: row.msg_id, role: 'user', time, text: row.content, foot: true });
      continue;
    }
    messages.push({
      id: row.msg_id,
      role: 'assistant',
      time,
      html: paragraphs(row.content),
      thinking: row.thinking || null,
      traces: traceRows(row.tool_trace),
      foot: true,
      basis: basisFromTrace(row.tool_trace),
      anchorStepId: row.anchor_step_id || null,
    });
  }
  return messages;
}

function adaptPlan(plan) {
  if (!plan) return null;
  const checkpoints = plan.checkpoints || [];
  const first = checkpoints[0];
  return {
    id: plan.plan_id,
    version: plan.version_no,
    currentVersion: plan.current_version,
    goalDerived: plan.goal_derived || '',
    steps: (plan.steps || []).map((step) => ({
      id: step.step_id,
      seq: step.seq,
      status: step.status,
      action: step.action,
      meta: step.output || step.method_ref || '',
    })),
    checkpoint: first
      ? {
        title: checkpoints.length > 1 ? `检查点 1 / ${checkpoints.length}` : '检查点',
        text: first.check_method,
        criteria: first.pass_criteria,
        atStep: first.at_step,
      }
      : null,
  };
}

const STRENGTH_BAR = { low: 1, medium: 2, high: 3 };

const MOTIVATION_TYPE = {
  intrinsic: ['内在', '因为这件事本身就值得做'],
  extrinsic: ['外在', '因为它带来的结果值得做'],
  mixed: ['内外混合', '既为了这件事本身，也为了它带来的结果'],
};

function bullets(value) {
  if (!value) return [];
  return String(value)
    .split(/\n|；|;/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function adaptMotivation(motivation) {
  if (!motivation || !motivation.type) return null;
  const [label, note] = MOTIVATION_TYPE[motivation.type] || [motivation.type, ''];
  return {
    type: label,
    typeNote: note,
    strength: STRENGTH_BAR[motivation.strength] || 0,
    drivers: bullets(motivation.drivers),
    obstacles: bullets(motivation.obstacles).map((line) => {
      const cut = line.search(/[：:，,]/);
      return cut > 0
        ? { title: line.slice(0, cut), note: line.slice(cut + 1) }
        : { title: line, note: '' };
    }),
    revision: motivation.updated_at ? `改于 ${shortTime(motivation.updated_at)}` : '还没有改过',
  };
}

/**
 * 上下文用量 → 右栏那张表。
 *
 * ★ `limit: 0` 表示**没测到**（模型没报窗口大小），不是"窗口是 0"。
 * 这时 `pct` 不显示成一个数，而由渲染层写「没测到」——
 * 一个 0% 的环会让人以为"几乎没用上下文"，那是相反的意思。
 */
export function adaptContext(meter) {
  if (!meter || !meter.measured) {
    return {
      used: '0',
      total: '没测到',
      pct: 0,
      measured: false,
      segments: [0, 0, 0, 0],
      sections: [],
      note: meter && meter.live_messages
        ? `已发出 ${meter.live_messages} 条消息，但没有拿到窗口上限。`
        : '这个会话还没有产生上下文。第一句话之后，这里会显示它实际看见的条目。',
    };
  }
  const used = Number(meter.used || 0);
  const limit = Number(meter.limit || 0);
  const measured = limit > 0;
  return {
    used: used.toLocaleString('zh-CN'),
    total: measured ? limit.toLocaleString('zh-CN') : '没测到',
    pct: measured ? Math.round((used / limit) * 100) : 0,
    measured,
    segments: [0, 0, 0, 0],
    sections: [],
    note: measured
      ? `这次回答实际发出 ${used.toLocaleString('zh-CN')} tokens。读数来自模型自己报的用量，不是估算。`
      : `这次回答实际发出 ${used.toLocaleString('zh-CN')} tokens；窗口上限模型没有报，所以没有百分比。`,
  };
}

/** `/state` 的整包 → 界面认的 detail。 */
export function adaptDetail(payload) {
  const session = payload?.session || null;
  return {
    session,
    loaded: true,
    messages: adaptMessages(payload?.chat),
    plan: adaptPlan(payload?.plan),
    motivation: adaptMotivation(payload?.motivation),
    input: payload?.input || null,
    snapshot: payload?.snapshot || null,
    context: adaptContext(null),
    versions: payload?.versions || null,
  };
}
