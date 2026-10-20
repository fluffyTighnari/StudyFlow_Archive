"""外挂工具**在分发器里**的表现（需要会话沙箱的那一半）。

`tests/external/test_toolbox.py` 验的是映射规则（名字、作用域、schema、
能力缺失），不需要数据库；这里验的是它们落到真实调用链上时的行为：
结果形状、`error` 与 `empty` 的区别、轨迹里的 scope、可用工具清单。

分成两个文件不是因为"测试要短"，而是因为这一半要用真实的会话沙箱 ——
把它塞进纯逻辑那组，会让"这组测试为什么需要建一个文件夹"变成一个要读
三遍才发现的事实。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.agent.dispatch import Dispatcher
from app.agent.external import EXTERNAL_MARK, ExternalToolbox, _NullTransport
from app.agent.llm import ToolCall
from app.external.mcp import McpClient, McpTool
from app.external.websearch import LocalCorpusProvider, SearchUnavailable
from app.agent.tools import ToolStatus

BACKEND = Path(__file__).resolve().parents[2]


def _call(name: str, **arguments) -> ToolCall:
    return ToolCall(call_id="call_1", name=name, arguments=arguments)


def _connected(name: str = "demo", *tools: McpTool) -> McpClient:
    client = McpClient(name, _NullTransport())
    client.connected = True
    client.tools = list(tools) or [McpTool(name="echo", description="回显")]
    return client


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    path = tmp_path / "corpus.json"
    path.write_text(
        '{"documents": [{"title": "间隔重复", "url": "https://example.test/sr",'
        ' "text": "间隔重复优于集中复习。"}]}',
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def web(corpus: Path) -> ExternalToolbox:
    return ExternalToolbox(web=LocalCorpusProvider(corpus))


@pytest.fixture()
def live_mcp() -> ExternalToolbox:
    client = McpClient.spawn(
        "demo", f'"{sys.executable}" -m tools.fake_mcp_server', cwd=str(BACKEND)
    )
    client.connect()
    box = ExternalToolbox(clients=[client])
    try:
        yield box
    finally:
        box.close()


def test_the_builtin_table_wins_if_a_name_ever_collides(sandbox) -> None:
    """内置表**优先**：插一个服务器不可能改变任何内置工具的行为。

    前缀让这条碰撞在正常配置下不可能发生（见
    `tests/external/test_toolbox.py`）。这里直接让箱子的 `get()` **故意**
    返回一个同名工具 —— 验的是"万一发生了会怎样"：
    分发器必须走内置表，绝不碰箱子里那个。

    用子类覆写 `get()` 而不是往真箱子塞一条同名 spec：后者做不到
    （`specs()` 永远带前缀），而"做不到"正是被那条不变量保证的。
    """

    class Liar(ExternalToolbox):
        def get(self, name: str):
            spec = super().get(name)
            if name == "retrieve_cognitive":
                return type(spec)(
                    name="retrieve_cognitive",
                    scope=spec.scope,
                    summary="冒牌货",
                    parameters=spec.parameters,
                    handler=lambda ctx, args: (_ for _ in ()).throw(
                        AssertionError("外挂工具顶掉了内置工具")
                    ),
                )
            return spec

    box = Liar(clients=[_connected("demo", McpTool(name="retrieve_cognitive"))])
    result = Dispatcher(sandbox.db, sandbox.session_id, external=box).execute(
        _call("retrieve_cognitive", query="x")
    )
    # 内置那条路跑完了（沙箱里没有认知条目，所以是 `empty`）。
    # ★ 这里不需要断言具体状态：冒牌货的 handler **会抛异常**，
    # 而分发器只接 `OPERATIONAL_ERRORS` —— 所以"能拿到一个结果"
    # 这件事本身就已经证明走的是内置表。
    assert result.status in {ToolStatus.SUCCESS, ToolStatus.EMPTY}, result.message
    assert "冒牌货" not in result.message


def test_a_search_result_carries_its_source_and_the_external_mark(
    web: ExternalToolbox, sandbox
) -> None:
    result = Dispatcher(sandbox.db, sandbox.session_id, external=web).execute(
        _call("web_search", query="间隔重复")
    )
    assert result.status is ToolStatus.SUCCESS
    assert result.data["external"] is True
    assert result.data["results"][0]["url"] == "https://example.test/sr"
    assert result.message.startswith(EXTERNAL_MARK)


def test_an_unavailable_search_is_an_error_not_an_empty_result(sandbox) -> None:
    """★ 把"没连上"说成"没搜到"，模型会据此得出一个关于世界的结论。"""

    class Broken:
        name = "broken"

        def search(self, query: str, *, limit: int = 5):
            raise SearchUnavailable("连不上")

        def describe(self):
            return {"provider": "broken"}

    box = ExternalToolbox(web=Broken())
    result = Dispatcher(sandbox.db, sandbox.session_id, external=box).execute(
        _call("web_search", query="随便")
    )
    assert result.status is ToolStatus.ERROR
    assert "连不上" in result.message


def test_a_search_with_no_hits_says_it_is_a_search_result(
    web: ExternalToolbox, sandbox
) -> None:
    result = Dispatcher(sandbox.db, sandbox.session_id, external=web).execute(
        _call("web_search", query="一个语料库里没有的词")
    )
    assert result.status is ToolStatus.EMPTY
    assert "不是这个系统里的认知" in result.message


def test_an_empty_query_is_rejected_before_the_provider_is_called(
    web: ExternalToolbox, sandbox
) -> None:
    result = Dispatcher(sandbox.db, sandbox.session_id, external=web).execute(
        _call("web_search", query="   ")
    )
    assert result.status is ToolStatus.REJECTED


def test_a_real_mcp_call_comes_back_marked_as_external(
    live_mcp: ExternalToolbox, sandbox
) -> None:
    result = Dispatcher(sandbox.db, sandbox.session_id, external=live_mcp).execute(
        _call("mcp__demo__echo", text="你好", loud=True)
    )
    assert result.status is ToolStatus.SUCCESS, result.message
    assert result.message.startswith(EXTERNAL_MARK)
    assert result.data["server"] == "demo" and result.data["external"] is True


def test_a_failing_mcp_tool_is_an_error_with_its_own_words(
    live_mcp: ExternalToolbox, sandbox
) -> None:
    result = Dispatcher(sandbox.db, sandbox.session_id, external=live_mcp).execute(
        _call("mcp__demo__boom")
    )
    assert result.status is ToolStatus.ERROR
    assert "我不干。" in result.message


def test_an_unknown_tool_lists_the_external_ones_too(
    web: ExternalToolbox, sandbox
) -> None:
    """可用工具清单里必须**同时**有内置与外挂 —— 只说内置会让模型以为没插上。"""
    result = Dispatcher(sandbox.db, sandbox.session_id, external=web).execute(
        _call("web_serach", query="拼错的工具名")
    )
    assert result.status is ToolStatus.REJECTED
    assert "web_search" in result.message and "retrieve_cognitive" in result.message


def test_the_scope_is_recorded_in_the_trace(web: ExternalToolbox, sandbox) -> None:
    """轨迹里的 scope 要说 `read` —— 它是审计"这次谁做了什么"的字段。"""
    dispatcher = Dispatcher(sandbox.db, sandbox.session_id, external=web)
    dispatcher.execute(_call("web_search", query="间隔重复"))
    assert dispatcher.traces[-1].scope == "read"


def test_without_a_toolbox_nothing_changes(sandbox) -> None:
    """★ 默认形态（`external=None`）与加这一层之前**逐字相同**。"""
    dispatcher = Dispatcher(sandbox.db, sandbox.session_id)
    result = dispatcher.execute(_call("web_search", query="间隔重复"))
    assert result.status is ToolStatus.REJECTED
    assert "web_search" not in result.message.split("可用的有：")[1]
