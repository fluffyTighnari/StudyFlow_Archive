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
    ClarificationStatus,
    ContextScope,
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
    #: 侧栏里的手动顺序（`Order by: Manual`）。建的时候取「当前最大 + 1」，
    #: 也就是新建的排在最后 —— 与所有文件管理器一致。
    #:
    #: 为什么不用 `created_at` 兼作顺序：用户拖过一次之后，顺序就不再等于创建顺序了。
    #: 两件事分开存，是"能被改的东西"与"事实"分开的最小代价。
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 归档（`归档`）—— **这不是会话状态**，是"还要不要出现在侧栏里"。
    #:
    #: 与 `session.state` 的区别是本表最容易被搞混的一处：
    #: `state` 回答"这条学习任务进行到哪了"（有状态机、有理由、有不变式盯着），
    #: `archived_at` 回答"它现在还要不要占着侧栏那一行"。
    #: 把归档塞进状态机会让 `TRANSITIONS` 多一条边，而那条边**不改变任何学习含义**。
    #: 用时间戳而不是布尔：`archived_at` 既能当开关，又能回答"什么时候收起来的"。
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
    #: 侧栏里的手动顺序，语义同 `Folder.sort_order`。
    #:
    #: ★ 这一列与 `version` **互不相干**：改名 / 归档 / 拖动排序都**不动 `version`**。
    #: 理由很实际 —— 用户正在对话时在侧栏把自己的会话改个名字，
    #: 不该让下一次内容写入撞上 409「版本不匹配」。`version` 护的是
    #: "同一条内容被两个人同时改"，而侧栏整理动作不是内容写。
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 归档 —— 同 `Folder.archived_at`，**不是**状态机的一个状态（见那里的长注释）。
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
    #: 前置基础：已经会的、缺的、以及卡在上一步的地方。
    #: 它不是"动机类型"，而是计划能不能落地的一手输入。
    foundation: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 价值：这件事对用户真正的用处。计划取舍靠它，不靠"内外混合"。
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
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
# 澄清问题（任务不明确时反问，P13）
# --------------------------------------------------------------------------- #


