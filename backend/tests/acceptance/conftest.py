"""验收套件的夹具：在**同一个 Session** 上直接驱动 Router。

与 `tests/api` 不同，这里刻意不走 HTTP：验收标准要断言的是**治理语义**
（日志与意图一一对应、历史行不可变、不变式可被破坏后被抓到），
而不是「某个端点的响应长什么样」—— 后者已经由 `tests/api` 覆盖。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.ids import next_intent_id
from app.db import DbSequenceStore
from app.router import Router, SubmitRequest, SubmitResult, UpdateIntent


@pytest.fixture()
def next_id(seeded: Session) -> Callable[[], str]:
    def _next() -> str:
        return next_intent_id(DbSequenceStore(seeded))

    return _next


def _m1_value(
    channel: str = "measured",
    score: float = 3.0,
    *,
    confidence: str = "calibrated",
) -> dict[str, Any]:
    return {
        "channel": channel,
        "score": score,
        "completeness": "complete",
        "confidence": confidence,
        "boundary": None,
    }


@pytest.fixture()
def write_baseline(
    seeded: Session, router: Router, next_id: Callable[[], str]
) -> Callable[..., SubmitResult]:
    """经 Router 写一条 M1 基线版本。默认 R2 + measured（auto_apply）。"""

    def _write(
        variable_id: str = "V-SE",
        channel: str = "measured",
        score: float = 3.0,
        *,
        basis: str = "measured",
        trigger: str = "R2",
        source_ref: str | None = "RUN-A",
        confidence: str = "calibrated",
    ) -> SubmitResult:
        intent = UpdateIntent(
            intent_id=next_id(),
            action="update_parameter",
            target=f"M1.{variable_id}",
            value=_m1_value(channel, score, confidence=confidence),
            basis=basis,
            reasoning="验收前置：基准测试结果",
            confidence="high",
        )
        result = router.submit(
            SubmitRequest(
                trigger_type=trigger, intents=[intent], source_ref=source_ref
            )
        )
        assert result.results[0].status.value == "applied", result.results[0].reason
        return result

    return _write


@pytest.fixture()
def write_preference(
    seeded: Session, router: Router, next_id: Callable[[], str]
) -> Callable[..., SubmitResult]:
    def _write(
        statement: str = "先看例子再看定义",
        *,
        trigger: str = "R1",
        basis: str = "stated",
        priority: int = 5,
        target_field: str | None = None,
        supersedes_pref_id: str | None = None,
    ) -> SubmitResult:
        value: dict[str, Any] = {"statement": statement, "priority": priority}
        if target_field is not None:
            value["target_field"] = target_field
        if supersedes_pref_id is not None:
            value["supersedes_pref_id"] = supersedes_pref_id
        intent = UpdateIntent(
            intent_id=next_id(),
            action="add_preference",
            target="M2.preferences",
            value=value,
            basis=basis,
            reasoning="验收前置：用户声明",
            confidence="high",
        )
        return router.submit(SubmitRequest(trigger_type=trigger, intents=[intent]))

    return _write


@pytest.fixture()
def write_observation(seeded: Session) -> Callable[..., None]:
    """直接写一条通道观测。

    这里**故意绕过 Router** —— 不是为了抄近路，而是因为 P4 之前还没有
    「Session 记录观测」这条治理路径（缺口 B7）。I6 要校验的正是
    「一条声明为证据的观测能不能被归因」，所以夹具必须能造出**坏的那一条**。
    """
    from app.global_domain.enums import ChannelObsSourceKind
    from app.global_domain.writes import append_channel_obs
    from app.router.capability import new_token

    def _write(
        *,
        kind: ChannelObsSourceKind = ChannelObsSourceKind.SESSION,
        source_ref: str | None = "S-001",
        variable_id: str = "V-SE",
        channel: str = "experienced",
    ) -> None:
        append_channel_obs(
            new_token(),
            seeded,
            variable_id=variable_id,
            channel=channel,
            value=3.0,
            source_kind=kind,
            source_ref=source_ref,
            note=None,
        )

    return _write
