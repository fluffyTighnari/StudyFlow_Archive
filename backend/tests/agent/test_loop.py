"""循环：**四种停止理由都要真出现，而且都要可解释**。

一个没有上限的 Agent 循环是最糟的失败形态：不报错、不崩溃，
只是安静地烧掉时间和 token。所以这里每一条测试都在问
「它是怎么停下来的、用户能不能看懂为什么」。
"""

from __future__ import annotations

import json

from app.agent.llm import ChatMessage, FakeLLM, reply, call
from app.agent.loop import AgentLoop, StopReason
from app.session_domain import reads as session_reads


def _loop(sandbox, llm, *, max_steps: int = 8, gestures=frozenset()) -> AgentLoop:
    return AgentLoop(
        sandbox.db, sandbox.session_id, llm, max_steps=max_steps, user_gestures=gestures
    )


def _steps(*actions: str) -> dict:
    return {
        "steps": [
            {
                "step_id": f"ST-{index:03d}",
                "action": action,
                "output": f"{action}的产出",
            }
            for index, action in enumerate(actions, start=1)
        ]
    }


# --------------------------------------------------------------------------- #
# 四种停止理由
# --------------------------------------------------------------------------- #


def test_a_plain_answer_completes_in_one_step(sandbox) -> None:
    llm = FakeLLM([reply("这一步看起来没问题，你可以直接开始。")])
    result = _loop(sandbox, llm).run("我现在该做什么？")

    assert result.stop_reason is StopReason.COMPLETED
    assert result.steps == 1
    assert result.traces == ()
    assert "没问题" in result.reply


def test_a_tool_call_then_an_answer_completes(sandbox) -> None:
    llm = FakeLLM(
        [
            reply("我先看一下你的认知基线。", call("retrieve_cognitive", scope="m1")),
            reply("看完了，你的基线里还没有读数。"),
        ]
    )
    result = _loop(sandbox, llm).run("看看我的基线")

    assert result.stop_reason is StopReason.COMPLETED
    assert result.steps == 2
    assert [entry.tool for entry in result.traces] == ["retrieve_cognitive"]


def test_the_loop_stops_at_the_step_budget(sandbox) -> None:
    """一直在调工具 → 用完预算就停，绝不无限转下去。"""
    llm = FakeLLM([reply("再查一下", call("retrieve_cognitive", scope="m1")) for _ in range(3)])
    result = _loop(sandbox, llm, max_steps=3).run("查")

    assert result.stop_reason is StopReason.MAX_STEPS
    assert result.steps == 3
    assert "步数预算" in result.reply
    # 模型中途说的那句话只能作为附注 —— 它**不是**最终答复，
    # 把"再查一下"当成结论端给用户，就是一次谎报"说完了"。
    assert "它最后说的是" in result.reply
    # 轨迹是完整的 —— 用户能看到它一共动了几次
    assert len(result.traces) == 3


def test_the_budget_message_does_not_pose_as_a_final_answer(sandbox) -> None:
    """预算用完 ≠ 说完了。这两件事被搞混时，用户会以为答案是"再查一下"。"""
    llm = FakeLLM([reply("再查一下", call("retrieve_cognitive", scope="m1")) for _ in range(2)])
    result = _loop(sandbox, llm, max_steps=2).run("查")
    assert result.reply.startswith("我用了 2 步还没得出结论")
    assert result.ok is False  # `ok` 只对 COMPLETED 为真


def test_an_llm_failure_stops_with_a_readable_reason(sandbox) -> None:
    """`FakeLLM` 脚本用完会抛 `LLMError` —— 正好用来模拟"服务没起"。

    它必须变成 `llm_error` 加一句人话，而不是一个 500 和一段堆栈：
    用户需要知道的是"模型这次没答上来"，而不是去看 traceback。
    """
    llm = FakeLLM([])
    result = _loop(sandbox, llm).run("在吗")

    assert result.stop_reason is StopReason.LLM_ERROR
    assert result.steps == 0
    assert "没能拿到模型" in result.reply
    assert "Ollama" in result.reply or "脚本" in result.reply


