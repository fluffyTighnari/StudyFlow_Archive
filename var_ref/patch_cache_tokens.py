"""给 chat_message / sidebar_message 补上"缓存命中"与"未命中"两个 token 读数。

## 为什么值得加这两列

界面上要显示 DSH 那样的 token 用量面板：总量 / 缓存命中率 / 未命中输入 /
命中输入 / 输出。前三个数需要「这次请求里有多少 prompt token 是命中了缓存的」，
而 DeepSeek 的响应里本来就有：

```
"usage": {"prompt_tokens": 1200, "prompt_cache_hit_tokens": 1024,
          "prompt_cache_miss_tokens": 176, "completion_tokens": 300}
```

我们此前只留了 `prompt_tokens` / `completion_tokens`，于是"缓存命中率"
这一格**永远算不出来** —— 而算不出来的时候唯一诚实的写法是「没测到」，
不是 0%（0% 的意思是"测过了，一次都没命中"，那是另一件事）。

## 为什么是两列而不是一列

只有命中数也能算出未命中（`prompt_tokens - hit`），但那要求两者来自同一次
测量。服务端两种都给的时候，把两个都给出来更保险：谁少了就只信另一个，
而不是替它做减法。

## 谁写它

`api/discuss.py` 的两条留档路径（正常收尾 / 断线异常）与 `api/sidebar.py`。
它们都从 `llm_meta` 里取 —— `agent/llm.py` 现在会把这两个键带出来。
没有这两个键的提供方（Ollama 本地、测试替身）写 `None`，界面显示「没测到」。
"""

from __future__ import annotations

import pathlib
import re

BACKEND = pathlib.Path(r"D:\Workspace\Code\phy\personal_learning_flow\backend")

# ---------------------------------------------------------------- llm.py ==
LLM = BACKEND / "app/agent/llm.py"
META_OLD = """            "eval_count": usage.get("completion_tokens"),
            "prompt_eval_count": usage.get("prompt_tokens"),"""
META_NEW = """            "eval_count": usage.get("completion_tokens"),
            "prompt_eval_count": usage.get("prompt_tokens"),
            # 缓存读数：DeepSeek 会在 usage 里给这两项（命中 / 未命中的 prompt
            # token）。别的提供方没有这两项 —— 那种情况下它们保持 `None`，
            # 界面显示「没测到」，绝不当成 0。
            "cache_hit_tokens": usage.get("prompt_cache_hit_tokens"),
            "cache_miss_tokens": usage.get("prompt_cache_miss_tokens"),"""

META_STREAM_OLD = """                    "eval_count": usage.get("completion_tokens"),
                    "prompt_eval_count": usage.get("prompt_tokens"),"""
META_STREAM_NEW = """                    "eval_count": usage.get("completion_tokens"),
                    "prompt_eval_count": usage.get("prompt_tokens"),
                    "cache_hit_tokens": usage.get("prompt_cache_hit_tokens"),
                    "cache_miss_tokens": usage.get("prompt_cache_miss_tokens"),"""


def patch_llm() -> None:
    text = LLM.read_text(encoding="utf-8")
    before = text.count("prompt_eval_count")
    if '"cache_hit_tokens"' in text:
        print("· llm.py 已经带缓存读数，跳过")
        return
    text = text.replace(META_OLD, META_NEW, 1)
    text = text.replace(META_STREAM_OLD, META_STREAM_NEW, 1)
    assert text.count('"cache_hit_tokens"') == 2, text.count('"cache_hit_tokens"')
    LLM.write_text(text, encoding="utf-8")
    print(f"✅ llm.py：两处 meta 都带上缓存读数（prompt_eval_count 出现 {before} 处）")


# ------------------------------------------------------------- models.py ==
MODELS = BACKEND / "app/session_domain/models.py"
ANCHOR = """    #: 这一次生成的字数（`eval_count`）。与 `prompt_tokens` 一起构成用量读数。
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
"""
ADDITION = """    #: 这一次生成的字数（`eval_count`）。与 `prompt_tokens` 一起构成用量读数。
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 这次请求里**命中**提示缓存的 prompt token 数（DeepSeek 的
    #: `usage.prompt_cache_hit_tokens`）。
    #:
    #: ★ `None` ≠ 0：没测到与"一次都没命中"是两件事。本地模型与测试替身
    #: 不报这两个数，界面因此显示「没测到」而不是「命中率 0%」——
    #: 后者会让用户以为缓存坏了（而它可能根本没被询问过）。
    cache_hit_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 未命中的 prompt token 数（`usage.prompt_cache_miss_tokens`）。同上。
    cache_miss_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
"""


def patch_models() -> None:
    text = MODELS.read_text(encoding="utf-8")
    if "cache_hit_tokens" in text:
        print("· models.py 已经有这两列，跳过")
        return
    assert text.count(ANCHOR) == 1, text.count(ANCHOR)
    text = text.replace(ANCHOR, ADDITION, 1)
    MODELS.write_text(text, encoding="utf-8")
    print("✅ models.py：ChatMessage 加了 cache_hit_tokens / cache_miss_tokens")


# ------------------------------------------------------------- discuss.py ==
DISCUSS = BACKEND / "app/api/discuss.py"
D_OLD_1 = """        prompt_tokens=result.llm_meta.get("prompt_eval_count"),
        completion_tokens=result.llm_meta.get("eval_count"),"""
