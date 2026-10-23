"""`/api/sessions/{id}/reflection{,s}` —— 复盘之后按指引写感想（P15）。

这一层要证明的是**用户在什么位置、拿到什么**：

1. 执行中点「写感想」得到的是**一条能照做的原因**（不是 403/404），
   而且指引里的事实来自这次会话真实的数据；
2. 复盘之后写下来的感想里，四要素**分项存着**（将来量完整度要用）；
3. `reflection`（指引，单数）与 `reflections`（写下的，复数）是两条路径 ——
   合并会得到一个「GET 有时返回指引、有时返回列表」的端点。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import items

GUIDE = "/api/sessions/{session_id}/reflection"
REFLECTIONS = "/api/sessions/{session_id}/reflections"


def _active_session(client: TestClient) -> str:
    """一个执行中的会话（带计划 + 用户自己写的完成判据）。"""
    folder = client.post("/api/folders", json={"name": "英语学习"}).json()["folder_id"]
    session_id = client.post(
        "/api/sessions",
        json={"folder_id": folder, "title": "背单词", "input_goal": "把第三章的虚拟语气学会"},
    ).json()["session_id"]
    state = client.get(f"/api/sessions/{session_id}/state").json()
    client.patch(
        f"/api/sessions/{session_id}/input",
        json={
            "expected_version": state["versions"]["input"],
            "success_criteria": "能做对 10 道虚拟语气题",
            "reason": "写下完成判据",
        },
    )
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
            "goal_derived": "先把第三章吃透",
            "steps": [
                {"action": "通读第三章", "output": "一份问题清单"},
                {"action": "做 10 道题", "output": "错题原因"},
            ],
            "checkpoints": [],
        },
    )
    assert plan.status_code == 201, plan.text
    return session_id


def _finish_steps(client: TestClient, session_id: str) -> None:
    state = client.get(f"/api/sessions/{session_id}/state").json()
    for step in state["plan"]["steps"]:
        response = client.patch(
            f"/api/sessions/{session_id}/plan/steps/{step['step_id']}",
            json={"status": "done", "expected_version": state["versions"]["plan"]},
        )
        assert response.status_code == 200, response.text
        state = client.get(f"/api/sessions/{session_id}/state").json()


def _reviewed_session(client: TestClient) -> str:
    """一个已经复盘过的会话（`review` 态 + 一条总结）。"""
    session_id = _active_session(client)
    _finish_steps(client, session_id)
    version = client.get(f"/api/sessions/{session_id}/state").json()["versions"]["session"]
    client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "review", "expected_version": version},
    )
    written = client.post(
        f"/api/sessions/{session_id}/summary",
        json={"content": "第三章过了一遍，题做了一半。"},
    )
    assert written.status_code == 201, written.text
    return session_id


# --------------------------------------------------------------------------- #
# 指引
# --------------------------------------------------------------------------- #


def test_the_guide_says_why_it_is_not_time_yet(client) -> None:
    """执行中点「写感想」：指引照样返回 200，并**说明现在该做什么**。

    ★ 不是 403：一条"现在还不该写"的答案里带着可照做的下一步
    （去写笔记 / 先去复盘），而一个禁用的按钮什么都没说。
    """
    session_id = _active_session(client)

    guide = client.get(GUIDE.format(session_id=session_id))

    assert guide.status_code == 200, guide.text
    body = guide.json()
    assert body["blocked_reason"], "执行中应当说明为什么还不该写"
    assert "/notes" in body["blocked_reason"] or "笔记" in body["blocked_reason"]
    # 指引里的事实是**这次会话的**：进度 + 用户自己写的完成判据
    facts = " ".join(
        fact for item in body["items"] for fact in item["facts"]
    )
    assert "2 步" in facts
    assert "能做对 10 道虚拟语气题" in facts
    assert body["existing"] == 0
    assert body["missing"] == ["what_happened", "expected_gap", "why", "next_change"]


def test_writing_an_empty_reflection_is_422(client) -> None:
    session_id = _reviewed_session(client)
    refused = client.post(REFLECTIONS.format(session_id=session_id), json={})
    assert refused.status_code == 422, refused.text
    assert "空的感想" in refused.json()["message"]


def test_the_four_elements_land_in_the_database(client) -> None:
    """**核心验收。** 四要素分项存下来，正文是拼出来的。"""
    session_id = _reviewed_session(client)
    answers = {
        "what_happened": "通读了第三章，题做了 5 道",
        "expected_gap": "以为能做对 10 道，实际错了 3 道",
        "why": "虚拟语气的时态倒装没认出来",
        "next_change": "下次先做 5 道只考倒装的题",
    }

    written = client.post(
        REFLECTIONS.format(session_id=session_id), json={"answers": answers}
    )

    assert written.status_code == 201, written.text
    body = written.json()
    assert body["reflection_id"] == "RF-0001"
    assert body["answers"] == answers
    assert "【原因】虚拟语气的时态倒装没认出来" in body["content"]
    assert body["summary_id"], "感想要记下它是在哪份总结之后写的"

    listed = items(client.get(REFLECTIONS.format(session_id=session_id)).json())
    assert len(listed) == 1 and listed[0]["reflection_id"] == "RF-0001"

    # 指引跟着更新：写过的那几项不再出现在 `missing` 里
    guide = client.get(GUIDE.format(session_id=session_id)).json()
    assert guide["missing"] == []
    assert guide["existing"] == 1


def test_the_guide_marks_what_is_already_written(client) -> None:
    """续写时能看出哪几项已经写过 —— 不必重新回答一遍。"""
    session_id = _reviewed_session(client)
    client.post(
        REFLECTIONS.format(session_id=session_id),
        json={"answers": {"why": "倒装没认出来"}},
    )

    guide = client.get(GUIDE.format(session_id=session_id)).json()

    answered = {item["key"]: item["answered"] for item in guide["items"]}
    assert answered == {
        "what_happened": False,
        "expected_gap": False,
        "why": True,
        "next_change": False,
    }
    assert guide["missing"] == ["what_happened", "expected_gap", "next_change"]


def test_a_plain_body_is_accepted(client) -> None:
    """只想写一段话也可以 —— 拼装是呈现，不是要求。"""
    session_id = _reviewed_session(client)
    written = client.post(
        REFLECTIONS.format(session_id=session_id),
        json={"content": "这次学得比上次踏实，但题做得太少。"},
    )
    assert written.status_code == 201, written.text
    assert written.json()["answers"] is None
