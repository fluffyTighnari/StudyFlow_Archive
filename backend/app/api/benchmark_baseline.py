"""`/api/benchmark/baseline` —— **基线测卷**：固定卷面、逐段存档、可续答。

## 它和 `/api/benchmark` 的关系

`/api/benchmark` 管的是**按变量抽题**的运行（一份材料 + 一套题 = 一个变量的一格）。
基线测卷是**固定卷**：A/B1–B5/C1–C2/D 按顺序走完，一次覆盖 11 个变量的
多个格子。卷面内容住在 `app.benchmark.baseline_paper`，这里只做
"下发卷面 / 建运行 / 存档 / 收卷"。

## 为什么收卷时只算客观题

卷面 §5 的算分口径里，三句话概括要判官、求助经过要换算表、决策要人看
"比较了哪几条"、复盘要按 rubric。这些**这一次都不自动算** ——
结果页只展示代码能核对的部分（B1、B2-2..6、B3 的材料内 5 项）与观测到的量。
把不能算的部分算成一个数，比空着更糟：它会让下一次真的判分时对不上。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_router, get_session
from app.api.envelope import ItemsResponse, ok
from app.benchmark import baseline_paper
from app.core.errors import NotFound
from app.global_domain import reads
from app.global_domain.enums import BenchmarkRunStatus
from app.router import Router

router = APIRouter(prefix="/api/benchmark/baseline", tags=["benchmark-baseline"])


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BaselinePaperRunRead(BaseModel):
    run_id: str
    paper_id: str
    paper_version: str
    status: BenchmarkRunStatus
    answers: dict[str, Any]
    current_section: str | None
    section_seconds: dict[str, Any]
    results: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None
    elapsed_minutes: float | None


class LatestRunRead(BaseModel):
    """「上一次」入口。`run` 为 `None` 是**一个正常答案**（没做过）。"""

    run: BaselinePaperRunRead | None


class StartRunWrite(_Request):
    paper_id: str = Field(default=baseline_paper.PAPER_ID, min_length=1)


class SaveAnswersWrite(_Request):
    answers: dict[str, Any] = Field(default_factory=dict)
    current_section: str | None = None
    section_seconds: dict[str, Any] = Field(default_factory=dict)


def _read(row) -> BaselinePaperRunRead:
    elapsed = None
    if row.finished_at is not None:
        elapsed = round((row.finished_at - row.started_at).total_seconds() / 60, 1)
    return BaselinePaperRunRead(
        run_id=row.run_id,
        paper_id=row.paper_id,
        paper_version=row.paper_version,
        status=row.status,
        answers=dict(row.answers or {}),
        current_section=row.current_section,
        section_seconds=dict(row.section_seconds or {}),
        results=dict(row.results or {}),
        started_at=row.started_at,
        finished_at=row.finished_at,
        elapsed_minutes=elapsed,
    )


def _require(session: Session, run_id: str):
    row = reads.get_baseline_paper_run(session, run_id)
    if row is None:
        raise NotFound(f"基线测卷运行不存在：{run_id}")
    return row


def _as_http(exc: ValueError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
    )


@router.get("/paper", response_model=dict[str, Any])
def get_paper() -> dict[str, Any]:
    """下发卷面。**不含答案与评分键** —— 泄题的卷子测不到它想测的东西。"""
    return baseline_paper.public_paper()


@router.get("/runs/latest", response_model=LatestRunRead)
def latest_run(session: Session = Depends(get_session)) -> LatestRunRead:
    row = reads.latest_baseline_paper_run(session)
    return LatestRunRead(run=_read(row) if row else None)


@router.get("/runs", response_model=ItemsResponse[BaselinePaperRunRead])
def list_runs(
    session: Session = Depends(get_session),
) -> dict[str, list[BaselinePaperRunRead]]:
    return ok([_read(row) for row in reads.list_baseline_paper_runs(session)])


@router.get("/runs/{run_id}", response_model=BaselinePaperRunRead)
def get_run(
    run_id: str, session: Session = Depends(get_session)
) -> BaselinePaperRunRead:
    return _read(_require(session, run_id))


@router.post("/runs", response_model=BaselinePaperRunRead, status_code=201)
def start_run(
    body: StartRunWrite,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> BaselinePaperRunRead:
    if body.paper_id != baseline_paper.PAPER_ID:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"没有这份卷子：{body.paper_id}（当前只有 "
                f"{baseline_paper.PAPER_ID} {baseline_paper.PAPER_VERSION}）"
            ),
        )
    run_id = router_.start_baseline_paper_run(
        paper_id=baseline_paper.PAPER_ID,
        paper_version=baseline_paper.PAPER_VERSION,
    )
    return _read(_require(session, run_id))


@router.post("/runs/{run_id}/answers", response_model=BaselinePaperRunRead)
def save_answers(
    run_id: str,
    body: SaveAnswersWrite,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> BaselinePaperRunRead:
    """逐段存档：**整份替换**客户端手里的作答。切段、关页面都调一次。"""
    _require(session, run_id)
    try:
        router_.save_baseline_paper_answers(
            run_id,
            answers=body.answers,
            current_section=body.current_section,
            section_seconds=body.section_seconds,
        )
    except ValueError as exc:
        raise _as_http(exc) from exc
    return _read(_require(session, run_id))


@router.post("/runs/{run_id}/finish", response_model=BaselinePaperRunRead)
def finish_run(
    run_id: str,
    body: SaveAnswersWrite,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> BaselinePaperRunRead:
    """收卷：存最后一份作答，并算出**能当场核对**的那部分。"""
    _require(session, run_id)
    results = baseline_paper.evaluate(body.answers)
    try:
        router_.finish_baseline_paper_run(
            run_id,
            answers=body.answers,
            current_section=body.current_section,
            section_seconds=body.section_seconds,
            results=results,
        )
    except ValueError as exc:
        raise _as_http(exc) from exc
    return _read(_require(session, run_id))
