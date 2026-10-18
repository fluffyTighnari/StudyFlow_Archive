"""写入裁定序 —— 模块级断言（M0 §7.4）。

重点是三件**结构性**的事：

1. 序只看 `(action, basis)`，不看 LLM 自评、不看 Session 内状态；
2. `analyze_conflict` 是纯函数 —— 它连 `Session` 都拿不到，
   因此不可能「顺手写一下」；
3. **分组依据是真实写入槽位，不是 `target` 字符串**（P5.2 修正）。
   这一条单独有一组回归用例，因为这个错误写错一次就会**静默丢数据**，
   而且不会让任何测试变红 —— 只要那个测试也写错了同一个假设。
   （原先的 `test_analyze_conflict_groups_by_target` 就是这样，
   它把「两条不相关的偏好算相撞」断言成了正确行为。）
"""

from __future__ import annotations

import inspect

from app.global_domain.enums import IntentAction
from app.router import conflict
from app.router.conflict import (
    RANK_EXPERIENCED,
    RANK_INFERRED,
    RANK_MEASURED,
    RANK_NO_CHANGE,
    RANK_USER_STATED,
    analyze_conflict,
    losers_of,
    rejected_of,
    write_arbitration_rank,
    write_slot,
)
from tests.router.helpers import evidence_intent, intent, m1_intent, pref_intent


def test_rank_follows_the_documented_order() -> None:
    """用户明确偏好 > measured 基线 > experienced 最近 Session > inferred 推断。"""
    ranks = [
        write_arbitration_rank(pref_intent(basis="stated")),
        write_arbitration_rank(m1_intent(basis="measured")),
        write_arbitration_rank(m1_intent(basis="experienced")),
        write_arbitration_rank(pref_intent(basis="inferred")),
    ]
    assert ranks == sorted(ranks, reverse=True)
    assert ranks == [RANK_USER_STATED, RANK_MEASURED, RANK_EXPERIENCED, RANK_INFERRED]


def test_no_change_ranks_lowest() -> None:
    assert write_arbitration_rank(
        intent(action="no_change", target="M1.V-SE", basis="experienced")
    ) == RANK_NO_CHANGE


def test_rank_ignores_the_llm_self_assessed_confidence() -> None:
    """否则一个语气自信的模型就能压过实测数据。"""
    for confidence in ("high", "medium", "low"):
        assert (
            write_arbitration_rank(m1_intent(basis="measured", confidence=confidence))
            == RANK_MEASURED
        )


def test_rank_only_depends_on_action_and_basis() -> None:
    """target / priority / value 都不得影响序。

    只改这三样，序必须一模一样 —— 这条测试是「序与其它字段无关」的可执行版本。
    """
    base = write_arbitration_rank(m1_intent("I-1", variable="V-SE", basis="measured"))
    variants = [
        m1_intent("I-2", variable="V-HSR", basis="measured"),
        m1_intent("I-3", variable="V-SE", basis="measured", channel="stated"),
        m1_intent("I-4", variable="V-SE", basis="measured", score=1.0),
    ]
    for variant in variants:
        assert write_arbitration_rank(variant) == base

    with_priority = pref_intent(basis="stated")
    with_priority = with_priority.model_copy(update={"priority": 10})
    assert write_arbitration_rank(with_priority) == RANK_USER_STATED


def test_rank_is_total_for_every_registered_combination() -> None:
    """每一个 (action, basis) 组合都必须落到某个档位，不存在「未定义」。"""
    labels = {
        conflict.rank_label(rank)
        for action in IntentAction
        for basis in ("stated", "measured", "experienced", "inferred")
        for rank in [
            write_arbitration_rank(
                intent(action=action, target="M1.V-SE", basis=basis)
            )
        ]
    }
    assert labels  # 非空
    assert all(not label.startswith("未标注档位") for label in labels)


# --------------------------------------------------------------------------- #
# analyze_conflict 的纯函数性质
# --------------------------------------------------------------------------- #


def test_analyze_conflict_takes_no_session() -> None:
    """纯函数的结构性证据：签名里根本没有 Session 可写。"""
    parameters = set(inspect.signature(analyze_conflict).parameters)
    assert parameters == {"intents"}


def test_analyze_conflict_ignores_single_writes() -> None:
    assert analyze_conflict([m1_intent()]) == []
    assert analyze_conflict([]) == []


