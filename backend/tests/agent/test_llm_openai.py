"""`OpenAICompatClient` —— 商用 API 那条路。

这个协议的细节**不是照文档猜的**，而是从三处会静默出错的地方反推出来的：

1. `arguments` 是 **JSON 字符串**（Ollama 给对象）；
2. 流式是 **SSE**（`data: {...}` + `data: [DONE]`），不是 NDJSON；
3. 流式会把 `arguments` **切碎了逐帧发**，必须按 `index` 拼起来再解析。

第 3 条是最贵的一条：忘了拼，得到的报错是"参数不是合法 JSON"，
而它读起来像**模型说错了话** —— 又是一次把锅甩给模型的误诊。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent.llm import (
    ChatMessage,
    LLMError,
    OpenAICompatClient,
    ToolSchema,
)


class RecordingPost:
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self._replies = list(replies)
        self.requests: list[tuple[str, dict, float]] = []

    def __call__(self, url: str, payload: dict, timeout: float) -> dict:
        self.requests.append((url, payload, timeout))
        return self._replies.pop(0)

    @property
    def payload(self) -> dict:
        return self.requests[0][1]

    @property
    def url(self) -> str:
        return self.requests[0][0]


class RecordingSse:
    """按预设回放 SSE 帧（每个元素是**一条 data 里的对象**）。"""

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self._frames = list(frames)
        self.requests: list[tuple[str, dict, float]] = []

    def __call__(self, url: str, payload: dict, timeout: float):
        self.requests.append((url, payload, timeout))
        yield from self._frames

    @property
    def payload(self) -> dict:
        return self.requests[0][1]


def _client(*, post: RecordingPost | None = None, sse: RecordingSse | None = None):
    return OpenAICompatClient(
        model="deepseek-chat",
        base_url="https://api.example.com/v1",
        api_key="sk-secret",
        transport=post,
        stream_transport=sse,
    )


def _completion(
    *,
    content: str = "",
    calls: list[dict[str, Any]] | None = None,
    finish: str = "stop",
    usage: dict[str, Any] | None = None,
    reasoning: str = "",
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content or None}
    if calls:
        message["tool_calls"] = calls
    if reasoning:
        message["reasoning_content"] = reasoning
    return {
        "model": "deepseek-chat",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 1234, "completion_tokens": 56},
    }


def test_the_endpoint_and_the_auth_header_are_what_the_protocol_says() -> None:
    """`/chat/completions` + `Bearer` —— 两处拼错都只会表现为 404/401。"""
    seen: list[dict[str, str]] = []

    def post(url: str, payload: dict, timeout: float) -> dict:
        seen.append({"url": url})
        return _completion(content="好")

    client = OpenAICompatClient(
        model="m", base_url="https://api.example.com/v1/", api_key="sk-x", transport=post
    )
    client.chat([ChatMessage.user("在吗")])

    assert seen[0]["url"] == "https://api.example.com/v1/chat/completions", (
        "base_url 结尾的斜杠没处理 —— 会出现双斜杠，而有些网关因此 404"
    )


def test_tool_arguments_go_out_as_a_json_string() -> None:
    """**与非流式那条路相反的格式**：OpenAI 要字符串，Ollama 要对象。

    发错的后果是服务直接 400（或更糟：把对象当成字符串的一个字段），
    而现象是"模型不调工具"。
    """
    post = RecordingPost([_completion(content="好")])
    client = _client(post=post)
    schema = ToolSchema(name="write_stage", description="写阶段", parameters={"type": "object"})
    client.chat([ChatMessage.user("在吗")], tools=[schema])

    assert post.payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "write_stage",
                "description": "写阶段",
                "parameters": {"type": "object"},
            },
        }
    ], "工具声明的形状不是 OpenAI 的形状"


def test_the_assistant_tool_call_echo_uses_ids_and_string_arguments() -> None:
    """把模型上一次的工具调用回灌时，形状必须与它给的**一模一样**。

    否则第二轮请求会被服务拒绝（`tool_call_id` 对不上），
    而现象是"模型改完第一步就不肯继续了"。
    """
    post = RecordingPost([_completion(content="好")])
    from app.agent.llm import ToolCall

    call = ToolCall(call_id="call_abc", name="ping", arguments={"n": 1})
    client = _client(post=post)
    client.chat([ChatMessage.assistant("", tool_calls=(call,))])

    sent = post.payload["messages"][0]
    assert sent["tool_calls"][0]["id"] == "call_abc"
    assert sent["tool_calls"][0]["function"]["arguments"] == '{"n": 1}'


def test_a_tool_result_is_linked_by_tool_call_id() -> None:
    """工具结果靠 `tool_call_id` 对应回去（Ollama 那边用 `tool_name`）。"""
    post = RecordingPost([_completion(content="好")])
    from app.agent.llm import ToolCall

    call = ToolCall(call_id="call_abc", name="ping", arguments={})
    client = _client(post=post)
    client.chat([ChatMessage.tool_result(call, "成功")])

    sent = post.payload["messages"][0]
    assert sent["tool_call_id"] == "call_abc"
    assert "tool_name" not in sent


def test_the_real_provider_id_is_kept() -> None:
    """服务给的 `id` 必须留着 —— 回灌工具结果时要靠它对应。"""
    post = RecordingPost(
        [
            _completion(
                calls=[
                    {
                        "id": "call_real_id",
                        "type": "function",
                        "function": {"name": "ping", "arguments": '{"n": 3}'},
                    }
                ],
                finish="tool_calls",
            )
        ]
    )
    reply = _client(post=post).chat([ChatMessage.user("ping")])

    assert reply.tool_calls[0].call_id == "call_real_id", (
        "丢了服务给的 id —— 工具结果将无法对应回这次调用"
    )
    assert reply.tool_calls[0].arguments == {"n": 3}
    assert reply.meta["done_reason"] == "tool_calls"
    assert reply.meta["eval_count"] == 56


def test_a_null_content_with_tool_calls_is_normal() -> None:
    """工具调用时 `content` 是 `null` —— 那是正常的，不是错误。"""
    post = RecordingPost(
        [
            _completion(
                calls=[
                    {"id": "c1", "type": "function", "function": {"name": "ping", "arguments": "{}"}}
                ],
                finish="tool_calls",
            )
        ]
    )
    reply = _client(post=post).chat([ChatMessage.user("ping")])

    assert reply.content == ""
    assert [call.name for call in reply.tool_calls] == ["ping"]


def test_reasoning_content_is_kept_as_thinking() -> None:
    """`reasoning_content` → `thinking`：界面上要能把思考与回答分开显示。"""
    post = RecordingPost([_completion(content="答案", reasoning="我先想了想……")])
    reply = _client(post=post).chat([ChatMessage.user("问")])

    assert reply.content == "答案"
    assert reply.thinking == "我先想了想……"


def test_streaming_tool_arguments_are_stitched_across_frames() -> None:
    """**最贵的那一条**：`arguments` 跨帧分片，必须按 index 拼起来再解析。

    分三帧发 `{"st`、`ep": "A"`、`}`。忘了拼的报错是
    "参数不是合法 JSON" —— 读起来像模型说错了话。
    """
    sse = RecordingSse(
        [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_x",
                                    "function": {"name": "update_task_plan"},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"st'}}]}}
                ]
            },
            {
                "choices": [
                    {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ep": "A"}'}}]}}
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
    )
    frames = list(_client(sse=sse).stream([ChatMessage.user("改计划")]))

    reply = frames[-1].reply
    assert reply is not None
    assert [call.name for call in reply.tool_calls] == ["update_task_plan"]
    assert reply.tool_calls[0].arguments == {"step": "A"}, "分片的参数没有被拼起来"
    assert reply.tool_calls[0].call_id == "call_x"


def test_two_tool_calls_in_separate_frames_do_not_merge() -> None:
    """`index` 在 tool_call 这一层；只看 function.index 会把两个调用拼成一个。"""
    sse = RecordingSse(
        [
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_a", "function": {"name": "first", "arguments": '{"x": 1}'}}
            ]}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 1, "id": "call_b", "function": {"name": "second", "arguments": '{"y": 2}'}}
            ]}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
    )
    reply = list(_client(sse=sse).stream([ChatMessage.user("x")]))[-1].reply
    assert reply is not None
    assert [call.name for call in reply.tool_calls] == ["first", "second"]
    assert reply.tool_calls[0].arguments == {"x": 1}
    assert reply.tool_calls[1].arguments == {"y": 2}


def test_streaming_yields_text_deltas_and_ends_with_the_whole_reply() -> None:
    """文本边到边显示，收官帧给出**与非流式相同的回复**。"""
    sse = RecordingSse(
        [
            {"choices": [{"delta": {"content": "第一"}}]},
            {"choices": [{"delta": {"content": "步"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
    )
    frames = list(_client(sse=sse).stream([ChatMessage.user("问")]))

    assert [frame.text for frame in frames[:-1]] == ["第一", "步"]
    assert frames[-1].reply is not None
    assert frames[-1].reply.content == "第一步"


def test_a_stream_without_finish_reason_is_an_error_not_a_half_answer() -> None:
    """连接断在半路时**不许**把半句话当回答。"""
    sse = RecordingSse([{"choices": [{"delta": {"content": "半句"}}]}])

    with pytest.raises(LLMError) as caught:
        list(_client(sse=sse).stream([ChatMessage.user("问")]))
    assert "断" in str(caught.value) or "finish_reason" in str(caught.value)


def test_streaming_asks_for_streaming() -> None:
    """两条路共用一份请求构造，`stream` 是唯一的差别。"""
    sse = RecordingSse([{"choices": [{"delta": {}, "finish_reason": "stop"}]}])
    list(_client(sse=sse).stream([ChatMessage.user("问")]))
    assert sse.payload["stream"] is True

    post = RecordingPost([_completion(content="好")])
    _client(post=post).chat([ChatMessage.user("问")])
    assert post.payload["stream"] is False


def test_no_max_tokens_is_sent() -> None:
    """**刻意不发 `max_tokens`**：它是输出上限，与上下文窗口不是一回事。

    发一个来路不明的上限，后果是回答被静默截断 —— 现象是"模型答到一半停了"。
    """
    post = RecordingPost([_completion(content="好")])
    _client(post=post).chat([ChatMessage.user("问")])
    assert "max_tokens" not in post.payload
    assert "max_completion_tokens" not in post.payload


def test_the_api_key_never_leaks_into_meta() -> None:
    """密钥进 meta 就会进 `tool_trace`，而那是给用户看的界面。"""
    post = RecordingPost([_completion(content="好")])
    reply = _client(post=post).chat([ChatMessage.user("问")])

    assert "sk-secret" not in json.dumps(reply.meta, ensure_ascii=False)
