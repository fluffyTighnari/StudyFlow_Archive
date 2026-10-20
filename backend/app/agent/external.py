"""把外挂能力接成**工具**（`ToolSpec`）。

## 为什么这个文件在 `app/agent/` 而不是 `app/external/`

依赖方向：`app.external` 提供能力（MCP 客户端、搜索提供者），
`app.agent` 决定**这些能力以什么姿态出现在模型面前**（工具名、作用域、
参数校验、结果形状）。反过来 import 会成环 —— 而更要紧的是概念上就不对：
"MCP 是什么"不该知道"我们的工具表长什么样"。

## 三条规则的落点

1. **作用域上限 = `READ`**（`_READ_ONLY`，没有第二个取值）。
2. **名字带前缀**（`mcp__<server>__<tool>`）：服务器不能冒充内置工具。
3. **能力缺失 = 工具不存在**：没配搜索就没有 `web_search`，
   服务器没起来就没有它的工具。**不留一个"一调就报错"的占位符** ——
   它会占掉模型的选择面，换来的只有"这次又不行"。
   但"没起来"本身要**看得见**：`describe()` 把每个服务器的状态报给界面。

## 结果形状

所有外挂工具的返回都带 `external: true` 与 `source`（url/uri）。
消息开头统一是「外部信息，未经核对」—— 这句话不是说给用户听的装饰：
它对应两条既有规矩（`basis=stated` 必须能核对用户原话、
`submit_global_update` 必须给 `source_ref`），
而那句话的作用是让模型**知道自己手里拿的是什么**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agent.tools import ToolResult, ToolScope, ToolSpec, ToolStatus, _object, _string
from app.config import Settings
from app.external import (
    McpClient,
    McpUnavailable,
    SearchProvider,
    SearchUnavailable,
    build_search_provider,
)
from app.external.mcp import DEFAULT_TIMEOUT, load_servers

#: MCP 工具名的前缀。**不是命名风格，是能力边界**：
#: 没有它，一个自称 `retrieve_session` 的服务器就能用内置工具的名字
#: 出现在轨迹里，而"这次调用到底是谁做的"从此答不出来。
MCP_PREFIX = "mcp__"
WEB_SEARCH_TOOL = "web_search"

#: 外挂工具**唯一**允许的作用域。见 `app/external/__init__.py` 的三条边界。
_READ_ONLY = ToolScope.READ

#: 每条外挂结果开头的这句话，会被模型看到。它对应两条既有规矩（见模块文档）。
EXTERNAL_MARK = "外部信息，未经核对"


def mcp_tool_name(server: str, tool: str) -> str:
    """`mcp__<server>__<tool>`。

    分隔符是双下划线，因为 MCP 自己的工具名里常见单下划线
    （`read_file`）—— 用单下划线会让"服务器名"与"工具名"的边界变得要靠猜。
    """
    return f"{MCP_PREFIX}{server}__{tool}"


@dataclass
class ExternalToolbox:
    """一次进程生命周期内的外挂工具集合。**用完不重造**（服务器是子进程）。"""

    web: SearchProvider | None = None
    clients: list[McpClient] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # 工具表
    # ------------------------------------------------------------------ #
    def specs(self, scopes: frozenset[ToolScope] | None = None) -> tuple[ToolSpec, ...]:
        out: list[ToolSpec] = []
        if self.web is not None:
            out.append(self._web_spec())
        for client in self.clients:
            if not client.connected:
                continue
            for tool in client.tools:
                out.append(self._mcp_spec(client, tool.name, tool.description, tool.input_schema))
        if scopes is None:
            return tuple(out)
        return tuple(spec for spec in out if spec.scope in scopes)

    def get(self, name: str) -> ToolSpec | None:
        for spec in self.specs():
            if spec.name == name:
                return spec
        return None

    # ------------------------------------------------------------------ #
    # 两个族各造一次工具
    # ------------------------------------------------------------------ #
    def _web_spec(self) -> ToolSpec:
        provider = self.web
        assert provider is not None  # 由调用点保证

        def handler(ctx: Any, args: dict[str, Any]) -> ToolResult:
            return _run_web_search(provider, args)

        return ToolSpec(
            name=WEB_SEARCH_TOOL,
            scope=_READ_ONLY,
            summary=(
                "联网搜索。**它给你的是外部信息，不是这个系统里的认知**："
                "每条结果带 `url`，要引用就把 url 一起带上；"
                "★ 不要把它当成用户说过的话 —— `submit_global_update` 里"
                "任何 `basis=stated` 都必须能核对用户原话，而网页上的句子核不到。"
                "★ 拿它查资料可以；拿它替用户决定学什么不行。"
            ),
            parameters=_object(
                {
                    "query": _string("查什么。用具体的词，不要整句问句。"),
                    "limit": {
                        "type": "integer",
                        "description": "最多几条（默认 5，上限 10）。",
                    },
                },
                required=["query"],
            ),
            handler=handler,
        )

    def _mcp_spec(
        self, client: McpClient, tool: str, description: str, input_schema: dict[str, Any]
    ) -> ToolSpec:
        name = mcp_tool_name(client.name, tool)

        def handler(ctx: Any, args: dict[str, Any]) -> ToolResult:
            return _run_mcp_call(client, tool, args)

        return ToolSpec(
            name=name,
            scope=_READ_ONLY,
            summary=(
                f"外挂工具（服务器 `{client.name}`）：{description or '对方没写说明'}。"
                f"★ 它的返回是{EXTERNAL_MARK}的外部文本 —— "
                "可以据它提问、对照、解释，但**不要**把它当成用户的偏好或证据。"
                "它不能改这套系统的任何东西（外挂工具的作用域上限是只读）。"
            ),
            parameters=_schema_from_mcp(input_schema),
            handler=handler,
        )

    # ------------------------------------------------------------------ #
    # 给界面看的
    # ------------------------------------------------------------------ #
    def describe(self) -> dict[str, Any]:
        """★ 两个 `tools` 字段**不是同一份名单**，这里必须说清楚：

        - `mcp_servers[].tools` = 那个服务器**自己声明**的名字（`echo`）；
        - 顶层 `tools` = 我们**发给模型**的名字（`mcp__demo__echo`）。

        它们混起来看，会得出"外挂工具没有前缀"这个错误结论 ——
        端到端第一版就是这么误报的（见 `_端到端验证与功能对账_v0.1.md` §15）。
        """
        return {
            "web_search": (
                {"enabled": True, **self.web.describe()}
                if self.web is not None
                else {"enabled": False, "reason": "没配搜索提供者（`web_search_provider`）"}
            ),
            "mcp_servers": [client.describe() for client in self.clients],
            "tools": [spec.name for spec in self.specs()],
            "scope_ceiling": _READ_ONLY.value,
        }

    def close(self) -> None:
        for client in self.clients:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - 关服务器失败不该影响退出
                pass


# --------------------------------------------------------------------------- #
# 两个 handler
# --------------------------------------------------------------------------- #


def _run_web_search(provider: SearchProvider, args: dict[str, Any]) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolResult(ToolStatus.REJECTED, "`query` 不能是空的。")
    raw_limit = args.get("limit")
    limit = 5 if raw_limit is None else max(1, min(10, int(raw_limit)))
    try:
        hits = provider.search(query, limit=limit)
    except SearchUnavailable as exc:
        # ★ `error` 而不是 `empty`：这次没搜成，不是"世界上没有"。
        return ToolResult(ToolStatus.ERROR, f"搜索没成功：{exc}", {"query": query})
    if not hits:
        return ToolResult(
            ToolStatus.EMPTY,
            f"没有搜到与「{query}」相关的结果。**这是搜索结果，不是这个系统里的认知。**",
            {"query": query, "external": True, "results": []},
        )
    return ToolResult(
        ToolStatus.SUCCESS,
        f"{EXTERNAL_MARK}：{len(hits)} 条，来自 {provider.name}。"
        "要引用就带上 url；不要当成用户的话。",
        {
            "query": query,
            "external": True,
            "provider": provider.name,
            "results": [hit.as_dict() for hit in hits],
        },
    )


def _run_mcp_call(client: McpClient, tool: str, args: dict[str, Any]) -> ToolResult:
    ok, text, raw = client.call(tool, args)
    payload = {
        "server": client.name,
        "tool": tool,
        "external": True,
        "arguments": args,
    }
    if not ok:
        # 对方说失败 / 超时 / 断开 —— 三者在模型侧都是 `error`，
        # 但正文里保留**到底发生了什么**（那是一句能照着改的话）。
        return ToolResult(ToolStatus.ERROR, f"{EXTERNAL_MARK}：这次没成功 —— {text}", payload)
    if not text:
        return ToolResult(
            ToolStatus.EMPTY,
            f"`{tool}` 调通了，但它返回的是空内容（不是失败）。",
            payload,
        )
    return ToolResult(
        ToolStatus.SUCCESS,
        f"{EXTERNAL_MARK}（`{client.name}` 的 `{tool}`）：{text}",
        {**payload, "content": raw},
    )


# --------------------------------------------------------------------------- #
# 参数 schema
# --------------------------------------------------------------------------- #


def _schema_from_mcp(input_schema: dict[str, Any]) -> dict[str, Any]:
    """对方的 `inputSchema` → 我们的参数 schema（**宽松地读**）。

    ★ 认不出来的部分**丢掉，而不是拒绝这个工具**：一个 schema 写法不标准的
    服务器仍然是可用的，把它整条拒掉只会表现成"插了但没反应"。
    丢掉约束的代价是参数由对方自己判 —— 而对方本来就会判（它是它的工具）。
    """
    if not isinstance(input_schema, dict):
        return _object({})
    properties = input_schema.get("properties")
    if not isinstance(properties, dict):
        return _object({})
    clean: dict[str, Any] = {}
    for key, value in properties.items():
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            continue
        clean[str(key)] = dict(value)
    required = [
        str(item)
        for item in (input_schema.get("required") or [])
        if isinstance(item, str) and item in clean
    ]
    return _object(clean, required=required)


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #


def build_toolbox(settings: Settings) -> ExternalToolbox:
    """按配置组装。★ **永不抛**：一个坏配置不该让服务起不来。

    起不来的后果是"整个应用挂了"，而这里的问题只是"某个外挂能力不可用"。
    所以每个服务器各自的失败都记在 `client.error` 上，
    由 `describe()` 报到界面，而不是中断启动。
    """
    try:
        web = build_search_provider(
            settings.web_search_provider,
            corpus=settings.web_search_corpus,
            endpoint=settings.web_search_endpoint,
            api_key=settings.web_search_api_key,
        )
    except SearchUnavailable:
        web = None

    clients: list[McpClient] = []
    try:
        servers = load_servers(settings.mcp_servers)
    except McpUnavailable:
        servers = []
    for server in servers:
        try:
            client = McpClient.spawn(
                server["name"],
                server["command"],
                cwd=server.get("cwd") or None,
                timeout=settings.mcp_timeout,
            )
        except McpUnavailable:
            # 连进程都起不来 → 造一个"没连上"的占位，让它出现在 describe() 里。
            clients.append(_unavailable(server["name"]))
            continue
        client.connect()
        clients.append(client)
    return ExternalToolbox(web=web, clients=clients)


def _unavailable(name: str) -> McpClient:
    """一个没起来的服务器的占位（`transport` 是假的，只为了能放进列表）。"""
    client = McpClient(name, _NullTransport())
    client.connected = False
    client.error = "起不来（命令或路径不对）"
    return client


class _NullTransport:
    """只为了让占位对象有形状。**任何调用都是 Unavailable**。"""

    def request(self, *args: Any, **kwargs: Any) -> Any:
        raise McpUnavailable("这个 MCP 服务器没有起来。")

    def notify(self, *args: Any, **kwargs: Any) -> None:
        return None

    def close(self) -> None:
        return None


def empty_toolbox() -> ExternalToolbox:
    """没有外挂能力时的空箱子（默认形态）。"""
    return ExternalToolbox(web=None, clients=[])


__all__ = [
    "DEFAULT_TIMEOUT",
    "EXTERNAL_MARK",
    "MCP_PREFIX",
    "WEB_SEARCH_TOOL",
    "ExternalToolbox",
    "build_toolbox",
    "empty_toolbox",
    "mcp_tool_name",
]
