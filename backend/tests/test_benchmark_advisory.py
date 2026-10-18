"""R2 基准重做的建议/警告（M0 §5.4）。

**本文件最重要的不是「警告会出现」，而是「建议永远不会变成拒绝」。**
因此除了级别判定，还专门断言 `blocks()` 恒为 False、且不存在任何阻断路径。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.core.timewin import quarter_bounds
from app.global_domain.enums import (
    AdvisoryLevel,
    Basis,
    Completeness,
    EvidenceChannel,
    IntentAction,
    ProposalSource,
    RouterLogStatus,
    TriggerType,
)
from app.global_domain.models import M1BaselineVersion
from app.global_domain.writes import append_router_log, insert_baseline_version
from app.router.policies.benchmark_advisory import (
    SUGGEST_AT,
    WARN_AT,
    BenchmarkAdvisory,
    evaluate_benchmark_advisory,
)
from app.seeds import load_all


@pytest.fixture()
def seeded(session):
    load_all(session)
    return session


def _benchmark_write(
    session,
    token,
    *,
    run_id: str,
    variable_id: str,
    channels: list[EvidenceChannel],
    trigger: TriggerType = TriggerType.R2,
) -> None:
    """模拟一次 Benchmark 落库：1 条日志 + N 个通道行，共享同一个 `run_id`。"""
    log = append_router_log(
        token,
        session,
        source=ProposalSource.BASELINE,
        status=RouterLogStatus.APPLIED,
        trigger=trigger,
        action=IntentAction.UPDATE_PARAMETER,
        target=f"M1.{variable_id}",
        basis=Basis.MEASURED,
        user_confirmed=True,
        reasoning=f"基准测绘完成 {run_id}",
    )
    for channel in channels:
        insert_baseline_version(
            token,
            session,
            variable_id=variable_id,
            channel=channel,
            basis=Basis(channel.value),
            completeness=Completeness.COMPLETE,
            score=6.0,
            source_ref=run_id,
            router_log_id=log.log_id,
        )


def _backdate(session, *, variable_id: str, when: datetime) -> None:
    """测试专用：把该变量的全部基线版本时间戳改到过去。

    写原语**刻意不接受 `created_at`** —— 审计时间戳不允许被调用方伪造，
    否则「什么时候发生了什么」这条唯一可信的记录就失去了意义。
    因此这个能力只存在于测试中，且必须显式写出来，不得泄漏进生产代码。
    """
    session.execute(
        update(M1BaselineVersion)
        .where(M1BaselineVersion.variable_id == variable_id)
        .values(created_at=when)
    )
    session.flush()


def _at(**kwargs) -> datetime:
    return datetime.now(UTC) if not kwargs else datetime(**kwargs, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# 级别判定
# --------------------------------------------------------------------------- #


def test_no_runs_gives_none(session, token, seeded) -> None:
    advisory = evaluate_benchmark_advisory(session, "V-SE")
    assert advisory.level == AdvisoryLevel.NONE
    assert advisory.run_count == 0
    assert "尚未重做" in advisory.message


def test_one_run_suggests(session, token, seeded) -> None:
    _benchmark_write(
        session,
        token,
        run_id="BENCH-1",
        variable_id="V-SE",
        channels=[EvidenceChannel.MEASURED],
    )
    advisory = evaluate_benchmark_advisory(session, "V-SE")
    assert advisory.level == AdvisoryLevel.SUGGEST
    assert advisory.run_count == 1
    assert "回看上次的 boundary" in advisory.message


def test_two_runs_warn(session, token, seeded) -> None:
    for index in range(2):
        _benchmark_write(
            session,
            token,
            run_id=f"BENCH-{index}",
            variable_id="V-SE",
            channels=[EvidenceChannel.MEASURED],
        )
    advisory = evaluate_benchmark_advisory(session, "V-SE")
    assert advisory.level == AdvisoryLevel.WARN
    assert advisory.run_count == 2
    assert "测量噪音" in advisory.message


def test_thresholds_are_the_documented_ones() -> None:
    assert SUGGEST_AT == 1
    assert WARN_AT == 2


# --------------------------------------------------------------------------- #
# 计数口径（两个都必须在意）
# --------------------------------------------------------------------------- #


def test_one_run_touching_many_channels_counts_once(session, token, seeded) -> None:
    """★ 回归测试：一次重做更新同一变量的多个通道，只能算 **1** 次。

    按写入行数计数会把「重做 1 次」误算成「重做 3 次」，从而在完全正常的首次
    重做上直接弹出警告——这正是最初设计里的一个真实缺陷。
    """
    _benchmark_write(
        session,
        token,
        run_id="BENCH-MULTI",
        variable_id="V-SE",
        channels=[
            EvidenceChannel.MEASURED,
            EvidenceChannel.EXPERIENCED,
            EvidenceChannel.STATED,
        ],
    )
    advisory = evaluate_benchmark_advisory(session, "V-SE")
    assert advisory.run_count == 1
    assert advisory.level == AdvisoryLevel.SUGGEST


def test_r1_direct_write_does_not_count_as_a_rerun(session, token, seeded) -> None:
    """用户在 UI 上直接改基线（R1）不是「重做基准测试」，不该计入重做次数。"""
    _benchmark_write(
        session,
        token,
        run_id="UI-EDIT",
        variable_id="V-SE",
        channels=[EvidenceChannel.STATED],
        trigger=TriggerType.R1,
    )
    advisory = evaluate_benchmark_advisory(session, "V-SE")
    assert advisory.run_count == 0
    assert advisory.level == AdvisoryLevel.NONE


def test_runs_are_counted_per_variable(session, token, seeded) -> None:
    _benchmark_write(
        session,
        token,
        run_id="BENCH-SE",
        variable_id="V-SE",
        channels=[EvidenceChannel.MEASURED],
    )
    assert evaluate_benchmark_advisory(session, "V-SE").run_count == 1
    assert evaluate_benchmark_advisory(session, "V-ATT").run_count == 0


def test_runs_from_a_previous_quarter_do_not_count(session, token, seeded) -> None:
    _benchmark_write(
        session,
        token,
        run_id="BENCH-OLD",
        variable_id="V-SE",
        channels=[EvidenceChannel.MEASURED],
    )
    now = datetime.now(UTC)
    last_day_of_previous_quarter = quarter_bounds(now)[0] - timedelta(days=1)
    _backdate(session, variable_id="V-SE", when=last_day_of_previous_quarter)

    advisory = evaluate_benchmark_advisory(session, "V-SE", now=now)
    assert advisory.run_count == 0
    assert advisory.level == AdvisoryLevel.NONE


def test_run_at_the_previous_quarter_boundary_is_included(
    session, token, seeded
) -> None:
    """窗口是半开区间 `[start, end)`：恰好落在本季度第一天 00:00 的重做要计入。"""
    _benchmark_write(
        session,
        token,
        run_id="BENCH-EDGE",
        variable_id="V-SE",
        channels=[EvidenceChannel.MEASURED],
    )
    now = datetime.now(UTC)
    quarter_start = quarter_bounds(now)[0]
    _backdate(session, variable_id="V-SE", when=quarter_start)

    assert evaluate_benchmark_advisory(session, "V-SE", now=now).run_count == 1


def test_unknown_variable_does_not_raise(session, seeded) -> None:
    """建议是纯提示，未知变量也必须给出可读结果而不是崩掉。"""
    advisory = evaluate_benchmark_advisory(session, "V-NOT-A-VARIABLE")
    assert advisory.variable_id == "V-NOT-A-VARIABLE"
    assert advisory.level == AdvisoryLevel.NONE
    assert "V-NOT-A-VARIABLE" in advisory.message


# --------------------------------------------------------------------------- #
# 结构性承诺：永不阻断
# --------------------------------------------------------------------------- #


def test_advisory_never_blocks_at_any_level(session, token, seeded) -> None:
    """无论重做多少次，`blocks()` 恒为 `False`——这是类型层面的承诺。"""
    for index in range(6):
        _benchmark_write(
            session,
            token,
            run_id=f"BENCH-{index}",
            variable_id="V-SE",
            channels=[EvidenceChannel.MEASURED],
        )
    advisory = evaluate_benchmark_advisory(session, "V-SE")
    assert advisory.run_count == 6
    assert advisory.level == AdvisoryLevel.WARN
    assert advisory.blocks() is False


def test_benchmark_advisory_has_no_blocking_capability() -> None:
    """除 `blocks()`（那个**自证不阻断**的方法本身）外，`BenchmarkAdvisory` 上
    不存在任何「拒绝/阻断/配额」语义的成员。

    用反射把承诺钉死：只要有人加了 `should_reject` / `exceeds_quota` 之类的字段，
    这个测试就会失败，从而强迫那次改动变成一次显式讨论，而不是悄悄通过。
    """
    banned = ("reject", "block", "quota", "deny", "forbid", "limit_exceeded")
    # `blocks()` 是本条测试所依赖的那个自证方法，显式豁免。
    allowed = {"blocks"}
    members = (
        set(dir(BenchmarkAdvisory)) | set(BenchmarkAdvisory.__dataclass_fields__)
    ) - allowed
    offenders = [name for name in members if any(w in name.lower() for w in banned)]
    assert offenders == [], f"BenchmarkAdvisory 上出现了阻断语义的成员：{offenders}"


def test_advisory_does_not_touch_precheck_or_persistence(session, token, seeded) -> None:
    """评估建议**不产生任何副作用**：不写日志、不改全局状态。

    否则「看一眼建议」这个只读动作会自己变成一次全局变更。
    """
    _benchmark_write(
        session,
        token,
        run_id="BENCH-1",
        variable_id="V-SE",
        channels=[EvidenceChannel.MEASURED],
    )
    from app.global_domain.reads import count_router_logs

    before = count_router_logs(session)
    evaluate_benchmark_advisory(session, "V-SE")
    evaluate_benchmark_advisory(session, "V-SE")
    assert count_router_logs(session) == before


def test_advisory_levels_are_exactly_three() -> None:
    """**没有**「阻断」这一级——枚举本身就是承诺的一部分。"""
    assert {level.value for level in AdvisoryLevel} == {"none", "suggest", "warn"}
