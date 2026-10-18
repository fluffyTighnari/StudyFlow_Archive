"""`/api/router-logs` 与跨端点的审计一致性。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_logs_are_empty_before_anything_is_written(client: TestClient) -> None:
    assert client.get("/api/router-logs").json() == []


def test_logs_are_newest_first(client: TestClient) -> None:
    client.post("/api/preferences", json={"statement": "甲"})
    client.post("/api/preferences", json={"statement": "乙"})
    client.post("/api/preferences", json={"statement": "丙"})

    ids = [log["log_id"] for log in client.get("/api/router-logs").json()]
    assert ids == ["RL-0003", "RL-0002", "RL-0001"]


def test_logs_can_be_filtered_by_canonical_target(client: TestClient, direct) -> None:
    """`target` 用 `M1.V-SE`（规范名），不是 `V-SE`。

    过滤参数与 `RouterLog.target` 同源 —— 在这里再解析一次 `V-SE`
    就会变成 A9 命名空间映射的第二处实现，两处迟早不一致。
    """
    direct.baseline(variable_id="V-SE", score=3.0)
    direct.baseline(variable_id="V-DC", score=2.0)

    assert len(client.get("/api/router-logs").json()) == 2
    only_se = client.get("/api/router-logs?target=M1.V-SE").json()
    assert [log["target"] for log in only_se] == ["M1.V-SE"]
    # 旧写法查不到东西 —— 这是刻意的，不是遗漏。
    assert client.get("/api/router-logs?target=V-SE").json() == []


def test_log_limit_is_bounded(client: TestClient) -> None:
    assert client.get("/api/router-logs?limit=0").status_code == 422
    assert client.get("/api/router-logs?limit=9999").status_code == 422


def test_every_global_write_has_exactly_one_log(client: TestClient, direct) -> None:
    """审计完整性：写了几次全局，就有几条日志 —— 不多不少。

    这是 P5 不变式 I1 在 HTTP 层的对应断言（I1 本身在 P5 由
    `app.core.invariants` 做全库自检）。
    """
    client.post("/api/preferences", json={"statement": "甲"})
    client.post("/api/evidence", json={
        "statement": "s", "condition": "c", "effect": "e",
        "evidence_strength": "weak",
    })
    direct.baseline(score=3.0)

    logs = client.get("/api/router-logs").json()
    assert len(logs) == 3
    assert {log["trigger"] for log in logs} == {"R1", "R2"}
    assert all(log["status"] == "applied" for log in logs)
