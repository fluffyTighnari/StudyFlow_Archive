"""打印 OpenAPI 里我要用的请求模型，省得一个个猜字段名。"""

from __future__ import annotations

import json

from app.main import create_app

spec = create_app().openapi()
schemas = spec["components"]["schemas"]

WANT = (
    "attach",
    "sediment",
    "summary",
    "reflect",
    "annotat",
    "clarif",
    "note",
    "stage",
    "milestone",
    "checkpoint",
    "snapshot",
    "motivation",
    "input",
    "transition",
    "plancreate",
    "planrevision",
    "stepmark",
    "folder",
    "session",
    "profilewrite",
)


def short(value: object) -> str:
    if not isinstance(value, dict):
        return str(value)
    if "type" in value:
        return str(value["type"])
    if "anyOf" in value:
        return "|".join(short(v) for v in value["anyOf"])[:40]
    return "?"


for name in sorted(schemas):
    if not any(word in name.lower() for word in WANT):
        continue
    schema = schemas[name]
    required = schema.get("required", [])
    props = ", ".join(
        f"{key}:{short(val)}" for key, val in list(schema.get("properties", {}).items())[:9]
    )
    print(f"{name}\n  required={required}\n  {props}")

# 顺便把我要打的那几个端点的路径参数/请求体也列出来
for path in (
    "/api/sessions/{session_id}/attachments",
    "/api/sessions/{session_id}/sediment",
    "/api/sessions/{session_id}/summary",
    "/api/sessions/{session_id}/reflections",
    "/api/sessions/{session_id}/annotations",
    "/api/sessions/{session_id}/clarifications",
    "/api/sessions/{session_id}/plan",
    "/api/sessions/{session_id}/stage",
    "/api/sessions/{session_id}/milestones",
):
    for method, op in spec["paths"].get(path, {}).items():
        body = (op.get("requestBody") or {}).get("content", {})
        ref = ""
        for _ctype, meta in body.items():
            ref = json.dumps(meta.get("schema", {}), ensure_ascii=False)[:80]
        print(f"\n{method.upper()} {path}\n  body={ref or '(无)'}\n  responses={list(op.get('responses', {}))}")
