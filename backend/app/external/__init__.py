"""外挂工具：**联网搜索**与 **MCP 工具服务器**。

## 这一层为什么单独存在

内置的 16 个工具是**这套系统的一部分**：它们的作用域、边界、拒绝理由
都写在这里，也由这里负责。而外挂工具不是 —— 它们是**别人写的**，
能力随时会变，随时会被拔掉。把两者混在同一张表里，
"工具数=16 是设计决定"这条约束会立刻失效（那张表再也没法被钉住），
更要紧的是**一条来自外部进程的调用会和一条内置调用长得一模一样**。

所以这一层的姿态是：**给能力，不给信任**。

## 三条边界（每一条都有测试钉着）

1. **作用域上限 = 只读。** 任何外挂工具都拿不到 `SESSION_WRITE` / `GLOBAL`。
   一个第三方进程不该决定"什么写进你的认知状态"。它要影响全局，
   只能把结论作为**文本**返回，由模型走 `submit_global_update`
   （审核队列那条路）—— 中间隔着一跳，而那一跳上有引文核对。
2. **名字带前缀（`mcp__<server>__<tool>`）。** 这不是命名风格：
   没有前缀，一个自称 `retrieve_session` 的服务器就能用**内置工具的名字**
   出现在轨迹与工具清单里，而"这次调用到底是谁做的"从此答不出来。
3. **外部信息默认不可信。** 返回的结果带 `source`（url/uri）与 `external: true`，
   消息里明说「外部信息，未经核对」。它不能被当成用户的原话 ——
   而这一点恰好由既有的规矩兜住：`basis=stated` 必须能核对用户原话
   （M8 §4.6.2），网页上的一句话核不到那条引文。

## 一个坏掉的服务器不许拖垮循环

每个服务器独立、每次调用有超时；失败**返回 `error` 而不是抛异常**。
`error` 与 `empty` 必须分开（M8 §4）：前者是"这次没拿到"，
后者是"世界上确实没有" —— 把一次超时说成"没搜到"，
模型会据此得出一个关于世界的结论。
"""

from __future__ import annotations

from app.external.mcp import McpClient, McpError, McpTool, McpUnavailable, StdioTransport
from app.external.websearch import (
    HttpSearchProvider,
    LocalCorpusProvider,
    SearchHit,
    SearchProvider,
    SearchUnavailable,
    build_search_provider,
)

__all__ = [
    "HttpSearchProvider",
    "LocalCorpusProvider",
    "McpClient",
    "McpError",
    "McpTool",
    "McpUnavailable",
    "SearchHit",
    "SearchProvider",
    "SearchUnavailable",
    "StdioTransport",
    "build_search_provider",
]
