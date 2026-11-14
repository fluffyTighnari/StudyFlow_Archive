"""基线测卷：卷面保密、逐段存档、收卷核对。

这三条正好对应它容易出的三类错：

1. 卷面把答案带下去 —— B1/B2 测的就不再是推理与提取；
2. 存档只存"当前段" —— 中断一次就丢掉前几段；
3. 把不能算的主观题算成 0 —— 一个"看起来像分数"的数比空着更坏。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.benchmark import baseline_paper


def _mcq_answers(keyed: dict[int, str]) -> dict[str, str]:
    return {str(no): value for no, value in keyed.items()}


def _full_answers() -> dict[str, dict]:
    return {
        "A": {
            "domains": ["CS", "LANG"],
            "lang_recent": "看过两篇语言学科普",
            "cs_recent": "写后端",
            "goal": "我到底适合先看案例还是先看定义",
        },
        "B1": {
            "answers": _mcq_answers(baseline_paper.B1_ANSWERS),
            "distractions": 0,
            "stop_at": 15,
        },
        "B2": {
            "summary": "主线是对齐方式；三个事实是印地语 ne、巴斯克语 -k、迪尔巴尔语句法作格；限定是形态与句法不必一致。",
            "answers": _mcq_answers(baseline_paper.B2_ANSWERS),
        },
        "B3": {
            "values": {"1": "-k", "2": "ne", "3": "ko", "4": "澳大利亚昆士兰", "5": "未知", "6": "未知", "7": "5"},
            "first_action_ms": 90000,
        },
        "B4": {"choice": "C", "compared": ["A", "C"], "criterion": "先要能讲清结构", "changed": "没有"},
        "B5": {"decision": "skip"},
        "C1": {"comfort": "B", "understanding": "B", "start": "B"},
        "C2": {"unrelated": ["9", "10"], "outline": "先定义声调，再说产生与分布", "first_move": "先分类"},
        "D1": {
            "events": [
                {"scene": "quiet", "minutes": "25", "interruptions": "0", "unknown": False},
                {"scene": "reachable", "minutes": "", "interruptions": "", "unknown": True},
            ]
        },
        "D2": {"what": "B2 读到第三段开始跳读", "expected": "以为能一次读完", "why": "材料密度超出预期", "next": "先标限定句再读"},
    }


def test_paper_hides_answers_from_the_client(client: TestClient) -> None:
    body = client.get("/api/benchmark/baseline/paper").json()
    assert body["paper_id"] == baseline_paper.PAPER_ID
    assert [section["id"] for section in body["sections"]] == [
        "A", "B1", "B2", "B3", "B4", "B5", "C1", "C2", "D1", "D2"
    ]
    # B1 / B2 的每个题目只有题号、题干、选项。
    b1 = next(section for section in body["sections"] if section["id"] == "B1")
    assert set(b1["items"][0]) == {"no", "stem", "options"}
    # B3 的答案不在卷面上（它在 `B3_EXPECTED`，只在服务端）。
    b3 = next(section for section in body["sections"] if section["id"] == "B3")
    assert "answers" not in b3
    assert "expected" not in str(b3)
    # 整份响应里没有答案键。
    assert '"answer"' not in str(body)


def test_run_is_created_and_can_be_resumed(client: TestClient) -> None:
    start = client.post("/api/benchmark/baseline/runs", json={})
    assert start.status_code == 201, start.text
    run = start.json()
    assert run["run_id"].startswith("BP-")
    assert run["status"] == "in_progress"
    assert run["answers"] == {}

    saved = client.post(
        f"/api/benchmark/baseline/runs/{run['run_id']}/answers",
        json={
            "answers": {"A": {"goal": "想知道自己的节奏"}, "B1": {"answers": {"1": "B"}}},
            "current_section": "B2",
            "section_seconds": {"A": 70, "B1": 600},
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["current_section"] == "B2"
    assert saved.json()["section_seconds"]["B1"] == 600

    latest = client.get("/api/benchmark/baseline/runs/latest").json()["run"]
    assert latest["run_id"] == run["run_id"]
    assert latest["answers"]["A"]["goal"] == "想知道自己的节奏"

    listing = client.get("/api/benchmark/baseline/runs").json()
    assert [row["run_id"] for row in listing["items"]] == [run["run_id"]]


def test_finish_scores_only_what_can_be_scored(client: TestClient) -> None:
    run_id = client.post("/api/benchmark/baseline/runs", json={}).json()["run_id"]
    answers = _full_answers()
    response = client.post(
        f"/api/benchmark/baseline/runs/{run_id}/finish",
        json={"answers": answers, "current_section": "D2", "section_seconds": {"B1": 600}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "finished"
    assert body["results"]["objective"][0]["section"] == "B1"
    assert body["results"]["objective"][0]["correct"] == 15
    assert body["results"]["objective"][1]["correct"] == 5
    # B3 只核对材料内 5 项，第 5、6 格不判对错。
    b3 = body["results"]["objective"][2]
    assert b3["correct"] == 5
    assert b3["total"] == 5
    assert "b3_no_external_action" not in body["results"]["flags"]
    # 主观部分只留原文，不产出分数。
    assert "score" not in body["results"]

    again = client.post(
        f"/api/benchmark/baseline/runs/{run_id}/finish",
        json={"answers": answers, "current_section": "D2", "section_seconds": {}},
    )
    assert again.status_code == 422
    frozen = client.post(
        f"/api/benchmark/baseline/runs/{run_id}/answers",
        json={"answers": {}, "current_section": "A", "section_seconds": {}},
    )
    assert frozen.status_code == 422


def test_b3_without_external_action_is_missing_not_zero(client: TestClient) -> None:
    run_id = client.post("/api/benchmark/baseline/runs", json={}).json()["run_id"]
    answers = _full_answers()
    answers["B3"].pop("first_action_ms")
    body = client.post(
        f"/api/benchmark/baseline/runs/{run_id}/finish",
        json={"answers": answers, "current_section": "D2", "section_seconds": {}},
    ).json()
    assert "b3_no_external_action" in body["results"]["flags"]
    observation = next(
        row for row in body["results"]["observations"] if "第一次外部动作" in row["label"]
    )
    assert observation["value"] == "未采到"
    # 缺动作不等于最低分 —— B3 的客观题仍然按材料内 5 项算。
    assert body["results"]["objective"][2]["correct"] == 5


def test_partial_paper_is_flagged(client: TestClient) -> None:
    run_id = client.post("/api/benchmark/baseline/runs", json={}).json()["run_id"]
    body = client.post(
        f"/api/benchmark/baseline/runs/{run_id}/finish",
        json={"answers": {"A": {"goal": "x"}}, "current_section": "B1", "section_seconds": {}},
    ).json()
    assert "partial" in body["results"]["flags"]
    assert any("未作答的段落" in note for note in body["results"]["notes"])
