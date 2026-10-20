"""基准运行的生命周期（用户要求：简版/全面版、可中断保存退出、可继续、可重开）。

这组测试守的是**用户那句话在代码里的样子**：

> 当我们任意时候进入基准测试，我们是可以保存中断退出的。
> 下一次进入时，我们可以继续该次测试，也可以选择重新开始。

## 为什么需要显式状态（这些测试存在的理由）

原本只有 `finished_at IS NULL`。而「还在做」与「我存了盘要走」在那个字段上
**是同一个值** —— 都是 NULL。于是"下次是继续还是重开"没有答案，
产品只能在两种错误里选一种：把想重开的人塞回半截的旧运行，
或者让存了盘的人第二天发现自己白做了一半。

`test_interrupted_and_in_progress_are_distinguishable` 就是钉住这件事的那一条。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.global_domain.enums import (
    BenchmarkForm,
    BenchmarkRunStatus,
    Completeness,
    EvidenceChannel,
    TriggerType,
)
from app.global_domain.models import BenchmarkRun, RouterLog
from app.global_domain.reads import get_benchmark_run, resumable_benchmark_run
from app.router import Router


def _start(router: Router, **overrides) -> str:
    kwargs: dict = {
        "form": BenchmarkForm.QUICK,
        "variable_id": "V-SE",
        "channel": EvidenceChannel.MEASURED,
        "method_id": "M-SE-04",
        "item_ids": ["SE-TXT-001", "SE-TXT-002"],
        "seed": 20260101,
        "topic_snapshot": [
            {"item_id": "SE-TXT-001", "topics": ["BIO-MARINE"], "familiar_at_run": False}
        ],
        "completeness": Completeness.PARTIAL,
    }
    kwargs.update(overrides)
    return router.start_benchmark_run(**kwargs)


def _finish(router: Router, run_id: str) -> None:
    router.finish_benchmark_run(
        run_id=run_id,
        method_score=6.0,
        channel_score=6.0,
        anchor_baseline={},
        confidence=None,
        completeness=Completeness.COMPLETE,
        boundary="只用于自己与自己比",
    )


# --------------------------------------------------------------------------- #
# 起点与"进行中"
# --------------------------------------------------------------------------- #


def test_a_new_run_starts_in_progress_with_a_history_entry(
    router: Router, session: Session
) -> None:
    run_id = _start(router)
    row = get_benchmark_run(session, run_id)
    assert row is not None
    assert row.status is BenchmarkRunStatus.IN_PROGRESS
    assert row.finished_at is None
    # 历史从第一行就有 —— 否则"状态史"在读回时会有两种形状（空 / 有内容），
    # 而调用方总得为"空"写一个特例。
    assert [h["status"] for h in row.status_history] == ["in_progress"]


def test_each_run_can_carry_a_different_form(router: Router, session: Session) -> None:
    """简短版与全面版是**两种仪器**，不是难易两档 —— 都要能开。"""
    quick = _start(router, form=BenchmarkForm.QUICK)
    full = _start(router, form=BenchmarkForm.FULL)
    assert get_benchmark_run(session, quick).form is BenchmarkForm.QUICK
    assert get_benchmark_run(session, full).form is BenchmarkForm.FULL


# --------------------------------------------------------------------------- #
# ★ 中断与进行中必须可区分
# --------------------------------------------------------------------------- #


def test_interrupted_and_in_progress_are_distinguishable(
    router: Router, session: Session
) -> None:
    """★ 本文件的中心用例。

    两者在 `finished_at` 上**是同一个值**（都是 NULL）。
    如果实现只靠那个字段判断，这条断言会失败 —— 而那就是
    「存了盘的人第二天发现自己白做了一半」这个 bug 的根。
    """
    running = _start(router)
    saved = _start(router)
    router.set_benchmark_run_status(
        run_id=saved, target=BenchmarkRunStatus.INTERRUPTED, reason="今天先到这儿"
    )

    a = get_benchmark_run(session, running)
    b = get_benchmark_run(session, saved)
    assert a.finished_at is None and b.finished_at is None, "前提：两者都没结束"
    assert a.status is not b.status
    assert a.status is BenchmarkRunStatus.IN_PROGRESS
    assert b.status is BenchmarkRunStatus.INTERRUPTED

    # 而"能不能继续"这个问题，问 `resumable_benchmark_run` 只有一个答案
    resumable = resumable_benchmark_run(session)
    assert resumable is not None and resumable.run_id == saved


def test_a_running_run_is_not_offered_as_resumable(
    router: Router, session: Session
) -> None:
    """★ 反例保护：只有 `in_progress` 而没有中断的运行 → **无可继续**。

    `resumable_benchmark_run` 若把 `in_progress` 也算进去，
    那么"打开基准页"这个动作会**悄悄接管**另一个标签页里正在进行的会话。
    没有这条断言，上面那条测试在"什么都算可继续"的实现下同样通过。
    """
    _start(router)
    assert resumable_benchmark_run(session) is None


def test_resuming_goes_back_to_in_progress_and_stops_being_resumable(
    router: Router, session: Session
) -> None:
    run_id = _start(router)
    router.set_benchmark_run_status(run_id=run_id, target=BenchmarkRunStatus.INTERRUPTED)
    assert resumable_benchmark_run(session) is not None

    router.set_benchmark_run_status(run_id=run_id, target=BenchmarkRunStatus.IN_PROGRESS)
    row = get_benchmark_run(session, run_id)
    assert row.status is BenchmarkRunStatus.IN_PROGRESS
    assert resumable_benchmark_run(session) is None
    # 继续过一次这件事留在历史里
    assert [h["status"] for h in row.status_history] == [
        "in_progress",
        "interrupted",
        "in_progress",
    ]


# --------------------------------------------------------------------------- #
# 状态史只追加
# --------------------------------------------------------------------------- #


def test_the_status_history_is_append_only(router: Router, session: Session) -> None:
    """★ 本表被批准为 `source_ref` 的理由是"只追加、不改写"，
    而 `status` 是会变的 —— 所以历史的完整性靠这条测试守。

    如果实现把历史写成"记录当前状态"，那么"他放弃过几次"会被下一次操作
    覆盖掉，而**当前状态照样是对的**：这个损失没有任何症状。
    """
    run_id = _start(router)
    router.set_benchmark_run_status(run_id=run_id, target=BenchmarkRunStatus.INTERRUPTED)
    router.set_benchmark_run_status(run_id=run_id, target=BenchmarkRunStatus.IN_PROGRESS)
    router.set_benchmark_run_status(run_id=run_id, target=BenchmarkRunStatus.INTERRUPTED)
    _finish(router, run_id)

    row = get_benchmark_run(session, run_id)
    assert [h["status"] for h in row.status_history] == [
        "in_progress",
        "interrupted",
        "in_progress",
        "interrupted",
        "finished",
    ]
    # 每一次迁移都留了时间戳
    assert all(h.get("at") for h in row.status_history)
    assert row.status is BenchmarkRunStatus.FINISHED


# --------------------------------------------------------------------------- #
# 非法迁移被拒
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "target",
    [BenchmarkRunStatus.IN_PROGRESS, BenchmarkRunStatus.INTERRUPTED],
)
def test_a_finished_run_is_terminal(
    router: Router, session: Session, target: BenchmarkRunStatus
) -> None:
    """★ 已经算出分数的运行**不可退回**。

    退回去之后，"这个分数是哪次算出来的"就不再是唯一的了 ——
    与 `finalize_benchmark_run` 只允许补一次同因。
    """
    run_id = _start(router)
    _finish(router, run_id)
    with pytest.raises(ValueError, match="不能从"):
        router.set_benchmark_run_status(run_id=run_id, target=target)
    assert get_benchmark_run(session, run_id).status is BenchmarkRunStatus.FINISHED


def test_an_abandoned_run_is_terminal(router: Router, session: Session) -> None:
    run_id = _start(router)
    router.set_benchmark_run_status(
        run_id=run_id, target=BenchmarkRunStatus.ABANDONED, reason="重新开始"
    )
    with pytest.raises(ValueError, match="不能从"):
        router.set_benchmark_run_status(
            run_id=run_id, target=BenchmarkRunStatus.IN_PROGRESS
        )
    assert resumable_benchmark_run(session) is None


def test_finishing_twice_is_refused(router: Router, session: Session) -> None:
    """重复补分会让"这个分数是哪次算出来的"失去唯一答案。"""
    run_id = _start(router)
    _finish(router, run_id)
    with pytest.raises(ValueError, match="已经结束过"):
        _finish(router, run_id)


def test_an_unknown_run_is_a_clear_error(router: Router) -> None:
    with pytest.raises(KeyError, match="不存在"):
        router.set_benchmark_run_status(
            run_id="BR-9999", target=BenchmarkRunStatus.INTERRUPTED
        )


# --------------------------------------------------------------------------- #
# 重新开始 = 作废旧的 + 新建一个
# --------------------------------------------------------------------------- #


def test_restart_keeps_the_abandoned_run_as_a_fact(
    router: Router, session: Session
) -> None:
    """★ 「重新开始」不应该抹掉"这一次被他放弃了"这件事。

    用户的原话是"也可以选择重新开始" —— 那句话的含义是**这一次不要了**，
    不是"这一次从没发生过"。所以旧运行停在 `abandoned`（看得见），
    新运行从头开始。
    """
    old = _start(router)
    router.set_benchmark_run_status(
        run_id=old, target=BenchmarkRunStatus.ABANDONED, reason="用户选择重新开始"
    )
    new = _start(router, note=f"重开（作废 {old}）")

    assert get_benchmark_run(session, old).status is BenchmarkRunStatus.ABANDONED
    assert get_benchmark_run(session, new).status is BenchmarkRunStatus.IN_PROGRESS
    # 两次都在库里有据可查 —— 这正是与"重置旧运行"的区别
    assert session.query(BenchmarkRun).count() == 2


# --------------------------------------------------------------------------- #
# 运行记录仍然不进判定表（与 G-03 一致）
# --------------------------------------------------------------------------- #


def test_lifecycle_transitions_do_not_create_a_proposal_or_log(
    router: Router, session: Session
) -> None:
    """状态变迁**不产生提案、也不写 RouterLog**。

    理由：它不是关于用户的断言，也不是一次认知写入，它只是这次测量过程的一部分
    —— 而过程的痕迹属于过程自己（记在行内的 `status_history`）。
    这条测试同时钉住反面：如果哪天有人在状态变迁里塞了一条 RouterLog，
    "他中断过几次"就会开始污染「用户直写了几次偏好」那类计数。
    """
    run_id = _start(router)
    before = session.query(RouterLog).count()
    router.set_benchmark_run_status(run_id=run_id, target=BenchmarkRunStatus.INTERRUPTED)
    router.set_benchmark_run_status(run_id=run_id, target=BenchmarkRunStatus.IN_PROGRESS)
    assert session.query(RouterLog).count() == before
    assert session.query(RouterLog).filter(RouterLog.trigger == TriggerType.R6).count() == 0
