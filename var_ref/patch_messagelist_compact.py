"""`compact`：已完成轮次里的过程内容（思考、工具轨迹）默认折叠。

## 语义

「已完成轮次」= 已经不在生成中的那些助手回复。设置里选「紧凑」时，
它们的思考与工具轨迹不是消失，而是**折起来** —— 用户点一下还能看见。
消失与折叠是两件事：消失会让人以为"它没思考过 / 没查过东西"。

## 为什么工具轨迹要用 `<details>` 包一层，而不是给它加个参数

`ToolTrace` 本身画的是**表格**（一行一次调用），它没有"开合"这个概念 ——
给它加一个 `defaultOpen` 参数，它会把这个参数一路传到内部的结构性节点上，
而那些节点不该知道"外面想让不想要我"。开合是**外面这一层**的决定，
所以 `<details>` 包在这里。
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(
    r"D:\Workspace\Code\phy\personal_learning_flow\frontend\app\src\components\MessageList.tsx"
)
text = PATH.read_text(encoding="utf-8")


def swap(old: str, new: str, *, label: str) -> None:
    global text
    found = text.count(old)
    assert found == 1, f"{label}：锚点出现 {found} 次"
    text = text.replace(old, new, 1)
    print(f"✅ {label}")


swap(
    """  stepSeqOf,
}: {
  message: ChatMessageRead;
  stepSeqOf: (stepId: string) => number | null;
}) {""",
    """  stepSeqOf,
  compact,
}: {
  message: ChatMessageRead;
  stepSeqOf: (stepId: string) => number | null;
  /** 紧凑：这条已完成轮次的过程内容默认折起来（用户点开仍能看到）。 */
  compact: boolean;
}) {""",
    label="AssistantMessage 接受 compact",
)

swap(
    """      {message.thinking !== null && message.thinking !== "" && (
        <details className="dsh-thinking">""",
    """      {message.thinking !== null && message.thinking !== "" && (
        <details className="dsh-thinking" open={!compact}>""",
    label="思考过程默认开合",
)

swap(
    """      {message.tool_trace !== null && message.tool_trace.length > 0 && (
        <ToolTrace entries={message.tool_trace} />
      )}""",
    """      {/*
        紧凑模式下把整张轨迹折起来：标题里写清"几次调用"，
        让用户不必展开也知道发生过什么。
      */}
      {message.tool_trace !== null && message.tool_trace.length > 0 && (
        compact ? (
          <details className="dsh-traceWrap">
            <summary className="dsh-traceSummary">
              工具调用 {message.tool_trace.length} 次
            </summary>
            <ToolTrace entries={message.tool_trace} />
          </details>
        ) : (
          <ToolTrace entries={message.tool_trace} />
        )
      )}""",
    label="工具轨迹：紧凑时折起来",
)

swap(
    """export function MessageList({
  messages,
  streamingText,
  streamingThinking,
  emptyHint,
  stepSeqOf,
}: {""",
    """export function MessageList({
  messages,
  streamingText,
  streamingThinking,
  emptyHint,
  stepSeqOf,
  compact,
}: {""",
    label="MessageList 接受 compact",
)

swap(
    """  /** 把 `step_id` 换成给人看的序号；找不到时返回 `null`。 */
  stepSeqOf: (stepId: string) => number | null;
}) {""",
    """  /** 把 `step_id` 换成给人看的序号；找不到时返回 `null`。 */
  stepSeqOf: (stepId: string) => number | null;
  /** 紧凑显示：已完成轮次的思考与工具轨迹折起来。 */
  compact: boolean;
}) {""",
    label="MessageList compact 类型",
)

swap(
    """          <AssistantMessage
            key={message.msg_id}
            message={message}
            stepSeqOf={stepSeqOf}
          />""",
    """          <AssistantMessage
            key={msg.msg_id}
            message={message}
            stepSeqOf={stepSeqOf}
            compact={compact}
          />""".replace("msg.", "message."),
    label="转发 compact",
)

PATH.write_text(text, encoding="utf-8")
print("完成")
