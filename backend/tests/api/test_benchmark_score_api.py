"""计分接进 HTTP 之后的那一层：`POST /api/benchmark/runs/{id}/score` 与 `/finish`（P9）。

`tests/benchmark/test_scoring.py` 测的是**规则**（纯函数）。
这里测的是**接线**，而接线上有四个只有走 HTTP 才看得见的错法：

1. **判官真的被问到了、而且它的引文真的被核对了。** 判分规则住在纯函数里，
   而"谁去问模型、问完把什么写回库"住在 API 层 —— 接错了的表现是
   `item_score` 照样有一个数，只是它来自模型的一面之词。
2. **`scoring_method` 真的落库了。** 它存在全部意义就是"半年后能回答
   这个分是哪条规则算的"，而它只在 `benchmark_response` 里看得见。
3. **作废题不参与聚合。** 这是最难发现的一类错：把"没测到"当 0 分，
   分数只是低了一点，看起来仍然是一个正常的分。
4. **`/finish` 不重新判分。** 重新判分会让他看到 7.5、库里存成 6.8 ——
   而那种不一致没有任何一步会报错。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.llm import FakeLLM, LLMReply

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"


def _client_for(bank_dir: Path):
    """按指定题库目录造一个应用。"""
    from app.config import Settings
    from app.main import create_app

    return create_app(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            seed_on_startup=True,
            items_dir=str(bank_dir),
            materials_dir=str(FIXTURE_ROOT / "materials"),
        )
    )


@pytest.fixture()
def app():
    return _client_for(FIXTURE_ROOT / "bank")


@pytest.fixture()
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _use(app, *replies: str) -> FakeLLM:
    """把应用那一侧换成确定性替身（判官要问模型）。

    ★ 顺手给替身一个 `model` 名字：真实客户端都有（判分要记 `judge_model`，
    否则"同一个答案两次判出不同分"这件事无法追）。
    `FakeLLM` 本来没有这个名字，因为它在别处只当"会说人话的东西"用。
    """
    fake = FakeLLM([LLMReply(content=text) for text in replies])
    fake.model = "fake-judge"  # type: ignore[attr-defined]
    app.state.llm_client = fake
    return fake


@pytest.fixture()
def patched_bank(tmp_path: Path):
    """复制夹具题库到临时目录，并允许改其中一道题的规格。

    ★ 为什么不直接往共享夹具里加题：夹具被单测与 `e2echeck` 共用，
    动它的**影响面比这个测试大得多**（抽题的候选集、每题的历史、
    别人钉住的数量断言都会跟着动）。"
    "为了测一个分支去改公共夹具，是在用一个更大的不确定性换一个小确定性。
    """
    import json
    import shutil

    bank = tmp_path / "bank"
    shutil.copytree(FIXTURE_ROOT / "bank", bank)
    # ★ 材料目录必须是题库目录的**兄弟**（`load_bank` 按这个约定找材料）——
    # 只复制 bank/ 的话，每一道题都会因为"材料缺失"加载失败。
    shutil.copytree(FIXTURE_ROOT / "materials", tmp_path / "materials")

    def patch(item_id: str, change) -> Path:
        path = bank / f"{item_id}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        change(raw)
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return bank

    return patch


def _start(client: TestClient, item_ids: list[str], **over) -> str:
    body = {
        "form": "quick",
        "variable_id": "V-SE",
        "channel": "measured",
        "method_id": "M-SE-04",
        "seed": 7,
        "item_ids": item_ids,
    }
    body.update(over)
    response = client.post("/api/benchmark/runs", json=body)
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


def _answer(client: TestClient, run_id: str, **body) -> dict:
    payload = {
        "item_id": "A-1",
        "question_no": 0,
        "kind": "key_points",
        "judged_by": "llm",
    }
    payload.update(body)
    response = client.post(f"/api/benchmark/runs/{run_id}/responses", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _score(client: TestClient, run_id: str) -> dict:
    response = client.post(f"/api/benchmark/runs/{run_id}/score")
    assert response.status_code == 200, response.text
    return response.json()


def _guide_of(item_id: str, root: Path | None = None) -> list[str]:
    import json

    base = root or (FIXTURE_ROOT / "bank")
    raw = json.loads((base / f"{item_id}.json").read_text("utf-8"))
    return raw["key_points_question"]["scoring_guide"]


def _answer_of(item_id: str, root: Path | None = None) -> str:
    """把评分指南的三条**原话**连起来当作他的答案 —— 这样引文一定核对得过。"""
    return "；".join(_guide_of(item_id, root))


# --------------------------------------------------------------------------- #
# 要点题：判官 + 引文核对
# --------------------------------------------------------------------------- #


def test_key_points_are_judged_by_the_llm_and_scored_by_verified_hits(
    app, client: TestClient
) -> None:
    run_id = _start(client, ["A-1", "A-2", "R-E0"])
    guide = _guide_of("A-1")
    answer = _answer_of("A-1")
    _answer(client, run_id, raw_answer=answer)
    _use(app, '{"hits": [{"no": 1, "quote": "%s"}, {"no": 2, "quote": "%s"}]}' % (guide[0], guide[1]))

    result = _score(client, run_id)
    scored = next(i for i in result["items"] if i["item_id"] == "A-1")
    assert scored["item_score"] == pytest.approx(10 * 2 / len(guide), abs=0.01)
    assert scored["judged_by"] == "llm"
    assert "命中 2/3" in scored["scoring_method"]


def test_a_fabricated_quote_earns_nothing(app, client: TestClient) -> None:
    """★ 整个判分设计的目的：**编一段引用换不到分**。"""
    run_id = _start(client, ["A-1", "A-2", "R-E0"])
    _answer(client, run_id, raw_answer="我随便写了一点东西，跟要点没什么关系。")
    _use(app, '{"hits": [{"no": 1, "quote": "主线是这篇材料的主线"}, {"no": 2, "quote": "关键事实"}]}')

    result = _score(client, run_id)
    scored = next(i for i in result["items"] if i["item_id"] == "A-1")
    assert scored["item_score"] == 0.0
    assert "引文核对降级 2" in scored["scoring_method"]
    assert any("降级" in note for note in result["notes"])


def test_a_broken_judge_is_void_not_zero(app, client: TestClient) -> None:
    """★ 判官坏了 ≠ 0 分：那会让一次模型故障把用户的分数打成 0。"""
    run_id = _start(client, ["A-1", "A-2", "R-E0"])
    _answer(client, run_id, raw_answer="认真答了三条，但格式不是 JSON。")
    _use(app, "我觉得答得挺好的")

    result = _score(client, run_id)
    scored = next(i for i in result["items"] if i["item_id"] == "A-1")
    assert scored["item_score"] is None
    assert scored["void_reason"] and "判官" in scored["void_reason"]
    assert any("没测到" in note for note in result["notes"])


def test_the_judge_is_actually_asked(app, client: TestClient) -> None:
    """判分规则住在纯函数里，而**谁去问模型**住在这一层 —— 接错了不会报错。"""
    run_id = _start(client, ["A-1"])
    _answer(client, run_id, raw_answer=_answer_of("A-1"))
    fake = _use(app, '{"hits": []}')
    _score(client, run_id)
    assert len(fake.seen) == 1, "要点题必须问一次判官"
    prompt = fake.seen[0][-1].content
    assert "评分指南" in prompt and "主线" in prompt


# --------------------------------------------------------------------------- #
# 客观题：代码判，不问模型
# --------------------------------------------------------------------------- #


def test_objective_questions_are_scored_by_code_without_touching_the_model(
    app, client: TestClient
) -> None:
    """客观题**不该**问模型：正确答案就在题目里，问模型只是多一次出错的机会。"""
    run_id = _start(client, ["A-1"])
    import json

    raw = json.loads((FIXTURE_ROOT / "bank" / "A-1.json").read_text("utf-8"))
    answer = raw["exact_questions"][0]["answer"]
    _answer(
        client,
        run_id,
        question_no=1,
        kind="exact",
        judged_by="code",
        raw_answer=answer,
    )
    fake = _use(app)  # 脚本为空：一旦被问到就会抛 LLMError
    result = _score(client, run_id)
    scored = next(i for i in result["items"] if i["question_no"] == 1)
    assert scored["item_score"] == 10.0
    assert scored["scoring_method"] == "code:exact"
    assert fake.seen == []


def test_a_wrong_objective_answer_is_zero_but_is_only_a_ruler(
    app, client: TestClient
) -> None:
    """★ 正误题得 0 分，但**它不产出读数**（红线 7）。"""
    run_id = _start(client, ["A-1", "A-2", "R-E0"])
    for item_id in ("A-1", "A-2", "R-E0"):
        _answer(
            client,
            run_id,
            item_id=item_id,
            question_no=1,
            kind="exact",
            judged_by="code",
            raw_answer="肯定不是这个答案",
        )
    _use(app)
    result = _score(client, run_id)
    assert all(i["item_score"] == 0.0 for i in result["items"])
    assert all(i["counted"] is False for i in result["items"]), (
        "正误题只做等值化标尺，不该进读数"
    )
    assert result["method_score"] is None
    assert "标尺题" in result["method_detail"]


def test_a_multi_blank_question_is_scored_by_blank(patched_bank) -> None:
    """一题两空按「对=10/错=0」给分会让"两空全对"与"只对一空"同分。"""

    def add_two_blank(raw: dict) -> None:
        raw["exact_questions"].append(
            {
                "no": 2,
                "type": "fill",
                "question": "A-1：两个空 —— 水深与 pH。",
                "answer": "350；2-3",
            }
        )

    bank = patched_bank("A-1", add_two_blank)
    app = _client_for(bank)
    with TestClient(app) as client:
        run_id = _start(client, ["A-1"])
        _answer(
            client,
            run_id,
            question_no=2,
            kind="exact_blank",
            judged_by="code",
            raw_answer="350；9",
        )
        _use(app)
        result = _score(client, run_id)
        scored = next(i for i in result["items"] if i["question_no"] == 2)
        assert scored["item_score"] == 5.0
        assert scored["scoring_method"] == "code:exact_blank(1/2 空)"


def test_a_question_and_its_kind_can_disagree(patched_bank) -> None:
    """记成客观题、题目却是两空填空时，**按题目的真实结构算**，并把分歧说出来。

    ★ 反过来（按记录的 `kind` 算）会让一题两空退化成"对=10/错=0"，
    而那是**分数失去分辨力**，不会报错。
    """

    def add_two_blank(raw: dict) -> None:
        raw["exact_questions"].append(
            {"no": 2, "type": "fill", "question": "两空", "answer": "甲；乙"}
        )

    bank = patched_bank("A-1", add_two_blank)
    app = _client_for(bank)
    with TestClient(app) as client:
        run_id = _start(client, ["A-1"])
        _answer(
            client,
            run_id,
            question_no=2,
            kind="exact",
            judged_by="code",
            raw_answer="甲；丙",
        )
        _use(app)
        result = _score(client, run_id)
        scored = next(i for i in result["items"] if i["question_no"] == 2)
        assert scored["item_score"] == 5.0
        assert any("按题目的真实结构算分" in note for note in result["notes"])


# --------------------------------------------------------------------------- #
# 观测题：换算表
# --------------------------------------------------------------------------- #


def _observation_bank(patched_bank) -> Path:
    def add_table(raw: dict) -> None:
        raw["scoring"] = {
            "rule": "observation_table",
            "weight_source": "difficulty",
            "observation_table": {
                "table_id": "fixture-hsr-1",
                "unit": "秒",
                "bands": [
                    {"upper": 60, "score": 10.0, "label": "很快"},
                    {"upper": 180, "score": 6.0, "label": "一般"},
                    {"upper": None, "score": 1.0, "label": "很慢"},
                ],
            },
        }

    return patched_bank("A-1", add_table)


def test_observation_maps_through_the_declared_table(patched_bank) -> None:
    app = _client_for(_observation_bank(patched_bank))
    with TestClient(app) as client:
        run_id = _start(client, ["A-1"])
        _answer(client, run_id, kind="observation", judged_by="code", raw_answer="30")
        _use(app)
        result = _score(client, run_id)
        scored = result["items"][0]
        assert scored["item_score"] == 10.0
        assert "fixture-hsr-1" in scored["scoring_method"]
        assert scored["counted"] is True, "行为观测是读数（与正误题不同）"


def test_an_unparseable_observation_is_void_not_the_lowest_score(patched_bank) -> None:
    """★ 「好像挺快的」不是 0 分也不是 1 分 —— 它是**没测到**。

    把解析失败当最低分，会让一次没测到的观测变成一个真实的低分，
    而两者在界面上长得一样。
    """
    app = _client_for(_observation_bank(patched_bank))
    with TestClient(app) as client:
        run_id = _start(client, ["A-1"])
        _answer(client, run_id, kind="observation", judged_by="code", raw_answer="好像挺快的")
        _use(app)
        result = _score(client, run_id)
        scored = result["items"][0]
        assert scored["item_score"] is None
        # ★ 作废理由必须说出**是哪一种**作废：这里是"读不出数"，
        # 而不是"没有卡点" —— 两者都会让这题没有分数，含义却相反。
        assert scored["void_reason"] and "读不出" in scored["void_reason"]
        assert "未测到" in scored["scoring_method"]


def test_an_observation_without_a_table_is_skipped_with_a_reason(client: TestClient, app) -> None:
    """★ 题目没声明换算表时**不猜**：换算表是一个测量设计决定，
    不是一个可以推断的默认值。未计分，并把原因说出来。"""
    run_id = _start(client, ["A-1"])
    _answer(client, run_id, kind="observation", judged_by="code", raw_answer="30")
    _use(app)
    result = _score(client, run_id)
    scored = result["items"][0]
    assert scored["item_score"] is None
    assert "换算表" in (scored["judge_note"] or scored["void_reason"] or "")
    assert any("换算表" in note for note in result["notes"])


# --------------------------------------------------------------------------- #
# scoring_method 真的落库了
# --------------------------------------------------------------------------- #


def test_the_scoring_method_is_persisted_with_the_score(app, client: TestClient) -> None:
    """★ 这一列的全部意义是"半年后仍能回答这个分是哪条规则算的"。"""
    run_id = _start(client, ["A-1", "A-2", "R-E0"])
    _answer(client, run_id, raw_answer=_answer_of("A-1"))
    _use(app, '{"hits": [{"no": 1, "quote": "%s"}]}' % _guide_of("A-1")[0])
    _score(client, run_id)

    from app.db import session_scope
    from app.global_domain import reads

    with session_scope(app.state.session_factory) as session:
        rows = reads.list_benchmark_responses(session, run_id)
        row = next(r for r in rows if r.item_id == "A-1")
        assert row.scoring_method and "llm:key_points" in row.scoring_method
        assert row.judged_hits
        assert row.judge_model  # 判分可复现的前提


def test_rescoring_does_not_touch_the_answer(app, client: TestClient) -> None:
    """判分变了要能看出来**是判分变了**，不是他改了答案。"""
    run_id = _start(client, ["A-1"])
    text = _answer_of("A-1")
    _answer(client, run_id, raw_answer=text)
    _use(app, '{"hits": []}', '{"hits": [{"no": 1, "quote": "%s"}]}' % _guide_of("A-1")[0])
    first = _score(client, run_id)
    second = _score(client, run_id)
    assert first["items"][0]["item_score"] != second["items"][0]["item_score"]

    from app.db import session_scope
    from app.global_domain import reads

    with session_scope(app.state.session_factory) as session:
        row = reads.list_benchmark_responses(session, run_id)[0]
        assert row.raw_answer == text, "重算分数不许改动作答"


# --------------------------------------------------------------------------- #
# 聚合与收尾
# --------------------------------------------------------------------------- #


def test_a_run_below_the_minimum_gets_no_method_score(app, client: TestClient) -> None:
    run_id = _start(client, ["A-1"])
    _answer(client, run_id, raw_answer=_answer_of("A-1"))
    _use(app, '{"hits": [{"no": 1, "quote": "%s"}]}' % _guide_of("A-1")[0])
    result = _score(client, run_id)
    assert result["method_score"] is None
    assert result["completeness"] == "partial"
    assert result["confidence"] == "hypothesis"
    assert result["confidence_reason"]


def test_enough_readings_produce_a_method_score(app, client: TestClient) -> None:
    run_id = _start(client, ["A-1", "A-2", "R-E0"])
    replies = []
    for item_id in ("A-1", "A-2", "R-E0"):
        _answer(client, run_id, item_id=item_id, raw_answer=_answer_of(item_id))
        guide = _guide_of(item_id)
        replies.append(
            '{"hits": [{"no": 1, "quote": "%s"}, {"no": 2, "quote": "%s"}]}'
            % (guide[0], guide[1])
        )
    _use(app, *replies)
    result = _score(client, run_id)
    assert result["method_score"] is not None
    assert result["completeness"] == "complete"
    assert result["channel_score"] == result["method_score"], (
        "一次运行只有一个方法，通道分就是方法分（合成规则只在多方法时才有区别）"
    )


def test_anchors_are_reported_in_the_confidence_reason(app, client: TestClient) -> None:
    """锚题是等值化的分母 —— 它必须出现在"为什么是这个置信档"里。"""
    run_id = _start(client, ["A-1", "A-2", "R-E0"])
    replies = []
    for item_id in ("A-1", "A-2", "R-E0"):
        _answer(client, run_id, item_id=item_id, raw_answer=_answer_of(item_id))
        guide = _guide_of(item_id)
        replies.append(
            '{"hits": [{"no": 1, "quote": "%s"}, {"no": 2, "quote": "%s"}]}'
            % (guide[0], guide[1])
        )
    _use(app, *replies)
    result = _score(client, run_id)
    assert "锚题" in result["confidence_reason"]


def test_the_notes_say_what_is_not_implemented_yet(app, client: TestClient) -> None:
    """★ 没做的事情要**自己说出来** —— 否则"有方法分"会被读成"等值化也做了"。

    ★ P15 改过一次口径：P12 之前这里钉的是「响应里必须有一句**等值化尚未实现**」。
    等值化做出来之后那句话没人删，于是同一个响应里一边说"尚未实现"、
    一边给出算好的 `shift` —— 两句话隔着几行对着看（**跑真链路时抓出来的**：
    `tools/runbench.py` 把整份报告打印出来）。
    现在钉的是**相反**的事实：这句话必须由**当场算出来的**等值化结果来说，
    而且要正面回答"校正了没有" —— 包括"校正不了，原因是……"。
    """
    run_id = _start(client, ["A-1"])
    _answer(client, run_id, raw_answer=_answer_of("A-1"))
    _use(app, '{"hits": []}')
    result = _score(client, run_id)

    assert any("等值化" in note for note in result["notes"])
    assert result["equating"] is not None
    # 这句话与 `equating` 是**同一个来源**：不会出现"描述说没做、字段里有值"
    assert result["equating"]["description"] in result["notes"]


def test_scoring_a_run_with_no_answers_is_refused(app, client: TestClient) -> None:
    run_id = _start(client, ["A-1"])
    _use(app)
    response = client.post(f"/api/benchmark/runs/{run_id}/score")
    assert response.status_code == 422
    assert "没有任何作答" in response.json()["message"]


def test_scoring_an_abandoned_run_is_refused(app, client: TestClient) -> None:
    """作废的运行保留记录，但不产出**新的**读数。"""
    run_id = _start(client, ["A-1"])
    _answer(client, run_id, raw_answer=_answer_of("A-1"))
    client.post(f"/api/benchmark/runs/{run_id}/status", json={"target": "abandoned"})
    _use(app)
    response = client.post(f"/api/benchmark/runs/{run_id}/score")
    assert response.status_code == 422
    assert "作废" in response.json()["message"]


# --------------------------------------------------------------------------- #
# /finish：服务端算分，且**不重新判分**
# --------------------------------------------------------------------------- #


def test_finish_fills_the_scores_the_caller_did_not_give(app, client: TestClient) -> None:
    run_id = _start(client, ["A-1", "A-2", "R-E0"])
    replies = []
    for item_id in ("A-1", "A-2", "R-E0"):
        _answer(client, run_id, item_id=item_id, raw_answer=_answer_of(item_id))
        guide = _guide_of(item_id)
        replies.append(
            '{"hits": [{"no": 1, "quote": "%s"}, {"no": 2, "quote": "%s"}]}'
            % (guide[0], guide[1])
        )
    _use(app, *replies)
    scored = _score(client, run_id)
    response = client.post(
        f"/api/benchmark/runs/{run_id}/finish",
        json={"completeness": "complete", "boundary": ""},
    )
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["method_score"] == scored["method_score"]
    assert run["channel_score"] == scored["channel_score"]
    assert run["confidence"] == scored["confidence"]
    assert run["status"] == "finished"


def test_finish_does_not_ask_the_judge_again(app, client: TestClient) -> None:
    """★ 重新判分意味着他看到的 7.5 与库里存的 6.8 可能不一样。"""
    run_id = _start(client, ["A-1", "A-2", "R-E0"])
    replies = []
    for item_id in ("A-1", "A-2", "R-E0"):
        _answer(client, run_id, item_id=item_id, raw_answer=_answer_of(item_id))
        guide = _guide_of(item_id)
        replies.append(
            '{"hits": [{"no": 1, "quote": "%s"}, {"no": 2, "quote": "%s"}]}'
            % (guide[0], guide[1])
        )
    fake = _use(app, *replies)
    scored = _score(client, run_id)
    asked_before = len(fake.seen)
    client.post(
        f"/api/benchmark/runs/{run_id}/finish",
        json={"completeness": "complete", "boundary": ""},
    )
    assert len(fake.seen) == asked_before, "收尾不许再问判官"


def test_an_explicit_score_still_wins(app, client: TestClient) -> None:
    """复现与测试路径：调用方给了就以它为准。"""
    run_id = _start(client, ["A-1"])
    response = client.post(
        f"/api/benchmark/runs/{run_id}/finish",
        json={
            "method_score": 4.5,
            "channel_score": 4.5,
            "confidence": "provisional",
            "completeness": "complete",
            "boundary": "外部校准过",
        },
    )
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["method_score"] == 4.5
    assert run["confidence"] == "provisional"
    assert run["boundary"] == "外部校准过"


def test_responses_can_still_carry_a_caller_computed_score(app, client: TestClient) -> None:
    """★ 保留"我说了算"的路径 —— 但它与"服务端算"不能同时生效。"""
    run_id = _start(client, ["A-1"])
    _answer(client, run_id, raw_answer="随便", item_score=8.0)
    _use(app, '{"hits": []}')
    result = _score(client, run_id)
    # /score 会按规则重算，覆盖调用方给的值 —— 因为规则只有一个入口
    assert result["items"][0]["item_score"] == 0.0


# --------------------------------------------------------------------------- #
# 等值化校正（P12）
# --------------------------------------------------------------------------- #


def _payload(item_id: str, hits: int, root: Path | None = None) -> str:
    """判官对**这一道题**的回复：报前 `hits` 条命中，每条都引原话。"""
    guide = _guide_of(item_id, root)
    return "{" + '"hits": [' + ", ".join(
        '{"no": %d, "quote": "%s"}' % (i + 1, guide[i]) for i in range(hits)
    ) + "]}"


def _run_with_anchors(
    app,
    client: TestClient,
    *,
    seed: int,
    hits: int,
    items: tuple[str, ...] = ("A-1", "A-2"),
    bank_root: Path | None = None,
) -> dict:
    """开一次运行、把每道锚题都答上、判官给 `hits` 条命中，然后计分。

    ★ 判官脚本按**作答顺序**逐题给（`FakeLLM` 是顺序消费的），
    且每道题引的是**它自己那份指南**的原话 —— 拿 A-1 的句子去答 A-2，
    引文核对会（正确）不认，于是所有题都得 0 分，而"得 0 分"看起来只是他答得差。
    """
    run_id = _start(client, list(items), seed=seed)
    for item_id in items:
        _answer(
            client, run_id, item_id=item_id, raw_answer=_answer_of(item_id, bank_root)
        )
    _use(app, *[_payload(item_id, hits, bank_root) for item_id in items])
    return _score(client, run_id)


@pytest.fixture()
def wide_bank(tmp_path: Path):
    """复制一份夹具题库，并**多出一道锚题**（`A-3`）。

    ★ 为什么需要它：方法分要**至少三道**读数题才给（`MIN_ITEMS_DEFAULT = 3`），
    而夹具里只有两道要点题。想看"校正真的落在分数上"，就得有三道。
    ★ 为什么不直接往共享夹具里加：那会动到抽题候选集，
    以及每个用数量断言钉住它的测试 —— 拿一个更大的不确定性换一个小确定性。
    """
    import json
    import shutil

    bank = tmp_path / "bank"
    shutil.copytree(FIXTURE_ROOT / "bank", bank)
    shutil.copytree(FIXTURE_ROOT / "materials", tmp_path / "materials")
    raw = json.loads((bank / "A-1.json").read_text(encoding="utf-8"))
    raw["item_id"] = "A-3"
    (bank / "A-3.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # 材料按 `<item_id>.md` 兜底查找 —— 不复制的话这道题会因为"材料缺失"加载失败
    shutil.copy(tmp_path / "materials" / "A-1.md", tmp_path / "materials" / "A-3.md")
    return bank


def test_the_first_run_establishes_the_baseline_and_is_not_corrected(
    app, client: TestClient
) -> None:
    """★ 第一次用这道锚题时**无从校正** —— 它自己成为刻度原点。

    这时若显示"已校正 0.00"，一个从没有过基线的系统会声称自己校正过了。
    `shift` 因此是 `None`（量不了），不是 `0.0`（量过了，没漂）。
    """
    first = _run_with_anchors(app, client, seed=21, hits=3)
    assert first["equating"]["shift"] is None
    assert "没有基线" in first["equating"]["reason"]
    assert first["equating"]["anchors"] == []


def test_a_second_run_is_corrected_against_the_first(
    app, client: TestClient
) -> None:
    """★ 校正量、参与校正的锚题、基线读数，一次运行里全都要看得见。

    夹具的锚题都按 `10 × 命中/3` 计分：第一次三条全中（10.0），
    第二次只认一条（3.33）→ 校正量 `10 - 3.33 = 6.67`。
    """
    _run_with_anchors(app, client, seed=22, hits=3)
    worse = _run_with_anchors(app, client, seed=23, hits=1)

    assert worse["equating"]["shift"] == pytest.approx(6.67, abs=0.01)
    assert {a["item_id"] for a in worse["equating"]["anchors"]} == {"A-1", "A-2"}
    for anchor in worse["equating"]["anchors"]:
        assert anchor["baseline"] == pytest.approx(10.0)
        assert anchor["current"] == pytest.approx(3.33, abs=0.01)
        assert anchor["delta"] == pytest.approx(6.67, abs=0.01)
    # 依据的假设必须跟着数字一起出来
    assert "假设" in worse["equating"]["description"]
    assert any("校正" in note for note in worse["notes"])


def test_the_baseline_comes_from_the_earliest_run_not_the_latest(
    app, client: TestClient
) -> None:
    """★ 基线**不能漂**：用它校正漂移的东西自己漂了，全部历史可比性同时失效。"""
    runs = [
        _run_with_anchors(app, client, seed=seed, hits=hits)
        for seed, hits in ((31, 3), (32, 2), (33, 1))
    ]
    third = runs[-1]
    # 基线仍取自**第一次**（10.0），而不是第二次（6.67）
    for anchor in third["equating"]["anchors"]:
        assert anchor["baseline"] == pytest.approx(10.0)
    assert third["equating"]["baseline_runs"] == [runs[0]["run_id"]]


def test_a_single_anchor_is_not_enough_to_correct(app, client: TestClient) -> None:
    """★ 一道锚题的波动分不清是能力还是仪器 —— 那时**不校正**，并说明为什么。"""
    _run_with_anchors(app, client, seed=41, hits=3, items=("A-1",))
    result = _run_with_anchors(app, client, seed=42, hits=1, items=("A-1",))
    assert result["equating"]["shift"] is None
    assert "只有 1 道" in result["equating"]["reason"]
    # 证据仍然给出来：不校正 ≠ 不报告
    assert len(result["equating"]["anchors"]) == 1
    assert result["equating"]["anchors"][0]["delta"] == pytest.approx(6.67, abs=0.01)


def test_the_correction_reaches_the_method_score_without_rewriting_it(
    app, wide_bank: Path
) -> None:
    """★ 端到端：校正**真的落在分数上**，而**原始分不被改写**。

    直接把 shift 加进 `method_score` 看起来更"干净"，
    但它让"当时是多少分"这个问题永远失去了答案 ——
    而半年后回看时，那个问题恰恰是最要紧的。
    """
    wide = _client_for(wide_bank)
    items = ("A-1", "A-2", "A-3")
    with TestClient(wide) as wide_client:
        first = _run_with_anchors(
            wide, wide_client, seed=61, hits=3, items=items, bank_root=wide_bank
        )
        assert first["method_score"] == pytest.approx(10.0)
        assert first["equating"]["shift"] is None

        worse = _run_with_anchors(
            wide, wide_client, seed=62, hits=1, items=items, bank_root=wide_bank
        )
        assert worse["method_score"] == pytest.approx(3.33, abs=0.01)  # 原始分
        assert worse["equating"]["shift"] == pytest.approx(6.67, abs=0.01)
        assert worse["equated_score"] == pytest.approx(10.0, abs=0.01)  # 校正后

        # 第一次运行的分**没有被改写**
        # （重判要重新问判官 —— `/score` 的语义就是"重新判一次"，脚本要续上）
        _use(wide, *[_payload(item_id, 3, wide_bank) for item_id in items])
        again = _score(wide_client, first["run_id"])
        assert again["method_score"] == pytest.approx(10.0)
        assert again["equated_score"] == pytest.approx(10.0)
