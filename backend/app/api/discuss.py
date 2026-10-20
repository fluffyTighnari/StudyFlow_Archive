"""讨论一个计划项，并且**真的能改计划**。两个端点，同一次讨论。

```
用户点某个计划项说出看法
  → Agent 循环（ReAct，步数预算）
      → dispatcher 执行工具（校验参数、强制作用域、留轨迹）
          → session_domain 写沙箱（改计划 / 记笔记 / 放 stage）
          → handoff → Router 写全局（只在它提议沉淀时）
  → 回复 + 轨迹落进 chat_message
```

| 端点 | 形态 | 给谁用 |
|---|---|---|
| `POST /{id}/discuss` | 一次返回完整结果 | 测试、脚本、`livedemo` —— 不需要过程 |
| `POST /{id}/discuss/stream` | SSE 逐帧 | 界面 —— 需要"边生成边看" |

两条路**共用同一个循环体**（`AgentLoop._walk`），差别只在
`AgentLoop._ask` 里"一次性取回复还是逐帧收"。这不只是省代码：
两条路各写一份循环，就会在"某条路上修了、另一条忘了"的时候安静地分叉，
而分叉的表现是**界面看到的与库里存的不一样**。

## 三件关于这两个端点必须说清楚的事

1. **它们不在「会话沙箱」之外开新路。** 端点自己不写任何东西 —— 写全都发生在
   工具层，走的是 P4 已经定下的 `session_domain.writes`。
   所以「沙箱里的写入不经过 Router」这条边界在这里没有被绕过，只是多了一个触发者。

2. **`step_id` 出现 = 用户点了那个计划项。** 这就是 `propose_plan_change` 需要的
   「用户本人要求改」那条证据（M8 §4.4 的三类证据之一）。它由**界面**提供，
   不由模型自述 —— 这是本轮唯一一处"用户手势"来源，见 `gestures`。

3. **这两个请求都会长时间持有数据库事务。** 本地 27B 模型的一次推理可能几十秒，
   而整个讨论在这一个事务里（工具写入 + 两条 chat_message + commit 都在最后）。
   SQLite 在这期间持有写锁。单人自用可以接受，但它是一条**真实的限制**，
   不是"以后再说"：多开一个界面窗口就会撞上 `database is locked`。
   **流式那条路把这条限制拉得更长** —— 它从"模型算完"变成"模型算完 + 用户看完"，
   而用户可能看很久。正确的修法是每一步工具写入各用一个短事务，
   而那是要单独裁定的改动（它会让"一次讨论"不再是原子操作）。见模块末尾「已知限制」。

## 流式特有的两件事（只在这条路上存在）

- **断线要留档**：客户端可能在任何一帧之后消失。这时回答没写完，
  半句话不能被当成回答，但这次讨论必须留下记录 —— 见 `DISCONNECTED_NOTE`
  与 `StopReason.CLIENT_DISCONNECTED`。
- **预检必须在开流之前做完**：响应头一旦发出就改不了状态码。
  所以"会话在不在""锚点定不定得下来""这个状态能不能聊"三件事都在
  返回 `StreamingResponse` 之前查 —— 否则同一个错误会在 `/discuss` 上
  是 404、在 `/discuss/stream` 上变成 200 + 一条事件，
  客户端再也分不清"没有计划"与"模型挂了"。
"""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterator
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.agent.context import build_memory, build_meter, record_usage
from app.agent.events import AgentEvent, EventType
from app.agent.external import ExternalToolbox
from app.agent.llm import ChatMessage, LLMClient
from app.agent.llm_factory import make_client_from_profile
from app.agent.loop import AgentLoop, StopReason
from app.agent.tools import GESTURE_PLAN_CHANGE
from app.api.deps import get_session
from app.db import session_scope
from app.session_domain import reads, writes
from app.session_domain.enums import ChatRole, ContextScope

