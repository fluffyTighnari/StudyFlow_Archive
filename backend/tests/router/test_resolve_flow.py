"""`Router.resolve` —— 用户裁决路径（红线 3 的落点）。

「用户是最高权威」在这条路径上有两个都必须成立、且方向相反的保证：

1. **未经确认的写入不得生效**（没裁决 → 什么都不会发生）；
2. **确认了也不等于无条件执行**（有硬前置条件时，用户同意也不行）。
"""

from __future__ import annotations

import pytest

from app.core.errors import NotFound, ValidationRejected, VersionConflict
from app.global_domain.enums import (
    ChannelObsSourceKind,
    EvidenceChannel,
    IntentStatus,
    LifecycleStatus,
    ProposalStatus,
    RouterLogStatus,
)
from app.global_domain.reads import (
    count_router_logs,
    current_baseline_version,
    get_proposal,
    list_active_preferences,
)
from app.global_domain.writes import append_channel_obs
from tests.router.helpers import decision, log_for, m1_intent, pref_intent, request


def _submit_two_pending(router):
    """两条都需确认、落在**同一个槽位**上，但**档位不同**。

    用同一槽位 + 不同档位是**有意的**（P5.2）：同档位相撞现在是「整组拒绝」
    （因为无法裁定，见 `conflict.py`），那验证不了「裁定发生在写入时刻」这件事
    —— 而后者才是这组用例要守的东西。

    原先这里用两条**完全相同**（只差 intent_id）的 intent，于是胜者只能靠
    `intent_id` 字典序决定。那不是「裁定」，那是把字符串顺序当成了语义。

    变量用 `V-SMP`（`preference` 类）而不是 trait 类：trait 的 experienced 写入
    还要过 §7.3 的「≥2 条独立 Session 证据」门槛，那会让两条**都**被前置条件拒掉，
    于是测不到冲突裁定。
    """
    return router.submit(
        request(
            "R3",
            m1_intent("I-1", variable="V-SMP", basis="experienced"),
            m1_intent("I-2", variable="V-SMP", basis="measured"),
        )
    )


# --------------------------------------------------------------------------- #
# 批准 / 否决
# --------------------------------------------------------------------------- #


def test_approving_a_pending_intent_applies_it(router, seeded) -> None:
    submitted = router.submit(request("R4A", pref_intent(basis="experienced")))
    assert submitted.status is ProposalStatus.PENDING_CONFIRMATION

    result = router.resolve(submitted.proposal_id, [decision("I-1", True)])

    assert result.status is ProposalStatus.APPLIED
    (outcome,) = result.results
    assert outcome.status is IntentStatus.APPLIED

    (pref,) = list_active_preferences(seeded)
    assert pref.status is LifecycleStatus.ACTIVE
    assert pref.router_log_id == outcome.router_log_id

    log = log_for(seeded, "I-1")
    assert log.status is RouterLogStatus.APPLIED
    assert log.user_confirmed is True
    # 事后必须还能回答「这个提案是谁批的、什么时候批的」。
    proposal = get_proposal(seeded, submitted.proposal_id)
    assert proposal is not None and proposal.resolved_at is not None


def test_rejecting_a_pending_intent_audits_the_refusal(router, seeded) -> None:
    submitted = router.submit(request("R4A", pref_intent(basis="experienced")))
    result = router.resolve(
        submitted.proposal_id, [decision("I-1", False, reason="这不是我的偏好")]
    )

    assert result.status is ProposalStatus.REJECTED
    (outcome,) = result.results
    assert outcome.status is IntentStatus.REJECTED
    assert "这不是我的偏好" in outcome.reason

    # 用户否决也要留痕：否则「这条为什么没生效」无从回答。
    log = log_for(seeded, "I-1")
    assert log.status is RouterLogStatus.REJECTED
    assert "这不是我的偏好" in log.reasoning
    assert list_active_preferences(seeded) == []


