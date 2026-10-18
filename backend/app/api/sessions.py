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

from app.api.deps import get_session
from app.session_domain import reads, state, writes
from app.session_domain.context import DiscussionContext, assemble_discussion_context
from app.session_domain.schemas import (
    ChatMessageCreate,
    ChatMessageRead,
    CheckpointRead,
    CheckpointRecord,
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
    SessionCreate,
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


@router.get("", response_model=list[SessionRead])
def list_sessions(
    folder_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[SessionRead]:
    return [
        state.build_session_read(session, row.session_id)
        for row in reads.list_sessions(session, folder_id=folder_id)
    ]


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


@router.get("/{session_id}/input/revisions", response_model=list[InputRevisionRead])
def list_input_revisions(
    session_id: str, session: Session = Depends(get_session)
) -> list[InputRevisionRead]:
    """定义区改动史（append-only）。「我当初怎么说的 / 后来改成了什么」在这里。"""
    return [
        InputRevisionRead.model_validate(row)
        for row in reads.list_input_revisions(session, session_id)
    ]


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
        drivers=body.drivers,
        obstacles=body.obstacles,
        reason=body.reason,
        basis=body.basis,
    )
    return MotivationRead.model_validate(row)


@router.get(
    "/{session_id}/motivation/revisions", response_model=list[MotivationRevisionRead]
)
def list_motivation_revisions(
    session_id: str, session: Session = Depends(get_session)
) -> list[MotivationRevisionRead]:
    return [
        MotivationRevisionRead.model_validate(row)
        for row in reads.list_motivation_revisions(session, session_id)
    ]


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
    "/{session_id}/plan/revisions", response_model=list[PlanRevisionRead]
)
def list_plan_revisions(
    session_id: str, session: Session = Depends(get_session)
) -> list[PlanRevisionRead]:
    plan = reads.require_plan(session, session_id)
    return [
        PlanRevisionRead.model_validate(row)
        for row in reads.list_plan_revisions(session, plan.plan_id)
    ]


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
