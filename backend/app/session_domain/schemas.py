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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.global_domain.enums import Basis
from app.session_domain.enums import (
    ChatRole,
    ChangedBy,
    CheckpointResult,
    ClarificationStatus,
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
    #: 侧栏手动顺序（`Order by: Manual`）。
    sort_order: int = 0
    #: 归档时刻 —— **不是状态**（见 `models.Folder.archived_at`）。
    archived_at: datetime | None = None
    #: 派生：`max(会话创建, 该会话最后一条聊天)`。不落列，所以永远不会是旧值。
    updated_at: datetime
    #: 未归档的会话数 / 其中处于 `active` 的个数（侧栏上的小数字）。
    session_count: int = 0
    active_session_count: int = 0


class FolderCreate(_WriteModel):
    name: str = Field(min_length=1, max_length=200)
    goal: str | None = None
    c_retrieval_dims: list[str] | None = None


class FolderPatch(_WriteModel):
    """侧栏对文件夹的整理动作：改名 / 归档 / 挪一位。

    四个字段都是"不传就不动"，所以 `archived: false` 与不传 `archived`
    是两件不同的事 —— 前者是取消归档，后者是"别碰它"。
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    goal: str | None = None
    archived: bool | None = None
    #: 在同层里挪一位。**没有 `sort_order` 这个可写字段**：顺序由服务端
    #: 重编号决定，客户端不参与算数（否则两个客户端各算各的，就会撞在一起）。
    move: Literal["up", "down"] | None = None


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
    #: 侧栏手动顺序（同一文件夹内比较才有意义）。
    sort_order: int = 0
    #: 归档时刻 —— **不是 `state`**。归档的会话可以有任意 `state`。
    archived_at: datetime | None = None
    #: 派生：`max(创建, 最后一条聊天)`。
    updated_at: datetime
    #: 当前状态下**可以**转到哪些状态。界面按钮直接由它渲染 ——
    #: 这样"哪些按钮该出现"只有一处答案（`guards.TRANSITIONS`），
    #: 而不是前端也维护一份状态机。
    allowed_transitions: list[str] = Field(default_factory=list)


class SessionPatch(_WriteModel):
    """侧栏对会话的整理动作：改名 / 归档 / 挪一位。

    ★ **不包含 `state`**：状态只能走 `/transitions`（带理由、带审计、
    受 `WRITE_GUARDS` 约束）。如果这里也能改 `state`，那条边表就白建了。
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None
    move: Literal["up", "down"] | None = None


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
# 澄清问题：任务不明确时反问（P13）
# --------------------------------------------------------------------------- #


class ClarificationAsk(_WriteModel):
    """问一个关于任务本身的问题。

    `slot` 只能是定义区真实存在的字段（`goal` / `starting_point` /
    `success_criteria`）—— 答案要真的写进去，不是存着好看。
    """

    slot: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1)
    #: 候选答案。给"他会怎么答"一个形状：
    #: 纯自由文本的问题，答案质量全看他那天的心情。
    options: list[str] | None = None
    asked_by: ChangedBy = ChangedBy.AGENT


class ClarificationAnswer(_WriteModel):
    answer: str = Field(min_length=1)
    expected_version: int = Field(ge=1)


class ClarificationDismiss(_WriteModel):
    #: 必填 —— 它是"这条计划为什么是按助手判断排的"的唯一解释。
    reason: str = Field(min_length=1)


class ClarificationRead(_ReadModel):
    clarification_id: str
    session_id: str
    slot: str
    question: str
    options: list[str] | None = None
    status: ClarificationStatus
    answer: str | None = None
    dismiss_reason: str | None = None
    asked_by: ChangedBy
    created_at: datetime
    resolved_at: datetime | None = None


