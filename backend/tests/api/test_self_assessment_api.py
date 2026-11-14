"""自评单例：计分规则与 HTTP 契约。

这一层的三个关键约束：

1. **卷面不泄露计分键** —— 反向句一旦被作答者看见，当场失效；
2. **计分规则只有一份** —— 四类变量算法不同，取均值会把四维图压成一个点；
3. **两档共用题号** —— `short` 是 `full` 的子集，所以两档可以共用一份计分器。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.benchmark import self_assessment as scale
from app.global_domain.enums import EvidenceChannel


def _all(option: str, tier: str = "full") -> dict[int, str]:
    return {statement.no: option for statement in scale.statements_for(tier)}


def _extreme(high: bool, tier: str = "full") -> dict[int, str]:
    """正向句取 D / 反向句取 A = 该变量满分；反过来 = 零分。

    这比"全部选 D"更能验证反向句真的在反向：全选同一档只是作答惯性。
    """
    answers: dict[int, str] = {}
    for statement in scale.statements_for(tier):
        answers[statement.no] = ("D" if not statement.reverse else "A") if high else (
            "A" if not statement.reverse else "D"
        )
    return answers


def _answers(**overrides: str) -> dict[int, str]:
    answers = _all("B")
    for key, value in overrides.items():
        answers[int(key.lstrip("n"))] = value
    return answers


def _set_facet(answers: dict[int, str], variable_id: str, facet: str, option: str) -> None:
    for statement in scale.STATEMENTS:
        if statement.variable_id == variable_id and statement.facet == facet:
            answers[statement.no] = option


def test_scale_hides_reverse_and_weights_from_the_client(client: TestClient) -> None:
    body = client.get("/api/self-assessment/scale").json()
    assert body["tier"] == "full"
    assert body["scale_version"] == "v2"
    assert body["total"] == 127
    assert body["full_total"] == 127
    assert len(body["statements"]) == 127
    # 卷面只带 `no` 与 `text` —— 没有 reverse、没有 weight。
    assert set(body["statements"][0]) == {"no", "text"}
    assert set(body["options"][0]) == {"value", "label"}
    assert "reverse" not in str(body)
    assert "weight" not in str(body)


def test_short_tier_is_a_subset_with_the_same_numbers(client: TestClient) -> None:
    full = client.get("/api/self-assessment/scale?tier=full").json()
    short = client.get("/api/self-assessment/scale?tier=short").json()
    assert short["total"] == 90
    assert short["full_total"] == 127
    full_nos = {item["no"] for item in full["statements"]}
    short_nos = {item["no"] for item in short["statements"]}
    assert short_nos < full_nos

    bad = client.get("/api/self-assessment/scale?tier=tiny")
    assert bad.status_code == 422


def test_extreme_answers_land_on_the_ends() -> None:
    lowest = scale.score(_extreme(False))
    highest = scale.score(_extreme(True))
    low = {row.variable_id: row.score for row in lowest.scores}
    high = {row.variable_id: row.score for row in highest.scores}
    # 正向句 A、反向句 D = 每一项都取到 0；反过来 = 每一项都取到 10。
    assert all(value == 0.0 for value in low.values()), low
    assert all(value == 10.0 for value in high.values()), high
    # 极值键不是直线作答，也不该被判成作答偏移。
    assert "straight_lining" not in lowest.flags
    assert "straight_lining" not in highest.flags
    assert not any(flag.startswith("acquiescence") for flag in highest.flags)


def test_missing_statement_is_refused() -> None:
    answers = _all("B")
    answers.pop(7)
    try:
        scale.score(answers)
    except scale.SelfAssessmentError as exc:
        assert "7" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("缺题时必须拒绝")


def test_short_tier_refuses_full_answers_and_vice_versa() -> None:
    try:
        scale.score(_all("B", "full"), tier="short")
    except scale.SelfAssessmentError as exc:
        assert "不在这一档" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("全面版作答不能当简短版提交")

    try:
        scale.score(_all("B", "short"), tier="full")
    except scale.SelfAssessmentError as exc:
        assert "没答" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("简短版作答不能当全面版提交")


def test_preference_variables_keep_both_ends() -> None:
    # 两端各自的条目都取到满分（反向句按它的方向作答）。
    answers = _all("B")
    for statement in scale.STATEMENTS:
        if statement.variable_id == "V-CP":
            answers[statement.no] = "A" if statement.reverse else "D"
    result = scale.score(answers)
    cp = next(row for row in result.scores if row.variable_id == "V-CP")
    assert set(cp.facets) == {"案例端", "抽象端"}
    assert cp.facets["案例端"] == 10.0
    assert cp.facets["抽象端"] == 10.0
    # 两端都 ≥8 → 记「两端都舒适」，不挑一端。
    assert any("两端都" in note for note in result.notes)

    # 只偏好案例端：抽象端必须掉到 0（v1 的反向键错误会让它错误地停在 10）。
    case_only = _all("B")
    for statement in scale.STATEMENTS:
        if statement.variable_id != "V-CP":
            continue
        if statement.facet == "案例端":
            case_only[statement.no] = "A" if statement.reverse else "D"
        else:
            case_only[statement.no] = "D" if statement.reverse else "A"
    cp_case = next(
        row for row in scale.score(case_only).scores if row.variable_id == "V-CP"
    )
    assert cp_case.facets["案例端"] == 10.0
    assert cp_case.facets["抽象端"] == 0.0
    assert cp_case.score == 10.0


def test_four_dimension_variable_takes_top_two() -> None:
    # V-SMP 四维：图 / 听 / 读写 / 动觉，各 4 条。
    answers = _all("B")
    _set_facet(answers, "V-SMP", "图", "D")
    _set_facet(answers, "V-SMP", "听", "B")
    _set_facet(answers, "V-SMP", "读写", "A")
    _set_facet(answers, "V-SMP", "动觉", "B")
    result = scale.score(answers)
    smp = next(row for row in result.scores if row.variable_id == "V-SMP")
    assert set(smp.facets) == {"图", "听", "读写", "动觉"}
    assert smp.facets["图"] == 10.0
    # 取最高两维（10 与 3.33…）的均值。
    assert 6.6 <= smp.score <= 6.7


def test_submit_writes_stated_baseline_and_records_the_run(client: TestClient) -> None:
    answers = _answers()
    response = client.post(
        "/api/self-assessment/submit",
        json={
            "answers": {str(no): value for no, value in answers.items()},
            "tier": "full",
            "attention_span": "C",
            "activity_rank": [1, 2, 3, 4],
            "elapsed_minutes": 20,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["run_id"].startswith("SA-")
    assert body["written"] == 11
    assert body["rejected"] == []

    # 11 个变量各得到一条 `stated` 基线；`measured` 通道**一条都没有**被写。
    for reading in body["readings"]:
        history = client.get(
            f"/api/m1/{reading['variable_id']}/history?channel=stated"
        ).json()["items"]
        assert history, reading["variable_id"]
        assert history[-1]["score"] == reading["score"]

    measured = client.get("/api/m1/V-SE/history?channel=measured").json()["items"]
    assert measured == [], "自评写进了 measured 通道 —— 那是另一把尺子"

    latest = client.get("/api/self-assessment/latest").json()
    assert latest["run_id"] == body["run_id"]
    assert latest["tier"] == "full"
    assert latest["scale_version"] == "v2"
    assert latest["flags"]  # 全部选 B 不是直线作答，但反向句会触发一致性标记

    extra = client.get("/api/self-assessment/extra").json()
    assert extra["attention_span"] == "C"
    assert extra["label"] == "25–45 分钟"


def test_short_submit_is_always_hypothesis(client: TestClient) -> None:
    answers = _all("B", "short")
    response = client.post(
        "/api/self-assessment/submit",
        json={
            "answers": {str(no): value for no, value in answers.items()},
            "tier": "short",
            "elapsed_minutes": 15,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["written"] == 11
    for reading in body["readings"]:
        history = client.get(
            f"/api/m1/{reading['variable_id']}/history?channel=stated"
        ).json()["items"]
        assert history[-1]["confidence"] == "hypothesis", reading["variable_id"]
    latest = client.get("/api/self-assessment/latest").json()
    assert latest["tier"] == "short"


def test_submit_refuses_an_incomplete_scale(client: TestClient) -> None:
    response = client.post(
        "/api/self-assessment/submit",
        json={"answers": {"1": "B", "2": "B"}},
    )
    assert response.status_code == 422
    assert "没答" in response.json()["detail"]
    # 拒绝时**不许留下运行记录** —— 否则"做过几次"会变成"点过几次按钮"。
    assert client.get("/api/self-assessment/history").json()["items"] == []


def test_fast_answers_are_flagged_and_lower_confidence(client: TestClient) -> None:
    answers = _answers()
    response = client.post(
        "/api/self-assessment/submit",
        json={
            "answers": {str(no): value for no, value in answers.items()},
            "elapsed_minutes": 2,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert "too_fast" in body["flags"]
    history = client.get("/api/m1/V-SE/history?channel=stated").json()["items"]
    assert history[-1]["confidence"] == "hypothesis"


def test_stated_channel_is_the_only_one_written(client: TestClient) -> None:
    """确认它走的是 `stated`，不是 `measured`。"""
    answers = _all("C")
    client.post(
        "/api/self-assessment/submit",
        json={"answers": {str(no): value for no, value in answers.items()}},
    )
    for channel in (EvidenceChannel.MEASURED, EvidenceChannel.EXPERIENCED):
        assert client.get(f"/api/m1/V-SE/history?channel={channel.value}").json()[
            "items"
        ] == []
