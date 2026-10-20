"""侧边栏常驻模型的上下文包（P6+.6）。

## 这组测试守的两件事

1. **侧边栏的上下文里没有会话** —— 没有计划、没有步骤、没有检查点。
   这不是"少给一点"，而是**不能给**：给一个空计划，模型会看到
   「当前步骤：」后面空着，然后合理地开始评论"你还没有计划"，
   而真相是**侧边栏根本没有计划这个概念**。两件事对用户含义完全不同。

2. **不乱截断全局数据**。侧边栏没有"当前这一步"可以用来判断相关性，
   所以会话侧那些"挑最相关的几条"的规则在这里**都不成立** ——
   硬约束永不截断，基线不排除任何变量，策略不砍到 4 条。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.session_domain.sidebar_context import (
    MAX_RECENT_SESSIONS,
    assemble_sidebar_context,
)


def test_it_has_no_session_plan_or_step(sandbox: object) -> None:
    """★ 侧边栏的上下文结构里**根本没有**会话字段。"""
    ctx = assemble_sidebar_context(sandbox.db)  # type: ignore[attr-defined]
    fields = set(type(ctx).model_fields)
    for absent in (
        "session_id",
        "plan_version",
        "step",
        "checkpoints",
        "stage",
        "step_history",
        "situation",
        "attachments",
    ):
        assert absent not in fields, f"侧边栏上下文里不该有 `{absent}`"


def test_hard_constraints_are_never_dropped(app, client) -> None:
    """★ M2 红线 2 在侧边栏同样成立。

    任何"挑几条最重要的"逻辑都等于系统替用户决定他哪句话不重要。
    侧边栏尤其危险：它没有当前任务，所以"和这次相关"这个判据
    在那里**完全不存在** —— 一个按相关性过滤的实现会在这里退化成
    "只给前 N 条"，而那是任意的。

    写入走**真实的用户路径**（`POST /api/preferences`，R1 直写），
    而不是直接捅库：这样这条测试同时也在验证"API 写进去的东西
    侧边栏读得到"。
    """
    for i in range(30):
        response = client.post(
            "/api/preferences", json={"statement": f"硬约束 {i}：我不用这种方式学"}
        )
        assert response.status_code == 201, response.text

    with app.state.session_factory() as db:
        ctx = assemble_sidebar_context(db)
    assert len(ctx.constraints) == 30, "硬约束被截断了"
    section = next(s for s in ctx.sections if s.name == "用户偏好（硬约束）")
    assert section.dropped_count == 0


def test_the_baseline_keeps_every_variable(sandbox: object) -> None:
    """★ 与会话侧故意不同：**不排除**任何变量。

    会话侧排除"不参与计划装配"的变量，因为那一步的讨论用不上它们。
    而侧边栏的话题可以碰到任何一个 —— `trait` 类变量恰恰是
    「我是一个什么样的人」这个问题的答案，而那正是侧边栏最该聊的事。
    """
    from app.global_domain import reads as global_reads
    from app.session_domain.context import PLANNING_VARIABLES

    db: Session = sandbox.db  # type: ignore[attr-defined]
    ctx = assemble_sidebar_context(db)
    total_variables = len(global_reads.current_baseline_map(db))
    # 基线为空时无从比较，但"排除了某些变量"这件事必须是 **0**
    section = next(s for s in ctx.sections if s.name == "M1 基线（按通道）")
    assert section.dropped_count == 0
    # 与会话侧那个 `PLANNING_VARIABLES` 白名单不同：这里没有白名单。
    if total_variables:
        got = {item.variable_id for item in ctx.baseline}
        assert got == set(global_reads.current_baseline_map(db))
    assert PLANNING_VARIABLES  # 会话侧的名单确实存在 —— 两者是刻意不同的


def test_it_lists_recent_sessions_without_their_contents(sandbox: object) -> None:
    """「最近的会话」是**索引**，不是对话原文。

    一个常驻助手如果不知道"他最近在学什么"，就只能等用户每次从头解释。
    但把原文也塞进来会让上下文被某一次会话占满 ——
    具体内容要模型自己 `retrieve_session`（并显式给 session_id）。
    """
    db: Session = sandbox.db  # type: ignore[attr-defined]
    ctx = assemble_sidebar_context(db)
    assert ctx.recent_sessions, "一个会话都没有 —— 索引是空的"
    ref = ctx.recent_sessions[0]
    assert ref.session_id and ref.title and ref.state
    # SessionRef 里没有任何正文字段
    assert set(type(ref).model_fields) == {
        "session_id",
        "folder_id",
        "title",
        "state",
        "summary_head",
    }


def test_the_index_is_capped_but_says_what_it_dropped(
    app, client, sandbox: object
) -> None:
    """上限可以有，**静默地上限不可以** —— 每一节都要报它排除了什么。"""
    from app.session_domain import writes as session_writes

    db: Session = sandbox.db  # type: ignore[attr-defined]
    for i in range(MAX_RECENT_SESSIONS + 3):
        session_writes.create_session(
            db,
            folder_id=sandbox.folder.folder_id,  # type: ignore[attr-defined]
            title=f"第 {i} 个会话",
            input_goal="学着玩",
        )
    ctx = assemble_sidebar_context(db)
    assert len(ctx.recent_sessions) == MAX_RECENT_SESSIONS
    section = next(s for s in ctx.sections if s.name == "最近的会话")
    assert section.dropped_count >= 3
    assert section.dropped_examples, "排除了东西却没给例子"
    # 而它必须告诉模型"还有别的，可以显式去读"
    assert any("retrieve_session" in text for text in section.dropped_examples)


def test_never_asked_is_not_the_same_as_an_empty_checklist(sandbox: object) -> None:
    """★ 「还没勾」与「勾了 0 个」是两件不同的事。

    前者是一条**可执行的事实**（可以邀请他去勾），后者不是。
    合并成 `None` 会让侧边栏失去那个邀请的时机。
    """
    db: Session = sandbox.db  # type: ignore[attr-defined]
    ctx = assemble_sidebar_context(db)
    assert ctx.domain_checklist is None, "新库里不该有领域勾选档案"


def test_a_filled_checklist_shows_up(app, client) -> None:
    """勾过之后侧边栏读得到 —— 走真实的 HTTP 写入路径。

    刻意用**目录里真实存在的** topic_id：写入层会校验它们
    （拼错的 id 会被安静地存下来，然后那条勾选永远匹配不上任何题目），
    所以编一个像模像样的假 id 会得到 422 而不是这条测试想验的东西。
    """
    response = client.post(
        "/api/benchmark/domains",
        json={"familiar": ["LANG-PHON"], "interested": ["LIT-POETRY"]},
    )
    assert response.status_code in (200, 201), response.text

    with app.state.session_factory() as db:
        ctx = assemble_sidebar_context(db)
    assert ctx.domain_checklist is not None
    assert ctx.domain_checklist.familiar == ["LANG-PHON"]
    assert ctx.domain_checklist.interested == ["LIT-POETRY"]
    assert ctx.domain_checklist.version_no == 1


def test_every_section_is_reported(sandbox: object) -> None:
    """每一节都必须进 `sections` —— 否则界面没法解释上下文里有什么。"""
    db: Session = sandbox.db  # type: ignore[attr-defined]
    ctx = assemble_sidebar_context(db)
    names = [s.name for s in ctx.sections]
    assert names == [
        "用户偏好（硬约束）",
        "用户偏好（软偏好）",
        "领域勾选",
        "M1 基线（按通道）",
        "证据",
        # ★ P7 新增的一节：他**说过想试、但还没试过**的做法。
        # 它与「证据」同 precedence（都是 4）但排在其后 ——
        # 因为同一档里先看已证实的，再看没证实的。
        "假说（想试但还没验证的做法）",
        "策略库",
        "最近的会话",
    ]
    # 采用序（小 = 先）必须单调不减
    precedences = [s.precedence for s in ctx.sections]
    assert precedences == sorted(precedences), "节没有按采用序排列"
    assert len(ctx.precedence_order) == len(ctx.sections)


def test_it_reads_only_and_never_writes(sandbox: object) -> None:
    """★ 组装上下文是**纯读**。它在每次对话前都会跑，而一次意外的写入
    会让"我只是问了一句话"变成一次全局变更。"""
    from app.db import Base
    from sqlalchemy import func, select

    db: Session = sandbox.db  # type: ignore[attr-defined]
    before = {
        table.name: db.scalar(select(func.count()).select_from(table))
        for table in Base.metadata.sorted_tables
    }
    assemble_sidebar_context(db)
    assemble_sidebar_context(db)
    after = {
        table.name: db.scalar(select(func.count()).select_from(table))
        for table in Base.metadata.sorted_tables
    }
    changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert not changed, f"组装侧边栏上下文写库了：{changed}"


@pytest.mark.parametrize("count", [0, 1, 5])
def test_it_works_with_an_empty_or_tiny_database(
    session: Session, count: int
) -> None:
    """空库也必须能组装出来。

    侧边栏是**常驻**的：用户装好系统、还没建任何会话时就会点开它。
    此时组装抛异常，用户看到的是一个坏掉的界面，而不是"还没东西可聊"。
    """
    from app.session_domain import writes as session_writes

    if count:
        folder = session_writes.create_folder(session, name="F", goal="G")
        for i in range(count):
            session_writes.create_session(
                session, folder_id=folder.folder_id, title=f"S{i}", input_goal="G"
            )
    ctx = assemble_sidebar_context(session)
    assert ctx.constraints == []
    assert ctx.recent_sessions is not None
    assert len(ctx.sections) == 8  # P7 加上「假说」一节（原 7 节）
