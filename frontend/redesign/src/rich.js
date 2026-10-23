/**
 * 模型回复的轻量富文本渲染。
 *
 * 约定（写在提示词里，模型照做）：
 * - `**…**`  强调：略大、加粗，但仍属于正文
 * - `` `…` `` 专有名词：细虚线下划线，悬停显示“专有名词”
 * - `[ST-001]` 计划步骤引用：可点击，跳到右侧计划并高亮
 * - 以 `? ` 开头的一行：变成可点击的快捷回复
 *
 * 只做白名单转换，模型输出一律先转义，不执行任何 HTML。
 */

const escapeHtml = (value) => String(value ?? '')
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&#39;');

function inline(text) {
  let out = escapeHtml(text);
  out = out.replace(/\[(ST-\d{3,})\]/g, (_, step) =>
    `<button type="button" class="step-ref" data-step-id="${step}" title="在计划里查看 ${step}">${step}</button>`);
  out = out.replace(/`([^`\n]+)`/g, (_, term) =>
    `<span class="term">${term}</span>`);
  out = out.replace(/\*\*([^*\n]+)\*\*/g, '<strong class="hl">$1</strong>');
  return out;
}

function plain(text) {
  return String(text ?? '')
    .replace(/\[(ST-\d{3,})\]/g, '$1')
    .replace(/`([^`\n]+)`/g, '$1')
    .replace(/\*\*([^*\n]+)\*\*/g, '$1')
    .trim();
}

/* ── 结构化块：mou-form / mou-plan ────────────────────────────────────
 *
 * 模型输出的是**协议**，不是让人读的 markdown：
 * - `mou-form`：需要用户填的值（bool / string），界面渲染成控件；
 * - `mou-plan`：计划草案，界面渲染成可确认的卡片。
 *
 * 两条纪律：
 * 1. **JSON 解析失败就退回普通代码块** —— 不猜、不修、不把半截 JSON 当成结构；
 * 2. 所有来自模型的字符串都先转义，再拼 HTML。
 */

const FORM_TYPES = new Set(['string', 'bool']);

