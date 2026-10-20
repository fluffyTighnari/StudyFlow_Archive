"""`/api/m1` —— 逐通道覆盖、历史、观测明细、重做建议、回滚。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.global_domain.enums import EvidenceChannel
from tests.helpers import items

VARIABLE_KEYS = {
    "variable_id",
    "name",
    "display_name",
    "construct_type",
    "definition_what",
    "definition_what_not",
    "scale_low_anchor",
    "scale_high_anchor",
    # ★ P10：0-4 / 4-8 / 8-10 的分段说明。
    # 用户拿到的是一个 **6.2** —— 锚点只回答"0 分和 10 分长什么样"，
    # 中间那一段必须有人写出来，否则每个人脑补的结果都不一样。
    "scale_bands",
    "measurement_def",
    "intervention_hooks",
    "pairing",
    "sort_order",
    "channels",
}


def test_variables_returns_all_eleven_with_three_channels_each(client: TestClient) -> None:
    response = client.get("/api/m1/variables")
    assert response.status_code == 200
    body = items(response.json())
    assert len(body) == 11
    assert {row["variable_id"] for row in body} == {
        "V-SE",
        "V-HSR",
        "V-DC",
        "V-IR",
        "V-PAR",
        "V-ENV",
        "V-ATT",
        "V-SMP",
        "V-ACT",
        "V-OSP",
        "V-CP",
    }
    for row in body:
        assert set(row.keys()) == VARIABLE_KEYS
        assert len(row["channels"]) == 3
        assert {ch["channel"] for ch in row["channels"]} == {
            EvidenceChannel.MEASURED,
            EvidenceChannel.EXPERIENCED,
            EvidenceChannel.STATED,
        }


def test_variables_carry_three_segment_explanations(client: TestClient) -> None:
    """★ 每个变量都要有 0-4 / 4-8 / 8-10 三段说明，且**首尾相接覆盖 0-10**。

    这里不满足于"有 scale_bands 字段"：字段存在但只有两段、
    或者中间有个洞，界面上看起来都只是"这一段没写"，
    而实际上某些分数已经没有说明了。
    """
    from app.benchmark.scale import as_bands, band_for

    body = items(client.get("/api/m1/variables").json())
    for row in body:
        bands = as_bands(row["scale_bands"])
        assert len(bands) == 3, f"{row['variable_id']} 有 {len(bands)} 段说明"
        for score in (0.0, 4.0, 8.0, 10.0):
            assert band_for(bands, score) is not None, f"{row['variable_id']} 的 {score} 分没有说明"
        assert bands[0].description == row["scale_low_anchor"]
        assert bands[-1].description == row["scale_high_anchor"]
        assert bands[1].draft is True, "中段是新写的初稿，必须标出来"


def test_no_response_exposes_a_variable_level_score(client: TestClient) -> None:
    """通道纯度（M0 §6.5）必须体现在**响应的形状**上，而不只是文档里。

    如果哪天有人在变量层加一个 `score`（哪怕叫 `overall`），前端就会立刻开始
    画雷达图，而「V-SE = 3.5」这个数在系统里根本没有定义。这条测试是那道闸门。
    """
    for row in items(client.get("/api/m1/variables").json()):
        assert not {"score", "overall", "total", "composite"} & set(row.keys())
        for channel in row["channels"]:
            # 分数只能挂在通道上，且只能通过该通道的当前版本出现。
            assert "score" not in channel
            assert channel["current"] is None or "score" in channel["current"]
            assert set(channel.keys()) >= {"channel", "designed", "completeness", "current"}


def test_channels_without_design_are_reported_not_applicable(
    client: TestClient,
) -> None:
    """`designed=false` 与「该测但还没测」是两件事，UI 需要区分它们。

    V-ENV 与 V-ACT 的 measured 通道按设计就没有方法覆盖（M1 §2 的 P2 列为「—」）；
    它们必须显示为 `not_applicable`，而不是一个看起来「还没测」的 `partial`。
    """
    body = items(client.get("/api/m1/variables").json())
    undesigned = {
        row["variable_id"]: [c for c in row["channels"] if not c["designed"]]
        for row in body
    }
    undesigned = {k: v for k, v in undesigned.items() if v}
    assert set(undesigned) == {"V-ENV", "V-ACT"}

    for channels in undesigned.values():
        for channel in channels:
            assert channel["channel"] == "measured"
            assert channel["completeness"] == "not_applicable"
            assert channel["current"] is None
            assert channel["method_ids"] == []
            assert channel["design_note"]  # 不设就要说明为什么不设

    # 反之：设计上该测但还没测的通道是 partial，不是 not_applicable。
    se = next(row for row in body if row["variable_id"] == "V-SE")
    assert {c["completeness"] for c in se["channels"]} == {"partial"}


def test_unregistered_variable_is_404_not_an_empty_skeleton(client: TestClient) -> None:
    """拼错的 `V_SE` 必须报错。

    返回空骨架会让它看起来像一个「还没测过的变量」，而系统里根本没有它 ——
    一个要等到几天后才被发现的静默错误。
    """
    response = client.get("/api/m1/V-SE/history".replace("V-SE", "V_SE"))
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_history_is_empty_then_ascending(client: TestClient, direct) -> None:
    assert items(client.get("/api/m1/V-SE/history").json()) == []

    direct.baseline(score=3.0)
    direct.baseline(score=4.0)

    rows = items(client.get("/api/m1/V-SE/history").json())
    assert [row["score"] for row in rows] == [3.0, 4.0]
    assert [row["version_id"] for row in rows] == ["M1-SE-001", "M1-SE-002"]


def test_history_can_be_narrowed_to_one_channel(client: TestClient, direct) -> None:
    direct.baseline(channel="measured", score=3.0)
    direct.baseline(channel="stated", score=5.0, basis="stated",
                    trigger="R1", source_ref=None)

    measured = items(client.get("/api/m1/V-SE/history?channel=measured").json())
    assert [row["score"] for row in measured] == [3.0]


def test_history_keeps_superseded_versions(client: TestClient, direct) -> None:
    """历史行**永不修改、永不删除** —— 两次写入后历史有 2 行，当前只有 1 行。"""
    direct.baseline(score=3.0)
    direct.baseline(score=4.0)

    assert len(items(client.get("/api/m1/V-SE/history").json())) == 2
    detail = next(
        row for row in items(client.get("/api/m1/variables").json()) if row["variable_id"] == "V-SE"
    )
    measured = next(c for c in detail["channels"] if c["channel"] == "measured")
    assert measured["current"]["score"] == 4.0
    assert measured["completeness"] == "complete"


def test_observations_expose_the_dedup_key(client: TestClient, direct) -> None:
    """§7.3 的 ≥2 条独立证据门槛必须可核对：`source_ref` 就是去重口径。"""
    assert items(client.get("/api/m1/V-SE/observations").json()) == []
    assert client.get("/api/m1/V-NOPE/observations").status_code == 404


def test_benchmark_advisory_has_no_blocking_field(client: TestClient) -> None:
    """建议**结构上**无法阻断：连响应里都没有「是否阻断」这种字段。"""
    body = client.get("/api/m1/V-SE/benchmark-advisory").json()
    assert set(body.keys()) == {"variable_id", "level", "run_count", "message"}
    assert body["level"] == "none"
    assert body["run_count"] == 0


def test_benchmark_advisory_counts_distinct_runs_in_the_quarter(
    client: TestClient, direct
) -> None:
    """一次 Benchmark 更新多个通道仍只算**一次**重做（按 source_ref 去重）。"""
    direct.baseline(channel="measured", score=3.0, source_ref="RUN-A")
    direct.baseline(channel="stated", score=4.0, basis="stated",
                    trigger="R1", source_ref=None)
    direct.baseline(channel="measured", score=3.5, source_ref="RUN-B")

    body = client.get("/api/m1/V-SE/benchmark-advisory").json()
    # RUN-A 与 RUN-B 两次；R1 那次 source_ref 为空，不计入基准重做。
    assert body["run_count"] == 2
    assert body["level"] == "warn"
    assert "不阻断" in body["message"] or "重做" in body["message"]


# --------------------------------------------------------------------------- #
# 回滚（R5）
# --------------------------------------------------------------------------- #


def test_rollback_requires_a_reason(client: TestClient, direct) -> None:
    direct.baseline(score=3.0)
    response = client.post(
        "/api/m1/V-SE/rollback",
        json={"channel": "measured", "to_version_id": "M1-SE-001", "reason": "   "},
    )
    assert response.status_code == 422


def test_rollback_to_unknown_version_is_404(client: TestClient) -> None:
    response = client.post(
        "/api/m1/V-SE/rollback",
        json={"channel": "measured", "to_version_id": "M1-SE-999", "reason": "手滑"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_rollback_across_channels_is_rejected(client: TestClient, direct) -> None:
    """版本属于哪个通道是它的一部分；跨通道回滚必须报错，不能静默成功。"""
    direct.baseline(channel="measured", score=3.0)
    response = client.post(
        "/api/m1/V-SE/rollback",
        json={"channel": "stated", "to_version_id": "M1-SE-001", "reason": "试试"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "intent_rejected"


def test_rollback_appends_a_version_and_moves_the_pointer(
    client: TestClient, direct
) -> None:
    direct.baseline(score=3.0)
    direct.baseline(score=1.0)

    response = client.post(
        "/api/m1/V-SE/rollback",
        json={"channel": "measured", "to_version_id": "M1-SE-001", "reason": "第二次数错了"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["new_version_id"] == "M1-SE-003"
    assert body["replaced_version_id"] == "M1-SE-002"
    assert body["to_version_id"] == "M1-SE-001"

    # 历史变成 3 行（没有被删），当前分数回到 3.0。
    history = items(client.get("/api/m1/V-SE/history").json())
    assert [row["score"] for row in history] == [3.0, 1.0, 3.0]
    detail = next(
        row for row in items(client.get("/api/m1/variables").json()) if row["variable_id"] == "V-SE"
    )
    measured = next(c for c in detail["channels"] if c["channel"] == "measured")
    assert measured["current"]["score"] == 3.0
    assert measured["current"]["version_id"] == "M1-SE-003"


def test_rollback_is_audited_as_r5_with_the_reason(client: TestClient, direct) -> None:
    """回滚不是「悄悄改个指针」：它必须留下带原因的 R5 审计记录。"""
    direct.baseline(score=3.0)
    direct.baseline(score=1.0)
    client.post(
        "/api/m1/V-SE/rollback",
        json={"channel": "measured", "to_version_id": "M1-SE-001", "reason": "第二次数错了"},
    )

    logs = items(client.get("/api/router-logs?target=M1.V-SE").json())
    rollback_log = next(log for log in logs if log["trigger"] == "R5")
    assert rollback_log["source"] == "user_ui"
    assert rollback_log["status"] == "applied"
    assert rollback_log["user_confirmed"] is True
    assert rollback_log["action"] is None
    assert "第二次数错了" in rollback_log["reasoning"]
    assert rollback_log["old_value"]["score"] == 1.0
    assert rollback_log["new_value"]["score"] == 3.0
