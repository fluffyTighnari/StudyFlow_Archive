/**
 * 只查一件事：**设置 → 模型** 那一页画出来的东西是不是后端给的。
 *
 * 为什么单独一个脚本：`?probe=1` 那个验收驱动会跑一整轮真实对话
 * （本地 9B 模型几十秒一轮），而这里要证的是"模型页不再是一份写死的假档案"。
 * 那是渲染问题，与对话无关，所以只开页面、等 `loadModels()` 落定、读 DOM。
 *
 * 用法（后端已在 8125 起着，`--dist` 指向 frontend/redesign）：
 *   node var_ref/check_model_page.mjs
 *
 * 退出码：0 = 与接口一致；1 = 不一致（会把两边的值都打出来）。
 */

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';

const URL_ = process.argv[2] || 'http://127.0.0.1:8125/';
const PORT = 9392;
const EDGE_CANDIDATES = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
];

async function cdpTarget() {
  for (let i = 0; i < 60; i += 1) {
    try {
      const resp = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await resp.json();
      const page = list.find((t) => t.type === 'page');
      if (page) return page;
    } catch {
      /* 浏览器还没起来 */
    }
    await delay(250);
  }
  throw new Error('连不上 CDP');
}

async function main() {
  const edge = EDGE_CANDIDATES.find((p) => existsSync(p));
  if (!edge) throw new Error('找不到 msedge.exe');

  const child = spawn(
    edge,
    [
      '--headless=new',
      `--remote-debugging-port=${PORT}`,
      '--user-data-dir=' + process.env.TEMP + '\\dsh-model-page-check',
      '--no-first-run',
      '--no-default-browser-check',
      '--window-size=1440,900',
      URL_,
    ],
    { stdio: 'ignore' },
  );

  try {
    const page = await cdpTarget();
    const ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve, { once: true });
      ws.addEventListener('error', reject, { once: true });
    });

    let seq = 0;
    const pending = new Map();
    ws.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && pending.has(msg.id)) {
        pending.get(msg.id)(msg);
        pending.delete(msg.id);
      }
    });
    const send = (method, params = {}) =>
      new Promise((resolve) => {
        const id = ++seq;
        pending.set(id, resolve);
        ws.send(JSON.stringify({ id, method, params }));
      });

    await send('Runtime.enable');
    //: 页面自己报的错必须被看见 —— 否则"界面画不出来"会变成我们猜。
    const events = [];
    ws.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data);
      if (msg.method === 'Runtime.exceptionThrown') {
        const d = msg.params.exceptionDetails;
        events.push('异常: ' + (d.exception?.description || d.text));
      }
      if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
        events.push('console: ' + msg.params.args.map((a) => a.value ?? a.description).join(' '));
      }
      if (msg.method === 'Network.loadingFailed') {
        events.push('请求失败: ' + msg.params.errorText + ' ' + (msg.params.blockedReason || ''));
      }
    });
    await send('Network.enable');
    // 等 `boot()` 里那次 `loadModels()` 落定：它失败时会把状态清空、并发一个 toast。
    await delay(4000);

    const evaluate = async (expression) => {
      const res = await send('Runtime.evaluate', {
        expression,
        awaitPromise: true,
        returnByValue: true,
      });
      if (res.result?.exceptionDetails) {
        throw new Error(JSON.stringify(res.result.exceptionDetails.exception?.description));
      }
      return res.result?.result?.value;
    };

    // `app.js` 是 ES module，`state` 不在 window 上 —— 所以只能读 DOM。
    // 这也正好是判据要的那一层：用户看到的是什么。
    const stateText = await evaluate(`JSON.stringify({
      rows: document.querySelectorAll('#profile-list .profile').length,
      empty: !!document.querySelector('#profile-list .profile-empty'),
      rowText: (document.querySelector('#profile-list') || {}).textContent?.trim().slice(0, 80) || '',
      chip: (document.querySelector('#model-chip-name') || {}).textContent || '',
      railName: (document.querySelector('#rail-model-name') || {}).textContent || '',
      cardTitle: (document.querySelector('#active-model-card h3') || {}).textContent || '',
      cardModel: (document.querySelector('#active-model-card .am-model') || {}).textContent || '',
      activeBadge: [...document.querySelectorAll('#profile-list .tag.accent')].length,
      //: loadModels() 失败时会把原因交给 toast —— 读它，别猜。
      toast: (document.querySelector('.toast, #toast, .toast-stack') || {}).textContent || '',
      bodyHead: document.body.innerText.slice(0, 120),
      hasCard: !!document.querySelector('#active-model-card'),
      hasList: !!document.querySelector('#profile-list'),
      cardHtml: (document.querySelector('#active-model-card') || {}).innerHTML?.slice(0, 100) || '',
    })`);

    const api = await (await fetch('http://127.0.0.1:8125/api/llm/config')).json();
    const apiProfiles = await (await fetch('http://127.0.0.1:8125/api/llm/profiles')).json();
    const view = JSON.parse(stateText);

    console.log('页面：', view);
    if (events.length) {
      console.log('页面报错：');
      for (const line of events.slice(0, 6)) console.log('  ! ' + line.slice(0, 300));
    }
    console.log('接口：', {
      source: api.source,
      active: (api.profile || {}).label || null,
      profiles: (apiProfiles.items || []).length,
    });

    const apiCount = (apiProfiles.items || []).length;
    const apiActive = (api.profile || {}).label || null;
    const apiModel = (api.profile || {}).model || null;
    const problems = [];
    if (view.rows !== apiCount) {
      problems.push(`档案行数不一致：界面 ${view.rows} 行 ≠ 接口 ${apiCount} 份`);
    }
    if (apiCount === 0 && !view.empty) {
      problems.push('接口说一份档案都没有，界面却没给出「还没有档案」的空态');
    }
    if (apiModel && !view.rowText.includes(apiModel.split(':')[0])) {
      problems.push(`列表里没出现 ${apiModel}：${view.rowText}`);
    }
    if (apiActive && view.cardTitle !== apiActive) {
      problems.push(`「当前印鉴」写的不是生效中的那份：界面 ${view.cardTitle} ≠ 接口 ${apiActive}`);
    }
    if (apiModel && view.chip !== apiActive && !view.chip.includes(apiModel.split(':')[0])) {
      problems.push(`顶栏模型名没跟上：${view.chip}（接口是 ${apiModel}）`);
    }
    if (view.activeBadge !== (apiActive ? 1 : 0)) {
      problems.push(`「当前」标记数量不对：界面 ${view.activeBadge} ≠ ${apiActive ? 1 : 0}`);
    }
    // 界面自己报了错，却还"看起来正常" —— 这种更要抓出来。
    if (view.chip === '未配置模型' && apiCount > 0) {
      problems.push(`界面说没有模型，但接口有 ${apiCount} 份：${view.toast || '(没有 toast)'}`);
    }

    if (problems.length) {
      console.log('\n✗ 不一致：');
      for (const problem of problems) console.log('  - ' + problem);
      process.exitCode = 1;
    } else {
      console.log('\n✓ 模型页画的就是接口里的东西');
    }
    ws.close();
  } finally {
    child.kill();
  }
}

main().catch((err) => {
  console.error('✗ ' + err.message);
  process.exitCode = 1;
});

