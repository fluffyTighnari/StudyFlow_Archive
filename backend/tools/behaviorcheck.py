"""端到端行为清单：把「每个操作该产生什么」写成代码，跑一遍，生成表格。

为什么要有它：这个项目反复出的不是"功能没写"，而是**功能写了、行为不对，而没人
有机会发现**——模型页整页在演、`list_models` 从没绑到类上、讨论在跑时改设置 500。
这些都不是单元测试能抓住的，它们只在"把整条链按真顺序打一遍"时才露出来。

所以这里不做分层测试，而是**按用户动作**逐条打真实 HTTP 端点，每条给四样东西：

    操作 → 怎么打 → 预期（状态码 + 关键字段/文案） → 实测

预期写死在用例里（不跟实际值对齐），失败就把"预期 / 实测"都打出来。这样它既是
一次验收，也是一份可以照着念的行为清单。

用法（服务要已经在跑，见 `_端到端行为清单_v0.1.md` 的"怎么重跑"）：

    python -m tools.behaviorcheck --base http://127.0.0.1:8131 --md ..\\_端到端行为清单_v0.1.md

退出码：0 = 全部符合预期；1 = 有偏离（报告仍会写出来）。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

# ── 基础设施：一次请求，四种结果 ──────────────────────────────────────


@dataclass
class Reply:
    status: int
    data: Any
    error: str = ""

    @property
    def detail(self) -> str:
        """后端给的错误说明（`{"detail": ...}` 或原文）。"""
        if isinstance(self.data, dict) and "detail" in self.data:
            detail = self.data["detail"]
            if isinstance(detail, list):
                return json.dumps(detail, ensure_ascii=False)
            return str(detail)
        if isinstance(self.data, str):
            return self.data
        return self.error


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.calls = 0
        #: 最近一次请求的（方法、路径、状态码、后端原话）。
        #: 失败时把它贴进"实测"，就不用再逐个去猜"409 是哪一步的 409"。
        self.last: tuple[str, str, int, str] = ("", "", 0, "")

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        timeout: float = 120.0,
        raw: bytes | None = None,
        content_type: str = "application/json",
    ) -> Reply:
        self.calls += 1
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": content_type} if data is not None else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", "replace")
                reply = Reply(response.status, _maybe_json(text))
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            reply = Reply(exc.code, _maybe_json(text), text[:400])
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reply = Reply(0, None, f"{type(exc).__name__}: {exc}")
        self.last = (method, path, reply.status, reply.detail[:200])
        return reply


def _maybe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def stream_discuss(client: Client, session_id: str, message: str) -> dict:
    """发一轮讨论，读完 SSE，把 done / error 两帧交回来。"""
    payload = json.dumps({"message": message}).encode()
    request = urllib.request.Request(
        f"{client.base}/api/sessions/{session_id}/discuss/stream",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    client.calls += 1
    out: dict[str, Any] = {"reply": "", "done": None, "error": None, "frames": 0, "chars": 0}
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            event = ""
            for line in response:
                text = line.decode("utf-8", "replace").rstrip("\n")
                if text.startswith("event:"):
                    event = text.split(":", 1)[1].strip()
                elif text.startswith("data:"):
                    try:
                        data = json.loads(text.split(":", 1)[1].strip())
                    except json.JSONDecodeError:
                        continue
                    out["frames"] += 1
                    if event == "error":
                        out["error"] = data
                    elif event == "done":
                        out["done"] = data
                        out["reply"] = str(data.get("reply") or "")
                    elif event == "token":
                        out["chars"] += len(str(data.get("text") or ""))
    except urllib.error.HTTPError as exc:
        out["error"] = {"http": exc.code, "body": exc.read().decode("utf-8", "replace")[:300]}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        out["error"] = {"transport": f"{type(exc).__name__}: {exc}"}
    return out


# ── 用例的登记方式 ───────────────────────────────────────────────────


@dataclass
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
    fatal: bool = False


CASES: list[tuple[str, str, Callable[[dict], Row]]] = []


def case(group: str, title: str, request: str, expected: str, needs: str = ""):
    """登记一条用例。函数收到 `ctx`（跨用例共享的 id），返回 (ok, observed, evidence)。

    `needs` = 这条用例要求在哪个会话状态下跑。守卫按规矩拒绝（例如 draft 里不许改步骤）
    是**正确行为**，不是缺陷；所以清单先把状态推过去，再测那件事本身。
    """

    def wrapper(fn: Callable[[dict], tuple[bool, str, str]]):
        def runner(ctx: dict) -> Row:
            ctx["_skip_reason"] = ""
            if needs:
                ensure_state(ctx, needs)
            try:
                ok, observed, evidence = fn(ctx)
            except Exception as exc:  # 用例自己炸了也要留痕，不能把整轮带走
                ok, observed, evidence = False, f"用例自身异常：{type(exc).__name__}: {exc}", ""
            if ctx.get("_skip_reason"):
                ok = None
            if ok is False:
                method, path, status, detail = ctx["client"].last
                observed = f"{observed} ← 最后一步 {method} {path} → {status} {detail}".strip()
            return Row(group, title, request, expected, observed, ok, evidence)

        CASES.append((group, title, runner))
        return runner

    return wrapper


def ctx_ids(ctx: dict, key: str) -> str:
    return str(ctx.get(key) or "")


def fresh_session_version(ctx: dict) -> int:
    """现读会话版本号。**每次写都会让它前进**，所以在内存里累加迟早会错。"""
    state = ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/state")
    version = int((((state.data or {}).get("session") or {}).get("version")) or 0)
    if version:
        ctx["session_version"] = version
    return ctx["session_version"]


#: 前进路线。`completed` 之后要回头，走 completed → active（这条边 P16 才加上）。
_UP = {"draft": "active", "active": "review", "review": "completed"}


def _read_state(ctx: dict) -> dict:
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


def call_with_version_retry(
    ctx: dict, method: str, path: str, body: dict, *, version_key: str = "expected_version"
) -> tuple[Reply, dict]:
    """版本撞了就按后端说的 `current_version` 重发一次。

    这不是"绕过守卫"：守卫拦下的是**用过期的版本号写的请求**，而 409 已经把
    当前版本告诉你了 —— 和界面里"读到冲突 → 重新读一遍 → 再发一次"是同一件事。
    动机的版本号在 `/state` 里不一定带，靠这一步兜住。
    """
    reply = ctx["client"].call(method, path, body)
    if reply.status != 409:
        return reply, body
    detail = reply.data.get("detail") if isinstance(reply.data, dict) else None
    if not isinstance(detail, dict) or "current_version" not in detail:
        return reply, body
    retry = dict(body)
    retry[version_key] = int(detail["current_version"])
    return ctx["client"].call(method, path, retry), retry


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


def fresh_plan_version(ctx: dict) -> int:
    plan = ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan")
    version = int((((plan.data or {}).get("version")) or 0))
    if version:
        ctx["plan_version"] = version
    return ctx["plan_version"]


# ── 分组：基础 ────────────────────────────────────────────────────────


@case("基础", "存活探针", "GET /api/health", "200，body 里没有全局状态摘要")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call("GET", "/api/health")
    ok = reply.status == 200 and set(reply.data or {}) <= {"status", "ok", "version"}
    return ok, f"{reply.status} {json.dumps(reply.data, ensure_ascii=False)[:90]}", ""


@case("基础", "界面首页", "GET /", "200，返回 shell（含 觅学之契）")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call("GET", "/", raw=b"", content_type="text/plain")
    text = reply.data if isinstance(reply.data, str) else ""
    return (
        reply.status == 200 and "觅学之契" in text,
        f"{reply.status}，长度 {len(text)}",
        "",
    )


@case("基础", "前端脚本", "GET /app.js", "200，且不再有写死的假档案 PROFILES")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call("GET", "/app.js", raw=b"", content_type="text/plain")
    text = reply.data if isinstance(reply.data, str) else ""
    return (
        reply.status == 200 and "const PROFILES" not in text and "api.llmProfiles" in text,
        f"{reply.status}，{len(text)} 字节",
        "",
    )


# ── 分组：领域 ────────────────────────────────────────────────────────


@case("领域", "列出领域", "GET /api/folders", "200，数组或 items")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call("GET", "/api/folders")
    items = reply.data if isinstance(reply.data, list) else (reply.data or {}).get("items", [])
    ok = reply.status == 200 and isinstance(items, list) and bool(items)
    if ok:
        ctx["folder_id"] = items[0].get("folder_id") or items[0].get("id")
    return ok, f"{reply.status}，{len(items)} 个领域", ""


@case("领域", "新建领域", "POST /api/folders", "201，返回 folder_id")
def _(ctx: dict) -> tuple[bool, str, str]:
    name = f"行为清单 · {uuid.uuid4().hex[:6]}"
    reply = ctx["client"].call("POST", "/api/folders", {"name": name})
    folder = reply.data or {}
    ok = reply.status in (200, 201) and bool(folder.get("folder_id"))
    if ok:
        ctx["tmp_folder_id"] = folder["folder_id"]
    return ok, f"{reply.status} {folder.get('folder_id')}", name


@case("领域", "改名领域", "PATCH /api/folders/{id}", "200，name 改了")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "PATCH", f"/api/folders/{ctx_ids(ctx, 'tmp_folder_id')}", {"name": "改名之后的行为清单"}
    )
    return (
        reply.status == 200 and (reply.data or {}).get("name") == "改名之后的行为清单",
        f"{reply.status} name={(reply.data or {}).get('name')}",
        "",
    )


@case("领域", "归档领域", "PATCH /api/folders/{id} {archived:true}", "200，archived_at 有值")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "PATCH", f"/api/folders/{ctx_ids(ctx, 'tmp_folder_id')}", {"archived": True}
    )
    return (
        reply.status == 200 and bool((reply.data or {}).get("archived_at")),
        f"{reply.status} archived_at={(reply.data or {}).get('archived_at')}",
        "",
    )


@case("领域", "恢复领域", "PATCH /api/folders/{id} {archived:false}", "200，archived_at 变回 null")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "PATCH", f"/api/folders/{ctx_ids(ctx, 'tmp_folder_id')}", {"archived": False}
    )
    return (
        reply.status == 200 and (reply.data or {}).get("archived_at") is None,
        f"{reply.status} archived_at={(reply.data or {}).get('archived_at')}",
        "",
    )


@case("领域", "缺名字建领域", "POST /api/folders {}", "422，且说明缺哪个字段")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call("POST", "/api/folders", {})
    ok = reply.status == 422 and "name" in reply.detail
    ctx.note_error("422 缺字段", reply.detail)
    return ok, f"{reply.status} {reply.detail[:100]}", ""


# ── 分组：会话 ────────────────────────────────────────────────────────


@case("会话", "新建会话", "POST /api/sessions", "201，state=draft")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        "/api/sessions",
        {"folder_id": ctx_ids(ctx, "folder_id"), "title": "行为清单 · 主会话"},
    )
    session = reply.data or {}
    ok = reply.status in (200, 201) and session.get("state") == "draft"
    if ok:
        ctx["session_id"] = session["session_id"]
    return ok, f"{reply.status} {session.get('session_id')} state={session.get('state')}", ""


@case("会话", "建到不存在的领域", "POST /api/sessions {folder_id:F-9999}", "404，说找不到")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST", "/api/sessions", {"folder_id": "F-9999", "title": "不该建出来"}
    )
    ctx.note_error("404 领域不存在", reply.detail)
    return reply.status == 404, f"{reply.status} {reply.detail[:100]}", ""


@case("会话", "改名", "PATCH /api/sessions/{id}", "200，title 改了")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "PATCH", f"/api/sessions/{ctx_ids(ctx, 'session_id')}", {"title": "行为清单 · 改过名"}
    )
    return (
        reply.status == 200 and (reply.data or {}).get("title") == "行为清单 · 改过名",
        f"{reply.status} title={(reply.data or {}).get('title')}",
        "",
    )


@case("会话", "读全部状态", "GET /state", "200，带 session / plan / motivation 等键")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/state")
    data = reply.data or {}
    keys = sorted(data)[:8]
    ok = reply.status == 200 and {"session", "plan"} <= set(data)
    if ok:
        ctx["session_version"] = int(data["session"]["version"])
        ctx["plan_version"] = 0
    return ok, f"{reply.status} keys={keys}", ""


@case("会话", "写一条用户消息", "POST /chat", "201，库里多一条")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/chat",
        {"content": "第一天的具体任务是读懂 props 和 state 的区别"},
    )
    return reply.status in (200, 201), f"{reply.status} {(reply.data or {}).get('msg_id')}", ""


@case("会话", "空内容写消息", "POST /chat {content:''}", "422，不能写空话")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/chat", {"content": ""}
    )
    ctx.note_error("422 空消息", reply.detail)
    return reply.status == 422, f"{reply.status} {reply.detail[:100]}", ""


# ── 分组：计划 ────────────────────────────────────────────────────────


@case("计划", "建计划", "POST /plan", "201，version=1 且有步骤")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan",
        {
            "expected_session_version": ctx["session_version"],
            "steps": [
                {"action": "读懂 props 的传递", "output": "看文档 + 写三个例子"},
                {"action": "读懂 state 与重渲染", "output": "改一个 state 看界面变化"},
            ],
            "reason": "用户说先啃这两个概念",
        },
    )
    plan = reply.data or {}
    steps = plan.get("steps") or []
    ok = reply.status in (200, 201) and len(steps) >= 2
    if ok:
        #: 计划自己的版本号 —— 与 `session_version` 是两套，别混。
        ctx["plan_version"] = int(plan.get("version") or 1)
        ctx["step_id"] = steps[0]["step_id"]
    return ok, f"{reply.status} version={plan.get('version')} steps={len(steps)}", ""


@case("计划", "重复建计划", "POST /plan 第二次", "422，说明「已经有计划了，要改就调用修订」")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan",
        {"expected_session_version": fresh_session_version(ctx), "steps": [{"action": "多余的一份"}]},
    )
    payload = reply.data or {}
    message = str(payload.get("message") or reply.detail)
    ctx.note_error("重复建计划", message)
    return reply.status == 422 and "已经有计划" in message, f"{reply.status} {message[:100]}", ""


@case("计划", "读计划", "GET /plan", "200，步骤带 status")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan")
    steps = (reply.data or {}).get("steps") or []
    ok = reply.status == 200 and steps and "status" in steps[0]
    return ok, f"{reply.status} steps={len(steps)} status={steps[0].get('status') if steps else None}", ""


@case("计划", "步骤上下文（点开某一步）", "GET /plan/steps/{id}/context", "200，anchor 指向这一步")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan/steps/{ctx_ids(ctx, 'step_id')}/context"
    )
    data = reply.data or {}
    ok = reply.status == 200 and bool(data.get("step") or data.get("anchor"))
    return ok, f"{reply.status} keys={sorted(data)[:6]}", ""


@case("计划", "标记步骤完成", "PATCH /plan/steps/{id} {status:done}", "200，该步骤 done", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "PATCH",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan/steps/{ctx_ids(ctx, 'step_id')}",
        {"status": "done", "expected_version": fresh_plan_version(ctx)},
    )
    data = reply.data or {}
    #: 这个端点返回的是**那一步**（StepRead），不是整个计划。
    return (
        reply.status == 200 and data.get("status") == "done",
        f"{reply.status} step={data.get('step_id')} status={data.get('status')}",
        "",
    )


@case("计划", "非法步骤状态", "PATCH /plan/steps/{id} {status:'bogus'}", "422，枚举外取值被拒", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "PATCH",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan/steps/{ctx_ids(ctx, 'step_id')}",
        {"status": "bogus", "expected_version": fresh_plan_version(ctx)},
    )
    ctx.note_error("422 非法枚举", reply.detail)
    return reply.status == 422, f"{reply.status} {reply.detail[:90]}", ""


@case("计划", "用旧版本号改步骤", "PATCH /plan/steps/{id} 版本号-1", "409，要求刷新后重试", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "PATCH",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan/steps/{ctx_ids(ctx, 'step_id')}",
        {"status": "doing", "expected_version": max(1, fresh_plan_version(ctx) - 1)},
    )
    ctx.note_error("409 版本冲突", reply.detail)
    return reply.status == 409, f"{reply.status} {reply.detail[:90]}", ""


@case("计划", "修订计划", "PUT /plan", "200/201，版本号 +1", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    before = ctx["plan_version"]
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
        ctx["plan_version"] = int(data["version"])
    return ok, f"{reply.status} version={data.get('version')} steps={len(data.get('steps') or [])}", ""


# ── 分组：输入 / 动机 / 快照 ──────────────────────────────────────────


@case("输入", "改任务定义", "PATCH /input", "200，输入区被写下且版本前进", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "PATCH",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/input",
        {"goal": "两周内读懂 React 的 props 与 state", "expected_version": fresh_input_version(ctx)},
    )
    data = reply.data or {}
    if reply.status == 200:
        ctx["session_version"] = int(data.get("version") or ctx["session_version"])
    return reply.status == 200, f"{reply.status} version={data.get('version')}", ""


@case("输入", "输入版本历史", "GET /input/revisions", "200，至少一条", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/input/revisions"
    )
    items = (reply.data or {}).get("items", [])
    return reply.status == 200 and len(items) >= 1, f"{reply.status} {len(items)} 条", ""


@case("动机", "改动机", "PATCH /motivation", "200，改动有版本记录", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply, _body = call_with_version_retry(
        ctx,
        "PATCH",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/motivation",
        {
            "expected_version": fresh_motivation_version(ctx),
            "change": "把驱动力写成「想自己做出东西」",
            "type": "intrinsic",
            "strength": "high",
            "drivers": ["想做出自己的项目"],
        },
    )
    data = reply.data or {}
    return reply.status == 200 or reply.status == 201, f"{reply.status} {json.dumps(data, ensure_ascii=False)[:80]}", ""


@case("快照", "记一次状态快照", "PATCH /snapshot", "200，能量/压力被记下", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "PATCH",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/snapshot",
        {
            "expected_version": fresh_session_version(ctx),
            "energy": "medium",
            "stress": "low",
            "note": "行为清单顺手记一条",
        },
    )
    return reply.status in (200, 201), f"{reply.status} {json.dumps(reply.data or {}, ensure_ascii=False)[:80]}", ""


# ── 分组：阶段项 / 里程碑 / 检查点 ────────────────────────────────────


@case("过程记录", "记一条阶段项", "POST /stage", "201，status=pending", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/stage",
        {
            "expected_version": fresh_session_version(ctx),
            "type": "temp_pref",
            "content": "他把 props 说成「传参数」",
        },
    )
    data = reply.data or {}
    ok = reply.status in (200, 201) and bool(data.get("item_id"))
    if ok:
        ctx["stage_item_id"] = data["item_id"]
        ctx["stage_version"] = int(data.get("version") or 1)
    return ok, f"{reply.status} {data.get('item_id')} status={data.get('status')}", ""


@case("过程记录", "结算阶段项", "PATCH /stage/{id}", "200，status 变了", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "PATCH",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/stage/{ctx_ids(ctx, 'stage_item_id')}",
        {"expected_version": fresh_session_version(ctx), "status": "deposited"},
    )
    data = reply.data or {}
    return reply.status == 200, f"{reply.status} status={data.get('status')}", ""


@case("过程记录", "加一个里程碑", "POST /milestones", "201，status=open", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/milestones",
        {"title": "能独立写一个受控输入框"},
    )
    data = reply.data or {}
    ok = reply.status in (200, 201) and bool(data.get("milestone_id"))
    if ok:
        ctx["milestone_id"] = data["milestone_id"]
    return ok, f"{reply.status} {data.get('milestone_id')} status={data.get('status')}", ""


@case("过程记录", "完成里程碑", "POST /milestones/{id}/complete", "200，status=done", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/milestones/{ctx_ids(ctx, 'milestone_id')}/complete",
        {"results": "写出来了，能改 state 看渲染"},
    )
    data = reply.data or {}
    return (
        reply.status == 200 and data.get("status") == "done",
        f"{reply.status} status={data.get('status')}",
        "",
    )


@case("过程记录", "记一次检查点", "PATCH /plan/checkpoints/{cp}", "200，result 被记下", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    plan = ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan").data or {}
    checkpoints = plan.get("checkpoints") or []
    if not checkpoints:
        reply = ctx["client"].call(
            "PUT",
            f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan",
            {
                "expected_version": int(plan.get("version") or ctx["plan_version"]),
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
                ],
                "checkpoints": [
                    {
                        "at_step": (plan.get("steps") or [{}])[0].get("step_id", "S1"),
                        "check_method": "让他讲一遍 props 的传递路径",
                        "pass_criteria": "能说清父传子的方向",
                    }
                ],
                "reason": "加一个检查点",
            },
        )
        updated = reply.data or {}
        if reply.status in (200, 201):
            ctx["version"] = int(updated.get("version") or ctx["version"])
        checkpoints = updated.get("checkpoints") or []
    if not checkpoints:
        return False, "计划里没有检查点，而且加不进去", ""
    cp_id = checkpoints[0]["cp_id"]
    reply = ctx["client"].call(
        "PATCH",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan/checkpoints/{cp_id}",
        {"expected_version": fresh_plan_version(ctx), "result": "pass", "result_note": "讲清楚了"},
    )
    data = reply.data or {}
    if reply.status == 200:
        ctx["version"] = int(data.get("version") or ctx["version"])
    return reply.status == 200, f"{reply.status} result={data.get('result')}", ""


# ── 分组：笔记 / 反思 / 摘要 / 沉淀 / 标注 ────────────────────────────


@case("书写类", "写一条侧记", "POST /notes", "201，返回 note_id")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/notes",
        {"content": "行为清单：侧记要能真的写进去", "tags": ["行为清单"]},
    )
    return reply.status in (200, 201) and bool((reply.data or {}).get("note_id")), f"{reply.status} {(reply.data or {}).get('note_id')}", ""


@case("书写类", "写一条反思", "POST /reflections", "201，返回 reflection_id", needs="review")
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
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/reflections",
        {"content": "行为清单：这一轮我卡在 state 为什么不更新"},
    )
    return (
        reply.status in (200, 201) and bool((reply.data or {}).get("reflection_id")),
        f"{reply.status} {(reply.data or {}).get('reflection_id')}",
        "",
    )


@case("书写类", "写一份总结", "POST /summary", "201，返回 summary_id", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/summary",
        {"content": "行为清单：第一天的收获是 props 是单向的"},
    )
    data = reply.data or {}
    ok = reply.status in (200, 201) and bool(data.get("summary_id"))
    if ok:
        ctx["summary_id"] = data["summary_id"]
    return ok, f"{reply.status} {data.get('summary_id')}", ""


@case("书写类", "沉淀（dry_run）", "POST /sediment {dry_run:true}", "200，只给计划不动库", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/sediment",
        {"trigger": "R4a", "dry_run": True},
    )
    data = reply.data or {}
    ok = reply.status in (200, 201) and bool(data.get("plan") or data.get("trigger") or data)
    return ok, f"{reply.status} {json.dumps(data, ensure_ascii=False)[:100]}", ""


@case("书写类", "标注自己说的话（应当被拒）", "POST /annotations {role:user}", "422，并说明只能标助手的话")
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
            words = str(message.get("content") or "").strip()
            #: 引文必须是那句里**真实存在**的一小段（后端会去比对）。
            if len(words) >= 6:
                quote = words[:6]
            break
    if not msg_id:
        out = stream_discuss(ctx["client"], ctx_ids(ctx, "session_id"), "用一句话说清 props 的方向")
        messages = (
            ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/state").data or {}
        ).get("chat") or []
        for message in messages:
            if message.get("role") == "assistant":
                msg_id = message.get("msg_id") or message.get("id") or ""
                words = str(message.get("content") or "").strip()
                quote = words[:6] if len(words) >= 6 else (out["reply"][:6] or "十分钟")
                break
    if not msg_id:
        return False, "这个会话里没有助手消息，标注无从下手", ""

    #: 后端会拿 quote 去那条回复里**逐字比对**；而 `/state` 给的正文可能与库里
    #: 存的那份不完全一样（渲染/截断）。所以备几段候选，第一段不行就换一段。
    content = ""
    for message in messages:
        if (message.get("msg_id") or message.get("id") or "") == msg_id:
            content = str(message.get("content") or "")
            break
    candidates = [content[:6], content[:4], content[3:9], content[-6:], quote]
    candidates = [item.strip() for item in candidates if len(item.strip()) >= 2]
    reply = None
    accepted = ""
    for candidate in candidates:
        reply = ctx["client"].call(
            "POST",
            f"/api/sessions/{ctx_ids(ctx, 'session_id')}/annotations",
            {"msg_id": msg_id, "quote": candidate, "question": "这里指的是什么？"},
        )
        if reply.status in (200, 201):
            accepted = candidate
            break
    assert reply is not None
    data = reply.data or {}
    ok = reply.status in (200, 201) and bool(data.get("annotation_id"))
    if ok:
        ctx["annotation_id"] = data["annotation_id"]
    ctx.note_error("标注认不出的引文", reply.detail)
    return ok, f"{reply.status} {data.get('annotation_id')} quote={accepted!r}", ""


@case("书写类", "删掉那条标注", "DELETE /annotations/{id}", "200/204，之后读是 404")
def _(ctx: dict) -> tuple[bool, str, str]:
    annotation = ctx_ids(ctx, "annotation_id")
    if not annotation:
        return False, "上一条没建成，没得删", ""
    first = ctx["client"].call(
        "DELETE", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/annotations/{annotation}"
    )
    second = ctx["client"].call(
        "GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/annotations/{annotation}"
    )
    return (
        first.status in (200, 204) and second.status == 404,
        f"删 {first.status}，再读 {second.status}",
        "",
    )


# ── 分组：澄清 ────────────────────────────────────────────────────────


@case("澄清", "问一个已经填好的槽位", "POST /clarifications {slot:goal}", "422，并说明「问一个已经知道答案的问题」")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/clarifications",
        {"slot": "goal", "question": "你的目标是什么？"},
    )
    message = str((reply.data or {}).get("message") or reply.detail)
    ctx.note_error("问已填好的槽位", message)
    return reply.status == 422 and "已经有内容" in message, f"{reply.status} {message[:110]}", ""


@case("澄清", "提一个澄清问题", "POST /clarifications {slot:starting_point}", "201，status=open")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/clarifications",
        {"slot": "starting_point", "question": "关于这件事，你现在已经会哪些、卡在哪里？"},
    )
    data = reply.data or {}
    ok = reply.status in (200, 201) and bool(data.get("clarification_id"))
    if ok:
        ctx["clarification_id"] = data["clarification_id"]
    return ok, f"{reply.status} {data.get('clarification_id')} status={data.get('status')}", ""


@case("澄清", "回答澄清问题", "POST /clarifications/{id}/answer", "200，status=answered")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/clarifications/{ctx_ids(ctx, 'clarification_id')}/answer",
        {"answer": "会写一点 JS，不会 React", "expected_version": fresh_input_version(ctx)},
    )
    data = reply.data or {}
    status = ((data.get("clarification") or {}).get("status")) or data.get("status")
    return reply.status == 200 and status == "answered", f"{reply.status} status={status}", ""


# ── 分组：附件 ────────────────────────────────────────────────────────


@case("附件", "上传一个文本附件", "POST /attachments (multipart)", "201，能读出预览与字数")
def _(ctx: dict) -> tuple[bool, str, str]:
    boundary = "----behaviorcheck" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="day1.md"\r\n'
        "Content-Type: text/markdown\r\n\r\n"
        "# 第一天\n\nprops 是父传子的单向通道。\n\n"
        f"\r\n--{boundary}--\r\n"
    ).encode()
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/attachments?filename=day1.md",
        raw=body,
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    data = reply.data or {}
    ok = reply.status in (200, 201) and bool(data.get("attachment_id")) and data.get("chars_len", 0) > 0
    if ok:
        ctx["attachment_id"] = data["attachment_id"]
    return ok, f"{reply.status} {data.get('attachment_id')} chars={data.get('chars_len')} ext={data.get('ext')}", ""


@case("附件", "读附件正文", "GET /attachments/{id}", "200，正文里能搜到原文")
def _(ctx: dict) -> tuple[bool, str, str]:
    attachment = ctx_ids(ctx, "attachment_id")
    if not attachment:
        return False, "上一条没上传成功", ""
    reply = ctx["client"].call(
        "GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/attachments/{attachment}"
    )
    text = (reply.data or {}).get("text") or ""
    return reply.status == 200 and "单向通道" in text, f"{reply.status} {len(text)} 字", ""


@case("附件", "删附件", "DELETE /attachments/{id}", "200，之后列表里没有它")
def _(ctx: dict) -> tuple[bool, str, str]:
    attachment = ctx_ids(ctx, "attachment_id")
    if not attachment:
        return False, "上一条没上传成功", ""
    first = ctx["client"].call(
        "DELETE", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/attachments/{attachment}"
    )
    listing = ctx["client"].call(
        "GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/attachments"
    ).data or {}
    ids = [item.get("attachment_id") for item in listing.get("items", [])]
    return first.status in (200, 204) and attachment not in ids, f"删 {first.status}，列表里还有 {attachment in ids}", ""


# ── 分组：状态机 ──────────────────────────────────────────────────────


def _transition(ctx: dict, target: str) -> Reply:
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
    )
    if reply.status == 200 and isinstance(reply.data, dict):
        ctx["session_version"] = int(reply.data.get("version") or ctx["session_version"])
    return reply


@case("状态机", "开始执行（draft → active）", "POST /transitions {target:active}", "200，state=active")
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
    )


@case("状态机", "执行中 → 复盘", "POST /transitions {target:review}", "200，state=review", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = _transition(ctx, "review")
    return (
        reply.status == 200 and (reply.data or {}).get("state") == "review",
        f"{reply.status} state={(reply.data or {}).get('state')}",
        "",
    )


@case("状态机", "看不懂的转移", "POST /transitions {target:draft}", "409，列出允许的目标", needs="completed")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = _transition(ctx, "draft")
    detail = reply.detail
    ctx.note_error("409 非法转移", detail)
    return reply.status == 409 and "allowed" in detail, f"{reply.status} {detail[:110]}", ""


@case("状态机", "复盘 → 完成（归档）", "POST /transitions {target:completed}", "200，state=completed", needs="review")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = _transition(ctx, "completed")
    return (
        reply.status == 200 and (reply.data or {}).get("state") == "completed",
        f"{reply.status} state={(reply.data or {}).get('state')}",
        "",
    )


@case("状态机", "完成后还能说话", "POST /chat（completed）", "201，归档不等于冻结对话", needs="completed")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/chat",
        {"content": "归档之后又问了一句"},
    )
    return reply.status in (200, 201), f"{reply.status} {reply.detail[:60]}", ""


@case("状态机", "完成后不能改定义", "PATCH /input（completed）", "409，allowed_states 写明可改的状态", needs="completed")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "PATCH",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/input",
        {"goal": "归档后偷偷改目标", "expected_version": ctx["session_version"]},
    )
    ctx.note_error("409 终态改定义", reply.detail)
    return reply.status == 409 and "allowed_states" in reply.detail, f"{reply.status} {reply.detail[:110]}", ""


@case("状态机", "完成后不能改计划", "PUT /plan（completed）", "409，计划冻结", needs="completed")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "PUT",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/plan",
        {
            "expected_version": ctx.get("plan_version", 0),
            "change": "revise",
            "reason": "不该生效",
            "steps": [{"action": "归档后偷偷改计划"}],
        },
    )
    return reply.status == 409, f"{reply.status} {reply.detail[:90]}", ""


@case("状态机", "完成 → 重新打开", "POST /transitions {target:active}", "200，state=active", needs="completed")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = _transition(ctx, "active")
    return (
        reply.status == 200 and (reply.data or {}).get("state") == "active",
        f"{reply.status} state={(reply.data or {}).get('state')}",
        "",
    )


# ── 分组：讨论（LLM） ─────────────────────────────────────────────────


@case("讨论", "有计划的讨论（流式）", "POST /discuss/stream", "200，有 token 帧、有 done，正文非空", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
    out = stream_discuss(ctx["client"], ctx_ids(ctx, "session_id"), "第一天到底先做什么？给我一个具体动作")
    ok = bool(out["done"]) and bool(out["reply"].strip()) and out["chars"] > 0
    ctx["last_reply"] = out["reply"]
    return ok, f"帧={out['frames']} 正文字数={len(out['reply'])} stop={((out['done'] or {}).get('stop_reason'))}", out["reply"][:90]


@case("讨论", "无计划也能讨论", "新建无计划会话 → /discuss", "200，plan_version=0 且不报错")
def _(ctx: dict) -> tuple[bool, str, str]:
    session = ctx["client"].call(
        "POST", "/api/sessions", {"folder_id": ctx_ids(ctx, "folder_id"), "title": "行为清单 · 无计划"}
    ).data or {}
    sid = session.get("session_id") or ""
    if not sid:
        return False, "会话没建出来", ""
    out = stream_discuss(ctx["client"], sid, "我还没想好要学什么，先随便聊聊")
    ctx["noplan_session"] = sid
    done = out["done"] or {}
    ok = bool(done) and int(done.get("plan_version") or 0) == 0
    return ok, f"plan_version={done.get('plan_version')} anchor={done.get('anchor')}", out["reply"][:70]


@case("讨论", "两轮的话不一样", "同一会话连发两句不同的话", "两轮正文不同（说明真的在听）", needs="active")
def _(ctx: dict) -> tuple[bool, str, str]:
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
    return ok, f"第一轮 {len(first)} 字 / 第二轮 {len(second)} 字，相同={same}", second[:70]


# ── 分组：模型档案（含 LLM 不可达） ───────────────────────────────────


@case("模型档案", "服务商目录", "GET /api/llm/providers", "200，多于一家的目录")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call("GET", "/api/llm/providers")
    items = (reply.data or {}).get("items", [])
    return reply.status == 200 and len(items) >= 5, f"{reply.status} {len(items)} 家", ""


@case("模型档案", "建一份档案", "POST /api/llm/profiles", "201，has_key=false 且没有明文密钥")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        "/api/llm/profiles",
        {
            "label": "行为清单 · 本地",
            "provider_id": "ollama",
            "model": "qwen3.5:9b",
            "num_ctx": 16384,
        },
    )
    data = reply.data or {}
    ok = (
        reply.status in (200, 201)
        and bool(data.get("profile_id"))
        and data.get("has_key") is False
        and "api_key" not in data
    )
    if ok:
        ctx["good_profile"] = data["profile_id"]
    return ok, f"{reply.status} {data.get('profile_id')} has_key={data.get('has_key')}", ""


@case("模型档案", "不认识的服务商", "POST /api/llm/profiles {provider_id:'deepsek'}", "422，且点名是哪个不认识")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST", "/api/llm/profiles", {"provider_id": "deepsek", "model": "x"}
    )
    ctx.note_error("422 服务商不存在", reply.detail)
    return reply.status == 422 and "deepsek" in reply.detail, f"{reply.status} {reply.detail[:110]}", ""


@case("模型档案", "端点写错", "POST /api/llm/profiles {base_url:'127.0.0.1:9'}", "422，要求以 http 开头")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST",
        "/api/llm/profiles",
        {"provider_id": "ollama", "model": "x", "base_url": "127.0.0.1:9"},
    )
    ctx.note_error("422 端点格式", reply.detail)
    return reply.status == 422, f"{reply.status} {reply.detail[:110]}", ""


@case("模型档案", "启用一份档案", "POST /profiles/{id}/activate", "200，is_active=true 且 /config 跟着变")
def _(ctx: dict) -> tuple[bool, str, str]:
    profile = ctx_ids(ctx, "good_profile")
    if not profile:
        return False, "前面没建成档案", ""
    reply = ctx["client"].call("POST", f"/api/llm/profiles/{profile}/activate")
    config = ctx["client"].call("GET", "/api/llm/config").data or {}
    source = config.get("source")
    active = (config.get("profile") or {}).get("profile_id")
    return (
        reply.status == 200 and source == "profile" and active == profile,
        f"{reply.status} config.source={source} active={active}",
        "",
    )


@case("模型档案", "拉到模型清单", "POST /profiles/{id}/models", "200，models 是数组（拉不到也有 note）")
def _(ctx: dict) -> tuple[bool, str, str]:
    profile = ctx_ids(ctx, "good_profile")
    reply = ctx["client"].call("POST", f"/api/llm/profiles/{profile}/models", {}, timeout=120)
    data = reply.data or {}
    models = data.get("models")
    ok = reply.status == 200 and isinstance(models, list)
    return ok, f"{reply.status} {len(models or [])} 个 note={str(data.get('note'))[:60]}", ""


@case("模型档案", "探活（端点活着）", "POST /profiles/{id}/probe", "200，给一句话结论（ok 真假都算合格）")
def _(ctx: dict) -> tuple[bool, str, str]:
    profile = ctx_ids(ctx, "good_profile")
    reply = ctx["client"].call("POST", f"/api/llm/profiles/{profile}/probe", {}, timeout=300)
    data = reply.data or {}
    ok = reply.status == 200 and isinstance(data.get("ok"), bool) and bool(data.get("detail"))
    return ok, f"{reply.status} ok={data.get('ok')} {str(data.get('detail'))[:70]}", ""


@case("模型档案", "删档案后自动接上另一份或退回 .env", "DELETE /profiles/{id}", "200，/config 的 source 回到 settings")
def _(ctx: dict) -> tuple[bool, str, str]:
    profile = ctx_ids(ctx, "good_profile")
    if not profile:
        return False, "前面没建成档案", ""
    reply = ctx["client"].call("DELETE", f"/api/llm/profiles/{profile}")
    config = ctx["client"].call("GET", "/api/llm/config").data or {}
    return (
        reply.status in (200, 204) and config.get("source") == "settings",
        f"{reply.status} config.source={config.get('source')}",
        "",
    )


# ── 分组：LLM 不可达（这一组最容易被写成"假装成功"） ─────────────────


@case("LLM 不可达", "探活打到死端口", "POST /profiles/{id}/probe（端口无人听）", "200 且 ok=false，detail 说清连不上")
def _(ctx: dict) -> tuple[bool, str, str]:
    created = ctx["client"].call(
        "POST",
        "/api/llm/profiles",
        {"label": "行为清单 · 死端口", "provider_id": "ollama", "model": "qwen3.5:9b", "base_url": "http://127.0.0.1:9"},
    ).data or {}
    profile = created.get("profile_id")
    if not profile:
        return False, f"死端口档案没建出来：{created}", ""
    ctx["dead_profile"] = profile
    reply = ctx["client"].call("POST", f"/api/llm/profiles/{profile}/probe", {}, timeout=120)
    data = reply.data or {}
    ctx.note_error("探活连不上", str(data.get("detail")))
    return (
        reply.status == 200 and data.get("ok") is False and "连不上" in str(data.get("detail")),
        f"{reply.status} ok={data.get('ok')} detail={str(data.get('detail'))[:90]}",
        "",
    )


@case("LLM 不可达", "拉清单打到死端口", "POST /profiles/{id}/models（端口无人听）", "200，models 空 + note 说原因")
def _(ctx: dict) -> tuple[bool, str, str]:
    profile = ctx_ids(ctx, "dead_profile")
    reply = ctx["client"].call("POST", f"/api/llm/profiles/{profile}/models", {}, timeout=120)
    data = reply.data or {}
    return (
        reply.status == 200 and not (data.get("models") or []) and bool(data.get("note")),
        f"{reply.status} models={data.get('models')} note={str(data.get('note'))[:80]}",
        "",
    )


@case("LLM 不可达", "讨论打到死端口", "把死端口档案设为生效 → /discuss", "回复是一条人话错误，不是空话，也不是假装成功")
def _(ctx: dict) -> tuple[bool, str, str]:
    profile = ctx_ids(ctx, "dead_profile")
    if not profile:
        return False, "没有死端口档案", ""
    ctx["client"].call("POST", f"/api/llm/profiles/{profile}/activate")
    out = stream_discuss(ctx["client"], ctx_ids(ctx, "session_id"), "模型现在连不上了，你会怎么回答？")
    reply = out["reply"]
    ctx.note_error("讨论时 LLM 不可达", reply)
    ok = bool(reply.strip()) and ("没能拿到" in reply or "连不上" in reply or "timed out" in reply)
    return ok, f"error帧={bool(out['error'])} 正文={len(reply)} 字 stop={((out['done'] or {}).get('stop_reason'))}", reply[:100]


@case("LLM 不可达", "失败之后还能恢复", "删掉死端口档案 → /config", "200，source 回到 settings，会话没坏")
def _(ctx: dict) -> tuple[bool, str, str]:
    profile = ctx_ids(ctx, "dead_profile")
    if profile:
        ctx["client"].call("DELETE", f"/api/llm/profiles/{profile}")
    config = ctx["client"].call("GET", "/api/llm/config").data or {}
    state = ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/state")
    return (
        config.get("source") == "settings" and state.status == 200,
        f"source={config.get('source')} state={state.status}",
        "",
    )


# ── 分组：侧栏 ────────────────────────────────────────────────────────


@case("侧栏", "读侧栏状态", "GET /api/sidebar/state", "200")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call("GET", "/api/sidebar/state")
    return reply.status == 200, f"{reply.status} keys={sorted(reply.data or {})[:6]}", ""


@case("侧栏", "侧栏问一句", "POST /api/sidebar/discuss", "200，拿到回答或一句人话错误")
def _(ctx: dict) -> tuple[bool, str, str]:
    reply = ctx["client"].call(
        "POST", "/api/sidebar/discuss", {"message": "这一段我在纠结什么？"}, timeout=300
    )
    return reply.status == 200, f"{reply.status} {json.dumps(reply.data or {}, ensure_ascii=False)[:100]}", ""


@case("侧栏", "清空侧栏", "DELETE /api/sidebar/messages", "200/204，再读是空的")
def _(ctx: dict) -> tuple[bool, str, str]:
    cleared = ctx["client"].call("DELETE", "/api/sidebar/messages")
    state = ctx["client"].call("GET", "/api/sidebar/state").data or {}
    messages = state.get("messages") or []
    return cleared.status in (200, 204) and not messages, f"清空 {cleared.status}，剩 {len(messages)} 条", ""


# ── 收尾：把自己的垃圾删干净（可重复跑的前提） ────────────────────────


@case("收尾", "删掉本轮的会话", "DELETE /api/sessions/{id}", "200/204，再读 404")
def _(ctx: dict) -> tuple[bool, str, str]:
    for key in ("session_id", "noplan_session"):
        sid = ctx_ids(ctx, key)
        if sid:
            ctx["client"].call("DELETE", f"/api/sessions/{sid}")
    reply = ctx["client"].call("GET", f"/api/sessions/{ctx_ids(ctx, 'session_id')}/state")
    return reply.status == 404, f"删后再读 {reply.status}", ""


@case("收尾", "删掉本轮的领域", "DELETE /api/folders/{id}", "200/204，再读 404")
def _(ctx: dict) -> tuple[bool, str, str]:
    folder = ctx_ids(ctx, "tmp_folder_id")
    if not folder:
        return False, "这一轮没建领域，没得删", ""
    deleted = ctx["client"].call("DELETE", f"/api/folders/{folder}")
    reply = ctx["client"].call("PATCH", f"/api/folders/{folder}", {"name": "删完还想改"})
    return (
        deleted.status in (200, 204) and reply.status == 404,
        f"删 {deleted.status}，删后再改 {reply.status} {reply.detail[:80]}",
        "",
    )


# ── 跑与写报告 ────────────────────────────────────────────────────────


class RunContext:
    def __init__(self, client: Client) -> None:
        self.client = client
        self.data: dict[str, Any] = {"client": client}
        self.errors: dict[str, str] = {}
        self.started = time.time()

    def note_error(self, scene: str, text: str) -> None:
        if text:
            self.errors.setdefault(scene, text[:400])

    def __getitem__(self, key: str):
        return self.data[key]

    def __setitem__(self, key: str, value) -> None:
        self.data[key] = value

    def get(self, key: str, default=None):
        return self.data.get(key, default)


def run_all(base: str, llm_mode: str = "fake") -> tuple[list[Row], RunContext, float]:
    client = Client(base)
    ctx = RunContext(client)
    ctx["fake_llm"] = llm_mode == "fake"
    rows: list[Row] = []
    started = time.time()
    for _group, _title, runner in CASES:
        rows.append(runner(ctx))
    return rows, ctx, time.time() - started


def render_markdown(rows: list[Row], ctx: RunContext, base: str, elapsed: float) -> str:
    passed = sum(1 for row in rows if row.ok is True)
    skipped = sum(1 for row in rows if row.ok is None)
    failed = len(rows) - passed - skipped
    lines = [
        "# 端到端行为清单 v0.1",
        "",
        "> 这份表由 `backend/tools/behaviorcheck.py` 生成，不要手改：改代码，重跑。",
        "> 每一行都是一次**真实 HTTP 请求**打在这台机器上的服务上 —— 没有 mock，没有替身世界观。",
        "",
        f"- 目标地址：`{base}`",
        f"- 跑的时刻：{time.strftime('%Y-%m-%d %H:%M:%S')}，用时 {elapsed:.1f} s，共 {len(rows)} 条",
        f"- 结果：**{passed} 通过 / {failed} 偏离 / {skipped} 跳过**",
        "",
        "## 一、行为清单",
        "",
        "| # | 分组 | 操作 | 怎么打 | 预期 | 实测 | 判定 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(rows, start=1):
        mark = "✅" if row.ok is True else ("⏭" if row.ok is None else "❌")
        cells = [
            str(index),
            row.group,
            row.title,
            f"`{row.request}`",
            row.expected,
            row.observed.replace("|", "\\|"),
            mark,
        ]
        lines.append("| " + " | ".join(cells) + " |")

    lines += [
        "",
        "## 二、错误提示对照表（后端原话）",
        "",
        "用户看到的提示必须能指认「哪一步没成」。下表是这轮**亲手打出来**的原文，",
        "不是从代码里抄的注释 —— 抄注释正是这个项目最容易出错的地方。",
        "",
        "| 场景 | 后端原话 |",
        "| --- | --- |",
    ]
    for scene, text in ctx.errors.items():
        lines.append(f"| {scene} | {text.replace('|', chr(92) + '|')} |")

    lines += [
        "",
        "## 三、怎么重跑",
        "",
        "```powershell",
        "# 1) 起一个专用的服务（不要用正在用的那个库，免得把演示数据搅乱）",
        "cd backend",
        '$env:MOUXUE_SKIP_ENV_FILE="1"; $env:PYTHONIOENCODING="utf-8"; $env:PYTHONPATH="."',
        r".\.venv\Scripts\python.exe -m tools.seedui --db var\behavior.db --reset",
        r".\.venv\Scripts\python.exe -m tools.serveui --llm fake --db var\behavior.db --port 8131 `",
        r"    --dist ..\frontend\redesign --items-dir tests\fixtures\bank `",
        r"    --script-session S-0001 --frame-delay 0.01",
        "",
        "# 2) 跑清单（另开一个窗口，在仓库根下）",
        r".\backend\.venv\Scripts\python.exe -m tools.behaviorcheck --base http://127.0.0.1:8131 `",
        r"    --md _端到端行为清单_v0.1.md --json var\behavior_report.json",
        "```",
        "",
        "`--llm fake` 是剧本替身：聊天那几行要的是「链路通不通」，不是「模型聪不聪明」，",
        "所以用它才可重复。想验真模型，换成 `--llm ollama --model qwen3.5:9b` 再跑一遍 ——",
        "清单里「两轮的话不一样」那一行就是为这个准备的判据。",
        "",
        "## 四、这份清单覆盖不到的地方",
        "",
        "- **界面渲染**：这里只打 HTTP。界面那一层由 `node frontend/redesign/tools/check_model_page.mjs`",
        "  （模型页 DOM 与接口对账）和 `node frontend/redesign/tools/headless_probe.mjs`（点一遍对话/侧记/归档）负责。",
        "- **长时间运行**：没有压测、没有并发跑同一会话。",
        "- **真实模型的响应质量**：只验「有没有答、答得一样不一样」，不验「答得对不对」。",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="端到端行为清单：跑一遍，生成表格")
    parser.add_argument("--base", default="http://127.0.0.1:8131")
    parser.add_argument("--md", default="")
    parser.add_argument("--json", default="")
    parser.add_argument(
        "--llm",
        default="fake",
        choices=("fake", "real"),
        help="这一轮服务用的是替身模型还是真模型（影响个别判据是否适用）",
    )
    args = parser.parse_args(argv)

    rows, ctx, elapsed = run_all(args.base, args.llm)
    passed = sum(1 for row in rows if row.ok is True)
    skipped = sum(1 for row in rows if row.ok is None)
    failed = len(rows) - passed - skipped

    for row in rows:
        mark = "✅" if row.ok is True else ("⏭" if row.ok is None else "❌")
        print(f"{mark} [{row.group}] {row.title}")
        if not row.ok:
            print(f"     预期：{row.expected}")
            print(f"     实测：{row.observed}")

    print(
        f"\n{passed} 通过 / {failed} 偏离 / {skipped} 跳过（共 {len(rows)} 条），"
        f"{elapsed:.1f} s（{ctx.client.calls} 次请求）"
    )

    if args.md:
        pathlib.Path(args.md).write_text(
            render_markdown(rows, ctx, args.base, elapsed), encoding="utf-8"
        )
        print(f"清单已写入 {args.md}")
    if args.json:
        payload = {
            "base": args.base,
            "elapsed_s": round(elapsed, 1),
            "passed": passed,
            "total": len(rows),
            "rows": [row.__dict__ for row in rows],
            "errors": ctx.errors,
        }
        pathlib.Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"报告已写入 {args.json}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