def test_repeating_a_rejected_call_stops_the_loop(sandbox) -> None:
    """**零进展检测**：把已经被拒的调用原样再来一次。

    再给它步数只会让用户看到一段"它一直在试同一件事"的对话。
    这条测试里的 `mark_step_done` 既没有用户手势、也没有可核对的引文
    → 必然被拒（政策变更只改了**入口条件有两条**，没改"没证据就打不了勾"）。

    ★ 末句断言从"一键操作"改成 `user_quote`：拒绝理由换了措辞，
    而这条测试真正钉的是**循环在零进展时把最后那条拒绝理由端出来**，
    不是那句措辞。用新版理由里的关键词，才能让"端出来的确实是最后那条"成立。
    """
    llm = FakeLLM(
        [
            reply("我帮你标一下。", call("mark_step_done", step_id="ST-001")),
            reply("那我再标一次。", call("mark_step_done", step_id="ST-001")),
        ]
    )
    result = _loop(sandbox, llm).run("我做完第一步了")

    assert result.stop_reason is StopReason.REJECTED_BY_DISPATCHER
    assert result.steps == 2
    assert "原样又试了一次" in result.reply
    assert "user_quote" in result.reply  # 最后一次的拒绝理由被端出来了


def test_a_corrected_call_is_not_treated_as_no_progress(sandbox) -> None:
    """改对了参数就不算零进展 —— 否则整个"看到拒绝再纠正"的流程会失效。

    这正是 `propose_plan_change` 的硬约束覆盖率检查所依赖的行为：
    第一次漏答约束被拒，第二次补上就该成功。
    """
    llm = FakeLLM(
        [
            reply("我调整一下。", call("retrieve_cognitive", scope="m1", max_item=3)),
            reply("参数写错了，重来。", call("retrieve_cognitive", scope="m1", max_items=3)),
            reply("好，看完了。"),
        ]
    )
    result = _loop(sandbox, llm).run("看看基线")

    assert result.stop_reason is StopReason.COMPLETED
    assert [entry.status for entry in result.traces] == ["rejected", "success"]


def test_an_empty_reply_is_a_failure_not_a_completion(sandbox) -> None:
    """**空回复不是"说完了"。**

    这是一次真实运行抓出来的：本地模型返回了既没有内容、也没有工具调用的响应，
    而当时这个分支返回 `completed` + 空字符串 —— 于是端点拿着一句空话去写
    `chat_message`，被数据库的「空消息不记」约束打回来，
    整个请求变成 422，**用户那句话也跟着丢了**。

    归到 `llm_error` 而不是硬凑一句话：这是模型这一轮没有产出，不是它的结论。
    """
    llm = FakeLLM([reply("")])
    result = _loop(sandbox, llm).run("在吗")

    assert result.stop_reason is StopReason.LLM_ERROR
    assert result.reply.strip()  # 永远不返回空串
    assert "空回复" in result.reply
    assert result.ok is False


def test_a_tool_call_with_no_text_is_not_an_empty_reply(sandbox) -> None:
    """反面的一半：**带工具调用**的空文本是正常的（模型先动手、后说话）。"""
    llm = FakeLLM(
        [
            reply("", call("retrieve_cognitive", scope="m1")),
            reply("看完了。"),
        ]
    )
    result = _loop(sandbox, llm).run("看看")
    assert result.stop_reason is StopReason.COMPLETED
    assert result.reply == "看完了。"
    """一次被拒不等于讨论结束 —— 模型还有机会换路子。"""
    llm = FakeLLM(
        [
            reply("先查对话记录。", call("retrieve_session", scope="chat_log", filter={})),
            reply("那我换个范围。", call("retrieve_session", scope="notes", filter={})),
            reply("你的笔记是空的。"),
        ]
    )
    result = _loop(sandbox, llm).run("我之前记过什么？")

    assert result.stop_reason is StopReason.COMPLETED
    assert [entry.status for entry in result.traces] == ["rejected", "empty"]


