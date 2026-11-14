/**
 * 与后端说话的**唯一出口**。
 *
 * 为什么单独一个文件：界面里任何一处 `fetch` 都是"这条数据从哪来"这个问题的
 * 一部分。把它们集中在同一页，读代码的人不需要为了知道"计划版本冲突时后端返回
 * 什么"去翻五个组件。它也顺便保证了 **同源** —— 这个目录被 `serveui` 当静态根，
 * 所以 `BASE` 留空即可；换成 Vite dev server 时只改这一行。
 *
 * 两条约定：
 * 1. **错误只有一种形状**：`ApiError`，带 `status` 与后端原话。
 *    界面上永远显示后端说的话，不显示"请求失败"这种把原因抹掉的句子。
 * 2. **流的解析在这里做完**：调用方拿到的是已经拆好的事件对象，
 *    不需要自己处理 `\n\n`、`data: ` 与断帧。
 */

/** `serveui` 把前端目录当静态根，因此 API 与页面同源。 */
const BASE = window.STUDYFLOW_API_BASE || window.MOUXUE_API_BASE || '';

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

/**
 * 把后端的错误体压成**一句人话**。
 *
 * FastAPI 的三种形状：`{detail: "..."}`、`{detail: [{loc, msg}]}`（校验失败）、
 * `{code, message}`（本项目自己的错误封套）。第三种优先 ——
 * 它带着给用户看的那句话，而前两种是给开发者看的。
 *
 * ★ 一律**只保留第一句**：错误提示不是说明文档，长解释会盖住"哪里错了"。
 */
function errorMessage(body, status) {
  const clean = (text) => {
    const flat = String(text || '').replace(/\s+/g, ' ').trim();
    if (!flat) return '';
    const sentence = flat.split(/(?<=[。！？.!?])\s*/)[0];
    const first = sentence || flat;
    return first.length > 120 ? `${first.slice(0, 120)}…` : first;
  };
  if (body && typeof body === 'object') {
    if (typeof body.message === 'string' && body.message) return clean(body.message);
    if (typeof body.detail === 'string' && body.detail) return clean(body.detail);
    if (Array.isArray(body.detail) && body.detail.length) {
      const first = body.detail[0] || {};
      const where = Array.isArray(first.loc) ? first.loc.slice(1).join('.') : '';
      return clean(where ? `${where}：${first.msg || '不合法'}` : first.msg || '请求不合法');
    }
    if (typeof body.error === 'string' && body.error) return clean(body.error);
  }
  return `请求失败（HTTP ${status}）`;
}

async function call(method, path, body) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const res = await fetch(BASE + path, init);
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) throw new ApiError(errorMessage(data, res.status), res.status, data);
  return data;
}

const get = (path) => call('GET', path);
const post = (path, body) => call('POST', path, body);
const patch = (path, body) => call('PATCH', path, body);
const put = (path, body) => call('PUT', path, body);
const del = (path) => call('DELETE', path);

/**
 * 上传一个文件：**请求体就是文件字节**。
 *
 * 不能走 `call()` —— 那会把 `body` 做 `JSON.stringify`，发出去的是一个
 * 引号包起来的空对象。这里让浏览器自己把 `File` 当成字节流发出去。
 */
async function rawUpload(path, file) {
  const res = await fetch(BASE + path, { method: 'POST', body: file });
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) throw new ApiError(errorMessage(data, res.status), res.status, data);
  return data;
}

const seg = (value) => encodeURIComponent(value);

