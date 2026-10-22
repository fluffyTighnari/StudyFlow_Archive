"""手动确认四个"到底怎么回事"：输入修订、侧栏清空、重复建计划、澄清槽位。"""

from __future__ import annotations

import json
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
status, session = call("POST", "/api/sessions", {"folder_id": folder_id, "title": "四个疑点"})
sid = session["session_id"]
print(f"会话 {sid}")

print("\n=== 1. 输入修订 ===")
state = call("GET", f"/api/sessions/{sid}/state")[1]
version = state["session"]["version"]
status, body = call(
    "PATCH", f"/api/sessions/{sid}/input", {"goal": "两周读懂 props", "expected_version": version}
)
print(f"  PATCH {status} goal={(body or {}).get('goal')} version={(body or {}).get('version')}")
status, revisions = call("GET", f"/api/sessions/{sid}/input/revisions")
print(f"  GET revisions {status} → {json.dumps(revisions, ensure_ascii=False)[:200]}")

print("\n=== 2. 重复建计划 ===")
state = call("GET", f"/api/sessions/{sid}/state")[1]
code, plan = call(
    "POST",
    f"/api/sessions/{sid}/plan",
    {
        "expected_session_version": state["session"]["version"],
        "steps": [{"action": "第一步"}],
        "reason": "探针",
    },
)
print(f"  第一次 {code} version={plan.get('version') if isinstance(plan, dict) else plan}")
code, again = call(
    "POST",
    f"/api/sessions/{sid}/plan",
    {"expected_session_version": state["session"]["version"], "steps": [{"action": "第二步"}]},
)
print(f"  第二次 {code} {json.dumps(again, ensure_ascii=False)[:180]}")

print("\n=== 3. 澄清槽位 ===")
for slot in ("goal", "starting_point", "success_criteria"):
    code, body = call(
        "POST",
        f"/api/sessions/{sid}/clarifications",
        {"slot": slot, "question": f"关于 {slot} 能不能说一下？"},
    )
    print(f"  slot={slot} → {code} {json.dumps(body, ensure_ascii=False)[:150]}")

print("\n=== 4. 侧栏清空 ===")
code, said = call("POST", "/api/sidebar/discuss", {"message": "我在纠结什么？"})
print(f"  先问一句 {code}")
before = call("GET", "/api/sidebar/state")[1]
print(f"  清空前 messages={len(before.get('messages') or [])}")
cleared = call("DELETE", "/api/sidebar/messages")
print(f"  DELETE {cleared[0]} {json.dumps(cleared[1], ensure_ascii=False)[:120]}")
after = call("GET", "/api/sidebar/state")[1]
print(f"  清空后 messages={len(after.get('messages') or [])}")
print(f"  剩下的：{json.dumps((after.get('messages') or [])[:2], ensure_ascii=False)[:220]}")

call("DELETE", f"/api/sessions/{sid}")