def test_partial_approval_yields_partially_applied(router, seeded) -> None:
    submitted = _submit_two_pending(router)
    assert submitted.status is ProposalStatus.PENDING_CONFIRMATION

    result = router.resolve(
        submitted.proposal_id,
        [decision("I-1", True), decision("I-2", False, reason="只同意第一条")],
    )

    assert result.status is ProposalStatus.PARTIALLY_APPLIED
    by_id = {r.intent_id: r for r in result.results}
    assert by_id["I-1"].status is IntentStatus.APPLIED
    assert by_id["I-2"].status is IntentStatus.REJECTED


def test_unresolved_intents_keep_the_proposal_pending(router, seeded) -> None:
    """提案可以分几次裁决完 —— 只批一半时不该提前收尾。"""
    submitted = _submit_two_pending(router)
    result = router.resolve(submitted.proposal_id, [decision("I-1", True)])

    assert result.status is ProposalStatus.PENDING_CONFIRMATION
    by_id = {r.intent_id: r for r in result.results}
    assert by_id["I-1"].status is IntentStatus.APPLIED
    assert by_id["I-2"].status is IntentStatus.PENDING

    proposal = get_proposal(seeded, submitted.proposal_id)
    assert proposal is not None
    assert proposal.status is ProposalStatus.PENDING_CONFIRMATION


def test_approving_everything_finishes_the_proposal(router, seeded) -> None:
    submitted = _submit_two_pending(router)
    result = router.resolve(
        submitted.proposal_id, [decision("I-1", True), decision("I-2", True)]
    )

    # 两条都批了，但同一个槽位上只有一条能生效 → 终态是 partially_applied。
    # 这不是错误：它准确表达了「你批准的两条里只有一条真的落到了全局状态」。
    # 胜者是 I-2（measured 高于 experienced），见写入裁定序。
    assert result.status is ProposalStatus.PARTIALLY_APPLIED
    by_id = {r.intent_id: r for r in result.results}
    assert by_id["I-2"].status is IntentStatus.APPLIED
    assert by_id["I-1"].status is IntentStatus.SKIPPED
    assert list_active_preferences(seeded) == []  # 这两条写的是 M1，不是偏好


# --------------------------------------------------------------------------- #
# 硬前置条件：用户同意也不行
# --------------------------------------------------------------------------- #


def test_approved_intent_with_blocker_is_still_rejected(router, seeded) -> None:
    """M0 §7.3：trait 变量以 experienced 为依据写入需 ≥2 条独立 Session 证据。

    用户同意**不能**越过它 —— 否则这条门槛就只是「多点一次确认」，
    而它要防的是「凭当下感受改写对自己能力的判断」。
    """
    submitted = router.submit(
        request("R4A", m1_intent("I-1", variable="V-SE", basis="experienced"))
    )
    (pending,) = submitted.results
    assert pending.status is IntentStatus.PENDING
    assert pending.blockers, "应该在提交时就把硬前置条件亮出来"
    assert "≥2 条独立 Session 证据" in pending.blockers[0]

    result = router.resolve(submitted.proposal_id, [decision("I-1", True)])

    assert result.status is ProposalStatus.REJECTED
    (outcome,) = result.results
    assert outcome.status is IntentStatus.REJECTED
    assert "无法绕过的前置条件" in outcome.reason
    assert current_baseline_version(seeded, "V-SE", EvidenceChannel.EXPERIENCED) is None


def test_blocker_clears_once_two_independent_sessions_are_recorded(
    router, seeded, token
) -> None:
    """门槛应当**可被满足** —— 否则它就是一条永久的禁令。

    这条测试同时验证 `resolve` 会**重新判定**：全局状态在提交与裁决之间
    发生了变化（多了两条 Session 证据），结论理应随之改变。
    """
    submitted = router.submit(
        request(
            "R4A",
            m1_intent(
                "I-1", variable="V-SE", basis="experienced", channel="experienced"
            ),
        )
    )
    (pending,) = submitted.results
    assert pending.blockers

    for session_id in ("S-001", "S-002"):
        append_channel_obs(
            token,
            seeded,
            variable_id="V-SE",
            channel=EvidenceChannel.EXPERIENCED,
            source_kind=ChannelObsSourceKind.SESSION,
            value=7.0,
            source_ref=session_id,
        )

    result = router.resolve(submitted.proposal_id, [decision("I-1", True)])

    assert result.status is ProposalStatus.APPLIED
    (outcome,) = result.results
    assert outcome.blockers == ()
    assert current_baseline_version(seeded, "V-SE", EvidenceChannel.EXPERIENCED) is not None


