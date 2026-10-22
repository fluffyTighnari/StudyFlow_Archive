"""执行器：**模型的输出是输入，不是指令**。

这里测的全是"模型给出了一个不合法/不允许的请求"时会发生什么。
每一条都必须以「一条可读的 rejected + 一句人话」收场 ——
不能崩溃、不能静默、也不能被改写成"看起来成功了"。
"""

from __future__ import annotations

import pytest

from app.agent.dispatch import Dispatcher, validate_arguments
from app.agent.llm import ToolCall
from app.agent.tools import ToolStatus, tool_schemas
from app.core.errors import CapabilityError, InvariantViolation
from app.global_domain import reads as global_reads
from app.global_domain.enums import EvidenceChannel
from app.session_domain import reads as session_reads
from app.session_domain import writes as session_writes


def _call(name: str, **arguments) -> ToolCall:
    return ToolCall(call_id="call_1", name=name, arguments=arguments)


def test_unknown_tool_name_is_rejected_with_the_list_of_real_ones(sandbox) -> None:
    """只说"没有这个工具"会让模型反复试别的错名字。"""
    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        _call("retrieve_session_history", query="x")
    )
    assert result.status is ToolStatus.REJECTED
    assert "retrieve_session" in result.message


def test_missing_required_argument_is_rejected_before_anything_runs(sandbox) -> None:
    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        _call("retrieve_session", scope="notes")
    )
    assert result.status is ToolStatus.REJECTED
    assert "缺少必填参数" in result.message and "filter" in result.message


def test_wrong_type_is_rejected_with_the_actual_type_named(sandbox) -> None:
    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        _call("retrieve_session", scope="notes", filter="全部给我", max_items="8")
    )
    assert result.status is ToolStatus.REJECTED
    assert "对象" in result.message and "字符串" in result.message


def test_unknown_argument_is_rejected_not_ignored(sandbox) -> None:
    """与 `_WriteModel(extra="forbid")` 同一个姿态：猜出来的字段不写。

    静默忽略一个拼错的字段，最坏的情况是**写下了与用户意图不同的东西**，
    而调用方以为成功了。
    """
    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        _call("write_stage", type="idea", content="试试", tags=["x"])
    )
    assert result.status is ToolStatus.REJECTED
    assert "不认识的参数" in result.message and "tags" in result.message


def test_enum_violation_is_rejected_with_the_allowed_values(sandbox) -> None:
    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        _call("write_stage", type="maybe", content="试试")
    )
    assert result.status is ToolStatus.REJECTED
    assert "temp_pref" in result.message


def test_no_implicit_type_coercion(sandbox) -> None:
    """`"8"` 不会变成 `8`。

    任何隐式转换都是系统在替模型猜它的意思，而猜错的后果
    （写进去一个错的值）比多走一步（模型看到提示再试一次）大得多。
    """
    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        _call("retrieve_cognitive", scope="all", max_items="8")
    )
    assert result.status is ToolStatus.REJECTED
    assert "整数" in result.message


def test_a_single_element_is_not_wrapped_into_a_list(sandbox) -> None:
    """模型只传一个元素而不是列表 —— 同样是"不替它猜"。"""
    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        _call("propose_plan_change", target="ST-001", user_view="看法",
              constraint_checks={"pref_id": "P-0001", "verdict": "ok"},
              suggested_change={"steps": []})
    )
    assert result.status is ToolStatus.REJECTED
    assert "列表" in result.message


def test_boolean_is_not_accepted_as_an_integer(sandbox) -> None:
    """`True` 是 `int` 的子类 —— 不特判的话 `max_items=True` 会被读成 1。"""
    assert validate_arguments({"type": "integer"}, True) != []
    assert validate_arguments({"type": "integer"}, 3) == []


