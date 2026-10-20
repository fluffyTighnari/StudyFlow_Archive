"""`/api/proposals` —— 提交、待确认队列、逐条裁决。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.helpers import items


def _r4a_intent(statement: str = "我更喜欢用思维导图整理") -> dict:
    """R4a + experienced → needs_confirmation（判定表 R4a.add_preference 行）。"""
    return {
        "trigger_type": "R4a",
        "session_ref": "S-001",
        "intents": [
            {
                "intent_id": f"I-{abs(hash(statement)) % 10**6}",
                "action": "add_preference",
                "target": "M2.preferences",
                "value": {"statement": statement, "priority": 7},
                "basis": "experienced",
                "reasoning": "Session 里反复出现同一种做法",
                "confidence": "medium",
            }
        ],
    }


def test_submitting_a_confirmable_intent_leaves_the_proposal_pending(
    client: TestClient,
) -> None:
    response = client.post("/api/proposals", json=_r4a_intent())
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending_confirmation"
    assert body["results"][0]["verdict"] == "needs_confirmation"
    assert body["results"][0]["status"] == "pending"
    # 还没裁决 → 偏好**没有**落库。
    assert items(client.get("/api/preferences").json()) == []


def test_pending_queue_is_queryable(client: TestClient) -> None:
    submitted = client.post("/api/proposals", json=_r4a_intent()).json()
    pending = items(client.get("/api/proposals?status=pending_confirmation").json())
    assert [row["proposal_id"] for row in pending] == [submitted["proposal_id"]]
    assert items(client.get("/api/proposals?status=applied").json()) == []


def test_proposal_detail_carries_the_system_verdict(client: TestClient) -> None:
    """提案卡必须能回答「为什么需要你确认」—— 靠的是 `precheck` 与 `precheck_reason`。"""
    submitted = client.post("/api/proposals", json=_r4a_intent()).json()
    detail = client.get(f"/api/proposals/{submitted['proposal_id']}").json()

    assert detail["trigger_type"] == "R4a"
    assert detail["resolved_at"] is None
    intent = detail["intents"][0]
    assert intent["precheck"] == "needs_confirmation"
    assert intent["precheck_reason"]
    # LLM 自评与系统结论是两个字段，不可混为一谈。
    assert intent["confidence"] == "medium"
    assert intent["status"] == "pending"
    assert intent["decided_at"] is None


def test_unknown_proposal_is_404(client: TestClient) -> None:
    assert client.get("/api/proposals/UP-9999").status_code == 404
    assert (
        client.post(
            "/api/proposals/UP-9999/resolve",
            json={"decisions": [{"intent_id": "I-1", "approved": True}]},
        ).status_code
        == 404
    )


def test_approving_writes_the_preference_and_closes_the_proposal(
    client: TestClient,
) -> None:
    submitted = client.post("/api/proposals", json=_r4a_intent()).json()
    intent_id = submitted["results"][0]["intent_id"]

    response = client.post(
        f"/api/proposals/{submitted['proposal_id']}/resolve",
        json={"decisions": [{"intent_id": intent_id, "approved": True}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "applied"
    assert body["results"][0]["status"] == "applied"

    assert [row["statement"] for row in items(client.get("/api/preferences").json())] == [
        "我更喜欢用思维导图整理"
    ]
    assert items(client.get("/api/proposals?status=pending_confirmation").json()) == []


def test_rejecting_leaves_no_data_but_keeps_the_record(client: TestClient) -> None:
    submitted = client.post("/api/proposals", json=_r4a_intent()).json()
    intent_id = submitted["results"][0]["intent_id"]

    body = client.post(
        f"/api/proposals/{submitted['proposal_id']}/resolve",
        json={"decisions": [{"intent_id": intent_id, "approved": False, "reason": "我没这习惯"}]},
    ).json()
    assert body["status"] == "rejected"
    assert body["results"][0]["status"] == "rejected"
    assert items(client.get("/api/preferences").json()) == []

    # 拒绝也必须留痕 —— 否则「我问过但被否了」这件事会消失，LLM 会反复重提。
    logs = items(client.get("/api/router-logs").json())
    assert logs[0]["status"] == "rejected"
    assert "我没这习惯" in logs[0]["reasoning"]


def test_resolving_twice_is_409(client: TestClient) -> None:
    """已裁决的提案不能重放 —— 否则同一份确认会被用来写两次。"""
    submitted = client.post("/api/proposals", json=_r4a_intent()).json()
    intent_id = submitted["results"][0]["intent_id"]
    payload = {"decisions": [{"intent_id": intent_id, "approved": True}]}

    assert client.post(f"/api/proposals/{submitted['proposal_id']}/resolve", json=payload).status_code == 200
    second = client.post(f"/api/proposals/{submitted['proposal_id']}/resolve", json=payload)
    assert second.status_code == 409
    assert second.json()["code"] == "version_conflict"
    # 关键：没有写第二条偏好。
    assert len(items(client.get("/api/preferences").json())) == 1


def test_resolving_an_intent_from_another_proposal_is_422(client: TestClient) -> None:
    submitted = client.post("/api/proposals", json=_r4a_intent()).json()
    response = client.post(
        f"/api/proposals/{submitted['proposal_id']}/resolve",
        json={"decisions": [{"intent_id": "I-does-not-exist", "approved": True}]},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "intent_rejected"


def test_partial_approval_keeps_the_proposal_open(client: TestClient) -> None:
    """只裁决一部分是允许的：没表态的 intent 保持 pending，提案不关闭。"""
    payload = {
        "trigger_type": "R4a",
        "session_ref": "S-001",
        "intents": [
            {
                "intent_id": "I-A",
                "action": "add_preference",
                "target": "M2.preferences",
                "value": {"statement": "偏好甲"},
                "basis": "experienced",
                "reasoning": "x",
                "confidence": "medium",
            },
            {
                "intent_id": "I-B",
                "action": "add_preference",
                "target": "M2.preferences",
                "value": {"statement": "偏好乙"},
                "basis": "experienced",
                "reasoning": "y",
                "confidence": "medium",
            },
        ],
    }
    submitted = client.post("/api/proposals", json=payload).json()
    assert submitted["status"] == "pending_confirmation"

    body = client.post(
        f"/api/proposals/{submitted['proposal_id']}/resolve",
        json={"decisions": [{"intent_id": "I-A", "approved": True}]},
    ).json()
    assert body["status"] == "pending_confirmation"
    assert {r["intent_id"]: r["status"] for r in body["results"]} == {
        "I-A": "applied",
        "I-B": "pending",
    }
    assert [row["statement"] for row in items(client.get("/api/preferences").json())] == ["偏好甲"]


def test_inferred_parameter_update_is_rejected_and_still_audited(
    client: TestClient,
) -> None:
    """合法但被策略拒绝 → 201 + rejected，并且**有**审计记录。

    这类拒绝不返回 422：它是系统的正式裁定，需要一份可回看的提案与日志
    （否则 LLM 会一遍遍重提同一件事，而没人知道上次为什么被否）。
    """
    payload = {
        "trigger_type": "R3",
        "intents": [
            {
                "intent_id": "I-INF",
                "action": "update_parameter",
                "target": "M1.V-SE",
                "value": {
                    "channel": "measured",
                    "score": 5.0,
                    "completeness": "complete",
                    "confidence": "provisional",
                },
                "basis": "inferred",
                "reasoning": "我觉得他应该挺强",
                "confidence": "low",
            }
        ],
    }
    response = client.post("/api/proposals", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "rejected"
    assert body["results"][0]["verdict"] == "must_reject"

    assert items(client.get("/api/m1/V-SE/history").json()) == []
    logs = items(client.get("/api/router-logs").json())
    assert logs[0]["status"] == "rejected"
    assert logs[0]["trigger"] == "R3"


def test_duplicate_intent_ids_are_refused_before_anything_is_written(
    client: TestClient,
) -> None:
    payload = {
        "trigger_type": "R4a",
        "intents": [
            {
                "intent_id": "I-SAME",
                "action": "add_preference",
                "target": "M2.preferences",
                "value": {"statement": "甲"},
                "basis": "experienced",
                "reasoning": "x",
                "confidence": "medium",
            },
            {
                "intent_id": "I-SAME",
                "action": "add_preference",
                "target": "M2.preferences",
                "value": {"statement": "乙"},
                "basis": "experienced",
                "reasoning": "y",
                "confidence": "medium",
            },
        ],
    }
    response = client.post("/api/proposals", json=payload)
    assert response.status_code == 422
    assert items(client.get("/api/proposals").json()) == []
    assert items(client.get("/api/router-logs").json()) == []
