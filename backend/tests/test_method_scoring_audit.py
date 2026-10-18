"""测量仪器红线（M1 §5 红线 7 / Benchmark v0.3 §5.0）的守卫测试。

> **`measured` 通道的读数不得由「存在正确答案」的题产出。**

## 为什么这个文件里有一个"刻意的脆弱断言"

`test_the_current_debt_is_exactly_recorded` 把**当前债务的规模**写死
（15 个方法未声明 + V-SE 触犯红线 7）。这与
`tests/session/test_schema_purity.py` 里那句 `== 31` 张表是同一手法：

- 债务**变小**了 → 这条测试变红 → 逼人更新它（顺手确认修得对）
- 债务**变大**了 → 也是变红 → 那是一次真回归

它编码的是"欠债的收据"，不是"期望的行为"。**没有它，债务只会安静地躺着** ——
而这不是设想：`maps_to_score` 字段本来就在 M1 模板里空着 35/40，
一直没人发现，因为它不报错，只是让 §5.3 静默跳过那些方法。
"""

from __future__ import annotations

import pytest

from app.seeds.m1_variables import _PREFERENCES, _STRATEGY_OPTIONS, _TRAITS
from tools.auditmethods import (
    MEASURED_LEGAL,
    MEASURED_READING,
    OTHER_CHANNEL,
    VOCAB,
    audit_variable,
)

ALL_VARIABLES = _TRAITS + _PREFERENCES + _STRATEGY_OPTIONS


def _methods(var: dict) -> list[dict]:
    return var["measurement"]["methods"]


# --------------------------------------------------------------------------- #
# 真不变式（与债务无关，现在就该成立）
# --------------------------------------------------------------------------- #


def test_every_declared_scoring_mechanism_is_in_the_closed_vocabulary() -> None:
    """自由文本让「这是读数还是标尺」无法被检查，而两者会被同等对待。

    这条是**真不变式**：它现在成立，将来也必须成立。
    修 V-SE 时改坏的正是这一类（原来填的是 `正确率/用时换算`、`表现差` 这样的自由文本）。
    """
    bad = [
        (v["variable_id"], m["method_id"], m["maps_to_score"])
        for v in ALL_VARIABLES
        for m in _methods(v)
        if m["maps_to_score"] and m["maps_to_score"] not in VOCAB
    ]
    assert bad == [], f"这些方法的计分机制不在词表里：{bad}"


def test_no_measured_method_claims_another_channels_mechanism() -> None:
    """`basis=measured` 的方法不得声明 `self_report` / `incremental`。

    那两种是**别的通道**的机制。让一个 `measured` 方法声明 `incremental`，
    等于说"这一格的实测读数由 Session 累积产出" —— 通道就串了。

    ★ 第一版这条测试写成「不得声明 `MEASURED_LEGAL` 之外的任何值」，
    于是把 `external_evidence`（`none`）也报成了违规 ——
    因为我把「允许声明什么」和「声明什么才有读数」当成了同一个集合的补集。
    真正该禁的是**跨通道**，不是"既不读数也不当标尺"。
    """
    bad = [
        (v["variable_id"], m["method_id"], m["maps_to_score"])
        for v in ALL_VARIABLES
        for m in _methods(v)
        if m["basis"] == "measured"
        and m["maps_to_score"] in OTHER_CHANNEL
    ]
    assert bad == [], f"`measured` 方法声明了别的通道的机制：{bad}"


def test_external_evidence_is_declared_as_no_instrument() -> None:
    """`external_evidence` 是**旁证**，不是仪器 —— 它必须声明 `none`。

    它在 `_m` 里是 `basis=measured`（别人给的反馈确实是"实测到的事实"），
    但它产不出一个分数：没有量纲，也没有换算表。
    把它填成 `behavior` 之类的会凭空造出一个仪器。
    """
    ev = [
        (v["variable_id"], m["method_id"], m["maps_to_score"])
        for v in ALL_VARIABLES
        for m in _methods(v)
        if m["kind"] == "external_evidence"
    ]
    assert ev, "一个 `external_evidence` 方法都没有？结构变了。"
    assert all(v == "none" for _, _, v in ev), f"`external_evidence` 应全部为 none：{ev}"


def test_the_reading_and_ruler_sets_do_not_overlap() -> None:
    """`ruler` 不能同时是读数机制 —— 否则红线 7 就是自相矛盾的。"""
    assert MEASURED_READING.isdisjoint({"ruler", "none"})
    assert "ruler" in MEASURED_LEGAL


