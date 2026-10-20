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

import json
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


#: 判官替身故意夹进来的**编造引用**。
#:
#: ★ 这不是"为了让测试失败"：它是**唯一能证明引文核对真的在跑**的办法。
#: 一个只会回真实引用的替身，在"代码根本没有核对引文"的实现上
#: 同样会得到满分 —— 于是那条最重要的防线从没被验过。
FABRICATED_QUOTE = "这段话是编的，它绝不可能出现在用户答案里"

#: 端到端里触发「反问」的那句话。用户这样说了，替身就**先问一句**而不是猜。
CLARIFY_TRIGGER = "我不太确定要做什么"

#: 端到端里触发「联网搜索」的那句话。它只在 `web_search` 真的在工具清单里时生效。
SEARCH_TRIGGER = "帮我查一下"


def search_reply_for(
    messages: Sequence[ChatMessage], tools: Sequence[ToolSchema]
) -> LLMReply | None:
    """用户说"帮我查一下…"时，替身**调用 `web_search`**。

    与 `clarification_reply_for` 同一个理由：没有这一支，
    "联网搜索接通了没有"在端到端里永远不会被走到（那个确定性替身
    本来只会改计划），于是那条链路只能靠单测断言 ——
    而单测不经过 HTTP、工具清单过滤、结果回灌这几层。

    ★ `web_search` 不在工具清单里时返回 `None`：没配搜索的服务器上，
    这句话应该走**原来的**剧本（替身照常改计划），而不是报一个
    "工具不存在"的错 —— 那样这一格验的就是错的东西了。

    ★ **这一次讨论的两轮都由这里回答**，一次都不碰剧本（不调 `_take()`）。
    理由不是省事：`_calls` 那个计数器按"每轮讨论两次"重建剧本，
    让搜索这条路悄悄吃掉一次 `_take()`，会把**后面**那一步计划修改
    推到剧本的第二句上（"改好了"而没有工具调用），
    而症状看起来像"计划闭环坏了"，原因却在完全无关的地方。
    """
    if not messages:
        return None
    last = messages[-1]
    if last.role == "tool" and _carries_external_result(messages):
        # 第二轮：搜完了，给一句收尾。**不碰剧本**。
        return reply("查到了。外面那份说法我念给你听，具体怎么用你定。")
    if last.role != "user":
        return None
    content = last.content or ""
    if SEARCH_TRIGGER not in content:
        return None
    if "web_search" not in {schema.name for schema in tools}:
        return None
    query = content.split(SEARCH_TRIGGER, 1)[1].strip() or "间隔重复"
    return reply(
        "我去外面查一下，回来告诉你。",
        call("web_search", query=query, limit=3),
    )


def _carries_external_result(messages: Sequence[ChatMessage]) -> bool:
    """最近几条里有没有一条"外部信息"的工具结果。

    按那句话的**原样**匹配，而不是按工具名：工具结果是以 JSON 回灌的，
    `tool` 这个角色也用于内置工具 —— 只有这句话能确定
    "刚才那次是外挂调用"。
    """
    from app.agent.external import EXTERNAL_MARK

    return any(
        message.role == "tool" and EXTERNAL_MARK in (message.content or "")
        for message in messages[-4:]
    )


def clarification_reply_for(
    messages: Sequence[ChatMessage], tools: Sequence[ToolSchema]
) -> LLMReply | None:
    """用户说"我不确定要做什么"时，替身**调用 `ask_about_task`**，而不是继续猜。

    ## 为什么替身要专门做这一件事

    "任务不明确时反问"这条规矩落在**模型愿不愿意问**上 ——
    而端到端里那个确定性替身本来只会改计划。没有这一支，
    `ask_about_task` 这条链路（工具 → 写库 → 界面读得到）在端到端里
    **一次都不会被走到**，于是"它接通了没有"只能靠单测断言，
    而单测不经过 HTTP、鉴权、序列化那几层。

    ★ 它只在**用户最后一句**里出现触发语时才这样回 ——
    不是"只要工具在就调用"，否则第一条讨论消息就会变成一次提问，
    而计划闭环那一步会被它搅乱。
    """
    if not messages:
        return None
    last = messages[-1]
    if last.role != "user":
        return None
    if CLARIFY_TRIGGER not in (last.content or ""):
        return None
    if "ask_about_task" not in {schema.name for schema in tools}:
        return None
    return reply(
        "等等，我先确认一件事再排计划。",
        call(
            "ask_about_task",
            slot="goal",
            question="你要的「第三章」是哪份材料的第三章？这次想达成什么？",
            options=["把这一章的公式自己推一遍", "先弄懂概念，不推公式"],
        ),
    )


def judge_reply_for(messages: Sequence[ChatMessage]) -> LLMReply | None:
    """如果这次调用是判官，就**按提示词里的答案**合成一份判定。

    返回 `None` = 不是判官调用，调用方走自己的剧本。

    ## 它判得"对"吗

    不对，也不打算对：它固定认为**前一半要点答到了**（引用直接取自答案原文），
    并且**多报一条编造的命中**。所以它给出的分数不是对能力的估计，
    而是一份"有真有假"的判官输出 —— 让下游的引文核对必须真的工作。
    """
    from app.benchmark.judge import parse_prompt

    if not messages:
        return None
    parsed = parse_prompt(messages[-1].content or "")
    if parsed is None:
        return None
    guide, answer = parsed
    normalized = "".join(answer.split())
    hits: list[dict[str, Any]] = []
    real = max(1, len(guide) // 2)
    for index in range(real):
        start = index * 8
        quote = normalized[start : start + 12] or normalized[:12]
        if len(quote) >= 4:
            hits.append({"no": index + 1, "quote": quote})
    if len(guide) > real:
        hits.append({"no": len(guide), "quote": FABRICATED_QUOTE})
    return reply(json.dumps({"hits": hits}, ensure_ascii=False))


class ScriptedPlanChange:
    """按真实 step_id 拼出一次合法的 `propose_plan_change` 调用。

    `target` 是**从库里读出来的真实 step_id** —— 不能写死 `ST-001`：
    换一个库之后那个 id 不存在，工具会以"目标不存在"为由拒绝，
    而那时看起来像是"闭环又断了"。

    ## ★ 它也兼任**计分的判官**（P9）

    判分要问模型，而端到端那一步用的是一个确定性替身。
    所以 `chat` 先看一眼"这次是不是判官调用"（`judge_reply_for`），
    是就直接按提示词里的答案合成判定，**不消耗讨论剧本**。

    为什么必须分开：讨论剧本是**按次数**发的（一段两回复），
    一次判官调用若吃掉一格剧本，下一次点计划项就会"替身失忆" ——
    而那看起来像闭环断了，排查方向完全错。

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

    #: 判分要记 `judge_model` —— 没有它，"同一个答案两次判出不同分"无法追。
    model = "scripted-judge"

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
        judging = judge_reply_for(messages)
        if judging is not None:
            return judging
        asking = clarification_reply_for(messages, tools)
        if asking is not None:
            return asking
        searching = search_reply_for(messages, tools)
        if searching is not None:
            return searching
        return self._take().chat(messages, tools=tools)

    def stream(
        self, messages: Sequence[ChatMessage], *, tools: Sequence[ToolSchema] = ()
    ) -> Iterator[LLMDelta]:
        fake = self._take()
        for delta in fake.stream(messages, tools=tools):
            if self.frame_delay:
                time.sleep(self.frame_delay)
            yield delta