function parseBlock(raw) {
  try {
    const value = JSON.parse(raw);
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
}

function formMarkup(spec) {
  const rawFields = Array.isArray(spec.fields) ? spec.fields.slice(0, 4) : [];
  const fields = rawFields
    .filter((field) => field && typeof field === 'object')
    .filter((field) => FORM_TYPES.has(field.type))
    .map((field) => ({
      key: String(field.key ?? '').trim() || `field_${Math.random().toString(36).slice(2, 7)}`,
      type: field.type,
      label: String(field.label ?? '').trim() || String(field.key ?? ''),
      placeholder: String(field.placeholder ?? ''),
      value: field.value,
    }));
  if (!fields.length) return '';

  const rows = fields.map((field) => {
    if (field.type === 'bool') {
      return `<label class="mou-bool">
        <input type="checkbox" data-mou-key="${escapeHtml(field.key)}"${field.value === true ? ' checked' : ''}>
        <span>${escapeHtml(field.label)}</span>
      </label>`;
    }
    const value = typeof field.value === 'string' ? field.value : '';
    return `<label class="mou-field">
      <span>${escapeHtml(field.label)}</span>
      <input type="text" data-mou-key="${escapeHtml(field.key)}"
        value="${escapeHtml(value)}" placeholder="${escapeHtml(field.placeholder)}">
    </label>`;
  }).join('');

  const title = String(spec.title ?? '').trim();
  const submit = String(spec.submit ?? '').trim() || '按这个继续';
  return `<div class="mou-form" data-mou-form="${escapeHtml(JSON.stringify(spec))}">
    <div class="mou-form-head">
      <span class="mou-form-mark">需要你定</span>
      ${title ? `<strong>${escapeHtml(title)}</strong>` : ''}
    </div>
    <div class="mou-form-fields">${rows}</div>
    <div class="mou-form-actions">
      <button type="button" class="mini-btn solid" data-action="submit-mou-form">${escapeHtml(submit)}</button>
      <span class="mou-form-hint">提交后会作为一条完整消息发回</span>
    </div>
  </div>`;
}

function planDraftMarkup(spec) {
  const steps = Array.isArray(spec.steps)
    ? spec.steps.filter((step) => step && typeof step === 'object' && String(step.action ?? '').trim())
    : [];
  if (!steps.length) return '';
  const checkpoints = Array.isArray(spec.checkpoints)
    ? spec.checkpoints.filter((cp) => cp && typeof cp === 'object')
    : [];
  const title = String(spec.title ?? '').trim() || '计划草案';
  return `<div class="mou-plan" data-mou-plan="${escapeHtml(JSON.stringify(spec))}">
    <div class="mou-plan-head">
      <span class="mou-plan-mark">草案 · 待确认</span>
      <strong>${escapeHtml(title)}</strong>
    </div>
    <ol class="mou-plan-steps">
      ${steps.map((step) => `<li>
        <span class="mou-plan-seq"></span>
        <div><strong>${escapeHtml(String(step.action))}</strong>
        ${String(step.output ?? '').trim() ? `<em>产出 · ${escapeHtml(String(step.output))}</em>` : ''}</div>
      </li>`).join('')}
    </ol>
    ${checkpoints.length ? `<div class="mou-plan-cps">
      <span>检查点</span>
      ${checkpoints.map((cp) => `<p>${escapeHtml(String(cp.check_method ?? '验证'))}
        · ${escapeHtml(String(cp.pass_criteria ?? ''))}</p>`).join('')}
    </div>` : ''}
    <label class="mou-plan-note">
      <span>想改的地方（可选）</span>
      <textarea rows="2" data-mou-note placeholder="例如：第二步再拆细一点"></textarea>
    </label>
    <div class="mou-plan-actions">
      <button type="button" class="mini-btn solid" data-action="submit-mou-plan">写入计划</button>
      <button type="button" class="mini-btn" data-action="dismiss-mou-plan">先不改</button>
    </div>
  </div>`;
}

/** 一段围栏内容 → 结构化块 HTML；不认识/不合法就返回空串。 */
function structuredBlock(lang, raw) {
  const spec = parseBlock(raw);
  if (!spec) return '';
  if (lang === 'mou-form') return formMarkup(spec);
  if (lang === 'mou-plan') return planDraftMarkup(spec);
  return '';
}

class Blocks {
  constructor() {
    this.html = [];
    this.para = [];
    this.list = null;
    this.asks = [];
  }

  flushPara() {
    if (!this.para.length) return;
    this.html.push(`<p>${this.para.map(inline).join('<br>')}</p>`);
    this.para = [];
  }

  flushList() {
    if (!this.list) return;
    const tag = this.list.ordered ? 'ol' : 'ul';
    this.html.push(`<${tag} class="rich-list">${this.list.items.map((item) => `<li>${inline(item)}</li>`).join('')}</${tag}>`);
    this.list = null;
  }

  flushAsks() {
    if (!this.asks.length) return;
    this.html.push(`<div class="quick-asks" role="group" aria-label="快捷回复">${this.asks
      .map((ask) => `<button type="button" class="quick-ask" data-prompt="${escapeHtml(ask)}">${escapeHtml(plain(ask))}<span>↵</span></button>`)
      .join('')}</div>`);
    this.asks = [];
  }

  flushAll() {
    this.flushPara();
    this.flushList();
    this.flushAsks();
  }
}

export function richText(text) {
  const source = String(text ?? '').replace(/\r\n/g, '\n');
  if (!source.trim()) return '';
  const blocks = new Blocks();
  const lines = source.split('\n');

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];

    // 围栏代码：原样保留（只转义）；`mou-form` / `mou-plan` 另作结构化渲染。
    if (/^\s*```/.test(line)) {
      blocks.flushAll();
      const lang = (line.match(/^\s*```\s*([\w-]*)/) || [])[1] || '';
      const code = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        code.push(lines[i]);
        i += 1;
      }
      const body = code.join('\n');
      const structured = structuredBlock(lang, body);
      if (structured) {
        blocks.html.push(structured);
      } else {
        blocks.html.push(`<pre class="rich-code"><code>${escapeHtml(body)}</code></pre>`);
      }
      continue;
    }

    if (!line.trim()) {
      blocks.flushPara();
      blocks.flushList();
      continue;
    }

    if (/^\s*[?？]\s+/.test(line)) {
      blocks.flushPara();
      blocks.flushList();
      blocks.asks.push(line.replace(/^\s*[?？]\s+/, ''));
      continue;
    }

    blocks.flushAsks();

    const heading = line.match(/^\s*#{1,4}\s+(.*)$/);
    if (heading) {
      blocks.flushPara();
      blocks.flushList();
      blocks.html.push(`<h4 class="rich-h">${inline(heading[1])}</h4>`);
      continue;
    }

    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (bullet) {
      blocks.flushPara();
      if (!blocks.list || blocks.list.ordered) {
        blocks.flushList();
        blocks.list = { ordered: false, items: [] };
      }
      blocks.list.items.push(bullet[1]);
      continue;
    }

    const numbered = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
    if (numbered) {
      blocks.flushPara();
      if (!blocks.list || !blocks.list.ordered) {
        blocks.flushList();
        blocks.list = { ordered: true, items: [] };
      }
      blocks.list.items.push(numbered[2]);
      continue;
    }

    const quote = line.match(/^\s*>\s?(.*)$/);
    if (quote) {
      blocks.flushPara();
      blocks.flushList();
      blocks.html.push(`<blockquote class="rich-quote">${inline(quote[1])}</blockquote>`);
      continue;
    }

    blocks.flushList();
    blocks.para.push(line);
  }

  blocks.flushAll();
  return blocks.html.join('');
}

export { escapeHtml };
