"""任务不明确时**反问**（P13）。

★ 政策变更（判据收紧）：`plan_gate` 现在**不再拒绝**。缺 `goal` 时
（没问过 / 问过没答 / 答过没写进定义区）一律放行，把这些槽位放进 `guessed`，
由 `guess_note()` 写进计划自己的理由（"这是按助手判断排的"）。

为什么改：原来"问过"之后计划就**永久锁死** —— 界面上没有回答入口
（`answer/dismiss` 只有 API），模型也不能自己搁置。一条把功能锁死的门，
比它防的风险更贵；而"这是猜的"这件事写进 `plan_revision.reason` 之后
反而更可核对：三个月后回看这条计划，能看见当时缺了哪一项。

这一组测试盯的因此变成三件事：

1. **闸门只记录，不拒绝。** 它把"缺了哪个槽位、为什么缺"变成一句
   可核对的话落在计划上，而不是一次拒绝。
2. **答案真的写进定义区**（带修订史）。只把答案挂在问题行上的话，
   定义区仍然是空的 —— 下一次排计划仍然要带"这是猜的"。
3. **搁置 ≠ 没答，但两者都放行**。区别只在理由那一句怎么写。

另外两条纯逻辑的边界：`goal=" "`（一个空格）**不算填过**；
闸门只对**助手**生效（用户自己排计划不该被"这是猜的"署名）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import InvalidField
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

    ★ 目标空着的会话现在**直接就能造**：`draft → active` 不再要求先写目标
    （那条前置条件已被撤掉，理由见
    `test_lifecycle.py::test_activation_no_longer_requires_a_goal`）。
    `goal` 参数仍然保留，因为"一开始就说了目标"与"什么都没说"
    是两种真实处境，而计划上的猜测说明必须只出现在后者。
    """
    folder = writes.create_folder(db, name="英语学习")
    row = writes.create_session(
        db,
        folder_id=folder.folder_id,
        title="学点什么",
        input_goal=goal,
        intent_raw="帮我复习第三章",
        intent_confidence=InputConfidence.STATED,
    )
    db.flush()
    writes.transition(db, row.session_id, target="active", expected_version=row.version)
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


def test_the_gate_does_not_block_it_records_the_guess() -> None:
    """★ 政策变更：缺 `goal` 且**没问过** —— 旧断言是"先拦下来让助手去问"。

    新判据：放行不会写出矛盾数据（计划仍然有版本号、`goal_derived` 空着也自洽），
    所以门该开；而要留痕的是"这一版是按助手判断排的"。
    于是 `blocked`（拒绝依据）永远是空的，缺的槽位只进 `guessed`。
    """
    gate = clarify.plan_gate({"goal": None}, {})
    assert gate.ok
    assert gate.blocked == ()
    assert [slot.name for slot in gate.guessed] == ["goal"]
    assert gate.reasons == ("没问过",)
    message = gate.message()
    assert "不是拦你，是记下来" in message
    # 话虽不拦，仍然要写明下一步怎么做 —— 想更准就去问一句。
    assert "ask_about_task" in message


def test_an_open_question_is_recorded_as_a_guess_not_a_block() -> None:
    """★ 问过没答：仍然放行，只是理由写成"已经问过，还在等答案"。

    旧行为下这一格是**最致命的**：计划从这里开始永久锁死 ——
    界面上没有回答入口，模型也不能自己搁置。
    """
    gate = clarify.plan_gate({"goal": None}, {"goal": clarify.STATUS_OPEN})
    assert gate.ok
    assert gate.blocked == ()
    assert [slot.name for slot in gate.guessed] == ["goal"]
    assert "还在等答案" in gate.message()


def test_an_answered_question_that_left_the_slot_empty_is_still_recorded() -> None:
    """★ 答过、但定义区还是空的 —— 这是唯一一处"不该发生"的状态。

    旧断言把它拦下来（沉默放行会让计划建在一个空槽位上）。
    现在同样放行，但理由里点明"问过，但答案没有写进定义区"：
    这样计划是可用的，而那条没写进去的答案仍然有人能看见、去修。
    """
    gate = clarify.plan_gate({"goal": None}, {"goal": clarify.STATUS_ANSWERED})
    assert gate.ok
    assert [slot.name for slot in gate.guessed] == ["goal"]
    assert "答案没有写进定义区" in gate.message()


def test_a_dismissed_question_allows_the_plan_but_marks_it_as_a_guess() -> None:
    gate = clarify.plan_gate({"goal": None}, {"goal": clarify.STATUS_DISMISSED})
    assert gate.ok
    assert [slot.name for slot in gate.guessed] == ["goal"]
    note = clarify.guess_note(gate.guessed)
    assert "goal" in note and "按助手判断排的" in note


def test_every_guessed_slot_has_exactly_one_reason() -> None:
    """★ 不变量：`guessed` 与 `reasons` 必须**等长且一一对应**。

    这条不是排版洁癖：`PlanGate.message()` 用
    `zip(self.guessed, self.reasons, strict=True)` 把它们配对，少一条就抛
    `ValueError` —— 而 `message()` 恰恰是在"说明这版计划是猜的"那条路上
    被调用，也就是最需要它说话的时候。

    曾经真的断过：`STATUS_DISMISSED` 那个分支只把槽位放进 `guessed`、
    忘了追加 reason。单个槽位时没问题，两个槽位（一个搁置 + 一个问过没写进）
    就会在 `message()` 里炸。所以这里穷举三种状态两两组合。
    """
    statuses = [clarify.STATUS_OPEN, clarify.STATUS_ANSWERED, clarify.STATUS_DISMISSED]
    combos = [(None, None), *[(a, b) for a in statuses for b in statuses]]
    for goal_status, starting_point_status in combos:
        questions = {
            name: status
            for name, status in (
                ("goal", goal_status),
                ("starting_point", starting_point_status),
            )
            if status is not None
        }
        gate = clarify.plan_gate({"goal": None, "starting_point": None}, questions)
        assert len(gate.reasons) == len(gate.guessed), (
            f"{questions}：guessed 与 reasons 长度不一 —— "
            f"message() 会在 zip(strict=True) 上抛 ValueError"
        )
        # 不抛就是它该有的样子；同时每条原因都要落在某个槽位上
        assert gate.message().strip()


