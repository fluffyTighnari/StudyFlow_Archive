"""LLM 客户端抽象 —— **Agent 循环只依赖这个契约，不依赖任何一个模型**。

## 为什么要有这一层（不是为了"将来换模型很方便"）

M0 §12 的验收前提之一是「**无 LLM 也能治理**」。如果测试直接调本地 Ollama：
- 没起服务时整批测试变红 —— 而那时治理内核其实完好无损；
- 模型版本一升级，断言就开始随机失败 —— 而失败的是**测试的措辞**，不是代码；
- 一个 27B 模型每次跑几十秒，测试套件会从 25 秒变成十几分钟。

所以这里的结构是：**真实客户端**（`OllamaClient`）与**确定性替身**（`FakeLLM`）
实现同一个 `LLMClient` 协议，**测试全部走替身**，真实客户端只在一个手工演练里用。
这也是这套代码里唯一一处"两套实现" —— 它换来的是「测试不依赖外部世界」。

## 循环拥有重试的决定权

客户端**不自动重试**。理由：一次 `chat` 调用是不是可以安全重发，取决于它在循环里的位置
（工具已经执行过几次、步数还剩多少），只有循环知道。客户端偷偷重试会让
「一次 LLM 调用」这个计数变得不可解释 —— 而循环的步数上限正是靠它算的。
传输失败一律抛 `LLMError`，由循环决定是重试、降级还是停下。

## 依赖

`OllamaClient` 用 **stdlib 的 `urllib`**，不引入 httpx。
httpx 目前只在 dev 依赖里（starlette 的 TestClient 要用），
而真实调用 LLM 是**运行期**行为 —— 让它依赖一个 dev 包会在正式安装时炸掉。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]


class LLMError(Exception):
    """LLM 调用失败（连不上、超时、返回了无法解析的东西）。

    它是一个**可预期的运行期状态**，不是程序缺陷：本地模型没起、显存不够、
    模型吐出了坏 JSON 都属于这一类。循环把它变成 `stop_reason="llm_error"`，
    而不是让它冒泡成 500 —— 用户需要看到的是「模型这次没答上来」，
    而不是一个堆栈。
    """


# --------------------------------------------------------------------------- #
# 线上格式
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ToolSchema:
    """给模型看的工具说明（OpenAI / Ollama 共用的形状）。

    `parameters` 是 JSON Schema。**它由契约生成，不手写** ——
    M8 §4.6 的 `statement` 规范（「理由一起写进来」）之所以必须落在
    `M2PreferenceValue` 的字段说明上，就是因为这里会把字段说明原样交给模型。
    """

    name: str
    description: str
    parameters: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolCall:
    """模型要求执行的一次工具调用。

    `call_id` 在 Ollama 的返回里**并不存在**（它按顺序匹配工具结果），
    因此由适配器**确定性地合成**（`call_1`、`call_2`…）。
    确定性是刻意的：轨迹（`tool_trace`）要能在两次运行之间比对，
    随机 id 会让「同样输入是否产生同样行为」无法回答。
    """

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatMessage:
    """一次对话里的一条消息。`tool` 角色用于把工具结果回灌给模型。

    **这个类没有 `as_dict()`，是刻意的。** 各家服务的线上格式真的不一样：
    Ollama 的工具结果用 `tool_name` 并**按顺序**匹配，OpenAI 兼容层用
    `tool_call_id` 并**按 id** 匹配。写一个"通用序列化器"就等于同时写错一边，
    而且错法很隐蔽（服务端会忽略无法识别的键，表现为模型忽然"看不见"工具结果）。
    所以序列化归各自的客户端 —— 见 `OllamaClient._message_payload`。
    """

    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    #: 内部标识；Ollama 不认这个字段，因此它**不出现在发出去的请求里**。
    tool_call_id: str | None = None
    #: Ollama 认这个字段（`tool_name`），工具结果靠它归类。
    name: str | None = None

    @staticmethod
    def system(content: str) -> ChatMessage:
        return ChatMessage(role="system", content=content)

    @staticmethod
    def user(content: str) -> ChatMessage:
        return ChatMessage(role="user", content=content)

    @staticmethod
    def assistant(content: str, tool_calls: Sequence[ToolCall] = ()) -> ChatMessage:
        return ChatMessage(role="assistant", content=content, tool_calls=tuple(tool_calls))

    @staticmethod
    def tool_result(call: ToolCall, content: str) -> ChatMessage:
        return ChatMessage(
            role="tool", content=content, tool_call_id=call.call_id, name=call.name
        )


@dataclass(frozen=True)
class LLMReply:
    """模型的一次回复。`content` 与 `tool_calls` 可以同时非空。

    真实模型经常这样做：先说一句「我先看一下你的计划」，同一条回复里带上工具调用。
    把 `content` 丢掉会让聊天记录看起来像模型什么都没说。
    """

    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    #: **思考内容**（thinking / reasoning 字段），与 `content` 分开存。
    #:
    #: 一次真实运行里，本地思考型模型生成 93 个 token、`done_reason=stop`，
    #: 而 `content` 是空串 —— 那 93 个 token 全在 `message.thinking` 里，
    #: 于是端点把一次"只想了没说"的回复误诊成"生成出了问题"。
    #:
    #: 为什么**不并进 `content`**：并进去就等于把模型的推理当成它的回答。
    #: 本项目从头到尾在区分"理由"与"事实"，而推理与结论是同一对区分的另一面。
    thinking: str = ""
    #: 诊断信息（模型名、耗时、token 数）。**不参与任何判定。**
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@dataclass(frozen=True)
class LLMDelta:
    """流式回复的**一帧**。

    两种帧，互斥且穷尽：

    - **增量帧**：`text` / `thinking` 非空，`reply is None`。模型还在生成。
    - **收官帧**：`reply` 非空。它就是同一次调用在非流式下的那个 `LLMReply`
      （含 `tool_calls` 与 meta）—— 所以循环**不需要**把增量拼起来再用，
      拼接只用于显示，判定一律用收官帧。

    为什么把"拼接"交给显示层：增量拼接是**一次重建**，
    而重建出来的字符串与收官帧的 `content` 在正常实现里必然相等。
    若让判定去用重建值，那么两者一旦不等（丢帧、重连、渲染器差异），
    系统会按一个"界面看到的东西"去做决定 —— 而界面看到的东西从来不是权威。
    """

    #: 这一帧新增的正文。可能为空串（例如只有思考的帧）。
    text: str = ""
    #: 这一帧新增的思考内容。**与正文分开**，理由同 `LLMReply.thinking`。
    thinking: str = ""
    #: 收官帧携带的完整回复；其余帧为 `None`。
    reply: LLMReply | None = None

    @property
    def is_final(self) -> bool:
        return self.reply is not None


class LLMClient(Protocol):
    """Agent 循环看到的全部 LLM 能力 —— 就这两个方法。

    `chat` 与 `stream` **必须给出同一个回复**，只是分批与一次性之别。
    这条不是风格要求：循环体只有一份（见 `AgentLoop._ask`），
    两条路若在"返回什么"上有分歧，分歧会以"界面看到的话与库里存的话不一样"
    的形式出现 —— 那是本项目最不愿意见到的一类不一致。

    为什么两个方法而不是一个带 `stream` 开关的方法：返回类型不同
    （一个 `LLMReply`，一个增量序列）。用开关会让调用方拿到一个
    "有时是回复、有时是迭代器"的东西，而每个调用点都得自己判断是哪种。
    """

    def chat(
        self, messages: Sequence[ChatMessage], *, tools: Sequence[ToolSchema] = ()
    ) -> LLMReply: ...

    def stream(
        self, messages: Sequence[ChatMessage], *, tools: Sequence[ToolSchema] = ()
    ) -> Iterator[LLMDelta]: ...


# --------------------------------------------------------------------------- #
# 确定性替身
# --------------------------------------------------------------------------- #


class FakeLLM:
    """按**脚本**回放回复的替身。

    脚本里的每一项对应一次 `chat` 调用。脚本用完之后再被调用会抛
    `LLMError` —— 这是刻意的：循环必须在预算内结束，
    而"脚本不够长"意味着**循环多跑了一步**，那正是要抓的缺陷
    （真实模型会无限配合你，测试里的替身不该配合）。

    每次收到的消息都被完整记录下来（`self.seen`），
    于是「系统提示词里到底有什么」「工具结果有没有回灌」都可以被断言 ——
    而不是只能断言最终写进库里的东西。
    """

    def __init__(
        self,
        script: Sequence[LLMReply] | None = None,
        *,
        chunk_size: int | None = None,
    ) -> None:
        self._script: list[LLMReply] = list(script or ())
        self.seen: list[list[ChatMessage]] = []
        self.tools_seen: list[list[ToolSchema]] = []
        #: 流式时每帧切多少字符。`None` = 整段一帧。
        #:
        #: 默认整段一帧，是为了让"只关心结果"的测试读起来干净；
        #: 而**测 SSE 的测试必须显式给一个小值** —— 否则"增量"这件事
        #: 在测试里从来没有发生过，解析器的分帧逻辑就永远没被走过。
        self.chunk_size = chunk_size
        #: 每一次 `stream` 调用发出去的帧（测试用来断言"真的分了帧"）。
        self.deltas: list[list[LLMDelta]] = []

    def chat(
        self, messages: Sequence[ChatMessage], *, tools: Sequence[ToolSchema] = ()
    ) -> LLMReply:
        self.seen.append(list(messages))
        self.tools_seen.append(list(tools))
        if not self._script:
            raise LLMError(
                f"FakeLLM 的脚本用完了（已经调用 {len(self.seen)} 次）—— "
                "循环比脚本预期的多走了一步"
            )
        return self._script.pop(0)

    def stream(
        self, messages: Sequence[ChatMessage], *, tools: Sequence[ToolSchema] = ()
    ) -> Iterator[LLMDelta]:
        """**与 `chat` 走同一次脚本消耗**，只是把结果切帧发出去。

        刻意调用 `self.chat(...)` 而不是自己 pop 脚本：这样"脚本用完了"
        那条保护、`seen` / `tools_seen` 的记录、以及"两条路给出同一个回复"
        这三件事**自动成立**，不会因为将来改了一处而只在一条路上生效。
        """
        reply = self.chat(messages, tools=tools)
        frames = _chunk(reply, self.chunk_size)
        self.deltas.append(list(frames))
        return iter(self.deltas[-1])

    def push(self, reply: LLMReply) -> FakeLLM:
        """往脚本末尾追加一条（构造多轮脚本时可读性更好）。"""
        self._script.append(reply)
        return self

    @property
    def remaining(self) -> int:
        return len(self._script)


def reply(content: str = "", *calls: ToolCall) -> LLMReply:
    """构造脚本项的简写：`reply("好", call("write_stage", ...))`。

    调用 id 在这里**统一重排**：`call_1`、`call_2`…
    不能沿用构造时给的 id —— 一次回复里若有两次调用，固定的 `call_1`
    会让轨迹里出现两个同名 id，而"哪条结果对应哪次调用"就答不上来了。
    """
    renumbered = tuple(
        ToolCall(call_id=f"call_{index}", name=item.name, arguments=item.arguments)
        for index, item in enumerate(calls, start=1)
    )
    return LLMReply(content=content, tool_calls=renumbered)


def call(name: str, /, **arguments: Any) -> ToolCall:
    """构造一次工具调用。

    `call_id` 只是占位，会被 `reply()` 重排成 `call_1`/`call_2`…；
    单独使用时（例如测试里手写某一步的期望）也能直接用。
    """
    return ToolCall(call_id="call_1", name=name, arguments=arguments)


def _chunk(reply: LLMReply, size: int | None) -> list[LLMDelta]:
    """把一个回复切成帧：若干增量帧 + **一个收官帧**。

    收官帧一定在最后，且它携带的就是整个 `reply` —— 这样循环里的判定
    永远只用收官帧，增量帧只服务显示（见 `LLMDelta`）。

    思考内容先于正文发：真实模型就是先想后说，顺序反了会让界面出现
    "话说完了还在想"的假象。
    """
    frames: list[LLMDelta] = []
    step = size or max(len(reply.thinking), len(reply.content), 1)
    for slice_ in _slices(reply.thinking, step):
        frames.append(LLMDelta(thinking=slice_))
    for slice_ in _slices(reply.content, step):
        frames.append(LLMDelta(text=slice_))
    frames.append(LLMDelta(reply=reply))
    return frames


def _slices(text: str, size: int) -> list[str]:
    if not text:
        return []
    return [text[index : index + size] for index in range(0, len(text), size)]


# --------------------------------------------------------------------------- #
# 真实客户端
# --------------------------------------------------------------------------- #

#: `(url, payload, timeout) -> 解析后的 JSON`。注入它是为了能在测试里
#: 断言**发出去的请求长什么样**，同时不需要起一个 HTTP 服务。
HttpPost = Callable[[str, dict[str, Any], float], dict[str, Any]]

#: 流式版本：`(url, payload, timeout) -> 逐帧的 JSON`。
#: 与 `HttpPost` 分开是刻意的 —— 一个返回"一个对象"，一个返回"一串对象"，
#: 让同一个函数两种都返回会让调用点必须自己判断拿到了哪种。
HttpStream = Callable[[str, dict[str, Any], float], Iterator[dict[str, Any]]]

#: `(url, headers, timeout) -> 解析后的 JSON` —— 只有 GET，用于拉模型清单。
#:
#: 单独一个类型而不是复用 `HttpPost`：那个签名里没有 header 位，
#: 而拉模型列表**必须**能带 `Authorization`（否则 OpenRouter 之类直接 401）。
HttpGet = Callable[[str, dict[str, str], float], dict[str, Any]]


def _urllib_get(url: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        if exc.code in (401, 403):
            # 这一条单独说：401 的通用文本是 "Unauthorized"，
            # 而用户真正需要知道的是"密钥不对、过期、或这把钥匙没有列模型的权限"。
            raise LLMError(
                f"服务拒绝了这次请求（{exc.code}）：密钥无效、已过期，"
                f"或这把钥匙没有列模型的权限。服务说：{detail}"
            ) from exc
        raise LLMError(f"拉模型列表失败（{exc.code}）：{detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(
            f"连不上 {url}：{exc}。地址填错、服务没起、或本机网络不通时都是这个错误。"
        ) from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(
            f"模型列表返回的不是 JSON（前 200 字符）：{raw[:200]!r}。"
            "**这通常说明 base_url 填错了** —— 打到了一个网页上。"
        ) from exc
    if not isinstance(parsed, dict):
        raise LLMError(f"模型列表不是对象：{type(parsed).__name__}")
    return parsed


class ModelLister(Protocol):
    """能问出"这家有哪些模型"的客户端。

    **不放进 `LLMClient`**：循环只需要 `chat`/`stream`，而 `FakeLLM` 不该
    被迫实现一个它永远用不到的方法（那会变成一堆 `raise NotImplementedError`）。
    """

    def list_models(self) -> list[str]: ...



def _urllib_post(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # 4xx/5xx：服务在，但拒绝了这个请求
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise LLMError(f"LLM 服务返回 {exc.code}：{detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(
            f"连不上 LLM 服务（{url}）：{exc}。"
            "本地 Ollama 没在跑时就是这个错误 —— 先确认服务已启动、模型已下载。"
        ) from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM 服务返回的不是 JSON：{raw[:300]}") from exc
    if not isinstance(parsed, dict):
        raise LLMError(f"LLM 服务返回的 JSON 不是对象：{type(parsed).__name__}")
    return parsed


def _urllib_stream(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    *,
    headers: dict[str, str] | None = None,
) -> Iterator[dict[str, Any]]:
    """按 NDJSON 逐行读 Ollama 的流式响应。

    `urlopen` 的返回值是文件对象，**逐行迭代它**就是流式 —— 不要用
    `response.read()`：那会等到连接关闭才返回，于是"流式"在观感上与
    一次性请求完全一样，而没有任何报错。

    一行的错误处理与 `_urllib_post` 同规格：坏行报出来，不静默跳过。
    静默跳过会让"模型漏了几个字"变成一件无从解释的事。
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:  # 4xx/5xx：服务在，但拒绝了这个请求
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise LLMError(f"LLM 服务返回 {exc.code}：{detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(
            f"连不上 LLM 服务（{url}）：{exc}。"
            "本地 Ollama 没在跑时就是这个错误 —— 先确认服务已启动、模型已下载。"
        ) from exc

    with response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LLMError(f"流式响应里有一行不是 JSON：{line[:300]}") from exc
            if not isinstance(frame, dict):
                raise LLMError(f"流式响应的一帧不是对象：{type(frame).__name__}")
            yield frame


