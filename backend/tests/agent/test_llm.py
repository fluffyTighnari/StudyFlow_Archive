"""LLM 客户端层 —— 契约、替身、以及**发出去的请求长什么样**。

这一层最大的风险不是"解析错了"，而是**测试偷偷依赖了外部世界**：
一旦某个测试真的去连 Ollama，那么在没起服务的机器上，
一整套治理内核的测试会跟着变红，而问题根本不在内核里。

所以这里刻意全部用注入的 `transport`（一个普通函数）来测真实客户端，
一个字节都不出网。
"""

from __future__ import annotations

import json

import pytest

from app.agent.llm import (
    ChatMessage,
    FakeLLM,
    LLMError,
    LLMReply,
    OllamaClient,
    ToolCall,
    ToolSchema,
    call,
    context_truncation_note,
    reply,
)


class RecordingTransport:
    """记录请求 + 返回预设响应的假传输层。"""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.requests: list[tuple[str, dict, float]] = []

    def __call__(self, url: str, payload: dict, timeout: float) -> dict:
        self.requests.append((url, payload, timeout))
        if not self._responses:
            raise AssertionError("假传输层的响应用完了")
        return self._responses.pop(0)

    @property
    def payloads(self) -> list[dict]:
        """发出去的请求体，按顺序。断言"我们发了什么"时用它。"""
        return [payload for _url, payload, _timeout in self.requests]


def _ollama_reply(
    content: str = "",
    tool_calls: list[dict] | None = None,
    **extra: object,
) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"model": "test-model", "message": message, "done": True, **extra}


# --------------------------------------------------------------------------- #
# FakeLLM：测试替身必须比真实模型更严格
# --------------------------------------------------------------------------- #


def test_fake_llm_plays_back_the_script_in_order() -> None:
    llm = FakeLLM([reply("第一句"), reply("第二句")])
    assert llm.chat([]).content == "第一句"
    assert llm.chat([]).content == "第二句"


def test_fake_llm_refuses_to_improvise_when_the_script_runs_out() -> None:
    """脚本用完还敢被调用 → 抛错，而不是返回空回复。

    真实模型会无限配合你，所以测试里的替身**不该**配合：
    脚本不够长意味着循环多走了一步，那正是要抓的缺陷。
    返回一句空话会让这个缺陷安静地过去。
    """
    llm = FakeLLM([reply("就一句")])
    llm.chat([])
    with pytest.raises(LLMError, match="多走了一步"):
        llm.chat([])


def test_fake_llm_records_what_it_was_told() -> None:
    """「系统提示词里到底有什么」必须可以被断言 —— 而不是只能推断。"""
    llm = FakeLLM([reply("好")])
    schema = ToolSchema(name="write_stage", description="写阶段", parameters={})
    llm.chat([ChatMessage.system("你是助手"), ChatMessage.user("你好")], tools=[schema])

    assert [message.role for message in llm.seen[0]] == ["system", "user"]
    assert llm.tools_seen[0][0].name == "write_stage"


def test_reply_renumbers_tool_calls_so_ids_are_unique() -> None:
    """一次回复里两次调用必须有**不同**的 id。

    否则轨迹里会出现两个 `call_1`，"哪条结果对应哪次调用"就答不上来了。
    """
    item = reply("先看两处", call("retrieve_session", q="a"), call("retrieve_folder", q="b"))
    assert [c.call_id for c in item.tool_calls] == ["call_1", "call_2"]
    assert [c.name for c in item.tool_calls] == ["retrieve_session", "retrieve_folder"]


def test_think_is_not_sent_unless_it_is_set_explicitly() -> None:
    """默认**不发** `think` —— 有些模型收到它会直接 400。

    `qwen2.5-coder:7b` / `phi3:3.8b` / `qwen2:1.5b` 的报错是
    "does not support thinking/tools"，而那句话会让人以为是提示词或工具声明的问题。
    """
    transport = RecordingTransport([_ollama_reply("好")])
    OllamaClient(model="m", transport=transport).chat([ChatMessage.user("在吗")])

    assert "think" not in transport.payloads[0], (
        "默认发了 think —— 不认识这个字段的模型会 400，而报错指向工具/提示词"
    )


