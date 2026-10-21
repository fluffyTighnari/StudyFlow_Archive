"""真的发一轮讨论，把 SSE 每一帧原样打印 —— 前端要照着这个写。

不做任何加工：`event:` 行 + `data:` 行都打出来，这样"我该监听哪些事件、
每个事件的 `data` 里有什么字段"就不用猜。
"""

from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8125"
URL = BASE + "/api/sessions/S-0001/discuss/stream"

body = json.dumps({"message": "这个计划我觉得第二天太满了，能拆一下吗"}, ensure_ascii=False).encode()
req = urllib.request.Request(  # noqa: S310
    URL,
    data=body,
    headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    method="POST",
)

with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
    print("HTTP", resp.status, dict(resp.headers).get("content-type"))
    for raw in resp:
        line = raw.decode("utf-8").rstrip("\n")
        if not line:
            print("---")
            continue
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            keys = sorted(payload)
            preview = json.dumps(payload, ensure_ascii=False)
            print(f"data  keys={keys}\n      {preview[:600]}")
        else:
            print(line)
