"""用后端自己的接口把"生效中的模型"换成一个真能跑起来的。

默认的 `qwen3.6:27b`（17 GB）在这台机器上**起不来**：llama-server 报了
`ggml_vulkan: Failed to allocate pinned memory` —— 显存不够。
本机还装了 `qwen3.5:9b`（6.6 GB，tools + thinking），它能跑。

这一步顺便证明"模型档案"这条链是通的：建一份 → 启用 → `/config` 的
`source` 从 `settings` 变成 `profile`。
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
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


print("=== 0. 现状 ===")
status, config = call("GET", "/api/llm/config")
print(f"  source={config['source']} model={config['settings']['llm_model']}")

status, profiles = call("GET", "/api/llm/profiles")
existing = [p for p in profiles.get("items", []) if p["model"] == "qwen3.5:9b"]
if existing:
    profile_id = existing[0]["profile_id"]
    print(f"=== 1. 已有这份档案：{profile_id} ===")
else:
    status, created = call(
        "POST",
        "/api/llm/profiles",
        {
            "label": "本地 · Qwen3.5 9B",
            "provider_id": "ollama",
            "model": "qwen3.5:9b",
            "num_ctx": 16384,
            "note": "离线可用 · 本机 9B（27B 那台显存不够）",
        },
    )
    print(f"=== 1. 建档案 {status} ===\n  {created}")
    profile_id = created["profile_id"]

print("\n=== 2. 启用 ===")
status, activated = call("POST", f"/api/llm/profiles/{profile_id}/activate")
print(f"  {status} active={activated.get('is_active')} label={activated.get('label')}")

print("\n=== 3. /config 现在说什么 ===")
status, config = call("GET", "/api/llm/config")
print(f"  source={config['source']}")
if config.get("profile"):
    p = config["profile"]
    print(f"  profile={p['label']} · {p['provider_id']} · {p['model']} @ {p['base_url']}")
print(f"  settings（兜底）= {config['settings']['llm_model']}")

print("\n=== 4. 探活（真的问一次）===")
status, probe = call("POST", f"/api/llm/profiles/{profile_id}/probe")
print(f"  {status} ok={probe.get('ok')}\n  {probe.get('detail')}")
