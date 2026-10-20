"""联网搜索。

## 这里只有**缝**，没有搜索服务

一个真实的搜索 API 需要密钥、需要网络、还会随服务商改协议。
把某一家写死在这里，等于让"能不能联网"取决于一个我没法替用户决定的选择。
所以这一层提供的是：

- `SearchProvider` —— 一个两方法的协议（`search` / `describe`）；
- `HttpSearchProvider` —— **通用的 HTTP 接法**（`POST {query, limit}` →
  `{results: [{title, url, snippet}]}`），端点由配置给，默认**没有**；
- `LocalCorpusProvider` —— 一个确定性的本地语料库，**离线**，
  给测试与端到端用（否则"联网搜索接通了没有"永远只能靠人转述）。

★ **默认是关的**（`web_search_provider="off"`）。理由与 `llm_provider`
默认 `ollama` 同源：默认值不该让一个新克隆的仓库**悄悄联网**出去。

## 搜到的每一条都带出处

`SearchHit` 的三个字段（`title` / `url` / `snippet`）里最要紧的是 `url`：
外挂结论若要进全局，`submit_global_update` 需要一个 `source_ref`，
而"这条结论是从哪来的"必须能指回去。只给一段没有出处的文本，
等于让模型用**看起来像知识的东西**去改用户的认知状态。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib import error as urlerror
from urllib import request as urlrequest


class SearchUnavailable(RuntimeError):
    """搜索没接通 / 没配。**不是**"搜了但没有结果"。"""


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str

    def as_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


class SearchProvider(Protocol):
    """两方法协议。`describe()` 是给界面看的（"现在接的是哪个"）。"""

    name: str

    def search(self, query: str, *, limit: int = 5) -> list[SearchHit]: ...

    def describe(self) -> dict[str, Any]: ...


class LocalCorpusProvider:
    """确定性语料库：**离线**，命中就是命中。

    它按**子串**匹配（大小写不敏感），并把命中的字段截断成摘要 ——
    刻意不做分词、不做打分排序：一旦这里有了"相关性算法"，
    端到端验证就会开始依赖那套算法的细节，而它本来只是个替身。
    命中按语料库里的顺序返回（可复现）。
    """

    name = "local"

    def __init__(self, corpus_path: str | Path, *, snippet_chars: int = 200) -> None:
        self.corpus_path = Path(corpus_path)
        self.snippet_chars = snippet_chars
        self._rows = self._load()

    def _load(self) -> list[dict[str, str]]:
        raw = json.loads(self.corpus_path.read_text(encoding="utf-8"))
        rows = raw["documents"] if isinstance(raw, dict) else raw
        out: list[dict[str, str]] = []
        for row in rows:
            out.append(
                {
                    "title": str(row["title"]),
                    "url": str(row["url"]),
                    "text": str(row.get("text") or row.get("snippet") or ""),
                }
            )
        return out

    def search(self, query: str, *, limit: int = 5) -> list[SearchHit]:
        needle = query.strip().lower()
        if not needle:
            return []
        hits: list[SearchHit] = []
        for row in self._rows:
            haystack = f"{row['title']}\n{row['text']}".lower()
            if needle not in haystack:
                continue
            hits.append(
                SearchHit(
                    title=row["title"],
                    url=row["url"],
                    snippet=row["text"][: self.snippet_chars],
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "corpus": str(self.corpus_path),
            "documents": len(self._rows),
            "offline": True,
        }


class HttpSearchProvider:
    """通用 HTTP 接法。

    请求：`POST endpoint {"query": ..., "limit": ...}`
    （`api_key` 非空时带 `Authorization: Bearer <key>`）
    响应：`{"results": [{"title": ..., "url": ..., "snippet": ...}]}`

    ★ 连接失败 / 超时 / 形状不对都抛 `SearchUnavailable` ——
    它们**不是**空结果。把"没连上"报成"没搜到"，
    模型会据此得出一个关于世界的结论（"这个主题没有资料"），
    而真相是这次请求没出去。
    """

    name = "http"

    def __init__(
        self,
        endpoint: str,
        *,
        api_key: str = "",
        timeout: float = 15.0,
        snippet_chars: int = 300,
    ) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout = timeout
        self.snippet_chars = snippet_chars

    def search(self, query: str, *, limit: int = 5) -> list[SearchHit]:
        body = json.dumps({"query": query, "limit": limit}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urlrequest.Request(self.endpoint, data=body, headers=headers)
        try:
            with urlrequest.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urlerror.URLError, OSError, TimeoutError) as exc:
            raise SearchUnavailable(f"搜索服务连不上（{self.endpoint}）：{exc}") from exc
        except json.JSONDecodeError as exc:
            raise SearchUnavailable(f"搜索服务返回的不是 JSON：{exc}") from exc

        rows = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise SearchUnavailable(
                "搜索服务的响应里没有 `results` 数组（形状不对，"
                "不是'没有结果'）。"
            )
        hits: list[SearchHit] = []
        for row in rows[:limit]:
            if not isinstance(row, dict) or not row.get("url"):
                # 没有出处的条目**丢掉**：它进不了 source_ref，
                # 而"一条查不到来源的结论"正是这一层要防的东西。
                continue
            hits.append(
                SearchHit(
                    title=str(row.get("title") or row.get("url")),
                    url=str(row["url"]),
                    snippet=str(row.get("snippet") or "")[: self.snippet_chars],
                )
            )
        return hits

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "endpoint": self.endpoint,
            "offline": False,
            "has_key": bool(self.api_key),
        }


def build_search_provider(
    kind: str, *, corpus: str = "", endpoint: str = "", api_key: str = ""
) -> SearchProvider | None:
    """按配置造一个提供者。`off` / 空 → `None`（**没有这个工具**）。

    ★ 没配搜索时，模型**看不到** `web_search`，而不是看到一个
    一调就报错的工具：一个总是失败的工具会占掉模型的选择面，
    而它换来的只有"这次又不行"。
    """
    value = (kind or "").strip().lower()
    if value in {"", "off", "none", "disabled"}:
        return None
    if value == "local":
        if not corpus:
            raise SearchUnavailable("local 搜索需要 `web_search_corpus` 指向语料库文件。")
        return LocalCorpusProvider(corpus)
    if value == "http":
        if not endpoint:
            raise SearchUnavailable("http 搜索需要 `web_search_endpoint`。")
        return HttpSearchProvider(endpoint, api_key=api_key)
    raise SearchUnavailable(
        f"看不懂的搜索提供者：{kind!r}。可选：off、local、http。"
    )
