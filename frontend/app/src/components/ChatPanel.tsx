import { useCallback, useEffect, useRef, useState } from "react";

import { describeError } from "../api/client";
import { streamDiscussion } from "../api/stream";
import type { AgentEvent, ChatMessageRead, StepRead, ToolTraceEntry } from "../api/types";

/**
 * 讨论区。这是整个界面的操作面 —— 计划不是"生成出来给人看"的，
 * 而是"一边聊一边改"的。
 *
 * 五条设计约束（前四条来自 P6+.1 的红线，第五条来自实测）：
 *
 * 1. **思考过程与回答必须分开**。后端把 `thinking` 与 `token` 分成两种事件；
 *    这里也分成两块，思考那块默认收起并标灰。混在一起时，用户会把
 *    "模型在想什么"读成"模型回答了什么"—— 而后者才是要落进计划的东西。
 * 2. **不许把半句话当回答**。断线/出错时已生成的半句仍然显示，但用
 *    "这次没说完"的样式并明确说明 —— 而不是让它看起来像一条完整的回答。
 * 3. **`done` 才是收尾**。收到 `done` 才把消息并入历史、才刷新计划。
 *    提前并入会让"刷新后消息变样"。（历史最终以后端为准。）
 * 4. **工具轨迹要看得见**：`rejected` 与 `ok` 用不同样式，因为
 *    "它想改、被拦下了"和"它改成了"是两件完全不同的事。
 * 5. **帧要节流**：真模型一次回答 100+ 帧，逐帧 setState 会把长回答卡住。
 */

interface LiveState {
  text: string;
  thinking: string;
  stage: string | null;
  traces: ToolTraceEntry[];
  error: string | null;
  /** 收到过 done → 这是一次正常收尾；用来区分"没说完"。 */
  finished: boolean;
}

const EMPTY_LIVE: LiveState = {
  text: "",
  thinking: "",
  stage: null,
  traces: [],
  error: null,
  finished: false,
};

const STAGE_TEXT: Record<string, string> = {
  read_context: "正在读取上下文",
  wait_model: "正在思考",
  run_tools: "正在执行",
};

