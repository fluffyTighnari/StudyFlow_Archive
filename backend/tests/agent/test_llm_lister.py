"""`list_models` 这一类"看起来实现了、其实没接上"的代码，必须有一条测试守着。

为什么单独一个文件：这个问题**不在逻辑里，在绑定里**。
`list_models` 的全部代码都是对的，唯一的问题是它的缩进让它落在了
`context_truncation_note()` 的函数体里（`return` 之后）—— 于是
`hasattr(OllamaClient, 'list_models')` 是 `False`，探活直接 500。

任何只测"解析对不对"的测试都会通过 —— 因为它们能直接调用那块代码。
所以这里测的是**它到底在不在类上**。
"""

from __future__ import annotations

from app.agent.llm import LLMError, ModelLister, OllamaClient


def test_ollama_client_really_exposes_list_models() -> None:
    """★ 这条就是那个缺陷的回归测试：方法必须在类上，不能只是写在文件里。"""
    client = OllamaClient(model="qwen3.5:9b")
    assert hasattr(client, "list_models"), (
        "OllamaClient 上没有 list_models —— 很可能是又被缩进到别的函数里了"
    )
    #: `ModelLister` 是运行时协议，探活端点靠它判断"这条客户端能不能列模型"。
    assert isinstance(client, ModelLister)


def test_ollama_list_models_reads_api_tags() -> None:
    client = OllamaClient(model="qwen3.5:9b", base_url="http://127.0.0.1:11434/")
    seen: list[str] = []

    def fake_get(url: str, _headers: dict[str, str], _timeout: float) -> dict:
        seen.append(url)
        return {
            "models": [
                {"name": "qwen3.6:27b"},
                {"model": "qwen3.5:9b"},
                {"name": "qwen3.5:9b"},  # 重复项要去重
                {"name": ""},  # 空名不算
                "不是字典",  # 脏数据要跳过，不能让整条路崩掉
            ]
        }

    assert client.list_models(get=fake_get) == ["qwen3.5:9b", "qwen3.6:27b"]
    #: 地址要拼在 `/api/tags` 上，且 base_url 末尾的斜杠不能拼出 `//api/tags`。
    assert seen == ["http://127.0.0.1:11434/api/tags"]


def test_ollama_list_models_says_something_when_not_ollama() -> None:
    """地址其实是个 OpenAI 兼容服务时，报错要说"该换服务商"，而不是抛 KeyError。"""

    def fake_get(_url: str, _headers: dict[str, str], _timeout: float) -> dict:
        return {"data": [{"id": "gpt-4o"}]}  # OpenAI 形状

    client = OllamaClient(model="qwen3.5:9b")
    try:
        client.list_models(get=fake_get)
    except LLMError as exc:
        assert "OpenAI 兼容" in str(exc)
    else:  # pragma: no cover - 走到这里说明错误信息丢了
        raise AssertionError("/api/tags 没有 models 列表时应该抛 LLMError")
