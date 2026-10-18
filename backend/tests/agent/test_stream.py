"""流式：**两条路必须是同一个循环**，而且过程要能被看见。

这个文件里最重要的不是"事件发出来了"，而是三条**不许漂移**的等式：

1. `run_stream` 的收官结果 == `run` 的返回值（逐字段）。
   两条路各写一份循环是最容易发生、也最难发现的分叉：
   测试全绿（它们只测了其中一条），而用户看到的与库里存的不一样。
2. 增量拼起来 == 收官帧的 `content`。**拼接只服务显示**，判定永远用收官帧。
3. `seq` 在一条流内严格递增 —— 前端据此判断有没有丢帧，
   而丢帧拼出来的回答读起来通顺、语义残缺，没有任何东西会提示用户。
"""

from __future__ import annotations

import pytest

from app.agent.dispatch import ToolTraceEntry
from app.agent.events import EventType, StageName
from app.agent.llm import FakeLLM, LLMDelta, LLMError, LLMReply, call, reply
from app.agent.loop import AgentLoop, DiscussionResult, StopReason


def _loop(sandbox, llm, *, max_steps: int = 8, gestures=frozenset()) -> AgentLoop:
    return AgentLoop(
        sandbox.db, sandbox.session_id, llm, max_steps=max_steps, user_gestures=gestures
    )


def _drain(loop: AgentLoop, message: str) -> tuple[list, DiscussionResult]:
    """跑一次流式，把事件收下来，**并从 `done` 里取回结果**。

    刻意从事件里取而不是另外调用一次循环：端点就是这么用的 ——
    测试与生产走的是同一条取结果的路。
    """
    events = list(loop.run_stream(message))
    assert events[-1].type is EventType.DONE, "最后一条事件必须是 done"
    data = events[-1].data
    result = DiscussionResult(
        reply=str(data["reply"]),
        stop_reason=StopReason(data["stop_reason"]),
        steps=int(data["steps"]),
        traces=tuple(ToolTraceEntry(**entry) for entry in data["tool_trace"]),
        llm_meta=dict(data["llm_meta"]),
    )
    return events, result


def _tokens(events: list, kind: EventType = EventType.TOKEN) -> str:
    return "".join(str(event.data["text"]) for event in events if event.type is kind)


def _comparable(entries) -> list[dict]:
    """把轨迹里**唯一允许两条路不同**的字段摘掉：`duration_ms`。

    它是量出来的墙钟时间，两次运行必然不等 —— 让它参与比对，
    这条断言就会变成一条随机红的测试，而随机红的测试最终会被 `skip` 掉。
    其余每一个字段都必须一致（尤其 `status` 与 `message`：
    "被拒的理由"本身是判定结果，不是耗时）。
    """
    out = []
    for entry in entries:
        item = entry.as_dict()
        assert item["duration_ms"] >= 0, "耗时要被真的量过，不能是占位值"
        out.append({key: value for key, value in item.items() if key != "duration_ms"})
    return out


# --------------------------------------------------------------------------- #
# 一：两条路给出同一个结果
# --------------------------------------------------------------------------- #


SCRIPT_PLAIN = [reply("这一步看起来没问题，你可以直接开始。")]

SCRIPT_TOOLS = [
    reply("我先看一下你的认知基线。", call("retrieve_cognitive", scope="m1")),
    reply("看完了，你的基线里还没有读数。"),
]

SCRIPT_BUDGET = [reply("再查一下", call("retrieve_cognitive", scope="m1")) for _ in range(3)]

SCRIPT_REJECTED = [
    reply("改一下", call("propose_plan_change")),
    reply("改一下", call("propose_plan_change")),
]

SCRIPT_LLM_ERROR: list = []  # 空脚本 → 第一次调用就 LLMError


@pytest.mark.parametrize(
    ("script", "steps", "kwargs"),
    [
        ("plain", 1, {}),
        ("tools", 2, {}),
        ("budget", 3, {"max_steps": 3}),
        ("rejected", 2, {}),
        ("llm_error", 1, {}),
    ],
)
def test_the_stream_and_the_sync_path_produce_the_same_result(
    sandbox, script: str, steps: int, kwargs: dict
) -> None:
    """**本文件的核心断言。** 五个场景覆盖全部五种停止理由。

    每一条都同时跑两条路、逐字段比对。这不是"顺手也测一下另一条路"：
    它是唯一能证明"循环体只有一份"的东西 —— 而只要循环体有两份，
    将来一定会有一次修复只落在其中一份上。
    """
    scripts = {
        "plain": SCRIPT_PLAIN,
        "tools": SCRIPT_TOOLS,
        "budget": SCRIPT_BUDGET,
        "rejected": SCRIPT_REJECTED,
        "llm_error": SCRIPT_LLM_ERROR,
    }
    make = lambda: [  # noqa: E731 - 脚本要被两条路各消耗一次，必须重建
        reply(item.content, *item.tool_calls) for item in scripts[script]
    ]

    sync_result = _loop(sandbox, FakeLLM(make()), **kwargs).run("开始")
    _, stream_result = _drain(_loop(sandbox, FakeLLM(make(), chunk_size=3), **kwargs), "开始")

    assert stream_result.stop_reason is sync_result.stop_reason
    assert stream_result.steps == sync_result.steps
    assert stream_result.reply == sync_result.reply
    assert stream_result.llm_meta == sync_result.llm_meta
    assert _comparable(stream_result.traces) == _comparable(sync_result.traces), (
        "两条路的工具轨迹必须逐字段一致（含被拒的理由）"
    )