def test_nested_argument_paths_are_reported_precisely(sandbox) -> None:
    """报错要指到 `参数.intents[1].target`，而不是笼统的"参数不对"。

    模型靠这条信息改参数；路径不精确时它只能整份重写，而重写经常顺手改坏
    本来没错的那几条。
    """
    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        _call(
            "submit_global_update",
            trigger_type="R3",
            intents=[
                {
                    "action": "add_preference",
                    "target": "M2.preferences",
                    "basis": "stated",
                    "reasoning": "因为",
                    "confidence": "high",
                },
                {"action": "add_preference"},
            ],
        )
    )
    assert result.status is ToolStatus.REJECTED
    assert "intents[1]" in result.message


def test_the_value_schema_accepts_a_real_preference_payload(sandbox) -> None:
    """正向：一个合法的偏好提交不该被我们自己的校验器挡住。

    只测"该拒的拒了"会掩盖另一类缺陷 —— 校验器比 schema 更严，
    于是写对的调用永远失败，而报错指向工具名，看起来像工具坏了。
    """
    _say(sandbox, "我讨厌看网课，因为浪费时间")
    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        _call(
            "submit_global_update",
            trigger_type="R3",
            intents=[
                {
                    "action": "add_preference",
                    "target": "M2.preferences",
                    "value": {"statement": "我讨厌看网课，因为浪费时间", "priority": 7},
                    "basis": "stated",
                    "basis_quote": "我讨厌看网课，因为浪费时间",
                    "reasoning": "用户在讨论里说的",
                    "confidence": "high",
                }
            ],
        )
    )
    assert result.status is ToolStatus.SUCCESS, result.message
    assert result.data["trigger_type"] == "R3"
    # 真的经 Router 落了库
    assert global_reads.count_router_logs(sandbox.db) == 1


def test_the_agent_cannot_claim_the_benchmark_trigger(sandbox) -> None:
    """**权限提升的守卫，第一道门在 schema。**

    R2（基准测试）配 `measured` 在判定表里是 auto_apply。一个能声称 R2 的模型
    可以直接改认知变量而不惊动任何人。

    这一道门是 `trigger_type` 的 `enum`：模型在**发出调用之前**就被告知
    只能填哪四个值。比"发出来再拒绝"好，因为它省掉一步。
    第二道门（运行期检查）见下一条测试。
    """
    for trigger in ("R1", "R2", "R5"):
        result = Dispatcher(sandbox.db, sandbox.session_id).execute(
            _call(
                "submit_global_update",
                trigger_type=trigger,
                intents=[
                    {
                        "action": "update_parameter",
                        "target": "M1.V-SE",
                        "value": {
                            "channel": "measured",
                            "score": 9.9,
                            "completeness": "complete",
                            "confidence": "calibrated",
                        },
                        "basis": "measured",
                        "reasoning": "我说了算",
                        "confidence": "high",
                    }
                ],
            )
        )
        assert result.status is ToolStatus.REJECTED, trigger
        assert "R3" in result.message  # 把允许的那几个列出来
    assert global_reads.count_router_logs(sandbox.db) == 0


def test_handoff_holds_a_second_door_for_the_trigger(sandbox) -> None:
    """第二道门：**绕过 dispatcher 直接调 handoff** 也拦得住。

    冗余是故意的 —— 与 `m1_policy` 里那层冗余的确认检查同一个理由：
    通向全局权威域的最后一道门，不该只有一个上游函数说了算。
    校验器哪天被放宽（例如为了兼容某个模型而放宽 enum），
    这道门仍然是关着的。
    """
    from app.agent.handoff import submit_global_update
    from app.agent.tools import ToolContext

    ctx = ToolContext(db=sandbox.db, session_id=sandbox.session_id)
    result = submit_global_update(
        ctx,
        {
            "trigger_type": "R2",
            "intents": [
                {
                    "action": "update_parameter",
                    "target": "M1.V-SE",
                    "value": {
                        "channel": "measured",
                        "score": 9.9,
                        "completeness": "complete",
                        "confidence": "calibrated",
                    },
                    "basis": "measured",
                    "reasoning": "我说了算",
                    "confidence": "high",
                }
            ],
        },
    )
    assert result.status is ToolStatus.REJECTED
    # 这句话是**给模型看的**（它会被原样塞回上下文），所以措辞是"你"而不是"Agent" ——
    # 内部术语出现在模型面前会让它照着编。断言的是"它明说了这不在许可范围内"，
    # 而不是某个具体措辞：措辞会改，这件事不会。
    assert "不是你能声称的" in result.message
    assert "R3、R4a、R4b" in result.message
    assert "R4c" in result.message, "R4c 现在也排除了，驳回语必须把它算进去"
    assert global_reads.count_router_logs(sandbox.db) == 0


