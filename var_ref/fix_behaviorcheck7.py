"""让清单按**真实生命周期**走：draft → active → review → completed。

第一轮跑出 21 条偏离，读下来几乎全是同一件事：我在 draft 里就去做只有
active/review 才允许的事（改步骤、记阶段项、写总结），于是拿到 409 ——
而那 409 本身是对的：`{'state': 'draft', 'operation': 'mark_step',
'allowed_states': ['active', 'review']}` 说得比任何注释都清楚。

所以给用例加一个 `needs=` 声明：跑之前先把会话推到那个状态（走合法边），
而不是在用例里手写版本号。这样清单既不用记状态机的账，也不会把"守卫按规矩
拒绝"误报成"产品坏了"。

顺带把四条实测结论写进预期：
- 重复建计划是 **422 + 「已经有计划了，要改就调用修订」**（不是 409）
- 澄清只能问**还空着的槽位**，问已经填好的会被拒并说明原因
- 标注只能标**助手说的话**
- 附件要带 `?filename=` 查询参数
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


# ── 1. 用例可以声明"我要在哪个状态跑" ────────────────────────────────
swap(
    """def case(group: str, title: str, request: str, expected: str):
    \"\"\"登记一条用例。函数收到 `ctx`（跨用例共享的 id），返回 (ok, observed, evidence)。\"\"\"

    def wrapper(fn: Callable[[dict], tuple[bool, str, str]]):
        def runner(ctx: dict) -> Row:
            ctx["_skip_reason"] = ""
            try:""",
    """def case(group: str, title: str, request: str, expected: str, needs: str = ""):
    \"\"\"登记一条用例。函数收到 `ctx`（跨用例共享的 id），返回 (ok, observed, evidence)。

    `needs` = 这条用例要求在哪个会话状态下跑。守卫按规矩拒绝（例如 draft 里不许改步骤）
    是**正确行为**，不是缺陷；所以清单先把状态推过去，再测那件事本身。
    \"\"\"

    def wrapper(fn: Callable[[dict], tuple[bool, str, str]]):
        def runner(ctx: dict) -> Row:
            ctx["_skip_reason"] = ""
            if needs:
                ensure_state(ctx, needs)
            try:""",
    "用例支持 needs=",
)

# ── 2. ensure_state：走合法边把会话推到目标状态 ──────────────────────
swap(
    '''def fresh_plan_version(ctx: dict) -> int:''',
    '''#: 前进路线。`completed` 之后要回头，走 completed → active（这条边 P16 才加上）。
_UP = {"draft": "active", "active": "review", "review": "completed"}


def current_state(ctx: dict) -> str:
    state = ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/state")
    return str(((state.data or {}).get("session") or {}).get("state") or "")


def ensure_state(ctx: dict, target: str) -> str:
    """把当前会话推到 `target`，每一步都现读版本号。返回最终状态。"""
    for _ in range(6):
        now = current_state(ctx)
        if not now or now == target:
            return now
        if now == "completed" and target != "completed":
            step = "active"
        elif now in _UP:
            step = _UP[now]
        else:  # abandoned 之类，回不去
            return now
        if step == target and now == target:
            return now
        _transition(ctx, step)
    return current_state(ctx)


def fresh_plan_version(ctx: dict) -> int:''',
    "ensure_state 助手",
)

# ── 3. 那些必须在 active 里做的事 ────────────────────────────────────
for title, needs in [
    ("标记步骤完成", "active"),
    ("非法步骤状态", "active"),
    ("用旧版本号改步骤", "active"),
    ("修订计划", "active"),
    ("改任务定义", "active"),
    ("输入版本历史", "active"),
    ("改动机", "active"),
    ("记一次状态快照", "active"),
    ("记一条阶段项", "active"),
    ("结算阶段项", "active"),
    ("加一个里程碑", "active"),
    ("完成里程碑", "active"),
    ("记一次检查点", "active"),
    ("写一份总结", "active"),
    ("沉淀（dry_run）", "active"),
    ("写一条反思", "review"),
]:
    marker = f'"200，'
    # 找到这条用例的装饰器行，在 expected 之后补 needs
    index = text.index(f'@case(')
    found = False
    cursor = 0
    while True:
        cursor = text.find("@case(", cursor)
        if cursor == -1:
            break
        line_end = text.index("\n", cursor)
        header = text[cursor:line_end]
        if f'"{title}"' in header:
            assert header.rstrip().endswith(")"), header
            new_header = header.rstrip()[:-1] + f', needs="{needs}")'
            text = text[:cursor] + new_header + text[line_end:]
            print(f"✅ {title} → needs={needs}")
            found = True
            break
        cursor = line_end
    assert found, f"没找到用例 {title}"

# ── 4. 状态机那一组：按合法边推进，再验非法边 ────────────────────────
swap(
    '''@case("状态机", "执行中 → 复盘", "POST /transitions {target:review}", "200，state=review")
def _(ctx: dict) -> tuple[bool, str, str]:
    _transition(ctx, "active")
    reply = _transition(ctx, "review")
    return (
        reply.status == 200 and (reply.data or {}).get("state") == "review",
        f"{reply.status} state={(reply.data or {}).get('state')}",
        "",
    )''',
    '''@case("状态机", "开始执行（draft → active）", "POST /transitions {target:active}", "200，state=active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = _transition(ctx, "active")
    return (
        reply.status == 200 and (reply.data or {}).get("state") == "active",
        f"{reply.status} state={(reply.data or {}).get('state')}",
        "",
    )


@case("状态机", "执行中 → 复盘", "POST /transitions {target:review}", "200，state=review", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = _transition(ctx, "review")
    return (
        reply.status == 200 and (reply.data or {}).get("state") == "review",
        f"{reply.status} state={(reply.data or {}).get('state')}",
        "",
    )''',
    "状态机：补上 draft → active 那条",
)
swap(
    '''@case("状态机", "看不懂的转移", "POST /transitions {target:draft}", "409，列出允许的目标")''',
    '''@case("状态机", "看不懂的转移", "POST /transitions {target:draft}", "409，列出允许的目标", needs="completed")''',
    "非法转移在 completed 里试",
)
swap(
    '''@case("状态机", "复盘 → 完成（归档）", "POST /transitions {target:completed}", "200，state=completed")''',
    '''@case("状态机", "复盘 → 完成（归档）", "POST /transitions {target:completed}", "200，state=completed", needs="review")''',
    "复盘 → 完成 needs=review",
)
swap(
    '''@case("状态机", "完成后还能说话", "POST /chat（completed）", "201，归档不等于冻结对话")''',
    '''@case("状态机", "完成后还能说话", "POST /chat（completed）", "201，归档不等于冻结对话", needs="completed")''',
    "完成后说话 needs=completed",
)
swap(
    '''@case("状态机", "完成后不能改定义", "PATCH /input（completed）", "409，allowed_states 写明可改的状态")''',
    '''@case("状态机", "完成后不能改定义", "PATCH /input（completed）", "409，allowed_states 写明可改的状态", needs="completed")''',
    "终态改定义 needs=completed",
)
swap(
    '''@case("状态机", "完成后不能改计划", "PUT /plan（completed）", "409，计划冻结")''',
    '''@case("状态机", "完成后不能改计划", "PUT /plan（completed）", "409，计划冻结", needs="completed")''',
    "终态改计划 needs=completed",
)
swap(
    '''@case("状态机", "完成 → 重新打开", "POST /transitions {target:active}", "200，state=active")''',
    '''@case("状态机", "完成 → 重新打开", "POST /transitions {target:active}", "200，state=active", needs="completed")''',
    "重新打开 needs=completed",
)

# ── 5. 讨论：要有计划、要在 active ──────────────────────────────────
swap(
    '''@case("讨论", "有计划的讨论（流式）", "POST /discuss/stream", "200，有 token 帧、有 done，正文非空")''',
    '''@case("讨论", "有计划的讨论（流式）", "POST /discuss/stream", "200，有 token 帧、有 done，正文非空", needs="active")''',
    "讨论 needs=active",
)
swap(
    '''@case("讨论", "两轮的话不一样", "同一会话连发两句不同的话", "两轮正文不同（说明真的在听）")''',
    '''@case("讨论", "两轮的话不一样", "同一会话连发两句不同的话", "两轮正文不同（说明真的在听）", needs="active")''',
    "两轮 needs=active",
)

# ── 6. 四条实测结论，写进预期 ───────────────────────────────────────
swap(
    '''@case("计划", "重复建计划", "POST /plan 第二次", "409，说明已有计划")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan",
        {"expected_session_version": ctx["session_version"], "steps": [{"action": "多余的一份"}]},
    )
    ctx.note_error("409 已有计划", reply.detail)
    return reply.status == 409, f"{reply.status} {reply.detail[:90]}", ""''',
    '''@case("计划", "重复建计划", "POST /plan 第二次", "422，说明「已经有计划了，要改就调用修订」")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan",
        {"expected_session_version": fresh_session_version(ctx), "steps": [{"action": "多余的一份"}]},
    )
    detail = reply.detail
    ctx.note_error("重复建计划", detail)
    return reply.status == 422 and "已经有计划" in detail, f"{reply.status} {detail[:100]}", ""''',
    "重复建计划：422 + 原话",
)

swap(
    '''@case("澄清", "提一个澄清问题", "POST /clarifications", "201，status=open")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/clarifications",
        {"slot": "goal", "question": "两周后你希望自己能独立写出什么？"},
    )''',
    '''@case("澄清", "问一个已经填好的槽位", "POST /clarifications {slot:goal}", "422，并说明「问一个已经知道答案的问题」")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/clarifications",
        {"slot": "goal", "question": "你的目标是什么？"},
    )
    detail = reply.detail
    ctx.note_error("问已填好的槽位", detail)
    return reply.status == 422 and "已经有内容" in detail, f"{reply.status} {detail[:110]}", ""


@case("澄清", "提一个澄清问题", "POST /clarifications {slot:starting_point}", "201，status=open")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/clarifications",
        {"slot": "starting_point", "question": "关于这件事，你现在已经会哪些、卡在哪里？"},
    )''',
    "澄清：问空槽位才是合法动作",
)

swap(
    '''        {"answer": "能独立写出一个带表单的页面", "expected_version": ctx["version"]},''',
    '''        {"answer": "会写一点 JS，不会 React", "expected_version": fresh_session_version(ctx)},''',
    "澄清回答：现读版本",
)

swap(
    '''        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/attachments",
        raw=body,''',
    '''        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/attachments?filename=day1.md",
        raw=body,''',
    "附件：带 filename 查询参数",
)

# ── 7. 标注：只能标助手说的话 ───────────────────────────────────────
swap(
    '''@case("书写类", "标注一句原话", "POST /annotations", "201，需要 msg_id 与 quote")
def _(ctx: dict) -> tuple[bool, str, str]:
    state = ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/state").data or {}
    messages = state.get("messages") or state.get("chat") or []
    msg_id = ""
    for message in messages:
        if message.get("role") == "user":
            msg_id = message.get("msg_id") or message.get("id") or ""
            break
    if not msg_id:
        return False, "这个会话里读不到用户消息的 id，标注无从下手", ""''',
    '''@case("书写类", "标注自己说的话（应当被拒）", "POST /annotations {role:user}", "422，并说明只能标助手的话")
def _(ctx: dict) -> tuple[bool, str, str]:
    state = ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/state").data or {}
    messages = state.get("chat") or []
    msg_id = ""
    for message in messages:
        if message.get("role") == "user":
            msg_id = message.get("msg_id") or message.get("id") or ""
            break
    if not msg_id:
        return False, "这个会话里读不到用户消息的 id", ""
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/annotations",
        {"msg_id": msg_id, "quote": "props 和 state"},
    )
    detail = reply.detail
    ctx.note_error("标注用户自己的话", detail)
    return reply.status == 422, f"{reply.status} {detail[:110]}", ""


@case("书写类", "标注助手说的一句", "POST /annotations {role:assistant}", "201，需要 msg_id 与 quote", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    messages = (
        ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/state").data or {}
    ).get("chat") or []
    msg_id = ""
    quote = "十分钟"
    for message in messages:
        if message.get("role") == "assistant":
            msg_id = message.get("msg_id") or message.get("id") or ""
            text = str(message.get("content") or "")
            if len(text) >= 6:
                quote = text[2:8]
            break
    if not msg_id:
        out = stream_discuss(ctx["client"], ctx_ids(ctx, "session_id"), "用一句话说清 props 的方向")
        messages = (
            ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/state").data or {}
        ).get("chat") or []
        for message in messages:
            if message.get("role") == "assistant":
                msg_id = message.get("msg_id") or message.get("id") or ""
                text = str(message.get("content") or "")
                quote = text[2:8] if len(text) >= 8 else (out["reply"][:6] or "十分钟")
                break
    if not msg_id:
        return False, "这个会话里没有助手消息，标注无从下手", ""''',
    "标注：分成「自己的话被拒」与「助手的话可以标」两条",
)

PATH.write_text(text, encoding="utf-8")
compile(PATH.read_text(encoding="utf-8"), str(PATH), "exec")
print("\n✅ 语法 OK")
