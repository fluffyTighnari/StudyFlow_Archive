"""工具层：**声明的边界必须与执行的边界一致**。

M8 把 14 个工具写成了三张表，每张表最后一列是「边界」。那些边界如果只写在
文档里、只写在 `if` 里、或只写在 description 里，任何一种都只是三分之一个保障：

- 只写文档 → 模型读不到，人会忘；
- 只写 `if` → 模型只会拿到一次 `rejected`，然后不知道为什么；
- 只写 description → 那是一句请求，不是一条规则。

所以这里对**同一条边界**分别断言"说明里有它"和"执行时会拦"，两边都测。
"""

from __future__ import annotations

import json

import pytest

from app.agent import tools as T
from app.agent.tools import _steps_from
from app.global_domain import reads as global_reads
from app.session_domain import reads as session_reads
from app.session_domain import writes as session_writes

#: M8 §4 的工具名，逐字抄自文档的表（P6+.5 起是 15 个，P13 起是 16 个）。
M8_TOOL_NAMES = [
    "retrieve_folder",
    "retrieve_session",
    "read_attachment",
    "retrieve_cognitive",
    "retrieve_strategy",
    "ask_about_task",
    "update_task_definition",
    "update_motivation",
    "write_chat_note",
    "create_task_plan",
    "update_task_plan",
    "propose_plan_change",
    "write_stage",
    "mark_step_done",
    "checkpoint_milestone",
    "submit_global_update",
]


# --------------------------------------------------------------------------- #
# 结构：工具集本身就是一处契约
# --------------------------------------------------------------------------- #


def test_there_are_exactly_fifteen_tools_with_the_names_m8_declares() -> None:
    """工具数是**设计决定**，不是实现的副产物。

    M8 §4.5 特意说明检查点结果由用户 UI 记录、「不新增 LLM 工具，工具数保持 14」——
    也就是说这个数字曾经被讨论过。它被钉住之后，任何"顺手加一个工具"都会被拦下。

    **P6+.5 从 14 改成 15**（用户裁定「让 chat 支持文件」）：`read_attachment`
    是第一个**因为用户上传了东西**而必须存在的工具 —— 上传的资料必须有一个
    取全文的入口，否则"清单在上下文里、正文永远进不来"。
    加它是权衡过的：工具数增长（模型的选择面变大）是代价，
    换来的是资料不必挤进上下文。写在这里，是为了让下一个人知道
    这个数字是**讨论过的**，而不是随手改的。

    **P13 从 15 改成 16**：`ask_about_task`（任务不明确时反问）。
    它是被一个结构性事实逼出来的 —— **模型改不了 `goal`**
    （改定义区需要用户自己的手势，M8 §4.2），所以目标不明时它只有
    "问"与"猜"两条路。没有这个工具，第二条路是唯一被实现的那条：
    它会自己选一个范围，产出一份看起来完整的计划。
    这一条同样是讨论过的：多一个工具 = 模型的选择面变大，
    而换来的是"计划建在猜出来的任务上"这件事不再无声发生。
    """
    assert [spec.name for spec in T.iter_tools()] == M8_TOOL_NAMES
    assert len(T.TOOLS) == 16


def test_only_the_global_tool_lacks_a_handler() -> None:
    """`handler is None` ⟺ 全局作用域。

    全局工具**故意没有 handler**：它的执行权在 `handoff`（ARCH-12）。
    这不是缺口 —— 只要工具层拿不到它的 handler，"绕过 handoff 直接写全局"
    在结构上就不成立，而不是靠一句注释提醒后来的人。
    """
    for spec in T.iter_tools():
        if spec.scope is T.ToolScope.GLOBAL:
            assert spec.handler is None, f"{spec.name} 是全局工具，不该有 handler"
        else:
            assert spec.handler is not None, f"{spec.name} 没有 handler，它就不会被执行"


def test_exactly_one_global_tool_and_it_is_the_guarded_one() -> None:
    """全局写入只有一条路。多出第二条就必须被看见。"""
    global_tools = [s.name for s in T.iter_tools() if s.scope is T.ToolScope.GLOBAL]
    assert global_tools == ["submit_global_update"]


def test_read_tools_are_declared_read_only() -> None:
    """检索类工具分两档，**不能合成一档**。

    `READ`（不碰任何会话）与 `SESSION_READ`（需要一个会话）在只读这件事上
    完全一样 —— 它们都不改数据、不需要手势、可以被重试。
    分成两档**只为一件事**：让"侧边栏能用哪些工具"变成一条**可推导的**规则
    （`SIDEBAR_TOOL_SCOPES = {READ, GLOBAL}`），而不是一份手写的名单。

    合回一档的代价是具体的：侧边栏会把 `retrieve_session` 暴露给模型，
    模型不带 `session_id` 调它，于是它拿哨兵值去查一个叫 `sidebar` 的会话，
    报出「会话不存在：sidebar」——**那条错误看起来像数据问题**。

    `read_attachment`（P6+.5）留在只读名单里的意义也不只是"它不改数据"：
    只读工具**不需要手势、不需要理由、可以被重试**，
    而"读到一半接着读"必须能反复调 —— 如果它被误标成写工具，
    分段读第二段就会被当成一次重复调用而被零进展检测拦下。
    """
    read_only = {s.name for s in T.iter_tools() if s.scope is T.ToolScope.READ}
    session_read = {
        s.name for s in T.iter_tools() if s.scope is T.ToolScope.SESSION_READ
    }
    assert read_only == {
        "retrieve_folder",
        "retrieve_cognitive",
        "retrieve_strategy",
    }
    assert session_read == {"retrieve_session", "read_attachment"}
    # ★ 两档合起来才是"能重试的只读工具"这个整体 —— 别让某一档悄悄变空。
    assert read_only | session_read == {
        "retrieve_folder",
        "retrieve_session",
        "read_attachment",
        "retrieve_cognitive",
        "retrieve_strategy",
    }
    # ★ 而侧边栏的作用域**恰好**是"不需要会话的那些"。
    assert T.SIDEBAR_TOOL_SCOPES == {T.ToolScope.READ, T.ToolScope.GLOBAL}


def test_every_tool_has_a_readable_description_and_an_object_schema() -> None:
    """模型看不到签名，只看得到 description。空描述等于没给它工具。"""
    for spec in T.iter_tools():
        assert len(spec.summary.strip()) >= 20, f"{spec.name} 的说明太短，模型读不出边界"
        assert spec.parameters.get("type") == "object", f"{spec.name} 的参数不是对象"
        assert "properties" in spec.parameters


