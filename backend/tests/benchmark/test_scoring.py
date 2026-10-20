"""计分器与要点判官（P9）。

这一层全是**纯函数**，而纯函数最容易被人当成"没什么好测的"。
但这两块恰恰是**错了也发现不了**的那种代码：

- 一次 60 分钟的全面卷只产生一个分数，**无法再跑一次来查**（人已经学会了）；
- 判官错的默认方向是**放行**（把没答到的算成答到）；
- 聚合把"没测到"算成 0 分时，分数只是**低了一点**，看起来仍然正常。

所以这里的每一条测试针对的都是一种**静默错误**，而不是"分支覆盖率"。
"""

from __future__ import annotations

import pytest

from app.benchmark.judge import (
    ANSWER_MARKER,
    JUDGE_PROMPT_MARKER,
    JudgeHit,
    JudgeVerdict,
    build_prompt,
    judge_key_points,
    parse_prompt,
    parse_verdict,
    verdict_note,
)
from app.benchmark.questions import (
    KIND_EXACT,
    KIND_EXACT_BLANK,
    KIND_KEY_POINTS,
    KIND_OBSERVATION,
    KIND_SELF_REPORT,
    SelfReportQuestion,
    SpecError,
    load_self_report,
)
from app.benchmark.scoring import (
    IMPLICIT_PARAMETERS,
    MIN_ITEMS_DEFAULT,
    READING_KINDS,
    ChannelAggregate,
    ObservationBand,
    ObservationTable,
    ScoringError,
    aggregate_method,
    compose_channel,
    counts_toward_reading,
    decide_confidence,
    observation_value,
    score_exact,
    score_exact_blank,
    score_key_points,
    score_observation,
    score_self_report,
)

# --------------------------------------------------------------------------- #
# 客观题
# --------------------------------------------------------------------------- #


def test_exact_right_and_wrong() -> None:
    assert score_exact("阿尔文号", "阿尔文号").item_score == 10.0
    assert score_exact("的里雅斯特号", "阿尔文号").item_score == 0.0


def test_exact_ignores_whitespace_and_typography() -> None:
    """空格是输入习惯，破折号/引号是输入法差异 —— 都不是知识差异。"""
    assert score_exact(" 阿尔文号 ", "阿尔文号").item_score == 10.0
    assert score_exact("2-3", "2–3").item_score == 10.0


def test_exact_still_fails_on_a_different_word() -> None:
    """★ 放宽排版不等于放宽用词 —— 这条是上面那条的边界。"""
    assert score_exact("阿尔文二号", "阿尔文号").item_score == 0.0


def test_exact_is_marked_as_a_ruler_not_a_reading() -> None:
    """★ 红线 7：正误题只做等值化标尺，不产出 `measured` 读数。"""
    result = score_exact("a", "a")
    assert result.counts_toward_reading is False
    assert result.counts_toward_reading == counts_toward_reading("exact")
    assert result.scoring_method == "code:exact"


def test_exact_blank_scores_by_blank_not_all_or_nothing() -> None:
    """★ 一题两空按「对=10/错=0」给分时，"两空全对"与"只对一空"拿一样的分 ——
    分数失去分辨力，而**这个损失不会报错**。"""
    assert score_exact_blank("350；2-3", "350；2–3").item_score == 10.0
    assert score_exact_blank("350；9", "350；2–3").item_score == 5.0
    assert score_exact_blank("9；9", "350；2–3").item_score == 0.0


def test_exact_blank_records_the_rule_with_its_numbers() -> None:
    result = score_exact_blank("350；9", "350；2–3")
    assert result.scoring_method == "code:exact_blank(1/2 空)"
    assert result.judge_note and "1/2" in result.judge_note


def test_exact_blank_refuses_an_answer_it_cannot_split() -> None:
    with pytest.raises(ScoringError):
        score_exact_blank("随便", "   ")


# --------------------------------------------------------------------------- #
# 判官：解析 + 引文核对
# --------------------------------------------------------------------------- #


