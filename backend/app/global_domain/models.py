"""全局认知域 ORM 模型（M0 §6.1）—— **权威数据，写入仅经 Router**。

本模块可被任何人 import（读路径需要模型）；**写函数**在 `writes.py`，
由架构规则 ARCH-01 限制为只有 `app.router.*` 可引用。

设计要点（M0 §6.5，证据通道纯度的结构保证）：
- `m1_baseline_version` 的**唯一约束含 channel** —— 数据库里根本不存在
  「一个变量一个分数」这一实体，因此「取三通道均值」在结构上无从下手。
- 部分唯一索引保证每个 (variable_id, channel) 至多一条「当前版本」
  （`superseded_by IS NULL`），历史版本永久保留。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.core.sqltypes import enum_col
from app.global_domain.enums import (
    Basis,
    BaselineConfidence,
    BenchmarkForm,
    CEvidenceSource,
    CEvidenceStrength,
    ChannelObsSourceKind,
    Completeness,
    ConstructType,
    EvidenceChannel,
    IntentAction,
    IntentStatus,
    JudgedBy,
    LifecycleStatus,
    LLMConfidence,
    Precheck,
    ProposalSource,
    ProposalStatus,
    ResponseQuestionKind,
    RouterLogStatus,
    StrategyStrength,
    TriggerType,
)


def _now() -> datetime:
    return datetime.now(UTC)


# `enum_col` 已移到 `app/core/sqltypes.py`（P4）：`session_domain.models` 也需要它，
# 而 ARCH-08 禁止沙箱域 import 本模块。**保留这行 import 是为了不改变对外形状**
# （本模块的 `__all__` 里仍有 `enum_col`，测试与工具照旧可以 `from ...models import enum_col`）——
# 但实现只有一份，见那边的模块文档。


# --------------------------------------------------------------------------- #
# M1 认知基准
# --------------------------------------------------------------------------- #


class M1Variable(Base):
    """M1 §1 变量定义（静态）。11 行种子数据。

    注意：这里**不存分数**。分数在 `m1_baseline_version`，且按通道分行。
    """

    __tablename__ = "m1_variable"

    #: M1 §2 用 `V-SE` 简称；路由层 `target` 记为 `M1.V-SE`（M0 §11 A9 归一）。
    variable_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    construct_type: Mapped[ConstructType] = mapped_column(
        enum_col(ConstructType, "ck_m1_variable_construct_type"), nullable=False
    )
    definition_what: Mapped[str] = mapped_column(Text, nullable=False)
    definition_what_not: Mapped[str] = mapped_column(Text, nullable=False)
    scale_low_anchor: Mapped[str] = mapped_column(Text, nullable=False)
    scale_high_anchor: Mapped[str] = mapped_column(Text, nullable=False)

    #: M1 §1 的 `measurement` 整块：goal / methods[] / evidence_channels /
    #: update_rule / update_frequency。
    measurement_def: Mapped[dict] = mapped_column(JSON, nullable=False)

    #: M1 §4 干预钩子（供计划生成器）。
    intervention_hooks: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    #: M1 §5.4：构念配对只能显式声明；当前无有效配对。
    pairing: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    versions: Mapped[list["M1BaselineVersion"]] = relationship(
        back_populates="variable", lazy="selectin"
    )
    channels: Mapped[list["M1VariableChannel"]] = relationship(
        back_populates="variable", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<M1Variable {self.variable_id} {self.display_name}>"


class M1VariableChannel(Base):
    """M1 §1 `evidence_channels` 的**逐通道落库**（M0 §6.6 / 缺口 A10）。

    为什么必须分通道，而不是在 `m1_variable` 上放一个 `measurement_completeness`：
    完整度天然是 `(变量, 通道)` 的属性——V-SE 完全可能 measured 已做完、stated 还没做。
    在变量级存一个值只有两种结局：丢失这个差异，或与逐通道的版本行形成
    **第二处真相**并静默漂移。

    本表只存**设计态**（静态目录数据，seeds / 迁移写，运行时无人可写）：

    - `designed`：该通道**是否有**测量方法覆盖（V-CP 的 measured 通道按设计不设）；
    - `method_ids`：覆盖该通道的方法（M1 §1 `methods[]` 的子集）；
    - `design_note`：该通道的说明（不设时即为原因）。

    「该通道**当前执行到哪一级**」不在此表存——由 `reads.measurement_coverage()`
    从当前基线版本派生（`designed=false` → `NOT_APPLICABLE`；无版本 → `PARTIAL`）。
    """

    __tablename__ = "m1_variable_channel"

    variable_id: Mapped[str] = mapped_column(
        ForeignKey("m1_variable.variable_id", ondelete="RESTRICT"), primary_key=True
    )
    channel: Mapped[EvidenceChannel] = mapped_column(
        enum_col(EvidenceChannel, "ck_m1_var_channel_channel"), primary_key=True
    )
    designed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    method_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    design_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    variable: Mapped[M1Variable] = relationship(back_populates="channels")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<M1VariableChannel {self.variable_id}/{self.channel}"
            f" designed={self.designed}>"
        )


class M1BaselineVersion(Base):
    """版本化的 M1 基线 —— **append-only，永不 UPDATE 历史行**。

    「当前生效版本」由 `m1_baseline_current` 指针表决定，**不在本表上做
    `superseded_by IS NULL` 的部分唯一索引**。

    ⚠️ 这是实现期发现的设计修正（记录于 M0 §6.5）：
    用 `superseded_by IS NULL` 表示「当前」时，替换当前版本必须「先取消旧标记、
    再插入新行」；而 `superseded_by` 若带外键，就要求被指向的新行已存在——
    两约束互斥，SQLite 又不支持可延迟外键。改用指针表后：
    1. 唯一性由 `m1_baseline_current` 的 `(variable_id, channel)` 主键**结构性**保证；
    2. 指针外键始终合法（新版本行先插入，再改指针）；
    3. `replaces_version_id` 记录版本链，指向**已存在**的旧行，无顺序问题；
    4. 历史永久完整，回滚只需再插一行并改指针。
    """

    __tablename__ = "m1_baseline_version"
    __table_args__ = (
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 10)",
            name="ck_m1_baseline_score_range",
        ),
        Index("ix_m1_baseline_variable_channel", "variable_id", "channel"),
    )

    #: M1 §3.3 的 log_id 格式：`M1-SE-001`，按变量各自计数。
    version_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    variable_id: Mapped[str] = mapped_column(
        ForeignKey("m1_variable.variable_id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[EvidenceChannel] = mapped_column(
        enum_col(EvidenceChannel, "ck_m1_baseline_channel"), nullable=False
    )

    #: 0-10；证据不足时为 NULL（Benchmark §6：不强行给分）。
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[BaselineConfidence | None] = mapped_column(
        enum_col(BaselineConfidence, "ck_m1_baseline_confidence"), nullable=True
    )
    completeness: Mapped[Completeness] = mapped_column(
        enum_col(Completeness, "ck_m1_baseline_completeness"), nullable=False
    )

    #: Benchmark §6：本测验能支持什么、不能支持什么。
    boundary: Mapped[str | None] = mapped_column(Text, nullable=True)

    basis: Mapped[Basis] = mapped_column(
        enum_col(Basis, "ck_m1_baseline_basis"), nullable=False
    )
    #: 证据出处：benchmark_run_id / session_id / user_ui / rollback:<版本号>。
    source_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    router_log_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: 版本链：本行取代了哪一行（指向**已存在**的旧行）。
    replaces_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("m1_baseline_version.version_id", ondelete="RESTRICT"), nullable=True
    )

    variable: Mapped[M1Variable] = relationship(back_populates="versions")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<M1BaselineVersion {self.version_id} {self.variable_id}"
            f"/{self.channel}={self.score}>"
        )


class M1BaselineCurrent(Base):
    """当前生效基线指针 —— **每个 (变量, 通道) 至多一行**（主键结构性保证）。

    这张表就是「S3 证据通道纯度」的落点：数据库里根本不存在
    「一个变量一个分数」的实体，三通道各自指向各自的版本，
    因此「取三通道均值」在结构上无从下手（M0 §6.5）。
    """

    __tablename__ = "m1_baseline_current"

    variable_id: Mapped[str] = mapped_column(
        ForeignKey("m1_variable.variable_id", ondelete="RESTRICT"), primary_key=True
    )
    channel: Mapped[EvidenceChannel] = mapped_column(
        enum_col(EvidenceChannel, "ck_m1_baseline_current_channel"), primary_key=True
    )
    version_id: Mapped[str] = mapped_column(
        ForeignKey("m1_baseline_version.version_id", ondelete="RESTRICT"), nullable=False
    )
    switched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<M1BaselineCurrent {self.variable_id}/{self.channel} → {self.version_id}>"


class M1ChannelObs(Base):
    """单条通道观测 —— 增量证据（M1 §3.2 第 3 条：只更新对应通道）。

    例：M-SE-05 Session 观测的 extraction_efficiency 累积。
    """

    __tablename__ = "m1_channel_obs"

    obs_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    variable_id: Mapped[str] = mapped_column(
        ForeignKey("m1_variable.variable_id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[EvidenceChannel] = mapped_column(
        enum_col(EvidenceChannel, "ck_m1_obs_channel"), nullable=False
    )
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: session | benchmark | user_ui | external
    source_kind: Mapped[ChannelObsSourceKind] = mapped_column(
        enum_col(ChannelObsSourceKind, "ck_m1_obs_source_kind"), nullable=False
    )
    source_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        Index("ix_m1_obs_variable_channel", "variable_id", "channel", "observed_at"),
    )


# --------------------------------------------------------------------------- #
# Benchmark 基准测验（Benchmark v0.3 §7.3，用户裁定 G-03 = 甲）
# --------------------------------------------------------------------------- #


class BenchmarkRun(Base):
    """一次基准运行 —— **分数追回答案的那一端**。

    为什么需要这张表（裁决 G-03 的理由原文）：

    > 一个落库了的 0-10 分数**就是一句关于用户的断言**，
    > 而它背后那个理由（答案）如果不落库，那句话就无法被检查。

    在此之前 `m1_baseline_version.source_ref` 只是一个悬空字符串：
    它能让 §5.4 数出"重做了几次"，但**说不出那个分数是怎么来的**。

    三条设计口径：

    1. **`domains` 存的是快照，不只是领域名。**
       领域画像住在题库的元数据文件里（裁决 G-04 = 乙），
       所以"当时他熟不熟这个领域"这个事实**必须写进本次运行的记录**，
       否则半年后改一次文件就会**改写历史**（§8.3 ③）。
    2. **`anchor_baseline` 首次运行时写入、此后只读。**
       锚题校正（§5.6）的分母是"基准通过率"，它一旦漂移，
       全部历史分数的可比性同时失效。
    3. **`item_ids` + `seed` 必须能复现同一套题。**
       抽题带随机性，而"两次运行分数不同"必须能区分
       「人变了」与「题变了」—— 没有种子就区分不了。
    """

    __tablename__ = "benchmark_run"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    form: Mapped[BenchmarkForm] = mapped_column(
        enum_col(BenchmarkForm, "ck_bench_run_form"), nullable=False
    )
    variable_id: Mapped[str] = mapped_column(
        ForeignKey("m1_variable.variable_id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[EvidenceChannel] = mapped_column(
        enum_col(EvidenceChannel, "ck_bench_run_channel"), nullable=False
    )
    #: 覆盖本次测量的方法（如 `M-SE-04`）。缺了它就无法回答
    #: 「这个读数是由哪个**已声明的方法**产生的」—— 而那正是 §5.7 的核心问题。
    method_id: Mapped[str] = mapped_column(String(32), nullable=False)
    #: 抽中的题目，顺序即施测顺序。
    item_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    #: `[{item_id, domain, familiarity_at_run}]` —— 见类文档第 1 条。
    domains: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    #: `{item_id: 通过率}` —— 锚题基准（§5.6 的校正分母）。
    anchor_baseline: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: 五层量表链的中间与最终结果（§5.1）。存下来是为了让 run 自描述。
    method_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    channel_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[BaselineConfidence | None] = mapped_column(
        enum_col(BaselineConfidence, "ck_bench_run_confidence"), nullable=True
    )
    completeness: Mapped[Completeness] = mapped_column(
        enum_col(Completeness, "ck_bench_run_completeness"), nullable=False
    )
    #: §5.6 要求每份分数带的边界声明（"只用于自己与自己比"）。
    boundary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "channel_score IS NULL OR (channel_score >= 0 AND channel_score <= 10)",
            name="ck_bench_run_channel_score_range",
        ),
        CheckConstraint(
            "method_score IS NULL OR (method_score >= 0 AND method_score <= 10)",
            name="ck_bench_run_method_score_range",
        ),
        Index("ix_bench_run_variable_started", "variable_id", "started_at"),
    )


class BenchmarkResponse(Base):
    """一道题的原始作答与单题分。

    **一行 = 一道题**，不是一份材料：一份材料有 1 道要点题 + 5 道客观题，
    它们的计分规则不同（§5.2），单题分是逐题算出来的。
    `question_no = 0` 表示要点题。

    ★ `judged_by` + `judge_model` + `judge_note` 三件套是**审计要求**：
    用户裁定要点题由 LLM 判，而 LLM 判分正是本项目一贯要防的那种
    「系统用一个理由解释自己的行为」。所以它必须留下：
    谁判的、哪个模型判的、**它自己声明的依据原话**。
    后者要经**引文核对**（命中声明里引用的原文必须真的在 `raw_answer` 里），
    核对不过的命中降级为未命中 —— 判分因此变成可核对的。
    """

    __tablename__ = "benchmark_response"

    response_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("benchmark_run.run_id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[str] = mapped_column(String(32), nullable=False)
    #: 0 = 要点题；1..n = 客观题题号。
    question_no: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[ResponseQuestionKind] = mapped_column(
        enum_col(ResponseQuestionKind, "ck_bench_resp_kind"), nullable=False
    )
    #: 题目自带的人标难度（易/中/难）与该题在聚合里的权重（§5.3）。
    difficulty: Mapped[str | None] = mapped_column(String(8), nullable=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: 原始作答（用户敲的字 / 选择的选项文本）。
    raw_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 单题分 0-10；`NULL` = **这题没测到**（作废），
    #: 与"0 分"是两件事（§5.2：把两者混起来会让一次太容易的任务静默压低分数）。
    item_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    elapsed_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    judged_by: Mapped[JudgedBy] = mapped_column(
        enum_col(JudgedBy, "ck_bench_resp_judged_by"), nullable=False
    )
    #: LLM 判官所用模型（`judged_by=llm` 时必需，否则判分不可复现）。
    judge_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 判分依据：LLM 的原话 / 代码算出的中间量 / 人工标记 / 换算表档位。
    judge_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 判官声明的命中要点 + 每条所依据的原文引用（供引文核对）。
    judged_hits: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id", "item_id", "question_no", name="uq_bench_resp_run_item_q"
        ),
        CheckConstraint(
            "item_score IS NULL OR (item_score >= 0 AND item_score <= 10)",
            name="ck_bench_resp_item_score_range",
        ),
        Index("ix_bench_resp_run", "run_id"),
    )


# --------------------------------------------------------------------------- #
# M2 偏好字段 与 B 策略库
# --------------------------------------------------------------------------- #


class M2Preference(Base):
    """M2 偏好字段（带 priority）。

    **这一张表装两种性质相反的东西**，靠 `basis` 分开（缺口 A18，用户裁定）：

    - `basis=stated` —— **用户下达的约束**。用户自己敲的字，不可被 LLM 静默推翻
      （M2 红线 2：HC1–HC4 优先于任何软偏好）。判定只用
      `reads.is_hard_constraint()`，**不要在这里或消费侧重写这个比较**。
    - `basis=experienced|inferred` —— **软偏好 / 候选**。LLM 观察或推断出来的，
      可被任务情况推翻，但推翻必须留痕（M2 §2.2 的 `overridden_soft_prefs`）。

    `target_field`（形如 `"SP1.presentation"`）只决定这条偏好是否**覆盖某个 SP1 槽位**，
    **不决定硬度** —— 一条没有槽位的「我讨厌看网课，因为浪费时间」与一条有槽位的
    「呈现优先用例子」同样不可被推翻。

    **`priority` 对硬约束的含义**：不是「可推翻的难度」，而是
    **多条约束互相冲突时的顺序**（谁优先满足）。对软偏好它才是排序权重。
    一条约束不会因为 `priority` 低而变得"可以违反"。
    """

    __tablename__ = "m2_preference"
    __table_args__ = (
        CheckConstraint(
            "priority >= 0 AND priority <= 10", name="ck_m2_pref_priority_range"
        ),
        Index("ix_m2_pref_status", "status"),
    )

    pref_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    #: **理由内联在 statement 里**（缺口 A19，用户裁定：「不加 reason 字段」）。
    #: 例：「我讨厌看网课，因为浪费时间」—— 理由才是 LLM 能泛化的那一半
    #: （→「凡是要占用大段连续时间的活动他都排斥」）。缺了它，LLM 只看到一条
    #: 孤立禁令，下次很可能用「看 2 小时讲座」来满足「别看网课」。
    #: 因此写入方（`M2PreferenceValue.statement` / `PreferenceCreate.statement`）
    #: 的字段说明里必须带上这条规范 —— 规范要写在 LLM 真正读得到的地方。
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    #: 形如 "SP1.presentation" / "SP1.activity_mix"。**只表示装配槽位，不表示硬度。**
    target_field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 硬约束下 = 多条约束冲突时的顺序；软偏好下 = 排序权重。见类文档。
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    #: **硬度的唯一来源**：见 `enums.HARD_CONSTRAINT_BASIS`。
    basis: Mapped[Basis] = mapped_column(
        enum_col(Basis, "ck_m2_pref_basis"), nullable=False, default=Basis.STATED
    )
    status: Mapped[LifecycleStatus] = mapped_column(
        enum_col(LifecycleStatus, "ck_m2_pref_status"),
        nullable=False,
        default=LifecycleStatus.ACTIVE,
    )
    router_log_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: **这条偏好明确推翻了哪一条**（缺口 A20，用户裁定：给 `add_preference` 加这个可选指向）。
    #:
    #: 只对 `target_field` 为空的通用声明有意义 —— 有槽位时"谁覆盖谁"由槽位唯一决定
    #: （`m2_policy._find_same_field`），不需要也不应该再声明一次。
    #:
    #: **为什么落成列而不是只写日志**：「这条推翻了哪条」是这条偏好自己的属性。
    #: 回看基线（如讨论上下文包）时应当能直接看到，而不必去翻审计日志；
    #: 为空表示"这是一条新声明，不是对某条旧声明的修正"。
    #:
    #: **自引用但不加外键约束**：归档是改 `status`，行永不删除，所以不存在悬空引用；
    #: 而 SQLite 上的自引用外键会让批量重建表变复杂，收益不抵成本。
    #: 真正的一致性由不变式 **I7** 全局校验（不允许一条 active 偏好推翻另一条 active 偏好）。
    supersedes_pref_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class M2Strategy(Base):
    """M2 §3.1 B 策略候选库 —— **种子数据，无人可写**（M0 §3 权限矩阵）。

    B 是参考不是约束：LLM 说明理由但**不被强制采用任何方法**。
    """

    __tablename__ = "m2_strategy"

    strategy_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    solves: Mapped[str] = mapped_column(Text, nullable=False)
    when_to_use: Mapped[str] = mapped_column(Text, nullable=False)
    cost: Mapped[str] = mapped_column(String(32), nullable=False)
    adjust_signals: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_strength: Mapped[StrategyStrength] = mapped_column(
        enum_col(StrategyStrength, "ck_m2_strategy_strength"), nullable=False
    )
    default_weight: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    catalog_version: Mapped[str] = mapped_column(String(16), nullable=False, default="v0.3")


# --------------------------------------------------------------------------- #
# C 个性化证据
# --------------------------------------------------------------------------- #


class CEvidence(Base):
    """M2 §4 C 个性化证据 —— **必须带适用条件**（HC4）。

    纯偏好（无 condition）不允许注册为 C 条目 → 归 SP1（M2 §4.4 第 3 条）。
    该约束由 `ck_c_evidence_condition_required` 在库层强制。
    """

    __tablename__ = "c_evidence"
    __table_args__ = (
        CheckConstraint(
            "length(trim(condition)) > 0", name="ck_c_evidence_condition_required"
        ),
        Index("ix_c_evidence_status", "status"),
    )

    evidence_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    #: 带适用条件的完整陈述（条件 + 方法 + 效果）。
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    condition: Mapped[str] = mapped_column(Text, nullable=False)
    effect: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_strength: Mapped[CEvidenceStrength] = mapped_column(
        enum_col(CEvidenceStrength, "ck_c_evidence_strength"), nullable=False
    )
    source: Mapped[CEvidenceSource] = mapped_column(
        enum_col(CEvidenceSource, "ck_c_evidence_source"), nullable=False
    )
    status: Mapped[LifecycleStatus] = mapped_column(
        enum_col(LifecycleStatus, "ck_c_evidence_status"),
        nullable=False,
        default=LifecycleStatus.ACTIVE,
    )
    router_log_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    revisions: Mapped[list["CEvidenceRevision"]] = relationship(
        back_populates="evidence", lazy="selectin"
    )


class CEvidenceRevision(Base):
    """C 条目修订史（append-only）。用户直接维护 C 不经过 LLM，但仍留痕。"""

    __tablename__ = "c_evidence_revision"

    rev_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("c_evidence.evidence_id", ondelete="RESTRICT"), nullable=False
    )
    change: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    basis: Mapped[Basis | None] = mapped_column(
        enum_col(Basis, "ck_c_evidence_rev_basis"), nullable=True
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    router_log_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    evidence: Mapped[CEvidence] = relationship(back_populates="revisions")


# --------------------------------------------------------------------------- #
# 路由层：提案 / 意图 / 审计日志
# --------------------------------------------------------------------------- #


class UpdateProposal(Base):
    """持久化的更新提案（M0 §5）—— 文档只给了个 `review_id`，此处补齐为实体。

    必须是实体而非内存弹窗，因为：Session 生命周期很长，用户可能数小时后才回来确认；
    一个 Session 可能累积多条待确认提案；且必须能表达「部分应用」。
    """

    __tablename__ = "update_proposal"
    __table_args__ = (Index("ix_proposal_status", "status", "created_at"),)

    proposal_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    #: 来源 Session（R3/R4 有；R1/R2 为 NULL）。
    session_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[ProposalSource] = mapped_column(
        enum_col(ProposalSource, "ck_proposal_source"), nullable=False
    )
    trigger_type: Mapped[TriggerType] = mapped_column(
        enum_col(TriggerType, "ck_proposal_trigger"), nullable=False
    )
    status: Mapped[ProposalStatus] = mapped_column(
        enum_col(ProposalStatus, "ck_proposal_status"),
        nullable=False,
        default=ProposalStatus.PROPOSED,
    )
    #: 触发上下文（如「Session 完成总结」/「用户一键沉淀」）。
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 证据出处：`benchmark_run_id`（R2）/ `session_id`（R3/R4）。
    #:
    #: ⚠️ 必须存在（补缺口 A13）：提案落库发生在 ④，而写入发生在 ⑥/裁决之后。
    #: 若出处只存在于 `SubmitRequest` 里，等到 `resolve` 时就已经丢了 ——
    #: 于是 R2 写入的 `m1_baseline_version.source_ref` 为空，
    #: §5.4 的「按 run_id 去重重做次数」会永远算出 0，重做建议从此再不出现。
    source_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    intents: Mapped[list["UpdateIntentRow"]] = relationship(
        back_populates="proposal", lazy="selectin", cascade="save-update, merge"
    )


class UpdateIntentRow(Base):
    """UpdateIntent 的持久化形态（路由层 §4 / M0 §7.2）。

    `precheck` 列保存策略查表的确定性判定结果——LLM 的 `confidence` 与系统的
    `precheck` 是两个不同的东西，不可混淆。
    """

    __tablename__ = "update_intent"
    __table_args__ = (
        CheckConstraint(
            "priority IS NULL OR (priority >= 0 AND priority <= 10)",
            name="ck_intent_priority_range",
        ),
        Index("ix_intent_proposal", "proposal_id"),
    )

    intent_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("update_proposal.proposal_id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[IntentAction] = mapped_column(
        enum_col(IntentAction, "ck_intent_action"), nullable=False
    )
    #: 必须匹配 UpdatePolicyRegistry，如 `M1.V-SE` / `C.evidence` / `M2.preferences.SP1`。
    target: Mapped[str] = mapped_column(String(96), nullable=False)
    value: Mapped[dict | list | str | float | None] = mapped_column(JSON, nullable=True)
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    basis: Mapped[Basis] = mapped_column(
        enum_col(Basis, "ck_intent_basis"), nullable=False
    )
    #: 必填：LLM 评估理由，进日志（路由层 §4「LLM 评估必做」第 3 条）。
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[LLMConfidence] = mapped_column(
        enum_col(LLMConfidence, "ck_intent_confidence"), nullable=False
    )
    #: 只报告不裁定（路由层 §8 第 2 条）。
    conflict_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: 系统判定（M0 §5.3），与 LLM 的 confidence 无关。
    precheck: Mapped[Precheck | None] = mapped_column(
        enum_col(Precheck, "ck_intent_precheck"), nullable=True
    )
    precheck_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[IntentStatus] = mapped_column(
        enum_col(IntentStatus, "ck_intent_status"),
        nullable=False,
        default=IntentStatus.PENDING,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    proposal: Mapped[UpdateProposal] = relationship(back_populates="intents")


class RouterLog(Base):
    """统一路由日志（路由层 §7）—— **唯一审计源**。

    不变式 I1（M0 §4.4）：全局状态的每一次变更都必须对应 ≥1 条本表记录。
    """

    __tablename__ = "router_log"
    __table_args__ = (
        Index("ix_router_log_target", "target", "created_at"),
        Index("ix_router_log_created", "created_at"),
        Index("ix_router_log_trigger_created", "trigger", "created_at"),
    )

    log_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    #: 本次写入的触发类型（R1/R2/R3/R4a-c）。
    #: 刻意放在**日志**上而非只放提案上——`trigger` 是写入事件自身的成因：
    #: 一条提案可派生多条写入，而 R1 直写与 schema 拒绝日志根本没有提案。
    #: 若只能反查 `update_proposal.trigger_type`，这两类日志的成因会永久丢失，
    #: 且 M0 §5.4 的重做计数无从判定。可为空 = 「本条日志没有触发类型」。
    trigger: Mapped[TriggerType | None] = mapped_column(
        enum_col(TriggerType, "ck_router_log_trigger"), nullable=True
    )
    source: Mapped[ProposalSource] = mapped_column(
        enum_col(ProposalSource, "ck_router_log_source"), nullable=False
    )
    action: Mapped[IntentAction | None] = mapped_column(
        enum_col(IntentAction, "ck_router_log_action"), nullable=True
    )
    target: Mapped[str | None] = mapped_column(String(96), nullable=True)
    old_value: Mapped[dict | list | str | float | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict | list | str | float | None] = mapped_column(JSON, nullable=True)
    basis: Mapped[Basis | None] = mapped_column(
        enum_col(Basis, "ck_router_log_basis"), nullable=True
    )
    intent_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    proposal_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: None = 该路由无需确认；True/False = 明确的用户裁决结果。
    user_confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[RouterLogStatus] = mapped_column(
        enum_col(RouterLogStatus, "ck_router_log_status"), nullable=False
    )


__all__ = [
    "M1Variable",
    "M1VariableChannel",
    "M1BaselineVersion",
    "M1BaselineCurrent",
    "M1ChannelObs",
    "M2Preference",
    "M2Strategy",
    "CEvidence",
    "CEvidenceRevision",
    "UpdateProposal",
    "UpdateIntentRow",
    "RouterLog",
    "BenchmarkRun",
    "BenchmarkResponse",
    "enum_col",
]
