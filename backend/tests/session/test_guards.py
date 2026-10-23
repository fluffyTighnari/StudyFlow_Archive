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
# 「全状态可写」的集合：说话、笔记，以及转移本身
# --------------------------------------------------------------------------- #


def test_only_chat_and_note_are_writable_in_every_state() -> None:
    """★ 政策变更（判据收紧之后）：全状态可写的只有 `ADD_CHAT` / `ADD_NOTE` / `TRANSITION`。

    旧断言是「**只有**笔记能全状态写」，并把它写成"终态的唯一例外口子"。
    新判据只有一条：**不拦就会写出自相矛盾、或无法核对的数据才拒绝**。
    说话（append-only，不涨版本、不参与任何判定）与笔记（草稿纸）
    都不会制造矛盾数据，所以它们没有理由在任何状态被拦下；
    其余写入要么会改写"当时为什么这么总结"，要么会往历史表里填新数据，
    所以那不是"例外口子"，而是这本表真正区分出来的那一类。
    """
    always = {
        guard.operation
        for guard in WRITE_GUARDS
        if set(ALL_STATES) <= guard.states
    }
    assert always == {
        SessionOperation.ADD_CHAT,
        SessionOperation.ADD_NOTE,
        # 动机是理解任务的背景，不是对已封存计划的改写；随时可改。
        SessionOperation.UPDATE_MOTIVATION,
        # 状态转移本身不看状态（它看 TRANSITIONS 边表），因此也在这一列 ——
        # 它不是"内容写入"，不该和笔记混为一谈，所以下面单独再断言一次。
        SessionOperation.TRANSITION,
    }


def test_completed_refuses_content_writes_but_accepts_notes_and_chat() -> None:
    """归档之后：计划/定义区封存，但聊天、笔记、动机仍然可写。

    动机不是对已封存计划的改写；它帮下一次讨论理解「当初为什么」。
    """
    assert is_writable(SessionState.COMPLETED, SessionOperation.ADD_NOTE)
    assert is_writable(SessionState.COMPLETED, SessionOperation.ADD_CHAT)
    assert is_writable(SessionState.COMPLETED, SessionOperation.UPDATE_MOTIVATION)
    for operation in (
        SessionOperation.UPDATE_INPUT,
        SessionOperation.UPDATE_SNAPSHOT,
        SessionOperation.UPDATE_SNAPSHOT,
        SessionOperation.CREATE_PLAN,
        SessionOperation.REVISE_PLAN,
        SessionOperation.MARK_STEP,
        SessionOperation.RECORD_CHECKPOINT,
        SessionOperation.ADD_STAGE_ITEM,
        SessionOperation.ADD_MILESTONE,
        SessionOperation.COMPLETE_MILESTONE,
        SessionOperation.ADD_ATTACHMENT,
        SessionOperation.ATTACHMENT_REMOVE,
        SessionOperation.WRITE_SUMMARY,
    ):
        assert not is_writable(SessionState.COMPLETED, operation), operation


def test_abandoned_can_still_settle_stage_items_but_not_add_them() -> None:
    """M7 §8：放弃的会话「部分条目可沉淀」—— 因此能标记归宿，不能再新增。"""
    assert is_writable(SessionState.ABANDONED, SessionOperation.SETTLE_STAGE_ITEM)
    assert not is_writable(SessionState.ABANDONED, SessionOperation.ADD_STAGE_ITEM)


def test_review_is_open_for_every_write() -> None:
    """★ 整理期**不设门**（政策变更）。旧表里 review 被当成"执行已经结束"。

    但 review 的意义是"执行完了、还没归档"：这时改计划、标步骤、补资料
    都仍然是在**这张会话自己的记录**上工作，写出来的东西相互自洽、
    也能在归档前核对。把它关掉换来的只是流程上的先后感，
    代价却是"整理时发现还差一步，改不了"—— 那是系统替用户做的决定。
    """
    for operation in SessionOperation:
        assert is_writable(SessionState.REVIEW, operation), operation


def test_chat_is_writable_in_every_state() -> None:
    """★ 政策变更：说话不设门。

    旧断言是「chat 只能在 active」——它挡掉的正是最正常的那句话：
    新建一个会话之后连 hello 都说不出去（要说话先激活，要激活先……
    而模型在 draft 里根本说不上话）。消息是 append-only 的，
    不涨版本、不参与任何判定，所以没有任何数据理由拦它。
    """
    assert [s.value for s in ALL_STATES if is_writable(s, SessionOperation.ADD_CHAT)] == [
        state.value for state in ALL_STATES
    ]


# --------------------------------------------------------------------------- #
# 拒绝的形状
# --------------------------------------------------------------------------- #


def test_refusal_carries_state_operation_and_allowed_states() -> None:
    """拒绝仍要带齐"哪个状态、哪个操作、还能去哪、为什么"。

    这里换了一个仍然**真的会拒**的操作（`UPDATE_INPUT` 在 `completed`）：
    旧版本拿 `ADD_CHAT` 当样本，而它现在全状态可写 —— 保留那一行就等于
    把"拒绝的形状"钉在一条不再拒绝的路径上，测试会永远绿得没有意义。
    """
    with pytest.raises(StateNotWritable) as excinfo:
        assert_writable(SessionState.COMPLETED, SessionOperation.UPDATE_INPUT)
    error = excinfo.value
    assert error.code == "state_not_writable"
    assert error.http_status == 409
    assert error.detail["state"] == "completed"
    assert error.detail["operation"] == "update_input"
    assert error.detail["allowed_states"] == ["active", "draft", "review"]
    # 拒绝语只说“当前状态 + 操作 + 允许什么”，不再附一段原因。
    assert "允许：" in error.message
    assert "update_input" in error.message


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


def test_draft_can_be_archived_directly() -> None:
    """归档是用户自己的动作；规划期也可以直接归档，不再强制先激活。"""
    assert can_transition(SessionState.DRAFT, SessionState.COMPLETED)
    assert_transition(SessionState.DRAFT, SessionState.COMPLETED)


def test_abandoned_is_terminal() -> None:
    assert allowed_targets(SessionState.ABANDONED) == []
    for target in ALL_STATES:
        if target is SessionState.ABANDONED:
            continue
        assert not can_transition(SessionState.ABANDONED, target)


def test_reopening_a_completed_session_is_one_hop() -> None:
    """★ 政策变更：`completed → active` 是一条直边（原来必须绕 `review`）。

    旧断言刻意让"重新打开"需要两次确认 —— 那是纯流程摩擦：
    状态与时间戳在任何一跳上都是一致的，所以多出来的这一跳不防任何数据问题，
    只防"用户本来就想继续干"。`completed → review` 仍然保留（先看再动手那条路）。
    """
    assert can_transition(SessionState.COMPLETED, SessionState.ACTIVE)
    assert can_transition(SessionState.COMPLETED, SessionState.REVIEW)
    assert can_transition(SessionState.REVIEW, SessionState.ACTIVE)


def test_active_can_be_completed_directly() -> None:
    """★ 政策变更：`active → completed` 也是一条直边。

    旧行为下用户在 active 里点了"完成"，会先写完总结、再在转移那一步吃到
    一个状态错 —— 于是"点完成报了个状态错"。给了总结就能直接归档。
    """
    assert can_transition(SessionState.ACTIVE, SessionState.COMPLETED)
    assert can_transition(SessionState.ACTIVE, SessionState.REVIEW)


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