def test_the_stop_reasons_really_differ_across_those_scenarios(sandbox) -> None:
    """上一条测试若"两条路都返回同一个错值"也会绿 —— 所以这里钉住取值本身。

    这是本项目反复栽的那个坑的通用形态：**一个恒等式测试在两边同时错时仍然通过。**
    因此每一条等式断言旁边都要有一条"取值对不对"的断言。
    """
    seen = {}
    for name, script, kwargs in (
        ("plain", SCRIPT_PLAIN, {}),
        ("budget", SCRIPT_BUDGET, {"max_steps": 3}),
        ("rejected", SCRIPT_REJECTED, {}),
        ("llm_error", SCRIPT_LLM_ERROR, {}),
    ):
        llm = FakeLLM([reply(item.content, *item.tool_calls) for item in script])
        seen[name] = _loop(sandbox, llm, **kwargs).run("开始").stop_reason

    assert seen == {
        "plain": StopReason.COMPLETED,
        "budget": StopReason.MAX_STEPS,
        "rejected": StopReason.REJECTED_BY_DISPATCHER,
        "llm_error": StopReason.LLM_ERROR,
    }


# --------------------------------------------------------------------------- #
# 二：过程可见
# --------------------------------------------------------------------------- #


def test_the_stream_shows_the_whole_path(sandbox) -> None:
    """事件序列本身要说清楚"它做了什么"。"""
    llm = FakeLLM(
        [reply(item.content, *item.tool_calls) for item in SCRIPT_TOOLS], chunk_size=4
    )
    events, _ = _drain(_loop(sandbox, llm), "看看我的基线")

    kinds = [event.type for event in events]
    assert kinds[0] is EventType.STAGE
    assert EventType.TOOL_CALL in kinds and EventType.TOOL_RESULT in kinds
    assert kinds[-1] is EventType.DONE

    # 阶段：读上下文 → 等模型 →（工具）→ 等模型
    stages = [
        event.data["name"] for event in events if event.type is EventType.STAGE
    ]
    assert stages == [
        StageName.READ_CONTEXT.value,
        StageName.WAIT_MODEL.value,
        StageName.RUN_TOOLS.value,
        StageName.WAIT_MODEL.value,
    ], "阶段序列是可读的过程说明，不是随意的事件"

    # 读上下文那一条要说清楚"锚在哪一步、计划是第几版"
    first = events[0].data
    assert first["anchor"] == "ST-001"
    assert first["plan_version"] == 1

    # 工具那两条要带上名字、参数、状态、结果
    call_event = next(event for event in events if event.type is EventType.TOOL_CALL)
    assert call_event.data["tool"] == "retrieve_cognitive"
    assert call_event.data["arguments"] == {"scope": "m1"}
    result_event = next(event for event in events if event.type is EventType.TOOL_RESULT)
    assert result_event.data["status"] == "success"
    assert result_event.data["call_id"] == call_event.data["call_id"]
    # `data` 必须发出去：界面上"它到底查到了什么"全在里面
    assert result_event.data["data"]


def test_a_rejection_is_visible_in_the_stream(sandbox) -> None:
    """被拦住必须看得见 —— 这是本项目最该被展示的一类事。"""
    llm = FakeLLM([reply(item.content, *item.tool_calls) for item in SCRIPT_REJECTED])
    events, result = _drain(_loop(sandbox, llm), "改")

    rejected = [
        event
        for event in events
        if event.type is EventType.TOOL_RESULT and event.data["status"] == "rejected"
    ]
    assert len(rejected) == 2
    assert "缺少必填参数" in rejected[0].data["message"]
    # 最终那句话本身也要说明"为什么停"（用户读的是它）
    assert result.stop_reason is StopReason.REJECTED_BY_DISPATCHER
    assert "原样又试了一次" in result.reply


