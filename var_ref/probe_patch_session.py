"""同一个会话：有些端点认得它，有些说它不存在 —— 到底差在哪。"""

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
folder_id = items[0]["folder_id"]
code, body = call("POST", "/api/sessions", {"folder_id": folder_id, "title": "端点探针"})
sid = json.loads(body)["session_id"]
print(f"新会话 {code} {sid}")

checks = [
    ("GET", f"/api/sessions/{sid}/state", None),
    ("GET", f"/api/sessions/{sid}/plan", None),
    ("POST", f"/api/sessions/{sid}/notes", {"content": "一条笔记"}),
    ("PATCH", f"/api/sessions/{sid}/input", {"goal": "目标 A", "expected_version": 1}),
    ("PATCH", f"/api/sessions/{sid}", {"title": "改个名"}),
    ("PATCH", f"/api/sessions/{sid}", {"archived": True}),
    ("GET", f"/api/sessions/{sid}/state", None),
]
for method, path, payload in checks:
    code, text = call(method, path, payload)
    print(f"  {method:6} {path:42} → {code} {text[:90]}")

print("\n=== 对照：预置会话 S-0001 能改名吗 ===")
print(f"  PATCH S-0001 → {call('PATCH', '/api/sessions/S-0001', {'title': '预置会话改名'})[0:2]}")

print("\n=== 列表里这个会话在哪 ===")
listing = json.loads(call("GET", "/api/sessions")[1])
rows = listing if isinstance(listing, list) else listing["items"]
print(f"  共 {len(rows)} 条；本轮的 {sid} 在里面吗：{sid in [r.get('session_id') for r in rows]}")
