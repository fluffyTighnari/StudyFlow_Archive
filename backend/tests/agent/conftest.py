"""工具层的夹具：一个装好文件夹、会话、计划的沙箱。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.agent.tools import (
    GESTURE_CHECKPOINT,
    GESTURE_DEFINITION,
    GESTURE_PLAN_CHANGE,
    GESTURE_STAGE,
    GESTURE_STEP_DONE,
    ToolContext,
)
from app.session_domain import writes as session_writes
from app.session_domain.writes import CheckpointDraft, StepDraft


@pytest.fixture()
def next_id(seeded: Session):
    """跟 `tests/acceptance` 里同名夹具一样：走真实的计数器存储。"""
    from app.core.ids import next_intent_id
    from app.db import DbSequenceStore

    def _next() -> str:
        return next_intent_id(DbSequenceStore(seeded))

    return _next


@pytest.fixture()
def sandbox(seeded: Session, next_id):
    """一个活跃会话 + 一份两步计划 + 一个检查点。"""
    folder = session_writes.create_folder(
        seeded, name="英语学习", goal="能读原版技术文档"
    )
    row = session_writes.create_session(
        seeded,
        folder_id=folder.folder_id,
        title="学习英语单词",
        input_goal="两个月内能读懂技术文档",
    )
    # `draft → active` 要求定义区已经有 goal（没有目标的执行只会记录一大堆
    # 「我不知道要做什么」），所以上面必须传 `input_goal`，否则这一步会 422。
    row = session_writes.transition(
        seeded, row.session_id, target="active", expected_version=row.version,
        reason="用户开始",
    )
    session_writes.create_plan(
        seeded,
        row.session_id,
        steps=[
            StepDraft(action="每天用 Anki 过 50 个新词", output="2000 词的复习记录"),
            StepDraft(action="读一篇技术文档并标注生词", output="标注过的文档一篇"),
        ],
        checkpoints=[
            CheckpointDraft(
                at_step="ST-001",
                check_method="随机抽 20 个词自测",
                pass_criteria="正确率 ≥ 80%",
                fail_action="回到上一批词重刷三天",
            )
        ],
        expected_session_version=row.version,
    )

    def ctx(*gestures: str) -> ToolContext:
        return ToolContext(
            db=seeded,
            session_id=row.session_id,
            folder_id=folder.folder_id,
            user_gestures=frozenset(gestures),
        )

    return SimpleNamespace(
        db=seeded,
        folder=folder,
        session=row,
        session_id=row.session_id,
        ctx=ctx,
        gesture_definition=GESTURE_DEFINITION,
        gesture_step_done=GESTURE_STEP_DONE,
        gesture_plan_change=GESTURE_PLAN_CHANGE,
        gesture_checkpoint=GESTURE_CHECKPOINT,
        gesture_stage=GESTURE_STAGE,
    )
