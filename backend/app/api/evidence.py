"""`/api/evidence` —— C 个性化证据（用户直维护）。

C 与 M2 偏好的分界由 `condition` 承担（M2 §4.4 第 3 条）：
**没有适用条件的陈述是偏好，不是证据**。这条约束在三个地方重复出现，
不是冗余而是纵深防御 —— 库层 CHECK、契约校验、判定表结构校验，
任一层被绕过都还有下一层。

## P7.1：与 `/api/hypotheses` 完全对称的条目生命周期

| 方法 | 路径 | 语义 | 判定 |
|---|---|---|---|
| GET | `/api/evidence` | 列表（含 `include_archived`） | — |
| POST | `/api/evidence` | 新增「我试过这个，有效」 | `R1 + experienced` |
| PUT | `/api/evidence/{id}` | 编辑（整体替换） | `R1 + revise` |
| DELETE | `/api/evidence/{id}` | **退场**（不是物理删除） | `R1 + archive` |
| POST | `/api/evidence/{id}/restore` | 撤销退场 | `R1 + restore` |

**两族逐条同构是刻意的**：它们本来就是把同一条「当 X 时做 Y」记在两个
可信度上，如果生命周期不一样，用户就得记住两套操作 —— 而那种记忆负担
会精确地落在他最不想学的东西上（"为什么假说能删、证据不能"）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.envelope import ItemsResponse, ok
from app.api.deps import get_router, get_session, new_ui_intent_id
from app.global_domain import reads
from app.global_domain.schemas import CEvidenceRead, EvidenceCreate, EvidenceRevise
from app.router import Router, SubmitRequest, SubmitResult, UpdateIntent

router = APIRouter(prefix="/api/evidence", tags=["evidence"])

EVIDENCE_TARGET = "C.evidence"


def _item_target(evidence_id: str) -> str:
    """逐条地址（`C.evidence.E-0001`）—— 归档/改写必须用它，见 hypotheses.py。"""
    return f"{EVIDENCE_TARGET}.{evidence_id}"


@router.get("", response_model=ItemsResponse[CEvidenceRead])
def list_evidence(
    keyword: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=200),
    include_archived: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> dict[str, list[CEvidenceRead]]:
    """C 条目（关键字匹配 condition / statement / effect；MVP 不上向量检索）。

    `include_archived=False` 是默认值，而且**装配侧根本不看这个参数** ——
    `list_active_evidence()` 是装配的唯一入口，它永远只拿 active 的。
    """
    rows = reads.list_active_evidence(
        session, keyword=keyword, limit=limit, include_archived=include_archived
    )
    return ok([CEvidenceRead.model_validate(row) for row in rows])


@router.post("", response_model=SubmitResult, status_code=201)
def create_evidence(
    body: EvidenceCreate,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> SubmitResult:
    """写入一条 C 条目 —— **已验证的经验**（R1 + `experienced`）。

    ## ★ P7 改了这里的 basis：`stated` → `experienced`

    改之前写的是 `stated`，理由是「用户主动在界面写下这条经验，其权威性来自
    用户声明」。那个理由把**两件事**混成了一件：

    - **谁在断言**（用户 vs 系统）—— 决定要不要再确认一次；
    - **断言的是什么**（我试过 vs 我想试）—— 决定它是经验还是假说。

    R1 这条路回答的是前者（用户亲手写的，不用确认），而 `basis` 记录的是后者。
    于是同一个字段被用来表达两件事，而拆格之后它们必须分开：
    `stated` 现在的含义就是"他还没试过"，那是**假说**（`/api/hypotheses`）。

    改写之后，这个端点与假说端点的差别变成**用户点了哪个表单** ——
    界面上一个「记一条已验证的经验」、一个「记一条想试的假说」。
    系统不需要从措辞里猜，模型更不需要。
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
        basis="experienced",
        reasoning="用户在界面直接写入一条已验证的经验（R1）",
        confidence="high",
    )
    return router_.submit(SubmitRequest(trigger_type="R1", intents=[intent]))


@router.put("/{evidence_id}", response_model=SubmitResult)
def revise_evidence(
    evidence_id: str,
    body: EvidenceRevise,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> SubmitResult:
    """编辑一条 C 条目（整体替换）。

    与 `/api/hypotheses/{id}` 的 `PUT` 逐条同构。**改不掉"他改过"这件事** ——
    前后快照都进 RouterLog。
    """
    _require_exists(session, evidence_id)
    intent = UpdateIntent(
        intent_id=new_ui_intent_id(session),
        action="revise",
        target=_item_target(evidence_id),
        value={
            "statement": body.statement,
            "condition": body.condition,
            "effect": body.effect,
            "evidence_strength": body.evidence_strength,
            "source": body.source,
            "reason": body.reason,
        },
        basis="experienced",
        reasoning="用户在界面直接编辑一条已验证的经验（R1）",
        confidence="high",
    )
    return router_.submit(SubmitRequest(trigger_type="R1", intents=[intent]))


@router.delete("/{evidence_id}", response_model=SubmitResult)
def archive_evidence(
    evidence_id: str,
    reason: str | None = Query(default=None),
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> SubmitResult:
    """删除（= **退场**）一条 C 条目。行还在，状态变 `archived`。

    ★ 这是**唯一**能让一条证据停止参与装配的用户动作。在此之前它不存在 ——
    一条被推翻的经验只能躺在库里继续当依据，而那是这份证据库最不该有的行为。
    """
    _require_exists(session, evidence_id)
    intent = UpdateIntent(
        intent_id=new_ui_intent_id(session),
        action="archive",
        target=_item_target(evidence_id),
        value={"reason": reason},
        basis="stated",
        reasoning="用户在界面删除（退场）一条已验证的经验（R1）",
        confidence="high",
    )
    return router_.submit(SubmitRequest(trigger_type="R1", intents=[intent]))


@router.post("/{evidence_id}/restore", response_model=SubmitResult)
def restore_evidence(
    evidence_id: str,
    reason: str | None = Query(default=None),
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> SubmitResult:
    """撤销删除 —— `archived → active`，这条证据重新参与装配。"""
    _require_exists(session, evidence_id)
    intent = UpdateIntent(
        intent_id=new_ui_intent_id(session),
        action="restore",
        target=_item_target(evidence_id),
        value={"reason": reason},
        basis="stated",
        reasoning="用户在界面撤销删除一条已验证的经验（R1）",
        confidence="high",
    )
    return router_.submit(SubmitRequest(trigger_type="R1", intents=[intent]))


def _require_exists(session: Session, evidence_id: str) -> None:
    """不存在的 id → 404（理由见 `hypotheses._require_exists`，逐字相同）。"""
    if reads.find_evidence(session, evidence_id) is None:
        raise HTTPException(status_code=404, detail=f"C 条目不存在：{evidence_id}")