# --------------------------------------------------------------------------- #
# 上下文与回灌
# --------------------------------------------------------------------------- #


def test_the_system_message_carries_the_real_session_state(sandbox) -> None:
    """模型看到的状态必须来自**代码组装的上下文包**，不是它自己检索来的。"""
    llm = FakeLLM([reply("好")])
    _loop(sandbox, llm).run("我该做什么？")

    (messages,) = llm.seen
    assert [message.role for message in messages] == ["system", "system", "user"]
    prompt = messages[0].content + messages[1].content

    assert "每天用 Anki 过 50 个新词" in prompt  # 当前这一步
    assert "硬约束" in prompt  # 采用序的第 1 段
    assert "stage" in prompt
    assert "方法候选" in prompt  # 策略库（采用序的最后一段）
    # 采用序本身也要能被解释 —— 六个以上的小节，每节有标题
    assert messages[1].content.count("##") >= 6


def test_the_context_pack_carries_the_checkpoint_so_the_model_can_talk_about_it(
    sandbox,
) -> None:
    """**这条测试的前身断言的是相反的结论，而它按计划红了。**

    前身叫 `test_the_context_pack_has_no_checkpoints_but_they_are_one_call_away`，
    钉的是"P4 的包里没有检查点"这个已知缺口，并在 docstring 里写明：
    它有朝一日会红，那时改动的人必须明确回答"检查点该不该进包"。

    用户裁定：**该进**。理由是一个具体场景 —— 用户点开"每天过 50 个新词"
    说"我记不住"，而这步的失败预案里本来就写着"回到上一批词重刷三天"，
    模型却看不见，于是它只能重新发明一遍建议。

    所以这里反过来断言：包里有它，而且是**完整的一行**（怎么验、过关标准、
    不过怎么办、当前结果）。只断言"出现了"会让一个只抄了方法名的实现也通过。
    """
    from app.agent.dispatch import Dispatcher
    from app.agent.llm import ToolCall

    llm = FakeLLM([reply("好")])
    _loop(sandbox, llm).run("我该做什么？")
    system = llm.seen[0][1].content
    assert "随机抽 20 个词自测" in system
    assert "正确率 ≥ 80%" in system
    assert "回到上一批词重刷三天" in system
    # 还要标明这是**这一步的验收标准**，而不是一条泛泛的建议
    assert "本步骤的验收标准" in system

    # 另一条通路也仍然可用（并且给出的是原始结构，不是渲染过的文本）
    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        ToolCall(call_id="call_1", name="retrieve_session",
                 arguments={"scope": "task_plan", "filter": {}})
    )
    checkpoints = [item for item in result.data["items"] if item["kind"] == "checkpoint"]
    assert checkpoints[0]["check_method"] == "随机抽 20 个词自测"
    assert checkpoints[0]["fail_action"] == "回到上一批词重刷三天"


def test_the_plan_reaches_the_model_with_step_ids(sandbox) -> None:
    """没有 step_id 就没有 `target`，`propose_plan_change` 也就无从指认。"""
    llm = FakeLLM([reply("好")])
    _loop(sandbox, llm).run("改一下第一步")
    prompt = llm.seen[0][1].content
    assert "ST-001" in prompt
    assert "产出：2000 词的复习记录" in prompt


