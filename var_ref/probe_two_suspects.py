"""单独复现两个疑点：删掉的领域再 PATCH 会 500？状态机那条边为什么 409？"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8131"


def call(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


print("=== 1. 删掉的领域再 PATCH ===")
status, body = call("POST", "/api/folders", {"name": "临时领域 A"})
folder = json.loads(body)["folder_id"]
print(f"  建 {status} {folder}")
print(f"  删 {call('DELETE', f'/api/folders/{folder}')[0]}")
code, text = call("PATCH", f"/api/folders/{folder}", {"name": "改不存在的"})
print(f"  删后 PATCH → {code} {text[:200]}")

print("\n=== 2. 状态机：draft → active → review ===")
folders = json.loads(call("GET", "/api/folders")[1])
folder_id = (folders[0] if isinstance(folders, list) else folders["items"][0])["folder_id"]
status, body = call("POST", "/api/sessions", {"folder_id": folder_id, "title": "状态机探针"})
session = json.loads(body)["session_id"]
print(f"  新会话 {status} {session}")

status, body = call("GET", f"/api/sessions/{session}/state")
version = json.loads(body)["session"]["version"]
print(f"  版本 {version}")

for target in ("active", "review"):
    code, text = call(
        "POST",
        f"/api/sessions/{session}/transitions",
        {"target": target, "expected_version": version, "reason": "探针"},
    )
    print(f"  → {target}: {code} {text[:160]}")
    if code == 200:
        version = json.loads(text).get("version", version)

print("\n=== 3. 计划创建（用 action 字段）===")
code, text = call(
    "POST",
    f"/api/sessions/{session}/plan",
    {
        "expected_session_version": version,
        "steps": [{"action": "读懂 props 的传递"}, {"action": "读懂 state 与重渲染"}],
        "reason": "探针用",
    },
)
print(f"  {code} {text[:220]}")
