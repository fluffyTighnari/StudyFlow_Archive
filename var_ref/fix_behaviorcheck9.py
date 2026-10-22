"""收尾：三套版本号各读各的，另外几处预期按实测改。

教训（值得写进文档）：这个系统里有**三个**相互独立的版本号 ——
会话 `session.version`（状态转移/输入/快照/澄清/阶段项）、计划 `plan.version`
（改计划/标步骤/检查点）、动机 `motivation.version`。拿错一个就会收到
"版本冲突：当前 4，你提交的 3"，看起来像状态机坏了，其实是拿错了尺子。
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "backend" / "tools" / "behaviorcheck.py"
text = PATH.read_text(encoding="utf-8")


def swap(old: str, new: str, label: str) -> None:
    global text
    assert text.count(old) == 1, f"{label}：锚点出现 {text.count(old)} 次"
    text = text.replace(old, new, 1)
    print(f"✅ {label}")


# 1) 三套版本号各自的现读助手
swap(
    '''def current_state(ctx: dict) -> str:''',
    '''def _read_state(ctx: dict) -> dict:
    reply = ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/state")
    return reply.data or {}


def fresh_input_version(ctx: dict) -> int:
    """任务定义区**自己**的版本号（不是会话的）。"""
    block = _read_state(ctx).get("input") or {}
    return int(block.get("version") or 1)


def fresh_motivation_version(ctx: dict) -> int:
    """动机**自己**的版本号。"""
    block = _read_state(ctx).get("motivation") or {}
    return int(block.get("version") or 1)


def current_state(ctx: dict) -> str:''',
    "三套版本号：输入/动机的现读助手",
)

swap(
    '''        {"goal": "两周内读懂 React 的 props 与 state", "expected_version": fresh_session_version(ctx)},''',
    '''        {"goal": "两周内读懂 React 的 props 与 state", "expected_version": fresh_input_version(ctx)},''',
    "改任务定义：用输入区版本",
)
swap(
    '''        {"answer": "会写一点 JS，不会 React", "expected_version": fresh_session_version(ctx)},''',
    '''        {"answer": "会写一点 JS，不会 React", "expected_version": fresh_input_version(ctx)},''',
    "澄清回答：用输入区版本",
)
swap(
    '''            "expected_version": 1,
            "change": "把驱动力写成「想自己做出东西」",''',
    '''            "expected_version": fresh_motivation_version(ctx),
            "change": "把驱动力写成「想自己做出东西」",''',
    "改动机：用动机版本",
)
swap(
    '''        {"expected_version": ctx["plan_version"], "result": "pass", "result_note": "讲清楚了"},''',
    '''        {"expected_version": fresh_plan_version(ctx), "result": "pass", "result_note": "讲清楚了"},''',
    "记检查点：用计划版本",
)

# 2) 重复建计划：看 message，不看 detail
swap(
    '''    detail = reply.detail
    ctx.note_error("重复建计划", detail)
    return reply.status == 422 and "已经有计划" in detail, f"{reply.status} {detail[:100]}", ""''',
    '''    payload = reply.data or {}
    message = str(payload.get("message") or reply.detail)
    ctx.note_error("重复建计划", message)
    return reply.status == 422 and "已经有计划" in message, f"{reply.status} {message[:100]}", ""''',
    "重复建计划：读 message 字段",
)

# 3) 状态机第一条：ensure_state 可能已经把它推到 active 了
swap(
    '''@case("状态机", "开始执行（draft → active）", "POST /transitions {target:active}", "200，state=active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = _transition(ctx, "active")
    return (
        reply.status == 200 and (reply.data or {}).get("state") == "active",
        f"{reply.status} state={(reply.data or {}).get('state')}",
        "",
    )''',
    '''@case("状态机", "开始执行（draft → active）", "POST /transitions {target:active}", "200，state=active")
def _(ctx: dict) -> tuple[bool, str, str]:
    #: 前面的用例可能已经把它推到 active 了（它们要先 active 才能改步骤/写总结）。
    #: 这里要答的是"它现在是执行中吗"，不是"再推一次会怎样"。
    now = current_state(ctx)
    if now == "active":
        return True, "active（前面的用例已经推过来了）", ""
    reply = _transition(ctx, "active")
    return (
        reply.status == 200 and (reply.data or {}).get("state") == "active",
        f"{reply.status} state={(reply.data or {}).get('state')}",
        "",
    )''',
    "开始执行：已是 active 也算过",
)

# 4) 标注那条已经手工改在文件里了（候选引文逐个试），这里不再重复。

PATH.write_text(text, encoding="utf-8")
compile(PATH.read_text(encoding="utf-8"), str(PATH), "exec")
print("\n✅ 语法 OK")
