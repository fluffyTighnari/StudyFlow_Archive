"""共享测试夹具。

注意：测试位于 `app/` 之外，因此**不受**架构依赖规则约束——测试可以自由
import 写入口与 ORM 模型，这是有意为之（否则无法验证它们）。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

#: **在导入 app 之前**关掉 `.env` 读取。
#:
#: 理由不是洁癖：开发者本机的 `.env` 里有一把**真密钥**
#: （这个项目的 `.env` 就是那样）。如果测试会读到它，会有两个后果：
#: ① 同一份代码在不同机器上结果不同（"我这儿是绿的"）；
#: ② 某个断言"没有密钥就该报错"的测试会在这台机器上莫名其妙地红。
#: 更糟的是有一天有人写了个测试把密钥打进日志。
#:
#: 想测 `.env` 本身时，`tests/test_env_file.py` 用显式路径调 `load_env(paths=...)`，
#: 不依赖这个开关。
os.environ.setdefault("MOUXUE_SKIP_ENV_FILE", "1")

if TYPE_CHECKING:  # pragma: no cover
    from fastapi.testclient import TestClient

# 必须 import 以使所有表注册到 Base.metadata。
# 两个域都要：`create_all` 只建已注册的表，漏一个域的模型会让一半的表悄悄不存在，
# 而"表不存在"在这里表现为一堆与真正原因无关的 `no such table`。
import app.global_domain.models  # noqa: F401
import app.session_domain.models  # noqa: F401
from app.core import capability
from app.db import Base, make_engine, make_session_factory


@pytest.fixture()
def engine() -> Iterator[Engine]:
    eng = make_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    factory = make_session_factory(engine)
    db = factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _isolated_token_registry() -> Iterator[None]:
    """令牌注册表是进程级状态，逐测试隔离，避免跨用例串味。"""
    capability.reset_registry_for_tests()
    try:
        yield
    finally:
        capability.reset_registry_for_tests()


@pytest.fixture()
def token():
    """有效令牌——模拟 Router 内部签发。"""
    from app.router.capability import new_token

    return new_token()


# --------------------------------------------------------------------------- #
# HTTP 层夹具（`tests/api` 与 `tests/acceptance` 共用）
# --------------------------------------------------------------------------- #


@pytest.fixture()
def app():
    """每个测试一个全新的内存库：lifespan 会建表并装种子。"""
    from app.config import Settings
    from app.main import create_app

    return create_app(
        Settings(database_url="sqlite+pysqlite:///:memory:", seed_on_startup=True)
    )


@pytest.fixture()
def client(app) -> Iterator["TestClient"]:
    """`with` 是必须的 —— 只有进入上下文才会跑 lifespan（建表 + 装种子）。"""
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def direct(app, client):
    """在与 `client` **同一个内存库**上直接调用 Router，用于铺前置状态。

    刻意**不**提供长生命周期的 Session fixture：`:memory:` 走 StaticPool，
    所有 Session 共用同一条 DBAPI 连接；跨 `client` 调用持有一个未结束的事务
    会与请求自己的事务互相踩踏。因此每次调用开一个短 Session 并立即提交。

    这也让测试更诚实：铺数据走的是与生产完全相同的那条路径（Router），
    而不是直接 `session.add()` 造行 —— 后者会掩盖「这条数据本不该存在」这类问题。
    """
    from types import SimpleNamespace
    from typing import Any

    from app.core.ids import next_intent_id
    from app.db import DbSequenceStore, session_scope
    from app.router import Router, SubmitRequest, UpdateIntent

    def submit(request: SubmitRequest):
        with session_scope(app.state.session_factory) as session:
            return Router(session).submit(request)

    def baseline(
        variable_id: str = "V-SE",
        channel: str = "measured",
        score: float = 3.0,
        *,
        basis: str = "measured",
        trigger: str = "R2",
        source_ref: str | None = "BENCH-1",
        action: str = "update_parameter",
    ):
        """写一条 M1 基线版本（默认走 R2 + measured，判定表里是 auto_apply）。"""
        with session_scope(app.state.session_factory) as session:
            value: dict[str, Any] = {
                "channel": channel,
                "completeness": "complete",
                "confidence": "calibrated",
                "boundary": None,
            }
            if action == "update_parameter":
                value["score"] = score
            intent = UpdateIntent(
                intent_id=next_intent_id(DbSequenceStore(session)),
                action=action,
                target=f"M1.{variable_id}",
                value=value,
                basis=basis,
                reasoning="测试前置：基准测试结果",
                confidence="high",
            )
            return Router(session).submit(
                SubmitRequest(
                    trigger_type=trigger, intents=[intent], source_ref=source_ref
                )
            )

    return SimpleNamespace(submit=submit, baseline=baseline)


@pytest.fixture()
def seeded(session: Session) -> Session:
    """装好目录数据（M1 11 变量 + 33 通道 + M2-B 16 策略）的 Session。"""
    from app.seeds.loader import load_all

    load_all(session)
    session.flush()
    return session


@pytest.fixture()
def router(seeded: Session):
    from app.router import Router

    return Router(seeded)


#: P19 规则变更后**已废弃**的旧门槛测试：计划修改不再要求手势 / 引文 /
#: 证据 / 逐条约束覆盖；动机现在全状态可写。它们断言的是一套已被产品
#: 明确移除的规则，保留会让 `pytest` 把“旧政策”误报成回归。
#: 新规则由 `tests/session/test_guards.py` 与 `tests/agent/test_simplified_rules.py` 覆盖。
_OBSOLETE_PLAN_GATE_TESTS: frozenset[str] = frozenset(
    {
        "test_plan_cannot_be_changed_without_recorded_evidence",
        "test_the_users_own_words_are_enough_to_change_the_plan",
        "test_a_quote_the_user_never_said_cannot_change_the_plan",
        "test_a_quote_shorter_than_four_characters_is_not_a_quote",
        "test_a_failed_checkpoint_is_enough_evidence_to_change_the_plan",
        "test_propose_plan_change_also_needs_the_user_to_have_asked",
        "test_propose_plan_change_refuses_a_quote_the_user_never_said",
        "test_the_plan_change_gesture_unlocks_propose_plan_change",
        "test_propose_plan_change_requires_a_verdict_for_every_hard_constraint",
        "test_update_task_plan_also_requires_a_verdict_for_every_hard_constraint",
        "test_propose_plan_change_refuses_when_a_constraint_is_violated",
        "test_a_stage_item_is_enough_evidence_to_change_the_plan",
        "test_create_plan_rejects_steps_without_output",
        "test_without_a_click_or_his_words_the_agent_cannot_change_the_plan",
        "test_a_failed_checkpoint_routes_to_the_model_initiated_tool",
        "test_a_click_alone_is_enough_for_the_model_initiated_tool",
    }
)


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.name in _OBSOLETE_PLAN_GATE_TESTS:
            item.add_marker(
                pytest.mark.skip(
                    reason="P19：计划修改不再要求手势/引文/证据/约束覆盖（规则已变更）"
                )
            )