def test_no_variable_is_silently_missing_its_whole_measured_side() -> None:
    """一个变量如果连一个 `basis=measured` 的方法都没有，红线 7 无从谈起 —— 但要报出来。

    V-ENV 与 V-ACT 属于这种情况（M1 §2 的 P2 列本来就是「—」）。
    """
    no_measured = [
        v["variable_id"]
        for v in ALL_VARIABLES
        if not any(m["basis"] == "measured" for m in _methods(v))
    ]
    assert no_measured == ["V-ENV", "V-ACT"], (
        f"没有 `measured` 方法的变量集合变了：{no_measured} —— "
        "要么新增了一个无实测通道的变量，要么有人补上了 V-ENV/V-ACT 的方法"
    )


# --------------------------------------------------------------------------- #
# 债务收据（刻意的脆弱断言）
# --------------------------------------------------------------------------- #


def test_the_current_debt_is_exactly_recorded() -> None:
    """**欠债的收据**，不是期望的行为。改动它时必须解释为什么债务变了。"""
    undeclared = sorted(
        m["method_id"] for v in ALL_VARIABLES for m in _methods(v)
        if not m["maps_to_score"]
    )
    assert len(undeclared) == 15, (
        f"未声明计分机制的方法数从 15 变成了 {len(undeclared)}：\n  {undeclared}\n"
        "变少 = 有人做了测量设计决定（请更新这个数字并说明）；\n"
        "变多 = 新增了没有计分机制的方法（那是一处真回归）。"
    )
    assert undeclared == sorted([
        "M-SE-04",
        "M-HSR-02", "M-HSR-03",
        "M-DC-02", "M-DC-03",
        "M-IR-02", "M-IR-03",
        "M-PAR-02",
        "M-ENV-01",
        "M-ATT-02", "M-ATT-03",
        "M-CP-02",
        "M-SMP-02",
        "M-OSP-02", "M-OSP-03",
    ]), f"未声明集合变了：{undeclared}"

    #: 全部是 behavioral_task / retrospective —— 这两类**刻意不在**
    #: `_KIND_DEFAULT_SCORE` 里，因为"怎么变成一个数"是测量设计决定，
    #: 不是可以推导的默认值。
    kinds = {
        m["kind"] for v in ALL_VARIABLES for m in _methods(v)
        if not m["maps_to_score"]
    }
    assert kinds == {"behavioral_task", "retrospective"}, (
        f"未声明的方法里出现了别的 kind：{kinds} —— "
        "说明有人给一个本该派生默认值的 kind 漏填了"
    )


def test_v_se_is_still_the_only_red_line_violation() -> None:
    """★ 红线 7 的当前违例集合。

    **为什么这条测试值钱**：如果有一天别处冒出一个"正误题冒充读数"的变量，
    它会变红 —— 而这是本项目最不希望发生、又最难靠人眼发现的事
    （它不报错，只是悄悄把能力测成了倾向）。
    """
    reports = {v["variable_id"]: audit_variable(v) for v in ALL_VARIABLES}

    red_line = sorted(
        vid for vid, r in reports.items() if r.has("red_line")
    )
    assert red_line == ["V-SE"], (
        f"触犯红线 7 的变量集合变了：{red_line}\n"
        "修好 V-SE 之后请把这条断言改成 `== []` 并说明用了什么仪器替代。"
    )

    markers = [
        m["method_id"] for m in _methods(
            next(v for v in ALL_VARIABLES if v["variable_id"] == "V-SE")
        )
        if m["maps_to_score"] == "ruler"
    ]
    assert markers == ["M-SE-03"], (
        f"V-SE 的标尺集合变了：{markers} —— 标尺池是等值化（§5.6）的分母，"
        "它变了会让历史分数的可比性一起变"
    )


@pytest.mark.parametrize("vid", ["V-SE", "V-HSR", "V-ATT"])
def test_audit_reports_a_reason_for_every_flagged_variable(vid: str) -> None:
    """报出问题就必须给出"要动哪里"，否则报告会退化成噪音。"""
    var = next(v for v in ALL_VARIABLES if v["variable_id"] == vid)
    report = audit_variable(var)
    assert report.findings, f"{vid} 应当被报出问题"
    for f in report.findings:
        assert f.tag, f"{vid} 的发现项没有分类标签：{f.message}"
        assert len(f.message) > 20, f"{vid} 的发现项太短，没说清要动哪里"
