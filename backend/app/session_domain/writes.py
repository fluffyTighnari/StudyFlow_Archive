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

from sqlalchemy.orm import Session as DbSession

from app.core.errors import (
    InvalidField,
    MissingPrerequisite,
    NotFound,
    VersionConflict,
)
from app.core.ids import (
    next_attachment_id,
    next_chat_msg_id,
    next_folder_id,
    next_input_rev_id,
    next_milestone_id,
    next_motivation_rev_id,
    next_note_id,
    next_plan_cp_id,
    next_plan_id,
    next_plan_rev_id,
    next_plan_step_id,
    next_session_id,
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
    InputConfidence,
    Level,
    MilestoneStatus,
    MotivationType,
    PlanStepStatus,
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
        created_at=_now(),
    )
    db.add(row)
    db.flush()
    return row


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


def transition(
    db: DbSession,
    session_id: str,
    *,
    target: str,
    expected_version: int,
    reason: str | None = None,
) -> SessionModel:
    """状态转移。**边表之外的转移一概拒绝**（`guards.TRANSITIONS`）。

    两个附带后果，都是这次实施里刻意加上的：

    1. `draft → active` 要求定义区**至少写了目标**。没有目标的会话被执行起来，
       只会把"我不知道要做什么"变成一大堆动作记录。
    2. 进入 `completed` / `abandoned` 时，仍处于 `active` 的 stage 条目
       一律转 `archived`（用户裁定：**保留行，不删除**）。
       这就是"结束即弃"在数据上的真实含义 —— 弃的是它的待定身份，不是这条记录。
    """
    row = reads.get_session(db, session_id)
    edge = assert_transition(row.state, _state(target))
    assert_writable(row.state, SessionOperation.TRANSITION)
    _check_version(row.version, expected_version, f"会话 {session_id}")
    if edge.target.value == "active":
        input_row = reads.get_input(db, session_id)
        if not (input_row.goal and input_row.goal.strip()):
            raise MissingPrerequisite(
                "激活之前要先写下这次会话的目标（任务定义区的 goal）。"
                "没有目标的执行，只会把「我不知道要做什么」记录成一大堆动作。",
                detail={"session_id": session_id, "field": "goal"},
            )

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
) -> ChatMessage:
    """记一条对话。P4 只写 `role=user`；P6 起 LLM 的回复也走这里。

    不带版本：消息是追加的，不存在"两人同时写同一条消息"。
    但**受状态机管**：只有 `active` 能聊（见 `guards`）。

    `tool_trace` 只有助手消息才有（用户消息不可能带工具调用）。
    它不是审计凭据 —— 真正的凭据是 `router_log` 与全局不变式；
    这里存的是**"它刚才做了什么"**，给用户看的。
    """
    row = reads.get_session(db, session_id)
    assert_writable(row.state, SessionOperation.ADD_CHAT)
    _require_text(content, "content", why="空消息不记")
    if tool_trace and role is not ChatRole.ASSISTANT:
        # 拒绝而不是忽略：一条带工具轨迹的用户消息会让「谁动过东西」这个问题
        # 多出一个不可能的答案，而它看起来还像是记录完整。
        raise InvalidField(
            "只有助手的回复可以带 tool_trace —— 用户消息不可能调用工具。",
            detail={"role": role.value},
        )

    msg = ChatMessage(
        msg_id=next_chat_msg_id(_seq(db)),
        session_id=session_id,
        role=role,
        content=content,
        tags=tags,
        tool_trace=tool_trace,
        created_at=_now(),
    )
    db.add(msg)
    db.flush()
    return msg


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
