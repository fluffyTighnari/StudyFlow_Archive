"""Router 的输入 / 输出契约（M0 §7.2）。

取值定义与 `global_domain.enums` **共用同一份** —— 判定表里的取值不允许在别处
手写字符串常量，否则策略表与实际存储会静默漂移。

`value` 的**结构**因 target 而异（M1 是分通道分数、M2 是偏好陈述、C 是带适用条件的
经验）。具体结构由 Registry 声明的 `value_schema` 在 **precheck 阶段**校验：
把「结构不合法」挡在提案落库之前，而不是等 commit 时才炸出一半。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.global_domain.enums import (
    AdvisoryLevel,
    Basis,
    BaselineConfidence,
    CEvidenceSource,
    CEvidenceStrength,
    Completeness,
    EvidenceChannel,
    IntentAction,
    IntentStatus,
    LLMConfidence,
    Precheck,
    ProposalStatus,
    TriggerType,
)


class _Strict(BaseModel):
    """所有契约的基类：多给字段直接报错。

    宽松解析会让「LLM 多传了一个字段」静默通过，而那个字段往往正是它想表达的
    真实意图（例如把 `channel` 写成 `evidence_channel`）—— 结果数据落进默认值，
    错误要等到几周后回看基线时才发现。宁可此刻报错。
    """

    model_config = ConfigDict(extra="forbid")


class _NonBlank(_Strict):
    """一组「不得为空白」的文本字段校验。"""

    @field_validator("statement", "condition", "effect", check_fields=False)
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("不得为空白")
        return value.strip()


# --------------------------------------------------------------------------- #
# target 参数的强类型形态（由 Registry 逐 target 指定）
# --------------------------------------------------------------------------- #


class M1ParameterValue(_Strict):
    """`M1.<variable_id>` 的 `update_parameter` 参数。

    `channel` 必填且**只描述一个通道** —— 不存在「一次写一个变量的总分」这种形态，
    因此也就不存在「把三个通道平均一下」的入口（M0 §6.5）。
    """

    channel: EvidenceChannel
    score: float | None = Field(default=None, ge=0, le=10)
    completeness: Completeness
    confidence: BaselineConfidence | None = None
    boundary: str | None = None

    @field_validator("boundary")
    @classmethod
    def _normalize_boundary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class M2PreferenceValue(_NonBlank):
    """`M2.preferences*` 的 `add_preference` 参数。

    **理由内联在 `statement` 里**（缺口 A19，用户裁定：不新增 `reason` 字段）。
    这条规范必须写在**这里**，而不是只写在文档里 —— 这是 LLM 真正读得到的
    位置（工具 schema 由本契约生成）。规范的作用很具体：「因为浪费时间」
    是唯一能泛化的那一半，缺了它，LLM 会把「别看网课」当成一条孤立禁令，
    下次用「看 2 小时讲座」来满足它。

    **硬度不由本契约决定**：`basis=stated` 写进来的偏好一律按用户约束消费
    （缺口 A18），`target_field` 只表示装配槽位。见
    `enums.HARD_CONSTRAINT_BASIS` 与 `reads.is_hard_constraint`。
    """

    statement: str = Field(
        description=(
            "完整表述，**把理由一起写进来**："
            "「我讨厌看网课，因为浪费时间」而非「我讨厌网课」。"
        )
    )
    priority: int = Field(
        default=5,
        ge=0,
        le=10,
        description="与其它偏好冲突时的顺序，不是「可推翻的难度」。",
    )
    target_field: str | None = Field(
        default=None,
        description=(
            "要覆盖的装配槽位（如 `SP1.presentation`）；留空 = 通用声明。"
            "只决定放在哪，不决定有多硬。"
        ),
    )
    value: str | None = None
    supersedes_pref_id: str | None = Field(
        default=None,
        description=(
            "这条偏好**推翻**的那一条的 `pref_id`（如 `P-0003`）。"
            "只在用户明确改主意时填（「其实我能接受精品网课」），"
            "被推翻的那条会自动归档、但**行留着**（「我曾经那样说过」是事实）。"
            "**拿不准就不要填** —— 填错 id 会被拒绝整条，"
            "而该填不填会让新旧两条同时生效、互相矛盾（缺口 A20）。"
            "不允许用 `inferred`/`experienced` 去推翻一条 `stated` 约束。"
        ),
    )

    @field_validator("target_field")
    @classmethod
    def _normalize_target_field(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class CEvidenceValue(_NonBlank):
    """`C.evidence*` 的 `add_preference` 参数。

    `condition` 必填（M2 §4.4 第 3 条）——纯偏好没有适用条件，应写 `M2.preferences`。
    这里挡住的是一种会污染证据库的输入：把「我喜欢先推导」当成一条「经验证据」。
    """

    statement: str
    condition: str
    effect: str
    evidence_strength: CEvidenceStrength
    source: CEvidenceSource = CEvidenceSource.SESSION_DERIVED


class HypothesisValue(_NonBlank):
    """`C.hypothesis` 的 `add_preference` 参数 —— 他**想试但还没试**的具体做法。

    ## 与 `CEvidenceValue` 的差别只有一个字段，而那个字段必须缺

    少了 `evidence_strength`，也**没有** `source`（来源是"他说的"，
    恒为 `stated`，不需要填）。多留一个强度槽会诱出「低强度假说」这种说法，
    而假说不是"最弱的证据"——它是"还没开始验证"，两者不能共用一个刻度。

    ## `condition` 为什么仍然必填

    这是"方法 vs 偏好"的结构判据：**偏好没有条件槽**（`M2PreferenceValue`
    里根本没有 `condition`），具体做法才有。于是「我喜欢在安静的环境下学习」
    这句话在结构上填不进假说 —— 它没有一个"什么场景"可以填。
    模型硬编一个假条件仍然可能，但那会显示在列表里，用户一眼看得见。
    """

    statement: str
    condition: str
    effect: str


class StatusChangeValue(_Strict):
    """`archive` / `restore` 的参数 —— 只有一句可选的 `reason`。

    ## 为什么归档也要带理由

    因为这是**唯一**会在用户几个月后回头看时解释"我当初为什么把它拿掉"的东西。
    可选的，不是必填：让他为了删掉一条东西必须先编一句话，他就不删了，
    而那些没删的东西会继续参与上下文。

    ## 为什么这里没有 `status` 字段

    `archive` 只可能是 `archived`、`restore` 只可能是 `active` —— 让调用方传
    `status` 等于给它一个"传错"的机会，而收益是零。**能填错的字段就是会填错的字段。**
    """

    reason: str | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def _strip(cls, value):
        if not isinstance(value, str):
            return value
        return value.strip() or None


class ReviseCEvidenceValue(CEvidenceValue):
    """`C.evidence` 的 `revise` 参数 —— 三个槽整体替换，外加一句改动理由。

    继承而不是重写：**能被写进来的东西，与新增时一模一样**。
    改写不该是一条更宽松的路 —— 那会让"先随便写一条，再改成想要的"成本低于
    "一次写对"，而后者才是我们想要的。
    """

    reason: str | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def _strip_reason(cls, value):
        if not isinstance(value, str):
            return value
        return value.strip() or None


class ReviseHypothesisValue(HypothesisValue):
    """`C.hypothesis` 的 `revise` 参数 —— 同上，且**仍然没有 `evidence_strength`**。

    这一条要特别防：改写是"它还是假说，只是说得更准了"，
    不是"它变成证据了"。所以 `basis` 依然只能 `stated`，强度槽依然不存在。
    """

    reason: str | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def _strip_reason(cls, value):
        if not isinstance(value, str):
            return value
        return value.strip() or None


# --------------------------------------------------------------------------- #
# 提交侧
# --------------------------------------------------------------------------- #


class UpdateIntent(_Strict):
    """LLM（或 UI）产出的**唯一**更新表达形态。

    LLM 永远不写字段，只产出本对象；`confidence` 是 LLM 的自评，
    与系统的 `precheck` 判定是两件事，不可混淆（M0 §7.2）。
    """

    intent_id: str = Field(min_length=1)
    action: IntentAction
    target: str = Field(min_length=1)
    value: Any = None
    priority: int | None = Field(default=None, ge=0, le=10)
    basis: Basis
    #: 必填，进日志（路由层 §4「LLM 评估必做」第 3 条）。
    reasoning: str = Field(min_length=1)
    confidence: LLMConfidence
    conflict_note: str | None = None


class SubmitRequest(_Strict):
    """一次 `submit_global_update` 的完整入参。"""

    trigger_type: TriggerType
    intents: list[UpdateIntent] = Field(min_length=1)
    #: 证据出处：`benchmark_run_id`（R2）/ `session_id`（R3/R4）/ 空（R1 直写）。
    #: 必需，否则 §5.4 的「按 run_id 去重重做次数」无从计算。
    source_ref: str | None = None
    session_ref: str | None = None
    note: str | None = None


class Decision(_Strict):
    """用户对单条 intent 的裁决。"""

    intent_id: str = Field(min_length=1)
    approved: bool
    reason: str | None = None


# --------------------------------------------------------------------------- #
# 输出侧
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BenchmarkAdvisory:
    """R2 重做建议。**故意不提供任何「阻断」能力**（M0 §5.4）。

    与 `PrecheckResult.verdict` 正交：它不产生、也不修改判定结果。
    """

    variable_id: str
    level: AdvisoryLevel
    run_count: int
    message: str

    def blocks(self) -> bool:
        """恒为 `False` —— 类型层面的承诺，不是一条可以改的策略。"""
        return False


class ConflictReport(_Strict):
    """冲突分析结果 —— **只报告，不裁定**（路由层 §8 第 2 条）。

    `slot` 是**真实写入位置**的标识（`M1.V-SE/measured`、`M2.preferences.SP1.presentation`），
    **不是** `target` 字符串 —— 按 target 分组会把两个独立通道、两条不相关的偏好
    错当成相撞（P5.2 修正，见 `router/conflict.py` 的模块文档）。

    `winner_intent_id is None` 表示**同档位并列、无法裁定**，整组应被拒绝，
    而不是按 `intent_id` 字典序挑一个。
    """

    slot: str
    intent_ids: tuple[str, ...]
    winner_intent_id: str | None
    order_name: str
    note: str

    def is_tie(self) -> bool:
        """并列 = 没有胜者 = 整组拒绝（不是「挑一个赢」）。"""
        return self.winner_intent_id is None


class PrecheckResult(_Strict):
    """单条 intent 的确定性判定。

    三个字段刻意**互不覆盖**：

    - `verdict`：§5.3 判定表的结论（要不要用户确认 / 是否直接拒绝）；
    - `blockers`：与判定正交的**硬前置条件**（如 §7.3 的「≥2 条独立 Session 证据」）。
      非空即表示「即便用户同意也无法应用」；
    - `advisories`：§5.4 的重做建议，**永不阻断**。

    若把这三者挤进一个字段，早晚会有人用「写个 blocker」实现「拦一下」，
    而 §5.4 的不阻断承诺就悄悄失效了。
    """

    intent_id: str
    verdict: Precheck
    reason: str
    advisories: tuple[BenchmarkAdvisory, ...] = ()
    blockers: tuple[str, ...] = ()

    @property
    def can_apply(self) -> bool:
        """是否具备被应用的资格（**不**代表已经获得确认）。"""
        return self.verdict is not Precheck.MUST_REJECT and not self.blockers


class IntentResult(_Strict):
    """单条 intent 在 `submit` / `resolve` 之后的完整结果。"""

    intent_id: str
    verdict: Precheck
    reason: str
    status: IntentStatus
    advisories: tuple[BenchmarkAdvisory, ...] = ()
    blockers: tuple[str, ...] = ()
    router_log_id: str | None = None


class SubmitResult(_Strict):
    proposal_id: str
    status: ProposalStatus
    trigger_type: TriggerType
    results: list[IntentResult]
    conflicts: list[ConflictReport] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 回滚（R5：不产生 Intent，因此不经过判定表）
# --------------------------------------------------------------------------- #


class RollbackRequest(_Strict):
    """回滚请求。`reason` 必填。

    为什么强制写原因：回滚是**唯一一种会让基线读数变小/变旧**的操作。
    半年后看到 `M1-SE-007 score=4.0` 时，唯一能区分「他真的退步了」与
    「他当时手滑点错了回滚」的东西，就是这条原因。这个字段的价值随时间的
    推移而上升，而填写成本只在当下 —— 因此不给默认值。
    """

    channel: EvidenceChannel
    to_version_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("回滚必须写明原因")
        return value.strip()


class RollbackResult(_Strict):
    """回滚结果。**注意 `new_version_id`**：回滚也生成新版本，绝不删历史。"""

    variable_id: str
    channel: EvidenceChannel
    to_version_id: str
    new_version_id: str
    replaced_version_id: str | None
    router_log_id: str
    reason: str