def _urllib_sse(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    *,
    headers: dict[str, str] | None = None,
) -> Iterator[dict[str, Any]]:
    """按 **SSE** 读流式响应（`data: {...}` 逐条）。

    与 `_urllib_stream` 的 NDJSON 是两种协议，**不能复用**：
    OpenAI 兼容服务发的是 `data: {json}\\n\\n`，还有注释行（`: keep-alive`）
    和结尾的 `data: [DONE]`。用 NDJSON 的解析器读它会立刻报
    "有一行不是 JSON" —— 而那句报错会把"协议不同"说成"服务返回了坏数据"。

    三种行各有处置，且都不静默：
    - 空行 / 以 `:` 开头（注释、心跳）→ 跳过（协议允许，不是数据）；
    - `data: [DONE]` → 正常结束（这就是收官标记）；
    - `data: {...}` → 解析成对象产出；
    - 其它 → 报错（`event:` 行之类我们不使用的字段也报错：
      静默跳过会让"服务换了协议"变成"模型话变少了"）。
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            **(headers or {}),
        },
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise LLMError(f"LLM 服务返回 {exc.code}：{detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"连不上 LLM 服务（{url}）：{exc}") from exc

    with response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                raise LLMError(f"流式响应里有一行不是 SSE 数据行：{line[:200]}")
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                return
            try:
                frame = json.loads(data)
            except json.JSONDecodeError as exc:
                raise LLMError(f"SSE 数据行不是 JSON：{data[:300]}") from exc
            if not isinstance(frame, dict):
                raise LLMError(f"SSE 的一帧不是对象：{type(frame).__name__}")
            yield frame


class OllamaClient:
    """Ollama `/api/chat` 的客户端。两个方法：一次性与流式。

    `chat` 用 `stream=False`：Agent 循环需要**一条完整的回复**才能决定下一步。
    `stream` 用 `stream=True`：界面需要"边生成边显示"，而它**不改变循环的判定** ——
    判定一律用收官帧（见 `LLMDelta`）。

    两个方法共用 `_payload()`，因此 `num_ctx`、工具声明这类"必须显式给"的东西
    只有一处，不会出现"同步那条路设了上下文窗口、流式那条忘了"这种
    **只在某一条路上复现**的缺陷。

    超时默认给得很宽（300 秒）：本地 27B 模型的**第一次**调用要先把它读进显存，
    在机械盘上几十秒很正常。超时太短会表现成"模型不会用"，
    而实际原因只是模型还在加载 —— 这类误诊最费时间。
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 300.0,
        num_ctx: int = 16384,
        think: bool | None = None,
        transport: HttpPost = _urllib_post,
        stream_transport: HttpStream = _urllib_stream,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.num_ctx = num_ctx
        #: 思考开关。`None` = **不发这个字段**（今天的行为，对所有模型都安全）。
        #:
        #: 为什么它值得一个显式开关（实测，见 `backend/var/model_trials.md`）：
        #: `qwen3.6:27b` 显式 `think=false` 时 **5/5** 次都产出工具调用；
        #: 而**不发这个字段**是唯一跑飞 900 秒超时的那一格。
        #: 反过来，`qwen2.5-coder:7b` / `phi3:3.8b` / `qwen2:1.5b` 这类模型
        #: 收到 `think` 会直接 HTTP 400（"does not support thinking/tools"）——
        #: 所以**默认不发**，由调用方按模型显式打开或关掉。
        self.think = think
        self._post = transport
        self._stream = stream_transport

    def _payload(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSchema], *, stream: bool
    ) -> dict[str, Any]:
        """**唯一的请求构造点。** 两个方法都从这里出发。"""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._message_payload(m) for m in messages],
            "stream": stream,
            # **必须显式给上下文窗口。** 不给的话 Ollama 按当前显存自己算一个，
            # 而它算出来的可能远小于我们发的 prompt：一次真实运行里它给了 4096，
            # 于是 7000 token 的请求被截到 4095、只剩 1 个 token 可生成
            # （返回 `done_reason=length`、`eval_count=1`、内容是空串）。
            # 表现出来是"模型答了个空话"，真正的原因却在我们发的东西太大。
            "options": {"num_ctx": self.num_ctx},
        }
        if self.think is not None:
            # **只在显式设置时发**：不认识这个字段的模型会 400，而那个 400
            # 看起来像"模型不支持工具"，比实际原因难查得多。
            payload["think"] = self.think
        if tools:
            payload["tools"] = [schema.as_dict() for schema in tools]
        return payload

    def chat(
        self, messages: Sequence[ChatMessage], *, tools: Sequence[ToolSchema] = ()
    ) -> LLMReply:
        raw = self._post(
            f"{self.base_url}/api/chat",
            self._payload(messages, tools, stream=False),
            self.timeout,
        )
        return self._parse(raw)

    def stream(
        self, messages: Sequence[ChatMessage], *, tools: Sequence[ToolSchema] = ()
    ) -> Iterator[LLMDelta]:
        """逐帧转发 Ollama 的 NDJSON 流。

        **收官帧一定是完整的回复**，包括 `content` 与 `thinking` —— 这一条
        是被一次真实运行逼出来的：

        Ollama 的收官帧里 `message` 常常是**空的**（只有 `done: true` 与计数）。
        于是"只把收官帧解析成 `LLMReply`"会得到 `content=""`、`thinking=""`，
        而真相是模型想了 197 个 token 却没落到回答上。循环拿这个 reply 去
        找解释时，`thinking_without_answer_note` 看到 `thinking` 是空的，
        于是端出另一句解释：「本地模型这一轮生成出了问题」——
        **一句错的解释**，而它离真相只差"把增量拼起来"这一步。

        所以这里把增量**累积**下来，在收官帧上补齐：收官帧自己带的那部分
        优先（它是权威的完整值），它空着而增量里有时才用增量。
        补齐之后 `stream` 与 `chat` 给出的回复是同一个东西 ——
        这正是 `LLMClient` 契约里那句话在流式路径上的兑现方式。

        **没有收官帧就抛错**：那说明连接断在半路，这时绝不能把
        "半句话"当成完整回复（用户会以为模型就是这么说的）。

        ## `tool_calls` 也必须累积 —— 这一条是**丢过东西之后**才补上的

        原先这里只累积 `text` 与 `thinking`，`tool_calls` 只在收官帧上取一次。
        而实测（`var/probe_stream_frames.py`）Ollama 的流式是：

        ```
        第 22 帧  done=False  content=""  tool_calls=['ping']
        第 23 帧  done=True   message 是空的
        ```

        —— **工具调用落在非收官帧上，而收官帧的 message 是空的**。
        于是流式路径把每一次工具调用都丢掉了，循环看到的是
        "既没说话也没调工具"，就报出它自己那句解释：模型只想不说。
        **那个解释是假的，锅是这段代码的**：模型其实调了工具。

        代价是这条线整体不可用：界面走 SSE，所以经界面的讨论**永远不可能
        改动任何东西** —— 而它看起来完全像"模型太笨"。
        """
        frames = self._stream(
            f"{self.base_url}/api/chat",
            self._payload(messages, tools, stream=True),
            self.timeout,
        )
        text: list[str] = []
        thinking: list[str] = []
        # 按 `function.index` 收集：Ollama 每帧给一个**完整**的调用
        # （参数是对象、不是分片的 JSON 字符串），但并行调用会落在多帧上。
        # 用 index 当键，重复帧**覆盖**而不追加 —— 追加会让同一次调用执行两遍。
        collected: dict[int, ToolCall] = {}
        for raw in frames:
            if "error" in raw:
                raise LLMError(f"LLM 服务报告错误：{raw['error']}")
            message = raw.get("message")
            piece = message if isinstance(message, dict) else {}
            delta_text = piece.get("content") or ""
            delta_thinking = piece.get("thinking") or ""
            if isinstance(delta_text, str) and delta_text:
                text.append(delta_text)
            if isinstance(delta_thinking, str) and delta_thinking:
                thinking.append(delta_thinking)

            for offset, item in enumerate(piece.get("tool_calls") or ()):
                position = offset
                if isinstance(item, dict):
                    function = item.get("function")
                    if isinstance(function, dict) and isinstance(
                        function.get("index"), int
                    ):
                        position = function["index"]
                # 交给**非流式那条路用的同一个** `_parse_call`：
                # 两条路各写一份解析，迟早会在某一种输入上不一致，
                # 而那时"流式与一次性给出同一个回复"这句话就不成立了。
                collected[position] = self._parse_call(position + 1, item)

            if raw.get("done"):
                reply = self._parse(raw)
                yield LLMDelta(
                    reply=replace(
                        reply,
                        content=reply.content or "".join(text),
                        thinking=reply.thinking or "".join(thinking),
                        tool_calls=reply.tool_calls
                        or tuple(collected[key] for key in sorted(collected)),
                    )
                )
                return

            if delta_text or delta_thinking:
                yield LLMDelta(
                    text=delta_text if isinstance(delta_text, str) else "",
                    thinking=delta_thinking if isinstance(delta_thinking, str) else "",
                )
        raise LLMError(
            "流式响应没有收官帧（连接在生成中途断了）。"
            "**这不等于模型说了什么** —— 已经收到的半句话没有被当成回复。"
        )

    @staticmethod
    def _message_payload(message: ChatMessage) -> dict[str, Any]:
        """按 **Ollama 的**格式序列化一条消息。

        两处与 OpenAI 形状不同，都是照官方文档写的，不是猜的：
        - 助手消息里的工具调用是 `{"type":"function","function":{"index",name,arguments}}`，
          **没有 `id`**（结果是按顺序对应回去的）；
        - 工具结果用 `tool_name`，**不是 `tool_call_id`**。

        把 `arguments` 作为**对象**发出（而不是 JSON 字符串）—— Ollama 两边都收，
        但对象形式在抓包日志里可读，出问题时不用二次解析。
        """
        payload: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "type": "function",
                    "function": {
                        "index": index,
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                }
                for index, call in enumerate(message.tool_calls)
            ]
        if message.role == "tool" and message.name:
            payload["tool_name"] = message.name
        return payload

    def _parse(self, raw: dict[str, Any]) -> LLMReply:
        if "error" in raw:
            raise LLMError(f"LLM 服务报告错误：{raw['error']}")
        message = raw.get("message")
        if not isinstance(message, dict):
            raise LLMError(f"返回里没有 message 字段：{list(raw)[:8]}")

        content = message.get("content") or ""
        if not isinstance(content, str):
            raise LLMError("message.content 不是字符串")

        # 思考型模型把推理放在 `thinking`（Ollama 的字段名），
        # 它与 `content` 是两件事 —— 见 `LLMReply.thinking`。取不到就空着，不是错误。
        thinking = message.get("thinking") or ""
        if not isinstance(thinking, str):
            thinking = ""

        calls: list[ToolCall] = []
        for position, item in enumerate(message.get("tool_calls") or (), start=1):
            calls.append(self._parse_call(position, item))

        meta = {
            "model": raw.get("model", self.model),
            "done_reason": raw.get("done_reason"),
            "eval_count": raw.get("eval_count"),
            "prompt_eval_count": raw.get("prompt_eval_count"),
            "total_duration_ms": (
                round(raw["total_duration"] / 1_000_000, 1)
                if isinstance(raw.get("total_duration"), int)
                else None
            ),
        }
        return LLMReply(
            content=content, tool_calls=tuple(calls), thinking=thinking, meta=meta
        )

    def _parse_call(self, position: int, item: Any) -> ToolCall:
        return _tool_call_from(position, item)