def test_every_tool_schema_is_self_contained() -> None:
    """发出去的 schema 里不能有 `$ref`。

    pydantic 生成的 schema 用 `$ref` + `$defs`，而多数本地工具解析器对
    `$ref` 只有半实现 —— 结果是模型看到一个**空对象**，于是把参数猜成一团。
    这类失败不报错，只表现为"模型总是填不对"，然后人去怀疑模型笨。
    """
    for schema in T.tool_schemas():
        blob = json.dumps(schema.parameters, ensure_ascii=False)
        assert "$ref" not in blob, f"{schema.name} 的参数 schema 里有 $ref"
        assert "$defs" not in blob, f"{schema.name} 的参数 schema 里有 $defs"


def test_the_a19_statement_rule_reaches_the_model_through_the_real_contract() -> None:
    """M8 §4.6：A19 的「理由要一起写进来」必须出现在**工具 schema 生成的位置**。

    它是从 `M2PreferenceValue.statement` 的字段说明**生成**出来的，
    不是在这里手抄一遍 —— 手抄的那一份会在契约改动后悄悄过期，
    而那时模型看到的规范与实际校验的规范不一致，且没有任何测试会发现。
    """
    value_schema = T.TOOLS["submit_global_update"].parameters["properties"]["intents"][
        "items"
    ]["properties"]["value"]
    blob = json.dumps(value_schema, ensure_ascii=False)

    assert "理由" in blob, "A19 的规范没有传到工具 schema 里"
    assert "我讨厌看网课，因为浪费时间" in blob or "因为浪费时间" in blob
    # A20 的字段也一并传来了（同一条通路）
    assert "supersedes_pref_id" in blob
    # 三个 value 变体都在，且是内联的
    assert {b.get("title") for b in value_schema["anyOf"]} == {
        "M1ParameterValue",
        "M2PreferenceValue",
        "CEvidenceValue",
    }


def test_definition_whitelist_matches_the_write_layer() -> None:
    """工具里列的白名单必须与写入层真正校验的那一份**只差系统字段**。

    `INPUT_FIELDS` 多出 `intent_raw` / `intent_confidence` —— 这两个是系统字段，
    写入层认它们（侧页的草稿捕捉会写），但 M8 §4.2.1 明确**不接受模型直接写**。
    差别必须恰好是这两个：多一个说明工具漏了字段（模型改不了它该能改的），
    少一个说明工具放开了不该放的（模型能写系统字段）。
    """
    assert set(T.DEFINITION_FIELDS) < set(session_writes.INPUT_FIELDS)
    assert set(session_writes.INPUT_FIELDS) - set(T.DEFINITION_FIELDS) == {
        "intent_raw",
        "intent_confidence",
    }


def test_session_scopes_have_no_all_option() -> None:
    """`retrieve_session` 没有「全部」这个选项 —— 那是「禁止全量」的第一道闸。"""
    scope_schema = T.TOOLS["retrieve_session"].parameters["properties"]["scope"]
    assert scope_schema["enum"] == ["chat_log", "notes", "milestones", "task_plan"]
    assert "all" not in scope_schema["enum"]


# --------------------------------------------------------------------------- #
# 执行：说明里写的边界，真的会被拦
# --------------------------------------------------------------------------- #


def _run(name: str, ctx, **args) -> T.ToolResult:
    spec = T.TOOLS[name]
    assert spec.handler is not None
    return spec.handler(ctx, args)


def test_chat_log_retrieval_without_a_filter_is_rejected(sandbox) -> None:
    """「禁止全量」是这条工具唯一一条硬边界，所以它必须真的拦。

    不给它留「过滤器空着就等于不过滤」的后门：聊天记录是唯一无界的表，
    全量读进来会挤掉真正重要的上下文，而模型还会以为自己看到了全部。
    """
    result = _run("retrieve_session", sandbox.ctx(), scope="chat_log", filter={})
    assert result.status is T.ToolStatus.REJECTED
    assert "禁止全量" in result.message


def test_chat_log_retrieval_with_a_filter_works(sandbox) -> None:
    session_writes.add_chat_message(
        sandbox.db, sandbox.session_id, content="今天背了 50 个词", role="user"
    )
    result = _run(
        "retrieve_session", sandbox.ctx(), scope="chat_log", filter={"topic": "背"}
    )
    assert result.status is T.ToolStatus.SUCCESS
    assert result.data["items"][0]["content"] == "今天背了 50 个词"


def _user_says(ctx, text: str) -> None:
    """在**当前会话里**写一条 user 角色的 chat 消息。

    ★ 为什么这些用例必须造一条用户消息：`user_quote` 的证据来自
    「**这一轮用户最新那条 chat 消息**」逐字比对（`_quoted_user_words`），
    没有那条消息的话，任何引文都=找不到，测的就不是入口条件而是空库。
    """
    session_writes.add_chat_message(ctx.db, ctx.session_id, content=text, role="user")


def test_agent_cannot_change_the_task_definition_on_its_own(sandbox) -> None:
    """M8 §4.2：改目标这件事必须由目标的主人做。

    入口条件现在是「界面手势 **或** 可核对的引文」（政策变更：
    只认手势会挡住最正常的动作 —— 用户在聊天框里说"把目标改成 X"）。
    所以这里的拒绝语变了，但**拒绝本身没有变成"随便"**：
    既没有手势、又引不出他的一句原话时，仍然拒绝。
    """
    result = _run(
        "update_task_definition", sandbox.ctx(), field="goal", new_value="改成别的", reason="我觉得"
    )
    assert result.status is T.ToolStatus.REJECTED
    assert "user_quote" in result.message, "拒绝语要给成一条能照做的路"
    # 定义区没被动过
    assert session_reads.get_input(sandbox.db, sandbox.session_id).goal == (
        "两个月内能读懂技术文档"
    )

    # 侧页点了【编辑】并保存 → 这时才允许
    ok = _run(
        "update_task_definition",
        sandbox.ctx(f"{sandbox.gesture_definition}goal"),
        field="goal",
        new_value="三个月内能写技术文档",
        reason="用户自己改的",
    )
    assert ok.status is T.ToolStatus.SUCCESS
    assert ok.data["scope_change_suspected"] is False


def test_the_users_own_words_are_enough_to_change_the_definition(sandbox) -> None:
    """★ 政策变更的那一半：**引文**也能当入口条件（手势不是唯一的路）。

    判据仍然是可核对的：引文由服务端在用户刚写下的那条消息里逐字比对
    （忽略空白差异），模型编不出一句用户没说过的引文。
    """
    _user_says(sandbox.ctx(), "把目标改成三个月内能写技术文档吧，我急着用")
    changed = _run(
        "update_task_definition",
        sandbox.ctx(),
        field="goal",
        new_value="三个月内能写技术文档",
        reason="他自己说的",
        user_quote="改成三个月内能写技术文档",
    )
    assert changed.status is T.ToolStatus.SUCCESS, changed.message
    assert session_reads.get_input(sandbox.db, sandbox.session_id).goal == (
        "三个月内能写技术文档"
    )


