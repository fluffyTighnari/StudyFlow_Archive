"""会话页第二阶段：用量聚合、紧凑显示、待回答问题的提醒、把新 props 传下去。

## 待回答的问题从哪来

它**不在** `/state` 快照里，是一个独立端点（`GET /{id}/clarifications`）。
所以这里不自己猜字段，而是由 App 查好、以 `pendingQuestion` 传进来 ——
一个组件的输入应该来自它的 props，而不是"它顺手又发了一次请求"。
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(
    r"D:\Workspace\Code\phy\personal_learning_flow\frontend\app\src\components\ConversationView.tsx"
)
text = PATH.read_text(encoding="utf-8")


def swap(old: str, new: str, *, label: str, count: int = 1) -> None:
    global text
    found = text.count(old)
    assert found == count, f"{label}：锚点出现 {found} 次（期望 {count}）"
    text = text.replace(old, new, count)
    print(f"✅ {label}")


# ------------------------------------------------------------ 用量聚合 ==
swap(
    """  let completionTokens: number | null = null;
  for (const message of state.chat) {
    if (message.completion_tokens !== null && message.completion_tokens !== undefined) {
      completionTokens = (completionTokens ?? 0) + message.completion_tokens;
    }
  }
""",
    """  let completionTokens: number | null = null;
  let promptTotal = 0;
  let hitTotal: number | null = null;
  let missTotal: number | null = null;
  let outputTotal = 0;
  let measuredAny = false;
  for (const message of state.chat) {
    if (message.completion_tokens !== null && message.completion_tokens !== undefined) {
      completionTokens = (completionTokens ?? 0) + message.completion_tokens;
    }
    if (message.prompt_tokens !== null && message.prompt_tokens !== undefined) {
      promptTotal += message.prompt_tokens;
      outputTotal += message.completion_tokens ?? 0;
      measuredAny = true;
    }
    if (message.cache_hit_tokens !== null && message.cache_hit_tokens !== undefined) {
      hitTotal = (hitTotal ?? 0) + message.cache_hit_tokens;
    }
    if (message.cache_miss_tokens !== null && message.cache_miss_tokens !== undefined) {
      missTotal = (missTotal ?? 0) + message.cache_miss_tokens;
    }
  }
  /*
   * 未命中的算法：提供方给了 `cache_miss` 就用它，否则用 `prompt − hit` 反推。
   * 两条路都不通时是 `null`（没测到），**不是 0** ——
   * 0 的意思是"测过了，一次都没命中"。
   */
  const uncachedInput =
    missTotal !== null ? missTotal : hitTotal !== null ? promptTotal - hitTotal : null;
  const hitRate = hitTotal !== null && promptTotal > 0 ? hitTotal / promptTotal : null;
  const tokenUsage: TokenUsage | null = measuredAny
    ? {
        total: promptTotal + outputTotal,
        uncachedInput: uncachedInput ?? 0,
        cachedInput: hitTotal ?? 0,
        output: outputTotal,
        hitRate,
      }
    : null;
""",
    label="用量聚合",
)

swap(
    """import { Composer } from "./Composer";""",
    """import { Composer, type TokenUsage } from "./Composer";""",
    label="导入 TokenUsage 类型",
)

# --------------------------------------------------- 待回答问题的提醒 ==
swap(
    """  const placeholder =
    focusStep === null
      ? "说说你打算怎么学，或者问它为什么这么安排…"
      : `第 ${focusStep.seq} 步想怎么改？例如：太长了，拆成两天`;
""",
    """  /*
   * 有待回答的问题时提醒一次（同一个问题只报一次）。
   *
   * 提醒走与"这一轮说完了"同一条路：页面不在前台才发（设置里可关）。
   * 用 ref 记住已经报过的 id —— 每次快照重读都会重跑这个 effect，
   * 不记的话同一条问题会被反复提醒，而反复提醒的后果是用户把通知关掉。
   */
  const notifiedQuestionRef = useRef<string | null>(null);
  useEffect(() => {
    if (!settings.notifyQuestion) return;
    if (pendingQuestion === null) return;
    if (notifiedQuestionRef.current === pendingQuestion.clarification_id) return;
    notifiedQuestionRef.current = pendingQuestion.clarification_id;
    notify("有一句话需要你回答", pendingQuestion.question, "unfocused");
  }, [pendingQuestion, settings.notifyQuestion]);

  const placeholder =
    focusStep === null
      ? "说说你打算怎么学，或者问它为什么这么安排…"
      : `第 ${focusStep.seq} 步想怎么改？例如：太长了，拆成两天`;
""",
    label="待回答问题的提醒",
)

swap(
    """  /** 把"正在生成的是哪个会话"报上去 —— 侧栏要据此显示动态点。 */
  onStreamingChange: (sessionId: string | null) => void;
}) {""",
    """  /** 把"正在生成的是哪个会话"报上去 —— 侧栏要据此显示动态点。 */
  onStreamingChange: (sessionId: string | null) => void;
  /** 这个会话里"还没回答"的那条问题（`null` = 没有）。用来发提醒。 */
  pendingQuestion: { clarification_id: string; question: string } | null;
}) {""",
    label="pendingQuestion prop 类型",
)

swap(
    """  settings,
  onStreamingChange,
}: {""",
    """  settings,
  onStreamingChange,
  pendingQuestion,
}: {""",
    label="pendingQuestion 解构",
)

# ------------------------------------------------ 紧凑 + 排队提示 + props ==
swap(
    """            <MessageList
              messages={state.chat}""",
    """            <MessageList
              compact={settings.compact}
              messages={state.chat}""",
    label="紧凑显示传给 MessageList",
)

swap(
    """          contextTokens={contextTokens}
          completionTokens={completionTokens}""",
    """          contextTokens={contextTokens}
          completionTokens={completionTokens}
          tokenUsage={tokenUsage}""",
    label="用量传给 Composer",
)

# 排队中的那句话要看得见（否则用户以为它被吞了）
swap(
    """          {live.error !== null && (""",
    """          {queued !== null && sending && (
            <p className="dsh-stageNote" style={{ textAlign: "center" }}>
              已排队，等这一轮说完就发：「{queued.text.slice(0, 40)}
              {queued.text.length > 40 ? "…" : ""}」
            </p>
          )}

          {live.error !== null && (""",
    label="排队提示",
)

PATH.write_text(text, encoding="utf-8")
print(f"\n第三阶段 B 写完（{len(text.splitlines())} 行）")
