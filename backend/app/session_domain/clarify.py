"""任务不明确时**反问**（P13）。

## 它防的是什么

用户说「帮我复习第三章」。模型不知道那指的是哪份材料的第三章，
于是它**自己选了一个范围**，产出一份漂亮、完整、自信的计划。
用户看到计划，以为模型听懂了 —— 而计划根本没对准他要的东西。

这个失败最阴的地方在于：**看起来一切正常**。计划有步骤、有检查点、
进度条也会走。错只错在"它做的是另一件事"，而这件事要到很晚才暴露
（那时已经花了几个小时）。

所以规则是：**定义区缺了计划离不开的那个槽位时，先问，不要猜。**

## ★ 为什么只有 `goal` 是硬拦

`starting_point` / `success_criteria` 缺了也会影响计划，但它们影响的是
**计划的形状**，不是"能不能规划"。把三个都做成硬拦的后果不是更严谨，
而是**每次会话都要先答三道题** —— 一条经常被触发的规矩，
最后一定会被人（和模型）绕过去，而绕过的方式通常是"随便填一句"，
那比不问更坏：它给了一个假的确定感。

所以：**硬拦只留一个**（`goal`），其余靠模型主动问 + 工具结果的提示。

## ★ 这条规矩只约束**助手**，不约束用户

`changed_by=user`（用户自己在界面上改计划）**不走这道闸**。
解释权永远在用户手里：他可以一边挂着一个没答的问题，一边自己把计划改了。
把用户也拦住的后果是"我的计划被一个我没答的问题锁住了" ——
那是在用一条防模型乱猜的规矩去限制用户。

## 三种槽位状态，而不是两种

| 状态 | 含义 | 能不能改计划 |
|---|---|---|
| 空着、**没问过** | 模型还不知道，也没说它不知道 | ❌ 先问 |
| 空着、**问过没答** | 模型说了它不知道，用户在答 | ❌ 等答案 |
| 空着、**用户说先不管** | **用户**决定按模型的判断走 | ✅ 但计划带上"这是猜的" |
| 有内容 | 知道了 | ✅ |

第三行是关键：用户有权说"你先按你的理解来"。
把它压成"要么答要么别改计划"，等于逼用户回答一个他不想回答的问题。
但既然那是猜的，**计划上就要写着它是猜的** —— 否则三个月后没人分得清
"这条计划为什么这么排"是因为他知道，还是因为模型以为他知道。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

#: 澄清问题的三种状态。与 `ClarificationStatus` 一一对应（有测试钉住）。
STATUS_OPEN = "open"
STATUS_ANSWERED = "answered"
STATUS_DISMISSED = "dismissed"


@dataclass(frozen=True)
class Slot:
    """定义区里的一个槽位。"""

    name: str
    label: str
    #: 为什么计划依赖它 —— 这句话会出现在**拒绝的理由**里，
    #: 让模型（和人）明白这不是流程上的形式主义。
    why: str
    #: 缺了就**不能**改计划吗（只对助手生效）。
    hard: bool
    #: 兜底问法。系统自己也能问出这一句，所以"反问"不依赖模型愿不愿意问。
    template: str


SLOTS: tuple[Slot, ...] = (
    Slot(
        name="goal",
        label="目标",
        why="不知道要达成什么，任何计划都是在猜一件别的事",
        hard=True,
        template="这次你想达成什么？（一句话就够）",
    ),
    Slot(
        name="starting_point",
        label="起点",
        why="不知道你现在会到哪，计划要么重讲一遍你已经会的，要么假设你会你还不会的",
        hard=False,
        template="关于这件事，你现在已经会哪些、卡在哪里？",
    ),
    Slot(
        name="success_criteria",
        label="完成判据",
        why="没有它，「做完了」只能由我来说 —— 而那是你的标准，不是我的",
        hard=False,
        template="怎么算这次做完了？（能说出一个可检验的样子最好）",
    ),
)

SLOT_BY_NAME: dict[str, Slot] = {slot.name: slot for slot in SLOTS}

#: 硬拦槽位。**上限刻意是 1** —— 见模块文档。
HARD_SLOTS: tuple[str, ...] = tuple(slot.name for slot in SLOTS if slot.hard)

#: 可以对着它问的字段名。答案会**真的写进定义区**，
#: 所以只能是定义区真实存在的列（`session_input`），不能是新发明的一个字段。
ASKABLE_FIELDS: frozenset[str] = frozenset(SLOT_BY_NAME)


def is_empty(value: object) -> bool:
    """空 = `None` 或**只有空白**。

    ★ 只判 `None` 是不够的：`goal=" "`（一个空格）会让闸门放行，
    而计划仍然建立在"什么都没说"之上 —— 而它在界面上看起来是填过的。
    """
    return value is None or not str(value).strip()


def missing_slots(values: Mapping[str, object]) -> tuple[Slot, ...]:
    """还空着的槽位（按 `SLOTS` 的顺序，**不是**按字典顺序）。"""
    return tuple(slot for slot in SLOTS if is_empty(values.get(slot.name)))


@dataclass(frozen=True)
class PlanGate:
    """改计划前的闸门结论。"""

    #: 必须先问到答案的槽位（模型还不知道、也没问；或者问了还没答）。
    blocked: tuple[Slot, ...]
    #: 用户明确说"先不管"的槽位 —— 计划允许改，但**要写明这是猜的**。
    guessed: tuple[Slot, ...]
    #: 每个被拦的槽位对应的原因（`没问过` / `还在等答案`）。
    reasons: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.blocked

    def message(self) -> str:
        """拒绝的理由。**要能直接照着做** —— 包含工具名与槽位名。"""
        lines = ["任务定义区还缺计划离不开的东西，先把它弄清楚再改计划："]
        for slot, reason in zip(self.blocked, self.reasons, strict=True):
            lines.append(f"· `{slot.name}`（{slot.label}）：{reason} —— {slot.why}")
        if any("没问过" in r for r in self.reasons):
            lines.append(
                "用 `ask_about_task` 问一句（带上槽位名），"
                "让界面把这个问题显示给他；**不要自己填一个看起来合理的值**。"
            )
        if any("还在等答案" in r for r in self.reasons):
            lines.append("问题已经显示在定义区了，等答案回来；现在改计划改的是猜出来的那个任务。")
        if self.guessed:
            names = "、".join(f"`{slot.name}`" for slot in self.guessed)
            lines.append(f"（{names} 他选择了先不管，那部分我按判断来，但会在计划上写明是猜测）")
        return "\n".join(lines)


def plan_gate(
    values: Mapping[str, object],
    questions: Mapping[str, str],
) -> PlanGate:
    """闸门判定。`questions` = 槽位名 → 该槽位最近一次问题的状态。

    ★ 只看**硬拦槽位**：软槽位缺了不拦（见模块文档）。
    但它们若被问过又被搁置，仍然会进 `guessed`，让计划带上"这是猜的"。
    """
    blocked: list[Slot] = []
    reasons: list[str] = []
    guessed: list[Slot] = []
    for slot in missing_slots(values):
        status = questions.get(slot.name)
        if status == STATUS_ANSWERED:
            # 答过了却还是空的：答案没能写进去。这是一处**不该发生**的状态，
            # 当成"没答"处理并说明 —— 沉默地放行会让计划建在一个空的槽位上。
            blocked.append(slot)
            reasons.append("问题答过，但定义区里还是空的（答案没写进去）—— 需要重问一次")
            continue
        if status == STATUS_OPEN:
            if slot.hard:
                blocked.append(slot)
                reasons.append("已经问过，还在等答案")
            continue
        if status == STATUS_DISMISSED:
            guessed.append(slot)
            continue
        if slot.hard:
            blocked.append(slot)
            reasons.append("没问过")
    return PlanGate(blocked=tuple(blocked), guessed=tuple(guessed), reasons=tuple(reasons))


def guess_note(guessed: tuple[Slot, ...], blocked: tuple[Slot, ...] = ()) -> str:
    """计划修订理由后面追加的一句。**空字符串 = 没有猜测**。

    ★ 它必须落在**计划自己的记录**上（`plan_revision.reason`），
    而不是只出现在这一次的对话里：三个月后回看这条计划，
    唯一还能回答"为什么这么排"的就是那一行理由。
    """
    names = [f"`{slot.name}`" for slot in (*guessed, *blocked)]
    if not names:
        return ""
    return f"（{'、'.join(names)} 仍无答案：这是按助手判断排的，不是按他的说明排的）"


__all__ = [
    "ASKABLE_FIELDS",
    "HARD_SLOTS",
    "SLOTS",
    "SLOT_BY_NAME",
    "STATUS_ANSWERED",
    "STATUS_DISMISSED",
    "STATUS_OPEN",
    "PlanGate",
    "Slot",
    "guess_note",
    "is_empty",
    "missing_slots",
    "plan_gate",
]
