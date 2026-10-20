"""应用配置。

刻意保持**极小**：这个项目是单人自用的，不需要 pydantic-settings + .env 加载链。
需要的值都从环境变量读，默认值面向本地开发。

**默认值本身是一处设计**：`llm_provider` 默认 `ollama`（真实模型）而不是 `fake`
（确定性替身）。替身只是为了让测试不依赖外部服务；如果它是默认值，
那么「忘了配模型」会表现成「模型答得很好」—— 这是最坏的一类失败。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from app.db import default_database_url

#: 仓库根目录。**用文件位置推，不用 cwd** ——
#: 服务可能从仓库根启动，也可能从 `backend/` 启动，而
#: "题库在哪"不该取决于这件事（`../items` 在两种启动方式下指向不同地方，
#: 而症状是"题库突然空了"，不是一句报错）。
REPO_ROOT = Path(__file__).resolve().parents[2]


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _think_flag(raw: str | None) -> bool | None:
    """`MOUXUE_LLM_THINK` 的三种状态：不设/`auto` → `None`（不发字段）。

    三态而不是 `bool`：Ollama 上有**两类**模型，
    认识 `think` 的（27B）、不认识并且会因为多一个字段而 400 的
    （coder / phi3 / qwen2）。二态开关总有一类被默认值坑到。
    """
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"", "auto", "none", "default"}:
        return None
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"看不懂的 MOUXUE_LLM_THINK：{raw!r}。可选：auto（不发字段）、true、false。"
    )


@dataclass(frozen=True)
class Settings:
    #: 种子数据是否在启动时加载。**默认开** —— 11 个 M1 变量与 16 条 M2 策略
    #: 是「目录数据」（无人可写），没有它们 `/api/m1/variables` 只会返回空列表。
    database_url: str = field(default_factory=default_database_url)
    seed_on_startup: bool = True
    #: 写过全局状态的请求，在提交前是否跑一遍不变式自检（M0 §4.1 第四重保障）。
    #:
    #: **默认开，而且不建议关掉**：关掉之后，一次绕过 Router 的幽灵写入会安静落库，
    #: 而它唯一的暴露时机就是「下次有人手动跑自检」—— 那可能是几周以后。
    #: 保留这个开关是为了能在排查期临时定位「是不是自检本身在报错」。
    check_invariants: bool = True
    #: 前端延后，但 Vite dev server 会跑在另一个端口上，因此默认允许本地来源。
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )

    #: 讨论（Agent）用哪个 LLM 客户端。
    #:
    #: - `openai` = **商用 API**（OpenAI 兼容：DeepSeek / 通义 / Moonshot …）
    #: - `ollama` = 本地模型（离线可用，不需要密钥，但实测能力不足以驱动闭环）
    #: - `fake`   = 确定性替身（脚本为空，每次调用都报错 —— 测试与演示用）
    #:
    #: **默认仍是 `ollama`**：它不需要密钥就能跑起来，而"配好 API key 再用"
    #: 是一件必须由人显式做的事。把默认指向商用服务会让新克隆的仓库
    #: 在没有任何配置的情况下**尝试联网**，那不是默认值该有的行为。
    llm_provider: str = "ollama"
    llm_model: str = "qwen3.6:27b"
    llm_base_url: str = "http://127.0.0.1:11434"
    #: 商用 API 的密钥。**只从环境变量读**（`MOUXUE_LLM_API_KEY`），
    #: 不落盘、不进日志、不进 trace —— 它会出现在给别人看的界面上。
    llm_api_key: str = ""
    #: 单次调用的超时（秒）。给得宽是因为本地 27B 模型的**第一次**调用
    #: 要先把它读进显存；超时太短会表现成"模型不会用工具"，而原因只是加载慢。
    llm_timeout: float = 300.0
    #: 上下文窗口（token）。**必须显式给。**
    #:
    #: 不给的话 Ollama 按当前显存自己算一个，而它算出来的可能远小于我们发的 prompt：
    #: 一次真实运行里它给了 4096，于是 7000 token 的请求（14 个工具声明约 1.7 万字符）
    #: 被截到 4095、只剩 1 个 token 可生成。表现出来是"模型答了个空话"。
    #: 默认 16384：够装下工具声明 + 会话状态，还要留出回答的余量。
    llm_num_ctx: int = 16384
    #: 思考开关：`None` = **不发 `think` 字段**（对任何模型都安全）。
    #:
    #: 实测（`backend/var/model_trials.md`）：`qwen3.6:27b` 显式 `think=false`
    #: 时 5/5 次都产出工具调用，而**不发这个字段**是唯一跑飞 900 秒超时的一格。
    #: 但 `qwen2.5-coder` / `phi3` / `qwen2` 这类模型收到它会 HTTP 400 ——
    #: 所以默认不发，本地演示由 `tools/serveui.py --think false` 显式打开。
    llm_think: bool | None = None
    #: Agent 循环的步数上限（缺口 C6：循环必须有预算，否则一次跑飞要人工杀进程）。
    agent_max_steps: int = 8

    #: 题库目录（Benchmark v0.3 §6.1）。
    #:
    #: ★ **为什么它进 Settings 而不是写死在抽题代码里**：
    #: 题池是**外部信息·渐进补**的一部分 —— 它会从 7 道长到几百道，
    #: 而"现在只有 7 道"与"抽题机制坏了"必须能分开。
    #: 目录可换之后，验证抽题机制可以用一份**完整的小题库**，
    #: 而不必等真的题池写完 —— 否则"抽题做完了没有"就永远只能靠人转述。
    items_dir: str = field(default_factory=lambda: str(REPO_ROOT / "items"))
    materials_dir: str = field(default_factory=lambda: str(REPO_ROOT / "materials"))

    # ------------------------------------------------------------------ #
    # 外挂能力（MCP / 联网搜索）。**默认全关。**
    #
    # 与 `llm_provider` 默认 `ollama` 同源的理由：默认值不该让一个新克隆的
    # 仓库**悄悄联网**出去，也不该去起一个别人没让他起的子进程。
    # 打开它们必须是一件由人显式做的事（`MOUXUE_WEB_SEARCH_PROVIDER` 等）。
    # ------------------------------------------------------------------ #
    #: `off` | `local` | `http`。`off` 时模型**看不到** `web_search` 这个工具。
    web_search_provider: str = "off"
    #: `local` 用的语料库（离线、确定性；端到端与测试用它）。
    web_search_corpus: str = ""
    #: `http` 用的端点：`POST {query, limit}` → `{results: [{title, url, snippet}]}`。
    web_search_endpoint: str = ""
    web_search_api_key: str = ""
    #: MCP 服务器。JSON 数组 `[{"name","command","cwd?"}]`，
    #: 或一行一条的 `名字=命令行`（手写配置时更省事）。空 = 不接任何服务器。
    mcp_servers: str = ""
    #: 单次 MCP 调用的超时（秒）。
    mcp_timeout: float = 20.0

    @classmethod
    def from_env(cls) -> Settings:
        # **先读 `.env`，再读环境变量** —— 且环境变量优先（`load_env` 保证）。
        # 放在这里而不是某个入口脚本里：任何入口（uvicorn、serveui、测试）
        # 只要用 `from_env()` 就自动一致。`MOUXUE_SKIP_ENV_FILE=1` 可以关掉它
        # （让测试与 CI 不受开发者本机 `.env` 影响）。
        env_loaded: dict[str, str] = {}
        if os.environ.get("MOUXUE_SKIP_ENV_FILE", "").strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            from app.env_file import load_env

            env_loaded = load_env()
        globals()["_ENV_FILE_KEYS_LOADED"] = sorted(env_loaded)

        origins = os.environ.get("MOUXUE_CORS_ORIGINS")
        return cls(
            database_url=default_database_url(),
            seed_on_startup=_env_flag("MOUXUE_SEED_ON_STARTUP", True),
            check_invariants=_env_flag("MOUXUE_CHECK_INVARIANTS", True),
            cors_origins=(
                tuple(o.strip() for o in origins.split(",") if o.strip())
                if origins
                else cls.cors_origins
            ),
            llm_provider=os.environ.get("MOUXUE_LLM_PROVIDER", cls.llm_provider),
            llm_model=os.environ.get("MOUXUE_LLM_MODEL", cls.llm_model),
            llm_base_url=os.environ.get("MOUXUE_LLM_BASE_URL", cls.llm_base_url),
            llm_api_key=os.environ.get("MOUXUE_LLM_API_KEY", cls.llm_api_key),
            llm_timeout=float(os.environ.get("MOUXUE_LLM_TIMEOUT", cls.llm_timeout)),
            llm_num_ctx=int(os.environ.get("MOUXUE_LLM_NUM_CTX", cls.llm_num_ctx)),
            llm_think=_think_flag(os.environ.get("MOUXUE_LLM_THINK")),
            agent_max_steps=int(
                os.environ.get("MOUXUE_AGENT_MAX_STEPS", cls.agent_max_steps)
            ),
            items_dir=os.environ.get(
                "MOUXUE_ITEMS_DIR", str(REPO_ROOT / "items")
            ),
            materials_dir=os.environ.get(
                "MOUXUE_MATERIALS_DIR", str(REPO_ROOT / "materials")
            ),
            web_search_provider=os.environ.get(
                "MOUXUE_WEB_SEARCH_PROVIDER", cls.web_search_provider
            ),
            web_search_corpus=os.environ.get(
                "MOUXUE_WEB_SEARCH_CORPUS", cls.web_search_corpus
            ),
            web_search_endpoint=os.environ.get(
                "MOUXUE_WEB_SEARCH_ENDPOINT", cls.web_search_endpoint
            ),
            web_search_api_key=os.environ.get(
                "MOUXUE_WEB_SEARCH_API_KEY", cls.web_search_api_key
            ),
            mcp_servers=os.environ.get("MOUXUE_MCP_SERVERS", cls.mcp_servers),
            mcp_timeout=float(os.environ.get("MOUXUE_MCP_TIMEOUT", cls.mcp_timeout)),
        )