def test_think_false_is_sent_when_the_caller_asks_for_it() -> None:
    """`think=False` 时**必须真的发出去**（是 `False`，不是"缺失"）。

    实测：`qwen3.6:27b` 在 `think=false` 时 5/5 次都产出工具调用，
    而不发这个字段是唯一跑飞 900 秒超时的一格。所以"发了 False"与
    "没发"在效果上完全不是一回事 —— 而 `payload.get("think")` 两者都假。
    """
    transport = RecordingTransport([_ollama_reply("好")])
    OllamaClient(model="m", think=False, transport=transport).chat(
        [ChatMessage.user("在吗")]
    )

    payload = transport.payloads[0]
    assert "think" in payload, "显式关掉思考却没发字段 —— 那就退回成跑飞的那一格了"
    assert payload["think"] is False


def test_think_true_is_sent_when_the_caller_asks_for_it() -> None:
    transport = RecordingTransport([_ollama_reply("好")])
    OllamaClient(model="m", think=True, transport=transport).chat(
        [ChatMessage.user("在吗")]
    )
    assert transport.payloads[0]["think"] is True


def test_the_think_env_var_has_three_states() -> None:
    """`auto` 与 `false` 必须是**不同的值**（`None` vs `False`）。

    二态开关总有一类模型被默认值坑到：认识 `think` 的（27B）需要 `false`，
    不认识的那批（coder/phi3/qwen2）需要**完全不发**。
    """
    from app.config import _think_flag

    assert _think_flag(None) is None
    assert _think_flag("auto") is None
    assert _think_flag("false") is False
    assert _think_flag("0") is False
    assert _think_flag("true") is True
    with __import__("pytest").raises(ValueError) as caught:
        _think_flag("maybe")
    assert "MOUXUE_LLM_THINK" in str(caught.value)


def test_fake_llm_can_be_extended_after_construction() -> None:
    llm = FakeLLM().push(reply("甲")).push(reply("乙"))
    assert llm.remaining == 2
    assert llm.chat([]).content == "甲"


# --------------------------------------------------------------------------- #
# OllamaClient：请求形状（照官方文档，不是猜的）
# --------------------------------------------------------------------------- #


def _schema() -> ToolSchema:
    return ToolSchema(
        name="retrieve_session",
        description="检索本会话",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    )


def test_client_posts_to_the_chat_endpoint_with_stream_disabled() -> None:
    transport = RecordingTransport([_ollama_reply("你好")])
    client = OllamaClient(model="m", base_url="http://host:1234/", transport=transport)
    client.chat([ChatMessage.user("在吗")], tools=[_schema()])

    url, payload, _ = transport.requests[0]
    assert url == "http://host:1234/api/chat"
    assert payload["model"] == "m"
    assert payload["stream"] is False
    assert payload["tools"][0]["function"]["name"] == "retrieve_session"


def test_client_omits_the_tools_key_when_there_are_no_tools() -> None:
    transport = RecordingTransport([_ollama_reply("你好")])
    OllamaClient(model="m", transport=transport).chat([ChatMessage.user("在吗")])
    assert "tools" not in transport.requests[0][1]


def test_tool_results_use_tool_name_not_tool_call_id() -> None:
    """Ollama 用 `tool_name` 并按**顺序**匹配结果，与 OpenAI 兼容层不同。

    发错字段不会报错 —— 服务端会忽略它，表现成**模型忽然看不见工具结果**，
    然后反复调用同一个工具。这正是那种"不报错的错"。
    """
    transport = RecordingTransport([_ollama_reply("好")])
    client = OllamaClient(model="m", transport=transport)
    used = ToolCall(call_id="call_1", name="retrieve_session", arguments={"query": "x"})
    client.chat([ChatMessage.assistant("", [used]), ChatMessage.tool_result(used, "结果")])

    _, payload, _ = transport.requests[0]
    assistant, tool = payload["messages"]
    assert "id" not in assistant["tool_calls"][0]  # Ollama 没有这个字段
    assert assistant["tool_calls"][0]["function"]["index"] == 0
    assert tool["role"] == "tool" and tool["tool_name"] == "retrieve_session"
    assert "tool_call_id" not in tool


def test_client_parses_tool_calls_into_typed_objects() -> None:
    transport = RecordingTransport(
        [
            _ollama_reply(
                tool_calls=[
                    {
                        "type": "function",
                        "function": {
                            "index": 0,
                            "name": "write_stage",
                            "arguments": {"stage": "练习", "reason": "因为"},
                        },
                    }
                ]
            )
        ]
    )
    result = OllamaClient(model="m", transport=transport).chat([ChatMessage.user("x")])

    assert result.wants_tools is True
    assert result.tool_calls[0].name == "write_stage"
    assert result.tool_calls[0].arguments == {"stage": "练习", "reason": "因为"}
    assert result.tool_calls[0].call_id == "call_1"


