"""讨论上下文包 —— **采用序由确定性代码决定**（P4 方案 A1，用户裁定）。

这些测试要钉住三件事：

1. **采用序是真的**：节与节的 precedence 与 M0 §7.4 的次序一致；
2. **用户声明永不截断**：30 条硬约束就进 30 条 —— 任何"挑几条"的逻辑
   都等于系统替用户决定他哪句话不重要（M2 红线 2）；
3. **排除了什么必须说得出为什么**：沉默地少给一条，比给错一条更难发现。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.core.ids import next_intent_id
from app.db import DbSequenceStore
from app.global_domain import reads as global_reads
from app.global_domain.enums import TriggerType
from app.router import SubmitRequest, UpdateIntent
from app.session_domain import reads, writes
from app.session_domain.context import PLANNING_VARIABLES, assemble_discussion_context
from app.session_domain.enums import PlanStepStatus, StageItemStatus, StageItemType

from tests.session.test_lifecycle import _live_session


def _intent(db: Session, **kwargs: Any) -> UpdateIntent:
    return UpdateIntent(
        intent_id=next_intent_id(DbSequenceStore(db)),
        reasoning="测试写入",
        confidence="high",
        **kwargs,
    )


def _add_preference(
    db: Session,
    router_,
    statement: str,
    *,
    basis: str = "stated",
    trigger: str = "R1",
):
    """写一条偏好。

    `basis=experienced` 必须配 **R4c**：R1 + experienced 不是"用户已确认"的手势
    （`precheck.USER_CONFIRMED_GESTURES`），因此它会停在待确认队列里而不是生效。
    这条测试里的"软偏好"要的是**已生效**的软偏好，所以用 R4c。
    """
    return router_.submit(
        SubmitRequest(
            trigger_type=trigger,
            intents=[
                _intent(
                    db,
                    action="add_preference",
                    target="M2.preferences",
                    # `priority` / `target_field` 显式传 None 会被契约拒绝
                    # （它们不是 Optional）。不传 = 用默认值。
                    value={"statement": statement},
                    basis=basis,
                )
            ],
        )
    )


def _add_evidence(db: Session, router_, *, statement: str, condition: str):
    return router_.submit(
        SubmitRequest(
            trigger_type=TriggerType.R1,
            intents=[
                _intent(
                    db,
                    action="add_preference",
                    target="C.evidence",
                    value={
                        "statement": statement,
                        "condition": condition,
                        "effect": "提高正确率",
                        "evidence_strength": "medium",
                        "source": "user_direct",
                    },
                    basis="experienced",
                )
            ],
        )
    )


def _add_baseline(db: Session, router_, variable_id: str, score: float = 6.0):
    return router_.submit(
        SubmitRequest(
            trigger_type=TriggerType.R2,
            intents=[
                _intent(
                    db,
                    action="update_parameter",
                    target=f"M1.{variable_id}",
                    value={
                        "channel": "measured",
                        "completeness": "complete",
                        "confidence": "calibrated",
                        "score": score,
                    },
                    basis="measured",
                )
            ],
            source_ref="BENCH-CTX",
        )
    )


def _session_with_plan(db: Session):
    folder, row = _live_session(db)
    plan = writes.create_plan(
        db,
        row.session_id,
        steps=[
            writes.StepDraft(action="通读 unit 1", output="一份生词表"),
            writes.StepDraft(action="精读 unit 1", output="10 个例句"),
        ],
        checkpoints=[
            writes.CheckpointDraft(
                at_step="ST-002", check_method="默写", pass_criteria="正确率 >= 80%"
            )
        ],
        expected_session_version=row.version,
    )
    db.flush()
    return row, plan


# --------------------------------------------------------------------------- #
# 包的结构与采用序
# --------------------------------------------------------------------------- #


def test_pack_carries_the_step_and_its_neighbours(session: Session, router) -> None:
    row, plan = _session_with_plan(session)
    step = reads.plan_steps(session, plan.plan_id)[0]

    pack = assemble_discussion_context(session, row.session_id, step.step_id)

    assert pack.step.step_id == step.step_id
    assert pack.step.action == "通读 unit 1"
    assert pack.step.output == "一份生词表"
    assert pack.plan_version == 1
    assert pack.situation.motivation_type == "mixed"


def test_precedence_numbers_follow_the_context_order(session: Session, router) -> None:
    """用户偏好 > 检查点 > stage > 基线/证据 > 策略库（M0 §7.4 的上下文采用序）。

    检查点是 P6 加进来的一节（用户裁定），排在 stage 之前：
    **验收标准是这一步的硬要求，而 stage 里是还没定的想法。**
    它的加入把后面三节的编号都往后推了一格 —— 这正是这条测试该红的原因。
    """
    row, plan = _session_with_plan(session)
    step = reads.plan_steps(session, plan.plan_id)[0]

    pack = assemble_discussion_context(session, row.session_id, step.step_id)
    by_name = {section.name: section.precedence for section in pack.sections}

    assert by_name["用户偏好（硬约束）"] == 1
    assert by_name["用户偏好（软偏好）"] == 1
    assert by_name["检查点（本步骤的验收标准）"] == 2
    assert by_name["stage（本次想试的）"] == 3
    assert by_name["M1 基线（按通道）"] == 4
    assert by_name["C 个性化证据"] == 4
    assert by_name["M2 策略库"] == 5


def test_precedence_order_is_returned_to_the_caller(session: Session, router) -> None:
    """"为什么这条排在那条前面"要能被界面解释，而不是只写在 docstring 里。"""
    row, plan = _session_with_plan(session)
    step = reads.plan_steps(session, plan.plan_id)[0]
    pack = assemble_discussion_context(session, row.session_id, step.step_id)
    # `precedence_order` 列的是**序**（0–5 共 6 个档位），不是每一节的名字 ——
    # 同档位下有几节（例如硬约束与软偏好都是 1）在这里只出现一次。
    assert len(pack.precedence_order) == 6
    # 资料排在最前（P6+.5，用户裁定「让 chat 支持文件」）——
    # 它排在偏好之前是一个具体的失败形态逼出来的：清单排在后面，
    # 模型可能整节没注意到，于是**从来不去调 read_attachment**，
    # 而现象是"我明明传了它却说没看到"。
    assert pack.precedence_order[0].startswith("0. 会话资料")
    assert pack.precedence_order[1].startswith("1.")
    assert pack.precedence_order[2].startswith("2. 检查点")


def test_hard_constraints_are_never_truncated(session: Session, router) -> None:
    """用户说过的话是约束，不是候选。30 条就进 30 条。"""
    row, plan = _session_with_plan(session)
    for index in range(30):
        _add_preference(session, router, f"约束 {index}：我不用手机背单词")
    session.flush()

    step = reads.plan_steps(session, plan.plan_id)[0]
    pack = assemble_discussion_context(session, row.session_id, step.step_id)

    assert len(pack.constraints) == 30
    assert all(item.hard for item in pack.constraints)
    hard_section = next(s for s in pack.sections if s.name == "用户偏好（硬约束）")
    assert hard_section.dropped_count == 0


def test_soft_preferences_are_marked_as_soft(session: Session, router) -> None:
    row, plan = _session_with_plan(session)
    _add_preference(session, router, "我可能更喜欢看视频", basis="experienced", trigger="R4c")
    session.flush()

    step = reads.plan_steps(session, plan.plan_id)[0]
    pack = assemble_discussion_context(session, row.session_id, step.step_id)

    assert [item.hard for item in pack.soft_preferences] == [False]
    assert "看视频" in pack.soft_preferences[0].statement


def test_only_active_stage_items_enter_and_settled_ones_are_reported(
    session: Session, router
) -> None:
    row, plan = _session_with_plan(session)
    keep = writes.add_stage_item(
        session,
        row.session_id,
        item_type=StageItemType.TEMP_PREF,
        content="想试出声背",
        expected_version=row.version,
    )
    session.flush()
    gone = writes.add_stage_item(
        session,
        row.session_id,
        item_type=StageItemType.IDEA,
        content="这个已经被丢弃",
        expected_version=row.version,
    )
    session.flush()
    writes.settle_stage_item(
        session,
        row.session_id,
        item_id=gone.item_id,
        status=StageItemStatus.DISCARDED,
        expected_version=row.version,
    )
    session.flush()

    step = reads.plan_steps(session, plan.plan_id)[0]
    pack = assemble_discussion_context(session, row.session_id, step.step_id)

    assert [item.item_id for item in pack.stage] == [keep.item_id]
    section = next(s for s in pack.sections if s.name.startswith("stage"))
    assert section.dropped_count == 1
    assert "discarded" in section.dropped_examples[0]


# --------------------------------------------------------------------------- #
# 基线与证据的取舍
# --------------------------------------------------------------------------- #


def test_baseline_keeps_only_planning_variables_and_says_why(session: Session, router) -> None:
    """不参与计划装配的数据不该进包 —— 它只给模型更多编造理由的素材。"""
    row, plan = _session_with_plan(session)
    _add_baseline(session, router, "V-SMP")
    _add_baseline(session, router, "V-SE")  # 自我效能，不在装配集合里
    session.flush()

    step = reads.plan_steps(session, plan.plan_id)[0]
    pack = assemble_discussion_context(session, row.session_id, step.step_id)

    assert [item.variable_id for item in pack.baseline] == ["V-SMP"]
    assert "V-SMP" in PLANNING_VARIABLES
    section = next(s for s in pack.sections if s.name.startswith("M1 基线"))
    assert section.dropped_count == 1
    assert "V-SE" in section.dropped_examples[0]
    assert "不参与计划装配" in section.dropped_examples[0]


def test_baseline_is_one_row_per_channel_never_averaged(session: Session, router) -> None:
    """证据通道纯度：一个变量三条通道就是三行，绝不给一个"综合分"。"""
    row, plan = _session_with_plan(session)
    _add_baseline(session, router, "V-SMP")
    session.flush()

    step = reads.plan_steps(session, plan.plan_id)[0]
    pack = assemble_discussion_context(session, row.session_id, step.step_id)

    assert len(pack.baseline) == 1
    assert pack.baseline[0].channel == "measured"
    assert pack.baseline[0].score == 6.0


def test_evidence_is_filtered_by_the_step_context_and_says_why(
    session: Session, router
) -> None:
    row, plan = _session_with_plan(session)
    session_row = reads.get_session(session, row.session_id)
    _add_evidence(session, router, statement="出声背记得牢", condition="背单词")
    _add_evidence(session, router, statement="练字要慢", condition="写字")
    session.flush()

    step = reads.plan_steps(session, plan.plan_id)[0]
    pack = assemble_discussion_context(session, row.session_id, step.step_id)

    kept = {item.statement for item in pack.evidence}
    assert "出声背记得牢" in kept, f"标题「{session_row.title}」应能匹配到这条证据"
    assert "练字要慢" not in kept
    section = next(s for s in pack.sections if s.name == "C 个性化证据")
    assert section.dropped_count >= 1
    assert any("不匹配" in example for example in section.dropped_examples)


def test_strategies_come_from_the_step_method_when_it_points_at_one(
    session: Session, router
) -> None:
    row, plan = _session_with_plan(session)
    chosen = global_reads.list_strategies(session, limit=1)[0]

    writes.revise_plan(
        session,
        row.session_id,
        steps=[
            writes.StepDraft(
                action="通读 unit 1", output="一份生词表", method_ref=chosen.strategy_id
            ),
            writes.StepDraft(action="精读 unit 1", output="10 个例句"),
        ],
        change="给第一步指定方法",
        reason="想试试这个方法",
        expected_version=plan.version,
    )
    session.flush()

    step = reads.plan_steps(session, plan.plan_id)[0]
    pack = assemble_discussion_context(session, row.session_id, step.step_id)

    assert [item.strategy_id for item in pack.strategies] == [chosen.strategy_id]
    section = next(s for s in pack.sections if s.name == "M2 策略库")
    assert section.dropped_count > 0


# --------------------------------------------------------------------------- #
# 步骤自己的历史
# --------------------------------------------------------------------------- #


def test_step_history_spans_plan_versions(session: Session, router) -> None:
    """「我当初是怎么写的、改了几次」—— 讨论一个计划项时最需要也最容易缺的东西。"""
    row, plan = _session_with_plan(session)
    step_id = reads.plan_steps(session, plan.plan_id)[0].step_id
    writes.mark_step(
        session,
        row.session_id,
        step_id=step_id,
        status=PlanStepStatus.DONE,
        expected_version=plan.version,
    )
    session.flush()

    writes.revise_plan(
        session,
        row.session_id,
        steps=[
            writes.StepDraft(
                action="通读 unit 1（改为出声读）", output="一份生词表", step_id=step_id
            ),
            writes.StepDraft(action="精读 unit 1", output="10 个例句", step_id="ST-002"),
        ],
        change="第一步改成出声读",
        reason="发现不出声记不住",
        expected_version=plan.version,
    )
    session.flush()

    pack = assemble_discussion_context(session, row.session_id, step_id)
    assert [item.version_no for item in pack.step_history] == [1, 2]
    assert pack.step_history[0].action == "通读 unit 1"
    assert pack.step_history[1].action == "通读 unit 1（改为出声读）"
    # 进度跨版本延续，所以两版都是 DONE
    assert {item.status for item in pack.step_history} == {PlanStepStatus.DONE}


# --------------------------------------------------------------------------- #
# 纯度与错误
# --------------------------------------------------------------------------- #


def test_the_pack_is_deterministic(session: Session, router) -> None:
    """同样的数据必然产出同样的包 —— 与调用时间、调用次数无关。"""
    row, plan = _session_with_plan(session)
    _add_preference(session, router, "我不用手机背单词")
    session.flush()
    step = reads.plan_steps(session, plan.plan_id)[0]

    first = assemble_discussion_context(session, row.session_id, step.step_id)
    second = assemble_discussion_context(session, row.session_id, step.step_id)
    assert first == second


def test_unknown_step_is_a_404_with_the_step_id(session: Session, router) -> None:
    row, _plan = _session_with_plan(session)
    with pytest.raises(NotFound) as excinfo:
        assemble_discussion_context(session, row.session_id, "ST-999")
    assert excinfo.value.detail["step_id"] == "ST-999"


def test_a_session_without_a_plan_is_a_404(session: Session, router) -> None:
    _folder, row = _live_session(session)
    with pytest.raises(NotFound):
        assemble_discussion_context(session, row.session_id, "ST-001")
