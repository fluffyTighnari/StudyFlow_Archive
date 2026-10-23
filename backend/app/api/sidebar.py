"""侧边栏常驻通用模型的端点（P6+.6）。

## 它与会话端点共享什么、不共享什么

**共享**（这是本模块存在的意义）：
- 记忆与计量：`agent/context.build_memory` / `build_meter` / `record_usage`；
- 压缩记录表 `context_compact`（靠 `scope` 判别列区分两者）；
- 循环体 `AgentLoop._walk`（`SidebarLoop` 只换掉五个钩子）。

**不共享**：
- 消息表（`sidebar_message` vs `chat_message`）—— 归属约束不同；
- 上下文包（全局那一半 vs 含计划的全部）;
- 工具集（4 个 vs 15 个）;
- **状态机** —— 侧边栏没有终态。

## ★ 为什么没有"侧边栏会话 id"这种参数

侧边栏**只有一个**。给它一个 `sidebar_id` 参数会让"能不能开两个"
变成一个看起来可配置、实际没有实现的问题；而一个"传什么都没区别"
的参数是最容易让人误判 API 语义的那类参数。

它用的是单例常量 `SIDEBAR_SCOPE_ID` —— 于是"侧边栏存在吗"这个问题
不需要查库就能回答，而"侧边栏有几条消息"仍然查得到。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.agent.context import build_memory, build_meter, meter_payload, record_usage
from app.agent.external import ExternalToolbox
from app.agent.llm import LLMClient, LLMError
from app.agent.sidebar import SidebarLoop, render_sidebar_context
from app.api.deps import get_session
# ★ 从 `discuss` 里 import 这两个依赖，而不是复制一份 —— 它们是
# "哪一次模型调用该用哪个客户端 / 步数上限是多少"的**唯一**定义。
# 复制会让侧边栏在某次配置变更后仍用旧上限，而那种不一致
# 表现为"两个界面同样一句话，一个有 8 步一个只有 8 步"以外的行为差异。
from app.api.discuss import (
    get_agent_max_steps,
    get_external_toolbox,
    get_llm_client,
)
from app.session_domain import reads, writes
from app.session_domain.enums import ChatRole, ContextScope, SIDEBAR_SCOPE_ID
from app.session_domain.schemas import ChatMessageRead
from app.session_domain.sidebar_context import assemble_sidebar_context

router = APIRouter(prefix="/api/sidebar", tags=["sidebar"])


class _Request(BaseModel):
    model_config = {"extra": "forbid"}


def _limit(client: LLMClient) -> int:
    """窗口上限。**从同一个来源取** —— 与会话侧逐字相同。

    写成一个函数是因为它有**两个**调用点（压缩与计量），
    而两处若各写一遍，某天改了 `num_ctx` 的读法就会出现
    "压缩按 8192 判断、计量按 16384 显示"这种没人能解释的不一致。
    """
    return int(getattr(client, "num_ctx", 0) or 0)


class SidebarMessageRead(ChatMessageRead):
    """侧边栏的一条消息。

    继承会话的消息读模型是**刻意的**：两者给界面的东西**完全相同**
    （角色、正文、标签、工具轨迹、思考过程、实测用量）。
    分成两个形状会让前端写两套渲染，而它们显示的应该是同一种气泡。
    """

    #: 覆盖父类的 `msg_id` 说明 —— 前缀不同（`SM-` vs `CM-`），
    #: 而这是唯一一处两者不一样的地方，值得写出来。
    msg_id: str = Field(description="侧边栏消息 id，形如 `SM-0001`。")


class SidebarDiscussRequest(_Request):
    message: str = Field(min_length=1, description="用户说的话。")

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("空消息不讨论")
        return value


class SidebarStateResponse(_Request):
    """侧边栏的状态：消息 + 上下文计量 + 它看得见什么。

    `context` 是**用户明确要求**的「时刻显示 token 使用量」；
    `sections` 让它可被解释（"为什么它说不知道你在哪一步"能查得到）。
    """

    messages: list[SidebarMessageRead]
    context: dict[str, Any]
    #: 它这次看得见哪些工具。**4 个**，且这个数字是推导出来的
    #: （`SIDEBAR_TOOL_SCOPES`），不是手写的名单。
    tools: list[str]
    #: 上下文包的节报告 —— 界面据此解释"它看到了什么、漏了什么"。
    sections: list[dict[str, Any]] = Field(default_factory=list)


class SidebarDiscussResponse(_Request):
    reply: str
    stop_reason: str
    steps: int
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    llm_meta: dict[str, Any] = Field(default_factory=dict)
    thinking: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    compacted: str | None = None
    compact_error: str | None = None


def _read_messages(db: Session) -> list[SidebarMessageRead]:
    return [
        SidebarMessageRead(
            msg_id=row.msg_id,
            role=row.role,
            content=row.content,
            tags=row.tags,
            tool_trace=row.tool_trace,
            thinking=row.thinking,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            created_at=row.created_at,
        )
        for row in reads.list_sidebar_messages(db)
    ]


@router.get("/state", response_model=SidebarStateResponse)
def sidebar_state(
    db: Session = Depends(get_session),
    client: LLMClient = Depends(get_llm_client),
) -> SidebarStateResponse:
    """侧边栏现在的样子。**没有任何前置条件** —— 它是常驻的。

    与会话端点的一个关键差别：这里**不检查任何状态机**。
    一个空库、一条消息都没有时它也必须返回 200 ——
    用户装好系统第一次点开侧边栏，看到的不该是一个错误页。
    """
    ctx = assemble_sidebar_context(db)
    meter = build_meter(
        db, ContextScope.SIDEBAR, SIDEBAR_SCOPE_ID, limit=_limit(client)
    )
    from app.agent.sidebar import SIDEBAR_TOOL_SCOPES
    from app.agent.tools import tool_schemas

    return SidebarStateResponse(
        messages=_read_messages(db),
        context=meter_payload(meter, client),
        tools=[spec.name for spec in tool_schemas(SIDEBAR_TOOL_SCOPES)],
        sections=[section.model_dump(mode="json") for section in ctx.sections],
    )


@router.get("/context")
def sidebar_context(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """侧边栏**这次会看到什么**（渲染好的那段文本 + 节报告）。

    与会话侧 `GET /api/sessions/{id}/plan/steps/{sid}/context` 同一个姿态：
    用户能看到模型看的是什么，于是"它为什么这么说"变得可追问。
    """
    ctx = assemble_sidebar_context(db)
    return {
        "rendered": render_sidebar_context(ctx),
        "precedence_order": ctx.precedence_order,
        "sections": [section.model_dump(mode="json") for section in ctx.sections],
    }


@router.post("/discuss", response_model=SidebarDiscussResponse)
def sidebar_discuss(
    body: SidebarDiscussRequest,
    db: Session = Depends(get_session),
    client: LLMClient = Depends(get_llm_client),
    max_steps: int = Depends(get_agent_max_steps),
    external: ExternalToolbox | None = Depends(get_external_toolbox),
) -> SidebarDiscussResponse:
    """和常驻助手聊一句。

    顺序与会话侧**逐字相同**（先压缩、再写用户那句话、再跑循环、
    再回填用量），因为那是被一次真实运行纠正过的顺序 ——
    见 `api/discuss.py` 里那段注释。

    ★ 侧边栏**没有状态机**，所以这里没有 `assert_writable` 的位置：
    它永远可以聊。这正是"常驻"的含义。
    """
    memory = build_memory(
        db, ContextScope.SIDEBAR, SIDEBAR_SCOPE_ID, client=client, limit=_limit(client)
    )
    writes.add_sidebar_message(db, content=body.message, role=ChatRole.USER)

    loop = SidebarLoop(
        db,
        client,
        max_steps=max_steps,
        external=external,
    )
    result = loop.run(body.message, history=memory.history)

    writes.add_sidebar_message(
        db,
        content=result.reply,
        role=ChatRole.ASSISTANT,
        tags=[f"stop:{result.stop_reason.value}"],
        tool_trace=[entry.as_dict() for entry in result.traces] or None,
        thinking=result.thinking or None,
        prompt_tokens=result.llm_meta.get("prompt_eval_count"),
        completion_tokens=result.llm_meta.get("eval_count"),
    )
    record_usage(db, ContextScope.SIDEBAR, SIDEBAR_SCOPE_ID, meta=result.llm_meta)
    after = build_meter(
        db, ContextScope.SIDEBAR, SIDEBAR_SCOPE_ID, limit=_limit(client)
    )

    return SidebarDiscussResponse(
        reply=result.reply,
        stop_reason=result.stop_reason.value,
        steps=result.steps,
        tool_trace=[entry.as_dict() for entry in result.traces],
        llm_meta=result.llm_meta,
        thinking=result.thinking,
        context=meter_payload(after, client),
        compacted=memory.new_compact_id,
        compact_error=memory.compact_error,
    )


@router.delete("/messages")
def clear_sidebar(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """清空侧边栏的对话。

    真正的删除逻辑在 `writes.clear_sidebar` —— **端点没有写权限**
    （`test_api_never_mutates_the_session_directly` 用 AST 钉住了这件事）。
    这句话不是为了整齐：绕过去的那份实现不会经过 `writes` 里的拒绝规则，
    也不会被 I1 的归属表覆盖。

    边界（详见 `writes.clear_sidebar` 的 docstring）：删的只有侧边栏的
    消息与这个 scope 的压缩记录，**全局认知一条都不动**。
    """
    deleted_messages, deleted_compacts = writes.clear_sidebar(db)
    return {
        "deleted_messages": deleted_messages,
        "deleted_compacts": deleted_compacts,
        "note": "全局认知（偏好/基线/证据/提案日志）**一条都没动**。",
    }
