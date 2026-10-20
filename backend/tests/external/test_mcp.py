"""MCP 客户端：真实的子进程，真实的 JSON-RPC。

这一组**故意用真进程**（`tools/fake_mcp_server.py`）而不是内存假传输：
传输层要证明的三件事（按行读、超时、对方退出）全都只在真进程上才存在。
用一个"什么都会乖乖回答"的内存替身测这三件事，测出来的是替身的行为。

（工具名映射、作用域上限、能力缺失那些**纯逻辑**，见 `test_toolbox.py`。）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.external.mcp import (
    McpClient,
    McpError,
    McpTool,
    McpUnavailable,
    StdioTransport,
    load_servers,
    parse_tools,
)

BACKEND = Path(__file__).resolve().parents[2]


def _spawn(name: str = "demo", *, timeout: float = 20.0) -> McpClient:
    client = McpClient.spawn(
        name,
        f'"{sys.executable}" -m tools.fake_mcp_server',
        cwd=str(BACKEND),
        timeout=timeout,
    )
    client.connect()
    return client


@pytest.fixture()
def client():
    client = _spawn()
    try:
        yield client
    finally:
        client.close()


def test_a_real_server_connects_and_lists_its_tools(client: McpClient) -> None:
    assert client.connected, client.error
    assert {tool.name for tool in client.tools} == {"echo", "boom", "slow"}
    echo = next(tool for tool in client.tools if tool.name == "echo")
    assert echo.input_schema["properties"]["text"]["type"] == "string"


def test_a_call_comes_back_with_the_text(client: McpClient) -> None:
    ok, text, raw = client.call("echo", {"text": "你好", "loud": True})
    assert ok, text
    assert text == "你好".upper()
    assert raw["content"][0]["type"] == "text"


def test_a_tool_that_reports_an_error_is_not_an_exception(client: McpClient) -> None:
    """★ 对方说失败 → `ok=False` + **它的话**（正文不丢）。"""
    ok, text, _ = client.call("boom", {})
    assert not ok
    assert "我不干。" in text


def test_an_unknown_tool_is_reported_by_the_server(client: McpClient) -> None:
    ok, text, _ = client.call("没有这个", {})
    assert not ok and "没有 没有这个 这个工具" in text


def test_a_slow_server_times_out_instead_of_hanging_the_discussion() -> None:
    """★ 一个卡住的服务器不许把整个讨论挂在那里。"""
    client = _spawn(timeout=0.6)
    try:
        ok, text, _ = client.call("slow", {"seconds": 5})
        assert not ok
        assert "没有回应" in text
    finally:
        client.close()


def test_a_server_that_never_started_reports_why() -> None:
    """★ "没起来"与"没有工具"必须分开 —— 否则人会去查配置而不是查原因。"""
    client = McpClient.spawn("ghost", f'"{sys.executable}" -m 不存在的模块名')
    client.connect()
    assert not client.connected
    assert client.error
    assert client.tools == []
    client.close()


def test_the_transport_ignores_non_json_lines_on_stdout() -> None:
    """真服务器常往 stdout 打日志；一行日志不该让这个服务器"不能用"。"""
    transport = StdioTransport(
        sys.executable,
        ["-c", "import sys;print('这是一行日志');print('{');sys.stdout.flush()"],
        timeout=1.0,
    )
    try:
        with pytest.raises(McpUnavailable):
            transport.request("initialize")
    finally:
        transport.close()


# --------------------------------------------------------------------------- #
# 纯解析
# --------------------------------------------------------------------------- #


def test_parse_tools_keeps_the_schema_verbatim() -> None:
    tools = parse_tools(
        {
            "tools": [
                {
                    "name": "read",
                    "description": "读",
                    "inputSchema": {"type": "object", "properties": {"p": {"type": "string"}}},
                }
            ]
        }
    )
    assert tools == [
        McpTool(
            name="read",
            description="读",
            input_schema={"type": "object", "properties": {"p": {"type": "string"}}},
        )
    ]


def test_parse_tools_refuses_a_wrong_shape() -> None:
    with pytest.raises(McpUnavailable, match="tools"):
        parse_tools({"不是": "数组"})


def test_a_server_row_without_a_name_is_skipped() -> None:
    assert parse_tools({"tools": [{"description": "没有名字"}]}) == []


def test_servers_can_be_configured_as_json_or_as_lines() -> None:
    assert load_servers('') == []
    assert load_servers('[{"name":"a","command":"python -m x"}]') == [
        {"name": "a", "command": "python -m x", "cwd": ""}
    ]
    assert load_servers("a=python -m x\n# 注释\nb=python -m y\n") == [
        {"name": "a", "command": "python -m x", "cwd": ""},
        {"name": "b", "command": "python -m y", "cwd": ""},
    ]


def test_bad_configuration_fails_loudly() -> None:
    """★ 配置写错了要立刻说 —— 跳过那一行的后果是"服务器没插上"，而人会去查服务器。"""
    with pytest.raises(McpUnavailable):
        load_servers("这一行没有等号")
    with pytest.raises(McpUnavailable):
        load_servers('[{"name": "少了 command"}]')
    with pytest.raises(McpUnavailable):
        load_servers("{ 坏掉的 json")
    with pytest.raises(McpUnavailable):
        load_servers("a=")
    with pytest.raises(McpUnavailable):
        load_servers("=python -m x")
