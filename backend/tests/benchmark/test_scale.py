"""分段说明（0-4 / 4-8 / 8-10）—— P10。

用户的要求原话是「每个参数旁边要有『0-4 分说明什么』这类分段说明」。
所以这里盯的是三件事：

1. **11 个变量都写齐了**，而且三段**首尾相接覆盖 0-10**。
   分段表有洞或重叠时，某些分数会没有说明（或有两个答案），
   而这两种错在界面上看起来都只是"这一段没写"。
2. **低端与高端两段与锚点一致**。锚点早就在种子里，
   分段说明若另写一套，用户会在同一个变量上看到两种自相矛盾的描述 ——
   而那不会有任何报错。
3. **中间那一段必须标成初稿**。中段没有现成锚点可引用，
   它必然是这一轮新写的文案；不标出来，用户会以为它已经定稿了。
"""

from __future__ import annotations

import pytest

from app.benchmark.scale import (
    Band,
    BandError,
    as_bands,
    band_for,
    check_bands,
    describe_score,
)
from app.seeds.m1_variables import VARIABLES, _SCALE_MID


def _seed_bands() -> list[tuple[str, tuple[Band, ...]]]:
    return [(v["variable_id"], as_bands(v["scale_bands"])) for v in VARIABLES]


# --------------------------------------------------------------------------- #
# 种子里的分段说明
# --------------------------------------------------------------------------- #


def test_every_variable_has_three_bands_covering_the_whole_scale() -> None:
    """★ 缺一个变量，那个变量的分数就没有任何说明 —— 而它看起来只是"没写"。"""
    assert len(VARIABLES) == 11
    for variable_id, bands in _seed_bands():
        assert len(bands) == 3, f"{variable_id} 有 {len(bands)} 段"
        assert bands[0].low == 0.0 and bands[-1].high == 10.0, variable_id


def test_every_score_has_exactly_one_band() -> None:
    """0.5 分的粒度扫一遍：每个分数都恰好落进一段，**没有洞也没有重叠**。"""
    for variable_id, bands in _seed_bands():
        for step in range(0, 21):
            score = step / 2
            matches = [b for b in bands if b.low <= score < b.high or score == 10.0 == b.high]
            assert len(matches) == 1, f"{variable_id} 的 {score} 分落在 {len(matches)} 段里"


def test_the_two_outer_bands_are_the_existing_anchors() -> None:
    """★ 分段说明不许与锚点各说一套 —— 同一个变量两种描述是"系统自相矛盾"。"""
    for variable in VARIABLES:
        bands = as_bands(variable["scale_bands"])
        assert bands[0].description == variable["scale_low_anchor"]
        assert bands[-1].description == variable["scale_high_anchor"]


def test_only_the_middle_band_is_a_draft() -> None:
    """中段没有现成锚点可引用，它必然是**新写的**；标出来而不是伪装成推导出来的。"""
    for variable in VARIABLES:
        bands = as_bands(variable["scale_bands"])
        assert bands[0].draft is False
        assert bands[1].draft is True
        assert bands[2].draft is False


def test_every_variable_has_its_own_middle_band() -> None:
    """11 段中段文字**各不相同**：一句通用的"中等水平"对任何变量都没有信息量。"""
    assert len(_SCALE_MID) == len(VARIABLES)
    assert len(set(_SCALE_MID.values())) == len(_SCALE_MID)
    assert set(_SCALE_MID) == {v["variable_id"] for v in VARIABLES}


def test_a_variable_without_a_middle_band_would_be_caught() -> None:
    """反面：种子里新增一个变量却忘了写中段时，必须**报错**而不是静默给空段。"""
    with pytest.raises(KeyError):
        from app.seeds.m1_variables import scale_bands

        scale_bands("V-NEW", "低", "高")


# --------------------------------------------------------------------------- #
# 取值：边界
# --------------------------------------------------------------------------- #


def _bands() -> tuple[Band, ...]:
    return (
        Band(low=0.0, high=4.0, label="低端", description="低"),
        Band(low=4.0, high=8.0, label="中段", description="中", draft=True),
        Band(low=8.0, high=10.0, label="高端", description="高"),
    )