def test_client_accepts_arguments_serialized_as_a_json_string() -> None:
    """OpenAI 兼容层常把参数塞成字符串 —— 两条路都要能走。"""
    transport = RecordingTransport(
        [
            _ollama_reply(
                tool_calls=[
                    {
                        "function": {
                            "name": "write_stage",
                            "arguments": json.dumps({"stage": "练习"}),
                        }
                    }
                ]
            )
        ]
    )
    result = OllamaClient(model="m", transport=transport).chat([])
    assert result.tool_calls[0].arguments == {"stage": "练习"}


def test_client_keeps_content_alongside_tool_calls() -> None:
    """真实模型常先说话再调工具。丢掉 content 会让聊天记录看起来像它没开口。"""
    transport = RecordingTransport(
        [
            _ollama_reply(
                "我先看一下你的计划。",
                tool_calls=[{"function": {"name": "retrieve_session", "arguments": {}}}],
            )
        ]
    )
    result = OllamaClient(model="m", transport=transport).chat([])
    assert result.content == "我先看一下你的计划。"
    assert result.tool_calls[0].name == "retrieve_session"


def test_client_surfaces_a_service_side_error() -> None:
    transport = RecordingTransport([{"error": "model 'x' not found"}])
    with pytest.raises(LLMError, match="not found"):
        OllamaClient(model="x", transport=transport).chat([])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({}, "没有 message"),
        ({"message": "不是对象"}, "没有 message"),
        ({"message": {"content": 5}}, "不是字符串"),
        ({"message": {"content": "", "tool_calls": [{"function": {}}]}}, "没有工具名"),
        ({"message": {"content": "", "tool_calls": ["x"]}}, "不是对象"),
        (
            {"message": {"content": "", "tool_calls": [{"function": {"name": "t",
                                                                   "arguments": "{坏 JSON"}}]}},
            "不是合法 JSON",
        ),
        (
            {"message": {"content": "", "tool_calls": [{"function": {"name": "t",
                                                                   "arguments": [1, 2]}}]}},
            "不是对象",
        ),
    ],
)
def test_client_rejects_malformed_replies_with_readable_errors(raw, expected) -> None:
    """畸形回复必须是 `LLMError`（可预期的运行期状态），不是 KeyError / 堆栈。

    它会被循环翻译成 `stop_reason="llm_error"`；如果这里漏成 KeyError，
    用户看到的会是一个 500 和一段堆栈，而真正的原因只是模型吐了一句坏 JSON。
    """
    transport = RecordingTransport([raw])
    with pytest.raises(LLMError, match=expected):
        OllamaClient(model="m", transport=transport).chat([])


def test_a_tool_call_without_arguments_is_accepted_as_empty() -> None:
    """缺 `arguments` 不是畸形 —— 无参工具本来就没有参数。

    把它当错误会误伤一类完全正常的调用（例如 `retrieve_cognitive()`），
    而那种误伤只会在真实模型上偶发出现，测试很容易漏掉。
    """
    transport = RecordingTransport(
        [_ollama_reply(tool_calls=[{"function": {"name": "retrieve_cognitive"}}])]
    )
    result = OllamaClient(model="m", transport=transport).chat([])
    assert result.tool_calls[0].arguments == {}


def test_client_reports_meta_without_using_it_for_decisions() -> None:
    transport = RecordingTransport(
        [_ollama_reply("好", eval_count=12, prompt_eval_count=34, total_duration=1_500_000)]
    )
    result = OllamaClient(model="m", transport=transport).chat([])
    assert result.meta["eval_count"] == 12
    assert result.meta["prompt_eval_count"] == 34
    assert result.meta["total_duration_ms"] == 1.5


