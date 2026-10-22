"""第一轮跑出来的 35/70，绝大多数是**我写错了请求体**，不是产品坏了。

这一轮把清单里的请求体换成真字段名（从 OpenAPI 里读出来的，不是猜的）：

- `/chat` 只要 `content`（`role` 是后端自己决定的，前端不该送）
- 计划的步骤叫 `action`（不是 `title`）、修订要带 `change`
- 输入区的字段叫 `goal`（不是 `input_goal`）
- 阶段项的类型是 `temp_pref|idea|plan_override`，结算是 `active|deposited|discarded|archived`
- 而且**会话版本号**与**计划版本号**是两套：状态转移与输入区用前者，
  改步骤/修订计划用后者 —— 混用就会拿到 409，看起来像"状态机坏了"

另外补两件事：
1. 替身模型下"两轮的话不一样"这一条必然不成立（同一句是剧本，不是缺陷），
   所以给它一个"跳过"结果，而不是假装通过或假装失败。
2. `Row` 增加跳过态，报告里用 ⏭ 表示，不计入通过/偏离。
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "backend" / "tools" / "behaviorcheck.py"
text = PATH.read_text(encoding="utf-8")


def swap(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    assert count == 1, f"{label}：锚点出现 {count} 次"
    text = text.replace(old, new, 1)
    print(f"✅ {label}")


# ── 1. Row 支持"跳过" ────────────────────────────────────────────────
swap(
    """@dataclass
class Row:
    group: str
    title: str
    request: str
    expected: str
    observed: str = ""
    ok: bool = False
    evidence: str = ""
    fatal: bool = False""",
    """@dataclass
