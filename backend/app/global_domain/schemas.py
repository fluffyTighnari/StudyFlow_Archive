"""全局域的**读模型**（DTO）—— ORM 行与 HTTP 响应之间的唯一转换层。

为什么这一层必须存在：**ARCH-08 禁止 `app.api` import `app.global_domain.models`。**
API 因此永远拿不到 ORM 类，只能拿到 `reads.py` 返回的对象与这里定义的 DTO。
这条约束的收益很具体：想「顺手 session.add() 一行」的路径在物理上不存在。

约定：

- `from_attributes=True`，由 `model_validate(orm_row)` 构造；
- 时间戳统一 ISO 8601（Pydantic 自动序列化 `datetime`）；
- **不提供任何「把多通道合成一个分数」的字段** —— 那是 §6.5 通道纯度要防的东西，
  连读模型的形状都不允许暗示它存在。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
)

from app.global_domain.enums import (
    HARD_CONSTRAINT_BASIS,
    AdvisoryLevel,
    Basis,
    BaselineConfidence,
    CEvidenceSource,
    CEvidenceStrength,
    ChannelObsSourceKind,
    Completeness,
    ConstructType,
    EvidenceChannel,
    IntentAction,
    IntentStatus,
    LifecycleStatus,
    LLMConfidence,
    Precheck,
    ProposalSource,
    ProposalStatus,
    RouterLogStatus,
    StrategyStrength,
    TriggerType,
)


class _Read(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# M1 认知基准
# --------------------------------------------------------------------------- #


class BaselineVersionRead(_Read):
    """一个 `(变量, 通道)` 的**一个**版本行。历史行永不修改。"""

    version_id: str
    variable_id: str
    channel: EvidenceChannel
    score: float | None
    confidence: BaselineConfidence | None
    completeness: Completeness
    boundary: str | None
    basis: Basis
    source_ref: str | None
    replaces_version_id: str | None
    router_log_id: str | None
    created_at: datetime


class ChannelCoverageRead(_Read):
    """某通道的设计态 + 运行态一次给全（M0 §6.6）。

    `designed=False` 时 `completeness` 恒为 `not_applicable`，`current` 必为 `None`
    —— 「按设计就不该测」与「该测但还没测」是两件事，UI 需要区分它们。
    """

    channel: EvidenceChannel
    designed: bool
    method_ids: list[str]
    design_note: str | None
    completeness: Completeness
    #: 该通道当前生效版本；无则为 `None`（`completeness=partial`）。
    current: BaselineVersionRead | None = None


class M1VariableRead(_Read):
    variable_id: str
    name: str
    display_name: str
    construct_type: ConstructType
    definition_what: str
    definition_what_not: str
    scale_low_anchor: str
    scale_high_anchor: str
    measurement_def: dict[str, Any]
    intervention_hooks: dict[str, Any] | None
    #: M1 §5.4：构念配对只能显式声明，当前无有效配对。
    pairing: dict[str, Any] | None
    sort_order: int
    #: 逐通道覆盖。**注意这里没有「变量总分」** —— 通道纯度由形状本身保证。
    channels: list[ChannelCoverageRead] = Field(default_factory=list)

    @classmethod
    def from_variable(
        cls, variable: Any, channels: list[ChannelCoverageRead]
    ) -> M1VariableRead:
        """显式逐字段构造 —— **不要**改回 `model_validate(variable)`。

        `M1Variable` 上有一个**同名**的 `channels` relationship（返回
        `M1VariableChannel` ORM 行）。`from_attributes` 按名字取值，于是它会
        把那个 relationship 读进来，再拿 ORM 行去构造 `ChannelCoverageRead`，
        抛出一条和真正原因毫无关系的 `Field required: completeness`。

        更麻烦的是这种错误只在**运行时、在某个请求上**出现，而单看 DTO 定义
        完全看不出问题。逐字段构造虽然啰嗦，但任何一次字段改名都会在这里
        立刻变成 `AttributeError` —— 静态可见、定位成本极低。
        （`tests/api/test_api_boundaries.py` 用 AST 禁止这里出现 `model_validate`。）
        """
        return cls(
            variable_id=variable.variable_id,
            name=variable.name,
            display_name=variable.display_name,
            construct_type=variable.construct_type,
            definition_what=variable.definition_what,
            definition_what_not=variable.definition_what_not,
            scale_low_anchor=variable.scale_low_anchor,
            scale_high_anchor=variable.scale_high_anchor,
            measurement_def=variable.measurement_def,
            intervention_hooks=variable.intervention_hooks,
            pairing=variable.pairing,
            sort_order=variable.sort_order,
            channels=channels,
        )


# --------------------------------------------------------------------------- #
# M2 偏好 / B 策略库 / C 证据
# --------------------------------------------------------------------------- #


class M2PreferenceRead(_Read):
    """M2 偏好。

    `basis` 是**存储事实**；`is_constraint` 是从它派生出来的**消费口径**
    （缺口 A18，用户裁定：`stated` 一律按硬约束消费）。派生值随读模型一起给出，
    是为了让前端与 Agent **没有机会**各自硬编码一遍 `basis === 'stated'`
    —— 判定集合只有 `enums.HARD_CONSTRAINT_BASIS` 一处。
    """

    pref_id: str
    statement: str
    target_field: str | None
    value: str | None
    priority: int
    basis: Basis
    status: LifecycleStatus
    router_log_id: str | None
    #: **这条偏好推翻了哪一条**（缺口 A20）。`None` = 它是一条新声明，不是对旧声明的修正。
    #: 随读模型给出，是为了让界面能回答「我之前那样说过，后来自己改了主意」——
    #: 而不必去翻审计日志。
    supersedes_pref_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_constraint(self) -> bool:
        """用户下达的约束（计划必须满足），而不是 LLM 推断出的软偏好（可留痕推翻）。

        与 `reads.is_hard_constraint()` 共用同一个集合常量，因此不可能不一致。
        """
        return self.basis in HARD_CONSTRAINT_BASIS


class M2StrategyRead(_Read):
    strategy_id: str
    name: str
    solves: str
    when_to_use: str
    cost: str
    adjust_signals: str
    evidence_strength: StrategyStrength
    default_weight: int
    catalog_version: str


class CEvidenceRead(_Read):
    """C 条目 —— **必带适用条件**（HC4）。`condition` 非空由库层 CHECK 保证。"""

    evidence_id: str
    statement: str
    condition: str
    effect: str
    evidence_strength: CEvidenceStrength
    source: CEvidenceSource
    status: LifecycleStatus
    router_log_id: str | None
    created_at: datetime
    updated_at: datetime


class ChannelObsRead(_Read):
    obs_id: str
    variable_id: str
    channel: EvidenceChannel
    value: float | None
    source_kind: ChannelObsSourceKind
    source_ref: str | None
    note: str | None
    observed_at: datetime


# --------------------------------------------------------------------------- #
# 提案 / 意图 / 审计日志
# --------------------------------------------------------------------------- #


class UpdateProposalRead(_Read):
    proposal_id: str
    session_ref: str | None
    source: ProposalSource
    trigger_type: TriggerType
    status: ProposalStatus
    source_ref: str | None
    note: str | None
    created_at: datetime
    resolved_at: datetime | None


class UpdateIntentRead(_Read):
    """**持久化形态**的 intent。

    注意与 `app.router.contracts.UpdateIntent` 的区别：那个是 LLM/UI 的**输入**
    （含 `value` 的原始结构），这个是库里的**记录**（含系统判定 `precheck`）。
    `confidence` 是 LLM 自评，`precheck` 是系统结论 —— 两者不可混淆（M0 §7.2）。
    """

    intent_id: str
    proposal_id: str
    action: IntentAction
    target: str
    value: Any = None
    priority: int | None
    basis: Basis
    reasoning: str
    confidence: LLMConfidence
    conflict_note: str | None
    precheck: Precheck | None
    precheck_reason: str | None
    status: IntentStatus
    decided_at: datetime | None


class ProposalDetailRead(UpdateProposalRead):
    """提案 + 其全部 intent —— 前端「提案卡」的数据源（M0 §10）。"""

    intents: list[UpdateIntentRead] = Field(default_factory=list)

    @classmethod
    def from_proposal(
        cls, proposal: Any, intents: list[UpdateIntentRead]
    ) -> ProposalDetailRead:
        """显式构造，理由同 `M1VariableRead.from_variable`。

        `UpdateProposal.intents` 也是一个 relationship。这里更隐蔽：
        `UpdateIntentRead` 恰好**能**从 `UpdateIntentRow` 构造成功，
        所以 `model_validate(proposal)` 今天不会报错 —— 它只会偷偷多查一次库、
        并且让「intent 从哪来」这件事失去唯一的答案。不报错的隐患更难发现。
        """
        return cls(
            proposal_id=proposal.proposal_id,
            session_ref=proposal.session_ref,
            source=proposal.source,
            trigger_type=proposal.trigger_type,
            status=proposal.status,
            source_ref=proposal.source_ref,
            note=proposal.note,
            created_at=proposal.created_at,
            resolved_at=proposal.resolved_at,
            intents=intents,
        )


class RouterLogRead(_Read):
    """**唯一审计源**的一行（M0 §6.1）。

    `trigger` 可为空：`router_log` 也记录没有触发类型的写入事件
    （如 schema 拒绝）。但一次正常的全局变更必定带上它。
    """

    log_id: str
    created_at: datetime
    trigger: TriggerType | None
    source: ProposalSource
    action: IntentAction | None
    target: str | None
    old_value: Any = None
    new_value: Any = None
    basis: Basis | None
    intent_ref: str | None
    proposal_ref: str | None
    user_confirmed: bool | None
    reasoning: str
    status: RouterLogStatus


# --------------------------------------------------------------------------- #
# 请求体（写侧：**只允许表达意图，不允许直接指定落库行**）
# --------------------------------------------------------------------------- #

#: 一条贯穿全部写侧请求体的原则（值得写下来，因为它决定了 422 与「201 + rejected」的分界）：
#:
#: - **HTTP 422 = 请求本身畸形**（类型不对、越界、空白）→ 由 Pydantic 挡住，**不落任何库**；
#: - **201 + `status=rejected` = 请求合法但被策略拒绝**（target 未注册、inferred 写参数、
#:   缺独立证据……）→ Router 照常落提案与审计日志，把理由放进 `results[].reason`。
#:
#: 两者不能混：若把策略拒绝也做成 422，调用方就只会看到一个错误码，
#: 而「这条建议被拒绝了，理由是……」这条信息会失去正式载体 ——
#: 提案卡正是靠它渲染的（M0 §10）。
def _reject_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("不得为空白")
    return value.strip()


class PreferenceCreate(_Read):
    """`POST /api/preferences` 的请求体。

    它会被转成一条 `UpdateIntent(action=add_preference, target=M2.preferences)`，
    再经 `Router.submit(trigger=R1)` —— 也就是说，**UI 直写和 LLM 提议走同一条治理路径**。
    这正是「唯一写入入口」在 API 层的体现：不存在一条更快的捷径。
    """

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(
        min_length=1,
        description=(
            "一条完整表述，**必须把理由一起写进来**（缺口 A19）："
            "「我讨厌看网课，因为浪费时间」，而不是「我讨厌网课」。"
            "理由才是能被泛化的那一半 —— 有了它才能推断出"
            "「凡是要占用大段连续时间的活动他都排斥」，否则只会得到一条孤立禁令，"
            "下次很可能用「看 2 小时讲座」来满足「别看网课」。"
        ),
    )
    priority: int = Field(
        default=5,
        ge=0,
        le=10,
        description=(
            "与其它偏好冲突时的顺序。**不是「可推翻的难度」** —— "
            "用户自己写下的偏好是约束，不会因为 priority 低就变得可以违反。"
        ),
    )
    target_field: str | None = Field(
        default=None,
        description=(
            "要覆盖的装配槽位（如 `SP1.presentation`）。留空 = 一条通用声明。"
            "**它只决定这条偏好放在哪，不决定它有多硬。**"
        ),
    )
    value: str | None = None
    supersedes_pref_id: str | None = Field(
        default=None,
        description=(
            "**这条偏好推翻的那一条**的 `pref_id`（缺口 A20）。"
            "只在用户明确改主意时填：「其实我能接受精品网课」→ 指向"
            "「我讨厌网课，因为浪费时间」。被推翻的那条会自动归档，"
            "但**行留着**（「我曾经那样说过」是事实）。"
            "填错 id 会整条被拒绝，不会静默忽略。"
        ),
    )

    _check_statement = field_validator("statement")(_reject_blank)


class EvidenceCreate(_Read):
    """`POST /api/evidence` 的请求体。`condition` 必填（M2 §4.4 第 3 条）。"""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1)
    condition: str = Field(min_length=1)
    effect: str = Field(min_length=1)
    evidence_strength: CEvidenceStrength
    source: CEvidenceSource = CEvidenceSource.USER_DIRECT

    _check_statement = field_validator("statement")(_reject_blank)
    _check_condition = field_validator("condition")(_reject_blank)
    _check_effect = field_validator("effect")(_reject_blank)


class DecisionBody(_Read):
    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(min_length=1)
    approved: bool
    reason: str | None = None


class ResolveRequest(_Read):
    """`POST /api/proposals/{id}/resolve` 的请求体。

    **允许只裁决一部分**：未出现在 `decisions` 里的 intent 保持 `pending`，
    提案继续停在 `pending_confirmation`。一条提案因此可以分几次裁决完
    （`Router.resolve` 的语义如此，这里只是如实暴露）。
    """

    model_config = ConfigDict(extra="forbid")

    decisions: list[DecisionBody] = Field(min_length=1)


# --------------------------------------------------------------------------- #
# 基准重做建议（§5.4）—— 只读视图，**没有「是否阻断」这种字段**
# --------------------------------------------------------------------------- #


class AdvisoryRead(_Read):
    model_config = ConfigDict(extra="forbid")

    variable_id: str
    level: AdvisoryLevel
    run_count: int
    message: str
