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

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session as DbSession

from app.core.errors import NotFound
from app.session_domain.enums import (
    ClarificationStatus,
    ContextScope,
    SessionState,
    StageItemStatus,
)
from app.session_domain.models import (
    ChatAttachment,
    ChatMessage,
    ChatMessage as ChatMessageRow,
    Clarification,
    ContextCompact,
    Folder,
    InputRevision,
    Milestone,
    MotivationRevision,
    Note,
    PlanCheckpoint,
    PlanRevision,
    PlanStep,
    Session as SessionModel,
    SessionAnnotation,
    SessionInput,
    SessionMotivation,
    SessionReflection,
    SessionSummary,
    SidebarMessage,
    StageItem,
    StateSnapshot,
    TaskPlan,
)


# --------------------------------------------------------------------------- #
# Folder
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FolderStat:
    """一个文件夹在侧栏上需要的那几个数字（不含归档的会话）。"""

    session_count: int
    active_session_count: int
    #: 最近动静：`max(会话创建时刻, 该会话最后一条聊天)`。
    updated_at: datetime


def list_folders(db: DbSession, *, include_archived: bool = True) -> list[Folder]:
    """侧栏的文件夹列表。

    `include_archived=False` 时**不返回归档的**（默认 `True` 保持内部调用的
    旧语义；API 边界上显式传 `False`，这样"侧栏看不看得到"这个决定只在一处）。
    """
    stmt = select(Folder).order_by(
        Folder.sort_order, Folder.created_at, Folder.folder_id
    )
    if not include_archived:
        stmt = stmt.where(Folder.archived_at.is_(None))
    return list(db.scalars(stmt))


def folder_stats(db: DbSession) -> dict[str, FolderStat]:
    """每个文件夹的"最近动静"与计数 —— 侧栏 `Order by: Last updated` 用。

    为什么在内存里分组，而不是写一句 `LEFT JOIN + GROUP BY + MAX(COALESCE(...))`：
    这是**个人工作台**，文件夹是几十个量级。一句五六层的聚合 SQL 读完
    要人相信它对；两行 Python 一眼就看得懂对不对。规模变了再改这里，
    而不是现在就为"可能会有的百万文件夹"付代价。

    两条口径：
    - **归档的会话不算**：归档本来就是"别再占着侧栏了"，
      它不该继续让一个文件夹往上浮。
    - `updated_at` = `max(session.created_at, 该会话最后一条聊天)` ——
      一个文件夹只要还在聊，就该往上浮。
    """
    rows = db.execute(
        select(
            SessionModel.session_id,
            SessionModel.folder_id,
            SessionModel.state,
            SessionModel.created_at,
        ).where(SessionModel.archived_at.is_(None))
    ).all()
    last_chat = last_chat_at_by_session(db)
    stats: dict[str, FolderStat] = {}
    for session_id, folder_id, state_value, created_at in rows:
        when = max(created_at, last_chat.get(session_id, created_at))
        busy = 1 if state_value == SessionState.ACTIVE else 0
        current = stats.get(folder_id)
        if current is None:
            stats[folder_id] = FolderStat(
                session_count=1, active_session_count=busy, updated_at=when
            )
        else:
            stats[folder_id] = FolderStat(
                session_count=current.session_count + 1,
                active_session_count=current.active_session_count + busy,
                updated_at=max(current.updated_at, when),
            )
    return stats


def last_chat_at(db: DbSession, session_id: str) -> datetime | None:
    """单条会话的最后一条聊天时刻（`state.build_session_read` 用）。"""
    return db.scalar(
        select(func.max(ChatMessage.created_at)).where(
            ChatMessage.session_id == session_id
        )
    )


def last_chat_at_by_session(db: DbSession) -> dict[str, datetime]:
    """`session_id → 最后一条聊天的时刻`。没有聊天记录的会话不在表里。

    "最后更新"由**派生**得到，不落一个冗余列：冗余列要靠每一处写入记得去 touch，
    忘一处就会显示一个看起来很正常、其实是旧的时刻 —— 而那种错没人会报。
    """
    rows = db.execute(
        select(ChatMessage.session_id, func.max(ChatMessage.created_at)).group_by(
            ChatMessage.session_id
        )
    ).all()
    return {session_id: when for session_id, when in rows if when is not None}


def get_folder(db: DbSession, folder_id: str) -> Folder:
    row = db.get(Folder, folder_id)
    if row is None:
        raise NotFound(f"文件夹不存在：{folder_id}", detail={"folder_id": folder_id})
    return row


# --------------------------------------------------------------------------- #
# Session
# --------------------------------------------------------------------------- #