def test_the_agent_cannot_claim_r4c_either(sandbox) -> None:
    """★ 缺口 G-01（用户裁定甲案）：**模型没有资格声称"用户勾选过"**。

    `R4c` 在判定表里的两条自动生效规则（`add_preference` / `update_parameter`，
    后者直接写 M1 认知变量）理由是**那次勾选动作本身**。
    而会话里没有勾选这种东西 —— 它由 `POST /sediment` 的界面通路产生。
    模型声称 `R4c` 时，没有任何东西核实过勾选发生过，
    所以它与被排除的 `R2` 是同一件事。

    这条测试的价值在于**它是行为层的**：`tests/router` 里那条从判定表与白名单
    的叉乘出发（"这条规则还在不在"），这一条从真的调一次出发
    （"换了个入口还拦不拦得住"）。两者坏了的方式不同 ——
    前者会在有人放宽白名单时红，后者会在有人给 handoff 开后门时红。
    """
    from app.agent.handoff import submit_global_update
    from app.agent.tools import ToolContext

    ctx = ToolContext(db=sandbox.db, session_id=sandbox.session_id)
    result = submit_global_update(
        ctx,
        {
            "trigger_type": "R4c",
            "intents": [
                {
                    "action": "update_parameter",
                    "target": "M1.V-SE",
                    "value": {
                        "channel": "experienced",
                        "score": 9.9,
                        "completeness": "partial",
                    },
                    "basis": "experienced",
                    "reasoning": "用户勾选过了",  # ← 这句正是模型**无权**说的话
                    "confidence": "high",
                }
            ],
        },
    )
    assert result.status is ToolStatus.REJECTED
    # 关键：**一个字节都没写**。判定表里这一格本来是 AUTO_APPLY，
    # 所以"它有没有真的生效"才是这条测试真正要看的东西。
    assert global_reads.count_router_logs(sandbox.db) == 0
    assert global_reads.list_proposals(sandbox.db) == []
    # 逐通道看当前版本：`experienced` 通道上不许出现任何 V-SE 版本。
    assert (
        global_reads.current_baseline_version(
            sandbox.db, "V-SE", EvidenceChannel.EXPERIENCED
        )
        is None
    ), "被拒的提交不许留下参数版本"


def test_the_agent_cannot_choose_the_intent_id(sandbox) -> None:
    """id 由代码分配。模型编 id 会产生重复，而重复的 intent 在审计里
    会表现成"同一个意图被应用了两次"—— 一个永远解释不清的记录。"""
    dispatcher = Dispatcher(sandbox.db, sandbox.session_id)
    result = dispatcher.execute(
        _call(
            "submit_global_update",
            trigger_type="R3",
            intents=[
                {
                    "intent_id": "I-9999",  # 不在 schema 里 → 被当成不认识的参数拒绝
                    "action": "add_preference",
                    "target": "M2.preferences",
                    "value": {"statement": "A", "priority": 5},
                    "basis": "stated",
                    "reasoning": "因为",
                    "confidence": "high",
                }
            ],
        )
    )
    assert result.status is ToolStatus.REJECTED
    assert "intent_id" in result.message


