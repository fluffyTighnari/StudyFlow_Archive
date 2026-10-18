"""§5.3 判定表 —— 逐坐标穷举 + 全局否决的优先级。

这里不测「策略是否好用」，只测**判定表本身没有歧义、没有不可达行、
每个坐标的结论与文档一致**。判定表是整个治理层的宪法条文，
它出错的后果是无声的：某条 intent 静默走了另一行的结论。
"""

from __future__ import annotations

import pytest

from app.agent.handoff import ALLOWED_TRIGGERS
from app.global_domain.enums import (
    INTENT_TRIGGERS,
    Basis,
    IntentAction,
    Precheck,
    TriggerType,
)
from app.router import precheck as precheck_module
from tests.router.helpers import c_value, evidence_intent, intent, m1_intent, pref_intent


def test_table_has_no_overlapping_rows() -> None:
    """任意两行不得在任何一个 (trigger, action, target 类, basis) 坐标上同时命中。"""
    assert precheck_module.overlapping_rules() == []


def test_every_row_is_the_unique_match_for_all_its_own_coordinates() -> None:
    """穷举每行的全部坐标组合，断言命中的**就是这一行**。

    只测「能找到某一行」是不够的：那正是重叠错误的表现形态 ——
    行存在、也有结论，只是结论来自另一行。
    """
    for rule in precheck_module.PRECHECK_TABLE:
        for trigger in rule.triggers:
            for action in rule.actions:
                for target_class in rule.target_classes:
                    for basis in rule.bases:
                        found = precheck_module.lookup_rule(
                            trigger, action, target_class, basis
                        )
                        assert found is rule, (
                            f"{rule.row_id} 的坐标 "
                            f"({trigger}, {action}, {target_class}, {basis}) "
                            f"被 {found.row_id if found else '无'} 抢先命中"
                        )


def test_every_row_id_is_unique() -> None:
    ids = [rule.row_id for rule in precheck_module.PRECHECK_TABLE]
    assert len(ids) == len(set(ids))


def test_table_covers_every_intent_driven_trigger() -> None:
    covered = {trigger for rule in precheck_module.PRECHECK_TABLE for trigger in rule.triggers}
    assert covered == set(INTENT_TRIGGERS)


def test_table_deliberately_excludes_the_rollback_trigger() -> None:
    """R5（回滚）不产出 Intent，因此**不该**在判定表里有任何一行。

    如果哪天有人给它加了一行，那说明「回滚」被重新定义成了一种 intent 更新 ——
    而那会同时改掉 `RollbackRequest` 的形状与 §7.3 的证据门槛语义。
    这一步不该由一次不起眼的表编辑悄悄完成。
    """
    covered = {trigger for rule in precheck_module.PRECHECK_TABLE for trigger in rule.triggers}
    assert TriggerType.R5 not in covered
    assert TriggerType.R5 not in INTENT_TRIGGERS


def test_evaluate_refuses_the_rollback_trigger(router) -> None:
    """误用必须**报错**，而不是静默套用某条 R1 规则。"""
    with pytest.raises(ValueError, match="不走 §5.3 判定表"):
        router.precheck(TriggerType.R5, pref_intent(basis="stated"))


# --------------------------------------------------------------------------- #
# ★ 「直接生效」的唯一判据（用户 P5.2 裁定，方案 A）
# --------------------------------------------------------------------------- #


def test_every_auto_apply_row_is_exactly_a_user_confirmed_gesture() -> None:
    """判定表的「直接生效」列必须**逐坐标**等于 `USER_CONFIRMED_GESTURES`。

    这是整张判定表的**唯一判据**（用户 P5.2 裁定，方案 A：确认动作是否已经发生）。
    断言两个方向，因为它们会以不同方式坏掉：

    - **AUTO_APPLY ⊄ 判据** → 出现了一条「没人确认过就直接写」的行，
      那正是红线 3 要防的事（系统的产出绕过用户）；
    - **判据 ⊄ AUTO_APPLY** → 用户明明确认了，系统却还要再问一次 ——
      这正是 P5.2 修掉的 `R4c.update_parameter`（同一个点击对偏好算确认、
      对参数不算，且给不出理由）。

    只断言其中一个方向的话，另一个方向的漂移不会被发现。
    """
    auto_coords = {
        (trigger, action, basis)
        for rule in precheck_module.PRECHECK_TABLE
        if rule.verdict is Precheck.AUTO_APPLY
        for trigger in rule.triggers
        for action in rule.actions
        for basis in rule.bases
    }
    assert auto_coords == set(precheck_module.USER_CONFIRMED_GESTURES)