@pytest.mark.parametrize(
    ("score", "label"),
    [
        (0.0, "低端"),
        (3.99, "低端"),
        (4.0, "中段"),
        (7.99, "中段"),
        (8.0, "高端"),
        (9.99, "高端"),
        (10.0, "高端"),
    ],
)
def test_the_boundary_convention_is_left_closed_right_open(
    score: float, label: str
) -> None:
    """★ `4.0` 归中段、`8.0` 归高端、`10.0` 归高端。

    这条约定只要散在两处，同一个 `4.0` 就会在两个地方显示成两段不同的话，
    而用户看到的是"这个系统说话前后不一致"。
    """
    band = band_for(_bands(), score)
    assert band is not None and band.label == label


def test_a_score_outside_the_scale_gets_no_band() -> None:
    """★ 不夹范围、不兜底：11 分落到「高端」会掩盖上游的错。"""
    assert band_for(_bands(), -0.1) is None
    assert band_for(_bands(), 10.1) is None


def test_an_empty_band_table_returns_none() -> None:
    assert band_for((), 6.2) is None


# --------------------------------------------------------------------------- #
# 校验：一张写错的分段表必须报出来
# --------------------------------------------------------------------------- #


def test_a_gap_in_the_table_is_refused() -> None:
    """★ 有洞 = 某些分数**没有说明**，而那正是要防的静默状态。"""
    with pytest.raises(BandError, match="有洞"):
        check_bands(
            (
                Band(0.0, 4.0, "低端", "低"),
                Band(5.0, 10.0, "高端", "高"),
            )
        )


def test_an_overlap_is_refused() -> None:
    """★ 重叠 = 一个分数**有两个答案**。"""
    with pytest.raises(BandError, match="重叠"):
        check_bands(
            (
                Band(0.0, 6.0, "低端", "低"),
                Band(4.0, 10.0, "高端", "高"),
            )
        )


def test_a_table_that_misses_the_ends_is_refused() -> None:
    with pytest.raises(BandError, match="没有从 0.0 开始"):
        check_bands((Band(1.0, 10.0, "低端", "低"),))
    with pytest.raises(BandError, match="没有覆盖到 10.0"):
        check_bands((Band(0.0, 9.0, "低端", "低"),))


def test_a_reversed_or_empty_range_is_refused() -> None:
    """★ 报的是「写反了」而不是「没覆盖到 10」—— 诊断指错方向的代价更高。

    一张写着 `10-4` 的表若被报成"最高一段到不了 10"，
    人会去补一段，而真正的问题是那一段写反了。
    """
    with pytest.raises(BandError, match="倒的或空的"):
        check_bands((Band(0.0, 4.0, "低端", "低"), Band(10.0, 4.0, "高端", "高")))
    with pytest.raises(BandError, match="倒的或空的"):
        check_bands((Band(0.0, 4.0, "低端", "低"), Band(4.0, 4.0, "空段", "中")))


def test_an_empty_description_is_refused() -> None:
    """空的说明等于没有说明，而它在界面上会显示成一片空白。"""
    with pytest.raises(BandError, match="没有说明文字"):
        check_bands(
            (
                Band(0.0, 4.0, "低端", "低"),
                Band(4.0, 8.0, "中段", "   "),
                Band(8.0, 10.0, "高端", "高"),
            )
        )


def test_a_band_missing_a_field_is_refused() -> None:
    with pytest.raises(BandError, match="缺字段"):
        as_bands([{"low": 0.0, "high": 4.0, "label": "低端"}])


def test_an_empty_table_is_refused() -> None:
    with pytest.raises(BandError, match="空的"):
        as_bands([])


# --------------------------------------------------------------------------- #
# 给用户的一句话：三种状态，三句话
# --------------------------------------------------------------------------- #


def test_the_three_states_read_differently() -> None:
    """★「没测到」「有分但没写说明」「有说明」必须读起来是三件事。

    混起来的话，"这次没测到"会被读成"0 分"，而那正是本项目从 §5.2 起
    一直在防的错。
    """
    void = describe_score(_bands(), None)
    no_bands = describe_score((), 6.2)
    normal = describe_score(_bands(), 6.2)
    assert "没测到" in void and "不是" in void
    assert "没有写分段说明" in no_bands
    assert "6.2" in normal and "中段" in normal
    assert len({void, no_bands, normal}) == 3


def test_a_draft_band_is_marked_in_the_sentence() -> None:
    assert "〔初稿〕" in describe_score(_bands(), 6.2)
    assert "〔初稿〕" not in describe_score(_bands(), 9.0)
