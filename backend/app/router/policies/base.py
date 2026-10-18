"""策略实现的公共契约。

职责边界（这条边界是整个 P2 的核心）：策略**只负责「原子地、留痕地把这次变更
写进去」**，不负责判断该不该写 —— 那是 `precheck` 的职责。

分离的收益很具体：策略层因此可以假定「调用我 = 已经决定要写」，
于是它只需要守住不变量，而不必把判定表再实现一遍。判定表只有一份
（`precheck.PRECHECK_TABLE`），策略只有一组，两者的覆盖情况可以分别穷举测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.capability import RouterToken
from app.global_domain.enums import TriggerType
from app.router.contracts import UpdateIntent
from app.router.registry import ResolvedTarget


class PolicyRefused(Exception):
    """策略层**主动拒绝**（例如红线 3 未获用户确认）。

    Router 捕获后写一条 `status=rejected` 的 RouterLog。
    **拒绝也必须留痕** —— 否则「这条为什么没生效」将无从回答，
    而用户会以为是自己没点对按钮。
    """


class PolicyError(Exception):
    """策略层执行失败（数据不合法、目标行缺失等）。同样会留痕。"""


@dataclass(frozen=True)
class PolicyContext:
    session: Session
    token: RouterToken
    trigger: TriggerType
    intent: UpdateIntent
    resolved: ResolvedTarget
    #: 已经过 Registry 声明的 schema 校验的参数对象（`no_change` 时为 `None`）。
    value: Any
    #: 证据出处：`benchmark_run_id` / `session_id` / `None`。
    source_ref: str | None
    #: **预分配**的 RouterLog id。
    #: 先有 id 再写数据行，数据行才能携带 `router_log_id` —— 否则要么写入行
    #: 无法关联日志（审计断链），要么只能事后 UPDATE 日志（审计不可信）。
    log_id: str
    #: 是否已获用户确认。红线 3 要求 `update_parameter` 必须为 `True`。
    user_confirmed: bool


@dataclass(frozen=True)
class PolicyOutcome:
    applied: bool
    reason: str
    rows_written: int = 0
    old_value: Any = None
    new_value: Any = None
