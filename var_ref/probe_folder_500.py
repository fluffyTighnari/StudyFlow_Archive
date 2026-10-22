"""复现「删掉的领域再 PATCH → 500」，并打印服务端到底在哪一行炸的。"""

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


folders = json.loads(call("GET", "/api/folders")[1])
items = folders if isinstance(folders, list) else folders["items"]
parent = items[0]["folder_id"]

print("=== 建一个领域，挂一个会话进去，再删领域 ===")
code, body = call("POST", "/api/folders", {"name": "复现 500 的领域"})
folder = json.loads(body)["folder_id"]
print(f"  建领域 {code} {folder}")

code, body = call("POST", "/api/sessions", {"folder_id": folder, "title": "里面有个会话"})
session = json.loads(body).get("session_id")
print(f"  建会话 {code} {session}")

print(f"  删会话 {call('DELETE', f'/api/sessions/{session}')[0]}")
code, body = call("DELETE", f"/api/folders/{folder}")
print(f"  删领域 {code} {body[:120]}")

code, body = call("PATCH", f"/api/folders/{folder}", {"name": "删完还想改"})
print(f"  删后 PATCH → {code} {body[:300]}")

print("\n=== 对照：没挂过会话的领域 ===")
code, body = call("POST", "/api/folders", {"name": "干净的领域"})
folder2 = json.loads(body)["folder_id"]
print(f"  删 {call('DELETE', f'/api/folders/{folder2}')[0]}")
code, body = call("PATCH", f"/api/folders/{folder2}", {"name": "也一样改"})
print(f"  删后 PATCH → {code} {body[:300]}")

print("\n=== 状态机：每一步都现读版本（界面就是这么做的）===")
code, body = call("POST", "/api/sessions", {"folder_id": parent, "title": "状态机探针 2"})
sid = json.loads(body)["session_id"]
for target in ("active", "review", "completed", "active"):
    state = json.loads(call("GET", f"/api/sessions/{sid}/state")[1])["session"]
    code, body = call(
        "POST",
        f"/api/sessions/{sid}/transitions",
        {"target": target, "expected_version": state["version"], "reason": "探针"},
    )
    print(f"  {state['state']} → {target}: {code} {body[:150]}")
