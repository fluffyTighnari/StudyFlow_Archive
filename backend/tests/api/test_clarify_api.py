"""澄清问题的 HTTP 层（P13）。

单测（`tests/session/test_clarify.py`）验的是**闸门与规则**。
这里验的是接线，而接线上有三件只有走 HTTP 才看得见的事：

1. **答案真的写进了定义区**：`/answer` 返回的 `input.goal` 必须是答案本身，
   而且 `GET /input/revisions` 里看得到那一条修订 ——
   答案只落在问题行上的话，定义区仍然是空的，下一回合闸门再拦一次，
   而用户会觉得"我明明答了"。
2. **重复问、重复答、问非定义区字段**都必须被拒（422，不是静默成功）。
3. **闸门只约束助手**：用户自己改计划/改定义区不受未答问题影响。
   反过来的表现是「我的计划被一个我没答的问题锁住了」。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app = create_app(
        Settings(database_url="sqlite+pysqlite:///:memory:", seed_on_startup=True)
    )
    with TestClient(app) as test_client:
        yield test_client


def _draft_session(client: TestClient) -> str:
    folder = client.post("/api/folders", json={"name": "复习"})
    assert folder.status_code in (200, 201), folder.text
    response = client.post(
        "/api/sessions",
        json={
            "title": "复习第三章",
            "input_goal": "先随便看看",
            "folder_id": folder.json()["folder_id"],
        },
    )
    assert response.status_code in (200, 201), response.text
    session_id = response.json()["session_id"]
    return session_id


def _clear_goal(client: TestClient, session_id: str) -> None:
    """把目标清空 —— 这是"目标空着"在**已激活**会话里唯一可能的状态。"""
    state = client.get(f"/api/sessions/{session_id}/state").json()
    version = state["versions"]["input"]
    response = client.patch(
        f"/api/sessions/{session_id}/input",
        json={"expected_version": version, "goal": None, "reason": "先清掉"},
    )
    assert response.status_code == 200, response.text


def _live_session(client: TestClient) -> str:
    session_id = _draft_session(client)
    state = client.get(f"/api/sessions/{session_id}/state").json()
    activated = client.post(
        f"/api/sessions/{session_id}/transitions",
        json={
            "target": "active",
            "expected_version": state["versions"]["session"],
            "reason": "开始",
        },
    )
    assert activated.status_code == 200, activated.text
    _clear_goal(client, session_id)
    return session_id


def _ask(client: TestClient, session_id: str, **over) -> dict:
    body = {
        "slot": "goal",
        "question": "这次你想达成什么？",
        "options": ["推一遍公式", "只弄懂概念"],
    }
    body.update(over)
    response = client.post(f"/api/sessions/{session_id}/clarifications", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _input_version(client: TestClient, session_id: str) -> int:
    return client.get(f"/api/sessions/{session_id}/state").json()["versions"]["input"]


def test_the_list_starts_empty_and_shows_the_open_question(
    client: TestClient,
) -> None:
    session_id = _live_session(client)
    assert client.get(f"/api/sessions/{session_id}/clarifications").json() == {
        "items": []
    }
    asked = _ask(client, session_id)
    assert asked["status"] == "open"
    assert asked["clarification_id"].startswith("CL-")

    listed = client.get(f"/api/sessions/{session_id}/clarifications").json()["items"]
    assert [row["clarification_id"] for row in listed] == [asked["clarification_id"]]
    only_open = client.get(
        f"/api/sessions/{session_id}/clarifications", params={"status": "open"}
    ).json()["items"]
    assert len(only_open) == 1


def test_answering_writes_the_definition_and_leaves_a_revision(
    client: TestClient,
) -> None:
    """★ 这是整套机制的关键一步，也是唯一会静默出错的一步。"""
    session_id = _live_session(client)
    asked = _ask(client, session_id)
    response = client.post(
        f"/api/sessions/{session_id}/clarifications/{asked['clarification_id']}/answer",
        json={"answer": "把第三章的公式推导自己推一遍", "expected_version": _input_version(client, session_id)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["clarification"]["status"] == "answered"
    assert body["input"]["goal"] == "把第三章的公式推导自己推一遍"

    # 定义区真的改了（不是只返回给前端看）
    state = client.get(f"/api/sessions/{session_id}/state").json()
    assert state["input"]["goal"] == "把第三章的公式推导自己推一遍"

    # 修订史里有一条，理由是这次回答
    revisions = client.get(f"/api/sessions/{session_id}/input/revisions").json()["items"]
    answered = [r for r in revisions if asked["clarification_id"] in (r["reason"] or "")]
    assert answered and answered[0]["field"] == "goal"
    assert answered[0]["changed_by"] == "user"


def test_asking_twice_on_the_same_slot_is_refused(client: TestClient) -> None:
    session_id = _live_session(client)
    _ask(client, session_id)
    again = client.post(
        f"/api/sessions/{session_id}/clarifications",
        json={"slot": "goal", "question": "到底要什么？"},
    )
    assert again.status_code == 422, again.text
    assert "已经有一个还没答的问题" in again.text


def test_asking_about_a_slot_that_already_has_content_is_refused(
    client: TestClient,
) -> None:
    session_id = _draft_session(client)  # 没清空 goal
    response = client.post(
        f"/api/sessions/{session_id}/clarifications",
        json={"slot": "goal", "question": "要达成什么？"},
    )
    assert response.status_code == 422, response.text
    assert "已经有内容" in response.text


def test_asking_about_a_field_outside_the_definition_is_refused(
    client: TestClient,
) -> None:
    session_id = _live_session(client)
    response = client.post(
        f"/api/sessions/{session_id}/clarifications",
        json={"slot": "mood", "question": "今天心情如何？"},
    )
    assert response.status_code == 422, response.text
    assert "不是任务定义区的字段" in response.text


def test_an_empty_answer_is_refused_and_a_second_answer_is_refused(
    client: TestClient,
) -> None:
    session_id = _live_session(client)
    asked = _ask(client, session_id)
    cid = asked["clarification_id"]

    empty = client.post(
        f"/api/sessions/{session_id}/clarifications/{cid}/answer",
        json={"answer": "   ", "expected_version": _input_version(client, session_id)},
    )
    assert empty.status_code == 422, empty.text

    first = client.post(
        f"/api/sessions/{session_id}/clarifications/{cid}/answer",
        json={"answer": "推一遍公式", "expected_version": _input_version(client, session_id)},
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/api/sessions/{session_id}/clarifications/{cid}/answer",
        json={"answer": "其实是背单词", "expected_version": _input_version(client, session_id)},
    )
    assert second.status_code == 422, second.text
    assert "已经结案" in second.text


def test_dismissing_needs_a_reason_and_keeps_the_slot_empty(
    client: TestClient,
) -> None:
    """★ 搁置 ≠ 答了：槽位仍然是空的，只是闸门放行并让计划带上"这是猜的"。"""
    session_id = _live_session(client)
    asked = _ask(client, session_id)
    cid = asked["clarification_id"]

    refused = client.post(
        f"/api/sessions/{session_id}/clarifications/{cid}/dismiss", json={"reason": " "}
    )
    assert refused.status_code == 422, refused.text

    done = client.post(
        f"/api/sessions/{session_id}/clarifications/{cid}/dismiss",
        json={"reason": "先按你的判断来"},
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "dismissed"
    assert done.json()["answer"] is None
    assert client.get(f"/api/sessions/{session_id}/state").json()["input"]["goal"] is None


def test_an_unknown_clarification_is_a_404(client: TestClient) -> None:
    session_id = _live_session(client)
    response = client.post(
        f"/api/sessions/{session_id}/clarifications/CL-9999/answer",
        json={"answer": "随便", "expected_version": _input_version(client, session_id)},
    )
    assert response.status_code == 404, response.text


def test_a_question_from_another_session_is_refused(client: TestClient) -> None:
    """★ 跨会话答题会把答案写进**别的**会话的定义区 —— 一条路径两个会话，必须拒。"""
    one = _live_session(client)
    two = _live_session(client)
    asked = _ask(client, one)
    response = client.post(
        f"/api/sessions/{two}/clarifications/{asked['clarification_id']}/answer",
        json={"answer": "推一遍公式", "expected_version": _input_version(client, two)},
    )
    assert response.status_code == 422, response.text
    assert "不属于会话" in response.text
