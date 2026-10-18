"""跑一次**真实的**流式讨论：真服务器、真 socket、可真断开。

```
# 确定性：一个"慢速替身"模型，不需要 Ollama
python -m tools.livesse --db var/sse.db

# 真实断开：读到第 3 帧就把连接掐掉，然后去库里看留下了什么
python -m tools.livesse --db var/sse.db --disconnect-after 3

# 真模型
python -m tools.livesse --db var/sse.db --llm ollama --model qwen3.5:9b
```

## 为什么必须是真服务器（这个文件存在的全部理由）

断线那条路**在 `TestClient` 上测不出来**：它先把整个响应在 portal 里跑完
再交给客户端，所以"提前跳出 `iter_lines()`"并不会真的断开 ——
实测库里拿到的仍然是 `stop:completed`。

而断线又恰恰是**实现里最容易骗过测试**的地方：起初的写法是在
`stream_events` 的生成器里 `except GeneratorExit` 留档，测试直接
`close()` 生成器就能过；可是真实服务器上，Starlette 的
`iterate_in_threadpool` 不带 `finally`，客户端断开时那个 `async for`
被直接放弃，同步生成器留在挂起状态 —— **留档代码根本不执行**。

所以：一个只测生成器的测试套件会全绿，而用户每刷新一次页面就丢掉一轮对话。
这个脚本就是用来让那件事**看得见**的：它用真 socket 断开，
然后从数据库里读出结论。
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class SlowFake:
    """一个**会慢慢说**的确定性模型。

    为什么要慢：断线测试需要在"回答说到一半"的时刻断开。
    一个瞬间说完的替身会让整个响应在客户端来得及断开之前就写完 ——
    于是这条测试测到的是"正常完成"，而它自己并不知道。
    """

    def __init__(self, delay: float) -> None:
        from app.agent.llm import FakeLLM, call, reply

        self.delay = delay
        self._inner = FakeLLM(
            [
                reply(
                    "我看了一下第一步。",
                    call(
                        "propose_plan_change",
                        target="ST-001",
                        user_view="一天 50 个新词太多了，我记不住",
                        suggested_change={
                            "steps": [
                                {
                                    "step_id": "ST-001",
                                    "action": "每天用 Anki 过 20 个新词",
                                    "output": "2000 词的复习记录",
                                },
                                {
                                    "step_id": "ST-002",
                                    "action": "读一篇技术文档并标注生词",
                                    "output": "标注过的文档",
                                },
                            ],
                            "change": "第一步从 50 个词减到 20 个",
                        },
                    ),
                ),
                reply("改好了。第一步现在是每天 20 个词，先按这个走一周看看。"),
            ],
            chunk_size=6,
        )

    def chat(self, messages, *, tools=()):
        return self._inner.chat(messages, tools=tools)

    def stream(self, messages, *, tools=()):
        for delta in self._inner.stream(messages, tools=tools):
            time.sleep(self.delay)
            yield delta


def _build(path: Path, *, max_steps: int):
    from app.config import Settings
    from app.main import create_app

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    return create_app(
        Settings(
            database_url=f"sqlite+pysqlite:///{path}",
            seed_on_startup=True,
            llm_provider="fake",  # 真的客户端在下面替换
            agent_max_steps=max_steps,
        )
    )


def _serve(app, port: int):
    """在后台线程里跑真 uvicorn，并等它就绪。

    `Server.started` 是 uvicorn 自己设的标志；用 `time.sleep` 猜就绪时间
    会让这个脚本偶发地连不上（而偶发失败的验证脚本最后会没人跑）。
    """
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started:
        if time.time() > deadline:  # pragma: no cover
            raise RuntimeError("uvicorn 30 秒没起来")
        time.sleep(0.05)
    return server, thread


def _setup(client, base: str) -> tuple[str, str]:
    folder = client.post(
        f"{base}/api/folders", json={"name": "英语学习"}
    ).json()["folder_id"]
    session_id = client.post(
        f"{base}/api/sessions",
        json={
            "folder_id": folder,
            "title": "学习英语单词",
            "input_goal": "能读原版技术文档",
        },
    ).json()["session_id"]
    state = client.get(f"{base}/api/sessions/{session_id}/state").json()
    client.post(
        f"{base}/api/sessions/{session_id}/transitions",
        json={"target": "active", "expected_version": state["versions"]["session"]},
    )
    state = client.get(f"{base}/api/sessions/{session_id}/state").json()
    plan = client.post(
        f"{base}/api/sessions/{session_id}/plan",
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


def _dump_chat(client, base: str, session_id: str) -> list[dict[str, Any]]:
    chat = client.get(f"{base}/api/sessions/{session_id}/state").json()["chat"]
    print("\n=== 库里留下了什么 ===")
    for row in chat:
        trace = row.get("tool_trace") or []
        print(f"  [{row['role']}] tags={row.get('tags')} {row['content'][:70]!r}")
        for entry in trace:
            print(f"        · {entry['tool']} → {entry['status']}")
    return chat


def main() -> int:
    parser = argparse.ArgumentParser(description="跑一次真实的流式讨论")
    parser.add_argument("--db", default="var/livesse.db")
    parser.add_argument("--llm", choices=("fake", "ollama"), default="fake")
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--frame-delay", type=float, default=0.35,
                        help="慢速替身每帧之间的间隔（秒）。断开测试靠它")
    parser.add_argument("--disconnect-after", type=int, default=0,
                        help="读到第 N 帧就断开。0 = 读完")
    parser.add_argument("--message", default="我试了一下，一天 50 个新词太多了，我记不住。")
    args = parser.parse_args()

    import httpx

    app = _build(Path(args.db), max_steps=args.max_steps)

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    _serve(app, port)

    # **替换客户端必须发生在服务器启动之后。**
    #
    # `lifespan` 在启动时会执行 `app.state.llm_client = make_llm_client(settings)`
    # ——启动之前注入的东西会被它覆盖掉。这不是猜测：这个脚本起初就是先注入、
    # 后启动的，表现出来是"模型在第一句话上就说脚本用完了"，而那其实是
    # `llm_provider="fake"` 造出来的**空脚本替身**在回话。
    # （`tools/livedemo.py` 之所以没这个问题，是因为它在 `with TestClient(...)`
    # 内部注入 —— 那时 lifespan 已经跑过了。）
    if args.llm == "ollama":
        from app.agent.llm import OllamaClient

        app.state.llm_client = OllamaClient(
            model=args.model, base_url=args.base_url, timeout=args.timeout
        )
        print(f"真服务器已就绪：{base}\n用真模型：{args.model} @ {args.base_url}")
    else:
        app.state.llm_client = SlowFake(args.frame_delay)
        print(f"真服务器已就绪：{base}\n用慢速替身：每帧 {args.frame_delay}s")

    with httpx.Client(timeout=args.timeout) as client:
        session_id, step_id = _setup(client, base)
        url = f"{base}/api/sessions/{session_id}/discuss/stream"
        payload = {"message": args.message, "step_id": step_id}

        print(f"POST {url}")
        print(f'body: {{"message": "{args.message[:24]}…", "step_id": "{step_id}"}}\n')
        seen = 0
        try:
            with client.stream("POST", url, json=payload) as response:
                print(f"← {response.status_code} {response.headers.get('content-type')}")
                for line in response.iter_lines():
                    if not line:
                        continue
                    seen += 1
                    print(f"  {line}")
                    if args.disconnect_after and seen >= args.disconnect_after:
                        print(
                            f"\n**在读到 {seen} 行之后主动掐断连接**"
                            "（这就是「用户刷新页面」）。\n"
                        )
                        break
        except httpx.HTTPError as exc:
            print(f"（连接在客户端侧被关掉：{type(exc).__name__}）")

        # 断开之后给服务端一点时间走完它的收尾（真实网络里这段时间是异步的）
        time.sleep(1.0)
        _dump_chat(client, base, session_id)

    if args.disconnect_after:
        print("\n=== 结论 ===")
        print("上面应当看到一条 stop:client_disconnected —— 半句话没有被存成回答，")
        print("但这次讨论留下了记录。若那一条不存在，说明断开时留档代码没执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
