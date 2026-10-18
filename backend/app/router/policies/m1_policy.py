"""M1 版本化覆盖策略（M0 §7.3）。

本策略只做两件事：**确认红线 3 已满足**，然后**版本化写入**。

季度重做建议**不在这里** —— 它属于 precheck 的 `advisories`，永不阻断（§5.4）。
放进策略层就意味着它离「拒绝」只差一个 if。
"""

from __future__ import annotations

from app.global_domain.models import M1BaselineVersion
from app.global_domain.reads import current_baseline_version
from app.global_domain.writes import insert_baseline_version
from app.router.policies.base import PolicyContext, PolicyError, PolicyOutcome, PolicyRefused


def snapshot(row: M1BaselineVersion | None) -> dict | None:
    """一个 M1 版本在审计日志里的样子。

    **公开**（而非 `_snapshot`）是因为它有两个正当调用方：本策略的写入，
    与 `Router.rollback`。两处必须是同一种形态 —— 否则同一条版本链上会出现
    两种结构不同的 `old_value`/`new_value`，而审计的价值正来自可比性。
    """
    if row is None:
        return None
    return {
        "version_id": row.version_id,
        "variable_id": row.variable_id,
        "channel": row.channel.value,
        "score": row.score,
        "completeness": row.completeness.value,
        "confidence": row.confidence.value if row.confidence is not None else None,
        "boundary": row.boundary,
        "basis": row.basis.value,
    }


def apply(ctx: PolicyContext) -> PolicyOutcome:
    if not ctx.user_confirmed:
        # 这层守卫是**冗余**的（precheck 已经把该确认的标出来了），
        # 但冗余是故意的：红线 3 是全局权威域的最后一道门，
        # 不该只由一个上游函数的状态决定。
        raise PolicyRefused(
            "红线 3：`update_parameter` 需 `measured/experienced` + 用户确认；"
            "未经确认的写入不得落到 M1（M0 §13.3）"
        )

    variable_id = ctx.resolved.variable_id
    if variable_id is None:
        raise PolicyError(f"M1 target {ctx.intent.target!r} 缺少 variable_id")

    value = ctx.value
    previous = current_baseline_version(ctx.session, variable_id, value.channel)
    row = insert_baseline_version(
        ctx.token,
        ctx.session,
        variable_id=variable_id,
        channel=value.channel,
        basis=ctx.intent.basis,
        completeness=value.completeness,
        score=value.score,
        confidence=value.confidence,
        boundary=value.boundary,
        source_ref=ctx.source_ref,
        router_log_id=ctx.log_id,
    )

    return PolicyOutcome(
        applied=True,
        reason=(
            f"版本化覆盖 {variable_id}/{value.channel.value}："
            f"{previous.version_id if previous is not None else '(无旧版本)'}"
            f" → {row.version_id}"
        ),
        rows_written=1,
        old_value=snapshot(previous),
        new_value=snapshot(row),
    )