export function ChatPanel({
  sessionId,
  messages,
  focusStep,
  disabled,
  onFinished,
  onClearFocus,
}: {
  sessionId: string;
  messages: ChatMessageRead[];
  focusStep: StepRead | null;
  disabled: boolean;
  onFinished: () => void;
  onClearFocus: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [live, setLive] = useState<LiveState>(EMPTY_LIVE);
  const [sending, setSending] = useState(false);
  const abortRef = useRef<(() => void) | null>(null);
  const logRef = useRef<HTMLDivElement | null>(null);

  // 累积文本用 ref，渲染走 rAF —— 一帧最多一次 setState。
  const textRef = useRef("");
  const thinkingRef = useRef("");
  const flushScheduled = useRef(false);

  const scheduleFlush = useCallback(() => {
    if (flushScheduled.current) return;
    flushScheduled.current = true;
    requestAnimationFrame(() => {
      flushScheduled.current = false;
      setLive((current) => ({
        ...current,
        text: textRef.current,
        thinking: thinkingRef.current,
      }));
    });
  }, []);

  // 新消息进来时滚到底（只在用户本来就贴着底部时才这么做，
  // 否则会把人正在读的地方拽走）。
  useEffect(() => {
    const element = logRef.current;
    if (element === null) return;
    const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 120;
    if (nearBottom) element.scrollTop = element.scrollHeight;
  }, [messages.length, live.text]);

  useEffect(() => {
    return () => abortRef.current?.();
  }, []);

  const handleEvent = useCallback(
    (event: AgentEvent) => {
      const data = event.data;
      switch (event.type) {
        case "stage": {
          const name = String(data["name"] ?? "");
          setLive((current) => ({ ...current, stage: STAGE_TEXT[name] ?? name }));
          break;
        }
        case "thinking": {
          thinkingRef.current += String(data["text"] ?? "");
          scheduleFlush();
          break;
        }
        case "token": {
          textRef.current += String(data["text"] ?? "");
          scheduleFlush();
          break;
        }
        case "tool_call": {
          setLive((current) => ({
            ...current,
            traces: [
              ...current.traces,
              { tool: String(data["tool"] ?? "?"), status: "calling", detail: null },
            ],
          }));
          break;
        }
        case "tool_result": {
          const tool = String(data["tool"] ?? "?");
          const status = String(data["status"] ?? "?");
          const message = data["message"] === null ? null : String(data["message"] ?? "");
          setLive((current) => ({
            ...current,
            traces: [...current.traces, { tool, status, detail: message }],
          }));
          break;
        }
        case "done": {
          textRef.current = String(data["reply"] ?? textRef.current);
          setLive((current) => ({
            ...current,
            text: textRef.current,
            thinking: thinkingRef.current,
            stage: null,
            finished: true,
          }));
          break;
        }
        case "error": {
          setLive((current) => ({
            ...current,
            stage: null,
            error: String(data["error"] ?? "未知错误"),
          }));
          break;
        }
        default:
          break;
      }
    },
    [scheduleFlush],
  );

  const send = useCallback(() => {
    const text = draft.trim();
    if (text === "" || sending) return;

    textRef.current = "";
    thinkingRef.current = "";
    setLive(EMPTY_LIVE);
    setSending(true);
    setDraft("");

    const handle = streamDiscussion(
      sessionId,
      { message: text, step_id: focusStep?.step_id ?? null },
      {
        onEvent: handleEvent,
        onError: (message) => {
          setLive((current) => ({ ...current, stage: null, error: message }));
        },
        onEnd: () => {
          setSending(false);
          abortRef.current = null;
          onFinished();
        },
      },
    );
    abortRef.current = handle.abort;
  }, [draft, sending, sessionId, focusStep, handleEvent, onFinished]);

  const streaming = sending || live.text !== "" || live.thinking !== "";

  return (
    <section className="panel tk-surface chat" aria-label="讨论区">
      <header className="panel__head">
        <span className="panel__title">讨论</span>
        {live.stage !== null && <span className="stage-note">{live.stage}</span>}
      </header>

      <div className="chat__log" ref={logRef}>
        {messages.length === 0 && live.text === "" && (
          <p className="empty-note">
            还没有对话。计划是从对话里长出来的 —— 说清楚你打算怎么学，
            它会给出第一版；之后点计划里的任意一项，我们就可以一起改它。
          </p>
        )}

        {messages.map((message) => (
          <article
            key={message.msg_id}
            className={message.role === "user" ? "bubble bubble--user" : "bubble bubble--assistant"}
          >
            <div className="bubble__body">{message.content}</div>
            {message.tool_trace !== null && message.tool_trace.length > 0 && (
              <TraceList entries={message.tool_trace} />
            )}
            {message.tags !== null && message.tags.length > 0 && (
              <div className="bubble__meta">{message.tags.join(" · ")}</div>
            )}
          </article>
        ))}

        {(streaming || live.error !== null) && (
          <article
            className={
              live.finished ? "bubble bubble--assistant" : "bubble bubble--assistant bubble--open"
            }
          >
            {live.thinking !== "" && (
              <details>
                <summary className="thinking__label">思考过程（不是回答）</summary>
                <div className="thinking">{live.thinking}</div>
              </details>
            )}
            {live.text !== "" && <div className="bubble__body">{live.text}</div>}
            {live.traces.length > 0 && <TraceList entries={live.traces} />}
            {live.error !== null && (
              <p className="error-note">
                这次没能说完：{live.error}
                <br />
                上面若是半句话，它不是回答。
              </p>
            )}
            {!live.finished && live.error === null && sending && (
              <div className="bubble__meta">正在生成…</div>
            )}
          </article>
        )}
      </div>

      <div className="composer">
        {focusStep !== null && (
          <div className="composer__focus">
            <span>
              正在针对第 {focusStep.seq} 步讨论：{focusStep.action}
            </span>
            <button type="button" className="button button--quiet tk-focusable" onClick={onClearFocus}>
              取消针对
            </button>
          </div>
        )}
        <div className="composer__row">
          <textarea
            className="field-input tk-focusable"
            rows={3}
            value={draft}
            placeholder={
              focusStep === null
                ? "说说你打算怎么学，或者问它为什么这么安排…"
                : "这一项想怎么改？例如：太长了，拆成两天"
            }
            disabled={disabled || sending}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              // 回车发送、Shift+回车换行 —— 但用输入法拼字时的回车不算（那是选词）。
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                send();
              }
            }}
          />
          <div className="composer__row">
            {sending ? (
              <button
                type="button"
                className="button button--danger tk-focusable"
                onClick={() => abortRef.current?.()}
              >
                停止
              </button>
            ) : (
              <button
                type="button"
                className="button tk-focusable"
                disabled={disabled || draft.trim() === ""}
                onClick={send}
              >
                发送
              </button>
            )}
          </div>
        </div>
        {disabled && <p className="panel__hint">这个会话当前状态不能讨论（先让它进入进行中）。</p>}
      </div>
    </section>
  );
}

function TraceList({ entries }: { entries: ToolTraceEntry[] }) {
  return (
    <div className="trace">
      {entries.map((entry, index) => (
        <div className="trace__row" key={`${entry.tool}-${index}`}>
          <span className="trace__tool">{entry.tool}</span>
          <span className={entry.status === "ok" ? "trace__ok" : "trace__rejected"}>
            {entry.status === "ok" ? "已执行" : entry.status}
          </span>
          {entry.detail !== null && <span>{entry.detail}</span>}
        </div>
      ))}
    </div>
  );
}

/** 让"发送失败"这类错误也走同一句话术，避免两个地方两种说法。 */
export function toDisplayError(cause: unknown): string {
  return describeError(cause);
}
