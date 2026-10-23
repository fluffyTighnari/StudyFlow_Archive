"""P19 简化后的计划 / 动机规则 —— 替代被移除的“手势 + 证据 + 约束覆盖”门槛。

这些测试只钉住产品现在真正承诺的东西：

1. 计划是会话内的黑盒，改它不需要先点某一项、不需要引文、不需要逐条约束；
2. 动机由前置基础 / 价值 / 障碍组成，随时可写，并进入讨论上下文；
3. 步骤产出可空（能写就写，写不了不拦）。
"""

from __future__ import annotations

from app.agent.tools import TOOLS, ToolContext
from app.session_domain import reads as session_reads
from app.session_domain import writes as session_writes
from app.session_domain.context import assemble_discussion_context


def _handler(name: str):
    spec = TOOLS[name]
    assert spec.handler is not None
    return spec.handler


def test_plan_update_needs_no_gesture_or_quote(sandbox) -> None:
    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    before_version = plan.current_version
    steps = session_reads.plan_steps(sandbox.db, plan.plan_id)
    result = _handler("update_task_plan")(
        sandbox.ctx(),  # 没有任何手势
        {
            "diff": {
                "steps": [
                    {"step_id": step.step_id, "action": f"{step.action}（已调整）"}
                    for step in steps
                ]
            }
        },
    )
    assert result.ok, result.message
    after = session_reads.require_plan(sandbox.db, sandbox.session_id)
    assert after.current_version == before_version + 1


def test_create_plan_allows_a_step_without_output(sandbox) -> None:
    folder = session_writes.create_folder(sandbox.db, name="草稿领域", goal=None)
    row = session_writes.create_session(
        sandbox.db,
        folder_id=folder.folder_id,
        title="还没有产出的计划",
        input_goal="先把动作写出来",
    )
    ctx = ToolContext(db=sandbox.db, session_id=row.session_id, folder_id=folder.folder_id)
    result = _handler("create_task_plan")(
        ctx, {"steps": [{"action": "先打开材料，看看有什么"}]}
    )
    assert result.ok, result.message


def test_motivation_foundation_value_obstacles_feed_the_context(sandbox) -> None:
    row = session_reads.get_session(sandbox.db, sandbox.session_id)
    session_writes.update_motivation(
        sandbox.db,
        sandbox.session_id,
        change="补全三项",
        expected_version=row.version,
        foundation="会基础语法，缺听力",
        value="能听懂会议",
        obstacles=["通勤太吵", "晚上容易拖延"],
    )
    motivation = session_reads.get_motivation(sandbox.db, sandbox.session_id)
    assert motivation.foundation == "会基础语法，缺听力"
    assert motivation.value == "能听懂会议"
    assert motivation.obstacles == [
        {"type": "障碍", "desc": "通勤太吵"},
        {"type": "障碍", "desc": "晚上容易拖延"},
    ]

    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    step = session_reads.plan_steps(sandbox.db, plan.plan_id)[0]
    context = assemble_discussion_context(sandbox.db, sandbox.session_id, step.step_id)
    assert context.situation.foundation == "会基础语法，缺听力"
    assert context.situation.value == "能听懂会议"
    assert context.situation.obstacles == motivation.obstacles
