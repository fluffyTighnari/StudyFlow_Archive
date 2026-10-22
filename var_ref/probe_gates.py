"""用一个新建的会话，把「哪些门会挡住正常使用」实测一遍。

不猜、不读代码推断 —— 直接对着跑着的后端发请求，把每一步的
HTTP 码与错误原文打出来。要回答的是用户问的那两句：

1. 新建会话之后，为什么连一句 chat 都加不了？
2. 会话在哪些情况下"点不开 / 用不了"？
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


def show(label: str, status: int, payload: object) -> None:
    detail = payload
    if isinstance(payload, dict):
        detail = payload.get("error") or payload.get("detail") or payload
    print(f"{label:<34} {status}  {str(detail)[:150]}")


folders = call("GET", "/api/folders")[1]
folder_id = folders[0]["folder_id"] if isinstance(folders, list) else folders["items"][0]["folder_id"]

print("=== 一、新建一个会话（和你点「新会话」一样）===")
status, created = call(
    "POST",
    "/api/sessions",
    {"folder_id": folder_id, "title": "探针 · 门禁", "input_goal": "想知道到底哪些地方会挡我"},
)
show("POST /api/sessions", status, created)
sid = created["session_id"]

print("\n=== 二、它一出生是什么状态 ===")
status, state = call("GET", f"/api/sessions/{sid}/state")
print(f"state={state['session']['state']}  有计划={bool(state.get('plan'))}  有总结={bool(state.get('summary'))}")

print("\n=== 三、在这个新会话里，能做/不能做什么 ===")
show("POST /chat（说一句话）", *call("POST", f"/api/sessions/{sid}/chat", {"content": "你好"}))
show("POST /discuss/stream（让它回答）", *call("POST", f"/api/sessions/{sid}/discuss/stream", {"message": "你好"}))
show("POST /plan（让它建计划）", *call("POST", f"/api/sessions/{sid}/plan", {"steps": [{"action": "a", "output": "b"}], "reason": "r"}))
show("PATCH /input（改目标）", *call("PATCH", f"/api/sessions/{sid}/input", {"changes": {"goal": "改成新的目标"}, "expected_version": state["input"]["version"]}))
show("POST /notes（记笔记）", *call("POST", f"/api/sessions/{sid}/notes", {"content": "笔记"}))
show("POST /transitions → active", *call("POST", f"/api/sessions/{sid}/transitions", {"target": "active", "expected_version": state["session"]["version"]}))

print("\n=== 四、激活之后呢（再看一遍上面那几件）===")
status, state2 = call("GET", f"/api/sessions/{sid}/state")
print(f"state={state2['session']['state']}  有计划={bool(state2.get('plan'))}")
show("POST /chat", *call("POST", f"/api/sessions/{sid}/chat", {"content": "激活后说一句"}))
show("POST /discuss/stream", *call("POST", f"/api/sessions/{sid}/discuss/stream", {"message": "激活后问一句"}))
status, plan = call(
    "POST",
    f"/api/sessions/{sid}/plan",
    {"steps": [{"action": "读 unit 1", "output": "词汇表"}], "reason": "先有一份计划"},
)
show("POST /plan", status, plan)

print("\n=== 五、清理这个探针会话 ===")
show("DELETE /api/sessions/{id}", *call("DELETE", f"/api/sessions/{sid}"))
