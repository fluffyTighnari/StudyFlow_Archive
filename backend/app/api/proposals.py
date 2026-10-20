"""`/api/proposals` —— 提案提交、待确认队列、逐条裁决。

这是治理内核在 HTTP 上的正面：**「计划区可点击讨论」与「沉淀需确认」的共同承载物**
就是这张提案卡（M0 §10）。因此这里的读接口必须能一次给全
「提案 + 它的每条 intent 的系统判定 + 重做建议 + 硬前置条件」，
否则前端无法把「为什么需要你确认」讲清楚。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.envelope import ItemsResponse, ok
from app.api.deps import get_router, get_session
from app.core.errors import NotFound
from app.global_domain import reads
from app.global_domain.enums import ProposalStatus
from app.global_domain.schemas import (
    ProposalDetailRead,
    ResolveRequest,
    UpdateIntentRead,
)
from app.router import Decision, Router, SubmitRequest, SubmitResult

router = APIRouter(prefix="/api/proposals", tags=["proposals"])


def _intents(session: Session, proposal_id: str) -> list[UpdateIntentRead]:
    return [
        UpdateIntentRead.model_validate(row)
        for row in reads.list_intents(session, proposal_id)
    ]


def _detail(session: Session, proposal_id: str) -> ProposalDetailRead:
    proposal = reads.get_proposal(session, proposal_id)
    if proposal is None:
        raise NotFound(f"提案不存在：{proposal_id}")
    return ProposalDetailRead.from_proposal(
        proposal, _intents(session, proposal_id)
    )


@router.post("", response_model=SubmitResult, status_code=201)
def submit(
    body: SubmitRequest,
    router_: Router = Depends(get_router),
) -> SubmitResult:
    """提交一批更新意图 —— 等价于 Agent 的 `submit_global_update`。

    同一份 `SubmitRequest` 契约、同一条 Router 路径，只是调用方从 LLM 换成了 UI/脚本。
    这正是「唯一写入入口」的检验方式：**如果这里需要一条特殊分支，说明设计破了。**
    """
    return router_.submit(body)


@router.get("", response_model=ItemsResponse[ProposalDetailRead])
def list_proposals(
    status: ProposalStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict[str, list[ProposalDetailRead]]:
    """提案列表。`?status=pending_confirmation` 即待确认队列。"""
    proposals = reads.list_proposals(session, status)[:limit]
    return ok([
        ProposalDetailRead.from_proposal(
            proposal, _intents(session, proposal.proposal_id)
        )
        for proposal in proposals
    ])


@router.get("/{proposal_id}", response_model=ProposalDetailRead)
def get_proposal(
    proposal_id: str, session: Session = Depends(get_session)
) -> ProposalDetailRead:
    return _detail(session, proposal_id)


@router.post("/{proposal_id}/resolve", response_model=SubmitResult)
def resolve(
    proposal_id: str,
    body: ResolveRequest,
    router_: Router = Depends(get_router),
) -> SubmitResult:
    """逐条裁决。

    三种失败在这里都变成明确的 HTTP：提案不存在 → 404；
    提案不在 `pending_confirmation`（已裁决过）→ 409；
    裁决引用了不属于该提案的 intent → 422。
    """
    decisions = [
        Decision(
            intent_id=item.intent_id, approved=item.approved, reason=item.reason
        )
        for item in body.decisions
    ]
    return router_.resolve(proposal_id, decisions)