GUIDE = ["主线是A", "关键事实是B", "限定条件是C"]
ANSWER = "这篇材料的主线是A，关键事实是B。另外还有别的细节。"


def _verdict(raw: str) -> JudgeVerdict:
    return parse_verdict(raw, guide_size=len(GUIDE), answer=ANSWER)


def test_a_hit_with_a_real_quote_is_verified() -> None:
    verdict = _verdict('{"hits": [{"no": 1, "quote": "主线是A"}]}')
    assert verdict.ok
    assert [hit.no for hit in verdict.verified_hits] == [1]


def test_a_hit_with_a_fabricated_quote_is_downgraded() -> None:
    """★ 这是整个判分设计的目的：**编一段引用换不到分**。"""
    verdict = _verdict('{"hits": [{"no": 2, "quote": "关键事实是B而且我还写了别的"}]}')
    assert verdict.ok
    assert verdict.verified_hits == ()
    assert len(verdict.downgraded) == 1
    assert "找不到" in verdict.downgraded[0].why


def test_a_very_short_quote_is_refused() -> None:
    """一两个字的"引文"在任何答案里都找得到 —— 下限不是严谨，是能不能成立。"""
    verdict = _verdict('{"hits": [{"no": 1, "quote": "A"}]}')
    assert verdict.verified_hits == ()
    assert len(verdict.downgraded) == 1


def test_a_quote_with_different_whitespace_still_counts() -> None:
    verdict = _verdict('{"hits": [{"no": 1, "quote": "主线是 A"}]}')
    assert [hit.no for hit in verdict.verified_hits] == [1]


def test_hits_outside_the_guide_are_recorded_as_inventing_points() -> None:
    """模型自己新增一个要点 —— 它不在指南里，所以不能计分，而且要留证。"""
    verdict = _verdict('{"hits": [{"no": 9, "quote": "主线是A"}]}')
    assert verdict.ok
    assert verdict.verified_hits == ()
    assert verdict.out_of_range == (9,)
    assert "在编要点" in verdict_note(verdict)


def test_the_same_point_twice_is_counted_once() -> None:
    """重复报同一条要点：分母是**要点总数**，不是"模型报了几条"。"""
    verdict = _verdict(
        '{"hits": [{"no": 1, "quote": "主线是A"}, {"no": 1, "quote": "关键事实是B"}]}'
    )
    assert [hit.no for hit in verdict.verified_hits] == [1]


def test_a_broken_judge_is_not_zero_hits() -> None:
    """★ 判官坏了 ≠ 0 分。混起来会让一次模型故障把用户的分数打成 0。"""
    verdict = _verdict("我觉得答得挺好的")
    assert verdict.ok is False
    assert verdict.hits == ()
    assert "没有给出 JSON" in verdict.error


def test_malformed_json_is_also_a_broken_judge() -> None:
    assert _verdict('{"hits": [{"no": 1, "quote": ').ok is False
    assert _verdict('{"hits": "all"}').ok is False
    assert _verdict('{"hits": []}').ok is True


def test_json_is_extracted_even_with_chatter_around_it() -> None:
    """真实模型经常先说一句「好的，我来判断」——为此让整次判分失败不划算。"""
    verdict = _verdict('好的，我来判断。\n{"hits": [{"no": 1, "quote": "主线是A"}]}\n以上。')
    assert verdict.ok
    assert [hit.no for hit in verdict.verified_hits] == [1]


def test_the_judge_callback_failure_becomes_a_broken_judge() -> None:
    def boom(_prompt: str) -> tuple[str, str | None]:
        raise RuntimeError("连不上模型")

    verdict = judge_key_points(GUIDE, ANSWER, ask=boom)
    assert verdict.ok is False
    assert "连不上模型" in verdict.error


