"""全局读接口 —— Agentic RAG 的只读契约（M7 §6）。

**只读，且不需要令牌。** 本模块不得 import `writes`（架构规则 ARCH-07），
因此它天然无法改写全局——这正是「Agent 可读全局，不可写全局」在代码层面的落点。

返回值刻意使用 ORM 对象：Router 需要它们做版本链操作。API 层**不得**直接 import
`models`（ARCH-08），应由 P3 的 `schemas.py` 提供 DTO。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.global_domain.enums import (
    HARD_CONSTRAINT_BASIS,
    ChannelObsSourceKind,
    Completeness,
    EvidenceChannel,
    LifecycleStatus,
    ProposalStatus,
    TriggerType,
)
from app.global_domain.models import (
    BenchmarkResponse,
    BenchmarkRun,
    CEvidence,
    M1BaselineCurrent,
    M1BaselineVersion,
    M1ChannelObs,
    M1Variable,
    M1VariableChannel,
    M2Preference,
    M2Strategy,
    RouterLog,
    UpdateIntentRow,
    UpdateProposal,
)


# --------------------------------------------------------------------------- #
# M1 认知基准
# --------------------------------------------------------------------------- #


def list_variables(session: Session) -> list[M1Variable]:
    return list(
        session.scalars(select(M1Variable).order_by(M1Variable.sort_order))
    )


def get_variable(session: Session, variable_id: str) -> M1Variable | None:
    return session.get(M1Variable, variable_id)


def current_baseline_version(
    session: Session, variable_id: str, channel: EvidenceChannel
) -> M1BaselineVersion | None:
    """某 (变量, 通道) 的当前生效版本（经 `m1_baseline_current` 指针）。"""
    return session.scalar(
        select(M1BaselineVersion)
        .join(
            M1BaselineCurrent,
            M1BaselineCurrent.version_id == M1BaselineVersion.version_id,
        )
        .where(
            M1BaselineCurrent.variable_id == variable_id,
            M1BaselineCurrent.channel == channel,
        )
    )


def list_current_baselines(session: Session) -> list[M1BaselineVersion]:
    """所有当前生效的基线行（按变量 + 通道）。一行 = 一个通道，**没有「变量总分」**。"""
    return list(
        session.scalars(
            select(M1BaselineVersion)
            .join(
                M1BaselineCurrent,
                M1BaselineCurrent.version_id == M1BaselineVersion.version_id,
            )
            .order_by(M1BaselineVersion.variable_id, M1BaselineVersion.channel)
        )
    )


def current_baseline_map(
    session: Session,
) -> dict[str, dict[EvidenceChannel, M1BaselineVersion]]:
    """`{变量: {通道: 当前版本}}`。刻意不提供任何「合成一个分数」的出口。"""
    result: dict[str, dict[EvidenceChannel, M1BaselineVersion]] = {}
    for row in list_current_baselines(session):
        result.setdefault(row.variable_id, {})[row.channel] = row
    return result


def baseline_history(
    session: Session, variable_id: str, channel: EvidenceChannel | None = None
) -> list[M1BaselineVersion]:
    """按时间升序返回全部历史版本（含已被取代的），用于回滚与审计。"""
    stmt: Select = select(M1BaselineVersion).where(
        M1BaselineVersion.variable_id == variable_id
    )
    if channel is not None:
        stmt = stmt.where(M1BaselineVersion.channel == channel)
    return list(session.scalars(stmt.order_by(M1BaselineVersion.created_at)))


def get_baseline_version(
    session: Session, version_id: str
) -> M1BaselineVersion | None:
    return session.get(M1BaselineVersion, version_id)


def count_benchmark_runs(
    session: Session, variable_id: str, start: datetime, end: datetime
) -> int:
    """某变量在窗口内**重做基准测试的次数**（M0 §5.4）。

    两个口径都必须在意：

    1. 按 `source_ref`（`benchmark_run_id`）**去重**，而不是数写入行数——一次
       Benchmark 可能对同一变量更新多个通道（N 行），按行计数会把「重做 1 次」
       误算成「重做 N 次」，从而在完全正常的流程上弹出警告。
    2. 只统计 `router_log.trigger = R2` 关联的写入——用户在 UI 上直接改基线（R1）
       同样是 `m1_baseline_version` 里的一行，但那是「直接写入」而非「重做基准测试」，
       不该计入重做次数。
    """
    return int(
        session.scalar(
            select(func.count(func.distinct(M1BaselineVersion.source_ref)))
            .select_from(M1BaselineVersion)
            .join(RouterLog, RouterLog.log_id == M1BaselineVersion.router_log_id)
            .where(
                M1BaselineVersion.variable_id == variable_id,
                M1BaselineVersion.source_ref.is_not(None),
                RouterLog.trigger == TriggerType.R2,
                M1BaselineVersion.created_at >= start,
                M1BaselineVersion.created_at < end,
            )
        )
        or 0
    )


def list_channel_obs(
    session: Session, variable_id: str, channel: EvidenceChannel | None = None
) -> list[M1ChannelObs]:
    """某 `(变量, 通道)` 的全部观测记录（时间倒序）。

    存在的理由很具体：§7.3 的「≥2 条**独立** Session 证据」是一道用户会撞上的
    硬门槛，而撞上之后他需要一个能回答「到底算了几条、是哪几个 Session」
    的地方。只给一个数字（`count_independent_session_evidence`）而看不到明细，
    门槛就会变成一句无法核对的断言 —— 那和「不透明地拒绝」没有区别。
    """
    stmt = select(M1ChannelObs).where(M1ChannelObs.variable_id == variable_id)
    if channel is not None:
        stmt = stmt.where(M1ChannelObs.channel == channel)
    return list(session.scalars(stmt.order_by(M1ChannelObs.observed_at.desc())))


def count_independent_session_evidence(
    session: Session, variable_id: str, channel: EvidenceChannel
) -> int:
    """该 `(变量, 通道)` 的**独立 Session 证据条数**（M0 §7.3 的 ≥2 硬前置条件）。

    按 `source_ref`（Session id）**去重** ——「独立」= 来自不同 Session。
    同一个 Session 里记 5 次仍然只是 1 条证据；按行计数会让「反复记同一件事」
    轻易凑够 ≥2 条，从而绕开这条门槛。
    """
    return int(
        session.scalar(
            select(func.count(func.distinct(M1ChannelObs.source_ref)))
            .select_from(M1ChannelObs)
            .where(
                M1ChannelObs.variable_id == variable_id,
                M1ChannelObs.channel == channel,
                M1ChannelObs.source_kind == ChannelObsSourceKind.SESSION,
                M1ChannelObs.source_ref.is_not(None),
            )
        )
        or 0
    )


# --------------------------------------------------------------------------- #
# 测量覆盖（设计态 + 运行态）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ChannelCoverage:
    """某 `(变量, 通道)` 的测量覆盖视图 —— 设计态与运行态一次给全（M0 §6.6）。"""

    variable_id: str
    channel: EvidenceChannel
    designed: bool
    method_ids: tuple[str, ...]
    design_note: str | None
    #: 该通道**当前**执行到哪一级。`NOT_APPLICABLE` 表示按设计就不该测。
    completeness: Completeness
    current_version_id: str | None


def list_variable_channels(
    session: Session, variable_id: str | None = None
) -> list[M1VariableChannel]:
    stmt = select(M1VariableChannel)
    if variable_id is not None:
        stmt = stmt.where(M1VariableChannel.variable_id == variable_id)
    return list(
        session.scalars(
            stmt.order_by(M1VariableChannel.variable_id, M1VariableChannel.channel)
        )
    )


def measurement_coverage(
    session: Session, variable_id: str
) -> list[ChannelCoverage]:
    """逐通道完整度 —— **唯一**的完整度读取入口。

    刻意不存第二处真相：设计态来自 `m1_variable_channel`，运行态来自当前基线版本。
    两条来源一旦都能被写，就必然漂移。
    """
    out: list[ChannelCoverage] = []
    for row in list_variable_channels(session, variable_id):
        current = current_baseline_version(session, variable_id, row.channel)
        if not row.designed:
            completeness = Completeness.NOT_APPLICABLE
        elif current is not None:
            completeness = current.completeness
        else:
            completeness = Completeness.PARTIAL
        out.append(
            ChannelCoverage(
                variable_id=variable_id,
                channel=row.channel,
                designed=row.designed,
                method_ids=tuple(row.method_ids or ()),
                design_note=row.design_note,
                completeness=completeness,
                current_version_id=(
                    current.version_id if current is not None else None
                ),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# M2 偏好 与 B 策略库
# --------------------------------------------------------------------------- #


def list_preferences(
    session: Session, status: LifecycleStatus | None = None
) -> list[M2Preference]:
    """偏好全量（含已归档）。`status=None` = 不筛。

    归档不是删除：偏好是**时机性**的，而「他三个月前说过什么」本身就是要保留的信息
    —— 否则事后无法解释**当时为什么那样规划**（`router_log` 的 `old_value` 是另一条
    独立的取证路径）。

    > 原先这里引的是「M2 §4 的沉淀逻辑依赖它」——**那是一处错误引用**：M2 §4 通篇讲
    > C 证据，`active|archived` 与「可添加/修改/删除/归档」都是 **C 条目**的规定。
    > 见 M0 §11 A14。
    """
    stmt = select(M2Preference)
    if status is not None:
        stmt = stmt.where(M2Preference.status == status)
    return list(
        session.scalars(
            stmt.order_by(M2Preference.priority.desc(), M2Preference.created_at)
        )
    )


def list_active_preferences(session: Session) -> list[M2Preference]:
    """当前生效的偏好 —— 用户声明的与 LLM 推断的**混在一起**，见下。"""
    return list(
        session.scalars(
            select(M2Preference)
            .where(M2Preference.status == LifecycleStatus.ACTIVE)
            .order_by(M2Preference.priority.desc(), M2Preference.created_at)
        )
    )


# --------------------------------------------------------------------------- #
# 偏好的硬度（缺口 A18 · 用户裁定）
# --------------------------------------------------------------------------- #


def is_hard_constraint(preference: M2Preference) -> bool:
    """这条偏好是不是用户下达的**约束**（而非 LLM 推断出的倾向）。

    判定只在这一个函数里实现。消费方（Agent 的计划生成、HC1 校验）
    **不得**自己比较 `preference.basis == Basis.STATED` —— 那会让
    「哪些声明不可被推翻」这个规则散落成多份，而其中任何一份写错都不会报错，
    只会让 LLM 悄悄推翻用户的话（M2 红线 2 禁止的正是这件事）。

    硬度只看 `basis`，**不看 `target_field`**：`target_field` 决定这条偏好
    是否覆盖某个 SP1 槽位（装配位置），不决定它有多硬。一条没有槽位的
    「我讨厌看网课，因为浪费时间」与一条有槽位的「呈现优先用例子」
    同样不可被推翻。
    """
    return preference.basis in HARD_CONSTRAINT_BASIS


def list_active_constraints(session: Session) -> list[M2Preference]:
    """当前生效的**硬约束** —— 计划必须满足，不是"优先参考"。

    与 `list_active_soft_preferences` 是同一个集合的二分：两者互不相交，
    且并集恰好是 `list_active_preferences()`（有测试钉住这三条性质）。
    """
    return [p for p in list_active_preferences(session) if is_hard_constraint(p)]


def list_active_soft_preferences(session: Session) -> list[M2Preference]:
    """当前生效的**软偏好** —— 可被任务情况推翻，但推翻必须留痕（M2 §2.2）。

    它们是 LLM 观察（`experienced`）或推断（`inferred`）出来的，
    因此与用户声明分属两种**不可互换**的消费口径。
    """
    return [p for p in list_active_preferences(session) if not is_hard_constraint(p)]


def list_strategies(session: Session, limit: int | None = 4) -> list[M2Strategy]:
    """B 策略候选库。默认只取 4 条 —— 对齐 M8 `retrieve_strategy` 的边界（≤4）。"""
    stmt = select(M2Strategy).order_by(
        M2Strategy.default_weight.desc(), M2Strategy.strategy_id
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


# --------------------------------------------------------------------------- #
# C 个性化证据
# --------------------------------------------------------------------------- #


def list_active_evidence(
    session: Session, *, keyword: str | None = None, limit: int | None = None
) -> list[CEvidence]:
    """按 condition / effect / statement 做关键字匹配（MVP 不上向量检索，M0 §6.4）。"""
    stmt = select(CEvidence).where(CEvidence.status == LifecycleStatus.ACTIVE)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                CEvidence.condition.like(like),
                CEvidence.statement.like(like),
                CEvidence.effect.like(like),
            )
        )
    stmt = stmt.order_by(CEvidence.updated_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


# --------------------------------------------------------------------------- #
# 提案 / 意图 / 审计日志
# --------------------------------------------------------------------------- #


def get_proposal(session: Session, proposal_id: str) -> UpdateProposal | None:
    return session.get(UpdateProposal, proposal_id)


def list_proposals(
    session: Session, status: ProposalStatus | None = None
) -> list[UpdateProposal]:
    stmt = select(UpdateProposal)
    if status is not None:
        stmt = stmt.where(UpdateProposal.status == status)
    return list(session.scalars(stmt.order_by(UpdateProposal.created_at.desc())))


def list_pending_proposals(session: Session) -> list[UpdateProposal]:
    """待用户确认队列 —— Agent 需要能感知「上次那条建议还没确认」（M0 §5）。"""
    return list_proposals(session, ProposalStatus.PENDING_CONFIRMATION)


def list_intents(session: Session, proposal_id: str) -> list[UpdateIntentRow]:
    return list(
        session.scalars(
            select(UpdateIntentRow)
            .where(UpdateIntentRow.proposal_id == proposal_id)
            .order_by(UpdateIntentRow.intent_id)
        )
    )


def list_router_logs(
    session: Session, *, target: str | None = None, limit: int = 100
) -> list[RouterLog]:
    stmt = select(RouterLog)
    if target is not None:
        stmt = stmt.where(RouterLog.target == target)
    stmt = stmt.order_by(RouterLog.created_at.desc()).limit(limit)
    return list(session.scalars(stmt))


def count_router_logs(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(RouterLog)) or 0)


# --------------------------------------------------------------------------- #
# Benchmark 运行记录（裁定 G-03 = 甲）
# --------------------------------------------------------------------------- #


def get_benchmark_run(session: Session, run_id: str) -> BenchmarkRun | None:
    return session.get(BenchmarkRun, run_id)


def list_benchmark_responses(
    session: Session, run_id: str
) -> list[BenchmarkResponse]:
    """一次运行的全部作答，按 `(item_id, question_no)` 稳定排序。

    排序刻意用题目与题号而不是 `created_at`：两次作答的时间戳可能相同，
    而"这一行对应哪道题"必须**只有一种解释**。
    """
    return list(
        session.scalars(
            select(BenchmarkResponse)
            .where(BenchmarkResponse.run_id == run_id)
            .order_by(BenchmarkResponse.item_id, BenchmarkResponse.question_no)
        )
    )


def latest_finished_benchmark_run(
    session: Session, variable_id: str, channel: EvidenceChannel
) -> BenchmarkRun | None:
    """该 (变量, 通道) 最近一次**跑完**的运行。

    只看 `finished_at IS NOT NULL`：中途中断的运行分数是半成品，
    拿它当"上次测出来多少"会把"没测完"读成"退步了"。
    """
    return session.scalars(
        select(BenchmarkRun)
        .where(
            BenchmarkRun.variable_id == variable_id,
            BenchmarkRun.channel == channel,
            BenchmarkRun.finished_at.is_not(None),
        )
        .order_by(BenchmarkRun.started_at.desc())
        .limit(1)
    ).first()


def anchor_baseline_for_item(
    session: Session, item_id: str, *, before: datetime | None = None
) -> float | None:
    """某锚题的**基准通过率**（§5.6 等值化的分母）。

    取**最早**一次记录了它的运行，而不是最近一次：
    这个数的作用是"把本次分数换算到可比刻度上"，
    如果它随每次运行自己漂移，那么所有历史分数的可比性会同时失效
    —— 也就是说，用它来校正漂移的东西本身不能漂移。
    """
    stmt = (
        select(BenchmarkRun)
        .where(BenchmarkRun.anchor_baseline.is_not(None))
        .order_by(BenchmarkRun.started_at)
    )
    if before is not None:
        stmt = stmt.where(BenchmarkRun.started_at < before)
    for row in session.scalars(stmt):
        baseline = row.anchor_baseline or {}
        rate = baseline.get(item_id)
        if isinstance(rate, int | float):
            return float(rate)
    return None
