"""`/api/benchmark` —— 领域勾选清单 + 基准运行的生命周期。

## 这一组端点回答用户实际会问的几个问题

| 用户的动作 | 端点 |
|---|---|
| 「我熟悉哪些专题？」（第一次进入） | `GET /domains`（含当前勾选） |
| 提交两次勾选 | `POST /domains` |
| 「我想看看清单变过没有」 | `GET /domains/history` |
| 「开始一次测试（简短 / 全面）」 | `POST /runs` |
| 「我要保存退出」 | `POST /runs/{id}/status` (`target=interrupted`) |
| 「继续上次」 | `POST /runs/{id}/status` (`target=in_progress`) |
| 「重新开始」 | `POST /runs/{id}/restart` |
| 「进入基准页该看到什么」 | `GET /state` |

## 为什么 `/state` 存在

「还没勾清单、有一次中断的运行、本季度该重做」这三件事，如果让前端各查一次，
就会出现**只有特定顺序下才复现**的错误（清单还没勾却进了测试）。
合成一个响应之后，那个顺序不存在了 —— 这是 `BenchmarkStateRead` 的说明里
写的那件事。

## 这一层不做任何裁定

它不判断"该不该让你开始测试"、也不判断"这份勾选合不合理"。
裁定属于 Router（并已在那里做完），这里只把结果翻译成 HTTP。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.discuss import get_llm_client
from app.api.envelope import ItemsResponse, ok
from app.api.deps import get_router, get_session, get_settings
from app.config import Settings
from app.benchmark.equating import EquatingResult, equate
from app.benchmark.items import ANSWER_SEP, BenchItem, load_bank
from app.benchmark.judge import SYSTEM_PROMPT, judge_key_points, verdict_note
from app.benchmark.scoring import (
    IMPLICIT_PARAMETERS,
    ChannelAggregate,
    MethodAggregate,
    ObservationBand,
    ObservationTable,
    ScoredItem,
    aggregate_method,
    compose_channel,
    counts_toward_reading,
    decide_confidence,
    score_exact,
    score_exact_blank,
    score_key_points,
    score_observation,
    score_self_report,
)
from app.benchmark.sampling import (
    History,
    SamplingPlan,
    SamplingRefused,
    plan_sampling,
    summarize_history,
)
from app.core.errors import NotFound, ValidationRejected
from app.global_domain import reads
from app.global_domain.enums import (
    BaselineConfidence,
    BenchmarkForm,
    BenchmarkRunStatus,
    Completeness,
    EvidenceChannel,
    JudgedBy,
    ResponseQuestionKind,
)
from app.global_domain.schemas import (
    BenchmarkFinishWrite,
    BenchmarkPlanItemRead,
    BenchmarkPlanRead,
    BenchmarkResponseWrite,
    BenchmarkRunRead,
    BenchmarkRunStart,
    BenchmarkRunStatusWrite,
    BenchmarkScoreRead,
    BenchmarkStateRead,
    DomainChecklistRead,
    DomainChecklistWrite,
    DomainGroupRead,
    EquatingAnchorRead,
    EquatingRead,
    DomainProfileRead,
    DomainTopicRead,
)
from app.router import Router

router = APIRouter(prefix="/api/benchmark", tags=["benchmark"])


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #


def _run_read(session: Session, row) -> BenchmarkRunRead:
    """ORM 行 → 读模型，并补上"已经答了几题"。

    这个数不在表上，因为它是**派生值**（数一下 `benchmark_response` 就有）。
    存成一列意味着每次落一道题都要同步更新它 —— 而两者一旦不同步，
    "我做到哪了"就会给出一个和实际作答不一致的答案。
    """
    out = BenchmarkRunRead.model_validate(row)
    return out.model_copy(
        update={"response_count": len(reads.list_benchmark_responses(session, row.run_id))}
    )


def _require_run(session: Session, run_id: str):
    row = reads.get_benchmark_run(session, run_id)
    if row is None:
        raise NotFound(f"基准运行不存在：{run_id}")
    return row


def _as_http(exc: ValueError) -> HTTPException:
    """`writes` 里的规则违反 → 422。

    不在 `writes` 里直接抛 HTTP 错误：那一层是领域层，它不该知道 HTTP 是什么。
    转换发生在这里（边界上），并且**只有这一处** —— 散在多处会让
    "同一类错误有时 422、有时 500"变成一个靠运气的行为。
    """
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
    )


# --------------------------------------------------------------------------- #
# 抽题的输入从哪来（纯函数在 app.benchmark.sampling，这里只做取数与翻译）
# --------------------------------------------------------------------------- #


def _load_bank(settings: Settings) -> list[BenchItem]:
    """读题库目录。**读不到就 ValueError**，不返回空列表。

    ★ 空列表与"题库坏了"必须分开：前者会让抽题器报"没有这个格子的题"
    （一句指错方向的理由），后者才是真的原因。
    题池是渐进补的，所以"目录不存在"是一个会持续存在一段时间的真实状态 ——
    它值得一句准确的话。
    """
    root = Path(settings.items_dir)
    if not root.is_dir():
        raise ValueError(
            f"题库目录不存在：{root}（MOUXUE_ITEMS_DIR 可改）。"
            f"抽题机制本身没问题，是题库还没放进来"
        )
    items = load_bank(root)
    if not items:
        raise ValueError(f"题库目录是空的：{root}")
    return items


def _sampling_history(session: Session) -> History:
    """把历次运行压成抽题器要的那份 `History`。

    ## 三个口径，都是刻意的

    1. **只算真的答过的题**（有 `benchmark_response` 才算），
       不算"这次运行包含它"。中途作废的运行里他可能一道都没做，
       而那不该让一道题被打上"做过"。
    2. **按 `started_at` 从旧到新编号**，序号就是"第几次运行"。
       用序号而不是时间戳：时间戳会让抽题结果依赖时钟，
       而"同一种子抽出同一套题"是 §6.3 的明文要求。
    3. **作废的运行也算历史**。他做过那些题 —— 那是关于他的一条事实，
       不因为他后来点了"重新开始"而消失。
    """
    runs = sorted(reads.list_benchmark_runs(session), key=lambda r: r.started_at)
    timeline: list[tuple[list[str], list[str]]] = []
    for run in runs:
        answered = sorted(
            {
                response.item_id
                for response in reads.list_benchmark_responses(session, run.run_id)
            }
        )
        topics = [
            entry.get("topic_id")
            for entry in (run.topic_snapshot or [])
            if isinstance(entry, dict) and entry.get("topic_id")
        ]
        timeline.append((answered, topics))
    return summarize_history(timeline)


def _plan_for(
    session: Session,
    settings: Settings,
    *,
    form: BenchmarkForm,
    variable_id: str,
    channel: EvidenceChannel,
    method_id: str,
    seed: int,
) -> SamplingPlan:
    """取数 → 抽题 → 把拒绝翻译成 422。"""
    profile = reads.current_domain_profile(session)
    try:
        return plan_sampling(
            _load_bank(settings),
            variable_id=variable_id,
            channel=str(channel),
            method_id=method_id,
            form=str(form),
            familiar=profile.familiar if profile else [],
            interested=profile.interested if profile else [],
            history=_sampling_history(session),
            seed=seed,
        )
    except SamplingRefused as exc:
        # `SamplingRefused` 是 `ValueError` 的子类，但**题库侧**的问题
        # （目录不存在、目录里一道题都读不出来）抛的是普通 `ValueError`。
        # 两者都该是 422 加一句能修的话：它们是"题库还没准备好"，
        # 不是服务端故障 —— 500 会让人去查日志，而日志里只有同一句话。
        raise _as_http(exc) from exc
    except ValueError as exc:
        raise _as_http(exc) from exc


# --------------------------------------------------------------------------- #
# 进入基准页该看到什么
# --------------------------------------------------------------------------- #


@router.get("/state", response_model=BenchmarkStateRead)
def benchmark_state(session: Session = Depends(get_session)) -> BenchmarkStateRead:
    """基准页的入口状态。三件事一次说清，见模块文档。"""
    profile = reads.current_domain_profile(session)
    resumable = reads.resumable_benchmark_run(session)
    runs = reads.list_benchmark_runs(session)
    in_progress = next(
        (r for r in runs if r.status is BenchmarkRunStatus.IN_PROGRESS), None
    )
    return BenchmarkStateRead(
        has_checklist=profile is not None,
        checklist_version=profile.version_no if profile else None,
        familiar_count=len(profile.familiar) if profile else 0,
        interested_count=len(profile.interested) if profile else 0,
        resumable_run=_run_read(session, resumable) if resumable else None,
        in_progress_run=_run_read(session, in_progress) if in_progress else None,
        run_count=len(runs),
    )


# --------------------------------------------------------------------------- #
# 领域勾选清单
# --------------------------------------------------------------------------- #


@router.get("/domains", response_model=DomainChecklistRead)
def domain_checklist(session: Session = Depends(get_session)) -> DomainChecklistRead:
    """整份清单 + 当前勾选状态。

    `familiar` / `interested` 从**档案**来，`label` / `covers` 从**目录**来。
    分开取而不是 join：目录是版本发布的静态数据，档案是用户数据，
    它们的生命周期不同（目录会随版本变，档案不会）。
    """
    topics = reads.list_domain_topics(session)
    profile = reads.current_domain_profile(session)
    familiar = set(profile.familiar) if profile else set()
    interested = set(profile.interested) if profile else set()

    groups: list[DomainGroupRead] = []
    for topic in topics:
        if not groups or groups[-1].group_id != topic.group_id:
            groups.append(
                DomainGroupRead(
                    group_id=topic.group_id,
                    group_label=topic.group_label,
                    topics=[],
                )
            )
        groups[-1].topics.append(
            DomainTopicRead(
                topic_id=topic.topic_id,
                group_id=topic.group_id,
                group_label=topic.group_label,
                label=topic.label,
                covers=topic.covers,
                familiar=topic.topic_id in familiar,
                interested=topic.topic_id in interested,
            )
        )

    total = len(topics)
    return DomainChecklistRead(
        profile_id=profile.profile_id if profile else None,
        version_no=profile.version_no if profile else None,
        familiar_count=len(familiar),
        interested_count=len(interested),
        unfamiliar_count=total - len(familiar),
        updated_at=profile.created_at if profile else None,
        groups=groups,
    )


@router.post("/domains", response_model=DomainProfileRead, status_code=201)
def set_domain_checklist(
    body: DomainChecklistWrite,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> DomainProfileRead:
    """提交两次勾选的结果，写入一版新档案（**只追加，不改写旧版**）。

    ★ 这是 **R6**：它不过 `PRECHECK_TABLE` —— 判定表回答"这条关于用户的断言
    准不准入"，而这份清单是**测量的参照**，不是断言。
    但**照记 RouterLog**（口径同 `m2_preference`）：
    记账与裁定是两件事。
    """
    try:
        profile_id = router_.set_domain_profile(
            familiar=body.familiar,
            interested=body.interested,
            reason=body.reason,
        )
    except ValueError as exc:
        raise _as_http(exc) from exc

    row = next(
        p for p in reads.domain_profile_history(session) if p.profile_id == profile_id
    )
    return DomainProfileRead.model_validate(row)


@router.get("/domains/history", response_model=ItemsResponse[DomainProfileRead])
def domain_checklist_history(
    session: Session = Depends(get_session),
) -> dict[str, list[DomainProfileRead]]:
    """清单的全部版本，最新在前 —— 「我对某专题熟不熟是怎么变的」。"""
    return ok([
        DomainProfileRead.model_validate(row)
        for row in reads.domain_profile_history(session)
    ])


# --------------------------------------------------------------------------- #
# 运行生命周期
# --------------------------------------------------------------------------- #


@router.get("/runs", response_model=ItemsResponse[BenchmarkRunRead])
def list_runs(session: Session = Depends(get_session)) -> dict[str, list[BenchmarkRunRead]]:
    """全部运行，最近的在前。**包含已作废的** —— 那是"我放弃过几次"的记录。"""
    return ok([_run_read(session, row) for row in reads.list_benchmark_runs(session)])


@router.get("/runs/{run_id}", response_model=BenchmarkRunRead)
def get_run(run_id: str, session: Session = Depends(get_session)) -> BenchmarkRunRead:
    return _run_read(session, _require_run(session, run_id))


@router.post("/runs", response_model=BenchmarkRunRead, status_code=201)
def start_run(
    body: BenchmarkRunStart,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
    settings: Settings = Depends(get_settings),
) -> BenchmarkRunRead:
    """开一次运行。`form` 只能是 `quick` / `full`（简短版 / 全面完整版）。

    ## 不传 `item_ids` = 请服务端抽题（P8）

    抽题规则**不住在这里**，它住在 `app.benchmark.sampling`（纯函数）。
    这里只做两件事：把数据库里的输入（题库文件 / 清单 / 历史）取出来喂给它，
    再把结果记进 `benchmark_run`。

    ★ 为什么必须由服务端委派，而不是"让前端自己算"：
    抽题规则散成两份实现时，**它们不会一直一致** —— 不一致的表现是
    "界面抽的题与记录的 `topic_snapshot` 对不上"，
    而这一步不会报错，只会让之后的领域轮换算错。

    ★ 抽不出题时是 **422**，不是"少抽两道凑一凑"：
    降级会产出一次看起来正常、但与别的运行**不可比**的运行。
    """
    item_ids = list(body.item_ids)
    topic_snapshot = list(body.topic_snapshot)
    note = body.note
    if not item_ids:
        plan = _plan_for(
            session,
            settings,
            form=body.form,
            variable_id=body.variable_id,
            channel=body.channel,
            method_id=body.method_id,
            seed=body.seed,
        )
        item_ids = plan.item_ids
        # 专题快照以**抽题实际用到的**为准 —— 这正是它要留档的东西（§7.3 ①）。
        topic_snapshot = topic_snapshot or list(plan.topics)
        note = note or plan.describe()
    run_id = router_.start_benchmark_run(
        form=body.form,
        variable_id=body.variable_id,
        channel=body.channel,
        method_id=body.method_id,
        item_ids=item_ids,
        seed=body.seed,
        topic_snapshot=topic_snapshot,
        completeness=body.completeness,
        note=note,
    )
    return _run_read(session, _require_run(session, run_id))


@router.get("/plan", response_model=BenchmarkPlanRead)
def preview_plan(
    form: BenchmarkForm = Query(...),
    variable_id: str = Query(min_length=1),
    channel: EvidenceChannel = Query(...),
    method_id: str = Query(min_length=1),
    seed: int = Query(default=0),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> BenchmarkPlanRead:
    """**抽题预览**：这次会考哪些题、按什么规则选的、涉及哪些专题。

    它不写任何东西（连 `benchmark_run` 都不建），所以可以随便点。
    存在的理由见 `BenchmarkPlanRead`：按"开始测试"之前该看见什么。
    """
    plan = _plan_for(
        session,
        settings,
        form=form,
        variable_id=variable_id,
        channel=channel,
        method_id=method_id,
        seed=seed,
    )
    return BenchmarkPlanRead(
        variable_id=plan.variable_id,
        channel=plan.channel,
        method_id=plan.method_id,
        form=plan.form,
        seed=plan.seed,
        items=[
            BenchmarkPlanItemRead(
                item_id=item.item_id,
                role=item.role,
                difficulty=item.difficulty,
                topics=list(item.topics),
                why=item.why,
            )
            for item in plan.items
        ],
        topics=list(plan.topics),
        notes=list(plan.notes),
        summary=plan.describe(),
    )


@router.post("/runs/{run_id}/status", response_model=BenchmarkRunRead)
def set_run_status(
    run_id: str,
    body: BenchmarkRunStatusWrite,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> BenchmarkRunRead:
    """中断（保存退出）/ 继续 / 作废。

    非法迁移是 **422**，不是"静默改成最近的合法状态" ——
    后者会让"我明明点了重新开始"变成一件无法追查的事。
    """
    _require_run(session, run_id)
    try:
        router_.set_benchmark_run_status(
            run_id=run_id, target=body.target, reason=body.reason
        )
    except ValueError as exc:
        raise _as_http(exc) from exc
    return _run_read(session, _require_run(session, run_id))


@router.post("/runs/{run_id}/restart", response_model=BenchmarkRunRead, status_code=201)
def restart_run(
    run_id: str,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> BenchmarkRunRead:
    """**重新开始**：把旧的那次作废，并开一次同样卷别的新运行。

    ★ 为什么不是"把旧运行重置成空的"：
    那会让「他中途放弃过几次」这个事实永久消失 —— 而用户明确要求
    "可以选择重新开始"，那句话的含义是**这一次不要了**，不是
    "这一次从没发生过"。作废 + 新建把两件事都记下来：
    旧的那次停在 `abandoned`，新的那次从头开始。
    """
    old = _require_run(session, run_id)
    try:
        router_.set_benchmark_run_status(
            run_id=run_id,
            target=BenchmarkRunStatus.ABANDONED,
            reason="用户选择重新开始",
        )
    except ValueError as exc:
        raise _as_http(exc) from exc
    new_id = router_.start_benchmark_run(
        form=old.form,
        variable_id=old.variable_id,
        channel=old.channel,
        method_id=old.method_id,
        item_ids=list(old.item_ids),
        seed=old.seed,
        topic_snapshot=list(old.topic_snapshot),
        completeness=old.completeness,
        note=f"重开（作废 {run_id}）",
    )
    return _run_read(session, _require_run(session, new_id))


@router.post("/runs/{run_id}/responses", status_code=201)
def record_response(
    run_id: str,
    body: BenchmarkResponseWrite,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> dict:
    """落一道题的作答。**逐题落**，所以中断也留得下东西。"""
    _require_run(session, run_id)
    response_id = router_.record_benchmark_response(
        run_id=run_id,
        item_id=body.item_id,
        question_no=body.question_no,
        kind=body.kind,
        judged_by=body.judged_by,
        raw_answer=body.raw_answer,
        item_score=body.item_score,
        difficulty=body.difficulty,
        weight=body.weight,
        elapsed_sec=body.elapsed_sec,
        # ★ 隐性参数**记录，不自动计分**（P11）。要它产出分数，
        # 题目得声明一条 `observation_table` 并把 `unit` 写成这些名字之一。
        hesitation_sec=body.hesitation_sec,
        switch_count=body.switch_count,
        help_request_count=body.help_request_count,
        hint_used_count=body.hint_used_count,
        judge_model=body.judge_model,
        judge_note=body.judge_note,
        judged_hits=body.judged_hits,
    )
    return {"response_id": response_id}


@router.post("/runs/{run_id}/finish", response_model=BenchmarkRunRead)
def finish_run(
    run_id: str,
    body: BenchmarkFinishWrite,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
    settings: Settings = Depends(get_settings),
) -> BenchmarkRunRead:
    """补齐分数与置信并把状态推到 `finished`。**只允许一次。**

    ## ★ P9：`method_score` / `channel_score` 没传就**服务端算**

    ★ 关键设计：**这里不重新判分**，只把库里已有的单题分加总。
    重新判分意味着同一个答案可能得到另一个分数 ——
    于是"界面上看到的 7.5"与"库里存的 6.8"会不一样，
    而那种不一致没有任何一步会报错。判分只发生在 `/score`。

    ★ 拿不到题池（题库目录还没放进来）**也照样能收尾**：
    难度优先从题库读，读不到就用落作答时记下的 `difficulty`。
    两者都没有的那几道会被记进 `skipped` 而不参与聚合 ——
    一条运行不该因为题池是渐进补的而永远结束不了。
    """
    run = _require_run(session, run_id)
    method_score = body.method_score
    channel_score = body.channel_score
    confidence = body.confidence
    completeness = body.completeness
    if method_score is None or channel_score is None or confidence is None:
        try:
            bank_items = _load_bank(settings)
        except ValueError:
            bank_items = []
        bank = {item.item_id: item for item in bank_items}
        aggregate, channel, decided, _reason, _items, _skipped, _notes = _aggregate_rows(
            session, run, bank
        )
        if method_score is None:
            method_score = aggregate.method_score
        if channel_score is None:
            channel_score = channel.channel_score
        if confidence is None:
            confidence = BaselineConfidence(decided)
        if aggregate.is_partial:
            completeness = Completeness.PARTIAL
    try:
        router_.finish_benchmark_run(
            run_id=run_id,
            method_score=method_score,
            channel_score=channel_score,
            anchor_baseline=body.anchor_baseline,
            confidence=confidence,
            completeness=completeness,
            boundary=body.boundary,
        )
    except ValueError as exc:
        raise _as_http(exc) from exc
    return _run_read(session, _require_run(session, run_id))


# --------------------------------------------------------------------------- #
# 计分（P9）：纯函数在 app.benchmark.scoring，这里只做取数、判官接线与写回
# --------------------------------------------------------------------------- #


def _method_priorities() -> dict[str, str]:
    """`method_id → P1/P2/P3`（来自 `app.seeds.m1_variables`，不另立一份）。

    ★ 不在这里写一张"优先级表"：那张表已经存在于变量定义里，
    抄一份的话，两处迟早会不一致 —— 而不一致的表现是通道分算错，
    却每一步都看起来正常（拿到的还是 0-10 的一个数）。
    """
    from app.seeds.m1_variables import _PREFERENCES, _STRATEGY_OPTIONS, _TRAITS

    out: dict[str, str] = {}
    for variable in (*_TRAITS, *_PREFERENCES, *_STRATEGY_OPTIONS):
        for method in variable.get("measurement", {}).get("methods", []):
            out[method["method_id"]] = method.get("priority", "P3")
    return out


def _judge_ask(client) -> Callable[[str], tuple[str, str | None]]:
    """把真实 LLM 客户端包成判官要的回调（`prompt → (原话, 模型名)`）。

    ★ 为什么在这里包：`app.benchmark` 是 L3''、`app.agent` 是 L4，
    低层不许 import 高层。所以"怎么够到模型"由这一层给，
    "怎么判"留在 `app.benchmark.judge` —— 那一条是**规则**，能被复算。
    """
    from app.agent.llm import ChatMessage

    def ask(prompt: str) -> tuple[str, str | None]:
        reply = client.chat(
            [
                ChatMessage(role="system", content=SYSTEM_PROMPT),
                ChatMessage(role="user", content=prompt),
            ]
        )
        return (reply.content or ""), getattr(client, "model", None)

    return ask


def _exact_question(item: BenchItem, question_no: int) -> dict | None:
    """按题号取客观题规格。题号从 1 开始（`exact_questions[].no`）。"""
    for question in item.exact_questions:
        if int(question.get("no", 0)) == question_no:
            return question
    if 1 <= question_no <= len(item.exact_questions):
        return item.exact_questions[question_no - 1]
    return None


def _score_one(
    row, item: BenchItem | None, ask: Callable[[str], tuple[str, str | None]]
) -> tuple[object | None, list[str]]:
    """给一条作答算分。返回 `(ScoredItem | None, 说明列表)`。

    `None` = 这题**没法算**（题库里没有它、或题型没有换算表），
    调用方据此把它记进 `skipped` —— 静默少算正是计分最容易出的一类错。
    """
    notes: list[str] = []
    if item is None:
        return None, [f"答题 {row.item_id}：题库里没有这道题，未计分"]
    answer = row.raw_answer or ""
    if row.kind == ResponseQuestionKind.KEY_POINTS:
        spec = item.key_points_question or {}
        guide = [str(point) for point in (spec.get("scoring_guide") or [])]
        if not guide:
            return None, [f"{row.item_id}：题目没有 `scoring_guide`，要点题无从判分"]
        verdict = judge_key_points(guide, answer, ask=ask)
        if not verdict.ok:
            notes.append(
                f"{row.item_id} 第 {row.question_no} 题：判官没有给出可用判定"
                f"（{verdict.error}）—— 记作**没测到**，不是 0 分"
            )
        elif verdict.downgraded or verdict.out_of_range:
            notes.append(f"{row.item_id} 第 {row.question_no} 题：{verdict_note(verdict)}")
        return score_key_points(guide, answer, verdict), notes

    if row.kind == ResponseQuestionKind.OBSERVATION:
        table = _observation_table(item)
        if table is None:
            return None, [
                f"{row.item_id}：这道观测题没有声明换算表"
                f"（`scoring.observation_table`）—— 未计分："
                f"换算表是一个测量设计决定，不是一个可以推断的默认值"
            ]
        # ★ 隐性参数与"他从材料里抄下的数字"走同一个出口（P11）：
        # 题目声明 `unit`，`observation_value` 按 unit 去取。
        # 没声明过的隐性参数**不参与计分** —— 记录不等于计分。
        return (
            score_observation(
                table,
                raw_answer=answer,
                implicit=_implicit_values(row),
            ),
            notes,
        )

    if row.kind == ResponseQuestionKind.SELF_REPORT:
        # ★ 选项权重来自**题目**（`self_report_question.options[].weight`），
        # 不由模型或界面临时定 —— 它是测量刻度，两次作答用不同刻度就不可比，
        # 而不可比的两个数放在一起看起来只是"他变了"。
        if item.self_report_question is None:
            return None, [
                f"{row.item_id}：这道自评题在题库里没有声明量表"
                f"（`self_report_question`）—— 未计分："
                f"选项权重是一个测量设计决定，不是可以推断的默认值"
            ]
        return score_self_report(item.self_report_question, answer), notes

    question = _exact_question(item, row.question_no)
    if question is None:
        return None, [f"{row.item_id} 第 {row.question_no} 题：题目里没有这个题号"]
    expected = str(question.get("answer") or "")
    if not expected:
        return None, [f"{row.item_id} 第 {row.question_no} 题：题目没有给答案，未计分"]
    blanks = [part for part in ANSWER_SEP.split(expected) if part.strip()]
    if len(blanks) > 1:
        if row.kind is not ResponseQuestionKind.EXACT_BLANK:
            notes.append(
                f"{row.item_id} 第 {row.question_no} 题：题目是 {len(blanks)} 空填空，"
                f"而作答记的是 `{row.kind.value}` —— 按题目的真实结构算分"
            )
        return score_exact_blank(answer, expected), notes
    if row.kind is ResponseQuestionKind.EXACT_BLANK:
        # 反过来：记成填空而题目只有一个空 —— 规则一样，不用提示。
        return score_exact_blank(answer, expected), notes
    return score_exact(answer, expected), notes


def _observation_table(item: BenchItem) -> ObservationTable | None:
    """从题目规格里读换算表。**读不出来就是 None**（未计分），不编一个。"""
    spec = item.scoring or {}
    raw = spec.get("observation_table")
    if not isinstance(raw, dict):
        return None
    bands = []
    for band in raw.get("bands") or []:
        if not isinstance(band, dict):
            continue
        upper = band.get("upper", None)
        bands.append(
            ObservationBand(
                upper=None if upper is None else float(upper),
                score=float(band.get("score", 0.0)),
                label=str(band.get("label") or ""),
            )
        )
    if not bands:
        return None
    return ObservationTable(
        table_id=str(raw.get("table_id") or "未命名"),
        unit=str(raw.get("unit") or ""),
        bands=tuple(bands),
    )


def _float_or_none(text: str | None) -> float | None:
    """观测题的原始量。★ **解析不出来 = `None`（没测到），不是 0。**

    把 `"好像挺快的"` 当成 0 会让一次没测到的观测变成一个最低分，
    而两者在界面上长得一样。
    """
    if text is None:
        return None
    try:
        return float(text.strip())
    except (TypeError, ValueError):
        return None


def _implicit_values(row) -> dict[str, float | int | None]:
    """从库里那一行取出**隐性参数**（P11）。

    ★ 取的是**作答记录上的列**，不是 `raw_answer` 里的字：
    这几个量是系统在作答过程中顺手记下来的（用时/犹豫/切换/求助/提示），
    他不需要手写它们 —— 让他手写"我犹豫了几秒"会把一个行为量
    变成一个自评量，而那正是两件不同的事。

    ★ `None` = 这次没采到。它**不等于 0**：观测题遇到它作废（不是最低分），
    "他没犹豫"与"我们忘了记犹豫"必须能分开。
    """
    return {
        name: getattr(row, name, None) for name in IMPLICIT_PARAMETERS
    }


def _aggregate_rows(
    session: Session, run, bank: dict[str, BenchItem]
) -> tuple[MethodAggregate, ChannelAggregate, str, str, list[dict], list[str], list[str]]:
    """从**库里已有的**单题分聚合出方法分/通道分/置信档。

    ★ 它**不重新判分**。这一点很重要：`/finish` 若再问一次判官，
    同一个答案可能得到另一个分数，于是"界面上看到的 7.5"与
    "库里存的 6.8"会不一样 —— 而那种不一致没有任何一步会报错。

    所以判分只发生在 `/score`；这里只做能复算的加总。
    """
    rows = reads.list_benchmark_responses(session, run.run_id)
    scored: list[tuple[ScoredItem, str]] = []
    items_out: list[dict] = []
    skipped: list[str] = []
    unknowns = 0
    for row in rows:
        item = bank.get(row.item_id)
        difficulty = (item.difficulty if item else None) or row.difficulty
        counted = row.item_score is not None and counts_toward_reading(row.kind.value)
        if row.item_score is not None and not difficulty:
            unknowns += 1
            skipped.append(
                f"答题 {row.item_id}：拿不到难度（题库里没有这道题），无法按难度加权 —— "
                f"分数还在，只是这次没算进方法分"
            )
        else:
            scored.append((_stored_result(row, row.item_score), difficulty or ""))
        items_out.append(
            {
                "response_id": row.response_id,
                "item_id": row.item_id,
                "question_no": row.question_no,
                "kind": row.kind,
                "difficulty": difficulty,
                "item_score": row.item_score,
                "scoring_method": row.scoring_method,
                "judged_by": row.judged_by,
                "judge_model": row.judge_model,
                "judge_note": row.judge_note,
                "void_reason": None if row.item_score is not None else _void_reason(row),
                "counted": counted,
            }
        )

    aggregate = aggregate_method(scored)
    priority = _method_priorities().get(run.method_id, "P3")
    channel = compose_channel([(run.method_id, aggregate.method_score, priority)])
    used = [
        result
        for result, _ in scored
        if result.item_score is not None and result.counts_toward_reading
    ]
    anchors = sum(
        1
        for row in rows
        if row.item_score is not None
        and row.item_id in bank
        and bank[row.item_id].is_anchor
    )
    all_objective = all(
        row.kind in (ResponseQuestionKind.EXACT, ResponseQuestionKind.EXACT_BLANK)
        for row in rows
    )
    has_observation = any(row.kind is ResponseQuestionKind.OBSERVATION for row in rows)
    confidence, reason = decide_confidence(
        has_stated=run.channel == EvidenceChannel.STATED,
        measured_used=len(used),
        anchor_count=anchors,
        all_objective=all_objective,
        has_observation=has_observation,
    )
    notes: list[str] = []
    if unknowns:
        notes.append(f"{unknowns} 道题拿不到难度，未参与聚合（见 skipped）")
    if has_observation:
        notes.append(
            "行为观测题参与读数，但它的换算是人定的档 —— 置信档因此封顶在 provisional"
        )
    # ★ 这里**曾经**写着「等值化校正尚未实现：锚题得分只统计了，没有用来校正难度漂移」。
    #   P12 把等值化做出来之后，这句话没人删 —— 于是同一个响应里
    #   一边说"尚未实现"，一边给出算好的 `shift`（**跑真链路时抓出来的**：
    #   `tools/runbench.py` 交完卷把整份报告打印出来，两句话隔着几行对着看）。
    #   现在等值化的状态由**它自己**说：`_equate_run` 的 `describe()` 会讲清
    #   "校正了多少"或"为什么校正不了"（`shift=None` + 理由），
    #   而那句话是**当场算出来的**，不会过期。
    return aggregate, channel, confidence, reason, items_out, skipped, notes


def _stored_result(row, item_score: float | None) -> ScoredItem:
    """把库里的那一行还原成聚合器看得懂的计分结果。

    ★ `counts_toward_reading` 由 `kind` 反推（`counts_toward_reading()`），
    而不是再从 `scoring_method` 里解析 —— 见 `scoring.READING_KINDS` 的说明：
    写入与读回必须用**同一个判定**，否则会出现"计分时算它、汇总时不算它"。
    """
    return ScoredItem(
        item_score=item_score,
        scoring_method=row.scoring_method or "未记录",
        judged_by=row.judged_by.value,
        judge_model=row.judge_model,
        judge_note=row.judge_note,
        judged_hits=row.judged_hits,
        counts_toward_reading=counts_toward_reading(row.kind.value),
        void_reason="" if item_score is not None else _void_reason(row),
    )


def _void_reason(row) -> str:
    """这一题为什么没有分 —— **要能直接给用户看**。"""
    if row.item_score is not None:
        return ""
    if row.judged_by.value == "llm":
        return f"判官没有给出可用判定（{row.judge_note or '无说明'}）—— 没测到，不是 0 分"
    return row.judge_note or "没测到（作废）"


def _score_run(
    session: Session,
    run,
    bank_items: list[BenchItem],
    *,
    client,
    router_: Router,
) -> BenchmarkScoreRead:
    """**判分并写回**：逐题算、逐题落，然后聚合。

    ★ 为什么逐题落而不是攒完再一次写：一次 60 分钟的全面卷里，
    用户可能在第 40 分钟关掉页面。那时前 30 分钟的分**必须已经在库里** ——
    否则"保存并退出"保存的是一份没有分数的答卷，
    而它之后再也不会被重新判一次（下次进来是"继续"，不是"重做"）。
    """
    bank = {item.item_id: item for item in bank_items}
    ask = _judge_ask(client)
    rows = reads.list_benchmark_responses(session, run.run_id)
    if not rows:
        raise ValidationRejected(
            f"这次运行（{run.run_id}）还没有任何作答 —— 没有可计分的东西"
        )
    notes: list[str] = []
    for row in rows:
        result, row_notes = _score_one(row, bank.get(row.item_id), ask)
        notes.extend(row_notes)
        if result is None:
            router_.apply_benchmark_score(
                response_id=row.response_id,
                item_score=None,
                scoring_method="未计分",
                judged_by=JudgedBy.CODE,
                judge_note=row_notes[0] if row_notes else "未计分",
            )
            continue
        router_.apply_benchmark_score(
            response_id=row.response_id,
            item_score=result.item_score,
            scoring_method=result.scoring_method,
            judged_by=JudgedBy(result.judged_by),
            judge_model=result.judge_model,
            judge_note=result.judge_note,
            judged_hits=result.judged_hits,
        )
    aggregate, channel, confidence, reason, items_out, skipped, agg_notes = (
        _aggregate_rows(session, run, bank)
    )
    equating, fresh_baseline = _equate_run(session, run, bank, items_out)
    if fresh_baseline:
        # ★ 基线**只写还没有基线的那些锚题**（P12）：`reads.anchor_baselines`
        # 取最早那次，所以后来写进去的不会改变它 —— 那会让"用来校正漂移的东西"
        # 自己漂移，而它一漂，全部历史分数的可比性同时失效。
        router_.record_anchor_baseline(run.run_id, baseline=fresh_baseline)
    score_notes = notes + agg_notes
    score_notes.append(equating.describe())
    return BenchmarkScoreRead(
        run_id=run.run_id,
        method_score=aggregate.method_score,
        channel_score=channel.channel_score,
        equated_score=equating.correct(aggregate.method_score),
        equating=EquatingRead(
            shift=equating.shift,
            reason=equating.reason,
            description=equating.describe(),
            anchors=[
                EquatingAnchorRead(
                    item_id=a.item_id,
                    baseline=a.baseline,
                    current=a.current,
                    delta=round(a.delta, 2),
                )
                for a in equating.anchors
            ],
            baseline_runs=list(equating.baseline_runs),
        ),
        completeness=(
            Completeness.COMPLETE if not aggregate.is_partial else Completeness.PARTIAL
        ),
        confidence=BaselineConfidence(confidence),
        confidence_reason=reason,
        method_detail=aggregate.detail,
        channel_detail=channel.detail,
        items=items_out,
        skipped=skipped,
        notes=score_notes,
    )

def _equate_run(
    session: Session,
    run,
    bank: dict[str, BenchItem],
    items_out: list[dict],
) -> tuple[object, dict]:
    """算这次运行的等值化校正，并给出**该记下来的新基线**（P12）。

    返回 `(EquatingResult, 需要记成基线的锚题读数)`。

    ★ 基线取"**这次运行之前**最早记录过它的那次"（`before=started_at`）：
    同一道锚题在**本次**的读数当然不能当自己的基线 —— 那会让校正量恒为 0，
    而恒为 0 的校正看起来就像"没有漂移"。

    ★ 新基线只包含**还没有基线**的锚题。第一次用某道锚题时它成为基线，
    此后那次运行的分数就是这把尺子的刻度原点。

    ★ 一道锚题可能有多行（要点题 + 客观题）。取的是**产出读数的那一行**
    （`counted`）：等值化的对象是读数，不是标尺自身的原始分。
    一道锚题若只有标尺行，就退而用它 —— 那时它作为锚题的意义仍然是"同一道题"。
    """
    readings: dict[str, float | None] = {}
    fallbacks: dict[str, float | None] = {}
    for row in items_out:
        item = bank.get(row["item_id"])
        if item is None or not item.is_anchor:
            continue
        if row.get("counted"):
            readings[row["item_id"]] = row.get("item_score")
        else:
            fallbacks.setdefault(row["item_id"], row.get("item_score"))
    anchors = {**fallbacks, **readings}
    baselines = reads.anchor_baselines(session, anchors.keys(), before=run.started_at)
    result = equate(
        anchors,
        {item_id: base.score for item_id, base in baselines.items()},
        baseline_runs={item_id: base.run_id for item_id, base in baselines.items()},
    )
    fresh = {
        item_id: score
        for item_id, score in anchors.items()
        if item_id not in baselines and score is not None
    }
    return result, fresh


@router.post("/runs/{run_id}/score", response_model=BenchmarkScoreRead)
def score_run(
    run_id: str,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
    settings: Settings = Depends(get_settings),
    client=Depends(get_llm_client),
) -> BenchmarkScoreRead:
    """**计分**：逐题判分 + 写回 + 聚合成方法分/通道分（P9）。

    ## 为什么计分在服务端

    原先 `item_score` 由调用方算好传进来。那意味着**判分规则要在前端
    再实现一遍**，而两份实现不会一直一致 —— 不一致的表现是同一个人的
    两个分数，且都不报错。规则只有一份（`app.benchmark.scoring`），
    所以判分也只能有一个入口。

    调用方仍可以传 `item_score`（复现与测试路径）；传了就以它为准。

    ## 它不做的事

    不算等值化校正、不写 `benchmark_run` 的最终分数 ——
    那是 `/finish` 的事。这个端点只回答「这套作答算下来是多少」，
    所以它可以被反复调用（改判分规则之后重算一次）。
    """
    run = _require_run(session, run_id)
    if run.status in (BenchmarkRunStatus.ABANDONED,):
        raise ValidationRejected(
            f"这次运行已经作废（{run.status.value}）—— 不再计分。"
            f"作废的运行保留记录，但不产出新的读数"
        )
    try:
        bank_items = _load_bank(settings)
    except ValueError as exc:
        raise _as_http(exc) from exc
    try:
        return _score_run(session, run, bank_items, client=client, router_=router_)
    except ValueError as exc:
        # ★ 只包 `ValueError`。`ValidationRejected`（域异常）**不包**：
        # 它由应用级处理器变成 `{code, message, detail}`，而 `_as_http`
        # 会把它变成 `{detail}` —— 同一类错误两种响应形状，
        # 调用方就得为每个端点各记一种。域异常一律走域处理器。
        raise _as_http(exc) from exc
