"""打印 `/api/sessions/{id}/state` 的真实形状 —— 只读，用来对着写前端映射。

为什么用脚本而不是 curl：这份响应里有中文，PowerShell 控制台会把 UTF-8 解成乱码，
而我要看的是**字段名与嵌套**，顺便把每个数组的第一项打印出来当样本。
"""

from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8125"


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def shape(value: object, depth: int = 0) -> str:
    pad = "  " * depth
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}{key}: {type(item).__name__}")
                lines.append(shape(item, depth + 1))
            else:
                lines.append(f"{pad}{key}: {type(item).__name__} = {item!r}"[:160])
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return f"{pad}(空数组)"
        lines = [f"{pad}[{len(value)} 项]"]
        first = value[0]
        if isinstance(first, dict):
            lines.append(f"{pad}样本 0 的键：{sorted(first)}")
            lines.append(shape(first, depth + 1))
        else:
            lines.append(f"{pad}样本 0 = {first!r}"[:200])
        return "\n".join(lines)
    return f"{pad}{value!r}"[:160]


data = get("/api/sessions/S-0001/state")
print("=== 顶层键 ===")
print(sorted(data))
print()
for key in data:
    print(f"--- {key} ---")
    print(shape(data[key]))
    print()
