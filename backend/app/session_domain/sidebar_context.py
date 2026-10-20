"""侧边栏常驻模型的上下文包（P6+.6）。

## 它与 `assemble_discussion_context` 的关系

**共用同一份采用序纪律，但不共用那个函数。** 两个原因，第二个是决定性的：

1. `DiscussionContext` 的字段里有一半是**会话专有**的
   （`step` / `plan_version` / `checkpoints` / `stage` / `step_history` / `situation`）。
   侧边栏一个都没有 —— 它不是"在讨论某一步"。
2. 用空值去填那些字段会**制造一个假的上下文**：模型会看到
   「当前步骤：」后面空着，然后合理地开始评论"你还没有计划"。
   而真相是**侧边栏根本没有计划这个概念** —— 那两件事对用户含义完全不同。

所以这里只组装**全局那一半**：偏好、基线、证据、策略、领域勾选、最近的会话。
「最近的会话」是侧边栏特有的：一个常驻助手如果不道"他最近在学什么"，
它就只能等用户每次从头解释。

## 采用序

与会话侧**同一套数字**（`precedence` 小 = 先），因为用户要在两个界面之间
建立同一个预期。侧边栏没有"当前任务"，所以第一档永远是**长期约束**：

| precedence | 节 |
|---|---|
| 1 | 用户偏好（硬约束）/ 用户偏好（软偏好） |
| 2 | 领域勾选（他熟悉什么、对什么感兴趣） |
| 3 | M1 基线（按通道） |
| 4 | 证据 |
| 5 | 策略库 |
| 6 | 最近的会话（他到过哪里） |

★ **硬约束永不截断**（与会话侧同一条 M2 红线 2）：任何"挑几条最重要的"
逻辑都等于系统替用户决定他哪句话不重要。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.global_domain import reads as global_reads
from app.session_domain import reads as session_reads
from app.session_domain.context import (
    BaselineItem,
    EvidenceItem,
    HypothesisItem,
    PreferenceItem,
    SectionReport,
    StrategyItem,
)

#: 侧边栏最多看几个最近会话。它只是"他到过哪里"的索引，
#: 具体内容要模型自己调 `retrieve_session`（并显式给出 session_id）。
MAX_RECENT_SESSIONS = 8

#: 侧边栏最多列几条假说（P7）。
#:
#: 比会话内的 `MAX_HYPOTHESES = 3` 宽，原因不是"侧边栏更重要"，而是
#: **它没有筛选手段**：会话内能按当前步骤的策略/任务关键词挑相关的，
#: 侧边栏没有步骤，只能按时间取最近的 N 条。用同一份上限会让侧边栏
#: 常常只显示 3 条很旧的，而真正"他刚说想试"的那条被挤掉。
MAX_SIDEBAR_HYPOTHESES = 6


class SessionRef(BaseModel):
    """一条"他到过哪里"的索引。**刻意不含对话原文** —— 那是按需检索的事。"""

    session_id: str
    folder_id: str
    title: str
    state: str
    #: 上一条完成总结的首段。它比标题更能说明"他学到了什么"。
    summary_head: str | None = None


class DomainChecklistRef(BaseModel):
    """领域勾选档案（P6+.5）。侧边栏据此知道"他自认为熟悉/感兴趣什么"。"""

    version_no: int
    familiar: list[str]
    interested: list[str]


class SidebarContext(BaseModel):
    """侧边栏一次对话所需的上下文。**没有计划、没有步骤、没有会话。**"""

    constraints: list[PreferenceItem]
    soft_preferences: list[PreferenceItem]
    domain_checklist: DomainChecklistRef | None
    baseline: list[BaselineItem]
    evidence: list[EvidenceItem]
    #: ★ 他想试但还没试的做法（P7）。与会话那边同一个 `HypothesisItem` ——
    #: 两处显示同一件东西，必须长得一样，否则模型会以为是两种东西。
    hypotheses: list[HypothesisItem]
    strategies: list[StrategyItem]
    recent_sessions: list[SessionRef]
    sections: list[SectionReport]
    precedence_order: list[str]


def _strategy_items(db: DbSession, limit: int | None) -> list[StrategyItem]:
    """策略库。

    `limit=None` 时 `list_strategies` 返回全部 —— 侧边栏用它，
    因为**它没有"当前这一步"可以用来挑 3–4 条**。会话侧那个 `limit=4` 的
    依据是"这一步只可能用到少数几条"，而侧边栏的话题可以横跨整个策略库，
    于是"挑 4 条"会变成系统替用户决定哪个方法不重要。
    """
    rows = global_reads.list_strategies(db, limit=limit)
    return [
        StrategyItem(
            strategy_id=row.strategy_id,
            name=row.name,
            solves=row.solves,
            when_to_use=row.when_to_use,
        )
        for row in rows
    ]


def assemble_sidebar_context(db: DbSession) -> SidebarContext:
    """组装侧边栏的上下文。**纯读取、无副作用、不调 LLM。**

    与会话侧一样，每一节都报 `item_count` 与 `dropped_count` ——
    **沉默地少给一条，比给错一条更难发现。**
    """
    sections: list[SectionReport] = []

    # ---- 1. 偏好：硬约束全进、软偏好全进 ---- #
    hard = global_reads.list_active_constraints(db)
    soft = global_reads.list_active_soft_preferences(db)
    constraints = [
        PreferenceItem(
            pref_id=row.pref_id,
            statement=row.statement,
            priority=row.priority,
            hard=True,
        )
        for row in hard
    ]
    soft_items = [
        PreferenceItem(
            pref_id=row.pref_id,
            statement=row.statement,
            priority=row.priority,
            hard=False,
        )
        for row in soft
    ]
    sections.append(
        SectionReport(
            name="用户偏好（硬约束）",
            precedence=1,
            item_count=len(constraints),
            dropped_count=0,
        )
    )
    sections.append(
        SectionReport(
            name="用户偏好（软偏好）",
            precedence=1,
            item_count=len(soft_items),
            dropped_count=0,
        )
    )

    # ---- 2. 领域勾选（他熟悉什么、对什么感兴趣）---- #
    profile = global_reads.current_domain_profile(db)
    checklist: DomainChecklistRef | None = None
    if profile is not None:
        checklist = DomainChecklistRef(
            version_no=profile.version_no,
            familiar=list(profile.familiar or []),
            interested=list(profile.interested or []),
        )
    sections.append(
        SectionReport(
            name="领域勾选",
            precedence=2,
            item_count=0 if checklist is None else 2,
            # **没勾过**与"勾了空的"是两件不同的事，所以这里分开报。
            # 「还没勾」是一条可执行的事实（可以邀请他去勾），
            # 而「勾了 0 个」不是。
            dropped_count=0,
        )
    )

    # ---- 3. 基线 ---- #
    baseline_map = global_reads.current_baseline_map(db)
    baseline: list[BaselineItem] = []
    for variable_id in sorted(baseline_map):
        for channel, row in sorted(
            baseline_map[variable_id].items(), key=lambda kv: kv[0].value
        ):
            baseline.append(
                BaselineItem(
                    variable_id=variable_id,
                    channel=channel.value,
                    score=row.score,
                    confidence=None if row.confidence is None else row.confidence.value,
                    completeness=row.completeness.value,
                    version_id=row.version_id,
                )
            )
    sections.append(
        SectionReport(
            name="M1 基线（按通道）",
            precedence=3,
            item_count=len(baseline),
            # ★ 与会话侧不同：这里**不排除**任何变量。
            # 会话侧排除"不参与计划装配"的变量，因为那一步的讨论用不上它们；
            # 而侧边栏的话题可以碰到任何一个变量 —— 包括 `trait` 类，
            # 它们恰恰是"我是一个什么样的人"这个问题的答案。
            dropped_count=0,
        )
    )

    # ---- 4. 证据 ---- #
    evidence = [
        EvidenceItem(
            evidence_id=row.evidence_id,
            statement=row.statement,
            condition=row.condition,
            effect=row.effect,
            strength=row.evidence_strength.value,
        )
        for row in global_reads.list_active_evidence(db)
    ]
    sections.append(
        SectionReport(
            name="证据",
            precedence=4,
            item_count=len(evidence),
            dropped_count=0,
        )
    )

    # ---- 4b. 假说（P7）---- #
    #
    # 侧边栏**不按步骤过滤**（它没有步骤），所以这里取全部 active 的假说，
    # 但仍然限量：侧边栏是"常驻陪伴"，它最该知道的几件事之一就是
    # **他最近提过想试什么** —— 用户那句话「我想试试之前的猜想」正是
    # 在这个场景下说的。上限用 `MAX_SIDEBAR_HYPOTHESES`，比会话内的 3 条宽，
    # 因为它没有"当前步骤"可以做相关度筛选。
    hypotheses_all = global_reads.list_hypotheses(db)
    kept_hypotheses = hypotheses_all[:MAX_SIDEBAR_HYPOTHESES]
    over_cap_hypotheses = hypotheses_all[MAX_SIDEBAR_HYPOTHESES:]
    hypotheses = [
        HypothesisItem(
            hypothesis_id=row.hypothesis_id,
            statement=row.statement,
            condition=row.condition,
            effect=row.effect,
            verified=False,
            source_session_id=row.source_session_id,
        )
        for row in kept_hypotheses
    ]
    sections.append(
        SectionReport(
            name="假说（想试但还没验证的做法）",
            precedence=4,
            item_count=len(hypotheses),
            dropped_count=len(over_cap_hypotheses),
            dropped_examples=[
                f"{row.hypothesis_id}（超出 {MAX_SIDEBAR_HYPOTHESES} 条上限；"
                f"要看全部请用 retrieve_cognitive(scope=\"hypothesis\")）"
                for row in over_cap_hypotheses[:3]
            ],
        )
    )

    # ---- 5. 策略库 ---- #
    strategies = _strategy_items(db, limit=None)
    sections.append(
        SectionReport(
            name="策略库",
            precedence=5,
            item_count=len(strategies),
            dropped_count=0,
        )
    )

    # ---- 6. 最近的会话 ---- #
    #
    # ★ **不在 Python 里重新排序。** `list_sessions` 已经在 SQL 里按
    # `(created_at, session_id)` 排好了，倒过来就是"最近的在前"。
    #
    # 这里曾经写的是 `sorted(..., key=lambda r: r.created_at, reverse=True)`，
    # 而它在一个真实的测试里炸了：`TypeError: can't compare offset-naive
    # and offset-aware datetimes`。原因是**同一个列表里混了两类对象** ——
    # 刚 `create_session` 出来的行带着 Python 侧的 aware 时间戳，
    # 而从库里读回来的行经过 SQLite 之后 tzinfo 是 None。
    #
    # 排序键一旦依赖"对象是怎么来的"，它就不是一个可靠的事实。
    # 让数据库排（它手里全是同一种值），Python 只负责取最后 N 条。
    all_sessions = list(reversed(session_reads.list_sessions(db)))
    kept = all_sessions[:MAX_RECENT_SESSIONS]
    dropped = all_sessions[MAX_RECENT_SESSIONS:]
    recent: list[SessionRef] = []
    for row in kept:
        summary = session_reads.latest_summary(db, row.session_id)
        head = None
        if summary is not None and summary.content:
            # 只取首段：这是索引用途，不是给人读总结。
            head = summary.content.strip().split("\n", 1)[0][:200]
        recent.append(
            SessionRef(
                session_id=row.session_id,
                folder_id=row.folder_id,
                title=row.title,
                state=row.state.value,
                summary_head=head,
            )
        )
    sections.append(
        SectionReport(
            name="最近的会话",
            precedence=6,
            item_count=len(recent),
            dropped_count=len(dropped),
            dropped_examples=[
                f"{row.session_id}（{row.title}，超出最近 {MAX_RECENT_SESSIONS} 条上限；"
                "用 retrieve_session 显式给 session_id 仍能读到）"
                for row in dropped[:3]
            ],
        )
    )

    return SidebarContext(
        constraints=constraints,
        soft_preferences=soft_items,
        domain_checklist=checklist,
        baseline=baseline,
        evidence=evidence,
        hypotheses=hypotheses,
        strategies=strategies,
        recent_sessions=recent,
        sections=sections,
        precedence_order=[
            "用户偏好（硬约束）",
            "用户偏好（软偏好）",
            "领域勾选",
            "M1 基线",
            "证据",
            "假说（想试但还没验证的做法）",
            "策略库",
            "最近的会话",
        ],
    )


def sidebar_context_as_dict(ctx: SidebarContext) -> dict[str, Any]:
    """给端点用的形状（与会话侧 `DiscussionContext` 同样的姿态：
    读模型一起给出来，界面才能解释"为什么这条排在那条前面"）。"""
    return ctx.model_dump(mode="json")
