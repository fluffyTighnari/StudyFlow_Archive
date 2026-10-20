"""`/api/preferences` —— R1 直写路径（M2 偏好）。

「直写」这个词容易误解。R1 **不是**一条绕过治理的捷径：
它同样被翻译成 `UpdateIntent`，同样经过 `Router.submit(trigger=R1)`，
同样落一条 `router_log`。它「直」的只是**不需要 LLM 评估也不需要用户再确认**
（用户自己敲的字就是他自己的声明，§5.3 的 R1 行判定为 auto_apply）。

换句话说：UI 直写与 LLM 提议走的是同一条路，只是前者在判定表上更快通过。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.envelope import ItemsResponse, ok
from app.api.deps import get_router, get_session, new_ui_intent_id
from app.global_domain import reads
from app.global_domain.enums import LifecycleStatus
from app.global_domain.schemas import M2PreferenceRead, PreferenceCreate
from app.router import Router, SubmitRequest, SubmitResult, UpdateIntent

router = APIRouter(prefix="/api/preferences", tags=["preferences"])

#: R1 直写偏好的 target。
#:
#: 注意 `M2Preference.target_field` 只有 `"SP1.*"` 这一类槽位（呈现/活动形态）。
#: 但那**不是「偏好都属于 SP1 软偏好」的意思** —— 这是一处曾经写错过的注释（缺口 A18）：
#: `basis=stated` 的偏好是**用户下达的约束**（M2 红线 2：HC1–HC4 优先于任何软偏好），
#: 只有 `experienced`/`inferred` 的才是可留痕推翻的软偏好。
#: 硬度判定只有一处：`reads.is_hard_constraint()`。
PREFERENCE_TARGET = "M2.preferences"


@router.get("", response_model=ItemsResponse[M2PreferenceRead])
def list_preferences(
    include_archived: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> dict[str, list[M2PreferenceRead]]:
    """偏好列表。默认只给 `active`。

    `include_archived=true` 时给全量 —— 归档不是删除：偏好是**时机性**的，
    而「他三个月前说过什么」本身就是要保留的信息，否则事后无法解释
    **当时为什么那样规划**。

    每条都带 `is_constraint`（派生自 `basis`，判定只在 `reads.is_hard_constraint()`
    一处）：`true` = 用户下达的约束，计划必须满足；`false` = LLM 推断的软偏好，
    可被任务情况推翻但必须留痕（M2 §2.2）。
    """
    rows = reads.list_preferences(
        session, None if include_archived else LifecycleStatus.ACTIVE
    )
    return ok([M2PreferenceRead.model_validate(row) for row in rows])


@router.post("", response_model=SubmitResult, status_code=201)
def create_preference(
    body: PreferenceCreate,
    session: Session = Depends(get_session),
    router_: Router = Depends(get_router),
) -> SubmitResult:
    """写入一条用户偏好（R1）。

    返回的是 `SubmitResult`（提案 + 逐条结果 + 审计日志 id），
    而不是 `M2PreferenceRead` —— 因为**这条写入产生的是一份治理记录**，
    不只是「一个对象」。前端要拿 `router_log_id` 才能跳到审计视图。
    """
    intent = UpdateIntent(
        intent_id=new_ui_intent_id(session),
        action="add_preference",
        target=PREFERENCE_TARGET,
        value={
            "statement": body.statement,
            "priority": body.priority,
            "target_field": body.target_field,
            "value": body.value,
            # 缺口 A20：用户在界面上说「其实我现在能接受精品网课」时，
            # 界面应当把被推翻的那一条带上来 —— 否则新旧两条会同时生效、
            # 同时是硬约束，计划生成时拿到一对互相矛盾的约束。
            "supersedes_pref_id": body.supersedes_pref_id,
        },
        basis="stated",
        reasoning="用户在界面直接写入偏好（R1）",
        confidence="high",
    )
    return router_.submit(SubmitRequest(trigger_type="R1", intents=[intent]))