# --------------------------------------------------------------------------- #
# 分组依据：**真实写入槽位**，不是 target 字符串（P5.2 修正）
# --------------------------------------------------------------------------- #


def test_two_channels_of_one_variable_are_not_a_conflict() -> None:
    """**回归用例**：一次基准测试写同一变量的两个通道 —— 这不是冲突。

    这是 P5.2 修掉的第一个真实缺陷。原先按 `target` 字符串分组，于是
    `M1.V-SE` 上的 measured 与 experienced 被当成相撞，**第二条被静默 skipped**。
    而「一次重做更新 N 个通道」正是 R2 的**常规**形态
    （`count_benchmark_runs` 按 run_id 去重就是因为它）——
    也就是说这个缺陷会打掉主用例。

    target 里根本没有通道：`M1.V-SE` 对两个通道是同一个字符串，
    而它们是两个**互相独立**的存储槽位（这正是通道纯度的意思）。
    """
    reports = analyze_conflict(
        [
            m1_intent("I-1", variable="V-SE", channel="measured"),
            m1_intent("I-2", variable="V-SE", channel="experienced"),
            m1_intent("I-3", variable="V-SE", channel="stated"),
        ]
    )
    assert reports == []


def test_two_unrelated_preferences_are_not_a_conflict() -> None:
    """**回归用例**：一次沉淀两条不同的偏好 —— 这不是冲突。

    这是 P5.2 修掉的第二个真实缺陷。两条声明共用 target `M2.preferences`
    只是因为它们存在同一张表里，不代表它们相撞。原先第二条被静默 skipped，
    **而那正是 R1/R4 沉淀的常规形态**（一次会话沉淀两三条发现）。
    """
    reports = analyze_conflict(
        [
            pref_intent("I-1", statement="我讨厌看网课，因为浪费时间"),
            pref_intent("I-2", statement="我早上效率最高"),
        ]
    )
    assert reports == []


def test_two_evidence_entries_are_never_a_conflict() -> None:
    """C 证据的每一条都是**独立实体**，没有「同一个槽位」可言。"""
    reports = analyze_conflict(
        [
            evidence_intent("I-1", statement="先看例子时我的理解更牢"),
            evidence_intent("I-2", statement="短时段比长时段更有效"),
        ]
    )
    assert reports == []


def test_same_channel_of_one_variable_is_a_conflict() -> None:
    """真正的相撞：**同变量 + 同通道**。"""
    (report,) = analyze_conflict(
        [
            m1_intent("I-1", variable="V-SE", channel="measured"),
            m1_intent("I-2", variable="V-SE", channel="measured", score=9.0),
        ]
    )
    assert report.slot == "M1.V-SE/measured"
    assert set(report.intent_ids) == {"I-1", "I-2"}


def test_same_preference_slot_is_a_conflict() -> None:
    """两条偏好写**同一个装配槽位**才是相撞（后者会把前者归档）。"""
    (report,) = analyze_conflict(
        [
            pref_intent("I-1", target_field="SP1.presentation"),
            pref_intent("I-2", target_field="SP1.presentation"),
        ]
    )
    assert report.slot == "M2.preferences.SP1.presentation"


def test_different_preference_slots_do_not_collide() -> None:
    assert (
        analyze_conflict(
            [
                pref_intent("I-1", target_field="SP1.presentation"),
                pref_intent("I-2", target_field="SP1.activity_mix"),
            ]
        )
        == []
    )


def test_no_change_never_collides() -> None:
    """`no_change` 不写任何东西，因此谈不上槽位。"""
    assert (
        analyze_conflict(
            [
                m1_intent("I-1", action="no_change"),
                m1_intent("I-2", action="no_change"),
            ]
        )
        == []
    )


def test_the_slot_key_accepts_both_dict_and_model_values() -> None:
    """`value` 在 HTTP 侧是 dict、在 Router 内部可能是模型 —— 两者都要认。

    这条防的是「冲突判定随调用路径不同而不同」：那会让同一个请求
    经由 API 与经由内部调用得到不同的结果。
    """
    from app.router.contracts import M1ParameterValue

    as_dict = m1_intent("I-1", variable="V-SE", channel="measured")
    as_model = as_dict.model_copy(
        update={"value": M1ParameterValue(**as_dict.value)}
    )
    assert write_slot(as_dict) == write_slot(as_model) == "M1.V-SE/measured"


