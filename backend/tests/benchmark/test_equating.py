"""等值化校正（P12）。

用户做不了 IRT：没有几百人几百题去估题目参数。用锚题做校正，
底下压着一句**必须说出口的假设**：

> 他对同一道锚题的能力没有变化，所以这次与基线的差是**仪器**的差。

这个文件盯的就是"这句假设有没有被说出来"，以及"说不了的时候有没有硬说"：

1. **校正量、参与校正的锚题、基线来自哪几次运行**，全部要在结果里看得见；
2. **共有锚题不足时 `shift=None`**，而不是"校正量为 0" ——
   把一道锚题的波动归给仪器，是**编一个理由**；
3. **原始分不被改写**：校正量会随基线变化，改写历史分数会让"当时是多少"无从回答。
"""

from __future__ import annotations

import pytest

from app.benchmark.equating import (
    MIN_EQUATING_ANCHORS,
    AnchorComparison,
    equate,
)


def test_no_baseline_means_no_correction_not_zero_correction() -> None:
    """★ `shift=None` 与 `shift=0.0` 是**两件事**。

    `0.0` 是在说"我量过了，没有漂移"；`None` 是在说"我量不了"。
    混起来的话，第一次运行也会显示"已校正 0.00"——
    一个从没有过基线的系统会声称自己校正过了。
    """
    result = equate({"A-1": 3.0}, {})
    assert result.shift is None
    assert not result.usable
    assert "没有基线" in result.reason
    assert result.correct(6.0) == 6.0  # 分数原样返回，**不是** 6.0 + 0


def test_the_shift_is_baseline_minus_current() -> None:
    """★ 符号方向：**这次比基线低**时应当**往上补**。

    搞反了不会有任何报错 —— 它只是让"退步的人"显得进步，
    而两种错误在数字上都很正常。
    """
    worse = equate({"A-1": 4.0, "A-2": 4.0}, {"A-1": 6.0, "A-2": 6.0})
    assert worse.shift == 2.0
    assert worse.correct(5.0) == 7.0

    better = equate({"A-1": 8.0, "A-2": 8.0}, {"A-1": 6.0, "A-2": 6.0})
    assert better.shift == -2.0
    assert better.correct(5.0) == 3.0


def test_the_shift_is_the_mean_over_shared_anchors() -> None:
    result = equate(
        {"A-1": 4.0, "A-2": 6.0},
        {"A-1": 6.0, "A-2": 6.0},
    )
    assert result.shift == 1.0
    assert [a.item_id for a in result.anchors] == ["A-1", "A-2"]


def test_one_shared_anchor_is_not_enough_to_blame_the_instrument() -> None:
    """★ 共有锚题不足 → **不校正**，并说明为什么。

    一道锚题这次没抓住，可能是他这次状态不好、也可能是仪器漂了 ——
    用一个数解释一件需要多个数才能说的事，就是编一个理由。
    """
    result = equate({"A-1": 4.0}, {"A-1": 6.0})
    assert result.shift is None
    assert not result.usable
    assert f"只有 1 道（需要 ≥{MIN_EQUATING_ANCHORS}）" in result.reason
    # 证据仍然带出来：不校正不等于不报告
    assert [a.item_id for a in result.anchors] == ["A-1"]


def test_an_anchor_that_was_not_measured_this_time_does_not_participate() -> None:
    """★ 没测到的锚题**不参与**：拿一个空缺去解释另一个空缺，什么也解释不了。"""
    result = equate({"A-1": 4.0, "A-2": None}, {"A-1": 6.0, "A-2": 6.0})
    assert len(result.anchors) == 1
    assert result.shift is None  # 只剩一道，不够


def test_the_corrected_score_stays_inside_the_scale() -> None:
    """★ 夹范围：校正量来自另一批题，越界是真实可能发生的。"""
    result = equate({"A-1": 0.0, "A-2": 0.0}, {"A-1": 8.0, "A-2": 8.0})
    assert result.shift == 8.0
    assert result.correct(9.0) == 10.0

    down = equate({"A-1": 10.0, "A-2": 10.0}, {"A-1": 1.0, "A-2": 1.0})
    assert down.shift == -9.0
    assert down.correct(2.0) == 0.0


