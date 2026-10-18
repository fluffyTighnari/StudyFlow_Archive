"""**调用层的联合验证**：prompt ↔ 上下文渲染 ↔ 工具声明 ↔ 实现，四者互不矛盾。

## 为什么单独一个文件

这个项目反复出现同一类缺陷：**系统用一个理由解释自己的行为，而那个理由不是真的。**

- 流式路径把工具调用丢了，却告诉用户"模型只给出了思考内容"；
- 27B 那次"让用户去点他刚点过的那一步"，因为上下文里从来没写"用户点了它"，
  而驳回语是一句条件句（详见 `_M9` §6.4.4）。

两次的共同点：**信息在一层存在、在另一层缺失，而缺的那一层决定行为。**
所以这里不按"模块"组织测试，而按**跨层一致性**组织：

1. 模型看到的事实（渲染）与授权层的事实（手势令牌）必须是同一件事；
2. 工具声明（模型据此选工具）与实现（实际会不会受理）必须一致；
3. 被驳回之后，驳回语必须**按真实状态**给出下一步 ——
   并有一条循环级测试证明"模型读了驳回语、换了工具、事情办成了"。

第 3 条尤其重要：它是"可恢复性"的证明。一个只会拒绝的系统与一个坏掉的系统
在用户看来没有区别。
"""

from __future__ import annotations

import pytest

from app.agent.llm import FakeLLM, call, reply
from app.agent.loop import SYSTEM_PROMPT, AgentLoop, StopReason, _render_context
from app.agent.tools import GESTURE_PLAN_CHANGE, get_tool, iter_tools
from app.session_domain.context import assemble_discussion_context
from app.session_domain import reads as session_reads
from app.session_domain import writes as session_writes
from app.global_domain import reads as global_reads


# --------------------------------------------------------------------------- #
# 1. 渲染层：用户点没点，模型必须看得见
# --------------------------------------------------------------------------- #


def _first_step_id(sandbox) -> str:
    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    return session_reads.plan_steps(sandbox.db, plan.plan_id)[0].step_id


def _render(sandbox, clicked: str | None, *, gesture: bool | None = None) -> str:
    """`gesture=None` 时按"端点会怎么做"推导（`step_id` 非空 → 加令牌）。"""
    anchor = clicked or _first_step_id(sandbox)
    context = assemble_discussion_context(sandbox.db, sandbox.session_id, anchor)
    if gesture is None:
        gesture = clicked is not None
    return _render_context(
        context, click_gesture=gesture, clicked_step_id=clicked
    )


def test_a_click_is_rendered_as_a_fact_not_hidden(sandbox) -> None:
    """**这是那次 27B 误判的根因所在。**

    点了计划项时，上下文里必须明确写出"用户此刻点开了它"。
    只写「当前这一步」是不够的 —— 模型读不出"这是我点的"与
    "这只是计划碰巧走到这里"的区别，于是它会去要一个**已经发生过的**动作。
    """
    text = _render(sandbox, clicked="ST-001")

    assert "用户此刻的动作" in text
    assert "他点开了计划项 ST-001" in text
    # 主关键词：入口条件已被满足，且**不要再让用户点**。
    assert "propose_plan_change" in text
    assert "不要再让他" in text or "不要让他再点一次" in text
    assert "他没有点任何计划项" not in text


def test_no_click_is_also_rendered_as_a_fact(sandbox) -> None:
    """没点的时候也要写出来。

    只写"点了"那一种、不写"没点"，模型就得靠**缺失**去推断 ——
    而"缺失"既可能是没点，也可能是我们忘了渲染。它只能猜。
    """
    text = _render(sandbox, clicked=None)

    assert "用户此刻的动作" in text
    assert "他没有点任何计划项" in text
    assert "用不了" in text or "用不了" in text
    assert "update_task_plan" in text


