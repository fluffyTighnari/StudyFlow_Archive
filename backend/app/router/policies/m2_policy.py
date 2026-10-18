"""M2.preferences 直写策略（M0 §7.3）。

规则一：**同 `target_field` 冲突时「用户最新修改优先」**。
规则二（缺口 A20，用户裁定）：**`supersedes_pref_id` 显式指认被推翻的那一条**。

实现方式是「归档旧行 + 插入新行」，而不是就地改写旧行。

**为什么偏好也留旧行**（原先这里引的是「M2 §4 的沉淀逻辑依赖它」——**那是一处错误引用**：
M2 §4 通篇讲 C 证据，`status: active|archived` 与「可添加/修改/删除/归档」都是 **C 条目**
的规定，偏好头上没有这条）。真实的理由有两条，与 M2 §4 无关：

1. 偏好是**时机性**的：从「我习惯先看例子」变成「我现在要看定义」是一次真实的改变，
   而三个月前那个版本的唯一痕迹如果被就地覆盖掉，事后就无法解释**当时为什么那样规划**；
2. 归档是**「不再生效」而不是「删除曾经说过的话」** —— 生命周期状态与内容分离，
   于是「他说过什么」与「它现在还算不算数」是两个可以分别回答的问题。

**注意这里不需要用户手动「归档」**：改就已经是归档（同 `target_field` 被覆盖时旧行自动
失效）。也不需要一个独立于替换之外的归档动作 —— 那在「声明即事实」的模型里是含混的，
见 M0 §11 A14。

---

## A20：为什么需要显式指认，而不是"让 LLM 自己看着办"

`_find_same_field` 只在 `target_field` 非空时起作用。于是「我讨厌网课，因为浪费时间」
与「我能接受精品网课，如果讲得好的话」**两条都 `target_field=None`**，
在库里**同时 active、同时是硬约束**，计划生成时会拿到一对互相矛盾的约束 ——
而系统完全不知道它们矛盾。这是 A18 落地后才暴露出来的：在"两条 active 声明都是硬的"
这个前提下，原先"总有一条会被覆盖"的错觉不再有退路。

用户裁定的解法是**加一个显式的 `supersedes_pref_id`**，而不是让系统按语义猜。
理由是这个字段的语义可以被检验：「我推翻了 P-0003」是一个**可证伪的断言**，
而"这两句话意思相反"不是 —— 后者只能由模型判断，判错的后果是**悄悄抹掉用户的一句约束**。

## 三种必须拒绝的情形（拒绝也留痕，见 `PolicyRefused`）

1. **指向不存在的偏好** —— 模型编了一个 id。若静默忽略，用户会以为旧约束已经退休，
   而实际上两条并存；
2. **指向已经归档的偏好** —— 它已经不生效了，"再推翻一次"说明模型对当前状态的理解是错的；
3. **用 `inferred`/`experienced` 去推翻一条 `stated`** —— 这是本策略里唯一一条
   **不可协商**的规则。用户亲口说过的约束只能由用户自己改主意（也是 `stated`）来推翻；
   否则等于让模型推断出的东西**静默抹掉用户的话**（M2 红线 2）。
   注意它与「软偏好可被任务情况推翻」不冲突：那条说的是**消费侧**，
   而这里要动的是**存储侧的硬度本身**。
"""

from __future__ import annotations

from app.global_domain.enums import HARD_CONSTRAINT_BASIS, LifecycleStatus
from app.global_domain.models import M2Preference
from app.global_domain.reads import is_hard_constraint, list_active_preferences
from app.global_domain.writes import insert_preference, set_preference_status
from app.router.policies.base import PolicyContext, PolicyOutcome, PolicyRefused

#: 生命周期状态与内容分离：归档是「不再生效」，不是「删除曾经说过的话」。
_ARCHIVED_BY_SUPERSESSION = LifecycleStatus.ARCHIVED


def _find_same_field(
    ctx: PolicyContext, target_field: str | None
) -> M2Preference | None:
    if not target_field:
        return None
    for existing in list_active_preferences(ctx.session):
        if existing.target_field == target_field:
            return existing
    return None


