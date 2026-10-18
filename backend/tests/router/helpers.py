"""Router 测试的 intent 构造器。

测试里手写 10 个字段的 `UpdateIntent` 会让「这条测试到底在验什么」被噪声淹没，
因此统一走构造器：只有本次测试真正关心的字段才出现在测试代码里。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.global_domain.enums import TriggerType
from app.global_domain.models import RouterLog
from app.router.contracts import Decision, SubmitRequest, UpdateIntent


def log_for(session: Session, intent_id: str) -> RouterLog:
    """取某条 intent 对应的 RouterLog。

    按 `intent_ref` 查而不是按下标取 `list_router_logs()` —— 后者的排序依赖
    `created_at`，而同一批写入的时间戳可能重合，测试会因此随机失败。
    """
    return session.scalars(
        select(RouterLog).where(RouterLog.intent_ref == intent_id)
    ).one()


def m1_value(
    *,
    channel: str = "measured",
    score: float | None = 7.0,
    completeness: str = "complete",
    confidence: str | None = "provisional",
    boundary: str | None = None,
) -> dict[str, Any]:
    return {
        "channel": channel,
        "score": score,
        "completeness": completeness,
        "confidence": confidence,
        "boundary": boundary,
    }


def pref_value(
    *,
    statement: str = "我喜欢先看例子再看定义",
    priority: int = 7,
    target_field: str | None = None,
    value: str | None = None,
    supersedes_pref_id: str | None = None,
) -> dict[str, Any]:
    return {
        "statement": statement,
        "priority": priority,
        "target_field": target_field,
        "value": value,
        "supersedes_pref_id": supersedes_pref_id,
    }


def c_value(
    *,
    statement: str = "先看例子时我的理解更牢",
    condition: str = "材料是程序性技能",
    effect: str = "优先安排示例先行的材料",
    evidence_strength: str = "strong",
    source: str = "session_derived",
) -> dict[str, Any]:
    return {
        "statement": statement,
        "condition": condition,
        "effect": effect,
        "evidence_strength": evidence_strength,
        "source": source,
    }


def intent(
    intent_id: str = "I-1",
    *,
    action: str = "update_parameter",
    target: str = "M1.V-SE",
    value: Any = None,
    basis: str = "measured",
    priority: int | None = None,
    reasoning: str = "测试理由：本次测量显示该通道取值变化。",
    confidence: str = "medium",
    conflict_note: str | None = None,
) -> UpdateIntent:
    return UpdateIntent(
        intent_id=intent_id,
        action=action,
        target=target,
        value=value,
        priority=priority,
        basis=basis,
        reasoning=reasoning,
        confidence=confidence,
        conflict_note=conflict_note,
    )


def m1_intent(
    intent_id: str = "I-1",
    *,
    variable: str = "V-SE",
    action: str = "update_parameter",
    basis: str = "measured",
    **value_kwargs: Any,
) -> UpdateIntent:
    return intent(
        intent_id,
        action=action,
        target=f"M1.{variable}",
        value=m1_value(**value_kwargs),
        basis=basis,
    )


def pref_intent(
    intent_id: str = "I-1",
    *,
    target: str = "M2.preferences",
    basis: str = "stated",
    **value_kwargs: Any,
) -> UpdateIntent:
    return intent(
        intent_id,
        action="add_preference",
        target=target,
        value=pref_value(**value_kwargs),
        basis=basis,
    )


def evidence_intent(
    intent_id: str = "I-1",
    *,
    basis: str = "experienced",
    **value_kwargs: Any,
) -> UpdateIntent:
    return intent(
        intent_id,
        action="add_preference",
        target="C.evidence",
        value=c_value(**value_kwargs),
        basis=basis,
    )


def trigger(value: TriggerType | str) -> TriggerType:
    """把测试里写的触发器标识规范化为枚举。

    先按**成员名**解析（`"R4A"`、`"R2"`），失败再按**取值**解析（`"R4a"`）——
    因为枚举取值是 `"R4a"` 而人们习惯写 `"R4A"`，测试里混用两种写法是必然的。
    集中在这里，测试代码就不必记住这个细节。
    """
    if isinstance(value, TriggerType):
        return value
    try:
        return TriggerType[value]
    except KeyError:
        return TriggerType(value)


def decision(
    intent_id: str, approved: bool, reason: str | None = None
) -> Decision:
    return Decision(intent_id=intent_id, approved=approved, reason=reason)


def request(
    trigger_type: TriggerType | str = "R2",
    *intents: UpdateIntent,
    source_ref: str | None = None,
    session_ref: str | None = None,
    note: str | None = None,
) -> SubmitRequest:
    return SubmitRequest(
        trigger_type=trigger(trigger_type),
        intents=list(intents),
        source_ref=source_ref,
        session_ref=session_ref,
        note=note,
    )
