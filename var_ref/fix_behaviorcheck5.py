"""让清单自己说话：每条失败都附上**最后那一步的后端原话**。

第一版失败只打状态码（"409"），于是我得逐个去猜原因 —— 而其中一半是我的
请求体写错了，一半是版本号记账错了。让 `Client` 记住调用历史，失败时把最后一步
的 `detail` 贴出来，一轮就能看清全部原因。

另外：会话版本号是"每次写就前进"的东西。凡是要 `expected_version` 的用例，
一律现读（跟界面一样），不要靠内存里累加。
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


# 1) Client 记住历史
swap(
    """class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.calls = 0""",
    """class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.calls = 0
        #: 最近一次请求的（方法、路径、状态码、后端原话）。
        #: 失败时把它贴进"实测"，就不用再逐个去猜"409 是哪一步的 409"。
        self.last: tuple[str, str, int, str] = ("", "", 0, "")""",
    "Client 记住最后一次请求",
)

swap(
    """        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", "replace")
                return Reply(response.status, _maybe_json(text))
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            return Reply(exc.code, _maybe_json(text), text[:400])
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return Reply(0, None, f"{type(exc).__name__}: {exc}")""",
    """        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", "replace")
                reply = Reply(response.status, _maybe_json(text))
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            reply = Reply(exc.code, _maybe_json(text), text[:400])
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reply = Reply(0, None, f"{type(exc).__name__}: {exc}")
        self.last = (method, path, reply.status, reply.detail[:200])
        return reply""",
    "每次请求都记下结果",
)

# 2) runner 在失败时把最后一步贴出来
swap(
    """            if ctx.get("_skip_reason"):
                ok = None
            return Row(group, title, request, expected, observed, ok, evidence)""",
    """            if ctx.get("_skip_reason"):
                ok = None
            if ok is False:
                method, path, status, detail = ctx["client"].last
                observed = f"{observed} ← 最后一步 {method} {path} → {status} {detail}".strip()
            return Row(group, title, request, expected, observed, ok, evidence)""",
    "失败时附上最后一步",
)

# 3) 需要版本号的用例：现读
swap(
    '''def ctx_ids(ctx: dict, key: str) -> str:
    return str(ctx.get(key) or "")''',
    '''def ctx_ids(ctx: dict, key: str) -> str:
    return str(ctx.get(key) or "")


def fresh_session_version(ctx: dict) -> int:
    """现读会话版本号。**每次写都会让它前进**，所以在内存里累加迟早会错。"""
    state = ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/state")
    version = int((((state.data or {}).get("session") or {}).get("version")) or 0)
    if version:
        ctx["session_version"] = version
    return ctx["session_version"]


def fresh_plan_version(ctx: dict) -> int:
    plan = ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan")
    version = int((((plan.data or {}).get("version")) or 0))
    if version:
        ctx["plan_version"] = version
    return ctx["plan_version"]''',
    "加上现读版本的两个助手",
)

# 4) 各用例改用现读版本
swap(
    '''        {"status": "done", "expected_version": ctx["plan_version"]},''',
    '''        {"status": "done", "expected_version": fresh_plan_version(ctx)},''',
    "标步骤完成：现读计划版本",
)
swap(
    '''        {"status": "bogus", "expected_version": ctx["plan_version"]},''',
    '''        {"status": "bogus", "expected_version": fresh_plan_version(ctx)},''',
    "非法状态：现读计划版本",
)
swap(
    '''        {"status": "doing", "expected_version": max(0, ctx["plan_version"] - 1)},''',
    '''        {"status": "doing", "expected_version": max(1, fresh_plan_version(ctx) - 1)},''',
    "旧版本号：现读计划版本 - 1",
)
swap(
    '''        {"goal": "两周内读懂 React 的 props 与 state", "expected_version": ctx["session_version"]},''',
    '''        {"goal": "两周内读懂 React 的 props 与 state", "expected_version": fresh_session_version(ctx)},''',
    "输入区：现读会话版本",
)
swap(
    '''        {"expected_version": ctx["session_version"], "energy": 3, "stress": 2, "note": "行为清单顺手记一条"},''',
    '''        {
            "expected_version": fresh_session_version(ctx),
            "energy": "medium",
            "stress": "low",
            "note": "行为清单顺手记一条",
        },''',
    "快照：现读版本 + 枚举值",
)
swap(
    '''        {"expected_version": ctx["session_version"], "type": "temp_pref", "content": "他把 props 说成「传参数」"},''',
    '''        {
            "expected_version": fresh_session_version(ctx),
            "type": "temp_pref",
            "content": "他把 props 说成「传参数」",
        },''',
    "阶段项：现读版本",
)
swap(
    '''        {"trigger": "manual", "dry_run": True},''',
    '''        {"trigger": "R1", "dry_run": True},''',
    "沉淀：trigger 用枚举",
)

PATH.write_text(text, encoding="utf-8")
compile(PATH.read_text(encoding="utf-8"), str(PATH), "exec")
print("\n✅ 语法 OK")
