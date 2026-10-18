"""自然季度窗口的边界测试（M0 §5.4）。

窗口算错**不会抛异常**，只会让某一类建议静默消失，因此边界必须逐月钉死。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.timewin import quarter_bounds


@pytest.mark.parametrize(
    ("moment", "expected_start", "expected_end"),
    [
        ("2026-01-01T00:00:00", "2026-01-01T00:00:00", "2026-04-01T00:00:00"),
        ("2026-03-31T23:59:59", "2026-01-01T00:00:00", "2026-04-01T00:00:00"),
        ("2026-04-01T00:00:00", "2026-04-01T00:00:00", "2026-07-01T00:00:00"),
        ("2026-06-30T12:00:00", "2026-04-01T00:00:00", "2026-07-01T00:00:00"),
        ("2026-07-01T00:00:00", "2026-07-01T00:00:00", "2026-10-01T00:00:00"),
        ("2026-09-30T23:00:00", "2026-07-01T00:00:00", "2026-10-01T00:00:00"),
        ("2026-10-01T00:00:00", "2026-10-01T00:00:00", "2027-01-01T00:00:00"),
        ("2026-12-31T23:59:59", "2026-10-01T00:00:00", "2027-01-01T00:00:00"),
    ],
)
def test_quarter_boundaries(moment: str, expected_start: str, expected_end: str) -> None:
    start, end = quarter_bounds(datetime.fromisoformat(moment))
    assert start == datetime.fromisoformat(expected_start)
    assert end == datetime.fromisoformat(expected_end)


def test_every_month_lands_inside_its_quarter() -> None:
    """全年 12 个月逐一验证：半开区间 `[start, end)` 必须包含该月，且恰好 3 个月宽。"""
    for month in range(1, 13):
        moment = datetime(2026, month, 15, 12, 0, 0)
        start, end = quarter_bounds(moment)
        assert start <= moment < end, month
        assert (end.year - start.year) * 12 + (end.month - start.month) == 3, month
        assert (start.day, start.hour, start.minute, start.second) == (1, 0, 0, 0)


def test_timezone_is_preserved_not_invented() -> None:
    """naive 进 → naive 出，aware 进 → aware 出。

    若这里「顺手」补一个默认时区，调用方与 SQLite 中存储的 naive 时间戳比较时会抛
    `can't compare offset-naive and offset-aware datetimes` —— 一个只在季度边界
    才炸的运行时地雷，而且与业务逻辑毫无关系。
    """
    naive_start, naive_end = quarter_bounds(datetime(2026, 2, 3, 4, 5, 6))
    assert naive_start.tzinfo is None
    assert naive_end.tzinfo is None

    aware_start, aware_end = quarter_bounds(datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC))
    assert aware_start.tzinfo is UTC
    assert aware_end.tzinfo is UTC


def test_bounds_are_stable_within_a_quarter() -> None:
    start, end = quarter_bounds(datetime(2026, 5, 20, 8, 0, 0))
    assert (start, end) == (datetime(2026, 4, 1), datetime(2026, 7, 1))
    assert quarter_bounds(start) == (start, end)
    # 右端点是**下一个**季度的起点（半开区间）。
    assert quarter_bounds(end) == (datetime(2026, 7, 1), datetime(2026, 10, 1))


def test_year_rollover() -> None:
    start, end = quarter_bounds(datetime(2026, 11, 15))
    assert (start, end) == (datetime(2026, 10, 1), datetime(2027, 1, 1))
