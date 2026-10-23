"""复盘之后的感想（P15）—— 领域层。

按「两道门槛 + 四要素 + 指引是这次会话的」组织：

| 关心的事 | 测试 |
|---|---|
| 执行中不许写（那时写笔记） | `test_active_has_notes_not_reflections` |
| 没复盘过不许写 | `test_a_session_without_a_summary_is_refused` |
| 空感想不写 | `test_an_empty_reflection_is_refused` |
| 四要素分项存下来 | `test_the_four_elements_are_stored_separately` |
| 只给正文也能写（拼装不是必须） | `test_a_plain_body_is_accepted` |
| 指引指向**这次会话**的真实数据 | `test_the_guide_quotes_this_session` |
| 指引不打分、也不生成文字 | `test_the_guide_only_reports_what_is_missing` |
| 终态仍可写（回看可以很久以后） | `test_completed_can_still_write_a_reflection` |
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.errors import MissingPrerequisite, StateNotWritable
from app.session_domain import reads, reflection, writes
from app.session_domain.enums import InputConfidence, PlanStepStatus


def _session_with_plan(db: Session, *, activate: bool = True):
    folder = writes.create_folder(db, name="英语学习")
    row = writes.create_session(
        db,
        folder_id=folder.folder_id,
        title="学点什么",
        input_goal="把第三章的虚拟语气学会",
        intent_raw="帮我复习第三章",
        intent_confidence=InputConfidence.STATED,
    )
    db.flush()
    writes.update_input(
        db,
        row.session_id,
        changes={"success_criteria": "能做对 10 道虚拟语气题"},
        expected_version=reads.get_input(db, row.session_id).version,
        changed_by=__import__(
            "app.session_domain.enums", fromlist=["ChangedBy"]
        ).ChangedBy.USER,
        reason="写下完成判据",
    )
    db.flush()
    if activate:
        writes.transition(
            db, row.session_id, target="active", expected_version=row.version
        )
        db.flush()
        writes.create_plan(
            db,
            row.session_id,
            steps=[
                writes.StepDraft(action="通读第三章", output="一份问题清单"),
                writes.StepDraft(action="做 10 道题", output="错题原因"),
            ],
            expected_session_version=reads.get_session(db, row.session_id).version,
            reason="创建计划",
        )
        db.flush()
    return row


def _finish_plan(db: Session, row) -> None:
    """把计划所有步骤标完成 —— 新规则下复盘只对“做完了的”计划开放。"""
    plan = reads.require_plan(db, row.session_id)
    for step in reads.plan_steps(db, plan.plan_id):
        current = reads.require_plan(db, row.session_id)
        writes.mark_step(
            db,
            row.session_id,
            step_id=step.step_id,
            status=PlanStepStatus.DONE,
            expected_version=current.version,
        )
    db.flush()


def _reviewed(db: Session):
    """一个**已经复盘过**的会话（`review` 态 + 一条总结）。"""
    row = _session_with_plan(db)
    _finish_plan(db, row)
    writes.transition(
        db, row.session_id, target="review", expected_version=row.version
    )
    db.flush()
    writes.write_summary(db, row.session_id, content="第三章过了一遍，题做了一半。")
    db.flush()
    return row


# --------------------------------------------------------------------------- #
# 两道门槛
# --------------------------------------------------------------------------- #


def test_active_has_notes_not_reflections(session: Session) -> None:
    """★ 执行中不开放 —— 那时想记就写**笔记**（它在所有状态都可写）。

    这条边界写在状态机里而不是留给界面：界面上的按钮拦不住 API。
    """
    row = _session_with_plan(session)  # active
    writes.add_note(session, row.session_id, content="今天状态一般")  # 可以

    with pytest.raises(StateNotWritable) as excinfo:
        writes.write_reflection(
            session, row.session_id, answers={"why": "题太难"}
        )
    assert excinfo.value.detail["operation"] == "write_reflection"


def test_a_session_without_a_summary_is_refused(session: Session) -> None:
    """★ 没复盘过不许写感想 —— 否则「感想」与「总结」变成同一件事的两个入口。

    这条不是状态门槛（`review` 态是允许写感想的），而是**事实**门槛：
    这张表管状态，那条管"有没有可对照的东西"。
    """
    row = _session_with_plan(session)
    _finish_plan(session, row)
    writes.transition(session, row.session_id, target="review", expected_version=row.version)
    session.flush()

    with pytest.raises(MissingPrerequisite) as excinfo:
        writes.write_reflection(session, row.session_id, answers={"why": "题太难"})
    assert excinfo.value.detail["missing"] == "session_summary"


def test_an_empty_reflection_is_refused(session: Session) -> None:
    """空的感想不写：四要素一项都没填、也没有正文。"""
    row = _reviewed(session)
    with pytest.raises(MissingPrerequisite):
        writes.write_reflection(session, row.session_id)
    with pytest.raises(MissingPrerequisite):
        writes.write_reflection(session, row.session_id, answers={"why": "   "})


def test_a_blank_element_is_dropped_not_stored(session: Session) -> None:
    """只填了空白的项**不存** —— 存了会让"完整度"看起来比真实的高。"""
    row = _reviewed(session)
    row_reflection = writes.write_reflection(
        session,
        row.session_id,
        answers={"why": "题型的坑没认出来", "next_change": "   "},
    )
    assert row_reflection.answers == {"why": "题型的坑没认出来"}


# --------------------------------------------------------------------------- #
# 形状：四要素分项
# --------------------------------------------------------------------------- #


def test_the_four_elements_are_stored_separately(session: Session) -> None:
    """★ 分项存的理由：`V-PAR` 的 `measured` 通道声明的正是
    「复盘产物的**四要素**完整度」。拼成一坨存，那个完整度就只能靠
    再解析一段自然语言来回答 —— 那是另一种猜。
    """
    row = _reviewed(session)
    answers = {
        "what_happened": "通读了第三章，题做了 5 道",
        "expected_gap": "以为能做对 10 道，实际错了 3 道",
        "why": "虚拟语气的时态倒装没认出来",
        "next_change": "下次先做 5 道只考倒装的题",
    }
    row_reflection = writes.write_reflection(session, row.session_id, answers=answers)

    assert row_reflection.reflection_id == "RF-0001"
    assert row_reflection.answers == answers
    # 正文是拼出来的：四个小标题 + 原话，一个字的加工都不加。
    assert "【发生了什么】通读了第三章，题做了 5 道" in row_reflection.content
    assert "【下次改变】下次先做 5 道只考倒装的题" in row_reflection.content
    # 时间锚：它是**在哪份总结之后**写的。
    assert row_reflection.summary_id == reads.latest_summary(session, row.session_id).summary_id
    # 只落会话，不进全局。
    assert reads.list_reflections(session, row.session_id) == [row_reflection]


def test_a_plain_body_is_accepted(session: Session) -> None:
    """只想写一段话也可以 —— 拼装是**呈现**，不是要求。"""
    row = _reviewed(session)
    row_reflection = writes.write_reflection(
        session, row.session_id, content="这次学得比上次踏实，但题做得太少。"
    )
    assert row_reflection.answers is None
    assert row_reflection.content == "这次学得比上次踏实，但题做得太少。"


def test_completed_can_still_write_a_reflection(session: Session) -> None:
    """回看可以在归档之后很久才发生（与笔记同源的理由）。"""
    row = _reviewed(session)
    version = reads.get_session(session, row.session_id).version
    writes.transition(session, row.session_id, target="completed", expected_version=version)
    session.flush()

    row_reflection = writes.write_reflection(
        session, row.session_id, answers={"why": "起步太晚"}
    )
    assert row_reflection.reflection_id == "RF-0001"


# --------------------------------------------------------------------------- #
# 指引：这次会话的，不是一张通用问卷
# --------------------------------------------------------------------------- #


def test_the_guide_quotes_this_session(session: Session) -> None:
    """★ 指引里每一条事实都来自**这次会话真实的数据**。

    固定四句话贴给任何会话都成立，因此也帮不上任何忙。
    """
    row = _session_with_plan(session)
    _finish_plan(session, row)
    writes.add_note(session, row.session_id, content="今天状态一般")
    writes.transition(session, row.session_id, target="review", expected_version=row.version)
    session.flush()
    writes.write_summary(session, row.session_id, content="过了一遍。")
    session.flush()

    guide = reflection.build_guide(session, row.session_id)
    by_key = {item.key: item for item in guide.items}

    assert [item.key for item in guide.items] == [
        "what_happened",
        "expected_gap",
        "why",
        "next_change",
    ]
    happened = " ".join(by_key["what_happened"].facts)
    assert "2 步" in happened and "完成" in happened, happened
    assert "通读第三章" in happened
    assert "今天状态一般" in " ".join(by_key["what_happened"].facts)
    # ② 对着**用户自己写的**完成判据问
    assert "能做对 10 道虚拟语气题" in " ".join(by_key["expected_gap"].facts)
    assert guide.blocked_reason == ""
    assert guide.missing == (
        "what_happened",
        "expected_gap",
        "why",
        "next_change",
    )


def test_the_guide_only_reports_what_is_missing(session: Session) -> None:
    """★ 指引**不打分**。`missing` 只回答"还差哪几块"。

    "这份感想写得好不好"不是这一步要回答的；它要回答的是
    "用户有没有被给到一个能写得出东西的位置"。
    """
    row = _reviewed(session)
    writes.write_reflection(
        session,
        row.session_id,
        answers={"why": "倒装没认出来", "next_change": "先做 5 道倒装题"},
    )
    session.flush()

    guide = reflection.build_guide(session, row.session_id)

    assert guide.missing == ("what_happened", "expected_gap")
    assert guide.existing == 1
    as_dict = guide.as_dict()
    # 指引要说明四要素的**出处**（否则下一个人会以为是我编的），
    # 并且明说它不打分 —— 这是这两句唯一的验收点。
    assert "V-PAR" in as_dict["elements_note"] and "完整度" in as_dict["elements_note"]
    assert "不是评分" in as_dict["elements_note"]
    assert not any(
        field in as_dict for field in ("score", "grade", "quality")
    ), "指引里不该出现分数"


def test_the_guide_explains_why_it_is_not_time_yet(session: Session) -> None:
    """不能写时给一条**能照做的**原因，而不是一个禁用的按钮。"""
    active = _session_with_plan(session)
    guide = reflection.build_guide(session, active.session_id)
    assert "复盘" in guide.blocked_reason and "/notes" in guide.blocked_reason


def test_the_guide_is_read_only(session: Session) -> None:
    row = _reviewed(session)
    before_new = set(session.new)
    before_dirty = set(session.dirty)
    reflection.build_guide(session, row.session_id)
    assert set(session.new) == before_new
    assert set(session.dirty) == before_dirty
