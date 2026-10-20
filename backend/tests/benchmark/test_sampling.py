"""抽题规则（Benchmark v0.3 §6.3）—— 逐条钉死。

这些测试存在的理由只有一个：**抽题的错法全都不报错**。
抽错难度构成、抽到他已经熟悉的领域、老是重复那几个专题 ——
每一种都会产出一次"看起来正常"的运行，而它的分数与别的运行不可比。
所以每条规则都要有一个**反例测试**：不是"它能抽出题"，
而是"它在违反规则时会拒绝，并且说出缺什么"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.benchmark.items import BenchItem
from app.benchmark.sampling import (
    HISTOGRAM_BLOCK,
    History,
    SamplingRefused,
    plan_sampling,
    summarize_history,
)

# --------------------------------------------------------------------------- #
# 构造
# --------------------------------------------------------------------------- #


def _item(
    item_id: str,
    *,
    role: str = "regular",
    difficulty: str = "中",
    topics: tuple[str, ...] = ("T-BASE",),
    variable_id: str = "V-SE",
    channel: str = "measured",
    method_id: str = "M-SE-04",
    status: str = "verified",
) -> BenchItem:
    return BenchItem(
        item_id=item_id,
        variable_id=variable_id,
        channel=channel,
        method_id=method_id,
        role=role,
        difficulty=difficulty,
        topics=topics,
        time_min=6.0,
        language="zh",
        media="text",
        scoring={"rule": "key_points", "weight_source": "difficulty"},
        source="测试",
        license="个人使用",
        status=status,
        title=f"{item_id} 的标题",
        material_path=Path(f"materials/{item_id}.md"),
        material_text="材料正文。",
        anchors={},
        key_points_question={"type": "key_points", "scoring_guide": ["要点一"]},
        exact_questions=[],
    )


def _bank(*items: BenchItem) -> list[BenchItem]:
    return list(items)


def _two_anchors() -> list[BenchItem]:
    """两道锚题，**各挂自己的专题** —— 这样普通题的专题断言不会被它们污染。"""
    return [
        _item("A-1", role="anchor", topics=("T-ANC1",)),
        _item("A-2", role="anchor", difficulty="易", topics=("T-ANC2",)),
    ]


def _one_block(
    *, easy: int = 1, mid: int = 2, hard: int = 1, prefix: str = "R"
) -> list[BenchItem]:
    """刚好凑出一个「易 1 : 中 2 : 难 1」块的普通题。"""
    out: list[BenchItem] = []
    for index in range(easy):
        out.append(_item(f"{prefix}-E{index}", difficulty="易"))
    for index in range(mid):
        out.append(_item(f"{prefix}-M{index}", difficulty="中"))
    for index in range(hard):
        out.append(_item(f"{prefix}-H{index}", difficulty="难"))
    return out


def _plan(bank: list[BenchItem], **kwargs):
    params: dict = {
        "variable_id": "V-SE",
        "channel": "measured",
        "method_id": "M-SE-04",
        "form": "quick",
        "familiar": (),
        "seed": 7,
    }
    params.update(kwargs)
    return plan_sampling(bank, **params)


# --------------------------------------------------------------------------- #
# ① 锚题每卷必出
# --------------------------------------------------------------------------- #


def test_anchors_always_come_out() -> None:
    plan = _plan(_bank(*_two_anchors(), *_one_block()))
    assert plan.anchor_ids == ["A-1", "A-2"]


def test_anchors_come_out_even_when_they_are_the_wrong_difficulty() -> None:
    """锚题**不进难度直方图** —— 它必须重复，所以"难度构成"这条规则不适用于它。

    如果锚题也去挤那 4 个名额，题池里锚题一多，普通题的难度构成就会先被挤歪。
    """
    bank = _bank(
        _item("A-1", role="anchor", difficulty="难"),
        _item("A-2", role="anchor", difficulty="难"),
        *_one_block(),
    )
    plan = _plan(bank)
    assert plan.anchor_ids == ["A-1", "A-2"]
    assert plan.difficulty_mix == {"难": 3, "易": 1, "中": 2}


def test_fewer_than_two_anchors_is_refused() -> None:
    bank = _bank(_item("A-1", role="anchor"), *_one_block())
    with pytest.raises(SamplingRefused) as excinfo:
        _plan(bank)
    message = str(excinfo.value)
    assert "锚题" in message
    assert "1 道" in message and "至少" in message


def test_the_refusal_for_missing_anchors_does_not_pretend_it_is_a_pool_problem() -> None:
    """拒绝的话要说清**是缺锚题**，而不是笼统的"抽不出题"。

    只说"抽不出题"的报告，下场是没人知道该补什么。
    """
    with pytest.raises(SamplingRefused) as excinfo:
        _plan(_bank(*_one_block()))
    assert "锚题" in str(excinfo.value)
    assert "V-SE/measured" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# ② 难度直方图固定
# --------------------------------------------------------------------------- #


def test_one_block_is_one_easy_two_mid_one_hard() -> None:
    plan = _plan(_bank(*_two_anchors(), *_one_block()))
    regulars = [item for item in plan.items if not item.is_anchor]
    assert [item.difficulty for item in regulars].count("易") == 1
    assert [item.difficulty for item in regulars].count("中") == 2
    assert [item.difficulty for item in regulars].count("难") == 1
    assert len(regulars) == len(HISTOGRAM_BLOCK)


def test_two_blocks_doubles_the_histogram() -> None:
    bank = _bank(*_two_anchors(), *_one_block(mid=4, prefix="R"), *_one_block(prefix="S"))
    plan = _plan(bank, blocks=2)
    regulars = [item for item in plan.items if not item.is_anchor]
    assert {d: [i.difficulty for i in regulars].count(d) for d in ("易", "中", "难")} == {
        "易": 2,
        "中": 4,
        "难": 2,
    }


def test_a_missing_difficulty_is_refused_not_substituted() -> None:
    """★ 本模块最重要的一条：缺"难"题时**不许用"中"题补**。

    补上的话，这次运行的分数会悄悄偏向一个方向，
    而它与别的运行"不可比"这件事**没有任何症状**。
    """
    bank = _bank(
        *_two_anchors(),
        _item("R-E0", difficulty="易"),
        _item("R-M0", difficulty="中"),
        _item("R-M1", difficulty="中"),
        _item("R-M2", difficulty="中"),  # 多一道"中"，够补缺
    )
    with pytest.raises(SamplingRefused) as excinfo:
        _plan(bank)
    message = str(excinfo.value)
    assert "难 缺 1 道" in message
    assert "不用别的难度补缺" in message


def test_the_refusal_says_how_many_of_each_difficulty_are_available() -> None:
    bank = _bank(*_two_anchors(), _item("R-E0", difficulty="易"))
    with pytest.raises(SamplingRefused) as excinfo:
        _plan(bank)
    message = str(excinfo.value)
    assert "中 缺 2 道" in message and "难 缺 1 道" in message
    assert "易 1 道" in message


def test_blocks_must_be_positive() -> None:
    with pytest.raises(SamplingRefused):
        _plan(_bank(*_two_anchors(), *_one_block()), blocks=0)


# --------------------------------------------------------------------------- #
# ③ 领域：定向过滤
# --------------------------------------------------------------------------- #


def test_items_touching_a_familiar_topic_are_excluded() -> None:
    bank = _bank(
        *_two_anchors(),
        _item("R-E0", difficulty="易", topics=("PHYS-CLASS",)),
        _item("R-E1", difficulty="易", topics=("CHEM-ORG",)),
        _item("R-M0", difficulty="中", topics=("BIO-MARINE",)),
        _item("R-M1", difficulty="中", topics=("CHEM-ORG",)),
        _item("R-H0", difficulty="难", topics=("EARTH-GEOL",)),
    )
    plan = _plan(bank, familiar=("PHYS-CLASS",))
    assert "R-E0" not in plan.item_ids
    assert "R-E1" in plan.item_ids


def test_one_familiar_topic_is_enough_to_exclude_a_cross_topic_item() -> None:
    """★ 判据是"一个都没沾到"，不是"主专题不熟悉"。

    一份材料挂两个专题时，只要有一个他熟，他就能靠那一格读下去 ——
    于是它**不是**在测"面对陌生材料"。这就是 `topics` 必须是列表的原因。
    """
    bank = _bank(
        *_two_anchors(),
        _item("R-E0", difficulty="易", topics=("BIO-MARINE", "PHYS-CLASS")),
        _item("R-E1", difficulty="易", topics=("BIO-MARINE",)),
        _item("R-M0", difficulty="中"),
        _item("R-M1", difficulty="中"),
        _item("R-H0", difficulty="难"),
    )
    plan = _plan(bank, familiar=("PHYS-CLASS",))
    assert "R-E0" not in plan.item_ids
    assert "R-E1" in plan.item_ids


def test_topic_filtering_is_not_applied_to_anchors() -> None:
    """锚题若也被熟悉度过滤，锚题通过率就会随清单变化 —— 那它就不是基准了。

    锚题的领域由**作者**保证它在陌生区（§2.1.1），不由这次勾选决定。
    """
    bank = _bank(
        _item("A-1", role="anchor", topics=("PHYS-CLASS",)),
        _item("A-2", role="anchor", topics=("PHYS-CLASS",)),
        *_one_block(),
    )
    plan = _plan(bank, familiar=("PHYS-CLASS",))
    assert plan.anchor_ids == ["A-1", "A-2"]


def test_excluding_every_candidate_is_refused_with_the_excluded_ids() -> None:
    bank = _bank(
        *_two_anchors(),
        _item("R-E0", difficulty="易", topics=("PHYS-CLASS",)),
        _item("R-M0", difficulty="中", topics=("PHYS-CLASS",)),
        _item("R-M1", difficulty="中", topics=("PHYS-CLASS",)),
        _item("R-H0", difficulty="难", topics=("PHYS-CLASS",)),
    )
    with pytest.raises(SamplingRefused) as excinfo:
        _plan(bank, familiar=("PHYS-CLASS",))
    message = str(excinfo.value)
    assert "4 道题被他标成熟悉而被排除" in message
    assert "R-E0" in message and "R-H0" in message


# --------------------------------------------------------------------------- #
# ④ 未做过的优先 + 领域轮换
# --------------------------------------------------------------------------- #


def test_never_seen_items_win_over_seen_ones() -> None:
    """一个格子里候选比名额多时，先挑**没做过的**。

    ★ 注意"名额"这件事：「易 1 : 中 2 : 难 1」意味着"中"有两格，
    所以只有一道"中"候选时，它是否做过**不影响结果** ——
    测试必须给出比名额多的候选，否则它什么都没测到。
    """
    bank = _bank(
        *_two_anchors(),
        _item("R-E0", difficulty="易", topics=("T-1",)),
        _item("R-E1", difficulty="易", topics=("T-2",)),
        _item("R-M0", difficulty="中", topics=("T-1",)),
        _item("R-M1", difficulty="中", topics=("T-2",)),
        _item("R-M2", difficulty="中", topics=("T-3",)),
        _item("R-H0", difficulty="难", topics=("T-1",)),
        _item("R-H1", difficulty="难", topics=("T-2",)),
    )
    # R-*1 / R-M2 做过，R-E0 / R-M0 / R-H0 没做过
    history = summarize_history([(["R-E1", "R-M1", "R-M2", "R-H1"], ["T-2", "T-3"])])
    plan = _plan(bank, history=history)
    regulars = {item.item_id for item in plan.items if not item.is_anchor}
    assert regulars == {"R-E0", "R-M0", "R-M1", "R-H0"}


def test_among_seen_items_the_least_recently_used_topic_wins() -> None:
    """★ 「同领域重复会让材料变熟悉，静默抬高 V-SE」。

    所以轮换的对象是**专题**，不只是"换一道题"。
    这里每一档都给了比的候选多一道，否则"名额定死了谁上"而不是规则定的。
    """
    bank = _bank(
        *_two_anchors(),
        _item("R-E0", difficulty="易", topics=("OLD",)),
        _item("R-E1", difficulty="易", topics=("NEW",)),
        _item("R-M0", difficulty="中", topics=("OLD",)),
        _item("R-M1", difficulty="中", topics=("OLD",)),
        _item("R-M2", difficulty="中", topics=("NEW",)),
        _item("R-M3", difficulty="中", topics=("NEW",)),
        _item("R-H0", difficulty="难", topics=("OLD",)),
        _item("R-H1", difficulty="难", topics=("NEW",)),
    )
    history = summarize_history(
        [
            (["R-E0"], ["OLD"]),  # 第 1 次运行用过 OLD
            (["R-E1"], ["NEW"]),  # 第 2 次运行用过 NEW
        ]
    )
    plan = _plan(bank, history=history)
    regulars = {item.item_id for item in plan.items if not item.is_anchor}
    assert regulars == {"R-E0", "R-M0", "R-M1", "R-H0"}, "OLD 更久没用过，应该优先"


def test_a_recently_used_topic_loses_to_a_never_used_one() -> None:
    bank = _bank(
        *_two_anchors(),
        _item("R-E0", difficulty="易", topics=("USED",)),
        _item("R-E1", difficulty="易", topics=("FRESH",)),
        _item("R-M0", difficulty="中", topics=("USED",)),
        _item("R-M1", difficulty="中", topics=("USED",)),
        _item("R-M2", difficulty="中", topics=("FRESH",)),
        _item("R-M3", difficulty="中", topics=("FRESH",)),
        _item("R-H0", difficulty="难", topics=("USED",)),
        _item("R-H1", difficulty="难", topics=("FRESH",)),
    )
    history = summarize_history([(["R-E0", "R-M0", "R-M1", "R-H0"], ["USED"])])
    plan = _plan(bank, history=history)
    regulars = {item.item_id for item in plan.items if not item.is_anchor}
    assert regulars == {"R-E1", "R-M2", "R-M3", "R-H1"}


def test_items_never_used_beat_items_used_long_ago() -> None:
    """两层优先级是**分开**的：没做过 > 做过但久没用过。

    混起来（比如只看"领域多久没用过"）会让一道**他做过、但专题很久没碰**
    的题压过一道**全新**的题 —— 而记忆污染恰恰是靠"做过"发生的。
    """
    bank = _bank(
        *_two_anchors(),
        _item("R-E0", difficulty="易", topics=("STALE",)),
        _item("R-E1", difficulty="易", topics=("FRESH",)),
        _item("R-M0", difficulty="中", topics=("STALE",)),
        _item("R-M1", difficulty="中", topics=("STALE",)),
        _item("R-M2", difficulty="中", topics=("FRESH",)),
        _item("R-M3", difficulty="中", topics=("FRESH",)),
        _item("R-H0", difficulty="难", topics=("STALE",)),
        _item("R-H1", difficulty="难", topics=("FRESH",)),
    )
    history = summarize_history([(["R-E0", "R-M0", "R-M1", "R-H0"], ["STALE"])])
    plan = _plan(bank, history=history)
    regulars = {item.item_id for item in plan.items if not item.is_anchor}
    assert regulars == {"R-E1", "R-M2", "R-M3", "R-H1"}


# --------------------------------------------------------------------------- #
# ⑤ 兴趣是记录，不是筛选
# --------------------------------------------------------------------------- #


def test_interest_never_filters() -> None:
    """★ 兴趣若参与筛选，"他对什么感兴趣"就变成了"他实际被考了什么"。

    那正是红线 7（用一个理由解释自己的行为）换个面孔回来。
    """
    bank = _bank(
        *_two_anchors(),
        _item("R-E0", difficulty="易", topics=("BORING",)),
        _item("R-M0", difficulty="中", topics=("BORING",)),
        _item("R-M1", difficulty="中", topics=("BORING",)),
        _item("R-H0", difficulty="难", topics=("BORING",)),
    )
    plan = _plan(bank, interested=("FUN",))
    assert {item.item_id for item in plan.items if not item.is_anchor} == {
        "R-E0",
        "R-M0",
        "R-M1",
        "R-H0",
    }


def test_interest_breaks_ties_in_priority() -> None:
    bank = _bank(
        *_two_anchors(),
        _item("R-E0", difficulty="易", topics=("FUN",)),
        _item("R-E1", difficulty="易", topics=("MEH",)),
        _item("R-M0", difficulty="中", topics=("FUN",)),
        _item("R-M1", difficulty="中", topics=("FUN",)),
        _item("R-M2", difficulty="中", topics=("MEH",)),
        _item("R-M3", difficulty="中", topics=("MEH",)),
        _item("R-H0", difficulty="难", topics=("FUN",)),
        _item("R-H1", difficulty="难", topics=("MEH",)),
    )
    plan = _plan(bank, interested=("FUN",))
    regulars = {item.item_id for item in plan.items if not item.is_anchor}
    assert regulars == {"R-E0", "R-M0", "R-M1", "R-H0"}


def test_interest_does_not_override_the_familiarity_filter() -> None:
    """勾了熟悉又勾了兴趣？题**照样出去** —— 兴趣不能把一份熟悉的材料变陌生。"""
    bank = _bank(
        *_two_anchors(),
        _item("R-E0", difficulty="易", topics=("FUN",)),
        _item("R-E1", difficulty="易", topics=("BORING",)),
        _item("R-M0", difficulty="中", topics=("BORING",)),
        _item("R-M1", difficulty="中", topics=("BORING",)),
        _item("R-H0", difficulty="难", topics=("BORING",)),
    )
    plan = _plan(bank, familiar=("FUN",), interested=("FUN",))
    assert "R-E0" not in plan.item_ids
    assert "R-E1" in plan.item_ids


def test_the_interest_note_is_recorded() -> None:
    bank = _bank(*_two_anchors(), *_one_block())
    plan = _plan(bank, interested=("FUN",))
    assert any("兴趣只影响优先级" in note for note in plan.notes)


# --------------------------------------------------------------------------- #
# ⑥ 可复现
# --------------------------------------------------------------------------- #


def test_same_seed_gives_the_same_set() -> None:
    bank = _bank(*_two_anchors(), *_one_block(mid=4, prefix="R"), *_one_block(prefix="S"))
    first = _plan(bank, seed=11)
    second = _plan(bank, seed=11)
    assert first.item_ids == second.item_ids


def test_different_seeds_still_produce_a_legal_plan() -> None:
    """换种子只该换**具体哪几道**，不该换难度构成、也不该换专题过滤结果。"""
    bank = _bank(*_two_anchors(), *_one_block(mid=4, prefix="R"), *_one_block(prefix="S"))
    mixes = set()
    for seed in range(12):
        plan = _plan(bank, seed=seed)
        mixes.add(tuple(sorted(plan.difficulty_mix.items())))
        assert plan.anchor_ids == ["A-1", "A-2"]
        assert len(plan.item_ids) == len(set(plan.item_ids))
    assert len(mixes) == 1


def test_a_seed_spreads_the_draw_across_the_pool() -> None:
    """★ 排序最后那层必须是**种子打散**，不能是 `item_id`。

    按 id 排会让"总是先抽到 001"，而那个顺序**看起来像规则其实不是** ——
    它会让题池往后扩的时候，新题永远排在后面看不到。
    """
    bank = _bank(
        *_two_anchors(),
        *[
            _item(f"R-E{index}", difficulty="易")
            for index in range(6)
        ],
        _item("R-M0", difficulty="中"),
        _item("R-M1", difficulty="中"),
        _item("R-H0", difficulty="难"),
    )
    drawn = {
        next(
            item.item_id
            for item in _plan(bank, seed=seed).items
            if item.difficulty == "易" and not item.is_anchor
        )
        for seed in range(20)
    }
    assert len(drawn) > 1, f"20 个种子只抽到同一道易题：{drawn}"


# --------------------------------------------------------------------------- #
# ⑦ 格子与状态
# --------------------------------------------------------------------------- #


def test_an_empty_cell_is_refused_and_names_the_cell() -> None:
    with pytest.raises(SamplingRefused) as excinfo:
        _plan(_bank(*_two_anchors()), method_id="M-SE-99")
    assert "V-SE/measured/M-SE-99" in str(excinfo.value)


def test_a_cell_without_regular_items_says_it_is_a_histogram_problem() -> None:
    """两道锚题 + 一道普通题都凑不出直方图时，理由要说**是直方图不够**。

    "这个格子没题"与"这个格子的题不够凑构成"是两件不同的事，
    补题的方向也不同（补一批 vs 补某一档）。
    """
    with pytest.raises(SamplingRefused) as excinfo:
        _plan(_bank(*_two_anchors()))
    assert "构成" in str(excinfo.value)


def test_retired_items_do_not_compete() -> None:
    bank = _bank(
        *_two_anchors(),
        _item("R-E0", difficulty="易", status="retired"),
        _item("R-E1", difficulty="易"),
        _item("R-M0", difficulty="中"),
        _item("R-M1", difficulty="中"),
        _item("R-H0", difficulty="难"),
    )
    plan = _plan(bank)
    assert "R-E0" not in plan.item_ids


def test_candidates_are_order_independent() -> None:
    """题库文件的顺序不该影响抽题结果 —— 它只是文件系统的巧合。"""
    bank = _bank(*_two_anchors(), *_one_block(mid=4, prefix="R"), *_one_block(prefix="S"))
    forward = _plan(bank, seed=3).item_ids
    backward = _plan(list(reversed(bank)), seed=3).item_ids
    assert forward == backward


# --------------------------------------------------------------------------- #
# ⑧ 计划本身要能核对
# --------------------------------------------------------------------------- #


def test_topics_are_collected_without_duplicates() -> None:
    bank = _bank(
        *_two_anchors(),
        _item("R-E0", difficulty="易", topics=("T-1", "T-2")),
        _item("R-M0", difficulty="中", topics=("T-1",)),
        _item("R-M1", difficulty="中", topics=("T-2",)),
        _item("R-H0", difficulty="难", topics=("T-3",)),
    )
    plan = _plan(bank)
    # 锚题的专题也算 —— 这次运行**实际用了**它们（§7.3 ① 的快照口径）
    assert set(plan.topics) == {"T-ANC1", "T-ANC2", "T-1", "T-2", "T-3"}
    # 顺序由抽中顺序决定（种子会影响），**但绝不允许重复**：
    # 重复的专题快照会让"这次涉及几个领域"算错，而它正是轮换的依据。
    assert len(plan.topics) == len(set(plan.topics))


def test_a_single_topic_run_is_flagged() -> None:
    """★ 「同一运行内跨领域取样」只涉及一个专题时必须说出来。

    不说的话，那一步就是"静默没做到" —— 而它会让人以为
    "这个人的表现跨领域一致"，实际上只是抽到的题都在一个领域里。
    """
    only = [
        _item("A-1", role="anchor", topics=("ONLY",)),
        _item("A-2", role="anchor", difficulty="易", topics=("ONLY",)),
        _item("R-E0", difficulty="易", topics=("ONLY",)),
        _item("R-M0", difficulty="中", topics=("ONLY",)),
        _item("R-M1", difficulty="中", topics=("ONLY",)),
        _item("R-H0", difficulty="难", topics=("ONLY",)),
    ]
    plan = _plan(_bank(*only))
    assert plan.topics == ("ONLY",)
    assert any("只涉及一个专题" in note for note in plan.notes)

    two_topics = _bank(
        _item("A-1", role="anchor", topics=("T-1",)),
        _item("A-2", role="anchor", difficulty="易", topics=("T-2",)),
        *_one_block(),
    )
    assert len(_plan(two_topics).topics) > 1


def test_every_pick_carries_a_reason() -> None:
    history = summarize_history([(["R-M0"], ["BIO-MARINE"])])
    plan = _plan(_bank(*_two_anchors(), *_one_block()), history=history)
    for item in plan.items:
        assert item.why, f"{item.item_id} 没有说明自己是怎么被抽中的"
    reg = next(item for item in plan.items if item.item_id == "R-M0")
    assert "做过" in reg.why


def test_the_first_run_says_so() -> None:
    plan = _plan(_bank(*_two_anchors(), *_one_block()))
    assert any("第一次跑" in note for note in plan.notes)


def test_summarize_history_counts_exposure_and_recency() -> None:
    history = summarize_history(
        [(["A"], ["T-1"]), (["A", "B"], ["T-1", "T-2"]), (["C"], ["T-3"])]
    )
    assert history.item_exposure == {"A": 2, "B": 1, "C": 1}
    assert history.item_last_run == {"A": 1, "B": 1, "C": 2}
    assert history.topic_last_run == {"T-1": 1, "T-2": 1, "T-3": 2}


def test_describe_is_a_human_readable_summary() -> None:
    plan = _plan(_bank(*_two_anchors(), *_one_block()))
    text = plan.describe()
    assert "quick" in text and "6 题" in text and "锚题 2" in text and "种子 7" in text