router = APIRouter(prefix="/api/sessions", tags=["discuss"])

#: ★ **已废弃**：历史不再是"最近 N 条原话"。
#:
#: P6+.6 之前这里是 `HISTORY_LIMIT = 6`，取最近 6 条。它的坏处不是记得少，
#: 而是**记忆的形状是错的**：第 7 轮之前说过的约束、约定一律消失，
#: 而消失是**静默**的 —— 模型不会说"我不记得了"，它会基于残缺的上下文
#: 给出一个看起来正常的回答。用户看到的现象是"聊着聊着它开始胡说"，
#: 而原因（六条窗口）在界面上没有任何痕迹。
#:
#: 现在由 `agent.context.build_memory` 装配：能放多少放多少，
#: 放不下的压成摘要，并把用量摊开给用户看。
#: 这个常量留着是为了让"它曾经是 6"这件事可查 —— 与 `_history` 同理。
HISTORY_LIMIT_DEPRECATED = 6


class _Request(BaseModel):
    """`extra="forbid"`：拼错的字段要报错，不能静默忽略。

    与 `api/sedimentation.py` 同一姿态 —— 那条缺陷（未知写入字段被静默丢弃）
    已经在 P4 栽过一次。
    """

    model_config = ConfigDict(extra="forbid")


class DiscussRequest(_Request):
    message: str = Field(min_length=1, description="用户说的话。")
    step_id: str | None = Field(
        default=None,
        description=(
            "讨论**哪一项**。留空 = 当前这一步（第一个还没做完的步骤）。"
            "**带上它就意味着用户在界面上点了那个计划项** —— "
            "这是「用户本人要求改」那条证据的来源。"
        ),
    )
    gestures: list[str] = Field(
        default_factory=list,
        description=(
            "用户在**界面**上明确做过的事，由界面提供（例如 `definition:goal` = "
            "他在侧页改过目标、`step_done:ST-002` = 他点了「完成」）。"
            "**这些不是模型的声明** —— 模型拿不到这个字段，只能被它限制。"
        ),
    )

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("空消息不讨论")
        return value


class TraceItem(BaseModel):
    step: int
    call_id: str
    tool: str
    scope: str
    arguments: dict[str, Any]
    status: str
    message: str
    duration_ms: float


class DiscussResponse(_Request):
    """一次讨论的结果。

    `stop_reason` 是**必须出现**的字段，不是诊断信息：
    「它说完了」与「它用完步数了」是两件对用户含义完全不同的事，
    界面要据此显示不同的话，而不能都渲染成一段正常回复。

    `thinking` 与 `context` 是 P6+.6 新增的，两者都是**用户明确要求的东西**：

    - `thinking`：完整思考过程。此前它只存在于流式帧里，刷新就没了。
    - `context`：token 用量与当前上下文状态（`ContextMeter.as_dict()`）。
      **每次响应都带** —— 用户要的是"时刻显示"，而不是"点一下才查"。
      做成一个独立端点会让界面必须额外发一次请求，于是它迟早会不同步；
      放在这里，它天然与这次回复是同一时刻的读数。
    """

    reply: str
    stop_reason: StopReason
    steps: int
    #: 这次讨论里系统实际执行过的工具调用 —— 用户明确要求能看到它做了什么。
    tool_trace: list[TraceItem] = Field(default_factory=list)
    llm_meta: dict[str, Any] = Field(default_factory=dict)
    #: 模型的完整思考过程（可能为空 —— 不思考的模型就是没有）。
    thinking: str = ""
    #: 这次讨论**之后**的上下文状态。见 `agent.context.ContextMeter`。
    context: dict[str, Any] = Field(default_factory=dict)
    #: 这一次讨论触发了一次上下文压缩 → 这是那条压缩记录的 id。
    compacted: str | None = None
    #: 压缩失败的原因。**不吞掉**：失败会让上下文继续膨胀，
    #: 而下一次就撞上真正的装不下。
    compact_error: str | None = None


