"""C 家族的生命周期（P7.1）：归档 / 恢复 / 改写。

## 这一组测试要钉住的三件事

1. **他能处置自己的东西**：三个动作都在判定表里，R1 直接生效、R3 需确认。
2. **"删除"是退场，不是消失**：行还在，`router_log` 里前后值都在 —— 否则
   I2（已应用的日志必须指向真实存在的地址）与"当时为什么删"同时断掉。
3. ★ **归档真的把它从装配里拿掉了**：这是整个功能唯一的功能性后果。
   "不参与装配"这句话如果只写在文档里，那它就是一句愿望。

第 3 条是这组测试里最值钱的一条：它把「用户把一条经验拿掉了」这个动作
和「下次规划不会再拿它当依据」之间的因果连起来。没有它，归档就只是
在列表里藏一行。
"""

from __future__ import annotations

import itertools

import pytest
from sqlalchemy.orm import Session

from app.global_domain import reads
from app.global_domain.enums import Basis, IntentAction, Precheck, TriggerType
from app.router import Router, SubmitRequest
from app.router.precheck import PRECHECK_TABLE, lookup_rule
from app.router.registry import TargetClass

from tests.router.helpers import c_value, intent

HYPOTHESIS_VALUE = {
    "statement": "背单词想试试 Anki",
    "condition": "背单词时",
    "effect": "用 Anki 的间隔重复",
}


# --------------------------------------------------------------------------- #
# ① 判定表：三个动作 × 两族 × 两个触发
# --------------------------------------------------------------------------- #


CASES = [
    # 归档 / 恢复不携带内容 -> 只有一格（stated），两族相同。
    (IntentAction.ARCHIVE, TargetClass.C_EVIDENCE, Basis.STATED),
    (IntentAction.ARCHIVE, TargetClass.HYPOTHESIS, Basis.STATED),
    (IntentAction.RESTORE, TargetClass.C_EVIDENCE, Basis.STATED),
    (IntentAction.RESTORE, TargetClass.HYPOTHESIS, Basis.STATED),
    # * 改写携带内容 -> 落在 2x2 格上：改证据要 experienced、改假说要 stated。
    (IntentAction.REVISE, TargetClass.C_EVIDENCE, Basis.EXPERIENCED),
    (IntentAction.REVISE, TargetClass.HYPOTHESIS, Basis.STATED),
]


def test_the_user_can_act_on_both_families_directly() -> None:
    """用户亲手点 -> 直接生效，两族完全一样。

    「解释权永远在用户手里」在判定表上的样子就是这一条：
    不需要任何模型参与，也不需要再弹一次确认。
    """
    for action, target_class, basis in CASES:
        rule = lookup_rule(TriggerType.R1, action, target_class, basis)
        assert rule is not None, (
            f"R1 + {action.value} + {target_class.value} + {basis.value} 没注册"
        )
        assert rule.verdict is Precheck.AUTO_APPLY


def test_the_model_can_only_propose_them() -> None:
    """模型在对话里提议 -> 需确认。

    这一格是「解释权」的另一半：他能自己直接做，而模型只能提议。
    两件事看起来矛盾，其实是同一句话的两面 ——
    决定权在他，而提议权可以是别人的。
    """
    for action, target_class, basis in CASES:
        rule = lookup_rule(TriggerType.R3, action, target_class, basis)
        assert rule is not None, (
            f"R3 + {action.value} + {target_class.value} + {basis.value} 没注册"
        )
        assert rule.verdict is Precheck.NEEDS_CONFIRMATION


def test_lifecycle_acts_only_on_the_c_family() -> None:
    """偏好与 M1 没有这些动作 —— 但理由不同，两边都不是遗漏。

    - 偏好：早就有 `supersedes_pref_id`（A20 裁定），语义是"被另一句话取代"；
    - M1：那是**测量结果**，不能由用户手改 —— 只能重测（R2）或回滚（R5）。
    """
    for action in (IntentAction.ARCHIVE, IntentAction.RESTORE, IntentAction.REVISE):
        for target_class in (TargetClass.M2_PREFERENCE, TargetClass.M1_TRAIT):
            rule = lookup_rule(TriggerType.R1, action, target_class, Basis.STATED)
            assert rule is None, (
                f"{target_class.value} 竟然能吃 {action.value} —— "
                f"那意味着用户能亲手改掉一条测量读数"
            )


