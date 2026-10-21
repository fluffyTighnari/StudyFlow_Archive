/**
 * 无头浏览器的**验收驱动**：开一个真的 Edge，跑 `?probe=1` 的探针，
 * 等页面把结果写进 DOM，再读出来。
 *
 * 为什么不用 `--dump-dom --virtual-time-budget`：那个组合会把
 * `setTimeout` 当虚拟时间快进，于是"定时器轮询"和"真实网络（一条 SSE 流）"
 * 按不同时钟赛跑 —— 实测会随机地在流跑到一半时把 DOM 抓走，
 * 报出来像"功能没做完"。这里改用 CDP：**真实时间**，等到探针自己说完成。
 *
 * 用法（后端已在 8125 起着，且 `--dist` 指向 frontend/redesign）：
 *   node var_ref/headless_probe.mjs [url] [outfile]
 *
 * 退出码：0 = 探针全部通过；1 = 有步骤失败或超时（报告仍会打印）。
 */

import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';

const URL_ = process.argv[2] || 'http://127.0.0.1:8125/?probe=1';
const OUT = process.argv[3] || 'var_ref/probe_report.json';
const PORT = 9391;
const DEADLINE_MS = 120_000;

const EDGE_CANDIDATES = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
];

const sleep = (ms) => delay(ms);

async function findEdge() {
  const { existsSync } = await import('node:fs');
  const found = EDGE_CANDIDATES.find((path) => existsSync(path));
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
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: false });
  return result && result.result ? result.result.value : undefined;
}

const edge = await findEdge();
const profile = new URL('../var_ref/_edgeprofile_cdp', import.meta.url).pathname.replace(/^\//, '');
const child = spawn(
  edge,
  [
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    // 这两条缺一不可：只给 port 时，实测这个 Edge 版本不开 CDP 监听。
    '--remote-debugging-address=127.0.0.1',
    '--remote-allow-origins=*',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    URL_,
  ],
  { stdio: 'ignore', detached: false },
);
child.on('error', (err) => {
  console.error('浏览器起不来：', err.message);
  process.exit(1);
});

let failure = null;
try {
  const target = await waitForTarget();
  const { ready, send, close } = connect(target.webSocketDebuggerUrl);
  await ready;
  await send('Runtime.enable');

  const started = Date.now();
  let report = null;
  for (;;) {
    const text = await evaluate(
      send,
      "(() => { const el = document.getElementById('probe-report'); return el ? el.textContent : ''; })()",
    );
    if (text) {
      try {
        const parsed = JSON.parse(text);
        if (parsed && parsed.ok !== null && parsed.ok !== undefined) {
          report = parsed;
          break;
        }
      } catch {
        /* 还没写完整 */
      }
    }
    if (Date.now() - started > DEADLINE_MS) {
      failure = '探针在 ' + DEADLINE_MS / 1000 + ' 秒内没有给出结论';
      const partial = await evaluate(
        send,
        "(() => { const el = document.getElementById('probe-report'); return el ? el.textContent : '(没有报告)'; })()",
      );
      console.log('--- 抓到的最后一份报告 ---');
      console.log(partial);
      break;
    }
    await sleep(200);
  }
  close();
  if (report) {
    writeFileSync(OUT, JSON.stringify(report, null, 2), 'utf8');
    console.log(JSON.stringify(report, null, 2));
  }
  const code = report && report.ok ? 0 : 1;
  child.kill();
  process.exit(code);
} catch (err) {
  console.error('驱动出错：', err && err.message ? err.message : err);
  child.kill();
  process.exit(1);
} finally {
  if (failure) child.kill();
}