def test_no_auto_apply_row_lacks_a_trigger_that_is_a_user_act() -> None:
    """「直接生效」的行，其 trigger 必须是一个**用户能做出来的手势**。

    R4a/R4b 是系统在会话/里程碑结束时自动触发的 —— 它们**永远不会**是
    「用户已经确认过」的时刻。若哪天有人把 R4a 改成直接生效，
    那条测试会红，并逼出「用户到底在哪一刻确认的」这个问题。
    """
    system_triggers = {TriggerType.R4A, TriggerType.R4B}
    for rule in precheck_module.PRECHECK_TABLE:
        if rule.verdict is not Precheck.AUTO_APPLY:
            continue
        overlap = rule.triggers & system_triggers
        assert not overlap, (
            f"{rule.row_id} 直接生效，但它的触发时机 {overlap} 是系统自动产生的，"
            f"用户不可能在那之前确认过"
        )


def test_the_inferred_candidate_still_needs_confirmation_on_every_trigger() -> None:
    """`inferred` 的**确认动作边界**：R4c 的点击只覆盖 stage 里的东西。

    R4c 的列表是 **stage 里的本次发现**（`experienced`），而 LLM 的 `inferred`
    推断不在 stage 里 —— 用户没看见它，所以那个点击没有确认它。
    因此 `R4c + inferred` 与 R4a/R4b 一样需确认（与 A11 一致）。

    这条测试防的是「把 R4c 的勾选当成一张万能确认券」：
    那会让一个 LLM 凭空猜出来的偏好因为用户勾了别的东西而静默生效。
    """
    for rule in precheck_module.PRECHECK_TABLE:
        if Basis.INFERRED not in rule.bases:
            continue
        assert rule.verdict is Precheck.NEEDS_CONFIRMATION, (
            f"{rule.row_id} 让 inferred 直接生效了"
        )
    assert not any(
        basis is Basis.INFERRED
        for _, _, basis in precheck_module.USER_CONFIRMED_GESTURES
    )


# --------------------------------------------------------------------------- #
# 逐行结论
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("trigger", "build", "expected"),
    [
        # R1：用户 UI 直写偏好。
        (TriggerType.R1, lambda: pref_intent(basis="stated"), Precheck.AUTO_APPLY),
        # R2：Benchmark 末端按钮即确认动作（A8）。
        (TriggerType.R2, lambda: m1_intent(basis="measured"), Precheck.AUTO_APPLY),
        # R3：加偏好直接生效；更新参数需确认。
        (TriggerType.R3, lambda: pref_intent(basis="stated"), Precheck.AUTO_APPLY),
        (
            TriggerType.R3,
            lambda: m1_intent(basis="measured"),
            Precheck.NEEDS_CONFIRMATION,
        ),
        (
            TriggerType.R3,
            lambda: m1_intent(basis="experienced"),
            Precheck.NEEDS_CONFIRMATION,
        ),
        # R4a：写偏好与参数都需确认。
        (
            TriggerType.R4A,
            lambda: pref_intent(basis="experienced"),
            Precheck.NEEDS_CONFIRMATION,
        ),
        (
            TriggerType.R4A,
            lambda: m1_intent(basis="experienced"),
            Precheck.NEEDS_CONFIRMATION,
        ),
        # R4b：同 R4a。
        (
            TriggerType.R4B,
            lambda: pref_intent(basis="experienced"),
            Precheck.NEEDS_CONFIRMATION,
        ),
        (
            TriggerType.R4B,
            lambda: m1_intent(basis="experienced"),
            Precheck.NEEDS_CONFIRMATION,
        ),
        # R4c：**勾选就是确认动作**，因此加偏好与更新参数都直接生效
        # （用户 P5.2 裁定，方案 A；原先更新参数需确认，那是对同一个点击的两套说法）。
        (
            TriggerType.R4C,
            lambda: pref_intent(basis="experienced"),
            Precheck.AUTO_APPLY,
        ),
        (
            TriggerType.R4C,
            lambda: m1_intent(basis="experienced"),
            Precheck.AUTO_APPLY,
        ),
        # A11 补缺口：inferred 偏好降级为候选（需确认），而不是直接丢弃。
        (
            TriggerType.R3,
            lambda: pref_intent(basis="inferred"),
            Precheck.NEEDS_CONFIRMATION,
        ),
        (
            TriggerType.R4A,
            lambda: pref_intent(basis="inferred"),
            Precheck.NEEDS_CONFIRMATION,
        ),
        (
            TriggerType.R4B,
            lambda: pref_intent(basis="inferred"),
            Precheck.NEEDS_CONFIRMATION,
        ),
        (
            TriggerType.R4C,
            lambda: pref_intent(basis="inferred"),
            Precheck.NEEDS_CONFIRMATION,
        ),
        # C.evidence 与 M2.preferences 走同一张行（同属「偏好类 target」）。
        (
            TriggerType.R3,
            lambda: evidence_intent(basis="experienced"),
            Precheck.NEEDS_CONFIRMATION,
        ),
        (
            TriggerType.R4C,
            lambda: evidence_intent(basis="experienced"),
            Precheck.AUTO_APPLY,
        ),
    ],
)
def test_precheck_verdict_per_row(router, trigger, build, expected) -> None:
    result = router.precheck(trigger, build())
    assert result.verdict is expected, result.reason


