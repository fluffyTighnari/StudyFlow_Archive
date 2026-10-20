"""P7 语义格：偏好 / 证据 / 假说 的 2×2 判定。

## 这个文件要证明的核心断言只有一条

**「他说想试」与「他试过」不再是同一个判断。**

P7 之前，判定表里 `_PREF_TARGETS = {M2_PREFERENCE, C_EVIDENCE}` 一行管两件
性质不同的东西，于是有两格语义不通的组合被放行：

- `C.evidence` + `stated`    → **自动生效**：一条"他只是说了"的「证据」
- `C.evidence` + `inferred`  → 需确认：一条"我推断出来的"「证据」

而这两样会被后续装配（M2 §2.2 SP2）当成**已验证的方法**使用。
也就是说：一句从未发生过的做法可以自动进库，然后在规划时被当成经验。

把这些格子钉住，比在文档里写一句"证据必须带条件"有用 ——
文档不会被 CI 读。

    2×2 语义格
    ┌─────────────┬─────────────────┬───────────────────────┐
    │             │ stated          │ experienced           │
    ├─────────────┼─────────────────┼───────────────────────┤
    │ 偏好(无条件) │ M2.preferences  │ M2.preferences        │
    │ 做法(带条件) │ C.hypothesis    │ C.evidence            │
    └─────────────┴─────────────────┴───────────────────────┘
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.global_domain.enums import Basis, IntentAction, Precheck, TriggerType
from app.router.contracts import UpdateIntent
from app.router.precheck import PRECHECK_TABLE
from app.router.registry import TargetClass

from tests.router.helpers import c_value, intent, pref_intent


HYPOTHESIS_TARGET = "C.hypothesis"
EVIDENCE_TARGET = "C.evidence"
PREFERENCE_TARGET = "M2.preferences"


def _hypothesis_intent(intent_id: str = "I-1", **value_kwargs) -> UpdateIntent:
    value = {
        "statement": "背单词也许该用 Anki",
        "condition": "背单词时",
        "effect": "用 Anki 间隔重复",
    }
    value.update(value_kwargs)
    return intent(
        intent_id,
        action="add_preference",
        target=HYPOTHESIS_TARGET,
        value=value,
        basis="stated",
    )


# --------------------------------------------------------------------------- #
# 格 1 & 2：`stated` 的两种做法
# --------------------------------------------------------------------------- #


def test_a_stated_practice_is_a_hypothesis_not_evidence(router) -> None:
    """★ 这是本轮关掉的第一格。

    「我想试试用 Anki 背单词」是一条**假说**。它以前可以走 `C.evidence` +
    `stated` 自动生效 —— 即一条从未发生过的做法被记成"证据"，
    然后在规划时被当成经验使用。
    """
    result = router.precheck(
        TriggerType.R3,
        c_value_intent(basis="stated"),
    )
    assert result.verdict is Precheck.MUST_REJECT
    # 提示要把模型引到正确的表，而不是只说"不允许"
    assert "假说" in result.reason or "hypothesis" in result.reason


def c_value_intent(*, basis: str, intent_id: str = "I-1") -> UpdateIntent:
    return intent(
        intent_id,
        action="add_preference",
        target=EVIDENCE_TARGET,
        value=c_value(),
        basis=basis,  # type: ignore[arg-type]
    )


def test_a_stated_practice_lands_in_the_hypothesis_table(router) -> None:
    """同一句话，换成 `C.hypothesis` 就自动生效 —— 因为它确实是假说。"""
    result = router.precheck(TriggerType.R3, _hypothesis_intent())
    assert result.verdict is Precheck.AUTO_APPLY


# --------------------------------------------------------------------------- #
# 格 3 & 4：`experienced` 的两种做法
# --------------------------------------------------------------------------- #


def test_an_experienced_practice_is_evidence_not_a_hypothesis(router) -> None:
    """★ 第二格：**试过了就不再是假说**。

    这条不是"严谨癖"：如果 `C.hypothesis` 接受 `experienced`，那么装配方
    读假说表就可能读到已验证的东西 —— 两张表的语义立刻开始互相渗透，
    而"只取证据"就退化成一个要记得的过滤。
    """
    bad = intent(
        "I-1",
        action="add_preference",
        target=HYPOTHESIS_TARGET,
        value={
            "statement": "背单词用 Anki 很有效",
            "condition": "背单词时",
            "effect": "用 Anki 间隔重复",
        },
        basis="experienced",
    )
    result = router.precheck(TriggerType.R4C, bad)
    assert result.verdict is Precheck.MUST_REJECT
    assert "证据" in result.reason or "evidence" in result.reason


def test_an_experienced_practice_into_evidence_is_still_auto_on_a_tick(router) -> None:
    """R4c 勾选沉淀：试过 + 他亲手勾选 → 直接生效（这条 P7 之前的结论不变）。"""
    result = router.precheck(TriggerType.R4C, c_value_intent(basis="experienced"))
    assert result.verdict is Precheck.AUTO_APPLY


def test_an_experienced_practice_needs_one_confirmation_when_auto_produced(router) -> None:
    """R4a（会话结束自动产出、他还没看过）→ 要确认一次。

    这正是用户在 P7 里选的那一条：「已验证」的说法要给他看一眼再入库。
    而「未验证」（R3 + stated）是直写的 —— 两种待遇是**刻意不同**的：
    猜想错了不要紧，结论是"关于他自己"的判断。
    """
    result = router.precheck(TriggerType.R4A, c_value_intent(basis="experienced"))
    assert result.verdict is Precheck.NEEDS_CONFIRMATION


# --------------------------------------------------------------------------- #
# `inferred`：既不构成证据，也不构成假说
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("target", [EVIDENCE_TARGET, HYPOTHESIS_TARGET])
def test_inference_never_becomes_a_practice(router, target: str) -> None:
    """推断出来的「做法」不写进任何一张做法表。

    - 写进证据 → 那是"我推断出你试过"，事实层面的错；
    - 写进假说 → 假说的定义是**他自己说想试**，推断不构成他说的。

    `inferred` 只留一个出口：`M2.preferences`（软偏好，需确认）。
    """
    value = (
        c_value()
        if target == EVIDENCE_TARGET
        else {
            "statement": "你可能想试试 Anki",
            "condition": "背单词时",
            "effect": "用 Anki",
        }
    )
    bad = intent(
        "I-1",
        action="add_preference",
        target=target,
        value=value,
        basis="inferred",
    )
    assert router.precheck(TriggerType.R3, bad).verdict is Precheck.MUST_REJECT


def test_inference_into_a_soft_preference_is_still_a_candidate(router) -> None:
    """偏好这一格没变：推断型偏好降级为候选，而不是丢掉。"""
    bad = pref_intent("我喜欢先看例子", basis="inferred")
    assert router.precheck(TriggerType.R3, bad).verdict is Precheck.NEEDS_CONFIRMATION


# --------------------------------------------------------------------------- #
# 偏好那一行：无条件倾向（没有 condition 槽），两个 basis 都收
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("basis", ["stated", "experienced"])
def test_a_preference_accepts_both_bases(router, basis: str) -> None:
    """偏好的性质没变：`stated` 直写、`experienced` 需确认。"""
    result = router.precheck(TriggerType.R3, pref_intent("我喜欢先看例子", basis=basis))
    expected = (
        Precheck.AUTO_APPLY if basis == "stated" else Precheck.NEEDS_CONFIRMATION
    )
    assert result.verdict is expected


# --------------------------------------------------------------------------- #
# UI 直写（R1）：他点的是哪个表单，决定的是哪一种
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("target", [EVIDENCE_TARGET, HYPOTHESIS_TARGET])
def test_the_ui_form_he_chose_is_what_decides(router, target: str) -> None:
    """R1 两条都直接生效 —— 他亲手写的东西不需要再问他一次。

    ★ 差别只在于**他点的是哪个表单**：
    「记一条已验证的经验」→ `/api/evidence`（`experienced`）；
    「记一条想试的假说」→ `/api/hypotheses`（`stated`）。
    系统不需要从措辞里猜，模型更不需要。
    """
    basis = "experienced" if target == EVIDENCE_TARGET else "stated"
    value = (
        c_value()
        if target == EVIDENCE_TARGET
        else {
            "statement": "想试试 Anki",
            "condition": "背单词时",
            "effect": "用 Anki",
        }
    )
    body = intent(
        "I-1",
        action="add_preference",
        target=target,
        value=value,
        basis=basis,  # type: ignore[arg-type]
    )
    assert router.precheck(TriggerType.R1, body).verdict is Precheck.AUTO_APPLY


# --------------------------------------------------------------------------- #
# 表本身的一致性
# --------------------------------------------------------------------------- #


def test_no_rule_covers_two_practice_targets() -> None:
    """没有任何**新增行**同时管「证据」和「假说」。

    这是 P7 拆分的**结构判据**：如果存在这样一行，两个目标就又有了一条
    共同的放行路径，而"两格关掉"随时会在下一次改动里悄悄打开。

    ## ★ P7.1 的例外，以及它为什么不是例外

    只看 `add_preference` 那几行。归档 / 恢复 / 改写**刻意同时覆盖两者** ——
    因为它管的是「他能不能处置自己的东西」，而那件事跟条目是"想试"
    还是"试过"毫无关系。

    混起来才会出问题：如果 `archive` 只管假说不管证据，用户就得记住
    「假说能删、证据不能」—— 而那种记忆负担会精确地落在他最不该学的
    地方（为什么两个看起来一样的东西操作不同）。

    所以判据要说准：**「可信度」这一维上不许合并，「生命周期」这一维上必须合并。**
    """
    both = {TargetClassPair.EVIDENCE, TargetClassPair.HYPOTHESIS}

    for rule in PRECHECK_TABLE:
        if IntentAction.ADD_PREFERENCE not in rule.actions:
            continue
        covered = {tc.value for tc in rule.target_classes} & both
        assert covered != both, (
            f"{rule.row_id} 同时覆盖了证据与假说 —— "
            f"两个可信度完全不同的东西共用一条放行路径，正是 P7 拆掉的那个合并类"
        )

    lifecycle = {IntentAction.ARCHIVE, IntentAction.RESTORE, IntentAction.REVISE}
    lifecycle_rows = [rule for rule in PRECHECK_TABLE if rule.actions & lifecycle]
    assert lifecycle_rows, "生命周期动作一行都没有 —— 用户没法处置自己的条目"

    # 不携带内容的动作（归档 / 恢复）：对两族**一格**，因为"他做了个动作"
    # 与条目是"想试"还是"试过"无关。
    for rule in lifecycle_rows:
        if rule.actions & {IntentAction.ARCHIVE, IntentAction.RESTORE}:
            covered = {tc.value for tc in rule.target_classes} & both
            assert covered == both, (
                f"{rule.row_id} 的归档/恢复只覆盖了 {covered} —— "
                f"「解释权在用户手里」对证据和假说必须一样"
            )

    # ★ 携带内容的动作（改写）：**必须**按可信度格拆开。
    #   改证据要 experienced、改假说要 stated —— 与新增那一列逐字相同。
    #   混成"生命周期都是 stated"会让**证据永远改不了**，而错误信息是一句
    #   "R1 + revise + c_evidence + experienced 未注册"，谁也读不懂。
    revise_rows = [
        rule for rule in lifecycle_rows if IntentAction.REVISE in rule.actions
    ]
    assert len(revise_rows) == 4, (
        f"revise 应该有 4 行（2 触发 × 2 target 类），实际 {len(revise_rows)} —— "
        "它是携带内容的动作，必须服从可信度格"
    )
    for rule in revise_rows:
        assert len(rule.target_classes) == 1, (
            f"{rule.row_id} 同时覆盖了两族 —— "
            f"改写携带内容，所以它的 basis 必须按 target 类区分"
        )
        expected = (
            Basis.EXPERIENCED
            if next(iter(rule.target_classes)) is TargetClass.C_EVIDENCE
            else Basis.STATED
        )
        assert rule.bases == {expected}, (
            f"{rule.row_id} 的 basis 是 {rule.bases}，应该是 {{{expected.value}}}"
        )


class TargetClassPair:
    """只为可读性：不要去 import `TargetClass` 再写字面量。"""

    EVIDENCE = "c_evidence"
    HYPOTHESIS = "hypothesis"


def test_the_old_merge_class_is_gone() -> None:
    """`_PREF_TARGETS` 这个合并类必须彻底消失。

    它叫"偏好类"却装着三种东西，那个**名字本身就是误会来源**。
    留着它（哪怕没人用）会让下一个人以为它还是权威。
    """
    import app.router.precheck as precheck_module

    assert not hasattr(precheck_module, "_PREF_TARGETS")
    for name in ("_STATED_TARGETS", "_EXPERIENCED_TARGETS", "_INFERRED_TARGETS"):
        assert hasattr(precheck_module, name), f"缺少拆分后的集合 {name}"


def test_every_practice_row_names_only_one_basis_family() -> None:
    """每一行装在做法表上的 basis，必须属于**一个**可信度家族。

    `stated` 与 `experienced` 不能出现在同一行里 —— 那等于说
    "说过的和试过的在这里一样"，而这句话正是本轮要否掉的。
    """
    for rule in PRECHECK_TABLE:
        practices = {
            tc for tc in rule.target_classes if tc.value in {"c_evidence", "hypothesis"}
        }
        if not practices:
            continue
        assert len(rule.bases) == 1, (
            f"{rule.row_id} 一次装了多个 basis（{rule.bases}）"
        )
