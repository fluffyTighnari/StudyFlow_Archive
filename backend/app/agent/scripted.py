"""确定性替身：**每次都能把「讨论 → 改计划」这条闭环走完**。

## 为什么需要它（这是一个演示与验证工具，不是测试替身）

真模型在这个任务上不稳定：实测 `qwen3.6:27b` 想了 462 帧、只思考不回答，
计划一个字没改（见 `_M9` §6.4）。于是两条完全不同的问题被搅在一起：

- **管子通不通**：界面点计划项 → 请求带 `step_id` → 后端补 `GESTURE_PLAN_CHANGE`
  → 工具被允许 → 计划产生新版本 → 界面重读显示新版；
- **模型行不行**：它肯不肯调那个工具。

前者是代码问题（可以修到确定），后者是模型问题（只能换模型）。
搅在一起的后果是：**闭环断了的时候，不知道该修哪儿。**
这个替身把"模型"换成确定的：它一定调 `propose_plan_change`，参数按真实 schema 填。

## 它不假装自己是模型

它每次都返回同一套剧本、**不做任何判断**。所以它证明的是"这条路能通"，
而不是"模型会这么做" —— 这两句话不能混着说。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterator, Sequence

from app.agent.llm import (
    ChatMessage,
    FakeLLM,
    LLMDelta,
    LLMReply,
    ToolSchema,
    call,
    reply,
)


class ScriptedPlanChange:
    """按真实 step_id 拼出一次合法的 `propose_plan_change` 调用。

    `target` 是**从库里读出来的真实 step_id** —— 不能写死 `ST-001`：
    换一个库之后那个 id 不存在，工具会以"目标不存在"为由拒绝，
    而那时看起来像是"闭环又断了"。

    ## 它是**幂等**的，而且这一点是刻意的

    新文案由 `original_action`（启动时读到的原文）生成，**不是**在当前文案上
    再拼一层。理由是演示会被点很多次：

    - 若每次在当前文案上加前缀，点三次会得到「分两天：分两天：分两天：…」——
      看起来像界面在重复渲染，实际是替身在叠字；
    - 若每次新增一个步骤，点三次计划就从 3 步长到 6 步 —— 演示完的库是脏的。

    现在每次点击的效果是：**步骤文案换成同一句、计划版本 +1**。
    于是"计划真的改了、界面看得到新版"可以被反复演示，而库不会越点越乱。
    """

    #: 一轮讨论消耗几条剧本。剧本就两条（带工具调用的一条 + 收尾的一条），
    #: 而循环在一轮里恰好问两次：先拿到工具调用，喂回结果后再要一句收尾。
    #: 这个数字有测试盯着（`tests/agent/test_scripted.py`）——
    #: 猜错的表现是"第二次点击时替身失忆"，而那看起来像闭环断了。
    REPLIES_PER_DISCUSSION = 2

    def __init__(
        self,
        *,
        target: str,
        steps: Sequence[dict[str, Any]],
        change: str,
        user_view: str,
        frame_delay: float = 0.0,
        chunk_size: int = 6,
    ) -> None:
        self.frame_delay = frame_delay
        self.chunk_size = chunk_size
        self._lock = threading.Lock()
        self._calls = 0

        original_action = str(steps[0]["action"]) if steps else "这一步"
        split_action = f"分两天做：{original_action}（每天一半）"

        new_steps: list[dict[str, Any]] = []
        for index, step in enumerate(steps):
            new_steps.append(
                {
                    "step_id": step.get("step_id"),
                    "action": split_action if index == 0 else step["action"],
                    "output": step.get("output"),
                }
            )

        self._replies: list[LLMReply] = [
            reply(
                "我看了一下第 1 步，按你说的分两天。",
                call(
                    "propose_plan_change",
                    target=target,
                    user_view=user_view,
                    suggested_change={"steps": new_steps, "change": change},
                ),
            ),
            reply(f"改好了：{change}。先按这个走一周看看，不合适再调。"),
        ]
        self._fake = self._rebuild()

    def _rebuild(self) -> FakeLLM:
        """装一份崭新的剧本。

        `FakeLLM` 的剧本是一次性的（取一条少一条），所以"能被点很多次"
        这件事必须由这里负责：每轮讨论开始时重建。
        """
        return FakeLLM(list(self._replies), chunk_size=self.chunk_size)

    def _take(self) -> FakeLLM:
        """取这一轮要用的那份剧本（必要时重建）。"""
        with self._lock:
            if self._calls % self.REPLIES_PER_DISCUSSION == 0:
                self._fake = self._rebuild()
            self._calls += 1
            return self._fake

    def chat(
        self, messages: Sequence[ChatMessage], *, tools: Sequence[ToolSchema] = ()
    ) -> LLMReply:
        return self._take().chat(messages, tools=tools)

    def stream(
        self, messages: Sequence[ChatMessage], *, tools: Sequence[ToolSchema] = ()
    ) -> Iterator[LLMDelta]:
        fake = self._take()
        for delta in fake.stream(messages, tools=tools):
            if self.frame_delay:
                time.sleep(self.frame_delay)
            yield delta