def get_llm_client(request: Request) -> LLMClient:
    """取当前应用的 LLM 客户端。

    **测试通过替换 `app.state.llm_client` 来注入确定性替身** ——
    因此测试从不依赖本地 Ollama 是否在跑（M0 §12 的验收前提：无 LLM 也能治理）。

    ## 为什么每次请求都要看一眼库里有没有生效中的配置

    用户在这一步选了"配置可写、界面里填"，那句话的**实际含义**是：
    他在界面上切换模型之后，**下一句话就该用新模型**。
    如果只在 `lifespan` 里读一次（原来的写法），"切换"就要重启才生效 ——
    而那正是把配置放进库、做成一整个界面的意义所在。
    一个"填了、存了、也显示生效了，但真正说话时还是旧的那个"的配置层，
    比没有这个界面更坏：它让人以为改过了。

    构造一个客户端是**纯对象构造**（不发请求、不连网），所以每请求一次的成本
    可以忽略；真正贵的连接发生在 `chat`/`stream` 里。
    密钥从 `var/secrets/` 现读现用，不在 `app.state` 里存留 ——
    那样它至少不会随着进程快照、日志或调试器停留。
    """
    from app.llm_config import active_profile, read_secret

    factory = getattr(request.app.state, "session_factory", None)
    settings = getattr(request.app.state, "settings", None)
    if factory is not None and settings is not None:
        with session_scope(factory) as session:
            profile = active_profile(session)
        if profile is not None:
            return make_client_from_profile(
                profile,
                api_key=read_secret(profile.profile_id),
                timeout=settings.llm_timeout,
            )

    client = getattr(request.app.state, "llm_client", None)
    if client is None:  # pragma: no cover - 只在配置缺失时发生
        raise RuntimeError(
            "应用没有配置 LLM 客户端。这是配置错误，不是运行期故障 —— "
            "见 `create_app` 里根据 `Settings.llm_provider` 构造的那一段。"
        )
    return client


def get_agent_max_steps(request: Request) -> int:
    """一次讨论最多走几步。由配置（`MOUXUE_AGENT_MAX_STEPS`）决定。

    中间这一层的意义：让端点**不**直接读 `Settings`。请求边界不该知道
    配置从哪来（与 `deps.get_session` 读 `app.state.check_invariants` 同一个理由）。
    """
    return int(getattr(request.app.state, "agent_max_steps", 8))


def get_external_toolbox(request: Request) -> ExternalToolbox | None:
    """外挂能力（联网搜索 / MCP）。**默认 `None`** —— 没配就没有。

    与 `get_agent_max_steps` 同类：端点在**构造循环时**读它一次。
    工具箱在进程启动时组装好、活到进程结束（它持有子进程），
    所以这里不重建、也不在这里关 —— 那是 `lifespan` 的事。
    """
    return getattr(request.app.state, "external", None)


