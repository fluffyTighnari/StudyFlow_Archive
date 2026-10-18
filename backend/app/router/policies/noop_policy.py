"""`no_change` 的执行侧：只写日志，不产生任何全局变更（M0 §5.3）。

**所有 target 都接受 `no_change`**（§5.3 的「任意」行）。它因此是一个独立策略模块，
而不是执行逻辑里的 `if action is NO_CHANGE` 特判 —— 特判会把
「no_change 走哪条策略」这个问题从策略分发表里漏出去，
于是「所有 target 都接受它」这个事实就有两个不一致的表述处。
"""

from __future__ import annotations

from app.router.policies.base import PolicyContext, PolicyOutcome


def apply(ctx: PolicyContext) -> PolicyOutcome:
    return PolicyOutcome(
        applied=True,
        reason="no_change：仅写日志，未产生全局变更（M0 §5.3）",
        rows_written=0,
        new_value=None,
    )
