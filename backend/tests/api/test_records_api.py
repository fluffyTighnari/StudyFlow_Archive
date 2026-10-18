"""`/api/preferences` 与 `/api/evidence` —— R1 直写路径。

这两组测试要证明的核心只有一句：**UI 直写没有走捷径。**
每次 POST 都会留下一份提案 + 一条审计日志，与 LLM 提议完全同构。
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_direct_write_creates_a_proposal_and_an_audit_log(client: TestClient) -> None:
    response = client.post(
        "/api/preferences",
        json={"statement": "先看例子再看定义", "priority": 8},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "applied"
    assert body["trigger_type"] == "R1"
    result = body["results"][0]
    assert result["verdict"] == "auto_apply"
    assert result["status"] == "applied"
    assert result["router_log_id"] is not None

    # 「直写」的完整含义：它有提案、有日志，可被审计、可被回滚指认。
    proposal = client.get(f"/api/proposals/{body['proposal_id']}").json()
    assert proposal["source"] == "user_ui"
    assert proposal["intents"][0]["basis"] == "stated"

    logs = client.get("/api/router-logs").json()
    assert [log["log_id"] for log in logs] == [result["router_log_id"]]
    assert logs[0]["status"] == "applied"
    assert logs[0]["user_confirmed"] is True


def test_every_page_refresh_is_a_legitimate_second_write(client: TestClient) -> None:
    """同一句话写两次是合法的（用户可能改主意再改回来），不能被「去重」掉。"""
    first = client.post("/api/preferences", json={"statement": "先看例子"})
    second = client.post("/api/preferences", json={"statement": "先看例子"})
    assert first.status_code == second.status_code == 201
    assert first.json()["results"][0]["intent_id"] != second.json()["results"][0]["intent_id"]
    assert len(client.get("/api/preferences").json()) == 2


def test_preferences_are_listed_by_priority_then_time(client: TestClient) -> None:
    client.post("/api/preferences", json={"statement": "低", "priority": 2})
    client.post("/api/preferences", json={"statement": "高", "priority": 9})
    statements = [row["statement"] for row in client.get("/api/preferences").json()]
    assert statements == ["高", "低"]


def test_a_new_preference_on_the_same_field_archives_the_old_one(
    client: TestClient,
) -> None:
    """偏好会变。「他三个月前说过什么」不该消失，但也不该同时生效。"""
    client.post(
        "/api/preferences",
        json={"statement": "每 25 分钟休息", "target_field": "SP1.activity_mix"},
    )
    client.post(
        "/api/preferences",
        json={"statement": "每 50 分钟休息", "target_field": "SP1.activity_mix"},
    )

    active = client.get("/api/preferences").json()
    assert [row["statement"] for row in active] == ["每 50 分钟休息"]
    assert active[0]["status"] == "active"

    everything = client.get("/api/preferences?include_archived=true").json()
    assert {row["status"] for row in everything} == {"active", "archived"}


def test_a_stated_preference_can_supersede_another_one(client: TestClient) -> None:
    """缺口 A20：用户在界面上说「其实我现在能接受精品网课」。

    两条都没有 `target_field`，所以**槽位规则管不着它们**。在 A20 之前
    这两条会同时 active、同时是硬约束 —— 计划生成时拿到一对互相矛盾的约束，
    而系统完全不知道它们矛盾。
    """
    first = client.post(
        "/api/preferences", json={"statement": "我讨厌网课，因为浪费时间"}
    ).json()
    assert first["results"][0]["status"] == "applied"

    second = client.post(
        "/api/preferences",
        json={
            "statement": "我能接受精品网课，如果讲得好",
            "supersedes_pref_id": "P-0001",
        },
    ).json()
    assert second["results"][0]["status"] == "applied"
    assert "P-0001" in second["results"][0]["reason"]

    active = client.get("/api/preferences").json()
    assert [row["statement"] for row in active] == ["我能接受精品网课，如果讲得好"]
    # 界面据此能显示「你之前那样说过，后来自己改了主意」
    assert active[0]["supersedes_pref_id"] == "P-0001"

    # 归档不是删除：旧的那句话一字未改
    archived = {
        row["statement"]: row
        for row in client.get("/api/preferences?include_archived=true").json()
    }
    assert set(archived) == {"我能接受精品网课，如果讲得好", "我讨厌网课，因为浪费时间"}
    assert archived["我讨厌网课，因为浪费时间"]["status"] == "archived"


def test_superseding_an_unknown_id_is_a_recorded_rejection_not_a_422(
    client: TestClient,
) -> None:
    """形状是合法的（就是一个字符串 id），只是那个 id 不存在 → 策略拒绝。

    它必须落一条 `rejected` 日志并带上理由：若静默忽略，用户会以为
    旧约束已经退休，而两条矛盾会照旧留在基线里。
    """
    response = client.post(
        "/api/preferences",
        json={"statement": "精品网课可以", "supersedes_pref_id": "P-9999"},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["results"][0]["status"] == "rejected"
    assert "P-9999" in payload["results"][0]["reason"]

    assert client.get("/api/preferences").json() == []
    logs = client.get("/api/router-logs").json()
    assert [log["status"] for log in logs] == ["rejected"]


def test_blank_statements_are_422_not_a_recorded_rejection(client: TestClient) -> None:
    """畸形请求不落库；策略拒绝才落库。

    这条分界是刻意的：把策略拒绝也做成 4xx，调用方就只看到一个错误码，
    而「被拒绝了，理由是……」这条信息会失去正式载体 —— 提案卡正是靠它渲染的。
    """
    response = client.post("/api/preferences", json={"statement": "   "})
    assert response.status_code == 422
    assert client.get("/api/router-logs").json() == []


def test_extra_fields_are_rejected_not_ignored(client: TestClient) -> None:
    """多给的字段往往是调用方真正想表达的东西，静默忽略会把它丢掉。"""
    response = client.post(
        "/api/preferences",
        json={"statement": "x", "prioroty": 9},  # 拼错
    )
    assert response.status_code == 422


def test_evidence_requires_a_condition(client: TestClient) -> None:
    """C 与 M2 偏好的分界就是适用条件（M2 §4.4 第 3 条）。"""
    response = client.post(
        "/api/evidence",
        json={
            "statement": "先做例题再看讲解",
            "condition": "   ",
            "effect": "理解更快",
            "evidence_strength": "medium",
        },
    )
    assert response.status_code == 422


def test_evidence_is_written_and_readable(client: TestClient) -> None:
    response = client.post(
        "/api/evidence",
        json={
            "statement": "先做例题再看讲解",
            "condition": "遇到新概念时",
            "effect": "理解更快、遗忘更慢",
            "evidence_strength": "medium",
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "applied"

    rows = client.get("/api/evidence").json()
    assert len(rows) == 1
    assert rows[0]["condition"] == "遇到新概念时"
    assert rows[0]["source"] == "user_direct"


def test_evidence_keyword_search_matches_condition_and_effect(
    client: TestClient,
) -> None:
    client.post(
        "/api/evidence",
        json={
            "statement": "先做例题",
            "condition": "遇到新概念时",
            "effect": "理解更快",
            "evidence_strength": "medium",
        },
    )
    client.post(
        "/api/evidence",
        json={
            "statement": "睡前复习",
            "condition": "睡前 30 分钟",
            "effect": "记忆保持更久",
            "evidence_strength": "strong",
        },
    )
    assert len(client.get("/api/evidence?keyword=记忆").json()) == 1
    assert len(client.get("/api/evidence?keyword=例题").json()) == 1
    assert len(client.get("/api/evidence?keyword=不存在的词").json()) == 0
