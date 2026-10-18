"""UpdatePolicyRegistry —— 数据驱动的策略查表（M0 §7.3）。

「数据驱动」的具体含义：**没有 if-else 链**。判定所需的全部信息（允许的 action、
参数结构、策略实现、来源限制）都写在下面的表里。新增一个 target 是**加一行**，
不是改一段判断逻辑 —— 改逻辑的改动无法被穷举测试，加一行的改动可以。

本模块**曾经**同时是 `M1.<variable_id>` ↔ 存储主键 `<variable_id>` 的唯一映射点
（M0 §11 A9）。P5 起该映射下沉到 `app.global_domain.targets`（理由见那里的模块说明），
本模块转出 `target_for_variable` / `variable_id_from_target` 以保持既有引用可用。
「转换只允许有一处实现」这条约束没有变 —— 只是那一处换了地址，
`tests/router/test_registry.py` 的 AST 断言随之改为盯 `targets.py`。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel

from app.global_domain.enums import (
    M1_PREFERENCE_VARIABLES,
    M1_TRAIT_VARIABLES,
    IntentAction,
    ProposalSource,
    TriggerType,
)
from app.router.contracts import (
    CEvidenceValue,
    M1ParameterValue,
    M2PreferenceValue,
)


class TargetClass(StrEnum):
    """§5.3 判定表里的「target 类」。"""

    M1_PREFERENCE = "m1_preference"
    M1_TRAIT = "m1_trait"
    M2_PREFERENCE = "m2_preference"
    C_EVIDENCE = "c_evidence"


#: 策略实现的键。真实实现位于 `app.router.policies`（ARCH-02：对外不可见）。
POLICY_M1 = "m1_policy"
POLICY_M2 = "m2_policy"
POLICY_C = "c_policy"
#: `no_change` 专用：所有 target 共用（M0 §5.3「任意 | no_change | 任意」行）。
POLICY_NOOP = "noop_policy"


@dataclass(frozen=True)
class TargetRule:
    """一个 target 类的完整策略声明。"""

    target_class: TargetClass
    allowed_actions: frozenset[IntentAction]
    policy_key: str
    #: 该 target 的 `value` 结构。`None` 表示该组合不承载参数。
    value_schema: type[BaseModel] | None
    #: 单条 intent 最多写几行 —— 超过就必须拆分 intent，不允许「一次写一片」。
    max_rows: int
    source_restriction: str


#: 所有 target 都接受 `no_change`。
#:
#: §5.3 有一行是「任意 | no_change | 任意 | 任意 | auto_apply（仅日志）」。
#: 把 `no_change` 放进每个 rule 的 `allowed_actions`（而不是在 precheck 里特判）
#: 是为了让「这个 target 接受哪些 action」永远只有一个回答处 ——
#: 特判会把同一个事实拆成两处，而两处迟早会不一致。
_NO_CHANGE = frozenset({IntentAction.NO_CHANGE})

RULES: dict[TargetClass, TargetRule] = {
    TargetClass.M2_PREFERENCE: TargetRule(
        target_class=TargetClass.M2_PREFERENCE,
        allowed_actions=frozenset({IntentAction.ADD_PREFERENCE}) | _NO_CHANGE,
        policy_key=POLICY_M2,
        value_schema=M2PreferenceValue,
        max_rows=1,
        source_restriction="stated / user-confirmed（M0 §7.3）",
    ),
    TargetClass.C_EVIDENCE: TargetRule(
        target_class=TargetClass.C_EVIDENCE,
        allowed_actions=frozenset({IntentAction.ADD_PREFERENCE}) | _NO_CHANGE,
        policy_key=POLICY_C,
        value_schema=CEvidenceValue,
        max_rows=1,
        source_restriction="user_direct / session_derived（M0 §7.3）",
    ),
    TargetClass.M1_PREFERENCE: TargetRule(
        target_class=TargetClass.M1_PREFERENCE,
        allowed_actions=frozenset({IntentAction.UPDATE_PARAMETER}) | _NO_CHANGE,
        policy_key=POLICY_M1,
        value_schema=M1ParameterValue,
        max_rows=1,
        source_restriction="measured / experienced + 确认；仅呈现/活动形态（M0 §7.3）",
    ),
    TargetClass.M1_TRAIT: TargetRule(
        target_class=TargetClass.M1_TRAIT,
        allowed_actions=frozenset({IntentAction.UPDATE_PARAMETER}) | _NO_CHANGE,
        policy_key=POLICY_M1,
        value_schema=M1ParameterValue,
        max_rows=1,
        source_restriction=(
            "measured 优先；experienced 须确认且需 ≥2 条独立 Session 证据或完整重测（M0 §7.3）"
        ),
    ),
}


# --------------------------------------------------------------------------- #
# 命名空间映射（A9 的唯一落点）—— 实现在 `app.global_domain.targets`，此处转出
# --------------------------------------------------------------------------- #
#
# P5 把这三个常量与两个转换函数**下沉到了全局域**：不变式自检需要它们，
# 而 ARCH-04/05 使得自检既不能进 `app.core`（不得依赖全局域），
# 也不能从全局域反向 import `app.router`。详见 `app/global_domain/targets.py` 的模块说明。
from app.global_domain.targets import (  # noqa: E402  (置于此处以贴近使用点)
    C_TARGET_ROOT,
    M1_TARGET_PREFIX,
    M2_TARGET_ROOT,
    target_for_variable,
    variable_id_from_target,
)


@dataclass(frozen=True)
class ResolvedTarget:
    target: str
    target_class: TargetClass
    rule: TargetRule
    #: 仅 `M1.*` 有值。
    variable_id: str | None


def resolve_target(target: str) -> ResolvedTarget | None:
    """把 target 字符串解析为策略声明。**未注册一律返回 `None`**（调用方必须拒绝）。"""
    for root, target_class in (
        (M2_TARGET_ROOT, TargetClass.M2_PREFERENCE),
        (C_TARGET_ROOT, TargetClass.C_EVIDENCE),
    ):
        if target == root or target.startswith(root + "."):
            return ResolvedTarget(target, target_class, RULES[target_class], None)

    if target.startswith(M1_TARGET_PREFIX):
        variable_id = variable_id_from_target(target)
        if variable_id in M1_PREFERENCE_VARIABLES:
            target_class = TargetClass.M1_PREFERENCE
        elif variable_id in M1_TRAIT_VARIABLES:
            target_class = TargetClass.M1_TRAIT
        else:
            # 未注册的 M1 变量必须拒绝，不能默默按某种默认类型处理。
            return None
        return ResolvedTarget(target, target_class, RULES[target_class], variable_id)

    return None


def action_allowed(resolved: ResolvedTarget, action: IntentAction) -> bool:
    return action in resolved.rule.allowed_actions


# --------------------------------------------------------------------------- #
# 来源映射
# --------------------------------------------------------------------------- #

#: `trigger_type` → `RouterLog.source`。
#:
#: 刻意**派生而非入参**：来源是事实（这条写入是谁发起的），让它可被调用方指定，
#: 就等于允许把 R2 的写入记成用户直写。触发类型已经唯一决定了来源。
SOURCE_BY_TRIGGER: dict[TriggerType, ProposalSource] = {
    TriggerType.R1: ProposalSource.USER_UI,
    TriggerType.R2: ProposalSource.BASELINE,
    TriggerType.R3: ProposalSource.IDEA_LLM,
    TriggerType.R4A: ProposalSource.SESSION_SUMMARY,
    TriggerType.R4B: ProposalSource.SESSION_SUMMARY,
    TriggerType.R4C: ProposalSource.SESSION_SUMMARY,
    #: 回滚是用户在界面上直接点的（没有 LLM、没有 Session 沉淀），来源与 R1 同类。
    TriggerType.R5: ProposalSource.USER_UI,
}


def source_for_trigger(trigger: TriggerType) -> ProposalSource:
    return SOURCE_BY_TRIGGER[trigger]
