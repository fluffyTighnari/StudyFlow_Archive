/**
 * 自评两档的**真浏览器验收**：开一个真的 Edge（CDP），
 * 1) 点「简短版」→ 卷面首条应是「1 / 90」；
 * 2) 关闭后点「全面版」→ 首条应是「1 / 127」；
 * 3) 在没有断开的情况下，用键盘把 90 条答完 → 附加 → 提交，
 *    结果页必须列出 11 个变量与各自的 facet。
 *
 * 全程截图。用法（后端已在 8125 起着，且 --dist 指向 frontend/redesign）：
 *   node frontend/redesign/tools/check_self_assessment.mjs [base] [outprefix]
 */

import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

// 产物路径按脚本位置解析，不依赖从哪里启动 —— 否则 `node ...` 换个工作目录
// 就会把截图写到别处（或因为父目录不存在而报错）。
const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = process.argv[2] || 'http://127.0.0.1:8125';
const PREFIX = process.argv[3]
  ? resolve(process.argv[3])
  : resolve(HERE, '_artifacts', 'sa-check');
const PORT = 9392;
const sleep = (ms) => delay(ms);

const EDGE_CANDIDATES = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
];

async function findEdge() {
  const found = EDGE_CANDIDATES.find((p) => existsSync(p));
  if (!found) throw new Error('找不到 msedge.exe');
  return found;
}

async function waitForTarget() {
  const started = Date.now();
  for (;;) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const targets = await res.json();
      const page = targets.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
      if (page) return page;
    } catch {
      /* 还没起来 */
    }
    if (Date.now() - started > 25_000) throw new Error('浏览器没有开出可调试的页面');
    await sleep(300);
  }
}

function connect(url) {
  const socket = new WebSocket(url);
  let nextId = 1;
  const pending = new Map();
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(JSON.stringify(message.error)));
      else resolve(message.result);
    }
  });
  const ready = new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', (err) => reject(new Error('WS 连接失败: ' + err.message)), { once: true });
  });
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = nextId++;
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
  return { ready, send, close: () => socket.close() };
}

async function evaluate(send, expression) {
  const result = await send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: false,
  });
  if (result && result.exceptionDetails) {
    throw new Error('页面内报错：' + JSON.stringify(result.exceptionDetails));
  }
  return result && result.result ? result.result.value : undefined;
}

async function poll(send, expression, predicate, { timeout = 20_000, label = '' } = {}) {
  const started = Date.now();
  for (;;) {
    const value = await evaluate(send, expression);
    if (predicate(value)) return value;
    if (Date.now() - started > timeout) {
      throw new Error(`等待超时${label ? '：' + label : ''}（最后读到 ${JSON.stringify(value)}）`);
    }
    await sleep(120);
  }
}

async function shot(send, name) {
  const { data } = await send('Page.captureScreenshot', { format: 'png' });
  const file = `${PREFIX}-${name}.png`;
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, Buffer.from(data, 'base64'));
  return file;
}