def _resolve_explicit(
    ctx: PolicyContext, pref_id: str | None
) -> M2Preference | None:
    """按 `supersedes_pref_id` 找出被推翻的那一条，或**拒绝**这次写入。

    三种拒绝的理由各写各的：它们对用户说的是三件不同的事
    （模型编了 id / 模型搞错了当前状态 / 模型想替用户改主意）。
    """
    if pref_id is None:
        return None

    row = ctx.session.get(M2Preference, pref_id)
    if row is None:
        raise PolicyRefused(
            f"`supersedes_pref_id={pref_id}` 指向一条不存在的偏好 —— "
            "这条没有生效。请先读取当前偏好列表，再指认要推翻的那一条；"
            "如果只是想新增一条声明，不要填这个字段。"
        )
    if row.status is not LifecycleStatus.ACTIVE:
        raise PolicyRefused(
            f"`supersedes_pref_id={pref_id}` 指向的偏好已经是 "
            f"{row.status.value}，它本来就不再生效了 —— 这次写入被拒绝，"
            "因为「再推翻一次」说明对当前状态的判断有误。"
        )
    if is_hard_constraint(row) and ctx.intent.basis not in HARD_CONSTRAINT_BASIS:
        raise PolicyRefused(
            f"{pref_id} 是用户亲口下达的约束（basis=stated），"
            f"而这条 intent 的 basis 是 {ctx.intent.basis.value} —— "
            "推断出来的东西不能推翻用户说过的话（M2 红线 2）。"
            "要么让用户自己改口（basis=stated），要么不要动它。"
        )
    return row


def _snapshot(row: M2Preference) -> dict:
    return {
        "pref_id": row.pref_id,
        "statement": row.statement,
        "target_field": row.target_field,
        "priority": row.priority,
        "basis": row.basis.value,
        "status": row.status.value,
        "supersedes_pref_id": row.supersedes_pref_id,
    }


def apply(ctx: PolicyContext) -> PolicyOutcome:
    value = ctx.value

    explicit = _resolve_explicit(ctx, value.supersedes_pref_id)
    by_slot = _find_same_field(ctx, value.target_field)

    # 两条归档路径指向**不同的行**时，这条指令自相矛盾：
    # 「我推翻的是 P-0003」与「P-0005 占着我要用的装配槽位」不可能同时为真。
    #
    # 为什么不"两条都归档"：`router_log.old_value` 只能装一条旧值，
    # 归档两条却只记一条，就是**静默丢掉一个旧值** —— 而 P5.2 的教训正是
    # 「报告与实际执行不一致」不会让任何测试变红。所以宁可拒绝。
    if (
        explicit is not None
        and by_slot is not None
        and by_slot.pref_id != explicit.pref_id
    ):
        raise PolicyRefused(
            f"这次写入说「推翻 {explicit.pref_id}」，但它的 `target_field`"
            f"（{value.target_field}）当前由 {by_slot.pref_id} 占着 —— "
            "两句话不可能同时为真，因此没有写入任何东西。"
            "请明确：到底要替换哪一条？"
        )

    superseded = explicit if explicit is not None else by_slot

    if superseded is not None:
        set_preference_status(
            ctx.token,
            ctx.session,
            superseded.pref_id,
            _ARCHIVED_BY_SUPERSESSION,
            router_log_id=ctx.log_id,
        )

    row = insert_preference(
        ctx.token,
        ctx.session,
        statement=value.statement,
        priority=value.priority,
        basis=ctx.intent.basis,
        target_field=value.target_field,
        value=value.value,
        router_log_id=ctx.log_id,
        supersedes_pref_id=explicit.pref_id if explicit is not None else None,
    )

    reason = f"新增偏好 {row.pref_id}"
    if explicit is not None:
        reason += f"；按 `supersedes_pref_id` 归档 {explicit.pref_id}（用户明确改主意）"
    elif by_slot is not None:
        reason += (
            f"；同 target_field（{value.target_field}）的 {by_slot.pref_id} "
            f"已归档 —— 用户最新修改优先（M0 §7.3）"
        )

    return PolicyOutcome(
        applied=True,
        reason=reason,
        rows_written=1 + (1 if superseded is not None else 0),
        old_value=_snapshot(superseded) if superseded is not None else None,
        new_value=_snapshot(row),
    )
