"""「我说改了」判定的边界。

这个判定只做一件事：**区分「已经改了」与「等下会改」**。
它一旦误判，界面会在模型诚实提问时弹出一句「这句话说改动发生了」——
用户没有理由相信一个会冤枉模型的系统。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.loop import claims_unsupported_change


def _trace(tool: str, status: str = "success") -> SimpleNamespace:
    return SimpleNamespace(tool=tool, status=status)


def test_future_conditional_is_not_a_claim() -> None:
    """真 DeepSeek 2026-11-21 的原话：三个澄清问题之后的将来条件句。"""
    reply = (
        "你把这三个答案给我，我就一次性把步骤表和检查点改好；"
        "如果你想先看到草案再决定要不要写入，我就先出一份草案给你看。"
    )
    assert claims_unsupported_change(reply, [_trace("retrieve_session")]) is False


def test_completed_claim_without_write_is_flagged() -> None:
    """真实故障：说「照做了 —— 现在延到八天」，但这一轮 0 次写操作。"""
    reply = "照做了——现在延到八天。"
    assert claims_unsupported_change(reply, [_trace("retrieve_session")]) is True


def test_completed_claim_with_successful_write_is_not_flagged() -> None:
    reply = "已经改好了，计划现在是第 3 版。"
    traces = [_trace("update_task_plan", "success")]
    assert claims_unsupported_change(reply, traces) is False


def test_claim_is_judged_sentence_by_sentence() -> None:
    """后一句在征求同意，不影响前一句是完成体。"""
    reply = "我已经把计划改到版本 3。你要不要看看？"
    assert claims_unsupported_change(reply, []) is True


def test_common_nouns_do_not_hide_a_real_claim() -> None:
    """「会话」「功能」里的字不该被当成将来标记。"""
    reply = "这个会话的功能我已经改好了。"
    assert claims_unsupported_change(reply, []) is True
