"""把"谁来当那个大脑"变成**运行期的事** —— 用文件交换，与外部对话。

## 为什么需要它（这条桥存在的唯一理由）

这条闭环此前一直测不通，而原因被误诊过一次：我们以为是模型太笨
（本地 27B 只思考不回答），后来才发现**流式路径把工具调用丢了**
（见 `OllamaClient.stream`）。两件事都能造成同样的现象，而它们要的修法完全不同。

所以要测"换个聪明的大脑会怎样"，就必须能**在不改代码、不要密钥**的前提下
把大脑换掉。商用 API 当然更真实（`OpenAICompatClient` 就是为此写的），
但它需要一把钥匙；这条桥不需要：它把每一次模型调用写成一个文件，
等人（或一个子代理、或另一个模型）把答案写回来。

## 交换协议

- 请求：`<dir>/req-<n>.json`
  `{"n": 1, "messages": [...], "tools": [{"name","description","parameters"}]}`
- 响应：`<dir>/res-<n>.json`
  `{"say": "要对用户说的话", "call": {"name": "…", "arguments": {…}}}`
  或 `{"say": "…", "calls": [{…}, {…}]}`（并行调用）

## 它不假装自己是模型

它只是把问题递出去、把答案收回来 —— **不做任何判断**。
所以它测出来的东西是"这条闭环在聪明大脑下会不会通"，
而不是"某个具体模型的表现"。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator, Sequence

from app.agent.llm import (
    ChatMessage,
    LLMDelta,
    LLMError,
    LLMReply,
    ToolCall,
    ToolSchema,
)


class SubagentBridgeClient:
    """阻塞式文件桥。每次调用等一个响应文件出现。"""

    def __init__(
        self,
        directory: str | Path,
        *,
        timeout: float = 3600.0,
        poll: float = 0.2,
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.poll = poll
        self._n = 0

    # ---- 请求/响应 ---------------------------------------------------- #

    def _request_payload(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSchema], n: int
    ) -> dict[str, Any]:
        return {
            "n": n,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                    "tool_calls": [
                        {"name": call.name, "arguments": call.arguments}
                        for call in message.tool_calls
                    ],
                    "tool_call_id": message.tool_call_id,
                    "name": message.name,
                }
                for message in messages
            ],
            "tools": [schema.as_dict()["function"] for schema in tools],
        }

    def _read_response(self, n: int) -> LLMReply:
        path = self.directory / f"res-{n}.json"
        deadline = time.monotonic() + self.timeout
        while not path.exists():
            if time.monotonic() > deadline:
                raise LLMError(
                    f"等了 {self.timeout:.0f} 秒没有人回答 {path.name}。"
                    "桥的另一端（人或子代理）没有把答案写回来 —— "
                    "**这不等于模型说了什么**。"
                )
            time.sleep(self.poll)

        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise LLMError(f"{path.name} 不是一个对象")

        items = raw.get("calls")
        if items is None:
            single = raw.get("call")
            items = [single] if isinstance(single, dict) else []
        calls = []
        for position, item in enumerate(items, start=1):
            if not isinstance(item, dict) or not item.get("name"):
                raise LLMError(f"{path.name} 里第 {position} 条调用没有 name")
            arguments = item.get("arguments")
            if not isinstance(arguments, dict):
                raise LLMError(
                    f"{path.name} 里 {item['name']} 的 arguments 必须是对象"
                )
            call_id = item.get("call_id")
            calls.append(
                ToolCall(
                    call_id=call_id if isinstance(call_id, str) and call_id else f"call_{position}",
                    name=item["name"],
                    arguments=arguments,
                )
            )

        say = raw.get("say")
        return LLMReply(
            content=say if isinstance(say, str) else "",
            tool_calls=tuple(calls),
            thinking=raw.get("thinking") if isinstance(raw.get("thinking"), str) else "",
            meta={"model": "subagent-bridge", "done_reason": "stop", "bridge_file": path.name},
        )

    def _exchange(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSchema]
    ) -> LLMReply:
        self._n += 1
        n = self._n
        request = self.directory / f"req-{n}.json"
        # 先删掉可能残留的旧响应（否则第二次会**立刻**读到上一次的答案，
        # 而那看起来像"模型没看就答了"）。
        (self.directory / f"res-{n}.json").unlink(missing_ok=True)
        request.write_text(
            json.dumps(
                self._request_payload(messages, tools, n), ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        return self._read_response(n)

    # ---- LLMClient 协议 ------------------------------------------------ #

    def chat(
        self, messages: Sequence[ChatMessage], *, tools: Sequence[ToolSchema] = ()
    ) -> LLMReply:
        return self._exchange(messages, tools)

    def stream(
        self, messages: Sequence[ChatMessage], *, tools: Sequence[ToolSchema] = ()
    ) -> Iterator[LLMDelta]:
        """桥没有增量 —— 一次给完整回复。

        这是**诚实的限制**：文件交换看不到对方在打字。所以界面在这条路上
        只会看到一条 `token` 帧，而不是逐字出现。它测的是闭环，不是观感。
        """
        reply = self._exchange(messages, tools)
        if reply.content:
            yield LLMDelta(text=reply.content)
        yield LLMDelta(reply=reply)