def test_a_quote_the_user_never_said_is_refused(sandbox) -> None:
    """★ 反例（防的就是"模型替用户发言"）。

    用户这一轮只说了「今天有点累」。模型却带上一段**读起来很像正经要求**
    的引文 —— 那句话在他说过的话里逐字找不到，所以入口条件不成立，
    定义区一个字都不许改。
    """
    _user_says(sandbox.ctx(), "今天有点累")
    result = _run(
        "update_task_definition",
        sandbox.ctx(),
        field="goal",
        new_value="改成当翻译",
        reason="用户说要当翻译",
        user_quote="我要改成当翻译",
    )
    assert result.status is T.ToolStatus.REJECTED
    assert "user_quote" in result.message
    assert session_reads.get_input(sandbox.db, sandbox.session_id).goal == (
        "两个月内能读懂技术文档"
    )


def test_goal_change_without_a_reason_is_rejected(sandbox) -> None:
    result = _run(
        "update_task_definition",
        sandbox.ctx(f"{sandbox.gesture_definition}goal"),
        field="goal",
        new_value="新目标",
    )
    assert result.status is T.ToolStatus.REJECTED
    assert "reason" in result.message


def test_fields_outside_the_whitelist_are_rejected(sandbox) -> None:
    result = _run(
        "update_task_definition",
        sandbox.ctx(f"{sandbox.gesture_definition}intent_raw"),
        field="intent_raw",
        new_value="伪造的意图",
    )
    assert result.status is T.ToolStatus.REJECTED
    assert "白名单" in result.message


def test_agent_cannot_tick_a_step_done(sandbox) -> None:
    """M8 §4.5：打勾是**用户的判断**，所以要么他点了，要么他说过。

    ★ 政策变更：入口条件是「界面手势 **或** 可核对引文」。
    旧断言是"没有手势就拒，理由叫'一键操作'"；现在拒绝语换成了
    "把他说过的那句话带进来"，而拒绝本身仍然成立 —— 因为这一步
    没有任何证据表明是他自己说做完的。
    """
    result = _run("mark_step_done", sandbox.ctx(), step_id="ST-001")
    assert result.status is T.ToolStatus.REJECTED
    assert "user_quote" in result.message
    assert session_reads.plan_steps(
        sandbox.db, session_reads.require_plan(sandbox.db, sandbox.session_id).plan_id
    )[0].status.value == "pending"

    ok = _run(
        "mark_step_done", sandbox.ctx(f"{sandbox.gesture_step_done}ST-001"), step_id="ST-001"
    )
    assert ok.status is T.ToolStatus.SUCCESS


def test_the_users_own_words_are_enough_to_tick_a_step(sandbox) -> None:
    """★ 政策变更的那一半：他说"第一步做完了"，把那句原话带进来就能打勾。

    这条覆盖的正是旧政策挡住的最正常场景：用户在聊天框里说做完了，
    系统却回他"请自己去界面上点一下"。
    """
    _user_says(sandbox.ctx(), "第一步做完了，词都过了一遍")
    done = _run(
        "mark_step_done",
        sandbox.ctx(),
        step_id="ST-001",
        user_quote="第一步做完了",
    )
    assert done.status is T.ToolStatus.SUCCESS, done.message
    assert done.data["status"] == "done"


def test_a_quote_the_user_never_said_cannot_tick_a_step(sandbox) -> None:
    """★ 反例：他说的是"还没做完"，模型却引一句"做完了" —— 逐字找不到，拒绝。

    这是"模型替用户打勾"的那一格：打勾之后「做完了没有」就不再是他的判断了，
    所以这一格必须是硬的。
    """
    _user_says(sandbox.ctx(), "第一步还没做完，卡在生词太多了")
    result = _run(
        "mark_step_done",
        sandbox.ctx(),
        step_id="ST-001",
        user_quote="第一步做完了",
    )
    assert result.status is T.ToolStatus.REJECTED
    assert session_reads.plan_steps(
        sandbox.db, session_reads.require_plan(sandbox.db, sandbox.session_id).plan_id
    )[0].status.value == "pending"


def test_plan_cannot_be_changed_without_recorded_evidence(sandbox) -> None:
    """M8 §4.4：既无落库证据、又无"用户这次要求改"的入口条件 → rejected。

    旧断言（"先把当前这一步做完"）钉的是"没证据就不许动"；
    新判据下同一个拒绝还在，只是**可核对的开头多了一路**：他说过那句原话。
    两者都没有时仍然拒绝，并且在驳回语里说清缺的是哪一条、怎么补。
    """
    result = _run(
        "update_task_plan",
        sandbox.ctx(),
        reason="我觉得应该换个方法",
        diff={"steps": [{"step_id": "ST-001", "action": "改", "output": "产出"}]},
    )
    assert result.status is T.ToolStatus.REJECTED
    assert "user_quote" in result.message
    assert session_reads.require_plan(sandbox.db, sandbox.session_id).version == 1


def test_the_users_own_words_are_enough_to_change_the_plan(sandbox) -> None:
    """★ 政策变更：`update_task_plan` 的入口条件多了一条「他说了要改」。

    原来它只认落在库里的证据（失败检查点 / 未结案的 stage），于是用户在
    聊天框里说「第一步太重了」，模型没有任何合法的路可以改计划。
    """
    _user_says(sandbox.ctx(), "第一步太重了，改成每天 20 个吧")
    result = _run(
        "update_task_plan",
        sandbox.ctx(),
        reason="用户说第一步太重",
        user_quote="第一步太重了，改成每天 20 个",
        diff={
            "steps": [
                {"step_id": "ST-001", "action": "每天过 20 个新词", "output": "20 词的记录"},
                {"step_id": "ST-002", "action": "读一篇技术文档并标注生词", "output": "标注稿"},
            ],
            "change": "第一步从 50 个降到 20 个",
        },
    )
    assert result.status is T.ToolStatus.SUCCESS, result.message
    assert result.data["version"] == 2
    # 依据必须是**他那句原话**（可核对），而不是模型自己总结的一句
    assert "第一步太重了，改成每天 20 个" in result.data["evidence"]


def test_a_quote_the_user_never_said_cannot_change_the_plan(sandbox) -> None:
    """★ 反例：模型自己编一句「改轻一点」当引文 —— 用户没说，入口条件不成立。

    这条防的是"模型替用户发言"：它可以把任何一句更顺耳的话写成 `user_quote`，
    而系统必须能分辨"他说过"与"模型希望他说过"。
    """
    _user_says(sandbox.ctx(), "今天先这样吧")
    result = _run(
        "update_task_plan",
        sandbox.ctx(),
        reason="用户说要改轻一点",
        user_quote="我觉得太累了，改轻一点",
        diff={
            "steps": [
                {"step_id": "ST-001", "action": "每天过 20 个新词", "output": "20 词的记录"}
            ]
        },
    )
    assert result.status is T.ToolStatus.REJECTED
    assert session_reads.require_plan(sandbox.db, sandbox.session_id).version == 1


