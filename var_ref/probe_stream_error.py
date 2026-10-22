"""把 discuss/stream 里那条 `error` 事件的内容打出来。

探针只收集了 event 名，于是"200 + error"看起来像"没回答"，而真正的原因
（哪个异常、哪一行）一个字都没留下。这里直接把 error 的 data 原样打印。
"""

from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8125"


def call(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else None


folders = call("GET", "/api/folders")
folder_id = folders[0]["folder_id"] if isinstance(folders, list) else folders["items"][0]["folder_id"]
created = call("POST", "/api/sessions", {"folder_id": folder_id, "title": "探针 · 错误定位"})
sid = created["session_id"]
print("session:", sid, "state:", created["state"])

data = json.dumps({"message": "我想读懂 React 文档第一章，帮我理一下。"}).encode("utf-8")
req = urllib.request.Request(
    f"{BASE}/api/sessions/{sid}/discuss/stream",
    data=data,
    method="POST",
    headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
)
with urllib.request.urlopen(req) as resp:
    for raw in resp:
        text = raw.decode("utf-8").rstrip("\n")
        if text.startswith("data:"):
            print(text)

print("\n--- 清理 ---")
call("DELETE", f"/api/sessions/{sid}")