def test_the_request_declares_a_context_window() -> None:
    """**必须显式给 `num_ctx`。**

    不给的话 Ollama 按当前显存自己算一个，而它算出来的可能远小于我们发的 prompt：
    一次真实运行里它给了 4096，于是 7000 token 的请求（14 个工具声明约 1.7 万字符）
    被截到 4095、只剩一个 token 可生成。表现出来是"模型答了个空话"。
    """
    transport = RecordingTransport([{"message": {"content": "好"}}])
    client = OllamaClient(model="m", num_ctx=8192, transport=transport)
    client.chat([ChatMessage.user("在吗")])
    assert transport.payloads[0]["options"]["num_ctx"] == 8192


def test_the_context_window_default_can_hold_the_tool_declarations() -> None:
    """默认值本身就是一处设计：太小会让**每一次**调用都失败，而不是偶尔。"""
    from app.config import Settings

    transport = RecordingTransport([{"message": {"content": "好"}}])
    OllamaClient(model="m", transport=transport).chat([ChatMessage.user("在吗")])
    assert transport.payloads[0]["options"]["num_ctx"] == 16384
    assert Settings().llm_num_ctx == 16384


def test_a_prompt_that_did_not_fit_is_reported_with_the_real_numbers() -> None:
    """**一句错的解释比不解释更坏。**

    真实返回：`done_reason=length`、`prompt_eval_count=4095`、`eval_count=1`、
    `content=""`。当时循环说"本地模型这一轮生成出了问题" ——
    模型没问题，是我们发的 7000 token 装不进它拿到的 4096。
    """
    transport = RecordingTransport(
        [
            {
                "message": {"content": ""},
                "done_reason": "length",
                "eval_count": 1,
                "prompt_eval_count": 4095,
            }
        ]
    )
    reply_ = OllamaClient(model="m", transport=transport).chat([ChatMessage.user("在吗")])

    note = context_truncation_note(reply_)
    assert note is not None
    assert "4095" in note and "MOUXUE_LLM_NUM_CTX" in note


def test_a_normal_length_stop_is_not_blamed_on_the_context_window() -> None:
    """反面：真的生成到了上限时不要甩锅给上下文窗口。

    只测"能识别截断"会让一个"只要 done_reason=length 就报截断"的实现在
    正常的长回答上也误报 —— 那会把用户支去调一个没问题的参数。
    """
    transport = RecordingTransport(
        [
            {
                "message": {"content": "很长的一段回答"},
                "done_reason": "length",
                "eval_count": 512,
                "prompt_eval_count": 3000,
            }
        ]
    )
    reply_ = OllamaClient(model="m", transport=transport).chat([ChatMessage.user("在吗")])
    assert context_truncation_note(reply_) is None
    stopped = LLMReply(content="", meta={"done_reason": "stop", "eval_count": 0})
    assert context_truncation_note(stopped) is None


def test_transport_failure_becomes_an_llm_error_naming_the_service() -> None:
    """连不上是最常见的运行期失败；错误信息要直接说清"服务没起"。"""

    def boom(url: str, payload: dict, timeout: float) -> dict:
        raise LLMError(f"连不上 LLM 服务（{url}）：Connection refused")

    with pytest.raises(LLMError, match="连不上"):
        OllamaClient(model="m", transport=boom).chat([])


def test_client_does_not_retry_on_its_own() -> None:
    """客户端不自动重试 —— 重试的决定权在循环手里。

    偷偷重试会让「一次 LLM 调用」这个计数不可解释，而循环的步数上限
    正是靠它算的。
    """
    calls = {"n": 0}

    def flaky(url: str, payload: dict, timeout: float) -> dict:
        calls["n"] += 1
        raise LLMError("boom")

    with pytest.raises(LLMError):
        OllamaClient(model="m", transport=flaky).chat([])
    assert calls["n"] == 1


# --------------------------------------------------------------------------- #
# 流式：NDJSON 分帧、收官帧、以及"半句话不算回复"
# --------------------------------------------------------------------------- #


class RecordingStream:
    """记录请求 + 按预设逐帧回放的假**流式**传输层。"""

    def __init__(self, frames: list[dict]) -> None:
        self._frames = list(frames)
        self.requests: list[tuple[str, dict, float]] = []

    def __call__(self, url: str, payload: dict, timeout: float):
        self.requests.append((url, payload, timeout))
        yield from self._frames

    @property
    def payload(self) -> dict:
        return self.requests[0][1]


def _client(stream: RecordingStream) -> OllamaClient:
    return OllamaClient(model="test-model", stream_transport=stream)


