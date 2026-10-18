"""`POST /api/sessions/{id}/discuss` —— **P6 要打通的那条闭环的验收**。

一条端到端的路：用户点一个计划项说出看法 → Agent 循环 → 工具层 →
沙箱真的改了 → 回复与轨迹落进 chat_message。

三条硬要求写在测试里，而不是写在文档里：

1. **这个测试不依赖任何 LLM 服务在跑。** 模型那一侧是确定性的替身
   （`app.state.llm_client` 换成 `FakeLLM`）。M0 §12 的验收前提是
   「无 LLM 也能治理」，而这里要证明的是更强的一句：
   **有 LLM 时的每一条链路，都能在没有 LLM 的情况下被验证。**
2. **`stop_reason` 必须出现在响应里。** 「它说完了」与「它用完步数了」
   对用户含义完全不同，界面不能把两者都渲染成一段正常回复。
3. **轨迹必须落库。** 用户明确要求「我能看到它做了什么」——
   只记在响应里、刷新就没了，等于没做。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent.llm import FakeLLM, call, reply

DISCUSS = "/api/sessions/{session_id}/discuss"


def _prepare(client: TestClient) -> tuple[str, str]:
    """铺一个能讨论的会话：文件夹 + 会话 + 计划。返回 `(session_id, step_id)`。"""
    folder = client.post("/api/folders", json={"name": "英语学习"}).json()["folder_id"]
    session_id = client.post(
        "/api/sessions",
        json={"folder_id": folder, "title": "背单词", "input_goal": "背完考研核心 1500 词"},
    ).json()["session_id"]

    state = client.get(f"/api/sessions/{session_id}/state").json()
    client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "active", "expected_version": state["versions"]["session"]},
    )
    state = client.get(f"/api/sessions/{session_id}/state").json()
    plan = client.post(
        f"/api/sessions/{session_id}/plan",
        json={
            "expected_session_version": state["versions"]["session"],
            "goal_derived": "先把 unit 1 吃透",
            "steps": [
                {"action": "通读 unit 1", "output": "一份生词表"},
                {"action": "精读 unit 1", "output": "10 个例句"},
            ],
            "checkpoints": [],
        },
    )
    assert plan.status_code == 201, plan.text
    return session_id, plan.json()["steps"][0]["step_id"]


def _chat(client: TestClient, session_id: str) -> list[dict]:
    """对话记录从 `/state` 读 —— **侧页唯一的数据入口**（P4 定的）。

    刻意不为它加一个 `GET /chat`：多一个读路径就多一处"权限与形状"要维护。
    而轨迹要证明的恰恰是"它能到达用户看得见的地方"——
    走侧页那个入口，证明力比新开一个端点更强。
    """
    response = client.get(f"/api/sessions/{session_id}/state")
    assert response.status_code == 200, response.text
    return response.json()["chat"]


def _use(app, *replies) -> FakeLLM:
    """把应用那一侧换成确定性替身，并把它交回测试以便断言。"""
    fake = FakeLLM(list(replies))
    app.state.llm_client = fake
    return fake


# --------------------------------------------------------------------------- #
# 闭环
# --------------------------------------------------------------------------- #


def test_discussing_a_plan_item_actually_changes_the_plan(app, client) -> None:
    """**核心验收。** 用户点第一步、说"太多了" → 计划真的改了，且被记下来。"""
    session_id, step_id = _prepare(client)
    _use(
        app,
        reply(
            "我把第一步拆细。",
            call(
                "propose_plan_change",
                target=step_id,
                user_view="一次 50 个词太多，我记不住",
                suggested_change={
                    "steps": [
                        {"step_id": step_id, "action": "每天通读 20 行", "output": "20 行生词"},
                        {"action": "精读 unit 1", "output": "10 个例句"},
                    ],
                    "change": "第一步从通读整章改成 20 行",
                },
            ),
        ),
        reply("改好了：第一步现在是每天 20 行。"),
    )

    response = client.post(
        DISCUSS.format(session_id=session_id),
        json={"message": "第一步太多了", "step_id": step_id},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stop_reason"] == "completed"
    assert body["steps"] == 2
    assert "20 行" in body["reply"]

    # 计划真的改了 —— 版本 +1，步骤动作变了
    plan = client.get(f"/api/sessions/{session_id}/plan").json()
    assert plan["version"] == 2
    assert plan["steps"][0]["action"] == "每天通读 20 行"

    # 轨迹落库了，而且第一条是那次真实的计划改动
    chat = _chat(client, session_id)
    assistant = [row for row in chat if row["role"] == "assistant"]
    assert len(assistant) == 1
    trace = assistant[0]["tool_trace"]
    assert [item["tool"] for item in trace] == ["propose_plan_change"]
    assert trace[0]["status"] == "success"
    assert trace[0]["scope"] == "session_write"
    assert trace[0]["arguments"]["target"] == step_id
    assert assistant[0]["tags"] == ["stop:completed"]


def test_the_user_message_is_recorded_even_when_the_model_fails(app, client) -> None:
    """模型挂了，用户说过的话仍然在 —— 聊天记录不能因为服务不可用而缺一块。"""
    session_id, step_id = _prepare(client)
    _use(app)  # 空脚本 → 每次调用都 LLMError

    response = client.post(
        DISCUSS.format(session_id=session_id), json={"message": "在吗", "step_id": step_id}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stop_reason"] == "llm_error"
    assert "没能拿到模型" in body["reply"]
    assert body["steps"] == 0
    assert body["tool_trace"] == []

    chat = _chat(client, session_id)
    assert [row["role"] for row in chat] == ["user", "assistant"]
    assert chat[0]["content"] == "在吗"
    assert chat[1]["tags"] == ["stop:llm_error"]


def test_the_step_id_is_what_tells_the_agent_the_user_asked_for_a_change(
    app, client
) -> None:
    """带上 `step_id` = 用户在界面上点了那个计划项。

    这条证据来自**界面**，不是模型的声明。缺了它，`propose_plan_change`
    必须拒绝 —— 一个"模型自己觉得该改"的计划改动没有资格生效。

    **这是一条补上来的断言。** 原先的实现里 `propose_plan_change` 完全不查入口条件，
    它唯一的依据是模型自己写的那段 `user_view`：
    于是"用户觉得太慢"这句话由模型说了算，而没有任何东西验证过它。
    面向用户的工具比面向模型的 `update_task_plan` 更松，是反的。
    """
    session_id, _ = _prepare(client)
    _use(
        app,
        reply(
            "我觉得应该改。",
            call(
                "propose_plan_change",
                target="ST-001",
                user_view="我自己觉得太慢",
                suggested_change={
                    "steps": [
                        {"step_id": "ST-001", "action": "改掉", "output": "x"},
                        {"step_id": "ST-002", "action": "精读 unit 1", "output": "10 个例句"},
                    ]
                },
            ),
        ),
        reply("那你直接说想怎么改。"),
    )

    # 不带 step_id，也不带任何手势 → 模型想改计划但没有资格
    response = client.post(
        DISCUSS.format(session_id=session_id), json={"message": "你觉得呢"}
    )

    assert response.status_code == 200, response.text
    trace = response.json()["tool_trace"]
    assert trace[0]["status"] == "rejected"
    assert "改计划要有据" in trace[0]["message"]
    # 计划没有被改动
    assert client.get(f"/api/sessions/{session_id}/plan").json()["version"] == 1


def test_a_failed_checkpoint_routes_to_the_model_initiated_tool(app, client) -> None:
    """**反过来的那一半，而且路径变了。**

    这条测试原先断言"有失败检查点 → `propose_plan_change` 成功"。
    P6 的真实运行抓出一条绕道之后，它被反转过来了：

    `propose_plan_change` 现在只认**用户这次点了那一项**，
    因为它是唯一强制"硬约束逐条覆盖"的路径；而模型可以挑参数更少的
    `update_task_plan` 来绕开覆盖率检查 —— 真实运行里它就正是这么做的。
    于是两条路各自只认一种证据：落库的证据（失败检查点 / 未结案的 stage）
    归 `update_task_plan`。

    所以这里断言的是：**同样的场景下，模型该走哪条路，以及走错时的提示**。
    """
    session_id, step_id = _prepare(client)
    # 先加一个检查点，并把它标成 fail
    plan = client.get(f"/api/sessions/{session_id}/plan").json()
    updated = client.put(
        f"/api/sessions/{session_id}/plan",
        json={
            "expected_version": plan["version"],
            "steps": [
                {"step_id": step_id, "action": "通读 unit 1", "output": "一份生词表"},
                {"step_id": "ST-002", "action": "精读 unit 1", "output": "10 个例句"},
            ],
            "checkpoints": [
                {
                    "at_step": "ST-002",
                    "check_method": "闭卷默写",
                    "pass_criteria": "正确率 >= 80%",
                    "fail_action": "回到 ST-001 重做",
                }
            ],
            "change": "加一个检查点",
            "reason": "想有个通过标准",
        },
    )
    assert updated.status_code == 200, updated.text
    cp_id = updated.json()["checkpoints"][0]["cp_id"]
    recorded = client.patch(
        f"/api/sessions/{session_id}/plan/checkpoints/{cp_id}",
        json={"result": "fail", "expected_version": updated.json()["version"]},
    )
    assert recorded.status_code == 200, recorded.text

    # ① 走错路：用户没点任何计划项，`propose_plan_change` 被拒，并被指到另一条路
    _use(
        app,
        reply(
            "检查点没过，我调整一下。",
            call(
                "propose_plan_change",
                target=step_id,
                user_view="默写没过",
                suggested_change={"steps": [{"step_id": step_id, "action": "x", "output": "y"}]},
            ),
        ),
        reply("我换条路。"),
    )
    wrong = client.post(DISCUSS.format(session_id=session_id), json={"message": "默写没过"})
    trace = wrong.json()["tool_trace"]
    assert trace[0]["status"] == "rejected", trace[0]["message"]
    assert "update_task_plan" in trace[0]["message"]

    # ② 走对路：`update_task_plan` 凭那条落库的检查点结果成功
    version_before = client.get(f"/api/sessions/{session_id}/plan").json()["version"]
    _use(
        app,
        reply(
            "检查点没过，我调整一下。",
            call(
                "update_task_plan",
                reason="闭卷默写没过，先把抄写补上",
                diff={
                    "steps": [
                        {"step_id": step_id, "action": "先抄写一遍再通读", "output": "抄写笔记"},
                        {"step_id": "ST-002", "action": "精读 unit 1", "output": "10 个例句"},
                    ],
                    "checkpoints": [],
                    "change": "加上抄写这一步",
                },
            ),
        ),
        reply("改好了。"),
    )
    right = client.post(DISCUSS.format(session_id=session_id), json={"message": "默写没过"})
    trace = right.json()["tool_trace"]
    assert trace[0]["status"] == "success", trace[0]["message"]
    assert "检查点" in trace[0]["message"]
    after = client.get(f"/api/sessions/{session_id}/plan").json()
    assert after["version"] == version_before + 1


def test_a_click_alone_does_not_unlock_the_model_initiated_tool(app, client) -> None:
    """**绕道被堵住的证据。**

    用户在界面上点了第一步（手势存在），但库里没有任何"计划该被改"的记录。
    此时 `update_task_plan` 必须拒绝 —— 否则模型只要选它，
    就能免掉 `propose_plan_change` 要求的硬约束逐条覆盖，
    而那正是真实运行里发生的事。
    """
    session_id, step_id = _prepare(client)
    _use(
        app,
        reply(
            "我直接改。",
            call(
                "update_task_plan",
                reason="用户点了它，所以我可以改",
                diff={
                    "steps": [
                        {"step_id": step_id, "action": "改成别的", "output": "x"},
                        {"step_id": "ST-002", "action": "精读 unit 1", "output": "10 个例句"},
                    ]
                },
            ),
        ),
        reply("那我还是先问你。"),
    )
    response = client.post(
        DISCUSS.format(session_id=session_id),
        json={"message": "这一步我想改", "step_id": step_id},
    )
    trace = response.json()["tool_trace"]
    assert trace[0]["status"] == "rejected", trace[0]["message"]
    assert "没有「计划该被改」的记录在案" in trace[0]["message"]
    assert client.get(f"/api/sessions/{session_id}/plan").json()["version"] == 1


def test_a_partial_change_now_drops_the_rest_of_the_plan(app, client) -> None:
    """**方案 B**（用户裁定）：`steps` 是那一版的整份计划，没列出来的就是删掉。

    ## 这条测试是**反转**过来的，原来的断言正相反

    原行为（方案 A，来自一次真实的数据丢失）：用户点了 ST-001 说"50 个太多"，
    模型只提交 ST-001 一条，计划从 2 步变成 1 步 —— ST-002 **静默消失**，
    裁定因此是「省略 = 保留」。

    现在的裁定是 B：**模型完整掌控任务表**。理由是 A 把能力锁死了 ——
    模型无法表达"插在这里"，而"这一步太大，拆成两半"是最常见的计划改动；
    A 之下第二半只能被追加到末尾（对有序的计划，那个位置是错的），而且不报错。
    用户判断这是**模型能力问题**（本地模型太笨），而产品形态是接商用 API，
    用一条限制把能力锁死并不划算。

    ## 反转之后，原来防的那件事靠什么防

    **靠"删除可见"**：`tool_trace` 里那条返回会写清删掉了哪几步
    （见下面的 `removed` 断言）。原方案是"不让删"，现方案是"删了要说"。
    另外计划是版本化的，旧版本的行原地不动，删掉的步骤回得去。
    """
    session_id, step_id = _prepare(client)
    _use(
        app,
        reply(
            "那改成 20 个。",
            call(
                "propose_plan_change",
                target=step_id,
                user_view="50 个太多了，改成 20 个",
                suggested_change={
                    "steps": [{"step_id": step_id, "action": "每天过 20 个新词", "output": "记录"}],
                    "change": "把新词量降到 20",
                },
            ),
        ),
        reply("改好了。"),
    )
    response = client.post(
        DISCUSS.format(session_id=session_id),
        json={"message": "50 个太多了，改成 20 个", "step_id": step_id},
    )
    assert response.status_code == 200, response.text
    trace = response.json()["tool_trace"]
    assert trace[0]["status"] == "success"

    plan = client.get(f"/api/sessions/{session_id}/plan").json()
    assert [step["step_id"] for step in plan["steps"]] == [step_id]
    assert [step["action"] for step in plan["steps"]] == ["每天过 20 个新词"]

    # **删除必须是可见的** —— 这是 B 换来的那一半。
    assert "删掉了" in trace[0]["message"], (
        "删了一步却没在返回里说 —— 那就退回成'静默丢东西'了：" + trace[0]["message"]
    )
    assert "ST-002" in trace[0]["message"]


def test_an_unknown_step_id_becomes_a_readable_refusal(app, client) -> None:
    """模型报一个不存在的 `step_id` 时，用户看到的是**可读的拒绝**，而不是 500。

    这条钉的是两件事的接缝：`_steps_from` 抛 `InvalidField`（它不能静默略过 ——
    否则改动什么都没发生，而模型会对用户说"已经改好了"），
    以及 dispatcher 的 `OPERATIONAL_ERRORS` 名单确实把它翻成了 `rejected`。
    """
    session_id, step_id = _prepare(client)
    _use(
        app,
        reply(
            "我改一下第九步。",
            call(
                "propose_plan_change",
                target="ST-009",
                user_view="改第九步",
                suggested_change={"steps": [{"step_id": "ST-009", "action": "x", "output": "y"}]},
            ),
        ),
        reply("我看错了，没有第九步。"),
    )
    response = client.post(
        DISCUSS.format(session_id=session_id),
        json={"message": "改一下第九步", "step_id": step_id},
    )
    assert response.status_code == 200, response.text
    trace = response.json()["tool_trace"]
    assert trace[0]["status"] == "rejected", trace[0]
    assert "ST-009" in trace[0]["message"]
    assert client.get(f"/api/sessions/{session_id}/plan").json()["version"] == 1


def test_a_step_renamed_keeps_the_other_rows_intact(app, client) -> None:
    """只有带了 `step_id` 的那一行被改写，其余原样保留。

    这钉的是 `_steps_from` 的"按 id 对齐"语义：不带 id 的行是**新增**，
    而不是"把第 N 行替换掉"。搞混它会让用户看到自己的完成状态错位。
    """
    session_id, step_id = _prepare(client)
    _use(
        app,
        reply(
            "换个说法。",
            call(
                "propose_plan_change",
                target=step_id,
                user_view="这步的说法我看不懂",
                suggested_change={
                    "steps": [
                        {"step_id": step_id, "action": "先看目录挑 20 行", "output": "20 行生词"},
                        {"step_id": "ST-002", "action": "精读 unit 1", "output": "10 个例句"},
                    ]
                },
            ),
        ),
        reply("改好了。"),
    )
    client.post(
        DISCUSS.format(session_id=session_id),
        json={"message": "看不懂第一步", "step_id": step_id},
    )
    plan = client.get(f"/api/sessions/{session_id}/plan").json()
    assert [step["action"] for step in plan["steps"]] == [
        "先看目录挑 20 行",
        "精读 unit 1",
    ]


# --------------------------------------------------------------------------- #
# 边界与失败
# --------------------------------------------------------------------------- #


def test_the_agent_cannot_tick_a_step_on_its_own(app, client) -> None:
    """模型不能自己打勾 —— 打勾是**用户做过的事**。

    `step_done` 手势只能由界面给出（他在界面上点了完成），
    这条测试就是"模型拿不到那个手势"的证据。
    """
    session_id, step_id = _prepare(client)
    _use(
        app,
        reply("我帮你标一下。", call("mark_step_done", step_id=step_id)),
        reply("这个我不能替你点，你自己点一下就行。"),
    )
    response = client.post(
        DISCUSS.format(session_id=session_id), json={"message": "第一步做完了"}
    )
    trace = response.json()["tool_trace"]
    assert trace[0]["status"] == "rejected"
    assert "一键操作" in trace[0]["message"]
    plan = client.get(f"/api/sessions/{session_id}/plan").json()
    assert plan["steps"][0]["status"] == "pending"


def test_a_gesture_from_the_interface_unlocks_the_tick(app, client) -> None:
    """反过来：界面说"他点了完成"，就能打勾。

    两半都要测 —— 只测"不能"会让一个"其实谁都不能"的实现也通过。
    """
    session_id, step_id = _prepare(client)
    _use(
        app,
        reply("好，标好了。", call("mark_step_done", step_id=step_id)),
        reply("标好了。"),
    )
    response = client.post(
        DISCUSS.format(session_id=session_id),
        json={"message": "我点完成了", "gestures": [f"step_done:{step_id}"]},
    )
    trace = response.json()["tool_trace"]
    assert trace[0]["status"] == "success", trace[0]["message"]
    plan = client.get(f"/api/sessions/{session_id}/plan").json()
    assert plan["steps"][0]["status"] == "done"


def test_discussing_an_unknown_session_is_a_404(app, client) -> None:
    _use(app)
    response = client.post(DISCUSS.format(session_id="S-9999"), json={"message": "在吗"})
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_a_blank_message_is_refused_before_anything_happens(app, client) -> None:
    session_id, _ = _prepare(client)
    fake = _use(app, reply("不该走到这里"))
    response = client.post(
        DISCUSS.format(session_id=session_id), json={"message": "   "}
    )
    assert response.status_code == 422
    assert fake.seen == []  # 模型一次都没被调用


def test_an_unknown_request_field_is_refused(client) -> None:
    """与 `sedimentation.py` 同一姿态：拼错的字段要报错，不能静默忽略。"""
    session_id, _ = _prepare(client)
    _use(client.app)
    response = client.post(
        DISCUSS.format(session_id=session_id),
        json={"message": "在吗", "stepp_id": "ST-001"},
    )
    assert response.status_code == 422


def test_discussing_in_a_session_without_a_plan_is_a_readable_refusal(app, client) -> None:
    """还没建计划 → 说得清楚，且**不花掉一次模型调用**。

    状态码是 **404 `not_found`** 而不是 422：`require_plan` 在 P4 就把
    「会话还没有计划」定成这条子资源不存在。这里不改它 ——
    改一个已被 P4 端到端测试钉住的状态码，代价是整条前端错误分支要重看一遍；
    而 404 配 `code=not_found` 与一句「会话 S-0001 还没有计划」已经足够分辨。
    """
    folder = client.post("/api/folders", json={"name": "英语学习"}).json()["folder_id"]
    session_id = client.post(
        "/api/sessions",
        json={"folder_id": folder, "title": "背单词", "input_goal": "背完考研核心 1500 词"},
    ).json()["session_id"]
    state = client.get(f"/api/sessions/{session_id}/state").json()
    client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "active", "expected_version": state["versions"]["session"]},
    )
    fake = _use(app, reply("不该走到这里"))

    response = client.post(
        DISCUSS.format(session_id=session_id), json={"message": "我该做什么"}
    )

    assert response.status_code == 404, response.text
    assert response.json()["code"] == "not_found"
    assert "还没有计划" in response.json()["message"]
    assert fake.seen == []


@pytest.mark.parametrize("message", ["", " ", "\n"])
def test_empty_messages_never_reach_the_model(app, client, message) -> None:
    session_id, _ = _prepare(client)
    fake = _use(app, reply("不该走到这里"))
    response = client.post(
        DISCUSS.format(session_id=session_id), json={"message": message}
    )
    assert response.status_code == 422
    assert fake.seen == []


def test_the_user_message_survives_an_empty_reply(app, client) -> None:
    """**一次真实运行抓出来的缺陷，钉在这里。**

    本地模型返回过"既没有内容也没有工具调用"的响应。原先两条消息都在循环之后写，
    于是那次写入被数据库的「空消息不记」约束打回、整个请求 422 ——
    **用户说过的那句话一个字都没留下。**

    修法有两处，这条测试钉住结果：① 空回复归到 `llm_error` 且回复非空；
    ② 用户的消息在循环**之前**就落库。所以这里即使模型什么都没答，
    用户的话仍然在。
    """
    session_id, step_id = _prepare(client)
    _use(app, reply(""))

    response = client.post(
        DISCUSS.format(session_id=session_id), json={"message": "在吗", "step_id": step_id}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stop_reason"] == "llm_error"
    assert body["reply"].strip()

    chat = _chat(client, session_id)
    assert [row["role"] for row in chat] == ["user", "assistant"]
    assert chat[0]["content"] == "在吗"


def test_a_refused_session_costs_no_model_call(app, client) -> None:
    """会话不在 `active` 时，**在花掉一次模型调用之前**就拒绝。

    用户的消息先写、循环后跑，这条顺序顺带带来的好处：
    状态机不允许时 `add_chat_message` 自己会拒，于是本地模型不必白跑一百多秒。
    """
    session_id, _ = _prepare(client)
    state = client.get(f"/api/sessions/{session_id}/state").json()
    client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "review", "expected_version": state["versions"]["session"]},
    )
    fake = _use(app, reply("不该走到这里"))

    response = client.post(
        DISCUSS.format(session_id=session_id), json={"message": "再聊两句"}
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "state_not_writable"
    assert fake.seen == []


def test_the_response_declares_every_field_the_ui_needs(app, client) -> None:
    """响应形状本身要被钉住 —— 界面靠它决定显示什么。"""
    session_id, _ = _prepare(client)
    _use(app, reply("先看 unit 1 的目录。"))
    body = client.post(
        DISCUSS.format(session_id=session_id), json={"message": "我该做什么"}
    ).json()
    assert set(body) == {"reply", "stop_reason", "steps", "tool_trace", "llm_meta"}


def test_a_rejected_tool_call_still_lands_in_the_trace(app, client) -> None:
    """轨迹要回答的是"它做过什么、被拒绝过什么"，只记成功会变成一份成绩单。"""
    session_id, step_id = _prepare(client)
    _use(
        app,
        reply("查一下不存在的范围。", call("retrieve_session", scope="全部", filter={})),
        reply("那我换个问法。"),
    )
    response = client.post(
        DISCUSS.format(session_id=session_id), json={"message": "我记过什么"}
    )
    trace = response.json()["tool_trace"]
    assert trace[0]["status"] == "rejected"
    chat = _chat(client, session_id)
    assert chat[-1]["tool_trace"][0]["status"] == "rejected"
