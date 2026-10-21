"""把 `done` 帧里的 `context`（上下文用量表）与 `llm_meta` 原样打印。

界面右侧那栏「上下文 · 它看见的」要按这份数据画：分段、每节的条数与排除数、
以及 `limit` 拿不到时怎么显示「没测到」。
"""

from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8125"
req = urllib.request.Request(  # noqa: S310
    BASE + "/api/sessions/S-0001/discuss/stream",
    data=json.dumps({"message": "随便聊两句，我想看看上下文用量"}, ensure_ascii=False).encode(),
    headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    method="POST",
)

with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310
    for raw in resp:
        line = raw.decode("utf-8").strip()
        if not line.startswith("data: "):
            continue
        payload = json.loads(line[6:])
        if "context" in payload:
            print("=== context ===")
            print(json.dumps(payload["context"], ensure_ascii=False, indent=2)[:2500])
            print("=== llm_meta ===")
            print(json.dumps(payload.get("llm_meta"), ensure_ascii=False, indent=2)[:800])
            print("=== thinking 前 200 字 ===")
            print((payload.get("thinking") or "(空)")[:200])
            break
