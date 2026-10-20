"""「凭什么这么说」—— 把一条助手回复**当时手里有什么**摊开（P15）。

## 这个模块存在的唯一理由

用户问「你凭什么这么说」时，有两种回答方式，而且只有一种是对的：

- ✗ **让模型再解释一遍**。它会生成一段听起来合理的话，
  而那段话与它当时真实的依据可能毫无关系 —— 用户**分不出来**。
  这是这套系统里反复出现的那类失败：看起来像解释，实际是新编的事实。
- ✓ **把当时真的发生过的东西摊开**：这条回复背后的工具轨迹、
  从轨迹里能抽出的条目 id、以及现在重放出来的上下文节
  （**标明这是重放的**，不是当时的快照）。

所以这个模块**不调用模型**，也不写任何库。它只做一件事：
把已经落库的事实重新组织成"可核对的一组东西"。

## 三条诚实性要求

1. **说清它能证明什么、不能证明什么。** `limits` 那一句必须跟着返回：
   它能告诉你"它当时读到了什么"，**不能**告诉你"它为什么选了这个措辞"。
   把这句省掉，用户会把"有依据"读成"这个结论是对的"——它们是两件事。
2. **区分当时与现在。** 工具轨迹是当时落库的（`replayed_now=False`）；
   上下文节是**现在重算**的（`replayed_now=True`）。两者混在一起，
   会让"当时它看到了 X"变成一句无法核对的话。
3. **`空` 不是 `没查到`。** 一条没调过任何工具的回复，它的依据就是
   "当时的上下文 + 它自己的话"，这件事要**说出来**，而不是给一个空列表
   （空列表会被读成"没有依据"）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.session_domain.context import assemble_discussion_context
from app.session_domain.models import ChatMessage

#: 从轨迹的文本里能抽出来的**条目 id**。
#:
#: 覆盖的是这套系统真实用到的几类：`P-0001`（偏好）、`C-0001`（证据）、
#: `H-0001`（假说）、`UP-0001`（提案）、`ST-001`/`CP-001`（计划步骤/检查点）、
#: `N-0001`（笔记）、`AT-0001`（资料）、`CL-0001`（澄清）、`RL-0001`（路由日志）。
#: `M1` 的取值没有 id（它是 `V-XX|channel`），所以另列。
_ENTRY_ID = re.compile(
    r"\b(?:P|C|H|UP|ST|CP|N|AT|CL|RL|M1O|M1B)-\d{3,4}\b"
)

#: `V-SE｜measured` 这种 M1 坐标（取值本身没有 id，用坐标指认）。
_M1_COORD = re.compile(r"\bV-[A-Z]{2}\b")

#: 一条回复最多列多少个引用条目 —— 超出的部分只报数量。
#: ★ 截断必须**说出来**（`entries_truncated`），否则用户会以为那是全部。
MAX_ENTRIES = 30


@dataclass(frozen=True)
class TraceFact:
    """轨迹里的一步。**逐字来自落库的 `tool_trace`**，不做加工。"""

    step: int
    tool: str
    scope: str
    status: str
    message: str
    arguments: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status not in {"success", "empty"}


@dataclass(frozen=True)
class ContextReplay:
    """**现在重放**上下文得到的结果，连同"没重放成"的原因。

    ★ 为什么"没重放成"要带一句原因，而不是给一个空列表：
    空列表会被读成「它当时没有上下文」—— 而真实情况可能是
    「这条消息写下时还没有记录锚点」。这两件事的含义完全不同。
    """

    step_id: str | None
    sections: tuple[str, ...]
    reason: str

    @property
    def replayed(self) -> bool:
        return bool(self.sections)


@dataclass(frozen=True)
class MessageBasis:
    """一条助手回复的依据。**全是可核对的事实**，没有一句推断。"""

    msg_id: str
    #: 这条回复背后真实执行过的工具（`tool_trace`）。
    traces: tuple[TraceFact, ...]
    #: 轨迹里出现过的条目 id（去重、保序）—— 这就是"它当时引用了哪几条"。
    entries: tuple[str, ...]
    entries_truncated: bool = False
    #: 上下文重放的结果（含"重放不成"的原因）。
    context: ContextReplay | None = None
    #: 这次标注对应的那句话（若调用方给了）。
    quote: str = ""
    question: str = ""

    def summary(self) -> str:
        """给界面的一句话。**不含结论** —— 它只描述证据的形状。"""
        if not self.traces:
            return (
                "这条回复没有调用任何工具：它的依据是当时装配的上下文"
                "（会话状态 + 检索结果）与它自己的话。"
            )
        failed = [fact for fact in self.traces if fact.failed]
        head = f"这条回复背后执行了 {len(self.traces)} 步工具"
        if failed:
            head += f"，其中 {len(failed)} 步没有成功"
        if self.entries:
            head += f"；轨迹里出现 {len(self.entries)} 个条目 id"
        return head + "。"

    def as_dict(self) -> dict[str, Any]:
        """`kind` 字段是给界面用的：每一块都要标出**它是哪一类事实**。"""
        replay = self.context
        return {
            "msg_id": self.msg_id,
            "quote": self.quote,
            "question": self.question,
            "summary": self.summary(),
            "traces": [
                {
                    "step": fact.step,
                    "tool": fact.tool,
                    "scope": fact.scope,
                    "status": fact.status,
                    "message": fact.message,
                    "arguments": fact.arguments,
                }
                for fact in self.traces
            ],
            "entries": list(self.entries),
            "entries_truncated": self.entries_truncated,
            "context_step_id": replay.step_id if replay else None,
            "context_sections": list(replay.sections) if replay else [],
            "context_note": replay.reason if replay else "",
            "context_replayed_now": True,
            "limits": LIMITS,
        }


#: ★ 这句话**必须**跟着依据一起返回。
#:
#: 它不是免责声明，而是这套系统对"解释"的一贯立场：
#: 能核对的是**它当时读到了什么**，不能核对的是**它为什么这么写**。
#: 把这条界线说清楚，用户才能正确地使用这份依据 ——
#: 有依据 ≠ 结论对。
LIMITS = (
    "这份依据能回答「它当时读到了什么、做了什么」——"
    "工具轨迹逐条落库、条目 id 可回到原条目、上下文节可以现场重放。"
    "它**不能**回答「它为什么选了这个措辞」：那发生在模型内部，"
    "没有落库，任何看起来像解释的话都是**事后新编的**，所以这里不编。"
    "★ 有依据 ≠ 这个结论对：依据说的是它手里有什么，不是它判断得对。"
)


def explain(
    db: DbSession,
    message: ChatMessage,
    *,
    quote: str = "",
    question: str = "",
) -> MessageBasis:
    """组装一条回复的依据。**纯读**：不写库、不调模型。"""
    traces = tuple(_trace_facts(message.tool_trace or []))
    entries, truncated = _entries_of(traces)
    return MessageBasis(
        msg_id=message.msg_id,
        traces=traces,
        entries=entries,
        entries_truncated=truncated,
        context=_replay_context(db, message),
        quote=quote,
        question=question,
    )


def _trace_facts(raw: list[dict[str, Any]]) -> list[TraceFact]:
    out: list[TraceFact] = []
    for index, row in enumerate(raw, start=1):
        if not isinstance(row, dict):
            continue
        arguments = row.get("arguments")
        out.append(
            TraceFact(
                step=int(row.get("step") or index),
                tool=str(row.get("tool") or "未知工具"),
                scope=str(row.get("scope") or "unknown"),
                status=str(row.get("status") or "unknown"),
                message=str(row.get("message") or ""),
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )
    return out


def _entries_of(traces: tuple[TraceFact, ...]) -> tuple[tuple[str, ...], bool]:
    """把轨迹里出现过的条目 id 抽出来（去重、保序）。

    ★ 抽的是**轨迹文本**，不是"查一遍数据库看有哪些条目"：
    后者会带进与这条回复无关的条目，而这份依据的全部价值就是"当时"。
    """
    seen: dict[str, None] = {}
    for fact in traces:
        blob = f"{fact.message} {fact.arguments}"
        for match in _ENTRY_ID.findall(blob):
            seen.setdefault(match, None)
    ordered = list(seen)
    if len(ordered) > MAX_ENTRIES:
        return tuple(ordered[:MAX_ENTRIES]), True
    return tuple(ordered), False


def _replay_context(db: DbSession, message: ChatMessage) -> ContextReplay:
    """按**这条消息落库时的锚点**重放上下文，只取**节标题**。

    ★ 只取标题、不取正文：正文会把人淹没，而标题已经能回答
    "它当时能看到哪几块"。而且这是**重放**的结果 ——
    重放出来的正文与当时那段文本很可能不同（会话状态变了），
    把正文端出去只会造成"你看，当时这里写的是…"这种**错误的引用**。

    ★ 锚点为空时**不猜**。过去的写法是按今天的规则挑第一个未完成的步骤，
    那样挑出来的节标题会被读成"当时的证据" —— 一个字段（`anchor_step_id`）
    换掉这个歧义。旧的（加字段之前的）消息因此如实报告"没记录锚点"。
    """
    if not message.anchor_step_id:
        return ContextReplay(
            step_id=None,
            sections=(),
            reason=(
                "这条消息没有记录锚点（它写下时还没有这个字段），"
                "所以不按今天的规则猜一个 —— 猜出来的节标题会看起来像当时的证据。"
                "下方的工具轨迹仍然是**当时真实发生**的。"
            ),
        )
    try:
        context = assemble_discussion_context(db, message.session_id, message.anchor_step_id)
    except Exception as exc:  # noqa: BLE001 - 计划被删/步骤没了都会走到这里
        return ContextReplay(
            step_id=message.anchor_step_id,
            sections=(),
            reason=(
                f"锚点记的是 `{message.anchor_step_id}`，但按它重放不成了"
                f"（{type(exc).__name__}）—— 计划可能已经改版或被删。"
                "工具轨迹仍然是当时真实发生的。"
            ),
        )
    sections = tuple(
        sorted(
            (
                f"{section.name}（{section.item_count} 条"
                + (f"，另有 {section.dropped_count} 条没装下" if section.dropped_count else "")
                + "）"
            )
            for section in context.sections
        )
    )
    return ContextReplay(
        step_id=message.anchor_step_id,
        sections=sections,
        reason=(
            "这些节标题是按**这条消息记下的锚点**现在重放的；"
            "当时装配出来的那段正文没有落库（它每轮都不同），所以标题相同、内容未必逐字相同。"
        ),
    )


__all__ = [
    "LIMITS",
    "MAX_ENTRIES",
    "ContextReplay",
    "MessageBasis",
    "TraceFact",
    "explain",
]
