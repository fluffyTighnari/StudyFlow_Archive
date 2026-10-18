"""R2 基准重做的建议/警告策略（M0 §5.4）。

**这不是配额。** 重做基准测试永不被拒绝——基线本来就是拿来校准的，
用一个硬门把校准锁死，与本项目的立项理由直接冲突。

本模块只产出「你需要先看见的信息」：

| 本季度重做次数 | 级别 |
|---|---|
| 0 | `none` |
| 1 | `suggest` |
| ≥2 | `warn` |

**结构性承诺**：`BenchmarkAdvisory` 没有阻断能力（`blocks()` 恒为 `False`），
且它**不参与** `precheck` 判定——`PrecheckResult` 只是**携带**它，
`verdict` 的计算路径上根本没有这个值。因此不存在「某天有人把建议接成拒绝」
的代码路径，不需要靠纪律来维持。

（这也是为什么 `blocks()` 是一个方法而不是一个属性：`test_benchmark_advisory.py`
用反射断言「本类除 `blocks` 外没有任何成员」，多出任何一个成员测试立刻失败。）
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.timewin import quarter_bounds
from app.global_domain.enums import AdvisoryLevel
from app.global_domain.reads import count_benchmark_runs, get_variable
from app.router.contracts import BenchmarkAdvisory

#: 触发「建议」的本季度重做次数。
SUGGEST_AT = 1
#: 触发「警告」的本季度重做次数。
WARN_AT = 2

__all__ = [
    "SUGGEST_AT",
    "WARN_AT",
    "BenchmarkAdvisory",
    "evaluate_benchmark_advisory",
]


def evaluate_benchmark_advisory(
    session: Session, variable_id: str, *, now: datetime | None = None
) -> BenchmarkAdvisory:
    """评估某变量本季度的重做建议。**永不抛异常、永不拒绝。**"""
    moment = now or datetime.now(UTC)
    start, end = quarter_bounds(moment)
    run_count = count_benchmark_runs(session, variable_id, start, end)

    variable = get_variable(session, variable_id)
    label = variable.display_name if variable is not None else variable_id

    if run_count >= WARN_AT:
        level = AdvisoryLevel.WARN
        message = (
            f"本季度已重做「{label}」基准测试 {run_count} 次。"
            "频繁重测会让基线随测量噪音漂移，建议先累积 Session 证据再回来。"
            "（这只是提示，你仍可照常写入。）"
        )
    elif run_count >= SUGGEST_AT:
        level = AdvisoryLevel.SUGGEST
        message = (
            f"本季度已重做「{label}」基准测试 1 次。"
            "建议先回看上次的 boundary 与本次变化原因，避免把测量噪音当成变化。"
        )
    else:
        level = AdvisoryLevel.NONE
        message = f"本季度尚未重做「{label}」的基准测试。"

    return BenchmarkAdvisory(
        variable_id=variable_id,
        level=level,
        run_count=run_count,
        message=message,
    )
