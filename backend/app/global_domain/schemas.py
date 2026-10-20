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
    BenchmarkForm,
    BenchmarkRunStatus,
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


class ScaleBandRead(_Read):
    """一段分数说明（P10）。

    用户拿到的是一个 `6.2`，他需要知道**中间那一段**是什么意思 ——
    锚点只回答"0 分和 10 分长什么样"。少了这一段，中间分只能靠脑补，
    而每个人脑补的结果都不一样。

    区间**左闭右开**（最后一段右闭）：`4.0` 属于中段、`10.0` 属于高端。
    这个约定只写在一处（`app/benchmark/scale.py::band_for`）。
    """

    low: float
    high: float
    label: str
    description: str
    #: 这一段是**系统写的初稿**，还没经用户确认。中段没有现成锚点可引用，
    #: 所以它必然是新写的文案 —— 标出来，而不是伪装成"从锚点算出来的"。
    draft: bool = False


class M1VariableRead(_Read):
    variable_id: str
    name: str
    display_name: str
    construct_type: ConstructType
    definition_what: str
    definition_what_not: str
    scale_low_anchor: str
    scale_high_anchor: str
    #: 0-4 / 4-8 / 8-10 三段说明。空列表 = 这个变量还没写分段说明。
    scale_bands: list[ScaleBandRead] = Field(default_factory=list)
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
            scale_bands=[
                ScaleBandRead.model_validate(band) for band in (variable.scale_bands or [])
            ],
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
    #: P7 新增。判定表只放 `experienced`/`measured` 进来，故此字段的取值域
    #: 由判定表决定，不由这一列决定 —— 列只能记录，判定表才能拒绝。
    basis: Basis
    evidence_strength: CEvidenceStrength
    source: CEvidenceSource
    status: LifecycleStatus
    router_log_id: str | None
    created_at: datetime
    updated_at: datetime


class HypothesisRead(_Read):
    """假说 —— 他**想试但还没试**的具体做法（P7）。

    ★ `condition` / `effect` 与 `c_evidence` 同形，因为它们是同一条
    「当 X 时做 Y」的两种可信度。**没有 `evidence_strength`**：
    未验证的东西没有强度（那会是"最弱的证据"，与"还没开始验证"混淆）。
    """

    hypothesis_id: str
    statement: str
    condition: str
    effect: str
    #: 恒为 `stated`。出现在响应里而不是被隐藏 —— 客户端不必猜，
    #: 而且"它为什么不能是 experienced"在判定表里有一行答案。
    basis: Basis
    source_session_id: str | None
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


class HypothesisCreate(_Read):
    """`POST /api/hypotheses` 的请求体 —— 他**想试但还没试**的做法。

    ## 没有 `basis` 字段，而且这是刻意的

    这个端点只装一种东西：**他还没试过的想法**。多给一个 `basis` 参数，
    就等于允许"以 experienced 写进假说表" —— 那恰好是判定表专门拒绝的那一格。
    一个字段填不出来，比一个字段能填错要好。

    想记一条已验证的经验，请用 `POST /api/evidence` ——
    **两个端点的差别就是他点了哪个表单**。

    ## `condition` 与 `effect` 都必填

    这两个槽就是"方法 vs 偏好"的结构判据。偏好没有它们
    （`M2PreferenceValue` 里根本没有 `condition`），所以
    「我喜欢在安静的环境下学习」在结构上填不进这张表 ——
    它会因为缺一个"什么场景"而被拒，而不是被模型默默地归错类。
    """

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1)
    condition: str = Field(min_length=1)
    effect: str = Field(min_length=1)

    _check_statement = field_validator("statement")(_reject_blank)
    _check_condition = field_validator("condition")(_reject_blank)
    _check_effect = field_validator("effect")(_reject_blank)


class HypothesisRevise(_Read):
    """`PUT /api/hypotheses/{id}` 的请求体 —— 三个槽整体替换。

    ## 为什么是整体替换而不是逐字段补丁

    PATCH 语义需要一套"哪些字段可以不动"的规则，而 `condition` 是结构判据。
    允许"不动它"就等于允许一条**没有场景**的假说继续存在 —— 而它正是"这不是
    方法"的那道闸。整体替换让「提交的表单就是这个条目的全部」，
    没有中间态可以藏东西。

    ## 为什么没有 `basis` / `status`

    与 `HypothesisCreate` 同一个理由：**想改可信度只能换一张表**
    （写成 `C.evidence`），想改状态请用归档/恢复端点。
    把它们塞进这个请求体，就等于给"以 experienced 改写假说"开了一个字段。
    """

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1)
    condition: str = Field(min_length=1)
    effect: str = Field(min_length=1)
    #: 改动理由。可选 —— 他删一条东西不该先交一篇小作文，
    #: 但想写的时候要有地方写（它进 RouterLog，不进行）。
    reason: str | None = None

    _check_statement = field_validator("statement")(_reject_blank)
    _check_condition = field_validator("condition")(_reject_blank)
    _check_effect = field_validator("effect")(_reject_blank)


