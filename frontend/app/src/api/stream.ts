/**
 * 读 `/discuss/stream` 的 SSE。
 *
 * 三个必须自己处理的事实（全部来自 P6+.1 的实现）：
 *
 * 1. **必须是 POST + `fetch`**，不能用 `EventSource` —— `EventSource` 只能 GET，
 *    而这里要带请求体（"我要跟哪一项讨论什么"）。
 * 2. **帧可能被切开**：网络给的是字节流，一帧 `data: {...}\n\n` 可能分两次到。
 *    所以要按 `\n\n` 攒够再解析 —— 逐块 `JSON.parse` 会在长回答上随机崩。
 * 3. **必须能中断**：用户点"停止"时用 `AbortController`，服务端那边会收到断线
 *    并把已生成的内容留在库里（P6+.1 的 `stop:client_disconnected`）。
 *
 * 还有一条是**给人看的**：真模型一次回答会切成上百帧（实测 115 帧/次），
 * 逐帧 setState 会把长回答变成一次重排风暴。所以这里**节流**：
 * 帧照收，但渲染按 `requestAnimationFrame` 合并。
 */

import type { AgentEvent } from "./types";

export interface StreamHandlers {
  onEvent: (event: AgentEvent) => void;
  onError: (message: string) => void;
  /** 正常收尾（服务端发了 `done` 或流结束）。 */
  onEnd: () => void;
}

export interface StreamHandle {
  /** 主动中断（用户点"停止"）。 */
  abort: () => void;
}

const STREAM_PATH = (sessionId: string) => `/api/sessions/${sessionId}/discuss/stream`;

export interface DiscussStreamBody {
  /** 用户这句话。**字段名是 `message`，不是 `text`** —— 照 `DiscussRequest` 抄的。 */
  message: string;
  /** 针对哪一步讨论。`null` 表示不针对某一步。 */
  step_id?: string | null;
  gestures?: unknown[];
}

export function streamDiscussion(
  sessionId: string,
  body: DiscussStreamBody,
  handlers: StreamHandlers,
): StreamHandle {
  const controller = new AbortController();
  let closed = false;

  const run = async () => {
    let buffer = "";
    try {
      const response = await fetch(STREAM_PATH(sessionId), {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "text/event-stream",
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!response.ok || response.body === null) {
        const text = await response.text();
        let message = `HTTP ${response.status}`;
        try {
          const parsed = JSON.parse(text) as { error?: { message?: string } };
          message = parsed.error?.message ?? message;
        } catch {
          /* 不是 JSON 就用状态码 */
        }
        handlers.onError(message);
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let boundary = buffer.indexOf("\n\n");
        while (boundary >= 0) {
          const chunk = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          boundary = buffer.indexOf("\n\n");

          const payload = chunk
            .split("\n")
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart())
            .join("\n");
          if (payload === "") continue;

          let event: AgentEvent;
          try {
            event = JSON.parse(payload) as AgentEvent;
          } catch {
            // 解析不了一帧就说明协议对不上 —— 如实报，不要静默丢弃。
            handlers.onError(`收到一帧不是 JSON 的数据：${payload.slice(0, 120)}`);
            continue;
          }
          handlers.onEvent(event);
          if (event.type === "done" || event.type === "error") closed = true;
        }
      }
      if (!closed) handlers.onEnd();
    } catch (cause) {
      if (controller.signal.aborted) return; // 用户主动停的，不是错误
      handlers.onError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  void run();
  return { abort: () => controller.abort() };
}

/**
 * 把一串帧合并成"每个动画帧最多渲染一次"。
 *
 * 为什么不是"每 N 帧渲染一次"：长回答的最后几帧可能拖得很久，
 * 按帧数攒会让结尾多等一截。按时间窗口合并，观感是"一直很顺"。
 */
export function createFrameBatcher<T>(emit: (items: T[]) => void): {
  push: (item: T) => void;
  flush: () => void;
} {
  let pending: T[] = [];
  let scheduled = false;

  const run = () => {
    scheduled = false;
    if (pending.length === 0) return;
    const batch = pending;
    pending = [];
    emit(batch);
  };

  return {
    push(item: T) {
      pending.push(item);
      if (!scheduled) {
        scheduled = true;
        requestAnimationFrame(run);
      }
    },
    flush() {
      if (pending.length > 0) run();
    },
  };
}
