"""冲突分析 —— **纯函数，只报告不裁定**（路由层 §8 第 2 条 / M0 §7.4）。

本模块回答一个问题：**这一批里，有没有两条 intent 会写到同一个存储位置？**

## 这个模块在 P5.2 期被重写过，原因是它原先错得很隐蔽

原先它按 `intent.target` **字符串**分组，把「同一个 target」当成了「同一个写入位置」。
**target 不是写入位置** —— 于是两种最常见的正常操作被静默损坏：

| 场景 | 旧行为 | 为什么错 |
|---|---|---|
| 一次基准测试写同一变量的 measured 与 experienced 两个通道 | 第二条被 `skipped` | 两个通道是**互相独立**的存储槽位（这正是通道纯度的意思），而 target 里**没有通道** |
| 一次沉淀两条不相关的偏好（「我讨厌看网课」/「我早上效率最高」） | 第二条被 `skipped` | 两条偏好是**两个独立实体**，共用 target `M2.preferences` 只是因为它是一张表，不是因为它们相撞 |

而且报告文案在这种情况下会写「有更高优先级的写入（X 胜出）」——
可两条 intent 的 basis **完全相同**，胜者是按 `intent_id` 字母序挑出来的。
**系统用一个不成立的理由解释了一次静默的数据丢失。**

单元测试没有发现它，因为 `test_analyze_conflict_groups_by_target` 把「按 target 分组」
**断言成了正确行为** —— 测试保护的是实现当时的样子，而不是需求。

## 现在只认真实的写入槽位

| 目标 | 槽位 | 两条 intent 会相撞的条件 |
|---|---|---|
| `M1.<var>` | `("M1", var, channel)` | 同变量**且同通道** |
| `M2.preferences` 带 `target_field` | `("M2", target_field)` | 同一个装配槽位被写两次 |
| `M2.preferences` 无 `target_field` | **无槽位** | 永不相撞 —— 两条声明是两个独立事实 |
| `C.evidence` | **无槽位** | 永不相撞 —— 每条证据是独立实体 |
| `no_change` / 未注册 | **无槽位** | 不写任何东西 |

## 同槽位相撞时不再"按优先级挑一个"

一个批次里对**同一个槽位**写两次，不是「优先级问题」而是**输入本身就矛盾**
（同一次测绘不可能测出两个 measured 值；同一个装配槽位不可能同时有两条偏好）。
所以：

- 两条**档位不同**（如用户声明 vs LLM 推断）→ 高档位胜出，低档位 `skipped`。
  这是「用户声明不可被 LLM 推断顶掉」的落点，是**真的**优先级。
- 两条**同档位** → **整组拒绝**，理由写「无法裁定」。
  绝不按 `intent_id` 字母序挑一个 —— 那会让「你的数据变成什么样」取决于
  一个字符串的字典序，而这是无法向用户解释的。

## 与「上下文采用序」的区别（两者禁止合并）

| 名称 | 序 | 用在哪 | 是否会写 |
|---|---|---|---|
| **写入裁定序** | 用户明确偏好 > measured 基线 > experienced 最近 Session > inferred 推断 | 仅 Router，**只处理同槽位相撞** | 是 |
| **上下文采用序** | 用户偏好 > stage(本次) > 基线/证据 > 策略库 | 仅 Agent 组装上下文 | 否 |

推论：**stage 永不参与写入裁定** —— Session 内未被沉淀的感受不是证据，
只有经沉淀变成 intent 之后，才以 experienced 身份进入本模块的序。
本模块因此不得接触 `app.session_domain`（架构规则 ARCH-09 强制）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.global_domain.enums import Basis, IntentAction
from app.global_domain.targets import (
    M2_TARGET_ROOT,
    TargetNamespace,
    classify_target,
    variable_id_from_target,
)
from app.router.contracts import ConflictReport, UpdateIntent

#: 本序的名字。写进报告是为了让「这条为什么没生效」可被直接回答。
ARBITRATION_ORDER_NAME = "写入裁定序"

RANK_USER_STATED = 4
RANK_MEASURED = 3
RANK_EXPERIENCED = 2
RANK_INFERRED = 1
RANK_NO_CHANGE = 0

#: 档位 → 人类可读标签。日志与提案卡都直接用它，避免各处手写文案。
RANK_LABELS: dict[int, str] = {
    RANK_USER_STATED: "用户明确偏好",
    RANK_MEASURED: "measured 基线",
    RANK_EXPERIENCED: "experienced 最近 Session",
    RANK_INFERRED: "inferred 推断",
    RANK_NO_CHANGE: "no_change",
}


def write_arbitration_rank(intent: UpdateIntent) -> int:
    """**写入裁定序**：把一条 intent 映射到优先级档位。数值越大越优先。

    刻意只依赖 `(action, basis)`，**不看** Session 内状态、不看 LLM 的 `confidence`：
    - 不看 Session：stage 永不参与写入裁定（见模块文档）；
    - 不看 confidence：那是 LLM 的自评，与「这条证据有多硬」无关
      （否则一个语气自信的模型就能压过实测数据）。
    """
    if intent.action is IntentAction.NO_CHANGE:
        return RANK_NO_CHANGE
    if intent.action is IntentAction.ADD_PREFERENCE and intent.basis is Basis.STATED:
        return RANK_USER_STATED
    if intent.action is IntentAction.UPDATE_PARAMETER and intent.basis is Basis.MEASURED:
        return RANK_MEASURED
    if intent.basis is Basis.EXPERIENCED:
        return RANK_EXPERIENCED
    return RANK_INFERRED


def rank_label(rank: int) -> str:
    return RANK_LABELS.get(rank, f"未标注档位 {rank}")


def _value_field(intent: UpdateIntent, name: str) -> Any:
    """从 `intent.value` 取一个字段，**同时支持 dict 与 pydantic 模型**。

    两种形态都真实存在：HTTP 进来的 `value` 是 dict，Router 内部的 intent
    可能已经被结构校验成模型。这里只读、不校验 —— 校验是 precheck 的职责。
    """
    value = intent.value
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _channel_of(intent: UpdateIntent) -> str | None:
    raw = _value_field(intent, "channel")
    if raw is None:
        return None
    return getattr(raw, "value", raw)


def write_slot(intent: UpdateIntent) -> str | None:
    """这条 intent 真正会写到的**存储槽位**。`None` = 与同批任何 intent 都不可能相撞。

    这是本模块唯一一处「什么算同一个位置」的定义。**不要在别处再写一遍** ——
    这个定义写错一次就会静默丢数据（见模块文档里的两个真实案例），
    而静默丢数据不会让任何测试变红，只要那个测试也写错了同一个假设。
    """
    if intent.action is IntentAction.NO_CHANGE:
        return None

    namespace = classify_target(intent.target)

    if namespace is TargetNamespace.M1:
        channel = _channel_of(intent)
        if channel is None:
            # 结构校验会先拒掉这种 intent（precheck 在冲突分析之前跑）。
            # 万一走到了这里，宁可**不报冲突**也不要误报：误报会丢数据，
            # 漏报最多让两条同槽位写入各自成版本（历史里看得见，可回滚）。
            return None
        return f"{intent.target}/{channel}"

    if namespace is TargetNamespace.M2:
        target_field = _value_field(intent, "target_field")
        if target_field is None:
            # 无装配槽位的声明是**独立事实**：两条不同的约束可以并存。
            return None
        return f"{M2_TARGET_ROOT}.{target_field}"

    # C 证据的每一条都是独立实体；未注册 namespace 已由 precheck 拒绝。
    return None


def analyze_conflict(intents: Sequence[UpdateIntent]) -> list[ConflictReport]:
    """指出同批内**写到同一槽位**的多条写入，以及写入裁定序下的胜者。

    **不修改任何东西，也不丢弃任何 intent** —— 丢弃是 Router 的动作（见
    `service.Router._write_batch`），本函数只负责把事实算清楚。
    """
    grouped: dict[str, list[UpdateIntent]] = {}
    for intent in intents:
        slot = write_slot(intent)
        if slot is not None:
            grouped.setdefault(slot, []).append(intent)

    reports: list[ConflictReport] = []
    for slot, group in grouped.items():
        if len(group) < 2:
            continue
        reports.append(_report_for(slot, group))
    return reports


def _report_for(slot: str, group: list[UpdateIntent]) -> ConflictReport:
    ranked = sorted(group, key=lambda item: (-write_arbitration_rank(item), item.intent_id))
    top = write_arbitration_rank(ranked[0])
    tied = [item for item in ranked if write_arbitration_rank(item) == top]

    if len(tied) > 1:
        # 同档位：**没有**更高优先级可言。绝不按 intent_id 挑一个 —— 那会让
        # 「数据变成什么样」取决于字符串字典序。
        return ConflictReport(
            slot=slot,
            intent_ids=tuple(item.intent_id for item in group),
            winner_intent_id=None,
            order_name=ARBITRATION_ORDER_NAME,
            note=(
                f"槽位 {slot} 上有 {len(group)} 条写入，其中 {len(tied)} 条档位相同"
                f"（{rank_label(top)}）——**无法裁定**，故整组拒绝。"
                f"同一次提交不可能对同一个位置给出两个平级的值；"
                f"请拆成两次提交，或先想清楚哪个是对的。"
            ),
        )

    winner = ranked[0]
    losers = [item.intent_id for item in group if item.intent_id != winner.intent_id]
    return ConflictReport(
        slot=slot,
        intent_ids=tuple(item.intent_id for item in group),
        winner_intent_id=winner.intent_id,
        order_name=ARBITRATION_ORDER_NAME,
        note=(
            f"槽位 {slot} 上有 {len(group)} 条写入。按{ARBITRATION_ORDER_NAME}，"
            f"{winner.intent_id}（{rank_label(top)}）胜过 "
            f"{'、'.join(losers)}（{rank_label(write_arbitration_rank(ranked[1]))}）。"
            f"落败者标记为 skipped 而不是丢弃 —— 用户仍能在提案里看到它们"
            f"并重新提交（M0 §7.4）。"
        ),
    )


def losers_of(report: ConflictReport) -> tuple[str, ...]:
    """报告中的落败 intent。Router 用它决定谁被 `skipped`。

    平局报告没有胜者，因此**没有落败者** —— 那一组全部按「无法裁定」拒绝，
    见 `rejected_of`。两者不能互相代替：`skipped` 是「你输了」，
    `rejected` 是「这一批本身说不通」。
    """
    if report.winner_intent_id is None:
        return ()
    return tuple(
        intent_id
        for intent_id in report.intent_ids
        if intent_id != report.winner_intent_id
    )


def rejected_of(report: ConflictReport) -> tuple[str, ...]:
    """平局报告里**全部**要拒绝的 intent。非平局报告返回空。"""
    if report.winner_intent_id is not None:
        return ()
    return report.intent_ids
