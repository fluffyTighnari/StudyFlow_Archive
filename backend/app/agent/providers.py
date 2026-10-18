"""公司目录：**任何一家的 apikey / base_url / model 都能填**。

## 为什么是一张内置表而不是让用户自己拼 URL

大多数人只知道"我用 DeepSeek"，不知道它的 base_url 是
`https://api.deepseek.com/v1` 还是 `.../v1/chat/completions`，
也不知道它走的是 OpenAI 兼容协议还是自己的协议。
把这三件事（协议 / 默认地址 / 一个能直接用的模型名）写在这里，
用户就只需要粘一把钥匙 —— 而**填错 base_url 的表现是 404 或一串看不懂的 HTML**，
最难自己查。

`custom` 留给表里没有的服务：用户自己填 base_url 与模型名。

## 与"支持拉取模型列表"的关系

`supports_model_list` 说明这家能不能问出模型清单（`GET {base_url}/models`，
Ollama 是 `GET /api/tags`）。**不能拉的就在界面上让用户手填模型名** ——
假装能拉、然后给一个空列表，比直接说"这家要手填"更让人困惑。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Provider:
    """一家服务商。"""

    #: 稳定的机器名（存库、走 API）。**不要改名** —— 它会被写进 profile 行。
    provider_id: str
    label: str
    #: 协议种类：`openai`（OpenAI 兼容）或 `ollama`（本地 Ollama 原生协议）。
    kind: str
    base_url: str
    #: 是否需要密钥。本地服务不需要 —— 界面据此决定要不要显示"密钥"输入框。
    needs_key: bool = True
    #: 能否列出模型清单。
    supports_model_list: bool = True
    #: 一个"大概率能用"的模型名，作为输入框的默认值（不是唯一选项）。
    default_model: str = ""
    #: 界面上给用户的一句提示（去哪里拿钥匙）。
    key_hint: str = ""
    #: 常见模型名，仅作候选提示；列表以 `/models` 拉到的为准。
    known_models: tuple[str, ...] = field(default_factory=tuple)


PROVIDERS: tuple[Provider, ...] = (
    Provider(
        provider_id="deepseek",
        label="DeepSeek（深度求索）",
        kind="openai",
        base_url="https://api.deepseek.com/v1",
        # **实测（2026-02，`tools/livedeepseek.py` 第一次真跑）**：
        # 它的 `/v1/models` 只列 `deepseek-flash` 与 `deepseek-v4-pro`，
        # **而 `deepseek-chat` 不在清单里却完全能用** —— 那一次讨论就是用
        # `deepseek-chat` 跑通整个闭环的（3 次工具调用、计划 v1→v2）。
        #
        # 所以这里保留 `deepseek-chat` 作默认值：它是稳定别名，
        # 而清单里的是会随版本换掉的新名字。**这件事本身就是"清单 ≠ 可用名"
        # 的证据**，`/api/llm/profiles/{id}/probe` 因此改成"不在清单里就真发一句话"。
        default_model="deepseek-chat",
        key_hint="platform.deepseek.com → API keys",
        known_models=("deepseek-chat", "deepseek-flash", "deepseek-v4-pro", "deepseek-reasoner"),
    ),
    Provider(
        provider_id="openai",
        label="OpenAI",
        kind="openai",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        key_hint="platform.openai.com → API keys",
        known_models=("gpt-4o", "gpt-4o-mini", "gpt-4.1", "o4-mini"),
    ),
    Provider(
        provider_id="dashscope",
        label="通义千问（阿里云百炼）",
        kind="openai",
        # `/compatible-mode/v1` 是它的 OpenAI 兼容入口 —— 写成 `dashscope.aliyuncs.com`
        # 会打到 DashScope 原生协议上，那条路的请求体形状完全不同。
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        key_hint="bailian.console.aliyun.com → API-KEY",
        known_models=("qwen-plus", "qwen-max", "qwen-turbo", "qwen3-max"),
    ),
    Provider(
        provider_id="moonshot",
        label="Moonshot（Kimi）",
        kind="openai",
        base_url="https://api.moonshot.cn/v1",
        default_model="moonshot-v1-8k",
        key_hint="platform.moonshot.cn → API Key 管理",
        known_models=("moonshot-v1-8k", "moonshot-v1-32k", "kimi-k2-0905-preview"),
    ),
    Provider(
        provider_id="zhipu",
        label="智谱 GLM",
        kind="openai",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4-plus",
        key_hint="open.bigmodel.cn → API keys",
        known_models=("glm-4-plus", "glm-4-air", "glm-4-flash"),
    ),
    Provider(
        provider_id="siliconflow",
        label="硅基流动 SiliconFlow",
        kind="openai",
        base_url="https://api.siliconflow.cn/v1",
        default_model="deepseek-ai/DeepSeek-V3",
        key_hint="cloud.siliconflow.cn → API 密钥",
        known_models=("deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct"),
    ),
    Provider(
        provider_id="openrouter",
        label="OpenRouter（一家聚合多家）",
        kind="openai",
        base_url="https://openrouter.ai/api/v1",
        default_model="deepseek/deepseek-chat",
        key_hint="openrouter.ai → Keys",
    ),
    Provider(
        provider_id="groq",
        label="Groq",
        kind="openai",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        key_hint="console.groq.com → API Keys",
    ),
    Provider(
        provider_id="ollama",
        label="本地 Ollama",
        kind="ollama",
        base_url="http://127.0.0.1:11434",
        needs_key=False,
        default_model="qwen3.6:27b",
        key_hint="不需要密钥",
    ),
    Provider(
        provider_id="lmstudio",
        label="本地 LM Studio",
        kind="openai",
        base_url="http://127.0.0.1:1234/v1",
        needs_key=False,
        default_model="",
        key_hint="不需要密钥（LM Studio 的本地端口）",
    ),
    Provider(
        provider_id="vllm",
        label="自建 vLLM / 兼容网关",
        kind="openai",
        base_url="http://127.0.0.1:8000/v1",
        needs_key=False,
        default_model="",
        key_hint="自建服务的 OpenAI 兼容端口",
    ),
    Provider(
        provider_id="custom",
        label="其它（自己填地址）",
        kind="openai",
        base_url="",
        default_model="",
        key_hint="任何 OpenAI 兼容服务：base_url 要含 /v1",
    ),
)

PROVIDER_BY_ID: dict[str, Provider] = {p.provider_id: p for p in PROVIDERS}


def get_provider(provider_id: str) -> Provider:
    """取一家服务商。不认识就抛 —— **不静默退回自定义**。

    静默退回的后果：一个拼错的 `deepseek` 会变成"往空地址发请求"，
    而报错会指向网络。
    """
    try:
        return PROVIDER_BY_ID[provider_id]
    except KeyError:
        raise ValueError(
            f"不认识的服务商：{provider_id!r}。"
            f"可用：{'、'.join(PROVIDER_BY_ID)}。"
            "表里没有的请用 provider_id='custom' 并自己填 base_url。"
        ) from None
