/**
 * 无头验收的**探针**：只在 `?probe=1` 时被动态加载。
 *
 * 它不 import 应用内部的东西，而是像一个人那样点：点计划项、打字、发送、
 * 在侧栏里新建 / 改名 / 归档 / 恢复 / 删除。验的是"用户真的走通了吗"，
 * 而不是"函数调对了没"。
 *
 * ★ 结果**每走一步就写一次** `<pre id="probe-report">`：无头浏览器在
 *   `--virtual-time-budget` 用尽时把 DOM 抓走，而这个探针要等一条 SSE 流跑完。
 *   只在最后写一次的话，抓到的永远是一张空白 —— 分不清"没跑"与"跑到一半被掐了"。
 *
 * ★ 等待用 **MutationObserver**，不用定时器：`--virtual-time-budget` 会把
 *   `setTimeout` 当虚拟时间快进，"每 120ms 看一眼、看 40 秒"在真实时间里
 *   只过了几十毫秒，而一条 SSE 流需要真实的一秒。用定时器等会赢在虚拟时间、
 *   输在真实网络上，报出来是"等太久了"，看着像功能坏了。
 *
 * ★ 接管 `error` / `unhandledrejection`：`--dump-dom` 看不见控制台，
 *   不把异常写进 DOM 的话，一次模块级语法错会表现成"什么都没发生"。
 */

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const report = { steps: [], ok: null };

const toasts = [];
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
}

function mark(name, detail) {
  report.steps.push({ name, ...(detail || {}) });
  flush();
}

/**
 * 等 DOM 出现某个东西 —— 用 MutationObserver 而不是轮询。
 *
 * ★ **必须带超时**：等不到的时候如果只是"永远挂着"，报告里只会看到
 *   "停在上一步、ok 还是 null"，真正的原因一个字都不剩。带上 `label`
 *   与真实超时，失败会指到具体的那个等待上。
 */
function waitDom(check, label = '等一个界面变化', timeoutMs = 15_000) {
  return new Promise((resolve, reject) => {
    const first = check();
    if (first) {
      resolve(first);
      return;
    }
    let timer = null;
    const observer = new MutationObserver(() => {
      const value = check();
      if (value) {
        observer.disconnect();
        if (timer) clearTimeout(timer);
        resolve(value);
      }
    });
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
    });
    timer = setTimeout(() => {
      observer.disconnect();
      reject(new Error(`等不到：${label}`));
    }, timeoutMs);
  });
}

/** 右栏计划那一栏写着「计划 · 第 N 版」，从那里读版本号。 */
function planVersion() {
  const head = $('.mpanel-head h3');
  const match = head ? head.textContent.match(/第\s*(\d+)\s*版/) : null;
  return match ? Number(match[1]) : null;
}

function sessionTitled(title) {
  // 只找「未归档」那一栏：归档行也带 `.session` 类（同一套卡片样式），
  // 不限定范围的话，"它还在侧栏吗"永远为真。
  return $$('#folder-list .session .session-title').find((el) => el.textContent.trim() === title) || null;
}

function archivedTitled(title) {
  return $$('#archive-list .archived-item').find((el) => {
    const span = el.querySelector('.session-title');
    return span && span.textContent.trim() === title;
  }) || null;
}

function click(selector, root = document) {
  const el = $(selector, root);
  if (!el) throw new Error(`界面上找不到：${selector}`);
  el.click();
  return el;
}

window.addEventListener('error', (event) => {
  mark('页面报错', { ok: false, message: String(event.message || event.error) });
});
window.addEventListener('unhandledrejection', (event) => {
  mark('未处理的 Promise', {
    ok: false,
    message: String(event.reason && event.reason.message ? event.reason.message : event.reason),
  });
});

/* ── 第一段：一轮真实讨论 ─────────────────────────────────────────── */
async function phaseChat() {
  await waitDom(() => $$('.session[data-session-id]').length > 0, '侧栏出现会话');
  mark('侧栏有真实会话', { ok: true, count: $$('.session[data-session-id]').length });
  report.planBefore = planVersion();

  const step = await waitDom(() => $('.steps .step[data-step-id]'), '计划里出现步骤');
  const stepId = step.dataset.stepId;
  $('.step-body', step).click();
  await sleep(150);
  const placeholder = $('#composer-input').placeholder || '';
  mark('点开计划项', { ok: placeholder.includes('关于第'), stepId, placeholder });

  report.messagesBefore = $$('.thread .msg').length;
  const input = $('#composer-input');
  input.value = '这一步我想拆成两天，每天一半';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  click('#send-btn');
  mark('已发送', { ok: true });

  await sleep(400);
  mark('锚点已清空', { ok: !($('#composer-input').placeholder || '').includes('关于第') });

  await waitDom(() => {
    const live = $('article.msg-assistant.is-live');
    const last = $$('article.msg-assistant').pop();
    return !live && last && last.querySelector('.msg-foot');
  }, '这一轮讨论结束');
  mark('这一轮结束', { ok: true });

  const last = $$('article.msg-assistant').pop();
  const traces = $$('.trace-item', last).map((el) => ({
    label: ($('strong', el) || {}).textContent || '',
    note: ($('span', el) || {}).textContent || '',
    state: el.className.replace('trace-item', '').trim(),
  }));
  const prose = (($('.msg-prose', last) || {}).textContent || '').trim();
  mark('有回答正文', { ok: prose.length > 0, chars: prose.length, head: prose.slice(0, 30) });
  // 工具轨迹只**记录**，不判失败：替身按场景轮换剧本，有的场景本来就不调工具
  mark('工具轨迹（仅供参考）', { ok: true, count: traces.length, traces });

  report.messages = $$('.thread .msg').length;
  report.messagesGrew = report.messages > (report.messagesBefore || 0);
  mark('消息条数增长', { ok: report.messagesGrew, from: report.messagesBefore, to: report.messages });
  report.planAfter = planVersion();
  report.planChanged = report.planBefore !== report.planAfter;
  mark('计划版本（仅供参考）', { ok: true, before: report.planBefore, after: report.planAfter });
}

