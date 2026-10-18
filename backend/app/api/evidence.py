"""`/api/evidence` —— C 个性化证据（用户直维护）。

C 与 M2 偏好的分界由 `condition` 承担（M2 §4.4 第 3 条）：
**没有适用条件的陈述是偏好，不是证据**。这条约束在三个地方重复出现，
不是冗余而是纵深防御 —— 库层 CHECK、契约校验、判定表结构校验，
任一层被绕过都还有下一层。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_router, get_session, new_ui_intent_id
from app.global_domain import reads
from app.global_domain.schemas import CEvidenceRead, EvidenceCreate
from app.router import Router, SubmitRequest, SubmitResult, UpdateIntent

router = APIRouter(prefix="/api/evidence", tags=["evidence"])

EVIDENCE_TARGET = "C.evidence"


@router.get("", response_model=list[CEvidenceRead])
def list_evidence(
    keyword: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[CEvidenceRead]:
    """C 条目（关键字匹配 condition / statement / effect；MVP 不上向量检索）。"""
    rows = reads.list_active_evidence(session, keyword=keyword, limit=limit)
    return [CEvidenceRead.model_validate(row) for row in rows]


@router.post("", response_model=SubmitResult, status_code=201)
def create_evidence(
    body: EvidenceCreate,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> SubmitResult:
    """写入一条 C 条目（R1 + `stated`）。

    `basis` 记为 `stated` 而不是 `experienced`：用户**主动在界面写下**这条经验，
    其权威性来自「用户声明」（§5.3 的 R1 行），而不是系统的观察。
    写成 `experienced` 会让它需要二次确认 —— 而用户刚刚亲手敲完，
    再弹一次「你确定吗」是把治理成本转嫁给了最不该承担它的人。
    """
    intent = UpdateIntent(
        intent_id=new_ui_intent_id(session),
        action="add_preference",
        target=EVIDENCE_TARGET,
        value={
            "statement": body.statement,
            "condition": body.condition,
            "effect": body.effect,
            "evidence_strength": body.evidence_strength,
            "source": body.source,
        },
        basis="stated",
        reasoning="用户在界面直接写入 C 条目（R1）",
        confidence="high",
    )
    return router_.submit(SubmitRequest(trigger_type="R1", intents=[intent]))
