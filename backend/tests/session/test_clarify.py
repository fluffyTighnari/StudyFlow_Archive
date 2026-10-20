"""任务不明确时**反问**（P13）。

这一组测试盯的是三件在界面上都看不出来的事：

1. **闸门拦的是助手，不是用户。** 反过来的话，用户会看到
   「我的计划被一个我没答的问题锁住了」—— 而那是他的会话。
2. **答案真的写进定义区**（带修订史）。只把答案挂在问题行上的话，
   定义区仍然是空的，下一个回合闸门**再拦一次**，
   而用户会觉得"我明明答了"。
3. **搁置 ≠ 没答**。用户说"先按你的判断来"是一个决定，
   它允许计划继续走，但计划上必须写着"这是猜的" ——
   否则三个月后没人分得清那条计划是因为他知道，还是因为模型以为他知道。

外加一条纯逻辑的边界：`goal=" "`（一个空格）**不算填过**。
只判 `None` 的话，闸门会放行，而计划仍然建立在"什么都没说"之上。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.errors import InvalidField, MissingPrerequisite
from app.session_domain import clarify, reads, writes
from app.session_domain.enums import (
    ChangedBy,
    ClarificationStatus,
    InputConfidence,
    PlanStepStatus,
)
from app.session_domain.writes import StepDraft


def _live_session(db: Session, *, goal: str | None = None):
    """一个已激活、**目标空着**的会话。

    ★ 目标空着的会话只能这样造出来：先写一个目标、激活、再把它清空。
    因为状态机**已经**要求 `draft → active` 必须有目标
    （`MissingPrerequisite: 激活之前要先写下这次会话的目标`）。
    这一点正好说明了这道闸门的位置：它拦的不是"一开始就没写目标"，
    而是**用户后来改主意**（把目标清掉）之后，助手还想照着旧的理解改计划。
    两处检查说的是同一件事，但一处是状态机、一处是"计划与定义是否一致"。
    """
    folder = writes.create_folder(db, name="英语学习")
    row = writes.create_session(
        db,
        folder_id=folder.folder_id,
        title="学点什么",
        input_goal=goal if goal is not None else "先随便看看第三章",
        intent_raw="帮我复习第三章",
        intent_confidence=InputConfidence.STATED,
    )
    db.flush()
    writes.transition(db, row.session_id, target="active", expected_version=row.version)
    db.flush()
    if goal is None:
        writes.update_input(
            db,
            row.session_id,
            changes={"goal": None},
            expected_version=reads.get_input(db, row.session_id).version,
            changed_by=ChangedBy.USER,
            reason="先清掉，等我想清楚",
        )
        db.flush()
    return row


def _one_step() -> list[StepDraft]:
    return [StepDraft(action="读一遍第三章并写下三个问题")]


def _version(db: Session, session_id: str) -> int:
    return reads.get_session(db, session_id).version


def input_row_version(db: Session, session_id: str) -> int:
    return reads.get_input(db, session_id).version


# --------------------------------------------------------------------------- #
# 纯逻辑
# --------------------------------------------------------------------------- #


def test_only_the_goal_is_a_hard_gate() -> None:
    """★ 硬拦只留一个。

    把三个都做成硬拦的后果不是更严谨，而是**每次会话都要先答三道题** ——
    一条经常被触发的规矩最后一定会被绕过去，而绕过的方式通常是"随便填一句"，
    那比不问更坏：它给了一个假的确定感。
    """
    assert clarify.HARD_SLOTS == ("goal",)
    assert {slot.name for slot in clarify.SLOTS} == {
        "goal",
        "starting_point",
        "success_criteria",
    }


def test_a_whitespace_only_goal_does_not_count_as_filled() -> None:
    """★ `" "` 不是内容。只判 `None` 会让闸门放行，而计划仍然建立在"什么都没说"上。"""
    assert clarify.is_empty(" ")
    assert clarify.is_empty("\n\t")
    assert clarify.is_empty(None)
    assert not clarify.is_empty("背完 1500 词")


def test_the_askable_slots_are_exactly_the_ones_with_a_column() -> None:
    """★ 问一个答案没地方落的字段，表现是"用户答了、定义区还是空的、闸门再拦一次"。"""
    from app.agent.tools import ASKABLE_SLOTS
    from app.session_domain.schemas import InputUpdate

    fields = set(InputUpdate.model_fields)
    assert set(ASKABLE_SLOTS) == set(clarify.SLOT_BY_NAME)
    assert set(ASKABLE_SLOTS) <= fields, "提问的槽位必须是定义区真实存在的列"


def test_the_gate_asks_before_it_allows_a_plan() -> None:
    gate = clarify.plan_gate({"goal": None}, {})
    assert not gate.ok
    assert [slot.name for slot in gate.blocked] == ["goal"]
    assert "没问过" in gate.message()
    assert "ask_about_task" in gate.message(), "拒绝的话里要写明下一步怎么做"


def test_an_open_question_blocks_instead_of_being_treated_as_answered() -> None:
    gate = clarify.plan_gate({"goal": None}, {"goal": clarify.STATUS_OPEN})
    assert not gate.ok
    assert "还在等答案" in gate.message()


def test_an_answered_question_that_left_the_slot_empty_still_blocks() -> None:
    """★ 这是唯一一处"不该发生"的状态：答过、但定义区还是空的。

    沉默放行会让计划建在一个空槽位上；当成"没答"处理并说明，
    才会有人去修那条没写进去的答案。
    """
    gate = clarify.plan_gate({"goal": None}, {"goal": clarify.STATUS_ANSWERED})
    assert not gate.ok
    assert "答案没写进去" in gate.message()


def test_a_dismissed_question_allows_the_plan_but_marks_it_as_a_guess() -> None:
    gate = clarify.plan_gate({"goal": None}, {"goal": clarify.STATUS_DISMISSED})
    assert gate.ok
    assert [slot.name for slot in gate.guessed] == ["goal"]
    note = clarify.guess_note(gate.guessed)
    assert "goal" in note and "按助手判断排的" in note


def test_a_filled_goal_needs_no_note() -> None:
    gate = clarify.plan_gate({"goal": "背完 1500 词"}, {})
    assert gate.ok
    assert gate.guessed == ()
    assert clarify.guess_note(gate.guessed) == ""


# --------------------------------------------------------------------------- #
# 问
# --------------------------------------------------------------------------- #


def test_asking_records_the_question_and_opens_the_gate_only_after_an_answer(
    session: Session,
) -> None:
    row = _live_session(session)
    item = writes.ask_about_task(
        session, row.session_id, slot="goal", question="这次你想达成什么？"
    )
    assert item.clarification_id.startswith("CL-")
    assert item.status is ClarificationStatus.OPEN
    assert reads.open_clarification_for_slot(session, row.session_id, "goal") is item

    # 问过之后仍然拦着 —— 因为**还没答**
    with_problem = writes.ask_about_task
    try:
        writes.create_plan(
            session,
            row.session_id,
            steps=_one_step(),
            expected_session_version=_version(session, row.session_id),
            changed_by=ChangedBy.AGENT,
        )
        raise AssertionError("问了但没答的时候，助手不应该能建计划")
    except MissingPrerequisite as exc:
        assert "还在等答案" in str(exc)
    assert with_problem is not None


def test_a_second_question_on_the_same_slot_is_refused(session: Session) -> None:
    """★ 重复提问的后果不是多一条记录：闸门以为"问过了"、界面显示两个一样的问题。"""
    row = _live_session(session)
    writes.ask_about_task(session, row.session_id, slot="goal", question="要达成什么？")
    try:
        writes.ask_about_task(session, row.session_id, slot="goal", question="到底要达成什么？")
        raise AssertionError("同一个槽位不该能问第二次")
    except InvalidField as exc:
        assert "已经有一个还没答的问题" in str(exc)


def test_asking_about_a_slot_that_already_has_content_is_refused(
    session: Session,
) -> None:
    """★ 问一个已经知道答案的问题，会让人以为它还是空的。"""
    row = _live_session(session, goal="背完 1500 词")
    try:
        writes.ask_about_task(session, row.session_id, slot="goal", question="要达成什么？")
        raise AssertionError("槽位非空时不该能提问")
    except InvalidField as exc:
        assert "已经有内容" in str(exc)


def test_asking_about_a_field_that_is_not_in_the_definition_is_refused(
    session: Session,
) -> None:
    row = _live_session(session)
    try:
        writes.ask_about_task(session, row.session_id, slot="mood", question="心情如何？")
        raise AssertionError("定义区没有这个字段，问它没有地方落答案")
    except InvalidField as exc:
        assert "不是任务定义区的字段" in str(exc)


# --------------------------------------------------------------------------- #
# 答
# --------------------------------------------------------------------------- #


def test_answering_writes_the_slot_and_unblocks_the_plan(session: Session) -> None:
    """★ 这是整套机制的关键一步：答案必须落到**定义区**，不是只存在问题行上。"""
    row = _live_session(session)
    item = writes.ask_about_task(
        session, row.session_id, slot="goal", question="这次你想达成什么？"
    )
    answered, input_row = writes.answer_clarification(
        session,
        row.session_id,
        item.clarification_id,
        answer="把第三章的公式推导全部自己推一遍",
        expected_version=input_row_version(session, row.session_id),
    )
    assert answered.status is ClarificationStatus.ANSWERED
    assert answered.answer == "把第三章的公式推导全部自己推一遍"
    assert input_row.goal == "把第三章的公式推导全部自己推一遍"

    # 修订史里**有一条记着这次回答**（清空 goal 那条另算），且记的是**用户**改的
    history = reads.list_input_revisions(session, row.session_id)
    answered_rev = [rev for rev in history if item.clarification_id in (rev.reason or "")]
    assert answered_rev and answered_rev[0].field == "goal"
    assert answered_rev[0].changed_by is ChangedBy.USER
    assert answered_rev[0].new_value == "把第三章的公式推导全部自己推一遍"

    # 现在助手可以建计划了
    writes.create_plan(
        session,
        row.session_id,
        steps=_one_step(),
        expected_session_version=_version(session, row.session_id),
        changed_by=ChangedBy.AGENT,
    )


def test_an_empty_answer_is_refused(session: Session) -> None:
    """★ 空答案会让闸门以为它解决了，而定义区还是空的。"""
    row = _live_session(session)
    item = writes.ask_about_task(session, row.session_id, slot="goal", question="要达成什么？")
    try:
        writes.answer_clarification(
            session,
            row.session_id,
            item.clarification_id,
            answer="   ",
            expected_version=input_row_version(session, row.session_id),
        )
        raise AssertionError("空答案不该被接受")
    except InvalidField as exc:
        assert "不能是空的" in str(exc)


def test_answering_twice_is_refused(session: Session) -> None:
    row = _live_session(session)
    item = writes.ask_about_task(session, row.session_id, slot="goal", question="要达成什么？")
    writes.answer_clarification(
        session,
        row.session_id,
        item.clarification_id,
        answer="推一遍公式",
        expected_version=input_row_version(session, row.session_id),
    )
    try:
        writes.answer_clarification(
            session,
            row.session_id,
            item.clarification_id,
            answer="其实是背单词",
            expected_version=input_row_version(session, row.session_id),
        )
        raise AssertionError("已经答过的问题不该能再答一次")
    except InvalidField as exc:
        assert "已经结案" in str(exc)


# --------------------------------------------------------------------------- #
# 搁置
# --------------------------------------------------------------------------- #


def test_dismissing_requires_a_reason_and_marks_the_plan_as_a_guess(
    session: Session,
) -> None:
    """★ 搁置也要说一句为什么 —— 它是"这条计划为什么是猜的"的唯一解释。"""
    row = _live_session(session)
    item = writes.ask_about_task(session, row.session_id, slot="goal", question="要达成什么？")
    try:
        writes.dismiss_clarification(
            session, row.session_id, item.clarification_id, reason="  "
        )
        raise AssertionError("搁置必须有理由")
    except InvalidField as exc:
        assert "搁置也要说一句为什么" in str(exc)

    writes.dismiss_clarification(
        session, row.session_id, item.clarification_id, reason="先随便看看"
    )
    plan = writes.create_plan(
        session,
        row.session_id,
        steps=_one_step(),
        expected_session_version=_version(session, row.session_id),
        changed_by=ChangedBy.AGENT,
        reason="按理解排一版",
    )
    revision = reads.list_plan_revisions(session, plan.plan_id)[0]
    assert "按助手判断排的" in revision.reason, (
        "计划上必须写着这是猜的 —— 否则三个月后没人分得清那条计划"
        "是因为他知道，还是因为模型以为他知道"
    )


# --------------------------------------------------------------------------- #
# 闸门只约束助手
# --------------------------------------------------------------------------- #


def test_the_gate_does_not_block_the_user(session: Session) -> None:
    """★ 解释权永远在用户手里。

    他可以一边挂着一个没答的问题，一边自己把计划建了 ——
    把用户也拦住的表现是「我的计划被一个我没答的问题锁住了」，
    而那是在用一条防模型乱猜的规矩去限制用户。
    """
    row = _live_session(session)
    writes.ask_about_task(session, row.session_id, slot="goal", question="要达成什么？")
    plan = writes.create_plan(
        session,
        row.session_id,
        steps=_one_step(),
        expected_session_version=_version(session, row.session_id),
        changed_by=ChangedBy.USER,
        reason="我自己先排一版",
    )
    assert plan.plan_id.startswith("PL-")


def test_the_gate_fires_for_the_string_form_the_tool_layer_actually_uses(
    session: Session,
) -> None:
    """★ 调用点有两种形态：领域内部传枚举，工具层传裸字符串 `"agent"`。

    第一版闸门写成 `if changed_by is not ChangedBy.AGENT: return ""` ——
    裸字符串**不是同一个对象**，于是那一次比较直接返回空、**闸门整条失效**，
    而所有传枚举的单测照样全绿。端到端把它抓了出来
    （`e2echeck` 里「助手改计划没有被拦」那一格）。
    所以这条测试用**工具层那种写法**再钉一遍。
    """
    row = _live_session(session)
    with pytest.raises(MissingPrerequisite):
        writes.create_plan(
            session,
            row.session_id,
            steps=_one_step(),
            expected_session_version=_version(session, row.session_id),
            changed_by="agent",  # ← 字符串，不是枚举
        )


def test_an_agent_with_an_empty_goal_cannot_create_a_plan(session: Session) -> None:
    """★ 这条是这套机制存在的全部理由。

    `update_task_definition` 只在用户自己要求时才动得了（M8 §4.2），
    所以目标空着时助手**没有别的办法**知道要做什么 ——
    它只能问，或者猜。建计划这条路必须先被挡住。
    """
    row = _live_session(session)
    try:
        writes.create_plan(
            session,
            row.session_id,
            steps=_one_step(),
            expected_session_version=_version(session, row.session_id),
            changed_by=ChangedBy.AGENT,
        )
        raise AssertionError("目标空着时助手不该能建计划")
    except MissingPrerequisite as exc:
        assert "`goal`" in str(exc)
        assert "ask_about_task" in str(exc)
        assert exc.detail["blocked_slots"] == ["goal"]


def test_the_same_gate_applies_to_revising_a_plan(session: Session) -> None:
    """★ 只拦"建"不拦"改"等于没拦：改计划同样是在对一个任务做承诺。"""
    row = _live_session(session, goal="先有个目标")
    writes.create_plan(
        session,
        row.session_id,
        steps=_one_step(),
        expected_session_version=_version(session, row.session_id),
        changed_by=ChangedBy.USER,
    )
    # 用户把目标清空了（改主意了，或者那本来就是随手填的）
    writes.update_input(
        session,
        row.session_id,
        changes={"goal": None},
        expected_version=input_row_version(session, row.session_id),
        changed_by=ChangedBy.USER,
        reason="先清掉",
    )
    plan = reads.require_plan(session, row.session_id)
    try:
        writes.revise_plan(
            session,
            row.session_id,
            steps=_one_step(),
            change="加一步",
            reason="看看",
            expected_version=plan.version,
            changed_by=ChangedBy.AGENT,
        )
        raise AssertionError("目标空着时助手不该能改计划")
    except MissingPrerequisite as exc:
        assert "`goal`" in str(exc)


def test_the_gate_reads_the_latest_status_per_slot(session: Session) -> None:
    """★ 取**最近一次**，不是最严的一次。

    用户在第三轮答了 goal，第一轮那个被搁置的问题不应该继续挡着；
    反过来，他刚把问题重新问一次，也不能被旧的 `answered` 放行。
    """
    row = _live_session(session)
    first = writes.ask_about_task(session, row.session_id, slot="goal", question="要达成什么？")
    writes.dismiss_clarification(
        session, row.session_id, first.clarification_id, reason="先不管"
    )
    second = writes.ask_about_task(session, row.session_id, slot="goal", question="那到底要什么？")
    status = reads.latest_status_by_slot(session, row.session_id)
    assert status["goal"] is ClarificationStatus.OPEN

    writes.answer_clarification(
        session,
        row.session_id,
        second.clarification_id,
        answer="推一遍公式",
        expected_version=input_row_version(session, row.session_id),
    )
    assert reads.latest_status_by_slot(session, row.session_id)["goal"] is (
        ClarificationStatus.ANSWERED
    )


def test_the_open_question_comes_first_in_the_list(session: Session) -> None:
    """★ 一进会话就要看见那个挡着计划的提问，而不是被旧的已答问题压下去。"""
    row = _live_session(session)
    first = writes.ask_about_task(
        session, row.session_id, slot="starting_point", question="你现在会到哪？"
    )
    writes.answer_clarification(
        session,
        row.session_id,
        first.clarification_id,
        answer="会一点点",
        expected_version=input_row_version(session, row.session_id),
    )
    second = writes.ask_about_task(session, row.session_id, slot="goal", question="要达成什么？")
    rows = reads.list_clarifications(session, row.session_id)
    assert [r.clarification_id for r in rows][0] == second.clarification_id
    assert [r.clarification_id for r in reads.list_clarifications(session, row.session_id, only_open=True)] == [
        second.clarification_id
    ]


def test_plan_steps_are_untouched_by_the_gate(session: Session) -> None:
    """闸门只决定"能不能写"，不改写任何计划内容。"""
    row = _live_session(session)
    with_plan = _live_session(session, goal="有目标")
    plan = writes.create_plan(
        session,
        with_plan.session_id,
        steps=_one_step(),
        expected_session_version=_version(session, with_plan.session_id),
        changed_by=ChangedBy.USER,
    )
    steps = reads.plan_steps(session, plan.plan_id, version_no=plan.current_version)
    assert steps[0].status is PlanStepStatus.PENDING
    assert reads.get_plan(session, row.session_id) is None
