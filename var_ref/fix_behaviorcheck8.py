"""最后一轮收尾：连接池 + 剩下的预期对齐。

一、连接池（真问题）
    现场：刚 `POST /api/sessions` 建出来的会话，紧接着 `PATCH /api/sessions/{id}`
    报「会话不存在」，而 `GET /state` 又读得到；过一会儿再试又全好。
    同一时刻的列表也少这一条。这是**旧连接看到旧快照**的典型样子：
    WAL 下读事务一旦开着，就一直看到它开始那一刻的库。连接池把这种连接留着复用，
    于是"偶尔读不到刚写的东西"。
    单用户本地应用不需要连接池 —— 每个请求一条新连接，问题从根上消失。

二、预期对齐（我的清单写错了）
    - 动机强度是 `high|medium|low`，不是 1–5
    - 沉淀只接受 R4a/R4b/R4c 触发
    - PATCH 步骤返回的是**那一步**，不是整个计划
    - 反思要求先有总结（`missing: session_summary`）
    - 补检查点时 PUT 的 steps 只能用 `action/output/method_ref/step_id`
"""

from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent

# ── 一、连接池 ────────────────────────────────────────────────────────
DB = REPO / "backend" / "app" / "db.py"
db_text = DB.read_text(encoding="utf-8")

old_engine = """    engine = create_engine(url, **kwargs)"""
new_engine = """    if url.startswith("sqlite") and ":memory:" not in url:
        # ★ 不用连接池：每个请求一条新连接。
        #
        # 现场：刚建出来的会话，紧接着的 PATCH 报「会话不存在」，而 GET /state 又读得到；
        # 同一时刻的列表也少这一条，过一会儿再试全好。这是**旧连接看旧快照**：
        # WAL 下读事务一旦开着就一直看那一刻的库，连接池却把这种连接留着复用。
        # 单用户本地应用没有并发收益可言，省掉连接池就把这类"偶尔读不到刚写的东西"消掉。
        kwargs["poolclass"] = NullPool
        kwargs["pool_pre_ping"] = True
    engine = create_engine(url, **kwargs)"""
assert db_text.count(old_engine) == 1
db_text = db_text.replace(old_engine, new_engine, 1)
db_text = db_text.replace(
    "from sqlalchemy.pool import StaticPool",
    "from sqlalchemy.pool import NullPool, StaticPool",
    1,
)
DB.write_text(db_text, encoding="utf-8")
compile(db_text, str(DB), "exec")
print("✅ db.py：SQLite 改用 NullPool（每个请求一条新连接）")

# ── 二、清单里的预期 ──────────────────────────────────────────────────
PATH = REPO / "backend" / "tools" / "behaviorcheck.py"
text = PATH.read_text(encoding="utf-8")


def swap(old: str, new: str, label: str) -> None:
    global text
    assert text.count(old) == 1, f"{label}：锚点出现 {text.count(old)} 次"
    text = text.replace(old, new, 1)
    print(f"✅ {label}")


swap(
    '''            "type": "intrinsic",
            "strength": 4,''',
    '''            "type": "intrinsic",
            "strength": "high",''',
    "动机强度用枚举",
)
swap(
    '''        {"trigger": "R1", "dry_run": True},''',
    '''        {"trigger": "R4a", "dry_run": True},''',
    "沉淀用 R4a",
)
swap(
    '''    data = reply.data or {}
    if reply.status == 200:
        ctx["plan_version"] = int(data.get("version") or ctx["plan_version"])
    found = next(
        (step for step in (data.get("steps") or []) if step.get("step_id") == ctx.get("step_id")),
        {},
    )
    return (
        reply.status == 200 and found.get("status") == "done",
        f"{reply.status} version={data.get('version')} status={found.get('status')}",
        "",
    )''',
    '''    data = reply.data or {}
    #: 这个端点返回的是**那一步**（StepRead），不是整个计划。
    return (
        reply.status == 200 and data.get("status") == "done",
        f"{reply.status} step={data.get('step_id')} status={data.get('status')}",
        "",
    )''',
    "标步骤完成：返回的是单步",
)
swap(
    '''                "expected_version": int(plan.get("version") or ctx["plan_version"]),
                "change": "add_checkpoint",
                "steps": plan.get("steps") or [],''',
    '''                "expected_version": int(plan.get("version") or ctx["plan_version"]),
                "change": "add_checkpoint",
                #: PUT 只收 `action/output/method_ref/step_id`；读模型多出来的
                #: `seq/status/...` 会被 `extra_forbidden` 挡下。
                "steps": [
                    {
                        "action": step.get("action") or "",
                        "output": step.get("output"),
                        "step_id": step.get("step_id"),
                    }
                    for step in (plan.get("steps") or [])
                ],''',
    "补检查点：steps 只送允许的字段",
)

# 反思：先要有总结
swap(
    '''@case("书写类", "写一条反思", "POST /reflections", "201，返回 reflection_id", needs="review")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(''',
    '''@case("书写类", "写一条反思", "POST /reflections", "201，返回 reflection_id", needs="review")
def _(ctx: dict) -> tuple[bool, str, str]:
    #: 反思要求先有总结（否则后端回 `missing: session_summary`）。
    #: 这不是刁难：没有总结，"这一轮学到了什么"没有可核对的对象。
    first = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/summary",
        {"content": "行为清单：先总结，再反思"},
    )
    if first.status not in (200, 201):
        return False, f"写总结这一步就没成：{first.status} {first.detail[:80]}", ""
    reply = ctx["client"].call(''',
    "反思：先写总结",
)

# 标注：引文取助手原话里真实存在的一小段
swap(
    '''    quote = "十分钟"
    for message in messages:
        if message.get("role") == "assistant":
            msg_id = message.get("msg_id") or message.get("id") or ""
            text = str(message.get("content") or "")
            if len(text) >= 6:
                quote = text[2:8]
            break''',
    '''    quote = "十分钟"
    for message in messages:
        if message.get("role") == "assistant":
            msg_id = message.get("msg_id") or message.get("id") or ""
            words = str(message.get("content") or "").strip()
            #: 引文必须是那句里**真实存在**的一小段（后端会去比对）。
            if len(words) >= 6:
                quote = words[:6]
            break''',
    "标注：引文取自原文开头",
)
swap(
    '''                quote = text[2:8] if len(text) >= 8 else (out["reply"][:6] or "十分钟")''',
    '''                words = str(message.get("content") or "").strip()
                quote = words[:6] if len(words) >= 6 else (out["reply"][:6] or "十分钟")''',
    "标注（现场补一轮的情况）：同一规则",
)

PATH.write_text(text, encoding="utf-8")
compile(PATH.read_text(encoding="utf-8"), str(PATH), "exec")
print("\n✅ 语法 OK")