D_NEW_1 = """        prompt_tokens=result.llm_meta.get("prompt_eval_count"),
        completion_tokens=result.llm_meta.get("eval_count"),
        cache_hit_tokens=result.llm_meta.get("cache_hit_tokens"),
        cache_miss_tokens=result.llm_meta.get("cache_miss_tokens"),"""

D_OLD_2 = """            prompt_tokens=meta.get("prompt_eval_count"),
            completion_tokens=meta.get("eval_count"),"""
D_NEW_2 = """            prompt_tokens=meta.get("prompt_eval_count"),
            completion_tokens=meta.get("eval_count"),
            cache_hit_tokens=meta.get("cache_hit_tokens"),
            cache_miss_tokens=meta.get("cache_miss_tokens"),"""


def patch_discuss() -> None:
    text = DISCUSS.read_text(encoding="utf-8")
    if "cache_hit_tokens" in text:
        print("· discuss.py 已接，跳过")
        return
    text = text.replace(D_OLD_1, D_NEW_1, 1)
    text = text.replace(D_OLD_2, D_NEW_2, 1)
    # 每个插入块里这个名字出现两次（关键字 + 字符串键），两处 → 四次。
    assert text.count("cache_hit_tokens") == 4, text.count("cache_hit_tokens")
    DISCUSS.write_text(text, encoding="utf-8")
    print("✅ discuss.py：两条留档路径都写缓存读数")


# -------------------------------------------------------------- writes.py ==
WRITES = BACKEND / "app/session_domain/writes.py"
W_SIG_OLD = """    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> SidebarMessage:"""
W_SIG_NEW = """    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cache_hit_tokens: int | None = None,
    cache_miss_tokens: int | None = None,
) -> SidebarMessage:"""

W_VALID_OLD = """            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }.items():"""
W_VALID_NEW = """            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cache_hit_tokens": cache_hit_tokens,
            "cache_miss_tokens": cache_miss_tokens,
        }.items():"""


def patch_writes() -> None:
    text = WRITES.read_text(encoding="utf-8")
    if "cache_hit_tokens" in text:
        print("· writes.py 已接，跳过")
        return
    assert text.count(W_SIG_OLD) == 1, text.count(W_SIG_OLD)
    text = text.replace(W_SIG_OLD, W_SIG_NEW, 1)
    text = text.replace(W_VALID_OLD, W_VALID_NEW, 1)
    # 落库那一句：`SidebarMessage(...)` 里跟着 prompt/completion 走
    row_old = """        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        created_at=_now(),"""
    row_new = """        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
        created_at=_now(),"""
    count = text.count(row_old)
    assert count == 1, f"落库那一处匹配到 {count} 次，需要人工看一下"
    text = text.replace(row_old, row_new, 1)
    WRITES.write_text(text, encoding="utf-8")
    print("✅ writes.py：add_sidebar_message 接受并落库缓存读数")


# -------------------------------------------------------------- sidebar.py ==
SIDEBAR_API = BACKEND / "app/api/sidebar.py"
S_OLD = """        prompt_tokens=result.llm_meta.get("prompt_eval_count"),
        completion_tokens=result.llm_meta.get("eval_count"),"""
S_NEW = """        prompt_tokens=result.llm_meta.get("prompt_eval_count"),
        completion_tokens=result.llm_meta.get("eval_count"),
        cache_hit_tokens=result.llm_meta.get("cache_hit_tokens"),
        cache_miss_tokens=result.llm_meta.get("cache_miss_tokens"),"""


def patch_sidebar_api() -> None:
    text = SIDEBAR_API.read_text(encoding="utf-8")
    if "cache_hit_tokens" in text:
        print("· api/sidebar.py 已接，跳过")
        return
    assert text.count(S_OLD) == 1, text.count(S_OLD)
    text = text.replace(S_OLD, S_NEW, 1)
    SIDEBAR_API.write_text(text, encoding="utf-8")
    print("✅ api/sidebar.py：侧边栏那条路径也写缓存读数")


# -------------------------------------------------------------- schemas.py ==
SCHEMAS = BACKEND / "app/session_domain/schemas.py"


def patch_schemas() -> None:
    text = SCHEMAS.read_text(encoding="utf-8")
    if "cache_hit_tokens" in text:
        print("· schemas.py 已接，跳过")
        return
    pattern = re.compile(
        r"(    completion_tokens: int \| None = None\n)",
    )
    matches = pattern.findall(text)
    if len(matches) != 1:
        print(f"⚠ schemas.py：完成 token 那一行匹配到 {len(matches)} 处，需要人工看")
        return
    text = pattern.sub(
        r"\1"
        "    #: 命中 / 未命中的 prompt token（DeepSeek 的 usage 里给）。\n"
        "    #: **`None` = 没测到**，不是 0 —— 界面据此显示「没测到」。\n"
        "    cache_hit_tokens: int | None = None\n"
        "    cache_miss_tokens: int | None = None\n",
        text,
        count=1,
    )
    SCHEMAS.write_text(text, encoding="utf-8")
    print("✅ schemas.py：ChatMessageRead 暴露两个缓存读数")


if __name__ == "__main__":
    patch_llm()
    patch_models()
    patch_discuss()
    patch_writes()
    patch_sidebar_api()
    patch_schemas()
