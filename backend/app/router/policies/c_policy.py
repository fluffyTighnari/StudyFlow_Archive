"""C.evidence 直写 / 沉淀升级策略（M0 §7.3）。

**关键约束：C 的写入永不自动改写 M1。**

这不是一句口号，而是由代码结构保证的：本模块只 import `insert_evidence`，
**没有** `insert_baseline_version` 这个符号 —— 想违反也无从下手。
（`tests/router/test_c_policy.py` 用 AST 断言这一点，而不是靠注释提醒。）
"""

from __future__ import annotations

from app.global_domain.models import CEvidence
from app.global_domain.writes import insert_evidence
from app.router.policies.base import PolicyContext, PolicyOutcome


def _snapshot(row: CEvidence) -> dict:
    return {
        "evidence_id": row.evidence_id,
        "statement": row.statement,
        "condition": row.condition,
        "effect": row.effect,
        "strength": row.evidence_strength.value,
        "source": row.source.value,
        "status": row.status.value,
    }


def apply(ctx: PolicyContext) -> PolicyOutcome:
    value = ctx.value
    row = insert_evidence(
        ctx.token,
        ctx.session,
        statement=value.statement,
        condition=value.condition,
        effect=value.effect,
        evidence_strength=value.evidence_strength,
        source=value.source,
        reason=ctx.intent.reasoning,
        router_log_id=ctx.log_id,
    )
    return PolicyOutcome(
        applied=True,
        reason=(
            f"新增 C 条目 {row.evidence_id}。适用条件：{row.condition}。"
            f"**C 不自动改写 M1** —— 若这条经验足够硬，应另提一条 M1 intent（M0 §7.3）"
        ),
        rows_written=1,
        new_value=_snapshot(row),
    )
