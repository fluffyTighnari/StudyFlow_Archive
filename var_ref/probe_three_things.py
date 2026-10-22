"""再确认三件小事：PATCH 会话标题、死端口档案建得成吗、PATCH 步骤返回什么。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8131"


def call(method: str, path: str, body: dict | None = None, *, timeout: float = 30.0):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode() or "null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "null")


folders = call("GET", "/api/folders")[1]
items = folders if isinstance(folders, list) else folders["items"]
folder_id = items[0]["folder_id"]

print("=== 1. PATCH 会话标题 ===")
code, session = call("POST", "/api/sessions", {"folder_id": folder_id, "title": "标题探针"})
sid = session["session_id"]
print(f"  建 {code} {sid}")
print(f"  PATCH → {call('PATCH', f'/api/sessions/{sid}', {'title': '改过'})}")
print(f"  再读 {call('GET', f'/api/sessions/{sid}/state')[1]['session']['title']}")

print("\n=== 2. 死端口档案 ===")
code, created = call(
    "POST",
    "/api/llm/profiles",
    {
        "label": "死端口探针",
        "provider_id": "ollama",
        "model": "qwen3.5:9b",
        "base_url": "http://127.0.0.1:9",
    },
)
print(f"  建 {code} {json.dumps(created, ensure_ascii=False)[:160]}")
profile = (created or {}).get("profile_id")
if profile:
    print(f"  列表里有吗：{profile in [p['profile_id'] for p in call('GET', '/api/llm/profiles')[1]['items']]}")
    code, probe = call("POST", f"/api/llm/profiles/{profile}/probe", {}, timeout=120)
    print(f"  探活 {code} {json.dumps(probe, ensure_ascii=False)[:200]}")
    call("DELETE", f"/api/llm/profiles/{profile}")

print("\n=== 3. PATCH 步骤返回什么 ===")
state = call("GET", f"/api/sessions/{sid}/state")[1]
print(f"  先推到 active：{call('POST', f'/api/sessions/{sid}/transitions', {'target': 'active', 'expected_version': state['session']['version'], 'reason': '探针'})[0]}")
code, plan = call(
    "POST",
    f"/api/sessions/{sid}/plan",
    {"expected_session_version": call("GET", f"/api/sessions/{sid}/state")[1]["session"]["version"], "steps": [{"action": "第一步"}], "reason": "探针"},
)
print(f"  建计划 {code} {json.dumps(plan, ensure_ascii=False)[:160]}")
step_id = (plan.get("steps") or [{}])[0].get("step_id")
code, marked = call(
    "PATCH",
    f"/api/sessions/{sid}/plan/steps/{step_id}",
    {"status": "done", "expected_version": plan.get("version")},
)
print(f"  PATCH 步骤 {code} {json.dumps(marked, ensure_ascii=False)[:220]}")

call("DELETE", f"/api/sessions/{sid}")
