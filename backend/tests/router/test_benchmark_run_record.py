"""基准运行记录的落库测试（裁定 G-03 = 甲）。

这些测试守的不是"能写进去"，而是**裁决 G-03 买下来的那件事**：

> 一个落库了的 0-10 分数就是一句关于用户的断言，
> 而它背后那个理由（答案）如果不落库，那句话就无法被检查。

所以本文件的中心用例是 `test_a_baseline_score_can_be_traced_back_to_the_answers`
—— 它从一条写进 `m1_baseline_version` 的分数出发，只凭 `source_ref`
一路走回到用户当时敲的原文。**那条路径断了，这张表就没有存在理由。**
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.global_domain.enums import (
    BenchmarkForm,
    Completeness,
    EvidenceChannel,
    JudgedBy,
    ResponseQuestionKind,
)
from app.global_domain.models import BenchmarkResponse, BenchmarkRun, M1BaselineVersion
from app.global_domain.reads import (
    anchor_baseline_for_item,
    get_benchmark_run,
    latest_finished_benchmark_run,
    list_benchmark_responses,
)
from app.router import Router


def _start(router: Router, **overrides) -> str:
    """开一次运行。默认值都是一次"最小可用"的运行。"""
    kwargs: dict = {
        "form": BenchmarkForm.QUICK,
        "variable_id": "V-SE",
        "channel": EvidenceChannel.MEASURED,
        "method_id": "M-SE-04",
        "item_ids": ["SE-TXT-001", "SE-TXT-002"],
        "seed": 20260101,
        "topic_snapshot": [
            {
                "item_id": "SE-TXT-001",
                "topics": ["BIO-MARINE"],
                "familiar_at_run": False,
            },
            {
                "item_id": "SE-TXT-002",
                "topics": ["PHYS-STELLAR"],
                "familiar_at_run": False,
            },
        ],
        "completeness": Completeness.COMPLETE,
        "app_version": "0.1",
    }
    kwargs.update(overrides)
    return router.start_benchmark_run(**kwargs)


def _answer(router: Router, run_id: str, **overrides) -> str:
    kwargs: dict = {
        "run_id": run_id,
        "item_id": "SE-TXT-001",
        "question_no": 1,
        "kind": ResponseQuestionKind.EXACT,
        "judged_by": JudgedBy.CODE,
        "raw_answer": "选项 B",
        "item_score": 10.0,
        "difficulty": "中",
        "weight": 1.4,
    }
    kwargs.update(overrides)
    return router.record_benchmark_response(**kwargs)


def _finish(router: Router, run_id: str, **overrides) -> None:
    kwargs: dict = {
        "run_id": run_id,
        "method_score": 6.4,
        "channel_score": 6.4,
        "anchor_baseline": {"SE-TXT-001": 0.62},
        "confidence": None,
        "completeness": Completeness.COMPLETE,
        "boundary": "只用于自己与自己比",
    }
    kwargs.update(overrides)
    router.finish_benchmark_run(**kwargs)


# --------------------------------------------------------------------------- #
# 运行与作答能落下来
# --------------------------------------------------------------------------- #


def test_a_run_gets_an_id_and_persists(router: Router, session: Session) -> None:
    run_id = _start(router)
    assert run_id == "BR-0001"
    row = get_benchmark_run(session, run_id)
    assert row is not None
    assert row.item_ids == ["SE-TXT-001", "SE-TXT-002"]
    assert row.seed == 20260101
    assert row.method_id == "M-SE-04"


def test_responses_are_kept_per_question(router: Router, session: Session) -> None:
    run_id = _start(router)
    _answer(router, run_id, question_no=0, kind=ResponseQuestionKind.KEY_POINTS,
            judged_by=JudgedBy.LLM, raw_answer="他写的三句话", item_score=6.7,
            judge_model="deepseek-chat", judge_note="命中要点 1、3",
            judged_hits=[{"no": 1, "quote": "深海热液喷口"}])
    _answer(router, run_id, question_no=1)
    _answer(router, run_id, question_no=2, item_score=0.0, raw_answer="选项 A")

    rows = list_benchmark_responses(session, run_id)
    assert [r.question_no for r in rows] == [0, 1, 2]
    assert rows[0].judged_by is JudgedBy.LLM
    assert rows[0].judge_model == "deepseek-chat"
    assert rows[0].judged_hits == [{"no": 1, "quote": "深海热液喷口"}]


def test_a_missing_score_is_not_a_zero(router: Router, session: Session) -> None:
    """★ 「没测到」与「答错了」必须能分开。

    把它们混起来，会让一次**太容易或作废**的题静默地压低分数 ——
    而症状是"这个人退步了"，没有人会想到去看某一行是不是 NULL。
    """
    run_id = _start(router)
    _answer(router, run_id, question_no=1, raw_answer="（这题作废）", item_score=None)
    _answer(router, run_id, question_no=2, raw_answer="选项 A", item_score=0.0)

    rows = {(r.item_id, r.question_no): r for r in list_benchmark_responses(session, run_id)}
    assert rows[("SE-TXT-001", 1)].item_score is None
    assert rows[("SE-TXT-001", 1)].raw_answer == "（这题作废）"
    assert rows[("SE-TXT-001", 2)].item_score == 0.0


# --------------------------------------------------------------------------- #
# ★ 中心用例：分数 → 答案
# --------------------------------------------------------------------------- #


def test_a_baseline_score_can_be_traced_back_to_the_answers(
    router: Router, session: Session
) -> None:
    """**裁决 G-03 的验收条件。**

    从 `m1_baseline_version` 里那条分数出发，只凭 `source_ref`，
    必须能走回：这次运行用了哪些题 → 每道题用户答了什么 → 单题分是谁给的。
    """
    run_id = _start(router)
    _answer(router, run_id, question_no=0, kind=ResponseQuestionKind.KEY_POINTS,
            judged_by=JudgedBy.LLM, raw_answer="深海热液喷口靠化能合成……",
            item_score=6.7, judge_model="deepseek-chat")
    _answer(router, run_id, question_no=1, raw_answer="选项 B", item_score=10.0)
    _finish(router, run_id)

    # 走生产同一条路径写一条基线：R2 + measured + source_ref = run_id
    from app.core.ids import next_intent_id
    from app.db import DbSequenceStore
    from app.router import SubmitRequest, UpdateIntent

    intent = UpdateIntent(
        intent_id=next_intent_id(DbSequenceStore(session)),
        action="update_parameter",
        target="M1.V-SE",
        value={
            "channel": "measured",
            "score": 6.4,
            "completeness": "complete",
            "confidence": "calibrated",
            "boundary": None,
        },
        basis="measured",
        reasoning="基准测验：全面卷",
        confidence="high",
    )
    result = router.submit(
        SubmitRequest(trigger_type="R2", intents=[intent], source_ref=run_id)
    )
    assert result.results[0].status.value == "applied", result.results[0].reason

    # ---- 现在只凭这一条基线，往回走 ----
    version = session.scalars(
        select(M1BaselineVersion).where(M1BaselineVersion.variable_id == "V-SE")
    ).one()
    assert version.source_ref == run_id
    assert version.score == pytest.approx(6.4)

    traced = get_benchmark_run(session, version.source_ref)
    assert traced is not None, "分数指向的运行必须还在"
    assert traced.method_id == "M-SE-04"
    assert traced.channel is EvidenceChannel.MEASURED
    assert traced.item_ids == ["SE-TXT-001", "SE-TXT-002"]

    answers = {
        (r.item_id, r.question_no): r
        for r in list_benchmark_responses(session, version.source_ref)
    }
    assert answers[("SE-TXT-001", 0)].raw_answer == "深海热液喷口靠化能合成……"
    assert answers[("SE-TXT-001", 0)].judged_by is JudgedBy.LLM
    assert answers[("SE-TXT-001", 1)].raw_answer == "选项 B"
    assert len(answers) == 2


def test_the_topic_snapshot_does_not_follow_later_edits(
    router: Router, session: Session
) -> None:
    """★ 清单住在库里、且会随用户改（G-04 = 乙），所以**历史必须冻结在运行记录里**。

    反例保护：`familiar_at_run` 如果只是"运行时去查一次当前档案"，
    那么半年后用户改一次清单，历史分数就会看起来是在另一种条件下测出来的
    —— 那等于**改写历史**。这里断言它存的是快照。

    与旧版（`domain_familiarity` 时期）的区别：当时字段是 `familiarity_at_run`
    加一个自由文本 `domain`；现在改成 `familiar_at_run` 加封闭引用的 `topics`
    （裁定 岔路 B = 甲）。
    """
    run_id = _start(router)
    row = get_benchmark_run(session, run_id)
    assert row is not None
    assert row.topic_snapshot[0]["familiar_at_run"] is False
    assert row.topic_snapshot[0]["topics"] == ["BIO-MARINE"]

    # 模拟"用户后来改了清单"：运行记录不受影响
    row.topic_snapshot = [
        dict(d, familiar_at_run=True) for d in row.topic_snapshot
    ]
    session.flush()
    session.expire_all()
    again = get_benchmark_run(session, run_id)
    assert again is not None
    # 这一条断言"改了就是改了"——它证明上面那次修改**确实生效**，
    # 因此前一个 `is False` 不是因为赋值被忽略而通过的。
    assert again.topic_snapshot[0]["familiar_at_run"] is True


# --------------------------------------------------------------------------- #
# 守卫：不许有第二条路径写同一个结果
# --------------------------------------------------------------------------- #


def test_finalizing_twice_is_rejected(router: Router) -> None:
    run_id = _start(router)
    _finish(router, run_id)
    with pytest.raises(ValueError, match="已经结束过"):
        _finish(router, run_id, method_score=9.9, channel_score=9.9)


def test_finalize_rejects_an_unknown_run(router: Router) -> None:
    with pytest.raises(KeyError):
        _finish(router, "BR-9999")


def test_score_out_of_range_is_rejected(router: Router) -> None:
    """越界的分数必须被**数据库**拦下，而不是靠调用方自觉。

    断言包住 `_answer` 而不是一次外部 `session.flush()`：
    写函数自己就会 flush，所以约束是在那一步触发的 ——
    包错地方会让测试通过而约束其实没生效（或反过来，像这次一样失败）。
    """
    run_id = _start(router)
    with pytest.raises(IntegrityError, match="ck_bench_resp_item_score_range"):
        _answer(router, run_id, item_score=11.0)


def test_response_is_unique_per_run_item_question(router: Router) -> None:
    """同一 (run, 题, 题号) 只能有一行。

    断言按**列名**匹配而不是约束名：SQLite 对 CHECK 违规报约束名，
    对 UNIQUE 违规报的是列名 —— 两种不一样，按约束名匹配这里会假失败。
    """
    run_id = _start(router)
    _answer(router, run_id, question_no=1)
    with pytest.raises(IntegrityError, match=r"benchmark_response\.run_id"):
        _answer(router, run_id, question_no=1, raw_answer="又答了一遍")


# --------------------------------------------------------------------------- #
# 只认跑完的运行 / 锚题基准不能自己漂移
# --------------------------------------------------------------------------- #


def test_an_unfinished_run_is_not_the_last_measurement(router: Router) -> None:
    """中断的运行分数是半成品，拿它当"上次测出来多少"会读出假的退步。"""
    finished = _start(router)
    _finish(router, finished)
    _start(router)  # 开了但没结束

    latest = latest_finished_benchmark_run(
        router._session, "V-SE", EvidenceChannel.MEASURED
    )
    assert latest is not None
    assert latest.run_id == finished
    # 反例保护：库里确实有两条运行，否则上面那条断言在只有一条时也会通过
    assert len(list(router._session.scalars(select(BenchmarkRun)))) == 2


def test_anchor_baseline_comes_from_the_earliest_run(router: Router) -> None:
    """★ 用来校正漂移的东西**自己不能漂移**（§5.6）。

    反例保护：两次运行的基准值必须不同，否则"取最早"与"取最新"
    会给出同一个答案，这条测试就成了装饰。
    """
    first = _start(router)
    _finish(router, first, anchor_baseline={"SE-TXT-001": 0.62})
    second = _start(router)
    _finish(router, second, anchor_baseline={"SE-TXT-001": 0.41})

    assert anchor_baseline_for_item(router._session, "SE-TXT-001") == pytest.approx(0.62)
    # 反例：第二次运行自己记的是另一个值
    rows = list(router._session.scalars(select(BenchmarkRun)))
    assert rows[1].anchor_baseline["SE-TXT-001"] == pytest.approx(0.41)


def test_anchor_baseline_is_absent_before_the_first_run(router: Router) -> None:
    assert anchor_baseline_for_item(router._session, "SE-TXT-001") is None
