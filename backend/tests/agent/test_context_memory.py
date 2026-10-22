"""对话记忆与上下文计量（P6+.6，session 与侧边栏共用）。

## 这组测试守的那一件事

在此之前记忆是一个硬截断：最近 6 条原话。要证明"它真的变成记忆了"，
只需要证明**第 7 条之前的东西还在**（`test_it_remembers_more_than_a_handful`）。

但"记得多"本身不是目的 —— 目的是**记得多而不撑爆窗口**。
所以真正的中心用例是压缩那一组：它必须同时做到

- 装不下时**自动压**（`test_it_compacts_when_the_measured_usage_crosses_the_line`）；
- 压掉的原话**一行都没删**（`test_compaction_deletes_nothing`）；
- 摘要在上下文里**明确标着"不是原话"**（`test_the_summary_is_marked_as_derived`）。

## 计量是实测的，所以这些测试必须喂真实的读数

`prompt_tokens` 只有模型能提供。测试里用一个**明确的数字**写进去，
而不是让代码去估算 —— 那正是被测代码不该做的事。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.agent.context import (
    COMPACT_AT,
    COMPACT_TO,
    MIN_LIVE_MESSAGES,
    SUMMARY_HEADER,
    build_memory,
    build_meter,
    record_usage,
)
from app.agent.llm import ChatMessage, LLMError, LLMReply
from app.core.errors import InvalidField, MissingPrerequisite, StateNotWritable
from app.session_domain import reads, writes
from app.session_domain.enums import ChatRole, ContextScope, SIDEBAR_SCOPE_ID
from app.session_domain.models import ChatMessage as ChatMessageRow
from app.session_domain.models import ContextCompact

LIMIT = 10000


class FakeClient:
    """一个只会说固定话的模型。

    `replies` 用尽之后重复最后一条 —— 测试不该因为"调用次数比预期多一次"
    而炸在 IndexError 上，那种失败看起来像业务缺陷。
    """

    num_ctx = LIMIT

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies) or ["（摘要）"]
        self.seen: list[list[ChatMessage]] = []

    def chat(self, messages, tools=()):  # noqa: ANN001, ANN201
        self.seen.append(list(messages))
        text = self.replies[min(len(self.seen) - 1, len(self.replies) - 1)]
        return LLMReply(content=text, meta={"prompt_eval_count": 1, "eval_count": 1})

    def stream(self, messages, tools=()):  # noqa: ANN001, ANN201
        raise AssertionError("记忆机制不该走流式")

    def list_models(self):  # noqa: ANN201
        return []


class BoomClient(FakeClient):
    def chat(self, messages, tools=()):  # noqa: ANN001, ANN201
        raise LLMError("模型连不上")


def _say(db, scope_id: str, *texts: str, role: ChatRole = ChatRole.USER) -> list[str]:
    """往对话里塞几条消息，返回它们的 id。"""
    ids = []
    for text in texts:
        row = writes.add_chat_message(db, scope_id, content=text, role=role)
        ids.append(row.msg_id)
    return ids


def _measure(db, scope_id: str, tokens: int) -> None:
    """把一条**实测的**用量写到最近一条助手消息上。

    这是唯一被允许的写入方式 —— 整个机制没有估算路径。
    """
    row = writes.add_chat_message(
        db, scope_id, content="（助手回复）", role=ChatRole.ASSISTANT
    )
    row.prompt_tokens = tokens
    db.flush()


def _long(text: str, n: int = 400) -> str:
    """一条**够长**的消息。

    ★ 长度在这里不是装饰：`_plan` 的目标是"把上下文降到窗口的 40%"，
    而 400 字符 × 若干条才可能超过那个预算。用短消息做压缩测试会
    得到"压不动"（那也是一个正确结论，但测的不是同一件事）。
    """
    return text + "。" * n


# --------------------------------------------------------------------------- #
# 记忆：不再硬截断
# --------------------------------------------------------------------------- #


def test_it_remembers_more_than_a_handful(sandbox) -> None:
    """★ 中心用例之一：第 7 条之前的话**还在**。

    旧实现取最近 6 条，所以下面第 1 条一定会消失。这条测试就是为它写的。
    """
    db, sid = sandbox.db, sandbox.session_id
    texts = [f"第 {i} 件事你要记住" for i in range(1, 13)]
    _say(db, sid, *texts)

    memory = build_memory(
        db, ContextScope.SESSION, sid, client=FakeClient(), limit=LIMIT
    )
    joined = "\n".join(m.content for m in memory.history)
    for text in texts:
        assert text in joined, f"{text} 丢了 —— 又回到硬截断了"
    # 而且它不该被压缩（没有读数 → 不知道超没超 → 不压）
    assert memory.new_compact_id is None
    assert memory.meter.used == 0
    assert memory.meter.pressure == "unmeasured"


def test_thinking_never_goes_back_into_the_context(sandbox) -> None:
    """★ 思考过程是留档给用户看的，**不是**喂回模型的材料。

    喂回去有两个坏处：每轮上下文翻倍，以及模型会把自己上一轮的思路
    当成"已经确认的事"。两者都不报错。
    """
    db, sid = sandbox.db, sandbox.session_id
    _say(db, sid, "先看目录")
    row = writes.add_chat_message(
        db,
        sid,
        content="我看了目录。",
        role=ChatRole.ASSISTANT,
        thinking="我觉得应该先看 unit 1",
    )
    assert row.thinking == "我觉得应该先看 unit 1"

    memory = build_memory(
        db, ContextScope.SESSION, sid, client=FakeClient(), limit=LIMIT
    )
    assert "我觉得应该先看 unit 1" not in "\n".join(m.content for m in memory.history)


# --------------------------------------------------------------------------- #
# 压缩：触发、留痕、不删
# --------------------------------------------------------------------------- #


def test_it_does_not_compact_before_any_measurement(sandbox) -> None:
    """★ 没有读数就不压 —— 这是"实测而非估算"的直接推论。

    如果实现偷偷按字符数估算，这条会失败。而估算的错法恰好是最坏的：
    估小了会等到装不下才发现，那时的现象是模型返回空回复。
    """
    db, sid = sandbox.db, sandbox.session_id
    _say(db, sid, *[f"第 {i} 句" * 50 for i in range(20)])
    memory = build_memory(
        db, ContextScope.SESSION, sid, client=FakeClient(), limit=LIMIT
    )
    assert memory.meter.used == 0
    assert memory.new_compact_id is None
    assert db.scalars(select(ContextCompact)).all() == []


def test_it_does_not_compact_below_the_line(sandbox) -> None:
    """实测值没超线时**不许压**。

    没有这一条，"每次都压"的实现也能让上面那些测试通过 ——
    而每次压都要多花一次模型调用，用户看到的现象是"越聊越慢"。
    """
    db, sid = sandbox.db, sandbox.session_id
    _say(db, sid, *[f"第 {i} 句" for i in range(12)])
    _measure(db, sid, int(LIMIT * COMPACT_AT) - 1)

    memory = build_memory(
        db, ContextScope.SESSION, sid, client=FakeClient(), limit=LIMIT
    )
    assert memory.meter.over_line is False
    assert memory.new_compact_id is None
    assert db.scalars(select(ContextCompact)).all() == []


def test_it_compacts_when_the_measured_usage_crosses_the_line(sandbox) -> None:
    """★ 中心用例：超线就压，并把这次压缩记下来。"""
    db, sid = sandbox.db, sandbox.session_id
    ids = _say(db, sid, *[_long(f"第 {i} 句话") for i in range(1, 15)])
    _measure(db, sid, int(LIMIT * COMPACT_AT) + 1)

    memory = build_memory(
        db, ContextScope.SESSION, sid, client=FakeClient("早先聊了 1 到 14 句"),
        limit=LIMIT,
    )
    assert memory.new_compact_id is not None
    row = db.get(ContextCompact, memory.new_compact_id)
    assert row is not None
    assert row.scope is ContextScope.SESSION
    assert row.scope_id == sid
    # 记的是**实测值**，不是估算
    assert row.prompt_tokens_before == int(LIMIT * COMPACT_AT) + 1
    assert row.limit_at_time == LIMIT
    assert row.summary == "早先聊了 1 到 14 句"
    # 真的压掉了一些原文，但绝没有全压光
    assert row.covers_msg_ids, "压了但一条都没吃掉"
    assert len(row.covers_msg_ids) < len(ids) + 1
    assert memory.meter.compacted_messages == len(row.covers_msg_ids)


def test_compaction_deletes_nothing(sandbox) -> None:
    """★ 被压掉的原话**一行都没删**。

    这是整个机制敢做有损压缩的前提：「他当时到底说了什么」永远还能查。
    删除是静默失忆，而摘要是**有记录**的省略 —— 两者不是一回事。
    """
    db, sid = sandbox.db, sandbox.session_id
    ids = _say(db, sid, *[f"第 {i} 句" for i in range(1, 15)])
    _measure(db, sid, int(LIMIT * COMPACT_AT) + 1)
    before = {row.msg_id for row in reads.list_chat_messages(db, sid)}

    memory = build_memory(
        db, ContextScope.SESSION, sid, client=FakeClient("压了"), limit=LIMIT
    )
    covered = db.get(ContextCompact, memory.new_compact_id).covers_msg_ids
    assert set(covered) <= set(ids) | set(before), "压缩记录指向了不存在的消息"

    after = {row.msg_id for row in reads.list_chat_messages(db, sid)}
    assert after == before, "有消息被删掉了 —— 压缩绝不允许删原文"
    # 而且被压掉的正文仍然读得出来
    for msg_id in covered:
        assert db.get(ChatMessageRow, msg_id).content


def test_the_summary_is_marked_as_derived(sandbox) -> None:
    """★ 摘要是**派生文本**，上下文里必须明确标出来。

    不标的话模型会引用摘要里的措辞，而用户会以为那话是自己说的。
    这一条是"压缩"与"编造用户说过的话"之间的分界。
    """
    db, sid = sandbox.db, sandbox.session_id
    _say(db, sid, *[_long(f"第 {i} 句话") for i in range(1, 15)])
    _measure(db, sid, int(LIMIT * COMPACT_AT) + 1)

    memory = build_memory(
        db, ContextScope.SESSION, sid, client=FakeClient("他说想先背单词"), limit=LIMIT
    )
    summary_msgs = [m for m in memory.history if SUMMARY_HEADER in m.content]
    assert len(summary_msgs) == 1
    assert summary_msgs[0].role == "system", "摘要与用户原话必须在消息角色上就分开"
    assert "他说想先背单词" in summary_msgs[0].content


def test_compacted_messages_stop_appearing_as_verbatim(sandbox) -> None:
    """★ 被压掉的原话不许**同时**以摘要和原文两种形式出现。

    两者都在时模型会看到两遍同样的话，而它会以为用户强调过两次。
    """
    db, sid = sandbox.db, sandbox.session_id
    _say(db, sid, *[_long(f"第 {i} 句独一无二") for i in range(1, 15)])
    _measure(db, sid, int(LIMIT * COMPACT_AT) + 1)

    memory = build_memory(
        db, ContextScope.SESSION, sid, client=FakeClient("压了"), limit=LIMIT
    )
    covered = set(db.get(ContextCompact, memory.new_compact_id).covers_msg_ids)
    verbatim = "\n".join(m.content for m in memory.history)
    for msg_id in covered:
        text = db.get(ChatMessageRow, msg_id).content
        assert text not in verbatim, f"{msg_id} 既在摘要里又当原文发了一遍"


def test_at_least_a_few_verbatim_messages_always_survive(sandbox) -> None:
    """★ 无论怎么压，最近几条原话必须留着。

    这是"压缩"与"失忆"的分界。压光了之后，模型连用户上一句说的是什么
    都不知道 —— 而它仍然会回答，且看起来很正常。
    """
    db, sid = sandbox.db, sandbox.session_id
    _say(db, sid, *[_long(f"第 {i} 句") for i in range(1, 40)])
    _measure(db, sid, LIMIT * 2)  # 远远超线

    memory = build_memory(
        db, ContextScope.SESSION, sid, client=FakeClient("压了"), limit=LIMIT
    )
    assert memory.meter.live_messages >= MIN_LIVE_MESSAGES
    assert memory.meter.live_messages == len(memory.history) - 1  # 减去摘要那一条


# --------------------------------------------------------------------------- #
# 摘要层的自我折叠
# --------------------------------------------------------------------------- #


def test_old_summaries_get_folded_so_the_mechanism_never_jams(sandbox) -> None:
    """★ 摘要自己也会累积，所以摘要层必须能被再压一次。

    没有这一层，窗口小的时候会卡死在"原文没得压了、而摘要自己就超线" ——
    机制再也降不下来，只能等用户自己去调 `num_ctx`。
    """
    db, sid = sandbox.db, sandbox.session_id
    _measure(db, sid, LIMIT * 2)

    # 反复触发压缩，逼出折叠。每一轮都灌进**够长**的新内容，
    # 否则历史太短会被 `MIN_LIVE_MESSAGES` 挡住（那也是一个正确结论，
    # 但测不到折叠）。
    for i in range(8):
        _say(db, sid, *[_long(f"第 {i} 轮第 {j} 句") for j in range(6)])
        _measure(db, sid, LIMIT * 2)
        build_memory(
            db, ContextScope.SESSION, sid, client=FakeClient(f"摘要 {i}"), limit=LIMIT
        )

    rows = reads.list_compacts(db, ContextScope.SESSION, sid)
    folded = {cid for row in rows for cid in row.folds_compact_ids}
    assert folded, "压了这么多次都没有发生折叠 —— 摘要层会一直长下去"

    live = reads.live_compacts(db, ContextScope.SESSION, sid)
    assert len(live) <= 4 or len(live) < len(rows), "折叠没有让有效摘要变少"
    # 被折叠的摘要**没有**从库里消失（只追加）
    for cid in folded:
        assert db.get(ContextCompact, cid) is not None, "折叠把旧记录删了"

    # ★ 而被折叠的摘要当年吃掉的原话，仍然算被吃掉
    folded_covers = set()
    for row in rows:
        if row.compact_id in folded:
            folded_covers |= set(row.covers_msg_ids)
    assert folded_covers <= reads.compacted_message_ids(db, ContextScope.SESSION, sid)


# --------------------------------------------------------------------------- #
# 压缩失败：不阻断，但必须可见
# --------------------------------------------------------------------------- #


def test_a_failed_compaction_does_not_break_the_discussion(sandbox) -> None:
    """★ 压缩失败**不阻断讨论** —— 用户想聊的是他的事。

    但它必须可见：失败的压缩会让上下文继续膨胀，
    而下一次就撞上真正的装不下。把它藏起来等于让用户自己发现。
    """
    db, sid = sandbox.db, sandbox.session_id
    _say(db, sid, *[_long(f"第 {i} 句话") for i in range(1, 15)])
    _measure(db, sid, int(LIMIT * COMPACT_AT) + 1)

    memory = build_memory(
        db, ContextScope.SESSION, sid, client=BoomClient(), limit=LIMIT
    )
    assert memory.new_compact_id is None
    assert memory.compact_error is not None
    assert "LLMError" in memory.compact_error
    # 上下文照常给出去（没压，但也没坏）
    assert memory.history
    assert db.scalars(select(ContextCompact)).all() == []


# --------------------------------------------------------------------------- #
# 写入守卫：三条拒绝，各配一个反例
# --------------------------------------------------------------------------- #


def test_add_compact_refuses_empty_and_blank(sandbox) -> None:
    """三条拒绝都要**同时**验证"合法输入能过"，否则守卫可能拦错了原因。"""
    db, sid = sandbox.db, sandbox.session_id
    ok = dict(prompt_tokens_before=9000, limit_at_time=LIMIT)

    with pytest.raises(InvalidField, match="假记录"):
        writes.add_compact(
            db, ContextScope.SESSION, sid, covers_msg_ids=[], summary="有内容", **ok
        )
    with pytest.raises(MissingPrerequisite, match="失忆"):
        writes.add_compact(
            db, ContextScope.SESSION, sid, covers_msg_ids=["CM-1"], summary="   ", **ok
        )
    with pytest.raises(InvalidField, match="实测"):
        writes.add_compact(
            db,
            ContextScope.SESSION,
            sid,
            covers_msg_ids=["CM-1"],
            summary="有内容",
            prompt_tokens_before=0,
            limit_at_time=LIMIT,
        )
    with pytest.raises(InvalidField, match="窗口上限"):
        writes.add_compact(
            db,
            ContextScope.SESSION,
            sid,
            covers_msg_ids=["CM-1"],
            summary="有内容",
            prompt_tokens_before=9000,
            limit_at_time=0,
        )

    # ★ 反例：合法输入必须过 —— 没有这一条，上面四条可能在
    # 因为别的原因被拒（比如参数名写错），而测试照样绿。
    row = writes.add_compact(
        db, ContextScope.SESSION, sid, covers_msg_ids=["CM-1"], summary="有内容", **ok
    )
    assert row.compact_id.startswith("CX-")
    # 折叠旧摘要也是一条合法的压缩（此时 covers 可以为空）
    folded = writes.add_compact(
        db,
        ContextScope.SESSION,
        sid,
        covers_msg_ids=[],
        folds_compact_ids=[row.compact_id],
        summary="折叠后的内容",
        **ok,
    )
    assert folded.folds_compact_ids == [row.compact_id]


# --------------------------------------------------------------------------- #
# 用量回填
# --------------------------------------------------------------------------- #


def test_usage_is_recorded_from_the_models_own_numbers(sandbox) -> None:
    db, sid = sandbox.db, sandbox.session_id
    _say(db, sid, "问题")
    writes.add_chat_message(db, sid, content="回答", role="assistant")

    record_usage(
        db,
        ContextScope.SESSION,
        sid,
        meta={"prompt_eval_count": 4321, "eval_count": 87},
    )
    row = reads.list_chat_messages(db, sid)[-1]
    assert row.prompt_tokens == 4321
    assert row.completion_tokens == 87
    assert build_meter(db, ContextScope.SESSION, sid, limit=LIMIT).used == 4321


def test_a_missing_measurement_stays_none_instead_of_becoming_zero(sandbox) -> None:
    """★ 「没测到」与「用量是 0」是相反的两件事。

    写 0 会让下一个判断认为"上下文几乎是空的"，于是永远不压缩 ——
    直到某一次装不下。保持 `None` 才会走到"没有读数就不压"那条路上。
    """
    db, sid = sandbox.db, sandbox.session_id
    writes.add_chat_message(db, sid, content="回答", role="assistant")
    record_usage(db, ContextScope.SESSION, sid, meta={})
    assert reads.list_chat_messages(db, sid)[-1].prompt_tokens is None
    assert build_meter(db, ContextScope.SESSION, sid, limit=LIMIT).used == 0
    assert build_meter(db, ContextScope.SESSION, sid, limit=LIMIT).measured is False


# --------------------------------------------------------------------------- #
# ★ 摘要不能成为全局写入的来源
# --------------------------------------------------------------------------- #


def test_a_summary_cannot_be_quoted_back_to_a_message(sandbox) -> None:
    """★ 结构断言：摘要拿不出 `chat_message.msg_id`。

    任何「用户说过……」的全局写入都必须能指回原话，而摘要**不是原话**。
    这条测试守的是那个不可能性：摘要住在 `context_compact` 表，
    它没有、也不该有指向消息的"我就是那一条"的能力 ——
    `covers_msg_ids` 说的是"我概括了哪几条"，不是"我就是哪一条"。

    如果哪天有人让摘要冒充消息（比如把摘要写回 `chat_message`），
    这条会失败。
    """
    db, sid = sandbox.db, sandbox.session_id
    _say(db, sid, *[_long(f"第 {i} 句话") for i in range(1, 15)])
    _measure(db, sid, int(LIMIT * COMPACT_AT) + 1)
    memory = build_memory(
        db, ContextScope.SESSION, sid, client=FakeClient("他说想先背单词"), limit=LIMIT
    )

    compact = db.get(ContextCompact, memory.new_compact_id)
    msg_ids = {row.msg_id for row in reads.list_chat_messages(db, sid)}
    # 摘要指的永远是**别的**消息，它自己没有一条消息的身份
    assert compact.compact_id not in msg_ids
    assert compact.covers_msg_ids
    assert set(compact.covers_msg_ids) <= msg_ids
    # 而摘要的正文不在任何一条 `chat_message` 里 —— 它没有冒充过用户原话
    contents = [row.content for row in reads.list_chat_messages(db, sid)]
    assert compact.summary not in contents


# --------------------------------------------------------------------------- #
# 两个 scope 共用一套
# --------------------------------------------------------------------------- #


def test_the_sidebar_and_a_session_use_the_same_meter(sandbox) -> None:
    """★ 侧边栏与 session 共用同一套机制 —— 拆成两套会让两个界面
    显示的用量算法不一样，而那种不一致没人能解释。

    存储是分开的（归属约束不同，见 `SidebarMessage`），
    但**计量与压缩是同一份代码**，所以两个 scope 的读数可以直接比。
    """
    db = sandbox.db
    writes.add_sidebar_message(db, content="侧边栏第一句")
    writes.add_sidebar_message(db, content="侧边栏第二句")
    _say(db, sandbox.session_id, "会话第一句")

    sidebar = build_meter(db, ContextScope.SIDEBAR, SIDEBAR_SCOPE_ID, limit=LIMIT)
    session = build_meter(db, ContextScope.SESSION, sandbox.session_id, limit=LIMIT)
    assert sidebar.live_messages == 2
    assert session.live_messages == 1
    # 两个 scope 的压缩互不影响
    assert sidebar.compacted_messages == 0 and session.compacted_messages == 0
    # 侧边栏的消息**没有**跑进会话（反之亦然）—— 分表的意义就在这里
    assert [m.content for m in reads.list_messages(db, ContextScope.SESSION, sandbox.session_id)] == [
        "会话第一句"
    ]


def test_sidebar_messages_are_not_gated_by_any_state_machine(sandbox) -> None:
    """★ 侧边栏**没有终态**，所以它不该被会话的状态机拦住。

    会话归档之后，**内容写入**仍然被拒；而侧边栏是常驻的 ——
    一个"侧边栏写不进去"的报错只能是 bug，不可能是用户的处境。

    ★ 政策变更：这条测试原来拿 `add_chat_message` 当"会话被拦住"的样本
    （"会话写完总结就不能再聊"）。现在说话全状态可写（append-only、
    不涨版本、不参与任何判定），所以样本换成真正仍然关着的那个：
    往已归档的会话里加一条 stage —— 那是在改写归档记录的内容。
    """
    from app.session_domain.enums import StageItemType

    db, sid = sandbox.db, sandbox.session_id
    # `active → completed` 现在是**一条直边**（政策变更），不再需要先绕 review。
    row = reads.get_session(db, sid)
    writes.transition(
        db, sid, target="completed", expected_version=row.version, reason="确认完成"
    )

    # 会话这边被拦住了（这是对的）：归档之后不再新增内容
    with pytest.raises(StateNotWritable):
        writes.add_stage_item(
            db,
            sid,
            item_type=StageItemType.IDEA,
            content="还想试试别的",
            expected_version=reads.get_session(db, sid).version,
        )

    # 侧边栏照常能写
    row = writes.add_sidebar_message(db, content="侧边栏不关心会话状态")
    assert row.msg_id.startswith("SM-")
    # 而且它自己也不允许用户消息带上模型才有的东西
    with pytest.raises(InvalidField, match="thinking"):
        writes.add_sidebar_message(db, content="假冒的", thinking="我偷偷想了")


# --------------------------------------------------------------------------- #
# ★ 超线但压不动：窗口被别的东西占满了
# --------------------------------------------------------------------------- #


def test_it_says_so_when_the_window_is_full_but_history_is_small(sandbox) -> None:
    """★ 这是被一次真实的测试数据逼出来的状态。

    `used` 远远超线，但对话只有十几句短话 —— 那 10001 个 token 里
    几乎没有历史的位置，大头是 system 提示与上下文包。
    把它们压掉是不可能的：历史本来就没占多少。

    此时**必须说"压不动"**，而不是显示"正在压缩"。
    没有这个信号的话，界面会一直显示在压，而其实什么也没压 ——
    用户会以为卡住了，而真相是"该调大 num_ctx"。
    """
    db, sid = sandbox.db, sandbox.session_id
    _say(db, sid, "一句话", "两句话", "三句话")
    _measure(db, sid, LIMIT * 2)

    memory = build_memory(db, ContextScope.SESSION, sid, client=FakeClient(), limit=LIMIT)
    assert memory.meter.over_line is True, "这么高的用量必须算超线"
    assert memory.meter.compactable is False, "十几句短话不该被认为压得动"
    assert memory.new_compact_id is None
    # 没有压缩，所以也没有失败 —— 「压不动」不是错误
    assert memory.compact_error is None
    # 而它带着完整的事实回给界面：超线、但压不动
    assert memory.meter.as_dict()["pressure"] == "over"
    assert memory.meter.as_dict()["compactable"] is False
