"""**模型调用配置不写认知状态** —— 从行为上证明这件事。

## 为什么需要这个文件

`llm_profile` 在 `PROVENANCE` 里声明为 `infrastructure`（与 `id_sequence` 同口径），
于是它**不需要 Router、不产生 `router_log`**。这让它成了整个系统里
第三个"能写库却不过 Router"的地方（前两个是目录种子与沙箱域）。

豁免是危险的东西：它在这里成立，不代表下一个人加字段时它还成立。
所以"它不是认知状态"这句话不能只写在注释里，得有测试：

1. **逐表比对行数** —— 写完一整套配置（建/改/删/切换）之后，
   认知四表与 `router_log` 的行数一个都没变；
2. **I1–I7 仍然通过** —— 这是这个项目自己的机制在检查我；
3. **加一张新表不会悄悄溜进"基础设施"** —— 见
   `tests/session/test_schema_purity.py::test_every_table_is_registered_and_classified`
   （它一加表就变红，必须显式表态）；
4. **密钥不出现在任何响应里**。

第 4 条单独说：用户选了"配置存库、界面里填"，所以密钥会**经过** HTTP
请求体。它不能经过响应。
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.global_domain.invariants import check_global_invariants
from app.global_domain.models import CEvidence, M1BaselineVersion, M2Preference, RouterLog
from app.llm_config import LlmProfile, read_secret, secret_path
from app.main import create_app

#: 一把**明显是假**的密钥。刻意长得像真的（前缀 + 长度），
#: 这样"响应里有没有它"这个断言才有意义。
FAKE_KEY = "sk-testkey-0000111122223333444455556666"


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """真 HTTP（TestClient）+ 临时库 + **临时密钥目录**。

    临时密钥目录是必须的：不隔离的话，测试会往开发者本机的
    `backend/var/secrets/` 里写文件，而那些文件会跨测试残留下来 ——
    "第二次跑就绿了/红了"这类问题最难查。
    """
    monkeypatch.setenv("MOUXUE_SECRETS_DIR", str(tmp_path / "secrets"))
    app = create_app(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            seed_on_startup=False,
            check_invariants=True,
        )
    )
    with TestClient(app) as test_client:
        yield test_client


def _row_counts(db: Session) -> dict[str, int]:
    """认知域 + 审计表的行数。**逐表数，不用总数** ——
    总数相同可能是"这边少一行那边多一行"互相抵消。"""
    models = {
        "router_log": RouterLog,
        "m1_baseline_version": M1BaselineVersion,
        "m2_preference": M2Preference,
        "c_evidence": CEvidence,
    }
    return {
        name: int(db.scalar(select(func.count()).select_from(model)) or 0)
        for name, model in models.items()
    }


def _session_db(client: TestClient) -> Session:
    factory = client.app.state.session_factory
    return factory()


def test_creating_a_profile_changes_no_cognition_row(client: TestClient) -> None:
    """建一份配置：认知四表与审计表的行数一个都不许变。

    这条断言看起来"当然如此"，但它抓的是一种具体的错法：
    将来有人在 `create_profile` 里顺手记一条 `router_log`（"变更留痕，听起来挺负责"），
    那时 `router_log` 就成了一个**混了两类东西**的表 ——
    而"审计日志里的每一条都对应一次认知状态变更"这个前提一破，
    所有基于它的推理（I5、rollback、提案追溯）都失去意义。
    """
    with _session_db(client) as db:
        before = _row_counts(db)

    created = client.post(
        "/api/llm/profiles",
        json={
            "label": "测试用",
            "provider_id": "deepseek",
            "model": "deepseek-chat",
            "api_key": FAKE_KEY,
        },
    )
    assert created.status_code == 201, created.text

    with _session_db(client) as db:
        after = _row_counts(db)
        assert after == before, f"写模型配置动到了认知/审计状态：{before} → {after}"
        assert check_global_invariants(db) == [], "写配置之后不变式不再成立"


def test_a_full_lifecycle_of_config_writes_stays_out_of_cognition(
    client: TestClient,
) -> None:
    """建 → 改 → 再建一份 → 切换 → 删。**整套动作走完**再比对。

    只测"建"是不够的：切换与删除是最容易顺手写日志的两处
    （"换了模型"听起来确实像个值得记录的事件）。
    """
    with _session_db(client) as db:
        before = _row_counts(db)

    first = client.post(
        "/api/llm/profiles",
        json={"label": "A", "provider_id": "deepseek", "api_key": FAKE_KEY},
    ).json()
    second = client.post(
        "/api/llm/profiles",
        json={"label": "B", "provider_id": "ollama", "base_url": "http://127.0.0.1:11434"},
    ).json()

    assert client.post(f"/api/llm/profiles/{second['profile_id']}/activate").status_code == 200
    assert (
        client.patch(
            f"/api/llm/profiles/{first['profile_id']}",
            json={"label": "A2", "provider_id": "deepseek", "model": "deepseek-reasoner"},
        ).status_code
        == 200
    )
    assert client.delete(f"/api/llm/profiles/{first['profile_id']}").status_code == 200

    with _session_db(client) as db:
        after = _row_counts(db)
        assert after == before, f"配置的完整生命周期动到了认知/审计状态：{before} → {after}"
        assert check_global_invariants(db) == []


def test_the_key_never_appears_in_any_response(client: TestClient) -> None:
    """拿真密钥跑一遍**全部** llm 端点，断言那句话不出现在任何响应里。

    这是"密钥不进数据库、也不回传"这句话的可执行形式。
    回显掩码（`sk-t…6666`）是允许的 —— 界面要能显示"当前用的是哪把"。
    """
    created = client.post(
        "/api/llm/profiles",
        json={"label": "A", "provider_id": "deepseek", "api_key": FAKE_KEY},
    )
    profile_id = created.json()["profile_id"]

    responses = [
        created,
        client.get("/api/llm/profiles"),
        client.get(f"/api/llm/profiles"),
        client.get("/api/llm/config"),
        client.get("/api/llm/providers"),
        client.patch(
            f"/api/llm/profiles/{profile_id}",
            json={"label": "A", "provider_id": "deepseek", "model": "deepseek-reasoner"},
        ),
        client.post(f"/api/llm/profiles/{profile_id}/activate"),
    ]
    for response in responses:
        assert FAKE_KEY not in response.text, (
            f"{response.request.method} {response.request.url} 的响应里出现了明文密钥"
        )

    # 掩码要真的出现 —— 否则"没泄漏"可能只是因为**什么都没回**。
    assert created.json()["has_key"] is True
    assert created.json()["key_masked"].startswith("sk-t")
    assert created.json()["key_masked"].endswith("6666")


def test_the_key_is_not_in_the_database(client: TestClient) -> None:
    """密钥落在文件里，**不在库里**。

    这条是这一版与"把密钥存进数据库"那句话的差别所在：库是最容易被
    整体复制的东西（拷 .db、备份、`git add` 时漏挡），而密钥是唯一
    "泄漏即损失"的字段。所以"配置存库、界面里填"照旧，但密钥另放。
    """
    created = client.post(
        "/api/llm/profiles",
        json={"label": "A", "provider_id": "deepseek", "api_key": FAKE_KEY},
    )
    profile_id = created.json()["profile_id"]

    with _session_db(client) as db:
        row = db.get(LlmProfile, profile_id)
        assert row is not None
        # 把整行的所有列都转成字符串找一遍 —— 不是"找已知字段名"，
        # 而是"整行里都不许有它"（将来加字段时这条仍然有效）。
        dumped = " ".join(
            str(getattr(row, column.name)) for column in row.__table__.columns
        )
        assert FAKE_KEY not in dumped, "密钥进了数据库：它应该只在 var/secrets/ 里"

    assert read_secret(profile_id) == FAKE_KEY
    assert secret_path(profile_id).is_file()


def test_clearing_the_key_is_not_the_same_as_leaving_it(client: TestClient) -> None:
    """`api_key` 三态：不给 = 不动、`""` = 清掉、有值 = 换一把。

    两态版本（不给就清掉）会让人在"只改模型名"时把密钥弄丢 ——
    而那时的表现是"改了个模型名，忽然认证失败了"。
    """
    profile_id = client.post(
        "/api/llm/profiles",
        json={"label": "A", "provider_id": "deepseek", "api_key": FAKE_KEY},
    ).json()["profile_id"]

    # 不给 api_key：密钥还在。
    client.patch(
        f"/api/llm/profiles/{profile_id}",
        json={"label": "A", "provider_id": "deepseek", "model": "deepseek-reasoner"},
    )
    assert read_secret(profile_id) == FAKE_KEY

    # 给空串：清掉。
    cleared = client.patch(
        f"/api/llm/profiles/{profile_id}",
        json={"label": "A", "provider_id": "deepseek", "model": "deepseek-reasoner", "api_key": ""},
    )
    assert cleared.json()["has_key"] is False
    assert read_secret(profile_id) == ""


def test_a_provider_without_a_default_url_must_be_given_one(client: TestClient) -> None:
    """`custom` 没有默认地址 —— 必须自己填。

    允许空地址的后果：请求发到 `"/chat/completions"` 这种相对路径上，
    报错是"URL 里没有 host"，一句话都不提"你没填地址"。
    """
    response = client.post(
        "/api/llm/profiles", json={"label": "X", "provider_id": "custom", "model": "m"}
    )
    assert response.status_code == 400 or response.status_code == 422, response.text
    assert "base_url" in response.text


def test_an_unknown_provider_is_refused_by_name(client: TestClient) -> None:
    """不认识的公司名要**报出它自己**，而不是静默退回自定义。"""
    response = client.post(
        "/api/llm/profiles",
        json={"label": "X", "provider_id": "deepsek", "model": "m", "base_url": "https://x/v1"},
    )
    assert response.status_code in (400, 422)
    assert "deepsek" in response.text


def test_the_first_profile_becomes_active(client: TestClient) -> None:
    """第一份自动生效。

    否则用户填完了却发现"没在用"，而界面上不会有任何提示 ——
    那时他会去查别的地方（重启？改 .env？）。
    """
    created = client.post(
        "/api/llm/profiles", json={"label": "A", "provider_id": "deepseek"}
    ).json()
    assert created["is_active"] is True
    assert client.get("/api/llm/config").json()["source"] == "profile"


def test_deleting_the_active_profile_hands_over_to_another(client: TestClient) -> None:
    """删掉生效中的那份，必须有另一份接上。

    不接的后果：下一句话打到一个空配置上 —— 而"配置在，只是没有生效的"
    会表现成"模型不回答了"。
    """
    first = client.post(
        "/api/llm/profiles", json={"label": "A", "provider_id": "deepseek"}
    ).json()["profile_id"]
    second = client.post(
        "/api/llm/profiles", json={"label": "B", "provider_id": "ollama"}
    ).json()["profile_id"]
    client.post(f"/api/llm/profiles/{first}/activate")

    deleted = client.delete(f"/api/llm/profiles/{first}")
    assert deleted.status_code == 200
    assert deleted.json()["active_profile_id"] == second
    assert client.get("/api/llm/config").json()["source"] == "profile"


def test_deleting_the_last_profile_falls_back_to_settings(client: TestClient) -> None:
    """删光了就退回 `.env` 那条路，而不是"没有模型可用"。"""
    profile_id = client.post(
        "/api/llm/profiles", json={"label": "A", "provider_id": "deepseek"}
    ).json()["profile_id"]
    deleted = client.delete(f"/api/llm/profiles/{profile_id}")
    assert deleted.json()["active_profile_id"] is None
    config = client.get("/api/llm/config").json()
    assert config["source"] == "settings"
    assert config["settings"]["llm_provider"], "退回 settings 之后要能看到它的取值"
