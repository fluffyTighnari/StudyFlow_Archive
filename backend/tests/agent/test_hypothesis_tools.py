"""假说的**工具层**行为（P7）：能翻到、能写、且猜的不许写。

## 三件事

1. **能翻到**：`retrieve_cognitive(scope="hypothesis")` 返回它，
   并带上 `verified=False` —— 用户那句「我想试试之前的猜想」靠这个满足。
2. **能写**：模型说出用户的原话（`basis_quote`）时，假说直接生效。
3. **猜的不许写**：没有原话时必须**拒绝**，而不是像偏好那样降级。

第 3 条是这一轮唯一改了 `handoff.py` 行为的地方，理由见那条测试的文档。
"""

from __future__ import annotations

from app.agent.dispatch import Dispatcher
from app.agent.tools import ToolStatus
from app.global_domain import writes
from app.router.capability import issue_token


def _call(name: str, **arguments):
    from app.agent.llm import ToolCall

    return ToolCall(call_id="call_1", name=name, arguments=arguments)


HYPOTHESIS_INTENT = {
    "action": "add_preference",
    "target": "C.hypothesis",
    "basis": "stated",
    "reasoning": "用户说想试试",
    "confidence": "high",
    "value": {
        "statement": "背单词想试试 Anki",
        "condition": "背单词时",
        "effect": "用 Anki 的间隔重复",
    },
}


def _add_hypothesis(db) -> None:
    writes.insert_hypothesis(
        issue_token(),
        db,
        statement="背单词想试试 Anki",
        condition="背单词时",
        effect="用 Anki 的间隔重复",
    )
    db.flush()


# --------------------------------------------------------------------------- #
# 读：翻得到
# --------------------------------------------------------------------------- #


def test_the_model_can_look_up_what_he_wanted_to_try(sandbox) -> None:
    """★ 「我想试试之前的猜想」就是这一条。

    如果假说只躺在 `/api/hypotheses` 里，模型永远想不起它 —— 那句话就没法被满足。
    """
    _add_hypothesis(sandbox.db)

    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        _call("retrieve_cognitive", scope="hypothesis")
    )
    assert result.status is ToolStatus.SUCCESS
    rows = result.data["items"]
    assert len(rows) == 1
    assert rows[0]["path"].startswith("C.hypothesis.")
    assert rows[0]["detail"]["verified"] is False
    # 给一句能直接读的解释，而不是让模型自己从 basis 推
    assert "未验证" in rows[0]["detail"]["verified_note"]


def test_scope_all_includes_hypotheses(sandbox) -> None:
    """`all` 也包含它 —— 但要注意 `max_items` 的默认值只有 8。

    11 个 M1 变量就把默认上限吃满了，所以这里显式放宽：
    这不是产品缺陷（`all` 本来就该被裁剪），而是测试必须知道
    "默认情况下第一条假说排在很后面"。
    """
    _add_hypothesis(sandbox.db)
    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        _call("retrieve_cognitive", scope="all", max_items=30)
    )
    paths = [row["path"] for row in result.data["items"]]
    assert any(path.startswith("C.hypothesis.") for path in paths)


def test_evidence_rows_carry_basis_and_verified_true(sandbox) -> None:
    """证据那一边也标注了 `verified=True` —— 两侧对称，模型不用猜哪边是哪边。"""
    from app.router import Router, SubmitRequest

    from tests.router.helpers import evidence_intent

    Router(sandbox.db).submit(
        SubmitRequest(
            trigger_type="R4c",
            intents=[evidence_intent(basis="experienced")],
        )
    )
    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        _call("retrieve_cognitive", scope="c_evidence")
    )
    assert result.status is ToolStatus.SUCCESS
    detail = result.data["items"][0]["detail"]
    assert detail["verified"] is True
    assert detail["basis"] == "experienced"


# --------------------------------------------------------------------------- #
# 写：说得出原话就能写
# --------------------------------------------------------------------------- #


def test_a_quoted_hypothesis_is_applied(sandbox) -> None:
    """模型带上用户的原话 → 直接生效（R3 + stated 是 auto_apply）。"""
    from app.session_domain import writes as session_writes

    session_writes.add_chat_message(
        sandbox.db,
        sandbox.session_id,
        content="我想试试用 Anki 背单词，间隔重复那种",
    )
    sandbox.db.flush()

    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        _call(
            "submit_global_update",
            trigger_type="R3",
            intents=[{**HYPOTHESIS_INTENT, "basis_quote": "我想试试用 Anki 背单词"}],
        )
    )
    assert result.status is ToolStatus.SUCCESS
    assert result.data["results"][0]["status"] == "applied"


# --------------------------------------------------------------------------- #
# 写：猜的不许写 —— 拒绝而不是降级
# --------------------------------------------------------------------------- #


def test_a_guessed_hypothesis_is_rejected_not_downgraded(sandbox) -> None:
    """★ 这一轮唯一改了 `handoff.py` 行为的地方。

    ## 为什么偏好能降级、假说不能

    降级的用途是"把一条可能对的观察从『已是事实』挪到『等人确认』"。
    对偏好，`stated → experienced` 说得通：模型可能真的观察到他有这个倾向。

    对假说，`C.hypothesis + experienced` 是判定表**专门拒绝**的一格
    （试过了就不再是假说）。于是"降级"会产出一条注定被拒的 intent，
    模型收到的错误信息会变成「假说不接受 experienced」——
    而真正的问题是「你没有他的原话」。

    **让错误信息指向真正的原因**，比让它指向机制细节有用得多。
    所以这里直接拒绝，并告诉他该怎么做：先问一句。
    """
    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        _call(
            "submit_global_update",
            trigger_type="R3",
            intents=[HYPOTHESIS_INTENT],  # 没有 basis_quote
        )
    )
    assert result.status is ToolStatus.REJECTED
    # 错误信息必须指出真正的解决办法（去问），而不是只说不允许
    assert "问" in result.message
    assert "原话" in result.message
    # 并且**没有**被降级成 evidentece 相关的错误
    assert "experienced" not in result.message
    # 什么都没写进去
    assert sandbox.db.execute(
        __import__("sqlalchemy").text("SELECT COUNT(*) FROM hypothesis")
    ).scalar_one() == 0


def test_a_guessed_preference_is_still_downgraded_not_rejected(sandbox) -> None:
    """对照组：偏好那一格的行为**没有**变（降级 + 一句说明）。

    这一条存在的意义是证明上一条是**针对假说的取舍**，
    而不是"顺手把整个降级机制关掉了"。
    """
    result = Dispatcher(sandbox.db, sandbox.session_id).execute(
        _call(
            "submit_global_update",
            trigger_type="R3",
            intents=[
                {
                    "action": "add_preference",
                    "target": "M2.preferences",
                    "basis": "stated",
                    "reasoning": "他好像喜欢安静",
                    "confidence": "medium",
                    "value": {"statement": "他喜欢安静的环境"},
                }
            ],
        )
    )
    # 降级成功 → 变成 experienced + 需确认 → 落成一条待确认提案
    assert result.status is ToolStatus.SUCCESS
    assert "降级" in result.message or "待确认" in str(result.data)