def test_stream_forwards_text_deltas_and_ends_with_the_full_reply() -> None:
    """收官帧携带的就是**非流式下的那个回复** —— 判定只用它。"""
    stream = RecordingStream(
        [
            {"message": {"content": "第一步"}, "done": False},
            {"message": {"content": "太多了。"}, "done": False},
            _ollama_reply("第一步太多了。", eval_count=12, done_reason="stop"),
        ]
    )
    frames = list(_client(stream).stream([ChatMessage.user("在吗")]))

    assert [frame.text for frame in frames[:-1]] == ["第一步", "太多了。"]
    assert frames[-1].is_final
    assert frames[-1].reply is not None
    assert frames[-1].reply.content == "第一步太多了。"
    assert frames[-1].reply.meta["eval_count"] == 12


def test_stream_asks_for_streaming_and_keeps_the_explicit_context_window() -> None:
    """**两个方法共用一份请求构造** —— 这条测试盯的就是"别只给一条路设参数"。

    `num_ctx` 必须显式给：一次真实运行里 Ollama 自己选了 4096，
    于是 7000 token 的请求被截断，表现成"模型答了个空话"。
    若只有非流式那条路设了它，流式这条路会**只在界面里**复现那个缺陷。
    """
    stream = RecordingStream([_ollama_reply("好")])
    schema = ToolSchema(name="write_stage", description="写阶段", parameters={})
    list(_client(stream).stream([ChatMessage.user("在吗")], tools=[schema]))

    assert stream.payload["stream"] is True
    assert stream.payload["options"]["num_ctx"] == 16384
    assert stream.payload["tools"][0]["function"]["name"] == "write_stage"


def test_the_non_streaming_path_still_says_stream_false() -> None:
    """反面：共用构造点之后，非流式那条路不能被顺手改成流式。"""
    transport = RecordingTransport([_ollama_reply("好")])
    OllamaClient(model="m", transport=transport).chat([ChatMessage.user("在吗")])
    assert transport.payloads[0]["stream"] is False


def test_thinking_arrives_on_its_own_field() -> None:
    """思考与正文分开走 —— 合并它们等于把推理当成回答。"""
    stream = RecordingStream(
        [
            {"message": {"thinking": "他问的是第一步"}, "done": False},
            {"message": {"content": "先别加量。"}, "done": False},
            _ollama_reply("先别加量。"),
        ]
    )
    frames = list(_client(stream).stream([ChatMessage.user("在吗")]))

    assert frames[0].thinking == "他问的是第一步"
    assert frames[0].text == ""
    assert frames[1].text == "先别加量。"
    assert frames[1].thinking == ""


def test_the_final_frame_must_be_completed_with_the_deltas() -> None:
    """**收官帧里没有的东西，要从增量里补回来。**

    这不是"更完整一点"的改进，而是一条真实运行抓出来的缺陷的形状：

    Ollama 的收官帧常常是空的（只有 `done: true` 与计数）。一次真实运行里
    它返回 `done_reason=stop`、`eval_count=197`、`content=""` ——
    197 个 token 全在 `message.thinking` 的**增量帧**里。
    只解析收官帧会得到 `thinking=""`，于是循环找不到"只想了没说"那条解释，
    端出另一句「本地模型这一轮生成出了问题」：**一句错的解释**。

    所以这条测试既钉住"补齐"，也钉住**补齐之后能走对那条解释**
    —— 后者才是用户看得见的东西。
    """
    from app.agent.llm import thinking_without_answer_note

    stream = RecordingStream(
        [
            {"message": {"thinking": "用户反馈"}, "done": False},
            {"message": {"thinking": "第一步太重了"}, "done": False},
            # ← 真实的收官帧：message 是空的
            {"model": "test-model", "message": {"role": "assistant", "content": ""},
             "done": True, "done_reason": "stop", "eval_count": 197,
             "prompt_eval_count": 7170},
        ]
    )
    frames = list(_client(stream).stream([ChatMessage.user("太多了")]))
    reply_ = frames[-1].reply
    assert reply_ is not None
    assert reply_.content == ""
    assert reply_.thinking == "用户反馈第一步太重了"
    assert reply_.meta["eval_count"] == 197

    note = thinking_without_answer_note(reply_)
    assert note is not None and "只" in note, (
        "补齐之前这里会返回 None，循环就会端出「生成出了问题」那句错的解释"
    )


