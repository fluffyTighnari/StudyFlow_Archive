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
from app.core.errors import ValidationRejected
from app.core.ids import (
    next_benchmark_response_id,
    next_benchmark_run_id,
    next_domain_profile_id,
    next_router_log_id,
    next_m1_baseline_id,
    next_m1_obs_id,
    next_preference_id,
    next_evidence_id,
    next_hypothesis_id,
)
from app.db import DbSequenceStore
from app.global_domain.enums import (
    Basis,
    BaselineConfidence,
    BenchmarkForm,
    BenchmarkRunStatus,
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
    DomainProfile,
    DomainTopic,
    Hypothesis,
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
    topic_snapshot: list[dict],
    completeness: Completeness,
    app_version: str | None = None,
    note: str | None = None,
) -> BenchmarkRun:
    """在**运行开始时**创建运行行，状态 `in_progress`。

    刻意在开始时创建、而不是等算完分再一次性写：
    一次 60 分钟的运行中途可能中断，而"他答到第 3 题就停了"这个事实
    本身就是有用的信息（它比"什么都没发生"更接近真相）。

    ★ **状态由 `status` 承担，不由 `finished_at IS NULL` 承担**：
    用户「保存并退出」之后，那次运行既不是"还在做"、也不是"做完了"。
    只有 NULL 时这两种情形无法区分，于是"下次是继续还是重开"没有答案。
    """
    require(token)
    started = _now()
    row = BenchmarkRun(
        run_id=next_benchmark_run_id(_seq(session)),
        form=form,
        status=BenchmarkRunStatus.IN_PROGRESS,
        variable_id=variable_id,
        channel=channel,
        method_id=method_id,
        item_ids=list(item_ids),
        seed=seed,
        topic_snapshot=list(topic_snapshot),
        anchor_baseline={},
        completeness=completeness,
        boundary="",
        started_at=started,
        app_version=app_version,
        note=note,
        status_history=[
            {"status": BenchmarkRunStatus.IN_PROGRESS.value, "at": started.isoformat()}
        ],
    )
    session.add(row)
    session.flush()
    return row


#: 允许的运行状态迁移。**只有这些**，其余一律拒绝。
#:
#: 为什么把这张表写出来、而不是在函数里散着判：
#: 「一次运行能被改成什么样」是产品规则（用户要求：可中断保存退出、可继续、
#: 可重新开始），而规则散在几个 `if` 里时，加一条新状态的人**看不到全貌**，
#: 只会补自己那一处。写成一张表之后，漏掉的迁移是**看得见的空白**。
#:
#: 注意 `FINISHED` 与 `ABANDONED` 都是**终态**：出了这两格没有任何下一步。
#: 尤其是 `FINISHED` 不可退回 —— 一次已经算出分数的运行若被改回"进行中"，
#: 那个分数的来历就不再是唯一的了（与 `finalize_benchmark_run` 只允许补一次同因）。
RUN_STATUS_TRANSITIONS: dict[BenchmarkRunStatus, frozenset[BenchmarkRunStatus]] = {
    BenchmarkRunStatus.IN_PROGRESS: frozenset(
        {
            BenchmarkRunStatus.INTERRUPTED,  # 用户"保存并退出"
            BenchmarkRunStatus.FINISHED,  # 正常走完
            BenchmarkRunStatus.ABANDONED,  # 用户"重新开始"
        }
    ),
    BenchmarkRunStatus.INTERRUPTED: frozenset(
        {
            BenchmarkRunStatus.IN_PROGRESS,  # 用户"继续上次"
            BenchmarkRunStatus.FINISHED,  # 继续之后走完
            BenchmarkRunStatus.ABANDONED,  # 用户改主意，重新开始
        }
    ),
    BenchmarkRunStatus.FINISHED: frozenset(),
    BenchmarkRunStatus.ABANDONED: frozenset(),
}