def list_sessions(
    db: DbSession, *, folder_id: str | None = None, include_archived: bool = True
) -> list[SessionModel]:
    """会话列表。排序 = `sort_order` → `created_at` → id（与文件夹同口径）。

    `include_archived` 的默认值与 `list_folders` 一致：内部调用拿到全量，
    API 边界上显式传参。
    """
    stmt = select(SessionModel).order_by(
        SessionModel.sort_order, SessionModel.created_at, SessionModel.session_id
    )
    if folder_id is not None:
        stmt = stmt.where(SessionModel.folder_id == folder_id)
    if not include_archived:
        stmt = stmt.where(SessionModel.archived_at.is_(None))
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


def get_chat_message(db: DbSession, msg_id: str) -> ChatMessage | None:
    """按 id 取一条消息。**返回 `None` 而不抛** —— 调用方要区分
    「这条消息不属于这个会话」与「没有这条消息」这两种情况下的措辞。

    没有 `require_` 版本是刻意的：这个取用点只有标注那一处，
    而它需要自己组织拒绝理由（见 `writes.annotate_message`）。
    """
    return db.get(ChatMessage, msg_id)


def list_annotations(db: DbSession, session_id: str) -> list[SessionAnnotation]:
    """一个会话里的标注，**按时间正序**（与人读对话的方向一致）。

    不按消息分组：分组是界面的事，而"先标了哪句"本身是有序信息 ——
    按消息分组会把这个顺序丢掉（同一句话可以标两次，问的是两件事）。
    """
    return list(
        db.scalars(
            select(SessionAnnotation)
            .where(SessionAnnotation.session_id == session_id)
            .order_by(SessionAnnotation.created_at, SessionAnnotation.annotation_id)
        )
    )


def list_reflections(db: DbSession, session_id: str) -> list[SessionReflection]:
    """一个会话里的感想，**按时间正序**（一次会话可以有几次回看）。"""
    return list(
        db.scalars(
            select(SessionReflection)
            .where(SessionReflection.session_id == session_id)
            .order_by(SessionReflection.created_at, SessionReflection.reflection_id)
        )
    )


def get_annotation(
    db: DbSession, session_id: str, annotation_id: str
) -> SessionAnnotation | None:
    row = db.get(SessionAnnotation, annotation_id)
    if row is None or row.session_id != session_id:
        return None
    return row


def list_compacts(
    db: DbSession, scope: ContextScope, scope_id: str
) -> list[ContextCompact]:
    """某个对话的全部压缩记录，**时间升序**（最早的在前）。

    升序是刻意的：装配上下文时要按"先压缩的在前"拼成一段连续的记忆，
    倒序会让调用方到处 `.reverse()`，而某处忘了 reverse 就是一段
    前后颠倒的摘要 —— 它读起来仍然通顺，所以没人会发现。
    """
    return list(
        db.scalars(
            select(ContextCompact)
            .where(ContextCompact.scope == scope, ContextCompact.scope_id == scope_id)
            .order_by(ContextCompact.created_at, ContextCompact.compact_id)
        )
    )


def compacted_message_ids(
    db: DbSession, scope: ContextScope, scope_id: str
) -> set[str]:
    """已经被压掉的那些消息 id。

    ★ 这是"还剩下哪些原文"的**唯一判据**。不用"最后一条压缩的时间戳之前"
    那种写法：同一毫秒内落的消息会让边界含糊，而含糊的边界意味着某条消息
    可能既在摘要里、又当原文发一遍（模型看到两遍同样的话）。
    按 id 集合判断没有这个问题。

    ★ **被折进新摘要的旧摘要，它当年吃掉的消息仍然算被吃掉。**
    所以这里遍历所有行（含被折叠的），而不是只遍历"还活着"的摘要 ——
    折叠只是让摘要变短，原话并没有因此回到上下文里。
    """
    out: set[str] = set()
    for row in list_compacts(db, scope, scope_id):
        out.update(row.covers_msg_ids)
    return out


def live_compacts(
    db: DbSession, scope: ContextScope, scope_id: str
) -> list[ContextCompact]:
    """**还没被折进更新的摘要**的那些压缩记录，时间升序。

    装配上下文用这个而不是 `list_compacts`：被折叠的摘要已经包含在
    折叠它的那一条里了，再带一遍就是同一段记忆说两次。
    """
    rows = list_compacts(db, scope, scope_id)
    folded = {cid for row in rows for cid in row.folds_compact_ids}
    return [row for row in rows if row.compact_id not in folded]