class Clarification(Base):
    """`clarification` = 助手问过的、关于**任务本身**的问题（P13）。

    ## 为什么它不是一条 `chat_message`

    因为它有一个**生命周期**，而对话消息没有：未答的问题**会让下一版计划带上「这是猜的」**（P16 起不再拒绝计划写入）
    （`clarify.plan_gate`）。挂在对话消息上会出两种错，而且都不会报错：

    - 消息被压缩 / 侧边栏被清空（`clear_sidebar`）之后，闸门自己解开了；
    - 同一条消息里既有问题又有别的闲聊，闸门只能靠"猜哪一句是问题"来判定。

    所以它是一行有状态的东西：`open → answered | dismissed`。

    ## 为什么记着 `asked_by`

    因为这条规矩**只约束助手**：用户自己改计划不走这道闸
    （解释权在用户手里）。要能区分"谁在问"，否则将来收紧闸门时
    会把用户自己也拦住 —— 而那种错的表现是"我的计划被一个我没答的问题锁住了"。
    """

    __tablename__ = "clarification"

    clarification_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: 定义区里的槽位名（`goal` / `starting_point` / `success_criteria`）。
    #: **必须是 `session_input` 真实存在的列** —— 答案要写进去，不是存着好看。
    slot: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    #: 候选答案（可空）。给"他会怎么答"一个形状：
    #: 自由文本的问题，答案质量全看用户那天的心情。
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[ClarificationStatus] = mapped_column(
        enum_col(ClarificationStatus, "ck_clarification_status"),
        nullable=False,
        default=ClarificationStatus.OPEN,
    )
    #: 用户的原话。**只存不改** —— 答案同时会写进定义区，
    #: 而这里留着的是"他当时是怎么说的"（定义区那一列可能被他后来改写）。
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 「先不管」也是一个决定，所以要留下理由 ——
    #: 它解释的是"为什么这条计划是按助手的判断排的"。
    dismiss_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    asked_by: Mapped[ChangedBy] = mapped_column(
        enum_col(ChangedBy, "ck_clarification_asked_by"),
        nullable=False,
        default=ChangedBy.AGENT,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # ★ 答了就必须有答案。没有这条约束，一个"答过但答案为空"的状态
        # 会让闸门放行（槽位被写过？）与不写（还是空的）之间反复横跳 ——
        # 而两种表现都是"计划建在了空槽位上"。
        CheckConstraint(
            "status <> 'answered' OR (answer IS NOT NULL AND length(trim(answer)) > 0)",
            name="ck_clarification_answered_has_text",
        ),
        # 搁置也要有理由：它是"这条计划为什么是猜的"的唯一解释。
        CheckConstraint(
            "status <> 'dismissed' OR "
            "(dismiss_reason IS NOT NULL AND length(trim(dismiss_reason)) > 0)",
            name="ck_clarification_dismissed_has_reason",
        ),
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
    #: **模型自己报的**这次调用的上下文（prompt）token 数（P6+.6）。
    #:
    #: ★ 为什么这个字段是整个记忆机制的基石：`num_ctx` 是窗口上限，而
    #: `prompt_eval_count` 是**实测**的占用 —— 有了它，「当前上下文用了多少」
    #: 就不需要估算。没有它就只能靠字符数猜，而猜错的方向恰好是最坏的：
    #: 猜小了会等到装不下才发现（那时模型给一个空回复，见 `OllamaClient`
    #: 里 `done_reason=length` 那段诊断）。
    #:
    #: 只写在 `role=assistant` 上；用户消息没有值 —— 它不是一个测量结果。
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 这一次生成的字数（`eval_count`）。与 `prompt_tokens` 一起构成用量读数。
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 这次请求里**命中**提示缓存的 prompt token 数（DeepSeek 的
    #: `usage.prompt_cache_hit_tokens`）。
    #:
    #: ★ `None` ≠ 0：没测到与"一次都没命中"是两件事。本地模型与测试替身
    #: 不报这两个数，界面因此显示「没测到」而不是「命中率 0%」——
    #: 后者会让用户以为缓存坏了（而它可能根本没被询问过）。
    cache_hit_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 未命中的 prompt token 数（`usage.prompt_cache_miss_tokens`）。同上。
    cache_miss_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 思考过程（`message.thinking`）。**与 `content` 分开存**，见下。
    #:
    #: 一次真实运行里模型生成了 93 个 token 而 `content` 是空串 ——
    #: 那 93 个全在 thinking 里。所以 thinking 既是"模型的思考过程"
    #: （用户明确要求能看到），也是"为什么这条回复是空的"的**唯一解释**。
    #: 合成一段文本会让两个问题都答不清楚。
    #:
    #: 它**不进上下文**（`agent.context` 装配时不带它）：留档是给人看的，
    #: 而把它喂回模型会让每轮上下文翻倍，还会让模型把自己的旧思路当事实。
    thinking: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 这条回复**当时锚定在哪一步**（P15）。
    #:
    #: 为什么要为"看推断理由"专门记一个锚点：用户问「你凭什么这么说」时，
    #: 系统要能重放**当时**装配出来的上下文节。而锚点过去没有落库 ——
    #: 事后只能按**今天**的规则（第一个未完成的步骤）挑一步，
    #: 挑出来的节标题会被读成"当时的证据"。一个字段换掉这个歧义。
    #:
    #: `NULL` = 这条消息写下时还没有这个字段（或侧边栏那种没有会话的路径）。
    #: 空着的处理方式与别处一致：**说出来**，而不是猜一个填上。
    anchor_step_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        # `retrieve_session` 的检索路径（M0 §6.4）：按会话时间序扫。
        Index("ix_chat_session_created", "session_id", "created_at"),
        CheckConstraint("length(trim(content)) > 0", name="ck_chat_content_present"),
    )