# --------------------------------------------------------------------------- #
# 全局否决（先于判定表）
# --------------------------------------------------------------------------- #


def test_unregistered_target_is_rejected(router) -> None:
    result = router.precheck(
        TriggerType.R2, intent(target="M1.V-NOPE", value={}, basis="measured")
    )
    assert result.verdict is Precheck.MUST_REJECT
    assert "UpdatePolicyRegistry" in result.reason


def test_unregistered_non_m1_namespace_is_rejected(router) -> None:
    result = router.precheck(
        TriggerType.R4A,
        intent(
            action="add_preference",
            target="M9.something",
            value=pref_intent().value,
            basis="experienced",
        ),
    )
    assert result.verdict is Precheck.MUST_REJECT


def test_update_parameter_on_non_m1_target_is_rejected(router) -> None:
    """§5.3「任意 | update_parameter | 非 M1.* | 任意 | must_reject」。

    M2/C 只接受 add_preference：它们没有「参数」这个概念，
    允许 update_parameter 会让它们长出可被数值覆盖的字段，
    而那一层覆盖没有版本化保护。
    """
    result = router.precheck(
        TriggerType.R1,
        intent(
            action="update_parameter",
            target="M2.preferences",
            value={"statement": "x"},
            basis="stated",
        ),
    )
    assert result.verdict is Precheck.MUST_REJECT
    assert "不允许此 action" in result.reason


def test_inferred_never_writes_parameter(router) -> None:
    """红线 3：推断不是证据，永不写参数。"""
    result = router.precheck(TriggerType.R3, m1_intent(basis="inferred"))
    assert result.verdict is Precheck.MUST_REJECT
    assert "inferred" in result.reason

    result_r2 = router.precheck(TriggerType.R2, m1_intent(basis="inferred"))
    assert result_r2.verdict is Precheck.MUST_REJECT


def test_inferred_preference_is_downgraded_not_dropped(router) -> None:
    """A11：inferred **偏好**是被降级为候选，而不是被拒绝。

    这两者的差别是「用户还有机会看到并同意」与「建议直接消失」。
    """
    result = router.precheck(TriggerType.R4A, pref_intent(basis="inferred"))
    assert result.verdict is Precheck.NEEDS_CONFIRMATION


def test_no_change_is_logged_only(router) -> None:
    result = router.precheck(
        TriggerType.R4A,
        intent(action="no_change", target="M1.V-SE", basis="experienced"),
    )
    assert result.verdict is Precheck.AUTO_APPLY
    assert "no_change" in result.reason


def test_no_change_on_unregistered_target_is_still_rejected(router) -> None:
    """优先级：未注册 target 先于 no_change。

    「什么都没改所以放行」看似无害，但它会让 RouterLog 里出现一条
    target 无法解析的记录 —— 审计断链正是 I1 要防的东西。
    """
    result = router.precheck(
        TriggerType.R4A,
        intent(action="no_change", target="M1.V-NOPE", basis="experienced"),
    )
    assert result.verdict is Precheck.MUST_REJECT


def test_no_change_without_value_is_accepted(router) -> None:
    """no_change 不承载参数，不该因缺 value 被判非法。"""
    result = router.precheck(
        TriggerType.R4A, intent(action="no_change", target="M2.preferences", basis="experienced")
    )
    assert result.verdict is Precheck.AUTO_APPLY


# --------------------------------------------------------------------------- #
# 参数结构校验（在提案落库之前挡住）
# --------------------------------------------------------------------------- #


def test_missing_required_field_in_value_is_rejected(router) -> None:
    bad = intent(
        target="M1.V-SE", value={"channel": "measured"}, basis="measured"
    )
    result = router.precheck(TriggerType.R2, bad)
    assert result.verdict is Precheck.MUST_REJECT
    assert "value 结构不合法" in result.reason
    assert "completeness" in result.reason