def set_benchmark_run_status(
    token: RouterToken,
    session: Session,
    *,
    run_id: str,
    target: BenchmarkRunStatus,
    reason: str | None = None,
) -> BenchmarkRun:
    """迁移一次运行的状态（中断 / 继续 / 作废）。

    非法迁移在这里**报错**，不静默改成"最近的合法状态" ——
    后者会让"我明明点了重新开始"变成一件无法追查的事。
    """
    require(token)
    row = session.get(BenchmarkRun, run_id)
    if row is None:
        raise KeyError(f"benchmark_run 不存在：{run_id}")
    allowed = RUN_STATUS_TRANSITIONS[BenchmarkRunStatus(row.status)]
    if target not in allowed:
        raise ValueError(
            f"benchmark_run {run_id} 不能从 {row.status} 转到 {target}；"
            f"允许的是：{sorted(allowed) or '（终态，无后续）'}"
        )
    row.status = target
    # ★ 历史**只追加**。把它写成"记录当前状态"会让本表失去它当初被批准为
    # `source_ref` 的那个理由（只追加、不改写）—— 而那个损失没有任何症状：
    # 当前状态照样是对的，只是"他放弃过几次"永远读不出来了。
    row.status_history = [
        *(row.status_history or []),
        {"status": target.value, "at": _now().isoformat(), "reason": reason},
    ]
    if reason:
        row.note = reason
    session.flush()
    return row


def append_domain_profile(
    token: RouterToken,
    session: Session,
    *,
    router_log_id: str,
    familiar: list[str],
    interested: list[str],
    reason: str | None = None,
) -> DomainProfile:
    """写入一版新的领域勾选档案。**只追加，不改写旧版。**

    ## 为什么不过 `PRECHECK_TABLE`（照抄 G-03 的先例）

    判定表回答的是「这条**关于用户的断言**准不准入」。而这份清单是
    **测量的参照**，不是断言 —— 「我熟悉生物学」这句话是用户对自己状态的
    直接声明，它不需要被 Router 判定"能不能进"，因为它本来就不是被推断出来的。
    把它塞进判定表会逼出一个荒唐的问题：「你熟悉什么，需要系统批准吗」。

    这与 `benchmark_run` 不进判定表是同一个理由（见 `Router.start_benchmark_run`）。

    ## 在这里（而不是在 API 层）校验，是因为这是唯一的写入口

    两条规则：
    1. 每个 `topic_id` 必须真的在 `domain_topic` 里
    2. `interested` 必须是 `familiar` 补集的子集 —— **不会有人对"自己已经
       声称熟悉"的东西再勾一次兴趣**

    第 2 条容易被当成界面细节，但它不是：如果允许重叠，那么同一个专题
    既"熟悉"又"感兴趣"，而这两个标签一个进陌生度计算、一个不进，
    重叠时**哪条规则生效**就取决于代码里的判断顺序 —— 那种东西无法向用户解释。

    **ARCH-05 意味着这里不能 import `app.benchmark.topics`**，
    所以校验问的是数据库里的 `domain_topic`（同域的表）。
    那份 JSON 与这张表由 `seeds.loader` 保持同步。
    """
    require(token)
    known = {row[0] for row in session.query(DomainTopic.topic_id).all()}
    if not known:
        raise ValueError(
            "domain_topic 目录表是空的 —— 主题表没有被播种。"
            "勾选无法被校验，因此拒绝写入（见 app.seeds.loader）"
        )
    for field_name, values in (("familiar", familiar), ("interested", interested)):
        if len(set(values)) != len(values):
            raise ValueError(f"{field_name} 里有重复项：{values}")
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(
                f"{field_name} 里有不在主题表里的专题：{unknown} —— "
                "拼错的 id 会被安静地存下来，然后那条勾选永远匹配不上任何题目"
            )
    overlap = sorted(set(familiar) & set(interested))
    if overlap:
        raise ValueError(
            f"这些专题同时被勾了「熟悉」和「感兴趣」：{overlap} —— "
            "兴趣只在**陌生区**里问（见 _领域清单与抽题 §2），"
            "重叠会让「陌生度」该按哪条规则算变成一个取决于判断顺序的问题"
        )

    latest = (
        session.query(DomainProfile).order_by(DomainProfile.version_no.desc()).first()
    )
    row = DomainProfile(
        profile_id=next_domain_profile_id(_seq(session)),
        version_no=(latest.version_no + 1) if latest else 1,
        router_log_id=router_log_id,
        familiar=sorted(familiar),
        interested=sorted(interested),
        reason=reason,
        created_at=_now(),
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
    #: 隐性参数（P11）：**记录，不自动计分**。见 `BenchmarkResponseWrite`。
    hesitation_sec: float | None = None,
    switch_count: int | None = None,
    help_request_count: int | None = None,
    hint_used_count: int | None = None,
    judge_model: str | None = None,
    judge_note: str | None = None,
    judged_hits: list | None = None,
    scoring_method: str | None = None,
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
        hesitation_sec=hesitation_sec,
        switch_count=switch_count,
        help_request_count=help_request_count,
        hint_used_count=hint_used_count,
        judged_by=judged_by,
        judge_model=judge_model,
        judge_note=judge_note,
        judged_hits=judged_hits,
        scoring_method=scoring_method,
        created_at=_now(),
    )
    session.add(row)
    session.flush()
    return row


