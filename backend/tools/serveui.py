"""开发用后端服务。

**两种模式：**

```powershell
# ① 真模型（默认）。前端的讨论会真的打 Ollama。
.\\.venv\\Scripts\\python.exe -m tools.serveui --db var/ui.db --port 8123

# ② 确定性替身。每次都能把「讨论 → 改计划」走完，不看模型脸色。
.\\.venv\\Scripts\\python.exe -m tools.serveui --db var/ui.db --port 8123 --llm fake

# 加 --dist 就是一个端口跑完整应用（前端已 pnpm build）
.\\.venv\\Scripts\\python.exe -m tools.serveui --db var/ui.db --port 8123 `
    --dist ..\\frontend\\app\\dist
```

**为什么需要 ②**：真模型在这个任务上不稳定（实测 27B 只思考不回答，计划未改），
于是"管子通不通"和"模型行不行"两件事被搅在一起，断了也不知道该修哪儿。
②把模型换成确定的，剩下的差异就只剩模型。

**为什么托管静态文件这件事在这里、而不是 `app/main.py` 里**：
应用工厂不该知道前端产物在哪个目录 —— 那是部署的事。让 `create_app` 挂一个
`frontend/app/dist`，等于把"前端构建过没有"变成后端启动的一个隐含前提，
而它的失败形态是"接口全对、首页 404"。

`--dist` 不存在时**只警告、不失败**：这样纯后端开发（跑测试、调接口）
不需要先构建前端。
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def _serve_in_thread(app, *, host: str, port: int):
    """在后台线程里跑真 uvicorn，等它就绪再返回。

    必须先起服务器、**再**替换 `app.state.llm_client`：
    `lifespan` 在启动时会重建那个客户端，启动之前注入的东西会被覆盖掉。
    （这条不是猜的 —— `tools/livesse.py` 起初就是先注入后启动，
    表现出来是"模型在第一句话上就说脚本用完了"。）
    """
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="info")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started:
        if time.time() > deadline:  # pragma: no cover
            raise RuntimeError("uvicorn 30 秒没起来")
        time.sleep(0.05)
    return server


def _install_scripted(app, *, session_id: str, frame_delay: float) -> bool:
    """按**库里真实的** step_id 装一个确定性替身。"""
    from app.agent.scripted import ScriptedPlanChange
    from app.session_domain import reads as session_reads

    factory = app.state.session_factory
    with factory() as db:
        plan = session_reads.get_plan(db, session_id)
        if plan is None:
            print(f"会话 {session_id} 还没有计划 —— 确定性替身需要一份计划来改。")
            print("先跑：python -m tools.seedui --db <同一个库> --reset")
            return False
        steps = session_reads.plan_steps(db, plan.plan_id)

    if not steps:
        print(f"会话 {session_id} 的计划没有步骤。")
        return False

    target = steps[0].step_id
    app.state.llm_client = ScriptedPlanChange(
        target=target,
        steps=[
            {"step_id": step.step_id, "action": step.action, "output": step.output}
            for step in steps
        ],
        change="第 1 步拆成两天，每天一半",
        user_view="第一单元太长了，我想拆成两天",
        frame_delay=frame_delay,
    )
    print(
        f"已装确定性替身：它会把 {target}（{steps[0].action[:24]}…）拆成两天，"
        f"每帧 {frame_delay}s。"
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="var/ui.db")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--dist",
        default="../frontend/app/dist",
        help="前端构建产物目录；不存在则只跑 API",
    )
    parser.add_argument(
        "--llm",
        choices=("ollama", "fake", "bridge", "openai"),
        default="ollama",
        help="fake = 确定性替身；bridge = 交给外部回答（子代理充当模型）；"
        "openai = 商用 API（需要 MOUXUE_LLM_API_KEY）",
    )
    parser.add_argument(
        "--bridge-dir",
        default="var/bridge",
        help="bridge 模式的交换目录",
    )
    parser.add_argument(
        "--think",
        choices=("auto", "true", "false"),
        default="false",
        help="本地模型的思考开关。默认 false —— 实测 27B 在 think=false 时 5/5 次"
        "都调工具，而不发这个字段是唯一跑飞 900s 超时的一格。"
        "换成不认识 think 的模型（coder / phi3 / qwen2）时用 auto。",
    )
    parser.add_argument("--frame-delay", type=float, default=0.02,
                        help="替身每帧之间的间隔（秒），让流式看起来像真的")
    parser.add_argument("--script-session", default="S-0001",
                        help="替身改哪个会话的计划")
    parser.add_argument(
        "--model",
        default=None,
        help="覆盖 Settings.llm_model（在本地试不同模型时省得改环境变量）",
    )
    parser.add_argument(
        "--items-dir",
        default=None,
        help="题库目录。默认用仓库里的 `items/`（那是**外部信息·渐进补**，"
        "现在还不完整）—— 想验证抽题机制本身，指向一份完整的题库："
        "`--items-dir tests/fixtures/bank`（由 tools.makefixturebank 生成）",
    )
    parser.add_argument(
        "--materials-dir",
        default=None,
        help="材料目录。只在 --items-dir 也给了的时候生效；"
        "不给我会按 `--items-dir` 的同级 `materials/` 猜 —— 猜错的表现是"
        "「题库里每道题都找不到材料」，而那句话不指向真正的原因",
    )
    parser.add_argument(
        "--web-search-corpus",
        default=None,
        help="打开**联网搜索**（离线语料库版）：给它一份 JSON 语料库，"
        "`web_search` 就会出现在工具清单里。不给 = 没有这个工具"
        "（默认，也是「新克隆的仓库不联网」那条约束的落点）",
    )
    parser.add_argument(
        "--mcp",
        default=None,
        help="接一个 MCP 工具服务器：`名字=命令行`（也可用 `;` 分隔多个）。"
        "例：`--mcp \"demo=python -m tools.fake_mcp_server\"`。"
        "外挂工具一律只读、名字带 `mcp__<名字>__` 前缀 —— 见 app/external/",
    )
    args = parser.parse_args()
    if args.materials_dir is None and args.items_dir:
        args.materials_dir = str(
            pathlib.Path(args.items_dir).resolve().parent / "materials"
        )

    settings = Settings(
        database_url=f"sqlite+pysqlite:///{args.db}",
        seed_on_startup=True,
        # `bridge` 不是一家服务商（它需要一个目录参数），
        # 所以先用 `fake` 起服务，启动之后再替换 —— 与替身同一条路。
        # 顺带一句：`--llm openai` 之外，`--llm deepseek` / `moonshot` / `zhipu` 等都行
        # （公司目录见 `app/agent/providers.py`，密钥从 `.env` 或环境变量读）。
        llm_provider="fake" if args.llm in ("fake", "bridge") else args.llm,
        **({"llm_model": args.model} if args.model else {}),
        llm_think={"auto": None, "true": True, "false": False}[args.think],
        **(
            {
                "items_dir": args.items_dir,
                "materials_dir": args.materials_dir,
            }
            if args.items_dir
            else {}
        ),
        # 外挂能力（默认全关）。`--mcp` 的 `;` 分隔在命令行上比 JSON 好写，
        # 而 `load_servers` 那两种写法（JSON / `名字=命令`）都认 ——
        # 这里只把分隔符换成换行，不引入第三种格式。
        **(
            {"web_search_provider": "local", "web_search_corpus": args.web_search_corpus}
            if args.web_search_corpus
            else {}
        ),
        **({"mcp_servers": args.mcp.replace(";", "\n")} if args.mcp else {}),
    )
    app = create_app(settings)

    dist = pathlib.Path(args.dist).resolve()
    if (dist / "index.html").is_file():
        from starlette.staticfiles import StaticFiles

        # 挂在最后：路由先匹配，所以 `/api/...` 不会被这个 mount 吃掉。
        app.mount("/", StaticFiles(directory=dist, html=True), name="app")
        print(f"已托管前端产物：{dist}")
    else:
        print(f"没有前端产物（{dist} 里没有 index.html）—— 只跑 API。")

    _serve_in_thread(app, host=args.host, port=args.port)
    print(f"API 已就绪：http://{args.host}:{args.port}")

    if args.llm == "fake":
        if not _install_scripted(
            app, session_id=args.script_session, frame_delay=args.frame_delay
        ):
            return 2
    elif args.llm == "bridge":
        from app.agent.bridge import SubagentBridgeClient

        bridge = SubagentBridgeClient(args.bridge_dir)
        app.state.llm_client = bridge
        print(
            f"已装**文件桥**：每一次模型调用会写成 "
            f"{bridge.directory.resolve()}\\req-N.json，"
            "等另一端把 res-N.json 写回来。"
        )
    elif args.llm == "openai":
        if not settings.llm_api_key:
            print("openai 模式需要 MOUXUE_LLM_API_KEY（密钥只从环境变量读）。")
            return 2
        print(f"用商用 API：{settings.llm_model} @ {settings.llm_base_url}")
    else:
        print(f"用本地模型：{settings.llm_model} @ {settings.llm_base_url}")

    # 主线程等在这里（uvicorn 在 daemon 线程里）。
    threading.Event().wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