def test_a_quote_shorter_than_four_characters_is_not_a_quote(sandbox) -> None:
    """★ 反例：**太短的引文不算引文**（`core.quotes.MIN_QUOTE_CHARS = 4`）。

    「改」这一个字在他那句「把第一步改成每天 20 个新词吧」里逐字都有 ——
    如果"出现过"就够，那这道门对任何一句用户消息都自动成立。
    所以判据是"能指认出一句话"：去掉空白后至少 4 个字，且是连续原文。
    这条下限只在一个地方实现（`app.core.quotes.contains_quote`，路由层与
    判分层共用），这里的四个工具都走它 —— 于是它们不可能各自为政。

    这条与「编一句用户没说过的引文」是**两道不同的关卡**：那个是找不到，
    这个找到了也不能算。所以这里用真实出现过的短词，而不是假话。
    """
    _user_says(sandbox.ctx(), "把第一步改成每天 20 个新词吧")
    diff = {
        "steps": [
            {"step_id": "ST-001", "action": "每天过 20 个新词", "output": "20 词的记录"}
        ]
    }
    calls = [
        (
            "update_task_definition",
            {
                "field": "goal",
                "new_value": "改成每天 20 个",
                "reason": "用户说要改",
                "user_quote": "改",
            },
        ),
        ("mark_step_done", {"step_id": "ST-001", "user_quote": "改"}),
        (
            "update_task_plan",
            {"reason": "用户说要改", "user_quote": "改", "diff": diff},
        ),
        (
            "propose_plan_change",
            {
                "target": "ST-001",
                "user_view": "用户说第一步要改",
                "user_quote": "改",
                "suggested_change": diff,
            },
        ),
    ]
    for name, args in calls:
        result = _run(name, sandbox.ctx(), **args)
        assert result.status is T.ToolStatus.REJECTED, f"{name}：一个字的引文不该开闸"
    # 一个字都没落地：定义区与计划都还是原样
    assert session_reads.get_input(sandbox.db, sandbox.session_id).goal == (
        "两个月内能读懂技术文档"
    )
    assert session_reads.require_plan(sandbox.db, sandbox.session_id).version == 1


def test_exactly_four_characters_is_enough_to_quote_the_user(sandbox) -> None:
    """★ 下限的**边界**：去掉空白正好 4 个字就放行（不是"还要更长"）。

    这条与上一条配对，钉住阈值的位置而不是"大概"：`每天 20` 去空白后是
    `每天20` 四个字、且是原文里连续的一段 —— 它已经能指认一句话了。
    """
    _user_says(sandbox.ctx(), "把第一步改成每天 20 个新词吧")
    changed = _run(
        "update_task_plan",
        sandbox.ctx(),
        reason="用户说每天 20 个就够",
        user_quote="每天 20",
        diff={
            "steps": [
                {"step_id": "ST-001", "action": "每天过 20 个新词", "output": "20 词的记录"}
            ]
        },
    )
    assert changed.status is T.ToolStatus.SUCCESS, changed.message
    assert changed.data["version"] == 2


def test_a_failed_checkpoint_is_enough_evidence_to_change_the_plan(sandbox) -> None:
    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    session_writes.record_checkpoint(
        sandbox.db,
        sandbox.session_id,
        cp_id="CP-001",
        result="fail",
        expected_version=plan.version,
        result_note="正确率只有 60%",
    )
    result = _run(
        "update_task_plan",
        sandbox.ctx(),
        reason="检查点没过，回到上一批重刷",
        diff={
            "steps": [
                {"step_id": "ST-001", "action": "重刷上一批 50 个词", "output": "重刷记录"},
                {"step_id": "ST-002", "action": "读技术文档", "output": "标注过的文档"},
            ],
            "change": "把第一步换成重刷",
        },
    )
    assert result.status is T.ToolStatus.SUCCESS
    assert "CP-001" in result.data["evidence"]


def test_propose_plan_change_also_needs_the_user_to_have_asked(sandbox) -> None:
    """**这条断言是补上去的，它钉住一处真实的漏洞。**

    `propose_plan_change` 原先完全不查入口条件 —— 它唯一的依据是模型自己写的
    那段 `user_view`。于是"用户觉得太慢"这句话由模型说了算，
    而没有任何东西验证过它；面向用户的工具反而比面向模型的
    `update_task_plan` 更松。这正是「系统用一个理由解释自己的行为，
    而那个理由没人验证过」的第 7 次重演，区别只在于这次的"理由"是模型产的。

    注意这条测试里 `user_view` 写得很有说服力 —— 那正是问题所在：
    **一段读起来像证据的话不是证据。** 入口条件现在有两条可核对的路
    （界面手势 / 他的原话），而 `user_view` 一条都不算。
    """
    result = _run(
        "propose_plan_change",
        sandbox.ctx(),
        target="ST-001",
        user_view="用户说这一步太慢了，他记不住 50 个词",
        suggested_change=_steps_arg(),
    )
    assert result.status is T.ToolStatus.REJECTED
    assert "user_quote" in result.message
    # 计划一行都没动
    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    assert plan.version == 1


def test_propose_plan_change_accepts_the_users_own_words(sandbox) -> None:
    """★ 政策变更的那一半：不用先去界面上点，**他说一句就行**。

    旧政策要求"用户本人点了那个计划项"，于是模型只能回一句
    「请先点开计划项」—— 那是一道没有数据理由的门。
    """
    _user_says(sandbox.ctx(), "这一步太慢了，改成每天 30 个词吧")
    result = _run(
        "propose_plan_change",
        sandbox.ctx(),
        target="ST-001",
        user_view="他说这一步太慢，要改成每天 30 个",
        user_quote="这一步太慢了，改成每天 30 个词",
        suggested_change=_steps_arg(),
    )
    assert result.status is T.ToolStatus.SUCCESS, result.message
    assert result.data["version"] == 2


def test_propose_plan_change_refuses_a_quote_the_user_never_said(sandbox) -> None:
    """★ 反例：引文不在用户这一轮那条消息里 → 拒绝，计划不动。

    用户只说了「我有点烦」。模型带上"要不改成 30 个"当引文 ——
    那句话他说不出来的，所以它不是证据。
    """
    _user_says(sandbox.ctx(), "我有点烦")
    result = _run(
        "propose_plan_change",
        sandbox.ctx(),
        target="ST-001",
        user_view="他说太慢了",
        user_quote="这一步太慢了，改成每天 30 个词",
        suggested_change=_steps_arg(),
    )
    assert result.status is T.ToolStatus.REJECTED
    assert session_reads.require_plan(sandbox.db, sandbox.session_id).version == 1