def _tool_call_from(position: int, item: Any) -> ToolCall:
    """把一条工具调用解析成 `ToolCall`。**两个客户端共用这一份。**

    分成两份的后果是具体的：Ollama 与 OpenAI 兼容服务的差别只有
    "参数是对象还是 JSON 字符串"和"id 有没有"两处，
    而两份实现迟早会在某一种输入上不一致 —— 那时"流式与一次性给出同一个回复"
    这句话在一条路上成立、另一条路上不成立，且没人知道。
    """
    if not isinstance(item, dict):
        raise LLMError(f"tool_calls[{position}] 不是对象")
    function = item.get("function")
    if not isinstance(function, dict):
        raise LLMError(f"tool_calls[{position}] 缺少 function")
    name = function.get("name")
    if not isinstance(name, str) or not name:
        raise LLMError(f"tool_calls[{position}] 没有工具名")

    arguments = function.get("arguments")
    if arguments is None:
        arguments = {}
    elif isinstance(arguments, str):
        # OpenAI 兼容服务把参数序列化成字符串（**并且会跨帧分片**，
        # 分片的拼接由调用方负责，这里只负责解析拼好的那一份）。
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"工具 {name} 的参数不是合法 JSON：{arguments[:200]}"
            ) from exc
    if not isinstance(arguments, dict):
        raise LLMError(f"工具 {name} 的参数不是对象（{type(arguments).__name__}）")

    # 服务方给了 id 就用它。这条在 OpenAI 兼容那条路上是**必须**的：
    # 工具结果要靠 `tool_call_id` 对应回去，而那个字段必须与请求里的一致。
    # Ollama 不给 id（结果是按顺序对应回去的），于是合成一个确定性 id。
    raw_id = item.get("id")
    call_id = (
        raw_id if isinstance(raw_id, str) and raw_id else f"call_{position}"
    )
    return ToolCall(call_id=call_id, name=name, arguments=arguments)