class ClarificationAnswerRead(_ReadModel):
    """回答的结果 = **问题 + 更新后的定义区**。

    两件东西一起返回，因为"答案到底写进定义区了没有"是这套机制
    唯一会出错的地方：只返回问题的话，前端还得再拉一次才知道它落到哪了。
    """

    clarification: ClarificationRead
    input: InputRead


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
    #: 模型的完整思考过程（P6+.6）。普通聊天与不思考的模型为 `None`。
    #:
    #: ★ 它与正文**分开**给出来，理由与 `tool_trace` 同源但更强：
    #: 思考是**推理**，正文是**结论**，而本项目从头到尾在区分
    #: 「理由 ≠ 事实」。合并成一个字段会让界面无法只显示结论，
    #: 也会让模型自己的猜测在下一次读回时看起来像是它说过的话。
    thinking: str | None = None
    #: 这次回复**实测**消耗的上下文（P6+.6）。
    #:
    #: 它是"当前上下文用了多少"的**唯一**来源 —— 模型每次调用都报
    #: `prompt_eval_count`，所以这个问题有真实答案，不需要估算。
    #: `None` = 没测到（与 0 相反：0 意味着"什么都没发"）。
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    #: 命中 / 未命中的 prompt token（DeepSeek 的 usage 里给）。
    #: **`None` = 没测到**，不是 0 —— 界面据此显示「没测到」。
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None
    #: 这条回复**当时锚定在哪一步**（P15）。`None` = 没记录（旧消息）。
    #:
    #: 它随读模型一起给出来，因为「看推断理由」要用它重放当时的上下文 ——
    #: 界面拿不到它就只能按今天的规则猜一步，而那会显示成"当时的证据"。
    anchor_step_id: str | None = None
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


class AnnotationRead(_ReadModel):
    """一条句子标注 + **它的推断理由**（P15）。

    `basis` 与标注同一次返回，是为了让界面点一下就出东西 ——
    理由不是一句话，而是一组可核对的事实（工具轨迹、条目 id、重放的节标题）。
    """

    annotation_id: str
    msg_id: str
    quote: str
    question: str | None = None
    created_at: datetime
    #: 现算的依据（`session_domain/basis.explain`）。**不落库**：
    #: 落下来就会过期（会话状态一变，"它当时能看到什么"就被这份快照顶替了）。
    basis: dict[str, Any]


class AnnotationCreate(_WriteModel):
    """标注一句话。

    `quote` 必须是那条回复里**逐字存在**的一段 —— 由 `writes.annotate_message`
    核对，对不上直接 422。前端负责把选中文字原样送回来。
    """

    msg_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    question: str | None = None


class DeletedAnnotationRead(_ReadModel):
    """删除结果。**返回被删掉的那一条**（与资料删除同一约定）。

    为什么不是 204：本项目「写端点必须声明响应模型」这条规矩不允许
    一个没有响应体的写端点 —— 而那不是形式主义，界面确实要能从响应里
    确认"删的是哪一句"（同一个会话里可以有好几条标注）。
    """

    deleted: AnnotationRead


class ReflectionGuideRead(_ReadModel):
    """「写感想」的指引：四要素 + 这次会话里与每项相关的事实。

    ★ 它不是一份通用问卷：每一条 `facts` 都来自这次会话真实的数据
    （计划进度、用户自己写的完成判据、过程记录、笔记）。
    固定的四句话贴给任何会话都成立，因此也帮不上任何忙。
    """

    session_id: str
    items: list[dict[str, Any]]
    #: 还没写的那几项（**不是分数**：它只回答"还差哪几块"）。
    missing: list[str]
    #: 已经写了几条感想。
    existing: int
    #: 现在不建议写的原因（空串 = 可以写）。
    blocked_reason: str
    elements_note: str


class ReflectionRead(_ReadModel):
    reflection_id: str
    #: 写这条感想时对照的那份复盘总结。`None` = 那份总结已不存在。
    summary_id: str | None = None
    answers: dict[str, str] | None = None
    content: str
    created_at: datetime


class ReflectionCreate(_WriteModel):
    """写一条感想。`answers` 按四要素给，`content` 不填时由它拼出来。

    ★ 两者都允许：有人愿意分项填（那样"完整度"是可读的），
    有人只想写一段话。但**至少要有一个** —— 空的感想不写。
    """

    answers: dict[str, str] | None = None
    content: str | None = None


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
    "AnnotationCreate",
    "AnnotationRead",
    "ChatMessageCreate",
    "ChatMessageRead",
    "CheckpointDraftIn",
    "CheckpointRead",
    "CheckpointRecord",
    "DeletedAnnotationRead",
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
    "ReflectionCreate",
    "ReflectionGuideRead",
    "ReflectionRead",
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