def test_the_prompt_numbers_the_guide_and_asks_for_json() -> None:
    prompt = build_prompt(GUIDE, ANSWER)
    assert "1. 主线是A" in prompt and "3. 限定条件是C" in prompt
    assert "共 3 条" in prompt
    assert "hits" in prompt


# --------------------------------------------------------------------------- #
# 提示词的逆（替身与测试用）
# --------------------------------------------------------------------------- #


def test_the_prompt_round_trips() -> None:
    """`parse_prompt` 是 `build_prompt` 的逆 —— 这条把它们钉在一起。

    ★ 没有这条，改一处分隔符就会让**判官替身**静默地认不出判官调用，
    于是它去回放讨论剧本、给出一个和判分毫无关系的回复 ——
    而那时"分数不对"会被归因到判分规则上。
    """
    guide, answer = parse_prompt(build_prompt(GUIDE, ANSWER))
    assert guide == GUIDE
    assert answer == ANSWER


def test_a_prompt_with_no_guide_is_not_a_judge_call() -> None:
    assert parse_prompt("帮我改一下计划") is None
    assert parse_prompt(f"{JUDGE_PROMPT_MARKER}（共 0 条）\n\n{ANSWER_MARKER}\n随便") is None
    assert parse_prompt(f"{JUDGE_PROMPT_MARKER}（共 1 条）\n1. 甲\n没有答案标记") is None


def test_an_answer_containing_the_marker_does_not_break_the_split() -> None:
    """★ 他的答案里如果恰好出现 `## 你的判断`（比如他在引用提示词），
    切割必须只认**第一个**分隔符 —— 否则答案会被截断，
    而截断之后引文核对会把**真实的引用**判成找不到（降级方向是"冤枉他"）。"""
    tricky = f"我在材料里看到这样一段：{ANSWER_MARKER}\n然后我答了一点。"
    guide, answer = parse_prompt(build_prompt(GUIDE, tricky))
    assert guide == GUIDE
    assert answer == tricky


# --------------------------------------------------------------------------- #
# 要点题计分
# --------------------------------------------------------------------------- #


def test_key_points_scores_by_verified_hits_over_the_guide() -> None:
    verdict = _verdict(
        '{"hits": [{"no": 1, "quote": "主线是A"}, '
        '{"no": 2, "quote": "关键事实是B"}, '
        '{"no": 3, "quote": "限定条件我忘了写"}]}'
    )
    result = score_key_points(GUIDE, ANSWER, verdict)
    # 3 条里核实过 2 条 —— 不是 3/3，也不是 0
    assert result.item_score == pytest.approx(6.67, abs=0.01)
    assert "命中 2/3" in result.scoring_method
    assert "引文核对降级 1" in result.scoring_method
    assert result.judged_by == "llm"
    assert result.counts_toward_reading is True


def test_key_points_records_every_hit_with_its_verdict() -> None:
    verdict = _verdict(
        '{"hits": [{"no": 1, "quote": "主线是A"}, {"no": 2, "quote": "编的引用编的引用"}]}'
    )
    result = score_key_points(GUIDE, ANSWER, verdict)
    assert result.judged_hits is not None
    assert [hit["verified"] for hit in result.judged_hits] == [True, False]
    assert result.judged_hits[1]["why"]


def test_key_points_of_a_broken_judge_is_void_not_zero() -> None:
    result = score_key_points(GUIDE, ANSWER, _verdict("不知道"))
    assert result.item_score is None
    assert result.void_reason
    assert "判官" in result.scoring_method


def test_key_points_needs_a_guide() -> None:
    with pytest.raises(ScoringError):
        score_key_points([], ANSWER, _verdict('{"hits": []}'))


# --------------------------------------------------------------------------- #
# 观测题的换算表
# --------------------------------------------------------------------------- #


def _table() -> ObservationTable:
    return ObservationTable(
        table_id="v-hsr-1",
        unit="秒",
        bands=(
            ObservationBand(upper=60, score=10.0, label="很快"),
            ObservationBand(upper=180, score=6.0, label="一般"),
            ObservationBand(upper=None, score=1.0, label="很慢"),
        ),
    )