def test_unknown_field_in_value_is_rejected(router) -> None:
    """多传一个字段必须报错。

    LLM 把 `channel` 写成 `evidence_channel` 时，宽松解析会让 channel 缺省、
    数据落进错误的通道 —— 而错误要等到几周后回看基线时才会被发现。
    """
    bad = m1_intent()
    bad = bad.model_copy(
        update={"value": {**bad.value, "evidence_channel": "measured"}}
    )
    result = router.precheck(TriggerType.R2, bad)
    assert result.verdict is Precheck.MUST_REJECT
    assert "evidence_channel" in result.reason


def test_score_out_of_range_is_rejected(router) -> None:
    result = router.precheck(TriggerType.R2, m1_intent(score=11.0))
    assert result.verdict is Precheck.MUST_REJECT


def test_c_evidence_without_condition_is_rejected(router) -> None:
    """M2 §4.4 第 3 条：无适用条件的陈述是偏好，不是 C 证据。"""
    bad = intent(
        action="add_preference",
        target="C.evidence",
        value=c_value(condition="   "),
        basis="stated",
    )
    result = router.precheck(TriggerType.R1, bad)
    assert result.verdict is Precheck.MUST_REJECT
    assert "condition" in result.reason


def test_blank_preference_statement_is_rejected(router) -> None:
    bad = pref_intent(statement="  ")
    result = router.precheck(TriggerType.R1, bad)
    assert result.verdict is Precheck.MUST_REJECT


def test_precheck_never_writes(router, seeded) -> None:
    """`precheck` 必须是纯判定：跑一遍不留下任何行。"""
    from app.global_domain.reads import count_router_logs, list_proposals

    router.precheck(TriggerType.R2, m1_intent())
    router.precheck(TriggerType.R4A, pref_intent(basis="experienced"))

    assert count_router_logs(seeded) == 0
    assert list_proposals(seeded) == []


# --------------------------------------------------------------------------- #
# ★ 判定表 × Agent 白名单：两个地方各自都对，错误只在**组合**上
# --------------------------------------------------------------------------- #

#: 判定表里**靠一次 UI 手势**充当确认动作的触发规则。
#:
#: 它们的「直接生效」理由就是那个动作本身：「用户自己写下这句话」（R1）、
#: 「用户点了确认写入基线」（R2）、「用户从 stage 勾选后点沉淀」（R4c）。
#:
#: 因此只要 **Agent 能凭空声称其中一个**，那个理由就不成立 ——
#: 不是因为判定表写错了，而是因为判定表假设了一件没人去核实的事。
UI_GESTURE_TRIGGERS = frozenset(
    {TriggerType.R1, TriggerType.R2, TriggerType.R4C}
)

#: Agent 能声称的触发规则，取自 `handoff.ALLOWED_TRIGGERS`（**唯一来源**，不抄一遍）。
AGENT_TRIGGERS: frozenset[TriggerType] = frozenset(
    TriggerType(name) for name in ALLOWED_TRIGGERS
)

#: 三个 basis 与两个 action 面。`inferred` 被红线 3 单独拦着，也一起过一遍 ——
#: 穷举的意义就在于**不让任何一格靠推理通过**。
_ALL_BASES = (Basis.STATED, Basis.MEASURED, Basis.EXPERIENCED, Basis.INFERRED)


def _agent_reachable_auto_apply(
    extra: frozenset[TriggerType] | None = None,
) -> set[tuple[TriggerType, str, Basis]]:
    """Agent 无手势就能拿到 `AUTO_APPLY` 的坐标集合。

    `extra` 用来问**反事实**的问题（"假如某个触发规则在白名单里会怎样"）——
    上一条测试靠它证明自己不是空过。
    """
    allowed = AGENT_TRIGGERS | (extra or frozenset())
    reachable: set[tuple[TriggerType, str, Basis]] = set()
    for rule in precheck_module.PRECHECK_TABLE:
        if rule.verdict is not Precheck.AUTO_APPLY:
            continue
        for trigger in rule.triggers & allowed:
            for action in rule.actions:
                for basis in rule.bases:
                    reachable.add((trigger, action.value, basis))
    return reachable