@router.post("/{session_id}/discuss", response_model=DiscussResponse)
def discuss(
    session_id: str,
    body: DiscussRequest,
    session: Session = Depends(get_session),
    client: LLMClient = Depends(get_llm_client),
    max_steps: int = Depends(get_agent_max_steps),
    external: ExternalToolbox | None = Depends(get_external_toolbox),
) -> DiscussResponse:
    # 会话不存在 → 404；没有计划 → 404 `not_found`（`require_plan` 抛的）。
    # **在任何 LLM 调用之前**先把它查出来：让一次注定失败的请求不花掉模型的时间。
    reads.get_session(session, session_id)

    # ★ 记忆：必要时先压缩，再取要带进上下文的那些消息。
    # 顺序是"先压后取"，理由见 `agent.context.build_memory` ——
    # 反过来的话，这次调用仍然带着即将被压掉的原话，注定还是超线。
    memory = build_memory(
        session,
        ContextScope.SESSION,
        session_id,
        client=client,
        limit=getattr(client, "num_ctx", 0) or 0,
    )

    # **先写用户那句话**，再跑循环。
    #
    # 这个顺序是被一次真实运行纠正过来的：原先两条消息都在循环之后写，
    # 于是"助手回复为空"会让那次写入失败、整个请求 422 ——
    # **用户说过的话一个字都没留下**。
    #
    # 两个理由支持先写：
    # ① 用户的话不该因为模型/写入的失败而消失；
    # ② 状态机不允许时会**在花掉一次模型调用之前**就拒绝（`add_chat_message`
    #    自己会 `assert_writable`）—— 一次注定失败的请求不该让本地模型白跑 150 秒。
    # 代价只有一个：这个事务从第一句话开始就持有写锁，而它本来也要为工具的写入持有。
    writes.add_chat_message(session, session_id, content=body.message, role=ChatRole.USER)

    gestures = set(body.gestures)
    if body.step_id:
        # 点开一个计划项来讨论 = 他在要求改它。这条推断由界面触发、不由模型声称。
        gestures.add(GESTURE_PLAN_CHANGE)

    loop = AgentLoop(
        session,
        session_id,
        client,
        max_steps=max_steps,
        user_gestures=frozenset(gestures),
        external=external,
    )
    result = loop.run(body.message, step_id=body.step_id, history=memory.history)

    # 循环保证 `reply` 非空（空回复归到 `llm_error`）。这里不再加一层兜底：
    # 一个"理论上到不了"的分支写在这里，会让人以为它是可达的，
    # 而真正该修的是上面那个不变量。测试钉着它。
    writes.add_chat_message(
        session,
        session_id,
        content=result.reply,
        role=ChatRole.ASSISTANT,
        # 停止理由进 tags：它让聊天记录自己说清楚"这条回复是什么性质"，
        # 而不必为此加一个字段或一张表。
        tags=[f"stop:{result.stop_reason.value}"],
        tool_trace=[entry.as_dict() for entry in result.traces] or None,
        thinking=result.thinking or None,
        prompt_tokens=result.llm_meta.get("prompt_eval_count"),
        completion_tokens=result.llm_meta.get("eval_count"),
        cache_hit_tokens=result.llm_meta.get("cache_hit_tokens"),
        cache_miss_tokens=result.llm_meta.get("cache_miss_tokens"),
        # ★ 锚点落库（P15）：事后"看推断理由"要能重放**当时**的上下文节。
        # 它从循环上取（`_open_context` 里写的），不在这里重算一遍 ——
        # 算两遍意味着两处可能给出不同的答案，而其中一个会进留档。
        anchor_step_id=loop.last_anchor or None,
    )

    # ★ 用量回填：把这次**实测**的 prompt token 数落到刚写的那条助手消息上。
    # 下一轮就靠它判断"该不该压缩" —— 这是整个记忆机制唯一的读数来源。
    record_usage(session, ContextScope.SESSION, session_id, meta=result.llm_meta)
    after = build_meter(
        session, ContextScope.SESSION, session_id, limit=getattr(client, "num_ctx", 0) or 0
    )

    return DiscussResponse(
        reply=result.reply,
        stop_reason=result.stop_reason,
        steps=result.steps,
        tool_trace=[TraceItem(**entry.as_dict()) for entry in result.traces],
        llm_meta=result.llm_meta,
        thinking=result.thinking,
        context=after.as_dict(),
        compacted=memory.new_compact_id,
        compact_error=memory.compact_error,
    )


def _history(session: Session, session_id: str) -> list[ChatMessage]:
    """**已废弃**：历史现在由 `agent.context.build_memory` 装配。

    留着这个函数是因为流式端点仍有一个调用点；它返回的是
    "没被压掉的原文 + 摘要头"，也就是 `build_memory` 的结果。
    刻意不再自己截最近 N 条 —— 那个硬截断正是 P6+.6 要修的东西。
    """
    raise NotImplementedError(
        "历史装配统一走 agent.context.build_memory —— "
        "不要在这里重新引入'取最近 N 条'的截断。"
    )


