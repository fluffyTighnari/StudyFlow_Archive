"""第二批：设置页的两个面板与全部按钮改成打真接口。

原来的四处假动作（都在这一个函数里）：
- 「探活」→ `Math.random()` 编延迟 + 编模型数，再 `toast('探活成功')`；
- 「思考」→ 只改内存里的字符串；
- 「新增档案」→ `toast('演示版暂不写入')`；
- 「启用」→ 只改 `state.activeProfile`，后端一无所知（下一轮还是老模型在答）。

现在：探活打 `POST /probe`（真发一句话，慢就慢）、思考打 `PATCH`、新增打 `POST`、
启用打 `POST /activate`（**立即生效**，下一轮讨论就用它）。
"""

from __future__ import annotations

import pathlib

HERE = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "redesign"
APP = HERE / "app.js"
text = APP.read_text(encoding="utf-8")


def swap(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    assert count == 1, f"{label}：锚点出现 {count} 次"
    text = text.replace(old, new, 1)
    print(f"✅ {label}")


# ── 1. renderSettings 整个换掉 ────────────────────────────────────────
START = "function renderSettings() {"
END = "\n}\n\n/* ── benchmark rendering"
start = text.index(START)
end = text.index(END, start)

NEW_SETTINGS = '''function renderSettings() {
  const card = $('#active-model-card');
  const prof = activeProfile();
  if (card) {
    if (!prof) {
      // 一份档案都没有：这时真正在说话的是 `.env` 里那个模型。
      // **不编一个档案出来**，如实说"还没有档案、现在用的是环境变量里的那个"。
      const s = state.llmConfig?.settings;
      card.innerHTML = `
        <span class="am-seal">当前印鉴</span>
        <div class="am-left">
          <div class="am-kicker"><span class="pulse-dot"></span>正在使用 · .env 默认值</div>
          <div class="am-title"><h3>还没有模型档案</h3><span class="am-model">${escapeHtml(s ? s.llm_model : '—')}</span></div>
          <div class="am-meta">
            <span>服务商 <b>${escapeHtml(s ? s.llm_provider : '—')}</b></span>
            <span>端点 <b>${escapeHtml(s ? s.llm_base_url : '—')}</b></span>
            <span>上下文 <b>${s && s.llm_num_ctx ? escapeHtml(String(s.llm_num_ctx)) : '—'}</b></span>
            <span>密钥 <b>${s && s.has_api_key ? '已设置' : '未设置'}</b></span>
          </div>
          <div class="am-think">
            <em>在下面新建一份档案并点「启用」，讨论就改由它回答。这一栏显示的永远是<b>实际</b>在用的东西。</em>
          </div>
        </div>`;
    } else {
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
            <em>自动 = 不发这个字段，对不认识的模型更安全（只能在新建时设定）</em>
          </div>
        </div>
        <div class="am-probe">
          <button class="mini-btn" data-action="probe" data-id="${prof.id}">${state.lang === 'en' ? 'Probe' : '探活'}</button>
          <span class="probe-result" id="probe-result">${escapeHtml(state.probeText[prof.id] || '尚未探活')}</span>
        </div>`;
    }
  }

  const list = $('#profile-list');
  if (list) {
    if (!state.profiles.length) {
      list.innerHTML = `<div class="profile-empty">还没有档案。点下面那一行新建一份 —— 本地 Ollama 或任意 OpenAI 兼容端点都行。</div>`;
    } else {
      list.innerHTML = state.profiles
        .map(
          (p) => `
      <div class="profile ${p.active ? 'is-active' : ''}">
        <span class="profile-mark">${escapeHtml(p.mark)}</span>
        <div class="profile-main">
          <strong>${escapeHtml(p.label)}</strong>
          <span>${escapeHtml(p.model)}${p.note ? ' · ' + escapeHtml(p.note) : ''}</span>
        </div>
        <div class="profile-tags">
          <span class="tag">${escapeHtml(p.provider)}</span>
          <span class="tag ${p.kind === 'local' ? 'jade' : ''}">${escapeHtml(p.ctx)}</span>
          <span class="tag">${escapeHtml(p.masked)}</span>
          ${p.active ? `<span class="tag accent">${t('model.current')}</span>` : ''}
        </div>
        <div class="profile-actions">
          ${
            p.active
              ? '<span class="tag jade">●</span>'
              : `<button class="mini-btn" data-action="activate-profile" data-id="${p.id}">${t('model.use')}</button>`
          }
          <button class="mini-btn" data-action="list-models" data-id="${p.id}">拉清单</button>
          <button class="mini-btn" data-action="edit-profile" data-id="${p.id}">编辑</button>
          <button class="mini-btn" data-action="delete-profile" data-id="${p.id}">删除</button>
        </div>
      </div>`,
        )
        .join('');
    }
  }
}'''
text = text[:start] + NEW_SETTINGS + text[end:]
print("✅ renderSettings：两个面板改成真数据")

# ── 2. 分发：探活 / 思考 / 新增 ───────────────────────────────────────
swap(
    """    case 'probe':
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
      break;""",
    """    case 'probe':
      probeProfile(id, el);
      break;
    case 'set-think': {
      const profile = state.profiles.find((p) => p.id === id);
      if (!profile) break;
      const value = el.dataset.value;
      if (value === 'auto') {
        // 后端把 `think: null` 当"不改"，所以「自动」只能在新建时设定。
        // 不假装切过去了 —— 假装的话，下一轮会照旧发送 think 字段。
        toast('「自动」= 不发送 think 字段，只能在新建档案时设定（已设过的改不回去）', 'danger');
        break;
      }
      updateProfile(id, { think: value === 'true' });
      break;
    }
    case 'add-profile':
      openProfileForm(null);
      break;
    case 'edit-profile':
      openProfileForm(state.profiles.find((p) => p.id === id) || null);
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
            await loadModels();
            const next = result.active_profile_id
              ? state.profiles.find((p) => p.id === result.active_profile_id)
              : null;
            toast(next ? `已删除 · 现在生效的是「${next.label}」` : '已删除 · 现在用 .env 里的模型');
          } catch (err) {
            toast(err.message, 'danger');
          }
        },
      });
      break;
    }
    case 'list-models': {
      const button = el;
      button.disabled = true;
      try {
        const got = await api.llmListModels(id);
        const models = got.models || [];
        if (models.length) {
          toast(`${models.length} 个模型：${models.slice(0, 6).join('、')}${models.length > 6 ? '…' : ''}`);
        } else {
          // `note` 是人话解释（不支持列清单 / 没密钥 / 网络不通），原样显示
          toast(got.note || '这家问不出模型清单 · 手动填模型名即可', 'danger');
        }
      } catch (err) {
        toast(err.message, 'danger');
      } finally {
        button.disabled = false;
      }
      break;
    }""",
    "分发：探活/思考/新增/编辑/删除/拉清单",
)

# ── 3. 启用与探活改成真调用 ──────────────────────────────────────────
OLD_FUNCS_START = "function activateProfile(id) {"
OLD_FUNCS_END = "\nfunction startBench(form) {"
a = text.index(OLD_FUNCS_START)
b = text.index(OLD_FUNCS_END, a)

NEW_FUNCS = '''/**
 * 启用一份档案。**这是真的切换**：`POST /activate` 之后下一轮讨论就用它，
 * 所以这里必须等后端回话，不能只改界面上的一个变量。
 */
async function activateProfile(id) {
  if (state.busy) {
    toast('正在生成 · 先停止这一轮再切换模型', 'danger');
    closePopover();
    return;
  }
  try {
    const row = await api.llmActivateProfile(id);
    closePopover();
    await loadModels({ render: false });
    renderSettings();
    renderTopbar();
    renderRail();
    updateBusyUI();
    toast(`已切换到 ${row.label || row.model} · 下一轮就由它回答`);
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
    await api.llmUpdateProfile(id, {
      label: current.label,
      provider_id: current.providerId,
      model: current.model,
      base_url: current.base,
      num_ctx: current.numCtx || null,
      note: current.note,
      ...changes,
    });
    await loadModels();
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
    toast(probe.ok ? '探活：通' : '探活：不通', probe.ok ? '' : 'danger');
  } catch (err) {
    state.probeText[id] = `✗ ${err.message}`;
    if (result) result.textContent = state.probeText[id];
    toast(err.message, 'danger');
  } finally {
    button.disabled = false;
    if (result) result.classList.remove('is-pending');
  }
}
'''
text = text[:a] + NEW_FUNCS + text[b:]
print("✅ 启用 / 编辑 / 探活改成真调用")

# ── 4. boot：先把模型档案读进来 ──────────────────────────────────────
swap(
    """  try {
    await loadWorkspace();
    if (!state.currentSession) {""",
    """  try {
    // 模型档案要在画侧栏之前就位（侧栏那行写的是"现在是谁在说话"）。
    // 它读的是 `/api/llm/*`，与会话数据互不依赖，失败也不该挡住会话。
    await loadModels({ render: false });
    await loadWorkspace();
    if (!state.currentSession) {""",
    "boot：先读模型档案",
)

swap(
    """    renderSession();
    if (state.currentSession) await loadDetail(state.currentSession);
    else ensureSelection();""",
    """    renderSession();
    renderSettings();
    renderTopbar();
    renderRail();
    if (state.currentSession) await loadDetail(state.currentSession);
    else ensureSelection();""",
    "boot：模型读到之后重画顶栏/侧栏/设置",
)

APP.write_text(text, encoding="utf-8")
print("\n第二批改完")
