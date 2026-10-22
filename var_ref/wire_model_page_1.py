"""模型页接真接口：这一页原来整页都是假的。

现场（`app.js`）：
- 模型清单是**写死的 JS 数组** `PROFILES`（四份手抄的档案）；
- 「启用」只改一个内存变量；
- 「探活」用 `Math.random()` 编一个延迟和一个模型数，再 `toast('探活成功')`；
- 「思考」开关只改内存；
- 「新增档案」直接 `toast('演示版暂不写入')`。

后端 `app/api/llm.py` 早就整套做好了（增删改查 + 启用 + 拉清单 + 探活，
密钥只落 `var/secrets/`、回显永远掩码）。所以这不是"功能没做"，
是**界面在演一个已经存在的功能** —— 这一页比没有它更糟。

改法：把四份写死的档案换成 `GET /api/llm/profiles`，所有按钮打真端点。
"""

from __future__ import annotations

import pathlib

HERE = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "redesign"
API = HERE / "src" / "api.js"
APP = HERE / "app.js"


def swap(path: pathlib.Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    assert count == 1, f"{label}：锚点出现 {count} 次"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"✅ {label}")


# ── 1. api.js：模型档案的九个端点 ─────────────────────────────────────
swap(
    API,
    """    post(`/api/sessions/${seg(sessionId)}/transitions`, {
      target,
      expected_version: expectedVersion,
      reason,
    }),
};""",
    """    post(`/api/sessions/${seg(sessionId)}/transitions`, {
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
  llmActivateProfile: (id) => post(`/api/llm/profiles/${seg(id)}/activate`, {}),
  /** **POST 不是 GET**：它会拿密钥去请求外部服务（见后端 docstring）。 */
  llmListModels: (id) => post(`/api/llm/profiles/${seg(id)}/models`, {}),
  /** 真的发一次请求。清单里没有 ≠ 不能用，但"探活"必须是真动作。 */
  llmProbe: (id) => post(`/api/llm/profiles/${seg(id)}/probe`, {}),
};""",
    "api.js：九个模型档案端点",
)

# ── 2. app.js：删掉写死的 PROFILES ───────────────────────────────────
APP_TEXT = APP.read_text(encoding="utf-8")
start = APP_TEXT.index("const PROFILES = [")
end = APP_TEXT.index("\n];\n", start) + len("\n];\n")
APP.write_text(
    APP_TEXT[:start]
    + """//: 模型档案**不再写死在界面里**（原来是四份手抄的假档案）。
//: 真的那份在 `GET /api/llm/profiles`，由 `loadModels()` 拉进 `state.profiles`。
""".replace("//:", "//")
    + APP_TEXT[end:],
    encoding="utf-8",
)
print("✅ app.js：写死的 PROFILES 已删除")

# ── 3. state：档案改成空数组 + 服务商目录 + 配置快照 ─────────────────
swap(
    APP,
    """  activeProfile: 'p-local',
  profiles: structuredClone(PROFILES),""",
    """  //: 生效中的档案 id（来自 `is_active`），没有档案时为 `null`。
  activeProfile: null,
  //: 真档案（`/api/llm/profiles` → `mapProfile`）。
  profiles: [],
  //: 服务商目录（`/api/llm/providers`），新建档案时的下拉框。
  providers: [],
  //: `GET /api/llm/config`：此刻实际用的是 profile 还是 `.env`。
  llmConfig: null,
  //: 探活结果按档案记（`probe.detail` 原话），不编造延迟。
  probeText: {},""",
    "state：模型档案改成真数据",
)