def test_blocker_is_not_bypassed_by_repeating_one_session(router, seeded, token) -> None:
    """同一 Session 记 5 次仍然只是 1 条证据。

    按行计数会让「反复记同一件事」轻易凑够 ≥2 条，从而绕开 §7.3 的门槛。
    """
    submitted = router.submit(
        request("R4A", m1_intent("I-1", variable="V-SE", basis="experienced"))
    )
    for _ in range(5):
        append_channel_obs(
            token,
            seeded,
            variable_id="V-SE",
            channel=EvidenceChannel.EXPERIENCED,
            source_kind=ChannelObsSourceKind.SESSION,
            value=7.0,
            source_ref="S-001",
        )

    result = router.resolve(submitted.proposal_id, [decision("I-1", True)])
    assert result.status is ProposalStatus.REJECTED


# --------------------------------------------------------------------------- #
# 裁决本身的合法性
# --------------------------------------------------------------------------- #


def test_resolve_unknown_proposal_raises_not_found(router) -> None:
    with pytest.raises(NotFound):
        router.resolve("UP-9999", [])


def test_resolve_rejects_unknown_intent_id(router) -> None:
    submitted = router.submit(request("R4A", pref_intent(basis="experienced")))
    with pytest.raises(ValidationRejected):
        router.resolve(submitted.proposal_id, [decision("I-999", True)])
    # 非法裁决不得留下任何副作用。
    assert count_router_logs(router._session) == 0


def test_resolve_rejects_double_decision_for_one_intent(router) -> None:
    submitted = router.submit(request("R4A", pref_intent(basis="experienced")))
    with pytest.raises(ValidationRejected, match="裁决两次"):
        router.resolve(
            submitted.proposal_id,
            [decision("I-1", True), decision("I-1", False)],
        )


def test_resolve_twice_is_a_version_conflict(router) -> None:
    """终态提案不可再裁决 —— 否则同一条 intent 可能被写两次。"""
    submitted = router.submit(request("R4A", pref_intent(basis="experienced")))
    router.resolve(submitted.proposal_id, [decision("I-1", True)])

    with pytest.raises(VersionConflict):
        router.resolve(submitted.proposal_id, [decision("I-1", True)])


def test_resolve_on_auto_applied_proposal_is_a_version_conflict(router) -> None:
    """已经全部自动生效的提案不该出现在待确认队列里。"""
    submitted = router.submit(request("R2", m1_intent()))
    assert submitted.status is ProposalStatus.APPLIED

    with pytest.raises(VersionConflict):
        router.resolve(submitted.proposal_id, [decision("I-1", True)])


def test_conflicting_pending_intents_are_arbitrated_at_write_time(
    router, seeded
) -> None:
    """同一**槽位**的两条写入在同一次裁决里被裁定：胜者应用，败者 skipped。

    裁定发生在**写入时刻**（这里是裁决那一刻），而不是提交时刻 ——
    否则若胜者恰好是待确认的那条，就可能出现「胜者没被批准、败者被提前丢弃」
    —— 一次操作什么都没发生。

    注意 `submitted.conflicts` 此时是**空**的：两条都还在待确认，
    **没有任何写入发生**，因此没有东西需要裁定。裁定只在真正要写的那一刻算。
    """
    submitted = _submit_two_pending(router)
    assert submitted.status is ProposalStatus.PENDING_CONFIRMATION
    assert submitted.conflicts == [], "还没写入，谈不上冲突"

    result = router.resolve(
        submitted.proposal_id, [decision("I-1", True), decision("I-2", True)]
    )

    (report,) = result.conflicts
    assert report.slot == "M1.V-SMP/measured"
    assert report.winner_intent_id == "I-2"

    by_id = {r.intent_id: r for r in result.results}
    assert by_id["I-2"].status is IntentStatus.APPLIED
    assert by_id["I-1"].status is IntentStatus.SKIPPED
    assert by_id["I-1"].router_log_id is not None

    log = log_for(seeded, "I-1")
    assert log.status is RouterLogStatus.OVERRIDDEN
    assert "写入裁定序" in log.reasoning