def test_observation_maps_through_the_declared_table() -> None:
    table = _table()
    assert table.score_of(30).item_score == 10.0
    assert table.score_of(120).item_score == 6.0
    assert table.score_of(999).item_score == 1.0


def test_observation_records_the_table_version() -> None:
    """★ 半年后回看一个 8 分，必须能回答"当时那张表把 180 秒映射成了几分"。"""
    result = _table().score_of(30)
    assert "v-hsr-1" in result.scoring_method
    assert "很快" in result.scoring_method


def test_a_void_observation_is_not_zero() -> None:
    """★ 「他没有卡住」与「他求助得很差」是两件事。

    混起来会让一次**太容易**的任务静默地压低分数 —— 而它看起来只是一个低分。
    """
    result = _table().score_of(None)
    assert result.item_score is None
    assert "未测到" in result.scoring_method
    assert "没有卡点" in result.judge_note
    assert result.void_reason


def test_the_two_void_causes_read_differently() -> None:
    """★ 「没卡住」与「没记到」都会让这题没有分数，但**不能是同一句话**。

    混成一句，事后就分不清"是他太强"还是"我们忘了记"——
    而这两件事对同一个分数的解释完全相反。
    """
    not_stuck = _table().score_of(None)
    not_recorded = _table().score_of(None, missing="这次没记到 `switch_count`（切换次数）")
    assert not_stuck.void_reason != not_recorded.void_reason
    assert "没记到" in not_recorded.void_reason


def test_a_table_without_a_catch_all_band_refuses() -> None:
    """最后一档没有兜底时，落表外的值会变成一个**没人发现的默认分**。"""
    table = ObservationTable(
        table_id="bad", unit="秒", bands=(ObservationBand(upper=60, score=10.0, label="快"),)
    )
    with pytest.raises(ScoringError):
        table.score_of(999)


# --------------------------------------------------------------------------- #
# 隐性参数：记录，但不自动计分（P11）
# --------------------------------------------------------------------------- #


def test_implicit_parameters_are_recorded_but_not_scored_by_default() -> None:
    """★ 一个隐性参数**没有声明换算表时不给分**。

    一个量该换算成几分取决于**当时的仪器**：在 4 档量表上切换两次、
    与在 10 档量表上切换两次，不是同一件事。所以这里没有任何默认换算 ——
    想要分数就在题目里声明换算表。替一门还没定义的测量学做决定，
    比不给分坏得多。
    """
    assert "switch_count" in IMPLICIT_PARAMETERS
    # 换算表的 unit 不是隐性参数时，它去读参考答案里的数字
    table = ObservationTable(
        table_id="t", unit="秒", bands=(ObservationBand(upper=None, score=5.0, label="兜底"),)
    )
    value, how = observation_value(table, raw_answer="180 秒", implicit={"switch_count": 3})
    assert value == 180.0 and "秒" in how


def test_an_implicit_parameter_can_feed_the_declared_table() -> None:
    """★ 隐性参数要能用起来，走的是**已有的那条路**：题目声明换算表，`unit` 写参数名。

    这样就不需要为"犹豫该扣几分"另发明一套公式 ——
    换算表是明示的、带版本号的、事后能回答"当时那张表把 3 次映射成了几分"。
    """
    table = ObservationTable(
        table_id="sw-1",
        unit="switch_count",
        bands=(
            ObservationBand(upper=1, score=10.0, label="一次就定"),
            ObservationBand(upper=None, score=4.0, label="反复横跳"),
        ),
    )
    result = score_observation(table, raw_answer="", implicit={"switch_count": 5})
    assert result.item_score == 4.0
    assert "sw-1" in result.scoring_method and "反复横跳" in result.scoring_method


