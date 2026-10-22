"""列表少了一条：接口 vs 库，逐字段对照。"""

from __future__ import annotations

import json
import pathlib
import sqlite3
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
            return response.status, json.loads(response.read().decode() or "null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "null")


folders = call("GET", "/api/folders")[1]
items = folders if isinstance(folders, list) else folders["items"]
folder_id = items[0]["folder_id"]
code, created = call("POST", "/api/sessions", {"folder_id": folder_id, "title": "列表可见性"})
sid = created["session_id"]
print(f"新建 {code} {sid}")

code, listing = call("GET", "/api/sessions")
rows = listing if isinstance(listing, list) else listing["items"]
ids = [row.get("session_id") for row in rows]
print(f"列表 {code} 有 {len(rows)} 条：{ids}")
print(f"  新会话在列表里吗：{sid in ids}")

code, again = call("PATCH", f"/api/sessions/{sid}", {"title": "立刻改名"})
print(f"  紧接着 PATCH → {code} {json.dumps(again, ensure_ascii=False)[:80]}")

code, state = call("GET", f"/api/sessions/{sid}/state")
print(f"  再读 state → {code}")

conn = sqlite3.connect(pathlib.Path(__file__).resolve().parent.parent / "backend" / "var" / "behavior.db")
row = conn.execute(
    "SELECT session_id, folder_id, title, state, sort_order, archived_at FROM session WHERE session_id = ?",
    (sid,),
).fetchone()
conn.close()
print(f"  库里：{row}")
