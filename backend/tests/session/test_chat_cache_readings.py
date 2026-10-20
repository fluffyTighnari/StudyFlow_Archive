"""提示缓存的读数：**给得出就说，给不出就空着**。

## 为什么这件事值得一组测试

用量面板上那行「缓存命中 98%」是一个会被当成事实读的数字。
它有两种坏法：一是把没测到的当 0%（"一次都没命中"），二是把命中数
当成 `prompt − miss` 猜出来。两种都看不出来 —— 界面上都是个百分数。

所以这里钉四件事：

1. 两个字段**可空**，且不带它们写进去就是 `None`；
2. 带进去就原样落库（不做 `max(0, …)` 之类的"修正"）；
3. 用户消息带这两个字段是**拒绝**（用户没有模型用量）；
4. 读端点把两个字段都交出去（前端据此决定显示数值还是「没测到」）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

import pytest

from app.core.errors import InvalidField
from app.session_domain import reads, writes
from app.session_domain.enums import ChatRole
from app.session_domain.schemas import ChatMessageRead

from tests.session.test_lifecycle import _live_session


def _live(session: Session, *, goal: str = "背完考研核心 1500 词") -> str:
    """建一个已激活的会话，返回它的 id（`_live_session` 给的是整行，这里只要 id）。"""
    _folder, row = _live_session(session, goal=goal)
    return row.session_id


def test_a_message_without_cache_readings_keeps_them_empty(session: Session) -> None:
    """没给 → 两个都是 `None`，而不是 0。

    0 是一个断言（"测过了，一点都没命中"），而这里什么都没测。
    """
    session_id = _live(session)
    row = writes.add_chat_message(
        session,
        session_id,
        content="没测到缓存读数的一条回复",
        role=ChatRole.ASSISTANT,
        prompt_tokens=1200,
        completion_tokens=180,
    )
    assert row.cache_hit_tokens is None
    assert row.cache_miss_tokens is None


def test_the_readings_are_stored_exactly_as_reported(session: Session) -> None:
    """原样落库。

    这条路刻意不做"校验"（比如命中不能大于 prompt）：那会把一个
    提供方的 bug 变成一个我们悄悄改过的数字，而错的是它的报告，
    不是我们要修的东西。数字对不上时，看数据的人应该看得见对不上。
    """
    session_id = _live(session)
    row = writes.add_chat_message(
        session,
        session_id,
        content="DeepSeek 报了缓存读数的一条回复",
        role=ChatRole.ASSISTANT,
        prompt_tokens=10_000,
        completion_tokens=250,
        cache_hit_tokens=9_500,
        cache_miss_tokens=500,
    )
    assert (row.cache_hit_tokens, row.cache_miss_tokens) == (9_500, 500)

    # 读回来也一样（落库时别被某个默认值改掉）。
    listed = reads.list_chat_messages(session, session_id)
    assert any(
        item.cache_hit_tokens == 9_500 and item.cache_miss_tokens == 500 for item in listed
    )


def test_a_user_message_cannot_carry_cache_readings(session: Session) -> None:
    """用户消息带用量 = 拒绝，理由与"用户消息不能有 tool_trace"同一条。"""
    session_id = _live(session)
    with pytest.raises(InvalidField):
        writes.add_chat_message(
            session,
            session_id,
            content="我说的话",
            role=ChatRole.USER,
            cache_hit_tokens=100,
        )


def test_the_read_model_exposes_both_readings(session: Session) -> None:
    """读模型必须**两个字段都在**：只给命中的话，界面算不出未命中。

    `ChatMessageRead` 由 Pydantic 从 ORM 行验证而来，所以这里直接验证
    一个"带读数"的行 —— 字段缺了会抛验证错误，而不是悄悄变成 `None`。
    """
    session_id = _live(session)
    row = writes.add_chat_message(
        session,
        session_id,
        content="一条带读数的回复",
        role=ChatRole.ASSISTANT,
        prompt_tokens=800,
        completion_tokens=90,
        cache_hit_tokens=640,
        cache_miss_tokens=160,
    )
    payload = ChatMessageRead.model_validate(row)
    assert payload.cache_hit_tokens == 640
    assert payload.cache_miss_tokens == 160
    assert payload.prompt_tokens == 800