def test_operational_errors_become_rejected_not_crashes(sandbox) -> None:
    """归档之后不能写 stage —— 这是**可预期**的运行期状态，不是缺陷。

    它必须变成一句模型能照做的话，而不是一个 409 冒泡到用户那里。

    ★ 政策变更：样本状态从 `review` 换成 `completed`。整理期现在**可以**
    写 stage（`review` 不再是"执行已经结束"），而归档之后仍然关着。
    拒绝的**形状**没有变（`rejected` + 一句人话 + 说明允许哪些状态），
    变的只是"哪个状态会拒"——这正是政策与测试该有的关系。
    """
    session = session_reads.get_session(sandbox.db, sandbox.session_id)
    session_writes.transition(
        sandbox.db, sandbox.session_id, target="completed", expected_version=session.version
    )
    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        _call("write_stage", type="idea", content="还想试试别的")
    )
    assert result.status is ToolStatus.REJECTED
    assert "completed" in result.message


def test_capability_and_invariant_errors_are_not_swallowed() -> None:
    """结构断言：这两类异常**不在** `rejected` 的名单里。

    它们意味着代码有缺陷（有人没拿令牌就想写全局 / 系统状态已经不一致）。
    把它们变成一句「这次操作不被允许」，等于让一个真实的缺陷
    以一个听起来很合理的拒绝继续活下去 —— 而那正是本项目反复栽的那一类。
    """
    from app.agent.dispatch import OPERATIONAL_ERRORS

    assert CapabilityError not in OPERATIONAL_ERRORS
    assert InvariantViolation not in OPERATIONAL_ERRORS


def test_the_trace_records_what_actually_happened(sandbox) -> None:
    """轨迹回答的是"它真的动过哪些东西"，不是"它说它想做什么"。"""
    dispatcher = Dispatcher(sandbox.db, sandbox.session_id)
    dispatcher.execute(_call("retrieve_cognitive", scope="m1", max_items=2))
    dispatcher.execute(_call("write_stage", type="idea", content="想试试先听音频"))
    dispatcher.execute(_call("mark_step_done", step_id="ST-001"))

    assert [entry.tool for entry in dispatcher.traces] == [
        "retrieve_cognitive",
        "write_stage",
        "mark_step_done",
    ]
    assert [entry.step for entry in dispatcher.traces] == [1, 2, 3]
    assert [entry.status for entry in dispatcher.traces] == [
        "success",
        "success",
        "rejected",
    ]
    assert dispatcher.traces[1].arguments == {"type": "idea", "content": "想试试先听音频"}
    assert dispatcher.traces[1].scope == "session_write"
    assert all(entry.duration_ms >= 0 for entry in dispatcher.traces)
    # 轨迹是可序列化的 —— 它要被写进 chat_message.tool_trace
    import json

    assert json.loads(json.dumps([e.as_dict() for e in dispatcher.traces]))


def test_the_read_scope_is_recorded_for_read_tools(sandbox) -> None:
    dispatcher = Dispatcher(sandbox.db, sandbox.session_id)
    dispatcher.execute(_call("retrieve_folder"))
    dispatcher.execute(
        _call(
            "submit_global_update",
            trigger_type="R3",
            intents=[
                {
                    "action": "add_preference",
                    "target": "M2.preferences",
                    "value": {"statement": "A", "priority": 5},
                    "basis": "stated",
                    "reasoning": "因为",
                    "confidence": "high",
                }
            ],
        )
    )
    assert [entry.scope for entry in dispatcher.traces] == ["read", "global"]


def test_every_dispatched_call_appends_exactly_one_trace_entry(sandbox) -> None:
    """一次调用一行 —— 包括被拒的那些。

    只记成功的调用会让轨迹变成一份"成绩单"，而它真正要回答的是
    「我让它做过什么、它拒绝了什么」。
    """
    dispatcher = Dispatcher(sandbox.db, sandbox.session_id)
    for call in (
        _call("no_such_tool"),
        _call("retrieve_session", scope="notes"),
        _call("write_stage", type="idea", content="ok"),
    ):
        dispatcher.execute(call)
    assert len(dispatcher.traces) == 3


