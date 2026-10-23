"""归档复盘端点：**只在归档后跑，而且不写用户消息**。

这条边界比"能不能跑"更重要：
内部指令如果落成一条 user 消息，它会被当成"用户说过的话"，
而 `stated` 偏好会自动生效 —— 等于系统自己给自己授权。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.agent.llm import FakeLLM, reply


def _folder_and_session(client: TestClient) -> str:
    folder = client.post("/api/folders", json={"name": "英语"}).json()["folder_id"]
    return client.post(
        "/api/sessions",
        json={"folder_id": folder, "title": "背单词", "input_goal": "背完核心词"},
    ).json()["session_id"]


def _archive(client: TestClient, session_id: str) -> None:
    state = client.get(f"/api/sessions/{session_id}/state").json()
    response = client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "completed", "expected_version": state["versions"]["session"]},
    )
    assert response.status_code == 200, response.text


def test_archive_review_requires_a_completed_session(app, client) -> None:
    session_id = _folder_and_session(client)
    fake = FakeLLM([reply("不该被调用")])
    app.state.llm_client = fake

    response = client.post(f"/api/sessions/{session_id}/archive-review")
    assert response.status_code == 409
    assert "completed" in response.json()["detail"]
    assert fake.seen == []  # 替身没有被消耗


def test_archive_review_writes_only_the_assistant_message(app, client) -> None:
    session_id = _folder_and_session(client)
    _archive(client, session_id)
    fake = FakeLLM([reply("这次会话没有值得写进全局认知的东西。")])
    app.state.llm_client = fake

    response = client.post(f"/api/sessions/{session_id}/archive-review")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"]
    assert body["proposals"] == []

    chat = client.get(f"/api/sessions/{session_id}/state").json()["chat"]
    # 归档复盘**只落模型回复**：内部指令不是用户说的，不能进证据链。
    assert [row["role"] for row in chat] == ["assistant"]
    assert "archive_review" in chat[0]["tags"]
    # 它确实读到了这次会话（prompt 里包含内部指令），但那条指令没有落库。
    assert "归档复盘" in fake.seen[0][-1].content
