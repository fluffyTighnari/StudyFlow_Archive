"""侧页的**全量状态**（M0 §10「单一状态源」）。

侧页（定义区 + 计划区 + stage + 动机 + 快照 + 里程碑 + 总结）与聊天是
**同一份 `session_state` 的两个视图**，没有独立状态。因此这个函数是侧页的唯一数据入口：
一次事务、一次读，读出来的东西互相一致。

## 为什么不是 8 个端点

前端为了渲染一个侧页发 8 个请求，会有两个后果：
第 5 个请求失败时界面处于半更新状态；而第 3 与第 7 个请求之间别人改了一笔，
界面就会显示一个**从未同时存在过的组合**。前端的"两处显示不一致"几乎都是这么来的。

## 三个版本号一起回去

`versions` 里同时给 `session` / `input` / `plan` 三个当前版本。
客户端下一次写要带哪一个，由它写的是哪一块决定（见 `writes` 的版本域表）——
把三个都给它，比让它猜「改 stage 该带哪个版本」可靠得多。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from sqlalchemy.orm import Session as DbSession

from app.session_domain import reads
from app.session_domain.guards import allowed_targets
from app.session_domain.models import TaskPlan
from app.session_domain.schemas import (
    ChatMessageRead,
    CheckpointRead,
    InputRead,
    MilestoneRead,
    MotivationRead,
    NoteRead,
    PlanRead,
    SessionRead,
    SnapshotRead,
    StageItemRead,
    StepRead,
    SummaryRead,
)


class VersionsRead(BaseModel):
    """本次读到的三个版本号。写哪个域就回传哪个。"""

    session: int
    #: 会话行 + stage 条目 + 状态快照。
    input: int
    plan: int | None = None


class SessionStateRead(BaseModel):
    """一次取全量。**这是侧页唯一的数据入口。**"""

    session: SessionRead
    input: InputRead
    motivation: MotivationRead
    #: 还没记录过动态状态时为 `None`（「没填」不同于「填了空」）。
    snapshot: SnapshotRead | None = None
    #: `draft` 期还没有计划 → `None`。界面据此显示「生成计划」入口而不是空列表。
    plan: PlanRead | None = None
    stage: list[StageItemRead]
    #: P4 是全量返回；长会话的检索/分页属于 P6 的 Agentic RAG（M7 §11）。
    chat: list[ChatMessageRead]
    notes: list[NoteRead]
    milestones: list[MilestoneRead]
    summary: SummaryRead | None = None
    versions: VersionsRead


def build_session_read(
    db: DbSession, session_id: str, *, last_chat_at: datetime | None = None
) -> SessionRead:
    """组装会话头。

    `last_chat_at` 可以由调用方预先批量读好传进来（列表端点就是这么用的），
    不传则这里单独查一次 —— 一个参数省掉 N+1，又不强迫调用方知道怎么算。
    """
    row = reads.get_session(db, session_id)
    if last_chat_at is None:
        last_chat_at = reads.last_chat_at(db, session_id)
    return SessionRead(
        session_id=row.session_id,
        folder_id=row.folder_id,
        title=row.title,
        state=row.state,
        version=row.version,
        created_at=row.created_at,
        activated_at=row.activated_at,
        completed_at=row.completed_at,
        abandoned_at=row.abandoned_at,
        sort_order=row.sort_order,
        archived_at=row.archived_at,
        updated_at=max(row.created_at, last_chat_at) if last_chat_at else row.created_at,
        allowed_transitions=allowed_targets(row.state),
    )


def build_plan_read(
    db: DbSession, plan: TaskPlan, *, version_no: int | None = None
) -> PlanRead:
    """组装一版计划。`version_no=None` → 当前版本；否则读历史版本（只读回看）。

    历史版本走同一个组装函数，是为了让"回看旧计划"与"看当前计划"共用一套渲染，
    前端不必为历史写第二套代码 —— 而第二套代码正是"历史显示得跟当时不一样"的来源。
    """
    if version_no is None:
        version_no = plan.current_version
    steps = reads.plan_steps(db, plan.plan_id, version_no=version_no)
    checkpoints = reads.plan_checkpoints(db, plan.plan_id, version_no=version_no)
    return PlanRead(
        plan_id=plan.plan_id,
        session_id=plan.session_id,
        goal_derived=plan.goal_derived,
        current_version=plan.current_version,
        version=plan.version,
        version_no=version_no,
        steps=[StepRead.model_validate(step) for step in steps],
        checkpoints=[CheckpointRead.model_validate(cp) for cp in checkpoints],
    )


def build_session_state(db: DbSession, session_id: str) -> SessionStateRead:
    """一次取全量。读路径不设防（防的是写）。"""
    session_row = reads.get_session(db, session_id)
    plan_row = reads.get_plan(db, session_id)
    snapshot = reads.get_snapshot(db, session_id)
    summary = reads.latest_summary(db, session_id)

    return SessionStateRead(
        session=build_session_read(db, session_id),
        input=InputRead.model_validate(reads.get_input(db, session_id)),
        motivation=MotivationRead.model_validate(
            reads.get_motivation(db, session_id)
        ),
        snapshot=None if snapshot is None else SnapshotRead.model_validate(snapshot),
        plan=None if plan_row is None else build_plan_read(db, plan_row),
        stage=[
            StageItemRead.model_validate(item)
            for item in reads.list_stage_items(db, session_id)
        ],
        chat=[
            ChatMessageRead.model_validate(msg)
            for msg in reads.list_chat_messages(db, session_id)
        ],
        notes=[NoteRead.model_validate(note) for note in reads.list_notes(db, session_id)],
        milestones=[
            MilestoneRead.model_validate(m)
            for m in reads.list_milestones(db, session_id)
        ],
        summary=None if summary is None else SummaryRead.model_validate(summary),
        versions=VersionsRead(
            session=session_row.version,
            input=reads.get_input(db, session_id).version,
            plan=None if plan_row is None else plan_row.version,
        ),
    )


__all__ = [
    "SessionStateRead",
    "VersionsRead",
    "build_plan_read",
    "build_session_read",
    "build_session_state",
]