@pytest.mark.parametrize("schema", [s.parameters for s in tool_schemas()])
def test_an_empty_argument_dict_only_ever_reports_missing_required(schema) -> None:
    """空参数下，唯一该报的是"缺少必填"。

    如果它还报出"不认识的参数"或类型问题，说明 schema 自身有问题
    （例如 `required` 列了一个 `properties` 里没有的键）——
    那种情况下**每一次**调用都会失败，而报错指向工具名，看起来像工具坏了。
    """
    assert all(
        "缺少必填参数" in problem for problem in validate_arguments(schema, {})
    ), validate_arguments(schema, {})


# --------------------------------------------------------------------------- #
# ★ 全局提交的结果必须**分三种说**（等确认 ≠ 被拒）
# --------------------------------------------------------------------------- #


def _say(sandbox, content: str) -> None:
    """让**用户**在会话里说一句话（`role=user`）。

    这是 `R3 + stated` 那道引用核对的唯一数据来源 ——
    测试必须真的把话写进 `chat_message`，而不是绕过校验。
    """
    session_writes.add_chat_message(sandbox.db, sandbox.session_id, role="user", content=content)


def _submit(sandbox, trigger: str, **intent_overrides):
    from app.agent.handoff import submit_global_update
    from app.agent.tools import ToolContext

    intent = {
        "action": "add_preference",
        "target": "M2.preferences",
        "value": {"statement": "我讨厌看网课，因为浪费时间", "priority": 5},
        "basis": "stated",
        "reasoning": "用户自己说的",
        "confidence": "high",
    }
    intent.update(intent_overrides)
    return submit_global_update(
        ToolContext(db=sandbox.db, session_id=sandbox.session_id),
        {"trigger_type": trigger, "intents": [intent]},
    )


def test_a_pending_proposal_is_not_reported_as_rejected(sandbox) -> None:
    """★ **等用户确认，不是被拒。**

    这是一条真实的缺陷回归（P6+.5 探针抓出来的）。旧代码判断
    「提案还在等确认」用的是 `result.status.value in {"pending", "partially_resolved"}`，
    而 `ProposalStatus` 的取值是
    `proposed / pending_confirmation / applied / partially_applied / rejected / expired`
    —— **一个都不匹配**。后果有两层：

    1. `data["pending_confirmation"]` **恒为 False**（死代码）；
    2. 一次全部待确认的提交，`applied` 为空、`pending` 被并进 `rejected`，
       于是走最后一个分支，报给模型的是 `ToolStatus.REJECTED` +
       「这次提交没有任何一条生效」。

    第 2 条是有后果的：界面上确实多了一张等待裁决的提案卡，
    而模型被告知"被拒了"、并且**没有任何一句话要它告诉用户**。
    那就等于用户永远不知道自己有一件事要确认 —— 审核队列成了摆设。

    `R3 + add_preference + experienced` 是造成这个局面的组合
    （`stated` 自动生效、`experienced` 只是观察 → 需确认）。
    """
    result = _submit(sandbox, "R3", basis="experienced")

    assert result.status is ToolStatus.SUCCESS, (
        "提交本身成功了（提案已落库），不该报成 rejected："
        f"{result.status} / {result.message}"
    )
    assert result.data["pending_confirmation"] is True
    assert result.data["pending_count"] == 1
    assert result.data["applied_count"] == 0
    # 三件事必须说全：还没生效、在等谁、要它做什么。
    assert "还没有生效" in result.message
    assert "待确认队列" in result.message or "确认" in result.message
    assert "告诉用户" in result.message
    # 而提案**真的**在队列里 —— 判据落在库上，不落在措辞上。
    assert len(global_reads.list_proposals(sandbox.db)) == 1