class ContextCompact(Base):
    """`context_compact` = 一次「把早期对话压成一段摘要」（P6+.6）。

    ## 为什么必须有这张表，而不是在 session 上加一个 `summary` 字段

    用户要求「上下文达到限制自动 compact」。而压缩是**有损**的：
    它把 N 条原话换成一段概括。于是有两个问题必须能回答：

    1. **它当时忘了什么？** —— 只留一个"当前摘要"字段的话，
       第二次压缩会覆盖第一次的摘要，而每一次覆盖都让一段对话永久消失。
       这张表只追加，所以「第 3 次压缩吃掉了 msg 12~31」永远查得到。
    2. **凭什么说这段摘要是可信的？** —— 摘要是**派生**文本，不是用户说的话。
       它因此**绝不能被当成证据来源**（"用户说过…"必须能指回原话）。
       把 `covers_msg_ids` 一并存下来，是为了让"这段话概括的是哪几条原话"
       随时可核对 —— 这是让派生文本不至于变成无源之言的唯一办法。

    ## 为什么 `scope` 是判别列而不是两张表

    session 与侧边栏用的是**同一套机制**（见 `ContextScope` 的说明）。
    拆成两张表就要写两遍读、两遍写、两遍测试，而它们的分歧会表现为
    "两个界面显示的上下文用量算法不一样" —— 那种不一致没人能解释。

    ## 为什么 `prompt_tokens_before` 记的是**实测值**

    它就是触发这次压缩的那次调用的 `prompt_eval_count`。记实测值而不是
    "压缩前估算"，是因为这个数将来要回答一个很实际的问题：
    「我调大了窗口，是不是就不用压这么频繁了」—— 那需要真实的历史读数。
    """

    __tablename__ = "context_compact"

    compact_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scope: Mapped[ContextScope] = mapped_column(
        enum_col(ContextScope, "ck_compact_scope"), nullable=False
    )
    #: `session_id` 或 `SIDEBAR_SCOPE_ID`。**刻意不加外键** ——
    #: 侧边栏不是会话，而"给一个判别列加两个可能的外键"在 SQLite 上做不到。
    #: 会话被删时留下的孤儿压缩记录由 `cleanup` 类逻辑处理（当前会话从不删除）。
    scope_id: Mapped[str] = mapped_column(String(32), nullable=False)
    #: 被这次压缩**吃掉的**消息 id，按原顺序。
    covers_msg_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    #: 被这次压缩**折进去的旧摘要** id，按原顺序。
    #:
    #: ★ 为什么需要它：摘要自己也会累积。若只压缩原文，那么压过 10 次之后
    #: 上下文里躺着 10 段摘要，而它们加起来可能又超过了窗口 ——
    #: 此时"没有原文可压了"，机制会卡死在"装不下又压不动"。
    #: 折叠让摘要层自己也能被压缩，于是机制在任何窗口尺寸下都收得住。
    #:
    #: 折叠**不删除**旧行（本表只追加）：`CX-0002` 折了 `CX-0001` 这件事
    #: 本身要看得到 —— 否则「这段记忆是怎么一步步变短的」无法回答。
    folds_compact_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    #: 触发这次压缩的那次调用**实测**的 prompt token 数。
    prompt_tokens_before: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 当时的窗口上限。它可能变了（用户改了 `num_ctx`），所以必须记当时的值 ——
    #: 否则"用量 14000"在一台窗口是 8192 的机器上看起来毫无问题。
    limit_at_time: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 哪一次生成做的压缩。空串 = 配置里没有模型名（本地默认）。
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        # 读路径永远是"某个 scope 的全部压缩，按时间"。这是那一条。
        Index("ix_compact_scope_created", "scope", "scope_id", "created_at"),
        CheckConstraint("length(trim(summary)) > 0", name="ck_compact_summary_present"),
        CheckConstraint("prompt_tokens_before >= 0", name="ck_compact_tokens_nonneg"),
        CheckConstraint("limit_at_time > 0", name="ck_compact_limit_positive"),
    )


