"""沉淀候选的分类 —— **沙箱里哪些东西是关于"你"的，哪些只关于"这次任务"**。

这是本模块最值得钉死的一件事：把计划调整沉淀成「我的特质」，
会让个人认知基线慢慢变成一个错误的自我描述。因此"排除"不是失败路径，
它和"沉淀"一样重要，而且必须带理由。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.errors import InvalidField, MissingPrerequisite
from app.global_domain.enums import Basis, TriggerType
from app.session_domain import reads, writes
from app.session_domain.enums import StageItemStatus, StageItemType
from app.session_domain.sedimentation import build_candidates

from tests.session.test_lifecycle import _live_session


def _stage_all_types(db: Session, session_id: str):
    """建三条 stage 条目，各一种类型。返回 `{type: item}`。

    每条都**现读版本号**：`add_stage_item` 会 +1 `session.version`，
    拿一开始那个版本连写三次必然撞 409 —— 这正是乐观锁该有的行为。
    """
    items = {}
    for item_type, content in (
        (StageItemType.TEMP_PREF, "我想试试出声背"),
        (StageItemType.IDEA, "先过一遍再精读"),
        (StageItemType.PLAN_OVERRIDE, "把第 2 步拆成两步"),
    ):
        items[item_type] = writes.add_stage_item(
            db,
            session_id,
            item_type=item_type,
            content=content,
            expected_version=reads.get_session(db, session_id).version,
        )
        db.flush()
    return items


# --------------------------------------------------------------------------- #
# R4c：用户勾选
# --------------------------------------------------------------------------- #


def test_temp_pref_becomes_a_preference_candidate(session: Session) -> None:
    _folder, row = _live_session(session)
    items = _stage_all_types(session, row.session_id)

    plan = build_candidates(
        session,
        row.session_id,
        trigger=TriggerType.R4C,
        stage_item_ids=[items[StageItemType.TEMP_PREF].item_id],
    )

    assert plan.trigger is TriggerType.R4C
    assert len(plan.included) == 1
    candidate = plan.included[0]
    assert candidate.target == "M2.preferences"
    assert candidate.action == "add_preference"
    assert candidate.statement == "我想试试出声背"
    # A21：(R4C, ADD_PREFERENCE, EXPERIENCED) 是"用户已确认"的手势之一 ——
    # 勾选本身就是确认动作。
    assert candidate.proposed_basis is Basis.EXPERIENCED
    assert plan.excluded == []


def test_idea_and_plan_override_are_excluded_with_a_reason(session: Session) -> None:
    """计划调整沉淀成「我的特质」，会让认知基线变成一个错误的自我描述。"""
    _folder, row = _live_session(session)
    items = _stage_all_types(session, row.session_id)

    plan = build_candidates(
        session,
        row.session_id,
        trigger=TriggerType.R4C,
        stage_item_ids=[
            items[StageItemType.IDEA].item_id,
            items[StageItemType.PLAN_OVERRIDE].item_id,
        ],
    )

    assert plan.included == []
    assert len(plan.excluded) == 2
    for excluded in plan.excluded:
        assert "计划修订" in excluded.reason
        assert excluded.source_ref.startswith("STG-")


def test_settled_stage_item_cannot_be_sedimented_twice(session: Session) -> None:
    _folder, row = _live_session(session)
    items = _stage_all_types(session, row.session_id)
    item = items[StageItemType.TEMP_PREF]
    writes.settle_stage_item(
        session,
        row.session_id,
        item_id=item.item_id,
        status=StageItemStatus.DISCARDED,
        expected_version=row.version,
    )

    with pytest.raises(MissingPrerequisite):
        build_candidates(
            session,
            row.session_id,
            trigger=TriggerType.R4C,
            stage_item_ids=[item.item_id],
        )


def test_stage_item_of_another_session_is_rejected(session: Session) -> None:
    folder = writes.create_folder(session, name="英语学习")
    first = writes.create_session(
        session, folder_id=folder.folder_id, title="背单词", input_goal="背完 1500 词"
    )
    second = writes.create_session(
        session, folder_id=folder.folder_id, title="学翻译", input_goal="译完一篇短文"
    )
    session.flush()
    writes.transition(session, first.session_id, target="active", expected_version=1)
    session.flush()
    item = writes.add_stage_item(
        session,
        first.session_id,
        item_type=StageItemType.TEMP_PREF,
        content="属于第一个会话",
        expected_version=first.version,
    )
    session.flush()

    with pytest.raises(InvalidField):
        build_candidates(
            session,
            second.session_id,
            trigger=TriggerType.R4C,
            stage_item_ids=[item.item_id],
        )


def test_r4c_without_ids_is_refused(session: Session) -> None:
    _folder, row = _live_session(session)
    with pytest.raises(MissingPrerequisite):
        build_candidates(session, row.session_id, trigger=TriggerType.R4C)
    # 空列表同样拒绝：它不表示"没有选中"，它表示调用方没告诉我们勾了什么。
    with pytest.raises(MissingPrerequisite):
        build_candidates(
            session, row.session_id, trigger=TriggerType.R4C, stage_item_ids=[]
        )


# --------------------------------------------------------------------------- #
# R4a：完成总结
# --------------------------------------------------------------------------- #


def test_findings_default_to_inferred_because_a_summary_is_an_inference(
    session: Session,
) -> None:
    """缺省 `inferred` 而不是 `experienced`：`inferred` 永远需要确认（A21），
    所以"少声明一点"是安全方向。反过来标成 experienced 会触发自动生效 ——
    而总结根本不是用户选的。
    """
    _folder, row = _live_session(session)
    writes.write_summary(
        session,
        row.session_id,
        content="总结",
        findings=[{"statement": "我更适合短时段多次"}],
    )
    session.flush()

    plan = build_candidates(session, row.session_id, trigger=TriggerType.R4A)
    assert len(plan.included) == 1
    assert plan.included[0].proposed_basis is Basis.INFERRED


def test_finding_can_carry_its_own_basis(session: Session) -> None:
    _folder, row = _live_session(session)
    writes.write_summary(
        session,
        row.session_id,
        content="总结",
        findings=[{"statement": "我讨厌看网课", "basis": "stated"}],
    )
    session.flush()

    plan = build_candidates(session, row.session_id, trigger=TriggerType.R4A)
    assert plan.included[0].proposed_basis is Basis.STATED


def test_a_variable_finding_without_a_channel_is_excluded(session: Session) -> None:
    """没有通道的分数会被迫在展示时合成一个平均值 —— 那正是被禁止的事（M0 §6.5）。"""
    _folder, row = _live_session(session)
    writes.write_summary(
        session,
        row.session_id,
        content="总结",
        findings=[{"statement": "自控力好像变强了", "variable_id": "V-SC", "score": 7.0}],
    )
    session.flush()

    plan = build_candidates(session, row.session_id, trigger=TriggerType.R4A)
    assert plan.included == []
    assert len(plan.excluded) == 1
    assert "证据通道" in plan.excluded[0].reason


def test_a_variable_finding_with_a_channel_targets_the_m1_namespace(
    session: Session,
) -> None:
    _folder, row = _live_session(session)
    writes.write_summary(
        session,
        row.session_id,
        content="总结",
        findings=[
            {
                "statement": "实测自控力 7 分",
                "variable_id": "V-SC",
                "channel": "measured",
                "score": 7.0,
            }
        ],
    )
    session.flush()

    plan = build_candidates(session, row.session_id, trigger=TriggerType.R4A)
    candidate = plan.included[0]
    assert candidate.target == "M1.V-SC"
    assert candidate.action == "update_parameter"
    assert candidate.value["channel"] == "measured"
    assert candidate.value["score"] == 7.0
    # 单次总结只覆盖一个通道的一个侧面 → 不完整是**默认**，不是特例。
    assert candidate.value["completeness"] == "partial"


def test_empty_statement_is_excluded_not_silently_dropped(session: Session) -> None:
    _folder, row = _live_session(session)
    writes.write_summary(
        session,
        row.session_id,
        content="总结",
        findings=[{"statement": "   "}, {"statement": "这条是好的"}],
    )
    session.flush()

    plan = build_candidates(session, row.session_id, trigger=TriggerType.R4A)
    assert [c.statement for c in plan.included] == ["这条是好的"]
    assert len(plan.excluded) == 1
    assert "statement" in plan.excluded[0].reason


def test_r4a_without_a_summary_is_refused(session: Session) -> None:
    _folder, row = _live_session(session)
    with pytest.raises(MissingPrerequisite):
        build_candidates(session, row.session_id, trigger=TriggerType.R4A)


# --------------------------------------------------------------------------- #
# R4b：里程碑
# --------------------------------------------------------------------------- #


def test_a_met_milestone_becomes_an_experienced_observation(session: Session) -> None:
    _folder, row = _live_session(session)
    milestone = writes.add_milestone(session, row.session_id, title="背完 unit 1")
    session.flush()
    writes.complete_milestone(
        session, row.session_id, milestone_id=milestone.milestone_id, results="80%"
    )
    session.flush()

    plan = build_candidates(
        session,
        row.session_id,
        trigger=TriggerType.R4B,
        milestone_ids=[milestone.milestone_id],
    )
    assert len(plan.included) == 1
    assert plan.included[0].proposed_basis is Basis.EXPERIENCED
    assert "80%" in plan.included[0].statement


def test_an_unmet_milestone_is_excluded(session: Session) -> None:
    """没发生的事不能成为证据。"""
    _folder, row = _live_session(session)
    milestone = writes.add_milestone(session, row.session_id, title="背完 unit 2")
    session.flush()

    plan = build_candidates(
        session,
        row.session_id,
        trigger=TriggerType.R4B,
        milestone_ids=[milestone.milestone_id],
    )
    assert plan.included == []
    assert "还没达成" in plan.excluded[0].reason


# --------------------------------------------------------------------------- #
# 触发类型与纯度
# --------------------------------------------------------------------------- #


def test_only_r4_triggers_can_sediment(session: Session) -> None:
    _folder, row = _live_session(session)
    for trigger in (TriggerType.R1, TriggerType.R2, TriggerType.R3):
        with pytest.raises(InvalidField):
            build_candidates(session, row.session_id, trigger=trigger)


def test_building_candidates_writes_nothing(session: Session) -> None:
    """候选组装是纯读取。它**不认识 Router**（ARCH-11），因此也不可能顺手写全局。"""
    _folder, row = _live_session(session)
    items = _stage_all_types(session, row.session_id)
    before_version = row.version
    before_status = reads.get_stage_item(
        session, items[StageItemType.TEMP_PREF].item_id
    ).status

    build_candidates(
        session,
        row.session_id,
        trigger=TriggerType.R4C,
        stage_item_ids=[items[StageItemType.TEMP_PREF].item_id],
    )

    assert row.version == before_version
    assert (
        reads.get_stage_item(session, items[StageItemType.TEMP_PREF].item_id).status
        is before_status
    )