export const api = {
  health: () => get('/api/health'),

  // ── 领域与会话（侧栏）─────────────────────────────────────────────
  folders: (includeArchived = true) => get(`/api/folders?include_archived=${includeArchived}`),
  createFolder: (name, goal = null) => post('/api/folders', { name, goal }),
  renameFolder: (id, name) => patch(`/api/folders/${seg(id)}`, { name }),
  archiveFolder: (id) => patch(`/api/folders/${seg(id)}`, { archived: true }),
  restoreFolder: (id) => patch(`/api/folders/${seg(id)}`, { archived: false }),
  deleteFolder: (id) => del(`/api/folders/${seg(id)}`),
  moveFolder: (id, direction) => patch(`/api/folders/${seg(id)}`, { move: direction }),

  sessions: (includeArchived = true) => get(`/api/sessions?include_archived=${includeArchived}`),
  createSession: (folderId, title, inputGoal = null) =>
    post('/api/sessions', { folder_id: folderId, title, input_goal: inputGoal }),
  renameSession: (id, title) => patch(`/api/sessions/${seg(id)}`, { title }),
  archiveSession: (id) => patch(`/api/sessions/${seg(id)}`, { archived: true }),
  restoreSession: (id) => patch(`/api/sessions/${seg(id)}`, { archived: false }),
  deleteSession: (id) => del(`/api/sessions/${seg(id)}`),
  moveSession: (id, direction) => patch(`/api/sessions/${seg(id)}`, { move: direction }),

  // ── 一个会话的全部状态（对话 / 计划 / 动机 / 上下文）──────────────
  state: (id) => get(`/api/sessions/${seg(id)}/state`),
  /** 上下文用量（窗口上限来自此刻生效的模型；与会话数据分开读）。 */
  contextMeter: (id) => get(`/api/sessions/${seg(id)}/context`),
  /** 归档后自动复盘：模型尝试把会话沉淀进全局认知，返回待确认提案。 */
  archiveReview: (id) => post(`/api/sessions/${seg(id)}/archive-review`, {}),
  /** 对一条提案逐条裁决（只传属于它的 intent_id）。 */
  resolveProposal: (proposalId, decisions) =>
    post(`/api/proposals/${seg(proposalId)}/resolve`, { decisions }),

  // ── 计划 ──────────────────────────────────────────────────────────
  markStep: (sessionId, stepId, status, expectedVersion) =>
    patch(`/api/sessions/${seg(sessionId)}/plan/steps/${seg(stepId)}`, {
      status,
      expected_version: expectedVersion,
    }),
  /** 「点开计划项 → 讨论」的上下文包。**不调模型、不写库**，所以可以随时点。 */
  stepContext: (sessionId, stepId) =>
    get(`/api/sessions/${seg(sessionId)}/plan/steps/${seg(stepId)}/context`),

  // ── 笔记（侧记）─────────────────────────────────────────────────
  /** 写笔记。后端唯一的「终态也能写」的操作 —— 它不进上下文，也不改计划。 */
  addNote: (sessionId, content, tags) =>
    post(`/api/sessions/${seg(sessionId)}/notes`, { content, tags: tags || null }),

  // ── 任务定义 / 动机 ───────────────────────────────────────────────
  patchInput: (sessionId, body) => patch(`/api/sessions/${seg(sessionId)}/input`, body),
  patchMotivation: (sessionId, body) => patch(`/api/sessions/${seg(sessionId)}/motivation`, body),

  // ── 会话状态机（执行中 → 复盘 → 完成）────────────────────────────
  transition: (sessionId, target, expectedVersion, reason = null) =>
    post(`/api/sessions/${seg(sessionId)}/transitions`, {
      target,
      expected_version: expectedVersion,
      reason,
    }),

  // ── 模型档案（`/api/llm`）：在界面里填、立即生效 ────────────────────
  //
  // 这一组不是"认知状态"：它不改基线/偏好/计划，只换接下来由谁说话。
  // 密钥**永远不回显**（后端只给 `has_key` 与掩码），所以前端也没有地方存它。
  llmProviders: () => get('/api/llm/providers'),
  llmConfig: () => get('/api/llm/config'),
  llmProfiles: () => get('/api/llm/profiles'),
  llmCreateProfile: (body) => post('/api/llm/profiles', body),
  llmUpdateProfile: (id, body) => patch(`/api/llm/profiles/${seg(id)}`, body),
  llmDeleteProfile: (id) => del(`/api/llm/profiles/${seg(id)}`),
  llmActivateProfile: (id, model = null) =>
    post(`/api/llm/profiles/${seg(id)}/activate`, model ? { model } : {}),
  /** **POST 不是 GET**：它会拿密钥去请求外部服务（见后端 docstring）。 */
  llmListModels: (id) => post(`/api/llm/profiles/${seg(id)}/models`, {}),
  /** 真的发一次请求。清单里没有 ≠ 不能用，但"探活"必须是真动作。 */
  llmProbe: (id) => post(`/api/llm/profiles/${seg(id)}/probe`, {}),

  // ── 侧记：常驻通用模型（与会话无关，没有状态机）────────────────────
  sidebarState: () => get('/api/sidebar/state'),
  sidebarContext: () => get('/api/sidebar/context'),
  sidebarDiscuss: (message) => post('/api/sidebar/discuss', { message }),
  clearSidebar: () => del('/api/sidebar/messages'),

  // ── 会话资料（附件）───────────────────────────────────────────────
  //
  // 上传是**原始请求体**：`body: file`，文件名走查询参数。不是 multipart，
  // 后端没有 `python-multipart`（见 `app/api/attachments.py` 的说明）。
  attachments: (sessionId) => get(`/api/sessions/${seg(sessionId)}/attachments`),
  uploadAttachment: (sessionId, file) =>
    rawUpload(
      `/api/sessions/${seg(sessionId)}/attachments?filename=${encodeURIComponent(file.name)}`,
      file,
    ),
  attachmentText: (sessionId, attachmentId) =>
    get(`/api/sessions/${seg(sessionId)}/attachments/${seg(attachmentId)}`),
  deleteAttachment: (sessionId, attachmentId) =>
    del(`/api/sessions/${seg(sessionId)}/attachments/${seg(attachmentId)}`),

  // ── 自评单例（与基准测试分开：不给材料、只产 stated）────────────────
  selfAssessmentScale: (tier = 'full') =>
    get(`/api/self-assessment/scale?tier=${encodeURIComponent(tier)}`),
  submitSelfAssessment: (body) => post('/api/self-assessment/submit', body),
  selfAssessmentLatest: () => get('/api/self-assessment/latest'),
  selfAssessmentHistory: () => get('/api/self-assessment/history'),

  // ── 全局认知（只读概览；写入仍走提案/直写端点）──────────────────────
  preferences: () => get('/api/preferences'),
  m1Variables: () => get('/api/m1/variables'),
  evidence: () => get('/api/evidence'),
  hypotheses: () => get('/api/hypotheses'),
  proposals: () => get('/api/proposals'),
};

