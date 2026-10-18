"""种子数据测试 —— 把 M1/M2 的文档红线变成可执行断言。"""

from __future__ import annotations

import pytest

from app.global_domain.enums import (
    Basis,
    Completeness,
    ConstructType,
    EvidenceChannel,
    MethodKind,
    MethodPriority,
)
from app.global_domain.models import M1Variable, M1VariableChannel, M2Strategy
from app.global_domain.reads import (
    list_strategies,
    list_variable_channels,
    list_variables,
    measurement_coverage,
)
from app.global_domain.writes import insert_baseline_version
from app.seeds import load_all
from app.seeds.m1_variables import PREFERENCE_VARIABLE_IDS

EXPECTED_VARIABLE_IDS = {
    "V-SE",
    "V-HSR",
    "V-DC",
    "V-IR",
    "V-PAR",
    "V-ENV",
    "V-ATT",
    "V-CP",
    "V-SMP",
    "V-OSP",
    "V-ACT",
}

EXPECTED_STRATEGY_IDS = {
    "retrieval_practice",
    "example_guided",
    "stepwise_practice",
    "project_practice",
    "comparative_analysis",
    "reflection_review",
    "spaced_repetition",
    "interleaving",
    "self_explanation",
    "elaboration",
    "dual_coding",
    "global_modeling",
    "sequential_walkthrough",
    "case_based",
    "hands_on_trial",
    "peer_discussion",
}


@pytest.fixture()
def seeded(session):
    created = load_all(session)
    return session, created


def test_loads_exactly_eleven_variables(seeded) -> None:
    session, created = seeded
    assert created["m1_variable"] == 11
    assert {v.variable_id for v in list_variables(session)} == EXPECTED_VARIABLE_IDS


def test_loads_exactly_sixteen_strategies(seeded) -> None:
    session, created = seeded
    assert created["m2_strategy"] == 16
    assert {s.strategy_id for s in list_strategies(session, limit=None)} == (
        EXPECTED_STRATEGY_IDS
    )


def test_loading_is_idempotent(session) -> None:
    first = load_all(session)
    second = load_all(session)
    assert first["m1_variable"] == 11
    assert second == {"m1_variable": 0, "m1_variable_channel": 0, "m2_strategy": 0}
    assert session.query(M1Variable).count() == 11
    assert session.query(M1VariableChannel).count() == 33
    assert session.query(M2Strategy).count() == 16


def test_every_variable_links_at_least_one_p1_method(seeded) -> None:
    """M1 §5.1 红线：测量目标必须链接至少 1 个 P1 方法，否则变量不落库。"""
    session, _ = seeded
    offenders: list[str] = []
    for variable in list_variables(session):
        methods = variable.measurement_def.get("methods", [])
        p1 = [m for m in methods if m["priority"] == MethodPriority.P1.value]
        if not p1:
            offenders.append(variable.variable_id)
    assert not offenders, f"以下变量的测量目标没有链接任何 P1 方法：{offenders}"


def test_method_kinds_are_from_the_documented_five(seeded) -> None:
    """M1 §1：方法类型只有五类。"""
    session, _ = seeded
    allowed = {k.value for k in MethodKind}
    for variable in list_variables(session):
        for method in variable.measurement_def.get("methods", []):
            assert method["kind"] in allowed, f"{variable.variable_id}/{method['method_id']}"


def test_method_priorities_are_p1_p2_p3(seeded) -> None:
    session, _ = seeded
    allowed = {p.value for p in MethodPriority}
    for variable in list_variables(session):
        for method in variable.measurement_def.get("methods", []):
            assert method["priority"] in allowed


def test_m1_v_signal_extraction_has_six_documented_methods(seeded) -> None:
    """M1 §2 的完整例子：V-SE 应有 6 个方法（01–06）。"""
    session, _ = seeded
    variable = session.get(M1Variable, "V-SE")
    assert variable is not None
    ids = [m["method_id"] for m in variable.measurement_def["methods"]]
    assert ids == [f"M-SE-0{i}" for i in range(1, 7)]


def test_trait_and_preference_split_matches_m1(seeded) -> None:
    """M1 §2 分类：7 个 trait、2 个 preference、2 个 strategy_option。"""
    session, _ = seeded
    by_type: dict[str, list[str]] = {}
    for variable in list_variables(session):
        by_type.setdefault(variable.construct_type.value, []).append(variable.variable_id)

    assert set(by_type["trait"]) == {"V-SE", "V-HSR", "V-DC", "V-IR", "V-PAR", "V-ENV", "V-ATT"}
    assert set(by_type["preference"]) == {"V-CP", "V-SMP"}
    assert set(by_type["strategy_option"]) == {"V-OSP", "V-ACT"}