def test_the_plan_change_gesture_unlocks_propose_plan_change(sandbox) -> None:
    """反面的一半：界面给出那个手势，就能改。

    只测"不能"会让一个"其实永远不能"的实现也通过。
    """
    result = _run(
        "propose_plan_change",
        sandbox.ctx(T.GESTURE_PLAN_CHANGE),
        target="ST-001",
        user_view="用户点了第一步，说太多了",
        suggested_change=_steps_arg(),
    )
    assert result.status is T.ToolStatus.SUCCESS, result.message
    assert "用户本人" in result.message


def test_a_step_only_change_does_not_delete_the_checkpoints(sandbox) -> None:
    """另一格：模型改步骤时通常**根本不提检查点**。

    旧行为是"没提 = 清空"，于是"通过标准"会随着一句措辞修改一起消失 ——
    而那恰恰是最不该无声消失的东西（M2-A4 的闭环全靠它）。

    这里顺带钉住"保留"是**连结果一起保留**：那条检查点已经判为 fail，
    它是这次改动的证据，也必须还在场。
    """
    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    session_writes.record_checkpoint(
        sandbox.db, sandbox.session_id, cp_id="CP-001", result="fail",
        expected_version=plan.version, result_note="正确率只有 60%",
    )
    result = _run(
        "update_task_plan",
        sandbox.ctx(),
        reason="第一步太重",
        diff={
            "steps": [{"step_id": "ST-001", "action": "每天过 20 个新词", "output": "记录"}]
        },
    )
    assert result.status is T.ToolStatus.SUCCESS, result.message
    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    checkpoints = session_reads.plan_checkpoints(sandbox.db, plan.plan_id)
    assert [cp.check_method for cp in checkpoints] == ["随机抽 20 个词自测"]
    # **这一条钉的是一个已知行为，而它有后果。**
    #
    # 检查点这一行留下了（方法、标准、失败预案都在），但 `result` 归零成 `pending`：
    # `revise_plan` 只 carry over **步骤**的状态，不 carry over 检查点的结果。
    #
    # 后果是真实的：`_recorded_evidence` 靠 `result is FAIL` 找证据，
    # 所以**一次计划改动就把那条证据抹掉了** —— 模型没法基于同一个失败再改第二次，
    # 除非用户重新记一次结果。用户界面上的观感则是"我记的'没过'不见了"。
    #
    # 这里不擅自改 P4 的计划版本语义（那是要单独裁定的改动）：
    # 该保留还是该重置，与"省略=保留"那条裁定是同一族问题，已记进缺口表等裁定。
    assert [cp.result.value for cp in checkpoints] == ["pending"]


def test_an_unknown_step_id_is_refused_rather_than_ignored(sandbox) -> None:
    """提到一个不存在的 id 不能静默略过。

    略过的后果是：**改动什么都没发生，而模型会告诉用户"已经改好了"**。
    那比报错坏得多 —— 用户不会再去核对。

    这里断言的是异常本身（`_run` 直接调 handler，不经过 dispatcher）。
    它对用户的表现形式是 `rejected`，那条断言在
    `tests/api/test_discuss_api.py` 里走完整链路验一遍。
    """
    with pytest.raises(T.InvalidField) as caught:
        _run(
            "propose_plan_change",
            sandbox.ctx(T.GESTURE_PLAN_CHANGE),
            target="ST-999",
            user_view="改那一步",
            suggested_change={
                "steps": [{"step_id": "ST-999", "action": "改成别的", "output": "x"}]
            },
        )
    assert "ST-999" in str(caught.value)
    # 计划一行都没动
    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    assert plan.version == 1


# --------------------------------------------------------------------------- #
# 方案 B（用户裁定）：`steps` 是那一版的整份有序表
#
# 这里原来的四条测试钉的是方案 A（「省略 = 保留」+ 位置不动 + `drop` 显式删除）。
# 裁定改成 B 之后它们被**反转**而不是删除 —— 反转才留下"我们改过主意"的痕迹，
# 而删掉的话，下一个人只会看到四条从来如此的测试。
# --------------------------------------------------------------------------- #


def test_a_partial_change_now_drops_the_steps_it_did_not_mention(sandbox) -> None:
    """**这条测试是反转过来的。**

    原行为（A）：模型只提交 ST-001，ST-002 原样保留。
    现行为（B）：没列出来的就是要删掉。
    """
    result = _run(
        "propose_plan_change",
        sandbox.ctx(T.GESTURE_PLAN_CHANGE),
        target="ST-001",
        user_view="50 个太多了",
        suggested_change={
            "steps": [
                {"step_id": "ST-001", "action": "每天过 20 个新词", "output": "20 词的记录"}
            ]
        },
    )
    assert result.status is T.ToolStatus.SUCCESS, result.message

    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    steps = session_reads.plan_steps(sandbox.db, plan.plan_id)
    assert [step.action for step in steps] == ["每天过 20 个新词"]
    assert "删掉了" in result.message and "ST-002" in result.message, (
        "删了一步却没报出来：" + result.message
    )


def test_the_model_can_put_a_step_where_it_belongs(sandbox) -> None:
    """**B 换来的核心能力**：新增的步骤可以插在中间，而不是被扔到末尾。

    这是 A 做不到的那一件事，也是裁定 B 的直接理由：
    "这一步太大了，拆成两半" —— 第二半必须紧跟在第一半后面，
    因为后面那些步骤（精读例句、自测）都建立在词表之上。
    A 之下它只能追加到计划末尾，**而且不报错**。
    """
    result = _run(
        "propose_plan_change",
        sandbox.ctx(T.GESTURE_PLAN_CHANGE),
        target="ST-001",
        user_view="第一单元太长了，拆成两天，每天一半",
        suggested_change={
            "steps": [
                {"step_id": "ST-001", "action": "（第 1 天）过前一半词", "output": "前一半的记录"},
                {"action": "（第 2 天）过后一半词", "output": "后一半的记录"},
                {"step_id": "ST-002", "action": "读一篇技术文档并标注生词", "output": "标注稿"},
            ],
            "change": "第 1 步拆成两天",
        },
    )
    assert result.status is T.ToolStatus.SUCCESS, result.message

    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    steps = session_reads.plan_steps(sandbox.db, plan.plan_id)
    assert [step.action for step in steps] == [
        "（第 1 天）过前一半词",
        "（第 2 天）过后一半词",  # ← 紧跟在第一半后面，没有被扔到末尾
        "读一篇技术文档并标注生词",
    ]
    assert [step.seq for step in steps] == [1, 2, 3]


