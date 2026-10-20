"""`/api/hypotheses` —— 假说：他**想试但还没试**的具体做法（P7 / P7.1）。

## 为什么它和 `/api/evidence` 是两个端点，而不是一个端点加个 `kind`

用户裁定 P7 的那句话是「我不希望用多元功能牺牲准确性」。两个端点看起来
是"多了一个功能"，实际正相反 —— 它是把**一个混淆**拆成两个明确的动作：

| 端点 | 他点它的时候在说什么 | basis |
|---|---|---|
| `POST /api/evidence` | 「我试过这个，有效」 | `experienced` |
| `POST /api/hypotheses` | 「我想试试这个」 | `stated` |

合成一个端点加 `kind=method|hypothesis` 参数，就等于让调用方（将来也包括模型）
**替用户判断他到底试没试过** —— 而那正是本轮要消灭的那种猜。
两个端点各自只装一种东西：填错是无从下手的，不是"要小心"。

## P7.1：完整的条目生命周期

用户原话：「假说应该支持删除啊，我们前面不是说好了偏好/方法这种可以手动编辑吗，
因为**解释权永远在用户手里**」。

于是这里有五条路径，构成一个闭环：

| 方法 | 路径 | 语义 | 判定 |
|---|---|---|---|
| GET | `/api/hypotheses` | 列表（含 `include_archived`） | — |
| POST | `/api/hypotheses` | 新增 | `R1 + stated` → 直接生效 |
| PUT | `/api/hypotheses/{id}` | 编辑（三槽整体替换） | `R1 + revise` → 直接生效 |
| DELETE | `/api/hypotheses/{id}` | **退场**（不是物理删除） | `R1 + archive` → 直接生效 |
| POST | `/api/hypotheses/{id}/restore` | 撤销退场 | `R1 + restore` → 直接生效 |

## ★ 为什么 `DELETE` 不删行

两个理由，都不是洁癖：

1. **I2 会当场失效。** 它要求「已应用的日志必须指向一个真实存在的全局地址」。
   物理删行之后，`router_log.target = C.hypothesis.H-0003` 指向空气 ——
   而那条日志正是"这条假说是他什么时候说的"的唯一证据。
2. **解释权包含反悔。** 一次误点不该让一条认知永久消失于视野。
   有 `restore` 这一格，`delete` 才敢做成"立刻生效、不用确认"。

界面上他看到的是一次删除；系统留下的是一次可撤销的状态变更。**这不是妥协，
这是"删除"在受治理系统里唯一站得住的定义。**

## `PUT` 为什么不是 `PATCH`

见 `HypothesisRevise` 的说明：`condition` 是结构判据，允许"不动它"就等于
允许一条没有场景的假说继续存在。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.envelope import ItemsResponse, ok
from app.api.deps import get_router, get_session, new_ui_intent_id
from app.global_domain import reads
from app.global_domain.schemas import HypothesisCreate, HypothesisRead, HypothesisRevise
from app.router import Router, SubmitRequest, SubmitResult, UpdateIntent

router = APIRouter(prefix="/api/hypotheses", tags=["hypotheses"])

#: 与 `app.global_domain.targets.C_HYPOTHESIS_ROOT` 同值。
#: 这里写字符串字面量而不是 import 那个常量，与 `evidence.py` 的
#: `EVIDENCE_TARGET = "C.evidence"` 保持同一种写法 —— 让"这个端点打在哪"
#: 在文件里一眼可见。
HYPOTHESIS_TARGET = "C.hypothesis"


def _item_target(hypothesis_id: str) -> str:
    """逐条地址（`C.hypothesis.H-0001`）。

    ★ 归档/改写**必须**用它，不能只用集合根。policy 会拒绝集合根，
    但这里就不该给出集合根 —— 错误应该在离调用方最近的地方被阻止，
    而不是等他拿到一句"对一整类东西归档没有确定的含义"。
    """
    return f"{HYPOTHESIS_TARGET}.{hypothesis_id}"


@router.get("", response_model=ItemsResponse[HypothesisRead])
def list_hypotheses(
    keyword: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=200),
    include_archived: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> dict[str, list[HypothesisRead]]:
    """他想试的做法（关键字匹配 condition / statement / effect）。

    ★ 这个列表就是「我想试试之前的猜想」那句话的来源 —— 他翻得到，
    模型也翻得到（`retrieve_cognitive(scope="hypothesis")`）。
    """
    rows = reads.list_hypotheses(
        session,
        keyword=keyword,
        limit=limit,
        include_archived=include_archived,
    )
    return ok([HypothesisRead.model_validate(row) for row in rows])


@router.post("", response_model=SubmitResult, status_code=201)
def create_hypothesis(
    body: HypothesisCreate,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> SubmitResult:
    """写入一条假说（R1 + `stated`）—— 立即生效。

    `R1 + stated` 在判定表里是 `auto_apply`：他亲手在界面写下「我想试试 X」，
    **没有第二个人需要再确认一次**。这正是"完全由用户支配"的具体含义 ——
    不是"用户能覆盖模型"，而是"用户写的不用问模型"。
    """
    intent = UpdateIntent(
        intent_id=new_ui_intent_id(session),
        action="add_preference",
        target=HYPOTHESIS_TARGET,
        value={
            "statement": body.statement,
            "condition": body.condition,
            "effect": body.effect,
        },
        basis="stated",
        reasoning="用户在界面直接写入一条想试的做法（R1）",
        confidence="high",
    )
    return router_.submit(SubmitRequest(trigger_type="R1", intents=[intent]))


@router.put("/{hypothesis_id}", response_model=SubmitResult)
def revise_hypothesis(
    hypothesis_id: str,
    body: HypothesisRevise,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> SubmitResult:
    """编辑一条假说（三个槽整体替换）。

    ★ **它能改，但改不掉「他改过」这件事**：改动前后的完整快照都写进
    `router_log.old_value` / `new_value`，所以"为什么这件事后来不一样了"
    永远有一个可回答的出处。

    ★ 改写**不会**把假说变成证据 —— `basis` 依然只能是 `stated`。
    试过了就该写成 `C.evidence`，那是另一张表。
    """
    _require_exists(session, hypothesis_id)
    intent = UpdateIntent(
        intent_id=new_ui_intent_id(session),
        action="revise",
        target=_item_target(hypothesis_id),
        value={
            "statement": body.statement,
            "condition": body.condition,
            "effect": body.effect,
            "reason": body.reason,
        },
        basis="stated",
        reasoning="用户在界面直接编辑一条想试的做法（R1）",
        confidence="high",
    )
    return router_.submit(SubmitRequest(trigger_type="R1", intents=[intent]))


@router.delete("/{hypothesis_id}", response_model=SubmitResult)
def archive_hypothesis(
    hypothesis_id: str,
    reason: str | None = Query(default=None),
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> SubmitResult:
    """删除（= **退场**）一条假说。**行还在**，状态变 `archived`。

    ## 为什么理由走 query 参数而不是请求体

    `DELETE` 带请求体在 HTTP 里是灰色地带（很多客户端会丢掉它，某些代理会拒绝）。
    理由是可选的补充信息，放在 query 里最不容易被静默吞掉 ——
    而被吞掉的恰恰会是"他当时为什么删它"。

    ## 为什么不返回 204

    因为这次操作**留下了东西**：一条 RouterLog，以及一个可以撤销的状态。
    返回 204 会让调用方以为"这条已经不存在了"，而它其实还在 ——
    只是不在默认列表里。返回 `SubmitResult` 让他能看见日志 id 与状态变化。
    """
    _require_exists(session, hypothesis_id)
    intent = UpdateIntent(
        intent_id=new_ui_intent_id(session),
        action="archive",
        target=_item_target(hypothesis_id),
        value={"reason": reason},
        basis="stated",
        reasoning="用户在界面删除（退场）一条想试的做法（R1）",
        confidence="high",
    )
    return router_.submit(SubmitRequest(trigger_type="R1", intents=[intent]))


@router.post("/{hypothesis_id}/restore", response_model=SubmitResult)
def restore_hypothesis(
    hypothesis_id: str,
    reason: str | None = Query(default=None),
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> SubmitResult:
    """撤销删除 —— `archived → active`。

    ## 为什么这个端点必须是"独立的一条路径"而不是 `DELETE ... ?undo=true`

    因为**两件事的方向相反**，而一个带布尔开关的端点会让"到底想干哪个"
    取决于一个容易被默认值决定的参数。误删之后点恢复，是用户在救自己 ——
    这条路径不该有任何可以传错的参数。
    """
    _require_exists(session, hypothesis_id)
    intent = UpdateIntent(
        intent_id=new_ui_intent_id(session),
        action="restore",
        target=_item_target(hypothesis_id),
        value={"reason": reason},
        basis="stated",
        reasoning="用户在界面撤销删除一条想试的做法（R1）",
        confidence="high",
    )
    return router_.submit(SubmitRequest(trigger_type="R1", intents=[intent]))


def _require_exists(session: Session, hypothesis_id: str) -> None:
    """不存在的 id → **404**，而不是落一条 `rejected` 日志。

    这条分界与 `/api/preferences` 的 `supersedes_pref_id` 相反，而且是有意的：
    那里 `P-9999` 是一个**用户填的字段**，他说出一个不存在的 id 是一种
    "他以为还在"的真实状态，值得留痕；这里是**路径参数**，404 就已经说清了
    "这条不存在"，再落一条日志只会让审计表里堆满 404 的回声。
    """
    if reads.find_hypothesis(session, hypothesis_id) is None:
        raise HTTPException(status_code=404, detail=f"假说不存在：{hypothesis_id}")