def test_strategy_option_variables_are_not_preference_type(seeded) -> None:
    """M1 §5.3 红线：strategy_option 类不作为稳定基线，必须与 preference 区分。"""
    session, _ = seeded
    for vid in ("V-OSP", "V-ACT"):
        assert session.get(M1Variable, vid).construct_type == ConstructType.STRATEGY_OPTION
    assert PREFERENCE_VARIABLE_IDS == {"V-CP", "V-SMP", "V-OSP", "V-ACT"}


def test_evidence_channels_are_declared_per_variable(seeded) -> None:
    """M1 §1 通用结构要求 evidence_channels 三通道齐备。"""
    session, _ = seeded
    for variable in list_variables(session):
        channels = variable.measurement_def.get("evidence_channels", {})
        assert {"measured", "experienced", "stated"} <= set(channels), variable.variable_id


def test_high_evidence_strategies_rank_first(seeded) -> None:
    """M2 §3.3 第 2 条：evidence_strength=high 默认优先（SP3）。"""
    session, _ = seeded
    ordered = list_strategies(session, limit=None)

    weights = [s.default_weight for s in ordered]
    assert weights == sorted(weights, reverse=True), "策略应按默认权重降序返回"

    high_count = sum(1 for s in ordered if s.evidence_strength.value == "high")
    assert high_count == 5
    assert all(
        s.evidence_strength.value == "high" for s in ordered[:high_count]
    ), "所有 high 策略必须排在非 high 策略之前"


def test_strategy_weights_are_derived_from_evidence_strength(seeded) -> None:
    session, _ = seeded
    expected = {"high": 8, "medium": 5, "low-medium": 4, "low": 2}
    for strategy in list_strategies(session, limit=None):
        assert strategy.default_weight == expected[strategy.evidence_strength.value]


def test_seed_definitions_have_no_duplicate_ids() -> None:
    from app.seeds.m1_variables import VARIABLES
    from app.seeds.m2_strategies import STRATEGIES

    var_ids = [v["variable_id"] for v in VARIABLES]
    assert len(var_ids) == len(set(var_ids))
    strategy_ids = [s["strategy_id"] for s in STRATEGIES]
    assert len(strategy_ids) == len(set(strategy_ids))


# --------------------------------------------------------------------------- #
# 逐通道测量覆盖（M0 §6.6 / 缺口 A10）
# --------------------------------------------------------------------------- #


def test_loads_thirty_three_channel_rows(seeded) -> None:
    """11 变量 × 3 通道。"""
    session, created = seeded
    assert created["m1_variable_channel"] == 33
    rows = list_variable_channels(session)
    assert len(rows) == 33
    assert {r.variable_id for r in rows} == EXPECTED_VARIABLE_IDS


def test_channel_design_is_derived_from_methods(seeded) -> None:
    """`designed` / `method_ids` 必须与 `methods[].basis` **完全一致**。

    刻意从 `measurement_def` 派生而非手抄 33 行——手抄与 `methods[].basis`
    之间一定会漂移，而漂移的表现形式是「UI 说该通道可测，实际没有任何方法支撑」，
    一个不会报错的错误。
    """
    session, _ = seeded
    for variable in list_variables(session):
        methods = variable.measurement_def["methods"]
        for row in list_variable_channels(session, variable.variable_id):
            label = f"{row.variable_id}/{row.channel}"
            expected = sorted(
                m["method_id"] for m in methods if m["basis"] == row.channel.value
            )
            assert sorted(row.method_ids) == expected, label
            assert row.designed == bool(expected), label


def test_every_variable_has_a_stated_channel_p1_method(seeded) -> None:
    """M1 §2 的 P1 列**每一个变量都以自评开头** —— 所以每个变量都必须有
    至少一个 `stated` 通道的 P1 方法。

    这条断言来自一次真实踩坑：我把 V-OSP 的「自评 + 过往绩效反推」合并成了一个
    retrospective 方法，于是 `stated` 通道静默失去了全部方法覆盖。逐通道表把这个
    缺口暴露了出来，但只有这条断言能**直接**指出「少了一个自评」。
    """
    session, _ = seeded
    offenders: list[str] = []
    for variable in list_variables(session):
        methods = variable.measurement_def["methods"]
        stated_p1 = [
            m
            for m in methods
            if m["basis"] == "stated" and m["priority"] == MethodPriority.P1.value
        ]
        if not stated_p1:
            offenders.append(variable.variable_id)
    assert not offenders, f"以下变量缺少 stated 通道的 P1（自评）方法：{offenders}"


