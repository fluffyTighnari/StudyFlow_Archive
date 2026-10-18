"""时间窗口工具 —— 自然季度等「人类日历」语义。

为什么不把这些边界散落在各个 policy 里：窗口算错会**静默**改变建议的触发时机
（例如把 Q1 算成 1–2 月），不抛异常、不打日志，只是让某类提示永远不出现。
集中一处 + 边界测试是唯一可靠的做法。
"""

from __future__ import annotations

from datetime import datetime

QUARTER_MONTHS = 3


def quarter_bounds(moment: datetime) -> tuple[datetime, datetime]:
    """返回 `moment` 所在**自然季度**的半开区间 `[start, end)`。

    Q1 = 1–3 月，Q2 = 4–6 月，Q3 = 7–9 月，Q4 = 10–12 月。

    输出的 tzinfo 与输入一致（naive 进 → naive 出），以便与 SQLite 中存储的
    naive 时间戳直接比较。**不要**在这里「顺手」加默认时区——那会让调用方
    在比较时踩到 `can't compare offset-naive and offset-aware` 的运行时错误。
    """
    quarter_index = (moment.month - 1) // QUARTER_MONTHS
    start_month = quarter_index * QUARTER_MONTHS + 1
    start = moment.replace(
        month=start_month, day=1, hour=0, minute=0, second=0, microsecond=0
    )
    if start_month + QUARTER_MONTHS > 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start_month + QUARTER_MONTHS)
    return start, end
