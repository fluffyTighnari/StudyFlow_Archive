"""多模型档案与上下文计量的 HTTP 契约。

这两件事是界面这一轮新增能力的**数据来源**：

1. 一个档案可以勾选多个模型，启用时**一次**同时选中档案与模型
   （`POST /activate {model}`）。分开两步会得到"启用了但用的还是旧模型"；
2. `GET /sessions/{id}/context` 是上下文环的唯一数据源。它必须能在
   **还没跑过一轮**时给出 200 与 `measured=false` —— 那是一个真实状态，
   不是错误。
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _profile(client: TestClient, **overrides) -> dict:
    body = {
        "label": "验收档案",
        "provider_id": "deepseek",
        "model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "base_url": "https://api.deepseek.com",
        "num_ctx": 16384,
    }
    body.update(overrides)
    response = client.post("/api/llm/profiles", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_a_profile_holds_several_models_and_activation_selects_one(
    client: TestClient,
) -> None:
    created = _profile(client)
    assert created["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert created["model"] == "deepseek-chat"
    # 第一份档案自动生效 —— 这是既有约定，多模型不该把它打破。
    assert created["is_active"] is True
    # 旧默认 16384 对 DeepSeek 太短：留空/旧默认时按模型窗口（64k）计预算。
    assert created["effective_num_ctx"] == 65536

    custom = _profile(
        client,
        label="自定义预算",
        num_ctx=8000,
        models=["deepseek-chat"],
    )
    assert custom["effective_num_ctx"] == 8000

    response = client.post(
        f"/api/llm/profiles/{created['profile_id']}/activate",
        json={"model": "deepseek-reasoner"},
    )
    assert response.status_code == 200, response.text
    activated = response.json()
    assert activated["model"] == "deepseek-reasoner"
    assert activated["is_active"] is True
    # 换模型不改变勾选清单 —— 它只是把这一个选中。
    assert set(activated["models"]) == {"deepseek-chat", "deepseek-reasoner"}


def test_activating_a_model_outside_the_checked_list_is_a_422(
    client: TestClient,
) -> None:
    created = _profile(client)
    response = client.post(
        f"/api/llm/profiles/{created['profile_id']}/activate",
        json={"model": "gpt-4o"},
    )
    assert response.status_code == 422, response.text
    assert "不在档案里" in response.json()["detail"]

    # `{}`（老客户端）仍然可用：沿用档案当前模型，不报错。
    legacy = client.post(f"/api/llm/profiles/{created['profile_id']}/activate", json={})
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["model"] == "deepseek-chat"


def test_updating_a_profile_can_add_and_remove_checked_models(
    client: TestClient,
) -> None:
    created = _profile(client)
    response = client.patch(
        f"/api/llm/profiles/{created['profile_id']}",
        json={
            "provider_id": "deepseek",
            "model": "deepseek-chat",
            "models": ["deepseek-chat", "deepseek-v4-pro"],
            "base_url": "https://api.deepseek.com",
        },
    )
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["models"] == ["deepseek-chat", "deepseek-v4-pro"]

    # 当前模型永远留在清单里：档案不能出现"正在用一个没勾选的模型"。
    response = client.patch(
        f"/api/llm/profiles/{created['profile_id']}",
        json={
            "provider_id": "deepseek",
            "model": "deepseek-chat",
            "models": ["deepseek-v4-pro"],
            "base_url": "https://api.deepseek.com",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["models"] == ["deepseek-chat", "deepseek-v4-pro"]


def test_a_legacy_single_model_profile_still_reads_back_as_one_model(
    client: TestClient,
) -> None:
    created = _profile(client, models=None, model="deepseek-chat")
    assert created["models"] == ["deepseek-chat"]


def test_context_meter_is_readable_before_the_first_round(
    client: TestClient,
) -> None:
    folder = client.post("/api/folders", json={"name": "英语"}).json()
    session = client.post(
        "/api/sessions",
        json={"folder_id": folder["folder_id"], "title": "背单词"},
    ).json()

    response = client.get(f"/api/sessions/{session['session_id']}/context")
    assert response.status_code == 200, response.text
    meter = response.json()

    assert meter["used"] == 0
    assert meter["measured"] is False
    assert meter["pressure"] == "unmeasured"
    assert meter["live_messages"] == 0
    assert meter["compact_count"] == 0
    # 窗口上限来自此刻生效的模型；默认 Ollama 客户端有 num_ctx。
    assert meter["limit"] >= 0

    missing = client.get("/api/sessions/S-9999/context")
    assert missing.status_code == 404