def test_the_seq_is_strictly_increasing_from_one(sandbox) -> None:
    """丢帧必须能被发现（前端按它判断）。"""
    llm = FakeLLM([reply(item.content, *item.tool_calls) for item in SCRIPT_TOOLS])
    events, _ = _drain(_loop(sandbox, llm), "看看我的基线")

    assert [event.seq for event in events] == list(range(1, len(events) + 1))


def test_thinking_never_leaks_into_the_answer(sandbox) -> None:
    """思考是独立事件，且**不进** `reply` —— 与 `LLMReply.thinking` 同一条规矩。"""
    thinking = "用户问的是第一步，我应该先看他的基线……"
    llm = FakeLLM([LLMReply(content="先别急着加量。", thinking=thinking)], chunk_size=5)
    events, result = _drain(_loop(sandbox, llm), "我该做什么")

    assert _tokens(events, EventType.THINKING) == thinking
    assert result.reply == "先别急着加量。"
    assert "基线" not in result.reply, "推理绝不能并进回答"


# --------------------------------------------------------------------------- #
# 三：拼接只服务显示
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("chunk_size", [1, 3, 1000])
def test_the_merged_tokens_equal_the_final_content(sandbox, chunk_size: int) -> None:
    """分帧大小不影响结论 —— 这句话只有在"判定用收官帧"时才成立。"""
    llm = FakeLLM(
        [reply("好。", call("retrieve_cognitive", scope="m1")), reply("先看基线。")],
        chunk_size=chunk_size,
    )
    events, result = _drain(_loop(sandbox, llm), "看看")

    # 两次模型调用各自的增量拼在一起，与"最后一次回答"不同 ——
    # 所以这里断言的是"拼接结果是增量之和"，而不是"等于 reply"。
    # `reply` 只可能是最后一次调用的正文（循环的语义），
    # 两者的差别正是"显示"与"判定"的差别。
    assert _tokens(events) == "好。先看基线。"
    assert result.reply == "先看基线。"
    assert result.reply in _tokens(events)


def test_the_thinking_without_an_answer_explanation_is_reachable_when_streaming(
    sandbox,
) -> None:
    """**每一条"系统解释自己为什么失败"的话，都必须能从流式那条路走到。**

    这条测试的由来是一次真实运行：模型想完 197 个 token 却没有落到回答上，
    而流式路径端给用户的是「本地模型这一轮生成出了问题」——
    真相是「只想了没说」。原因是收官帧里 `message` 是空的，
    只解析收官帧就丢掉了全部思考（那条缺陷在 `test_llm.py` 里被钉住）。

    单测 `thinking_without_answer_note` 一直是对的：**它只是走不到**。
    所以这里断言的不是那个函数，而是"这条路能不能走到那句话"。
    """
    from app.agent.llm import LLMDelta

    class ThinkingOnly(FakeLLM):
        """复刻真实收官帧的形状：思考在增量里，收官帧是空的。"""

        def stream(self, messages, *, tools=()):
            self.chat(messages, tools=tools)
            yield LLMDelta(thinking="用户反馈第一步太重了，")
            yield LLMDelta(thinking="我在想怎么把它拆细……")
            yield LLMDelta(reply=LLMReply(content="", thinking="用户反馈第一步太重了，我在想怎么把它拆细……"))

    events, result = _drain(_loop(sandbox, ThinkingOnly([reply("用不到")])), "太多了")

    assert result.stop_reason is StopReason.LLM_ERROR
    assert "没有落到回答或工具调用上" in result.reply, result.reply
    assert "生成出了问题" not in result.reply, (
        "这句是错的解释：模型没问题，它只是只想了没说"
    )
    # 思考内容要能被用户看见（否则"它想了什么"就成了内部状态）
    assert "把它拆细" in _tokens(events, EventType.THINKING)


def test_a_broken_stream_is_reported_as_an_llm_error_not_as_an_answer(sandbox) -> None:
    """**半句话不能被当成回复。**

    `OllamaClient.stream` 在缺收官帧时抛错（那里有它自己的测试）；
    这里钉住上游的反应：循环把它变成 `llm_error`，而不是把半个回答端出去。
    """

    class HalfBroken(FakeLLM):
        def stream(self, messages, *, tools=()):  # type: ignore[override]
            self.chat(messages, tools=tools)  # 消耗脚本，保持"脚本用完了"的保护
            yield LLMDelta(text="我认为第")
            raise LLMError("流式响应没有收官帧（连接在生成中途断了）。")

    events, result = _drain(
        _loop(sandbox, HalfBroken([reply("我认为第一步太多。")])), "呢"
    )

    assert result.stop_reason is StopReason.LLM_ERROR
    assert "我没能拿到模型的回复" in result.reply
    assert "我认为第" not in result.reply, "断开时收到的那半句不许出现在回答里"
    # 但**它必须出现在事件里**：用户已经看到了那半句，界面不能假装没发生过。
    assert _tokens(events) == "我认为第"