class Row:
    group: str
    title: str
    request: str
    expected: str
    observed: str = ""
    #: True = 符合预期；False = 偏离；None = **跳过**（这条在当下的配置里没有意义，
    #: 例如用替身模型时"两轮的话不一样"必然不成立 —— 那是剧本，不是缺陷）。
    ok: bool | None = False
    evidence: str = ""
    fatal: bool = False""",
    "Row.ok 支持 None（跳过）",
)

swap(
    """        def runner(ctx: dict) -> Row:
            try:
                ok, observed, evidence = fn(ctx)
            except Exception as exc:  # 用例自己炸了也要留痕，不能把整轮带走
                ok, observed, evidence = False, f"用例自身异常：{type(exc).__name__}: {exc}", ""
            return Row(group, title, request, expected, observed, ok, evidence)""",
    """        def runner(ctx: dict) -> Row:
            ctx["_skip_reason"] = ""
            try:
                ok, observed, evidence = fn(ctx)
            except Exception as exc:  # 用例自己炸了也要留痕，不能把整轮带走
                ok, observed, evidence = False, f"用例自身异常：{type(exc).__name__}: {exc}", ""
            if ctx.get("_skip_reason"):
                ok = None
            return Row(group, title, request, expected, observed, ok, evidence)""",
    "runner 支持跳过",
)

# ── 2. 请求体：真字段名 ──────────────────────────────────────────────
swap(
    '        {"role": "user", "content": "第一天的具体任务是读懂 props 和 state 的区别"},',
    '        {"content": "第一天的具体任务是读懂 props 和 state 的区别"},',
    "/chat 只要 content",
)
swap(
    '        {"role": "user", "content": "归档之后又问了一句"},',
    '        {"content": "归档之后又问了一句"},',
    "完成后说话：content",
)
swap(
    'chat", {"role": "user", "content": ""}',
    'chat", {"content": ""}',
    "空内容消息",
)

swap(
    """        {
            "expected_session_version": ctx["version"],
            "steps": [
                {"title": "读懂 props 的传递", "detail": "看文档 + 写三个例子"},
                {"title": "读懂 state 与重渲染", "detail": "改一个 state 看界面变化"},
            ],
            "reason": "用户说先啃这两个概念",
        },""",
    """        {
            "expected_session_version": ctx["session_version"],
            "steps": [
                {"action": "读懂 props 的传递", "output": "看文档 + 写三个例子"},
                {"action": "读懂 state 与重渲染", "output": "改一个 state 看界面变化"},
            ],
            "reason": "用户说先啃这两个概念",
        },""",
    "建计划：steps 用 action",
)
swap(
    """    if ok:
        ctx["version"] = int(plan.get("version") or 1)
        ctx["step_id"] = steps[0]["step_id"]""",
    """    if ok:
        #: 计划自己的版本号 —— 与 `session_version` 是两套，别混。
        ctx["plan_version"] = int(plan.get("version") or 1)
        ctx["step_id"] = steps[0]["step_id"]""",
    "记下 plan_version",
)

swap(
    """        {"expected_session_version": ctx["version"], "steps": [{"title": "多余的一份"}]},""",
    """        {"expected_session_version": ctx["session_version"], "steps": [{"action": "多余的一份"}]},""",
    "重复建计划",
)

swap(
    """        {"status": "done", "expected_version": ctx["version"]},
    )
    data = reply.data or {}
    if reply.status == 200:
        ctx["version"] = int(data.get("version") or ctx["version"])""",
    """        {"status": "done", "expected_version": ctx["plan_version"]},
    )
    data = reply.data or {}
    if reply.status == 200:
        ctx["plan_version"] = int(data.get("version") or ctx["plan_version"])""",
    "标步骤完成用计划版本",
)

swap(
    """        {"status": "bogus", "expected_version": ctx["version"]},""",
    """        {"status": "bogus", "expected_version": ctx["plan_version"]},""",
    "非法状态用计划版本",
)

swap(
    """        {"status": "doing", "expected_version": max(0, ctx["version"] - 1)},""",
    """        {"status": "doing", "expected_version": max(0, ctx["plan_version"] - 1)},""",
    "旧版本号用计划版本",
)

swap(
    """    before = ctx["version"]
    reply = ctx["client"].call(
        "PUT",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan",
        {
            "expected_session_version": before,
            "steps": [
                {"title": "读懂 props 的传递", "detail": "看文档 + 写三个例子"},
                {"title": "读懂 state 与重渲染", "detail": "改一个 state 看界面变化"},
                {"title": "写一个最小的受控组件", "detail": "把两者合起来"},
            ],
            "reason": "用户说第三天加一个综合练习",
        },
    )
    data = reply.data or {}
    ok = reply.status in (200, 201) and int(data.get("version") or 0) > 0
    if ok:
        ctx["version"] = int(data["version"])""",
    """    before = ctx["plan_version"]
    reply = ctx["client"].call(
        "PUT",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan",
        {
            "expected_version": before,
            "change": "add_step",
            "steps": [
                {"action": "读懂 props 的传递", "output": "看文档 + 写三个例子"},
                {"action": "读懂 state 与重渲染", "output": "改一个 state 看界面变化"},
                {"action": "写一个最小的受控组件", "output": "把两者合起来"},
            ],
            "reason": "用户说第三天加一个综合练习",
        },
    )
    data = reply.data or {}
    ok = reply.status in (200, 201) and int(data.get("version") or 0) > 0
    if ok:
        ctx["plan_version"] = int(data["version"])""",
    "修订计划：expected_version + change",
)

swap(
    """        {"input_goal": "两周内读懂 React 的 props 与 state", "expected_version": ctx["version"]},
    )
    data = reply.data or {}
    if reply.status == 200:
        ctx["version"] = int(data.get("version") or ctx["version"])""",
    """        {"goal": "两周内读懂 React 的 props 与 state", "expected_version": ctx["session_version"]},
    )
    data = reply.data or {}
    if reply.status == 200:
        ctx["session_version"] = int(data.get("version") or ctx["session_version"])""",
    "输入区字段叫 goal",
)
swap(
    """        {"input_goal": "归档后偷偷改目标", "expected_version": ctx["version"]},""",
    """        {"goal": "归档后偷偷改目标", "expected_version": ctx["session_version"]},""",
    "终态改定义用会话版本",
)

swap(
    """        {"expected_version": 0, "energy": 3, "stress": 2, "note": "行为清单顺手记一条"},""",
    """        {"expected_version": ctx["session_version"], "energy": 3, "stress": 2, "note": "行为清单顺手记一条"},""",
    "快照用会话版本",
)

swap(
    """        {"expected_version": 0, "type": "observation", "content": "他把 props 说成「传参数」"},""",
    """        {"expected_version": ctx["session_version"], "type": "temp_pref", "content": "他把 props 说成「传参数」"},""",
    "阶段项类型用枚举内的值",
)
swap(
    """        {"expected_version": ctx.get("stage_version", 1), "status": "accepted"},""",
    """        {"expected_version": ctx.get("stage_version", 1), "status": "deposited"},""",
    "阶段项结算状态",
)

swap(
    '"expected_version": ctx["version"], "result": "pass", "result_note": "讲清楚了"',
    '"expected_version": ctx["plan_version"], "result": "pass", "result_note": "讲清楚了"',
    "检查点用计划版本",
)
swap(
    """                "expected_session_version": ctx["version"],
                "steps": plan.get("steps") or [],
                "checkpoints": [""",
    """                "expected_version": int(plan.get("version") or ctx["plan_version"]),
                "change": "add_checkpoint",
                "steps": plan.get("steps") or [],
                "checkpoints": [""",
    "补检查点时用 PUT 的真字段",
)

# ── 3. 状态机：全部换成会话版本 ──────────────────────────────────────
swap(
    """def _transition(ctx: dict, target: str) -> Reply:
    return ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/transitions",
        {"target": target, "expected_version": ctx["version"], "reason": "行为清单推着走"},
    )""",
    """def _transition(ctx: dict, target: str) -> Reply:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/transitions",
        {"target": target, "expected_version": ctx["session_version"], "reason": "行为清单推着走"},
    )
    if reply.status == 200 and isinstance(reply.data, dict):
        ctx["session_version"] = int(reply.data.get("version") or ctx["session_version"])
    return reply""",
    "_transition 用会话版本并跟着前进",
)

swap(
    """    _transition(ctx, "active")
    state = ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/state").data or {}
    ctx["version"] = int(state["session"]["version"])
    reply = _transition(ctx, "review")
    if reply.status == 200:
        ctx["version"] = int((reply.data or {}).get("version") or ctx["version"])""",
    """    _transition(ctx, "active")
    reply = _transition(ctx, "review")""",
    "执行中 → 复盘",
)
swap(
    """    reply = _transition(ctx, "completed")
    if reply.status == 200:
        ctx["version"] = int((reply.data or {}).get("version") or ctx["version"])""",
    """    reply = _transition(ctx, "completed")""",
    "复盘 → 完成",
)
swap(
    """    reply = _transition(ctx, "active")
    if reply.status == 200:
        ctx["version"] = int((reply.data or {}).get("version") or ctx["version"])
    return (
        reply.status == 200 and (reply.data or {}).get("state") == "active",""",
    """    reply = _transition(ctx, "active")
    return (
        reply.status == 200 and (reply.data or {}).get("state") == "active",""",
    "完成 → 重新打开",
)

# ── 4. `读全部状态` 同时记下两套版本 ────────────────────────────────
swap(
    """    if ok:
        ctx["version"] = int(data["session"]["version"])""",
    """    if ok:
        ctx["session_version"] = int(data["session"]["version"])
        ctx["plan_version"] = 0""",
    "读状态：两套版本分开记",
)

# ── 5. 替身模型下"两轮"这条跳过 ────────────────────────────────────
swap(
    """def _(ctx: dict) -> tuple[bool, str, str]:
    first = ctx.get("last_reply") or ""
    out = stream_discuss(ctx["client"], ctx_ids(ctx, "session_id"), "反过来问：如果只有三天，砍掉哪部分？")
    second = out["reply"]
    ok = bool(first) and bool(second) and first.strip() != second.strip()
    return ok, f"第一轮 {len(first)} 字 / 第二轮 {len(second)} 字，相同={first.strip() == second.strip()}", second[:70]""",
    """def _(ctx: dict) -> tuple[bool, str, str]:
    first = ctx.get("last_reply") or ""
    out = stream_discuss(ctx["client"], ctx_ids(ctx, "session_id"), "反过来问：如果只有三天，砍掉哪部分？")
    second = out["reply"]
    same = first.strip() == second.strip()
    if ctx.get("fake_llm") and same:
        #: 替身模型按剧本回同一句 —— 那是它的定义，不是缺陷。
        #: 这一条只在真模型那一轮有意义，所以标记为跳过，而不是通过/失败。
        ctx["_skip_reason"] = "替身模型按剧本回同一句"
        return True, f"第一轮 {len(first)} 字 / 第二轮 {len(second)} 字（替身模型，两句相同属预期）", second[:60]
    ok = bool(first) and bool(second) and not same
    return ok, f"第一轮 {len(first)} 字 / 第二轮 {len(second)} 字，相同={same}", second[:70]""",
    "两轮比对新旧话：替身下跳过",
)

# ── 6. CLI：标注这一轮用的是替身还是真模型 ──────────────────────────
swap(
    """    parser.add_argument("--json", default="")""",
    """    parser.add_argument("--json", default="")
    parser.add_argument(
        "--llm",
        default="fake",
        choices=("fake", "real"),
        help="这一轮服务用的是替身模型还是真模型（影响个别判据是否适用）",
    )""",
    "CLI 增加 --llm",
)
swap(
    """def run_all(base: str) -> tuple[list[Row], RunContext, float]:
    client = Client(base)
    ctx = RunContext(client)""",
    """def run_all(base: str, llm_mode: str = "fake") -> tuple[list[Row], RunContext, float]:
    client = Client(base)
    ctx = RunContext(client)
    ctx["fake_llm"] = llm_mode == "fake" """.rstrip(),
    "run_all 记住模型模式",
)
swap(
    """    rows, ctx, elapsed = run_all(args.base)""",
    """    rows, ctx, elapsed = run_all(args.base, args.llm)""",
    "把模式传下去",
)

# ── 7. 通过/跳过/偏离的计数 ─────────────────────────────────────────
#: 这一行在「渲染报告」和「控制台汇总」各出现一次，两处都要换成三态计数。
count_old = "    passed = sum(1 for row in rows if row.ok)"
count_new = (
    "    passed = sum(1 for row in rows if row.ok is True)\n"
    "    skipped = sum(1 for row in rows if row.ok is None)\n"
    "    failed = len(rows) - passed - skipped"
)
assert text.count(count_old) == 2, text.count(count_old)
text = text.replace(count_old, count_new)
print("✅ 计数：通过/跳过/偏离（两处）")
swap(
    """        f"- 结果：**{passed} 通过 / {len(rows) - passed} 偏离**",""",
    """        f"- 结果：**{passed} 通过 / {failed} 偏离 / {skipped} 跳过**",""",
    "报告头部的结果行也要三态",
)
swap(
    """    for row in rows:
        mark = "✅" if row.ok else "❌\"""",
    """    for row in rows:
        mark = "✅" if row.ok is True else ("⏭" if row.ok is None else "❌")""",
    "控制台三态",
)
swap(
    """    print(f"\\n{passed}/{len(rows)} 通过，{elapsed:.1f} s（{ctx.client.calls} 次请求）")""",
    """    print(
        f"\\n{passed} 通过 / {failed} 偏离 / {skipped} 跳过（共 {len(rows)} 条），"
        f"{elapsed:.1f} s（{ctx.client.calls} 次请求）"
    )""",
    "控制台汇总三态",
)
swap(
    """    return 0 if passed == len(rows) else 1""",
    """    return 0 if failed == 0 else 1""",
    "退出码只看偏离",
)

PATH.write_text(text, encoding="utf-8")
compile(PATH.read_text(encoding="utf-8"), str(PATH), "exec")
print("\n✅ 语法 OK")
