"""一个**最小可用的 MCP 服务器**（stdio + 按行 JSON-RPC），给测试与端到端用。

它刻意长得像一个真服务器：只实现 `initialize` / `tools/list` / `tools/call`，
不认识的方法回 `-32601`，非 JSON 的行**直接忽略**（真服务器也常往 stdout 打日志）。

三个工具，各自为了验一件事：

| 工具 | 验什么 |
|---|---|
| `echo` | 正常链路：参数进去、结果出来、形状被摊平 |
| `boom` | 对方**明确报错**时，我们给模型的是 `error` + 它的话（不是 0、不是空） |
| `slow` | 卡住的服务器不许把讨论挂住 → 触发超时那条路 |

用法：
```powershell
python -m tools.fake_mcp_server
# 或者作为配置：
MOUXUE_MCP_SERVERS='[{"name":"demo","command":"python -m tools.fake_mcp_server"}]'
```
"""

from __future__ import annotations

import json
import sys
import time

TOOLS = [
    {
        "name": "echo",
        "description": "把参数原样回显（验证链路用）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要回显的文字。"},
                "loud": {"type": "boolean", "description": "是否大写。"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "boom",
        "description": "总是报错（验证失败路径用）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "slow",
        "description": "睡很久（验证超时用）。",
        "inputSchema": {
            "type": "object",
            "properties": {"seconds": {"type": "number", "description": "睡多少秒。"}},
        },
    },
]


def _content(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def handle(method: str, params: dict) -> dict:
    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-mcp", "version": "0.1"},
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            text = str(args.get("text", ""))
            if args.get("loud"):
                text = text.upper()
            return _content(text)
        if name == "boom":
            return {"content": [{"type": "text", "text": "我不干。"}], "isError": True}
        if name == "slow":
            time.sleep(float(args.get("seconds", 5)))
            return _content("终于醒了")
        return {"content": [{"type": "text", "text": f"没有 {name} 这个工具。"}], "isError": True}
    raise LookupError(method)


def main() -> int:
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            # 真服务器也会在 stdout 上打日志；按行读的话那些行会到这里来。
            # 忽略它们是**协议要求**的一部分（我们这边也必须这样）。
            continue
        method = message.get("method")
        request_id = message.get("id")
        if method and method.startswith("notifications/"):
            continue
        try:
            result = handle(method, message.get("params") or {})
        except LookupError as exc:
            if request_id is None:
                continue
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": f"没有这个方法：{exc}"},
                    }
                ),
                flush=True,
            )
            continue
        if request_id is None:
            continue
        print(
            json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
