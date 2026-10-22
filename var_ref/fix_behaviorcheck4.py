"""状态机那几条 409 是**清单自己的记账错误**，不是产品坏了。

证据（`var_ref/probe_folder_500.py` 的最后一段）：每一步都现读版本号再转移，
draft → active → review → completed → active 全部 200。而清单里我是在内存里
自己累加版本号 —— 中间任何一次写（聊天、输入区、计划）都会让会话版本前进，
我却没跟上，于是拿着旧版本去换，拿到 409。

界面本来就是这么干的（点状态胶囊前先 `GET /state` 现读版本）。所以清单也该这么干，
否则它测的是"我的记账准不准"，而不是"产品对不对"。
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


swap(
    """def _transition(ctx: dict, target: str) -> Reply:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/transitions",
        {"target": target, "expected_version": ctx["session_version"], "reason": "行为清单推着走"},
    )""",
    """def _transition(ctx: dict, target: str) -> Reply:
    #: 现读版本号再转移 —— 界面的状态胶囊就是这么做的。
    #: 不这么做，中间任何一次写（聊天、输入区、计划）都会让版本前进，
    #: 于是清单拿着旧版本去换，收到 409，看起来像"状态机坏了"。
    state = ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/state")
    fresh = int((((state.data or {}).get("session") or {}).get("version")) or 0)
    if fresh:
        ctx["session_version"] = fresh
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/transitions",
        {"target": target, "expected_version": ctx["session_version"], "reason": "行为清单推着走"},
    )""",
    "_transition 现读版本",
)

swap(
    """        {"expected_session_version": ctx["version"], "steps": [{"title": "归档后偷偷改计划"}], "reason": "不该生效"},""",
    """        {
            "expected_version": ctx.get("plan_version", 0),
            "change": "revise",
            "reason": "不该生效",
            "steps": [{"action": "归档后偷偷改计划"}],
        },""",
    "终态改计划：用 PUT 的真字段",
)

# 收尾那两条也把"第一步的状态码"打出来，否则 500 出现在哪一步都看不出来
swap(
    """    folder = ctx_ids(ctx, "tmp_folder_id")
    if folder:
        ctx["client"].call("DELETE", f"/api/folders/{folder}")
    reply = ctx["client"].call("PATCH", f"/api/folders/{folder}", {"name": "删完还想改"}) if folder else Reply(404, None)
    return reply.status == 404, f"删后再改 {reply.status}", \"\"""",
    """    folder = ctx_ids(ctx, "tmp_folder_id")
    if not folder:
        return False, "这一轮没建领域，没得删", ""
    deleted = ctx["client"].call("DELETE", f"/api/folders/{folder}")
    reply = ctx["client"].call("PATCH", f"/api/folders/{folder}", {"name": "删完还想改"})
    return (
        deleted.status in (200, 204) and reply.status == 404,
        f"删 {deleted.status}，删后再改 {reply.status} {reply.detail[:80]}",
        \"\",
    )""",
    "收尾：把删除与再改的状态码都打出来",
)

PATH.write_text(text, encoding="utf-8")
compile(PATH.read_text(encoding="utf-8"), str(PATH), "exec")
print("\n✅ 语法 OK")