class EvidenceRevise(EvidenceCreate):
    """`PUT /api/evidence/{id}` 的请求体 —— 与新增同一套字段，外加一句理由。

    继承 `EvidenceCreate`：**能被改进去的东西，与能新增的东西一模一样**。
    改写不该是一条更宽松的路 —— 那会让"先随便写一条再改"比"一次写对"便宜。
    """

    reason: str | None = None


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


# --------------------------------------------------------------------------- #
# 领域勾选清单（用户裁定 岔路 A = 乙）
# --------------------------------------------------------------------------- #


class DomainTopicRead(BaseModel):
    """清单里的一格。

    **不是 `_Read`**：它的 `familiar` / `interested` 不在这张表上 ——
    它们来自用户的档案，所以这一行是**目录 × 档案**的合成结果，
    由 API 层构造。把它写成 `from_attributes` 会暗示"表里就有这两个字段"。
    """

    model_config = ConfigDict(extra="forbid")

    topic_id: str
    group_id: str
    group_label: str
    label: str
    #: 边界说明。**这是用户勾选时唯一的依据** —— 没有它勾选会变成猜。
    covers: str
    familiar: bool
    interested: bool


class DomainGroupRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str
    group_label: str
    topics: list[DomainTopicRead]


class DomainChecklistRead(BaseModel):
    """整份清单的当前状态。这是「第一次进入基准测试」看到的那个页面。

    `version_no=None` 表示**还没勾过**。这与"勾了但全空"不同：
    后者表示"我熟悉所有领域"，前者表示"这个问题还没问过他"。
    """

    model_config = ConfigDict(extra="forbid")

    profile_id: str | None
    version_no: int | None
    familiar_count: int
    interested_count: int
    #: 只在**陌生区**里问兴趣，所以这里是 `familiar` 的补集大小 ——
    #: 它是"第二遍还能勾多少格"的分母。
    unfamiliar_count: int
    updated_at: datetime | None
    groups: list[DomainGroupRead]


class DomainChecklistWrite(BaseModel):
    """两次勾选的结果一起提交。

    **两个列表一起提交、而不是分两次**：因为 `interested ⊆ ¬familiar`
    是一条跨两者的约束。分两次提交时，第一次提交后系统处于一个
    "兴趣还落在熟悉区里"的中间状态，而那个状态没有任何意义。
    """

    model_config = ConfigDict(extra="forbid")

    familiar: list[str] = Field(default_factory=list)
    interested: list[str] = Field(default_factory=list)
    reason: str | None = None


class DomainProfileRead(_Read):
    """档案的一版。历史版本永不修改。"""

    profile_id: str
    version_no: int
    familiar: list[str]
    interested: list[str]
    reason: str | None
    created_at: datetime
    router_log_id: str


# --------------------------------------------------------------------------- #
# 基准运行（用户要求：简版/全面版、可中断保存退出、可继续、可重开）
# --------------------------------------------------------------------------- #


class BenchmarkRunRead(_Read):
    run_id: str
    form: BenchmarkForm
    status: BenchmarkRunStatus
    variable_id: str
    channel: EvidenceChannel
    method_id: str
    item_ids: list[str]
    seed: int
    topic_snapshot: list[Any]
    method_score: float | None
    channel_score: float | None
    confidence: BaselineConfidence | None
    completeness: Completeness
    boundary: str
    started_at: datetime
    finished_at: datetime | None
    #: `[{status, at, reason?}]` —— 状态变迁史。用户要看到"我中断过几次"。
    status_history: list[Any]
    note: str | None
    #: 已经落了多少道题的作答。中断后靠它回答"我做到哪了"。
    response_count: int = 0


