"""侧边栏的工具作用域边界（P6+.6）。

## 为什么这个文件单独存在

`tests/api/test_sidebar_api.py` 验的是**行为**（调了会被拒、边界不被绕过）。
这里验的是**定义**：哪些工具属于哪一档，以及**谁依赖这些名单**。

其中最关键的一条是给 `e2echeck` 的手写名单兜底 ——
那个验证器故意不 import 被测代码（否则它的检查是恒真的），
代价是名单会腐烂。一条单测把两个来源钉在一起，两边的好处都要。
"""

from __future__ import annotations

from app.agent.tools import (
    SIDEBAR_TOOL_SCOPES,
    ToolScope,
    iter_tools,
    tool_schemas,
)


def _names(*scopes: ToolScope) -> set[str]:
    wanted = set(scopes)
    return {spec.name for spec in iter_tools() if spec.scope in wanted}


def test_the_two_session_scopes_are_exactly_the_session_bound_tools() -> None:
    """`SESSION_READ | SESSION_WRITE` 就是"需要会话的工具"这个整体。"""
    assert _names(ToolScope.SESSION_READ) == {"retrieve_session", "read_attachment"}
    assert _names(ToolScope.SESSION_WRITE) == {
        "ask_about_task",
        "create_task_plan",
        "update_task_definition",
        "update_task_plan",
        "propose_plan_change",
        "mark_step_done",
        "checkpoint_milestone",
        "update_motivation",
        "write_chat_note",
        "write_stage",
    }


def test_the_sidebar_scopes_are_the_complement() -> None:
    """★ 侧边栏的作用域是"不需要会话的那些" —— **从全集补出来的**。

    这条断言的意思是：`SIDEBAR_TOOL_SCOPES` 不是一份可以单独编辑的名单。
    想给侧边栏加一个工具，唯一的做法是给那个工具换一个不属于
    `SESSION_READ|SESSION_WRITE` 的作用域 —— 而那正是应该发生的讨论。
    """
    session_scopes = {ToolScope.SESSION_READ, ToolScope.SESSION_WRITE}
    assert SIDEBAR_TOOL_SCOPES == set(ToolScope) - session_scopes
    assert not (SIDEBAR_TOOL_SCOPES & session_scopes)


def test_no_tool_escapes_the_two_categories() -> None:
    """每个工具要么需要会话，要么不需要。**没有第三种**。

    一个新增的、既不属于侧边栏也不属于会话的档位会让上面那条补集断言
    悄悄失效（补出来的集合会包含它）。
    """
    for spec in iter_tools():
        assert spec.scope in SIDEBAR_TOOL_SCOPES or spec.scope in {
            ToolScope.SESSION_READ,
            ToolScope.SESSION_WRITE,
        }, f"{spec.name} 的作用域 {spec.scope} 不在任何一档里"


def test_the_e2echeck_session_tool_list_is_exact() -> None:
    """★ 钉住 `e2echeck._SESSION_ONLY_TOOLS` 这份手写名单。

    那个验证器**故意不 import 被测代码**：若它直接读 `ToolScope`，
    它的检查就退化成"集合等于它自己"，一个恒真的断言。
    代价是手写名单会腐烂 —— 第一版就写错了 6 个名字（有编造的、有漏掉真的），
    而**一份会腐烂的手写名单比没有更糟**：真工具泄漏时它照样通过。

    这条测试就是那个代价的价钱：两个来源必须逐字一致。
    于是 e2echeck 里那句断言仍然是一次**独立的**检查，
    而它不可能悄悄与真实注册表脱节。
    """
    from tools.e2echeck import _SESSION_ONLY_TOOLS

    actual = _names(ToolScope.SESSION_READ, ToolScope.SESSION_WRITE)
    assert set(_SESSION_ONLY_TOOLS) == actual, (
        "e2echeck 里的会话工具名单与真实注册表不一致 —— "
        "要么新增/删除了会话工具，要么那份名单写错了。"
        f"只在 e2echeck 里：{sorted(set(_SESSION_ONLY_TOOLS) - actual)}；"
        f"只在注册表里：{sorted(actual - set(_SESSION_ONLY_TOOLS))}"
    )


def test_the_sidebar_schemas_never_include_a_session_tool() -> None:
    """交给模型的清单里一个会话工具都没有 —— 端到端再确认一次。"""
    names = {spec.name for spec in tool_schemas(SIDEBAR_TOOL_SCOPES)}
    assert names == {
        "retrieve_folder",
        "retrieve_cognitive",
        "retrieve_strategy",
        "submit_global_update",
    }
    assert not (names & _names(ToolScope.SESSION_READ, ToolScope.SESSION_WRITE))


def test_the_session_schemas_are_still_complete() -> None:
    """★ 反向：给会话的清单**一个都不能少** —— 别忘了 `scopes=None` 的含义。"""
    assert len(tool_schemas()) == len(list(iter_tools()))
    assert {spec.name for spec in tool_schemas()} == {
        spec.name for spec in iter_tools()
    }