const edge = await findEdge();
const profile = new URL('./_edgeprofile_sa', import.meta.url).pathname.replace(/^\//, '');
mkdirSync(dirname(PREFIX), { recursive: true });
const child = spawn(
  edge,
  [
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--remote-debugging-address=127.0.0.1',
    '--remote-allow-origins=*',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    `${BASE}/`,
  ],
  { stdio: 'ignore', detached: false },
);

const report = { base: BASE, checks: [], screenshots: [] };
let exitCode = 0;
let send = null;
let close = null;

function check(name, ok, detail) {
  report.checks.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ' — ' + detail : ''}`);
  if (!ok) exitCode = 1;
}

try {
  const target = await waitForTarget();
  const conn = connect(target.webSocketDebuggerUrl);
  send = conn.send;
  close = conn.close;
  await conn.ready;
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1280, height: 900, deviceScaleFactor: 1, mobile: false,
  });

  // 1. 导航到「认识自己」页
  await poll(send, "document.readyState === 'complete'", Boolean, { label: '页面加载' });
  // 模块脚本渲染导航之后才可点；`complete` 不保证 DOM 已铺好 —— 所以先等元素出现。
  await poll(
    send,
    "!!document.querySelector('[data-view=\"benchmark\"]')",
    Boolean,
    { label: '侧栏出现自评入口' },
  );
  // 模块脚本挂 click 监听也晚于 DOM 出现，第一次点击可能落空 —— 按"是否真的切过去了"重试。
  const navOk = await (async () => {
    const started = Date.now();
    for (;;) {
      await evaluate(
        send,
        "document.querySelector('[data-view=\"benchmark\"]')?.click()",
      );
      const active = await evaluate(
        send,
        "(() => { const v = document.getElementById('view-benchmark'); return v ? v.classList.contains('is-active') : false; })()",
      );
      if (active) return true;
      if (Date.now() - started > 8_000) return false;
      await sleep(300);
    }
  })();
  check('切到自评页', navOk === true);
  report.screenshots.push(await shot(send, 'entry'));

  // 2. 简短版
  await poll(send, "!!document.getElementById('sa-start')", Boolean, { label: '自评入口按钮' });
  await evaluate(send, "document.getElementById('sa-start').click()");
  const shortCounter = await poll(
    send,
    "(() => { const el = document.querySelector('[data-sa=\"counter\"]'); return el ? el.textContent.trim() : null; })()",
    (v) => v && v.includes('/'),
    { label: '简短版卷面' },
  );
  const shortTotal = await evaluate(
    send,
    "document.querySelectorAll('.sa-option').length",
  );
  const shortText = await evaluate(send, "document.querySelector('.sa-text')?.textContent || ''");
  check('简短版首条计数为 1 / 90', shortCounter === '1 / 90', shortCounter);
  check('简短版每条 4 个选项', shortTotal === 4, `options=${shortTotal}`);
  report.screenshots.push(await shot(send, 'short-1'));

  // 关闭（草稿保留）
  await evaluate(send, "document.querySelector('[data-sa=\"close\"]').click()");
  await poll(
    send,
    "document.getElementById('self-assessment')?.hidden",
    Boolean,
    { label: '关闭自评' },
  );

  // 3. 全面版
  await poll(send, "!!document.getElementById('sa-start-full')", Boolean, { label: '全面版入口' });
  await evaluate(send, "document.getElementById('sa-start-full').click()");
  const fullCounter = await poll(
    send,
    "(() => { const el = document.querySelector('[data-sa=\"counter\"]'); return el ? el.textContent.trim() : null; })()",
    (v) => v && v.includes('/'),
    { label: '全面版卷面' },
  );
  check('全面版首条计数为 1 / 127', fullCounter === '1 / 127', fullCounter);
  report.screenshots.push(await shot(send, 'full-1'));

  // 关掉全面版，回到简短版做完整提交（90 条）
  await evaluate(send, "document.querySelector('[data-sa=\"close\"]').click()");
  await poll(send, "document.getElementById('self-assessment')?.hidden", Boolean, { label: '关闭全面版' });
  await evaluate(send, "document.getElementById('sa-start').click()");
  await poll(
    send,
    "(() => { const el = document.querySelector('[data-sa=\"counter\"]'); return el ? el.textContent.trim() : null; })()",
    (v) => v === '1 / 90',
    { label: '重开简短版（应回到第 1 条）' },
  );

  // 4. 用键盘答完 90 条：A/B/C/D 轮转，避免直线作答
  for (let i = 0; i < 90; i += 1) {
    const key = 'ABCD'[i % 4];
    await evaluate(
      send,
      `document.dispatchEvent(new KeyboardEvent('keydown', { key: '${key}', bubbles: true }))`,
    );
    await sleep(210);
  }
  const extraSeen = await poll(
    send,
    "(() => { const el = document.querySelector('.sa-extra'); return !!el; })()",
    Boolean,
    { label: '答完 90 条后进入附加题', timeout: 10_000 },
  );
  check('答完 90 条后进入附加题', extraSeen === true);
  report.screenshots.push(await shot(send, 'extra'));

  // 5. 提交
  await evaluate(send, "document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))");
  await poll(
    send,
    "!!document.querySelector('.sa-result')",
    Boolean,
    { label: '结果页', timeout: 30_000 },
  );
  const result = await evaluate(
    send,
    `(() => ({
      rows: Array.from(document.querySelectorAll('.sa-readings > li')).length,
      facetGroups: Array.from(document.querySelectorAll('.sa-facets')).length,
      firstRow: document.querySelector('.sa-readings > li')?.innerText || '',
      note: document.querySelector('.sa-result .sa-note')?.innerText || '',
      runId: document.querySelector('.sa-result .sa-no')?.textContent || '',
    }))()`,
  );
  check('结果页列出 11 个变量', result.rows === 11, `rows=${result.rows}`);
  check('每个变量都带 facet 明细', result.facetGroups === 11, `facetGroups=${result.facetGroups}`);
  check('结果页标明 run_id', /SA-/.test(result.runId), result.runId.trim());
  report.result = result;
  report.screenshots.push(await shot(send, 'result'));

  // 6. 后端确实记下了 tier
  const latest = await (await fetch(`${BASE}/api/self-assessment/latest`)).json();
  check('latest.tier === short', latest.tier === 'short', String(latest.tier));
  check('latest.scale_version === v2', latest.scale_version === 'v2', String(latest.scale_version));
  report.latest = {
    run_id: latest.run_id,
    tier: latest.tier,
    scale_version: latest.scale_version,
    readings: (latest.readings || []).length,
    flags: latest.flags,
  };
} catch (err) {
  check('驱动过程无异常', false, err && err.message ? err.message : String(err));
} finally {
  if (close) close();
  child.kill();
}

mkdirSync(dirname(`${PREFIX}-report.json`), { recursive: true });
writeFileSync(`${PREFIX}-report.json`, JSON.stringify(report, null, 2), 'utf8');
console.log(`\n报告：${PREFIX}-report.json`);
console.log(`截图：${report.screenshots.join(', ')}`);
process.exit(exitCode);
