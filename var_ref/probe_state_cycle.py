"""把状态走满一圈：draft → active → review → completed → active。

验的是"想说什么就说什么、想改什么就改什么"的边界到底在哪：

| 动作 | 归档后 | 为什么 |
| --- | --- | --- |
| 说话 `/chat` | 允许 | append-only，不涨版本 |
| 记笔记 `/notes` | 允许 | 同上 |
| 改定义区 `/input` | **拒绝** | 归档之后再改，「当时为什么这么总结」就核对不了 |
| 改计划 `/plan`（revise） | **拒绝** | 同上 |
| 回到执行 | 允许 | 状态与时间戳在任何一跳上都自洽 |
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8125"


def call(method: str, path: str, body: dict | None = None):
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


def show(label: str, status: int, payload) -> None:
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error") or payload
    else:
        detail = payload
    print(f"  {label:<34} {status}  {str(detail)[:120]}")


folders = call("GET", "/api/folders")[1]
folder_id = folders[0]["folder_id"] if isinstance(folders, list) else folders["items"][0]["folder_id"]
_, created = call("POST", "/api/sessions", {"folder_id": folder_id, "title": "探针 · 状态一圈"})
sid = created["session_id"]
print(f"会话 {sid}  state={created['state']}")

print("\n=== 走一圈 ===")
status, state = call("GET", f"/api/sessions/{sid}/state")
for target in ("active", "review", "completed"):
    version = state["session"]["version"]
    status, payload = call(
        "POST", f"/api/sessions/{sid}/transitions", {"target": target, "expected_version": version}
    )
    show(f"→ {target}", status, payload)
    state = call("GET", f"/api/sessions/{sid}/state")[1]

print("\n=== 归档之后 ===")
show("POST /chat", *call("POST", f"/api/sessions/{sid}/chat", {"content": "归档后再说一句"}))
show("POST /notes", *call("POST", f"/api/sessions/{sid}/notes", {"content": "归档后的笔记"}))
show(
    "PATCH /input（应拒）",
    *call(
        "PATCH",
        f"/api/sessions/{sid}/input",
        {"goal": "改成别的", "expected_version": state["input"]["version"]},
    ),
)
show(
    "POST /plan（应拒）",
    *call(
        "POST",
        f"/api/sessions/{sid}/plan",
        {
            "steps": [{"action": "a", "output": "b"}],
            "reason": "归档后改计划",
            "expected_session_version": state["session"]["version"],
        },
    ),
)
print("\n=== 回到执行 ===")
show(
    "→ active",
    *call(
        "POST",
        f"/api/sessions/{sid}/transitions",
        {"target": "active", "expected_version": call("GET", f"/api/sessions/{sid}/state")[1]["session"]["version"]},
    ),
)
print("\n=== 清理 ===")
show("DELETE", *call("DELETE", f"/api/sessions/{sid}"))