class BenchmarkRunStart(BaseModel):
    """开一次运行。

    ## ★ P8：`item_ids` 不传 = **请服务端抽题**

    原先这里写的是「`item_ids` 由调用方给，不由服务端抽」，
    理由是"抽题规则住在 `app.benchmark`，而这里只是记账"。

    那个理由对了一半：**抽题确实不该住在 API 层**，但"不由服务端抽"
    并不等于"由调用方抽" —— 第三个人选是**服务端把抽题委派给
    `app.benchmark.sampling`**。API 依然是记账，而规则依然只有一份。

    不委派的下场是：抽题规则要被执行两次（前端一次、`e2echeck` 一次），
    而两份实现**不会一直一致** —— 不一致的表现是"界面抽的题和记录的
    `topic_snapshot` 对不上"，没有任何一步会报错。

    保留 `item_ids` 参数是为了**两条正当用途**：复现（拿上次的 `seed`
    与题目列表重跑）与测试（指定几道题，不依赖题池当前内容）。
    """

    model_config = ConfigDict(extra="forbid")

    form: BenchmarkForm
    variable_id: str = Field(min_length=1)
    channel: EvidenceChannel
    method_id: str = Field(min_length=1)
    #: 留空 → 由 `app.benchmark.sampling` 按 §6.3 的规则抽一套。
    item_ids: list[str] = Field(default_factory=list)
    #: 抽题随机种子。**同一种子必须抽出同一套题**（§6.3「可复现」）。
    seed: int = 0
    topic_snapshot: list[Any] = Field(default_factory=list)
    completeness: Completeness = Completeness.PARTIAL
    note: str | None = None


class BenchmarkPlanItemRead(_Read):
    """抽中的一道题，**带上它是怎么被抽中的**。

    `why` 不是装饰：用户问"为什么考这篇"时，答案必须是当初算出来的那一句，
    而不是事后编的一句解释。
    """

    item_id: str
    role: str
    difficulty: str
    topics: list[str]
    why: str


class BenchmarkPlanRead(_Read):
    """一次**抽题预览**：抽哪些题、为什么、涉及哪些专题。

    ## 为什么要有预览端点

    "开始测试"是一个不可逆的用户动作（他接下来要花 30 或 60 分钟）。
    在按下去之前让他看见**这次要考什么、按什么规则选的**，
    比事后解释"为什么这次分低"便宜得多。

    它同时也是**抽题机制的验证面**：规则是不是真的按难度直方图、
    是不是真的排除了他标熟悉的领域 —— 这些都能在这个响应里逐条核对，
    而不必跑完一次 60 分钟的卷子再看分数。
    """

    variable_id: str
    channel: EvidenceChannel
    method_id: str
    form: BenchmarkForm
    seed: int
    items: list[BenchmarkPlanItemRead]
    #: 本次涉及的专题 —— 会写进 `benchmark_run.topic_snapshot`。
    topics: list[str]
    #: 抽题过程留给人的几句话（锚题几道、排除了哪些、有没有跨领域）。
    notes: list[str]
    #: 一行摘要。
    summary: str


class BenchmarkRunStatusWrite(BaseModel):
    """中断 / 继续 / 作废。三种用户动作走同一个端点，取值不同。"""

    model_config = ConfigDict(extra="forbid")

    target: BenchmarkRunStatus
    reason: str | None = None