def test_the_model_can_reorder_existing_steps(sandbox) -> None:
    """B 的另一半：顺序就是列表顺序，所以**重排生效**。

    A 之下这件事根本做不到（注解曾写着"要换顺序先 drop 再新增"，
    而那也插不回原位 —— 新增一律追加到末尾）。
    """
    result = _run(
        "propose_plan_change",
        sandbox.ctx(T.GESTURE_PLAN_CHANGE),
        target="整个计划",
        user_view="先读文档再背词，先看看真实语料",
        suggested_change={
            "steps": [
                {"step_id": "ST-002", "action": "读一篇技术文档并标注生词", "output": "标注稿"},
                {"step_id": "ST-001", "action": "每天用 Anki 过 50 个新词", "output": "2000 词的复习记录"},
            ],
            "change": "两步调换顺序",
        },
    )
    assert result.status is T.ToolStatus.SUCCESS, result.message

    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    assert [step.step_id for step in session_reads.plan_steps(sandbox.db, plan.plan_id)] == [
        "ST-002",
        "ST-001",
    ]


def test_a_step_is_removed_by_leaving_it_out(sandbox) -> None:
    """删除就是"不列出来" —— 没有 `drop` 这个参数。

    它曾经存在过：A 的注解写着"删除必须是说出来的动作（`drop`）"，
    而**那个参数从来没进过任何工具 schema** —— 模型根本没有办法传它。
    也就是说 A 之下删除这条路是**堵死的**（省略 = 保留，`drop` 传不进来），
    而"删除要说出来"那句注解是在描述一个不存在的能力。
    """
    result = _run(
        "propose_plan_change",
        sandbox.ctx(T.GESTURE_PLAN_CHANGE),
        target="ST-002",
        user_view="读文档那步先不要了",
        suggested_change={
            "steps": [
                {"step_id": "ST-001", "action": "每天用 Anki 过 50 个新词", "output": "2000 词的复习记录"}
            ]
        },
    )
    assert result.status is T.ToolStatus.SUCCESS, result.message
    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    assert [step.action for step in session_reads.plan_steps(sandbox.db, plan.plan_id)] == [
        "每天用 Anki 过 50 个新词"
    ]


def test_the_same_step_id_twice_is_refused(sandbox) -> None:
    """同一版里重复的 `step_id` 要拦下来。

    B 之前不会发生（提到两次会被并成一条，后者覆盖前者）。
    B 之后 `steps` 是列表，重复的 id 会撞同一行主键 ——
    那时抛出的是谁也看不懂的 IntegrityError，而这里给的是一句能读的拒绝。
    """
    import pytest as _pytest

    with _pytest.raises(T.InvalidField) as caught:
        _run(
            "propose_plan_change",
            sandbox.ctx(T.GESTURE_PLAN_CHANGE),
            target="ST-001",
            user_view="改两次第一步",
            suggested_change={
                "steps": [
                    {"step_id": "ST-001", "action": "甲", "output": "x"},
                    {"step_id": "ST-001", "action": "乙", "output": "y"},
                ]
            },
        )
    assert "ST-001" in str(caught.value) and "两次" in str(caught.value)


def test_a_changed_step_still_inherits_its_progress(sandbox) -> None:
    """`carry_over` 与顺序无关：带对 `step_id`，完成状态就跟着走。

    这条是原「位置不动」那条的**重写**：位置现在由列表决定（B），
    但"改了措辞不该把已完成状态抹掉"这件事没有变，也不该变。
    """
    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    session_writes.mark_step(
        sandbox.db, sandbox.session_id, step_id="ST-001", status="done",
        expected_version=plan.version,
    )
    result = _run(
        "propose_plan_change",
        sandbox.ctx(T.GESTURE_PLAN_CHANGE),
        target="ST-001",
        user_view="第一步措辞改一下",
        suggested_change={
            "steps": [
                {"step_id": "ST-001", "action": "每天用 Anki 过 50 个新词（含复习）", "output": "2000 词的复习记录"},
                {"step_id": "ST-002", "action": "读一篇技术文档并标注生词", "output": "标注稿"},
            ]
        },
    )
    assert result.status is T.ToolStatus.SUCCESS, result.message

    steps = session_reads.plan_steps(
        sandbox.db, session_reads.require_plan(sandbox.db, sandbox.session_id).plan_id
    )
    assert [step.step_id for step in steps] == ["ST-001", "ST-002"]
    assert steps[0].action == "每天用 Anki 过 50 个新词（含复习）"
    assert steps[0].status.value == "done", "改了措辞把'已完成'抹掉了"


def test_the_deletion_report_carries_the_full_content_of_what_was_removed(sandbox) -> None:
    """删掉的东西必须**连产出一起**报出来，否则模型没法把它放回去。

    来源是一次真运行（子代理充当模型）：它只交了拆开后的两步，另外两步按方案 B
    被删掉了。它发现了这件事、想加回来，但**取不回原文**
    （`retrieve_session` 只读当前版本），于是只能请用户重新说一遍。
    "删除"是裁定允许的，"取不回"不是 —— 所以报告里带上原文。
    """
    result = _run(
        "propose_plan_change",
        sandbox.ctx(T.GESTURE_PLAN_CHANGE),
        target="ST-001",
        user_view="第一步拆成两天",
        suggested_change={
            "steps": [
                {"step_id": "ST-001", "action": "（第 1 天）过前一半词", "output": "前一半的记录"},
                {"action": "（第 2 天）过后一半词", "output": "后一半的记录"},
            ]
        },
    )
    assert result.status is T.ToolStatus.SUCCESS, result.message
    assert "删掉了" in result.message
    # 被删的那一步：id、action、**output** 三者都要在
    assert "ST-002" in result.message
    assert "读一篇技术文档并标注生词" in result.message
    assert "标注过的文档一篇" in result.message, (
        "删除报告没给产出 —— 模型没法把这一步原样放回去：" + result.message
    )
    assert "重新放进 steps" in result.message, "报告没告诉模型怎么恢复"


def test_create_plan_is_unaffected_by_the_carry_over_rule(sandbox) -> None:
    """新建计划没有"现有版本"，所以行为不变：按给的顺序依次新增。"""
    steps = _steps_from({"steps": [{"action": "A", "output": "a"}, {"action": "B", "output": "b"}]})
    assert [step.action for step in steps] == ["A", "B"]
    assert all(step.step_id is None for step in steps)


def _steps_arg(*, marker: str = "原样") -> dict:
    return {
        "steps": [
            {"step_id": "ST-001", "action": "每天过 30 个新词", "output": "复习记录"},
            {"step_id": "ST-002", "action": f"读技术文档({marker})", "output": "标注稿"},
        ]
    }