def set_benchmark_response_score(
    token: RouterToken,
    session: Session,
    response_id: str,
    *,
    item_score: float | None,
    scoring_method: str,
    judged_by: JudgedBy,
    judge_model: str | None = None,
    judge_note: str | None = None,
    judged_hits: list | None = None,
) -> BenchmarkResponse:
    """**改写**一条作答的计分结果（P9：服务端计分）。

    ## 为什么是"改写"而不是"追加一条"

    计分是对**同一次作答**的重新判定，不是一次新的作答 ——
    追加会让"他答了几题"这个数越算越大，而那是 `response_count`
    与最小题数的分母。

    ## 什么会被改写、什么不会

    只改这五列：`item_score` / `scoring_method` / `judged_by` /
    `judge_model` / `judge_note` / `judged_hits`。
    **`raw_answer` 一个字都不动** —— 它是"他当时写了什么"，
    而这个系统里没有任何理由去改它。判分变了要能看出来**是判分变了**，
    不是他改了答案。
    """
    require(token)
    row = session.get(BenchmarkResponse, response_id)
    if row is None:
        raise ValidationRejected(f"作答不存在：{response_id}")
    row.item_score = item_score
    row.scoring_method = scoring_method
    row.judged_by = judged_by
    row.judge_model = judge_model
    row.judge_note = judge_note
    row.judged_hits = judged_hits
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
    """运行结束时补上分数与置信，并把状态推到 `finished`。

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
    # 状态与 `finished_at` **一起**推，不让它们有机会不一致。
    # 一次"有 finished_at 但状态还是 in_progress"的运行会让
    # 「他做完了没有」有两个互相矛盾的答案，而两个都来自同一行数据。
    allowed = RUN_STATUS_TRANSITIONS[BenchmarkRunStatus(row.status)]
    if BenchmarkRunStatus.FINISHED not in allowed:
        raise ValueError(
            f"benchmark_run {run_id} 的状态是 {row.status}，不能补分"
            "（只有 in_progress / interrupted 能走到 finished）"
        )
    row.status = BenchmarkRunStatus.FINISHED
    row.status_history = [
        *(row.status_history or []),
        {
            "status": BenchmarkRunStatus.FINISHED.value,
            "at": _now().isoformat(),
            "reason": "补分并结束",
        },
    ]
    row.method_score = method_score
    row.channel_score = channel_score
    row.anchor_baseline = dict(anchor_baseline)
    row.confidence = confidence
    row.completeness = completeness
    row.boundary = boundary
    row.finished_at = _now()
    session.flush()
    return row


def record_anchor_baseline(
    token: RouterToken, session: Session, run_id: str, *, baseline: dict
) -> BenchmarkRun:
    """把这次运行的锚题读数记成**基线**（P12）。

    ★ 只写**还没有基线**的那些锚题：`reads.anchor_baselines` 取的是最早那次，
    所以一道锚题一旦有了基线，后来的运行写进去也不会改变它 ——
    那会让"用来校正漂移的东西"自己漂移，而它一漂，**全部历史分数的可比性同时失效**。
    与其留一个"写了但不算数"的字段（下一个人会以为它算数），
    不如在这里就只写该写的那部分。

    ★ 空字典**不写**：一次没有锚题读数的运行不该留下一个空的"基线已建立"痕迹 ——
    那会让"这次建立了基线吗"这个问题的答案变成"是，但是空的"。
    """
    require(token)
    row = session.get(BenchmarkRun, run_id)
    if row is None:
        raise ValueError(f"benchmark_run {run_id} 不存在")
    if not baseline:
        return row
    merged = dict(row.anchor_baseline or {})
    fresh = {item_id: value for item_id, value in baseline.items() if item_id not in merged}
    if not fresh:
        return row
    row.anchor_baseline = {**merged, **fresh}
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
    basis: Basis = Basis.EXPERIENCED,
    reason: str | None = None,
    router_log_id: str | None = None,
) -> CEvidence:
    """新增 C 条目。`condition` 必填 —— 无条件的陈述应归 SP1 而非 C（M2 §4.4 第 3 条）。

    ★ P7：`basis` 必须落在 `{experienced, measured}`。两个理由，第一个是硬的：

    1. **判定表已经挡过这一层**（`_EXPERIENCED_TARGETS` 收紧到
       `{M2_PREFERENCE, C_EVIDENCE}`）—— 这里再挡一次是"纵深"，不是重复：
       直接调 `insert_evidence` 的代码路径（测试、种子、将来的脚本）绕得过判定表，
       但绕不过这里。
    2. 「他只是说想试试」不是证据，它是**假说**（`insert_hypothesis`）。
       把两者混在一起，装配时「只看已验证」就会退化成一个要记得的过滤。
    """
    require(token)
    if not condition or not condition.strip():
        raise ValueError(
            "C 条目必须带适用条件；纯偏好（无 condition）应写入 M2.preferences（M2 §4.4）"
        )
    if basis not in (Basis.EXPERIENCED, Basis.MEASURED):
        raise ValueError(
            f"C 条目（已验证的做法）不能以 basis={basis.value} 写入 —— "
            "他只是说想试的话，那是**假说**，请写 C.hypothesis；"
            "推断出来的也不构成证据（红线 3 的同一条理由）"
        )
    row = CEvidence(
        evidence_id=next_evidence_id(_seq(session)),
        statement=statement,
        condition=condition,
        effect=effect,
        basis=basis,
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


def insert_hypothesis(
    token: RouterToken,
    session: Session,
    *,
    statement: str,
    condition: str,
    effect: str,
    source_session_id: str | None = None,
    router_log_id: str | None = None,
) -> Hypothesis:
    """新增一条假说 —— 他**想试但还没试**的具体做法。

    ## 三条拒绝规则，各自对应一个具体的坏输入

    - **`condition` 为空** → 一句"我喜欢先推导"没有场景槽。它不是具体做法，
      是偏好，该走 `M2.preferences`。这条规则让"方法 vs 偏好"成为**结构的**
      区分而不是提示词里的请求（用户 P7 的明确要求）。
    - **`effect` 为空** → 「我想试试」但说不出试什么。那不是假说，
      那是一次还没想清楚的感觉 —— 它该留在聊天记录里。
    - **`statement` 为空** → 没有给人看的一句话。

    ## `basis` 恒为 stated，且不接受参数

    这是本函数**唯一不开放给调用方的字段**。理由：假说的定义就是"他只是说了"。
    开一个 `basis` 参数会让"以 experienced 写在假说表里"变成一次拼写错误，
    而那恰好是判定表专门拒绝的那一格。想让一个字段填错也不容易，
    比多给一个参数更有价值。
    """
    require(token)
    for name, value, why in (
        ("condition", condition, "假说必须说明**什么场景**；没有场景的倾向是偏好（M2.preferences）"),
        ("effect", effect, "假说必须说明**具体怎么做**；说不清做什么就还不算一个方法"),
        ("statement", statement, "假说要有一句给人看的完整表述"),
    ):
        if not value or not value.strip():
            raise ValueError(f"`{name}` 不能为空 —— {why}")
    row = Hypothesis(
        hypothesis_id=next_hypothesis_id(_seq(session)),
        statement=statement,
        condition=condition,
        effect=effect,
        basis=Basis.STATED,
        source_session_id=source_session_id,
        status=LifecycleStatus.ACTIVE,
        router_log_id=router_log_id,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(row)
    session.flush()
    return row


def set_hypothesis_status(
    token: RouterToken,
    session: Session,
    hypothesis_id: str,
    status: LifecycleStatus,
    *,
    reason: str | None = None,
    router_log_id: str | None = None,
) -> Hypothesis:
    """归档 / 恢复一条假说。

    ## 入口（P7.1 起）

    `IntentAction.ARCHIVE` / `IntentAction.RESTORE` → `hypothesis_policy`，
    对外是 `DELETE /api/hypotheses/{id}` 与 `POST /api/hypotheses/{id}/restore`。

    之前这里写着"入口待裁定（缺口 A14）"。那个说法是错的：判定表要挡的是
    **模型替用户断言**，而用户删自己的东西从来不该被挡 —— 缺的不是裁定，
    是 `IntentAction` 里那个词。

    ## 为什么 `reason` 只进日志、不进行

    理由不是假说的一部分，是**这次改动**的一部分。写进行里会让"他当初为什么
    删掉它"跟着行一起被下一次改动覆盖，而日志是 append-only 的。
    """

    require(token)
    row = session.get(Hypothesis, hypothesis_id)
    if row is None:
        raise KeyError(f"假说不存在：{hypothesis_id}")
    row.status = status
    row.router_log_id = router_log_id or row.router_log_id
    row.updated_at = _now()
    session.flush()
    return row


def revise_hypothesis(
    token: RouterToken,
    session: Session,
    hypothesis_id: str,
    *,
    statement: str,
    condition: str,
    effect: str,
    router_log_id: str | None = None,
) -> Hypothesis:
    """改写一条假说的三个槽（整体替换）。

    ## 为什么不能改 `basis`

    因为假说的可信度**不由内容决定，由"他试没试过"决定**。改写说的是
    "我上次说得不准"，不是"我试过了" —— 后者只有写成 `C.evidence` 才成立。
    所以这个函数里没有 `basis` 参数，与 `insert_hypothesis` 一致：
    **想改可信度，只能换一张表。**

    ## 为什么不能改 `hypothesis_id`

    id 出现在 `router_log.target` 里（`C.hypothesis.H-0001`）。换 id 就等于
    让"这条被改过三次"的历史断成三条互不相干的记录，而历史的价值恰恰在连续性。
    """

    require(token)
    for name, value, why in (
        ("condition", condition, "场景不能空 —— 空了它就不是具体做法"),
        ("effect", effect, "做法不能空 —— 那他就没说想试什么"),
        ("statement", statement, "要有一句给人看的完整表述"),
    ):
        if not value or not value.strip():
            raise ValueError(f"`{name}` 不能为空 —— {why}")

    row = session.get(Hypothesis, hypothesis_id)
    if row is None:
        raise KeyError(f"假说不存在：{hypothesis_id}")
    row.statement = statement
    row.condition = condition
    row.effect = effect
    row.router_log_id = router_log_id or row.router_log_id
    row.updated_at = _now()
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
    """归档 / 恢复一条 C 证据。与 `set_hypothesis_status` 逐字同构。"""

    require(token)
    row = session.get(CEvidence, evidence_id)
    if row is None:
        raise KeyError(f"C 条目不存在：{evidence_id}")
    row.status = status
    row.router_log_id = router_log_id or row.router_log_id
    row.updated_at = _now()
    session.flush()
    return row


def revise_evidence(
    token: RouterToken,
    session: Session,
    evidence_id: str,
    *,
    statement: str,
    condition: str,
    effect: str,
    evidence_strength: object,
    router_log_id: str | None = None,
) -> CEvidence:
    """改写一条 C 证据（三个槽 + 强度整体替换）。

    ## 为什么这次没有写 `c_evidence_revision`

    那张表是为「用户直接维护 C，但仍留痕」设计的，而它现在**有更好的替代**：
    `router_log.old_value / new_value` 记的是改动前后的完整快照，是结构化 JSON，
    而且它就在文档指定的「唯一审计源」里。

    往两处各写一份改动史，最坏的结果不是冗余，是**两处会不一致**，
    而不一致时没有人能判断哪份是对的。`c_evidence_revision` 因此保持
    "有表、无写入方"的状态 —— 这是刻意留下的决定，不是遗漏（见缺口表 A14 的收尾说明）。

    ## `basis` 同样不可改

    见 `revise_hypothesis` 的说明：改写不是"我试过了"的意思表示。
    """

    require(token)
    for name, value, why in (
        ("condition", condition, "场景不能空 —— 空了它就不是具体做法，那是偏好"),
        ("effect", effect, "做法不能空"),
        ("statement", statement, "要有一句给人看的完整表述"),
    ):
        if not value or not value.strip():
            raise ValueError(f"`{name}` 不能为空 —— {why}")

    row = session.get(CEvidence, evidence_id)
    if row is None:
        raise KeyError(f"C 条目不存在：{evidence_id}")
    row.statement = statement
    row.condition = condition
    row.effect = effect
    row.evidence_strength = evidence_strength  # type: ignore[assignment]
    row.router_log_id = router_log_id or row.router_log_id
    row.updated_at = _now()
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
    "insert_hypothesis",
    "revise_evidence",
    "revise_hypothesis",
    "set_hypothesis_status",
    "set_evidence_status",
]