def test_the_final_frame_wins_where_it_has_content() -> None:
    """反面：收官帧自己带的正文是权威的，不能被增量拼出来的东西覆盖。

    一个"总是用增量"的实现会在这里把回答写成两遍（服务端在收官帧里
    重复了完整正文时）—— 而回答被写两遍，用户会以为模型真的说了两遍。
    """
    stream = RecordingStream(
        [
            {"message": {"content": "前半"}, "done": False},
            {"message": {"content": "后半"}, "done": False},
            _ollama_reply("前半后半（完整正文）"),
        ]
    )
    frames = list(_client(stream).stream([ChatMessage.user("在吗")]))
    assert frames[-1].reply is not None
    assert frames[-1].reply.content == "前半后半（完整正文）"


def test_a_stream_that_never_finishes_is_an_error_not_a_half_answer() -> None:
    """**半句话不能被当成回复。**

    连接在生成中途断掉时，客户端手里有半句读起来很通顺的话。
    把它当回复，用户会以为模型就是这么说的 —— 而实际上后面还有内容。
    """
    stream = RecordingStream(
        [
            {"message": {"content": "我认为第"}, "done": False},
            {"message": {"content": "一步太多"}, "done": False},
        ]
    )
    with pytest.raises(LLMError, match="没有收官帧"):
        list(_client(stream).stream([ChatMessage.user("在吗")]))


def test_an_error_frame_becomes_an_llm_error() -> None:
    stream = RecordingStream([{"error": "model 'x' not found"}])
    with pytest.raises(LLMError, match="model 'x' not found"):
        list(_client(stream).stream([ChatMessage.user("在吗")]))


def test_empty_frames_are_skipped_not_emitted_as_empty_deltas() -> None:
    """Ollama 会发只有元数据的帧（例如加载进度）。

    把它们变成空增量会让界面收到一串"什么都没变"的事件，
    而"事件多"在界面上表现为回答在抖。
    """
    stream = RecordingStream(
        [
            {"message": {"content": ""}, "done": False},
            {"message": {"content": "好"}, "done": False},
            _ollama_reply("好"),
        ]
    )
    frames = list(_client(stream).stream([ChatMessage.user("在吗")]))
    assert [frame.text for frame in frames if not frame.is_final] == ["好"]


def test_a_stream_transport_failure_becomes_an_llm_error() -> None:
    def boom(url: str, payload: dict, timeout: float):
        raise LLMError(f"连不上 LLM 服务（{url}）：Connection refused")
        yield  # pragma: no cover - 让它成为一个生成器

    with pytest.raises(LLMError, match="连不上"):
        list(
            OllamaClient(model="m", stream_transport=boom).stream(
                [ChatMessage.user("在吗")]
            )
        )


def test_fake_llm_streams_the_same_script_it_would_have_answered() -> None:
    """替身的两条路**消耗同一个脚本** —— 否则测试与生产会各走一套账。

    这也是为什么 `FakeLLM.stream` 内部调用 `self.chat`：
    "脚本用完了"那条保护自动对两条路都生效。
    """
    llm = FakeLLM([reply("一"), reply("二")], chunk_size=1)
    assert [frame.text for frame in llm.stream([]) if not frame.is_final] == ["一"]
    assert llm.remaining == 1
    assert [frame.text for frame in llm.stream([]) if not frame.is_final] == ["二"]
    assert llm.remaining == 0
    with pytest.raises(LLMError, match="多走了一步"):
        list(llm.stream([]))


def test_fake_llm_stream_records_the_deltas_it_emitted() -> None:
    """SSE 那条路的测试必须能断言"真的分了帧"。

    若替身永远只发一帧，解析器的分帧逻辑就从来没被走过 ——
    而"从来没被走过"与"是对的"在测试报告里长得一模一样。
    """
    llm = FakeLLM([reply("四个字")], chunk_size=2)
    list(llm.stream([]))
    assert [frame.text for frame in llm.deltas[0] if not frame.is_final] == ["四个", "字"]


def test_fake_llm_sends_the_whole_answer_in_one_frame_when_asked_to() -> None:
    """默认整段一帧：让"只关心结果"的测试读起来干净。"""
    llm = FakeLLM([reply("四个字")])
    list(llm.stream([]))
    assert [frame.text for frame in llm.deltas[0] if not frame.is_final] == ["四个字"]


