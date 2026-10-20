"""工具调用的执行器：**校验参数、强制作用域、留下轨迹**。

## 这一层存在的理由只有一个：模型的输出是**输入**，不是指令

模型产出的 `ToolCall` 是一个来路不明的请求。它可能：

- 报一个不存在的工具名（它记错了，或者它见过的工具比我们多）；
- 参数类型不对、少必填、多带了一个字段；
- 试图调用一个它没有权限的动作。

这三件事都不该让进程崩溃、也不该悄悄过去。它们的正确归宿是
**一条可读的 `rejected` + 一句人话**：模型看到之后能自我纠正，
而人看到之后知道发生了什么。

## 为什么参数校验要自己写

项目里没有 `jsonschema` 依赖，而这一层要校验的 schema 是**我们自己发出去的**
（`tools.py` 里那几种子集：type / properties / required / enum / items /
additionalProperties）。用一个通用库来校验自己造的方言，收益不抵一个新依赖。

**不做任何隐式类型转换**：`"8"` 不会变成 `8`。任何隐式转换都是系统在替模型
猜它的意思，而猜错的后果（写进去一个错的值）比多走一步（模型看到
「max_items 要是整数」再试一次）大得多。这条与项目的整体姿态一致：
宁可拒绝得响亮，不要接受得含糊。

## `global` 作用域的执行不在这个文件里

它交给 `app.agent.handoff` —— 全项目唯一允许 import `app.router` 的 Agent 模块
（ARCH-12）。本文件调它，因此本文件**不需要**知道 Router 长什么样。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.agent.external import ExternalToolbox
from app.agent.llm import ToolCall
from app.agent.tools import ToolContext, ToolResult, ToolScope, ToolStatus, get_tool
from app.core.errors import (
    InvalidField,
    MissingPrerequisite,
    NotFound,
    StateNotWritable,
    ValidationRejected,
    VersionConflict,
)
from app.session_domain import reads as session_reads

#: 「可预期的运行期状态」—— 这些是**可以被说出来**的失败。
#:
#: 刻意**不包含** `CapabilityError` 与 `InvariantViolation`，也没有笼统地写
#: `DomainError`：
#:
#: - `CapabilityError` 意味着有代码在没拿到令牌的情况下试图写全局 —— 那是缺陷；
#: - `InvariantViolation` 意味着系统自己的状态已经不一致了 —— 那更是缺陷。
#:
#: 把它们变成一句 `rejected: 这次操作不被允许`，等于让一个真实的缺陷
#: 以一个**听起来很合理的拒绝**继续活下去。而这正是本项目反复栽的那一类：
#: 「凡是系统用一个理由解释自己的行为的地方，都要检查那个理由是不是真的」。
OPERATIONAL_ERRORS = (
    StateNotWritable,
    MissingPrerequisite,
    NotFound,
    VersionConflict,
    InvalidField,
    ValidationRejected,
)


@dataclass(frozen=True)
class ToolTraceEntry:
    """轨迹里的一行 —— **用户要能看到「它做过什么」**。

    轨迹是给两拨人看的：用户（"它刚才查了什么、写了什么"）和三个月后的开发者
    （"上次为什么这么答"）。因此它记的是**发生过的事**，不是模型的推理：
    工具名、参数、结果状态、耗时。理由写在 `chat_message` 的正文里，
    而"它真的动过哪些东西"必须由轨迹回答 —— 两者的区别正是
    「理由 ≠ 事实」那条教训。
    """

    step: int
    call_id: str
    tool: str
    scope: str
    arguments: dict[str, Any]
    status: str
    message: str
    duration_ms: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "call_id": self.call_id,
            "tool": self.tool,
            "scope": self.scope,
            "arguments": self.arguments,
            "status": self.status,
            "message": self.message,
            "duration_ms": self.duration_ms,
        }


class Dispatcher:
    """一次讨论（一个会话或侧边栏、一段用户手势）里的工具执行器。

    ## ★ P6+.6：为什么加了 `scopes` 与 `in_sidebar`

    侧边栏常驻模型**没有当前会话**，所以它不能用需要会话的工具。
    挡住它们的机制有两层，而且是**故意的两层**：

    1. **模型根本看不见**：`tool_schemas(SIDEBAR_TOOL_SCOPES)` 不发给它。
       这是主要的防线 —— 一个从未出现在工具清单里的工具，模型几乎不会去调。
    2. **调了也会被拒**：`self.scopes` 在这里再挡一次（见 `_execute`）。

    只留第一层是不够的：模型会编工具名（`retrieve_session` 这种它见过的
    名字尤其容易），而那时若放它进 handler，它会拿到一个哨兵 `session_id`
    去查一个不存在的会话。**"看不见"是概率，"拒绝"是保证** ——
    本项目反复出现的教训是：**只靠"它不会那么做"的防线，迟早会被走到。**
    """

    def __init__(
        self,
        db: DbSession,
        session_id: str,
        *,
        user_gestures: frozenset[str] = frozenset(),
        #: 允许的作用域。`None` = 全部（会话讨论用的就是它）。
        scopes: frozenset[ToolScope] | None = None,
        #: 这次执行发生在侧边栏里 —— 没有当前会话。
        in_sidebar: bool = False,
        #: 外挂工具（联网搜索 / MCP）。**默认没有**。
        #: 它只在"内置表里找不到这个名字"时才被查到 ——
        #: 内置工具的名字因此**不可能**被外挂工具顶掉（前缀是第二道保险）。
        external: ExternalToolbox | None = None,
    ) -> None:
        self.db = db
        self.session_id = session_id
        self.scopes = frozenset(ToolScope) if scopes is None else scopes
        self.in_sidebar = in_sidebar
        self.external = external
        if in_sidebar:
            # 侧边栏：**不查会话**（它不存在），folder 也没有默认值。
            # `folder_id=None` 会让 `retrieve_folder` 要求显式给一个 ——
            # 那是对的：侧边栏不属于任何文件夹，"当前文件夹"在那里没有意义。
            self._ctx = ToolContext(
                db=db,
                session_id=session_id,
                folder_id=None,
                user_gestures=user_gestures,
                in_sidebar=True,
            )
        else:
            row = session_reads.get_session(db, session_id)
            self._ctx = ToolContext(
                db=db,
                session_id=session_id,
                folder_id=row.folder_id,
                user_gestures=user_gestures,
            )
        #: 按调用顺序累积。调用方（循环 / API 层）负责把它写进 `chat_message.tool_trace`。
        self.traces: list[ToolTraceEntry] = []

    # ----------------------------------------------------------------- #
    # 执行
    # ----------------------------------------------------------------- #

    def execute(self, call: ToolCall) -> ToolResult:
        started = time.perf_counter()
        result = self._execute(call)
        self.traces.append(
            ToolTraceEntry(
                step=len(self.traces) + 1,
                call_id=call.call_id,
                tool=call.name,
                scope=self._scope_of(call.name),
                arguments=call.arguments,
                status=result.status.value,
                message=result.message,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        )
        return result

    def _scope_of(self, name: str) -> str:
        spec = get_tool(name)
        if spec is None and self.external is not None:
            spec = self.external.get(name)
        return spec.scope.value if spec is not None else "unknown"

    def _execute(self, call: ToolCall) -> ToolResult:
        spec = get_tool(call.name)
        if spec is None and self.external is not None:
            # ★ **内置表优先**：外挂工具只在内置表里找不到时才被查到。
            # 这保证了一件事：插一个 MCP 服务器**不可能**改变任何内置工具的行为。
            # （第二道保险是 `mcp__<server>__<tool>` 那个前缀。）
            spec = self.external.get(call.name)
        if spec is None:
            # 把可用工具名列出来 —— 只说"没有这个工具"会让模型反复试别的错名字。
            from app.agent.tools import TOOLS

            external_names = (
                [s.name for s in self.external.specs()] if self.external else []
            )
            available = "、".join([*TOOLS, *external_names])
            return ToolResult(
                ToolStatus.REJECTED,
                f"没有叫 `{call.name}` 的工具。可用的有：{available}。",
            )

        problems = validate_arguments(spec.parameters, call.arguments)
        if problems:
            return ToolResult(
                ToolStatus.REJECTED,
                f"`{call.name}` 的参数不对，因此没有执行："
                + "；".join(problems)
                + "。改好参数再来一次。",
            )

        if spec.scope not in self.scopes:
            # 第二层防线（见类文档）。它必须给出**为什么**，而不是"不允许"——
            # 模型能照着"这个工具需要一个具体的会话"改口（换一个能用的工具，
            # 或者显式给出 session_id），而"不允许"它只能反复试。
            return ToolResult(
                ToolStatus.REJECTED,
                f"`{call.name}` 在这里用不了：它需要一个**具体的会话**，"
                f"而这次对话（{self.session_id}）不绑定任何会话。"
                "要么换成不需要会话的工具，要么在参数里显式给出 `session_id`。",
                {"scope": spec.scope.value},
            )

        if spec.scope is ToolScope.GLOBAL:
            # 局部 import：既然只有 handoff 能碰 Router，那就让这个依赖
            # 在**唯一需要它的那一刻**才出现。模块级 import 会让
            # 「dispatch 知道 handoff 存在」变成一条随时可以扩大的通道。
            from app.agent.handoff import submit_global_update

            return submit_global_update(self._ctx, call.arguments)

        if spec.handler is None:
            # 结构不变量被破坏了。这不该发生（`test_only_the_global_tool_lacks_a_handler`
            # 钉着它），所以报成 `error` 而不是 `rejected`：它不是模型的错。
            return ToolResult(
                ToolStatus.ERROR,
                f"工具 `{call.name}` 没有执行体，这是系统缺陷而不是你的参数问题。",
            )

        try:
            return spec.handler(self._ctx, call.arguments)
        except OPERATIONAL_ERRORS as exc:
            # 「会话已经结束」「计划还没建」「版本被人改过」—— 这些对模型说的话
            # 是可以照着改的，所以归入 `rejected` 并把 `detail` 一起给它。
            return ToolResult(
                ToolStatus.REJECTED,
                f"{exc}",
                getattr(exc, "detail", None) or {},
            )
        # 其它异常**故意不接**（包括 `CapabilityError` / `InvariantViolation`）：
        # 那是程序缺陷，接住它会把它伪装成"这个动作不允许"，
        # 而真正的修复动作是改代码，不是让模型换种问法。


# --------------------------------------------------------------------------- #
# 参数校验
# --------------------------------------------------------------------------- #

_TYPE_CHECKS: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
    "null": type(None),
}

#: JSON Schema 的类型名 → 说给模型听的中文。
#: 直接把 `integer` 塞进中文句子里，模型要额外做一次翻译才明白 ——
#: 而这句提示的全部价值就在于**它一眼就能照做**。
_TYPE_LABELS = {
    "string": "字符串",
    "integer": "整数",
    "number": "数字",
    "boolean": "布尔值",
    "object": "对象",
    "array": "列表",
    "null": "空值",
}


def validate_arguments(schema: dict[str, Any], arguments: Any) -> list[str]:
    """把参数与 schema 对一遍，返回**人类可读的**问题清单（空 = 通过）。

    只支持本项目自己发出去的那几种关键字。遇到不认识的 `type`
    一律**放行而不是拒绝** —— 校验器比 schema 严格时，
    一个写对参数的调用会被它自己的检查器挡住，而报错指向工具名，
    看起来像工具坏了。
    """
    problems: list[str] = []

    def check(node: dict[str, Any], value: Any, path: str) -> None:
        expected = node.get("type")
        if expected == "object":
            if not isinstance(value, dict):
                problems.append(f"`{path}` 要是对象，得到 {_name(value)}")
                return
            for key in node.get("required", ()):
                if key not in value:
                    problems.append(f"缺少必填参数 `{_join(path, key)}`")
            properties = node.get("properties", {})
            if node.get("additionalProperties") is False:
                for key in value:
                    if key not in properties:
                        problems.append(
                            f"不认识的参数 `{_join(path, key)}`"
                            f"（可用：{'、'.join(properties) or '无'}）"
                        )
            for key, sub in properties.items():
                if key in value:
                    check(sub, value[key], _join(path, key))
            return

        if expected == "array":
            if not isinstance(value, list):
                # 一个常见形态：模型只传了一个元素，而不是一个列表。
                # 这里**不做包装** —— 见模块开头"不做隐式转换"。
                problems.append(f"`{path}` 要是列表，得到 {_name(value)}")
                return
            items = node.get("items")
            if isinstance(items, dict):
                for index, item in enumerate(value):
                    check(items, item, f"{path}[{index}]")
            return

        enum = node.get("enum")
        if enum is not None and value not in enum:
            problems.append(f"`{path}` 只能是 {'、'.join(map(str, enum))}，得到 {value!r}")
            return

        if expected in _TYPE_CHECKS:
            python_type = _TYPE_CHECKS[expected]
            label = _TYPE_LABELS.get(expected, expected)
            # `bool` 是 `int` 的子类：`True` 不该被当成合法的整数。
            if expected in ("integer", "number") and isinstance(value, bool):
                problems.append(f"`{path}` 要是{label}，得到布尔值")
                return
            if not isinstance(value, python_type):
                problems.append(f"`{path}` 要是{label}，得到 {_name(value)}")

    check(schema, arguments, "参数")
    return problems


def _join(path: str, key: str) -> str:
    return key if path == "参数" else f"{path}.{key}"


def _name(value: Any) -> str:
    return {
        str: "字符串",
        int: "整数",
        float: "小数",
        bool: "布尔值",
        dict: "对象",
        list: "列表",
        type(None): "空值",
    }.get(type(value), type(value).__name__)
