"""Router —— 全局策略的**唯一写入入口**（M0 §5 / §7）。

提交时序（§5.2）在本文件里逐条对应：

```
① Schema 校验      —— Pydantic（contracts）+ Registry 声明的 value_schema
② 策略查表 precheck —— precheck.evaluate（逐条，确定性）
③ 冲突分析         —— conflict.analyze_conflict（纯函数，只报告）
④ 落库 Proposal    —— status=proposed → 立即转 pending_confirmation
⑤ 用户确认（如需）  —— resolve()
⑥ 策略执行         —— policies（逐条，各自 SAVEPOINT 原子）
⑦ RouterLog 固化   —— 每条动作一条日志，关联 intent_id / proposal_id
```

**事务边界**：④ 与 ⑦ 各自原子；⑥ 的逐条应用各自原子（允许部分成功 →
`partially_applied`）。本类**不 commit** —— 提交由调用方（API 层 / 测试）决定，
这样一次请求要么整体生效、要么整体不生效，而单条 intent 的失败不会拖垮同批的其它条。

**裁定发生在「写入时刻」而不是「提交时刻」**：提交时可能只有一部分 intent 会被立刻
写入（其余等待用户确认）。如果在提交时就把同 target 的落败者标成 skipped，
而胜者恰好是等待确认的那条，就可能出现「胜者永远没被批准、落败者被提前丢弃」
—— 一次提交什么都没发生。因此每次**实际写入**都重新做一次冲突裁定。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.capability import RouterToken
from app.core.errors import NotFound, ValidationRejected, VersionConflict
from app.core.ids import next_proposal_id, next_router_log_id
from app.db import DbSequenceStore
from app.global_domain.enums import (
    Basis,
    BaselineConfidence,
    BenchmarkForm,
    BenchmarkRunStatus,
    Completeness,
    EvidenceChannel,
    IntentAction,
    IntentStatus,
    JudgedBy,
    Precheck,
    ProposalSource,
    ProposalStatus,
    ResponseQuestionKind,
    RouterLogStatus,
    TriggerType,
)
from app.global_domain.invariants import (
    assert_global_invariants,
    invariants_enabled,
)
from app.global_domain.models import UpdateIntentRow
from app.global_domain.reads import (
    current_baseline_version,
    get_baseline_version,
    get_proposal,
    list_intents,
)
from app.global_domain.writes import (
    add_intent,
    append_benchmark_response,
    append_domain_profile,
    append_router_log,
    create_benchmark_run,
    create_proposal,
    finalize_benchmark_run,
    record_anchor_baseline,
    rollback_baseline_version,
    set_benchmark_run_status,
    set_benchmark_response_score,
    set_intent_status,
    set_proposal_status,
)
from app.router import precheck as precheck_module
from app.router.capability import new_token
from app.router.conflict import analyze_conflict, losers_of, rejected_of
from app.router.contracts import (
    BenchmarkAdvisory,
    ConflictReport,
    Decision,
    IntentResult,
    PrecheckResult,
    RollbackResult,
    SubmitRequest,
    SubmitResult,
    UpdateIntent,
)
from app.router.policies import (
    c_policy,
    hypothesis_policy,
    m1_policy,
    m2_policy,
    noop_policy,
)
from app.router.policies.base import (
    PolicyContext,
    PolicyError,
    PolicyRefused,
)
from app.global_domain.targets import D_TARGET_ROOT
from app.router.policies.benchmark_advisory import evaluate_benchmark_advisory
from app.router.registry import (
    POLICY_C,
    POLICY_HYPOTHESIS,
    POLICY_M1,
    POLICY_M2,
    POLICY_NOOP,
    resolve_target,
    source_for_trigger,
    target_for_variable,
)

#: `TargetRule.policy_key` → 策略实现。**唯一**的策略分发点：
#: 加一个 target 只需在 Registry 里加一行 + 这里加一个映射。
_POLICIES = {
    POLICY_M1: m1_policy,
    POLICY_M2: m2_policy,
    POLICY_C: c_policy,
    POLICY_HYPOTHESIS: hypothesis_policy,
    POLICY_NOOP: noop_policy,
}


def _as_channel(value: EvidenceChannel | str) -> EvidenceChannel:
    """把通道归一成枚举；未知取值转成领域级拒绝而不是 `AttributeError`。

    `EvidenceChannel` 是 `StrEnum`，因此 `"measured" == EvidenceChannel.MEASURED`
    为真 —— 这反而更危险：任何一处用 `==` 比较的地方都会**静默通过**，
    而用 `is` 比较的地方会静默不等。两种都不会报错，直到很远的地方才以
    `AttributeError: 'str' object has no attribute 'value'` 的形式暴露。
    所以入口处一次性归一。
    """
    if isinstance(value, EvidenceChannel):
        return value
    try:
        return EvidenceChannel(value)
    except ValueError as exc:
        raise ValidationRejected(
            f"未知证据通道：{value!r}（合法取值：{[c.value for c in EvidenceChannel]}）"
        ) from exc


def _policy_for(intent: UpdateIntent):
    """`no_change` 是唯一不按 target 分发的情况（§5.3「任意」行）。

    它仍然经过这张表，而不是在执行逻辑里特判 —— 分发表因此始终是
    「action + target → 策略」的完整回答处。
    """
    if intent.action is IntentAction.NO_CHANGE:
        return _POLICIES[POLICY_NOOP]
    resolved = resolve_target(intent.target)
    if resolved is None:  # pragma: no cover - precheck 已挡住
        raise PolicyError(f"target 未注册：{intent.target}")
    return _POLICIES[resolved.rule.policy_key]


@dataclass(frozen=True)
class _Batch:
    """一次写入批次的不变上下文。"""

    trigger: TriggerType
    source: ProposalSource
    proposal_id: str
    #: 证据出处：`benchmark_run_id` / `session_id` / `None`。
    source_ref: str | None


def _intent_from_row(row: UpdateIntentRow) -> UpdateIntent:
    return UpdateIntent(
        intent_id=row.intent_id,
        action=row.action,
        target=row.target,
        value=row.value,
        priority=row.priority,
        basis=row.basis,
        reasoning=row.reasoning,
        confidence=row.confidence,
        conflict_note=row.conflict_note,
    )


def _result_from_row(row: UpdateIntentRow) -> IntentResult:
    return IntentResult(
        intent_id=row.intent_id,
        verdict=row.precheck or Precheck.MUST_REJECT,
        reason=row.precheck_reason or "",
        status=row.status,
    )


class Router:
    """全局策略的唯一写入入口。

    令牌在**构造时签发并只保存在实例内部**：调用方拿不到它，因此无法绕过本类
    直接调用 `global_domain.writes`（即便强行 import 也会被 `require(token)` 拒绝）。
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._token: RouterToken = new_token()

    # ------------------------------------------------------------------ #
    # 只读：判定
    # ------------------------------------------------------------------ #

    def precheck(self, trigger: TriggerType, intent: UpdateIntent) -> PrecheckResult:
        """确定性判定，**不写任何东西**（含 `advisories` / `blockers`）。"""
        return precheck_module.evaluate(self._session, trigger, intent)

    def benchmark_advisory(self, variable_id: str) -> BenchmarkAdvisory:
        """某变量本季度的 R2 重做建议（M0 §5.4）。

        存在的理由一半是设计、一半是架构：UI 需要在**跑基准测试之前**就把
        「本季度已重做 N 次」显示出来，但 `app.api` 不得 import
        `app.router.policies`（ARCH-02）。与其让 API 自己再算一遍
        （那就是第二处真相），不如在唯一入口上开一个只读方法。
        """
        return evaluate_benchmark_advisory(self._session, variable_id)

    # ------------------------------------------------------------------ #
    # 写入：测量记账（基准运行记录，**不产生 Intent，因此不经过判定表**）
    # ------------------------------------------------------------------ #

    def start_benchmark_run(
        self,
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
    ) -> str:
        """开一次基准运行，返回 `run_id`。状态起点是 `in_progress`。

        **为什么不放进判定表**：判定表回答的是「这条关于用户的断言准不准入」，
        而一次运行记录本身**不是断言** —— 它是断言的依据。把它塞进判定表
        会逼出一个荒唐的问题：「测量过程需要用户确认吗」。

        **为什么归 Router 而不是让 benchmark 自己写**：ARCH-01 规定
        `app.global_domain.writes` 只有 `app.router.*` 能引用，令牌也只有
        Router 能签发（ARCH-03）。而这不是勉强的安排 —— `count_benchmark_runs`
        说明 Router **本来就认识** `benchmark_run_id` 这个词汇（§5.4 按 run_id
        去重计算重做次数），所以"谁来记账"早有唯一答案。
        """
        return create_benchmark_run(
            self._token,
            self._session,
            form=form,
            variable_id=variable_id,
            channel=channel,
            method_id=method_id,
            item_ids=item_ids,
            seed=seed,
            topic_snapshot=topic_snapshot,
            completeness=completeness,
            app_version=app_version,
            note=note,
        ).run_id

    def set_benchmark_run_status(
        self,
        *,
        run_id: str,
        target: BenchmarkRunStatus,
        reason: str | None = None,
    ):
        """中断 / 继续 / 作废一次运行。

        **三种用户动作走同一个方法**，因为它们是同一个东西的三个取值：
        「这次测试现在算什么状态」。拆成三个方法会让
        `RUN_STATUS_TRANSITIONS` 那张表被绕过 —— 每个方法各写各的判断，
        而漏掉一条非法迁移是看不见的。
        """
        return set_benchmark_run_status(
            self._token,
            self._session,
            run_id=run_id,
            target=target,
            reason=reason,
        )

    def set_domain_profile(
        self,
        *,
        familiar: list[str],
        interested: list[str],
        reason: str | None = None,
    ) -> str:
        """写入一版领域勾选清单（G-04 = 乙），返回 `profile_id`。

        ## 为什么不过判定表，却仍然记日志

        判定表回答「这条关于用户的断言准不准入」。而这份清单**不是断言** ——
        它是测量的**参照**：「我熟悉生物学」是用户对自己状态的直接声明，
        不是系统推断出来的结论。把它塞进判定表会逼出一个荒唐的问题：
        「你熟悉什么，需要系统批准吗」。

        这与 `start_benchmark_run` 不进判定表是同一个理由（G-03 的先例）。
        但两者的**记账**不同：运行记录用 `source_ref`（分数指向它），
        而勾选清单用 `R6` + 一条 RouterLog —— 理由与 `m2_preference` 完全相同：
        「用户声明即事实，因此更需要知道它是什么时候被谁写进来的」。
        """
        session, token = self._session, self._token
        store = DbSequenceStore(session)

        with session.begin_nested():
            # 先铸 log_id：profile 行要带上它，而日志又要引用 profile_id，
            # 两者互相引用，所以必须先把 id 拿在手里（`rollback` 同一手法）。
            log_id = next_router_log_id(store)
            row = append_domain_profile(
                token,
                session,
                router_log_id=log_id,
                familiar=familiar,
                interested=interested,
                reason=reason,
            )
            append_router_log(
                token,
                session,
                log_id=log_id,
                source=ProposalSource.USER_UI,
                status=RouterLogStatus.APPLIED,
                trigger=TriggerType.R6,
                # R6 没有 IntentAction（它不是对某个变量的写入），
                # 但**必须有 target**：I2 要求"已应用的日志指向一个真实存在的
                # 全局地址" —— 否则这条日志就是"有东西变了但不知道是什么"。
                action=None,
                target=D_TARGET_ROOT,
                basis=Basis.STATED,  # 用户自己说的
                old_value=None,
                new_value={
                    "profile_id": row.profile_id,
                    "version_no": row.version_no,
                    "familiar": row.familiar,
                    "interested": row.interested,
                },
                intent_ref=None,
                proposal_ref=None,
                user_confirmed=True,
                reasoning=(
                    f"领域勾选清单第 {row.version_no} 版："
                    f"熟悉 {len(row.familiar)} 个专题，"
                    f"陌生区里感兴趣 {len(row.interested)} 个。"
                    + (f"原因：{reason.strip()}" if reason else "")
                ),
            )

        if invariants_enabled(session):
            assert_global_invariants(session)
        return row.profile_id

    def record_benchmark_response(
        self,
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
        hesitation_sec: float | None = None,
        switch_count: int | None = None,
        help_request_count: int | None = None,
        hint_used_count: int | None = None,
        judge_model: str | None = None,
        judge_note: str | None = None,
        judged_hits: list | None = None,
        scoring_method: str | None = None,
    ) -> str:
        """落一道题的作答与单题分，返回 `response_id`。

        逐题落库，因此一次中断的运行也能留下"答到哪一题"这个事实。
        """
        return append_benchmark_response(
            self._token,
            self._session,
            run_id=run_id,
            item_id=item_id,
            question_no=question_no,
            kind=kind,
            judged_by=judged_by,
            raw_answer=raw_answer,
            item_score=item_score,
            difficulty=difficulty,
            weight=weight,
            elapsed_sec=elapsed_sec,
            hesitation_sec=hesitation_sec,
            switch_count=switch_count,
            help_request_count=help_request_count,
            hint_used_count=hint_used_count,
            judge_model=judge_model,
            judge_note=judge_note,
            judged_hits=judged_hits,
            scoring_method=scoring_method,
        ).response_id

    def apply_benchmark_score(
        self,
        *,
        response_id: str,
        item_score: float | None,
        scoring_method: str,
        judged_by: JudgedBy,
        judge_model: str | None = None,
        judge_note: str | None = None,
        judged_hits: list | None = None,
    ) -> None:
        """改写一条作答的计分结果（P9）。**计分只走这一个入口。**

        ## 为什么计分必须经路由器

        因为它是**对全局表的一次写入**，而本项目的铁律是"全局写只经路由器"
        （I2 要求每次写入有日志与令牌）。计分看起来像"算一个数"，
        但它算完要落库 —— 落库那一步与别的写入没有区别。

        ## 为什么算法本身不在这里

        计分规则是纯函数（`app.benchmark.scoring`），住在 benchmark 域。
        路由器只负责**把它算出来的东西安全地写下去**。
        把规则搬进路由器会让它认识题库与判官，而它现在的职责边界
        「裁定 + 写」是它能被审计的全部原因。
        """
        set_benchmark_response_score(
            self._token,
            self._session,
            response_id,
            item_score=item_score,
            scoring_method=scoring_method,
            judged_by=judged_by,
            judge_model=judge_model,
            judge_note=judge_note,
            judged_hits=judged_hits,
        )

    def record_anchor_baseline(self, run_id: str, *, baseline: dict) -> None:
        """把这次运行的锚题读数记成基线（P12）。

        ★ 计分与"记基线"是**两件事**，所以是两个入口，不是一个：
        计分改写一条作答的分，记基线定下一把尺子的刻度原点。
        把它们合成一个方法，"这次计分了"会被读成"基线也更新了"。

        ★ **只写还没有基线的锚题**（见 `writes.record_anchor_baseline`）：
        基线一漂，全部历史分数的可比性同时失效 ——
        而它漂起来没有任何症状，只是历史分数悄悄变得不可比。
        """
        record_anchor_baseline(self._token, self._session, run_id, baseline=baseline)

    def finish_benchmark_run(
        self,
        *,
        run_id: str,
        method_score: float | None,
        channel_score: float | None,
        anchor_baseline: dict,
        confidence: BaselineConfidence | None,
        completeness: Completeness,
        boundary: str,
    ) -> None:
        """补齐分数与置信。重复调用会抛错（见 `writes.finalize_benchmark_run`）。"""
        finalize_benchmark_run(
            self._token,
            self._session,
            run_id=run_id,
            method_score=method_score,
            channel_score=channel_score,
            anchor_baseline=anchor_baseline,
            confidence=confidence,
            completeness=completeness,
            boundary=boundary,
        )

    # ------------------------------------------------------------------ #
    # 写入：回滚（R5 —— 不产生 Intent，因此不经过判定表）
    # ------------------------------------------------------------------ #

    def rollback(
        self,
        variable_id: str,
        channel: EvidenceChannel,
        to_version_id: str,
        *,
        reason: str,
    ) -> RollbackResult:
        """把某 `(变量, 通道)` 的当前版本指回一个旧版本。**生成新版本，不删历史。**

        **为什么不走判定表**：`UpdateIntent` 携带的是「一个新的取值」，
        而回滚携带的是「一个已存在版本的引用」—— 没有新取值，就没有
        `(action, target, basis)` 三元组可供判定。回滚因此被定为 R5，
        是一条独立路径（`precheck.evaluate` 收到 R5 会直接抛错，防止误用）。

        **为什么不需要 §7.3 的 ≥2 条证据门槛**：被回滚到的那个版本当初是通过了
        门槛才落到库里的。回滚只是把指针移回一个已被批准的既有状态，
        不引入任何新证据 —— 对它再设一次门槛，等于用「后来变严格了」追溯否决过去的决定。
        """
        session, token = self._session, self._token
        store = DbSequenceStore(session)

        if not reason or not reason.strip():
            raise ValidationRejected("回滚必须写明原因（M0 §9.2 / 契约 RollbackRequest）")

        # 通道必须在这里归一成枚举。**不能省**：下游用的是 `is not channel` 身份比较，
        # 而传进一个字符串 `"measured"` 时它既不相等也不会报错，
        # 直到某处调用 `.value` 才炸成 AttributeError（P5 验收套件抓到过）。
        # 同类问题在 `reads.current_baseline_version` 也成立，故归一放在入口一次。
        channel = _as_channel(channel)

        target = get_baseline_version(session, to_version_id)
        if target is None:
            raise NotFound(f"目标版本不存在：{to_version_id}")
        if target.variable_id != variable_id or target.channel is not channel:
            raise ValidationRejected(
                f"版本 {to_version_id} 属于 {target.variable_id}/{target.channel.value}，"
                f"不能用于回滚 {variable_id}/{channel.value}"
            )

        previous = current_baseline_version(session, variable_id, channel)

        with session.begin_nested():
            log_id = next_router_log_id(store)
            row = rollback_baseline_version(
                token,
                session,
                variable_id=variable_id,
                channel=channel,
                to_version_id=to_version_id,
                router_log_id=log_id,
            )
            append_router_log(
                token,
                session,
                log_id=log_id,
                source=ProposalSource.USER_UI,
                status=RouterLogStatus.APPLIED,
                trigger=TriggerType.R5,
                # 回滚不是 IntentAction 之一，也不产生 intent：两个引用都为空。
                action=None,
                target=target_for_variable(variable_id),
                basis=None,
                old_value=m1_policy.snapshot(previous),
                new_value=m1_policy.snapshot(row),
                intent_ref=None,
                proposal_ref=None,
                user_confirmed=True,
                reasoning=(
                    f"回滚 {variable_id}/{channel.value}："
                    f"{previous.version_id if previous is not None else '(无旧版本)'}"
                    f" → 复制自 {to_version_id} 生成 {row.version_id}。原因：{reason.strip()}"
                ),
            )

        if invariants_enabled(session):
            assert_global_invariants(session)

        return RollbackResult(
            variable_id=variable_id,
            channel=channel,
            to_version_id=to_version_id,
            new_version_id=row.version_id,
            replaced_version_id=previous.version_id if previous is not None else None,
            router_log_id=log_id,
            reason=reason.strip(),
        )

    # ------------------------------------------------------------------ #
    # 写入：提交 / 裁决
    # ------------------------------------------------------------------ #

    def submit(self, request: SubmitRequest) -> SubmitResult:
        session, token = self._session, self._token
        store = DbSequenceStore(session)

        self._reject_duplicate_intent_ids(request.intents)
        self._reject_taken_intent_ids(request.intents)

        pairs = [
            (
                intent,
                precheck_module.evaluate(session, request.trigger_type, intent),
            )
            for intent in request.intents
        ]

        proposal_id = next_proposal_id(store)
        create_proposal(
            token,
            session,
            proposal_id=proposal_id,
            source=source_for_trigger(request.trigger_type),
            trigger_type=request.trigger_type,
            status=ProposalStatus.PROPOSED,
            session_ref=request.session_ref,
            source_ref=request.source_ref,
            note=request.note,
        )
        for intent, pre in pairs:
            add_intent(
                token,
                session,
                intent_id=intent.intent_id,
                proposal_id=proposal_id,
                action=intent.action,
                target=intent.target,
                basis=intent.basis,
                reasoning=intent.reasoning,
                confidence=intent.confidence,
                value=intent.value,
                priority=intent.priority,
                conflict_note=intent.conflict_note,
                precheck=pre.verdict,
                precheck_reason=pre.reason,
            )

        batch = _Batch(
            trigger=request.trigger_type,
            source=source_for_trigger(request.trigger_type),
            proposal_id=proposal_id,
            source_ref=request.source_ref,
        )

        # ⑤/⑥：本轮**会被立刻写入**的只有 auto_apply 且无硬前置条件的那部分。
        auto_pairs = [
            (intent, pre)
            for intent, pre in pairs
            if pre.verdict is Precheck.AUTO_APPLY and pre.can_apply
        ]
        written: dict[str, IntentResult] = {}
        conflicts: list[ConflictReport] = []
        if auto_pairs:
            batch_results, conflicts = self._write_batch(batch, auto_pairs)
            written = {result.intent_id: result for result in batch_results}

        results: list[IntentResult] = []
        for intent, pre in pairs:
            if intent.intent_id in written:
                results.append(written[intent.intent_id])
            elif pre.verdict is Precheck.MUST_REJECT:
                results.append(
                    self._reject_intent(batch, intent, pre, pre.reason)
                )
            else:
                # 等待用户确认；**保持 pending**，不预设结论。
                results.append(
                    IntentResult(
                        intent_id=intent.intent_id,
                        verdict=pre.verdict,
                        reason=pre.reason,
                        status=IntentStatus.PENDING,
                        advisories=pre.advisories,
                        blockers=pre.blockers,
                    )
                )

        status = self._derive_status(results)
        set_proposal_status(token, session, proposal_id, status)

        return SubmitResult(
            proposal_id=proposal_id,
            status=status,
            trigger_type=request.trigger_type,
            results=results,
            # **报告的就是真正被裁定的那些**（P5.2 修正）。
            # 原先这里对**全部** intent 重算一遍冲突，包括已经被判定表拒掉的那些 ——
            # 于是报告会写「measured 胜过了 experienced」，而那条 experienced
            # 其实是被判定表拒的、根本没进冲突裁定。那又是一次
            # 「用一个不成立的理由解释系统的行为」。
            # 被拒的 intent 的原因已经写在它自己的 `reason` 里。
            conflicts=conflicts,
        )

    def resolve(
        self, proposal_id: str, decisions: Sequence[Decision]
    ) -> SubmitResult:
        session, token = self._session, self._token

        proposal = get_proposal(session, proposal_id)
        if proposal is None:
            raise NotFound(f"提案不存在：{proposal_id}")
        if proposal.status is not ProposalStatus.PENDING_CONFIRMATION:
            raise VersionConflict(
                f"提案 {proposal_id} 的状态是 {proposal.status.value}，不可裁决"
                "（只有 pending_confirmation 可裁决）"
            )

        rows = list_intents(session, proposal_id)
        known = {row.intent_id for row in rows}
        decision_map: dict[str, Decision] = {}
        for decision in decisions:
            if decision.intent_id in decision_map:
                raise ValidationRejected(
                    f"同一 intent 被裁决两次：{decision.intent_id}"
                )
            decision_map[decision.intent_id] = decision
        unknown = sorted(set(decision_map) - known)
        if unknown:
            raise ValidationRejected(
                f"裁决引用了不属于提案 {proposal_id} 的 intent：{unknown}"
            )

        batch = _Batch(
            trigger=proposal.trigger_type,
            source=proposal.source,
            proposal_id=proposal_id,
            source_ref=proposal.source_ref,
        )

        outcomes: dict[str, IntentResult] = {}
        pairs: list[tuple[UpdateIntent, PrecheckResult]] = []

        for row in rows:
            if row.status is not IntentStatus.PENDING:
                outcomes[row.intent_id] = _result_from_row(row)
                continue
            decision = decision_map.get(row.intent_id)
            if decision is None:
                # 未裁决的保持 pending —— 提案可以分几次裁决完。
                outcomes[row.intent_id] = _result_from_row(row)
                continue

            intent = _intent_from_row(row)
            # **重新判定**：全局状态可能在提交与裁决之间变了（别人写了一条基线、
            # 某条偏好被归档）。沿用提交时的结论会让提案按过期事实执行。
            pre = precheck_module.evaluate(session, proposal.trigger_type, intent)

            if not decision.approved:
                outcomes[row.intent_id] = self._reject_intent(
                    batch, intent, pre, decision.reason or "用户否决"
                )
                continue
            if not pre.can_apply:
                reasons = list(pre.blockers) or [pre.reason]
                outcomes[row.intent_id] = self._reject_intent(
                    batch,
                    intent,
                    pre,
                    "用户已同意，但存在无法绕过的前置条件："
                    + "；".join(reasons),
                    # ★ 用户**确实同意了** —— 是系统挡住了它。
                    user_confirmed=True,
                )
                continue
            pairs.append((intent, pre))

        conflicts: list[ConflictReport] = []
        if pairs:
            batch_results, conflicts = self._write_batch(batch, pairs)
            for result in batch_results:
                outcomes[result.intent_id] = result

        ordered = [outcomes[row.intent_id] for row in rows]
        status = self._derive_status(ordered)
        set_proposal_status(token, session, proposal_id, status)

        return SubmitResult(
            proposal_id=proposal_id,
            status=status,
            trigger_type=proposal.trigger_type,
            results=ordered,
            # 同 `submit`：只报告真正经过裁定的那些（P5.2 修正）。
            conflicts=conflicts,
        )

    # ------------------------------------------------------------------ #
    # 内部：批次执行
    # ------------------------------------------------------------------ #

    def _write_batch(
        self,
        batch: _Batch,
        pairs: list[tuple[UpdateIntent, PrecheckResult]],
    ) -> tuple[list[IntentResult], list[ConflictReport]]:
        """对本批**将要写入**的 intent 做冲突裁定，然后逐条执行。

        裁定只在本批内进行 —— 这正是「裁定发生在写入时刻」的落点（见模块文档）。

        冲突分析只看**真实写入槽位**（`conflict.write_slot`），因此
        「一次测绘写多个通道」「一次沉淀多条偏好」都不会被误判（P5.2 修正）。
        同槽位而档位并列时**整组拒绝**：那一批本身说不通，
        按 `intent_id` 字典序挑一个赢是无法向用户解释的。
        """
        if not pairs:
            return [], []
        conflicts: list[ConflictReport] = analyze_conflict(
            [intent for intent, _ in pairs]
        )
        skipped = {
            intent_id: report
            for report in conflicts
            for intent_id in losers_of(report)
        }
        rejected = {
            intent_id: report
            for report in conflicts
            for intent_id in rejected_of(report)
        }

        results: list[IntentResult] = []
        for intent, pre in pairs:
            if intent.intent_id in rejected:
                results.append(
                    self._reject_intent(
                        batch, intent, pre, rejected[intent.intent_id].note
                    )
                )
            elif intent.intent_id in skipped:
                results.append(
                    self._skip_intent(batch, intent, pre, skipped[intent.intent_id])
                )
            else:
                results.append(self._apply_intent(batch, intent, pre))

        # 第四重保障（M0 §4.1④）落在这里：`_write_batch` 是**全部**全局写入的唯一
        # 汇聚点（`submit` 与 `resolve` 都经过它），因此自检放在这一处就等于
        # 「任何写入在离开路由层之前都被核对过」，没有第二条路可以绕开。
        self._verify_after_write(results)
        return results, conflicts

    def _verify_after_write(self, results: Sequence[IntentResult]) -> None:
        """写入后自检。**没有真正写入时不跑** —— 空批与全被拒的批不该付这个代价。

        为什么自检在 Router 里，而不在 HTTP 层的事务边界上：

        P5 起初把 `assert_global_invariants` 放在 `app.api.deps.get_session` 的
        `yield` 之后（「提交之前的那一瞬间」），结果发现**那一瞬间在 HTTP 上并不存在**：
        这个 FastAPI/Starlette 版本在响应**已经发出之后**才关闭依赖的退出栈，
        于是自检抛出的异常只能变成
        `RuntimeError: Caught handled exception, but response already started.`
        —— 检查明明跑了，客户端却收到 201。

        两个结论，第二个比第一个重要：

        1. **靠框架的回调时机来承载治理保证是不可靠的**：它随时会随框架版本变化，
           而失败方式是静默的（响应已经发出）。
        2. 写入的唯一入口本来就是 Router，所以自检的正确位置就是**唯一入口本身**，
           与框架无关。脚本、测试、将来的后台 worker 走同一条路，也就自动获得同样的保护。
        """
        if not any(r.status is IntentStatus.APPLIED for r in results):
            return
        if invariants_enabled(self._session):
            assert_global_invariants(self._session)

    def _apply_intent(
        self, batch: _Batch, intent: UpdateIntent, pre: PrecheckResult
    ) -> IntentResult:
        session, token = self._session, self._token
        store = DbSequenceStore(session)

        resolved = resolve_target(intent.target)
        if resolved is None:  # pragma: no cover - precheck 已挡住
            return self._reject_intent(
                batch, intent, pre, precheck_module.REASON_UNREGISTERED_TARGET
            )
        value, error = precheck_module.validate_value(resolved, intent)
        if error is not None:  # pragma: no cover - precheck 已挡住
            return self._reject_intent(batch, intent, pre, error)

        try:
            with session.begin_nested():
                # 先分配 log_id，再写数据行 —— 数据行因此能携带 router_log_id。
                log_id = next_router_log_id(store)
                outcome = _policy_for(intent).apply(
                    PolicyContext(
                        session=session,
                        token=token,
                        trigger=batch.trigger,
                        intent=intent,
                        resolved=resolved,
                        value=value,
                        source_ref=batch.source_ref,
                        log_id=log_id,
                        # 能走到这里的 intent，其确认语义都已满足：
                        # auto_apply（R1/R2/R3/R4c 的自身语义）或用户已裁决同意。
                        user_confirmed=True,
                    )
                )
                append_router_log(
                    token,
                    session,
                    log_id=log_id,
                    source=batch.source,
                    status=RouterLogStatus.APPLIED,
                    trigger=batch.trigger,
                    action=intent.action,
                    target=intent.target,
                    basis=intent.basis,
                    old_value=outcome.old_value,
                    new_value=outcome.new_value,
                    intent_ref=intent.intent_id,
                    proposal_ref=batch.proposal_id,
                    user_confirmed=True,
                    reasoning=f"{outcome.reason}｜intent 理由：{intent.reasoning}",
                )
        except (PolicyRefused, PolicyError) as exc:
            # SAVEPOINT 已回滚：本条的写入与日志一起消失，其它 intent 不受影响。
            return self._reject_intent(batch, intent, pre, str(exc))

        set_intent_status(token, session, intent.intent_id, IntentStatus.APPLIED)
        return IntentResult(
            intent_id=intent.intent_id,
            verdict=pre.verdict,
            reason=outcome.reason,
            status=IntentStatus.APPLIED,
            advisories=pre.advisories,
            blockers=pre.blockers,
            router_log_id=log_id,
        )

    def _reject_intent(
        self,
        batch: _Batch,
        intent: UpdateIntent,
        pre: PrecheckResult,
        reason: str,
        *,
        user_confirmed: bool = False,
    ) -> IntentResult:
        """记一条 `rejected` 日志并如实标注**用户当时同意没同意**。

        ★ `user_confirmed` 是显式参数，不是恒为 `False` 的常量。
        同一个"被拒"有两种成因，而它们在审计上完全不同：

        - **用户否决**（`update` 里 `not decision.approved`）→ `False`。用户说不要。
        - **用户同意、但前置条件绕不过**（`update` 里 `not pre.can_apply`）→ `True`。
          用户点了"同意"，是系统挡住了它。

        把它们都写成 `False` 会让日志说"用户否决了"，
        而同一行的 `reasoning` 里却写着"用户已同意，但…" ——
        **一行日志自相矛盾**，而半年后查"他到底同意过没有"时，
        唯一能信的就是 `user_confirmed` 这个布尔字段。

        其余调用点（提交时就被判定表挡下、冲突落选、执行抛错）
        都发生在用户有机会表态之前或之外，所以默认 `False` 是对的。
        """
        session, token = self._session, self._token
        log = append_router_log(
            token,
            session,
            source=batch.source,
            status=RouterLogStatus.REJECTED,
            trigger=batch.trigger,
            action=intent.action,
            target=intent.target,
            basis=intent.basis,
            old_value=None,
            new_value=intent.value,
            intent_ref=intent.intent_id,
            proposal_ref=batch.proposal_id,
            user_confirmed=user_confirmed,
            reasoning=f"拒绝：{reason}｜intent 理由：{intent.reasoning}",
        )
        set_intent_status(token, session, intent.intent_id, IntentStatus.REJECTED)
        return IntentResult(
            intent_id=intent.intent_id,
            verdict=pre.verdict,
            reason=reason,
            status=IntentStatus.REJECTED,
            advisories=pre.advisories,
            blockers=pre.blockers,
            router_log_id=log.log_id,
        )

    def _skip_intent(
        self,
        batch: _Batch,
        intent: UpdateIntent,
        pre: PrecheckResult,
        report: ConflictReport,
    ) -> IntentResult:
        session, token = self._session, self._token
        reason = (
            f"槽位 {report.slot} 上有更高档位的写入"
            f"（{report.order_name}：{report.winner_intent_id} 胜出）。"
            f"本条标记为 skipped 而非丢弃 —— 可重新提交为新提案（M0 §7.4）。"
        )
        log = append_router_log(
            token,
            session,
            source=batch.source,
            status=RouterLogStatus.OVERRIDDEN,
            trigger=batch.trigger,
            action=intent.action,
            target=intent.target,
            basis=intent.basis,
            old_value=None,
            new_value=intent.value,
            intent_ref=intent.intent_id,
            proposal_ref=batch.proposal_id,
            user_confirmed=False,
            reasoning=reason,
        )
        set_intent_status(token, session, intent.intent_id, IntentStatus.SKIPPED)
        return IntentResult(
            intent_id=intent.intent_id,
            verdict=pre.verdict,
            reason=reason,
            status=IntentStatus.SKIPPED,
            advisories=pre.advisories,
            blockers=pre.blockers,
            router_log_id=log.log_id,
        )

    # ------------------------------------------------------------------ #
    # 内部：校验与状态推导
    # ------------------------------------------------------------------ #

    @staticmethod
    def _reject_duplicate_intent_ids(intents: Sequence[UpdateIntent]) -> None:
        seen: set[str] = set()
        duplicates: list[str] = []
        for intent in intents:
            if intent.intent_id in seen:
                duplicates.append(intent.intent_id)
            seen.add(intent.intent_id)
        if duplicates:
            raise ValidationRejected(
                f"同一次提交内 intent_id 重复：{sorted(set(duplicates))}"
            )

    def _reject_taken_intent_ids(self, intents: Sequence[UpdateIntent]) -> None:
        taken = [
            intent.intent_id
            for intent in intents
            if self._session.get(UpdateIntentRow, intent.intent_id) is not None
        ]
        if taken:
            raise ValidationRejected(
                f"intent_id 已被占用：{sorted(taken)}。"
                "intent_id 由调用方提供且是数据库主键；Agent 工具层（P6）"
                "应使用 `next_intent_id()` 之类的生成器保证全局唯一。"
            )

    @staticmethod
    def _derive_status(results: Sequence[IntentResult]) -> ProposalStatus:
        pending = sum(1 for r in results if r.status is IntentStatus.PENDING)
        applied = sum(1 for r in results if r.status is IntentStatus.APPLIED)
        if pending:
            # 还有待裁决 → 状态机停在 pending_confirmation，
            # `partially_applied` 只作为**终态**出现（M0 §5.1）。
            return ProposalStatus.PENDING_CONFIRMATION
        if applied == len(results):
            return ProposalStatus.APPLIED
        if applied:
            return ProposalStatus.PARTIALLY_APPLIED
        return ProposalStatus.REJECTED