def test_tool_results_are_fed_back_as_tool_messages(sandbox) -> None:
    """结果必须原样回灌 —— 循环不做二次解释。"""
    llm = FakeLLM(
        [
            reply("查一下", call("retrieve_cognitive", scope="m1", max_items=2)),
            reply("看完了"),
        ]
    )
    _loop(sandbox, llm).run("看看")

    second_round = llm.seen[1]
    roles = [message.role for message in second_round]
    assert roles[-2:] == ["assistant", "tool"]

    assistant = second_round[-2]
    assert assistant.tool_calls[0].name == "retrieve_cognitive"
    assert assistant.tool_calls[0].call_id == "call_1"

    tool_message = second_round[-1]
    assert tool_message.name == "retrieve_cognitive"
    payload = json.loads(tool_message.content)
    assert payload["status"] == "success"
    assert payload["data"]["items"]


def test_history_is_passed_in_by_the_caller_not_guessed(sandbox) -> None:
    """上几轮的原话由调用方给 —— 循环不去猜"最后一条是不是我刚收到的那条"。

    那种猜测写起来很短，错起来很隐蔽：模型会看到自己刚说的话被重复两遍，
    然后开始复读。
    """
    llm = FakeLLM([reply("对，就按刚才说的改。")])
    _loop(sandbox, llm).run(
        "那就这么办",
        history=[
            ChatMessage.user("第二步是不是太慢了？"),
            ChatMessage.assistant("可以把它拆成两次。"),
        ],
    )
    roles = [message.role for message in llm.seen[0]]
    assert roles == ["system", "system", "user", "assistant", "user"]
    assert llm.seen[0][-1].content == "那就这么办"


def test_every_tool_schema_is_offered_on_every_round(sandbox) -> None:
    llm = FakeLLM([reply("查", call("retrieve_folder")), reply("好")])
    _loop(sandbox, llm).run("看看文件夹")
    assert all(len(tools) == 16 for tools in llm.tools_seen)


# --------------------------------------------------------------------------- #
# 闭环：讨论 → 改计划
# --------------------------------------------------------------------------- #


def test_the_discussion_can_actually_change_a_step(sandbox) -> None:
    """**P6 要打通的那条闭环**：用户点一个计划项说出看法 → 计划真的改了。

    走完整条路：循环 → dispatcher → `propose_plan_change` → `revise_plan`。
    这里没有硬约束，所以 `constraint_checks` 不是必须的。
    """
    llm = FakeLLM(
        [
            reply(
                "我把第一步拆细一点。",
                call(
                    "propose_plan_change",
                    target="ST-001",
                    user_view="一次 50 个新词太多了，我记不住",
                    suggested_change={
                        "steps": [
                            {"step_id": "ST-001", "action": "每天过 20 个新词", "output": "20 词的复习记录"},
                            {"step_id": "ST-002", "action": "读技术文档并标注生词", "output": "标注过的文档"},
                        ],
                        "change": "第一步从 50 个降到 20 个",
                    },
                ),
            ),
            reply("改好了：第一步现在是每天 20 个新词。"),
        ]
    )
    result = _loop(
        sandbox, llm, gestures=frozenset({"plan_change"})
    ).run("第一步太多了，我记不住")

    assert result.stop_reason is StopReason.COMPLETED
    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    assert plan.version == 2
    actions = [step.action for step in session_reads.plan_steps(sandbox.db, plan.plan_id)]
    assert actions[0] == "每天过 20 个新词"
    # 第二步被原样保留（带了 step_id，所以完成状态与那一行的身份都还在）
    assert actions[1] == "读技术文档并标注生词"


def test_the_loop_anchors_to_the_first_unfinished_step(sandbox) -> None:
    """没指定讨论哪一项时锚到"当前这一步"，且这个选择必须是确定性的。

    否则同一个会话在两次请求里会锚到不同步骤，用户会看到上下文莫名变化。
    """
    from app.session_domain import writes as session_writes

    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    session_writes.mark_step(
        sandbox.db, sandbox.session_id, step_id="ST-001", status="done",
        expected_version=plan.version,
    )

    llm = FakeLLM([reply("好")])
    _loop(sandbox, llm).run("下一步干什么")
    prompt = llm.seen[0][1].content
    assert "当前这一步（ST-002）" in prompt