def test_stream_keeps_the_tool_call_that_arrived_before_the_final_frame() -> None:
    """**流式也要能拿到工具调用** —— 这一条是丢过东西之后补的。

    帧序列不是我编的理想形态，而是**实测抄下来的**
    （`var/probe_stream_frames.py`，qwen3.5:9b）：工具调用在 `done: false`
    的一帧上给全（参数是对象），紧随其后的收官帧 `message` 是**空的**。

    补这条之前，这个序列会被解析成"既没说话也没调工具"，
    而循环据此报出的解释是"模型只想了没说" —— **一句由我们自己的 bug
    造出来、却指向模型的假解释**。
    """
    stream = RecordingStream(
        [
            {"message": {"content": "", "thinking": "该调 ping"}, "done": False},
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_jqz01xvo",
                            "function": {
                                "index": 0,
                                "name": "ping",
                                "arguments": {"n": 1},
                            },
                        }
                    ],
                },
                "done": False,
            },
            {"message": {"content": ""}, "done": True, "eval_count": 4},
        ]
    )
    frames = list(_client(stream).stream([ChatMessage.user("调用 ping")]))

    reply = frames[-1].reply
    assert reply is not None
    assert [call.name for call in reply.tool_calls] == ["ping"], (
        "流式丢掉了工具调用 —— 那经界面的讨论永远改不动任何东西"
    )
    assert reply.tool_calls[0].arguments == {"n": 1}
    assert reply.thinking == "该调 ping", "思考增量也不许因为补工具调用而丢"


def test_stream_does_not_run_the_same_call_twice() -> None:
    """同一个 `index` 的重复帧**覆盖**、不追加。

    追加的后果是同一句"把第 1 步拆成两天"被执行两遍 ——
    看起来像模型说重复了，实际是我们收重了。
    """
    item = {
        "message": {
            "tool_calls": [
                {"function": {"index": 0, "name": "ping", "arguments": {"n": 1}}}
            ]
        },
        "done": False,
    }
    stream = RecordingStream([item, item, {"message": {}, "done": True}])
    frames = list(_client(stream).stream([ChatMessage.user("调用 ping")]))

    assert len(frames[-1].reply.tool_calls) == 1


def test_stream_keeps_parallel_calls_in_their_own_order() -> None:
    """并行调用落在多帧上时按 `index` 排序 —— 顺序是执行顺序。"""
    stream = RecordingStream(
        [
            {
                "message": {
                    "tool_calls": [
                        {"function": {"index": 1, "name": "second", "arguments": {}}}
                    ]
                },
                "done": False,
            },
            {
                "message": {
                    "tool_calls": [
                        {"function": {"index": 0, "name": "first", "arguments": {}}}
                    ]
                },
                "done": False,
            },
            {"message": {}, "done": True},
        ]
    )
    frames = list(_client(stream).stream([ChatMessage.user("两个都调")]))

    assert [call.name for call in frames[-1].reply.tool_calls] == ["first", "second"]


def test_stream_and_chat_agree_on_the_same_frames() -> None:
    """**同一个回复，两条路给出一致的结果**（`LLMClient` 契约里那句话）。

    非流式那一次是"完整的一帧"，流式那一次是"分片 + 收官帧为空"。
    两者必须给出同样的 `content` / `thinking` / `tool_calls`。
    """
    call_frame = {
        "function": {"index": 0, "name": "ping", "arguments": {"n": 7}}
    }
    streamed = RecordingStream(
        [
            {"message": {"content": "好", "thinking": "想"}, "done": False},
            {"message": {"tool_calls": [call_frame]}, "done": False},
            {"message": {}, "done": True, "eval_count": 9},
        ]
    )
    once = RecordingTransport(
        [
            {
                "message": {
                    "content": "好",
                    "thinking": "想",
                    "tool_calls": [call_frame],
                },
                "done": True,
                "eval_count": 9,
            }
        ]
    )

    streamed_reply = list(_client(streamed).stream([ChatMessage.user("x")]))[-1].reply
    once_reply = OllamaClient(model="test-model", transport=once).chat(
        [ChatMessage.user("x")]
    )

    assert streamed_reply is not None
    assert streamed_reply.content == once_reply.content
    assert streamed_reply.thinking == once_reply.thinking
    assert [call.name for call in streamed_reply.tool_calls] == [
        call.name for call in once_reply.tool_calls
    ]
    assert streamed_reply.tool_calls[0].arguments == once_reply.tool_calls[0].arguments