def test_propose_plan_change_requires_a_verdict_for_every_hard_constraint(
    sandbox, router, next_id
) -> None:
    """**P6 的核心保障**：漏掉一条硬约束就整次拒绝。

    让模型自己说「这条改动没违反任何约束」恰好是「理由 ≠ 事实」的形态；
    而「你有没有逐条回答」是机械可验的。它拦住的正是最危险的失败 ——
    **沉默地略过用户亲口说过的一句话**。
    """
    from app.router import SubmitRequest, UpdateIntent

    router.submit(
        SubmitRequest(
            trigger_type="R1",
            intents=[
                UpdateIntent(
                    intent_id=next_id(),
                    action="add_preference",
                    target="M2.preferences",
                    value={"statement": "我讨厌看网课，因为浪费时间", "priority": 7},
                    basis="stated",
                    reasoning="用户声明",
                    confidence="high",
                )
            ],
        )
    )
    constraints = global_reads.list_active_constraints(sandbox.db)
    assert [row.pref_id for row in constraints] == ["P-0001"]

    # ① 整批略过 → 拒绝
    skipped = _run(
        "propose_plan_change",
        sandbox.ctx(sandbox.gesture_plan_change),
        target="ST-001",
        user_view="这一步太慢了",
        suggested_change=_steps_arg(),
    )
    assert skipped.status is T.ToolStatus.REJECTED
    assert "逐条" in skipped.message

    # ② 少答一条 → 拒绝，并且**指名道姓**说出漏了哪一条
    partial = _run(
        "propose_plan_change",
        sandbox.ctx(sandbox.gesture_plan_change),
        target="ST-001",
        user_view="这一步太慢了",
        constraint_checks=[{"pref_id": "P-9999", "verdict": "ok"}],
        suggested_change=_steps_arg(),
    )
    assert partial.status is T.ToolStatus.REJECTED
    assert "P-0001" in partial.message
    assert "我讨厌看网课" in partial.message

    # ③ 覆盖全了 → 应用
    applied = _run(
        "propose_plan_change",
        sandbox.ctx(sandbox.gesture_plan_change),
        target="ST-001",
        user_view="这一步太慢了",
        constraint_checks=[{"pref_id": "P-0001", "verdict": "ok", "why": "没有引入网课"}],
        suggested_change=_steps_arg(marker="改后"),
    )
    assert applied.status is T.ToolStatus.SUCCESS
    assert applied.data["version"] == 2
    assert applied.data["constraint_checks"][0]["pref_id"] == "P-0001"


def test_update_task_plan_also_requires_a_verdict_for_every_hard_constraint(
    sandbox, router, next_id
) -> None:
    """★ **两条改计划的路共用同一份硬约束门禁**（这是补上来的断言）。

    原先只有 `propose_plan_change` 查这一关，`update_task_plan` 不查 ——
    而模型会挑松的那条走（实测抓到过）。于是"计划与用户亲口说的硬约束冲突"
    在数据里成立，而系统假装不可能。同一条规矩在两个地方不一样严，
    等于没有这条规矩。

    这条用例用**落库证据**开 `update_task_plan`（失败检查点 → 入口条件成立），
    这样它测试的就不是入口条件，而**只有**硬约束覆盖：
    ① 整批略过要拒、② 少答一条要指名、③ 覆盖全了才生效、
    ④ 判 `conflict` 也整次拒绝（哪怕入口条件成立）。
    """
    from app.router import SubmitRequest, UpdateIntent

    router.submit(
        SubmitRequest(
            trigger_type="R1",
            intents=[
                UpdateIntent(
                    intent_id=next_id(),
                    action="add_preference",
                    target="M2.preferences",
                    value={"statement": "我讨厌看网课，因为浪费时间", "priority": 7},
                    basis="stated",
                    reasoning="用户声明",
                    confidence="high",
                )
            ],
        )
    )
    # 落库证据：stage 里一条未结案的想法 —— `update_task_plan` 的入口条件。
    # （刻意不用"失败检查点"当样本：`record_checkpoint` 会推进计划版本，
    #  而新版本里检查点结果归零成 pending —— 那会让这条用例测的东西变成版本语义。）
    from app.session_domain.enums import StageItemType

    session_writes.add_stage_item(
        sandbox.db,
        sandbox.session_id,
        item_type=StageItemType.IDEA,
        content="先试试每天只读 20 行",
        expected_version=session_reads.get_session(
            sandbox.db, sandbox.session_id
        ).version,
    )

    # ① 整批略过 → 拒绝（不是"证据够了就随便改"）
    skipped = _run(
        "update_task_plan",
        sandbox.ctx(),
        reason="先把这步拆细",
        diff={**_steps_arg(), "change": "拆细第一步"},
    )
    assert skipped.status is T.ToolStatus.REJECTED, skipped.message
    assert "constraint_checks" in skipped.message
    assert session_reads.require_plan(sandbox.db, sandbox.session_id).version == 1

    # ② 少答一条 → 拒绝，并且**指名道姓**说出漏了哪一条
    partial = _run(
        "update_task_plan",
        sandbox.ctx(),
        reason="先把这步拆细",
        constraint_checks=[{"pref_id": "P-9999", "verdict": "ok"}],
        diff={**_steps_arg(), "change": "拆细第一步"},
    )
    assert partial.status is T.ToolStatus.REJECTED
    assert "P-0001" in partial.message
    assert "我讨厌看网课" in partial.message

    # ③ 判成冲突 → 整次拒绝（入口条件成立也没用）
    conflicted = _run(
        "update_task_plan",
        sandbox.ctx(),
        reason="改成看网课",
        constraint_checks=[
            {"pref_id": "P-0001", "verdict": "conflict", "why": "网课正是他排斥的"}
        ],
        diff={**_steps_arg(), "change": "改成看网课"},
    )
    assert conflicted.status is T.ToolStatus.REJECTED
    assert "冲突" in conflicted.message

    # ④ 覆盖全了、且不冲突 → 生效
    applied = _run(
        "update_task_plan",
        sandbox.ctx(),
        reason="先把这步拆细",
        constraint_checks=[{"pref_id": "P-0001", "verdict": "ok", "why": "没有引入网课"}],
        diff={**_steps_arg(marker="改后"), "change": "拆细第一步"},
    )
    assert applied.status is T.ToolStatus.SUCCESS, applied.message
    assert applied.data["version"] == 2


def test_propose_plan_change_refuses_when_a_constraint_is_violated(
    sandbox, router, next_id
) -> None:
    """违反硬约束 → rejected，但**不影响执行**：拒绝只是"这次调整不生效"。"""
    from app.router import SubmitRequest, UpdateIntent

    router.submit(
        SubmitRequest(
            trigger_type="R1",
            intents=[
                UpdateIntent(
                    intent_id=next_id(),
                    action="add_preference",
                    target="M2.preferences",
                    value={"statement": "我讨厌看网课，因为浪费时间", "priority": 7},
                    basis="stated",
                    reasoning="用户声明",
                    confidence="high",
                )
            ],
        )
    )
    result = _run(
        "propose_plan_change",
        sandbox.ctx(sandbox.gesture_plan_change),
        target="ST-001",
        user_view="要不我们看网课吧",
        constraint_checks=[
            {"pref_id": "P-0001", "verdict": "conflict", "why": "网课正是他排斥的"}
        ],
        suggested_change=_steps_arg(),
    )
    assert result.status is T.ToolStatus.REJECTED
    assert "不影响" in result.message
    # 计划没有被改动
    assert session_reads.require_plan(sandbox.db, sandbox.session_id).version == 1


