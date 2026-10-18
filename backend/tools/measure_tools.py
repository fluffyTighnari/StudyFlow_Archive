"""量一下"发给模型的工具 schema 有多大"。**不进测试，只用来量。**

起因是一次真实运行：本地 9B 模型收到 7100 token 的 prompt，
其中绝大部分不是会话状态，而是 14 个工具的声明。这个脚本把账算清楚：
谁最大、为什么大。
"""

from __future__ import annotations

import json

from app.agent.tools import tool_schemas

schemas = [schema.as_dict() for schema in tool_schemas()]
blob = json.dumps(schemas, ensure_ascii=False)
print(f"工具声明总字符数：{len(blob)}")
print()
def _size(schema: dict) -> int:
    return len(json.dumps(schema, ensure_ascii=False))


rows = sorted(((_size(s), s["function"]["name"]) for s in schemas), reverse=True)
for size, name in rows:
    print(f"{size:8d}  {name}")

print()
biggest = max(schemas, key=_size)
print(f"最大的那个：{biggest['function']['name']}")
params = biggest["function"]["parameters"]
print(f"  描述：{len(biggest['function']['description'])} 字符")
for key, value in (params.get("properties") or {}).items():
    print(f"  {key}: {len(json.dumps(value, ensure_ascii=False))} 字符")
