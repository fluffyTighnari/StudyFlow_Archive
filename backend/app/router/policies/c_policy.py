"""C.evidence 直写 / 沉淀升级 / 生命周期策略（M0 §7.3 / P7.1）。

**关键约束：C 的写入永不自动改写 M1。**

这不是一句口号，而是由代码结构保证的：本模块只 import `insert_evidence` /
`revise_evidence` / `set_evidence_status`，**没有** `insert_baseline_version`
这个符号 —— 想违反也无从下手。
（`tests/router/test_c_policy.py` 用 AST 断言这一点，而不是靠注释提醒。）

## P7.1：为什么生命周期动作也放在这里，而不是另开一个 policy

因为 `policy_key` 是**按 target 类**声明的，一个 target 类只有一个策略入口。
为 `archive` 另开一个 policy key 会让"谁能写 c_evidence"从一处变成两处，
而两处迟早会不一致 —— 那时"归档能不能顺带写一条 M1"就重新成为一个问题。

按 action 在**同一个函数入口**里分派，import 面的收窄一条都没有丢。
"""

from __future__ import annotations

from app.global_domain.enums import IntentAction, LifecycleStatus
from app.global_domain.models import CEvidence
from app.global_domain.writes import (
    insert_evidence,
    revise_evidence,
    set_evidence_status,
)
from app.router.policies.base import PolicyContext, PolicyOutcome, PolicyRefused
from app.router.registry import TargetClass, item_id_from_target


def _snapshot(row: CEvidence) -> dict:
    return {
        "evidence_id": row.evidence_id,
        "statement": row.statement,
        "condition": row.condition,
        "effect": row.effect,
        "strength": row.evidence_strength.value,
        "source": row.source.value,
        "basis": row.basis.value,
        "status": row.status.value,
    }


def apply(ctx: PolicyContext) -> PolicyOutcome:
    if ctx.intent.action is IntentAction.REVISE:
        return _revise(ctx)
    if ctx.intent.action is IntentAction.ARCHIVE:
        return _set_status(ctx, LifecycleStatus.ARCHIVED, verb="归档")
    if ctx.intent.action is IntentAction.RESTORE:
        return _set_status(ctx, LifecycleStatus.ACTIVE, verb="恢复")
    return _insert(ctx)


def _insert(ctx: PolicyContext) -> PolicyOutcome:
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


def _revise(ctx: PolicyContext) -> PolicyOutcome:
    value = ctx.value
    evidence_id = _require_item_id(ctx)
    before = ctx.session.get(CEvidence, evidence_id)
    if before is None:
        raise PolicyRefused(f"C 条目不存在：{evidence_id}（可能是 id 打错了）")
    was_archived = before.status is LifecycleStatus.ARCHIVED
    old_value = _snapshot(before)

    row = revise_evidence(
        ctx.token,
        ctx.session,
        evidence_id,
        statement=value.statement,
        condition=value.condition,
        effect=value.effect,
        evidence_strength=value.evidence_strength,
        router_log_id=ctx.log_id,
    )
    note = (
        "（它目前是**已归档**状态，改写不会自动恢复 —— 要恢复请点恢复）"
        if was_archived
        else ""
    )
    reason = value.reason or "（未填写理由）"
    return PolicyOutcome(
        applied=True,
        reason=(
            f"改写 C 条目 {row.evidence_id}。理由：{reason}。"
            f"改动前后的完整快照都记在 RouterLog 里{note}"
        ),
        rows_written=1,
        old_value=old_value,
        new_value=_snapshot(row),
    )


def _set_status(
    ctx: PolicyContext, status: LifecycleStatus, *, verb: str
) -> PolicyOutcome:
    evidence_id = _require_item_id(ctx)
    before = ctx.session.get(CEvidence, evidence_id)
    if before is None:
        raise PolicyRefused(f"C 条目不存在：{evidence_id}（可能是 id 打错了）")
    old_value = _snapshot(before)
    # ★ `already` 必须在**写入之前**算出来。
    #
    # `set_evidence_status` 改的是**同一个 ORM 对象**（`session.get` 给的是
    # 身份映射里的那一份），所以写入之后再读 `before.status`，读到的永远是
    # 新值 —— 于是每一次归档都会说"它本来就是该状态"。
    # 这个 bug 是那条幂等测试抓到的，而且它只在**第二次**调用时才看得出来，
    # 因为第一次的"新旧对比"恰好被读成了"没有变化"。
    already = "（它本来就是该状态，本次只留痕）" if before.status is status else ""

    row = set_evidence_status(
        ctx.token,
        ctx.session,
        evidence_id,
        status,
        reason=ctx.value.reason if ctx.value else None,
        router_log_id=ctx.log_id,
    )
    reason = (ctx.value.reason if ctx.value else None) or "（未填写理由）"
    return PolicyOutcome(
        applied=True,
        reason=f"{verb} C 条目 {row.evidence_id}。理由：{reason}{already}。"
        f"**行还在**，只是状态变成 `{status.value}` —— 随时可以恢复",
        rows_written=1,
        old_value=old_value,
        new_value=_snapshot(row),
    )


def _require_item_id(ctx: PolicyContext) -> str:
    """归档 / 恢复 / 改写必须给逐条地址（`C.evidence.E-0001`）。

    见 `hypothesis_policy._require_item_id` —— 同一个理由，逐字相同。
    """
    item_id = item_id_from_target(ctx.intent.target, TargetClass.C_EVIDENCE)
    if item_id is None:
        raise PolicyRefused(
            f"`{ctx.intent.action.value}` 要给具体条目的地址"
            f"（`C.evidence.E-0001`），而不是 `{ctx.intent.target}` —— "
            "对一整类东西归档没有确定的含义"
        )
    return item_id
