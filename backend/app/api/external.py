"""外挂能力的状态（联网搜索 / MCP）。

## 为什么需要这个端点

外挂能力是**配置出来的**：没配就没有。而"没配"与"配了但没起来"
在界面上曾经长得一模一样（都是"没有这个工具"）——
那会让人去查服务器，而真相是他少写了一行配置。

所以这里把三件事分开说清楚：

| 情况 | 这里报什么 |
|---|---|
| 没配 | `web_search.enabled=false` + 原因；`mcp_servers` 为空数组 |
| 配了但起不来 | 那个服务器出现在 `mcp_servers` 里，`connected=false` + `error` |
| 接通了 | `connected=true` + `tools`（它声明了哪些工具） |

## 它只读，而且不碰 Router

这份状态既不是认知状态、也不是会话状态 —— 它是**进程状态**
（哪几个子进程还活着）。所以它不写 `router_log`，也不参与不变式自检，
与 `/api/health` 同类：回答"现在是什么样子"，不改任何东西。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.agent.external import ExternalToolbox, empty_toolbox

router = APIRouter(prefix="/api/external", tags=["external"])


def _toolbox(request: Request) -> ExternalToolbox:
    """拿不到就当成"什么都没有"。

    ★ 这里**不报错**：`/api/external/tools` 的用途是"告诉我现在插了什么"，
    而"应用没装好外挂层"这件事本身就该以"什么都没插"的形式被看到。
    """
    return getattr(request.app.state, "external", None) or empty_toolbox()


@router.get("/tools")
def list_external_tools(request: Request) -> dict[str, object]:
    """现在插了哪些外挂工具、作用域上限是什么、每个服务器的状态。"""
    return _toolbox(request).describe()
