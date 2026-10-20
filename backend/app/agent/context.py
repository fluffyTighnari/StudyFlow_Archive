"""对话的记忆与上下文计量 —— session 与侧边栏**共用同一套**（P6+.6）。

## 这个模块解决的那一件事

在此之前，历史是一个硬截断：`HISTORY_LIMIT = 6`，取最近 6 条原话。
它的坏处不是"记得少"，而是**记忆的形状是错的**：

- 第 7 轮之前说过的约束、约定、用户明确表达过的偏好，**一律消失**；
- 而消失是**静默**的 —— 模型不会说"我不记得了"，它会基于残缺的上下文
  给出一个看起来正常的回答。用户看到的现象是"聊着聊着它开始胡说"，
  而原因（六条窗口）在界面上没有任何痕迹。

现在换成：**能放多少放多少，放不下的压成摘要，并且把用量摊开给用户看。**

## ★ 计量是实测的，不是估算的

`num_ctx` 是窗口上限，而模型每次调用都会返回真实的 `prompt_eval_count`
（落进 `chat_message.prompt_tokens`）。所以「当前上下文用了多少」这个问题
**有真实答案**，本模块因此**不做任何 token 估算**。

这一点值得强调，因为估算看起来很诱人（省一次查询）而错法很隐蔽：
中文的 token/字符比取决于分词器，估小了会**等到装不下才发现**，
而那时的现象是模型返回空回复（见 `OllamaClient` 里 `done_reason=length`
那段诊断）。与其猜，不如用上一轮的实测值 —— 它就在库里。

代价是**滞后一轮**：压缩发生在"上一次调用已经超线"之后。这可以接受，
因为压缩的触发线（见 `COMPACT_AT`）留了 25% 的余量，
而一次对话的一轮不可能在一瞬间吃掉 25% 的窗口。

## 压缩的自我纠正

拆分点是**按字符占比**定的（`_split_index`），因为逐条消息的 token 数
并没有被单独测量过。所以一次压缩可能压得不够。这没关系：
下一轮的真实 `prompt_eval_count` 会告诉我们还超不超线，超了就再压一次。
**机制不依赖拆分点算得准**，只依赖"超线就压"这一条。

## 摘要不是原话，所以它绝不进全局

摘要是**派生文本**。被压掉的原话仍然在 `chat_message` 里（本模块从不删除
任何行），但装配后的上下文里那一段只剩概括。

由此有一条必须成立的边界：**任何"用户说过……"的全局写入都不能以摘要为来源。**
结构上它自动成立 —— 摘要住在 `context_compact` 表，而按原话核对走的是
`chat_message.msg_id`，摘要拿不出 id。这条由测试钉住（见
`tests/agent/test_context_memory.py`）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.agent.llm import ChatMessage, LLMClient, LLMError
from app.session_domain import reads, writes
from app.session_domain.enums import ContextScope
from app.session_domain.models import ChatMessage as ChatMessageRow

#: 上下文用量到窗口的这个比例时开始压缩。
#:
#: 为什么不是 1.0：压缩本身要花一次模型调用，而它必须在**装不下之前**发生。
#: 留 25% 的余量给"这一轮还会加进来的东西" —— 工具结果可以很长
#: （读一份资料、拉一次计划），而它们的长度在压缩发生时还不知道。
COMPACT_AT = 0.75

#: 压缩后希望降到窗口的这个比例。
#:
#: 压到刚好 `COMPACT_AT` 之下会有个坏结果：下一轮马上又触发一次压缩，
#: 于是每一轮都在压 —— 每次压都要重写一遍摘要，代价是双倍的模型调用，
#: 而用户看到的现象是"越聊越慢"。压到 0.40 换来的是"很久才压一次"。
COMPACT_TO = 0.40

#: 无论怎么压，都要留下这么多条**原文**。
#:
#: 这是"压缩"与"失忆"的分界。如果预算紧到连最近几条都放不下，
#: 那就不是压缩能解决的问题（prompt 本身就超了），此时应如实报 "hard"。
MIN_LIVE_MESSAGES = 4

#: 摘要层最多留几段。超过就折叠最旧的两段（见 `_plan_compaction`）。
#:
#: 4 是个经验值：它让"很久以前的对话"仍有痕迹，而摘要总量不至于
#: 自己顶到窗口的 20% 以上。
MAX_SUMMARIES = 4

#: 压进上下文时，摘要段的抬头。
#:
#: **必须显眼**：模型要能分辨"这是概括"与"这是用户的原话"。
#: 不标的话它会引用摘要里的措辞，而用户会以为那话是自己说的。
SUMMARY_HEADER = "【以下是本次对话较早部分的压缩摘要（不是原话）】"

COMPACT_SYSTEM_PROMPT = """你在压缩一段对话的早期部分，以便它还能留在上下文里。

