"""`/api/external/tools`：把"现在插了什么"说清楚。

这个端点的全部价值在于**分开三种情况**：没配 / 配了但没起来 / 接通了。
它们在界面上曾经长得一模一样（都是"没有这个工具"），
而那会让人去查服务器 —— 而真相可能是他少写了一行配置。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

BACKEND = Path(__file__).resolve().parents[2]


def _client(**overrides) -> TestClient:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        seed_on_startup=True,
        **overrides,
    )
    return TestClient(create_app(settings))


def test_without_configuration_it_says_what_is_missing(client) -> None:
    body = client.get("/api/external/tools").json()
    assert body["web_search"]["enabled"] is False
    assert body["web_search"]["reason"], "没配要给出**原因**，不能只说没有"
    assert body["mcp_servers"] == []
    assert body["tools"] == []
    assert body["scope_ceiling"] == "read"


def test_an_unreachable_server_is_reported_rather_than_hidden() -> None:
    """★ "没起来"必须看得见 —— 否则它会表现成"我插的服务器没有工具"。"""
    with _client(mcp_servers='[{"name": "ghost", "command": "绝对不存在的可执行文件"}]') as c:
        body = c.get("/api/external/tools").json()
    assert [row["server"] for row in body["mcp_servers"]] == ["ghost"]
    assert body["mcp_servers"][0]["connected"] is False
    assert body["mcp_servers"][0]["error"]


def test_a_working_server_and_its_tools_show_up() -> None:
    """接通之后：工具名带前缀、作用域上限仍是只读。"""
    config = json.dumps(
        [
            {
                "name": "demo",
                "command": f'"{sys.executable}" -m tools.fake_mcp_server',
                "cwd": str(BACKEND),
            }
        ]
    )
    with _client(mcp_servers=config) as c:
        body = c.get("/api/external/tools").json()
    assert body["mcp_servers"][0]["connected"] is True, body["mcp_servers"][0]["error"]
    assert "mcp__demo__echo" in body["tools"]
    assert body["scope_ceiling"] == "read"


def test_a_local_corpus_turns_on_web_search(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.json"
    corpus.write_text(
        '{"documents": [{"title": "t", "url": "https://e.test/x", "text": "间隔重复"}]}',
        encoding="utf-8",
    )
    with _client(
        web_search_provider="local", web_search_corpus=str(corpus)
    ) as c:
        body = c.get("/api/external/tools").json()
    assert body["web_search"]["enabled"] is True
    assert body["web_search"]["offline"] is True
    assert body["web_search"]["documents"] == 1
    assert "web_search" in body["tools"]


def test_off_means_the_model_never_sees_the_tool() -> None:
    """★ 没配时 `web_search` **不在工具清单里** —— 不是一个一调就报错的占位符。"""
    with _client() as c:
        body = c.get("/api/external/tools").json()
    assert body["tools"] == []
    assert body["scope_ceiling"] == "read"
