"""句子标注 + 推断理由（P15）—— 领域层。

三条拒绝与三条诚实性是这一步的全部内容，所以测试按它们组织：

| 拒绝 / 诚实性 | 测试 |
|---|---|
| 只能标注助手的回复 | `test_a_user_message_cannot_be_annotated` |
| 引用必须逐字存在 | `test_a_quote_that_is_not_in_the_message_is_refused` |
| 消息必须属于这个会话 | `test_a_message_from_another_session_is_refused` |
| 没调过工具也要能标注 | `test_a_reply_without_tools_is_still_annotatable` |
| 条目 id 来自**轨迹文本**，不是查库 | `test_entries_come_from_the_trace_not_from_a_query` |
| 锚点没记就**不猜** | `test_missing_anchor_is_reported_not_guessed` |
| 依据不调模型、不写库 | `test_explain_never_touches_the_model_or_the_db` |
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.errors import InvalidField, NotFound
from app.session_domain import basis, reads, writes
from app.session_domain.enums import ChatRole, InputConfidence
from app.session_domain.models import ChatMessage


def _live_session(db: Session, *, goal: str = "把第三章的虚拟语气学会"):
    """一个已激活的会话。标注挂在会话上，所以先要有会话。"""
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


def _session_with_plan(db: Session):
    """带一个两步计划的会话 —— 用来验**正面**的上下文重放。"""
    row = _live_session(db)
    writes.create_plan(
        db,
        row.session_id,
        steps=[
            writes.StepDraft(action="通读第三章", output="一份问题清单"),
            writes.StepDraft(action="精读虚拟语气", output="10 个例句"),
        ],
        expected_session_version=row.version,
        reason="创建计划",
    )
    db.flush()
    return row


def _assistant(
    db: Session,
    session_id: str,
    *,
    content: str = "你更适合先把概念过一遍再做题。",
    tool_trace: list[dict] | None = None,
    anchor_step_id: str | None = None,
) -> ChatMessage:
    return writes.add_chat_message(
        db,
        session_id,
        content=content,
        role=ChatRole.ASSISTANT,
        tool_trace=tool_trace,
        anchor_step_id=anchor_step_id,
    )


# --------------------------------------------------------------------------- #
# 三条拒绝
# --------------------------------------------------------------------------- #


def test_a_quote_from_the_reply_is_recorded_verbatim(session: Session) -> None:
    """最朴素的一条：引用进库、逐字不改。"""
    row = _live_session(session)
    message = _assistant(session, row.session_id)

    annotation = writes.annotate_message(
        session,
        row.session_id,
        msg_id=message.msg_id,
        quote="先把概念过一遍",
        question="为什么？",
    )

    assert annotation.annotation_id == "AN-0001"
    assert annotation.quote == "先把概念过一遍"
    assert annotation.question == "为什么？"
    assert reads.list_annotations(session, row.session_id) == [annotation]


def test_a_user_message_cannot_be_annotated(session: Session) -> None:
    """标注问的是「**你**为什么这么说」。

    允许标用户自己的话会让 `annotation` 与 `note` 变成同一件东西 ——
    而两者的区别正是"这是关于助手的输出，还是关于我的记录"。
    """
    row = _live_session(session)
    mine = writes.add_chat_message(
        session, row.session_id, content="我想先做题。", role=ChatRole.USER
    )

    with pytest.raises(InvalidField) as excinfo:
        writes.annotate_message(
            session, row.session_id, msg_id=mine.msg_id, quote="我想先做题"
        )
    assert excinfo.value.detail["role"] == "user"


def test_a_quote_that_is_not_in_the_message_is_refused(session: Session) -> None:
    """★ 这条是整张表的**全部意义**。

    对不上的引用会让界面出现一条指向不存在内容的标注 ——
    而"凭什么这么说"的回答就挂在一句谁都没说过的话上。
    """
    row = _live_session(session)
    message = _assistant(session, row.session_id)

    with pytest.raises(InvalidField) as excinfo:
        writes.annotate_message(
            session,
            row.session_id,
            msg_id=message.msg_id,
            quote="你更适合直接做难题",  # 那句话里没有这句
        )
    assert "逐字" in excinfo.value.message


def test_a_message_from_another_session_is_refused(session: Session) -> None:
    """跨会话引用会让标注变成一条不受会话约束的通道。"""
    one = _live_session(session)
    other = _live_session(session, goal="另一件事")
    message = _assistant(session, one.session_id)

    with pytest.raises(NotFound):
        writes.annotate_message(
            session, other.session_id, msg_id=message.msg_id, quote="先把概念过一遍"
        )


def test_the_quote_is_trimmed_but_not_rewritten(session: Session) -> None:
    """`strip()` 去掉前端带来的空白，**不改一个字**。

    归一化（大小写、全角半角）会让"逐字"这条约束名存实亡：
    通过校验的那段文字与消息里真实存在的那段就未必是同一段了。
    """
    row = _live_session(session)
    message = _assistant(session, row.session_id)

    annotation = writes.annotate_message(
        session, row.session_id, msg_id=message.msg_id, quote="  先把概念过一遍  "
    )
    assert annotation.quote == "先把概念过一遍"


def test_a_blank_quote_is_refused(session: Session) -> None:
    row = _live_session(session)
    message = _assistant(session, row.session_id)
    with pytest.raises(InvalidField):
        writes.annotate_message(session, row.session_id, msg_id=message.msg_id, quote="   ")


def test_a_question_is_optional(session: Session) -> None:
    """「就问问为什么」是一个完整的意图 —— 不给问题不该被当成没填完。"""
    row = _live_session(session)
    message = _assistant(session, row.session_id)
    annotation = writes.annotate_message(
        session, row.session_id, msg_id=message.msg_id, quote="先把概念过一遍"
    )
    assert annotation.question is None


# --------------------------------------------------------------------------- #
# 依据：全是可核对的事实
# --------------------------------------------------------------------------- #


def test_a_reply_without_tools_is_still_annotatable(session: Session) -> None:
    """没调过工具的回复**同样可以被标注**，而且依据要如实说出来。

    ★ 空列表会被读成"没有依据"，所以这种情况必须有一句话解释 ——
    它的依据是当时装配的上下文与它自己的话，不是"什么都没有"。
    """
    row = _live_session(session)
    message = _assistant(session, row.session_id)

    result = basis.explain(session, message, quote="先把概念过一遍")

    assert result.traces == ()
    assert "没有调用任何工具" in result.summary()
    assert "不能" in result.as_dict()["limits"]


def test_failed_steps_are_counted_and_kept(session: Session) -> None:
    """轨迹里被拒绝的一步**要留着并且要数出来**。

    只报成功会让依据变成一份成绩单 —— 而"它试过、被拒了"正是
    「为什么它没做那件事」的答案。
    """
    row = _live_session(session)
    message = _assistant(
        session,
        row.session_id,
        tool_trace=[
            {
                "step": 1,
                "call_id": "c1",
                "tool": "retrieve_session",
                "scope": "read",
                "status": "success",
                "message": "找到 2 条：N-0001、N-0002",
                "arguments": {"query": "概念"},
            },
            {
                "step": 2,
                "call_id": "c2",
                "tool": "create_task_plan",
                "scope": "session_write",
                "status": "rejected",
                "message": "澄清问题还没答（CL-0001）。",
                "arguments": {},
            },
        ],
    )

    result = basis.explain(session, message)
    payload = result.as_dict()

    assert [fact.status for fact in result.traces] == ["success", "rejected"]
    assert "1 步没有成功" in payload["summary"]
    assert payload["entries"] == ["N-0001", "N-0002", "CL-0001"]


def test_entries_come_from_the_trace_not_from_a_query(session: Session) -> None:
    """★ 条目 id 是从**轨迹文本**里抽的，不是"查一遍库里有哪些条目"。

    两个方向都要钉：

    - 轨迹里提到一个**库里不存在**的 `C-9999` —— 它仍然要出现。
      这证明抽取是文本的：一个"查库再过滤"的实现会把它丢掉。
    - 库里有一条真实的笔记 `N-0001`，但轨迹**没提**它 —— 它不该出现。
      这证明这份依据的范围是"当时"，不是"现在库里有什么"。
    """
    row = _live_session(session)
    note = writes.add_note(session, row.session_id, content="今天状态一般")
    message = _assistant(
        session,
        row.session_id,
        tool_trace=[
            {
                "step": 1,
                "tool": "retrieve_cognitive",
                "scope": "read",
                "status": "success",
                # ★ 这句话里**故意不写**那条笔记的 id —— 写了就变成
                # "轨迹里提到了它"，而这条测试要证明的正是**没提到就不出现**。
                "message": "读到 C-9999（这一条其实不存在）。库里那条笔记与这次无关。",
                "arguments": {},
            }
        ],
    )

    result = basis.explain(session, message)

    assert "C-9999" in result.entries
    assert note.note_id not in result.entries, "轨迹没提到的条目不该被带进来"


def test_entries_are_deduplicated_and_ordered(session: Session) -> None:
    row = _live_session(session)
    message = _assistant(
        session,
        row.session_id,
        tool_trace=[
            {
                "step": 1,
                "tool": "retrieve_cognitive",
                "scope": "read",
                "status": "success",
                "message": "C-0002 与 C-0001 都提到了 C-0002。",
                "arguments": {},
            }
        ],
    )
    assert basis.explain(session, message).entries == ("C-0002", "C-0001")


def test_too_many_entries_are_truncated_and_that_is_said(session: Session) -> None:
    """截断必须**说出来** —— 否则用户会以为那是全部。"""
    row = _live_session(session)
    many = "、".join(f"C-{i:04d}" for i in range(1, basis.MAX_ENTRIES + 6))
    message = _assistant(
        session,
        row.session_id,
        tool_trace=[
            {
                "step": 1,
                "tool": "retrieve_cognitive",
                "scope": "read",
                "status": "success",
                "message": many,
                "arguments": {},
            }
        ],
    )

    payload = basis.explain(session, message).as_dict()

    assert len(payload["entries"]) == basis.MAX_ENTRIES
    assert payload["entries_truncated"] is True


def test_missing_anchor_is_reported_not_guessed(session: Session) -> None:
    """★ 锚点没记就**不猜**（`anchor_step_id is None`）。

    按今天的规则挑一个步骤会让那节标题看起来像"当时的证据" ——
    而它只是"今天会看到的东西"。这条测试钉住的是"说出来"而不是"填一个"。
    """
    row = _live_session(session)
    message = _assistant(session, row.session_id, anchor_step_id=None)

    replay = basis.explain(session, message).as_dict()

    assert replay["context_step_id"] is None
    assert replay["context_sections"] == []
    assert "没有记录锚点" in replay["context_note"]


def test_a_recorded_anchor_replays_the_sections_it_saw(session: Session) -> None:
    """★ 记了锚点，重放就能给出当时的节标题 —— 而且**标明这是重放**。

    这是 P15 加 `anchor_step_id` 那一列的全部理由：不记锚点，事后只能按
    今天的规则挑一步，而那节标题会被读成"当时的证据"。
    """
    row = _session_with_plan(session)
    step = reads.plan_steps(session, reads.require_plan(session, row.session_id).plan_id)[0]
    message = _assistant(session, row.session_id, anchor_step_id=step.step_id)

    replay = basis.explain(session, message).as_dict()

    assert replay["context_step_id"] == step.step_id
    assert replay["context_sections"], "有锚点就该重放出节标题"
    assert replay["context_replayed_now"] is True
    assert "重放" in replay["context_note"]


def test_the_anchor_is_stored_on_the_assistant_message_only(session: Session) -> None:
    """用户消息**不能**带锚点：他发那句话时没有经过 `resolve_anchor`。

    给它填一个会造出一条查不到出处的记录 —— 而依据机制的全部价值
    就是"每一条都能回到它真实的来源"。
    """
    row = _session_with_plan(session)
    with pytest.raises(InvalidField) as excinfo:
        writes.add_chat_message(
            session,
            row.session_id,
            content="我想先做题。",
            role=ChatRole.USER,
            anchor_step_id="ST-001",
        )
    assert excinfo.value.detail["field"] == "anchor_step_id"


def test_a_broken_anchor_does_not_raise(session: Session) -> None:
    """锚点指向一个不存在的步骤（计划改版了）时，依据仍然要给得出来。"""
    row = _live_session(session)
    message = _assistant(session, row.session_id, anchor_step_id="ST-999")

    replay = basis.explain(session, message).as_dict()

    assert replay["context_step_id"] == "ST-999"
    assert "重放不成" in replay["context_note"]


def test_explain_never_touches_the_model_or_the_db(session: Session) -> None:
    """依据是**读**出来的，不是问出来的、也不是写出来的。

    ★ 这条是这一步的立身之本：一旦 `explain` 需要问模型，
    它给出的"理由"就是**新编的**，而用户分不出来。
    """
    row = _live_session(session)
    message = _assistant(session, row.session_id)

    before_new = set(session.new)
    before_dirty = set(session.dirty)
    basis.explain(session, message)
    basis.explain(session, message, quote="先把概念过一遍", question="为什么？")

    assert set(session.new) == before_new, "依据不该产生任何待写对象"
    assert set(session.dirty) == before_dirty, "依据不该修改任何对象"


def test_deleting_an_annotation_leaves_everything_else_alone(session: Session) -> None:
    """删除只删它自己 —— 标注是问题，不是结论。"""
    row = _live_session(session)
    message = _assistant(session, row.session_id)
    annotation = writes.annotate_message(
        session, row.session_id, msg_id=message.msg_id, quote="先把概念过一遍"
    )

    writes.delete_annotation(session, row.session_id, annotation.annotation_id)

    assert reads.list_annotations(session, row.session_id) == []
    assert reads.get_chat_message(session, message.msg_id) is not None


def test_deleting_a_missing_annotation_is_a_404_not_a_crash(session: Session) -> None:
    row = _live_session(session)
    with pytest.raises(NotFound):
        writes.delete_annotation(session, row.session_id, "AN-9999")