def test_a_void_score_stays_void_after_correction() -> None:
    """★ 没测到就是没测到：给 `None` 加一个校正量会把它变成一个数字。"""
    result = equate({"A-1": 4.0, "A-2": 4.0}, {"A-1": 6.0, "A-2": 6.0})
    assert result.usable
    assert result.correct(None) is None


def test_the_description_states_the_assumption() -> None:
    """★ 校正不是"一个小数点级别的技术细节"，它建立在**一句关于他的假设**上。

    所以那句话必须跟着数字一起出来。没有它，一个校正过的 6.8 看起来
    与一个没校正过的 6.8 完全一样，而两者不是一回事。
    """
    text = equate({"A-1": 4.0, "A-2": 4.0}, {"A-1": 6.0, "A-2": 6.0}).describe()
    assert "+2.00" in text
    assert "A-1" in text
    assert "假设" in text
    assert "隔得越久越不成立" in text


def test_the_description_says_why_when_it_cannot_correct() -> None:
    """★ 校正不了也要把**为什么**说出来，并且正面回答"校正了没有"。

    它开头那几个字是用户唯一会读到的部分：写「未校正：」而不是
    让它看起来像"校正量 0" —— 那两件事差得很远。
    """
    text = equate({"A-1": 4.0}, {"A-1": 6.0}).describe()
    assert text.startswith("等值化：未校正 —— ")
    assert "只有 1 道" in text


def test_the_baseline_run_ids_are_carried_out() -> None:
    """★ 基线来自哪几次运行要带出来：**假设成不成立取决于那是多久以前**。"""
    result = equate(
        {"A-1": 4.0, "A-2": 4.0},
        {"A-1": 6.0, "A-2": 6.0},
        baseline_runs={"A-1": "BR-0001", "A-2": "BR-0009"},
    )
    assert set(result.baseline_runs) == {"BR-0001", "BR-0009"}


def test_the_same_run_is_not_listed_twice() -> None:
    result = equate(
        {"A-1": 4.0, "A-2": 4.0},
        {"A-1": 6.0, "A-2": 6.0},
        baseline_runs={"A-1": "BR-0001", "A-2": "BR-0001"},
    )
    assert result.baseline_runs == ("BR-0001",)


def test_delta_reads_as_baseline_minus_current() -> None:
    comparison = AnchorComparison(item_id="A-1", baseline=7.0, current=5.0)
    assert comparison.delta == 2.0


def test_equate_is_order_independent() -> None:
    """锚题的**枚举顺序**不该影响校正量（它是均值，但实现里排序过）。"""
    one = equate({"A-2": 4.0, "A-1": 6.0}, {"A-1": 6.0, "A-2": 6.0})
    two = equate({"A-1": 6.0, "A-2": 4.0}, {"A-2": 6.0, "A-1": 6.0})
    assert one.shift == two.shift == 1.0
    assert [a.item_id for a in one.anchors] == [a.item_id for a in two.anchors] == ["A-1", "A-2"]


def test_the_minimum_is_two_anchors() -> None:
    assert MIN_EQUATING_ANCHORS == 2


@pytest.mark.parametrize("score", [3.33, 6.0, 0.0, 10.0])
def test_a_correction_of_zero_is_reported_as_usable(score: float) -> None:
    """★ 真的量过、真的没有漂移时，`shift=0.0` 是**可用**的。

    这一条是上面"没有基线 = None"的边界：两者的数字一样（都不改变分数），
    差别在"量过没有"。
    """
    result = equate({"A-1": score, "A-2": score}, {"A-1": score, "A-2": score})
    assert result.usable
    assert result.shift == 0.0
    assert result.correct(7.0) == 7.0
