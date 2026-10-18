"""状态机与写准入表 —— 纯逻辑，不需要数据库。

用「数据表 + 逐行穷举」而不是 `if state == ...` 的理由：
决定拒绝什么的东西，必须能被**测试枚举**。一段 if-else 链能被测试的只有
作者当时想到的那几条路径，而 WRITE_GUARDS 能被 `parametrize` 全量覆盖。
"""

from __future__ import annotations

import pytest

from app.core.errors import StateNotWritable
from app.session_domain.enums import SessionOperation, SessionState
from app.session_domain.guards import (
    TRANSITIONS,
    WRITE_GUARDS,
    allowed_targets,
    assert_transition,
    assert_writable,
    can_transition,
    guard_for,
    is_writable,
    overlapping_operations,
    unguarded_operations,
)

ALL_STATES = list(SessionState)


# --------------------------------------------------------------------------- #
# 表本身的完整性
# --------------------------------------------------------------------------- #


def test_every_operation_declares_where_it_is_writable() -> None:
    """新增一个操作却忘了表态 → 这里红。

    这是本模块最重要的测试：漏一行的后果是那个操作要么哪里都写不了，
    要么被某处硬编码放行 —— 两种都会在很久以后才被发现。
    """
    assert unguarded_operations() == []


def test_no_operation_is_declared_twice() -> None:
    assert overlapping_operations() == []


def test_every_guard_explains_itself() -> None:
    """准入表里的每一行都要写清"为什么是这些状态"。

    没有理由的准入规则，下一个人只敢往上加状态，不敢删 —— 半年后
    `completed` 可以写一切，而没有任何一条规定被违反过。
    """
    for guard in WRITE_GUARDS:
        assert guard.reason.strip(), f"{guard.operation} 缺少理由"
        assert guard.states, f"{guard.operation} 允许的状态集合为空"


def test_every_transition_explains_itself() -> None:
    for edge in TRANSITIONS:
        assert edge.label.strip(), f"{edge.source}→{edge.target} 缺少界面文字"
        assert edge.reason.strip(), f"{edge.source}→{edge.target} 缺少理由"


def test_guard_lookup_fails_loudly_for_an_unknown_operation() -> None:
    class _Fake:
        pass

    with pytest.raises(LookupError):
        guard_for(_Fake())  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 终态的例外口子只有一条
# --------------------------------------------------------------------------- #


def test_note_is_the_only_operation_writable_in_every_state() -> None:
    """用户裁定：`completed` 之后仍能写笔记，且**只有**笔记。"""
    always = {
        guard.operation
        for guard in WRITE_GUARDS
        if set(ALL_STATES) <= guard.states
    }
    assert always == {
        SessionOperation.ADD_NOTE,
        # 状态转移本身不看状态（它看 TRANSITIONS 边表），因此也在这一列 ——
        # 它不是"内容写入"，不该和笔记混为一谈，所以下面单独再断言一次。
        SessionOperation.TRANSITION,
    }


def test_completed_refuses_content_writes_but_accepts_notes() -> None:
    assert is_writable(SessionState.COMPLETED, SessionOperation.ADD_NOTE)
    for operation in (
        SessionOperation.UPDATE_INPUT,
        SessionOperation.UPDATE_MOTIVATION,
        SessionOperation.UPDATE_SNAPSHOT,
        SessionOperation.CREATE_PLAN,
        SessionOperation.REVISE_PLAN,
        SessionOperation.MARK_STEP,
        SessionOperation.RECORD_CHECKPOINT,
        SessionOperation.ADD_STAGE_ITEM,
        SessionOperation.ADD_CHAT,
        SessionOperation.ADD_MILESTONE,
        SessionOperation.WRITE_SUMMARY,
    ):
        assert not is_writable(SessionState.COMPLETED, operation), operation


def test_abandoned_can_still_settle_stage_items_but_not_add_them() -> None:
    """M7 §8：放弃的会话「部分条目可沉淀」—— 因此能标记归宿，不能再新增。"""
    assert is_writable(SessionState.ABANDONED, SessionOperation.SETTLE_STAGE_ITEM)
    assert not is_writable(SessionState.ABANDONED, SessionOperation.ADD_STAGE_ITEM)


def test_chat_is_only_possible_while_active() -> None:
    """「讨论要落到这件事上」的结构保证：归档之后还能聊，结论就无处可去。"""
    assert [s.value for s in ALL_STATES if is_writable(s, SessionOperation.ADD_CHAT)] == [
        "active"
    ]


# --------------------------------------------------------------------------- #
# 拒绝的形状
# --------------------------------------------------------------------------- #


def test_refusal_carries_state_operation_and_allowed_states() -> None:
    with pytest.raises(StateNotWritable) as excinfo:
        assert_writable(SessionState.COMPLETED, SessionOperation.ADD_CHAT)
    error = excinfo.value
    assert error.code == "state_not_writable"
    assert error.http_status == 409
    assert error.detail["state"] == "completed"
    assert error.detail["operation"] == "add_chat"
    assert error.detail["allowed_states"] == ["active"]
    # 拒绝理由里要出现"为什么"，而不只是"不行"。
    assert "执行" in error.message


# --------------------------------------------------------------------------- #
# 状态转移边表：25 个组合逐个验
# --------------------------------------------------------------------------- #


def test_transition_matrix_is_exactly_the_declared_edges() -> None:
    declared = {(edge.source, edge.target) for edge in TRANSITIONS}
    assert len(declared) == len(TRANSITIONS), "边表里有重复的边"
    for source in ALL_STATES:
        for target in ALL_STATES:
            assert can_transition(source, target) is ((source, target) in declared), (
                f"{source.value} → {target.value} 的判定与边表不一致"
            )


def test_draft_cannot_jump_straight_to_completed() -> None:
    """跳过执行与整理直接归档，等于没发生任何学习。"""
    assert not can_transition(SessionState.DRAFT, SessionState.COMPLETED)
    with pytest.raises(StateNotWritable):
        assert_transition(SessionState.DRAFT, SessionState.COMPLETED)


def test_abandoned_is_terminal() -> None:
    assert allowed_targets(SessionState.ABANDONED) == []
    for target in ALL_STATES:
        if target is SessionState.ABANDONED:
            continue
        assert not can_transition(SessionState.ABANDONED, target)


def test_reopening_a_completed_session_takes_two_steps() -> None:
    """`completed → review → active`：刻意让"重新打开"需要两次确认。"""
    assert not can_transition(SessionState.COMPLETED, SessionState.ACTIVE)
    assert can_transition(SessionState.COMPLETED, SessionState.REVIEW)
    assert can_transition(SessionState.REVIEW, SessionState.ACTIVE)


def test_forbidden_edge_tells_the_client_where_it_can_go() -> None:
    with pytest.raises(StateNotWritable) as excinfo:
        assert_transition(SessionState.REVIEW, SessionState.DRAFT)
    assert excinfo.value.detail["allowed_targets"] == [
        "abandoned",
        "active",
        "completed",
    ]


def test_allowed_targets_matches_the_edge_table() -> None:
    for state in ALL_STATES:
        expected = sorted(
            edge.target.value for edge in TRANSITIONS if edge.source is state
        )
        assert allowed_targets(state) == expected
