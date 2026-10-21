"""列出后端真实的路由表（从运行中的 `/openapi.json` 取），按前缀分组。

写前端时最怕的不是"接口不存在"，是**记错形状**：路径对了、方法错了，或者
参数名是 `new_state` 而你以为叫 `state`。所以这份清单直接从 OpenAPI 读，
不靠记忆。
"""

from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8125"

with urllib.request.urlopen(BASE + "/openapi.json", timeout=20) as resp:  # noqa: S310
    spec = json.loads(resp.read().decode("utf-8"))

groups: dict[str, list[tuple[str, str]]] = {}
for path, ops in spec["paths"].items():
    for method in ops:
        if method not in {"get", "post", "patch", "delete", "put"}:
            continue
        prefix = "/".join(path.split("/")[:3])
        groups.setdefault(prefix, []).append((method.upper(), path))

for prefix in sorted(groups):
    print(f"\n## {prefix}")
    for method, path in sorted(groups[prefix], key=lambda item: item[1]):
        print(f"  {method:6} {path}")
print(f"\n共 {sum(len(v) for v in groups.values())} 条")
