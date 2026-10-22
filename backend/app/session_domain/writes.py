"""Session 域写路径 —— **沙箱内自由读写**，但受三条硬约束。

## 三条约束

1. **状态机**（`guards.WRITE_GUARDS`）：每个写函数第一句都是
   `assert_writable(row.state, op)`。状态不对 → 409 + `code=state_not_writable`。
2. **乐观锁**（M0 §6.2，缺口 C5）：三个版本域，各自独立 ——

   | 版本 | 保护什么 | 谁 +1 |
   |---|---|---|
   | `session.version` | 会话行 + **stage 条目** + `state_snapshot` | 状态转移、stage 增改、快照 |
   | `session_input.version` | 任务定义区 | 定义区写入 |
   | `task_plan.version` | 计划结构 + 步骤进度 + 检查点结果 | 建计划之后的全部计划写入 |

   分成三个而不是一个，是为了让「我在改计划、你在改动机」不会互相 409 ——
   而两者都改计划时**必须**撞上。
3. **修订留痕**：定义区/动机/计划每次真正改变都写一条 append-only 修订。
   **值没变就不写修订** —— 伪造的历史和没有历史一样没用。

## 事务边界不在本模块

这里只 `flush()`，从不 `commit()`。提交由 `api.deps.get_session` 的
`session_scope` 决定（与 Router 同一条规矩：写函数不决定事务边界）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DbSession

from app.core.errors import (
    InvalidField,
    MissingPrerequisite,
    NotFound,
    VersionConflict,
)
from app.core.ids import (
    next_annotation_id,
    next_attachment_id,
    next_chat_msg_id,
    next_clarification_id,
    next_compact_id,
    next_folder_id,
    next_input_rev_id,
    next_milestone_id,
    next_motivation_rev_id,
    next_note_id,
    next_plan_cp_id,
    next_plan_id,
    next_plan_rev_id,
    next_plan_step_id,
    next_reflection_id,
    next_session_id,
    next_sidebar_msg_id,
    next_stage_item_id,
    next_summary_id,
    plan_cp_row_id,
    plan_step_row_id,
)
from app.db import DbSequenceStore
from app.global_domain.enums import Basis
from app.session_domain import reads
from app.session_domain.enums import (
    ChatRole,
    ChangedBy,
    CheckpointResult,
    ClarificationStatus,
    ContextScope,
    InputConfidence,
    Level,
    MilestoneStatus,
    MotivationType,
    PlanStepStatus,
    SIDEBAR_SCOPE_ID,
    SessionOperation,
    SessionState,
    StageItemStatus,
    StageItemType,
)
from app.session_domain.guards import (
    assert_transition,
    assert_writable,
)
from app.session_domain.models import (
    ChatAttachment,
    ChatMessage,
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


def _now() -> datetime:
    return datetime.now(UTC)


def _seq(db: DbSession) -> DbSequenceStore:
    return DbSequenceStore(db)


# --------------------------------------------------------------------------- #
# 校验小工具
# --------------------------------------------------------------------------- #


def _check_version(actual: int, expected: int, what: str) -> None:
    """不匹配就抛 409。`actual` 一并回传，客户端据此可以**自动重读再重试**，
    而不是把用户刚输入的内容丢掉。"""
    if actual == expected:
        return
    raise VersionConflict(
        f"{what}已被修改（当前版本 {actual}，你提交的是 {expected}）。"
        "请重新读取后再提交。",
        detail={"what": what, "current_version": actual, "expected_version": expected},
    )


def _require_text(value: str | None, field_name: str, *, why: str) -> str:
    if value is None or not value.strip():
        raise MissingPrerequisite(f"{field_name} 不能为空。{why}", detail={"field": field_name})
    return value


def assert_session_version(db: DbSession, session_id: str, expected: int) -> SessionModel:
    """公开的版本前置校验：**先验版本，再动任何数据**。

    有些流程（例如"写总结 + 沉淀 + 归档"）要连做几件事，此时客户端带来的版本
    必须在**第一件事之前**校验。若等到最后一步才撞上版本冲突，
    前面几件事已经写进事务里了 —— 虽然会回滚，但"先做后验"这个形状
    迟早会在某个不支持回滚的副作用上出事。
    """
    row = reads.get_session(db, session_id)
    _check_version(row.version, expected, f"会话 {session_id}")
    return row


def _check_known_fields(changes: dict[str, Any], allowed: frozenset[str]) -> None:
    unknown = set(changes) - allowed
    if unknown:
        raise InvalidField(
            f"这些字段不可写：{' / '.join(sorted(unknown))}",
            detail={"unknown": sorted(unknown), "allowed": sorted(allowed)},
        )


#: 任务定义区可写的字段（M7 §3）。白名单而不是黑名单：
#: 黑名单会让"新加一个列"自动变成"可以经 API 随便写"，包括 `version` 与 `session_id`。
INPUT_FIELDS: frozenset[str] = frozenset(
    {
        "goal",
        "why",
        "starting_point",
        "task_type",
        "constraints",
        "success_criteria",
        "priority",
        "intent_raw",
        "intent_confidence",
    }
)


# --------------------------------------------------------------------------- #
# Folder
# --------------------------------------------------------------------------- #


def create_folder(
    db: DbSession,
    *,
    name: str,
    goal: str | None = None,
    c_retrieval_dims: list[str] | None = None,
) -> Folder:
    """建一个长期领域（「英语学习」）。

    C 检索维度默认给 `["task_type"]`：同领域的历史证据才相关，
    而"按任务类型取"是最小可用的切法 —— 默认值不该是空列表，
    空列表意味着"所有证据都相关"，那是最坏的一种默认。
    """
    _require_text(name, "name", why="文件夹得有个名字")
    row = Folder(
        folder_id=next_folder_id(_seq(db)),
        name=name.strip(),
        goal=goal,
        c_retrieval_dims=c_retrieval_dims if c_retrieval_dims is not None else ["task_type"],
        sort_order=_next_sort_order(db, Folder),
        created_at=_now(),
    )
    db.add(row)
    db.flush()
    return row


# --------------------------------------------------------------------------- #
# 侧栏整理：改名 / 归档 / 排序 / 删除（文件夹与会话共用同一套语义）
#
# 这一组写入的共同点：**它们都不是"内容写"**。
#   - 不动 `version`（否则用户在侧栏改个名，正在进行的对话写就撞 409）；
#   - 不动 `state`（归档不是状态机的一个状态，见 `models.Folder.archived_at`）；
#   - 不写修订表（修订表记的是"这条定义/计划当时是什么"，不是"它叫什么名字"）。
# --------------------------------------------------------------------------- #


def _next_sort_order(db: DbSession, model: Any) -> int:
    """新条目排最后：取当前最大 + 1。

    不用 `count()`：删过条目之后 `count` 会与已有的 `sort_order` 撞上，
    而撞上的后果是"新条目插到了中间" —— 一个只有用户能发现、我们永远测不到的错。
    """
    current = db.scalar(select(func.max(model.sort_order)))
    return 0 if current is None else int(current) + 1


def _next_sort_order_in_folder(db: DbSession, folder_id: str) -> int:
    """会话的顺序**只在它自己的文件夹里**有意义 —— 跨文件夹比大小是无意义的。"""
    current = db.scalar(
        select(func.max(SessionModel.sort_order)).where(
            SessionModel.folder_id == folder_id
        )
    )
    return 0 if current is None else int(current) + 1


def _renumber(db: DbSession, model: Any, ids: list[str]) -> None:
    """把一组 id 的 `sort_order` 按给定顺序**重编成 0..n-1**。

    为什么不是"交换相邻两行的 sort_order"：历史数据里可能有一堆 `sort_order=0`
    （加这一列之前建的），交换两个 0 等于什么都没做 —— 而界面上表现为
    "点了上移，没反应"。重编号是幂等的，且不依赖旧值。
    """
    for index, row_id in enumerate(ids):
        row = db.get(model, row_id)
        if row is not None:
            row.sort_order = index
    db.flush()


def _move_in_place(
    db: DbSession, model: Any, ids: list[str], target_id: str, *, direction: str
) -> bool:
    """在给定的同级顺序里把 `target_id` 挪一位。到头了就原样返回 `False`。"""
    if direction not in {"up", "down"}:
        raise InvalidField(
            "排序方向只能是 up 或 down。",
            detail={"field": "direction", "got": direction},
        )
    index = ids.index(target_id)
    other = index - 1 if direction == "up" else index + 1
    if other < 0 or other >= len(ids):
        return False
    order = list(ids)
    order[index], order[other] = order[other], order[index]
    _renumber(db, model, order)
    return True


def rename_folder(db: DbSession, folder_id: str, *, name: str) -> Folder:
    row = reads.get_folder(db, folder_id)
    _require_text(name, "name", why="文件夹得有个名字")
    row.name = name.strip()
    db.flush()
    return row


def set_folder_goal(
    db: DbSession, folder_id: str, *, goal: str | None
) -> Folder:
    """改目标。`None` 是"清空"，不是"不改" —— 改不改由调用方先判断。

    folder 的 `goal` 是**给人看的一句话**（"把英语捡回来"），不是约束，
    所以改它不写修订、不涨版本：它不是证据，也不会被任何判定读。
    """
    row = reads.get_folder(db, folder_id)
    row.goal = goal
    db.flush()
    return row


def archive_folder(db: DbSession, folder_id: str, *, archived: bool = True) -> Folder:
    """归档 / 取消归档文件夹。

    **归档不等于结束**：文件夹里会话的 `state` 一个都不改。
    归档只是"别在侧栏占位置了"，恢复之后一切照旧。
    """
    row = reads.get_folder(db, folder_id)
    row.archived_at = _now() if archived else None
    db.flush()
    return row


def move_folder(db: DbSession, folder_id: str, *, direction: str) -> Folder:
    """在同一层（未归档的文件夹里）上移/下移一位。

    已经在头/尾时**什么都不做**，也不报错：界面那头按钮本来就该置灰，
    而"到边界了"不是错误 —— 报 409 只会让前端多写一个用不到的分支。
    """
    row = reads.get_folder(db, folder_id)
    siblings = [item.folder_id for item in reads.list_folders(db, include_archived=False)]
    if folder_id not in siblings:
        return row
    _move_in_place(db, Folder, siblings, folder_id, direction=direction)
    db.flush()
    return row


def delete_folder(db: DbSession, folder_id: str) -> dict[str, int]:
    """硬删除一个文件夹**连同它里面的会话**。

    为什么要连带删会话：`session.folder_id` 是 `ondelete="RESTRICT"`，
    不先删会话就会撞 FK。而"留下会话、只删文件夹"在数据上不存在
    —— 会话的归属是必填的。

    代价要说清：这一下会连同这些会话的对话、计划、批注、资料一起消失。
    所以界面上的确认框必须**先说出数量**（"3 个会话、12 条对话"），
    而不是问一句"确定删除吗"。
    """
    row = reads.get_folder(db, folder_id)
    sessions = reads.list_sessions(db, folder_id=folder_id)
    for item in sessions:
        db.delete(item)  # 子表都是 ON DELETE CASCADE，交给数据库
    db.flush()
    db.delete(row)
    db.flush()
    return {"deleted_sessions": len(sessions), "deleted_folders": 1}


# --------------------------------------------------------------------------- #
# Session：创建与状态机
# --------------------------------------------------------------------------- #


def create_session(
    db: DbSession,
    *,
    folder_id: str,
    title: str,
    input_goal: str | None = None,
    intent_raw: str | None = None,
    intent_confidence: InputConfidence = InputConfidence.MISSING,
) -> SessionModel:
    """建会话，并**一次建齐它的四个 1:1 头**：会话行 / 定义区 / 动机 / 状态快照。

    为什么要一次建齐：侧页在任何状态下都要显示这四块（M0 §10）。
    如果它们按需创建，前端就得处理"某一小块还不存在"的空状态 ——
    而空状态的数量会随表增长，最终没人能穷举。
    """
    reads.get_folder(db, folder_id)  # 不存在就 404，而不是等 FK 报 IntegrityError
    _require_text(title, "title", why="会话得有个标题")

    row = SessionModel(
        session_id=next_session_id(_seq(db)),
        folder_id=folder_id,
        title=title.strip(),
        version=1,
        sort_order=_next_sort_order_in_folder(db, folder_id),
        created_at=_now(),
    )
    db.add(row)
    db.flush()

    db.add(
        SessionInput(
            session_id=row.session_id,
            goal=input_goal,
            intent_raw=intent_raw,
            intent_confidence=intent_confidence,
            version=1,
            updated_at=_now(),
        )
    )
    db.add(
        SessionMotivation(
            session_id=row.session_id,
            type=MotivationType.MIXED,
            strength=Level.MEDIUM,
            updated_at=_now(),
        )
    )
    db.add(StateSnapshot(session_id=row.session_id, updated_at=_now()))
    db.flush()
    return row


def rename_session(db: DbSession, session_id: str, *, title: str) -> SessionModel:
    row = reads.get_session(db, session_id)
    _require_text(title, "title", why="会话得有个标题")
    row.title = title.strip()
    # 有意不动 `version`：见本段开头那条注释。
    db.flush()
    return row


def archive_session(
    db: DbSession, session_id: str, *, archived: bool = True
) -> SessionModel:
    """归档 / 取消归档会话。**`state` 一点不变**。"""
    row = reads.get_session(db, session_id)
    row.archived_at = _now() if archived else None
    db.flush()
    return row


def move_session(db: DbSession, session_id: str, *, direction: str) -> SessionModel:
    """在**同一文件夹内**上移/下移一位；到头了就什么都不做。"""
    row = reads.get_session(db, session_id)
    siblings = [
        item.session_id
        for item in reads.list_sessions(
            db, folder_id=row.folder_id, include_archived=False
        )
    ]
    if session_id not in siblings:
        return row
    _move_in_place(db, SessionModel, siblings, session_id, direction=direction)
    db.flush()
    return row


def delete_session(db: DbSession, session_id: str) -> dict[str, int]:
    """硬删除一条会话，**连同它的对话、计划、批注、资料**。

    子表全部声明了 `ON DELETE CASCADE`，且连接上开了 `PRAGMA foreign_keys=ON`
    （`db.py`），所以这里只删会话这一行，剩下的交给数据库 ——
    手写十几次 `delete(...)` 才是那个会漏掉一张表的写法。

    返回删掉了什么，给界面确认"删的是哪一条"用。
    """
    row = reads.get_session(db, session_id)
    chat = reads.list_chat_messages(db, session_id)
    db.delete(row)
    db.flush()
    return {"deleted_sessions": 1, "deleted_chat_messages": len(chat)}


def transition(
    db: DbSession,
    session_id: str,
    *,
    target: str,
    expected_version: int,
    reason: str | None = None,
) -> SessionModel:
    """状态转移。**边表之外的转移一概拒绝**（`guards.TRANSITIONS`）。

    一个附带后果（另有一条**曾经**在这里、已经撤掉）：

    1. ~~`draft → active` 要求定义区至少写了目标~~ —— 撤了。判据是不拦会不会
       写出自相矛盾的数据：目标空着执行起来，定义区那一栏就是空的（可看见），
       计划 `goal_derived` 为空（可核对），没有任何一行在说谎。它挡住的却是
       "新建会话必须先想出一句目标才能开始"，而这句话用户本来就会在聊的过程中说出来。
    2. 进入 `completed` / `abandoned` 时，仍处于 `active` 的 stage 条目
       一律转 `archived`（用户裁定：**保留行，不删除**）。
       这就是"结束即弃"在数据上的真实含义 —— 弃的是它的待定身份，不是这条记录。
    """
    row = reads.get_session(db, session_id)
    edge = assert_transition(row.state, _state(target))
    assert_writable(row.state, SessionOperation.TRANSITION)
    _check_version(row.version, expected_version, f"会话 {session_id}")
    row.state = edge.target
    now = _now()
    if edge.target.value == "active" and row.activated_at is None:
        row.activated_at = now
    if edge.target.value == "completed":
        row.completed_at = now
    if edge.target.value == "abandoned":
        row.abandoned_at = now

    # 离开执行/整理期 → 未决的 stage 条目全部归档（不删除）。
    if edge.target.value in {"completed", "abandoned"}:
        for item in reads.list_stage_items(
            db, session_id, statuses=frozenset({StageItemStatus.ACTIVE})
        ):
            item.status = StageItemStatus.ARCHIVED
            item.settled_at = now

    row.version += 1
    db.flush()
    return row


def _state(value: str) -> SessionState:
    try:
        return SessionState(value)
    except ValueError as exc:
        raise InvalidField(f"未知的会话状态：{value}", detail={"state": value}) from exc


# --------------------------------------------------------------------------- #
# 任务定义区
# --------------------------------------------------------------------------- #


def update_input(
    db: DbSession,
    session_id: str,
    *,
    changes: dict[str, Any],
    expected_version: int,
    changed_by: ChangedBy = ChangedBy.USER,
    reason: str | None = None,
) -> SessionInput:
    """改定义区，**逐字段**写修订；值没变的字段不写。

    「值没变不写」不是优化，是正确性：点一次保存就产生 7 条"goal 从 X 改成 X"，
    修订表立刻被噪声淹没，而"我什么时候改过目标"这个问题就再也答不出来了。
    """
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.UPDATE_INPUT)
    _check_known_fields(changes, INPUT_FIELDS)

    input_row = reads.get_input(db, session_id)
    _check_version(input_row.version, expected_version, f"会话 {session_id} 的任务定义区")

    changed = False
    for field_name, new_value in changes.items():
        old_value = getattr(input_row, field_name)
        if _same(old_value, new_value):
            continue
        setattr(input_row, field_name, new_value)
        db.add(
            InputRevision(
                rev_id=next_input_rev_id(_seq(db)),
                session_id=session_id,
                field=field_name,
                old_value=None if old_value is None else str(old_value),
                new_value=None if new_value is None else str(new_value),
                changed_by=changed_by,
                reason=reason,
                created_at=_now(),
            )
        )
        changed = True

    if changed:
        input_row.version += 1
        input_row.updated_at = _now()
    db.flush()
    return input_row


# --------------------------------------------------------------------------- #
# 澄清问题：任务不明确时反问（P13）
# --------------------------------------------------------------------------- #


def ask_about_task(
    db: DbSession,
    session_id: str,
    *,
    slot: str,
    question: str,
    options: list[str] | None = None,
    asked_by: ChangedBy = ChangedBy.AGENT,
) -> Clarification:
    """记下一个**关于任务本身**的问题。

    ## 三种拒绝，都是"再问一次没有意义"的形态

    1. **槽位已经有内容** → 拒绝。问一个已经知道答案的问题，
       会让人以为它还是空的（或者以为系统没读他写的东西）。
    2. **同一个槽位已经有未答的问题** → 拒绝并**返回那个问题的 id**。
       重复提问的后果不是多一条记录，而是闸门以为"问过了"、
       界面显示两个一样的问题、而他答了其中一个之后另一个还挂着。
    3. **槽位名不是定义区的字段** → 拒绝。答案要真的写进定义区，
       所以问题只能对着真实的列问。

    ★ 它**不**检查模型问得好不好。问得含糊是模型的问题，
    而"问了两遍"和"问了一个不存在的字段"是系统的问题 —— 后者能判，前者不能。
    """
    from app.session_domain import clarify  # 局部导入：避免与 clarify 互相 import

    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.UPDATE_INPUT)

    if slot not in clarify.ASKABLE_FIELDS:
        raise InvalidField(
            f"`{slot}` 不是任务定义区的字段，问它没有地方落答案。"
            f"可以问的是：{'、'.join(sorted(clarify.ASKABLE_FIELDS))}",
            detail={"slot": slot},
        )
    if not str(question).strip():
        raise InvalidField("问题不能是空的", detail={"slot": slot})

    input_row = reads.get_input(db, session_id)
    if not clarify.is_empty(getattr(input_row, slot, None)):
        raise InvalidField(
            f"`{slot}` 已经有内容了（{getattr(input_row, slot)!r}）—— "
            f"问一个已经知道答案的问题，会让人以为它还是空的",
            detail={"slot": slot},
        )
    existing = reads.open_clarification_for_slot(db, session_id, slot)
    if existing is not None:
        raise InvalidField(
            f"`{slot}` 上已经有一个还没答的问题（{existing.clarification_id}）："
            f"{existing.question}",
            detail={"slot": slot, "clarification_id": existing.clarification_id},
        )

    item = Clarification(
        clarification_id=next_clarification_id(_seq(db)),
        session_id=session_id,
        slot=slot,
        question=str(question).strip(),
        options=[str(o) for o in options] if options else None,
        status=ClarificationStatus.OPEN,
        asked_by=asked_by,
        created_at=_now(),
    )
    db.add(item)
    db.flush()
    return item


def answer_clarification(
    db: DbSession,
    session_id: str,
    clarification_id: str,
    *,
    answer: str,
    expected_version: int,
) -> tuple[Clarification, SessionInput]:
    """回答 → **真的写进定义区**（走 `update_input`），并标记已答。

    ★ 这一步是整套机制的关键：只把答案存在问题行上的话，
    定义区仍然是空的，于是下一个回合的闸门**再拦一次** ——
    用户会觉得"我明明答了"。答案必须落到它该在的那一列。
    """
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.UPDATE_INPUT)
    item = reads.get_clarification(db, clarification_id)
    if item.session_id != session_id:
        raise InvalidField(
            f"问题 {clarification_id} 不属于会话 {session_id}",
            detail={"clarification_id": clarification_id, "session_id": session_id},
        )
    if item.status is not ClarificationStatus.OPEN:
        raise InvalidField(
            f"问题 {clarification_id} 已经结案（{item.status}），不能再答一次",
            detail={"clarification_id": clarification_id, "status": item.status.value},
        )
    if not str(answer).strip():
        raise InvalidField(
            "答案不能是空的 —— 空答案会让闸门以为它解决了，而定义区还是空的",
            detail={"clarification_id": clarification_id},
        )

    text = str(answer).strip()
    input_row = update_input(
        db,
        session_id,
        changes={item.slot: text},
        expected_version=expected_version,
        changed_by=ChangedBy.USER,
        reason=f"回答澄清问题 {clarification_id}（{item.slot}）",
    )
    item.status = ClarificationStatus.ANSWERED
    item.answer = text
    item.resolved_at = _now()
    db.flush()
    return item, input_row


def dismiss_clarification(
    db: DbSession,
    session_id: str,
    clarification_id: str,
    *,
    reason: str,
) -> Clarification:
    """「先不管」——**允许计划继续走，但它是猜的**。

    ★ 理由必填（库层也有 CHECK）。它是"这条计划为什么是按助手判断排的"
    的唯一解释；没有它，三个月后回看只知道当初搁置了，不知道为什么。
    """
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.UPDATE_INPUT)
    item = reads.get_clarification(db, clarification_id)
    if item.session_id != session_id:
        raise InvalidField(
            f"问题 {clarification_id} 不属于会话 {session_id}",
            detail={"clarification_id": clarification_id, "session_id": session_id},
        )
    if item.status is not ClarificationStatus.OPEN:
        raise InvalidField(
            f"问题 {clarification_id} 已经结案（{item.status}）",
            detail={"clarification_id": clarification_id, "status": item.status.value},
        )
    if not str(reason).strip():
        raise InvalidField(
            "搁置也要说一句为什么 —— 它是「这条计划为什么是猜的」的唯一解释",
            detail={"clarification_id": clarification_id},
        )
    item.status = ClarificationStatus.DISMISSED
    item.dismiss_reason = str(reason).strip()
    item.resolved_at = _now()
    db.flush()
    return item


# --------------------------------------------------------------------------- #
# 澄清问题：任务不明确时反问（P13）
# --------------------------------------------------------------------------- #


def _assert_task_is_clear_enough(
    db: DbSession, session_id: str, *, changed_by: ChangedBy
) -> str:
    """改计划前的闸门。返回**要追加到计划理由上的一句猜测说明**（可空）。

    ★ 为什么这里用 `_same` 而不是 `changed_by is not ChangedBy.AGENT`：
    这个函数的调用点有**两种形态** —— 领域内部的测试传枚举，
    而工具层（`app/agent/tools.py`）传的是裸字符串 `"agent"`。
    用 `is not` 比较时，裸字符串**不是同一个对象**，于是这一句直接返回空，
    **闸门整条失效**，而所有传枚举的单测照样全绿。
    这不是假设：第一版就是这么写的，端到端把它抓了出来
    （`e2echeck` 那一步里「助手改计划没有被拦」的那一格）。
    """
    if not _same(changed_by, ChangedBy.AGENT):
        return ""
    from app.session_domain import clarify  # 局部导入：避免与 clarify 互相 import

    input_row = reads.get_input(db, session_id)
    values = {slot.name: getattr(input_row, slot.name, None) for slot in clarify.SLOTS}
    gate = clarify.plan_gate(values, reads.latest_status_by_slot(db, session_id))
    # ★ 不抛了。缺 goal 就拒会让计划**永久**改不了：模型问过之后闸门就锁死，
    # 而界面上没有回答澄清的入口。改成把"这是按判断排的、缺了哪一项"写进
    # 计划自己的理由里 —— 那句话是可核对的，比一次拒绝更有用。
    return clarify.guess_note(gate.guessed, gate.blocked)


def _same(a: Any, b: Any) -> bool:
    """枚举与字符串视为同值：`Basis.STATED == "stated"` 为真，但 `Priority` 之类
    从 JSON 进来的是裸字符串。把这件事收在一处，别散在每个字段旁边。
    """
    if a == b:
        return True
    if isinstance(a, str) or isinstance(b, str):
        return str(a) == str(b)
    return False


# --------------------------------------------------------------------------- #
# 动机
# --------------------------------------------------------------------------- #


def update_motivation(
    db: DbSession,
    session_id: str,
    *,
    change: str,
    expected_version: int,
    type: MotivationType | None = None,
    strength: Level | None = None,
    drivers: list[str] | None = None,
    obstacles: list[dict] | None = None,
    reason: str | None = None,
    basis: Basis = Basis.STATED,
    changed_by: ChangedBy = ChangedBy.USER,
) -> SessionMotivation:
    """改动机。**动机是一次整体判断**，因此一次调用写一条修订，而不是逐字段。

    与定义区的差别是有道理的：定义区是并列的事实字段（改 goal 不影响 why），
    动机的三项互相解释（强度变了，通常是因为驱动因素变了）。
    逐字段记录会把一次"重新看了一遍为什么学"拆成三条互不相干的碎片。
    """
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.UPDATE_MOTIVATION)
    _require_text(change, "change", why="动机改动要写清楚改了什么")

    motivation = reads.get_motivation(db, session_id)
    _check_version(row.version, expected_version, f"会话 {session_id} 的动机")

    if type is not None:
        motivation.type = type
    if strength is not None:
        motivation.strength = strength
    if drivers is not None:
        motivation.drivers = drivers
    if obstacles is not None:
        motivation.obstacles = obstacles
    motivation.updated_at = _now()

    db.add(
        MotivationRevision(
            rev_id=next_motivation_rev_id(_seq(db)),
            session_id=session_id,
            change=change,
            reason=reason,
            basis=basis,
            changed_by=changed_by,
            created_at=_now(),
        )
    )
    # 动机没有自己的 `version` 列（M0 §6.2 只给了 session/input/plan/stage），
    # 因此**借用 `session.version`**，并且同样 +1 —— 借了却不加，
    # 就等于两个写者拿着同一个版本号各写各的，锁形同虚设。
    row.version += 1
    db.flush()
    return motivation


# --------------------------------------------------------------------------- #
# 状态快照
# --------------------------------------------------------------------------- #


def update_snapshot(
    db: DbSession,
    session_id: str,
    *,
    expected_version: int,
    energy: Level | None = None,
    stress: Level | None = None,
    environment: str | None = None,
    note: str | None = None,
) -> StateSnapshot:
    """写动态状态。**覆盖式、无修订**（M7 §4.1），且**永不进 M1**（M1 §5.5）。

    版本借用 `session.version`（与 stage 同域）：它属于"会话工作区"，
    而 M0 §6.2 没有给它单独的 `version` 列。
    """
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.UPDATE_SNAPSHOT)
    _check_version(row.version, expected_version, f"会话 {session_id}")

    snapshot = reads.get_snapshot(db, session_id)
    if snapshot is None:  # 旧数据兜底：create_session 之后不该发生
        snapshot = StateSnapshot(session_id=session_id)
        db.add(snapshot)
    if energy is not None:
        snapshot.energy = energy
    if stress is not None:
        snapshot.stress = stress
    if environment is not None:
        snapshot.environment = environment
    if note is not None:
        snapshot.note = note
    snapshot.updated_at = _now()
    row.version += 1
    db.flush()
    return snapshot


# --------------------------------------------------------------------------- #
# 计划
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StepDraft:
    """一个待写入的计划步骤。`step_id` 为空表示新建（由计数器分配）。"""

    action: str
    output: str | None = None
    method_ref: str | None = None
    step_id: str | None = None


@dataclass(frozen=True)
class CheckpointDraft:
    at_step: str
    check_method: str
    pass_criteria: str
    fail_action: str | None = None
    cp_id: str | None = None
    result: CheckpointResult = CheckpointResult.PENDING
    result_note: str | None = None


def create_plan(
    db: DbSession,
    session_id: str,
    *,
    steps: list[StepDraft],
    expected_session_version: int,
    goal_derived: str | None = None,
    checkpoints: list[CheckpointDraft] = (),
    reason: str = "创建计划",
    changed_by: ChangedBy = ChangedBy.USER,
) -> TaskPlan:
    """建计划（版本 1）。

    版本用 `session.version` 而不是 `plan.version` —— 因为此刻**还没有 plan 行**，
    没有自己的版本可带。这一点在 `revise_plan` 之后就变了，那里用的是 plan 的版本。
    看起来不统一，但另一边（先建一个计划行、令它的版本从 0 开始）会引入
    一个"版本 0"的假状态，更糟。
    """
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.CREATE_PLAN)
    _check_version(row.version, expected_session_version, f"会话 {session_id}")
    # ★ 猜测说明并入 `reason`：三个月后回看这条计划，唯一还能回答
    # "为什么这么排"的就是 `plan_revision.reason` 那一行。
    guess = _assert_task_is_clear_enough(db, session_id, changed_by=changed_by)
    if guess:
        reason = f"{reason}{guess}"

    if reads.get_plan(db, session_id) is not None:
        raise MissingPrerequisite(
            "这个会话已经有计划了。要改就调用修订（revise），"
            "否则旧版本与它的进度会被第二次创建覆盖掉。",
            detail={"session_id": session_id},
        )
    if not steps:
        raise MissingPrerequisite("计划至少要有一个步骤", detail={"session_id": session_id})

    plan = TaskPlan(
        plan_id=next_plan_id(_seq(db)),
        session_id=session_id,
        goal_derived=goal_derived,
        current_version=1,
        version=1,
        created_at=_now(),
    )
    db.add(plan)
    db.flush()

    _write_plan_rows(
        db,
        plan=plan,
        version_no=1,
        steps=steps,
        checkpoints=list(checkpoints),
    )
    db.add(
        PlanRevision(
            rev_id=next_plan_rev_id(_seq(db)),
            plan_id=plan.plan_id,
            from_version=0,
            to_version=1,
            change=f"创建计划（{len(steps)} 步）",
            reason=reason,
            basis=None,
            changed_by=changed_by,
            created_at=_now(),
        )
    )
    db.flush()
    return plan


def revise_plan(
    db: DbSession,
    session_id: str,
    *,
    steps: list[StepDraft],
    checkpoints: list[CheckpointDraft] = (),
    change: str,
    reason: str,
    expected_version: int,
    basis: Basis | None = None,
    changed_by: ChangedBy = ChangedBy.USER,
) -> TaskPlan:
    """改计划结构 → **新版本**。旧版本的行原地不动，因此"当时为什么那样规划"看得见。

    `reason` 必填（M7 §9 第 3 条「无理由不改计划」）。它在库层也是 NOT NULL —
    这里先拦一次，是为了给出可读的中文提示而不是一条 IntegrityError。

    进度**不是**通过重写步骤来保留的：新版本的行会继承同名 `step_id` 的
    `status`（见 `_write_plan_rows` 的 `carry_over`），所以"第 3 步已经做完"
    不会因为改了一句措辞而回到 pending。
    """
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.REVISE_PLAN)
    _require_text(reason, "reason", why="无理由不改计划（M7 §9 第 3 条）")
    _require_text(change, "change", why="要写清楚改了什么")
    if not steps:
        raise MissingPrerequisite("修订后的计划至少要有一个步骤", detail={"session_id": session_id})
    guess = _assert_task_is_clear_enough(db, session_id, changed_by=changed_by)
    if guess:
        reason = f"{reason}{guess}"

    plan = reads.require_plan(db, session_id)
    _check_version(plan.version, expected_version, f"会话 {session_id} 的计划")

    old_version = plan.current_version
    new_version = old_version + 1
    previous = _status_by_step_id(db, plan.plan_id, old_version)

    _write_plan_rows(
        db,
        plan=plan,
        version_no=new_version,
        steps=steps,
        checkpoints=list(checkpoints),
        carry_over=previous,
    )
    plan.current_version = new_version
    plan.version += 1
    db.add(
        PlanRevision(
            rev_id=next_plan_rev_id(_seq(db)),
            plan_id=plan.plan_id,
            from_version=old_version,
            to_version=new_version,
            change=change,
            reason=reason,
            basis=basis,
            changed_by=changed_by,
            created_at=_now(),
        )
    )
    db.flush()
    return plan


def _status_by_step_id(
    db: DbSession, plan_id: str, version_no: int
) -> dict[str, PlanStepStatus]:
    return {
        step.step_id: step.status
        for step in reads.plan_steps(db, plan_id, version_no=version_no)
    }


def _write_plan_rows(
    db: DbSession,
    *,
    plan: TaskPlan,
    version_no: int,
    steps: list[StepDraft],
    checkpoints: list[CheckpointDraft],
    carry_over: dict[str, PlanStepStatus] | None = None,
) -> None:
    """把一份计划的结构写成一版行。`carry_over` 让进度跨版本延续。"""
    allocated: list[str] = []
    for seq, draft in enumerate(steps, start=1):
        _require_text(draft.action, "action", why="每个步骤都要说清做什么")
        step_id = draft.step_id or next_plan_step_id(_seq(db), plan.plan_id)
        allocated.append(step_id)
        status = PlanStepStatus.PENDING
        if carry_over and step_id in carry_over:
            status = carry_over[step_id]
        db.add(
            PlanStep(
                row_id=plan_step_row_id(plan.plan_id, version_no, step_id),
                plan_id=plan.plan_id,
                version_no=version_no,
                step_id=step_id,
                seq=seq,
                action=draft.action,
                output=draft.output,
                method_ref=draft.method_ref,
                status=status,
            )
        )

    known_steps = set(allocated)
    for draft in checkpoints:
        if draft.at_step and draft.at_step not in known_steps:
            raise InvalidField(
                f"检查点 {draft.cp_id or '(新)'} 指向的步骤 {draft.at_step} "
                f"不在这一版计划里",
                detail={"at_step": draft.at_step, "steps": sorted(known_steps)},
            )
        cp_id = draft.cp_id or next_plan_cp_id(_seq(db), plan.plan_id)
        db.add(
            PlanCheckpoint(
                row_id=plan_cp_row_id(plan.plan_id, version_no, cp_id),
                plan_id=plan.plan_id,
                version_no=version_no,
                cp_id=cp_id,
                at_step=draft.at_step,
                check_method=draft.check_method,
                pass_criteria=draft.pass_criteria,
                fail_action=draft.fail_action,
                result=draft.result,
                result_at=_now() if draft.result is not CheckpointResult.PENDING else None,
                result_note=draft.result_note,
            )
        )
    db.flush()


def mark_step(
    db: DbSession,
    session_id: str,
    *,
    step_id: str,
    status: PlanStepStatus,
    expected_version: int,
) -> PlanStep:
    """标步骤进度。**不产生新版本** —— 否则"标记一步做完"会伪造一次计划修订。

    但**会** +1 `plan.version`：进度是计划对象的一部分，
    两个窗口同时标不同步骤时，后写的那个该知道"计划已经被谁动过"。
    """
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.MARK_STEP)

    plan = reads.require_plan(db, session_id)
    _check_version(plan.version, expected_version, f"会话 {session_id} 的计划")

    target = next(
        (
            step
            for step in reads.plan_steps(db, plan.plan_id)
            if step.step_id == step_id
        ),
        None,
    )
    if target is None:
        raise NotFound(
            f"当前版本的计划里没有步骤 {step_id}",
            detail={"session_id": session_id, "step_id": step_id},
        )
    target.status = status
    plan.version += 1
    db.flush()
    return target


def record_checkpoint(
    db: DbSession,
    session_id: str,
    *,
    cp_id: str,
    result: CheckpointResult,
    expected_version: int,
    result_note: str | None = None,
) -> PlanCheckpoint:
    """记录检查点结果（UI 一键操作，M0 §11-A2 不新增 LLM 工具）。

    `result=fail` 时界面按 `fail_action` 呈现，并建议 `propose_plan_change`
    （M8 §4.3）—— 但**不自动改计划**：检查点失败是"计划该被讨论"的信号，
    不是"系统替用户改计划"的许可。
    """
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.RECORD_CHECKPOINT)

    plan = reads.require_plan(db, session_id)
    _check_version(plan.version, expected_version, f"会话 {session_id} 的计划")

    target = next(
        (
            cp
            for cp in reads.plan_checkpoints(db, plan.plan_id)
            if cp.cp_id == cp_id
        ),
        None,
    )
    if target is None:
        raise NotFound(
            f"当前版本的计划里没有检查点 {cp_id}",
            detail={"session_id": session_id, "cp_id": cp_id},
        )
    target.result = result
    target.result_at = _now()
    if result_note is not None:
        target.result_note = result_note
    plan.version += 1
    db.flush()
    return target


# --------------------------------------------------------------------------- #
# stage
# --------------------------------------------------------------------------- #


def add_stage_item(
    db: DbSession,
    session_id: str,
    *,
    item_type: StageItemType,
    content: str,
    expected_version: int,
) -> StageItem:
    """往 stage 加一条。**stage 永不进全局**：它只是"想试但还没决定"的暂存区。"""
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.ADD_STAGE_ITEM)
    _check_version(row.version, expected_version, f"会话 {session_id}")
    _require_text(content, "content", why="空的暂存条目没有意义")

    item = StageItem(
        item_id=next_stage_item_id(_seq(db)),
        session_id=session_id,
        type=item_type,
        content=content.strip(),
        status=StageItemStatus.ACTIVE,
        created_at=_now(),
    )
    db.add(item)
    row.version += 1
    db.flush()
    return item


def settle_stage_item(
    db: DbSession,
    session_id: str,
    *,
    item_id: str,
    status: StageItemStatus,
    expected_version: int,
    result_ref: str | None = None,
) -> StageItem:
    """给一条 stage 条目定归宿：`deposited` / `discarded` / `archived`。

    **单向**：已经定过归宿的条目不能再改（`active` 是唯一可离开的状态）。
    否则"沉淀过又改回未决"会让 `result_ref` 指向一次真实发生过的全局写入，
    而界面显示它"还在待定"—— 那种不一致比丢数据更糟。
    """
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.SETTLE_STAGE_ITEM)
    _check_version(row.version, expected_version, f"会话 {session_id}")

    item = reads.get_stage_item(db, item_id)
    if item.session_id != session_id:
        raise NotFound(
            f"条目 {item_id} 不属于会话 {session_id}",
            detail={"item_id": item_id, "session_id": session_id},
        )
    if item.status is not StageItemStatus.ACTIVE:
        raise MissingPrerequisite(
            f"条目已经是 {item.status.value}，归宿只能定一次。",
            detail={"item_id": item_id, "status": item.status.value},
        )
    if status is StageItemStatus.ACTIVE:
        raise InvalidField(
            "不能把条目改回 active —— 归宿是单向的", detail={"item_id": item_id}
        )

    item.status = status
    item.settled_at = _now()
    if result_ref is not None:
        item.result_ref = result_ref
    row.version += 1
    db.flush()
    return item


# --------------------------------------------------------------------------- #
# 聊天 / 笔记 / 里程碑 / 总结（都不带版本：它们是追加记录）
# --------------------------------------------------------------------------- #


def add_chat_message(
    db: DbSession,
    session_id: str,
    *,
    content: str,
    role: ChatRole = ChatRole.USER,
    tags: list[str] | None = None,
    tool_trace: list[dict] | None = None,
    thinking: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cache_hit_tokens: int | None = None,
    cache_miss_tokens: int | None = None,
    anchor_step_id: str | None = None,
) -> ChatMessage:
    """记一条对话。P4 只写 `role=user`；P6 起 LLM 的回复也走这里。

    不带版本：消息是追加的，不存在"两人同时写同一条消息"。
    但**受状态机管**：只有 `active` 能聊（见 `guards`）。

    `tool_trace` 只有助手消息才有（用户消息不可能带工具调用）。
    它不是审计凭据 —— 真正的凭据是 `router_log` 与全局不变式；
    这里存的是**"它刚才做了什么"**，给用户看的。

    `thinking` / `prompt_tokens` / `completion_tokens` 同样是**只有助手才有**
    的东西（用户消息不可能有思考过程，也没有模型用量），因此共用同一条拒绝规则 ——
    见下面那个 `if`。三样一起加进来而不是分三次改签名：
    它们都是"这一次模型调用留下的痕迹"，分开加会出现
    "有用量但没思考过程"这种半截状态，而那种状态的合法值是不存在的。

    `anchor_step_id`（P15）是**这一次讨论锚定在哪一步** —— 它进同一个
    `llm_fields` 拒绝集合：用户消息没有"锚点"可言（他发那句话时没有
    经过 `resolve_anchor`），给用户消息填一个会造出一条查不到出处的记录。

    `cache_hit_tokens` / `cache_miss_tokens`（本版）是提供方报的**提示缓存**
    读数：命中与未命中各多少 prompt token。它们同样只属于助手消息，
    同样进那个拒绝集合。**两个都可空**，因为不是每个提供方都报
    （本地 Ollama 与测试替身就不报），而"没测到"与"0"是两件事。
    """
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.ADD_CHAT)
    _require_text(content, "content", why="空消息不记")
    llm_fields = {
        "tool_trace": tool_trace,
        "thinking": thinking,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_hit_tokens": cache_hit_tokens,
        "cache_miss_tokens": cache_miss_tokens,
        "anchor_step_id": anchor_step_id,
    }
    if role is not ChatRole.ASSISTANT:
        for name, value in llm_fields.items():
            if value:
                # 拒绝而不是忽略：一条带工具轨迹/用量的用户消息会让
                # 「谁动过东西」「这轮花了多少上下文」这两个问题
                # 多出不可能的答案，而它看起来还像是记录完整。
                raise InvalidField(
                    f"只有助手的回复可以带 {name} —— 用户消息不可能有它。",
                    detail={"role": role.value, "field": name},
                )

    msg = ChatMessage(
        msg_id=next_chat_msg_id(_seq(db)),
        session_id=session_id,
        role=role,
        content=content,
        tags=tags,
        tool_trace=tool_trace,
        thinking=thinking,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
        anchor_step_id=anchor_step_id,
        created_at=_now(),
    )
    db.add(msg)
    db.flush()
    return msg


def add_sidebar_message(
    db: DbSession,
    *,
    content: str,
    role: ChatRole = ChatRole.USER,
    tags: list[str] | None = None,
    tool_trace: list[dict] | None = None,
    thinking: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> SidebarMessage:
    """记一条侧边栏消息。

    ## 为什么它不是 `add_chat_message(..., session_id=SIDEBAR_SCOPE_ID)`

    三个理由，每一个单独都足够：

    1. **外键**：`chat_message.session_id` 真的指向 `session`，而"sidebar"
       不是会话。要绕过就得删掉那条外键。
    2. **状态机**：`add_chat_message` 会 `assert_writable(session.state)`。
       侧边栏**没有终态** —— 它是常驻的。一个"侧边栏写不进去"的报错
       只能是 bug，不可能是用户的处境。
    3. **`_require_text` 与拒绝规则要一样，但拒绝规则的理由不一样**：
       `add_chat_message` 拒绝"用户消息带 tool_trace"是因为
       "用户不可能调用工具"；这里同样拒绝，理由相同，所以校验照抄、
       归属校验去掉。

    刻意**不传 session_id**：侧边栏不属于任何会话，而一个能被传进来的
    `session_id` 参数迟早会被传进来。
    """
    _require_text(content, "content", why="空消息不记")
    if role is not ChatRole.ASSISTANT:
        for name, value in {
            "tool_trace": tool_trace,
            "thinking": thinking,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }.items():
            if value:
                raise InvalidField(
                    f"只有助手的回复可以带 {name} —— 用户消息不可能有它。",
                    detail={"role": role.value, "field": name},
                )

    row = SidebarMessage(
        msg_id=next_sidebar_msg_id(_seq(db)),
        role=role,
        content=content,
        tags=tags,
        tool_trace=tool_trace,
        thinking=thinking,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        created_at=_now(),
    )
    db.add(row)
    db.flush()
    return row


def clear_sidebar(db: DbSession) -> tuple[int, int]:
    """清空侧边栏，返回 `(删掉的消息数, 删掉的压缩记录数)`。

    ## 为什么它必须在 `writes.py` 而不是端点的函数体里

    端点是**没有写权限**的 —— `test_api_never_mutates_the_session_directly`
    用 AST 钉住了这件事（API 层出现 `db.execute` / `db.delete` 就是绕过）。
    这条规矩在这里不是为了整齐：一个在端点里手写 `delete()` 的实现
    不会经过本模块的任何一条拒绝规则，也不会被 I1 的归属表覆盖
    （`sidebar_message` 的 provenance 是 `sandbox_record`，
    那条归属声明是关于**这张表的所有写入**，不是关于"大部分写入"）。

    ## 这个函数的边界（它是本项目里唯一的真删除）

    - 删的是 `sidebar_message` 的**行**，以及这个 scope 的压缩记录；
    - **不碰任何全局认知**。已批准的提案与它的 `router_log` 留在原处 ——
      那是审计凭据，不该因为"我清了聊天记录"而消失。
    - 全局那条"原始记录不该被修订"的规矩针对的是**记忆**
      （原话、勾选、偏好）。侧边栏是**草稿纸**：用户想从空白开始，
      那是他的界面，不是历史。会话的 `chat_message` 不允许删，
      因为一个会话的任务定义与结论要靠它复现 —— 侧边栏没有结论。
    """
    messages = db.execute(delete(SidebarMessage)).rowcount or 0
    compacts = (
        db.execute(
            delete(ContextCompact).where(
                ContextCompact.scope == ContextScope.SIDEBAR,
                ContextCompact.scope_id == SIDEBAR_SCOPE_ID,
            )
        ).rowcount
        or 0
    )
    db.flush()
    return int(messages), int(compacts)


def add_compact(
    db: DbSession,
    scope: ContextScope,
    scope_id: str,
    *,
    covers_msg_ids: list[str],
    summary: str,
    prompt_tokens_before: int,
    limit_at_time: int,
    model: str = "",
    folds_compact_ids: list[str] | None = None,
) -> ContextCompact:
    """记一次对话压缩。**只追加，不改写任何旧压缩记录。**

    ## 三条被拒绝的输入，以及为什么每条都不是"顺手校验"

    - **`covers_msg_ids` 与 `folds_compact_ids` 同时为空** —— 一次没吃掉
      任何东西的压缩是一条假记录。它会出现在历史里，
      让"压过 3 次"变成一个查不清的说法。
    - **`summary` 空白** —— 空摘要不是一个"压得比较狠"的压缩，它是**失忆**：
      被吃掉的那些原话再也回不到上下文里，而这次压缩却声称处理过了。
    - **`prompt_tokens_before <= 0`** —— 这个数只有一个来源：模型报的
      `prompt_eval_count`。传 0 意味着"在没有任何实测的情况下压了"，
      而那种压缩的触发条件无从解释。

    ## 为什么它**不受状态机管**

    会话已经 `completed` 的对话当然不需要再压缩。但如果状态机在这里拦一道，
    会出现一个更坏的组合：**上下文满了 → 状态机不让压 → 下一轮装不下**。
    而压缩不改变任何业务事实（它只影响"模型看得见什么"），
    所以让它绕过状态机是安全的；真正越界的是压缩**写全局**，而它没有这个能力 ——
    它是沙箱记录，`ARCH-11` 保证它连 Router 都够不到。
    """
    folds = list(folds_compact_ids or [])
    if not covers_msg_ids and not folds:
        raise InvalidField(
            "压缩必须真的吃掉一些东西（原文或旧摘要）—— 两者都空是一条假记录。",
            detail={"scope": scope.value, "scope_id": scope_id},
        )
    _require_text(summary, "summary", why="空摘要不是压缩，是失忆")
    if prompt_tokens_before <= 0:
        raise InvalidField(
            "prompt_tokens_before 必须来自实测的 prompt_eval_count（> 0）。",
            detail={"prompt_tokens_before": prompt_tokens_before},
        )
    if limit_at_time <= 0:
        raise InvalidField(
            "limit_at_time 必须是当时的窗口上限（> 0）。",
            detail={"limit_at_time": limit_at_time},
        )

    row = ContextCompact(
        compact_id=next_compact_id(_seq(db)),
        scope=scope,
        scope_id=scope_id,
        covers_msg_ids=list(covers_msg_ids),
        folds_compact_ids=folds,
        summary=summary,
        prompt_tokens_before=prompt_tokens_before,
        limit_at_time=limit_at_time,
        model=model,
        created_at=_now(),
    )
    db.add(row)
    db.flush()
    return row


def annotate_message(
    db: DbSession,
    session_id: str,
    *,
    msg_id: str,
    quote: str,
    question: str | None = None,
) -> SessionAnnotation:
    """在一条助手回复上标注一句话，问「为什么这么说」（P15）。

    ## 三条拒绝，每条都对应一种会让这个功能变成噪声的用法

    1. **只能标注助手说的话。** 标注自己的话没有意义（"我为什么这么说"
       不需要系统回答），而且会让 `annotation` 与 `note` 变成两件一样的事。
    2. **`quote` 必须在那条消息里逐字存在。** 前端选中一段文字、原样发回来；
       对不上就拒收。★ 这条是这张表的**全部意义**：一条指向不存在内容的标注
       会让"凭什么这么说"的回答挂在一句谁都没说过的话上。
    3. **消息必须属于这个会话。** 否则 `annotation` 能变成跨会话的引用通道，
       而它的理由（"回到那句话"）只在同一个会话里成立。

    这里**不**检查"这条回复是不是调过工具"：没调过工具的回复同样可以被标注
    （依据就是当时的上下文），`basis.explain` 会把这件事如实说出来。
    """
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.ADD_NOTE)
    # ★ 空引用用 `InvalidField`（422）而不是 `MissingPrerequisite`（409）：
    # 它不是"前置状态没满足"，而是"你给的这一条不对"。同一函数里其余
    # 三条拒绝也都是 `InvalidField` —— 四种"这样标不行"应该是同一种错误。
    needle = (quote or "").strip()
    if not needle:
        raise InvalidField(
            "引用不能为空。空引用不记：标注必须指向一句话。",
            detail={"field": "quote"},
        )

    message = reads.get_chat_message(db, msg_id)
    if message is None or message.session_id != session_id:
        raise NotFound(
            "这条消息不在这个会话里。",
            detail={"session_id": session_id, "msg_id": msg_id},
        )
    if message.role is not ChatRole.ASSISTANT:
        raise InvalidField(
            "只能标注助手的回复。",
            detail={"msg_id": msg_id, "role": message.role.value},
        )

    if needle not in message.content:
        raise InvalidField(
            "这段引用在那条回复里找不到 —— 标注必须逐字来自它。",
            detail={"msg_id": msg_id, "quote": needle[:120]},
        )

    annotation = SessionAnnotation(
        annotation_id=next_annotation_id(_seq(db)),
        session_id=session_id,
        msg_id=msg_id,
        quote=needle,
        question=(question or "").strip() or None,
        created_at=_now(),
    )
    db.add(annotation)
    db.flush()
    return annotation


def delete_annotation(db: DbSession, session_id: str, annotation_id: str) -> None:
    """删掉一条标注。**用户有解释权**：他标错了、或不想追问了。

    删除不级联任何东西 —— 标注不改变权威状态（它是问题，不是结论）。
    """
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.ADD_NOTE)
    annotation = reads.get_annotation(db, session_id, annotation_id)
    if annotation is None:
        raise NotFound(
            "没有这条标注。",
            detail={"session_id": session_id, "annotation_id": annotation_id},
        )
    db.delete(annotation)
    db.flush()


def write_reflection(
    db: DbSession,
    session_id: str,
    *,
    answers: dict[str, str] | None = None,
    content: str | None = None,
) -> SessionReflection:
    """复盘之后按指引写下感想（P15）。

    ## 两道门槛，各管一件不同的事

    1. **状态**（`WRITE_REFLECTION` 那行）：`review` / `completed` / `abandoned`。
       执行中（`active`）不开放 —— 那时想记就写笔记。界面上的按钮拦不住 API，
       所以这条边界必须在状态机里。
    2. **事实**：这个会话得**真的复盘过**（至少一条 `session_summary`）。
       否则"感想"与"总结"会变成同一件事的两个入口，而 `V-PAR` 要评的
       「复盘产物完整度」就没有唯一的那份产物了。

    空感想不写：四要素一项都没填、也没给正文 → 拒绝。
    """
    from app.session_domain import reflection as reflection_module

    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.WRITE_REFLECTION)

    summary = reads.latest_summary(db, session_id)
    if summary is None:
        raise MissingPrerequisite(
            "这个会话还没有复盘总结 —— 感想是对复盘的补充，不是替代。"
            "先写下（或让助手写下）总结。",
            detail={"session_id": session_id, "missing": "session_summary"},
        )

    cleaned = {
        key: value.strip()
        for key, value in (answers or {}).items()
        if isinstance(value, str) and value.strip()
    }
    text = reflection_module.compose(cleaned, content)
    if not text.strip():
        raise MissingPrerequisite(
            "空的感想不写：四要素里至少写一项（或直接给正文）。",
            detail={"field": "content", "elements": [e.key for e in reflection_module.ELEMENTS]},
        )

    reflection = SessionReflection(
        reflection_id=next_reflection_id(_seq(db)),
        session_id=session_id,
        summary_id=summary.summary_id,
        answers=cleaned or None,
        content=text,
        created_at=_now(),
    )
    db.add(reflection)
    db.flush()
    return reflection


def add_note(
    db: DbSession,
    session_id: str,
    *,
    content: str,
    tags: list[str] | None = None,
) -> Note:
    """写笔记。**唯一在终态仍可写的操作**（用户裁定的例外口子）。"""
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.ADD_NOTE)
    _require_text(content, "content", why="空笔记不写")

    note = Note(
        note_id=next_note_id(_seq(db)),
        session_id=session_id,
        content=content,
        tags=tags,
        created_at=_now(),
    )
    db.add(note)
    db.flush()
    return note


def add_milestone(db: DbSession, session_id: str, *, title: str) -> Milestone:
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.ADD_MILESTONE)
    _require_text(title, "title", why="里程碑得有个名字")

    milestone = Milestone(
        milestone_id=next_milestone_id(_seq(db)),
        session_id=session_id,
        title=title.strip(),
        status=MilestoneStatus.PENDING,
        created_at=_now(),
    )
    db.add(milestone)
    db.flush()
    return milestone


def complete_milestone(
    db: DbSession, session_id: str, *, milestone_id: str, results: str | None = None
) -> Milestone:
    """达成里程碑。

    **完成不自动沉淀**（M8 §4.3：`checkpoint_milestone` 只提示）。
    这里只把状态与成果写下来；要不要把这次达成变成一条全局认知，
    是用户在沉淀那一步（R4b）自己决定的事。
    """
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.COMPLETE_MILESTONE)

    milestone = reads.get_milestone(db, milestone_id)
    if milestone.session_id != session_id:
        raise NotFound(
            f"里程碑 {milestone_id} 不属于会话 {session_id}",
            detail={"milestone_id": milestone_id, "session_id": session_id},
        )
    if milestone.status is MilestoneStatus.DONE:
        raise MissingPrerequisite(
            f"里程碑 {milestone_id} 已经是达成状态", detail={"milestone_id": milestone_id}
        )
    milestone.status = MilestoneStatus.DONE
    milestone.done_at = _now()
    if results is not None:
        milestone.results = results
    db.flush()
    return milestone


def write_summary(
    db: DbSession,
    session_id: str,
    *,
    content: str,
    findings: list[dict] | None = None,
) -> SessionSummary:
    """写完成总结。**这是 R4a 的触发点**，但总结本身不写全局。

    `findings` 是沉淀通路的原料（`sedimentation.build_candidates` 从这里取），
    它刻意只是**候选**：一条 finding 变成一条全局写入，要经 Router 的判定表，
    而且默认还要用户确认（R4a = 自动产出、用户未过目）。
    """
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.WRITE_SUMMARY)
    _require_text(content, "content", why="空的总结不写")

    summary = SessionSummary(
        summary_id=next_summary_id(_seq(db)),
        session_id=session_id,
        content=content,
        findings=findings,
        created_at=_now(),
    )
    db.add(summary)
    db.flush()
    return summary


# --------------------------------------------------------------------------- #
# 会话资料（用户上传的附件，P6+.5）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AttachResult:
    """一次上传的结果。

    `duplicate=True` 表示**入库这一步没发生** —— 同一个会话里已经有一份
    内容完全相同的资料，直接把那一份还回去。这是刻意的：
    同一份文件传两次会在上下文里出现两条一模一样的清单项，
    而模型看到两条同样的东西，会以为用户强调了两遍。
    """

    attachment: ChatAttachment
    duplicate: bool


def attach_file(
    db: DbSession,
    session_id: str,
    *,
    filename: str,
    raw: bytes,
) -> AttachResult:
    """把一次上传落成会话资料。

    ## 顺序：先解析 → 再看是不是重复 → 落盘 → 入库

    每一步的位置都是有理由的：

    1. **先解析**：解析失败（类型不对/编码不对/伪装的文件）就根本不该落盘 ——
       否则 `var/uploads/` 会积一堆谁也读不了的文件；
    2. **再看重复**（按 `sha256`）：重复时**不写盘也不入库**，
       省掉一次磁盘写，也让"传过了"这件事不产生第二行；
    3. **落盘先于入库**：万一入库失败，留下的是没人引用的文件（垃圾）；
       反过来会是"库里记着有原件、磁盘上没有"（数据丢失）。
       两害相权取垃圾 —— 见 `app/attachments.py` 的同一段说明；
    4. **入库**。

    ## 它不写全局

    上传本身**只写沙箱**。经历要变成基线/偏好/证据，只能由模型在讨论里
    提 `submit_global_update`（走审核队列），或者用户在沉淀那一步自己选。
    这条边界有测试钉着（`tests/api/test_attachments_api.py`：
    上传前后认知四表与 `router_log` 行数一个都不变）。
    """
    # 放在函数体内 import：`app.attachments` 是纯解析模块，
    # 而这里的每次调用都会真的读字节、开 zip —— 让它出现在 import 图谱里
    # 会让人以为本域依赖文件系统（其实只有这一个函数依赖）。
    from app import attachments as attachment_layer

    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.ADD_ATTACHMENT)

    parsed = attachment_layer.parse(filename, raw)
    existing = reads.find_attachment_by_digest(db, session_id, parsed.sha256)
    if existing is not None:
        return AttachResult(attachment=existing, duplicate=True)

    attachment_id = next_attachment_id(_seq(db))
    stored_path = attachment_layer.store_original(
        session_id, attachment_id, parsed.ext, raw
    )
    attachment = ChatAttachment(
        attachment_id=attachment_id,
        session_id=session_id,
        filename=parsed.filename,
        ext=parsed.ext,
        kind=parsed.kind,
        bytes_len=parsed.bytes_len,
        chars_len=parsed.chars_len,
        sha256=parsed.sha256,
        stored_path=stored_path,
        text=parsed.text,
        truncated=parsed.truncated,
        note=parsed.note,
        created_at=_now(),
    )
    db.add(attachment)
    db.flush()
    return AttachResult(attachment=attachment, duplicate=False)


def link_attachment_to_message(
    db: DbSession, attachment_id: str, msg_id: str
) -> ChatAttachment:
    """把资料记到"第一次带上它的那条消息"上。

    **单独一个函数**，而不是 `attach_file` 的参数：上传与发消息是两个动作
    （用户可以传完先不聊、也可以一次带多份）。合成一个参数会逼着
    "上传即发言"，而那不是用户要的流程。
    """
    attachment = reads.get_attachment(db, attachment_id)
    attachment.msg_id = msg_id
    db.flush()
    return attachment


def delete_attachment(db: DbSession, session_id: str, attachment_id: str) -> ChatAttachment:
    """删掉一份资料（连同磁盘上的原件）。

    **删不掉原件不算失败**：库里那一行已经删了，真相是一致的；
    磁盘上残留一个文件只是垃圾。所以 `remove_original` 的返回值只被丢掉，
    不参与任何判断 —— 让它有机会把一次成功的删除变成一次报错，
    是"副本管住了真相"。
    """
    from app import attachments as attachment_layer

    row = reads.get_session(db, session_id)
    # **先认领属主，再问状态**：一个不属于这个会话的 id 无论在什么状态下
    # 都该是 404（"没有这个东西"），而不是"你现在的状态不能删它"（409）。
    # 顺序反过来的话，归档会话上的跨会话探测会得到 409 —— 那就等于确认了它的存在。
    attachment = reads.require_attachment(db, session_id, attachment_id)
    assert_writable(row.state, SessionOperation.ATTACHMENT_REMOVE)
    stored_path = attachment.stored_path
    db.delete(attachment)
    db.flush()
    attachment_layer.remove_original(stored_path)
    return attachment


__all__ = [
    "AttachResult",
    "CheckpointDraft",
    "INPUT_FIELDS",
    "StepDraft",
    "add_chat_message",
    "add_milestone",
    "add_note",
    "add_stage_item",
    "attach_file",
    "complete_milestone",
    "create_folder",
    "create_plan",
    "create_session",
    "delete_attachment",
    "link_attachment_to_message",
    "mark_step",
    "record_checkpoint",
    "revise_plan",
    "settle_stage_item",
    "transition",
    "update_input",
    "update_motivation",
    "update_snapshot",
    "write_summary",
]