def test_the_lifecycle_only_accepts_stated() -> None:
    """`archive` + `experienced` 不是一个"更硬的删除"，它根本不是一件事。

    这条挡住的是模型说"我观察到他想删掉它" —— 删除是**意思表示**，
    只能他说。它和 `C.evidence + stated` 被拒是同一个道理的反面。
    """
    for action in (IntentAction.ARCHIVE, IntentAction.RESTORE, IntentAction.REVISE):
        for basis in (Basis.EXPERIENCED, Basis.INFERRED):
            assert (
                lookup_rule(TriggerType.R1, action, TargetClass.HYPOTHESIS, basis)
                is None
            ), f"{action.value} + {basis.value} 不该被注册"


def test_every_lifecycle_row_is_reachable_by_some_action() -> None:
    """表里没有一行是死的 —— 每一行都对应 `IntentAction` 里真实存在的值。"""
    lifecycle = {IntentAction.ARCHIVE, IntentAction.RESTORE, IntentAction.REVISE}
    rows = [rule for rule in PRECHECK_TABLE if rule.actions & lifecycle]
    assert len(rows) == 8, f"生命周期应该有 8 行，实际 {len(rows)}"
    covered = set()
    for rule in rows:
        covered |= {action for action in rule.actions if action in lifecycle}
    assert covered == lifecycle


# --------------------------------------------------------------------------- #
# ② 集合根 vs 逐条地址
# --------------------------------------------------------------------------- #


def test_archiving_a_whole_class_is_refused(router: Router) -> None:
    """`DELETE C.hypothesis`（不带 id）→ policy 拒绝，并说清该给什么地址。

    「把这一类东西全部归档」有两种读法（全删 / 什么也没做），
    两种都比一句明确的拒绝糟糕。而且两种都很危险：
    前者会一次清空认知库，后者会让用户以为自己删了。
    """
    result = router.submit(
        SubmitRequest(
            trigger_type="R1",
            intents=[
                intent(
                    action="archive",
                    target="C.hypothesis",
                    value={"reason": "不要了"},
                    basis="stated",
                )
            ],
        )
    )
    assert result.results[0].status.value == "rejected"
    assert "C.hypothesis.H-0001" in result.results[0].reason


# --------------------------------------------------------------------------- #
# ③ 端到端：改写 / 归档 / 恢复，以及"归档真的实现了不参与装配"
# --------------------------------------------------------------------------- #


def _add_hypothesis(seeded: Session, router: Router) -> str:
    result = _submit(router, "add_preference", "C.hypothesis", HYPOTHESIS_VALUE)
    assert result.results[0].status.value == "applied"
    return reads.list_hypotheses(seeded)[0].hypothesis_id


#: 每次提交都要一个**没用过**的 intent_id（它是数据库主键）。
#: 固定 `I-1` 会让第二次提交被 `_reject_taken_intent_ids` 挡下 ——
#: 那个防护本身是对的，它防的正是两次提交被当成同一条。
_SEQ = itertools.count(1)


def _submit(
    router: Router,
    action: str,
    target: str,
    value: dict | None = None,
    *,
    basis: str = "stated",
):
    return router.submit(
        SubmitRequest(
            trigger_type="R1",
            intents=[
                intent(
                    intent_id=f"I-{next(_SEQ)}",
                    action=action,
                    target=target,
                    value=value,
                    basis=basis,
                )
            ],
        )
    )


def _add_evidence(seeded: Session, router: Router) -> str:
    """写一条 C 证据 —— 注意 basis 必须是 `experienced`（P7 拆格之后）。"""
    result = _submit(
        router, "add_preference", "C.evidence", c_value(), basis="experienced"
    )
    assert result.results[0].status.value == "applied", result.results[0].reason
    return reads.list_active_evidence(seeded)[0].evidence_id


