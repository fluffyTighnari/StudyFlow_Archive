"""C.hypothesis 直写策略（P7 / P7.1）。

## 它为什么是一个**独立**的 policy key

`registry.POLICY_HYPOTHESIS` 与 `POLICY_C` 分开，于是本模块只能 import
`insert_hypothesis` / `revise_hypothesis` / `set_hypothesis_status`，
**拿不到** `insert_evidence` 这一族符号 —— 想"顺手也写一条证据"无从而下手。
"假说被误写成证据"因此是 import 层面不可能，
和 `c_policy.py` 里那条「C 永不自动改写 M1」用同一个手法。

## 它按 action 分派（P7.1）

用户裁定「解释权永远在用户手里」，于是同一个 target 上有四种动作：

| action | 做什么 | 判据 |
|---|---|---|
| `add_preference` | 插入一条新假说 | 判定表只收 `stated` |
| `revise` | 三个槽整体替换（行不换 id） | 同上，且**仍不可能变成证据** |
| `archive` | `status → archived`（**行还在**） | 他亲手点了删除 |
| `restore` | `status → active` | 他改主意了 |

## 它不做的事

- **不判断这条值不值得记** —— 用户说想试试就是想试试，判定表已经放行了
  该放行的部分（只收 `stated`），这里不再叠一层主观审查；
- **不检查冲突** —— 用户裁定「完全由用户支配，不管是否冲突」。两条矛盾的
  假说可以同时存在，因为**他还没试过**，矛盾本身就是要试的东西之一；
- **不替他判断删得对不对** —— 归档一条假说不需要理由，理由字段是可选的。
"""

from __future__ import annotations

from app.global_domain.enums import IntentAction
from app.global_domain.models import Hypothesis
from app.global_domain.writes import (
    insert_hypothesis,
    revise_hypothesis,
    set_hypothesis_status,
)
from app.router.policies.base import PolicyContext, PolicyOutcome, PolicyRefused
from app.router.registry import TargetClass, item_id_from_target

from app.global_domain.enums import LifecycleStatus


def _snapshot(row: Hypothesis) -> dict:
    return {
        "hypothesis_id": row.hypothesis_id,
        "statement": row.statement,
        "condition": row.condition,
        "effect": row.effect,
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
    row = insert_hypothesis(
        ctx.token,
        ctx.session,
        statement=value.statement,
        condition=value.condition,
        effect=value.effect,
        source_session_id=_source_session(ctx),
        router_log_id=ctx.log_id,
    )
    return PolicyOutcome(
        applied=True,
        reason=(
            f"新增假说 {row.hypothesis_id}（**未验证**）。场景：{row.condition}；"
            f"做法：{row.effect}。它**不参与计划装配** —— 只有试过之后写成"
            f"`C.evidence` 才会被当成经验使用（M0 §5.3.1）"
        ),
        rows_written=1,
        new_value=_snapshot(row),
    )


def _revise(ctx: PolicyContext) -> PolicyOutcome:
    value = ctx.value
    hypothesis_id = _require_item_id(ctx)
    before = ctx.session.get(Hypothesis, hypothesis_id)
    if before is None:
        raise PolicyRefused(f"假说不存在：{hypothesis_id}（可能是 id 打错了）")
    # 归档过的条目仍然可以被改写 —— 但要说出来，否则他会以为改完就回来了。
    was_archived = before.status is LifecycleStatus.ARCHIVED
    old_value = _snapshot(before)

    row = revise_hypothesis(
        ctx.token,
        ctx.session,
        hypothesis_id,
        statement=value.statement,
        condition=value.condition,
        effect=value.effect,
        router_log_id=ctx.log_id,
    )
    note = "（它目前是**已归档**状态，改写不会自动恢复 —— 要恢复请点恢复）" if was_archived else ""
    reason = value.reason or "（未填写理由）"
    return PolicyOutcome(
        applied=True,
        reason=(
            f"改写假说 {row.hypothesis_id}。理由：{reason}。"
            f"改动前后的完整快照都记在 RouterLog 里{note}"
        ),
        rows_written=1,
        old_value=old_value,
        new_value=_snapshot(row),
    )


def _set_status(
    ctx: PolicyContext, status: LifecycleStatus, *, verb: str
) -> PolicyOutcome:
    hypothesis_id = _require_item_id(ctx)
    before = ctx.session.get(Hypothesis, hypothesis_id)
    if before is None:
        raise PolicyRefused(f"假说不存在：{hypothesis_id}（可能是 id 打错了）")
    old_value = _snapshot(before)
    # ★ `already` 必须在写入之前算 —— 见 `c_policy._set_status` 的同一处说明：
    # `set_hypothesis_status` 改的是同一个 ORM 对象，写入后再读
    # `before.status` 读到的永远是**新值**。
    already = "（它本来就是该状态，本次只留痕）" if before.status is status else ""

    row = set_hypothesis_status(
        ctx.token,
        ctx.session,
        hypothesis_id,
        status,
        reason=ctx.value.reason if ctx.value else None,
        router_log_id=ctx.log_id,
    )
    reason = (ctx.value.reason if ctx.value else None) or "（未填写理由）"
    return PolicyOutcome(
        applied=True,
        reason=f"{verb}假说 {row.hypothesis_id}。理由：{reason}{already}。"
        f"**行还在**，只是状态变成 `{status.value}` —— 随时可以恢复",
        rows_written=1,
        old_value=old_value,
        new_value=_snapshot(row),
    )


def _require_item_id(ctx: PolicyContext) -> str:
    """归档 / 恢复 / 改写必须给**逐条地址**（`C.hypothesis.H-0001`）。

    对集合根（`C.hypothesis`）说"删除"没有正确答案：要么理解为"全部删掉"
    （一次误操作清空认知库），要么当作"什么也没做"（而他以为删了）。
    两种猜测都比一句明确的拒绝糟糕。
    """
    item_id = item_id_from_target(ctx.intent.target, TargetClass.HYPOTHESIS)
    if item_id is None:
        raise PolicyRefused(
            f"`{ctx.intent.action.value}` 要给具体条目的地址"
            f"（`C.hypothesis.H-0001`），而不是 `{ctx.intent.target}` —— "
            "对一整类东西归档没有确定的含义"
        )
    return item_id


def _source_session(ctx: PolicyContext) -> str | None:
    """这条假说是在哪次会话里说的。

    `R3`（对话中说出）有会话；`R1`（UI 直接写）没有 —— 那不是缺失，
    是他确实在界面里手写的，所以留空。**留空比填当前会话更诚实**：
    填一个"当时恰好开着的会话"会让「他在哪次聊的时候说的」变成一个假事实。
    """
    session_ref = getattr(ctx.intent, "session_ref", None)
    if session_ref:
        return str(session_ref)
    return None