def test_a_rejected_submission_still_says_no_proposal_was_queued(sandbox) -> None:
    """真被拒的那次，必须与"等确认"区分开 —— 而且不许暗示队列里有东西。

    用**红线拦下的组合**造一个真被拒：`R3 + update_parameter + inferred`
    —— 判定表里被红线 3（`inferred` 永不写参数）挡下，与 basis 降级无关。

    > 这里有两个容易写错的地方，我第一版就写错了：
    > 1. 我原先用 `R3 + update_parameter + stated`，而它现在的下场是
    >    **降级**（`stated → experienced` → 需确认），不再是"被拒" ——
    >    引用核对那道门把它挪走了；
    > 2. 被拒的提交**也会落一行提案**（状态 `rejected`）—— 那是 P3 定稿的行为
    >    （"策略拒绝 → 201，照常落提案与审计日志"，提案卡靠它渲染）。
    >    所以「没进待确认队列」的判据**不是**"提案表里没有行"，
    >    而是"按 `pending_confirmation` 查不到它"。
    """
    result = _submit(
        sandbox,
        "R3",
        action="update_parameter",
        target="M1.V-SMP",
        value={"channel": "inferred", "score": 6.0, "completeness": "partial"},
        basis="inferred",
    )
    assert result.status is ToolStatus.REJECTED
    assert "也没有进待确认队列" in result.message
    assert result.data["pending_confirmation"] is False
    assert result.data["rejected_count"] == 1

    proposals = global_reads.list_proposals(sandbox.db)
    assert [p.status.value for p in proposals] == ["rejected"], (
        "被拒的那次应当留下一行 status=rejected 的提案（审计靠它），"
        "但它**不在**待确认队列里"
    )
    assert global_reads.list_proposals(
        sandbox.db, status="pending_confirmation"
    ) == []


def test_an_auto_applied_submission_says_it_took_effect(sandbox) -> None:
    """第三种结局：直接生效。这句话必须与"等确认"读起来不同。

    模型要靠这句话决定"能不能拿它当already-true的前提往下推理"。
    **而这条路上现在多了一道门**：`stated` 必须引用户的原话，
    所以测试先让用户真的说一句。
    """
    _say(sandbox, "我讨厌看网课，因为浪费时间")
    result = _submit(
        sandbox, "R3", basis="stated", basis_quote="我讨厌看网课，因为浪费时间"
    )
    assert result.status is ToolStatus.SUCCESS
    assert "已生效" in result.message
    assert result.data["applied_count"] == 1
    assert result.data["pending_confirmation"] is False
    # 落库的判据：偏好表里真的有那一行。
    assert global_reads.list_preferences(sandbox.db) != []


def test_a_mixed_batch_reports_all_three_counts(sandbox) -> None:
    """一批里三种结局同时出现 —— 每个数都要如实给出，不许合并成一句话。"""
    from app.agent.handoff import submit_global_update
    from app.agent.tools import ToolContext

    _say(sandbox, "我讨厌看网课，因为浪费时间")
    base = {
        "target": "M2.preferences",
        "value": {"statement": "我讨厌看网课，因为浪费时间", "priority": 5},
        "reasoning": "混合批次",
        "confidence": "high",
    }
    result = submit_global_update(
        ToolContext(db=sandbox.db, session_id=sandbox.session_id),
        {
            "trigger_type": "R3",
            "intents": [
                {  # 生效（引到了用户原话）
                    **base,
                    "action": "add_preference",
                    "basis": "stated",
                    "basis_quote": "我讨厌看网课",
                },
                {**base, "action": "add_preference", "basis": "experienced"},  # 等确认
                {
                    "action": "update_parameter",
                    "target": "M1.V-SMP",
                    "value": {"channel": "inferred", "score": 6.0, "completeness": "partial"},
                    "basis": "inferred",
                    "reasoning": "红线 3 拦下的组合",
                    "confidence": "low",
                },  # 被拒
            ],
        },
    )
    assert result.status is ToolStatus.SUCCESS
    assert result.data["applied_count"] == 1
    assert result.data["pending_count"] == 1
    assert result.data["rejected_count"] == 1
    # 三个数都要出现在给模型看的那句话里，且**等确认那条要它去告诉用户**。
    assert "1 条**已生效**" in result.message
    assert "1 条**进了待确认队列**" in result.message
    assert "1 条被拒" in result.message


