"""`/api/m1` —— 变量定义、逐通道覆盖、版本历史、回滚、重做建议。

这一组端点体现 M0 §6.5 的通道纯度：**没有任何一个响应把多通道合成一个分数**。
UI 拿到的是「11 个变量，每个 3 个通道，每个通道一个当前版本」——
想画雷达图是前端的事，系统不提供那个数。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_router, get_session
from app.core.errors import NotFound
from app.global_domain import reads
from app.global_domain.enums import EvidenceChannel
from app.global_domain.schemas import (
    AdvisoryRead,
    BaselineVersionRead,
    ChannelCoverageRead,
    ChannelObsRead,
    M1VariableRead,
)
from app.router import RollbackRequest, RollbackResult, Router

router = APIRouter(prefix="/api/m1", tags=["m1"])


def _require_variable(session: Session, variable_id: str) -> None:
    """未注册的变量要 **404**，而不是返回空骨架。

    返回空骨架会让拼错的 `V_SE` 看起来像一个「还没测过的变量」，
    而实际上系统里根本没有这个变量 —— 一个静默的错误。
    """
    if reads.get_variable(session, variable_id) is None:
        raise NotFound(f"M1 变量不存在：{variable_id}")


@router.get("/variables", response_model=list[M1VariableRead])
def list_variables(session: Session = Depends(get_session)) -> list[M1VariableRead]:
    """11 个变量的定义 + **逐通道**的设计态与当前版本。"""
    out: list[M1VariableRead] = []
    for variable in reads.list_variables(session):
        channels: list[ChannelCoverageRead] = []
        for coverage in reads.measurement_coverage(session, variable.variable_id):
            current = reads.current_baseline_version(
                session, variable.variable_id, coverage.channel
            )
            channels.append(
                ChannelCoverageRead(
                    channel=coverage.channel,
                    designed=coverage.designed,
                    method_ids=list(coverage.method_ids),
                    design_note=coverage.design_note,
                    completeness=coverage.completeness,
                    current=(
                        BaselineVersionRead.model_validate(current)
                        if current is not None
                        else None
                    ),
                )
            )
        out.append(M1VariableRead.from_variable(variable, channels))
    return out


@router.get("/{variable_id}/history", response_model=list[BaselineVersionRead])
def variable_history(
    variable_id: str,
    channel: EvidenceChannel | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[BaselineVersionRead]:
    """全部历史版本（**时间升序**，含已被取代的）。

    升序是刻意的：前端的时间轴与「回滚到某版本」的候选列表都按时间读，
    倒序会让调用方到处 `.reverse()`，而某处忘了 reverse 就是一个难以发现的 UI 错序。
    """
    _require_variable(session, variable_id)
    return [
        BaselineVersionRead.model_validate(row)
        for row in reads.baseline_history(session, variable_id, channel)
    ]


@router.get("/{variable_id}/observations", response_model=list[ChannelObsRead])
def variable_observations(
    variable_id: str,
    channel: EvidenceChannel | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[ChannelObsRead]:
    """观测明细（时间倒序）。

    §7.3 的「≥2 条独立 Session 证据」是用户会撞上的硬门槛。只给一个计数，
    门槛就变成一句无法核对的断言；因此这里把「算了几条、是哪几个 Session」
    原样摊开 —— `source_ref` 就是去重口径（同一 Session 记 5 次仍然只算 1 条）。
    """
    _require_variable(session, variable_id)
    return [
        ChannelObsRead.model_validate(row)
        for row in reads.list_channel_obs(session, variable_id, channel)
    ]


@router.get("/{variable_id}/benchmark-advisory", response_model=AdvisoryRead)
def benchmark_advisory(
    variable_id: str,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> AdvisoryRead:
    """本季度 R2 重做建议（§5.4）。

    **响应里没有「是否阻断」这种字段**，因为不存在这种可能 ——
    `BenchmarkAdvisory` 类型本身就没有阻断能力。
    """
    _require_variable(session, variable_id)
    advisory = router_.benchmark_advisory(variable_id)
    return AdvisoryRead(
        variable_id=advisory.variable_id,
        level=advisory.level,
        run_count=advisory.run_count,
        message=advisory.message,
    )


@router.post("/{variable_id}/rollback", response_model=RollbackResult)
def rollback(
    variable_id: str,
    body: RollbackRequest,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> RollbackResult:
    """回滚（R5）：把指针移回一个旧版本，**生成新版本、不删历史**。

    `reason` 必填 —— 回滚是唯一会让基线读数变旧的操作，半年后唯一能区分
    「他真的退步了」与「当时手滑点错了」的就是这条原因。
    """
    _require_variable(session, variable_id)
    return router_.rollback(
        variable_id, body.channel, body.to_version_id, reason=body.reason
    )