def test_a_missing_implicit_parameter_voids_the_item() -> None:
    """★ 没记到 ≠ 0 分（"他没犹豫" 与 "我们忘了记犹豫" 是两件事）。"""
    table = ObservationTable(
        table_id="hs-1",
        unit="hesitation_sec",
        bands=(ObservationBand(upper=None, score=3.0, label="兜底"),),
    )
    result = score_observation(table, raw_answer="", implicit={"hesitation_sec": None})
    assert result.item_score is None
    assert "没记到" in result.void_reason
    assert "hesitation_sec" in result.void_reason


def test_a_raw_answer_without_a_single_number_is_void() -> None:
    """★ 读不出**唯一**一个数就作废：`"3 到 5 次"` 取第一个数会静默地编一个量。"""
    table = ObservationTable(
        table_id="t", unit="次", bands=(ObservationBand(upper=None, score=5.0, label="兜底"),)
    )
    for text in ("", "很多次", "3 到 5 次", "-"):
        result = score_observation(table, raw_answer=text)
        assert result.item_score is None, text
        assert result.void_reason, text
    assert score_observation(table, raw_answer="约 3 次").item_score == 5.0


# --------------------------------------------------------------------------- #
# 自评量表（P11）
# --------------------------------------------------------------------------- #


def _self_report() -> SelfReportQuestion:
    """一份三句的量表：两句正向、一句反向。"""
    return load_self_report(
        {
            "prompt": "下面几句话，哪些像你？",
            "options": [
                {"value": "never", "label": "从不", "weight": 0.0},
                {"value": "sometimes", "label": "偶尔", "weight": 0.34},
                {"value": "often", "label": "经常", "weight": 0.67},
                {"value": "always", "label": "总是", "weight": 1.0},
            ],
            "statements": [
                {"no": 1, "text": "读完密集材料后我能说出主线"},
                {"no": 2, "text": "我能抓住容易被忽略的限定条件"},
                {"no": 3, "text": "我常常读到一半才发现没抓住重点", "reverse": True},
            ],
        },
        where="S-1.json",
    )


def test_a_self_report_scale_is_the_declared_option_weights() -> None:
    """★ 选项权重由**题目**声明 —— 它是测量刻度，不由模型或界面临时定。"""
    result = score_self_report(_self_report(), "1=always；2=always；3=never")
    assert result.item_score == 10.0
    assert result.judged_by == "code"
    assert "self_report" in result.scoring_method


def test_a_reverse_statement_subtracts_instead_of_adding() -> None:
    """★ 反向句：`1 - weight`。

    全是正向句时，"看到就选最右边那个"这种作答习惯会**直接抬高分数** ——
    而那不是他的倾向，是他填表的习惯。所以 `3=never` 在这一句上是最高的。
    """
    forwards = score_self_report(_self_report(), "1=never；2=never；3=always")
    assert forwards.item_score == 0.0
    mixed = score_self_report(_self_report(), "1=always；2=never；3=never")
    assert mixed.item_score == round(10 * (1.0 + 0.0 + 1.0) / 3, 2)


def test_the_zero_and_full_ends_are_reachable() -> None:
    """★ 权重必须顶到两端，否则"从来没有"的人拿不到 0 分、天花板也不会有 10。

    这个损失不会报错 —— 分数照样落在 0-10 里，只是永远到不了边。
    """
    question = _self_report()
    assert min(option.weight for option in question.options) == 0.0
    assert max(option.weight for option in question.options) == 1.0


def test_a_scale_whose_weights_do_not_follow_the_declared_order_is_refused() -> None:
    """声明顺序必须就是分值顺序 —— 否则换个显示顺序就换个分数。"""
    with pytest.raises(SpecError, match="严格递增"):
        load_self_report(
            {
                "prompt": "p",
                "options": [
                    {"value": "a", "label": "A", "weight": 0.5},
                    {"value": "b", "label": "B", "weight": 0.0},
                    {"value": "c", "label": "C", "weight": 1.0},
                ],
                "statements": [{"no": 1, "text": "t"}],
            },
            where="x.json",
        )