def thinking_without_answer_note(reply: LLMReply) -> str | None:
    """**「只想了、没说」** —— 思考型模型的一种正常失败形态。

    真实返回：`eval_count=93`、`done_reason=stop`、`content=""`，而
    `message.thinking` 里是完整的一段推理。若只看 `content`，
    这会被误诊成"生成出了问题"；而真相是模型想完了却没落到回答上。

    这里把它的思考**截断着端出来**：用户看到"它在想什么"才能判断
    是问题太模糊，还是模型走偏了。但绝不当成回答 —— 所以只作附注。
    """
    if reply.content.strip() or not reply.thinking.strip():
        return None
    excerpt = reply.thinking.strip()
    if len(excerpt) > 300:
        excerpt = excerpt[:300] + "…"
    return (
        "模型只给出了思考内容，没有落到回答或工具调用上。它想的是："
        f"「{excerpt}」—— 可以说得更具体一点，或者再问一次。"
    )


def context_truncation_note(reply: LLMReply) -> str | None:
    """这一轮是不是**「我们把 prompt 发得比上下文窗口还大」**？是就返回一句人话。

    为什么值得单独一个函数：这个失败**看起来完全像"模型答了个空话"**。
    一次真实运行里，本地模型返回 `done_reason=length`、`prompt_eval_count=4095`、
    `eval_count=1`、`content=""` —— 信号在 meta 里，而现象是空回复。
    当时循环给用户的解释是"本地模型这一轮生成出了问题"，
    那句话**是错的**：模型没问题，是我们发的 7000 token 装不进它拿到的 4096。

    这正是本项目反复栽的那一类：系统用一个理由解释自己的行为，而那个理由没人验证过。
    判据取自服务自己报的数字，因此它是可核对的，不是猜的。
    """
    meta = reply.meta
    if meta.get("done_reason") != "length":
        return None
    generated = meta.get("eval_count")
    # `eval_count <= 1` 是"一个 token 都没生成出来"的特征。
    # 生成到了上限（例如我们真的设了 num_predict）不属于这一种，此时不猜。
    if not isinstance(generated, int) or generated > 1:
        return None
    evaluated = meta.get("prompt_eval_count")
    return (
        "这一轮的 prompt 装不进模型的上下文窗口："
        f"服务只处理了 {evaluated} 个 token 就没了余量，"
        f"而我们发出去的是整份工具声明加会话状态（14 个工具约 1.7 万字符）。"
        "把窗口调大（`MOUXUE_LLM_NUM_CTX`），或者减少同时暴露的工具数。"
    )

    # ---- 模型清单 ------------------------------------------------------ #

    def list_models(self, *, get: HttpGet | None = None) -> list[str]:
        """`GET {base_url}/api/tags` → `["qwen3.6:27b", …]`。

        Ollama 的字段是 `models[].name`（有的版本还有 `model`）。
        两种都认，且**去重后排序** —— 界面上让它稳定，不要每次刷新都换顺序。
        """
        fetch = get or (
            lambda url, headers, timeout: _urllib_get(url, headers, timeout)
        )
        raw = fetch(f"{self.base_url}/api/tags", {}, self.timeout)
        items = raw.get("models")
        if not isinstance(items, list):
            raise LLMError(
                f"{self.base_url}/api/tags 里没有 models 列表。"
                "如果这个地址其实是个 OpenAI 兼容服务，请把服务商选成对应的那家"
                "（它会走 /v1/models）。"
            )
        names: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("name", "model"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    names.add(value)
                    break
        return sorted(names)


class OpenAICompatClient:
    """**商用 API** 的客户端：`POST {base_url}/chat/completions`（OpenAI 兼容形状）。

    ## 为什么它是主路，而 Ollama 是兜底

    DeepSeek / OpenAI / 通义 / Moonshot / 智谱 都发这一个协议，
    所以**一个客户端覆盖了产品实际会用的全部模型**；换服务方只改
    `MOUXUE_LLM_BASE_URL` 与 `MOUXUE_LLM_MODEL`，不碰代码。
    本地 Ollama 保留下来是因为它不需要联网也不需要密钥，适合离线调试 ——
    但它的能力不足以驱动"讨论 → 改计划"这条闭环（实测：同一个提示词，
    本地 9B 只想不答，27B 同样只思考）。

    ## 与 Ollama 客户端的三处**协议差异**（都是实测的，不是照文档猜的）

    1. **消息里的工具调用**：OpenAI 要 `tool_calls[].id` + `arguments` 是
       **JSON 字符串**；工具结果用 `tool_call_id` 对应回去。
       Ollama 那边不要 id、参数给对象、结果用 `tool_name`。
    2. **流式是 SSE**（`data: {...}` + `data: [DONE]`），不是 NDJSON。
    3. **参数分片**：OpenAI 的流式把 `arguments` **切碎了逐帧发**，
       要按 `index` 把字符串拼起来再解析。Ollama 一帧给全。

    第 3 条是这里最容易写错、也最难发现的一处：忘了拼，得到的
    就是一个"参数不是合法 JSON"的报错，而它看起来像模型说错了话。
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str = "",
        timeout: float = 300.0,
        transport: HttpPost | None = None,
        stream_transport: HttpStream | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # 密钥只留在这里，**不进 meta、不进日志**（它会出现在 trace 里，
        # 而 trace 是给用户看的）。
        self._api_key = api_key
        if transport is not None:
            self._post = transport
        else:
            self._post = lambda url, payload, timeout: _urllib_post(
                url, payload, timeout, headers=self._headers()
            )
        if stream_transport is not None:
            self._stream = stream_transport
        else:
            self._stream = lambda url, payload, timeout: _urllib_sse(
                url, payload, timeout, headers=self._headers()
            )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _message_payload(self, message: ChatMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": message.role}
        # 助手消息带工具调用时 `content` 允许为 null；带内容的工具结果也一样。
        if message.role == "assistant" and message.tool_calls:
            payload["content"] = message.content or None
            payload["tool_calls"] = [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        # **字符串**，不是对象 —— 这一条与 Ollama 相反。
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
            return payload
        if message.role == "tool":
            # 结果靠 `tool_call_id` 对应回去（Ollama 用 `tool_name`）。
            # **用 `tool_call_id`、不是 `name`**：`name` 是工具名，
            # 把它当 id 发出去，服务端的反应是"找不到这次调用"，
            # 而现象是模型忽然看不见工具结果 —— 于是它会重新调一遍同一个工具。
            if not message.tool_call_id:
                raise LLMError(
                    "工具结果缺少 tool_call_id：OpenAI 兼容服务靠它把结果"
                    "对应回某一次调用。没有它，这一轮会失败在一个看不出原因的地方。"
                )
            payload["tool_call_id"] = message.tool_call_id
            payload["content"] = message.content
            return payload
        payload["content"] = message.content
        return payload

    def _payload(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSchema], *, stream: bool
    ) -> dict[str, Any]:
        """**唯一的请求构造点**（与非流式/流式共用，理由同 Ollama 那边）。

        **刻意不发 `max_tokens`**：那是**输出上限**，与我们给 Ollama 的
        `num_ctx`（上下文窗口）不是同一件事。发一个来路不明的上限，
        后果是回答被**静默截断**，而现象是"模型答到一半停了"。
        要限长就显式配一个输出上限，而不是拿窗口当它用。
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._message_payload(m) for m in messages],
            "stream": stream,
        }
        if tools:
            # 工具声明的形状两个协议**恰好一样**（都是 `{"type","function":{…}}`），
            # 所以这里与 Ollama 那边共用 `ToolSchema.as_dict()` ——
            # 不要在这里再包一层 `{"type","function"}`：那会造出嵌套两层的声明，
            # 而服务端的报错是"tools[i].function 缺少 name"（看不出是我们包多了）。
            payload["tools"] = [schema.as_dict() for schema in tools]
        return payload

    def chat(
        self, messages: Sequence[ChatMessage], *, tools: Sequence[ToolSchema] = ()
    ) -> LLMReply:
        raw = self._post(
            self.endpoint, self._payload(messages, tools, stream=False), self.timeout
        )
        return self._parse(raw)

    def _parse(self, raw: dict[str, Any]) -> LLMReply:
        if "error" in raw:
            raise LLMError(f"LLM 服务报告错误：{raw['error']}")
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMError(f"返回里没有 choices：{list(raw)[:8]}")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise LLMError("choices[0] 不是对象")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise LLMError(f"choices[0].message 不是对象：{list(choice)[:8]}")

        content = message.get("content") or ""
        if not isinstance(content, str):
            # 有些服务在工具调用时给 `content: null`，那是**正常**的。
            content = ""
        thinking = _reasoning_of(message)

        calls: list[ToolCall] = []
        for position, item in enumerate(message.get("tool_calls") or (), start=1):
            calls.append(_tool_call_from(position, item))

        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        meta = {
            "model": raw.get("model", self.model),
            "done_reason": choice.get("finish_reason"),
            "eval_count": usage.get("completion_tokens"),
            "prompt_eval_count": usage.get("prompt_tokens"),
            "total_duration_ms": None,  # 这个协议不报耗时
        }
        return LLMReply(
            content=content, tool_calls=tuple(calls), thinking=thinking, meta=meta
        )

    def stream(
        self, messages: Sequence[ChatMessage], *, tools: Sequence[ToolSchema] = ()
    ) -> Iterator[LLMDelta]:
        """逐帧转发 SSE。收官帧是最后一条带 `finish_reason` 的帧。

        **与 `chat` 给出同一个回复**（`LLMClient` 契约）：
        - 文本与思考按帧累积；
        - **工具调用的 `arguments` 要按 `index` 拼字符串**（见类 docstring 第 3 条），
          拼完再解析 —— 每一帧单独解析必然失败；
        - `finish_reason` 那一帧可能是空的（只有 usage），所以收官帧
          **不能**只靠它来判定内容。
        """
        frames = self._stream(
            self.endpoint, self._payload(messages, tools, stream=True), self.timeout
        )
        text: list[str] = []
        thinking: list[str] = []
        # index → {"id":…, "name":…, "arguments": "拼到一半的 JSON 字符串"}
        partial: dict[int, dict[str, str]] = {}
        usage: dict[str, Any] = {}
        finish_reason: str | None = None
        last_raw: dict[str, Any] = {}

        for raw in frames:
            if "error" in raw:
                raise LLMError(f"LLM 服务报告错误：{raw['error']}")
            if isinstance(raw.get("usage"), dict):
                usage = raw["usage"]
            last_raw = raw
            choices = raw.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                continue
            if isinstance(choice.get("finish_reason"), str):
                finish_reason = choice["finish_reason"]
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue

            piece = delta.get("content") or ""
            if isinstance(piece, str) and piece:
                text.append(piece)
                yield LLMDelta(text=piece)
            reason = _reasoning_of(delta)
            if reason:
                thinking.append(reason)
                yield LLMDelta(thinking=reason)

            for offset, item in enumerate(delta.get("tool_calls") or ()):
                if not isinstance(item, dict):
                    continue
                function = item.get("function")
                function = function if isinstance(function, dict) else {}
                index = function.get("index")
                if not isinstance(index, int):
                    index = offset
                slot = partial.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if isinstance(item.get("id"), str) and item["id"]:
                    slot["id"] = item["id"]
                if isinstance(function.get("name"), str) and function["name"]:
                    slot["name"] = function["name"]
                fragment = function.get("arguments")
                if isinstance(fragment, str):
                    slot["arguments"] += fragment

        if not partial and finish_reason is None:
            raise LLMError(
                "流式响应里既没有 finish_reason 也没有任何一块内容"
                "（连接在生成中途断了）。**这不等于模型说了什么** —— "
                "已经收到的半句话没有被当成回复。"
            )

        calls: list[ToolCall] = []
        for position, index in enumerate(sorted(partial), start=1):
            slot = partial[index]
            calls.append(
                _tool_call_from(
                    position,
                    {
                        "id": slot["id"],
                        "function": {
                            "name": slot["name"],
                            "arguments": slot["arguments"] or "{}",
                        },
                    },
                )
            )

        yield LLMDelta(
            reply=LLMReply(
                content="".join(text),
                thinking="".join(thinking),
                tool_calls=tuple(calls),
                meta={
                    "model": last_raw.get("model", self.model),
                    "done_reason": finish_reason,
                    "eval_count": usage.get("completion_tokens"),
                    "prompt_eval_count": usage.get("prompt_tokens"),
                    "total_duration_ms": None,
                },
            )
        )


    # ---- 模型清单 ------------------------------------------------------ #

    def list_models(self, *, get: HttpGet | None = None) -> list[str]:
        """`GET {base_url}/models` → `["deepseek-chat", …]`。

        OpenAI 的返回是 `{"data": [{"id": …}]}`，但**兼容层并不都守规矩**：
        实测见过 `{"data": {"models": [...]}}`（把 data 写成对象）与
        直接一个数组。这里三种都认，认不出来就报一句能读的错，
        而不是回一个空列表 —— 空列表会让人以为"这家没有模型"。
        """
        fetch = get or (
            lambda url, headers, timeout: _urllib_get(url, headers, timeout)
        )
        raw = fetch(f"{self.base_url}/models", self._headers(), self.timeout)

        containers: list[Any] = [raw.get("data"), raw.get("models"), raw.get("result")]
        if isinstance(raw.get("data"), dict):
            containers.insert(0, raw["data"].get("models"))
        for container in containers:
            names = _model_ids(container)
            if names:
                return names
        raise LLMError(
            "没能从 `/models` 的返回里认出模型清单（见过的形状：`data[].id`、"
            "`data.models[]`、`models[].name`）。"
            f"它返回的顶层键是：{sorted(raw)[:8]}。"
            "**可以手动填模型名** —— 拉列表只是为了方便，不是必需的。"
        )


def _model_ids(container: Any) -> list[str]:
    """从各种形状里抠出模型名。认不出就返回空列表（由调用方决定怎么报错）。"""
    if not isinstance(container, list):
        return []
    names: set[str] = set()
    for item in container:
        if isinstance(item, str) and item:
            names.add(item)
            continue
        if not isinstance(item, dict):
            continue
        for key in ("id", "name", "model"):
            value = item.get(key)
            if isinstance(value, str) and value:
                names.add(value)
                break
    return sorted(names)


def _reasoning_of(piece: dict[str, Any]) -> str:
    """思考型模型把推理放在哪个字段 —— **两个名字都要认**。

    DeepSeek 用 `reasoning_content`，另一些 OpenAI 兼容服务用 `reasoning`。
    只认一个的后果是"思考看不见"，而那是我们在界面上明确承诺要分开显示的东西
    （见 `LLMReply.thinking` 与 M9 §6.4.1）。
    """
    for key in ("reasoning_content", "reasoning"):
        value = piece.get(key)
        if isinstance(value, str) and value:
            return value
    return ""
