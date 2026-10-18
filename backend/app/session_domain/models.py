"""Session 域 ORM 模型（M0 §6.2，M7 §2）—— **沙箱域，16 张表**。

与 `global_domain.models` 的根本区别：

| | 全局域 | Session 域 |
|---|---|---|
| 写入路径 | **只有 Router**（令牌 + 判定表 + 日志） | 本域自己的写函数 |
| 审计 | `router_log` 是唯一审计源 | 三个 `*_revision` append-only 表 |
| 权限 | 权威数据，错一行就是错的 | 沙箱，错了改回来就行 |

**"沙箱"不等于"没有约束"**：本域有三条硬约束，都在 `guards.py` 里以数据形式表达 ——
状态机（什么状态下能写什么）、乐观锁（`version` 不匹配就 409）、
以及「修订必须留痕」。

`I1` 要求**每张表都声明可追溯机制**（`unclassified_tables()`），因此本域 16 张表
在 `global_domain.invariants.PROVENANCE` 里逐张表了态 —— 一加表就变红，
这是刻意的：沙箱域也必须回答「改了这个东西之后，还看得出是谁改的吗」。

三个与文档不同、但必须说明的取舍：

1. **`plan_step` / `plan_checkpoint` 按 `version_no` 各存一份**。
   计划结构与"当时的进度"是两件事：改计划会产生新版本（旧版本的步骤与它们的状态
   原地冻结，所以「当时为什么那样规划」看得见），而在**当前版本**内标记
   `pending → done` 是就地更新、**不产生新版本** —— 否则「标记一步做完」
   会伪造一次计划修订，`plan_revision` 立刻失去意义。
2. **`stage` 的乐观锁版本挂在 `session.version` 上**。M0 §6.2 要求 stage 带版本，
   但 stage 没有独立的头表（它是 `stage_item` 的集合）。stage 与状态机同属
   「会话的工作区」，共用一个版本号；代价是改状态机与改 stage 会互相撞 409，
   而这在实践中不会同时发生。
3. **`state_snapshot` 没有 revision**（M7 §4.1 明确可覆盖）。
   它是「当前精力/压力/环境」，不是事实声明；但它**永不进 M1**（M1 §5.5），
   这条由一个测试钉住：这里的任何字段名都不得出现在 `m1_variable` 里。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.sqltypes import enum_col
from app.db import Base
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


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# 文件夹与会话（1:1 的头部）
# --------------------------------------------------------------------------- #


class Folder(Base):
    """`folder` = 长期领域（「英语学习」）。M7 §1。

    `c_retrieval_dims` 是 C 证据的**检索维度**（M7 §2 Folder 那一行）：
    同领域的历史证据才相关，跨领域套用是「别人有效所以我有效」的典型错误。
    """

    __tablename__ = "folder"

    folder_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 例如 `["task_type", "material_kind"]`；空表示「不分维度」。
    c_retrieval_dims: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class Session(Base):
    """`session` = 一个主题（「背单词」）。**乐观锁覆盖 session 行与 stage 条目**。

    `version` 的行为：本表的任何写入（含状态转移）与任何 `stage_item` 的写入
    都会 +1。客户端每次写都要带上它读到的值，不匹配 → 409（缺口 C5）。
    """

    __tablename__ = "session"

    session_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    folder_id: Mapped[str] = mapped_column(
        ForeignKey("folder.folder_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[SessionState] = mapped_column(
        enum_col(SessionState, "ck_session_state"),
        nullable=False,
        default=SessionState.DRAFT,
    )
    #: 乐观锁（含 stage）。初值 1，任何写入 +1。
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    abandoned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_session_version_positive"),
    )


# --------------------------------------------------------------------------- #
# 任务定义区（M7 §3）
# --------------------------------------------------------------------------- #


class SessionInput(Base):
    """`session_input` = 任务定义区。**可手动修改，每次改动写 `input_revision`**。"""

    __tablename__ = "session_input"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), primary_key=True
    )
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    why: Mapped[str | None] = mapped_column(Text, nullable=True)
    starting_point: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: `理解概念|掌握技能|完成项目|解决问题`（M7 §3）。刻意用 Text 而不是枚举：
    #: M7 给的是示例集合，加一类任务类型不该需要一次 schema 迁移。
    task_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    constraints: Mapped[str | None] = mapped_column(Text, nullable=True)
    success_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 用户原始话。**只提取不编造**（M7 §3）：它是回查"我当初是怎么说的"的唯一入口。
    intent_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent_confidence: Mapped[InputConfidence] = mapped_column(
        enum_col(InputConfidence, "ck_session_input_intent_confidence"),
        nullable=False,
        default=InputConfidence.MISSING,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        CheckConstraint(
            "priority IS NULL OR (priority >= 0 AND priority <= 10)",
            name="ck_session_input_priority_range",
        ),
        CheckConstraint("version >= 1", name="ck_session_input_version_positive"),
    )


class InputRevision(Base):
    """任务定义区的改动史（append-only）。**旧值一定留着** —— 那是"我改过主意"的证据。"""

    __tablename__ = "input_revision"

    rev_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), nullable=False, index=True
    )
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[ChangedBy] = mapped_column(
        enum_col(ChangedBy, "ck_input_rev_changed_by"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


# --------------------------------------------------------------------------- #
# 动机（M7 §4）
# --------------------------------------------------------------------------- #


class SessionMotivation(Base):
    """`session_motivation` = 动机持久状态。**可随时更新**，每次改动留 revision。"""

    __tablename__ = "session_motivation"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), primary_key=True
    )
    type: Mapped[MotivationType] = mapped_column(
        enum_col(MotivationType, "ck_motivation_type"),
        nullable=False,
        default=MotivationType.MIXED,
    )
    strength: Mapped[Level] = mapped_column(
        enum_col(Level, "ck_motivation_strength"),
        nullable=False,
        default=Level.MEDIUM,
    )
    #: 引原文的动机表述（M7 §4：`drivers` 要求引用原文，不是概括）。
    drivers: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    #: `[{type, desc, likelihood, evidence}]`
    obstacles: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class MotivationRevision(Base):
    """动机改动史（append-only）。`basis` 说明这次改动凭什么 —— 用户说的还是推断的。"""

    __tablename__ = "motivation_revision"

    rev_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), nullable=False, index=True
    )
    change: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    basis: Mapped[Basis] = mapped_column(
        enum_col(Basis, "ck_motivation_rev_basis"), nullable=False
    )
    changed_by: Mapped[ChangedBy] = mapped_column(
        enum_col(ChangedBy, "ck_motivation_rev_changed_by"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


# --------------------------------------------------------------------------- #
# 计划（M7 §5）
# --------------------------------------------------------------------------- #


class TaskPlan(Base):
    """`task_plan` = 持久化计划对象，一个 session 一个（版本化在 steps/checkpoints 里）。"""

    __tablename__ = "task_plan"

    plan_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    goal_derived: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 当前生效的计划版本号（从 1 开始）。历史版本的行仍在，按 `version_no` 取。
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: 乐观锁：改计划（含标步骤状态）都要带它。
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        CheckConstraint("current_version >= 1", name="ck_plan_current_version"),
        CheckConstraint("version >= 1", name="ck_plan_version_positive"),
    )


class PlanRevision(Base):
    """计划修订史（append-only）。`reason` **必填** —— M7 §5/§9 第 3 条：

    「调整必须写 reason；无理由调整 Agent 提示『先跑一步再改』」。
    这条约束落在数据库列上（`nullable=False`），而不是只写在提示词里。
    """

    __tablename__ = "plan_revision"

    rev_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("task_plan.plan_id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_version: Mapped[int] = mapped_column(Integer, nullable=False)
    to_version: Mapped[int] = mapped_column(Integer, nullable=False)
    change: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[Basis | None] = mapped_column(
        enum_col(Basis, "ck_plan_rev_basis"), nullable=True
    )
    changed_by: Mapped[ChangedBy] = mapped_column(
        enum_col(ChangedBy, "ck_plan_rev_changed_by"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        CheckConstraint("length(trim(reason)) > 0", name="ck_plan_rev_reason_present"),
    )


class PlanStep(Base):
    """计划步骤。**结构按版本存，进度就地改**（见模块文档取舍 ①）。"""

    __tablename__ = "plan_step"

    row_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("task_plan.plan_id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 跨版本稳定的步骤标识（`ST-001`）。UI 用它指认「哪一步」，不暴露 `version_no`。
    step_id: Mapped[str] = mapped_column(String(32), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    #: M2-A3/HC3：关键步骤必须带 output（「学完了」不是一个可交付物）。
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 引用 M2-B 策略库的 `strategy_id`。
    method_ref: Mapped[str | None] = mapped_column(String(48), nullable=True)
    status: Mapped[PlanStepStatus] = mapped_column(
        enum_col(PlanStepStatus, "ck_plan_step_status"),
        nullable=False,
        default=PlanStepStatus.PENDING,
    )

    __table_args__ = (
        UniqueConstraint(
            "plan_id", "version_no", "step_id", name="uq_plan_step_version_step"
        ),
        CheckConstraint("version_no >= 1", name="ck_plan_step_version"),
        CheckConstraint("seq >= 1", name="ck_plan_step_seq"),
    )


class PlanCheckpoint(Base):
    """计划检查点（M2-A4）。**检查点 ≠ 里程碑**（M8 §4.5）：本表验证某个关键步骤，
    `milestone` 表是会话级目标；两者结构不同，不可互相代替。"""

    __tablename__ = "plan_checkpoint"

    row_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("task_plan.plan_id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    #: M0 §11-A7 统一：`cp_id`（不是 `checkpoint_id`）。
    cp_id: Mapped[str] = mapped_column(String(32), nullable=False)
    at_step: Mapped[str] = mapped_column(String(32), nullable=False)
    check_method: Mapped[str] = mapped_column(Text, nullable=False)
    pass_criteria: Mapped[str] = mapped_column(Text, nullable=False)
    #: 失败时 UI 呈现这个动作，并建议触发 `propose_plan_change`（M0 §11-A2）。
    fail_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[CheckpointResult] = mapped_column(
        enum_col(CheckpointResult, "ck_plan_cp_result"),
        nullable=False,
        default=CheckpointResult.PENDING,
    )
    result_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "plan_id", "version_no", "cp_id", name="uq_plan_cp_version_cp"
        ),
        CheckConstraint("version_no >= 1", name="ck_plan_cp_version"),
    )


# --------------------------------------------------------------------------- #
# 会话内的记录（聊天 / 笔记 / stage / 里程碑 / 总结 / 状态快照）
# --------------------------------------------------------------------------- #


class ChatMessage(Base):
    """`chat_message` = 对话记录（M7 §1）。

    P4 只写 `role=user`；P6 起 LLM 的回复也写进来（`role=assistant`）。
    """

    __tablename__ = "chat_message"

    msg_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[ChatRole] = mapped_column(
        enum_col(ChatRole, "ck_chat_role"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    #: **这条回复背后，系统实际执行过的工具调用**（P6 新增）。
    #:
    #: 形状：`[{"step","call_id","tool","scope","arguments","status","message",
    #: "duration_ms"}, ...]`，见 `agent.dispatch.ToolTraceEntry`。
    #:
    #: 为什么落成 JSON 列而不是一张 `tool_call` 表：
    #: 轨迹**只对读它的那一刻有意义**（"它刚才做了什么"），
    #: 没有任何查询需要跨会话按工具名聚合；而多一张表就多一处
    #: 需要在归档、清理、导出时被想起的地方。
    #: 表数量保持 29 是一个副作用，真正的原因是**它没有独立的生命周期**：
    #: 消息没了，轨迹就没有意义。
    #:
    #: 为空的两种情况不同：`NULL` = 这一轮没调过工具（普通聊天）；
    #: 非空 = 调过，逐条都在。**不做"只记成功的"** ——
    #: 轨迹要回答的是"它做过什么、被拒绝过什么"，只记成功会变成一份成绩单。
    tool_trace: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        # `retrieve_session` 的检索路径（M0 §6.4）：按会话时间序扫。
        Index("ix_chat_session_created", "session_id", "created_at"),
        CheckConstraint("length(trim(content)) > 0", name="ck_chat_content_present"),
    )


class ChatAttachment(Base):
    """`chat_attachment` = 用户上传给这个会话的文本资料（P6+.5）。

    ## 为什么正文在这里，而不在 `chat_message.content` 里

    这是实测出来的：`discuss.py` 的 `_history()` 每次把**最近 6 条消息的原话**
    带进上下文。如果附件正文写进那条用户消息，它会**跟着后面两三轮反复回流**，
    把上下文挤掉 —— 而现象是"聊了两句之后模型开始忘事"，看不出跟附件有关。

    所以消息里只留一句标记，正文落在这里；模型要看全文就调 `read_attachment`。

    ## 为什么原件路径与正文都要存

    - **正文入库**：上下文可审计、可重建（"它当时到底看到了什么"），
      导出/备份也不依赖磁盘上那些文件；
    - **原件落盘**（`var/uploads/`，git 已忽略）：解析器以后改进了可以重解析，
      而一份被解析坏了的原件如果随手删了，就再也回不来。

    真相在库里那一行；磁盘上的文件是**副本**。

    ## 边界

    它是**沙箱**记录：不参与上下文采用序以外的任何治理，
    **上传本身绝不写全局认知**。经历要变成基线/偏好/证据，
    只能由模型在讨论里提 `submit_global_update`（走审核队列），
    或者用户在沉淀那一步自己选。
    """

    __tablename__ = "chat_attachment"

    attachment_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: 第一次把它带进对话的那条消息。`SET NULL` 而不是 `CASCADE`：
    #: 消息被清理掉不该连资料一起消失（资料是用户的东西，不是那条消息的附属）。
    msg_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_message.msg_id", ondelete="SET NULL"), nullable=True
    )
    #: 原始文件名（已清洗，仅用于显示与下载命名）。**不是身份**。
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 小写扩展名，含点。
    ext: Mapped[str] = mapped_column(String(12), nullable=False)
    #: 解析器种类：`plain` / `docx`。
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    bytes_len: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 提取出的正文字符数（`text` 的实际长度）。
    chars_len: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 内容哈希。用途只有一个：同一份文件传两次时认出来，
    #: 不让会话资料里出现两份一模一样的清单项。
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 原件相对 `uploads_dir()` 的路径，形如 `S-0001/AT-0001.md`。
    stored_path: Mapped[str] = mapped_column(String(300), nullable=False)
    #: 提取出的正文。可能已被截断（见 `truncated`）。
    text: Mapped[str] = mapped_column(Text, nullable=False)
    #: 是否因为超过 `attachments.MAX_CHARS` 被截断。
    #: **必须让模型知道**：不然它会以为"文件就这么多"，然后基于一个
    #: 不完整的材料下结论 —— 这与"prompt 装不下"是同一类失败。
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: 解析取舍说明（编码、表格拍平、不取了什么）。跟着附件进上下文与界面。
    note: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        CheckConstraint("length(trim(text)) > 0", name="ck_attachment_text_present"),
        CheckConstraint("bytes_len > 0", name="ck_attachment_bytes_positive"),
    )


class Note(Base):
    """会话笔记。**`COMPLETED` 之后仍然可写** —— 这是用户裁定给终态留的唯一例外口子：

    「状态机的意义在于它拒绝什么」，而做完一个会话之后想补一句笔记，
    是真实需求，不是绕过治理。
    """

    __tablename__ = "note"

    note_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("length(trim(content)) > 0", name="ck_note_content_present"),
    )


class StageItem(Base):
    """`stage_item` = 暂存区条目（M7 §7.1）。**不进全局**，也不经路由。

    `status` 的四个取值构成一条单向路：`active → deposited | discarded | archived`。
    没有 `DELETED`（用户裁定，见 `enums.StageItemStatus` 的文档）。
    """

    __tablename__ = "stage_item"

    item_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[StageItemType] = mapped_column(
        enum_col(StageItemType, "ck_stage_item_type"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[StageItemStatus] = mapped_column(
        enum_col(StageItemStatus, "ck_stage_item_status"),
        nullable=False,
        default=StageItemStatus.ACTIVE,
    )
    #: 沉淀成功后指向那次全局写入的提案（`UP-0001`），否则为空。
    result_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("length(trim(content)) > 0", name="ck_stage_content_present"),
        Index("ix_stage_session_status", "session_id", "status"),
    )


class Milestone(Base):
    """会话级里程碑。**与 `plan_checkpoint` 是两件事**（M8 §4.5）。

    「完成」不自动沉淀 —— `checkpoint_milestone` 工具只提示（M8 §4.3）。
    """

    __tablename__ = "milestone"

    milestone_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[MilestoneStatus] = mapped_column(
        enum_col(MilestoneStatus, "ck_milestone_status"),
        nullable=False,
        default=MilestoneStatus.PENDING,
    )
    results: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    done_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SessionSummary(Base):
    """完成总结（M7 §2）。**这是 R4a 的触发点**，但总结本身不写全局。"""

    __tablename__ = "session_summary"

    summary_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: 逐条发现 `[{statement, basis, target_field?, channel?, score?}]` ——
    #: 沉淀通路的原料（`sedimentation.py` 从这里取），**不是**全局写入本身。
    findings: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        CheckConstraint(
            "length(trim(content)) > 0", name="ck_summary_content_present"
        ),
    )


class StateSnapshot(Base):
    """动态状态快照（M1 §5.5 / M7 §4.1）。**可覆盖、无 revision、永不进 M1**。

    `environment` 是自由文本而不是枚举：M7 §4.1 的取值集合以「其他」结尾，
    意味着它是开放的。把开放集合钉成枚举，只会在第一次遇到「咖啡厅」时被迫改 schema。
    """

    __tablename__ = "state_snapshot"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), primary_key=True
    )
    energy: Mapped[Level | None] = mapped_column(
        enum_col(Level, "ck_snapshot_energy"), nullable=True
    )
    stress: Mapped[Level | None] = mapped_column(
        enum_col(Level, "ck_snapshot_stress"), nullable=True
    )
    environment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


__all__ = [
    "ChatAttachment",
    "ChatMessage",
    "Folder",
    "InputRevision",
    "Milestone",
    "MotivationRevision",
    "Note",
    "PlanCheckpoint",
    "PlanRevision",
    "PlanStep",
    "Session",
    "SessionInput",
    "SessionMotivation",
    "SessionSummary",
    "StageItem",
    "StateSnapshot",
    "TaskPlan",
]
