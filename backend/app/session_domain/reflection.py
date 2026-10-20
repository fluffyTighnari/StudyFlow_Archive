"""「复盘之后按指引写感想」—— 指引是**这次会话的**，不是一张通用模板。

## 为什么指引必须由代码生成，而不是写一段固定提示词

固定的四句话（"发生了什么？""哪里和预期不一样？"）贴给任何一次会话都成立，
于是它也**帮不上任何忙**：用户看到的是一份问卷，而不是"这次我到底经历了什么"。
这里生成的问题全部**指向这次会话里真实存在的东西**：

- 步骤与检查点的**真实完成情况**（哪些打了勾、哪些没有）；
- 定义区里**用户自己写的**完成判据（让他对着自己那句判据回答）；
- 这次会话真实用过的 stage 条目与笔记（"你记下过这些"）；
- 复盘总结里已经写下的内容（感想是**补充**，不是抄一遍）。

## 四要素来自测量设计，不是我自己想的

`V-PAR` 的 `measured` 通道声明的是「复盘产物的**四要素**完整度」，
四要素在 `_题库采购清单_v0.1.md` §7 里写死了：

| 要素 | 问的是 | 缺了会怎样 |
|---|---|---|
| ① 发生了什么 | 事实层 | 感想变成感受，无法与任何事对应 |
| ② 预期差异 | 结果和预期哪里不一样 | 没有差异就没有教训可提取 |
| ③ 原因 | 为什么会这样 | ★ 最容易缺 |
| ④ 下次改变 | 下次具体改什么 | ★ 最容易缺，而这一项才是"产出" |

★ 于是指引与仪器**共用同一套判据**：用户按指引写出来的东西，
将来就是 `M-PAR-02/03` 要评的那份产物。这不是巧合 ——
如果指引问的是另一套东西，那"完整度"就永远量不出来。

## 这里**不**做什么

- 不生成感想。四要素的问题给出去，答案只能由用户给（`writes.write_reflection`）。
- 不打分。"这份感想写得好不好"不是这一步要回答的；
  它要回答的是"用户有没有被给到一个能写得出东西的位置"。
- 不把感想写进全局。它落在会话里（`reflection` 表），要变成全局认知
  仍然只有那一条路：沉淀 → Router。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.session_domain import reads


@dataclass(frozen=True)
class Element:
    """四要素之一。`key` 是数据里的名字（稳定），`question` 是给人看的那句。"""

    key: str
    label: str
    lead: str
    #: 这一项为什么容易缺 —— 写在指引里，让人知道它值得停下来想。
    caution: str = ""


#: 四要素（顺序就是"从事实到行动"的顺序）。
#:
#: ★ 顺序有含义：先写发生了什么，再写差异与原因，最后才写下次改变。
#: 反过来先问"下次改什么"，人会直接跳到结论，而那个结论通常与
#: 这次真实发生的事无关 —— 那是"计划"而不是"复盘"。
ELEMENTS: tuple[Element, ...] = (
    Element(
        "what_happened",
        "发生了什么",
        "事实层：这一次你实际做了什么、做到哪一步停下了？",
    ),
    Element(
        "expected_gap",
        "预期差异",
        "结果和你原本以为的哪里不一样？",
        caution="如果答案是「差不多」，那多半是没细看 —— 差异往往在小处。",
    ),
    Element(
        "why",
        "原因",
        "为什么会这样？哪一部分是方法的问题，哪一部分是别的原因？",
        caution="★ 这一项最容易缺。给一个原因，比给一个态度有用。",
    ),
    Element(
        "next_change",
        "下次改变",
        "下次具体改什么？（要具体到能执行的一步）",
        caution="★ 这一项最容易缺，而它才是复盘真正的产出。",
    ),
)


@dataclass(frozen=True)
class GuideItem:
    """指引里的一条：四要素之一 + **这次会话里与它相关的事实**。"""

    key: str
    label: str
    question: str
    caution: str
    facts: tuple[str, ...] = field(default_factory=tuple)
    answered: bool = False


@dataclass(frozen=True)
class ReflectionGuide:
    """一次「写感想」的指引。"""

    session_id: str
    items: tuple[GuideItem, ...]
    #: 已经有几条感想了（可以写多条：一次会话可以有几次回看）。
    existing: int = 0
    #: 现在**不建议**写的原因（比如还没有复盘总结）。空串 = 可以写。
    blocked_reason: str = ""

    @property
    def missing(self) -> tuple[str, ...]:
        """还没写的那几项。**不是分数** —— 它只回答"还差哪几块"。"""
        return tuple(item.key for item in self.items if not item.answered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "items": [
                {
                    "key": item.key,
                    "label": item.label,
                    "question": item.question,
                    "caution": item.caution,
                    "facts": list(item.facts),
                    "answered": item.answered,
                }
                for item in self.items
            ],
            "missing": list(self.missing),
            "existing": self.existing,
            "blocked_reason": self.blocked_reason,
            "elements_note": (
                "四要素来自 V-PAR 的测量设计（`_题库采购清单` §7）："
                "① 发生了什么 ② 预期差异 ③ 原因 ④ 下次改变；"
                "V-PAR 的 `measured` 通道声明的正是这四要素的**完整度**。"
                "★ 指引与仪器共用同一套判据 —— 你按它写出来的东西，"
                "就是将来「复盘产出质量」要评的那一份。"
                "★ 这里是**指引**，不是评分：`missing` 只回答还差哪几块。"
            ),
        }


def _is_done(step: Any) -> bool:
    """步骤是不是"完成了"。**只认 `done`** —— `skipped` 不是完成。

    ★ 把跳过算成完成，会让"我做到哪了"这件事在指引进里变得比真实的好看，
    而指引的全部价值就是让人对着**真实进度**写。
    """
    from app.session_domain.enums import PlanStepStatus

    return getattr(step, "status", None) in (
        PlanStepStatus.DONE,
        PlanStepStatus.DONE.value,
    )


def _action_of(step: Any) -> str:
    return str(getattr(step, "action", "") or "").strip()


def _content_of(row: Any) -> str:
    """一条过程记录 / 笔记的内容（截断到 40 字，指引不该被长文本淹没）。"""
    text = str(getattr(row, "content", "") or "").strip().replace("\n", " ")
    return text if len(text) <= 40 else text[:40] + "…"


def build_guide(
    db: DbSession, session_id: str, *, answers: dict[str, str] | None = None
) -> ReflectionGuide:
    """生成这次会话的指引。**纯读**：不写库、不调模型。

    `answered` 的默认口径是**这次会话里所有感想写过的项的并集** ——
    不是"最近一条写了什么"。理由：「还差哪几块」问的是这次会话
    有没有覆盖过那一项，而一次会话可以回看好几次。
    调用方也可以用 `answers` 显式覆盖（续写某一条时用得上）。
    """
    session = reads.get_session(db, session_id)
    plan = reads.get_plan(db, session_id)
    steps = reads.plan_steps(db, plan.plan_id) if plan is not None else []
    checkpoints = reads.plan_checkpoints(db, plan.plan_id) if plan is not None else []
    summary = reads.latest_summary(db, session_id)
    notes = reads.list_notes(db, session_id)
    stage = reads.list_stage_items(db, session_id)
    input_row = reads.get_input(db, session_id)

    done = [step for step in steps if _is_done(step)]
    facts_by_key: dict[str, list[str]] = {element.key: [] for element in ELEMENTS}

    # ① 发生了什么 —— 真实进度，不是"做了几步"这个数字
    if steps:
        facts_by_key["what_happened"].append(
            f"计划共 {len(steps)} 步，标记完成的 {len(done)} 步"
            + (f"：{'、'.join(_action_of(step) for step in done[:5])}" if done else "（还没有打勾的）")
        )
    else:
        facts_by_key["what_happened"].append("这次会话没有计划 —— 那也是一种事实，照实写。")
    if checkpoints:
        used = [cp for cp in checkpoints if getattr(cp, "result", None) is not None]
        facts_by_key["what_happened"].append(
            f"检查点 {len(checkpoints)} 个，有结果的 {len(used)} 个"
        )
    if stage:
        facts_by_key["what_happened"].append(
            f"过程记录 {len(stage)} 条" + f"：{'；'.join(_content_of(row) for row in stage[:3])}"
        )
    if notes:
        facts_by_key["what_happened"].append(
            f"笔记 {len(notes)} 条" + f"：{'；'.join(_content_of(row) for row in notes[:3])}"
        )

    # ② 预期差异 —— 对着**用户自己写的**完成判据问
    criteria = getattr(input_row, "success_criteria", None)
    if criteria:
        facts_by_key["expected_gap"].append(f"你自己写的完成判据是：「{criteria}」")
        facts_by_key["expected_gap"].append("对照它回答：达到了吗？差在哪一步？")
    else:
        facts_by_key["expected_gap"].append(
            "定义区里没有写完成判据 —— 那么「有没有达到预期」就没有共同的尺子，"
            "照你心里的那把尺子写，顺便考虑下次把它写下来。"
        )

    # ③ 原因 —— 让人区分"方法"与"别的原因"
    facts_by_key["why"].append(
        "★ 分一分：哪些是**方法**的问题（换一种学法就会不一样），"
        "哪些是**当时条件**的问题（时间、精力、环境）。"
        "混在一起的话，下一次会改错东西。"
    )

    # ④ 下次改变 —— 接上这次的真实材料
    if done:
        facts_by_key["next_change"].append(
            f"这次走到第 {len(done)} 步就停了 —— 下次从那里接，还是换个走法？"
        )
    facts_by_key["next_change"].append("写成一句能直接执行的话（不是「要多注意」这种）。")

    answered = {key for key, value in (answers or {}).items() if (value or "").strip()}
    if answers is None:
        # 并集：这次会话里**任何一条**感想写过的项都算写过。
        answered = {
            key
            for row in reads.list_reflections(db, session_id)
            for key, value in (row.answers or {}).items()
            if (value or "").strip()
        }
    items = tuple(
        GuideItem(
            key=element.key,
            label=element.label,
            question=element.lead,
            caution=element.caution,
            facts=tuple(facts_by_key[element.key]),
            answered=element.key in answered,
        )
        for element in ELEMENTS
    )

    return ReflectionGuide(
        session_id=session_id,
        items=items,
        existing=len(list_reflections(db, session_id)),
        blocked_reason=_blocked_reason(session, summary),
    )


def _blocked_reason(session: Any, summary: Any) -> str:
    """现在能不能写感想。**不能**时要给出一条能照做的原因。

    ★ 为什么要求"先有复盘总结"：感想是**对复盘的补充**，不是替代。
    允许在没有复盘时写感想，会让"复盘产出"这件事有两个入口，
    而 `V-PAR` 要评的正是那份产物的完整度 —— 两个入口意味着
    同一件事有两种形态，而它们无法互相比较。
    """
    from app.session_domain.enums import SessionState

    state = getattr(session, "state", None)
    if state is SessionState.DRAFT:
        return "会话还在起草阶段：先让它跑起来，才有东西可复盘。"
    if state is SessionState.ACTIVE:
        return (
            "感想是**复盘之后**的动作。执行中想记就写笔记（`POST /notes`）—— "
            "先走「复盘 → 完成」，再来这里写。"
        )
    if summary is None:
        return (
            "这个会话还没有复盘总结。先写下（或让助手写下）总结，"
            "感想才有可对照的对象。"
        )
    return ""


def compose(answers: dict[str, str], content: str | None = None) -> str:
    """把四要素拼成一段文本。**只在调用方没给正文时用**。

    ★ 拼装是**呈现**，不是生成：四个小标题 + 用户的原话，
    一个字的加工都不加。感想里出现模型的语言，它就不再是"他的感想"了。
    """
    if content and content.strip():
        return content.strip()
    blocks: list[str] = []
    for element in ELEMENTS:
        value = (answers.get(element.key) or "").strip()
        if value:
            blocks.append(f"【{element.label}】{value}")
    return "\n".join(blocks)


def list_reflections(db: DbSession, session_id: str) -> list[Any]:
    return reads.list_reflections(db, session_id)


__all__ = [
    "ELEMENTS",
    "Element",
    "GuideItem",
    "ReflectionGuide",
    "build_guide",
    "compose",
    "list_reflections",
]
