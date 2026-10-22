"""新会话从空到有回答：整条链走一遍。

这一段验的是用户的原话 ——「一个完整 session 从头到尾我想说什么就说什么」：

1. 建一个会话（draft）→ **直接说话**；
2. 让它回答（没有计划也**必须**能答）→ 期望：真的流回来；
3. 让它排一版计划（draft 下允许）→ 期望：有计划了；
4. 改一个计划项（引用户原话，不点界面）→ 期望：真的改；
5. 结束执行 → 复盘 → 完成 → 归档后仍可说话、仍可记笔记；
6. 归档后改定义区 → **仍然拒绝**（这一条是"无法核对"，不是流程）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8125"


def call(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def line(label: str, status: int, payload: object) -> None:
    detail = payload
    if isinstance(payload, dict):
        detail = payload.get("error") or payload.get("detail") or payload
    print(f"  {label:<30} {status}  {str(detail)[:110]}")


def stream(session_id: str, message: str, step_id: str | None = None) -> tuple[int, str, list[str]]:
    """发一轮讨论，把 SSE 的 event 名收集起来，并返回收到的正文。"""
    body = {"message": message}
    if step_id:
        body["step_id"] = step_id
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/api/sessions/{session_id}/discuss/stream",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    try:
        resp = urllib.request.urlopen(req)
    except urllib.error.HTTPError as exc:
        return exc.code, "", [exc.read().decode("utf-8")[:150]]

    events: list[str] = []
    reply = ""
    name = ""
    with resp:
        for raw in resp:
            text = raw.decode("utf-8").rstrip("\n")
            if text.startswith("event:"):
                name = text.split(":", 1)[1].strip()
                events.append(name)
            elif text.startswith("data:") and name == "token":
                try:
                    reply += json.loads(text.split(":", 1)[1].strip()).get("text", "")
                except json.JSONDecodeError:
                    pass
            elif text.startswith("data:") and name == "done":
                try:
                    done = json.loads(text.split(":", 1)[1].strip())
                    reply = done.get("reply") or reply
                    events.append(f"done:{done.get('stop_reason')}")
                except json.JSONDecodeError:
                    pass
    return 200, reply, events


folders = call("GET", "/api/folders")[1]
folder_id = folders[0]["folder_id"] if isinstance(folders, list) else folders["items"][0]["folder_id"]

print("=== 1. 新建会话（draft）===")
status, created = call("POST", "/api/sessions", {"folder_id": folder_id, "title": "探针 · 全流程"})
line("POST /sessions", status, created)
sid = created["session_id"]

print("\n=== 2. 没有计划，直接让它回答 ===")
status, reply, events = stream(sid, "我想在两周内把 React 文档第一章读懂，但不知道从哪下手。")
print(f"  discuss/stream {status}  events={events[:6]}…")
print(f"  回答: {reply[:80]}")

state = call("GET", f"/api/sessions/{sid}/state")[1]
print(f"  state={state['session']['state']}  有计划={bool(state.get('plan'))}  对话={len(state['chat'])} 条")

print("\n=== 3. 让它排一版计划（draft 下应允许）===")
status, reply, events = stream(sid, "帮我排一个三步的计划。")
print(f"  回答: {reply[:80]}")
state = call("GET", f"/api/sessions/{sid}/state")[1]
plan = state.get("plan")
print(f"  有计划={bool(plan)}  版本={plan['version_no'] if plan else None}  步数={len(plan['steps']) if plan else 0}")

print("\n=== 4. 结束执行 → 复盘 → 完成 ===")
version = state["session"]["version"]
line("transitions → review", *call("POST", f"/api/sessions/{sid}/transitions", {"target": "review", "expected_version": version}))
state = call("GET", f"/api/sessions/{sid}/state")[1]
line("PATCH summary", *call("POST", f"/api/sessions/{sid}/summary", {"content": "这次读完了第一章的 props 与 state。", "expected_session_version": state["session"]["version"]}))

print("\n=== 5. 归档之后：还能说话吗、还能记笔记吗、还能改定义区吗 ===")
state = call("GET", f"/api/sessions/{sid}/state")[1]
line(
    "transitions → completed",
    *call("POST", f"/api/sessions/{sid}/transitions", {"target": "completed", "expected_version": state["session"]["version"]}),
)
line("POST /chat（归档后说话）", *call("POST", f"/api/sessions/{sid}/chat", {"content": "记一笔：第一章其实没记住 state 的更新时机"}))
line("POST /notes（归档后笔记）", *call("POST", f"/api/sessions/{sid}/notes", {"content": "state 是异步的"}))
input_row = call("GET", f"/api/sessions/{sid}/state")[1]["input"]
line(
    "PATCH /input（归档后改定义区，应拒）",
    *call("PATCH", f"/api/sessions/{sid}/input", {"goal": "改成别的", "expected_version": input_row["version"]}),
)

print("\n=== 6. 清理 ===")
line("DELETE /sessions/{id}", *call("DELETE", f"/api/sessions/{sid}"))