# --------------------------------------------------------------------------- #
# 流式讨论：POST /api/sessions/{id}/discuss/stream
# --------------------------------------------------------------------------- #


class EventStreamResponse(StreamingResponse):
    """`text/event-stream` 响应。两件事：媒体类型，以及**保证生成器被关掉**。

    ## 一、媒体类型

    `StreamingResponse.media_type` 是 `None`，于是 OpenAPI 里这条响应
    **没有 content-type** —— 文档上看不出它是 SSE。子类化把媒体类型变成类属性。

    它同时让「哪些端点是流式的」成为一个**可以被断言的事实**
    （`route.response_class`），而不是靠路径名去猜 —— 见
    `tests/api/test_api_boundaries.py::test_every_route_declares_a_response_model`。

    ## 二、保证同步生成器被关闭（**这条是被实测逼出来的**）

    `StreamingResponse` 对同步迭代器的处理是
    `iterate_in_threadpool(content)`，而那个包装器**没有 `finally`**：
    它只是 `as_iterator = iter(iterator)` 然后一轮轮 `next()`。
    于是当客户端断开、`send()` 抛 `OSError` 时（Starlette 1.8 在
    `spec_version >= 2.4` 下走的就是这条路），`stream_response` 里那个
    `async for` 被直接放弃 —— **同步生成器被留在挂起状态**，
    它的 `GeneratorExit` 只会在某次 GC 时（甚至只在进程退出时）被投进去。

    这意味着一件很要命的事：`stream_events` 里那段"断线也要留档"的代码
    会**在大多数情况下不执行**，而所有测试仍然全绿 ——
    因为那些测试直接 `close()` 生成器，走的不是这条路。

    所以这里显式关：

    ```
    try:     await super().stream_response(send)
    finally: self._sync_frames.close()
    ```

    为什么把钩子挂在 `stream_response` 上：它是**断开与正常结束都必经**的
    那一个点。正常跑完时 `close()` 是空操作；断开时它把 `GeneratorExit`
    投进生成器，于是留档那段代码确定性地执行。

    为什么在 `finally` 里 `close()` 是安全的（不会撞上
    `ValueError: generator already executing`）：`anyio.to_thread.run_sync`
    默认 `cancellable=False`，取消会被**推迟到线程函数返回之后** ——
    也就是说，`finally` 运行时没有任何线程还在 `next()` 里面。
    这一点依赖 anyio 的语义，而 Starlette 自己就是这么跑同步迭代器的。
    """

    media_type = "text/event-stream"

    def __init__(self, content: Any, **kwargs: Any) -> None:
        # 同步内容由我们自己持有引用 —— 否则没有任何地方拿得到它：
        # `body_iterator` 只是那层不关门的线程池包装。
        self._sync_frames = None if isinstance(content, AsyncIterable) else content
        super().__init__(content, **kwargs)

    async def stream_response(self, send: Any) -> None:
        try:
            await super().stream_response(send)
        finally:
            if self._sync_frames is not None:
                self._sync_frames.close()


#: 客户端断开、而回答还没写完时，留在库里的话。
#:
#: **必须是一句真话。** 这时我们手里只有半截回答，而"半截回答"被当成回答
#: 会让用户以为自己读到的是完整的。所以不存半截，存一句说明 ——
#: 用户回来时看到的是"这次讨论没留下结论"，而不是一段读起来通顺、
#: 实际缺了后半段的文字。
DISCONNECTED_NOTE = "（你在回答写完之前离开了，这次讨论没有留下结论。）"


