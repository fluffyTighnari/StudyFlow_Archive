"""Session 域 DTO —— **ORM → HTTP 的唯一转换层**（与 `global_domain.schemas` 同分工）。

两条约定：

1. **读模型全部 `from_attributes=True`**，写模型全部"显式字段 + `exclude_unset`"。
   前者让 `model_validate(row)` 直接可用；后者让 `PATCH` 能区分
   「没传这个字段」与「显式传了 null 要清空」—— 这两件事在合并语义里必须分开，
   否则界面永远没法把一个字段改回空。
2. **枚举与全局域共用一份定义**（`Basis` 从 `global_domain.enums` 引入），
   本文件不重复声明任何取值。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.global_domain.enums import Basis
from app.session_domain.enums import (
    ChatRole,
    ChangedBy,
    CheckpointResult,
    InputConfidence,
    Level,
    MilestoneStatus,
    MotivationType,
    PlanStepStatus,
    SessionState,
    StageItemStatus,
    StageItemType,
)


class _ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class _WriteModel(BaseModel):
    """写模型的基类：**多给的字段一律 422，不静默忽略**。

    这一条与全局域的 `router.contracts._Strict` 是同一个理由，值得在这儿再说一遍：
    静默忽略未知字段的后果是**用户以为存上了**。把 `goal` 打成 `gol`，
    界面提交成功、返回 200、侧页上那一栏还是空的 —— 而失败信息从头到尾没有出现过。
    这类"看起来成功"的失败，比直接报错难查一个数量级。

    顺带它还分开了一件容易被混为一谈的事：**形状不对（422）与策略拒绝
    （201 + `status=rejected`）**（M0 §9 的分界）。未知字段属于前者。
    """

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# Folder
# --------------------------------------------------------------------------- #


class FolderRead(_ReadModel):
    folder_id: str
    name: str
    goal: str | None = None
    c_retrieval_dims: list[str] | None = None
    created_at: datetime


class FolderCreate(_WriteModel):
    name: str = Field(min_length=1, max_length=200)
    goal: str | None = None
    c_retrieval_dims: list[str] | None = None


# --------------------------------------------------------------------------- #
# 会话头
# --------------------------------------------------------------------------- #


class SessionRead(_ReadModel):
    session_id: str
    folder_id: str
    title: str
    state: SessionState
    version: int
    created_at: datetime
    activated_at: datetime | None = None
    completed_at: datetime | None = None
    abandoned_at: datetime | None = None
    #: 当前状态下**可以**转到哪些状态。界面按钮直接由它渲染 ——
    #: 这样"哪些按钮该出现"只有一处答案（`guards.TRANSITIONS`），
    #: 而不是前端也维护一份状态机。
    allowed_transitions: list[str] = Field(default_factory=list)


class SessionCreate(_WriteModel):
    folder_id: str
    title: str = Field(min_length=1, max_length=200)
    input_goal: str | None = None
    intent_raw: str | None = None
    intent_confidence: InputConfidence = InputConfidence.MISSING


class TransitionRequest(_WriteModel):
    target: SessionState
    expected_version: int = Field(ge=1)
    reason: str | None = None


# --------------------------------------------------------------------------- #
# 任务定义区
# --------------------------------------------------------------------------- #


class InputRead(_ReadModel):
    session_id: str
    goal: str | None = None
    why: str | None = None
    starting_point: str | None = None
    task_type: str | None = None
    constraints: str | None = None
    success_criteria: str | None = None
    priority: int | None = None
    intent_raw: str | None = None
    intent_confidence: InputConfidence
    version: int
    updated_at: datetime


class InputRevisionRead(_ReadModel):
    rev_id: str
    field: str
    old_value: str | None = None
    new_value: str | None = None
    changed_by: ChangedBy
    reason: str | None = None
    created_at: datetime


class InputUpdate(_WriteModel):
    """**只写你传了的字段**（`exclude_unset`）。

    显式传 `null` 表示"清空这一项"，不传表示"别动它"。
    没有这个区分，界面上就没办法把一个填错的字段改回空白 ——
    只能填一个空格凑合，而空格会在下一次比对里变成一次假改动。
    """

    expected_version: int = Field(ge=1)
    goal: str | None = None
    why: str | None = None
    starting_point: str | None = None
    task_type: str | None = None
    constraints: str | None = None
    success_criteria: str | None = None
    priority: int | None = Field(default=None, ge=0, le=10)
    intent_raw: str | None = None
    intent_confidence: InputConfidence | None = None
    reason: str | None = None


# --------------------------------------------------------------------------- #
# 动机
# --------------------------------------------------------------------------- #


class MotivationRead(_ReadModel):
    session_id: str
    type: MotivationType
    strength: Level
    drivers: list[str] | None = None
    obstacles: list[dict[str, Any]] | None = None
    updated_at: datetime


class MotivationRevisionRead(_ReadModel):
    rev_id: str
    change: str
    reason: str | None = None
    basis: Basis
    changed_by: ChangedBy
    created_at: datetime


class MotivationUpdate(_WriteModel):
    expected_version: int = Field(ge=1)
    #: 必填：一次动机改动必须说清"改了什么"。这里不做"字段都传齐"的校验，
    #: 因为动机常常只改一项（例如只是强度变了）。
    change: str = Field(min_length=1)
    type: MotivationType | None = None
    strength: Level | None = None
    drivers: list[str] | None = None
    obstacles: list[dict[str, Any]] | None = None
    reason: str | None = None
    basis: Basis = Basis.STATED


# --------------------------------------------------------------------------- #
# 状态快照
# --------------------------------------------------------------------------- #


class SnapshotRead(_ReadModel):
    session_id: str
    energy: Level | None = None
    stress: Level | None = None
    environment: str | None = None
    note: str | None = None
    updated_at: datetime


class SnapshotUpdate(_WriteModel):
    expected_version: int = Field(ge=1)
    energy: Level | None = None
    stress: Level | None = None
    environment: str | None = Field(default=None, max_length=64)
    note: str | None = None


# --------------------------------------------------------------------------- #
# 计划
# --------------------------------------------------------------------------- #


class StepDraftIn(_WriteModel):
    action: str = Field(min_length=1)
    output: str | None = None
    method_ref: str | None = None
    #: 传了就沿用这个 `step_id`（改措辞而不换身份 → 进度跟着走）。
    step_id: str | None = None


class CheckpointDraftIn(_WriteModel):
    at_step: str
    check_method: str = Field(min_length=1)
    pass_criteria: str = Field(min_length=1)
    fail_action: str | None = None
    cp_id: str | None = None


class StepRead(_ReadModel):
    step_id: str
    seq: int
    action: str
    output: str | None = None
    method_ref: str | None = None
    status: PlanStepStatus


class CheckpointRead(_ReadModel):
    cp_id: str
    at_step: str
    check_method: str
    pass_criteria: str
    fail_action: str | None = None
    result: CheckpointResult
    result_at: datetime | None = None
    result_note: str | None = None


class PlanRevisionRead(_ReadModel):
    rev_id: str
    from_version: int
    to_version: int
    change: str
    reason: str
    basis: Basis | None = None
    changed_by: ChangedBy
    created_at: datetime


class PlanRead(BaseModel):
    """一版计划的完整快照。**历史版本也用这个形状** —— 于是"回看旧计划"
    与"看当前计划"用的是同一个模型，前端不必为历史写第二套渲染。"""

    plan_id: str
    session_id: str
    goal_derived: str | None = None
    current_version: int
    version: int
    version_no: int
    steps: list[StepRead]
    checkpoints: list[CheckpointRead]


class PlanCreate(_WriteModel):
    expected_session_version: int = Field(ge=1)
    goal_derived: str | None = None
    steps: list[StepDraftIn] = Field(min_length=1)
    checkpoints: list[CheckpointDraftIn] = Field(default_factory=list)
    reason: str = "创建计划"


class PlanRevise(_WriteModel):
    expected_version: int = Field(ge=1)
    change: str = Field(min_length=1)
    #: **必填**（M7 §9 第 3 条）。写成 `min_length=1` 而不是 `str | None`：
    #: 无理由不改计划这条规矩，在 422 层就挡住，不等到库层 NOT NULL。
    reason: str = Field(min_length=1)
    steps: list[StepDraftIn] = Field(min_length=1)
    checkpoints: list[CheckpointDraftIn] = Field(default_factory=list)
    basis: Basis | None = None


class StepMark(_WriteModel):
    expected_version: int = Field(ge=1)
    status: PlanStepStatus


class CheckpointRecord(_WriteModel):
    expected_version: int = Field(ge=1)
    result: CheckpointResult
    result_note: str | None = None


# --------------------------------------------------------------------------- #
# stage
# --------------------------------------------------------------------------- #


class StageItemRead(_ReadModel):
    item_id: str
    type: StageItemType
    content: str
    status: StageItemStatus
    result_ref: str | None = None
    created_at: datetime
    settled_at: datetime | None = None


class StageItemCreate(_WriteModel):
    expected_version: int = Field(ge=1)
    type: StageItemType
    content: str = Field(min_length=1)


class StageItemSettle(_WriteModel):
    expected_version: int = Field(ge=1)
    status: StageItemStatus
    result_ref: str | None = None


# --------------------------------------------------------------------------- #
# 聊天 / 笔记 / 里程碑 / 总结
# --------------------------------------------------------------------------- #


class ChatMessageRead(_ReadModel):
    msg_id: str
    role: ChatRole
    content: str
    tags: list[str] | None = None
    #: 这条回复背后**系统实际执行过的工具调用**（P6）。普通聊天为 `None`。
    #:
    #: 它随读模型一起给出来，是因为用户明确要求「我能看到它做了什么」——
    #: 而理由写在正文里、动作写在轨迹里，两者的区别正是「理由 ≠ 事实」那条教训。
    tool_trace: list[dict[str, Any]] | None = None
    created_at: datetime


class ChatMessageCreate(_WriteModel):
    content: str = Field(min_length=1)
    tags: list[str] | None = None


class NoteRead(_ReadModel):
    note_id: str
    content: str
    tags: list[str] | None = None
    created_at: datetime
    updated_at: datetime | None = None


class NoteCreate(_WriteModel):
    content: str = Field(min_length=1)
    tags: list[str] | None = None


class MilestoneRead(_ReadModel):
    milestone_id: str
    title: str
    status: MilestoneStatus
    results: str | None = None
    created_at: datetime
    done_at: datetime | None = None


class MilestoneCreate(_WriteModel):
    title: str = Field(min_length=1, max_length=200)


class MilestoneComplete(_WriteModel):
    results: str | None = None


class SummaryRead(_ReadModel):
    summary_id: str
    content: str
    findings: list[dict[str, Any]] | None = None
    created_at: datetime


class SummaryCreate(_WriteModel):
    content: str = Field(min_length=1)
    findings: list[dict[str, Any]] | None = None


__all__ = [
    "ChatMessageCreate",
    "ChatMessageRead",
    "CheckpointDraftIn",
    "CheckpointRead",
    "CheckpointRecord",
    "FolderCreate",
    "FolderRead",
    "InputRead",
    "InputRevisionRead",
    "InputUpdate",
    "MilestoneComplete",
    "MilestoneCreate",
    "MilestoneRead",
    "MotivationRead",
    "MotivationRevisionRead",
    "MotivationUpdate",
    "NoteCreate",
    "NoteRead",
    "PlanCreate",
    "PlanRead",
    "PlanRevisionRead",
    "PlanRevise",
    "SessionCreate",
    "SessionRead",
    "SnapshotRead",
    "SnapshotUpdate",
    "StageItemCreate",
    "StageItemRead",
    "StageItemSettle",
    "StepDraftIn",
    "StepMark",
    "StepRead",
    "SummaryCreate",
    "SummaryRead",
    "TransitionRequest",
]