/* ── 第一段半：侧记**真的和模型对话** ─────────────────────────────────
 *
 * 判据改过一次（2026-11-08）。上一版查的是「回执里有没有 `已存为笔记 N-0001`」——
 * 那是把侧记当成"写笔记"的旧实现。现在侧记走 `POST /api/sidebar/discuss`，
 * 回答来自模型，**没有笔记编号可查**；继续按旧判据查，只会永远红着，
 * 而红的原因不是功能坏了，是探针没跟上产品。
 *
 * 现在的判据是它真正该做的事：**用户说一句，助手回一段非空正文**，
 * 并且这一段不是用户那句的复读。`noteId` 仍原样记录（有就记，没有就是 null），
 * 供人工核对用 —— 但不作为通过条件。
 */
async function phaseSideNote() {
  click('[data-action="open-side-note"]');
  const input = await waitDom(() => $('#sn-input'), '侧记面板打开');
  const before = $$('#sn-thread .sn-msg').length;
  const asked = '词根 re- 我总记混，先记一笔。';
  input.value = asked;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  click('#sn-thread ~ * [data-action="sn-send"], [data-action="sn-send"]');

  const reply = await waitDom(() => {
    const rows = $$('#sn-thread .sn-msg');
    const last = rows[rows.length - 1];
    return rows.length > before && last && last.classList.contains('from-bot') ? last : null;
  }, '侧记给出回执');
  const text = String(reply.textContent || '').trim();
  // 角色标签（「侧记」两字）也在 textContent 里，所以只要求"除去标签后仍有正文"。
  const body = text.replace(/^侧记\s*/, '').trim();
  const echoed = body === asked;
  mark('侧记给出非空回答', {
    ok: body.length > 0 && !echoed,
    len: body.length,
    echoed,
    text: body.slice(0, 60),
  });
  // 有笔记编号就记下来供核对，但不作为通过条件（当前实现不产编号）。
  mark('侧记笔记编号（仅记录）', {
    ok: true,
    noteId: (text.match(/已存为笔记\s*([A-Z]+-\d+)/) || [])[1] || null,
  });
  mark('侧记顶上的上下文数字', { ok: true, tokens: String(($('#sn-tokens') || {}).textContent || '') });
  click('[data-action="close-side-note"]');
}

/* ── 第二段：会话的生命周期（新建 → 改名 → 归档 → 恢复 → 删除）──────── */
async function phaseLifecycle() {
  const title = `探针会话 ${new Date().toISOString().slice(11, 19)}`;
  const renamed = `${title} · 改过`;

  click('[data-action="new-session"]');
  const form = await waitDom(() => ($('#rail-form').hidden ? null : $('#rail-form')), '新建表单出现');
  $('#rail-form-input').value = title;
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

  await waitDom(() => sessionTitled(title), `新会话出现在侧栏：${title}`);
  mark('新建会话', { ok: true, title, current: !!$('.session.is-current') });

  // 改名：点这一行的 ⋯ → 菜单里的「重命名」→ 填新名字 → 保存
  click('.session-menu-btn', sessionTitled(title).closest('.session'));
  click('#popover-layer [data-action="rename-session"]');
  const renameInput = await waitDom(() => $('#popover-layer .rename-input'), '重命名小窗出现');
  renameInput.value = renamed;
  $('#popover-layer form.rename-form').dispatchEvent(
    new Event('submit', { bubbles: true, cancelable: true }),
  );
  await waitDom(() => sessionTitled(renamed), `改名后的标题出现在侧栏：${renamed}`);
  mark('重命名会话', { ok: true, title: renamed });

  // 归档：这一行从「会话」里消失，出现在「已归档」里
  click('.session-menu-btn', sessionTitled(renamed).closest('.session'));
  click('#popover-layer [data-action="archive-session"]');
  await waitDom(() => !sessionTitled(renamed) && archivedTitled(renamed), '会话移进「已归档」');
  mark('归档会话', { ok: true, archived: !!archivedTitled(renamed) });

  // 恢复
  click('.session-menu-btn', archivedTitled(renamed));
  click('#popover-layer [data-action="restore-session"]');
  await waitDom(() => sessionTitled(renamed) && !archivedTitled(renamed), '会话从「已归档」回到侧栏');
  mark('恢复会话', { ok: true });

  // 删除：先出确认框，再点「删除会话」
  click('.session-menu-btn', sessionTitled(renamed).closest('.session'));
  click('#popover-layer [data-action="delete-session"]');
  await waitDom(() => $('#confirm-dialog[open]'), '删除确认框打开');
  click('#confirm-ok');
  await waitDom(() => !sessionTitled(renamed), '删除后会话从侧栏消失');
  mark('删除会话', { ok: true });
}

export async function run() {
  watchToasts();
  flush();
  try {
    await phaseChat();
    await phaseSideNote();
    await phaseLifecycle();
    report.ok = true;
  } catch (err) {
    report.ok = false;
    report.error = String(err && err.message ? err.message : err);
  }
  flush();
}
