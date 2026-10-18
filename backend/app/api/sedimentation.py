"""沉淀通路 —— **沙箱 → 全局的唯一接口**（P4 两条端到端通路之一）。

## 为什么调用 Router 的代码在这里，而不在 `session_domain`

架构规则 ARCH-11 禁止 `app.session_domain` import `app.router`。这不是形式主义：
让沙箱域有能力"顺手写全局"，§7.4 那条「stage 永不参与写入裁定」的隔离就退化成注释 ——
因为沙箱域手里有 stage，而 stage 里全是用户当下的即时效感受。

于是分工是：

```
session_domain.sedimentation   →  候选条目 + 排除理由（纯函数，不认识 Router）
app/api/sedimentation          →  映射成 UpdateIntent（本模块，唯一的映射点）
Router.submit                  →  判定表裁定 + 冲突仲裁 + 落审计日志
```

映射只在这一处，因此「一条 stage 条目变成什么全局写入」只有一个答案。

## 沉淀**不保证**写入

`Router` 的判定表说了算：

- `R4a`（完成总结）：自动产出、用户未过目 → **needs_confirmation**，进待确认队列。
- `R4b`（里程碑）：同 R4a。
- `R4c`（用户勾选）：用户勾选本身是确认动作（A21）→ 满足条件的直接生效。

也就是说：**同一批候选，走到 Router 之后可能只有一部分真的写进全局。**
本模块的响应里同时给出「候选」「排除」「逐条裁定结果」三样东西 ——
少了任何一样，用户都会觉得系统在偷偷替他做决定。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_router, get_session, new_ui_intent_id
from app.global_domain.enums import IntentStatus, LLMConfidence, TriggerType
from app.router import Router, SubmitRequest, SubmitResult, UpdateIntent
from app.session_domain import reads, state, writes
from app.session_domain.enums import StageItemStatus
from app.session_domain.sedimentation import (
    SedimentationCandidate,
    SedimentationPlan,
    build_candidates,
)

router = APIRouter(prefix="/api/sessions", tags=["sedimentation"])


# --------------------------------------------------------------------------- #
# 请求 / 响应模型
# --------------------------------------------------------------------------- #


class _Request(BaseModel):
    """请求基类：多给的字段一律 422（与 `session_domain.schemas._WriteModel` 同规矩）。

    沉淀是沙箱里唯一会改变全局认知的动作。它由一次勾选触发 ——
    此时静默忽略一个拼错的字段（`stage_item_id` 而不是 `stage_item_ids`）
    的后果是：用户以为勾选生效了，而系统沉淀了别的、或者什么都没做。
    """

    model_config = ConfigDict(extra="forbid")


class SedimentRequest(_Request):
    """`dry_run=True` 时**只预览不写** —— 让用户先看清"这次会沉淀什么"。

    这个字段不是锦上添花：沉淀是沙箱里唯一会改变全局认知的动作，
    而它由一个勾选手势触发。不给出预览，用户就是在点一个看不清后果的按钮。
    """

    trigger: TriggerType
    stage_item_ids: list[str] = Field(default_factory=list)
    milestone_ids: list[str] = Field(default_factory=list)
    summary_id: str | None = None
    dry_run: bool = False


class CandidateRead(BaseModel):
    source_kind: str
    source_ref: str
    statement: str
    target: str
    action: str
    proposed_basis: str
    value: dict[str, Any]


class ExcludedRead(BaseModel):
    source_kind: str
    source_ref: str
    content: str
    reason: str


class SedimentPlanRead(BaseModel):
    trigger: TriggerType
    included: list[CandidateRead]
    excluded: list[ExcludedRead]


class SedimentResult(BaseModel):
    """预览或执行的结果。`submit` 为 `None` 表示只预览（`dry_run`）。"""

    plan: SedimentPlanRead
    submit: SubmitResult | None = None


class CompleteRequest(_Request):
    """`POST /api/sessions/{id}/complete` 的入参。

    一次做完四件事：写总结 → 组 R4a 候选 → 提交 Router → 归档会话。
    合并成一个端点而不是让前端连打四个请求，是因为中间任何一步失败都会留下
    「总结写了但没沉淀」「沉淀了但会话没归档」这类需要人手工收拾的半成品。
    """

    expected_version: int = Field(ge=1)
    content: str = Field(min_length=1)
    findings: list[dict[str, Any]] | None = None
    #: 关掉它 = 只归档、不沉淀（例如这个会话什么都没学到）。
    sediment: bool = True


class CompleteResult(BaseModel):
    session: state.SessionStateRead
    plan: SedimentPlanRead | None = None
    submit: SubmitResult | None = None


# --------------------------------------------------------------------------- #
# 映射与执行
# --------------------------------------------------------------------------- #


def _to_read(plan: SedimentationPlan) -> SedimentPlanRead:
    return SedimentPlanRead(
        trigger=plan.trigger,
        included=[
            CandidateRead(
                source_kind=item.source_kind,
                source_ref=item.source_ref,
                statement=item.statement,
                target=item.target,
                action=item.action,
                proposed_basis=item.proposed_basis.value,
                value=item.value,
            )
            for item in plan.included
        ],
        excluded=[
            ExcludedRead(
                source_kind=item.source_kind,
                source_ref=item.source_ref,
                content=item.content,
                reason=item.reason,
            )
            for item in plan.excluded
        ],
    )


def _to_intents(
    session: Session, plan: SedimentationPlan, *, session_id: str
) -> list[tuple[SedimentationCandidate, UpdateIntent]]:
    """候选 → `UpdateIntent`。**这是整个项目里唯一一处这样的映射。**

    返回 `(候选, 意图)` 配对而不是裸意图列表：之后要把"哪一条真的写进去了"
    回填到 stage 条目上，而下标对齐是个脆弱的假设 ——
    Router 哪天调整了结果的顺序，`result.results[i]` 就会静默地对错号。

    `reasoning` 写的是「这条是怎么来的」（哪个 stage 条目 / 第几条 finding），
    而不是「为什么它是对的」—— 后者属于 `statement` 自己的理由（A19：
    不在结构上单开一个 `reason` 字段，理由内联在陈述里）。
    """
    pairs: list[tuple[SedimentationCandidate, UpdateIntent]] = []
    for candidate in plan.included:
        pairs.append(
            (
                candidate,
                UpdateIntent(
                    intent_id=new_ui_intent_id(session),
                    action=candidate.action,  # type: ignore[arg-type]
                    target=candidate.target,
                    value=candidate.value,
                    basis=candidate.proposed_basis,
                    reasoning=(
                        f"{plan.trigger.value} 沉淀：来源 {candidate.source_kind}"
                        f" {candidate.source_ref}"
                    ),
                    # 沉淀出来的不是模型当下的自信，而是一次观察的重述 ——
                    # 因此固定 `medium`，不让它冒充 `high`。
                    confidence=LLMConfidence.MEDIUM,
                ),
            )
        )
    return pairs


def _submit(
    db: Session,
    router_: Router,
    plan: SedimentationPlan,
    *,
    session_id: str,
) -> tuple[SubmitResult, dict[str, SedimentationCandidate]]:
    pairs = _to_intents(db, plan, session_id=session_id)
    by_intent = {intent.intent_id: candidate for candidate, intent in pairs}
    result = router_.submit(
        SubmitRequest(
            trigger_type=plan.trigger,
            intents=[intent for _candidate, intent in pairs],
            source_ref=session_id,
            session_ref=session_id,
        )
    )
    return result, by_intent


def _settle_candidates(
    db: Session,
    session_id: str,
    *,
    result: SubmitResult,
    by_intent: dict[str, SedimentationCandidate],
) -> None:
    """把**真的写进去了**的 stage 条目打上 `deposited`，并指回提案。

    只标真正 `applied` 的：一条候选被 Router 拒绝（或还在待确认），
    条目就该留在 `active` —— 否则用户会看到"已沉淀"而全局认知里什么都没有，
    而那种不一致会让人不再相信任何标记。

    映射走 `intent_id → 候选`，不靠下标：`SubmitResult.results` 的顺序
    是 Router 的实现细节，不该成为本模块正确性的前提。
    """
    for item in result.results:
        if item.status is not IntentStatus.APPLIED:
            continue
        candidate = by_intent.get(item.intent_id)
        if candidate is None or candidate.source_kind != "stage_item":
            continue
        writes.settle_stage_item(
            db,
            session_id,
            item_id=candidate.source_ref,
            status=StageItemStatus.DEPOSITED,
            expected_version=reads.get_session(db, session_id).version,
            result_ref=result.proposal_id,
        )


# --------------------------------------------------------------------------- #
# 端点
# --------------------------------------------------------------------------- #


@router.post("/{session_id}/sediment", response_model=SedimentResult, status_code=201)
def sediment(
    session_id: str,
    body: SedimentRequest,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> SedimentResult:
    """沉淀（R4a / R4b / R4c）。`dry_run=True` 只预览。

    返回里 `plan.excluded` 是**同等重要**的输出：它回答「我勾了 5 条，为什么只沉淀了 3 条」。
    """
    plan = build_candidates(
        session,
        session_id,
        trigger=body.trigger,
        stage_item_ids=body.stage_item_ids,
        milestone_ids=body.milestone_ids,
        summary_id=body.summary_id,
    )
    if body.dry_run or plan.is_empty:
        return SedimentResult(plan=_to_read(plan), submit=None)

    result, by_intent = _submit(session, router_, plan, session_id=session_id)
    _settle_candidates(
        session, session_id, result=result, by_intent=by_intent
    )
    return SedimentResult(plan=_to_read(plan), submit=result)


@router.post("/{session_id}/complete", response_model=CompleteResult)
def complete(
    session_id: str,
    body: CompleteRequest,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> CompleteResult:
    """完成会话：写总结 → 组 R4a 候选 → 提交 Router → 归档。

    顺序是刻意的：**先沉淀，后归档**。反过来的话，`completed` 状态会拒绝
    写总结与沉淀（状态机强制），于是"完成后才沉淀"就永远做不到了。
    """
    plan_read: SedimentPlanRead | None = None
    submit_result: SubmitResult | None = None

    # **先验版本，再动任何数据**（见 `writes.assert_session_version` 的说明）。
    writes.assert_session_version(session, session_id, body.expected_version)

    writes.write_summary(
        session, session_id, content=body.content, findings=body.findings
    )

    if body.sediment:
        plan = build_candidates(session, session_id, trigger=TriggerType.R4A)
        plan_read = _to_read(plan)
        if not plan.is_empty:
            submit_result, _by_intent = _submit(
                session, router_, plan, session_id=session_id
            )
            # 总结里的 finding 不是 stage 条目，因此这里没有条目可标记 ——
            # 刻意不调 `_settle_candidates`，避免"为了对称"而写一段没有对象的代码。

    # 版本现取：上面每一步都可能 +1（沉淀会改 stage 条目的归宿）。
    writes.transition(
        session,
        session_id,
        target="completed",
        expected_version=reads.get_session(session, session_id).version,
    )
    return CompleteResult(
        session=state.build_session_state(session, session_id),
        plan=plan_read,
        submit=submit_result,
    )


__all__: list[str] = ["router"]
