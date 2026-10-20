"""`/api/sessions/{id}/annotations` —— 标注某句 + 看推断理由（P15）。

这一层要证明的是**用户能拿到什么**，而不只是"函数返回了对象"：

1. 标注之后，同一次响应里就有依据（界面不必再发一次请求）；
2. 对不上的引用是 **422**，且响应里说的是"引用找不到"；
3. 列表是信封 `{"items": [...]}`（与其他列表端点同形）；
4. 依据来自**这条消息真实落库的轨迹** —— 所以先用 `/discuss` 真跑一轮
   带工具调用的讨论，再去标注它说的那句话。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import items

ANNOTATIONS = "/api/sessions/{session_id}/annotations"


def _prepare(client: TestClient) -> tuple[str, str]:
    """文件夹 + 会话 + 两步计划。返回 `(session_id, step_id)`。"""
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


def _assistant_message(client: TestClient, session_id: str) -> dict:
    chat = client.get(f"/api/sessions/{session_id}/state").json()["chat"]
    assistants = [row for row in chat if row["role"] == "assistant"]
    assert assistants, "这一轮讨论没有留下助手消息"
    return assistants[-1]


def _annotate(
    client: TestClient, session_id: str, msg_id: str, quote: str, question: str | None = None
):
    body: dict = {"msg_id": msg_id, "quote": quote}
    if question is not None:
        body["question"] = question
    return client.post(ANNOTATIONS.format(session_id=session_id), json=body)


def _reply_with_tools(app, client: TestClient, session_id: str, step_id: str) -> dict:
    """真跑一轮带工具调用的讨论，再返回那条助手消息。

    ★ 走 `/discuss` 而不是直接往库里塞一条消息：依据要证明的是
    "轨迹真的落库了、标注真的读得到它"，而手写的行证明不了这件事。
    """
    from app.agent.llm import FakeLLM, call, reply  # noqa: PLC0415 - 只有这里需要

    app.state.llm_client = FakeLLM(
        [
            reply("", call("propose_plan_change", target=step_id, action="拆小一点")),
            reply("你更适合先把概念过一遍再做题。"),
        ]
    )
    response = client.post(
        f"/api/sessions/{session_id}/discuss",
        json={"message": f"这一步太多了，帮我改一下（{step_id}）", "step_id": step_id},
    )
    assert response.status_code == 200, response.text
    return _assistant_message(client, session_id)


# --------------------------------------------------------------------------- #
# 标注 + 依据
# --------------------------------------------------------------------------- #


def test_annotating_a_sentence_returns_its_basis_at_once(app, client) -> None:
    """**核心验收。** 标一句话，同一次响应里就有它当时的依据。"""
    session_id, step_id = _prepare(client)
    message = _reply_with_tools(app, client, session_id, step_id)

    response = _annotate(
        client, session_id, message["msg_id"], "先把概念过一遍", question="凭什么这么说？"
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["annotation_id"] == "AN-0001"
    assert payload["quote"] == "先把概念过一遍"
    assert payload["question"] == "凭什么这么说？"
    basis = payload["basis"]
    assert basis["msg_id"] == message["msg_id"]
    # 这条回复真的调过工具 —— 依据里必须逐条列出来，且带作用域。
    assert [item["tool"] for item in basis["traces"]] == ["propose_plan_change"]
    assert basis["traces"][0]["scope"] == "session_write"
    # 锚点是**当时记下的**那一步，所以重放得出来。
    assert basis["context_step_id"] == step_id
    assert basis["context_sections"], "有锚点就该重放出节标题"
    assert basis["context_replayed_now"] is True
    # ★ 那句"能证明什么、不能证明什么"必须跟着走。
    assert "不能" in basis["limits"]


def test_a_quote_that_is_not_in_the_reply_is_422(app, client) -> None:
    """引用对不上 → 422，而且理由说的是"逐字来自它"。"""
    session_id, step_id = _prepare(client)
    message = _reply_with_tools(app, client, session_id, step_id)

    refused = _annotate(client, session_id, message["msg_id"], "你更适合直接刷难题")
    assert refused.status_code == 422, refused.text
    assert "逐字" in refused.json()["message"]


def test_a_user_message_cannot_be_annotated(client) -> None:
    """用户的自己的话不能标 —— 那条路是 `/notes`，不是这个。"""
    session_id, _ = _prepare(client)
    client.post(f"/api/sessions/{session_id}/chat", json={"content": "先看一遍"})
    chat = client.get(f"/api/sessions/{session_id}/state").json()["chat"]
    user_id = [row for row in chat if row["role"] == "user"][-1]["msg_id"]

    refused = _annotate(client, session_id, user_id, "先看一遍")
    assert refused.status_code == 422, refused.text
    assert "助手" in refused.json()["message"]


def test_a_missing_message_is_404(client) -> None:
    session_id, _ = _prepare(client)
    response = _annotate(client, session_id, "CM-9999", "随便一句")
    assert response.status_code == 404, response.text


def test_the_list_is_enveloped(client) -> None:
    """列表端点与其他端点同形：`{"items": [...]}`（空的时候也一样）。"""
    session_id, _ = _prepare(client)
    listed = client.get(ANNOTATIONS.format(session_id=session_id))
    assert listed.status_code == 200
    assert listed.json() == {"items": []}


def test_annotations_are_listed_after_being_created(app, client) -> None:
    session_id, step_id = _prepare(client)
    message = _reply_with_tools(app, client, session_id, step_id)
    _annotate(client, session_id, message["msg_id"], "先把概念过一遍")

    listed = client.get(ANNOTATIONS.format(session_id=session_id)).json()
    rows = items(listed)
    assert len(rows) == 1
    assert rows[0]["quote"] == "先把概念过一遍"
    assert rows[0]["basis"]["traces"][0]["tool"] == "propose_plan_change"


def test_getting_one_annotation_recomputes_the_basis(app, client) -> None:
    """单独取一条 = 重新算一次依据（重放），所以理由带 `context_note`。"""
    session_id, step_id = _prepare(client)
    message = _reply_with_tools(app, client, session_id, step_id)
    annotation_id = _annotate(
        client, session_id, message["msg_id"], "先把概念过一遍"
    ).json()["annotation_id"]

    one = client.get(
        ANNOTATIONS.format(session_id=session_id) + f"/{annotation_id}"
    )
    assert one.status_code == 200, one.text
    assert one.json()["basis"]["context_note"]


def test_deleting_returns_the_deleted_annotation(app, client) -> None:
    """删除返回被删掉的那一条 —— 界面据此确认"删的是哪句"。"""
    session_id, step_id = _prepare(client)
    message = _reply_with_tools(app, client, session_id, step_id)
    annotation_id = _annotate(
        client, session_id, message["msg_id"], "先把概念过一遍"
    ).json()["annotation_id"]

    deleted = client.delete(
        ANNOTATIONS.format(session_id=session_id) + f"/{annotation_id}"
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"]["annotation_id"] == annotation_id
    assert items(client.get(ANNOTATIONS.format(session_id=session_id)).json()) == []
    # 消息本身还在 —— 标注是问题，不是结论。
    assert _assistant_message(client, session_id)["msg_id"] == message["msg_id"]


def test_an_annotation_from_another_session_is_not_visible(app, client) -> None:
    """跨会话取不到 —— 标注只在它自己那个会话里成立。"""
    session_id, step_id = _prepare(client)
    message = _reply_with_tools(app, client, session_id, step_id)
    annotation_id = _annotate(
        client, session_id, message["msg_id"], "先把概念过一遍"
    ).json()["annotation_id"]

    other, _ = _prepare(client)
    response = client.get(ANNOTATIONS.format(session_id=other) + f"/{annotation_id}")
    assert response.status_code == 404, response.text
