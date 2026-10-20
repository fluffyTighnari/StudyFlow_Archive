"""MCP（Model Context Protocol）客户端 —— 只要**够用**的那一部分。

## 为什么是手写的、而且只有一小块

MCP 的完整规范包含 resources / prompts / sampling / roots / 进度通知……
而这个项目要用到的只有两件事：**列出对方有哪些工具**、**调一个**。
把整套规范实现对这里没有任何好处，坏处却明确：协议面越大，
"一个第三方进程能碰到什么"就越难回答 —— 而这一层的全部意义是**把那个问题回答清楚**。

实现的传输是 **stdio + 按行 JSON-RPC 2.0**（MCP 的基础形态）：
`initialize` / `tools/list` / `tools/call`。对方发通知（没有 `id`）时**忽略**，
不认识的响应字段**忽略** —— 宽松地读，严格地限制能力。

## 三条边界

1. **作用域上限 = `READ`**（见 `app/external/__init__.py`）。
2. **名字带服务器前缀**。一个自称 `retrieve_session` 的服务器不能冒充内置工具。
3. **坏掉的服务器不拖垮循环**：读用后台线程 + 队列，带超时；
   任何异常都变成 `error` 结果，而不是抛进循环体。

## 一个刻意的不做：不做"工具名去重"

两个服务器都提供 `search` 时，它们各自是 `mcp__a__search` 与 `mcp__b__search`。
这一点由前缀自动成立，不需要额外机制 —— 而且**不该**做"后者覆盖前者"
那种去重：那会让"我插了第二个服务器"表现成"第一个工具悄悄换了实现"。
"""

from __future__ import annotations

import json
import queue
import shlex
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any

#: 单次调用的默认超时。给得不宽：一个外挂工具卡住时，
#: 用户看到的是"这次讨论停了"，而原因在另一个进程里。
DEFAULT_TIMEOUT = 20.0

#: 协议版本。写在 `initialize` 里；对方回什么都不影响我们的能力上限。
PROTOCOL_VERSION = "2024-11-05"


class McpError(RuntimeError):
    """对方明确回了一个错误（`error` 字段）。"""


class McpUnavailable(RuntimeError):
    """连不上 / 起来了但答非所问 / 超时。**不是**"对方说这个调用失败"。"""


@dataclass(frozen=True)
class McpTool:
    """对方声明的一个工具。

    `input_schema` **原样保留**：校验由我们的 `validate_arguments` 做，
    而它需要的正是对方给的那份 JSON Schema（缺字段就按"没有约束"处理，
    不用替它编一份 —— 编出来的约束会拒掉对方本来支持的调用）。
    """

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


def parse_tools(payload: Any) -> list[McpTool]:
    """`tools/list` 的响应 → `McpTool`。形状不对就抛，不猜。"""
    rows = payload.get("tools") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise McpUnavailable("`tools/list` 的响应里没有 `tools` 数组。")
    out: list[McpTool] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        schema = row.get("inputSchema") or row.get("input_schema") or {}
        out.append(
            McpTool(
                name=str(row["name"]),
                description=str(row.get("description") or ""),
                input_schema=schema if isinstance(schema, dict) else {},
            )
        )
    return out