def test_the_rendered_click_and_the_gesture_token_agree(sandbox) -> None:
    """授权层与信息层必须同时成立。

    `discuss.py` 的规则是"`step_id` 非空 → 加 `plan_change` 令牌"。
    渲染层的规则是"**有令牌** → 写出他点了"，而且与工具层读的是
    **同一个变量**（`AgentLoop.user_gestures`），因此两处不可能给出不一致的答案。

    为什么不是"`step_id` 非空 → 写出他点了"：那样两层读的是两个不同输入，
    调用方一旦给了手势却没给 `step_id`（或反过来），授权与信息就会分叉。
    分叉的两个方向都已经真实发生过：
    授权给了、模型却不知道自己有权（那次 27B 的处境），
    以及模型以为自己有权、工具却拒绝它。

    第三种情况单独测：**有手势、没指名哪一项** —— 那一格不能渲染成"他没点"。
    """
    tool = get_tool("propose_plan_change")
    for clicked, gesture in (("ST-001", True), (None, False), (None, True)):
        rendered = _render(sandbox, clicked=clicked, gesture=gesture)
        model_told = "他点开了某个计划项" in rendered or "他点开了计划项" in rendered
        assert gesture == model_told, (
            f"clicked={clicked} gesture={gesture}：授权={gesture}，"
            f"但模型被告知={model_told} —— 两层必须读同一个来源"
        )
    # 有手势但没指名：不能让模型以为"用不了"（工具层会放行）
    anonymous = _render(sandbox, clicked=None, gesture=True)
    assert "用不了" not in anonymous
    assert "入口条件已满足" in anonymous
    assert tool.name == "propose_plan_change"


# --------------------------------------------------------------------------- #
# 2. 声明层：模型据此选工具，因此不许与实现矛盾
# --------------------------------------------------------------------------- #


def test_every_tool_has_a_non_trivial_summary() -> None:
    """没有说明的工具等于要求模型猜它的入口条件。"""
    for spec in iter_tools():
        assert spec.summary.strip(), f"{spec.name} 没有说明"
        assert len(spec.summary.strip()) >= 20, f"{spec.name} 的说明太短，等于没说"


def test_both_plan_tools_state_their_entry_condition_first() -> None:
    """两个改计划的工具**开头就是入口条件**。

    顺序不是排版问题：模型先读到的那一句决定了它的第一判断，
    而这两个工具是唯一一对"条件互斥、看着都能用"的工具。
    """
    update = get_tool("update_task_plan").summary
    propose = get_tool("propose_plan_change").summary

    assert update.strip().startswith("**入口条件"), update[:40]
    assert propose.strip().startswith("**入口条件"), propose[:40]
    assert "用户没有点" in update[:60]
    assert "用户本人点了" in propose[:60]


def test_the_steps_semantics_is_stated_identically_in_both_summaries() -> None:
    """方案 B 的核心语义（整份列表 / 没列出来的会被删掉）必须在两个工具里说法一致。

    一个说法有、另一个没有，模型就会以为"那两个工具的 steps 语义不同"——
    而它选哪个工具是看情况的，于是同一个动作在两条路上产生不同结果。
    """
    for name in ("update_task_plan", "propose_plan_change"):
        summary = get_tool(name).summary
        assert "没列出来的步骤会被删掉" in summary, f"{name} 没写清 steps 的整份语义"


def test_both_summaries_warn_against_rewriting_checkpoints() -> None:
    """`checkpoints` 的不对称（键不传 = 保留）必须在两个工具里都说。

    实测踩过：模型改步骤时顺手带上 `checkpoints`，漏了 `check_method`，
    整次调用被拒 —— 而它本来**根本不需要**碰检查点。
    """
    for name in ("update_task_plan", "propose_plan_change"):
        assert "checkpoints" in get_tool(name).summary, f"{name} 没提 checkpoints"


def test_the_gesture_tool_says_the_click_is_already_done(sandbox) -> None:
    """声明里必须要求模型**不要**让用户再点一次。

    否则模型即使知道"点了"，也可能回一句"请点击计划项" ——
    因为工具描述没告诉它"点过了就是点过了"。
    """
    summary = get_tool("propose_plan_change").summary
    assert "再点" in summary or "不要让他" in summary