def test_propose_plan_change_needs_no_checks_when_there_are_no_constraints(
    sandbox,
) -> None:
    """没有硬约束时不该逼模型凭空造一份 checks 出来。"""
    result = _run(
        "propose_plan_change",
        sandbox.ctx(sandbox.gesture_plan_change),
        target="ST-002",
        user_view="想换个读法",
        suggested_change=_steps_arg(),
    )
    assert result.status is T.ToolStatus.SUCCESS


def test_plan_revision_carries_over_step_progress(sandbox) -> None:
    """改计划不该让"做过的第 1 步"变回未开始（M7 §5）。"""
    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    session_writes.mark_step(
        sandbox.db, sandbox.session_id, step_id="ST-001", status="done",
        expected_version=plan.version,
    )
    _run(
        "propose_plan_change",
        sandbox.ctx(sandbox.gesture_plan_change),
        target="ST-002",
        user_view="第二步行不行",
        suggested_change=_steps_arg(marker="v2"),
    )
    plan = session_reads.require_plan(sandbox.db, sandbox.session_id)
    statuses = {
        step.step_id: step.status.value
        for step in session_reads.plan_steps(sandbox.db, plan.plan_id)
    }
    assert statuses["ST-001"] == "done"


def test_stage_items_stay_in_the_sandbox(sandbox) -> None:
    """写 stage 不产生任何全局写入 —— 这是沙箱边界在工具层的体现。"""
    before = global_reads.count_router_logs(sandbox.db)
    result = _run(
        "write_stage",
        sandbox.ctx(),
        type="temp_pref",
        content="想试试先听音频再背词",
    )
    assert result.status is T.ToolStatus.SUCCESS
    assert global_reads.count_router_logs(sandbox.db) == before


def test_a_stage_item_is_enough_evidence_to_change_the_plan(sandbox) -> None:
    """stage 里"想试试别的"是用户自己写下的记录，算证据。"""
    _run("write_stage", sandbox.ctx(), type="idea", content="想试试先听音频")
    result = _run(
        "update_task_plan",
        sandbox.ctx(),
        reason="用户把想法放进了 stage",
        diff={
            "steps": [{"step_id": "ST-001", "action": "先听音频再背词", "output": "音频笔记"}],
            "change": "换方法",
        },
    )
    assert result.status is T.ToolStatus.SUCCESS
    assert "stage" in result.data["evidence"]


def test_create_plan_rejects_steps_without_output(sandbox) -> None:
    """M2-A3：没有产出的步骤无法验收。"""
    result = _run(
        "create_task_plan",
        sandbox.ctx(),
        steps=[{"action": "看一会儿书"}],
    )
    assert result.status is T.ToolStatus.REJECTED
    assert "[1]" in result.message


def test_retrieve_cognitive_returns_field_paths(sandbox) -> None:
    """模型必须拿到**字段路径**才能写出规范的 target，而不是自己编个名字。"""
    result = _run("retrieve_cognitive", sandbox.ctx(), scope="m1", max_items=3)
    assert result.status is T.ToolStatus.SUCCESS
    paths = [item["path"] for item in result.data["items"]]
    assert paths and all(path.startswith("M1.V-") for path in paths)

    prefs = _run("retrieve_cognitive", sandbox.ctx(), scope="m2_preferences")
    assert prefs.data["items"] == []  # 没有任何偏好时是"空"，不是"错误"


def test_retrieve_cognitive_marks_which_preferences_are_hard_constraints(
    sandbox, router, next_id
) -> None:
    """硬度只有一处判定（A18）。工具直接把结论给模型，避免它自己重算一遍。"""
    from app.router import SubmitRequest, UpdateIntent

    router.submit(
        SubmitRequest(
            trigger_type="R1",
            intents=[
                UpdateIntent(
                    intent_id=next_id(),
                    action="add_preference",
                    target="M2.preferences",
                    value={"statement": "我讨厌看网课，因为浪费时间", "priority": 7},
                    basis="stated",
                    reasoning="用户声明",
                    confidence="high",
                )
            ],
        )
    )
    result = _run(
        "retrieve_cognitive", sandbox.ctx(), scope="m2_preferences", query="网课"
    )
    (item,) = result.data["items"]
    assert item["detail"]["is_hard_constraint"] is True
    assert item["path"] == "M2.preferences.P-0001"


def test_retrieve_cognitive_says_what_it_cannot_do_with_task_type(sandbox) -> None:
    """说不出来的能力不能装作有。

    M8 §4.1 说 scope=m1 按 task_type 过滤，但 `m1_variable` 表里没有这个字段。
    按一个猜测的键去过滤，最可能的结果是**一条都不返回**，
    而模型会把"没查到"读成"这类任务没有相关变量"。所以只排序，并把事实说出来。
    """
    result = _run(
        "retrieve_cognitive", sandbox.ctx(), scope="m1", task_type="掌握技能"
    )
    assert result.status is T.ToolStatus.SUCCESS
    assert "只用于排序" in result.message


def test_retrieve_strategy_never_returns_more_than_four(sandbox) -> None:
    """≤4 是 M8 的边界；模型要 10 条也只给 4 条。"""
    result = _run("retrieve_strategy", sandbox.ctx(), task_type="掌握技能", max_items=10)
    assert len(result.data["strategies"]) <= 4
    assert "参考不是约束" in result.message


def test_retrieve_folder_gives_summaries_not_transcripts(sandbox) -> None:
    """不返回 chat_log 全文（M8 §4.1）。"""
    session_writes.add_chat_message(
        sandbox.db, sandbox.session_id, content="这是一句很长很长的对话原文", role="user"
    )
    result = _run("retrieve_folder", sandbox.ctx())
    blob = json.dumps(result.data, ensure_ascii=False)
    assert "很长很长" not in blob
    assert result.data["folder_goal"] == "能读原版技术文档"
    assert result.data["recent_sessions"][0]["is_current"] is True


def test_tool_results_always_report_a_status_and_a_message(sandbox) -> None:
    """M8 §4：所有工具输出统一带 status + message，主 Agent 依 status 决定下一步。"""
    for name, spec in T.TOOLS.items():
        if spec.handler is None:
            continue
        # 故意用错参数：要的是「任何一条路径都返回 ToolResult」，
        # 而不是「抛异常出去」。抛出去的异常在循环里会变成一次崩溃，
        # 而模型需要看到的是 `status=rejected/error` + 一句人话。
        try:
            result = spec.handler(sandbox.ctx(), {})
        except (KeyError, TypeError):
            continue  # 缺必填参数的 TypeError 由 dispatcher 先拦，见 test_dispatch
        assert isinstance(result, T.ToolResult), name
        assert result.status in set(T.ToolStatus)
        assert result.message.strip()