class StdioTransport:
    """按行 JSON-RPC 的 stdio 传输。

    ★ **读要带超时，所以读在后台线程里。** 直接 `readline()` 会在对方
    卡住时把整个讨论挂在那里 —— 而"一个外挂工具卡住"与"这套系统坏了"
    在界面上长得一模一样。后台线程 + `queue.get(timeout=...)` 把前者
    变成一次可报告的失败。

    ★ **只读 `stdout`，不读 `stderr`。** 对方往 stderr 写日志是常态，
    把它混进协议流会让 JSON 解析随机失败。子进程的 stderr 直接丢弃
    （要看日志就自己去跑那个服务器）。
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        env: dict[str, str] | None = None,
    ) -> None:
        self.command = command
        self.args = list(args or [])
        self.timeout = timeout
        self._next_id = 0
        self._lock = threading.Lock()
        self._inbox: queue.Queue[dict[str, Any] | BaseException | None] = queue.Queue()
        try:
            self.proc = subprocess.Popen(  # noqa: S603 - 命令来自本地配置，不是用户输入
                [command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=cwd,
                env=env,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            raise McpUnavailable(f"起不来 MCP 服务器 {command!r}：{exc}") from exc
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        stream = self.proc.stdout
        if stream is None:  # pragma: no cover - Popen 成功时不会发生
            self._inbox.put(McpUnavailable("MCP 服务器没有 stdout。"))
            return
        try:
            for line in stream:
                text = line.strip()
                if not text:
                    continue
                try:
                    self._inbox.put(json.loads(text))
                except json.JSONDecodeError:
                    # 非 JSON 的一行**忽略**：对方可能在 stdout 上打了日志。
                    # 让它变成一次失败会把"服务器爱打日志"变成"这个服务器不能用"。
                    continue
        except (OSError, ValueError) as exc:  # pragma: no cover - 进程结束时
            self._inbox.put(McpUnavailable(f"读 MCP 输出失败：{exc}"))
        finally:
            self._inbox.put(None)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """发一条通知（没有 `id`，不等回复）。"""
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _send(self, payload: dict[str, Any]) -> None:
        if self.proc.stdin is None:  # pragma: no cover
            raise McpUnavailable("MCP 服务器没有 stdin。")
        try:
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise McpUnavailable(f"写不进 MCP 服务器：{exc}") from exc

    def request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> Any:
        """发一条请求并等回复。超时 → `McpUnavailable`。"""
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        deadline = timeout if timeout is not None else self.timeout
        while True:
            try:
                item = self._inbox.get(timeout=deadline)
            except queue.Empty:
                raise McpUnavailable(
                    f"`{method}` 超过 {deadline:.0f} 秒没有回应（服务器卡住了？）。"
                ) from None
            if item is None:
                raise McpUnavailable(f"`{method}` 还没回，MCP 服务器就退出了。")
            if isinstance(item, BaseException):
                raise item
            if item.get("id") != request_id:
                # 不是这次的回复（可能是上一条的超时回复）→ 继续等。
                continue
            if "error" in item:
                raise McpError(f"{method} 失败：{item['error']}")
            return item.get("result")

    def close(self) -> None:
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except OSError:  # pragma: no cover
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
            self.proc.kill()


class McpClient:
    """一个 MCP 服务器：**列工具 + 调工具**，别的都不做。"""

    def __init__(self, name: str, transport: StdioTransport) -> None:
        self.name = name
        self.transport = transport
        #: 连接与 `tools/list` 的结果。失败时是 `None` 并记 `error` ——
        #: "这个服务器没起来"必须是界面上看得见的一件事，
        #: 而不是"它没有工具"（那会让用户以为插错了配置）。
        self.tools: list[McpTool] = []
        self.error: str = ""
        self.connected = False

    @classmethod
    def spawn(
        cls,
        name: str,
        command_line: str,
        *,
        cwd: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> McpClient:
        parts = shlex.split(command_line, posix=False)
        if not parts:
            raise McpUnavailable(f"服务器 `{name}` 的命令是空的。")
        command, *args = parts
        # 去掉 Windows 上 `shlex.split(posix=False)` 留下的引号
        command = command.strip('"')
        args = [a.strip('"') for a in args]
        return cls(name, StdioTransport(command, args, cwd=cwd, timeout=timeout))

    def connect(self) -> None:
        """握手 + 拉工具清单。**失败不抛**：记在 `error` 上，能力为空。"""
        try:
            self.transport.request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "mouxue", "version": "0.1"},
                },
            )
            self.transport.notify("notifications/initialized")
            self.tools = parse_tools(self.transport.request("tools/list"))
            self.connected = True
            self.error = ""
        except (McpError, McpUnavailable) as exc:
            self.connected = False
            self.error = str(exc)

    def call(self, tool: str, arguments: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        """调一个工具。返回 `(ok, text, raw)` —— **从不抛异常**。

        三件事在这里被压平成同一种结果：
        - 对方回 `isError: true` → `ok=False`，正文照给（那是对方的话）；
        - 对方回错误对象 / 超时 → `ok=False`，正文是**我们能说清的原因**；
        - 正常 → `ok=True`。

        ★ 无论哪种，正文都不会被丢掉：一个失败的 MCP 调用，
        "它到底说了什么"往往正是要看的。
        """
        try:
            result = self.transport.request(
                "tools/call", {"name": tool, "arguments": arguments or {}}
            )
        except (McpError, McpUnavailable) as exc:
            return False, str(exc), {"tool": tool, "server": self.name}
        text, raw = _flatten(result)
        if isinstance(result, dict) and result.get("isError"):
            return False, text, raw
        return True, text, raw

    def describe(self) -> dict[str, Any]:
        return {
            "server": self.name,
            "connected": self.connected,
            "error": self.error,
            "tools": [tool.name for tool in self.tools],
        }

    def close(self) -> None:
        self.transport.close()


def _flatten(result: Any) -> tuple[str, dict[str, Any]]:
    """MCP 的 `content` 数组 → 一段文本（+ 原始结构）。

    只认 `type=text`；其它类型（图片等）**说出来但不解读** ——
    把一段 base64 当成文本塞进上下文，模型会拿它编东西。
    """
    if not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False), {"raw": result}
    chunks: list[str] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            chunks.append(str(item.get("text") or ""))
        else:
            chunks.append(f"[{item.get('type')} 内容未解读]")
    if not chunks and "structuredContent" in result:
        chunks.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    return "\n".join(chunks).strip(), {"content": result.get("content") or []}


def load_servers(raw: str) -> list[dict[str, str]]:
    """解析 `mcp_servers` 配置：JSON 数组 `[{name, command, cwd?}]`。

    也接受 `name=命令行` 这种一行一条的写法（手写配置时更省事）。
    ★ 形状不对**抛错**而不是跳过那一行：配置写错了要立刻说，
    否则表现是"服务器没插上"，而人会去查服务器。
    """
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            rows = json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpUnavailable(f"`mcp_servers` 不是合法 JSON：{exc}") from exc
        if not isinstance(rows, list):
            raise McpUnavailable("`mcp_servers` 必须是数组。")
        out: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("name") or not row.get("command"):
                raise McpUnavailable(
                    "`mcp_servers` 的每一项都要有 `name` 与 `command`。"
                )
            out.append(
                {
                    "name": str(row["name"]).strip(),
                    "command": str(row["command"]).strip(),
                    "cwd": str(row.get("cwd") or ""),
                }
            )
        return _validated(out)
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise McpUnavailable(f"看不懂的 MCP 服务器配置行：{line!r}（要 `名字=命令`）。")
        name, command = line.split("=", 1)
        out.append({"name": name.strip(), "command": command.strip(), "cwd": ""})
    return _validated(out)


def _validated(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """空白名字 / 空命令在这里就拒掉。

    ★ 不在更深的地方拒（例如 `spawn` 里）：配置写错的症状会是
    "那个服务器没插上"，而人会去查服务器。错误要在**读配置**那一步说。
    """
    for row in rows:
        if not row["name"]:
            raise McpUnavailable("MCP 服务器配置里有空名字。")
        if not row["command"]:
            raise McpUnavailable(
                f"MCP 服务器 `{row['name']}` 的命令是空的（要 `名字=命令`）。"
            )
    return rows
