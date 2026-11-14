/**
 * 自评单例 —— **全屏、一次一条、可中断**。
 *
 * 为什么单独一个模块而不是塞进 app.js：
 *
 * 1. 它是一块**全屏独占**的作答面（`position: fixed; inset: 0`），
 *    与聊天区的渲染没有交集；混进 app.js 会让那个文件再多一百行互相无关的状态。
 * 2. 将来题量变大（或出现第二份量表）时，这里只需要换数据源：
 *    `mount()` 接收一份 `scale`（题目 + 选项），不假设条数。
 *
 * 三条界面纪律：
 *
 * - **一次只显示一条**。整卷铺在一页上，人会开始"调整前后一致性"——
 *   而那正是反向句要防的东西。
 * - **不显示任何计分信息**（第几条属于哪个变量、哪句是反向）。
 *   这些只在服务端；界面上只出现题号与进度。
 * - **可以中断**。答案实时存 localStorage，关掉页面再回来接着答；
 *   作答时间从第一次进入算，而不是从最后一次算。
 */

const AUTO_PREFIX = 'studyflow.selfAssessment.v2';
const AUTO_TTL_MS = 1000 * 60 * 60 * 24 * 3;

/** 两档各存各的草稿：简短版答到一半去开全面版，不该把对方的题号盖掉。 */
function draftKey(tier) {
  return `${AUTO_PREFIX}:${tier === 'short' ? 'short' : 'full'}`;
}

function readDraft(tier) {
  try {
    const raw = JSON.parse(localStorage.getItem(draftKey(tier)) || 'null');
    if (!raw || !raw.answers) return null;
    if (Date.now() - (raw.savedAt || 0) > AUTO_TTL_MS) return null;
    return raw;
  } catch {
    return null;
  }
}

function writeDraft(tier, draft) {
  try {
    localStorage.setItem(
      draftKey(tier),
      JSON.stringify({ ...draft, savedAt: Date.now() }),
    );
  } catch {
    /* storage may be unavailable */
  }
}

function clearDraft(tier) {
  try {
    localStorage.removeItem(draftKey(tier));
  } catch {
    /* storage may be unavailable */
  }
}

const optionKeyLabel = (index) => 'ABCDE'[index] || String(index + 1);

/**
 * 挂载一次自评。
 *
 * @param {object} options
 * @param {HTMLElement} options.root 全屏容器（会在里面建 DOM）
 * @param {object} options.scale `{options, statements, extra, total}`
 * @param {(payload: object) => Promise<object>} options.onSubmit
 * @param {(err: Error) => void} [options.onError]
 * @param {() => void} options.onClose
 */