def test_a_scale_that_does_not_reach_both_ends_is_refused() -> None:
    with pytest.raises(SpecError, match="顶到两端"):
        load_self_report(
            {
                "prompt": "p",
                "options": [
                    {"value": "a", "label": "A", "weight": 0.34},
                    {"value": "b", "label": "B", "weight": 0.67},
                ],
                "statements": [{"no": 1, "text": "t"}],
            },
            where="x.json",
        )


def test_a_scale_with_one_option_has_no_resolution() -> None:
    with pytest.raises(SpecError, match="至少要两个选项"):
        load_self_report(
            {
                "prompt": "p",
                "options": [{"value": "a", "label": "A", "weight": 0.0}],
                "statements": [{"no": 1, "text": "t"}],
            },
            where="x.json",
        )


def test_a_partial_self_report_is_void_not_average_of_what_was_answered() -> None:
    """★ 少答一句**不是随机的**（跳过通常意味着那句不好答）。

    按答了的那几句求均值会得到一个**系统偏移**的分数，而它看起来完全正常。
    少答就是没测到。
    """
    result = score_self_report(_self_report(), "1=always；2=always")
    assert result.item_score is None
    assert "第 3 句没答" in result.void_reason


def test_an_unknown_option_is_void_and_names_the_allowed_values() -> None:
    """★ 认不出的选项说明**量表与作答不是同一版**（选项改过，或串了另一份量表）——
    那是一个应当被看见的上游问题，不是一次 0 分作答。
    """
    result = score_self_report(_self_report(), "1=always；2=seldom；3=never")
    assert result.item_score is None
    assert "seldom" in result.void_reason
    assert "always" in result.void_reason  # 可选值列表


def test_a_statement_answered_twice_is_void() -> None:
    result = score_self_report(_self_report(), "1=always；1=never；2=never；3=never")
    assert result.item_score is None
    assert "两次" in result.void_reason


def test_an_out_of_range_statement_number_is_void() -> None:
    result = score_self_report(_self_report(), "1=always；2=always；3=never；9=always")
    assert result.item_score is None
    assert "第 9 句" in result.void_reason


def test_an_empty_self_report_is_void() -> None:
    result = score_self_report(_self_report(), "   ")
    assert result.item_score is None
    assert "一个字都没答" in result.void_reason


def test_an_item_without_a_declared_scale_refuses() -> None:
    """★ 没声明量表 → **报错**，不是"当作空量表给 0 分"。"""
    with pytest.raises(ScoringError, match="没有声明自评量表"):
        score_self_report(None, "1=always")


def test_self_report_counts_toward_reading_but_objective_questions_do_not() -> None:
    """★ 红线 7 针对的是**正误题**，不是自评。

    自评进的是 `stated` 通道，问的是"你平时怎样"，不随能力移动。
    把它也排除掉，`stated` 通道会永远拿不到任何读数 ——
    而那看起来只是"还没测"。
    """
    assert counts_toward_reading("self_report") is True
    assert counts_toward_reading("key_points") is True
    assert counts_toward_reading("observation") is True
    assert counts_toward_reading("exact") is False
    assert counts_toward_reading("exact_blank") is False


def test_the_reading_kinds_match_the_declared_kind_constants() -> None:
    """题型常量与 `READING_KINDS` 必须是**同一批字符串**。

    两处各写一遍的话，改一处之后另一处会安静地失配 ——
    表现是某个题型的分**有时算读数、有时不算**。
    """
    assert READING_KINDS == {KIND_KEY_POINTS, KIND_OBSERVATION, KIND_SELF_REPORT}


