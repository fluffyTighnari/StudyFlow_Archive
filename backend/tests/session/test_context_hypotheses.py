"""假说在**上下文里**的位置（P7）—— 看得见，但不装配。

## 这一组测试要守住的边界只有一句

> 模型能看见它，但**不能把它当成已验证的做法去安排计划**。

把它做进上下文是有代价的：它和「个性化证据」在结构上同形（都是
「当 X 时做 Y」）。同形的东西最容易被当成同一种东西 —— 所以这条边界不能只
存在于文档里，必须是可断言的事实：

1. `EvidenceItem` 与 `HypothesisItem` 是**不同的类型**，后者带 `verified=False`；
2. 假说**不在** `ctx.evidence` 里（装配路径读不到它）；
3. 渲染出来的文本里，「未验证」这三个字出现在这一节（模型读得到）；
4. 侧边栏与会话两侧显示同一件东西时用的是**同一个类型** ——
   否则模型会以为是两种东西。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agent.sidebar import render_sidebar_context
from app.global_domain import writes
from app.session_domain.context import (
    MAX_HYPOTHESES,
    HypothesisItem,
    assemble_discussion_context,
)
from app.session_domain.sidebar_context import assemble_sidebar_context

from tests.session.test_context import _session_with_plan


def _add_hypothesis(
    db: Session,
    *,
    condition: str,
    effect: str = "用间隔重复",
    statement: str | None = None,
) -> None:
    """直接走 `writes`（需要令牌）—— 用 `token` fixture 拿不到时走 Router 更麻烦。

    这里刻意**不**经过 API：要测的是"装配器怎么读这张表"，
    而不是"API 能不能写"（那是 `test_hypotheses_api.py` 的事）。
    """
    writes.insert_hypothesis(
        _token(),
        db,
        statement=statement or f"{condition}时{effect}",
        condition=condition,
        effect=effect,
    )
    db.flush()


def _token():
    from app.router.capability import issue_token

    return issue_token()


def test_a_hypothesis_shows_up_in_the_session_context(session: Session) -> None:
    row, plan = _session_with_plan(session)
    _add_hypothesis(session, condition=row.title or "背单词")

    step = _first_step(session, plan)
    pack = assemble_discussion_context(session, row.session_id, step)

    assert len(pack.hypotheses) == 1
    item = pack.hypotheses[0]
    assert isinstance(item, HypothesisItem)
    # ★ 恒假：这条东西不是证据，而"不是证据"这件事写在数据里，不只写在标题里
    assert item.verified is False


def test_a_hypothesis_is_never_in_the_evidence_list(session: Session) -> None:
    """★ 装配路径读不到假说 —— 这是"不进装配"的可断言形式。

    `ctx.evidence` 是规划时真正会用的那份东西。假说出现在这里，
    就等于它被当成了已验证的做法。
    """
    row, plan = _session_with_plan(session)
    _add_hypothesis(session, condition=row.title or "背单词")

    step = _first_step(session, plan)
    pack = assemble_discussion_context(session, row.session_id, step)

    assert pack.evidence == []
    assert len(pack.hypotheses) == 1


def test_the_section_is_reported_with_its_own_name(session: Session) -> None:
    """它在 `sections` 里有自己的一节 —— 于是界面能解释"上下文里有什么"。"""
    row, plan = _session_with_plan(session)
    _add_hypothesis(session, condition=row.title or "背单词")
    pack = assemble_discussion_context(
        session, row.session_id, _first_step(session, plan)
    )
    names = [s.name for s in pack.sections]
    assert "假说（想试但还没验证的做法）" in names
    # 与「C 个性化证据」是**两节**，不是一节
    assert "C 个性化证据" in names
    assert names.index("C 个性化证据") != names.index("假说（想试但还没验证的做法）")


def test_the_adoption_order_mentions_it(session: Session) -> None:
    """采用序里要写出来 —— 否则"为什么这条这样排"只存在于代码里。"""
    row, plan = _session_with_plan(session)
    pack = assemble_discussion_context(
        session, row.session_id, _first_step(session, plan)
    )
    assert any("假说" in line for line in pack.precedence_order)


def test_unrelated_hypotheses_are_capped_and_say_so(session: Session) -> None:
    """无关的假说会被挡住，且**说出被挡住了**。

    假说会无限积累（每句"我想试试 X"都是一条），所以必须有上限；
    而上限必须被报告 —— 一份静默截断的清单会让模型以为"他就想过这些"。
    """
    row, plan = _session_with_plan(session)
    for index in range(MAX_HYPOTHESES + 2):
        _add_hypothesis(session, condition=row.title or "背单词", effect=f"做法 {index}")

    pack = assemble_discussion_context(
        session, row.session_id, _first_step(session, plan)
    )
    assert len(pack.hypotheses) == MAX_HYPOTHESES
    section = next(s for s in pack.sections if s.name.startswith("假说"))
    assert section.dropped_count >= 2


def test_the_sidebar_sees_the_same_thing_with_the_same_type(session: Session) -> None:
    """两侧显示同一件东西时用**同一个类型** —— 否则模型会以为是两种东西。"""
    _add_hypothesis(session, condition="背单词")
    ctx = assemble_sidebar_context(session)
    assert len(ctx.hypotheses) == 1
    assert isinstance(ctx.hypotheses[0], HypothesisItem)
    assert ctx.hypotheses[0].verified is False


def test_the_sidebar_render_says_unverified(session: Session) -> None:
    """★ 渲染出来的文字里必须有「未验证」。

    这一节唯一的失败形态是"被当成证据用"，所以那句话不能只存在于
    组装代码里 —— 它必须出现在模型真正读到的那段文本里。
    """
    _add_hypothesis(session, condition="背单词")
    rendered = render_sidebar_context(assemble_sidebar_context(session))
    assert "假说" in rendered
    assert "未验证" in rendered
    # 而它不能和证据混在同一节标题下
    assert "## 个性化证据" in rendered


def test_an_empty_database_renders_an_empty_section(session: Session) -> None:
    """没有假说时这一节仍然在 —— "没有"和"这一节不存在"是两件事。"""
    rendered = render_sidebar_context(assemble_sidebar_context(session))
    assert "假说" in rendered
    assert "（没有）" in rendered


def _first_step(session: Session, plan) -> str:
    from app.session_domain import reads

    return reads.plan_steps(session, plan.plan_id)[0].step_id