# --------------------------------------------------------------------------- #
# ★ 第二道门：`R3 + stated` 必须引用户的原话（缺口 G-01b，用户裁定甲案）
# --------------------------------------------------------------------------- #


def test_stated_is_downgraded_when_the_quote_is_not_the_users_words(sandbox) -> None:
    """★ **主句：`stated` 声称的是"用户说的"，那就得真的是用户说的。**

    这是缺口 G-01b 的回归测试。真模型实测记录了它的形状：
    用户只说了"我传了几份资料…"，而模型读完文件后提交了
    `trigger=R3、basis=['stated'] × 5`，**五条全部直接写进基线、队列为空**。

    文件是用户给的**材料**，不是用户说的**话**。这里的断言落在三件事上：

    1. 结果状态是 `success`（降级不是失败，是换了个出口）；
    2. 那条**没有生效**、而是进了待确认队列 —— 这是用户裁定要的结果；
    3. 给模型的话里**必须解释发生了降级**，否则它会以为自己没说话算数、
       或者以为系统坏了，然后反复重试同一条。
    """
    # 用户说过的话与模型引用的话**不是同一句** —— 模型引的是文件里的内容。
    _say(sandbox, "我传了几份资料，你看看有什么值得记下来的")
    result = _submit(
        sandbox,
        "R3",
        basis="stated",
        basis_quote="每天背 200 个词，这是我给自己定的铁律",  # ← 文件里写的
        value={"statement": "每天背 200 个词", "priority": 9},
    )

    assert result.status is ToolStatus.SUCCESS, "降级不等于失败"
    assert result.data["applied_count"] == 0, "★ 没有生效"
    assert result.data["pending_count"] == 1, "★ 进了待确认队列"
    assert result.data["pending_confirmation"] is True
    assert "降级" in result.message
    assert "stated" in result.message and "experienced" in result.message
    # 判据落在库上：偏好表里**没有**这一行（只是提案等着）。
    assert global_reads.list_preferences(sandbox.db) == []
    assert len(global_reads.list_proposals(sandbox.db, status="pending_confirmation")) == 1


def test_the_quote_check_ignores_uploaded_files(sandbox) -> None:
    """★ **资料不算"用户说过的话"** —— 这是整个修复的支点。

    如果这条测试红了（上传的内容让 `stated` 通过了），
    那么 G-01b 就原样回来了，只是绕过的方式从"模型编一句话"变成"模型从文件里抄一句"。
    抄一句比编一句**更容易**，所以这条比上面那条更要紧。
    """
    from app.session_domain import writes as session_writes_for_file

    # 文件里的原话，一字不差。
    file_text = "我每天晚上只能学 30 分钟，因为下班太晚"
    session_writes_for_file.attach_file(
        sandbox.db,
        sandbox.session_id,
        filename="经历.md",
        raw=file_text.encode("utf-8"),
    )
    result = _submit(
        sandbox,
        "R3",
        basis="stated",
        basis_quote=file_text,  # ← 一字不差，但它来自文件
        value={"statement": file_text, "priority": 6},
    )
    assert result.data["applied_count"] == 0, (
        "★ 上传文件里的原话**不能**让 `stated` 通过 —— "
        "文件是材料，不是用户在这个对话里说过的话"
    )
    assert result.data["pending_count"] == 1


