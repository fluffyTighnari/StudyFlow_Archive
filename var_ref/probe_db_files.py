"""在库文件上直接点数：新建的会话到底写进了哪个文件。"""

from __future__ import annotations

import pathlib
import sqlite3

backend = pathlib.Path(__file__).resolve().parent.parent / "backend"
for name in ("behavior.db", "ui_live.db", "mouxue.db"):
    path = backend / "var" / name
    if not path.exists():
        print(f"{name}: 不存在")
        continue
    conn = sqlite3.connect(path)
    try:
        sessions = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
        last = conn.execute("SELECT session_id, title FROM sessions ORDER BY rowid DESC LIMIT 3").fetchall()
        folders = conn.execute("SELECT count(*) FROM folders").fetchone()[0]
        profiles = conn.execute("SELECT count(*) FROM llm_profiles").fetchone()[0]
    except sqlite3.Error as exc:
        print(f"{name}: 读不了（{exc}）")
        continue
    finally:
        conn.close()
    print(f"{name}: 会话 {sessions} 个，领域 {folders} 个，模型档案 {profiles} 份")
    for row in last:
        print(f"    {row[0]} · {row[1]}")

print("\n=== 8131 上服务的库是哪个 ===")
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8131/api/sessions", timeout=20) as response:
    listing = json.loads(response.read().decode())
rows = listing if isinstance(listing, list) else listing["items"]
print(f"  接口列了 {len(rows)} 条：{[r.get('session_id') for r in rows]}")