def test_tied_same_slot_writes_are_rejected_not_silently_tie_broken(
    router, seeded
) -> None:
    """**回归用例（P5.2）**：同槽位 + 同档位 → **整组拒绝**，不是挑一个赢。

    原先的行为是按 `intent_id` 字典序挑一个，并且报告文案写
    「有**更高优先级**的写入」—— 两条档位完全相同，所以那句话是假的。
    结果是：**你的数据变成什么样，取决于一个字符串的字典序**，
    而系统用一个不成立的理由解释了它。
    """
    submitted = router.submit(
        request(
            "R2",
            m1_intent("I-1", variable="V-SMP", basis="measured"),
            m1_intent("I-2", variable="V-SMP", basis="measured", score=9.0),
        )
    )
    assert submitted.status is ProposalStatus.REJECTED, (
        "两条都进了 auto_apply 路径，但都被裁定拒绝 —— 什么都没写，"
        "所以提案终态是 rejected 而不是 applied（后者会谎称写成功了）"
    )
    by_id = {r.intent_id: r for r in submitted.results}
    assert by_id["I-1"].status is IntentStatus.REJECTED
    assert by_id["I-2"].status is IntentStatus.REJECTED
    assert "无法裁定" in by_id["I-1"].reason

    (report,) = submitted.conflicts
    assert report.is_tie()
    # 一条都没写进去 —— 拒绝是**真的**拒绝，不是「挑了一个」。
    assert current_baseline_version(seeded, "V-SMP", EvidenceChannel.MEASURED) is None
    assert count_router_logs(seeded) == 2, "两条拒绝各自留痕（红线：被拒也要可追溯）"


def test_the_reported_conflicts_are_the_ones_actually_arbitrated(
    router, seeded
) -> None:
    """**回归用例**：报告里的冲突必须就是**真正经过裁定**的那些。

    原先 `SubmitResult.conflicts` 对**全部** intent 重算一遍，包括已经被
    判定表拒掉的那些 —— 于是报告会写「measured 胜过了 experienced」，
    而那条 experienced 其实是被判定表拒的、根本没进冲突裁定。
    又是一次「用一个不成立的理由解释系统的行为」。
    """
    submitted = router.submit(
        request(
            "R2",
            # 同槽位，但 experienced 在 R2 下**未注册** → 被判定表拒，不进裁定。
            m1_intent("I-1", variable="V-SE", basis="experienced"),
            m1_intent("I-2", variable="V-SE", basis="measured"),
        )
    )
    by_id = {r.intent_id: r for r in submitted.results}
    assert by_id["I-1"].status is IntentStatus.REJECTED
    assert "判定表" in by_id["I-1"].reason
    assert by_id["I-2"].status is IntentStatus.APPLIED

    assert submitted.conflicts == [], (
        "只有一条 intent 真的走到了写入，没有同槽位相撞 —— "
        "报告里不该出现一个把被拒 intent 也算进去的『冲突』"
    )


def test_a_multi_channel_batch_is_not_reported_as_conflicting(router, seeded) -> None:
    """**回归用例（P5.2）**：一次测绘写三个通道 —— 零冲突、三条全生效。"""
    submitted = router.submit(
        request(
            "R2",
            m1_intent("I-1", variable="V-SE", channel="measured", score=3.0),
            m1_intent("I-2", variable="V-SE", channel="experienced", score=7.0),
            m1_intent("I-3", variable="V-SE", channel="stated", score=5.0),
            source_ref="RUN-A",
        )
    )
    assert submitted.conflicts == []
    assert {r.status for r in submitted.results} == {IntentStatus.APPLIED}
    for channel in ("measured", "experienced", "stated"):
        assert current_baseline_version(seeded, "V-SE", EvidenceChannel(channel))
