"""`/api/self-assessment` —— 自评单例：**一次性、全屏、不给人材料**。

## 它与 `/api/benchmark` 的分工

| | 自评 | 基准测试 |
|---|---|---|
| 给材料 | 不给 | 给 |
| 计时/判分 | 不计分，只算加权 | 逐题判分 |
| 产出通道 | **`stated`** | `measured` / `experienced` |
| 重做 | 单例，只在用户主动或矛盾核对时重做 | 每季度可重测 |
| 页面 | 全屏聚焦 | 常规页面 |

两者能写同一个变量，但**不是同一把尺子**：把自评的 7 分与任务的 7 分平均，
等于把「他觉得自己怎样」和「他做起来怎样」混成一个数。

## 写入路径：R2 + `basis=measured` + `value.channel=stated`

基准规范 §8 明确写过这一点：`update_parameter` 要求 `basis=measured`
（仪器是受控施测的），而 `value.channel` 是"这个读数属于哪个证据通道"。
自评量表是受控施测的仪器，它量的东西是**他的自述**，所以走 `stated` 通道。

用户点「写入基线」就是确认动作（`USER_CONFIRMED_GESTURES` 里已有一格），
因此判定是 `auto_apply`，不再弹一次确认。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_router, get_session, new_ui_intent_id
from app.api.envelope import ItemsResponse, ok
from app.benchmark import self_assessment as scale
from app.global_domain import reads
from app.global_domain.enums import (
    Basis,
    BaselineConfidence,
    Completeness,
    EvidenceChannel,
    IntentStatus,
    LLMConfidence,
    TriggerType,
)
from app.global_domain.targets import target_for_variable
from app.router import Router, SubmitRequest, UpdateIntent

router = APIRouter(prefix="/api/self-assessment", tags=["self-assessment"])


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StatementRead(BaseModel):
    no: int
    text: str


class OptionRead(BaseModel):
    value: str
    label: str


class AttentionSpanRead(BaseModel):
    prompt: str
    options: list[OptionRead]


class ActivityRankRead(BaseModel):
    prompt: str
    options: list[str]


class ExtraRead(BaseModel):
    attention_span: AttentionSpanRead
    activity_rank: ActivityRankRead


class ScaleRead(BaseModel):
    """卷面。**没有 `reverse`、没有 `weight`** —— 见模块文档。"""

    tier: str
    scale_version: str
    available_tiers: list[str]
    options: list[OptionRead]
    statements: list[StatementRead]
    extra: ExtraRead
    total: int
    #: 全面版的条数。简短版据此告诉用户"还有多少没问"。
    full_total: int


class SubmitWrite(_Request):
    #: `{卷面号: 选项}`。卷面号是字符串键（JSON 只有字符串键）。
    answers: dict[str, str] = Field(min_length=1)
    attention_span: str | None = None
    activity_rank: list[int] = Field(default_factory=list)
    elapsed_minutes: float | None = Field(default=None, ge=0, le=600)
    #: `short`（90 条）/ `full`（127 条）。缺省即全面版。
    tier: str = "full"

    @field_validator("answers")
    @classmethod
    def _numeric_keys(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            if not str(key).isdigit():
                raise ValueError(f"卷面号必须是数字，收到 {key!r}")
        return value


class ReadingRead(BaseModel):
    variable_id: str
    variable_name: str
    score: float
    facets: dict[str, float] = Field(default_factory=dict)
    boundary: str = ""


class SubmitRead(BaseModel):
    run_id: str
    readings: list[ReadingRead]
    flags: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    #: 写进基线的条数（与 `readings` 同长才算全部成功）。
    written: int
    #: 逐条拒绝理由（例如方案未注册）。**不吞掉**。
    rejected: list[str] = Field(default_factory=list)


class HistoryItemRead(BaseModel):
    run_id: str
    status: str
    tier: str | None = None
    scale_version: str | None = None
    started_at: str
    finished_at: str | None = None
    elapsed_minutes: float | None = None
    flags: list[str] = Field(default_factory=list)
    readings: list[ReadingRead] = Field(default_factory=list)


@router.get("/scale", response_model=ScaleRead)
def get_scale(tier: str = "full") -> ScaleRead:
    """卷面。**只有题目与选项标签** —— 反向句与权重永远不出这个端点。"""
    try:
        payload = scale.public_scale(tier)
    except scale.SelfAssessmentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ScaleRead(
        tier=payload["tier"],
        scale_version=payload["scale_version"],
        available_tiers=list(payload["available_tiers"]),
        options=[OptionRead(**item) for item in payload["options"]],
        statements=[StatementRead(**item) for item in payload["statements"]],
        extra=ExtraRead(
            attention_span=AttentionSpanRead(
                prompt=payload["extra"]["attention_span"]["prompt"],
                options=[
                    OptionRead(**item)
                    for item in payload["extra"]["attention_span"]["options"]
                ],
            ),
            activity_rank=ActivityRankRead(
                prompt=payload["extra"]["activity_rank"]["prompt"],
                options=list(payload["extra"]["activity_rank"]["options"]),
            ),
        ),
        total=payload["total"],
        full_total=payload["full_total"],
    )


def _confidence(flags: list[str], tier: str = "full") -> BaselineConfidence:
    """有有效性标记就降一档。**不因为"看起来还行"就给 calibrated。**

    简短版再多也只是每个 facet 两条：能给方向，不够做稳定基线，
    所以无论作答多干净都停在 `hypothesis`。
    """
    if tier == "short" or flags:
        return BaselineConfidence.HYPOTHESIS
    return BaselineConfidence.PROVISIONAL


@router.post("/submit", response_model=SubmitRead, status_code=201)
def submit(
    body: SubmitWrite,
    db: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> SubmitRead:
    """收一份自评：算分 → 一次运行 → 11 条 `stated` 基线 → 收尾。

    顺序是刻意的：**先算分再开运行**。算分失败（缺题、选项不合法）时
    不该留下一条空运行 —— 那会让「他做过几次自评」变成"点过几次按钮"。
    """
    answers = {int(key): value.strip().upper() for key, value in body.answers.items()}
    try:
        statements = scale.statements_for(body.tier)
        result = scale.score(
            answers, elapsed_minutes=body.elapsed_minutes, tier=body.tier
        )
    except scale.SelfAssessmentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    run_id = router_.start_self_assessment_run(
        answers={str(no): value for no, value in sorted(answers.items())},
        attention_span=body.attention_span,
        activity_rank=body.activity_rank,
        elapsed_minutes=body.elapsed_minutes,
        tier=body.tier,
        scale_version=scale.SCALE_VERSION,
    )

    confidence = _confidence(list(result.flags), body.tier)
    intents: list[UpdateIntent] = []
    for reading in result.scores:
        value = {
            "channel": EvidenceChannel.STATED.value,
            "score": reading.score,
            "completeness": Completeness.COMPLETE.value,
            "confidence": confidence.value,
            "boundary": reading.boundary or None,
        }
        intents.append(
            UpdateIntent(
                intent_id=new_ui_intent_id(db),
                action="update_parameter",
                target=target_for_variable(reading.variable_id),
                value=value,
                basis=Basis.MEASURED,
                reasoning=(
                    f"自评单例（{run_id}）：{len(statements)} 条量表（{body.tier}），"
                    f"通道 {EvidenceChannel.STATED.value}"
                ),
                confidence=LLMConfidence.HIGH,
            )
        )

    submitted = router_.submit(
        SubmitRequest(
            trigger_type=TriggerType.R2,
            intents=intents,
            source_ref=run_id,
            note=f"自评单例 {run_id}",
        )
    )
    applied = [
        item for item in submitted.results if item.status is IntentStatus.APPLIED
    ]
    rejected = [
        f"{item.intent_id}：{item.reason}"
        for item in submitted.results
        if item.status is not IntentStatus.APPLIED
    ]

    names = {
        row.variable_id: row.display_name for row in reads.list_variables(db)
    }
    readings = [
        ReadingRead(
            variable_id=row.variable_id,
            variable_name=names.get(row.variable_id, row.variable_id),
            score=row.score,
            facets=dict(row.facets),
            boundary=row.boundary,
        )
        for row in result.scores
    ]
    router_.finish_self_assessment_run(
        run_id,
        answers={str(no): value for no, value in sorted(answers.items())},
        attention_span=body.attention_span,
        activity_rank=body.activity_rank,
        elapsed_minutes=body.elapsed_minutes,
        flags=list(result.flags),
        notes=list(result.notes),
        readings=[reading.model_dump() for reading in readings],
        written_log_ids=[item.intent_id for item in applied],
        tier=body.tier,
        scale_version=scale.SCALE_VERSION,
    )

    return SubmitRead(
        run_id=run_id,
        readings=readings,
        flags=list(result.flags),
        notes=list(result.notes),
        written=len(applied),
        rejected=rejected,
    )


@router.get("/history", response_model=ItemsResponse[HistoryItemRead])
def history(db: Session = Depends(get_session)) -> dict[str, list[HistoryItemRead]]:
    """自评历史（最新在前）。**旧版本不删** —— 两次的差值本身是信息。"""
    return ok([
        HistoryItemRead(
            run_id=row.run_id,
            status=row.status.value,
            tier=row.tier,
            scale_version=row.scale_version,
            started_at=row.started_at.isoformat(),
            finished_at=row.finished_at.isoformat() if row.finished_at else None,
            elapsed_minutes=row.elapsed_minutes,
            flags=list(row.flags or []),
            readings=[ReadingRead(**item) for item in (row.readings or [])],
        )
        for row in reads.list_self_assessment_runs(db)
    ])


@router.get("/latest", response_model=HistoryItemRead | None)
def latest(db: Session = Depends(get_session)) -> HistoryItemRead | None:
    """最近一次**做完**的自评；没有就 `null`（不拿半截作答冒充读数）。"""
    row = reads.latest_finished_self_assessment(db)
    if row is None:
        return None
    return HistoryItemRead(
        run_id=row.run_id,
        status=row.status.value,
        tier=row.tier,
        scale_version=row.scale_version,
        started_at=row.started_at.isoformat(),
        finished_at=row.finished_at.isoformat() if row.finished_at else None,
        elapsed_minutes=row.elapsed_minutes,
        flags=list(row.flags or []),
        readings=[ReadingRead(**item) for item in (row.readings or [])],
    )


@router.get("/extra", response_model=dict[str, Any])
def extra_answers(db: Session = Depends(get_session)) -> dict[str, Any]:
    """附加两题的作答（专注时长区间 + 学习方式排序）。

    它们不进 M1 读数：区间不是分数，排序也不是。但它们**是这件事的答案本身**，
    所以照样存、照样读回来。
    """
    row = reads.latest_finished_self_assessment(db)
    if row is None:
        return {"attention_span": None, "activity_rank": [], "label": ""}
    return {
        "attention_span": row.attention_span,
        "activity_rank": list(row.activity_rank or []),
        "label": scale.attention_span_label(row.attention_span),
    }