/**
 * 发一轮讨论，逐帧回调。**返回一个 `AbortController`** —— 停止键要能真的停下，
 * 而不是只把界面上的转圈关掉（那样服务端会把整轮跑完，用户的"停止"变成了谎话）。
 *
 * 帧格式（`app/agent/events.py`）：
 *   event: stage | thinking | token | tool_call | tool_result | done | error
 *   data:  {"seq": n, "step": n, ...}
 */
export function streamDiscuss(sessionId, { message, stepId = null, gestures = [] }, onEvent) {
  const controller = new AbortController();
  const path = `/api/sessions/${seg(sessionId)}/discuss/stream`;

  const run = (async () => {
    const res = await fetch(BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ message, step_id: stepId, gestures }),
      signal: controller.signal,
    });
    if (!res.ok) {
      const text = await res.text();
      let body = null;
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
      throw new ApiError(errorMessage(body, res.status), res.status, body);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut = buffer.indexOf('\n\n');
      while (cut >= 0) {
        const frame = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const event = parseFrame(frame);
        if (event) onEvent(event);
        cut = buffer.indexOf('\n\n');
      }
    }
  })();

  return { controller, run };
}

function parseFrame(frame) {
  let type = 'message';
  const dataLines = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) type = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  let data = {};
  try {
    data = JSON.parse(dataLines.join('\n'));
  } catch {
    data = { text: dataLines.join('\n') };
  }
  return { type, ...data };
}
