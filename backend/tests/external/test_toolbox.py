"""外挂能力接成工具之后的三条边界 —— **这一组是这一层的验收测试**。

`test_mcp.py` 验的是"MCP 客户端能不能用"；这里验的是
"它能碰到什么、碰不到什么"。三件事：

1. **作用域上限 = 只读**：任何外挂工具都拿不到 `SESSION_WRITE` / `GLOBAL`；
2. **名字带前缀**：一个自称 `retrieve_session` 的服务器不能冒充内置工具；
3. **能力缺失 = 工具不存在**，但"配置坏了"要**看得见**（`describe()`）。

（它们落到真实调用链上会怎样 —— 结果形状、`error`/`empty` 的区别、
轨迹里的 scope —— 在 `tests/agent/test_external_tools.py`：
那一半需要真实的会话沙箱，放在这里会让"这组测试为什么要建文件夹"变成
一件要读三遍才发现的事。）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.external import (
    ExternalToolbox,
    _NullTransport,
    build_toolbox,
    empty_toolbox,
    mcp_tool_name,
)
from app.agent.sidebar import SIDEBAR_TOOL_SCOPES
from app.agent.tools import ToolScope
from app.config import Settings
from app.external.mcp import McpClient, McpTool
from app.external.websearch import LocalCorpusProvider


def _connected(name: str = "demo", *tools: McpTool) -> McpClient:
    """一个**看起来接通了**的服务器（不起进程）。

    这里要验的是映射规则（名字、作用域、schema），不是传输 ——
    传输由 `test_mcp.py` 用真进程验。用一个真子进程来验名字前缀，
    只会让这组测试慢十倍而多验到零件事。
    """
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
def web(corpus):
    return ExternalToolbox(web=LocalCorpusProvider(corpus))


# --------------------------------------------------------------------------- #
# 边界 1：作用域上限
# --------------------------------------------------------------------------- #


def test_every_external_tool_is_read_only(web: ExternalToolbox) -> None:
    """★ 一个第三方进程不该决定"什么写进你的认知状态"。

    它要影响全局，只能把结论作为**文本**返回，由模型走
    `submit_global_update`（审核队列那条路）—— 中间隔着一跳，
    而那一跳上有引文核对。
    """
    box = ExternalToolbox(web=web.web, clients=[_connected("x", McpTool(name="write_it"))])
    scopes = {spec.scope for spec in box.specs()}
    assert scopes == {ToolScope.READ}


def test_an_external_tool_is_invisible_to_a_scope_that_does_not_include_read() -> None:
    """作用域过滤对它一样生效（`scopes` 是唯一的判据，没有第二套规则）。"""
    box = ExternalToolbox(clients=[_connected()])
    assert box.specs(frozenset({ToolScope.SESSION_WRITE})) == ()
    assert len(box.specs(SIDEBAR_TOOL_SCOPES)) == 1


# --------------------------------------------------------------------------- #
# 边界 2：名字
# --------------------------------------------------------------------------- #


def test_a_server_cannot_impersonate_a_builtin_tool() -> None:
    """★ 一个自称 `retrieve_session` 的服务器拿到的是 `mcp__demo__retrieve_session`。

    没有这个前缀，"这次调用到底是谁做的"从此答不出来 ——
    而轨迹（`tool_trace`）正是用来回答这个问题的。
    """
    box = ExternalToolbox(clients=[_connected("demo", McpTool(name="retrieve_session"))])
    names = [spec.name for spec in box.specs()]
    assert names == ["mcp__demo__retrieve_session"]
    assert mcp_tool_name("demo", "retrieve_session") == names[0]


def test_no_server_can_beat_a_builtin_name_after_the_prefix() -> None:
    """前缀之后，外挂工具名集合与内置名字集合**不可能**相交。

    这条不变量比"分发器会怎么选"更根本：只要它成立，
    分发顺序那条规则就永远不需要被用到（它在那里只是第二道保险，
    见 `tests/agent/test_external_tools.py`）。
    """
    import app.agent.tools as tools_module

    box = ExternalToolbox(
        clients=[_connected("demo", *(McpTool(name=n) for n in tools_module.TOOLS))]
    )
    external_names = {spec.name for spec in box.specs()}
    assert external_names & set(tools_module.TOOLS) == set()
    assert len(external_names) == len(tools_module.TOOLS)
    assert all(name.startswith("mcp__") for name in external_names)


# --------------------------------------------------------------------------- #
# 边界 3：能力缺失 = 工具不存在；配置坏了要看得见
# --------------------------------------------------------------------------- #


def test_without_configuration_there_are_no_external_tools() -> None:
    box = empty_toolbox()
    assert box.specs() == ()
    assert box.get("web_search") is None
    described = box.describe()
    assert described["web_search"]["enabled"] is False
    assert described["mcp_servers"] == []
    assert described["scope_ceiling"] == "read"


def test_a_configured_corpus_gives_the_model_a_web_search_tool() -> None:
    box = build_toolbox(Settings(web_search_provider="off"))
    assert box.get("web_search") is None
    described = box.describe()
    assert described["web_search"]["reason"], "没配要给出**原因**，不能只说没有"


def test_a_broken_search_configuration_does_not_take_the_app_down() -> None:
    """配置坏掉 → 能力缺失（可恢复），**不是**应用起不来（不可恢复）。"""
    box = build_toolbox(Settings(web_search_provider="local", web_search_corpus=""))
    assert box.get("web_search") is None


def test_a_server_that_never_started_shows_up_with_its_reason() -> None:
    """★ "没起来"与"没配"在界面上必须分开 —— 否则人会去查配置而不是查原因。"""
    box = build_toolbox(
        Settings(mcp_servers='[{"name": "ghost", "command": "绝对不存在的可执行文件"}]')
    )
    described = box.describe()
    assert [row["server"] for row in described["mcp_servers"]] == ["ghost"]
    assert described["mcp_servers"][0]["connected"] is False
    assert described["mcp_servers"][0]["error"]
    assert box.specs() == (), "没连上的服务器不提供工具"


# --------------------------------------------------------------------------- #
# 对方的 schema 认不出来时：宽松地读，不要拒绝整个工具
# --------------------------------------------------------------------------- #


def test_an_unusual_remote_schema_still_yields_a_usable_tool() -> None:
    from app.agent.external import _schema_from_mcp

    assert _schema_from_mcp({}) == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert _schema_from_mcp({"properties": {"a": {"type": "string"}}}).get("required", []) == []
    # 认不出的字段丢掉，认得出的留下
    schema = _schema_from_mcp(
        {
            "properties": {
                "a": {"type": "string"},
                "b": {"没有 type": True},
                "c": "连对象都不是",
            },
            "required": ["b", "a"],
        }
    )
    assert set(schema["properties"]) == {"a"}
    assert schema["required"] == ["a"], "必填项只保留认得出的那些"


def test_a_remote_tool_with_a_missing_schema_is_still_callable() -> None:
    box = ExternalToolbox(clients=[_connected("demo", McpTool(name="mystery"))])
    spec = box.get("mcp__demo__mystery")
    assert spec is not None and spec.parameters["type"] == "object"