def test_agent_cannot_claim_a_ui_gesture_it_never_received() -> None:
    """Agent 拿不到手势，就不该能靠**声称有手势**换来「直接生效」。

    这是 `handoff` 排除 R1/R2 的**同一条理由**，只是当时只排到了 R2：

    > 「R2 + measured 在判定表里是自动生效的，等于绕过用户确认。
    >   这是本文件守着的最重要的一条。」

    R4c 与 R2 在这里没有区别 —— 它的自动生效同样建立在一次 UI 动作上，
    而 Agent 同样可以凭空声称。区别只是 R2 改的是 M1 认知变量、
    R4c 改的是偏好/证据/参数，而**参数那一格它也能改**。

    **这条测试曾经是红的**（`tools/triggerscan` 穷举出来的 5 格，
    其中 `R4c + update_parameter + experienced` 直接写 M1 变量）。
    P6+.5 用户裁定「甲案」后 `R4c` 已从 `ALLOWED_TRIGGERS` 移除，因此现在通过。
    它的兄弟测试在下面，负责证明它**不是空过**。
    """
    offenders = sorted(
        f"{trigger.value}+{action}+{basis.value}"
        for trigger, action, basis in _agent_reachable_auto_apply()
        if trigger in UI_GESTURE_TRIGGERS
    )
    assert not offenders, (
        "Agent 无需任何用户手势就能让这些组合**直接生效**："
        + "、".join(offenders)
        + "。它们的自动生效理由是一次 UI 手势，而 Agent 只是**声称**了它。"
    )


def test_the_gesture_check_would_actually_catch_r4c() -> None:
    """★ 上一条测试「通过」这件事本身必须有证据，否则它什么都没证明。

    一条断言"集合里没有坏东西"的测试，在集合**恒为空**时也会通过 ——
    而那正是它在缺陷被修好之后的处境。所以这里把 R4c 放回白名单，
    要求探测器**精确报出**它当初抓到的那两格。

    这也顺便钉住了当初的缺陷形状：`add_preference` 与 **`update_parameter`**
    —— 后一格就是"直接写 M1 认知变量"那一格。

    > **这里有一个容易数错的细节**（我第一版就数错了）：判定表里**没有**
    > `add_evidence` 这个 action。C 个性化证据是靠 `add_preference`
    > 配 `target="C.evidence"` 写进去的（`IntentAction` 只有三个取值）。
    > 所以 `tools/triggerscan` 报的"5 个组合"是按**目标面**数的
    > （偏好 / M1 变量 / C 证据），而 (trigger, action, basis) 坐标
    > **只有 2 格**。两个数都对，但它们不是同一件事 —— 混起来就会
    > 得出"少堵了一格"的假结论。
    """
    reachable = {
        (trigger.value, action, basis.value)
        for trigger, action, basis in _agent_reachable_auto_apply(extra={TriggerType.R4C})
        if trigger in UI_GESTURE_TRIGGERS
    }
    assert reachable == {
        ("R4c", "add_preference", "experienced"),
        ("R4c", "update_parameter", "experienced"),
    }, "探测器没牙：它抓不出当初那个缺陷"

    # 并且确认「C 证据也走 add_preference」这个事实没变 ——
    # 上一条注释里的说法要是错了，这个测试会红。
    assert {action.value for action in IntentAction} == {
        "add_preference",
        "update_parameter",
        "no_change",
    }


def test_the_agent_whitelist_excludes_every_ui_direct_write_trigger() -> None:
    """R1/R2/R4c 被排除是刻意的，且要一直排除 —— 这是 `handoff` 存在的第一理由。

    三者是同一类东西：**"用户已经做过的某个动作"**。
    模型说"他做过了"不构成他做过，所以模型没有资格声称它们。
    """
    assert TriggerType.R1 not in AGENT_TRIGGERS
    assert TriggerType.R2 not in AGENT_TRIGGERS
    assert TriggerType.R4C not in AGENT_TRIGGERS
    assert AGENT_TRIGGERS == {
        TriggerType.R3,
        TriggerType.R4A,
        TriggerType.R4B,
    }


def test_r4c_still_works_on_the_users_own_path() -> None:
    """★ 收窄的是**模型声称它的资格**，不是 R4c 这条规则本身。

    用户从 stage 勾选后点沉淀（`POST /api/sessions/{id}/sediment`）走的仍然是 R4c，
    而且那条通路上的勾选**真的发生过** —— `stage_item_ids` 由界面给出。
    如果哪天有人"顺手把 R4c 从判定表里删掉"，这条会红。
    """
    assert TriggerType.R4C in precheck_module._R4_ANY
    assert (
        TriggerType.R4C,
        IntentAction.ADD_PREFERENCE,
        Basis.EXPERIENCED,
    ) in precheck_module.USER_CONFIRMED_GESTURES
