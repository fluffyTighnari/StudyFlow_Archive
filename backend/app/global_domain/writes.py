"""★ 全局写入口 —— **只有 `app.router.*` 可以 import 本模块**（架构规则 ARCH-01）。

本模块提供「原语级」写操作，由 Router（P2）编排为策略化写入。**不在这里做策略判断**：
- 该不该写、要不要用户确认、配额够不够 → 全部属于 Router 的职责（M0 §5、§7.3）。
- 本模块只负责「原子地、留痕地把一行数据写进权威域」。

不变量约定（由 `tests/architecture/test_write_guard.py` 以 AST 强制）：
**每个公开函数的第一条语句必须是 `require(token)`。**

关于保障强度：ARCH-01 收窄了路径，ARCH-08 阻止其他层直接 session.add()，
但真正的兜底是保障④——不变式 I1「全局状态每次变更必须对应 ≥1 条 RouterLog」
（M0 §4.4）。任何绕过本模块的直接插入都会在不变式自检时暴露。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.capability import RouterToken, assert_capability
from app.core.ids import (
    next_benchmark_response_id,
    next_benchmark_run_id,
    next_router_log_id,
    next_m1_baseline_id,
    next_m1_obs_id,
    next_preference_id,
    next_evidence_id,
)
from app.db import DbSequenceStore
from app.global_domain.enums import (
    Basis,
    BaselineConfidence,
    BenchmarkForm,
    CEvidenceSource,
    CEvidenceStrength,
    ChannelObsSourceKind,
    Completeness,
    EvidenceChannel,
    IntentAction,
    IntentStatus,
    JudgedBy,
    LifecycleStatus,
    LLMConfidence,
    Precheck,
    ProposalSource,
    ProposalStatus,
    ResponseQuestionKind,
    RouterLogStatus,
    TriggerType,
)
from app.global_domain.models import (
    BenchmarkResponse,
    BenchmarkRun,
    CEvidence,
    M1BaselineCurrent,
    M1BaselineVersion,
    M1ChannelObs,
    M2Preference,
    RouterLog,
    UpdateIntentRow,
    UpdateProposal,
)
from app.global_domain.reads import current_baseline_version


def require(token: RouterToken) -> None:
    """全局写函数的第一条语句。缺失令牌时抛 CapabilityError。"""
    assert_capability(token)


def _seq(session: Session) -> DbSequenceStore:
    return DbSequenceStore(session)


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# 审计日志（保障④的落点：所有写入都必须伴随一条）
# --------------------------------------------------------------------------- #


def append_router_log(
    token: RouterToken,
    session: Session,
    *,
    source: ProposalSource,
    status: RouterLogStatus,
    trigger: TriggerType | None = None,
    action: IntentAction | None = None,
    target: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    basis: Basis | None = None,
    intent_ref: str | None = None,
    proposal_ref: str | None = None,
    user_confirmed: bool | None = None,
    reasoning: str = "",
    log_id: str | None = None,
) -> RouterLog:
    require(token)
    row = RouterLog(
        log_id=log_id or next_router_log_id(_seq(session)),
        created_at=_now(),
        trigger=trigger,
        source=source,
        action=action,
        target=target,
        old_value=old_value,
        new_value=new_value,
        basis=basis,
        intent_ref=intent_ref,
        proposal_ref=proposal_ref,
        user_confirmed=user_confirmed,
        reasoning=reasoning,
        status=status,
    )
    session.add(row)
    session.flush()
    return row


# --------------------------------------------------------------------------- #
# 提案 / 意图（M0 §5）
# --------------------------------------------------------------------------- #


def create_proposal(
    token: RouterToken,
    session: Session,
    *,
    proposal_id: str,
    source: ProposalSource,
    trigger_type: TriggerType,
    status: ProposalStatus = ProposalStatus.PROPOSED,
    session_ref: str | None = None,
    source_ref: str | None = None,
    note: str | None = None,
) -> UpdateProposal:
    require(token)
    row = UpdateProposal(
        proposal_id=proposal_id,
        session_ref=session_ref,
        source=source,
        trigger_type=trigger_type,
        status=status,
        note=note,
        source_ref=source_ref,
        created_at=_now(),
    )
    session.add(row)
    session.flush()
    return row


def add_intent(
    token: RouterToken,
    session: Session,
    *,
    intent_id: str,
    proposal_id: str,
    action: IntentAction,
    target: str,
    basis: Basis,
    reasoning: str,
    confidence: LLMConfidence,
    value: Any = None,
    priority: int | None = None,
    conflict_note: str | None = None,
    precheck: Precheck | None = None,
    precheck_reason: str | None = None,
) -> UpdateIntentRow:
    require(token)
    row = UpdateIntentRow(
        intent_id=intent_id,
        proposal_id=proposal_id,
        action=action,
        target=target,
        value=value,
        priority=priority,
        basis=basis,
        reasoning=reasoning,
        confidence=confidence,
        conflict_note=conflict_note,
        precheck=precheck,
        precheck_reason=precheck_reason,
        status=IntentStatus.PENDING,
    )
    session.add(row)
    session.flush()
    return row


def set_intent_status(
    token: RouterToken,
    session: Session,
    intent_id: str,
    status: IntentStatus,
    *,
    decided_at: datetime | None = None,
) -> UpdateIntentRow:
    require(token)
    row = session.get(UpdateIntentRow, intent_id)
    if row is None:
        raise KeyError(f"intent 不存在：{intent_id}")
    row.status = status
    row.decided_at = decided_at or _now()
    session.flush()
    return row


def set_proposal_status(
    token: RouterToken,
    session: Session,
    proposal_id: str,
    status: ProposalStatus,
    *,
    resolved_at: datetime | None = None,
) -> UpdateProposal:
    require(token)
    row = session.get(UpdateProposal, proposal_id)
    if row is None:
        raise KeyError(f"proposal 不存在：{proposal_id}")
    row.status = status
    if status in (
        ProposalStatus.APPLIED,
        ProposalStatus.PARTIALLY_APPLIED,
        ProposalStatus.REJECTED,
        ProposalStatus.EXPIRED,
    ):
        row.resolved_at = resolved_at or _now()
    session.flush()
    return row


# --------------------------------------------------------------------------- #
# M1 基线：版本化覆盖与回滚
# --------------------------------------------------------------------------- #


def _point_to(
    session: Session,
    variable_id: str,
    channel: EvidenceChannel,
    version_id: str,
) -> None:
    """移动「当前生效版本」指针（UPSERT）。

    私有内部原语：**只能从带令牌守卫的公开写函数调用**。
    唯一性由 `m1_baseline_current` 的 (variable_id, channel) 主键保证。
    """
    pointer = session.get(M1BaselineCurrent, (variable_id, channel))
    if pointer is None:
        session.add(
            M1BaselineCurrent(
                variable_id=variable_id,
                channel=channel,
                version_id=version_id,
                switched_at=_now(),
            )
        )
    else:
        pointer.version_id = version_id
        pointer.switched_at = _now()
    session.flush()


def insert_baseline_version(
    token: RouterToken,
    session: Session,
    *,
    variable_id: str,
    channel: EvidenceChannel,
    basis: Basis,
    completeness: Completeness,
    score: float | None = None,
    confidence: BaselineConfidence | None = None,
    boundary: str | None = None,
    source_ref: str | None = None,
    router_log_id: str | None = None,
) -> M1BaselineVersion:
    """版本化覆盖：**先插入新版本行，再移动当前指针**。

    历史行永不修改内容。`replaces_version_id` 在插入时指向**已存在**的旧行，
    因此版本链天然可追溯（见 `models.M1BaselineVersion` 的设计说明）。
    """
    require(token)
    previous = current_baseline_version(session, variable_id, channel)

    row = M1BaselineVersion(
        version_id=next_m1_baseline_id(_seq(session), variable_id),
        variable_id=variable_id,
        channel=channel,
        score=score,
        confidence=confidence,
        completeness=completeness,
        boundary=boundary,
        basis=basis,
        source_ref=source_ref,
        created_at=_now(),
        router_log_id=router_log_id,
        replaces_version_id=previous.version_id if previous is not None else None,
    )
    session.add(row)
    session.flush()

    _point_to(session, variable_id, channel, row.version_id)
    return row


def rollback_baseline_version(
    token: RouterToken,
    session: Session,
    *,
    variable_id: str,
    channel: EvidenceChannel,
    to_version_id: str,
    router_log_id: str | None = None,
) -> M1BaselineVersion:
    """回滚 = 复制旧版本取值**再插一行**并移动指针；绝不删除历史（M0 §6.5）。"""
    require(token)
    target = session.get(M1BaselineVersion, to_version_id)
    if target is None:
        raise KeyError(f"目标版本不存在：{to_version_id}")
    if target.variable_id != variable_id or target.channel != channel:
        raise ValueError(
            f"版本 {to_version_id} 不属于 {variable_id}/{channel}"
        )

    previous = current_baseline_version(session, variable_id, channel)
    row = M1BaselineVersion(
        version_id=next_m1_baseline_id(_seq(session), variable_id),
        variable_id=variable_id,
        channel=channel,
        score=target.score,
        confidence=target.confidence,
        completeness=target.completeness,
        boundary=target.boundary,
        basis=target.basis,
        source_ref=f"rollback:{to_version_id}",
        created_at=_now(),
        router_log_id=router_log_id,
        replaces_version_id=previous.version_id if previous is not None else None,
    )
    session.add(row)
    session.flush()

    _point_to(session, variable_id, channel, row.version_id)
    return row


def append_channel_obs(
    token: RouterToken,
    session: Session,
    *,
    variable_id: str,
    channel: EvidenceChannel,
    source_kind: ChannelObsSourceKind,
    value: float | None = None,
    source_ref: str | None = None,
    note: str | None = None,
) -> M1ChannelObs:
    """追加一条通道观测（增量证据，不产生新基线版本）。"""
    require(token)
    row = M1ChannelObs(
        obs_id=next_m1_obs_id(_seq(session)),
        variable_id=variable_id,
        channel=channel,
        value=value,
        source_kind=source_kind,
        source_ref=source_ref,
        note=note,
        observed_at=_now(),
    )
    session.add(row)
    session.flush()
    return row


# --------------------------------------------------------------------------- #
# Benchmark 运行记录（Benchmark v0.3 §7.3；裁定 G-03 = 甲）
# --------------------------------------------------------------------------- #


def create_benchmark_run(
    token: RouterToken,
    session: Session,
    *,
    form: BenchmarkForm,
    variable_id: str,
    channel: EvidenceChannel,
    method_id: str,
    item_ids: list[str],
    seed: int,
    domains: list[dict],
    completeness: Completeness,
    app_version: str | None = None,
    note: str | None = None,
) -> BenchmarkRun:
    """在**运行开始时**创建运行行。

    刻意在开始时创建、而不是等算完分再一次性写：
    一次 60 分钟的运行中途可能中断，而"他答到第 3 题就停了"这个事实
    本身就是有用的信息（它比"什么都没发生"更接近真相）。
    `finished_at IS NULL` 就是"这次没跑完"的标记。
    """
    require(token)
    row = BenchmarkRun(
        run_id=next_benchmark_run_id(_seq(session)),
        form=form,
        variable_id=variable_id,
        channel=channel,
        method_id=method_id,
        item_ids=list(item_ids),
        seed=seed,
        domains=list(domains),
        anchor_baseline={},
        completeness=completeness,
        boundary="",
        started_at=_now(),
        app_version=app_version,
        note=note,
    )
    session.add(row)
    session.flush()
    return row


def append_benchmark_response(
    token: RouterToken,
    session: Session,
    *,
    run_id: str,
    item_id: str,
    question_no: int,
    kind: ResponseQuestionKind,
    judged_by: JudgedBy,
    raw_answer: str | None = None,
    item_score: float | None = None,
    difficulty: str | None = None,
    weight: float | None = None,
    elapsed_sec: float | None = None,
    judge_model: str | None = None,
    judge_note: str | None = None,
    judged_hits: list | None = None,
) -> BenchmarkResponse:
    """落一道题的作答与单题分。逐题落，所以中断也留得下东西。

    `item_score=None` 且 `raw_answer` 非空 = **没测到**（作废），
    与"答错了得 0 分"是两件事（§5.2）。
    """
    require(token)
    row = BenchmarkResponse(
        response_id=next_benchmark_response_id(_seq(session)),
        run_id=run_id,
        item_id=item_id,
        question_no=question_no,
        kind=kind,
        difficulty=difficulty,
        weight=weight,
        raw_answer=raw_answer,
        item_score=item_score,
        elapsed_sec=elapsed_sec,
        judged_by=judged_by,
        judge_model=judge_model,
        judge_note=judge_note,
        judged_hits=judged_hits,
        created_at=_now(),
    )
    session.add(row)
    session.flush()
    return row


def finalize_benchmark_run(
    token: RouterToken,
    session: Session,
    *,
    run_id: str,
    method_score: float | None,
    channel_score: float | None,
    anchor_baseline: dict,
    confidence: BaselineConfidence | None,
    completeness: Completeness,
    boundary: str,
) -> BenchmarkRun:
    """运行结束时补上分数与置信。

    **只允许补一次**：已经 `finished_at` 的运行再补分，说明有两条路径
    在写同一次运行的结果 —— 那会让"这个分数是哪次算出来的"重新变成一个问题。
    """
    require(token)
    row = session.get(BenchmarkRun, run_id)
    if row is None:
        raise KeyError(f"benchmark_run 不存在：{run_id}")
    if row.finished_at is not None:
        raise ValueError(
            f"benchmark_run {run_id} 已经结束过（finished_at={row.finished_at}）；"
            "重复补分会让「这个分数是哪次算出来的」失去唯一答案"
        )
    row.method_score = method_score
    row.channel_score = channel_score
    row.anchor_baseline = dict(anchor_baseline)
    row.confidence = confidence
    row.completeness = completeness
    row.boundary = boundary
    row.finished_at = _now()
    session.flush()
    return row


# --------------------------------------------------------------------------- #
# M2 偏好 与 C 证据
# --------------------------------------------------------------------------- #


def insert_preference(
    token: RouterToken,
    session: Session,
    *,
    statement: str,
    priority: int,
    basis: Basis = Basis.STATED,
    target_field: str | None = None,
    value: str | None = None,
    router_log_id: str | None = None,
    supersedes_pref_id: str | None = None,
) -> M2Preference:
    require(token)
    row = M2Preference(
        pref_id=next_preference_id(_seq(session)),
        statement=statement,
        target_field=target_field,
        value=value,
        priority=priority,
        basis=basis,
        status=LifecycleStatus.ACTIVE,
        router_log_id=router_log_id,
        supersedes_pref_id=supersedes_pref_id,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(row)
    session.flush()
    return row


def set_preference_status(
    token: RouterToken,
    session: Session,
    pref_id: str,
    status: LifecycleStatus,
    *,
    router_log_id: str | None = None,
) -> M2Preference:
    require(token)
    row = session.get(M2Preference, pref_id)
    if row is None:
        raise KeyError(f"偏好不存在：{pref_id}")
    row.status = status
    row.router_log_id = router_log_id or row.router_log_id
    session.flush()
    return row


def insert_evidence(
    token: RouterToken,
    session: Session,
    *,
    statement: str,
    condition: str,
    effect: str,
    evidence_strength: CEvidenceStrength,
    source: CEvidenceSource,
    reason: str | None = None,
    router_log_id: str | None = None,
) -> CEvidence:
    """新增 C 条目。`condition` 必填 —— 无条件的陈述应归 SP1 而非 C（M2 §4.4 第 3 条）。"""
    require(token)
    if not condition or not condition.strip():
        raise ValueError(
            "C 条目必须带适用条件；纯偏好（无 condition）应写入 M2.preferences（M2 §4.4）"
        )
    row = CEvidence(
        evidence_id=next_evidence_id(_seq(session)),
        statement=statement,
        condition=condition,
        effect=effect,
        evidence_strength=evidence_strength,
        source=source,
        status=LifecycleStatus.ACTIVE,
        router_log_id=router_log_id,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(row)
    session.flush()
    return row


def set_evidence_status(
    token: RouterToken,
    session: Session,
    evidence_id: str,
    status: LifecycleStatus,
    *,
    reason: str | None = None,
    router_log_id: str | None = None,
) -> CEvidence:
    require(token)
    row = session.get(CEvidence, evidence_id)
    if row is None:
        raise KeyError(f"C 条目不存在：{evidence_id}")
    row.status = status
    row.router_log_id = router_log_id or row.router_log_id
    session.flush()
    return row


__all__ = [
    "require",
    "append_router_log",
    "create_proposal",
    "add_intent",
    "set_intent_status",
    "set_proposal_status",
    "insert_baseline_version",
    "rollback_baseline_version",
    "append_channel_obs",
    "insert_preference",
    "set_preference_status",
    "insert_evidence",
    "set_evidence_status",
]