class BenchmarkResponseWrite(BaseModel):
    """落一道题的作答。`item_score=None` + `raw_answer` 非空 = **没测到**。

    ## ★ P9：`item_score` 可以**不传**

    不传 = 请服务端算（`POST /runs/{id}/score`）。传了就用它 ——
    与 `BenchmarkRunStart.item_ids` 同一个道理：服务端有规则，
    但复现与测试需要有"我说了算"的路径。

    ★ 传与不传的差别**不是风格问题**：由调用方算分意味着判分规则
    要在前端再实现一遍，而两份实现不会一直一致 ——
    不一致的表现是两个界面显示同一个人的两个分数，且都不报错。
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1)
    question_no: int = Field(ge=0)
    kind: ResponseQuestionKind
    judged_by: JudgedBy
    raw_answer: str | None = None
    item_score: float | None = Field(default=None, ge=0, le=10)
    difficulty: str | None = None
    weight: float | None = None
    elapsed_sec: float | None = None
    #: ↓↓ **隐性参数**（P11）：作答过程中顺手记下的量，**不由他填**。
    #:
    #: ★ 传进来 = **记录**，不等于计分。想要它产出分数，
    #: 题目必须声明一条 `observation_table` 并把 `unit` 写成这些名字之一。
    #: 不给默认权重是刻意的：在 4 档量表上切换两次与在 10 档量表上切换两次
    #: 不是同一件事，替一门还没定义的测量学做决定，代价是他的分数
    #: 被一个没写下来的假设拖偏。
    #:
    #: 不传 = 这次没采到（`NULL`），与"0 次"是两件事：
    #: 观测题遇到它**作废**，而不是拿最低分。
    hesitation_sec: float | None = None
    switch_count: int | None = None
    help_request_count: int | None = None
    hint_used_count: int | None = None
    judge_model: str | None = None
    judge_note: str | None = None
    judged_hits: list[Any] | None = None


class BenchmarkScoreItemRead(_Read):
    """一道题的计分结果，**带上"这个数是怎么来的"**（P9）。

    `scoring_method` 是这一层最要紧的字段：它让"这个 8 分是哪条规则算的"
    在半年后仍然可答 —— `kind` 只说"这是要点题"，
    而规则（尤其换算表）是会改的。
    """

    response_id: str
    item_id: str
    question_no: int
    kind: ResponseQuestionKind
    difficulty: str | None
    item_score: float | None
    scoring_method: str | None
    judged_by: JudgedBy
    judge_model: str | None
    judge_note: str | None
    #: 作废原因（`item_score is None` 时非空）。**它必须说出来** ——
    #: 一个没分的题与一个 0 分的题在界面上长得一样，而含义完全相反。
    void_reason: str | None = None
    #: 是否参与了方法分聚合（标尺题与作废题不参与）。
    counted: bool = False


class EquatingAnchorRead(_Read):
    """一道锚题的这次读数 vs 基线读数（P12）。"""

    item_id: str
    baseline: float
    current: float
    #: `基线 - 本次`。**正数 = 这次比基线低**（该往上补）。
    delta: float


class EquatingRead(_Read):
    """等值化校正（P12）—— **连同它底下那个假设一起给出来**。

    ★ 为什么不能只给小一个 `shift`：等值化底下压着一句
    「他对这几道锚题的能力与基线时相同，所以差值归给仪器」。
    这个假设隔得越久越不成立，而系统**分不出**"他退步了"与"仪器漂了"。
    把假设、参与的锚题、基线来自哪几次运行一起给出来，读的人才有判断的依据。

    ★ `shift is None` = **做不了校正**，不是"校正量为 0"：
    共有的锚题不足时把差量归给仪器，是**编一个理由**。
    """

    shift: float | None
    reason: str
    #: 校正后仍可用的说明（含假设），可直接展示给人看。
    description: str
    anchors: list[EquatingAnchorRead] = Field(default_factory=list)
    #: 基线取自哪几次运行 —— 假设成不成立取决于那是多久以前。
    baseline_runs: list[str] = Field(default_factory=list)


class BenchmarkScoreRead(_Read):
    """一次运行的计分总账（P9）。

    ## 为什么它要把"没算进去的东西"也列出来

    `skipped` 与 `notes` 是这一层诚实性的全部。计分最容易出的一类错
    是**静默少算**：某道题因为题库里找不到、或题型没有换算表而没计分，
    于是方法分看起来一样正常，只是它建立在比声称更少的题上。
    """

    run_id: str
    method_score: float | None
    channel_score: float | None
    #: ★ 等值化校正后的方法分（P12）。`method_score` **保持原始值**：
    #: 校正量会随基线变化，改写历史分数会让"当时是多少"无从回答。
    equated_score: float | None = None
    equating: EquatingRead | None = None
    completeness: Completeness
    #: 置信档（`hypothesis` / `provisional` / `calibrated`）与**它的理由**。
    #: 一个没有理由的置信档会被读成"系统在谦虚"或"在吹牛"，两种都没法核对。
    confidence: BaselineConfidence | None
    confidence_reason: str
    method_detail: str
    channel_detail: str
    items: list[BenchmarkScoreItemRead]
    #: 没参与计分的题与原因。空列表 = 每一道都算进去了。
    skipped: list[str] = Field(default_factory=list)
    #: 这一层**还没做**的部分（如等值化校正），如实列出。
    notes: list[str] = Field(default_factory=list)


class BenchmarkFinishWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method_score: float | None = Field(default=None, ge=0, le=10)
    channel_score: float | None = Field(default=None, ge=0, le=10)
    anchor_baseline: dict = Field(default_factory=dict)
    confidence: BaselineConfidence | None = None
    completeness: Completeness
    boundary: str = ""


class BenchmarkStateRead(BaseModel):
    """进入基准页时**先问的那一个问题**的答案。

    它是这一整块功能对用户的入口，所以刻意把三件事一次说清：

    1. `has_checklist` —— 还没勾过清单时，页面第一屏应该是清单而不是测试
    2. `resumable_run` —— 有中断的运行时要问"继续还是重开"
    3. `advisory` —— 本季度该不该重做

    把这三件事分开让前端各查一次，会出现"清单还没勾却进了测试"这类
    只有特定顺序下才复现的错误；合成一个响应之后，那个顺序不存在了。
    """

    model_config = ConfigDict(extra="forbid")

    has_checklist: bool
    checklist_version: int | None
    familiar_count: int
    interested_count: int
    #: 可以继续的那次运行（`status=interrupted`），没有则为 `None`。
    resumable_run: BenchmarkRunRead | None
    #: 正在进行的那次（`status=in_progress`），没有则为 `None`。
    in_progress_run: BenchmarkRunRead | None
    run_count: int