def test_revise_keeps_the_id_and_the_trail(seeded: Session, router: Router) -> None:
    """改写：id 不变，前后快照都进日志。"""
    hypothesis_id = _add_hypothesis(seeded, router)
    result = _submit(
        router,
        "revise",
        f"C.hypothesis.{hypothesis_id}",
        {
            "statement": "背单词想试试 Anki，每天不超过 20 张",
            "condition": "每天背单词时",
            "effect": "用 Anki，上限 20 张",
            "reason": "加个上限",
        },
    )
    assert result.results[0].status.value == "applied"

    rows = reads.list_hypotheses(seeded)
    assert len(rows) == 1, "改写不该产生第二行"
    assert rows[0].hypothesis_id == hypothesis_id
    assert rows[0].effect == "用 Anki，上限 20 张"

    from tests.router.helpers import log_for

    log = log_for(seeded, result.results[0].intent_id)
    assert log.old_value["effect"] == "用 Anki 的间隔重复"
    assert log.new_value["effect"] == "用 Anki，上限 20 张"


def test_archive_removes_it_from_assembly_but_keeps_the_row(
    seeded: Session, router: Router
) -> None:
    """★ 归档的功能性后果：装配读不到它，行还在。"""
    evidence_id = _add_evidence(seeded, router)
    assert [row.evidence_id for row in reads.list_active_evidence(seeded)] == [
        evidence_id
    ]

    archived = _submit(
        router, "archive", f"C.evidence.{evidence_id}", {"reason": "后来发现不成立"}
    )
    assert archived.results[0].status.value == "applied"

    assert reads.list_active_evidence(seeded) == [], (
        "★ 归档之后它必须从装配源里消失 —— 否则「拿掉一条证据」这个动作没有后果"
    )
    everything = reads.list_active_evidence(seeded, include_archived=True)
    assert [row.evidence_id for row in everything] == [evidence_id], (
        "行必须还在：物理删除会让 router_log 指向空气，也让「为什么删」无从回答"
    )
    assert everything[0].status.value == "archived"

    restored = _submit(router, "restore", f"C.evidence.{evidence_id}")
    assert restored.results[0].status.value == "applied"
    assert [row.evidence_id for row in reads.list_active_evidence(seeded)] == [
        evidence_id
    ], "恢复之后 id 还是原来那个 —— 历史继续连得上"


def test_archiving_twice_is_idempotent_and_says_so(
    seeded: Session, router: Router
) -> None:
    """重复删除是幂等的，但要说出来。

    静默成功会让界面上的双击看起来像"又删了一次"，而他无从判断
    第二次到底做了什么。说出「它本来就是该状态」比返回一个绿色勾更有用。
    """
    evidence_id = _add_evidence(seeded, router)
    first = _submit(router, "archive", f"C.evidence.{evidence_id}")
    second = _submit(router, "archive", f"C.evidence.{evidence_id}")
    assert second.results[0].status.value == "applied"
    assert "本来就是该状态" in second.results[0].reason
    assert "本来就是该状态" not in first.results[0].reason


def test_revising_an_archived_item_does_not_silently_restore_it(
    seeded: Session, router: Router
) -> None:
    """改一条已归档的东西，它**不会**因此回到视野 —— 而且系统会说出来。

    这一格是"两件事被合起来做"的典型陷阱：如果改写顺手把状态改回 active，
    用户会以为"我只是改了个措辞"，而一条他以为已经拿掉的依据悄悄回来了。
    """
    evidence_id = _add_evidence(seeded, router)
    _submit(router, "archive", f"C.evidence.{evidence_id}")

    result = _submit(
        router,
        "revise",
        f"C.evidence.{evidence_id}",
        {
            **c_value(),
            "effect": "改成示例先行",
            "reason": "措辞调整",
        },
        basis="experienced",
    )
    assert result.results[0].status.value == "applied", result.results[0].reason
    assert "不会自动恢复" in result.results[0].reason
    assert reads.list_active_evidence(seeded) == [], "改写不该让已归档的东西复活"


def test_the_unknown_id_is_refused_with_the_id_in_the_message(
    router: Router,
) -> None:
    """id 打错 → 拒绝，并把那个 id 原样说回来。

    只说"不存在"会让调用方以为是自己传错了别的东西。
    """
    result = _submit(router, "archive", "C.hypothesis.H-9999")
    assert result.results[0].status.value == "rejected"
    assert "H-9999" in result.results[0].reason
