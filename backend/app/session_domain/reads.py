"""Session 域读路径（**无需令牌**，与 `global_domain.reads` 同口径）。

读路径不设防是刻意的：防的是写。把读也拦起来，只会让人为了"读一眼"
去申请权限，然后为了省事绕过去 —— 而绕过的那条路，通常顺便也能写。

两条约定：

1. **找不到就抛 `NotFound`**，不返回 `None`。返回 `None` 会把「不存在」与
   「存在但为空」混成一个值，调用方于是写出 `if x is None: ...` 这种
   抓不住真问题的分支。
2. **一次性取全量**。`session_state()` 把侧页要的全部数据一次读出来
   （M0 §10「单一状态源」）：前端不该为了渲染一个侧页发 8 个请求，
   而 8 个请求之间的不一致，正是「两处显示不同」的来源。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.errors import NotFound
from app.session_domain.enums import StageItemStatus
from app.session_domain.models import (
    ChatAttachment,
    ChatMessage,
    Folder,
    InputRevision,
    Milestone,
    MotivationRevision,
    Note,
    PlanCheckpoint,
    PlanRevision,
    PlanStep,
    Session as SessionModel,
    SessionInput,
    SessionMotivation,
    SessionSummary,
    StageItem,
    StateSnapshot,
    TaskPlan,
)


# --------------------------------------------------------------------------- #
# Folder
# --------------------------------------------------------------------------- #


def list_folders(db: DbSession) -> list[Folder]:
    return list(db.scalars(select(Folder).order_by(Folder.created_at, Folder.folder_id)))


def get_folder(db: DbSession, folder_id: str) -> Folder:
    row = db.get(Folder, folder_id)
    if row is None:
        raise NotFound(f"文件夹不存在：{folder_id}", detail={"folder_id": folder_id})
    return row


# --------------------------------------------------------------------------- #
# Session
# --------------------------------------------------------------------------- #


def list_sessions(
    db: DbSession, *, folder_id: str | None = None
) -> list[SessionModel]:
    stmt = select(SessionModel).order_by(
        SessionModel.created_at, SessionModel.session_id
    )
    if folder_id is not None:
        stmt = stmt.where(SessionModel.folder_id == folder_id)
    return list(db.scalars(stmt))


def get_session(db: DbSession, session_id: str) -> SessionModel:
    row = db.get(SessionModel, session_id)
    if row is None:
        raise NotFound(f"会话不存在：{session_id}", detail={"session_id": session_id})
    return row


def get_input(db: DbSession, session_id: str) -> SessionInput:
    row = db.get(SessionInput, session_id)
    if row is None:
        raise NotFound(
            f"会话 {session_id} 没有任务定义区", detail={"session_id": session_id}
        )
    return row


def list_input_revisions(db: DbSession, session_id: str) -> list[InputRevision]:
    return list(
        db.scalars(
            select(InputRevision)
            .where(InputRevision.session_id == session_id)
            .order_by(InputRevision.created_at, InputRevision.rev_id)
        )
    )


def get_motivation(db: DbSession, session_id: str) -> SessionMotivation:
    row = db.get(SessionMotivation, session_id)
    if row is None:
        raise NotFound(
            f"会话 {session_id} 没有动机记录", detail={"session_id": session_id}
        )
    return row


def list_motivation_revisions(db: DbSession, session_id: str) -> list[MotivationRevision]:
    return list(
        db.scalars(
            select(MotivationRevision)
            .where(MotivationRevision.session_id == session_id)
            .order_by(MotivationRevision.created_at, MotivationRevision.rev_id)
        )
    )


def get_snapshot(db: DbSession, session_id: str) -> StateSnapshot | None:
    """快照可以为空（还没填过状态）。**这是唯一允许返回 `None` 的读函数** ——
    「没填」是一个真实且需要区分的状态（界面要显示"记录一下当前状态"的入口）。
    """
    return db.get(StateSnapshot, session_id)


# --------------------------------------------------------------------------- #
# 计划
# --------------------------------------------------------------------------- #


def get_plan(db: DbSession, session_id: str) -> TaskPlan | None:
    """没有计划是正常状态（`draft` 期还没有计划），因此返回 `None`。"""
    return db.scalar(select(TaskPlan).where(TaskPlan.session_id == session_id))


def require_plan(db: DbSession, session_id: str) -> TaskPlan:
    plan = get_plan(db, session_id)
    if plan is None:
        raise NotFound(
            f"会话 {session_id} 还没有计划", detail={"session_id": session_id}
        )
    return plan


def plan_steps(
    db: DbSession, plan_id: str, *, version_no: int | None = None
) -> list[PlanStep]:
    """`version_no=None` → 当前版本。传具体版本号 → 那个版本的快照（只读回看）。"""
    if version_no is None:
        plan = db.get(TaskPlan, plan_id)
        if plan is None:
            raise NotFound(f"计划不存在：{plan_id}", detail={"plan_id": plan_id})
        version_no = plan.current_version
    return list(
        db.scalars(
            select(PlanStep)
            .where(PlanStep.plan_id == plan_id, PlanStep.version_no == version_no)
            .order_by(PlanStep.seq, PlanStep.step_id)
        )
    )


def plan_checkpoints(
    db: DbSession, plan_id: str, *, version_no: int | None = None
) -> list[PlanCheckpoint]:
    if version_no is None:
        plan = db.get(TaskPlan, plan_id)
        if plan is None:
            raise NotFound(f"计划不存在：{plan_id}", detail={"plan_id": plan_id})
        version_no = plan.current_version
    return list(
        db.scalars(
            select(PlanCheckpoint)
            .where(
                PlanCheckpoint.plan_id == plan_id,
                PlanCheckpoint.version_no == version_no,
            )
            .order_by(PlanCheckpoint.cp_id)
        )
    )


def list_plan_revisions(db: DbSession, plan_id: str) -> list[PlanRevision]:
    return list(
        db.scalars(
            select(PlanRevision)
            .where(PlanRevision.plan_id == plan_id)
            .order_by(PlanRevision.to_version, PlanRevision.rev_id)
        )
    )


# --------------------------------------------------------------------------- #
# stage / 聊天 / 笔记 / 里程碑 / 总结
# --------------------------------------------------------------------------- #


def list_stage_items(
    db: DbSession,
    session_id: str,
    *,
    statuses: frozenset[StageItemStatus] | None = None,
) -> list[StageItem]:
    stmt = (
        select(StageItem)
        .where(StageItem.session_id == session_id)
        .order_by(StageItem.created_at, StageItem.item_id)
    )
    if statuses is not None:
        stmt = stmt.where(StageItem.status.in_([s.value for s in statuses]))
    return list(db.scalars(stmt))


def get_stage_item(db: DbSession, item_id: str) -> StageItem:
    row = db.get(StageItem, item_id)
    if row is None:
        raise NotFound(f"stage 条目不存在：{item_id}", detail={"item_id": item_id})
    return row


def list_chat_messages(db: DbSession, session_id: str) -> list[ChatMessage]:
    return list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at, ChatMessage.msg_id)
        )
    )


def list_notes(db: DbSession, session_id: str) -> list[Note]:
    return list(
        db.scalars(
            select(Note)
            .where(Note.session_id == session_id)
            .order_by(Note.created_at, Note.note_id)
        )
    )


def list_milestones(db: DbSession, session_id: str) -> list[Milestone]:
    return list(
        db.scalars(
            select(Milestone)
            .where(Milestone.session_id == session_id)
            .order_by(Milestone.created_at, Milestone.milestone_id)
        )
    )


def list_attachments(db: DbSession, session_id: str) -> list[ChatAttachment]:
    """这个会话收到的资料，**按上传时间升序**（新传的排在后面）。

    升序而不是降序，是为了让上下文里的清单顺序与用户记忆的一致
    （"我最开始传的那份"总在第一条），而"最近传的"由末尾几条承担。
    """
    return list(
        db.scalars(
            select(ChatAttachment)
            .where(ChatAttachment.session_id == session_id)
            .order_by(ChatAttachment.created_at, ChatAttachment.attachment_id)
        )
    )


def get_attachment(db: DbSession, attachment_id: str) -> ChatAttachment:
    row = db.get(ChatAttachment, attachment_id)
    if row is None:
        raise NotFound(f"没有这份资料：{attachment_id}", detail={"attachment_id": attachment_id})
    return row


def require_attachment(
    db: DbSession, session_id: str, attachment_id: str
) -> ChatAttachment:
    """「这个会话里的这份资料」—— 不存在、或不属于这个会话，都是 404。

    **两种失败合并成同一个 404 是一个刻意的决定**：分开回 403
    等于告诉探测者"那个 id 存在，只是不属于你"，而附件 id 是顺序生成的
    （`AT-0001`、`AT-0002`…），那会把"这个库里有多少份资料"泄出去。

    它同时是**唯一**的判断点：早些时候 GET 与 DELETE 各自写了一遍这个判断，
    于是出现了"读取回 404、删除回 422"的分歧 —— 同一个请求条件下
    两个动词给两个不同的答案，而调用方只能靠猜。放在这里，它们不可能再分叉。
    """
    row = get_attachment(db, attachment_id)
    if row.session_id != session_id:
        raise NotFound(
            f"会话 {session_id} 里没有这份资料：{attachment_id}",
            detail={"attachment_id": attachment_id, "session_id": session_id},
        )
    return row


def find_attachment_by_digest(
    db: DbSession, session_id: str, sha256: str
) -> ChatAttachment | None:
    """同一个会话里、内容完全相同的那一份（用来识别"这份你传过了"）。"""
    return db.scalars(
        select(ChatAttachment).where(
            ChatAttachment.session_id == session_id, ChatAttachment.sha256 == sha256
        )
    ).first()


def get_milestone(db: DbSession, milestone_id: str) -> Milestone:
    row = db.get(Milestone, milestone_id)
    if row is None:
        raise NotFound(
            f"里程碑不存在：{milestone_id}", detail={"milestone_id": milestone_id}
        )
    return row


def latest_summary(db: DbSession, session_id: str) -> SessionSummary | None:
    return db.scalar(
        select(SessionSummary)
        .where(SessionSummary.session_id == session_id)
        .order_by(SessionSummary.created_at.desc(), SessionSummary.summary_id.desc())
        .limit(1)
    )