def test_the_kind_constants_match_the_response_enum() -> None:
    """★ 题库规格里的题型字符串必须与**作答记录**上的枚举一一对应。

    `app.benchmark` 不能 import `app.global_domain`（那是写入侧，
    方向上高一层），所以两边各有一份字符串常量 —— 那就必须有东西把它们钉住。
    失配的表现是：一道自评题的作答被记成 `kind="self_report"`，
    而计分侧不认识它，于是它**静默地不产出读数**。
    """
    from app.global_domain.enums import ResponseQuestionKind

    assert {kind.value for kind in ResponseQuestionKind} == {
        KIND_EXACT,
        KIND_EXACT_BLANK,
        KIND_KEY_POINTS,
        KIND_OBSERVATION,
        KIND_SELF_REPORT,
    }


def test_every_implicit_parameter_is_a_column_on_the_response() -> None:
    """★ 隐性参数表与 `benchmark_response` 的列**必须一一对应**。

    两处各写一遍的话，改列名之后这里会安静地读到一个永远为 `None` 的值 ——
    而那表现为"这条观测点总是没记到"，看起来像数据没采上，不像代码错了。
    """
    from app.global_domain.models import BenchmarkResponse

    columns = set(BenchmarkResponse.__table__.columns.keys())
    assert set(IMPLICIT_PARAMETERS) <= columns, set(IMPLICIT_PARAMETERS) - columns



# --------------------------------------------------------------------------- #
# 方法内聚合
# --------------------------------------------------------------------------- #


def _kp(hits: int, total: int = len(GUIDE)) -> object:
    """造一个要点题的计分结果：`hits` 条核实过的命中，`total` 条要点。"""
    return score_key_points(
        GUIDE[:total],
        ANSWER,
        JudgeVerdict(
            ok=True,
            hits=tuple(
                JudgeHit(no=index, quote="主线是A", verified=True)
                for index in range(1, hits + 1)
            ),
        ),
    )


def test_aggregate_weights_by_difficulty() -> None:
    """一道难题顶两道易题 —— 难度加权不是装饰，它决定换题会不会挪动分数。"""
    counted = [
        (score_exact("a", "a"), "易"),
        (score_exact("a", "b"), "难"),
    ]
    # 全是标尺题 → 没有读数可用
    assert aggregate_method(counted).method_score is None
    readable = [
        (_kp(0), "易"),
        (_kp(1), "中"),
        (_kp(2), "难"),
    ]
    result = aggregate_method(readable)
    assert result.method_score is not None
    # 0（易，1.0）+ 3.33（中，1.4）+ 6.67（难，2.0）
    # → (0*1.0 + 3.33*1.4 + 6.67*2.0) / 4.4
    assert result.method_score == pytest.approx(4.09, abs=0.03)
    assert result.used_count == 3


def test_aggregate_says_why_a_score_is_missing() -> None:
    """★ 一个没有方法分的运行必须能解释自己 —— 否则"0 道可用于读数的题"
    会被读成"他没有作答"。"""
    result = aggregate_method([(score_exact("a", "a"), "易")])
    assert result.method_score is None
    assert result.ruler_count == 1
    assert "标尺题" in result.detail
    assert "红线 7" in result.detail


def test_void_items_are_neither_numerator_nor_denominator() -> None:
    """★ 作废题必须**完全不参与** —— 把它当 0 分算会把一次没测到的观测
    变成一个真实的低分。"""
    void = ObservationTable("t", "秒", (ObservationBand(None, 5.0, "兜底"),)).score_of(None)
    good = score_key_points(
        GUIDE,
        ANSWER,
        JudgeVerdict(
            ok=True,
            hits=(
                JudgeHit(1, "主线是A", True),
                JudgeHit(2, "关键事实是B", True),
                JudgeHit(3, "限定条件我忘了", False),
            ),
        ),
    )
    mixed = aggregate_method([(good, "易"), (void, "易")])
    alone = aggregate_method([(good, "易")])
    assert mixed.method_score == alone.method_score
    assert mixed.void_count == 1


