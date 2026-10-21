/**
 * 一轮讨论的生命周期：把 SSE 的七种事件翻译成**界面上该发生的动作**。
 *
 * 为什么单独一个文件：会话流最容易坏的地方不是渲染，是**时序** ——
 * 谁在什么时候被叫、`done` 之后还要不要改状态、用户按了停止之后还会不会
 * 有回调跑进来。这些规则写在一处，review 的时候只看这一页就够了。
 *
 * 它**不碰 DOM**：所有动作通过 `view` 交给调用方。好处是同一个生命周期既能
 * 驱动真实界面，也能在无头脚本里用假的 `view` 跑一遍（事件顺序对不对，
 * 不需要浏览器就能验）。
 *
 * 一条硬规则：**停止要真的停**。`stop()` 直接 abort 掉 fetch，服务端会因此
 * 走 `client_disconnected` 分支，把"发生过"这件事补完 —— 而不是让界面假装
 * 停了、服务端跑到天亮。
 */

import { adaptContext, traceRow } from './adapt.js';
import { streamDiscuss } from './api.js';

/** `stage` 事件的名字 → 状态行里那个词。查不到就原样显示，不编一个更好听的。 */
const STAGE_WORD = {
  read_context: '读取会话',
  wait_model: '组织回答',
  run_tools: '调用工具',
  persist: '写入记录',
  done: '收尾',
};

export function runDiscussion({ sessionId, text, stepId = null, gestures = [], view }) {
  const box = { cancelled: false, reply: '' };

  const handle = (event) => {
    if (box.cancelled) return;
    switch (event.type) {
      case 'stage':
        view.status(stageWord(event), stageNote(event));
        break;

      case 'thinking':
        // 思考是**推理**，正文是**结论**。有的模型给，有的不给；不给就不显示。
        if (event.text) view.thinking(event.text);
        break;

      case 'token':
        box.reply += event.text || '';
        view.append(event.text || '');
        break;

      case 'tool_call': {
        const row = traceRow({ tool: event.tool, status: 'running', call_id: event.call_id });
        row.state = 'is-running';
        row.note = summarize(event.arguments);
        view.trace(row);
        break;
      }

      case 'tool_result': {
        // `status` 必须如实传到界面上：`rejected` = 模型想做但被守卫拦住了。
        // 把被拦下的写入显示成"已完成"，是这一整层最不能犯的错。
        const row = traceRow(event);
        view.traceUpdate(event.call_id, row);
        break;
      }

      case 'done':
        view.finish({
          reply: event.reply || box.reply,
          toolTrace: Array.isArray(event.tool_trace) ? event.tool_trace : [],
          thinking: event.thinking || null,
          context: adaptContext(event.context),
          stopReason: event.stop_reason || 'completed',
          steps: event.steps || 0,
        });
        break;

      case 'error':
        view.fail(event.error || '这一轮中断了');
        break;

      default:
        break;
    }
  };

  const { controller, run } = streamDiscuss(sessionId, { message: text, stepId, gestures }, handle);

  const settled = run.catch((err) => {
    if (box.cancelled || err?.name === 'AbortError') return;
    view.fail(err?.message || String(err));
  });

  return {
    /** 用户按了停止：真的掐断连接。 */
    stop() {
      box.cancelled = true;
      controller.abort();
    },
    settled,
  };
}

function stageWord(event) {
  return STAGE_WORD[event.name] || event.name;
}

function stageNote(event) {
  if (event.name === 'read_context') {
    const anchor = event.anchor ? `锚点 ${event.anchor}` : '整份会话';
    return event.plan_version ? `${anchor} · 计划第 ${event.plan_version} 版` : anchor;
  }
  if (event.name === 'wait_model') return event.round ? `第 ${event.round} 轮` : '';
  if (event.name === 'run_tools') return event.count ? `${event.count} 个工具动作` : '';
  return '';
}

/** 工具参数 → 一行小字。只写**这一次调用干了什么**，不做解释。 */
function summarize(args) {
  if (!args || typeof args !== 'object') return '';
  const change = args.suggested_change?.change || args.change || args.reason;
  if (change) return String(change).slice(0, 36);
  const keys = Object.keys(args);
  return keys.length ? keys.slice(0, 3).join(' / ') : '';
}