def test_the_quote_check_ignores_the_agents_own_replies(sandbox) -> None:
    """模型自己的回复也不算 —— 否则它可以先说一句、再引自己那句。

    这是一条**很便宜就能绕过去**的路：`role=assistant` 的消息里有大量
    它自己写的、读起来像用户在说话的句子（它就是照用户的口吻写的）。
    """
    session_writes.add_chat_message(
        sandbox.db,
        sandbox.session_id,
        role="assistant",
        content="我记住了：你更喜欢先看例子再学概念。",
    )
    result = _submit(
        sandbox,
        "R3",
        basis="stated",
        basis_quote="你更喜欢先看例子再学概念",
    )
    assert result.data["applied_count"] == 0
    assert result.data["pending_count"] == 1


def test_the_quote_check_tolerates_whitespace_but_not_a_changed_word(sandbox) -> None:
    """白名单**只有空白**：换行空格随便，改一个字就算对不上。

    这一条把这个校验的边界钉死。放松到"意思像就行"会让它变成
    一次同义词判断 —— 而那正是模型自己最擅长骗过的东西。
    """
    _say(sandbox, "我一次记不住 50 个词，太多了")

    # 空白不同 → 通过
    ok = _submit(
        sandbox,
        "R3",
        basis="stated",
        basis_quote="我一次记不住50个词，\n太多了",
        value={"statement": "一次 50 个词太多", "priority": 4},
    )
    assert ok.data["applied_count"] == 1, "空白不该让引用失败"
    assert ok.data["pending_count"] == 0

    # 改一个字 → 不通过
    bad = _submit(
        sandbox,
        "R3",
        basis="stated",
        basis_quote="我一次记不住 60 个词，太多了",
        value={"statement": "一次 60 个词太多", "priority": 4},
    )
    assert bad.data["applied_count"] == 0
    assert bad.data["pending_count"] == 1


def test_a_too_short_quote_is_downgraded(sandbox) -> None:
    """引用太短会被挡下 —— 否则"我"能匹配任何一段用户文本，校验形同虚设。

    这条防的是**校验退化成永远通过**：那种缺陷不会让任何东西变红，
    它只会让一道门看起来在，实际上不在。
    """
    from app.agent.handoff import MIN_BASIS_QUOTE_CHARS

    _say(sandbox, "我想想")
    result = _submit(sandbox, "R3", basis="stated", basis_quote="我")
    assert result.data["applied_count"] == 0
    assert str(MIN_BASIS_QUOTE_CHARS) in result.message, (
        "驳回语要告诉它门槛是多少，否则它只会一遍遍给同样短的引用"
    )


def test_a_missing_quote_is_downgraded_and_explained(sandbox) -> None:
    """完全不给引用 → 降级，并明说"你没给"。"""
    _say(sandbox, "我讨厌看网课，因为浪费时间")
    result = _submit(sandbox, "R3", basis="stated")  # 没带 basis_quote
    assert result.data["applied_count"] == 0
    assert result.data["pending_count"] == 1
    assert "basis_quote" in result.message


def test_the_quote_gate_does_not_touch_other_bases_or_triggers(sandbox) -> None:
    """门只在该在的地方：**`R3 + stated`**。

    三个"不该被它碰"的方向各测一个，因为一道过宽的门比没有门更坏 ——
    它会让 `experienced`（本该等确认的）也被当成 `stated`，
    或者让 `R4a`（系统自动产出）莫名多出一项要求。
    """
    # ① `experienced` 本来就不声称"用户说过"，不该被降级 —— 它本来就进队列。
    experienced = _submit(sandbox, "R3", basis="experienced")
    assert experienced.data["pending_count"] == 1
    assert "降级" not in experienced.message

    # ② `R4a`（会话完成沉淀）不带 `basis_quote` 也应当照常工作。
    r4a = _submit(sandbox, "R4a", basis="experienced")
    assert r4a.status is ToolStatus.SUCCESS
    assert "降级" not in r4a.message

    # ③ `no_change` 什么都不写，没有"声称"可言 —— 不给引用也不该被降级。
    no_change = _submit(sandbox, "R3", action="no_change", basis="stated")
    assert "降级" not in no_change.message
