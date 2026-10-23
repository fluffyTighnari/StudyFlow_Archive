"""从一份配置（`llm_profile`）造出能用的客户端。

## 为什么单独一个模块

`app/main.py` 里原来有一张 `PROVIDER_FACTORIES` 表，那是**按 provider 名字**造客户端；
现在还要考虑"这份配置是用户填的、存在库里、密钥在文件里"。
把这两层混在一起，`main.py` 会同时负责：应用组装、启动顺序、以及模型调用的构造细节。

这里的职责只有一件：**输入一份 profile（或一份 Settings），输出一个 LLMClient。**
它不读数据库（由调用方把 profile 取出来），也不写任何状态。

## `Settings` 与 profile 的关系

启动时 `app.state.llm_client` 用 `Settings` 造（`.env` / 环境变量那条路）；
数据库里有一份 `is_active` 的配置时，**它覆盖 Settings**。
这样"我配了但没生效"有一个确定的答案：看哪一份 active。
"""

from __future__ import annotations

from typing import Any

from app.agent.llm import FakeLLM, LLMClient, OllamaClient, OpenAICompatClient
from app.agent.providers import context_window_for, get_provider
from app.config import Settings


def make_client_from_profile(profile: Any, *, api_key: str, timeout: float = 300.0) -> LLMClient:
    """按 profile 的 `kind` 造客户端。`kind` 不认识就抛（不静默退回）。"""
    kind = profile.kind
    common = {
        "model": profile.model,
        "base_url": profile.base_url,
        "timeout": timeout,
    }
    if kind == "ollama":
        # 上下文窗口对 Ollama 是**必须显式给**的（见 OllamaClient 的说明）；
        # 没配就退回 Settings 的默认值，而不是不给 —— 不给会被截到 4095。
        return OllamaClient(
            **common,
            num_ctx=profile.num_ctx or Settings.llm_num_ctx,
            think=profile.think_flag,
        )
    if kind == "openai":
        client = OpenAICompatClient(**common, api_key=api_key)
        # `num_ctx` **不发给 OpenAI 兼容服务**（见 `_payload` 的说明），
        # 但它是用户明确配置的**上下文预算**：计量与压缩要用它，
        # 否则界面永远只能显示"窗口没报"，而压缩也不会发生。
        # 给 `num_ctx_is_budget` 是为了让界面能如实区分
        # "模型自己上报的窗口" 与 "按配置算的预算"。
        client.num_ctx = _openai_budget(profile.provider_id, profile.num_ctx)
        client.num_ctx_is_budget = True
        return client
    raise ValueError(
        f"配置 {profile.profile_id} 的 kind={kind!r} 不认识。"
        "可选：openai（OpenAI 兼容协议）、ollama（本地 Ollama）。"
    )


def make_client_from_settings(settings: Settings) -> LLMClient:
    """`.env` / 环境变量那条路（没有可用 profile 时用它）。**未知 provider 直接抛。**

    三处"没被显式改过就用默认值"的判断（model / base_url）都是同一件事：
    `Settings` 的默认值是**为 Ollama 定的**（`qwen3.6:27b`）。
    如果用户写了 `MOUXUE_LLM_PROVIDER=deepseek` 却没写模型名，
    把 `qwen3.6:27b` 发给 DeepSeek 会得到一个 "model not found" ——
    那句话不会提示"你应该填 deepseek-chat"。
    """
    provider_id = settings.llm_provider
    if provider_id == "fake":
        # 脚本为空，因此每次调用都会 `LLMError` —— 这正是"忘了配置"应有的样子。
        return FakeLLM([])

    if provider_id == "ollama":
        return OllamaClient(
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout,
            num_ctx=settings.llm_num_ctx,
            think=settings.llm_think,
        )
    if provider_id == "openai":
        client = OpenAICompatClient(
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key or _key_for("openai"),
            timeout=settings.llm_timeout,
        )
        client.num_ctx = _openai_budget("openai", settings.llm_num_ctx)
        client.num_ctx_is_budget = True
        return client

    # 其余一律当成**目录里的一家公司**（`MOUXUE_LLM_PROVIDER=deepseek`）。
    # 不认识就抛 —— 不静默退回默认值：一个拼错的名字表现成"往错地址发请求"，
    # 是最难查的一类。
    provider = get_provider(provider_id)
    model = settings.llm_model
    if model == Settings.llm_model:  # 没被显式改过
        model = provider.default_model
    base_url = settings.llm_base_url
    if base_url == Settings.llm_base_url:  # 没被显式改过
        base_url = provider.base_url

    if provider.kind == "ollama":
        return OllamaClient(
            model=model,
            base_url=base_url,
            timeout=settings.llm_timeout,
            num_ctx=settings.llm_num_ctx,
            think=settings.llm_think,
        )
    client = OpenAICompatClient(
        model=model,
        base_url=base_url,
        api_key=settings.llm_api_key or _key_for(provider_id),
        timeout=settings.llm_timeout,
    )
    client.num_ctx = _openai_budget(provider_id, settings.llm_num_ctx)
    client.num_ctx_is_budget = True
    return client


def _key_for(provider_id: str) -> str:
    """某家公司的密钥从哪个环境变量读（`MOUXUE_LLM_API_KEY` 之外的口子）。

    有这一条是因为**人会把密钥按公司命名**（`DEEPSEEK_API_KEY`）——
    那是最自然的写法，而我们不该要求他改成我们的名字。
    """
    import os

    candidates = {
        "deepseek": ("DEEPSEEK_API_KEY",),
        "openai": ("OPENAI_API_KEY",),
        "moonshot": ("MOONSHOT_API_KEY",),
        "zhipu": ("ZHIPUAI_API_KEY", "ZHIPU_API_KEY"),
        "dashscope": ("DASHSCOPE_API_KEY",),
        "siliconflow": ("SILICONFLOW_API_KEY",),
        "openrouter": ("OPENROUTER_API_KEY",),
        "groq": ("GROQ_API_KEY",),
    }.get(provider_id, ())
    for name in candidates:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def base_url_for(provider_id: str, settings: Settings) -> str:
    """这家公司在当前配置下的地址（给启动日志与 `/api/llm/config` 用）。"""
    if provider_id in ("openai", "ollama", "fake"):
        return settings.llm_base_url
    provider = get_provider(provider_id)
    if settings.llm_base_url != Settings.llm_base_url:
        return settings.llm_base_url
    return provider.base_url


def _openai_budget(provider_id: str, configured: int | None) -> int:
    """OpenAI 兼容接口的上下文预算。

    - 用户填了**非默认值** → 就用它；
    - 留空、或仍是旧的默认 16384 → 用这家模型的保守窗口（DeepSeek 64k）。
      `16384` 是 Ollama 时代的默认值，对商用模型太短，会把两轮对话压成摘要。
    """
    if configured and configured != Settings.llm_num_ctx:
        return int(configured)
    return context_window_for(provider_id)


def effective_context(profile: Any) -> int:
    """档案实际使用的上下文预算（界面显示与计量共用这一处）。"""
    if getattr(profile, "kind", "") == "ollama":
        return int(getattr(profile, "num_ctx", 0) or Settings.llm_num_ctx)
    return _openai_budget(
        getattr(profile, "provider_id", "custom"), getattr(profile, "num_ctx", 0)
    )