要求：
1. **只写对话里真实出现过的内容**，不推测、不补充、不美化。
2. 优先保留：用户明确表达的偏好与约束、已经定下的结论、尚未解决的疑问、
   用户纠正过你的地方、以及任何"以后再聊到就还用得上"的事实。
3. 丢掉：寒暄、重复、已经被后续对话推翻的中间结论。
4. 用紧凑的条目式中文写，不要小标题、不要客套。
5. 如果某件事你不确定，就写"用户提过但未确认：…"，**不要写成确定的事实**。

你的输出会直接替换掉那些原话，所以**漏掉一件事的代价是它永久消失**。"""


@dataclass(frozen=True)
class ContextMeter:
    """当前对话的上下文用量。**给用户看的**，所以它必须能被解释。

    `used == 0` 表示**还没测过**（这个对话还没发生过一次成功的模型调用）。
    这与"用量是 0"不同：后者意味着"什么都没发"，
    而前者意味着"我们手里还没有读数"。界面上这两种要说不同的话。
    """

    #: 上一次模型调用**实测**的 prompt token 数。0 = 还没测过。
    used: int
    #: 窗口上限（`num_ctx`）。
    limit: int
    #: 当前参与上下文的原文条数。
    live_messages: int
    #: 被压成摘要的原文条数。
    compacted_messages: int
    #: 当前有效的摘要段数。
    summaries: int
    #: 累计压缩次数（含被折叠的）。
    compact_count: int
    #: ★ **压得动吗**：现在真的存在"可以拿掉的东西"吗。
    #:
    #: 这个字段是被一次真实的测试数据逼出来的：把 `used` 设成 10001、
    #: 而对话只有十几句短话时，机制**正确地拒绝压缩** —— 因为那 10001 个
    #: token 里几乎没有历史的位置，大头是 system 提示与上下文包。
    #: 把它们压掉是不可能的：历史本来就没占多少。
    #:
    #: 于是 `over_line=True, compactable=False` 是一个**真实的、且必须说出口的**
    #: 状态：窗口要满了，但**压缩解决不了** —— 该做的是调大 `num_ctx`
    #: 或者精简上下文包。没有这个字段的话，界面只能显示"正在压缩"，
    #: 而它其实什么也没压，用户会以为是卡住了。
    compactable: bool

    @property
    def measured(self) -> bool:
        return self.used > 0

    @property
    def ratio(self) -> float:
        if self.limit <= 0:
            return 0.0
        return self.used / self.limit

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def over_line(self) -> bool:
        """该压缩了吗。**没有读数时恒为 False** —— 见模块文档"滞后一轮"。"""
        return self.measured and self.ratio >= COMPACT_AT

    @property
    def pressure(self) -> str:
        """`ok` / `near` / `over` / `unmeasured`。

        四态而不是布尔：界面要能区分"还宽裕"、"快满了"、"已经超线（正在压）"
        与"还没有读数"，而这四种情况下该显示的话完全不同。
        """
        if not self.measured:
            return "unmeasured"
        if self.ratio >= COMPACT_AT:
            return "over"
        if self.ratio >= COMPACT_AT * 0.7:
            return "near"
        return "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
            "ratio": round(self.ratio, 4),
            "pressure": self.pressure,
            "measured": self.measured,
            "live_messages": self.live_messages,
            "compacted_messages": self.compacted_messages,
            "summaries": self.summaries,
            "compact_count": self.compact_count,
            "compactable": self.compactable,
        }


@dataclass(frozen=True)
class Memory:
    """装配好的一段记忆：摘要 + 还在的原文。"""

    history: list[ChatMessage]
    meter: ContextMeter
    #: 本次调用**新产生**的压缩。没有则 `None`。
    new_compact_id: str | None = None
    #: 压缩失败时的原因（模型挂了）。**不吞掉** —— 见 `build_memory`。
    compact_error: str | None = None


def _row_to_chat(row: ChatMessageRow) -> ChatMessage:
    """落库的消息 → 发出去的消息。

    ★ **刻意不带 `thinking`**。思考过程是留档给用户看的；把它喂回模型
    会让每轮上下文翻倍，还会让模型把自己上一轮的思路当成"已经确认的事"。
    这条边界在这里只有一处实现，因此它不会在某个新调用点上被忘掉。
    """
    if row.role.value == "assistant":
        return ChatMessage.assistant(row.content)
    return ChatMessage.user(row.content)


def _last_measured_tokens(db: Session, scope: ContextScope, scope_id: str) -> int:
    """取**最近一次实测的** prompt token 数。

    从全部消息里找（不只是还活着的），因为一个刚被压缩过的对话里，
    最后一条带读数的消息可能恰好是被压掉的那条助手回复 ——
    而那个读数仍然是当前窗口用量的最好证据。

    找不到就返回 `0`（还没测过）。**不返回估算值** —— 见模块文档。
    """
    for row in reversed(reads.list_messages(db, scope, scope_id)):
        if row.prompt_tokens is not None:
            return int(row.prompt_tokens)
    return 0


def _plan(
    live: list[ChatMessageRow],
    summaries: list[Any],
    limit: int,
    used: int,
) -> tuple[list[ChatMessageRow], list[Any]]:
    """决定这次压缩吃哪些东西：`(更早的原文, 被折叠的旧摘要)`。

    ★ **只有这一个地方做这个决定。** `build_meter` 要用它回答"压得动吗"，
    `build_memory` 要用它决定吃什么 —— 两处各写一遍必然会出现
    "界面说可以压、而实际什么也没压"，而那种不一致看起来像卡住了。

    ## ★ 为什么是"按比例砍"，而不是"按 token 预算换算字符"

    早先这里写的是 `keep_chars = limit * COMPACT_TO * 3` —— 用
    "1 token ≈ 3 字符"把窗口换算成字符数。**那是错的，而且在中文上错得很远**：
    中文的 token/字符比接近 1（一个汉字常常就是一两个 token），
    于是那个 `* 3` 把预算放宽了三倍，"该压"的对话被判成"压得动也不用压"。

    这个错误被一组测试数据当场抓住：15 条 400 字的消息、实测 10001 token、
    窗口 10000 —— 机制**拒绝压缩**，因为 6000 字符远小于算出来的 36000。

    现在的做法不换算任何单位：

        要留下的比例 = (窗口 × COMPACT_TO) / 实测用量

    它是**两个 token 数之比**，是无量纲的；再把这个比例乘到历史的字符数上。
    于是"该砍掉多少"不依赖任何关于 tokenizer 的假设 ——
    只依赖"历史与整个 prompt 大致同比例"这一个弱假设，
    而它错了也没关系（见模块文档"压缩的自我纠正"）。

    两者都为空是**一个合法的结论**，不是缺陷：它表示窗口被
    system 提示与上下文包占满了，而历史本来就没占多少。
    """
    # 摘要本身也占地方，所以先把它已经占掉的部分从预算里扣掉。
    summary_chars = sum(len(row.summary) for row in summaries) + len(SUMMARY_HEADER)
    total_chars = sum(len(row.content) for row in live)
    # `used` 已经保证 > 0（`over_line` 要求有读数），所以这里不会除零。
    keep_ratio = min(1.0, (limit * COMPACT_TO) / used) if used > 0 else 0.0
    keep_chars = max(0, int(total_chars * keep_ratio) - summary_chars)
    index = _split_index(live, keep_chars=keep_chars)
    # 无论怎么压，最近 MIN_LIVE_MESSAGES 条正文必须留下。
    must_keep = min(len(live), MIN_LIVE_MESSAGES)
    can_cover = max(0, min(index, len(live) - must_keep))
    older = live[:can_cover]
    # ★ 折叠与"压原文"是**两件独立的事，可以同时发生**。
    #
    # 早先这里写的是"`not older` 时才折叠"（即原文没得压了才动摘要层）。
    # 那个条件在现实中几乎永远不成立：只要对话还在继续，总有一两条更早的
    # 原文可以压，于是 `older` 一直非空，而**摘要层就永远不折** ——
    # 它一路长下去，最后自己把窗口撑满。这正是折叠机制本来要防的事。
    #
    # `+ 1` 是因为这一次压缩还会**新增**一段摘要，所以要让
    # "折叠掉若干段之后再新增一段"的结果不超过上限。
    folded: list[Any] = []
    target = len(summaries) + 1 - MAX_SUMMARIES
    if target > 0:
        folded = summaries[:target]
    return older, folded


def _meter(
    db: Session,
    scope: ContextScope,
    scope_id: str,
    limit: int,
    *,
    live: list[ChatMessageRow] | None = None,
) -> ContextMeter:
    live = reads.list_live_messages(db, scope, scope_id) if live is None else live
    summaries = reads.live_compacts(db, scope, scope_id)
    used = _last_measured_tokens(db, scope, scope_id)
    older, folded = _plan(live, summaries, limit, used)
    return ContextMeter(
        used=used,
        limit=limit,
        live_messages=len(live),
        compacted_messages=len(reads.compacted_message_ids(db, scope, scope_id)),
        summaries=len(summaries),
        compact_count=len(reads.list_compacts(db, scope, scope_id)),
        compactable=bool(older or folded),
    )


def build_meter(
    db: Session, scope: ContextScope, scope_id: str, *, limit: int
) -> ContextMeter:
    """算当前用量。**在任何写入之前调用** —— 它反映的是"上一次调用时"的状态。"""
    return _meter(db, scope, scope_id, limit)


def _split_index(live: list[ChatMessageRow], *, keep_chars: int) -> int:
    """决定"从第几条开始留原文"。

    从末尾往前累加字符数，直到超过 `keep_chars`。**按字符而不是按 token**：
    逐条消息的 token 数没有被单独测量过（我们只测整个 prompt）。
    这个近似**不需要准** —— 见模块文档"压缩的自我纠正"。
    """
    total = 0
    index = len(live)
    for i in range(len(live) - 1, -1, -1):
        total += len(live[i].content)
        if total > keep_chars:
            # 这一条已经把预算撑破了，但它本身要留着（否则可能一条都不留）
            index = i + 1
            break
        index = i
    return index


def _summarize(
    client: LLMClient,
    *,
    older: list[ChatMessageRow],
    folded: list[Any],
) -> str:
    """把"旧摘要 + 更早的原文"压成一段。

    失败时**抛出去让调用方决定**，而不是在这里返回一个占位摘要：
    一个"（压缩失败）"的摘要会被当成真的记忆继续往下传，
    而那比不压缩坏得多 —— 它是**静默失忆**。
    """
    parts: list[str] = []
    for row in folded:
        parts.append(f"[此前的摘要 {row.compact_id}]\n{row.summary}")
    for row in older:
        speaker = "用户" if row.role.value == "user" else "助手"
        parts.append(f"[{speaker} {row.msg_id}] {row.content}")
    body = "\n\n".join(parts)
    reply = client.chat(
        [
            ChatMessage.system(COMPACT_SYSTEM_PROMPT),
            ChatMessage.user(f"请压缩以下内容：\n\n{body}"),
        ]
    )
    return reply.content.strip()


def build_memory(
    db: Session,
    scope: ContextScope,
    scope_id: str,
    *,
    client: LLMClient,
    limit: int,
    model: str = "",
    allow_compact: bool = True,
) -> Memory:
    """装配一段记忆：**必要时先压缩，再返回要带进上下文的那些消息**。

    ## 顺序为什么是"先压后取"

    压缩会改变"还剩哪些原文"，所以必须在取原文**之前**做完。
    反过来（先取再压）会让本次调用仍然带着即将被压掉的那些原话，
    于是这次调用**注定还是超线** —— 压缩要等下一轮才生效，
    而那正是"越聊越慢"的成因。

    ## 压缩失败怎么办

    **照常继续，但把原因带回去**（`Memory.compact_error`）。
    不阻断讨论：用户想聊的是他的事，而"这次压缩没成功"是系统的内部状况。
    但它必须**可见** —— 一个失败的压缩会让上下文继续膨胀，
    而下一次就会撞上真正的装不下。把它藏起来等于让用户自己发现。
    """
    live = reads.list_live_messages(db, scope, scope_id)
    live_summaries = reads.live_compacts(db, scope, scope_id)
    meter = _meter(db, scope, scope_id, limit, live=live)

    new_compact_id: str | None = None
    compact_error: str | None = None

    if allow_compact and meter.over_line:
        used = meter.used
        older, folded = _plan(live, live_summaries, limit, used)
        if not older and not folded:
            # ★ 超线了但**压不动**。这不是错误，是一个必须说出口的真实结论：
            # 窗口被 system 提示与上下文包占满，而历史本来就没占多少 ——
            # 压缩在这里帮不上忙，该做的是调大 `num_ctx`。
            # `meter.compactable` 已经是 False，界面据此显示不同的话。
            pass
        else:
            try:
                summary = _summarize(client, older=older, folded=folded)
            except LLMError as exc:
                compact_error = f"压缩失败（{type(exc).__name__}）：{exc}"
            else:
                row = writes.add_compact(
                    db,
                    scope,
                    scope_id,
                    covers_msg_ids=[m.msg_id for m in older],
                    folds_compact_ids=[c.compact_id for c in folded],
                    summary=summary,
                    prompt_tokens_before=used,
                    limit_at_time=limit,
                    model=model,
                )
                new_compact_id = row.compact_id
                # 重新取：压缩已经改变了"还剩哪些原文"
                live = reads.list_live_messages(db, scope, scope_id)
                live_summaries = reads.live_compacts(db, scope, scope_id)
                meter = _meter(db, scope, scope_id, limit, live=live)

    history: list[ChatMessage] = []
    if live_summaries:
        text = "\n\n".join(
            f"[摘要 {row.compact_id}] {row.summary}" for row in live_summaries
        )
        history.append(ChatMessage.system(f"{SUMMARY_HEADER}\n{text}"))
    history.extend(_row_to_chat(row) for row in live)
    return Memory(
        history=history,
        meter=meter,
        new_compact_id=new_compact_id,
        compact_error=compact_error,
    )


def record_usage(
    db: Session,
    scope: ContextScope,
    scope_id: str,
    *,
    meta: dict[str, Any],
) -> None:
    """把这次调用**实测的用量回填**到最后一条助手消息上。

    为什么回填而不是单独一张用量表：读数的归属就是**那一次回复** ——
    "这条回答消耗了多少上下文"是最自然的问法，而单独一张表会逼出
    一个"用 msg_id 去 join"的查询，那个 join 的唯一作用就是重建
    本来就在一起的两个字段。

    `prompt_eval_count` 缺席时**什么都不写**（保持 `None`）。
    写 0 会把"没测到"伪装成"用量是 0"，而那两个结论相反。
    """
    prompt_tokens = meta.get("prompt_eval_count")
    completion_tokens = meta.get("eval_count")
    if prompt_tokens is None and completion_tokens is None:
        return
    for row in reversed(reads.list_messages(db, scope, scope_id)):
        if row.role.value == "assistant":
            if prompt_tokens is not None:
                row.prompt_tokens = int(prompt_tokens)
            if completion_tokens is not None:
                row.completion_tokens = int(completion_tokens)
            db.flush()
            return