def test_a_filled_goal_needs_no_note() -> None:
    gate = clarify.plan_gate({"goal": "背完 1500 词"}, {})
    assert gate.ok
    assert gate.guessed == ()
    assert clarify.guess_note(gate.guessed) == ""


# --------------------------------------------------------------------------- #
# 问
# --------------------------------------------------------------------------- #


def test_asking_is_recorded_and_the_plan_carries_the_guess_instead_of_being_refused(
    session: Session,
) -> None:
    """★ 政策变更：问过之后**不再**挡住建计划。

    旧断言是"问了但没答的时候，助手不应该能建计划"。而现在挡住计划的代价是
    "这个会话的计划永远建不出来"（界面上没有回答入口）—— 那是一条把功能
    锁死的门。所以问这件事仍然有后果，但后果变成了**可核对的一句话**：
    计划建得出来，而它的修订理由写着"`goal` 仍无答案：这是按助手判断排的"。
    """
    row = _live_session(session)
    item = writes.ask_about_task(
        session, row.session_id, slot="goal", question="这次你想达成什么？"
    )
    assert item.clarification_id.startswith("CL-")
    assert item.status is ClarificationStatus.OPEN
    assert reads.open_clarification_for_slot(session, row.session_id, "goal") is item

    plan = writes.create_plan(
        session,
        row.session_id,
        steps=_one_step(),
        expected_session_version=_version(session, row.session_id),
        changed_by=ChangedBy.AGENT,
        reason="先按理解排一版",
    )
    revision = reads.list_plan_revisions(session, plan.plan_id)[0]
    assert "`goal`" in revision.reason
    assert "按助手判断排的" in revision.reason
    # 计划本身是完整可用的：版本 1、有步骤 —— 不是半个东西。
    assert plan.version == 1
    assert reads.plan_steps(session, plan.plan_id)


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

    闸门现在不拒绝了，但这条形态差异仍然要钉：**判断错了，"这是猜的"
    那句话就不会被写下来** —— 而它正是新政策下唯一的后果。
    所以这里用工具层那种写法调一次，断言理由里带上了猜测说明。
    """
    row = _live_session(session)
    plan = writes.create_plan(
        session,
        row.session_id,
        steps=_one_step(),
        expected_session_version=_version(session, row.session_id),
        changed_by="agent",  # ← 字符串，不是枚举
        reason="先排一版",
    )
    revision = reads.list_plan_revisions(session, plan.plan_id)[0]
    assert "按助手判断排的" in revision.reason, (
        "传字符串 `agent` 时猜测说明没被写上 —— changed_by 的比较又退化成 `is` 了"
    )


def test_a_user_plan_carries_no_guess_note(session: Session) -> None:
    """★ 闸门只给**助手**排的计划署名"这是猜的"。

    用户自己排的计划不需要这句：缺什么他自己知道，而把"这是猜的"写到
    他亲手排的计划上，是在伪造一条关于他的事实。
    """
    row = _live_session(session)
    plan = writes.create_plan(
        session,
        row.session_id,
        steps=_one_step(),
        expected_session_version=_version(session, row.session_id),
        changed_by=ChangedBy.USER,
        reason="我自己排的",
    )
    revision = reads.list_plan_revisions(session, plan.plan_id)[0]
    assert "按助手判断排的" not in revision.reason


def test_an_agent_with_an_empty_goal_creates_a_plan_that_says_it_is_a_guess(
    session: Session,
) -> None:
    """★ 这条是政策变更最直接的落点。

    旧断言：目标空着时助手**不能**建计划（闸门拦住它）。
    新行为：计划建得出来，而"缺了 `goal`、这一版是按助手判断排的"
    写进 `plan_revision.reason` —— 那是可核对的，比一次拒绝更有用：
    拒绝只留下"没做成"，这句话留下"当时缺什么、谁决定的"。
    """
    row = _live_session(session)
    plan = writes.create_plan(
        session,
        row.session_id,
        steps=_one_step(),
        expected_session_version=_version(session, row.session_id),
        changed_by=ChangedBy.AGENT,
        reason="按理解排一版",
    )
    revision = reads.list_plan_revisions(session, plan.plan_id)[0]
    assert "`goal`" in revision.reason
    assert "仍无答案" in revision.reason
    assert "按助手判断排的" in revision.reason


def test_the_same_guess_note_applies_to_revising_a_plan(session: Session) -> None:
    """★ 建与改走同一个闸门：改计划同样是在对一个任务做承诺。

    旧断言是"目标空着时助手不该能改计划"。现在改得动，但新版本的
    `plan_revision.reason` 同样会带上"这是猜的"—— 否则旧版本带了署名、
    新版本不带，回看时就分不清哪一版是猜的。
    """
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
    revised = writes.revise_plan(
        session,
        row.session_id,
        steps=_one_step(),
        change="加一步",
        reason="看看",
        expected_version=plan.version,
        changed_by=ChangedBy.AGENT,
    )
    latest = reads.list_plan_revisions(session, revised.plan_id)[-1]
    assert "`goal`" in latest.reason
    assert "按助手判断排的" in latest.reason


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
