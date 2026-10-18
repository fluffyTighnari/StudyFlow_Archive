"""§5.3 判定表 —— 一个**确定性函数**，而不是一段政策散文。

设计要点：判定表以数据形式逐行列在这里，`evaluate` 只是「先查全局否决、再查表」。
**没有 if-elif 链**，因为 if-elif 链的覆盖情况无法被穷举验证，而表可以
（`tests/router/test_precheck_table.py` 会逐行断言）。

三类结论被刻意分成三个互不覆盖的字段（见 `contracts.PrecheckResult`）：

- `verdict`   —— 判定表的结论；
- `blockers`  —— 与判定正交的硬前置条件（M0 §7.3 的「≥2 条独立 Session 证据」）；
- `advisories`—— §5.4 的重做建议，**永不阻断**。
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.global_domain.enums import (
    INTENT_TRIGGERS,
    Basis,
    IntentAction,
    Precheck,
    TriggerType,
)
from app.global_domain.reads import count_independent_session_evidence
from app.router.contracts import BenchmarkAdvisory, PrecheckResult, UpdateIntent
from app.router.policies.benchmark_advisory import evaluate_benchmark_advisory
from app.router.registry import (
    ResolvedTarget,
    TargetClass,
    action_allowed,
    resolve_target,
)

# --------------------------------------------------------------------------- #
# 全局否决（先于判定表）
# --------------------------------------------------------------------------- #

REASON_UNREGISTERED_TARGET = "target 未在 UpdatePolicyRegistry 注册（M0 §5.3 / §7.3）"
REASON_ACTION_NOT_ALLOWED = "该 target 不允许此 action（M0 §7.3）"
REASON_INFERRED_PARAMETER = (
    "红线 3：`inferred` 永不写参数 —— 推断不是证据（M0 §5.3 / §13.3）"
)
REASON_UNREGISTERED_COMBINATION = (
    "该 (trigger, action, target 类, basis) 组合未在 §5.3 判定表中注册"
)


# --------------------------------------------------------------------------- #
# 判定表本体
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PrecheckRule:
    """判定表的一行，与 M0 §5.3 的表格行一一对应。"""

    row_id: str
    triggers: frozenset[TriggerType]
    actions: frozenset[IntentAction]
    target_classes: frozenset[TargetClass]
    bases: frozenset[Basis]
    verdict: Precheck
    reason: str

    def matches(
        self,
        trigger: TriggerType,
        action: IntentAction,
        target_class: TargetClass,
        basis: Basis,
    ) -> bool:
        return (
            trigger in self.triggers
            and action in self.actions
            and target_class in self.target_classes
            and basis in self.bases
        )


_PREF_TARGETS = frozenset({TargetClass.M2_PREFERENCE, TargetClass.C_EVIDENCE})
_M1_TARGETS = frozenset({TargetClass.M1_PREFERENCE, TargetClass.M1_TRAIT})
_R4_ANY = frozenset({TriggerType.R4A, TriggerType.R4B, TriggerType.R4C})


# --------------------------------------------------------------------------- #
# ★ 「直接生效」的唯一判据（用户于 P5.2 裁定，方案 A）
# --------------------------------------------------------------------------- #

#: 一条 intent 可以直接生效 ⟺ **用户在那个手势里已经自己给出或确认了这个值**。
#:
#: 这是 R4 判定表里那一列「是否直接生效」的**唯一**依据，取代原先那句
#: 「以 M7 §7.2 为准」—— 那是一句没有理由的理由，而它掩盖了 M7 §7.2 第 170 行
#: 的自相矛盾（前半句「沉淀必确认」，后半句立刻给 R4c 开了一个没有理由的例外）。
#:
#: **另一个候选依据是「证据强度」（§5 的 R4 表：R4a 强 / R4b 中 / R4c 弱），
#: 用户在 P5.2 明确否决了它** —— 它方向相反（最弱的 R4c 反而最宽松），
#: 而且那张表自己声明「证据强度只影响 LLM 的 confidence 标注，不改变路由动作」。
#:
#: 注意这个判据有一个**边界**：确认动作只覆盖**当时摆在用户面前的东西**。
#: - R2 的按钮说的是「确认写入基线」，所以它覆盖 `measured`，**不覆盖**
#:   顺手记的一条 `experienced` 体感（`R2 + experienced` 至今未注册，见缺口表）；
#: - R4c 的列表是 **stage 里的本次发现**（即 `experienced`），所以他勾选的确认
#:   **不覆盖** `inferred` —— LLM 的推断不在 stage 里，用户没看见它。
#:   于是 `R4c + inferred` 仍是「需确认」（与 M0 §11 A11 一致）。
#:
#: `tests/router/test_precheck_table.py` 会把这张判定表**逐行**与这个集合对齐：
#: 新增一行而没在这里表态，测试就会红。
USER_CONFIRMED_GESTURES: frozenset[tuple[TriggerType, IntentAction, Basis]] = frozenset(
    {
        # 用户在 UI 偏好栏里自己写下这句话。
        (TriggerType.R1, IntentAction.ADD_PREFERENCE, Basis.STATED),
        # 用户在 Benchmark 末端点「确认写入基线」——那次测绘的结果是他自己确认的。
        (TriggerType.R2, IntentAction.UPDATE_PARAMETER, Basis.MEASURED),
        # 用户在对话框里明确说出自己的偏好（不是「我观察到…」）。
        (TriggerType.R3, IntentAction.ADD_PREFERENCE, Basis.STATED),
        # 用户从 stage 勾选条目后点「沉淀本次发现」——勾选就是确认动作。
        # 两种 action 都覆盖：同一个手势确认的是**这些条目**，不是某个 action。
        # （R2 也是这样：按钮确认的是那次测绘，与写的是偏好还是参数无关。）
        (TriggerType.R4C, IntentAction.ADD_PREFERENCE, Basis.EXPERIENCED),
        (TriggerType.R4C, IntentAction.UPDATE_PARAMETER, Basis.EXPERIENCED),
    }
)


def is_user_confirmed_gesture(
    trigger: TriggerType, action: IntentAction, basis: Basis
) -> bool:
    """这个组合是否「用户在那个手势里已经自己给出或确认了这个值」。"""
    return (trigger, action, basis) in USER_CONFIRMED_GESTURES


PRECHECK_TABLE: tuple[PrecheckRule, ...] = (
    PrecheckRule(
        "R1.add_preference",
        frozenset({TriggerType.R1}),
        frozenset({IntentAction.ADD_PREFERENCE}),
        _PREF_TARGETS,
        frozenset({Basis.STATED}),
        Precheck.AUTO_APPLY,
        "R1 用户 UI 直写偏好：用户声明即事实（路由层 §5）",
    ),
    PrecheckRule(
        "R2.update_parameter",
        frozenset({TriggerType.R2}),
        frozenset({IntentAction.UPDATE_PARAMETER}),
        _M1_TARGETS,
        frozenset({Basis.MEASURED}),
        Precheck.AUTO_APPLY,
        "R2：Benchmark 流程末端的「确认写入基线」按钮即确认动作，记 user_confirmed=true"
        "（M0 §11 A8）",
    ),
    PrecheckRule(
        "R3.add_preference",
        frozenset({TriggerType.R3}),
        frozenset({IntentAction.ADD_PREFERENCE}),
        _PREF_TARGETS,
        frozenset({Basis.STATED}),
        Precheck.AUTO_APPLY,
        "R3 调节 LLM UI（用户已确信）：加偏好直接生效（路由层 §5）",
    ),
    PrecheckRule(
        "R3.update_parameter",
        frozenset({TriggerType.R3}),
        frozenset({IntentAction.UPDATE_PARAMETER}),
        _M1_TARGETS,
        frozenset({Basis.MEASURED, Basis.EXPERIENCED}),
        Precheck.NEEDS_CONFIRMATION,
        "R3 更新参数需用户确认（路由层 §5）",
    ),
    PrecheckRule(
        "R3.add_preference.experienced",
        frozenset({TriggerType.R3}),
        frozenset({IntentAction.ADD_PREFERENCE}),
        _PREF_TARGETS,
        frozenset({Basis.EXPERIENCED}),
        Precheck.NEEDS_CONFIRMATION,
        "R3 的「加偏好直接生效」以**用户自己声明**为前提；experienced 只是观察，"
        "不构成「用户已确信」，故需确认（M0 §11 A12）",
    ),
    PrecheckRule(
        "R4a.add_preference",
        frozenset({TriggerType.R4A}),
        frozenset({IntentAction.ADD_PREFERENCE}),
        _PREF_TARGETS,
        frozenset({Basis.EXPERIENCED}),
        Precheck.NEEDS_CONFIRMATION,
        "R4a Session 完成沉淀：**系统在会话结束时自动产出，用户还没看过** → 需确认"
        "（判据见 `USER_CONFIRMED_GESTURES`）",
    ),
    PrecheckRule(
        "R4a.update_parameter",
        frozenset({TriggerType.R4A}),
        frozenset({IntentAction.UPDATE_PARAMETER}),
        _M1_TARGETS,
        frozenset({Basis.EXPERIENCED}),
        Precheck.NEEDS_CONFIRMATION,
        "R4a 同上：自动产出、用户未过目 → 需确认",
    ),
    PrecheckRule(
        "R4b.add_preference",
        frozenset({TriggerType.R4B}),
        frozenset({IntentAction.ADD_PREFERENCE}),
        _PREF_TARGETS,
        frozenset({Basis.EXPERIENCED}),
        Precheck.NEEDS_CONFIRMATION,
        "R4b 里程碑沉淀：同 R4a（自动产出、用户未过目）",
    ),
    PrecheckRule(
        "R4b.update_parameter",
        frozenset({TriggerType.R4B}),
        frozenset({IntentAction.UPDATE_PARAMETER}),
        _M1_TARGETS,
        frozenset({Basis.EXPERIENCED}),
        Precheck.NEEDS_CONFIRMATION,
        "R4b 里程碑沉淀：同 R4a（自动产出、用户未过目）",
    ),
    PrecheckRule(
        "R4c.add_preference",
        frozenset({TriggerType.R4C}),
        frozenset({IntentAction.ADD_PREFERENCE}),
        _PREF_TARGETS,
        frozenset({Basis.EXPERIENCED}),
        Precheck.AUTO_APPLY,
        "R4c 手动沉淀：**勾选就是确认动作**（用户裁定，P5.2 方案 A），"
        "与 R2 的「确认写入基线」按钮同理 —— 见 `USER_CONFIRMED_GESTURES`",
    ),
    PrecheckRule(
        "R4c.update_parameter",
        frozenset({TriggerType.R4C}),
        frozenset({IntentAction.UPDATE_PARAMETER}),
        _M1_TARGETS,
        frozenset({Basis.EXPERIENCED}),
        Precheck.AUTO_APPLY,
        "R4c 手动沉淀：同一个手势确认的是**这些条目**，与 action 无关 —— "
        "原先这里写「更新参数仍需用户确认（M0 §11 A6）」是错的，"
        "因为它等于说「同一个点击对偏好是确认、对参数不是」，而给不出理由。"
        "注意 trait 变量仍被 §7.3 的「≥2 条独立 Session 证据」硬前置条件拦住"
        "（那是 blocker，与判定正交，因此 `AUTO_APPLY + 有 blocker` 依然是待确认）",
    ),
    # ↓ 补缺口 A11：§5.3 未覆盖 `inferred` + add_preference，而路由层 §4.2 第 2 条
    #   要求「inferred 的建议默认降级为 no_change 候选（除非用户显式确认）」。
    #   故判定为 needs_confirmation 而**不是** must_reject —— 红线 3 只禁止
    #   inferred 写**参数**；直接丢掉 LLM 的推断型偏好建议会让它连被看见的机会都没有。
    PrecheckRule(
        "R3.add_preference.inferred",
        frozenset({TriggerType.R3}),
        frozenset({IntentAction.ADD_PREFERENCE}),
        _PREF_TARGETS,
        frozenset({Basis.INFERRED}),
        Precheck.NEEDS_CONFIRMATION,
        "inferred 偏好降级为候选，需用户显式确认（路由层 §4.2 第 2 条；M0 §11 A11）",
    ),
    PrecheckRule(
        "R4.add_preference.inferred",
        _R4_ANY,
        frozenset({IntentAction.ADD_PREFERENCE}),
        _PREF_TARGETS,
        frozenset({Basis.INFERRED}),
        Precheck.NEEDS_CONFIRMATION,
        "inferred 偏好降级为候选，需用户显式确认（路由层 §4.2 第 2 条；M0 §11 A11）。"
        "**对 R4c 也成立**，理由是确认动作只覆盖当时摆在他面前的东西："
        "R4c 的列表是 stage 里的本次发现（即 experienced），"
        "而 LLM 的推断不在 stage 里 —— 用户没看见它，所以那个点击没有确认它",
    ),
)


def lookup_rule(
    trigger: TriggerType,
    action: IntentAction,
    target_class: TargetClass,
    basis: Basis,
) -> PrecheckRule | None:
    """返回第一条匹配的行。表中无重叠匹配，故「第一条」与「唯一一条」等价。"""
    for rule in PRECHECK_TABLE:
        if rule.matches(trigger, action, target_class, basis):
            return rule
    return None


def overlapping_rules() -> list[tuple[str, str]]:
    """检测判定表内的重叠 —— 供测试断言表本身无歧义。"""
    overlaps: list[tuple[str, str]] = []
    for index, left in enumerate(PRECHECK_TABLE):
        for right in PRECHECK_TABLE[index + 1 :]:
            if (
                left.triggers & right.triggers
                and left.actions & right.actions
                and left.target_classes & right.target_classes
                and left.bases & right.bases
            ):
                overlaps.append((left.row_id, right.row_id))
    return overlaps


# --------------------------------------------------------------------------- #
# 评估
# --------------------------------------------------------------------------- #


def validate_value(resolved: ResolvedTarget, intent: UpdateIntent):
    """按 Registry 声明的结构校验参数。返回 `(值对象, 错误信息)`。"""
    schema = resolved.rule.value_schema
    if schema is None:
        return None, None
    if intent.action is IntentAction.NO_CHANGE:
        return None, None
    try:
        return schema.model_validate(intent.value), None
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "(根)"
        return None, f"value 结构不合法：{location} —— {first['msg']}"


def evaluate(
    session: Session, trigger: TriggerType, intent: UpdateIntent
) -> PrecheckResult:
    """对单条 intent 做确定性判定。**不写任何东西。**"""
    if trigger not in INTENT_TRIGGERS:
        # R5（回滚）不产生 intent，因此没有「判定」这回事。
        # 让它走到这里只可能是编程错误 —— 静默套用某条 R1 规则会更糟。
        raise ValueError(
            f"{trigger} 不产出 UpdateIntent，不走 §5.3 判定表；"
            "回滚请用 Router.rollback()（M0 §7.1）"
        )

    resolved = resolve_target(intent.target)

    # ① 未注册 target —— 最优先，因为后续所有判断都依赖它。
    if resolved is None:
        return PrecheckResult(
            intent_id=intent.intent_id,
            verdict=Precheck.MUST_REJECT,
            reason=REASON_UNREGISTERED_TARGET,
        )

    # ② (target, action) 组合是否被允许。这一条同时覆盖了 §5.3 的
    #    「update_parameter 非 M1.*」与「未注册 target」两行。
    if not action_allowed(resolved, intent.action):
        return PrecheckResult(
            intent_id=intent.intent_id,
            verdict=Precheck.MUST_REJECT,
            reason=f"{REASON_ACTION_NOT_ALLOWED}：target={intent.target}，action={intent.action}",
        )

    # ③ 红线：inferred 永不写参数。
    if intent.action is IntentAction.UPDATE_PARAMETER and intent.basis is Basis.INFERRED:
        return PrecheckResult(
            intent_id=intent.intent_id,
            verdict=Precheck.MUST_REJECT,
            reason=REASON_INFERRED_PARAMETER,
        )

    # ④ 参数结构校验：挡在提案落库之前，而不是等 commit 才炸出一半。
    value, value_error = validate_value(resolved, intent)
    if value_error is not None:
        return PrecheckResult(
            intent_id=intent.intent_id,
            verdict=Precheck.MUST_REJECT,
            reason=value_error,
        )

    # ⑤ no_change 仅写日志（target 必须已注册）。
    if intent.action is IntentAction.NO_CHANGE:
        return PrecheckResult(
            intent_id=intent.intent_id,
            verdict=Precheck.AUTO_APPLY,
            reason="no_change：仅写日志，不产生任何全局变更（M0 §5.3）",
        )

    # ⑥ 判定表。
    rule = lookup_rule(trigger, intent.action, resolved.target_class, intent.basis)
    if rule is None:
        return PrecheckResult(
            intent_id=intent.intent_id,
            verdict=Precheck.MUST_REJECT,
            reason=(
                f"{REASON_UNREGISTERED_COMBINATION}："
                f"trigger={trigger}, action={intent.action}, "
                f"target 类={resolved.target_class}, basis={intent.basis}"
            ),
        )

    return PrecheckResult(
        intent_id=intent.intent_id,
        verdict=rule.verdict,
        reason=rule.reason,
        advisories=_advisories(session, trigger, resolved),
        blockers=_blockers(session, trigger, resolved, intent, value),
    )


def _advisories(
    session: Session, trigger: TriggerType, resolved: ResolvedTarget
) -> tuple[BenchmarkAdvisory, ...]:
    """§5.4 重做建议。**只对 R2 生效** —— 只有「重做基准测试」才有重做代价。

    R1 直写、R3/R4 增量沉淀都不是重测，不该弹「本季度已重做 N 次」。
    """
    if trigger is not TriggerType.R2 or resolved.variable_id is None:
        return ()
    return (evaluate_benchmark_advisory(session, resolved.variable_id),)


def _blockers(
    session: Session,
    trigger: TriggerType,
    resolved: ResolvedTarget,
    intent: UpdateIntent,
    value,
) -> tuple[str, ...]:
    """与判定正交的硬前置条件。

    目前只有一条：M1 **trait** 变量以 `experienced` 为依据写入时，
    M0 §7.3 要求「≥2 条独立 Session 证据**或**完整重测」。完整重测即 R2/measured，
    所以这里只在 experienced 路径上检查。
    """
    if (
        resolved.target_class is not TargetClass.M1_TRAIT
        or intent.action is not IntentAction.UPDATE_PARAMETER
        or intent.basis is not Basis.EXPERIENCED
        or value is None
        or resolved.variable_id is None
    ):
        return ()

    count = count_independent_session_evidence(
        session, resolved.variable_id, value.channel
    )
    if count >= 2:
        return ()
    return (
        f"「{resolved.variable_id}」是 trait 变量，以 experienced 为依据写入需要"
        f"≥2 条独立 Session 证据，或走完整重测（R2/measured）。"
        f"当前 {value.channel} 通道只有 {count} 条独立 Session 证据（M0 §7.3）。",
    )