# ── 4. activeProfile + 拉取 ───────────────────────────────────────────
swap(
    APP,
    "function activeProfile() { return state.profiles.find((p) => p.id === state.activeProfile) || state.profiles[0]; }",
    """function activeProfile() {
  return state.profiles.find((p) => p.id === state.activeProfile) || null;
}

/**
 * 顶栏/侧栏该显示哪个模型名。
 *
 * 没有档案时**不编一个名字**：这时真正在说话的是 `.env` 里那一个
 * （`/api/llm/config` 的 `settings`），那就如实显示它。
 */
function currentModelLabel() {
  const prof = activeProfile();
  if (prof) return prof.short || prof.model;
  const model = state.llmConfig?.settings?.llm_model;
  return model ? `${model} · 来自 .env` : '未配置模型';
}

/** 后端的一行档案 → 界面用的视图模型（字段名照抄，方便对照接口）。 */
function mapProfile(row) {
  const meta = state.providers.find((x) => x.provider_id === row.provider_id);
  return {
    id: row.profile_id,
    label: row.label || row.profile_id,
    short: row.label || row.model,
    model: row.model,
    model2: row.model,
    providerId: row.provider_id,
    provider: meta ? meta.label : row.provider_id,
    kind: row.kind,
    base: row.base_url,
    numCtx: row.num_ctx,
    ctx: row.num_ctx ? String(row.num_ctx) : '—',
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
function openProfileForm(existing) {
  if (!state.providers.length) {
    toast('服务商目录还没读到 · 稍后再试', 'danger');
    return;
  }
  const p = existing || null;
  const options = state.providers
    .map(
      (x) =>
        `<option value="${x.provider_id}"${p && p.providerId === x.provider_id ? ' selected' : ''}>${escapeHtml(x.label)}</option>`,
    )
    .join('');
  const pop = openPopover(
    $('#profile-list') || document.body,
    `<div class="popover-label">${p ? '编辑模型档案' : '新增模型档案'}</div>
     <form class="profile-form">
       <label>服务商<select class="pf-provider">${options}</select></label>
       <label>模型名<input class="pf-model" value="${escapeHtml(p ? p.model : '')}" placeholder="例如 qwen3.6:27b"></label>
       <label>档案名<input class="pf-label" value="${escapeHtml(p ? p.label : '')}" placeholder="不填就用模型名"></label>
       <label>端点<input class="pf-base" value="${escapeHtml(p ? p.base : '')}" placeholder="留空用服务商默认"></label>
       <label>上下文 tokens<input class="pf-ctx" type="number" min="512" step="512" value="${p && p.numCtx ? p.numCtx : ''}" placeholder="留空用默认"></label>
       <label>密钥<input class="pf-key" type="password" autocomplete="off" placeholder="${p && p.hasKey ? '已设置 · 留空表示不改' : '需要密钥的服务商才填'}" /></label>
       ${p && p.hasKey ? '<label class="pf-row"><input type="checkbox" class="pf-clear-key"> 清掉已存的密钥</label>' : ''}
       <div class="inline-actions">
         <button type="submit" class="mini-btn solid">保存</button>
         <button type="button" class="mini-btn" data-action="close-popover">取消</button>
       </div>
     </form>`,
    'popover-form',
  );
  pop.querySelector('form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const providerId = pop.querySelector('.pf-provider').value;
    const meta = state.providers.find((x) => x.provider_id === providerId);
    const model = pop.querySelector('.pf-model').value.trim() || meta?.default_model || '';
    const body = {
      label: pop.querySelector('.pf-label').value.trim() || model,
      provider_id: providerId,
      model,
      base_url: pop.querySelector('.pf-base').value.trim() || null,
      num_ctx: Number(pop.querySelector('.pf-ctx').value) || null,
      note: p ? p.note : '',
    };
    const keyInput = pop.querySelector('.pf-key');
    const clearKey = pop.querySelector('.pf-clear-key');
    if (clearKey && clearKey.checked) body.api_key = '';
    else if (keyInput.value) body.api_key = keyInput.value;
    try {
      if (p) await api.llmUpdateProfile(p.id, body);
      else await api.llmCreateProfile(body);
      closePopover();
      await loadModels();
      toast(p ? '档案已更新' : '档案已建好 · 点「启用」才生效');
    } catch (err) {
      toast(err.message, 'danger');
    }
  });
}""",
    "activeProfile / mapProfile / loadModels / 表单",
)

# ── 5. 侧栏与顶栏显示真模型 ──────────────────────────────────────────
swap(
    APP,
    """  const prof = activeProfile();
  $('#rail-model-name').textContent = prof.short;
  $('#rail-model .foot-model-text em').textContent = (state.lang === 'en')
    ? `${prof.provider} · ${prof.ctx} context`
    : `${prof.provider === 'Ollama' ? '本地 Ollama' : '外部 API'} · 上下文 ${prof.ctx.replace(' 000', 'k').replace(' 768', 'k')}`;""",
    """  const prof = activeProfile();
  $('#rail-model-name').textContent = currentModelLabel();
  $('#rail-model .foot-model-text em').textContent = prof
    ? `${prof.provider} · 上下文 ${prof.ctx}`
    : (state.llmConfig?.settings ? '设置 → 模型：加一份档案就能换' : '读不到模型配置');""",
    "侧栏：显示真模型",
)

swap(
    APP,
    """  const prof = activeProfile();
  $('#model-chip-name').textContent = prof.short;""",
    """  $('#model-chip-name').textContent = currentModelLabel();""",
    "顶栏：显示真模型",
)

print("\n第一批改完（接口 / 状态 / 拉取 / 显示）")