def list_sidebar_messages(db: DbSession) -> list[SidebarMessage]:
    """侧边栏的全部消息，时间升序。侧边栏只有一个，所以不需要 id 参数。"""
    return list(
        db.scalars(
            select(SidebarMessage).order_by(
                SidebarMessage.created_at, SidebarMessage.msg_id
            )
        )
    )


def list_messages(
    db: DbSession, scope: ContextScope, scope_id: str
) -> list[ChatMessageRow]:
    """某个对话的全部消息（含已被压缩的）。

    ★ **只有这一个地方知道"哪个 scope 用哪张表"。**
    `agent.context` 与端点都走这里 —— 让它们各自判断的话，
    将来新增一种 scope 时必然漏掉一处，而漏掉的表现是
    "某一侧的上下文用量恒为 0"（看起来只是没在记，不会报错）。
    """
    if scope is ContextScope.SIDEBAR:
        return list_sidebar_messages(db)
    return list_chat_messages(db, scope_id)


def list_live_messages(
    db: DbSession, scope: ContextScope, scope_id: str
) -> list[ChatMessageRow]:
    """**没被压掉**的原文，按时间升序。这就是装配上下文时要带的那一段。

    侧边栏与 session 走同一条路，为的是让"还剩哪些原文"只有一个实现。
    """
    dead = compacted_message_ids(db, scope, scope_id)
    return [row for row in list_messages(db, scope, scope_id) if row.msg_id not in dead]


def get_compact(db: DbSession, compact_id: str) -> ContextCompact:
    row = db.get(ContextCompact, compact_id)
    if row is None:
        raise NotFound(f"压缩记录不存在：{compact_id}")
    return row


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


# --------------------------------------------------------------------------- #
# 澄清问题（任务不明确时反问，P13）
# --------------------------------------------------------------------------- #


def list_clarifications(
    db: DbSession, session_id: str, *, only_open: bool = False
) -> list[Clarification]:
    """会话的问题，**未答的排在最前**，同组内新的在前。

    排序是为了"一进会话就看到那个挡着计划的提问"：
    按时间倒序会让一个刚问的问题被旧的已答问题压下去 ——
    而闸门拦住的正是那个刚问的。

    ★ 这里用 `case` 显式表达"未答优先"，**不靠状态字符串的字典序**。
    `open / answered / dismissed` 按字典序排恰好不是这个顺序
    （`dismissed` 夹在中间），而那种排序看起来"也像是排过了"。
    """
    stmt = select(Clarification).where(Clarification.session_id == session_id)
    if only_open:
        stmt = stmt.where(Clarification.status == ClarificationStatus.OPEN)
    open_first = case((Clarification.status == ClarificationStatus.OPEN, 0), else_=1)
    return list(
        db.scalars(
            stmt.order_by(
                open_first,
                Clarification.created_at.desc(),
                Clarification.clarification_id.desc(),
            )
        )
    )


def get_clarification(db: DbSession, clarification_id: str) -> Clarification:
    row = db.get(Clarification, clarification_id)
    if row is None:
        raise NotFound(
            f"澄清问题不存在：{clarification_id}",
            detail={"clarification_id": clarification_id},
        )
    return row


def latest_status_by_slot(
    db: DbSession, session_id: str
) -> dict[str, ClarificationStatus]:
    """槽位名 → **最近一次**问题的状态。闸门的输入。

    ★ 取最近一次而不是最严的一次：用户在第三轮答了 goal，
    第一轮那个被搁置的问题不应该继续挡着。反过来，
    他刚把刚答的那个问题撤了（重新问），也不能被旧的 `answered` 放行。

    ★ 这里**不依赖 `list_clarifications` 的展示顺序**，而是显式取
    `(created_at, id)` 最大的那一行。展示顺序是为了让人先看见该看的东西，
    闸门读的是事实 —— 把两者绑在一起，将来有人为了界面好看调一次排序，
    闸门的判定就跟着变了，而那种错不会有任何症状。
    """
    newest: dict[str, Clarification] = {}
    for row in list_clarifications(db, session_id):
        current = newest.get(row.slot)
        if current is None or (row.created_at, row.clarification_id) > (
            current.created_at,
            current.clarification_id,
        ):
            newest[row.slot] = row
    return {slot: row.status for slot, row in newest.items()}


def open_clarification_for_slot(
    db: DbSession, session_id: str, slot: str
) -> Clarification | None:
    return db.scalar(
        select(Clarification)
        .where(
            Clarification.session_id == session_id,
            Clarification.slot == slot,
            Clarification.status == ClarificationStatus.OPEN,
        )
        .order_by(Clarification.created_at.desc())
        .limit(1)
    )
