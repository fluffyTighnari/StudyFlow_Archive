"""让会话页接上这一轮的四件事：用量聚合、排队/打断、生成中状态、通知。

## 四件事各自的落点

1. **用量聚合**：`tokenUsage` 由 `state.chat` 现算（每条回复的实测值相加），
   不新增端点 —— 这份数据本来就随快照一起来了。
2. **排队 / 打断**：`sending` 为真时，`submit` 不再直接 return。
   `queue` → 把这条存进 `queuedRef`，本轮 `onEnd` 后自动发出去；
   `interrupt` → 先 `abort()` 再发。**Cmd/Ctrl+Enter 走另一个行为**
   （与 DSH 一致：默认 Enter = 排队，修饰键 = 打断）。
3. **生成中状态**：`onStreamingChange(sessionId | null)` 报给 App，
   侧栏据此在那一行左边显示动态点。
4. **通知**：`onEnd` 里报"这一轮说完了"；另外当快照里出现**待回答的问题**时
   报一次（同一 `clarification_id` 只报一次，用 ref 记住）。
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


# ----------------------------------------------------------------- 导入 ==
swap(
    """import { useCallback, useEffect, useRef, useState } from "react";""",
    """import { useCallback, useEffect, useRef, useState } from "react";

import { notify } from "../settings";
import type { Settings } from "../settings";""",
    label="导入设置模块",
)

# ---------------------------------------------------------------- props ==
swap(
    """  onDiscussionFinished: () => void;
}) {""",
    """  onDiscussionFinished: () => void;
  /** 界面设置（紧凑显示 / 忙碌时的发送行为 / 通知）。 */
  settings: Settings;
  /** 把"正在生成的是哪个会话"报上去 —— 侧栏要据此显示动态点。 */
  onStreamingChange: (sessionId: string | null) => void;
}) {""",
    label="props 类型",
)

swap(
    """  onDiscussionFinished,
}: {""",
    """  onDiscussionFinished,
  settings,
  onStreamingChange,
}: {""",
    label="props 解构",
)

# ------------------------------------------------------- 排队与打断 ==
SWAP_SEND = """  const send = useCallback(() => {
    const text = draft.trim();
    if (text === "" || sending) return;

    textRef.current = "";
    thinkingRef.current = "";
    setLive(EMPTY_LIVE);
    setSending(true);
    setDraft("");
    // 回复是消息，所以发送后一定回到对话页。
    onTab("chat");

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
          onDiscussionFinished();
        },
      },
    );
    abortRef.current = handle.abort;
  }, [draft, sending, sessionId, focusStep, handleEvent, onDiscussionFinished, onTab]);
"""

NEW_SEND = """  /**
   * 排队区：忙碌时按 Enter 想说的话先存在这里，本轮结束后自动发出。
   *
   * 存的是**文本 + 当时锚定的那一步**：等轮次结束再发时，用户可能已经点了
   * 别的计划项，若那时再读 `focusStep`，这条消息就会挂到另一步上，
   * 而用户根本没有改过目标。
   */
  const queuedRef = useRef<{ text: string; stepId: string | null } | null>(null);
  const [queued, setQueued] = useState<{ text: string; stepId: string | null } | null>(null);

  /** 真正发出去的那一层（`send` 与"排队出队"共用）。 */
  const startStream = useCallback(
    (text: string, stepId: string | null) => {
      textRef.current = "";
      thinkingRef.current = "";
      setLive(EMPTY_LIVE);
      setSending(true);
      // 回复是消息，所以发送后一定回到对话页。
      onTab("chat");
      onStreamingChange(sessionId);

      const handle = streamDiscussion(
        sessionId,
        { message: text, step_id: stepId },
        {
          onEvent: handleEvent,
          onError: (message) => {
            setLive((current) => ({ ...current, stage: null, error: message }));
          },
          onEnd: () => {
            setSending(false);
            abortRef.current = null;
            onStreamingChange(null);
            onDiscussionFinished();
            /*
             * 用户正开着"完成提醒"时，这一轮结束了就说一声。
             * 提醒只在页面不在前台时发（`unfocused` 那一档），
             * 而"没权限"由设置页如实说出来 —— 这里不假装。
             */
            const reasons = {
              always: "always",
              unfocused: "unfocused",
              never: "never",
            } as const;
            notify("这一轮说完了", "回来看看它的回复。", reasons[settings.notifyTurn]);

            const pending = queuedRef.current;
            queuedRef.current = null;
            setQueued(null);
            if (pending !== null) {
              // 出队：本轮已经结束，现在发出去。
              startStreamRef.current?.(pending.text, pending.stepId);
            }
          },
        },
      );
      abortRef.current = handle.abort;
    },
    [
      sessionId,
      handleEvent,
      onDiscussionFinished,
      onStreamingChange,
      onTab,
      settings.notifyTurn,
    ],
  );

  /* 自引用：出队时要再次调用 `startStream` 本身。 */
  const startStreamRef = useRef<((text: string, stepId: string | null) => void) | null>(null);
  useEffect(() => {
    startStreamRef.current = startStream;
  }, [startStream]);

  /**
   * `interrupt` = 先停下再发（`queue` 的相反行为，也是 Cmd/Ctrl+Enter 的那一档）。
   */
  const sendNow = useCallback(
    (text: string, stepId: string | null) => {
      if (abortRef.current !== null) abortRef.current();
      abortRef.current = null;
      setLive(EMPTY_LIVE);
      startStream(text, stepId);
    },
    [startStream],
  );

  const send = useCallback(
    (interrupt = false) => {
      const text = draft.trim();
      if (text === "") return;
      const stepId = focusStep?.step_id ?? null;
      const behavior = interrupt
        ? settings.sendWhileBusy === "queue"
          ? "interrupt"
          : "queue"
        : settings.sendWhileBusy;

      if (sending) {
        setDraft("");
        if (behavior === "queue") {
          /*
           * 排队：**只留最后一条**。留一队的话，用户中途改主意想说别的，
           * 前一句还会在后面被发出来 —— 而那时它已经不成立了。
           */
          queuedRef.current = { text, stepId };
          setQueued({ text, stepId });
          return;
        }
        sendNow(text, stepId);
        return;
      }

      setDraft("");
      startStream(text, stepId);
    },
    [draft, focusStep, sending, settings.sendWhileBusy, sendNow, startStream],
  );
"""

swap(SWAP_SEND, NEW_SEND, label="发送 / 排队 / 打断")

# 离开会话时清掉排队
swap(
    """  // 离开这个会话时中断正在进行的流（否则它会往别人的界面里写）。
  useEffect(() => {
    return () => abortRef.current?.();
  }, [sessionId]);""",
    """  /*
   * 离开这个会话时中断正在进行的流（否则它会往别人的界面里写）。
   * 排队里那条也要丢掉 —— 它是"发给这个会话"的。
   */
  useEffect(() => {
    return () => {
      abortRef.current?.();
      queuedRef.current = null;
    };
  }, [sessionId]);""",
    label="离开会话时清排队",
)

PATH.write_text(text, encoding="utf-8")
print(f"\n第三阶段 A 写完（{len(text.splitlines())} 行）")