def test_a_below_minimum_run_gets_no_score() -> None:
    """低于最小题数时给它一个数，那个数看起来像读数、其实只是噪声。"""
    one = [
        (
            score_key_points(
                GUIDE,
                ANSWER,
                JudgeVerdict(ok=True, hits=(JudgeHit(1, "主线是A", True),)),
            ),
            "易",
        )
    ]
    result = aggregate_method(one)
    assert result.method_score is None
    assert result.completeness == "partial"
    assert str(MIN_ITEMS_DEFAULT) in result.detail


def test_a_bad_difficulty_is_refused_not_defaulted() -> None:
    """难度不在权重表里时**拒绝** —— 猜一个权重会让分数悄悄错。"""
    with pytest.raises(ScoringError):
        aggregate_method([(_kp(1), "很难")] * MIN_ITEMS_DEFAULT)


def test_a_ruler_with_a_bad_difficulty_is_never_looked_up() -> None:
    """★ 标尺题连难度都不查 —— 它根本不进聚合。

    这不是漏判，是**顺序**：先按"参不参与读数"筛，再查权重。
    反过来（先查权重）会让一道难度写错的标尺题把整次计分打挂，
    而它本来对这个分数没有任何影响。
    """
    assert aggregate_method([(score_exact("a", "a"), "很难")]).method_score is None


# --------------------------------------------------------------------------- #
# 通道内合成
# --------------------------------------------------------------------------- #


def test_channel_composes_by_priority_weight() -> None:
    result = compose_channel([("M1", 10.0, "P1"), ("M2", 0.0, "P3")])
    assert result.channel_score == pytest.approx((10.0 * 1.0 + 0.0 * 0.4) / 1.4, abs=0.01)
    assert result.used_methods == ("M1", "M2")


def test_channel_excludes_methods_that_have_no_score_and_says_so() -> None:
    result = compose_channel([("M1", 8.0, "P1"), ("M2", None, "P1")])
    assert result.channel_score == 8.0
    assert result.excluded_methods == ("M2",)
    assert "M2" in result.detail


def test_a_channel_with_no_usable_method_is_null_not_zero() -> None:
    result = compose_channel([("M1", None, "P1")])
    assert result.channel_score is None
    assert "不是 0" in result.detail


def test_an_unknown_priority_is_refused() -> None:
    with pytest.raises(ScoringError):
        compose_channel([("M1", 8.0, "P9")])


# --------------------------------------------------------------------------- #
# 置信判定
# --------------------------------------------------------------------------- #


def test_confidence_can_actually_fail_to_reach_calibrated() -> None:
    """★ 一个三档标签如果永远只能取最高档，它就不是标签，是装饰。"""
    assert decide_confidence(
        has_stated=False, measured_used=0, anchor_count=0,
        all_objective=True, has_observation=False,
    )[0] == "hypothesis"
    assert decide_confidence(
        has_stated=False, measured_used=3, anchor_count=1,
        all_objective=True, has_observation=False,
    )[0] == "provisional"
    assert decide_confidence(
        has_stated=False, measured_used=3, anchor_count=2,
        all_objective=True, has_observation=False,
    )[0] == "calibrated"


def test_an_observation_item_caps_confidence_below_calibrated() -> None:
    """换算表是人定的档 —— 它参与读数，但读数依赖于一个人为决定。"""
    value, why = decide_confidence(
        has_stated=False, measured_used=3, anchor_count=2,
        all_objective=True, has_observation=True,
    )
    assert value == "provisional"
    assert "换算表" in why


def test_every_confidence_comes_with_a_reason() -> None:
    """一个没有理由的置信档会被读成"系统在谦虚"或"在吹牛"。"""
    for kwargs in (
        dict(has_stated=True, measured_used=0, anchor_count=0, all_objective=True, has_observation=False),
        dict(has_stated=False, measured_used=5, anchor_count=0, all_objective=True, has_observation=False),
        dict(has_stated=False, measured_used=5, anchor_count=2, all_objective=False, has_observation=False),
    ):
        value, why = decide_confidence(**kwargs)
        assert value in ("hypothesis", "provisional", "calibrated")
        assert why