class SidebarMessage(Base):
    """`sidebar_message` = 侧边栏那个**常驻通用模型**的对话（P6+.6）。

    ## 为什么是单独一张表，而不是 `chat_message` 加一列

    `chat_message.session_id` 有一条**真外键**指向 `session`。侧边栏不是会话
    （它没有任务、没有计划、没有状态机、不需要复盘），所以往
    `chat_message` 里塞一个 `session_id="sidebar"` 会立刻撞上外键 ——
    而"为了塞进去而把外键删掉"是拿一条真实的数据完整性
    去换一点代码复用，方向反了。

    ## 为什么复用**压缩表**却另建**消息表**

    这两件事的判据不同：

    - 压缩算法与压缩记录**必须共用**（`context_compact` 上的 `scope` 判别列）。
      拆成两套就会出现"两个界面显示的上下文用量算法不一样" ——
      那种不一致没人能解释，而它恰好是用户最在意的那块界面。
    - 消息存储**不该共用**，因为两者的**归属约束不同**：会话消息属于一个
      有状态机的会话，侧边栏消息不属于任何东西。硬要合成一张表，就得
      把"属于会话"这件事从外键降级成一个可为空的列 ——
      于是"这条消息属于哪个会话"从数据库保证变成了代码自觉。

    一句话：**算法共用，归属各管**。

    ## 为什么它没有状态机

    会话受 `SessionState` 管（写完总结就不能再聊）。侧边栏**没有终态** ——
    它永远可以聊，这正是"常驻"的含义。给它加一个状态机只会多出
    一个"什么时候算结束"的假问题。
    """

    __tablename__ = "sidebar_message"

    msg_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    role: Mapped[ChatRole] = mapped_column(
        enum_col(ChatRole, "ck_sidebar_message_role"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    #: 与 `chat_message` 逐字相同的四个字段，含义也相同 —— 见那里的说明。
    tool_trace: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    thinking: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        Index("ix_sidebar_message_created", "created_at"),
        CheckConstraint("length(trim(content)) > 0", name="ck_sidebar_message_content"),
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


class SessionAnnotation(Base):
    """`annotation` = 用户在某一条助手回复上**标注一句话**并问「为什么这么说」（P15）。

    ## 它回答的是什么问题

    用户看到助手写的一句「你更适合先把概念过一遍再做题」，想追问：
    **凭什么这么说？** 而这个问题有两种回答方式，只有一种是对的：

    - ✗ 让模型再生成一段听起来合理的解释 —— 那是**新编的理由**，
      与它当时真实的依据可能毫无关系，而用户分不出来；
    - ✓ 把**当时真的发生过的东西**摊开：这条回复背后的工具轨迹
      （读了哪几条、被拒绝过什么）、从里面能抽出的条目 id、
      以及**现在重放**的上下文节（并标明这是重放的，不是当时的快照）。

    这张表存的是**用户的那个问题**（哪一句、问什么），
    而"推断理由"是**现算**的（`session_domain/basis.py`）——
    理由不是一句话，是一组可核对的事实，存下来只会过期。

    ## `quote` 必须能在那条消息里**逐字找到**

    这是这张表唯一的硬约束，也是它区别于"评论区"的地方：
    标注的意义是"这句话是谁说的、在哪条消息里"，
    所以引文核对走的是与 `basis_quote`（M8 §4.6.2）**同一套**逻辑。
    对不上的引用一律拒收 —— 否则界面会出现一条指向不存在内容的标注。
    """

    __tablename__ = "annotation"

    annotation_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: 被标注的那条消息。**只能是助手说的**（见 `writes.annotate_message`）。
    msg_id: Mapped[str] = mapped_column(
        ForeignKey("chat_message.msg_id", ondelete="CASCADE"), nullable=False
    )
    #: 用户选中的那一句（原文片段，逐字）。
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    #: 用户想问什么。可空 —— 「就问问为什么」是一个完整的意图。
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        CheckConstraint("length(trim(quote)) > 0", name="ck_annotation_quote_present"),
        Index("ix_annotation_message", "msg_id"),
    )


class SessionReflection(Base):
    """`reflection` = 复盘之后按指引写下的感想（P15）。

    ## 为什么它单独一张表，而不是塞进 `note`

    笔记与感想在这里**长度、形状、用途**都不同：

    - 一条笔记是"随手记下的一件事"，`tags` 可空、`content` 一段文字；
    - 一条感想是**按四要素组织**的产物，它要能被逐项读出
      （哪一项是"原因"、哪一项是"下次改变"），因为 `V-PAR` 的
      `measured` 通道声明的正是「复盘产物的四要素完整度」。
      把四段话拼成一坨存进 `note.content`，那个"完整度"就只能靠再解析一遍 ——
      而解析一段自然语言来回答"他写没写原因"，是另一种猜。

    ## 为什么 `answers` 存 JSON 而不是四列

    四要素是**测量设计**里的东西（`_题库采购清单` §7），它有可能会变
    （多一项、少一项、换个说法）。四个列意味着每次调整都要一次迁移，
    而 JSON 里的键可以让新旧两种感想共存：老的没有 `why` 就是没有。
    `content` 仍然是**拼好的正文**，因为那是给人看、也会进上下文的那一份。

    ## 硬约束

    - `answers` 至少要有一项非空，或 `content` 非空（库层 CHECK 管不到 JSON，
      所以这条由 `writes.write_reflection` 把关）—— 空的感想不写；
    - `summary_id` 指向写它时对照的那一份复盘总结：**感想是对复盘的补充**，
      "他在哪份总结之后写的"是这句话唯一的时间锚。
    """

    __tablename__ = "reflection"

    reflection_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.session_id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: 写这条感想时对照的那份复盘总结。**可空**：总结可能被删（未来若允许）。
    summary_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: 四要素的答案 `{what_happened, expected_gap, why, next_change}`。
    answers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: 拼好的正文（给人看、也可能进上下文的那一份）。
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        CheckConstraint("length(trim(content)) > 0", name="ck_reflection_content_present"),
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
