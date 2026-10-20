"""抽题接进 HTTP 之后的那一层：`/api/benchmark/plan` 与 `/runs` 自动抽题。

`tests/benchmark/test_sampling.py` 测的是**规则**（纯函数）。
这里测的是**接线**，而接线上有三个只有走 HTTP 才看得见的错法：

1. **`/plan` 显示的与 `/runs` 记录的必须是同一套题。**
   两处各抽一次的话（哪怕规则相同），只要参数有一处不同，
   "预览说考这 6 道、实际记录成另外 6 道"就会发生 —— 而它不报错。
2. **他勾了熟悉的领域必须真的进入过滤。** 清单住在数据库，
   过滤住在纯函数，接线错了的表现是"抽题看起来正常，只是总抽到他熟的题"。
3. **题池有问题时必须是 422 加一句能修的话**，而不是 500 或者空计划。

这个测试用**自己的题库目录**（`Settings.items_dir`）而不是仓库里那份：
后者是**外部信息·渐进补**的内容，它现在还不完整，
而"机制对不对"不该等到内容写完才能验证。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.helpers import items

# --------------------------------------------------------------------------- #
# 一份**完整的小题库**（只为测试存在，不是产品内容）
# --------------------------------------------------------------------------- #

#: 夹具题库住在 `tests/fixtures/bank`，由 `tools/makefixturebank.py` 生成。
#:
#: ★ **单测与 `e2echeck` 指向同一个目录**，而不是各自造一份：
#: 两份夹具一定会漂移，而漂移之后"单测绿、端到端红"的排查成本
#: 会落在"到底是机制坏了还是夹具不一样"这个问题上。
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture()
def bank_dir() -> Path:
    assert FIXTURE_ROOT.is_dir(), (
        f"夹具题库不存在：{FIXTURE_ROOT}（跑 `python -m tools.makefixturebank` 生成）"
    )
    return FIXTURE_ROOT / "bank"


@pytest.fixture()
def client(bank_dir: Path) -> Iterator[TestClient]:
    """题库目录可换的应用实例。"""
    from app.config import Settings
    from app.main import create_app

    app = create_app(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            seed_on_startup=True,
            items_dir=str(bank_dir),
            materials_dir=str(FIXTURE_ROOT / "materials"),
        )
    )
    with TestClient(app) as test_client:
        yield test_client


def _plan(client: TestClient, **query) -> dict:
    params = {
        "form": "quick",
        "variable_id": "V-SE",
        "channel": "measured",
        "method_id": "M-SE-04",
        "seed": 5,
    }
    params.update(query)
    response = client.get("/api/benchmark/plan", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _checklist(client: TestClient, familiar: list[str], interested: list[str] | None = None):
    response = client.post(
        "/api/benchmark/domains",
        json={"familiar": familiar, "interested": interested or [], "reason": "测试"},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


# --------------------------------------------------------------------------- #
# 预览
# --------------------------------------------------------------------------- #


def test_the_plan_has_two_anchors_and_a_fixed_histogram(client: TestClient) -> None:
    plan = _plan(client)
    roles = {item["item_id"]: item["role"] for item in plan["items"]}
    assert [i for i, role in roles.items() if role == "anchor"] == ["A-1", "A-2"]
    regulars = [item for item in plan["items"] if item["role"] == "regular"]
    assert sorted(item["difficulty"] for item in regulars) == ["中", "中", "易", "难"]


def test_every_planned_item_says_why_it_is_there(client: TestClient) -> None:
    plan = _plan(client)
    assert all(item["why"] for item in plan["items"])
    anchor = next(item for item in plan["items"] if item["item_id"] == "A-1")
    assert "锚题" in anchor["why"]
    assert "等值化" in anchor["why"]


def test_the_plan_lists_the_topics_it_used(client: TestClient) -> None:
    plan = _plan(client)
    assert len(plan["topics"]) == len(set(plan["topics"]))
    assert all(topic and "-" in topic for topic in plan["topics"])


def test_the_same_seed_gives_the_same_plan(client: TestClient) -> None:
    assert _plan(client, seed=3)["items"] == _plan(client, seed=3)["items"]


def test_a_different_seed_can_change_which_items_appear(client: TestClient) -> None:
    ids = {tuple(i["item_id"] for i in _plan(client, seed=s)["items"]) for s in range(8)}
    assert len(ids) > 1


def test_the_plan_does_not_write_anything(client: TestClient) -> None:
    """预览是只读的 —— 点几次"看看会考什么"不该在他历史里留下运行记录。"""
    _plan(client)
    _plan(client)
    assert items(client.get("/api/benchmark/runs").json()) == []


# --------------------------------------------------------------------------- #
# 启动运行时用同一套题
# --------------------------------------------------------------------------- #


def test_starting_a_run_without_item_ids_samples_for_you(client: TestClient) -> None:
    preview = _plan(client)
    response = client.post(
        "/api/benchmark/runs",
        json={
            "form": "quick",
            "variable_id": "V-SE",
            "channel": "measured",
            "method_id": "M-SE-04",
            "seed": 5,
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["item_ids"] == [item["item_id"] for item in preview["items"]], (
        "预览与真正记录下来的题必须是同一套"
    )
    assert run["topic_snapshot"] == preview["topics"]
    assert "quick" in (run["note"] or "")


def test_explicit_item_ids_are_still_honoured(client: TestClient) -> None:
    """复现与测试需要指定题目。指定了就不抽题 —— 这一条不能被"自动抽题"吃掉。"""
    response = client.post(
        "/api/benchmark/runs",
        json={
            "form": "quick",
            "variable_id": "V-SE",
            "channel": "measured",
            "method_id": "M-SE-04",
            "seed": 5,
            "item_ids": ["R-E0"],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["item_ids"] == ["R-E0"]


# --------------------------------------------------------------------------- #
# 清单真的参与过滤
# --------------------------------------------------------------------------- #


def test_marking_a_topic_familiar_removes_items_that_touch_it(
    client: TestClient,
) -> None:
    """★ 选题不能写死 id：**谁被抽中是种子的结果**。

    写死 `R-E0` 的测试在换一个种子之后就会红，而它红的原因与"熟悉度过滤
    坏没坏"毫无关系 —— 那种测试会让人去改测试，而不是去查代码。
    所以先问"这次抽到了谁"，再把他那份材料的专题标成熟悉。
    """
    before = {item["item_id"]: item for item in _plan(client, seed=1)["items"]}
    easy = next(
        item
        for item in before.values()
        if item["difficulty"] == "易" and item["role"] == "regular"
    )
    _checklist(client, familiar=[easy["topics"][0]], interested=[])
    after = {item["item_id"] for item in _plan(client, seed=1)["items"]}
    assert easy["item_id"] in before
    assert easy["item_id"] not in after


def test_the_item_that_replaces_it_is_from_another_topic(client: TestClient) -> None:
    before = [item for item in _plan(client, seed=1)["items"] if item["role"] == "regular"]
    easy = next(item for item in before if item["difficulty"] == "易")
    _checklist(client, familiar=[easy["topics"][0]], interested=[])
    plan = _plan(client, seed=1)
    easy_after = [
        item
        for item in plan["items"]
        if item["difficulty"] == "易" and item["role"] == "regular"
    ]
    assert easy_after[0]["item_id"] != easy["item_id"]
    assert easy["topics"][0] not in easy_after[0]["topics"]
    assert any(easy["item_id"] in note for note in plan["notes"]), (
        "被排除的题必须出现在 notes 里 —— 否则「为什么没考那道题」没有答案"
    )


def test_interest_does_not_shrink_the_pool(client: TestClient) -> None:
    """★ 兴趣是**记录**，不是筛选（v0.3 §6）：勾了兴趣不该让题变少。

    如果它参与筛选，抽到的题会**全部落在兴趣专题里** ——
    而"他对什么感兴趣"就悄悄变成了"他实际被考了什么"。
    那正是红线 7（用一个系统理由解释自己的行为）换个面孔回来。
    """
    before = _plan(client, seed=1)
    _checklist(client, familiar=[], interested=["PHYS-QUANTUM", "LAW-IP"])
    after = _plan(client, seed=1)
    assert len(after["items"]) == len(before["items"])
    interest = {"PHYS-QUANTUM", "LAW-IP"}
    regulars = [item for item in after["items"] if item["role"] == "regular"]
    assert any(not interest.intersection(item["topics"]) for item in regulars), (
        "抽到的题全都落在兴趣专题里 —— 说明兴趣被当成筛选用了"
    )
    assert any("兴趣只影响优先级" in note for note in after["notes"])


def test_interest_only_reorders_equal_candidates(client: TestClient) -> None:
    """它确实会改变**具体抽到哪一道**（那是优先级），这一点也要钉住。

    否则"兴趣无效"与"兴趣是筛选"这两种相反的错法会同时通过上面的测试。
    """
    plain = _plan(client, seed=1)
    _checklist(client, familiar=[], interested=["PHYS-QUANTUM"])
    with_interest = _plan(client, seed=1)
    easy = lambda plan: next(  # noqa: E731
        item["item_id"]
        for item in plan["items"]
        if item["difficulty"] == "易" and item["role"] == "regular"
    )
    assert easy(with_interest) == "R-E0", "兴趣里的那道应该赢过同档的另一道"
    assert easy(plain) != easy(with_interest)


# --------------------------------------------------------------------------- #
# 答过的题会让位
# --------------------------------------------------------------------------- #


def test_answering_an_item_makes_the_next_run_prefer_another_one(
    client: TestClient,
) -> None:
    first = _plan(client, seed=2)
    easy_first = next(
        item for item in first["items"] if item["difficulty"] == "易" and item["role"] == "regular"
    )
    run = client.post(
        "/api/benchmark/runs",
        json={
            "form": "quick",
            "variable_id": "V-SE",
            "channel": "measured",
            "method_id": "M-SE-04",
            "seed": 2,
        },
    ).json()
    write = client.post(
        f"/api/benchmark/runs/{run['run_id']}/responses",
        json={
            "item_id": easy_first["item_id"],
            "question_no": 0,
            "kind": "key_points",
            "judged_by": "code",
            "raw_answer": "答了",
            "item_score": 6,
        },
    )
    assert write.status_code in (200, 201), write.text

    second = _plan(client, seed=2)
    easy_second = next(
        item for item in second["items"] if item["difficulty"] == "易" and item["role"] == "regular"
    )
    assert easy_second["item_id"] != easy_first["item_id"]
    assert "没做过" in easy_second["why"]


# --------------------------------------------------------------------------- #
# 抽不出题时的说法
# --------------------------------------------------------------------------- #


def test_an_unknown_cell_is_a_422_that_names_the_cell(client: TestClient) -> None:
    response = client.get(
        "/api/benchmark/plan",
        params={
            "form": "quick",
            "variable_id": "V-SE",
            "channel": "measured",
            "method_id": "M-SE-99",
            "seed": 0,
        },
    )
    assert response.status_code == 422, response.text
    assert "V-SE/measured/M-SE-99" in response.json()["detail"]


def test_a_missing_bank_directory_is_a_422_saying_the_mechanism_is_fine(
    bank_dir: Path,
) -> None:
    """★ 「题库目录不存在」与「抽题规则抽不出」必须说成两件事。

    混成一句"抽不出题"，会让人去查抽题代码 —— 而那里没有错。
    """
    from app.config import Settings
    from app.main import create_app

    app = create_app(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            seed_on_startup=True,
            items_dir=str(bank_dir.parent / "does-not-exist"),
        )
    )
    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/benchmark/plan",
            params={
                "form": "quick",
                "variable_id": "V-SE",
                "channel": "measured",
                "method_id": "M-SE-04",
                "seed": 0,
            },
        )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "题库目录不存在" in detail
    assert "抽题机制本身没问题" in detail


def test_an_exhausted_pool_says_which_difficulty_is_short(
    bank_dir: Path, client: TestClient
) -> None:
    """把"难"题全部标成熟悉 → 抽不出构成 → 422 且指出是哪一档。"""
    _checklist(client, familiar=["PSY-COG", "LAW-IP"], interested=[])
    response = client.get(
        "/api/benchmark/plan",
        params={
            "form": "quick",
            "variable_id": "V-SE",
            "channel": "measured",
            "method_id": "M-SE-04",
            "seed": 0,
        },
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "难 缺 1 道" in detail
    assert "2 道题被他标成熟悉而被排除" in detail
