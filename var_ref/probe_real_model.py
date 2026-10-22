"""真模型探针：同一句话问两遍不同的问题，回答必须**不一样**。

这是这次要证的那种缺陷：句子相同就说明对面根本没在听。
所以判据不是"有没有回答"，而是"两轮回答是不是同一句话"。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8125"


def call(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


print("=== 1. 此刻实际在用的是什么 ===")
status, config = call("GET", "/api/llm/config")
print(f"  GET /api/llm/config {status}")
print(f"  source={config.get('source')}")
if config.get("profile"):
    p = config["profile"]
    print(f"  profile: {p['label']} · {p['provider_id']} · {p['model']} @ {p['base_url']}")
print(f"  settings: {config.get('settings')}")
print(f"  .env 读到了吗: {config.get('env_file_read')}")

print("\n=== 2. 后端能不能真的问到本地模型 ===")
status, providers = call("GET", "/api/llm/providers")
print(f"  GET /api/llm/providers {status}，{len(providers.get('items', []))} 个服务商")

folders = call("GET", "/api/folders")[1]
folder_id = folders[0]["folder_id"] if isinstance(folders, list) else folders["items"][0]["folder_id"]
status, created = call(
    "POST", "/api/sessions", {"folder_id": folder_id, "title": "探针 · 真模型"}
)
sid = created["session_id"]
print(f"  新会话 {sid}（state={created['state']}）")

questions = [
    "我想在两周内读懂 React 的 props 和 state，你觉得第一天先做什么？",
    "接着说：第二天做什么？",
]
answers: list[str] = []
for question in questions:
    data = json.dumps({"message": question}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/api/sessions/{sid}/discuss/stream",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    reply = ""
    error = None
    done = None
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            name = ""
            for raw in resp:
                text = raw.decode("utf-8").rstrip("\n")
                if text.startswith("event:"):
                    name = text.split(":", 1)[1].strip()
                elif text.startswith("data:") and name in ("done", "error"):
                    try:
                        payload = json.loads(text.split(":", 1)[1].strip())
                    except json.JSONDecodeError:
                        continue
                    if name == "error":
                        error = payload
                    else:
                        done = payload
                        reply = payload.get("reply") or ""
    except urllib.error.HTTPError as exc:
        error = {"http": exc.code, "body": exc.read().decode("utf-8")[:200]}
    answers.append(reply)
    tools = [t.get("tool") for t in (done or {}).get("tool_trace") or []]
    print(f"\n  Q: {question}")
    if error:
        print(f"  ✗ error: {error}")
    else:
        print(f"  A: {reply[:220]}")
        print(f"  停止原因={done.get('stop_reason')} 工具={tools}")

print("\n=== 3. 两轮回答是同一句吗 ===")
if len(answers) == 2 and answers[0] and answers[1]:
    same = answers[0].strip() == answers[1].strip()
    print(f"  相同: {same}")
    print("  → " + ("✗ 又是同一句：对面没在听" if same else "✓ 两轮不同，模型真的在回答"))
else:
    print("  ✗ 有一轮没拿到回答，见上面的错误")

print("\n=== 4. 清理 ===")
print(f"  DELETE {call('DELETE', f'/api/sessions/{sid}')[0]}")
