"""`Router.submit` —— 提案落库、逐条执行、状态机推导、审计闭环。

这些测试关心的是「治理内核能不能被信任」：写进去了什么、日志对不对得上、
被拒的东西有没有留下痕迹、未确认的东西有没有偷偷生效。
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationRejected
from app.global_domain.enums import (
    Basis,
    Completeness,
    EvidenceChannel,
    IntentAction,
    IntentStatus,
    LifecycleStatus,
    Precheck,
    ProposalSource,
    ProposalStatus,
    RouterLogStatus,
    TriggerType,
)
from app.global_domain.reads import (
    count_router_logs,
    current_baseline_version,
    get_proposal,
    list_active_evidence,
    list_active_preferences,
    list_pending_proposals,
    list_proposals,
)
from tests.router.helpers import log_for, m1_intent, pref_intent, request


# --------------------------------------------------------------------------- #
# 自动生效
# --------------------------------------------------------------------------- #


def test_r2_auto_applies_and_writes_one_audited_log(router, seeded) -> None:
    result = router.submit(
        request("R2", m1_intent(basis="measured"), source_ref="RUN-1")
    )

    assert result.status is ProposalStatus.APPLIED
    (outcome,) = result.results
    assert outcome.status is IntentStatus.APPLIED
    assert outcome.verdict is Precheck.AUTO_APPLY
    assert outcome.router_log_id is not None

    version = current_baseline_version(seeded, "V-SE", EvidenceChannel.MEASURED)
    assert version is not None
    assert version.score == 7.0
    assert version.completeness is Completeness.COMPLETE
    assert version.basis is Basis.MEASURED
    assert version.source_ref == "RUN-1"
    assert version.replaces_version_id is None
    # 数据行携带 router_log_id —— 审计链不是靠「时间戳差不多」猜出来的。
    assert version.router_log_id == outcome.router_log_id

    log = log_for(seeded, "I-1")
    assert log.status is RouterLogStatus.APPLIED
    assert log.trigger is TriggerType.R2
    assert log.source is ProposalSource.BASELINE
    assert log.action is IntentAction.UPDATE_PARAMETER
    assert log.user_confirmed is True
    assert log.proposal_ref == result.proposal_id


def test_r2_second_write_replaces_and_links_previous_version(router, seeded) -> None:
    first = router.submit(request("R2", m1_intent("I-1", score=6.0)))
    second = router.submit(request("R2", m1_intent("I-2", score=8.0)))

    assert first.status is ProposalStatus.APPLIED
    assert second.status is ProposalStatus.APPLIED

    current = current_baseline_version(seeded, "V-SE", EvidenceChannel.MEASURED)
    assert current is not None and current.score == 8.0
    assert current.replaces_version_id is not None

    previous = seeded.get(type(current), current.replaces_version_id)
    assert previous is not None and previous.score == 6.0
    # 历史行内容永不修改。
    assert previous.variable_id == "V-SE"


def test_r1_preference_auto_applies(router, seeded) -> None:
    result = router.submit(request("R1", pref_intent(priority=9)))

    assert result.status is ProposalStatus.APPLIED
    (pref,) = list_active_preferences(seeded)
    assert pref.priority == 9
    assert pref.basis is Basis.STATED
    assert pref.status is LifecycleStatus.ACTIVE
    assert pref.router_log_id == result.results[0].router_log_id

    log = log_for(seeded, "I-1")
    assert log.source is ProposalSource.USER_UI
    assert log.trigger is TriggerType.R1


def test_no_change_writes_only_a_log(router, seeded) -> None:
    """§5.3：no_change 的结论是 auto_apply，但它不该产生任何全局变更。"""
    from tests.router.helpers import intent

    result = router.submit(
        request(
            "R4A",
            intent(action="no_change", target="M1.V-SE", basis="experienced"),
        )
    )

    assert result.status is ProposalStatus.APPLIED
    assert count_router_logs(seeded) == 1
    assert log_for(seeded, "I-1").status is RouterLogStatus.APPLIED
    assert current_baseline_version(seeded, "V-SE", EvidenceChannel.MEASURED) is None
    assert current_baseline_version(seeded, "V-SE", EvidenceChannel.EXPERIENCED) is None


def test_c_evidence_is_written_but_m1_is_untouched(router, seeded) -> None:
    from tests.router.helpers import evidence_intent

    result = router.submit(request("R4C", evidence_intent(statement="先例后理更牢")))

    assert result.status is ProposalStatus.APPLIED
    (evidence,) = list_active_evidence(seeded)
    assert evidence.statement == "先例后理更牢"
    assert evidence.condition == "材料是程序性技能"
    assert current_baseline_version(seeded, "V-SE", EvidenceChannel.MEASURED) is None


# --------------------------------------------------------------------------- #
# 提案状态机
# --------------------------------------------------------------------------- #


def test_proposal_is_never_left_in_proposed(router, seeded) -> None:
    """`proposed` 是瞬时态：提交返回时必已进入某个确定状态（M0 §5.1）。"""
    result = router.submit(request("R2", m1_intent()))
    proposal = get_proposal(seeded, result.proposal_id)

    assert proposal is not None
    assert proposal.status is result.status
    assert proposal.status is not ProposalStatus.PROPOSED
    assert proposal.status is not ProposalStatus.EXPIRED


def test_source_ref_survives_until_resolve(router, seeded) -> None:
    """A13：出处必须随提案落库。

    若出处只存在于 `SubmitRequest` 里，裁决时它已经丢了 ——
    于是 R2 写入的 `source_ref` 为空，§5.4 的重做计数永远算出 0，
    重做建议从此再不出现（一个静默失效的功能）。
    """
    from tests.router.helpers import decision

    result = router.submit(
        request(
            "R4A",
            m1_intent(variable="V-SMP", basis="experienced"),
            source_ref="RUN-7",
            session_ref="S-1",
        )
    )
    proposal = get_proposal(seeded, result.proposal_id)
    assert proposal is not None
    assert proposal.source_ref == "RUN-7"
    assert proposal.session_ref == "S-1"

    router.resolve(result.proposal_id, [decision("I-1", True)])

    version = current_baseline_version(seeded, "V-SMP", EvidenceChannel.MEASURED)
    assert version is not None
    assert version.source_ref == "RUN-7"


def test_needs_confirmation_stays_pending_and_writes_nothing(router, seeded) -> None:
    result = router.submit(request("R4A", pref_intent(basis="experienced")))

    assert result.status is ProposalStatus.PENDING_CONFIRMATION
    (outcome,) = result.results
    assert outcome.status is IntentStatus.PENDING
    assert outcome.verdict is Precheck.NEEDS_CONFIRMATION

    assert list_active_preferences(seeded) == []
    # 未确认 = 尚未发生写入事件 = 不应有审计日志。
    assert count_router_logs(seeded) == 0
    assert len(list_pending_proposals(seeded)) == 1


def test_mixed_batch_waits_for_confirmation(router, seeded) -> None:
    """一个批次里既有直接生效的、也有待确认的 —— 提案停在 pending_confirmation。

    用 **R3** 来构造这个混合批次（P5.2 起 R4c 的两条 action 都直接生效了，
    因此 R4c 造不出混合批次）：R3 + `stated` 偏好是用户自己说的 → 直接生效；
    R3 + 参数**永远**需确认（红线 3：一句对话不能改基线数值）。
    """
    result = router.submit(
        request(
            "R3",
            pref_intent("I-1", basis="stated"),
            m1_intent("I-2", variable="V-SMP", basis="measured"),
        )
    )

    by_id = {r.intent_id: r for r in result.results}
    assert by_id["I-1"].status is IntentStatus.APPLIED
    assert by_id["I-2"].status is IntentStatus.PENDING
    # 还有待裁决的条目 → 停在 pending_confirmation；
    # partially_applied 只作为**终态**出现。
    assert result.status is ProposalStatus.PENDING_CONFIRMATION
    assert len(list_active_preferences(seeded)) == 1
    assert current_baseline_version(seeded, "V-SMP", EvidenceChannel.MEASURED) is None


def test_r4c_applies_parameters_too_because_the_click_is_the_confirmation(
    router, seeded
) -> None:
    """**P5.2 用户裁定**：R4c 的勾选对**两种 action** 都是确认动作。

    原先 `R4c.add_preference` 直接生效而 `R4c.update_parameter` 需确认 ——
    那等于说「同一个点击，对偏好算确认、对参数不算」，而给不出理由
    （文档里唯一可用的理由「证据强度 强/中/弱」指向相反方向，
    且那张表自己声明「不改变路由动作」）。

    变量用 `V-SMP`（`preference` 类）而不是 trait：见下一条测试。
    """
    result = router.submit(
        request("R4c", m1_intent("I-1", variable="V-SMP", basis="experienced"))
    )

    (outcome,) = result.results
    assert outcome.verdict is Precheck.AUTO_APPLY
    assert outcome.status is IntentStatus.APPLIED
    current = current_baseline_version(seeded, "V-SMP", EvidenceChannel.MEASURED)
    assert current is not None
    assert current.router_log_id == outcome.router_log_id


def test_r4c_is_still_blocked_by_the_two_session_rule_on_traits(
    router, seeded
) -> None:
    """**判定与硬前置条件是两个正交的东西** —— 这是上一条测试的安全边界。

    `R4c + update_parameter + experienced` 现在是 `AUTO_APPLY`，但 trait 变量
    仍被 §7.3 的「≥2 条独立 Session 证据」拦住（那是 `blockers`，不是 `verdict`）。
    于是 `AUTO_APPLY + 有 blocker` ⇒ **依然待确认、依然没有写入**。

    若哪天有人把 blocker 合并进 verdict（或让 auto_apply 无视 blocker），
    这条测试会红 —— 而那正是「凭当下感受改写对自己能力的判断」的唯一防线。
    """
    result = router.submit(
        request("R4c", m1_intent("I-1", variable="V-SE", basis="experienced"))
    )

    (outcome,) = result.results
    assert outcome.verdict is Precheck.AUTO_APPLY, "判定这一层确实放行了"
    assert outcome.blockers, "但硬前置条件必须把它拦下来"
    assert outcome.status is IntentStatus.PENDING, "因此最终仍然是待确认"
    assert current_baseline_version(seeded, "V-SE", EvidenceChannel.MEASURED) is None
    assert count_router_logs(seeded) == 0, "没写入就不该有审计日志"


def test_must_reject_is_audited_with_a_reason(router, seeded) -> None:
    result = router.submit(request("R3", m1_intent(basis="inferred")))

    assert result.status is ProposalStatus.REJECTED
    (outcome,) = result.results
    assert outcome.status is IntentStatus.REJECTED
    assert outcome.verdict is Precheck.MUST_REJECT

    log = log_for(seeded, "I-1")
    assert log.status is RouterLogStatus.REJECTED
    assert "inferred" in log.reasoning
    assert count_router_logs(seeded) == 1
    assert current_baseline_version(seeded, "V-SE", EvidenceChannel.MEASURED) is None


# --------------------------------------------------------------------------- #
# 提交前后的一致性
# --------------------------------------------------------------------------- #


def test_duplicate_intent_id_in_one_request_is_rejected(router, seeded) -> None:
    with pytest.raises(ValidationRejected):
        router.submit(
            request("R2", m1_intent("I-1"), m1_intent("I-1", variable="V-HSR"))
        )
    # 校验发生在写任何东西之前 —— 不留空提案。
    assert list_proposals(seeded) == []


def test_taken_intent_id_is_rejected(router) -> None:
    router.submit(request("R2", m1_intent("I-1")))
    with pytest.raises(ValidationRejected) as excinfo:
        router.submit(request("R2", m1_intent("I-1", variable="V-HSR")))
    assert "已被占用" in str(excinfo.value)


def test_submit_rejects_empty_intent_list() -> None:
    """空提案没有意义：`SubmitRequest.intents` 至少一条。"""
    from pydantic import ValidationError

    from app.router.contracts import SubmitRequest

    with pytest.raises(ValidationError):
        SubmitRequest(trigger_type="R2", intents=[])


def test_intent_timestamps_are_not_caller_supplied(router, seeded) -> None:
    """审计时间戳不可注入 —— 否则「谁先谁后」可以被伪造。"""
    from app.router.contracts import UpdateIntent

    assert "created_at" not in UpdateIntent.model_fields
    assert "decided_at" not in UpdateIntent.model_fields
    assert "created_at" not in type(request("R2", m1_intent())).model_fields