def stream_events(
    session: Session,
    loop: AgentLoop,
    *,
    message: str,
    step_id: str | None,
    history: list[ChatMessage],
) -> Iterator[str]:
    """把一次讨论变成 SSE 帧序列。

    ## 为什么单独一个函数，而不是写在端点里的内联生成器

    因为**断线那一条路必须能被测**。端点返回的是一个 `StreamingResponse`，
    而 `TestClient` 会先把整个响应在 portal 里跑完再交给客户端
    （提前跳出 `iter_lines()` 并不会真的断开 —— 库里拿到的仍是
    `stop:completed`），所以"断开时到底发生了什么"在 HTTP 层测不出来。
    把这个生成器单独拿出来，测试就可以只取前几帧、然后 `close()`。

    ## 为什么循环是**传进来的**，不在这里构造

    因为锚点必须在**开流之前**定下来（见 `discuss_stream` 的说明）。
    循环构造与锚点解析都属于预检，预检不能发生在生成器里 ——
    生成器跑起来的时候，响应头已经发出去了。

    ## 断线（`GeneratorExit`）时的行为

    **把已经发生的事留下来。** 客户端断开时 Starlette 会关闭这个生成器，
    而此刻事务还没提交（`session_scope` 的退出栈在响应体发完之后才关），
    所以这里仍然能写。写什么：

    - 回答已经收官 → 与正常路径完全相同（回复 + 轨迹）；
    - 回答没收官 → `DISCONNECTED_NOTE` + `stop:client_disconnected`。

    为什么值得为它写一段代码：用户刷新一次页面就丢掉一整轮对话，
    而他会以为"什么都没发生" —— 这与 P4 修掉的"静默的数据丢失"是同一类。
    """
    session_id = loop.session_id
    written = False
    #: 已经发出去的最后一帧序号。`error` 帧必须接着它往下数 ——
    #: 契约是「`seq` 从 1 严格递增」，而异常路径不在循环里、拿不到循环的计数器。
    #: 早先这里写死 `seq=0`，于是同一路流里出现了 1,2,3…,0：客户端若按 seq
    #: 去重或排序，那一帧会被当成"重复/过期"丢掉 —— 而它恰恰是唯一一条
    #: 告诉用户"为什么没有回答"的信息。
    last_seq = 0

    def _persist(reply: str, stop: str, traces: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        nonlocal written
        if written:
            return
        written = True
        writes.add_chat_message(
            session,
            session_id,
            content=reply,
            role=ChatRole.ASSISTANT,
            tags=[f"stop:{stop}"],
            tool_trace=traces or None,
            # thinking 与用量**在每一条留档路径上都要写**，包括断线与异常 ——
            # 那两条路恰恰是最需要解释"它当时在想什么"的。
            thinking=loop.thinking or None,
            prompt_tokens=meta.get("prompt_eval_count"),
            completion_tokens=meta.get("eval_count"),
            cache_hit_tokens=meta.get("cache_hit_tokens"),
            cache_miss_tokens=meta.get("cache_miss_tokens"),
            # 锚点在这条路上**同样要写**（P15）：断线/异常那两条留档路径
            # 恰恰最需要回答"它当时站在哪一步上说话"。
            anchor_step_id=loop.last_anchor or None,
        )
        record_usage(session, ContextScope.SESSION, session_id, meta=meta)

    try:
        events = loop.run_stream(message, step_id=step_id, history=history)
        try:
            for event in events:
                last_seq = event.seq
                if event.type is EventType.DONE:
                    meta = dict(event.data.get("llm_meta") or {})
                    _persist(
                        str(event.data["reply"]),
                        str(event.data["stop_reason"]),
                        list(event.data["tool_trace"]),
                        meta,
                    )
                    # ★ 把上下文用量**加进收官帧**。用户要的是"时刻显示"，
                    # 而流式路径下这是唯一一个"这次讨论结束了"的时刻 ——
                    # 让他为了看用量再发一次请求，界面就必然有一段时间
                    # 显示的是上一轮的读数（而它看起来是对的）。
                    #
                    # 不新开一帧：`done` 是这条流的终态，在它之后再发东西
                    # 会让"seq 严格递增"与"done 即结束"这两条契约互相矛盾。
                    meter = build_meter(
                        session,
                        ContextScope.SESSION,
                        session_id,
                        limit=getattr(loop.client, "num_ctx", 0) or 0,
                    )
                    event = replace(
                        event,
                        data={
                            **event.data,
                            "context": meter.as_dict(),
                            "thinking": loop.thinking,
                        },
                    )
                yield event.to_sse()
        finally:
            # 显式关掉内层生成器。不等 GC：`run_stream` 里 `yield from` 的
            # `_walk` 可能在 `finally` 上做事（将来若加了"收尾写入"，
            # 靠 GC 决定它跑不跑是不可接受的）。
            # 已跑完时 `close()` 是空操作。
            events.close()
    except GeneratorExit:
        # 客户端走了。**把用户那句话的事实补完整** —— 一次讨论在库里
        # 至少要留下"发生过"，否则下次他回来看到的是一句自己说过的话，
        # 后面什么都没有，而系统也不记得自己没回答完。
        _persist(
            DISCONNECTED_NOTE,
            StopReason.CLIENT_DISCONNECTED.value,
            [entry.as_dict() for entry in loop.dispatcher.traces],
            # 断线时拿不到 `llm_meta`（循环还没收官），所以用量为空 ——
            # 那是对的：这一次调用的读数确实没拿到，
            # 而"没拿到"与"用量是 0"是相反的两件事。
            {},
        )
        raise
    except Exception as exc:  # noqa: BLE001 - 见下面的说明
        # **流已经开始，改不了状态码了。** 此刻唯一的出路是把错误作为一条
        # 事件发出去，让界面能显示"出错了"，而不是让连接无声地断掉
        # （后者在界面上表现为"卡住"，而在服务端只有一行栈）。
        #
        # 刻意宽 catch：走到这里说明工具层或写库抛了契约之外的异常。
        # 吞掉它比让它变成「响应已开始却抛异常」要好 —— 后者客户端只看到
        # 连接断，拿不到任何原因。
        #
        # 同时**留下一条痕迹**（与 `stop:llm_error` 那条路径同一个姿态：
        # 那时循环自己产出"我没能拿到模型的回复"，端点照常入库）。
        # 不留的话，用户刷新页面只会看到自己那句没有回音的话，
        # 分不清是"回答丢了"还是"请求失败了"。
        # `type(exc).__name__` 一并写进内容与事件：它是分辨"哪一类坏"的唯一线索。
        kind = type(exc).__name__
        _persist(
            f"（这次讨论中断了，没有得出结论。原因：{kind}: {exc}）",
            StopReason.INTERNAL_ERROR.value,
            [entry.as_dict() for entry in loop.dispatcher.traces],
            {},
        )
        yield AgentEvent(
            type=EventType.ERROR,
            seq=last_seq + 1,
            step=0,
            data={"error": str(exc), "kind": kind},
        ).to_sse()


@router.post(
    "/{session_id}/discuss/stream",
    response_class=EventStreamResponse,
    status_code=200,
)
def discuss_stream(
    session_id: str,
    body: DiscussRequest,
    session: Session = Depends(get_session),
    client: LLMClient = Depends(get_llm_client),
    max_steps: int = Depends(get_agent_max_steps),
    external: ExternalToolbox | None = Depends(get_external_toolbox),
) -> StreamingResponse:
    """与 `/discuss` 同一次讨论，但**过程也发出来**。

    ## 为什么是 POST + `fetch`，而不是 GET + `EventSource`

    原生 `EventSource` 只支持 GET，而讨论要带 body（消息、`step_id`、手势）。
    把消息塞进 query string 有两个代价：长度受限，且**用户说的话会进日志**
    （访问日志、浏览器历史）。所以这条流由前端用 `fetch` + `ReadableStream` 读，
    事件格式仍是标准的 SSE —— 格式标准，读取方式按需要选。

    ## 为什么先做预检、再开始流

    `reads.get_session`、会话可写性、以及**锚点能不能定下来**，
    都在**返回 `StreamingResponse` 之前**做完。
    原因是 HTTP 的硬约束：**响应头一旦发出，就再也改不了状态码**。
    若把"会话不存在"留到流里，客户端只会收到 200 + 一条 error 事件 ——
    而 404、409 是它能自动分辨并正确处理的东西。

    锚点那一条是被自己的实现纠正过来的：`/discuss` 在"会话没有计划"时抛 404，
    而 `/discuss/stream` 起初会把同一个错误推迟到流里变成一条事件。
    **同一个错误在两个端点上状态码不同**，客户端于是分不清
    "没有计划"与"模型挂了"。修法是让预检也把锚点定下来 ——
    用的是循环自己的 `resolve_anchor`，没有第二份锚点规则。
    """
    reads.get_session(session, session_id)

    memory = build_memory(
        session,
        ContextScope.SESSION,
        session_id,
        client=client,
        limit=getattr(client, "num_ctx", 0) or 0,
    )
    # 与 `/discuss` 完全同一个顺序与理由：先写用户那句话（它不该因为
    # 模型或写入的失败而消失），并且让状态机在花掉一次模型调用之前就拒绝。
    writes.add_chat_message(session, session_id, content=body.message, role=ChatRole.USER)

    gestures = set(body.gestures)
    if body.step_id:
        gestures.add(GESTURE_PLAN_CHANGE)

    loop = AgentLoop(
        session,
        session_id,
        client,
        max_steps=max_steps,
        user_gestures=frozenset(gestures),
        external=external,
    )
    # 预检：锚点定不下来（没有计划 / 计划里没有步骤）就不要开始流。
    loop.resolve_anchor(body.step_id)

    frames = stream_events(
        session,
        loop,
        message=body.message,
        step_id=body.step_id,
        history=memory.history,
    )
    return EventStreamResponse(
        frames,
        headers={
            # 不缓存：一次讨论的流没有任何可复用的部分。
            "Cache-Control": "no-cache",
            # 关掉 nginx 一类反代的缓冲 —— 开着的话事件会被攒起来一起发，
            # 于是"流式"在观感上退化成一次性返回，且没有任何报错。
            "X-Accel-Buffering": "no",
        },
    )

# --------------------------------------------------------------------------- #
# 已知限制（真实存在，不是"以后再说"）
# --------------------------------------------------------------------------- #
#
# 1. **一次讨论持有一个长事务，流式把这段时间拉得更长。**
#    工具写入、两条 chat_message、提交全在最后发生，SQLite 在这期间持有写锁。
#    流式路径的持有时间从"模型算完"变成"模型算完 + 用户看完"。
#    症状：多开一个界面窗口就会 `database is locked`（单人自用可以接受，
#    但它会在最不方便的时候出现）。修法是把每一步工具写入拆成短事务，
#    而那会让"一次讨论"不再是原子操作 —— 那是一个要单独裁定的改动。
#
# 2. **没有心跳帧。** 本地大模型在"读 prompt"那段时间（实测可以到几十秒）
#    一个 token 都不吐，这段时间这条流上没有任何字节在动。
#    我们靠**提前发出 `stage: wait_model` 那一帧**让界面有东西可显示 ——
#    但那是"界面知道自己在等"，不是"连接被保活"。
#    浏览器对 `fetch` 的流没有空闲超时，中间的反代可能有；
#    真接反代时要么调它的读超时，要么在生成线程之外加一条心跳。
#    没有现在就加心跳，是因为它要求"生成"与"发送"解耦
#    （一条队列 + 一个线程），而那会把整个循环的同步性拆掉 ——
#    那是另一个量级的改动。
