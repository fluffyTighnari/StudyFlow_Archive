"""`/api/sessions` —— Session 域（L3 沙箱，M7 v0.4）。

## 这个文件的分工

它**只做三件事**：解析请求形状（Pydantic 已做完大半）、调 `session_domain` 的
读写函数、把 ORM 转成 DTO。**没有任何业务判断** ——
状态机在 `guards`、版本校验在 `writes`、上下文采用序在 `context`。
判断散落到端点里，是"同一条规矩在两个端点上不一样"的唯一成因。

## 写端点的版本号约定

三个版本域（见 `writes` 模块文档），每类写必须带对的那一个：

| 写什么 | 带哪个版本 |
|---|---|
| 状态转移 / stage / 快照 | `session.version` |
| 任务定义区 | `input.version` |
| 建计划 | `session.version`（此刻还没有 plan 行） |
| 改计划 / 标步骤 / 记检查点 | `plan.version` |

`GET /api/sessions/{id}/state` 一次把三个都返回（`versions`），
客户端不需要猜，也不需要在界面上维护"我现在该带哪个"。

## 只读的历史端点

`input/revisions`、`motivation/revisions`、`plan/revisions` 以及
`GET plan?version_no=n` 都是**只读**的：修订史与旧计划一旦写入就不再改变
（append-only），因此它们不需要版本号，也不可能有冲突。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.agent.context import build_meter, meter_payload
from app.agent.llm import LLMClient
from app.api.discuss import get_llm_client
from app.api.envelope import ItemsResponse, ok
from app.api.deps import get_session
from app.core.errors import InvalidField, NotFound
from app.session_domain import basis as basis_module
from app.session_domain import reflection as reflection_module
from app.session_domain import reads, state, writes
from app.session_domain.context import DiscussionContext, assemble_discussion_context
from app.session_domain.enums import ChangedBy, ClarificationStatus, ContextScope
from app.session_domain.schemas import (
    AnnotationCreate,
    AnnotationRead,
    ChatMessageCreate,
    ChatMessageRead,
    CheckpointRead,
    CheckpointRecord,
    ClarificationAnswer,
    ClarificationAnswerRead,
    ClarificationAsk,
    ClarificationDismiss,
    DeletedAnnotationRead,
    ClarificationRead,
    InputRead,
    InputRevisionRead,
    InputUpdate,
    MilestoneComplete,
    MilestoneCreate,
    MilestoneRead,
    MotivationRead,
    MotivationRevisionRead,
    MotivationUpdate,
    NoteCreate,
    NoteRead,
    PlanCreate,
    PlanRead,
    PlanRevisionRead,
    PlanRevise,
    ReflectionCreate,
    ReflectionGuideRead,
    ReflectionRead,
    SessionCreate,
    SessionPatch,
    SessionRead,
    SnapshotRead,
    SnapshotUpdate,
    StageItemCreate,
    StageItemRead,
    StageItemSettle,
    StepMark,
    StepRead,
    SummaryCreate,
    SummaryRead,
    TransitionRequest,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


# --------------------------------------------------------------------------- #
# 会话本身
# --------------------------------------------------------------------------- #


@router.get("", response_model=ItemsResponse[SessionRead])
def list_sessions(
    folder_id: str | None = Query(default=None),
    include_archived: bool = Query(
        default=False,
        description="是否把归档的会话也列出来。侧栏默认看不到它们（另有「已归档」区）。",
    ),
    session: Session = Depends(get_session),
) -> dict[str, list[SessionRead]]:
    rows = reads.list_sessions(
        session, folder_id=folder_id, include_archived=include_archived
    )
    # 一次把"最后一条聊天"读全，避免每条会话各查一次（N+1）。
    last_chat = reads.last_chat_at_by_session(session)
    return ok([
        state.build_session_read(
            session, row.session_id, last_chat_at=last_chat.get(row.session_id)
        )
        for row in rows
    ])


@router.patch("/{session_id}", response_model=SessionRead)
def patch_session(
    session_id: str, body: SessionPatch, session: Session = Depends(get_session)
) -> SessionRead:
    """侧栏整理：改名 / 归档 / 挪一位。

    ★ 这里**改不了 `state`**。状态只能走 `/transitions` ——
    那条路带理由、带审计、受 `WRITE_GUARDS` 约束。如果 PATCH 也能改状态，
    那张边表（`guards.TRANSITIONS`）就形同虚设。
    """
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise InvalidField(
            "这个请求什么都没改（title / archived / move 至少要给一个）。",
            detail={"field": None},
        )
    if "title" in updates:
        writes.rename_session(
            session, session_id, title=updates["title"] or ""
        )
    if "archived" in updates:
        writes.archive_session(
            session, session_id, archived=bool(updates["archived"])
        )
    if updates.get("move"):
        writes.move_session(session, session_id, direction=updates["move"])
    return state.build_session_read(session, session_id)


@router.delete("/{session_id}", response_model=dict[str, int])
def delete_session(
    session_id: str, session: Session = Depends(get_session)
) -> dict[str, int]:
    """删除一条会话，连同它的对话 / 计划 / 批注 / 资料。

    返回 `{"deleted_sessions": 1, "deleted_chat_messages": n}` ——
    界面用这个数字确认"删掉的是哪一条、里面有多少对话"。
    """
    return writes.delete_session(session, session_id)


@router.post("", response_model=SessionRead, status_code=201)
def create_session(
    body: SessionCreate, session: Session = Depends(get_session)
) -> SessionRead:
    """建会话（状态 `draft`）。定义区 / 动机 / 快照一并建齐（见 `writes.create_session`）。"""
    row = writes.create_session(
        session,
        folder_id=body.folder_id,
        title=body.title,
        input_goal=body.input_goal,
        intent_raw=body.intent_raw,
        intent_confidence=body.intent_confidence,
    )
    return state.build_session_read(session, row.session_id)


@router.get("/{session_id}/state", response_model=state.SessionStateRead)
def get_session_state(
    session_id: str, session: Session = Depends(get_session)
) -> state.SessionStateRead:
    """侧页的**唯一**数据入口：一次取全量（M0 §10 单一状态源）。"""
    return state.build_session_state(session, session_id)


@router.get("/{session_id}/context", response_model=dict[str, Any])
def get_context_meter(
    session_id: str,
    session: Session = Depends(get_session),
    client: LLMClient = Depends(get_llm_client),
) -> dict[str, Any]:
    """这个会话此刻的上下文用量。

    **为什么不塞进 `/state`**：窗口上限来自"此刻生效的模型"（`.env` 或
    数据库里那份档案），而 `/state` 是会话域的数据、不该因为换模型而改变形状。
    分开之后，"上下文"这一栏可以在切换模型后单独刷新，也能解释
    `limit=0`（模型没报窗口）与 `used=0`（还没跑过一轮）这两件不同的事。
    """
    reads.get_session(session, session_id)
    meter = build_meter(
        session, ContextScope.SESSION, session_id, limit=getattr(client, "num_ctx", 0) or 0
    )
    return meter_payload(meter, client)


@router.post("/{session_id}/transitions", response_model=SessionRead)
def transition(
    session_id: str,
    body: TransitionRequest,
    session: Session = Depends(get_session),
) -> SessionRead:
    """状态转移。边表之外的转移 → 409（`code=state_not_writable`）。

    拒绝时把「现在能去哪」一起返回（`detail.allowed_targets`），
    界面据此渲染按钮，不必自己维护第二份状态机。
    """
    writes.transition(
        session,
        session_id,
        target=body.target.value,
        expected_version=body.expected_version,
        reason=body.reason,
    )
    return state.build_session_read(session, session_id)


# --------------------------------------------------------------------------- #
# 澄清问题：任务不明确时反问（P13）
# --------------------------------------------------------------------------- #


@router.get(
    "/{session_id}/clarifications",
    response_model=ItemsResponse[ClarificationRead],
)
def list_clarifications(
    session_id: str,
    status: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, list[ClarificationRead]]:
    """这个会话问过的、关于任务本身的问题。**未答的排在最前**。

    ★ 为什么要有这个读端点：那个问题**挡着计划修改**。
    如果界面上看不见它，"计划改不了"就变成了一个没有原因的现象 ——
    而用户会以为是坏了，而不是"它在等我说一句话"。
    """
    rows = reads.list_clarifications(
        session, session_id, only_open=(status == ClarificationStatus.OPEN.value)
    )
    return {"items": [ClarificationRead.model_validate(row) for row in rows]}


@router.post(
    "/{session_id}/clarifications",
    response_model=ClarificationRead,
    status_code=201,
)
def ask_about_task(
    session_id: str,
    body: ClarificationAsk,
    session: Session = Depends(get_session),
) -> ClarificationRead:
    """记下一个关于任务的问题。

    两个刻意的拒绝（都在 `writes.ask_about_task` 里）：
    槽位已经有内容、或同一个槽位已经有一个没答的问题。
    """
    row = writes.ask_about_task(
        session,
        session_id,
        slot=body.slot,
        question=body.question,
        options=body.options,
        asked_by=ChangedBy.AGENT,
    )
    return ClarificationRead.model_validate(row)


@router.post(
    "/{session_id}/clarifications/{clarification_id}/answer",
    response_model=ClarificationAnswerRead,
)
def answer_clarification(
    session_id: str,
    clarification_id: str,
    body: ClarificationAnswer,
    session: Session = Depends(get_session),
) -> ClarificationAnswerRead:
    """回答 → **真的写进定义区**（带修订史），并让闸门放开。

    ★ 返回的是「问题 + 更新后的定义区」两件东西：
    只返回问题的话，前端还得再拉一次定义区才知道答案落到哪了，
    而"答案到底写进去了没有"正是这套机制唯一会出错的地方。
    """
    item, input_row = writes.answer_clarification(
        session,
        session_id,
        clarification_id,
        answer=body.answer,
        expected_version=body.expected_version,
    )
    return ClarificationAnswerRead(
        clarification=ClarificationRead.model_validate(item),
        input=InputRead.model_validate(input_row),
    )


@router.post(
    "/{session_id}/clarifications/{clarification_id}/dismiss",
    response_model=ClarificationRead,
)
def dismiss_clarification(
    session_id: str,
    clarification_id: str,
    body: ClarificationDismiss,
    session: Session = Depends(get_session),
) -> ClarificationRead:
    """「先不管」。计划可以继续改，但会带着「这是按助手判断排的」。"""
    row = writes.dismiss_clarification(
        session, session_id, clarification_id, reason=body.reason
    )
    return ClarificationRead.model_validate(row)


# --------------------------------------------------------------------------- #
# 任务定义区
# --------------------------------------------------------------------------- #


@router.patch("/{session_id}/input", response_model=InputRead)
def update_input(
    session_id: str,
    body: InputUpdate,
    session: Session = Depends(get_session),
) -> InputRead:
    """改定义区。**只写你传了的字段**（`exclude_unset`），逐字段留修订。"""
    payload: dict[str, Any] = body.model_dump(exclude_unset=True)
    expected_version = int(payload.pop("expected_version"))
    reason = payload.pop("reason", None)
    row = writes.update_input(
        session,
        session_id,
        changes=payload,
        expected_version=expected_version,
        reason=reason,
    )
    return InputRead.model_validate(row)


@router.get("/{session_id}/input/revisions", response_model=ItemsResponse[InputRevisionRead])
def list_input_revisions(
    session_id: str, session: Session = Depends(get_session)
) -> dict[str, list[InputRevisionRead]]:
    """定义区改动史（append-only）。「我当初怎么说的 / 后来改成了什么」在这里。"""
    return ok([
        InputRevisionRead.model_validate(row)
        for row in reads.list_input_revisions(session, session_id)
    ])


# --------------------------------------------------------------------------- #
# 动机
# --------------------------------------------------------------------------- #


@router.patch("/{session_id}/motivation", response_model=MotivationRead)
def update_motivation(
    session_id: str,
    body: MotivationUpdate,
    session: Session = Depends(get_session),
) -> MotivationRead:
    row = writes.update_motivation(
        session,
        session_id,
        change=body.change,
        expected_version=body.expected_version,
        type=body.type,
        strength=body.strength,
        foundation=body.foundation,
        value=body.value,
        drivers=body.drivers,
        obstacles=body.obstacles,
        reason=body.reason,
        basis=body.basis,
    )
    return MotivationRead.model_validate(row)


@router.get(
    "/{session_id}/motivation/revisions", response_model=ItemsResponse[MotivationRevisionRead]
)
def list_motivation_revisions(
    session_id: str, session: Session = Depends(get_session)
) -> dict[str, list[MotivationRevisionRead]]:
    return ok([
        MotivationRevisionRead.model_validate(row)
        for row in reads.list_motivation_revisions(session, session_id)
    ])


# --------------------------------------------------------------------------- #
# 状态快照
# --------------------------------------------------------------------------- #


@router.patch("/{session_id}/snapshot", response_model=SnapshotRead)
def update_snapshot(
    session_id: str,
    body: SnapshotUpdate,
    session: Session = Depends(get_session),
) -> SnapshotRead:
    """写动态状态（覆盖式、无修订、**永不进 M1**，M1 §5.5）。"""
    row = writes.update_snapshot(
        session,
        session_id,
        expected_version=body.expected_version,
        energy=body.energy,
        stress=body.stress,
        environment=body.environment,
        note=body.note,
    )
    return SnapshotRead.model_validate(row)


# --------------------------------------------------------------------------- #
# 计划
# --------------------------------------------------------------------------- #


def _plan_read(session: Session, session_id: str) -> PlanRead:
    plan = reads.require_plan(session, session_id)
    return state.build_plan_read(session, plan)


@router.post("/{session_id}/plan", response_model=PlanRead, status_code=201)
def create_plan(
    session_id: str,
    body: PlanCreate,
    session: Session = Depends(get_session),
) -> PlanRead:
    writes.create_plan(
        session,
        session_id,
        steps=[
            writes.StepDraft(
                action=step.action,
                output=step.output,
                method_ref=step.method_ref,
                step_id=step.step_id,
            )
            for step in body.steps
        ],
        checkpoints=[
            writes.CheckpointDraft(
                at_step=cp.at_step,
                check_method=cp.check_method,
                pass_criteria=cp.pass_criteria,
                fail_action=cp.fail_action,
                cp_id=cp.cp_id,
            )
            for cp in body.checkpoints
        ],
        expected_session_version=body.expected_session_version,
        goal_derived=body.goal_derived,
        reason=body.reason,
    )
    return _plan_read(session, session_id)


@router.get("/{session_id}/plan", response_model=PlanRead)
def get_plan(
    session_id: str,
    version_no: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
) -> PlanRead:
    """读计划。`version_no` 给了就读**那一版**（只读回看，M7 §5 的版本化）。"""
    plan = reads.require_plan(session, session_id)
    return state.build_plan_read(session, plan, version_no=version_no)


@router.put("/{session_id}/plan", response_model=PlanRead)
def revise_plan(
    session_id: str,
    body: PlanRevise,
    session: Session = Depends(get_session),
) -> PlanRead:
    """改计划结构 → 新版本。`reason` 必填（M7 §9 第 3 条「无理由不改计划」）。"""
    writes.revise_plan(
        session,
        session_id,
        steps=[
            writes.StepDraft(
                action=step.action,
                output=step.output,
                method_ref=step.method_ref,
                step_id=step.step_id,
            )
            for step in body.steps
        ],
        checkpoints=[
            writes.CheckpointDraft(
                at_step=cp.at_step,
                check_method=cp.check_method,
                pass_criteria=cp.pass_criteria,
                fail_action=cp.fail_action,
                cp_id=cp.cp_id,
            )
            for cp in body.checkpoints
        ],
        change=body.change,
        reason=body.reason,
        expected_version=body.expected_version,
        basis=body.basis,
    )
    return _plan_read(session, session_id)


@router.get(
    "/{session_id}/plan/revisions", response_model=ItemsResponse[PlanRevisionRead]
)
def list_plan_revisions(
    session_id: str, session: Session = Depends(get_session)
) -> dict[str, list[PlanRevisionRead]]:
    plan = reads.require_plan(session, session_id)
    return ok([
        PlanRevisionRead.model_validate(row)
        for row in reads.list_plan_revisions(session, plan.plan_id)
    ])


@router.patch("/{session_id}/plan/steps/{step_id}", response_model=StepRead)
def mark_step(
    session_id: str,
    step_id: str,
    body: StepMark,
    session: Session = Depends(get_session),
) -> StepRead:
    """标步骤进度。**不产生新版本**，但会 +1 `plan.version`。"""
    row = writes.mark_step(
        session,
        session_id,
        step_id=step_id,
        status=body.status,
        expected_version=body.expected_version,
    )
    return StepRead.model_validate(row)


@router.patch(
    "/{session_id}/plan/checkpoints/{cp_id}", response_model=CheckpointRead
)
def record_checkpoint(
    session_id: str,
    cp_id: str,
    body: CheckpointRecord,
    session: Session = Depends(get_session),
) -> CheckpointRead:
    """记录检查点结果。**失败不自动改计划** —— 它是"计划该被讨论"的信号。"""
    row = writes.record_checkpoint(
        session,
        session_id,
        cp_id=cp_id,
        result=body.result,
        expected_version=body.expected_version,
        result_note=body.result_note,
    )
    return CheckpointRead.model_validate(row)


@router.get(
    "/{session_id}/plan/steps/{step_id}/context",
    response_model=DiscussionContext,
)
def get_discussion_context(
    session_id: str,
    step_id: str,
    session: Session = Depends(get_session),
) -> DiscussionContext:
    """**「点计划项讨论」的上下文包**（P4 方案 A1，用户裁定）。

    采用序（哪些认知数据与这一步相关、以什么优先）由确定性代码决定，
    不由模型每次自己挑。返回里包含 `sections`：每一节进了几条、
    **排除了几条、为什么** —— 让"它漏了什么"可以被一眼看出，
    而不是只能相信它给的都对了。

    这个端点**不调 LLM**、不产生任何写入。计划怎么改仍需用户点头。
    """
    return assemble_discussion_context(session, session_id, step_id)


# --------------------------------------------------------------------------- #
# stage
# --------------------------------------------------------------------------- #


@router.post("/{session_id}/stage", response_model=StageItemRead, status_code=201)
def add_stage_item(
    session_id: str,
    body: StageItemCreate,
    session: Session = Depends(get_session),
) -> StageItemRead:
    row = writes.add_stage_item(
        session,
        session_id,
        item_type=body.type,
        content=body.content,
        expected_version=body.expected_version,
    )
    return StageItemRead.model_validate(row)


@router.patch("/{session_id}/stage/{item_id}", response_model=StageItemRead)
def settle_stage_item(
    session_id: str,
    item_id: str,
    body: StageItemSettle,
    session: Session = Depends(get_session),
) -> StageItemRead:
    """给条目定归宿。**单向**：`active` 是唯一可离开的状态。

    注意：正常流程里 `deposited` 由沉淀端点自动标记（带 `result_ref` 指回提案），
    手动调用它主要是为了明确 `discarded`（"我不要这个了"）。
    """
    row = writes.settle_stage_item(
        session,
        session_id,
        item_id=item_id,
        status=body.status,
        expected_version=body.expected_version,
        result_ref=body.result_ref,
    )
    return StageItemRead.model_validate(row)


# --------------------------------------------------------------------------- #
# 聊天 / 笔记 / 里程碑 / 总结
# --------------------------------------------------------------------------- #


@router.post("/{session_id}/chat", response_model=ChatMessageRead, status_code=201)
def add_chat_message(
    session_id: str,
    body: ChatMessageCreate,
    session: Session = Depends(get_session),
) -> ChatMessageRead:
    """记一条对话。**只有 `active` 能聊**（状态机强制）。

    这是"只落一条消息"的**裸端点**：它不调模型，也不产生回答。
    带模型的讨论在 `app/api/discuss.py`（`POST /{id}/discuss` 一次性、
    `POST /{id}/discuss/stream` 流式）—— 两者都**先落用户消息、再跑循环**，
    原因见那里：消息落库不该依赖模型是否响应成功，否则一次超时或一次
    空回复就会话丢一段用户输入（这是被一次真实运行纠正过来的）。
    """
    row = writes.add_chat_message(session, session_id, content=body.content, tags=body.tags)
    return ChatMessageRead.model_validate(row)


@router.post("/{session_id}/notes", response_model=NoteRead, status_code=201)
def add_note(
    session_id: str,
    body: NoteCreate,
    session: Session = Depends(get_session),
) -> NoteRead:
    """写笔记。**唯一在终态仍可写的操作**（用户裁定）。"""
    row = writes.add_note(session, session_id, content=body.content, tags=body.tags)
    return NoteRead.model_validate(row)


@router.post("/{session_id}/annotations", response_model=AnnotationRead, status_code=201)
def annotate_message(
    session_id: str,
    body: AnnotationCreate,
    session: Session = Depends(get_session),
) -> AnnotationRead:
    """标注助手说的某一句话，并**当场**拿回它的推断理由（P15）。

    ★ 为什么理由与标注同一次返回：用户点「为什么这么说」时想要的是
    **立刻**看到依据。分两步（先记标注、再查理由）会让界面出现
    "标注成功了，理由是空的"这种中间态，而那个中间态最容易被读成
    「它没有依据」。

    ★ `basis` 是**现算**的、不落库。落下来就会过期 ——
    会话状态一变，那份快照就从"当时的依据"变成"一份旧文档"，
    而它看起来仍然像是当时的证据（见 `session_domain/basis.py` 的模块文档）。
    """
    row = writes.annotate_message(
        session,
        session_id,
        msg_id=body.msg_id,
        quote=body.quote,
        question=body.question,
    )
    return _annotation_read(session, row)


@router.get("/{session_id}/annotations", response_model=ItemsResponse[AnnotationRead])
def list_annotations(
    session_id: str,
    session: Session = Depends(get_session),
) -> ItemsResponse[AnnotationRead]:
    """这个会话里的全部标注（含各自的依据）。"""
    rows = reads.list_annotations(session, session_id)
    return ItemsResponse[AnnotationRead](
        items=[_annotation_read(session, row) for row in rows]
    )


@router.get(
    "/{session_id}/annotations/{annotation_id}", response_model=AnnotationRead
)
def get_annotation(
    session_id: str,
    annotation_id: str,
    session: Session = Depends(get_session),
) -> AnnotationRead:
    """重新算一次这条标注的依据（**重放**，所以可能与第一次看到的不同）。"""
    row = reads.get_annotation(session, session_id, annotation_id)
    if row is None:
        raise NotFound(
            "没有这条标注。",
            detail={"session_id": session_id, "annotation_id": annotation_id},
        )
    return _annotation_read(session, row)


@router.delete(
    "/{session_id}/annotations/{annotation_id}",
    response_model=DeletedAnnotationRead,
)
def delete_annotation(
    session_id: str,
    annotation_id: str,
    session: Session = Depends(get_session),
) -> DeletedAnnotationRead:
    """删掉一条标注。**解释权在用户手里**：标错了、或不想追问了。

    返回被删掉的那一条（连同它当时的依据）—— 界面据此确认"删的是哪一句"。
    """
    row = reads.get_annotation(session, session_id, annotation_id)
    if row is None:
        raise NotFound(
            "没有这条标注。",
            detail={"session_id": session_id, "annotation_id": annotation_id},
        )
    payload = _annotation_read(session, row)
    writes.delete_annotation(session, session_id, annotation_id)
    return DeletedAnnotationRead(deleted=payload)


@router.get("/{session_id}/reflection", response_model=ReflectionGuideRead)
def reflection_guide(
    session_id: str,
    session: Session = Depends(get_session),
) -> ReflectionGuideRead:
    """「写感想」的指引：四要素 + **这次会话里与每项相关的事实**（P15）。

    ★ 指引由代码生成、不调模型：固定的四句话贴给任何会话都成立，
    因此也帮不上任何忙。这里每一条 `facts` 都来自真实数据
    （计划进度、你自己写的完成判据、过程记录、笔记）。

    `blocked_reason` 非空时**照样 200** —— "现在还不该写"是一条答案，
    不是一个错误；界面据此显示原因而不是一个禁用的按钮。
    """
    guide = reflection_module.build_guide(session, session_id)
    return ReflectionGuideRead(**guide.as_dict())


@router.post("/{session_id}/reflections", response_model=ReflectionRead, status_code=201)
def write_reflection(
    session_id: str,
    body: ReflectionCreate,
    session: Session = Depends(get_session),
) -> ReflectionRead:
    """写一条感想。两道门槛：状态（`review`/`completed`/`abandoned`）+ **已复盘过**。"""
    row = writes.write_reflection(
        session, session_id, answers=body.answers, content=body.content
    )
    return ReflectionRead.model_validate(row)


@router.get("/{session_id}/reflections", response_model=ItemsResponse[ReflectionRead])
def list_reflections(
    session_id: str,
    session: Session = Depends(get_session),
) -> ItemsResponse[ReflectionRead]:
    """这个会话里的全部感想（一次会话可以有几次回看）。"""
    rows = reads.list_reflections(session, session_id)
    return ItemsResponse[ReflectionRead](
        items=[ReflectionRead.model_validate(row) for row in rows]
    )


def _annotation_read(session: Session, row: Any) -> AnnotationRead:
    """标注 + 依据。依据要**读那条消息**（轨迹在消息上）。

    ★ 显式构造 DTO，不走 `model_validate(row)`：`basis` 不是这一行的字段
    （它是现算的），而"响应里的每一个字段都来自哪里"必须一眼看得出。
    """
    message = reads.get_chat_message(session, row.msg_id)
    basis: dict[str, Any] = {}
    if message is not None:
        basis = basis_module.explain(
            session, message, quote=row.quote, question=row.question or ""
        ).as_dict()
    return AnnotationRead(
        annotation_id=row.annotation_id,
        msg_id=row.msg_id,
        quote=row.quote,
        question=row.question,
        created_at=row.created_at,
        basis=basis,
    )


@router.post("/{session_id}/milestones", response_model=MilestoneRead, status_code=201)
def add_milestone(
    session_id: str,
    body: MilestoneCreate,
    session: Session = Depends(get_session),
) -> MilestoneRead:
    row = writes.add_milestone(session, session_id, title=body.title)
    return MilestoneRead.model_validate(row)


@router.post(
    "/{session_id}/milestones/{milestone_id}/complete",
    response_model=MilestoneRead,
)
def complete_milestone(
    session_id: str,
    milestone_id: str,
    body: MilestoneComplete,
    session: Session = Depends(get_session),
) -> MilestoneRead:
    """达成里程碑。**不自动沉淀** —— 要不要把它变成一条全局认知是 R4b 那一步的事。"""
    row = writes.complete_milestone(
        session, session_id, milestone_id=milestone_id, results=body.results
    )
    return MilestoneRead.model_validate(row)


@router.post("/{session_id}/summary", response_model=SummaryRead, status_code=201)
def write_summary(
    session_id: str,
    body: SummaryCreate,
    session: Session = Depends(get_session),
) -> SummaryRead:
    """写完成总结。**这是 R4a 的触发点**，但总结本身不写全局。

    真正的沉淀发生在 `POST /api/sessions/{id}/complete`
    （写总结 + 产出 R4a 提案 + 归档），见 `api.sedimentation`。
    """
    row = writes.write_summary(
        session, session_id, content=body.content, findings=body.findings
    )
    return SummaryRead.model_validate(row)