export function mountSelfAssessment({ root, scale, onSubmit, onError, onClose }) {
  const statements = scale.statements || [];
  const options = scale.options || [];
  const tier = scale.tier === 'short' ? 'short' : 'full';
  // 旧版（44 条）留下的草稿按题号可能已经不属于这一档：只保留这一档还在问的题。
  const allowed = new Set(statements.map((statement) => statement.no));
  const draft = readDraft(tier);
  const answers = new Map(
    Object.entries(draft?.answers || {})
      .map(([k, v]) => [Number(k), v])
      .filter(([no]) => allowed.has(no)),
  );
  let index = Math.min(Number(draft?.index || 0), Math.max(0, statements.length - 1));
  let startedAt = draft?.startedAt || Date.now();
  let extraSpan = draft?.attentionSpan || '';
  let extraRank = Array.isArray(draft?.activityRank) ? draft.activityRank.slice() : [];
  let stage = 'statements'; // statements | extra | result | submitting
  let result = null;
  let error = '';
  /**
   * 选完自动前进的计时器。
   *
   * 必须能取消：用户点选之后 170ms 内再点「下一条」，如果不取消，
   * 两次前进会叠加 —— 进度条从他眼前跳两条，甚至跳过一条没看见的题。
   * 这也正是上一版「进度条乱跳」的根因。
   */
  let advanceTimer = null;
  function cancelAdvance() {
    if (advanceTimer !== null) {
      window.clearTimeout(advanceTimer);
      advanceTimer = null;
    }
  }

  root.hidden = false;
  root.innerHTML = `
    <div class="sa-shell">
      <header class="sa-top">
        <span class="sa-brand">
          <span class="sa-brand-mark">自评</span>
          <span class="sa-brand-note" data-sa="note">一次性 · ${statements.length} 条</span>
        </span>
        <div class="sa-progress" aria-hidden="true"><i data-sa="bar"></i></div>
        <span class="sa-counter" data-sa="counter">1 / ${statements.length}</span>
        <button class="icon-btn sa-close" data-sa="close" aria-label="退出自评">
          <svg><use href="#i-close"/></svg>
        </button>
      </header>

      <main class="sa-stage" data-sa="stage"></main>

      <footer class="sa-foot">
        <button class="mini-btn" data-sa="prev">上一条</button>
        <span class="sa-hint" data-sa="hint"></span>
        <button class="mini-btn solid" data-sa="next">下一条</button>
      </footer>
    </div>`;

  const el = (name) => root.querySelector(`[data-sa="${name}"]`);
  const stageEl = el('stage');
  const barEl = el('bar');
  const counterEl = el('counter');
  const hintEl = el('hint');
  const prevBtn = el('prev');
  const nextBtn = el('next');
  const noteEl = el('note');

  el('close').addEventListener('click', () => close());

  function persist() {
    writeDraft(tier, {
      answers: Object.fromEntries(answers),
      index,
      startedAt,
      attentionSpan: extraSpan,
      activityRank: extraRank,
    });
  }

  function answeredCount() {
    return answers.size;
  }

  function renderStatements() {
    cancelAdvance();
    stage = 'statements';
    const statement = statements[index];
    if (!statement) return renderExtra();
    const chosen = answers.get(statement.no);
    stageEl.innerHTML = `
      <div class="sa-card">
        <div class="sa-no">第 ${statement.no} 条</div>
        <p class="sa-text">${escape(statement.text)}</p>
        <div class="sa-options" role="radiogroup" aria-label="选择最常发生的一档">
          ${options.map((option, i) => `
            <button class="sa-option${chosen === option.value ? ' is-on' : ''}"
              data-value="${escape(option.value)}" role="radio"
              aria-checked="${chosen === option.value}">
              <span class="sa-option-key">${optionKeyLabel(i)}</span>
              <span class="sa-option-label">${escape(option.label)}</span>
            </button>`).join('')}
        </div>
      </div>`;
    stageEl.querySelectorAll('.sa-option').forEach((button) => {
      button.addEventListener('click', () => choose(button.dataset.value));
    });
    updateChrome();
  }

  function renderExtra() {
    cancelAdvance();
    stage = 'extra';
    const span = scale.extra?.attention_span;
    const rank = scale.extra?.activity_rank;
    stageEl.innerHTML = `
      <div class="sa-card sa-extra">
        <div class="sa-no">附加 · 两题（不进分数）</div>
        <p class="sa-text">${escape(span?.prompt || '')}</p>
        <div class="sa-options sa-options-row">
          ${(span?.options || []).map((option, i) => `
            <button class="sa-option${extraSpan === option.value ? ' is-on' : ''}"
              data-span="${escape(option.value)}" role="radio"
              aria-checked="${extraSpan === option.value}">
              <span class="sa-option-key">${optionKeyLabel(i)}</span>
              <span class="sa-option-label">${escape(option.label)}</span>
            </button>`).join('')}
        </div>
        <p class="sa-text sa-text-sub">${escape(rank?.prompt || '')}</p>
        <div class="sa-rank">
          ${(rank?.options || []).map((label, i) => `
            <label class="sa-rank-row">
              <span>${escape(label)}</span>
              <select data-rank="${i}">
                <option value="">—</option>
                ${[1, 2, 3, 4].map((n) => `<option value="${n}"${extraRank[i] === n ? ' selected' : ''}>${n}</option>`).join('')}
              </select>
            </label>`).join('')}
        </div>
        <p class="sa-note">排序可以整列留空 —— 那本身是一个答案，比随便排一个更真实。</p>
      </div>`;
    stageEl.querySelectorAll('[data-span]').forEach((button) => {
      button.addEventListener('click', () => {
        extraSpan = button.dataset.span;
        persist();
        renderExtra();
      });
    });
    stageEl.querySelectorAll('[data-rank]').forEach((select) => {
      select.addEventListener('change', () => {
        extraRank[Number(select.dataset.rank)] = Number(select.value) || 0;
        persist();
      });
    });
    counterEl.textContent = '附加';
    barEl.style.width = '100%';
    hintEl.textContent = '附加两题不参与分数';
    prevBtn.disabled = false;
    nextBtn.textContent = '提交';
    nextBtn.disabled = false;
  }

  function renderResult() {
    stage = 'result';
    const readings = result?.readings || [];
    const flags = result?.flags || [];
    stageEl.innerHTML = `
      <div class="sa-card sa-result">
        <div class="sa-no">自评已写入 · ${escape(result?.run_id || '')}</div>
        <p class="sa-text">${readings.length} 个变量各得到一条「自述」读数。它与基准测试的读数不换算。</p>
        ${tier === 'short'
          ? '<p class="sa-note">这次是简短版：每个方面只问了两条，能看方向，还不足以当稳定基线。想要更细的分项，随时可以做全面版。</p>'
          : ''}
        <ul class="sa-readings">
          ${readings.map((row) => `
            <li>
              <div class="sa-reading-head"><strong>${escape(row.variable_name)}</strong><span>${row.score}</span></div>
              ${Object.keys(row.facets || {}).length
                ? `<div class="sa-facets">${Object.entries(row.facets).map(([k, v]) => `<span>${escape(k)} ${v}</span>`).join('')}</div>`
                : ''}
            </li>`).join('')}
        </ul>
        ${flags.length ? `<p class="sa-note">${escape(flagText(flags))}（置信度已相应下调）</p>` : ''}
        <div class="sa-result-actions">
          <button class="mini-btn solid" data-sa="done">完成</button>
        </div>
      </div>`;
    el('done').addEventListener('click', () => close());
    counterEl.textContent = '完成';
    barEl.style.width = '100%';
    hintEl.textContent = '随时可以在自评页重做';
    prevBtn.disabled = true;
    nextBtn.disabled = true;
  }

  function renderError() {
    stageEl.innerHTML = `
      <div class="sa-card">
        <div class="sa-no">没有写入</div>
        <p class="sa-text">${escape(error)}</p>
        <p class="sa-note">答案还在这台机器上，可以重试，不会丢。</p>
        <div class="sa-result-actions">
          <button class="mini-btn solid" data-sa="retry">重试</button>
          <button class="mini-btn" data-sa="back">回到作答</button>
        </div>
      </div>`;
    el('retry').addEventListener('click', () => submit());
    el('back').addEventListener('click', () => renderStatements());
    nextBtn.disabled = true;
    prevBtn.disabled = true;
  }

  function updateChrome() {
    const total = statements.length;
    counterEl.textContent = `${index + 1} / ${total}`;
    barEl.style.width = `${Math.round(((index + 1) / total) * 100)}%`;
    noteEl.textContent = `已答 ${answeredCount()} / ${total}`;
    prevBtn.disabled = index === 0;
    nextBtn.textContent = index === total - 1 ? '去附加' : '下一条';
    const statement = statements[index];
    hintEl.textContent = answers.has(statement?.no) ? '' : '选最常发生的一档，不要折中';
  }

  function choose(value) {
    const statement = statements[index];
    if (!statement) return;
    cancelAdvance();
    answers.set(statement.no, value);
    persist();
    stageEl.querySelectorAll('.sa-option').forEach((button) => {
      const on = button.dataset.value === value;
      button.classList.toggle('is-on', on);
      button.setAttribute('aria-checked', String(on));
    });
    updateChrome();
    // 选完自动前进：一条 × 一次点击，不该再要求第二次点击。
    const at = index;
    advanceTimer = window.setTimeout(() => {
      advanceTimer = null;
      if (stage !== 'statements') return;
      // 这 170ms 里用户可能已经手动翻页 —— 那这次前进就作废。
      if (index !== at) return;
      if (answers.get(statement.no) !== value) return;
      index += 1;
      persist();
      renderStatements();
    }, 170);
  }

  function goPrev() {
    cancelAdvance();
    if (stage === 'extra') {
      index = statements.length - 1;
      renderStatements();
      return;
    }
    if (index > 0) {
      index -= 1;
      persist();
      renderStatements();
    }
  }

  function goNext() {
    cancelAdvance();
    if (stage === 'extra') {
      submit();
      return;
    }
    const statement = statements[index];
    if (statement && !answers.has(statement.no)) {
      hintEl.textContent = '先选一档（A–D）';
      return;
    }
    if (index < statements.length - 1) {
      index += 1;
      persist();
      renderStatements();
    } else {
      persist();
      renderExtra();
    }
  }

  async function submit() {
    cancelAdvance();
    const missing = statements.filter((s) => !answers.has(s.no)).map((s) => s.no);
    if (missing.length) {
      index = statements.findIndex((s) => s.no === missing[0]);
      renderStatements();
      hintEl.textContent = `还有 ${missing.length} 条没答`;
      return;
    }
    stage = 'submitting';
    stageEl.innerHTML = `
      <div class="sa-card sa-wait">
        <span class="ink-loader"><i></i><i></i><i></i></span>
        <p class="sa-text">正在写入自述读数…</p>
      </div>`;
    nextBtn.disabled = true;
    prevBtn.disabled = true;
    hintEl.textContent = '';
    try {
      const elapsed = Math.max(0, (Date.now() - startedAt) / 60000);
      result = await onSubmit({
        answers: Object.fromEntries(answers),
        tier,
        attention_span: extraSpan || null,
        activity_rank: extraRank.filter((n) => n),
        elapsed_minutes: Number(elapsed.toFixed(1)),
      });
      clearDraft(tier);
      renderResult();
    } catch (err) {
      error = err.message || String(err);
      if (onError) onError(err);
      renderError();
    }
  }

  function close() {
    cancelAdvance();
    if (stage === 'submitting') return;
    document.removeEventListener('keydown', onKey);
    root.hidden = true;
    root.innerHTML = '';
    if (onClose) onClose({ answered: answeredCount(), total: statements.length });
  }

  function onKey(event) {
    if (root.hidden) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
      return;
    }
    if (stage === 'statements') {
      const key = event.key.toUpperCase();
      const hit = options.find((option, i) => optionKeyLabel(i) === key || String(i + 1) === key);
      if (hit) {
        event.preventDefault();
        choose(hit.value);
        return;
      }
      if (event.key === 'ArrowLeft') { event.preventDefault(); goPrev(); }
      if (event.key === 'ArrowRight') { event.preventDefault(); goNext(); }
      return;
    }
    if (stage === 'extra') {
      const index2 = Number(event.key) - 1;
      const span = scale.extra?.attention_span?.options?.[index2];
      if (span) {
        event.preventDefault();
        extraSpan = span.value;
        persist();
        renderExtra();
      }
      if (event.key === 'Enter') { event.preventDefault(); submit(); }
    }
  }

  prevBtn.addEventListener('click', goPrev);
  nextBtn.addEventListener('click', goNext);
  document.addEventListener('keydown', onKey);

  if (answers.size && stage === 'statements') {
    // 续答：直接回到上次停下的那一条，不重放"从头开始"。
    renderStatements();
  } else if (answers.size >= statements.length) {
    renderExtra();
  } else {
    renderStatements();
  }

  return { close: () => close() };
}

/** 有效性标记 → 一句话（逐条列会把结果页变成一段日志）。 */
function flagText(flags) {
  const parts = [];
  if (flags.includes('straight_lining')) parts.push('直线作答');
  const reverse = flags.filter((f) => f.startsWith('acquiescence:')).length;
  if (reverse) parts.push(`反向不一致 ${reverse} 项`);
  if (flags.includes('too_fast')) parts.push('过快');
  if (flags.includes('too_slow')) parts.push('过慢');
  return parts.length ? `有效性：${parts.join(' · ')}` : '';
}

function escape(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
