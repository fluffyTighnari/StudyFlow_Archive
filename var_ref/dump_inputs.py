"""把行为清单里要打的那几个请求体，字段名一次问清楚（省得靠 422 反推）。"""

from __future__ import annotations

from app.main import create_app

spec = create_app().openapi()
schemas = spec["components"]["schemas"]

WANT = (
    "Chat",
    "Plan",
    "Step",
    "Input",
    "Transition",
    "Annotation",
    "Sediment",
    "Reflection",
    "Note",
    "Summary",
    "Clarification",
    "Stage",
    "Milestone",
    "Checkpoint",
    "Snapshot",
    "Motivation",
    "Session",
    "Folder",
    "Sidebar",
)

out: list[str] = []
for name in sorted(schemas):
    if not any(word in name for word in WANT):
        continue
    schema = schemas[name]
    #: 只看入参（read 结尾的是响应，跳过）
    if name.endswith("Read") or name.endswith("Item") or name.endswith("Ref"):
        continue
    required = schema.get("required", [])
    props = {}
    for key, value in schema.get("properties", {}).items():
        if isinstance(value, dict):
            if "anyOf" in value:
                props[key] = "|".join(str(v.get("type") or v.get("$ref", "?")) for v in value["anyOf"])[:36]
            else:
                props[key] = value.get("type") or value.get("$ref", "?")
        else:
            props[key] = "?"
    out.append(f"{name}\n  required={required}\n  props={props}")

print("\n".join(out))
