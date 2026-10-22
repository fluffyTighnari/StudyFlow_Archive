"""`ask_about_task` 走一遍循环（工具 → 写库 → 回答 → 放行）。

`tests/session/test_clarify.py` 验的是规则，`tests/api/test_clarify_api.py`
验的是接线。**这里验的是"模型真的会问"这条路上的一环**：
循环把工具调用派下来、作用域检查放行、结果是 `ok` 而不是 `rejected`。

★ 为什么值得单独写：`ask_about_task` 是一个**新增的会话写工具**，
而"新增工具忘了加进作用域"这类错在单测里不会出现 ——
它只在派发那一刻才暴露（`rejected: 这个工具需要会话`）。
"""

from __future__ import annotations

from app.agent.llm import FakeLLM, call, reply
from app.agent.loop import AgentLoop
from app.agent.scripted import CLARIFY_TRIGGER, clarification_reply_for
from app.session_domain import reads as session_reads
from app.session_domain import writes as session_writes
from app.session_domain.enums import ClarificationStatus
from app.session_domain.writes import StepDraft


def _loop(sandbox, llm) -> AgentLoop:
    return AgentLoop(sandbox.db, sandbox.session_id, llm, max_steps=8)


def _tools(names: list[str]):
    from app.agent.tools import get_tool

    return [get_tool(name).schema() for name in names if get_tool(name) is not None]


def test_the_scripted_model_asks_instead_of_guessing() -> None:
    """替身只在**用户说了那句话**时反问，别的时候照旧改计划。"""
    from app.agent.llm import ChatMessage

    asked = clarification_reply_for(
        [ChatMessage(role="user", content=f"{CLARIFY_TRIGGER}，先帮我理一下")],
        _tools(["ask_about_task"]),
    )
    assert asked is not None
    assert [c.name for c in asked.tool_calls] == ["ask_about_task"]
    assert asked.tool_calls[0].arguments["slot"] == "goal"

    # ① 别的话题：不问
    assert (
        clarification_reply_for(
            [ChatMessage(role="user", content="一天 50 个新词太多了")],
            _tools(["ask_about_task"]),
        )
        is None
    )
    # ② 工具不在（侧边栏那种没有会话的场景）：不问 —— 否则它会调一个用不了的工具
    assert (
        clarification_reply_for(
            [ChatMessage(role="user", content=CLARIFY_TRIGGER)],
            _tools(["retrieve_folder"]),
        )
        is None
    )


def test_the_tool_writes_a_question_and_tells_the_model_what_it_changes(
    sandbox,
) -> None:
    """★ 政策变更：这个工具**不再挡住计划**，它改变的是计划的署名。

    旧断言（`"计划改不了" in trace[0].message`）与旧文案是配套的：
    那时缺 `goal` 时 `plan_gate` 会拒，所以"问了就等于锁住计划"是真的。
    现在不再拒 —— 计划照排，但理由里会被追加"这是按助手判断排的"。
    所以模型该被告知的是**这件事**：不必等答案，可他排的东西会被标记。

    关键在于指出这句话**为什么必须跟着改**：那句"计划改不了"是
    **模型读到的、关于系统行为的陈述**。它一旦不真，模型就会按一个
    不存在的规则工作（以为要等答案、或者以为自己越权了）——
    而"系统用一个理由解释自己的行为，而那个理由不是真的"正是本项目
    反复栽的那一类。
    """
    llm = FakeLLM(
        [
            reply(
                "我先问一句。",
                call(
                    "ask_about_task",
                    slot="success_criteria",
                    question="你希望我怎么算这一章算过了？",
                    options=["能自己推导", "能复述结论"],
                ),
            ),
            reply("好，那我等你回答。"),
        ]
    )
    result = _loop(sandbox, llm).run("开始吧")

    trace = list(result.traces)
    assert [entry.tool for entry in trace] == ["ask_about_task"]
    assert trace[0].status == "success", trace[0].message
    # 真话：它不挡计划，但它决定署名
    assert "按助手判断排的" in trace[0].message
    assert "不挡住改计划" in trace[0].message
    assert "计划改不了" not in trace[0].message

    rows = session_reads.list_clarifications(sandbox.db, sandbox.session_id)
    assert len(rows) == 1
    assert rows[0].slot == "success_criteria"
    assert rows[0].status is ClarificationStatus.OPEN
    assert rows[0].options == ["能自己推导", "能复述结论"]


def test_asking_about_a_filled_slot_is_reported_as_rejected_not_as_an_error(
    sandbox,
) -> None:
    """★ 形状不对（422）与策略拒绝要分开，但它们对模型都必须是 `rejected` + 一句人话。

    这里的 `goal` 在沙箱里**已经有内容**，所以工具会拒绝。
    这条测试盯的是：它拒绝得**说得清楚**，而不是抛一个 500 出去。
    """
    llm = FakeLLM(
        [
            reply("我先问一句。", call("ask_about_task", slot="goal", question="要达成什么？")),
            reply("好，那我不问了。"),
        ]
    )
    result = _loop(sandbox, llm).run("开始吧")
    trace = list(result.traces)
    assert trace[0].status == "rejected"
    assert "已经有内容" in trace[0].message
    assert session_reads.list_clarifications(sandbox.db, sandbox.session_id) == []


def test_answering_through_the_domain_unblocks_the_next_plan_change(sandbox) -> None:
    """答完之后，**同一个会话**的计划修改不再被闸门拦下。"""
    # 先把目标清空 → 助手被拦
    input_row = session_reads.get_input(sandbox.db, sandbox.session_id)
    session_writes.update_input(
        sandbox.db,
        sandbox.session_id,
        changes={"goal": None},
        expected_version=input_row.version,
        changed_by="user",
        reason="先清掉",
    )
    asked = session_writes.ask_about_task(
        sandbox.db, sandbox.session_id, slot="goal", question="要达成什么？"
    )
    session_writes.answer_clarification(
        sandbox.db,
        sandbox.session_id,
        asked.clarification_id,
        answer="能读完一章原版文档",
        expected_version=session_reads.get_input(sandbox.db, sandbox.session_id).version,
    )
    plan = session_reads.get_plan(sandbox.db, sandbox.session_id)
    session_writes.revise_plan(
        sandbox.db,
        sandbox.session_id,
        steps=[StepDraft(action="读第二章并写下三个问题")],
        change="换一步",
        reason="考完第一章了",
        expected_version=plan.version,
        changed_by="agent",
    )
    assert session_reads.get_input(sandbox.db, sandbox.session_id).goal == (
        "能读完一章原版文档"
    )