def test_exactly_two_channels_are_undesigned(seeded) -> None:
    """当前设计下「按设计不测」的通道只有两个（M1 §2 的 P2 列为「—」）。

    这个断言**故意写死**：新增或移除一个不设方法的通道，必须是一次显式决定，
    而不是某次种子编辑的副作用。
    """
    session, _ = seeded
    undesigned = sorted(
        (r.variable_id, r.channel.value)
        for r in list_variable_channels(session)
        if not r.designed
    )
    assert undesigned == [("V-ACT", "measured"), ("V-ENV", "measured")]


def test_design_note_never_contradicts_the_derived_flag(seeded) -> None:
    """注记声称「无方法覆盖」的通道，`designed` 必须是 False —— 反之亦然。

    这条测试来自一次真实的自我踩坑：种子注记里我手写了一版
    「暂不设（偏好不做行为任务判定）」，而同一个变量的方法表里明明有一个
    `behavioral_task`（M1 §2 明确把「成对双版本选择 `(任务)`」列为 V-CP 的 P2 方法）。
    注记与方法互相矛盾，**而且不会报任何错**。语义矛盾必须能被机器抓到。
    """
    session, _ = seeded
    for row in list_variable_channels(session):
        note = row.design_note or ""
        claims_absent = "无方法覆盖" in note
        assert claims_absent == (not row.designed), (
            f"{row.variable_id}/{row.channel} 的注记与方法表冲突：{note!r}"
        )


def test_every_channel_has_a_design_note(seeded) -> None:
    """没有注记的通道行等于没有信息的行 —— 不允许。"""
    session, _ = seeded
    for row in list_variable_channels(session):
        assert (row.design_note or "").strip(), f"{row.variable_id}/{row.channel}"


def test_undesigned_channel_reports_not_applicable(seeded) -> None:
    """「按设计不测」与「该测还没测完」必须区分开。

    混为一谈的后果：UI 永远显示「未完成」，从而逼用户去做一个设计上就不该做的测量。
    """
    session, _ = seeded
    coverage = {c.channel.value: c for c in measurement_coverage(session, "V-ACT")}
    assert coverage["measured"].designed is False
    assert coverage["measured"].completeness == Completeness.NOT_APPLICABLE
    assert coverage["measured"].current_version_id is None
    # 有方法但还没测 → partial（"没做完"，不是"不适用"）
    assert coverage["stated"].designed is True
    assert coverage["stated"].completeness == Completeness.PARTIAL


def test_coverage_tracks_the_current_version(seeded, token) -> None:
    """写入当前版本后，该通道完整度随版本走 —— 运行态只有一处真相。"""
    session, _ = seeded
    insert_baseline_version(
        token,
        session,
        variable_id="V-ATT",
        channel=EvidenceChannel.STATED,
        basis=Basis.STATED,
        completeness=Completeness.COMPLETE,
        score=7.0,
    )
    coverage = {c.channel.value: c for c in measurement_coverage(session, "V-ATT")}
    assert coverage["stated"].completeness == Completeness.COMPLETE
    assert coverage["stated"].current_version_id == "M1-ATT-001"
    # 其它通道不受影响 —— 完整度是按通道算的，不是一个变量一个值
    assert coverage["measured"].current_version_id is None
    assert coverage["measured"].completeness == Completeness.PARTIAL


def test_coverage_reflects_only_the_current_version_after_replacement(
    seeded, token
) -> None:
    session, _ = seeded
    for completeness, score in (
        (Completeness.PARTIAL, 4.0),
        (Completeness.COMPLETE, 8.0),
    ):
        insert_baseline_version(
            token,
            session,
            variable_id="V-SE",
            channel=EvidenceChannel.MEASURED,
            basis=Basis.MEASURED,
            completeness=completeness,
            score=score,
        )
    coverage = {c.channel.value: c for c in measurement_coverage(session, "V-SE")}
    assert coverage["measured"].completeness == Completeness.COMPLETE
    assert coverage["measured"].current_version_id == "M1-SE-002"
