"""侧边栏常驻模型（P6+.6）：端点到循环，走真实 HTTP。

## 这组测试要证明的三件事

1. **它真的能聊** —— 一次讨论落两条消息、带实测用量、带思考过程。
2. **它真的不能改会话** —— 会话工具**连清单里都没有**；
   而模型硬编一个工具名去调时，分发器也会明确拒绝。
   这两层必须分别验证：只验第一层的话，"看不见"就成了唯一防线，
   而**只靠"它不会那么做"的防线，迟早会被走到。**
3. **它与会话共用记忆机制** —— 两边的 `context` 形状逐字段相同，
   因为它们本来就是同一份代码。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent.llm import FakeLLM, call, reply
from tests.helpers import items

SIDEBAR = "/api/sidebar"


def _use(app, *replies) -> FakeLLM:
    """装一个按脚本回放的模型。与会话侧测试同一个姿态。"""
    fake = FakeLLM(list(replies))
    app.state.llm_client = fake
    return fake


# --------------------------------------------------------------------------- #
# 它能聊
# --------------------------------------------------------------------------- #


def test_it_answers_on_an_empty_database(app, client: TestClient) -> None:
    """★ 空库也要能用。

    侧边栏是**常驻**的：用户装好系统、还没建任何会话时就会点开它。
    此时返回 404/500，用户看到的是一个坏掉的界面，
    而不是一个"还没什么可聊"的空面板。
    """
    response = client.get(f"{SIDEBAR}/state")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["messages"] == []
    assert body["context"]["live_messages"] == 0
    assert body["context"]["measured"] is False
    assert body["sections"], "空库也要报节（否则界面无从解释它看到了什么）"


def test_a_discussion_lands_two_messages_with_usage(app, client: TestClient) -> None:
    """一次讨论 = 用户一句 + 助手一句，且助手那句带**实测**用量。"""
    _use(app, reply("听起来这个过程让你很别扭。是卡在哪一步？"))
    response = client.post(f"{SIDEBAR}/discuss", json={"message": "背单词好痛苦"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "听起来这个过程让你很别扭。是卡在哪一步？"
    assert body["stop_reason"] == "completed"
    assert body["context"]["measured"] is True, "用量没回填"
    assert body["context"]["live_messages"] == 2

    state = client.get(f"{SIDEBAR}/state").json()
    assert [m["role"] for m in state["messages"]] == ["user", "assistant"]
    assert state["messages"][0]["content"] == "背单词好痛苦"
    assert state["messages"][1]["prompt_tokens"] > 0
    # 思考过程字段必须在（哪怕是 null）—— 见会话侧那条测试的说明
    assert "thinking" in state["messages"][1]


def test_it_keeps_the_whole_history(app, client: TestClient) -> None:
    """侧边栏**有记忆**，且不是"最近 6 条"那个硬截断。"""
    _use(app, *[reply(f"第 {i} 句回话") for i in range(10)])
    for i in range(10):
        response = client.post(f"{SIDEBAR}/discuss", json={"message": f"第 {i} 句话"})
        assert response.status_code == 200, response.text

    state = client.get(f"{SIDEBAR}/state").json()
    assert len(state["messages"]) == 20
    # 第 1 句还在 —— 旧的 `HISTORY_LIMIT = 6` 会让它消失
    assert state["messages"][0]["content"] == "第 0 句话"


def test_the_meter_shape_matches_the_session_one(app, client: TestClient) -> None:
    """★ 两个 scope 的计量形状**逐字段相同** —— 因为它们本来就是同一份代码。

    这条测试的意义在于：它会在有人"顺手给侧边栏加一个特制字段"时变红。
    那种改动看起来无害，而它的后果是两个界面显示的用量算法开始分叉，
    且分叉是**安静的**。
    """
    _use(app, reply("好。"))
    session_side = client.post(f"{SIDEBAR}/discuss", json={"message": "嗨"})
    assert session_side.status_code == 200
    sidebar_keys = set(session_side.json()["context"])

    # 会话侧的同一个东西：形状来自 `ContextMeter.as_dict()`，两边同一个来源
    from app.agent.context import ContextMeter

    assert sidebar_keys == set(
        ContextMeter(
            used=0,
            limit=1,
            live_messages=0,
            compacted_messages=0,
            summaries=0,
            compact_count=0,
            compactable=False,
        ).as_dict()
    )


# --------------------------------------------------------------------------- #
# 它不能改会话
# --------------------------------------------------------------------------- #


def test_session_tools_are_not_in_its_tool_list(app, client: TestClient) -> None:
    """★ 第一层防线：会话工具**连清单里都不出现**。

    这不是"禁用"，是"不存在" —— 见 `ToolScope` 的说明。
    """
    body = client.get(f"{SIDEBAR}/state").json()
    assert body["tools"] == [
        "retrieve_folder",
        "retrieve_cognitive",
        "retrieve_strategy",
        "submit_global_update",
    ]
    for absent in (
        "retrieve_session",
        "read_attachment",
        "propose_plan_change",
        "update_task_plan",
        "create_task_plan",
        "update_motivation",
        "mark_step_done",
    ):
        assert absent not in body["tools"], f"{absent} 不该出现在侧边栏的工具清单里"


def test_calling_a_session_tool_anyway_is_refused_with_a_reason(
    app, client: TestClient
) -> None:
    """★ 第二层防线：模型硬编一个会话工具去调，也要被拒**并说清原因**。

    只验第一层的话，"模型看不见"就成了唯一防线。而模型会编工具名
    （它见过的名字尤其容易），那时若放它进 handler，它会拿一个哨兵
    `session_id` 去查一个不存在的会话 —— 报出「会话不存在：sidebar」，
    而那条错误看起来像**数据问题**，不是"这里根本不该有会话"。

    "不允许"与"为什么不允许"的区别在这里是实质的：
    模型能照着后者换一种做法，对着前者只能反复试。
    """
    fake = _use(
        app,
        reply("我查一下。", call("retrieve_session", scope="chat_log", filter={"topic": "词"})),
        reply("我读不到，换个说法。"),
    )
    response = client.post(f"{SIDEBAR}/discuss", json={"message": "帮我看看上次那个会话"})
    assert response.status_code == 200, response.text
    body = response.json()

    refused = [item for item in body["tool_trace"] if item["tool"] == "retrieve_session"]
    assert refused, "模型调了会话工具，但轨迹里没有它"
    assert refused[0]["status"] == "rejected"
    assert "具体的会话" in refused[0]["message"], refused[0]["message"]
    # 而它**没有**去查一个叫 sidebar 的会话
    assert "会话不存在" not in refused[0]["message"]
    # 第一次调用被拒之后循环继续了（模型有机会换做法）
    assert len(fake.seen) == 2


def test_the_sidebar_has_no_state_machine(app, client: TestClient) -> None:
    """★ 侧边栏**没有终态** —— 这正是"常驻"在代码里的样子。

    会话写完总结就不能再聊（`assert_writable`）；侧边栏永远可以聊。
    给它加一个状态机只会多出一个"什么时候算结束"的假问题。
    """
    _use(app, reply("一"), reply("二"), reply("三"))
    for text in ("甲", "乙", "丙"):
        response = client.post(f"{SIDEBAR}/discuss", json={"message": text})
        assert response.status_code == 200, response.text


def test_clearing_it_does_not_touch_global_cognition(app, client: TestClient) -> None:
    """★ 清空侧边栏是**唯一**的真删除端点，所以它的边界要说清、也要验。

    删的只有 `sidebar_message` 与这个 scope 的压缩记录。
    已批准的提案与它的 `router_log` **留在原处** —— 那是审计凭据，
    不该因为"我清了聊天记录"而消失。
    """
    client.post("/api/preferences", json={"statement": "先看例子再看定义"})
    _use(app, reply("记住了。"))
    client.post(f"{SIDEBAR}/discuss", json={"message": "我喜欢先看例子"})

    prefs_before = items(client.get("/api/preferences").json())
    logs_before = items(client.get("/api/router-logs").json())

    result = client.delete(f"{SIDEBAR}/messages")
    assert result.status_code == 200, result.text
    assert result.json()["deleted_messages"] == 2

    assert client.get(f"{SIDEBAR}/state").json()["messages"] == []
    # ★ 全局认知一条都没动
    assert items(client.get("/api/preferences").json()) == prefs_before
    assert items(client.get("/api/router-logs").json()) == logs_before


# --------------------------------------------------------------------------- #
# 它看到的是什么
# --------------------------------------------------------------------------- #


def test_the_context_endpoint_shows_what_it_sees(app, client: TestClient) -> None:
    """用户能看到模型看的是什么 —— 于是"它为什么这么说"可以追问。"""
    client.post("/api/preferences", json={"statement": "我讨厌网课"})
    body = client.get(f"{SIDEBAR}/context").json()
    assert "我讨厌网课" in body["rendered"]
    # ★ 而它必须**明说**自己没有计划 —— 这是整份上下文里唯一说明
    # "缺了什么"的地方。一份只列"有什么"的上下文会让模型把它当成
    # 完整的世界图景，于是开始推断"他接下来该做第几步"。
    assert "你不在任何任务里" in body["rendered"]
    assert "没有当前计划" in body["rendered"]
    # 采用序也回给界面（与会话侧同一个姿态）
    assert body["precedence_order"]
    # 8 节：P7 在「证据」之后加了「假说」。
    assert len(body["sections"]) == 8


def test_the_rendered_context_never_uses_the_session_renderer(
    app, client: TestClient
) -> None:
    """★ 侧边栏的渲染器里**不许出现会话渲染器的抬头**。

    出现就意味着有人在"顺手复用" `loop._render_context`，
    而那个渲染器会输出「## 当前这一步（ST-001）」这一类抬头。
    在侧边栏里那个值只能是空的，于是模型会读到
    「当前这一步（）」并开始推断"你还没有计划" ——
    而真相是**侧边栏没有计划这个概念**。两件事对用户含义完全不同。

    ★ 判据是**会话渲染器的抬头清单**，不是"步骤"这个词：
    侧边栏的上下文里**应该**出现「你没有当前步骤」这句**否定**，
    那正是本模块最重要的一句（见 `render_sidebar_context` 的末段）。
    一条按关键词粗暴禁词的测试会把它一起禁掉 —— 那测的不是对的东西。
    """
    rendered = client.get(f"{SIDEBAR}/context").json()["rendered"]
    session_only_headers = [
        "# 当前会话（",
        "## 当前这一步（",
        "## 用户此刻的动作",
        "## 用户上传的资料",
        "## 检查点（这一步的验收标准",
        "## stage（想试试但还没决定的）",
        "## 这一步的历史版本",
        "## 动机",
        "计划版本",
    ]
    for forbidden in session_only_headers:
        assert forbidden not in rendered, f"侧边栏上下文里出现了会话专有的「{forbidden}」"
    # 而那句**否定**必须还在 —— 它是侧边栏上下文的最后一段，
    # 也是唯一说明"缺了什么"的地方。
    assert "你不在任何任务里" in rendered
    assert "没有当前计划、没有当前步骤" in rendered


@pytest.mark.parametrize("message", ["", "   "])
def test_blank_messages_are_refused(app, client: TestClient, message: str) -> None:
    """空消息不讨论 —— 与会话侧同一条规矩。"""
    _use(app, reply("不该走到这里"))
    response = client.post(f"{SIDEBAR}/discuss", json={"message": message})
    assert response.status_code == 422, response.text
