"""`POST /api/sessions/{id}/discuss/stream` —— 流式那条路自己的规矩。

一次性端点（`test_discuss_api.py`）与这里测的是**同一次讨论**，
所以这里的重点不是"能改计划"（那边已经证明了），而是流式**独有**的四件事：

1. **帧格式真的是 SSE**（`event:` + `data:` + 空行）。前端用 `fetch` 手工解析它，
   所以格式不是"反正浏览器会懂"的东西 —— 破一格就是前端全盘收不到。
2. **同一个错误在两个端点上状态码一致。** 响应头一发出就改不了状态码，
   于是"预检做没做全"变成一个可观测的差别：`/discuss` 报 404 的事，
   `/discuss/stream` 不能变成 200 + 一条事件。这条缺陷是写这个文件时
   被自己的测试抓出来的（锚点没有在开流前解析）。
3. **断线要留下真话。** 回答没写完时不许把半句话当回答（那会让用户以为
   他读到的是完整的），但必须留下"这次讨论发生过、而且没结论"。
4. **未预期的错误要看得见**：流已经开始，改不了状态码，唯一出路是发一条
   `error` 事件 **并且**在库里留一条带 `stop:internal_error` 的痕迹 ——
   否则用户刷新页面只会看到自己那句没有回音的话。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agent.events import AgentEvent, EventType
from app.agent.llm import FakeLLM, call, reply
from app.agent.loop import AgentLoop

STREAM = "/api/sessions/{session_id}/discuss/stream"
DISCUSS = "/api/sessions/{session_id}/discuss"


def _prepare(client: TestClient) -> tuple[str, str]:
    """与 `test_discuss_api.py` 铺同一个会话（同一份前置，便于两个文件对读）。"""
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


def _events(body: str) -> list[tuple[str, dict]]:
    """把响应体按 **SSE 的字面格式**解析成 `(事件名, 数据)`。

    刻意手写而不引入客户端库：这条测试要证明的正是帧格式本身对。
    用库去解析会让"格式写错了但库容错"变成一件看不见的事。
    任何一格不符（少一个空行、`data:` 后少一个空格）都会在这里炸开。
    """
    out: list[tuple[str, dict]] = []
    assert body.endswith("\n\n"), "每一帧都必须以空行结束，否则浏览器不会派发它"
    for block in body[:-2].split("\n\n"):
        lines = block.split("\n")
        assert len(lines) == 2, f"一帧必须正好两行，实际 {len(lines)} 行：{block!r}"
        assert lines[0].startswith("event: "), lines[0]
        assert lines[1].startswith("data: "), lines[1]
        out.append((lines[0][len("event: ") :], json.loads(lines[1][len("data: ") :])))
    return out


def _chat(client: TestClient, session_id: str) -> list[dict]:
    return client.get(f"/api/sessions/{session_id}/state").json()["chat"]


def _use(app, *replies, chunk_size: int | None = 2) -> FakeLLM:
    """流式的测试**必须分帧**：整段一帧的话，"解析多帧"这件事从没被走过。"""
    fake = FakeLLM(list(replies), chunk_size=chunk_size)
    app.state.llm_client = fake
    return fake


# --------------------------------------------------------------------------- #
# 闭环与可见性
# --------------------------------------------------------------------------- #


def test_the_stream_carries_the_whole_path(app, client) -> None:
    """一次讨论的全过程都在流里，而且**顺序**就是它实际发生的顺序。"""
    session_id, step_id = _prepare(client)
    _use(
        app,
        reply("我先看一眼你的基线。", call("retrieve_cognitive", scope="m1")),
        reply("看完了，你的基线里还没有读数。"),
    )

    response = client.post(
        STREAM.format(session_id=session_id),
        json={"message": "我现在该做什么？", "step_id": step_id},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache"
    # 反代缓冲会把"流式"退化成一次性返回，且没有任何报错 —— 所以显式关掉
    assert response.headers["x-accel-buffering"] == "no"

    events = _events(response.text)
    kinds = [name for name, _ in events]
    assert kinds[0] == "stage"
    assert kinds[-1] == "done"
    assert "tool_call" in kinds and "tool_result" in kinds

    # 阶段名要说清楚"它在做什么"，而不是只发一个笼统的 stage
    stages = [data["name"] for name, data in events if name == "stage"]
    assert stages == ["read_context", "wait_model", "run_tools", "wait_model"]

    # seq 严格递增：前端据此判断丢帧
    assert [data["seq"] for _, data in events] == list(range(1, len(events) + 1))

    # 增量拼起来的内容（显示用）与收官帧的回复（判定用）
    tokens = "".join(data["text"] for name, data in events if name == "token")
    assert tokens == "我先看一眼你的基线。看完了，你的基线里还没有读数。"
    done = events[-1][1]
    assert done["reply"] == "看完了，你的基线里还没有读数。"
    assert done["stop_reason"] == "completed"
    assert [entry["tool"] for entry in done["tool_trace"]] == ["retrieve_cognitive"]


def test_the_stream_actually_changes_the_plan(app, client) -> None:
    """**流式那条路也是真的能改计划的** —— 不是只把话说出来。

    一次性端点已经证明过闭环，但那证明不了"流式路径上工具也真的执行了"
    （一个只 yield 事件、不调 dispatcher 的实现同样能通过上一条测试）。
    """
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
        reply("改好了，你看一下。"),
    )

    response = client.post(
        STREAM.format(session_id=session_id),
        json={"message": "一次 50 个词太多了", "step_id": step_id},
    )
    assert response.status_code == 200

    plan = client.get(f"/api/sessions/{session_id}/plan").json()
    assert plan["version"] == 2, "流式路径上计划必须真的被改了"
    assert plan["steps"][0]["action"] == "每天通读 20 行"

    events = _events(response.text)
    result = next(data for name, data in events if name == "tool_result")
    assert result["status"] == "success"
    assert result["tool"] == "propose_plan_change"


def test_the_assistant_message_is_persisted_with_its_trace(app, client) -> None:
    """用户明确要求过：能看到它做了什么。轨迹要能到达用户看得见的地方。"""
    session_id, step_id = _prepare(client)
    _use(
        app,
        reply("我查一下。", call("retrieve_cognitive", scope="m1")),
        reply("查完了。"),
    )
    client.post(
        STREAM.format(session_id=session_id), json={"message": "看看基线", "step_id": step_id}
    )

    chat = _chat(client, session_id)
    assert [row["role"] for row in chat] == ["user", "assistant"]
    assert chat[0]["content"] == "看看基线"
    assert chat[1]["content"] == "查完了。"
    assert chat[1]["tags"] == ["stop:completed"]
    assert [entry["tool"] for entry in chat[1]["tool_trace"]] == ["retrieve_cognitive"]


# --------------------------------------------------------------------------- #
# 预检：错误的状态码必须在开流之前决定
# --------------------------------------------------------------------------- #


def test_a_missing_session_is_a_404_and_costs_no_model_call(app, client) -> None:
    fake = _use(app, reply("这句不该被用到"))
    response = client.post(
        STREAM.format(session_id="S-9999"), json={"message": "在吗"}
    )
    assert response.status_code == 404
    assert fake.seen == [], "一次注定失败的请求不该让本地模型白跑"


def test_a_planless_session_gives_the_same_404_on_both_endpoints(app, client) -> None:
    """**同一个错误，两个端点必须同一个状态码。**

    这条是写这个文件时被自己的实现绊出来的：预检起初只查了"会话在不在"，
    于是"会话没有计划"在 `/discuss` 上是 404、在 `/discuss/stream` 上
    变成 200 + 一条 error 事件 —— 客户端再也分不清"没有计划"与"模型挂了"。
    修法是让预检也把锚点定下来（`AgentLoop.resolve_anchor`）。
    """
    folder = client.post("/api/folders", json={"name": "空会话"}).json()["folder_id"]
    session_id = client.post(
        "/api/sessions",
        json={"folder_id": folder, "title": "还没规划", "input_goal": "先想想"},
    ).json()["session_id"]
    state = client.get(f"/api/sessions/{session_id}/state").json()
    client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "active", "expected_version": state["versions"]["session"]},
    )
    fake = _use(app, reply("这句不该被用到"))

    sync = client.post(DISCUSS.format(session_id=session_id), json={"message": "在吗"})
    stream = client.post(STREAM.format(session_id=session_id), json={"message": "在吗"})

    assert sync.status_code == 404
    assert stream.status_code == 404, stream.text[:200]
    assert stream.json()["code"] == "not_found"
    assert fake.seen == []


def test_a_state_that_cannot_be_chatted_in_is_a_real_409(app, client) -> None:
    """状态机拒绝要在**花掉一次模型调用之前**给出 409，而不是 SSE 事件。"""
    folder = client.post("/api/folders", json={"name": "草稿"}).json()["folder_id"]
    session_id = client.post(
        "/api/sessions",
        json={"folder_id": folder, "title": "还没开始", "input_goal": "先想想"},
    ).json()["session_id"]
    fake = _use(app, reply("这句不该被用到"))

    response = client.post(
        STREAM.format(session_id=session_id), json={"message": "聊两句"}
    )
    assert response.status_code == 409, response.text[:200]
    assert response.json()["code"] == "state_not_writable"
    assert fake.seen == []


# --------------------------------------------------------------------------- #
# 断线：**不能丢话，也不能把半句话当成回答**
# --------------------------------------------------------------------------- #

#: 与 `app.api.discuss.DISCONNECTED_NOTE` 同一句话。这里刻意写字面量：
#: 若那句提示被改掉，这条测试必须跟着被看一眼 —— 它是给用户看的话。
DISCONNECTED_NOTE = "（你在回答写完之前离开了，这次讨论没有留下结论。）"


def test_a_disconnect_leaves_an_honest_note_instead_of_half_an_answer(
    app, client
) -> None:
    """**HTTP 层测不出来这条** —— 所以直接驱动生成器。

    `TestClient` 会先把整个响应在 portal 里跑完再交给客户端：提前跳出
    `iter_lines()` 时库里拿到的仍然是 `stop:completed`（这一点是实测的）。
    因此"断开"必须在生成器这一层制造：读几帧、然后 `close()`。

    验证的是两件事，缺一不可：
    ① 半句话**没有**被存成回答（那会让用户以为他读到的是完整的）；
    ② 但这次讨论**留下了记录**（否则用户刷新页面后，只看到自己那句
       没有回音的话，分不清是"回答丢了"还是"请求失败了"）。
    """
    from app.api.discuss import stream_events
    from app.db import session_scope

    session_id, step_id = _prepare(client)
    fake = FakeLLM([reply("好的，我把第一步拆细一点，你一次只记 20 个。")], chunk_size=2)

    with session_scope(app.state.session_factory) as db:
        loop = AgentLoop(db, session_id, fake)
        frames = stream_events(
            db, loop, message="这一步太难", step_id=step_id, history=[]
        )
        # 读到第一个 token 就走 —— 此时模型已经回了话，但讨论没有收官
        taken = [next(frames) for _ in range(3)]
        assert taken[-1].startswith("event: token"), taken
        frames.close()  # ← 客户端断开

    chat = _chat(client, session_id)
    assert len(chat) == 1, "端点没跑（这里只驱动生成器），所以只有助手那一条落库"
    row = chat[0]
    assert row["role"] == "assistant"
    assert row["tags"] == ["stop:client_disconnected"]
    assert row["content"] == DISCONNECTED_NOTE
    assert "20 个" not in row["content"], "半句话不许被存成回答"


def test_the_response_closes_the_generator_when_the_client_goes_away(
    app, client
) -> None:
    """**这条测试盯的是一个"测试全绿但真实行为不同"的缺口。**

    Starlette 的 `StreamingResponse` 对同步迭代器用的是
    `iterate_in_threadpool(content)`，而那个包装器**没有 `finally`**。
    客户端断开时 `send()` 抛 `OSError`，`stream_response` 里那个 `async for`
    被直接放弃 —— 同步生成器**留在挂起状态**，它的 `GeneratorExit`
    只会在某次 GC 时被投进去。

    后果是：上面那条"断线留档"的测试仍然会绿（它直接 `close()` 生成器），
    而真实服务器上那段留档代码**大多数时候不执行**。
    所以这里不测生成器，测**响应**：

    ① 让 `send` 在第二帧之后抛 `OSError`（这就是真实的断开）；
    ② 断言那次讨论仍然在库里留下了记录。

    用 `asyncio.run` 直接驱动 `stream_response`，因此不需要起服务器 ——
    而它测的正是服务器上会走的那段代码。
    """
    import asyncio

    from app.api.discuss import EventStreamResponse, stream_events
    from app.db import session_scope

    session_id, step_id = _prepare(client)
    fake = FakeLLM([reply("好的，我把第一步拆细一点，你一次只记 20 个。")], chunk_size=2)

    async def drive() -> None:
        with session_scope(app.state.session_factory) as db:
            loop = AgentLoop(db, session_id, fake)
            response = EventStreamResponse(
                stream_events(db, loop, message="这一步太难", step_id=step_id, history=[])
            )
            sent = 0

            async def send(message: dict) -> None:
                nonlocal sent
                if message["type"] == "http.response.body" and message.get("body"):
                    sent += 1
                    if sent >= 2:  # 客户端在第二帧之后走了
                        raise OSError("客户端断开")

            with pytest.raises(OSError):
                await response.stream_response(send)

    asyncio.run(drive())

    chat = _chat(client, session_id)
    assert chat, (
        "断开时那段留档代码没有执行 —— 说明同步生成器没有被关闭，"
        "而真实服务器上用户的一整轮对话就这样无声地消失了"
    )
    assert chat[-1]["tags"] == ["stop:client_disconnected"]
    assert chat[-1]["content"] == DISCONNECTED_NOTE


def test_a_disconnect_after_the_answer_keeps_the_answer(app, client) -> None:
    """回答已经收官之后再断开 → 存的就是那条回答（不是"你离开了"）。

    反面例子：一个"只要断开就写提示"的实现会让这条测试红。
    断在收官之后与断在收官之前是两件事，它们的区别必须可观测。
    """
    from app.api.discuss import stream_events
    from app.db import session_scope

    session_id, step_id = _prepare(client)
    fake = FakeLLM([reply("先别加量，按 20 个来。")], chunk_size=3)

    with session_scope(app.state.session_factory) as db:
        loop = AgentLoop(db, session_id, fake)
        frames = list(
            stream_events(db, loop, message="太多了", step_id=step_id, history=[])
        )
        assert frames[-1].startswith("event: done")

    chat = _chat(client, session_id)
    assert chat[-1]["tags"] == ["stop:completed"]
    assert chat[-1]["content"] == "先别加量，按 20 个来。"


# --------------------------------------------------------------------------- #
# 未预期的错误：流已经开始，也要留下能查的东西
# --------------------------------------------------------------------------- #


def test_an_internal_error_is_visible_and_leaves_a_trace(
    app, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """工具层抛出契约之外的异常时，用户要看到错误、库里要留下痕迹。

    为什么必须两样都有：只发事件的话，用户刷新页面后看到的是自己那句
    没有回音的话（分不清"回答丢了"还是"请求失败了"）；只入库不发事件的话，
    界面会一直卡在他等回答的地方。
    """

    def broken(self, message, *, step_id=None, history=()):
        yield AgentEvent(type=EventType.TOKEN, seq=1, step=1, data={"text": "我认为第"})
        raise RuntimeError("工具层炸了")

    monkeypatch.setattr(AgentLoop, "run_stream", broken)
    session_id, step_id = _prepare(client)
    _use(app, reply("用不到"))

    response = client.post(
        STREAM.format(session_id=session_id), json={"message": "改一下", "step_id": step_id}
    )
    # 头已经发出去了，状态码只能是 200 —— 所以错误必须是一条事件
    assert response.status_code == 200
    events = _events(response.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["kind"] == "RuntimeError"
    assert "工具层炸了" in events[-1][1]["error"]

    row = _chat(client, session_id)[-1]
    assert row["tags"] == ["stop:internal_error"]
    assert "RuntimeError" in row["content"], "类型名是分辨哪一类坏的唯一线索"
    # 已经说过的那半句**不在**回答里（它不是回答），但用户确实看到过它
    assert "我认为第" not in row["content"]


def test_the_error_frame_continues_the_sequence(app, client, monkeypatch: pytest.MonkeyPatch) -> None:
    """`error` 帧的 `seq` 必须**接着往下数**，不能重来也不能是 0。

    契约是「`seq` 从 1 严格递增」。异常路径不在循环里，拿不到循环的计数器，
    早先这里写死 `seq=0` —— 于是同一路流里出现 `1,2,3…,0`。
    客户端若按 `seq` 去重或排序（这是这类协议最常见的用法），
    那一帧会被当成"过期帧"丢掉，而它恰恰是唯一一条告诉用户
    "为什么没有回答"的信息：界面会停在"正在思考"，直到用户自己放弃。

    这类缺陷不会让任何现有测试变红 —— 因为**没有人检查顺序**。
    """

    def broken(self, message, *, step_id=None, history=()):
        yield AgentEvent(type=EventType.TOKEN, seq=1, step=1, data={"text": "半句"})
        yield AgentEvent(type=EventType.TOKEN, seq=2, step=1, data={"text": "再说半句"})
        raise RuntimeError("工具层炸了")

    monkeypatch.setattr(AgentLoop, "run_stream", broken)
    session_id, step_id = _prepare(client)
    _use(app, reply("用不到"))

    response = client.post(
        STREAM.format(session_id=session_id), json={"message": "改一下", "step_id": step_id}
    )
    events = _events(response.text)
    seqs = [payload.get("seq") for _type, payload in events]
    assert seqs == [1, 2, 3], f"error 帧没有接着数：{seqs}"