def test_the_system_prompt_names_both_entry_conditions() -> None:
    """系统提示词里那张"入口条件 → 用哪个工具"的表必须存在。

    工具声明是**选项**，提示词是**规则**。只有选项没有规则时，
    模型要自己从三份长文本里归纳出规则 —— 那是它最容易归纳错的地方。
    """
    assert "propose_plan_change" in SYSTEM_PROMPT
    assert "update_task_plan" in SYSTEM_PROMPT
    assert "用户本人点了某个计划项" in SYSTEM_PROMPT
    assert "落在库里的证据" in SYSTEM_PROMPT
    assert "不要猜他有没有点" in SYSTEM_PROMPT
    assert "checkpoints" in SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# 3. 行为层：驳回语按真实状态分支 + 模型能自己改对
# --------------------------------------------------------------------------- #


def _plan_steps(sandbox) -> list[str]:
    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    return [step.action for step in session_reads.plan_steps(sandbox.db, plan.plan_id)]


class _RecordingLLM:
    """转发给替身，**并记下每一轮实际收到的 messages**。

    这个记录是"渲染层真的起作用了"唯一的证据来源：
    光断言 `_render_context()` 的输出（上面那几条）只能证明那个函数写对了，
    证明不了它**被送到了模型面前**。两者之间是可以断的
    （比如 `_walk` 里忘了把 `clicked_step_id` 传下去）——
    而那种断法恰好就是原缺陷的形状：事实存在，但不在模型看得见的地方。
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.seen: list[list] = []
    def chat(self, messages, *, tools=()):  # noqa: ANN001, ANN201
        self.seen.append(list(messages))
        return self._inner.chat(messages, tools=tools)

    def stream(self, messages, *, tools=()):  # noqa: ANN001, ANN201
        self.seen.append(list(messages))
        return self._inner.stream(messages, tools=tools)


def test_the_rejection_tells_the_truth_about_the_click(sandbox) -> None:
    """用户**点了**、但没有落库证据、模型却调了 `update_task_plan`：

    驳回语必须说"用户已经点了，改用 propose_plan_change"，
    **不能**说"他点开那个计划项就能改"（条件句会被读成"还没点"）。
    """
    llm = FakeLLM(
        [
            reply(
                "我改一下计划。",
                call(
                    "update_task_plan",
                    reason="用户觉得第一步太多",
                    diff={"steps": [{"step_id": "ST-001", "action": "少一点", "output": "记录"}]},
                ),
            ),
            reply("好，我用正确的方式再来一次。"),
        ]
    )
    result = AgentLoop(
        sandbox.db,
        sandbox.session_id,
        llm,
        user_gestures=frozenset({GESTURE_PLAN_CHANGE}),
    ).run("第一步太多了")

    assert result.stop_reason is StopReason.COMPLETED
    rejection = next(t for t in result.traces if t.status == "rejected")
    # 事实要说对
    assert "用户已经点了那个计划项" in rejection.message
    # 出路要指名
    assert "propose_plan_change" in rejection.message
    # **不要**让他去点已经点过的东西
    assert "不要回复用户让他去点击" in rejection.message
    # 旧的那句条件句不能再出现（它就是误导源）
    assert "他点开那个计划项说要改，就能改" not in rejection.message


def test_the_rejection_when_nobody_clicked_is_different(sandbox) -> None:
    """没人点时驳回语是**另一句**：这时候才该让用户去点。

    两条分支必须真的不同 —— 否则"按真实状态分支"就成了一句注释。
    """
    llm = FakeLLM(
        [
            reply(
                "我改一下计划。",
                call(
                    "update_task_plan",
                    reason="我觉得有必要",
                    diff={"steps": [{"step_id": "ST-001", "action": "少一点", "output": "记录"}]},
                ),
            ),
            reply("那我先不改。"),
        ]
    )
    result = AgentLoop(
        sandbox.db, sandbox.session_id, llm, user_gestures=frozenset()
    ).run("第一步太多了")

    rejection = next(t for t in result.traces if t.status == "rejected")
    assert "用户也**没有点任何计划项**" in rejection.message
    assert "点开那个计划项再说" in rejection.message
    assert "用户已经点了那个计划项" not in rejection.message


def test_the_model_can_read_the_rejection_and_switch_tools(sandbox) -> None:
    """**可恢复性的证明**：选错工具 → 读驳回语 → 换对工具 → 计划真的改了。

    这一条覆盖的是"多测试联合"里最后那一环：
    前两条测的是"驳回语说得对不对"，这一条测的是"**说得对有没有用**"。
    一个只会拒绝、拒绝之后模型仍然原地打转的系统，与坏掉的系统等价 ——
    用户看到的是同一件事：什么都没发生。

    顺带钉住渲染层的效果：模型在**第二轮**收到的消息里，
    必须看得到"他点开了计划项"这句事实。
    """
    # 第一轮：故意选错工具（这是真实运行里 27B 干的事）
    llm = FakeLLM(
        [
            reply(
                "我把第一步拆细。",
                call(
                    "update_task_plan",
                    reason="用户觉得一次太多",
                    diff={
                        "steps": [
                            {"step_id": "ST-001", "action": "每天过 20 个新词", "output": "20 词的记录"},
                            {"step_id": "ST-002", "action": "读技术文档并标注生词", "output": "标注过的文档"},
                        ]
                    },
                ),
            ),
            # 第二轮：读了驳回语之后换成正确的入口
            reply(
                "我换一种方式改。",
                call(
                    "propose_plan_change",
                    target="ST-001",
                    user_view="一次 50 个新词太多了",
                    suggested_change={
                        "steps": [
                            {"step_id": "ST-001", "action": "每天过 20 个新词", "output": "20 词的记录"},
                            {"step_id": "ST-002", "action": "读技术文档并标注生词", "output": "标注过的文档"},
                        ],
                        "change": "第一步从 50 个降到 20 个",
                    },
                ),
            ),
            reply("改好了：第一步现在每天 20 个新词。"),
        ]
    )
    recorder = _RecordingLLM(llm)
    result = AgentLoop(
        sandbox.db,
        sandbox.session_id,
        recorder,
        user_gestures=frozenset({GESTURE_PLAN_CHANGE}),
        # 这两件事在真实系统里由 `discuss.py` 从**同一个 `step_id`** 推出来：
        # 令牌加上、并把 `step_id` 传给循环。这里照做，否则测的就不是真实形状。
    ).run("第一步太多了", step_id="ST-001")

    assert result.stop_reason is StopReason.COMPLETED
    # 真的改了（不是"说了要改"）
    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    assert plan.version == 2, "模型读了驳回语、换了工具，但计划没变 —— 闭环没通"
    assert _plan_steps(sandbox)[0] == "每天过 20 个新词"
    # 工具序列：先错、后对
    tools = [t.tool for t in result.traces]
    assert tools == ["update_task_plan", "propose_plan_change"]
    assert [t.status for t in result.traces] == ["rejected", "success"]

    # **渲染层真的起作用了**：模型实际收到的消息里看得到"他点了"。
    # 看的是第一轮（从一开始它就该知道），而不是"被驳回之后才补给它"。
    first_round = recorder.seen[0]
    system_text = "\n".join(m.content for m in first_round if m.role == "system")
    assert "他点开了计划项 ST-001" in system_text, (
        "模型收到的 system 消息里没有'他点了'这句事实 —— "
        "那么它选择 update_task_plan 就是**我们没有告诉它**，而不是它判断错"
    )


def test_a_model_that_repeats_the_wrong_call_is_stopped_with_the_reason(sandbox) -> None:
    """拒绝之后**原样**再试一次 → 停下来，并把最后那条理由端给用户。

    这是"零进展"停止理由在调用层的一个实例：不做这件事的话，
    用户会看到模型把同一个错误调用复读八次。
    """
    wrong = call(
        "update_task_plan",
        reason="我觉得有必要",
        diff={"steps": [{"step_id": "ST-001", "action": "少一点", "output": "记录"}]},
    )
    llm = FakeLLM([reply("改。", wrong), reply("我再试一次。", wrong)])
    result = AgentLoop(
        sandbox.db, sandbox.session_id, llm, user_gestures=frozenset()
    ).run("第一步太多了")

    assert result.stop_reason is StopReason.REJECTED_BY_DISPATCHER
    assert "原样又试了一次" in result.reply
    assert _plan_steps(sandbox)[0] == "每天用 Anki 过 50 个新词"  # 计划没被动过


# --------------------------------------------------------------------------- #
# 4. 会话资料（用户上传的文件）：清单 ↔ 取正文 ↔ 提示词，三者一致
# --------------------------------------------------------------------------- #
#
# 这一节钉的是一个**新的失败形态**：清单在上下文里、正文在库里，
# 而"该去取"这件事只能靠提示词约束。本地与商用模型都可能出现
# "看到了清单却没去读，然后回答'我没看到文件'" —— 那正是这一次要防的事。
# 所以断言分三处：清单真的渲染出来了、工具真的能取到、
# 提示词真的把"先去看再回答"写成了规则。


def _upload(sandbox, filename: str, text: str) -> str:
    from app.session_domain import writes as session_writes

    result = session_writes.attach_file(
        sandbox.db, sandbox.session_id, filename=filename, raw=text.encode()
    )
    sandbox.db.flush()
    return result.attachment.attachment_id


def test_attachment_brief_is_rendered_but_the_body_is_not(sandbox) -> None:
    """清单必须渲染出来，而**正文不进上下文** —— 只有开头 150 字的预览。

    "正文不进上下文"这句话要说准：短文件（<150 字）的预览**就是全文**，
    这是对的（它本来就只有 150 字，再截就没法判断"这是不是我要找的那份"）。
    真正要钉的是**上界**：无论文件多长，进上下文的那一份都不超过预览长度。
    所以要测的是"尾巴不在里面"，而不是"一个字都不在"。
    """
    head = "开头这句必须在（用来判断是不是我要找的那份）。"
    tail = "结尾这句独特的话XYZ绝对不该出现在上下文里。"
    body = head + "中间" * 300 + tail
    attachment_id = _upload(sandbox, "我的经历.md", body)

    text = _render(sandbox, clicked="ST-001")

    assert "用户上传的资料" in text
    assert attachment_id in text
    assert "我的经历.md" in text
    assert head in text, "预览是显式的上界 —— 开头必须在"
    assert tail not in text, "正文尾巴不该进上下文 —— 它按需用 read_attachment 取"
    assert f"{len(body)} 字" in text, "清单要报全文长度，否则模型不知道还有多少没看"


def test_a_huge_attachment_does_not_grow_the_context(sandbox) -> None:
    """上下文的大小**与文件长度无关**。

    这是"清单常驻 + 按需取正文"这个形状的全部意义。它一旦被破坏，
    症状不是报错而是**悄悄变笨**：窗口被一份文件占满，
    真正重要的偏好/检查点被挤出去，而没有任何一条日志会说哪里不对。
    """
    _upload(sandbox, "短.md", "短内容")
    short_render = len(_render(sandbox, clicked="ST-001"))

    _upload(sandbox, "巨大.md", "字" * 150_000)
    long_render = _render(sandbox, clicked="ST-001")

    # 多出来的只有清单那一行（文件名 + 字数 + 150 字预览）与题头，
    # 远小于文件本身的 15 万字。给一个宽松但有效的上界：< 2000 字。
    assert len(long_render) - short_render < 2000, (
        f"上传一份 15 万字的文件让上下文涨了 {len(long_render) - short_render} 字 —— "
        "正文漏进上下文了"
    )


def test_attachment_section_is_the_first_section_rendered(sandbox) -> None:
    """渲染顺序必须与 `precedence_order` 一致（资料 = 0，排在最前）。

    两处说法不同就是又一次"信息在一层、不在另一层"：界面按 `precedence_order`
    向用户解释"系统先看什么"，而模型看到的是另一个顺序。
    """
    _upload(sandbox, "a.md", "内容")
    text = _render(sandbox, clicked="ST-001")

    attachment_at = text.index("## 用户上传的资料")
    constraint_at = text.index("## 用户亲口下达的约束")
    assert attachment_at < constraint_at, "资料那一节必须排在偏好之前（precedence=0）"

    pack = assemble_discussion_context(
        sandbox.db, sandbox.session_id, _first_step_id(sandbox)
    )
    names_in_order = [s.name for s in sorted(pack.sections, key=lambda s: s.precedence)]
    assert names_in_order[0] == "会话资料（用户上传的清单）"


def test_truncated_attachment_is_flagged_in_the_context(sandbox) -> None:
    """上传时被截断过 → 上下文里必须写明。

    不写的话模型会把半份材料当成完整的，然后基于一个不完整的事实下结论 ——
    与"prompt 装不下"是同一类失败。
    """
    from app.attachments import MAX_CHARS

    _upload(sandbox, "很长.md", "字" * (MAX_CHARS + 100))
    text = _render(sandbox, clicked="ST-001")
    assert "上传时已被截断" in text
    assert "不要把它当成完整材料" in text


def test_read_attachment_lists_everything_without_args(sandbox) -> None:
    """不带参数 = 列清单。

    这是"上下文清单只有最近 20 份"的出口：模型必须有一个办法看到全部，
    否则第 21 份资料对它就**不存在**，而用户以为传过了。
    """
    first = _upload(sandbox, "a.md", "内容 A")
    second = _upload(sandbox, "b.md", "内容 B")

    tool = get_tool("read_attachment")
    from app.agent.tools import ToolContext

    result = tool.handler(ToolContext(db=sandbox.db, session_id=sandbox.session_id), {})
    assert result.status.value == "success"
    ids = [item["attachment_id"] for item in result.data["items"]]
    assert ids == [first, second]
    assert "要读哪一份" in result.message


def test_read_attachment_returns_a_slice_and_says_what_is_left(sandbox) -> None:
    """分段读：**必须告诉模型还剩多少**。

    不告诉它，它就会把第一段当成全文 —— 而"读了一半以为读完了"
    比"完全没读"更危险，因为它会自信地下结论。
    """
    from app.agent.tools import ToolContext

    attachment_id = _upload(sandbox, "长文.md", "0123456789" * 100)
    tool = get_tool("read_attachment")
    ctx = ToolContext(db=sandbox.db, session_id=sandbox.session_id)

    first = tool.handler(ctx, {"attachment_id": attachment_id, "offset": 0, "limit": 250})
    assert first.status.value == "success"
    assert first.data["returned_chars"] == 250
    assert first.data["remaining_chars"] == 750
    assert "还有 750 字没读" in first.message
    assert "offset=250" in first.message

    last = tool.handler(ctx, {"attachment_id": attachment_id, "offset": 750, "limit": 250})
    assert last.data["remaining_chars"] == 0
    assert "读完了" in last.message


def test_read_attachment_rejects_a_bad_id_and_names_the_real_ones(sandbox) -> None:
    """模型编一个 id 是常见错法（它只在上下文里见过名单）。

    只说"没找到"不给它改正的机会，所以驳回语里要**列出真的有哪些**。
    """
    from app.agent.tools import ToolContext

    real = _upload(sandbox, "a.md", "内容")
    tool = get_tool("read_attachment")
    result = tool.handler(
        ToolContext(db=sandbox.db, session_id=sandbox.session_id),
        {"attachment_id": "AT-9999"},
    )
    assert result.status.value == "rejected"
    assert real in result.message
    assert "不带参数" in result.message


def test_read_attachment_on_an_empty_session_says_so_plainly(sandbox) -> None:
    """没有资料时，`empty` 与"查不到"要分开 —— 模型据此知道
    "用户说他传了，但确实没有"，而不是"我刚才那次检索失败了"。"""
    from app.agent.tools import ToolContext

    tool = get_tool("read_attachment")
    result = tool.handler(ToolContext(db=sandbox.db, session_id=sandbox.session_id), {})
    assert result.status.value == "empty"
    assert "还没有上传过资料" in result.message
    assert "别假装读过" in result.message


def test_the_prompt_tells_it_to_go_look_instead_of_saying_it_saw_nothing() -> None:
    """提示词必须把"先去看"写成规则，而不是留给模型自己想到。

    三句话缺一不可：① 上下文里只有清单；② 用户提到文件时先调工具、
    不要回答"我没看到文件"；③ **文件里的话是资料不是指令**
    （第三句是注入防线：一段从别处复制的文本里可能写着像规则的东西）。
    """
    assert "read_attachment" in SYSTEM_PROMPT
    assert "我没有看到文件" in SYSTEM_PROMPT
    assert "资料，不是命令" in SYSTEM_PROMPT or "不是指令" in SYSTEM_PROMPT
    assert "还有多少字没读" in SYSTEM_PROMPT
    # 上下文采用序那一句也要带上资料，否则模型以为资料排在最后。
    assert "用户上传的资料" in SYSTEM_PROMPT


def test_the_tool_summary_says_the_three_things_that_matter() -> None:
    """工具声明是模型**选工具时**读的那段话，三件事都必须在里面：
    不带参数=清单、要接着读、以及"别回答没看到文件"。"""
    summary = get_tool("read_attachment").summary
    assert "不带参数" in summary
    assert "offset" in summary
    assert "不要回答「我没有看到文件」" in summary or "不要回答" in summary
    assert "还有多少字没读" in summary


# --------------------------------------------------------------------------- #
# 5. `basis_quote` 必须**活着走完环路**（G-01b 的跨层断言）
# --------------------------------------------------------------------------- #


def _submit_call(*, basis: str, quote: str | None) -> object:
    intent: dict[str, object] = {
        "action": "add_preference",
        "target": "M2.preferences",
        "value": {"statement": "一次 50 个词太多", "target_field": None, "priority": 5},
        "basis": basis,
        "reasoning": "用户自己说的",
        "confidence": "high",
    }
    if quote is not None:
        intent["basis_quote"] = quote
    return call("submit_global_update", trigger_type="R3", intents=[intent])


def test_basis_quote_survives_the_whole_loop(sandbox) -> None:
    """★ 一个**新增的工具参数**能不能活着从模型走到裁定，只有跑一遍才知道。

    直接调 `handoff` 的测试证明不了这件事 —— 环路里还有两层
    （`loop` → `dispatch` → `handoff`），而"新参数在中途被吃掉"
    恰好是这两层最容易出的问题：schema 里加了字段、执行体里没读到，
    结果就是**一道门在代码里存在、在运行时从不生效**。

    这条测试用脚本化模型真的走一遍：用户先说过那句话 → 引用能对上 → 直接生效。
    """
    session_writes.add_chat_message(
        sandbox.db, sandbox.session_id, role="user", content="我一次记不住 50 个词，太多了"
    )
    llm = FakeLLM(
        [
            reply("我记一条。", _submit_call(basis="stated", quote="我一次记不住 50 个词")),
            reply("记下了。"),
        ]
    )
    result = AgentLoop(sandbox.db, sandbox.session_id, llm).run("我记不住 50 个词")

    assert result.stop_reason is StopReason.COMPLETED
    submit = next(t for t in result.traces if t.tool == "submit_global_update")
    assert submit.status == "success"
    assert "已生效" in submit.message, (
        "`basis_quote` 没能活着走完环路 —— 那道引用核对在运行时从不生效"
    )
    assert "降级" not in submit.message
    assert global_reads.list_preferences(sandbox.db) != []


def test_a_bad_quote_is_downgraded_through_the_whole_loop(sandbox) -> None:
    """反方向：引用对不上 → 走完整环路后是**待确认**，而且模型被告知了原因。

    与上一条配对，因为"新参数被吃掉"有两种表现：要么它总是失败，
    要么它总是通过。两条一起测，才把中间那条正确的路夹住。
    """
    session_writes.add_chat_message(
        sandbox.db, sandbox.session_id, role="user", content="我传了几份资料，你看看"
    )
    llm = FakeLLM(
        [
            reply(
                "我记一条。",
                _submit_call(basis="stated", quote="每天背 200 个词，这是我给自己定的铁律"),
            ),
            reply("好，那要你确认一下。"),
        ]
    )
    result = AgentLoop(sandbox.db, sandbox.session_id, llm).run("看看我的资料")

    submit = next(t for t in result.traces if t.tool == "submit_global_update")
    assert submit.status == "success", "降级不是失败"
    assert "降级" in submit.message
    assert "还没有生效" in submit.message
    assert global_reads.list_preferences(sandbox.db) == []
