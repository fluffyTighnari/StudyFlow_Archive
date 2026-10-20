"""联网搜索：**只有缝，没有服务**。

这一组测试盯的是三件在真接了一家搜索 API 之后依然成立的事：

1. `off` 时**没有这个工具**（不是一个一调就报错的占位符）；
2. 连不上**不是**"没搜到"（`error` vs `empty`）—— 把一次超时说成
   "世界上没有"，模型会据此得出一个关于世界的结论；
3. 每条结果都带 `url`：没有出处的条目**丢掉**，
   因为它进不了 `source_ref`，而"查不到来源的结论"正是这一层要防的东西。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.external.websearch import (
    HttpSearchProvider,
    LocalCorpusProvider,
    SearchUnavailable,
    build_search_provider,
)

CORPUS = {
    "documents": [
        {
            "title": "间隔重复",
            "url": "https://example.test/spaced-repetition",
            "text": "间隔重复是把复习分散到多天的方法，效果优于集中复习。",
        },
        {
            "title": "交错练习",
            "url": "https://example.test/interleaving",
            "text": "交错练习把不同类型的问题混在一起练，短期更难受，长期更好。",
        },
    ]
}


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(CORPUS, ensure_ascii=False), encoding="utf-8")
    return path


def test_off_means_there_is_no_tool() -> None:
    """★ 没配就没有 —— 而不是配一个"总是失败"的工具。"""
    assert build_search_provider("off") is None
    assert build_search_provider("") is None
    assert build_search_provider("disabled") is None


def test_a_local_corpus_is_deterministic(corpus: Path) -> None:
    provider = LocalCorpusProvider(corpus)
    first = provider.search("间隔重复")
    second = provider.search("间隔重复")
    assert [h.as_dict() for h in first] == [h.as_dict() for h in second]
    assert first[0].url == "https://example.test/spaced-repetition"
    assert "复习分散" in first[0].snippet


def test_case_and_whitespace_do_not_change_the_hit(corpus: Path) -> None:
    provider = LocalCorpusProvider(corpus)
    assert provider.search("  间隔重复  ") == provider.search("间隔重复")


def test_an_empty_query_returns_nothing_rather_than_everything(corpus: Path) -> None:
    """★ 空查询返回全部是一个很容易写出来的 bug，而它的症状是"搜什么都对"。"""
    assert LocalCorpusProvider(corpus).search("   ") == []


def test_the_limit_is_respected(corpus: Path) -> None:
    provider = LocalCorpusProvider(corpus)
    assert len(provider.search("练习", limit=1)) == 1


def test_local_configuration_must_point_at_a_corpus() -> None:
    with pytest.raises(SearchUnavailable, match="corpus"):
        build_search_provider("local")


def test_http_configuration_must_point_at_an_endpoint() -> None:
    with pytest.raises(SearchUnavailable, match="endpoint"):
        build_search_provider("http")


def test_an_unknown_provider_says_what_is_allowed() -> None:
    with pytest.raises(SearchUnavailable, match="可选：off、local、http"):
        build_search_provider("google")


def test_a_broken_http_endpoint_is_unavailable_not_empty() -> None:
    """★ 连不上必须抛，**不能**静默变成空结果。"""
    provider = HttpSearchProvider("http://127.0.0.1:1/search", timeout=0.5)
    with pytest.raises(SearchUnavailable):
        provider.search("随便")


def test_an_http_result_without_a_url_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有出处的条目丢掉 —— 它进不了 `source_ref`。"""

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "results": [
                        {"title": "有出处", "url": "https://a.test/x", "snippet": "s"},
                        {"title": "没有出处", "snippet": "s"},
                        {"url": "https://b.test/y"},
                    ]
                }
            ).encode("utf-8")

    monkeypatch.setattr(
        "app.external.websearch.urlrequest.urlopen", lambda *a, **k: FakeResponse()
    )
    hits = HttpSearchProvider("https://search.test").search("q")
    assert [h.url for h in hits] == ["https://a.test/x", "https://b.test/y"]
    assert hits[1].title == "https://b.test/y", "没给标题就拿 url 当标题，而不是留空"


def test_an_http_response_without_results_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """形状不对**不是**"没有结果"——这是两件不同的事。"""

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"error": "rate limited"}).encode("utf-8")

    monkeypatch.setattr(
        "app.external.websearch.urlrequest.urlopen", lambda *a, **k: FakeResponse()
    )
    with pytest.raises(SearchUnavailable, match="results"):
        HttpSearchProvider("https://search.test").search("q")
