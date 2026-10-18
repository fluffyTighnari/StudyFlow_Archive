"""跑**一次真实的讨论**，把过程原样打出来。

这不是测试（测试用确定性替身，`tests/api/test_discuss_api.py`），
而是一个用来"亲眼看一次"的入口：

```
python -m tools.livedemo --model qwen3.6:27b
```

它做四件事：
1. 在一个**文件库**里铺一个会话（文件夹 → 会话 → 计划）；
2. 把应用的 LLM 客户端换成真的 `OllamaClient`；
3. POST 一次 `/discuss`，把 `step_id` 带上（等同"用户在界面上点了第一步"）；
4. 打出回复、停止理由、**每一步的工具调用**，以及讨论前后的计划。

## 为什么要有这个文件

因为"它在真实模型下能跑"和"它在测试里能跑"是两件不同的事：
测试证明的是**每条链路都能在没有模型的情况下被验证**；
这个脚本回答的是另一个问题 —— 那 14 个工具的说明、参数 schema
和拒绝话术，一个真的模型读得懂吗？它会一次就调对，还是先撞两次墙？

**撞墙不是失败。** 拒绝之后它能改对，恰恰是这个设计要的效果。
所以脚本把轨迹全部打出来，让人自己看。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _build(path: Path, *, max_steps: int):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    app = create_app(
        Settings(
            database_url=f"sqlite+pysqlite:///{path}",
            seed_on_startup=True,
            # 先建成 fake；真的客户端在 `_install_real_client` 里替换掉。
            llm_provider="fake",
            agent_max_steps=max_steps,
        )
    )
    return app, TestClient(app)


def _setup(client) -> tuple[str, str]:
    folder = client.post("/api/folders", json={"name": "英语学习"}).json()["folder_id"]
    session_id = client.post(
        "/api/sessions",
        json={
            "folder_id": folder,
            "title": "学习英语单词",
            "input_goal": "能读原版技术文档",
        },
    ).json()["session_id"]
    state = client.get(f"/api/sessions/{session_id}/state").json()
    client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "active", "expected_version": state["versions"]["session"]},
    )
    state = client.get(f"/api/sessions/{session_id}/state").json()
    plan = client.post(
        f"/api/sessions/{session_id}/plan",
        json={
            "expected_session_version": state["versions"]["session"],
            "goal_derived": "先把技术文档里的高频词过一遍",
            "steps": [
                {"action": "每天用 Anki 过 50 个新词", "output": "2000 词的复习记录"},
                {"action": "读一篇技术文档并标注生词", "output": "标注过的文档"},
            ],
            "checkpoints": [
                {
                    "at_step": "ST-002",
                    "check_method": "随机抽 20 个词自测",
                    "pass_criteria": "答对 16 个以上",
                    "fail_action": "回到上一批词重刷三天",
                }
            ],
        },
    )
    assert plan.status_code == 201, plan.text
    return session_id, plan.json()["steps"][0]["step_id"]


def main() -> int:
    parser = argparse.ArgumentParser(description="跑一次真实模型的讨论")
    parser.add_argument("--model", default="qwen3.6:27b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--db", default="var/livedemo.db")
    parser.add_argument("--message", default="我试了一下，一天 50 个新词太多了，我记不住。")
    args = parser.parse_args()

    from app.agent.llm import OllamaClient

    app, holder = _build(Path(args.db), max_steps=args.max_steps)
    with holder as client:
        session_id, step_id = _setup(client)
        # **不改生产代码**，只换 `app.state.llm_client` —— 与测试用的是同一个注入点。
        # 如果这里需要改生产代码才能接上真模型，那就说明注入点设计错了。
        app.state.llm_client = OllamaClient(
            model=args.model, base_url=args.base_url, timeout=args.timeout
        )

        print(f"模型：{args.model}（{args.base_url}）")
        print(f"库：{args.db}  会话：{session_id}  步骤：{step_id}  步数上限：{args.max_steps}")
        print(f"用户说：{args.message}")
        print("-" * 72)

        before = client.get(f"/api/sessions/{session_id}/plan").json()
        response = client.post(
            f"/api/sessions/{session_id}/discuss",
            json={"message": args.message, "step_id": step_id},
        )
        print(f"HTTP {response.status_code}")
        if response.status_code != 200:
            print(response.text)
            return 1

        body = response.json()
        for item in body["tool_trace"]:
            mark = {"success": "OK", "empty": "--", "rejected": "XX", "error": "!!"}
            print(f"  [{mark.get(item['status'], '??')}] {item['tool']}  {item['duration_ms']}ms")
            print(f"       参数 {json.dumps(item['arguments'], ensure_ascii=False)[:200]}")
            print(f"       结果 {item['message'][:200]}")
        print("-" * 72)
        print(f"stop_reason = {body['stop_reason']}   steps = {body['steps']}")
        print(f"llm_meta = {json.dumps(body['llm_meta'], ensure_ascii=False)}")
        print("-" * 72)
        print("回复：")
        print(body["reply"])
        print("-" * 72)

        after = client.get(f"/api/sessions/{session_id}/plan").json()
        print(f"计划版本 {before['version']} → {after['version']}")
        for step in after["steps"]:
            print(f"  {step['step_id']} [{step['status']}] {step['action']}")
        router_logs = client.get("/api/router-logs").json()
        print(f"全局写入（router_log 条数）：{len(router_logs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