def test_a_slot_less_m1_value_does_not_fabricate_a_conflict() -> None:
    """取不到通道时**不报冲突** —— 误报会丢数据，漏报最多多一个版本。

    （结构校验会先拒掉这种 intent；这里是万一漏过来时的降级方向。
    方向是刻意选的：误报 = 静默丢一条写入，漏报 = 历史里多一行看得见的版本。）
    """
    slotless = intent("I-1", target="M1.V-SE", value=None, basis="measured")
    assert write_slot(slotless) is None


# --------------------------------------------------------------------------- #
# 同槽位相撞：档位不同 → 裁定；档位相同 → 拒绝
# --------------------------------------------------------------------------- #


def test_analyze_conflict_prefers_the_higher_rank() -> None:
    reports = analyze_conflict(
        [
            m1_intent("I-low", variable="V-SE", basis="experienced"),
            m1_intent("I-high", variable="V-SE", basis="measured"),
        ]
    )
    (report,) = reports
    assert report.winner_intent_id == "I-high"
    assert losers_of(report) == ("I-low",)
    assert not report.is_tie()


def test_tied_ranks_have_no_winner_at_all() -> None:
    """**同档位 = 无法裁定**，绝不按 `intent_id` 字典序挑一个。

    原先的实现会挑 `"I-1"` 胜出，然后报告文案写「有**更高优先级**的写入」——
    可两条的档位完全相同。于是系统**用一个不成立的理由解释了一次静默的数据丢失**，
    而「你的数据变成什么样」取决于一个字符串的字典序。
    """
    (report,) = analyze_conflict(
        [
            m1_intent("I-1", variable="V-SE", channel="measured"),
            m1_intent("I-2", variable="V-SE", channel="measured", score=9.0),
        ]
    )
    assert report.is_tie()
    assert report.winner_intent_id is None
    assert losers_of(report) == (), "平局没有落败者 —— 整组都要拒，不是挑一个输"
    assert set(rejected_of(report)) == {"I-1", "I-2"}
    assert "无法裁定" in report.note
    assert "更高优先级" not in report.note, "不能说有更高优先级，因为根本没有"


def test_ranking_is_independent_of_input_order() -> None:
    """真冲突下的胜者必须与输入顺序无关 —— 否则同一请求跑两次结果不同。

    （原先这条测试用两条**同档位偏好**来验，而那本身不是冲突。
    现在改用真冲突：measured 对 experienced，谁在前都是 measured 赢。）
    """
    group = [
        m1_intent("I-low", variable="V-SE", basis="experienced"),
        m1_intent("I-high", variable="V-SE", basis="measured"),
    ]
    forward = analyze_conflict(group)
    backward = analyze_conflict(list(reversed(group)))

    assert [r.winner_intent_id for r in forward] == [
        r.winner_intent_id for r in backward
    ]
    assert forward[0].winner_intent_id == "I-high"


def test_report_names_the_order_and_explains_itself() -> None:
    (report,) = analyze_conflict(
        [
            m1_intent("I-low", variable="V-SE", basis="experienced"),
            m1_intent("I-high", variable="V-SE", basis="measured"),
        ]
    )
    assert report.order_name == conflict.ARBITRATION_ORDER_NAME
    assert "skipped" in report.note
    assert "丢弃" in report.note
    # 报告要说出**真实**的槽位与**真实**的胜者档位，而不是含糊的 target。
    assert report.slot == "M1.V-SE/measured"
    assert "measured 基线" in report.note


def test_analyze_conflict_does_not_mutate_its_input() -> None:
    group = [
        m1_intent("I-low", variable="V-SE", basis="experienced"),
        m1_intent("I-high", variable="V-SE", basis="measured"),
    ]
    before = [item.model_dump() for item in group]
    analyze_conflict(group)
    assert [item.model_dump() for item in group] == before


def test_context_adoption_order_lives_elsewhere() -> None:
    """「上下文采用序」不属于本模块 —— 两个序绝不可合并（M0 §7.4）。

    这里只有写入裁定序。若哪天有人把上下文采用（用户偏好 > stage > 基线/证据 > 策略）
    也塞进这个模块，`conflict` 就会出现两个同名却语义相反的排序函数，
    而 Router 与 Agent 会开始互相污染对方的判断。
    """
    public = {
        name
        for name in dir(conflict)
        if not name.startswith("_") and callable(getattr(conflict, name))
    }
    assert "resolve_context_order" not in public
    assert "adoption_rank" not in public
    assert "stage_rank" not in public
