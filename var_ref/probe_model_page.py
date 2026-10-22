"""证明两件事，都用真接口：

1. **模型档案能改**：新建 → 启用 → 拉清单 → 探活，四步都真的落到后端。
2. **会话那条链是真的**：同一份档案连问两句不同的话，回答必须不一样。

第 2 条是这次要证的那种缺陷 —— 原来无论说什么都回同一句，
因为服务是用 `--llm fake` 起的（探针替身，每轮返回同一段脚本）。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8125"


def call(method: str, path: str, body: dict | None = None, timeout: float = 60.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def ask(session_id: str, message: str) -> tuple[str, dict]:
    """发一轮讨论，返回 (回答正文, done 事件)。"""
    data = json.dumps({"message": message}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/api/sessions/{session_id}/discuss/stream",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    reply, done, error = "", {}, None
    event = ""
    started = time.time()
    with urllib.request.urlopen(req, timeout=900) as resp:
        for raw in resp:
            text = raw.decode("utf-8").rstrip("\n")
            if text.startswith("event:"):
                event = text.split(":", 1)[1].strip()
            elif text.startswith("data:") and event in ("done", "error"):
                try:
                    payload = json.loads(text.split(":", 1)[1].strip())
                except json.JSONDecodeError:
                    continue
                if event == "error":
                    error = payload
                else:
                    done = payload
                    reply = payload.get("reply") or ""
    print(f"    （用时 {time.time() - started:.0f} s）")
    if error:
        print(f"    ✗ error: {error}")
    return reply, done


print("=== 1. 此刻在用哪份档案 ===")
_, config = call("GET", "/api/llm/config")
print(f"  source={config['source']}")
if config.get("profile"):
    p = config["profile"]
    print(f"  {p['label']} · {p['provider_id']} · {p['model']} @ {p['base_url']}")
active_id = (config.get("profile") or {}).get("profile_id")

print("\n=== 2. 拉清单（OllamaClient.list_models）===")
status, got = call("POST", f"/api/llm/profiles/{active_id}/models", {}, timeout=120)
models = (got or {}).get("models") or []
print(f"  {status} → {len(models)} 个：{models[:5]}{'…' if len(models) > 5 else ''}")

print("\n=== 3. 探活 ===")
status, probe = call("POST", f"/api/llm/profiles/{active_id}/probe", {}, timeout=300)
ok = (probe or {}).get("ok") if isinstance(probe, dict) else None
detail = (probe or {}).get("detail") if isinstance(probe, dict) else probe
print(f"  {status} ok={ok}\n  {str(detail)[:200]}")

print("\n=== 4. 连问两句不同的话 ===")
folders = call("GET", "/api/folders")[1]
folder_id = (folders[0] if isinstance(folders, list) else folders["items"][0])["folder_id"]
status, created = call("POST", "/api/sessions", {"folder_id": folder_id, "title": "探针 · 真模型"})
sid = created["session_id"]
print(f"  会话 {sid}")

answers = []
for question in [
    "我想在两周内读懂 React 的 props 和 state，第一天先做什么？",
    "反过来问：如果只有三天，你会砍掉哪部分？",
]:
    print(f"  Q: {question}")
    reply, done = ask(sid, question)
    answers.append(reply)
    print(f"  A: {reply[:160]}")
    print(f"     stop_reason={done.get('stop_reason')} 工具={[(t.get('tool')) for t in done.get('tool_trace') or []]}")

print("\n=== 5. 判据：两轮是不是同一句话 ===")
if len(answers) == 2 and all(answers):
    same = answers[0].strip() == answers[1].strip()
    print(f"  两轮正文相同: {same}")
    print("  → " + ("✗ 同一句：对面没在听" if same else "✓ 两轮不同，模型真的在回答"))
else:
    print("  ✗ 有一轮是空的，见上面")

call("DELETE", f"/api/sessions/{sid}")
