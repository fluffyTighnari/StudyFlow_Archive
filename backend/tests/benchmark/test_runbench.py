"""`tools/runbench.py` 的**纯函数**部分：出卷 / 收答案 / 写报告。

它不上网（那一半靠真的跑一次证明），所以这里钉的是**三件会骗人的事**：

| 会骗人的失败态 | 测试 |
|---|---|
| 没答的题被上报成 0 分 | `test_unanswered_questions_are_not_reported` |
| `0.0` 与「没测到」在报告里长得一样 | `test_the_report_keeps_zero_and_not_measured_apart` |
| 题库里缺一道题时静默丢掉（卷子看起来更小、分数看起来更高） | `test_a_missing_item_stays_in_the_paper` |

★ 还有一个更根本的口径：**这个工具不替人答题** ——
`build_paper` 出来的每一格答案都是空串（`test_the_paper_comes_back_empty`）。

★ 题目正文与问法**用的是运行时那个加载器**（`app.benchmark.items.load_bank`），
不另写一份解析 —— 两份判断的下场是「校验器放行的题，出卷工具读不懂」。
所以这里的夹具是**真的题目 JSON**，不是手搓的 dict。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.benchmark.items import BenchItem, load_item
from tools import runbench

PLAN = {
    "variable_id": "V-SE",
    "channel": "measured",
    "method_id": "M-SE-04",
    "form": "quick",
    "seed": 7,
    "summary": "抽了 2 道题",
    "notes": ["锚题 1 道"],
    "topics": ["T-001"],
    "items": [
        {"item_id": "A-1", "role": "anchor", "difficulty": "中", "topics": ["T-001"], "why": "锚题"},
        {"item_id": "A-9", "role": "normal", "difficulty": "易", "topics": ["T-001"], "why": "补难度"},
    ],
}

ITEM_JSON = {
    "item_id": "A-1",
    "variable_id": "V-SE",
    "channel": "measured",
    "method_id": "M-SE-04",
    "role": "anchor",
    "difficulty": "中",
    "topics": ["BIO-MARINE"],
    "time_min": 6,
    "language": "zh",
    "media": "text",
    "prompt": "读下面这篇材料，用三句话概括",
    "material": "这里是一段材料正文。",
    "answer": {"key_points": ["要点一", "要点二"]},
    "scoring": {"key_points": 10},
    "source": "fixture",
    "license": "internal",
    "status": "active",
    "anchors": {"low": "低", "mid": "中", "high": "高"},
    "key_points_question": {
        "type": "key_points",
        "prompt": "三句话概括：主线 / 一个关键事实 / 一个限定条件",
        "scoring_guide": ["要点一", "要点二"],
    },
    "exact_questions": [
        {
            "no": 1,
            "type": "choice",
            "question": "限定条件是什么？",
            "options": ["只在某范围内成立", "没有限定条件"],
            "answer": "只在某范围内成立",
        }
    ],
}


@pytest.fixture()
def bank_dir(tmp_path: Path) -> Path:
    """一个**能过运行时加载器**的最小题库：`bank/A-1.json` + `materials/A-1.md`。

    ★ 刻意用真的题目 JSON + 真的材料文件：手搓一个 dict 只能测出
    "我按我以为的形状读对了"，而这里的风险恰恰是"我以为的形状与加载器不一样"。
    """
    items = tmp_path / "bank"
    materials = tmp_path / "materials"
    items.mkdir()
    materials.mkdir()
    (items / "A-1.json").write_text(
        json.dumps(ITEM_JSON, ensure_ascii=False), encoding="utf-8"
    )
    (materials / "A-1.md").write_text("这里是一段材料正文。", encoding="utf-8")
    return items


@pytest.fixture()
def item(bank_dir: Path) -> BenchItem:
    return load_item(bank_dir / "A-1.json", materials_dir=bank_dir.parent / "materials")


def test_the_paper_comes_back_empty(item: BenchItem) -> None:
    """★ 每一格答案都是空串 —— 这个工具准备一份卷子，不替人答一份。

    给它一个默认答案的后果不是"省事"：分数会建立在一份**没人做过**的卷子上，
    而那与"他做了"在库里长得一样。
    """
    paper = runbench.build_paper(PLAN, {"A-1": item}, run_id="BR-0001")
    assert [q["answers"] for q in paper["questions"]] == [{"0": "", "1": ""}, {}]
    assert "空串" not in paper["how_to_answer"]  # 口径换成了"逐个问的格子"
    assert "空格子" in paper["how_to_answer"]

    # ★ 反过来钉一次：这个模块里没有任何"参考答案"的来源。
    source = Path(runbench.__file__).read_text(encoding="utf-8")
    assert item.raw["exact_questions"][0]["answer"] not in source


def test_each_question_gets_its_own_slot(item: BenchItem) -> None:
    """★ **一个问一格**，不是一个题一格。

    这道题有"要点题（问 0）+ 对错题（问 1）"两个问。合成一格之后，
    交卷时同一段话会被当成两个问的答案上报 —— 对错题必然判错，
    而报告上看起来只是"他答错了"，没有人会怀疑格式。
    （这个缺陷是**跑真链路时**抓出来的：第一版卷子只有 `answer` 一格。）
    """
    paper = runbench.build_paper(PLAN, {"A-1": item}, run_id="BR-0001")
    assert paper["questions"][0]["answers"] == {"0": "", "1": ""}
    assert [p["question_no"] for p in paper["questions"][0]["prompts"]] == [0, 1]


def test_the_paper_carries_the_question_text_and_the_reason_it_was_picked(
    item: BenchItem,
) -> None:
    """卷子要能直接打印出来答：材料 + 问法都在；`why` 也留着。"""
    paper = runbench.build_paper(PLAN, {"A-1": item}, run_id="BR-0001")
    first = paper["questions"][0]
    assert first["material"] == "这里是一段材料正文。"
    assert first["prompt"] == "读下面这篇材料，用三句话概括"
    assert first["prompts"] == [
        {
            "question_no": 0,
            "kind": "key_points",
            "text": "三句话概括：主线 / 一个关键事实 / 一个限定条件",
        },
        {
            "question_no": 1,
            "kind": "exact",
            "text": "限定条件是什么？",
            "options": ["只在某范围内成立", "没有限定条件"],
        },
    ]
    assert first["why"] == "锚题"
    assert paper["run_id"] == "BR-0001"


def test_a_missing_item_stays_in_the_paper(item: BenchItem) -> None:
    """★ 题库里缺一道题时**不能静默丢掉**。

    丢掉一道会让卷子看起来比真实的小，而分数照常算出来 ——
    于是"他答了全部题"是一个假象，且没有任何症状。
    """
    paper = runbench.build_paper(PLAN, {"A-1": item}, run_id="BR-0001")
    missing = paper["questions"][1]
    assert missing["item_id"] == "A-9"
    assert missing["missing_item"] and "A-9" in missing["missing_item"]
    assert missing["answers"] == {}


def test_unanswered_questions_are_not_reported(item: BenchItem) -> None:
    """★ 没答的问**不上报**。

    上报一条空作答，在库里与"他答了但没测到"长得一样 ——
    而"他没做这道题"与"他做了、仪器没量出来"必须分得清。
    """
    paper = runbench.build_paper(PLAN, {"A-1": item}, run_id="BR-0001")
    first = paper["questions"][0]
    first["answers"] = {
        "0": "主线是…；关键事实是…；限定条件是…",
        "1": "只在某范围内成立",
    }

    payloads, unanswered = runbench.collect_answers(paper)

    # ★ 没答的以**问**为单位报：整道 A-9 没做，就是一格
    assert unanswered == ["A-9"]
    assert [p["item_id"] for p in payloads] == ["A-1", "A-1"]
    assert {p["question_no"] for p in payloads} == {0, 1}
    assert {p["kind"] for p in payloads} == {"key_points", "exact"}
    # ★ 两个问各带各的答案 —— 这正是"一个问一格"要保证的事
    by_no = {p["question_no"]: p["raw_answer"] for p in payloads}
    assert by_no[0] == "主线是…；关键事实是…；限定条件是…"
    assert by_no[1] == "只在某范围内成立"
    # ★ 分数**不由这里算**：`item_score` 必须缺省，让服务端算。
    assert all("item_score" not in p for p in payloads)


def test_an_unanswered_question_inside_an_answered_item_is_named(
    item: BenchItem,
) -> None:
    """一道题里答了一半：报出来的是**哪一个问**空着，不是"这道题没做"。"""
    paper = runbench.build_paper(PLAN, {"A-1": item}, run_id="BR-0001")
    paper["questions"][0]["answers"] = {"0": "写了要点", "1": "   "}

    payloads, unanswered = runbench.collect_answers(paper)

    assert [p["question_no"] for p in payloads] == [0]
    assert unanswered == ["A-1 问1", "A-9"]


def test_a_whitespace_answer_counts_as_unanswered(item: BenchItem) -> None:
    """只敲了空格 / 回车不算答了 —— 那是"我以为我写了"。"""
    paper = runbench.build_paper(PLAN, {"A-1": item}, run_id="BR-0001")
    paper["questions"][0]["answers"] = {"0": "   \n  ", "1": ""}
    _, unanswered = runbench.collect_answers(paper)
    assert unanswered == ["A-1 问0", "A-1 问1", "A-9"]


def test_the_report_keeps_zero_and_not_measured_apart() -> None:
    """★ `0.0` 与「没测到」是两种写法，两种含义。

    一个没分的题与一个 0 分的题在界面上长得一样，而含义完全相反：
    前者是仪器没量出来（或这道题作废），后者是他真的得了 0。
    """
    score = {
        "run_id": "BR-0001",
        "method_score": 0.0,
        "equated_score": None,
        "confidence": "hypothesis",
        "confidence_reason": "只有一道题",
        "items": [
            {
                "item_id": "A-1",
                "question_no": 0,
                "kind": "key_points",
                "item_score": 0.0,
                "scoring_method": "code:key_points",
                "counted": True,
                "void_reason": None,
            },
            {
                "item_id": "A-2",
                "question_no": 0,
                "kind": "observation",
                "item_score": None,
                "scoring_method": None,
                "counted": False,
                "void_reason": "没记到切换次数",
            },
            {
                "item_id": "A-3",
                "question_no": 0,
                "kind": "exact",
                "item_score": None,
                "scoring_method": "code:exact",
                "counted": False,
                "void_reason": None,
            },
        ],
        "skipped": ["A-2：观测量缺失"],
        "notes": ["等值化：没有基线"],
    }

    report = runbench.render_report(score)

    assert "方法分（原始）：0.0" in report
    assert "A-1 问0 key_points：0.0 · code:key_points" in report
    assert "A-2 问0 observation：没测到（没记到切换次数）（不进读数）" in report
    # ★ 没有原因的那种必须自己承认是缺陷 —— 不能显示成"0 分"混过去
    assert "A-3 问0 exact：没测到（没有原因，这是缺陷）" in report
    assert "没参与计分的题：" in report
    assert "等值化：没有基线" in report


def test_a_void_score_is_reported_as_not_measured() -> None:
    """方法分是 `NULL` 时报告写「没测到」，**不写 0**。"""
    report = runbench.render_report(
        {
            "run_id": "BR-0002",
            "method_score": None,
            "confidence": None,
            "confidence_reason": "没有可用的题",
            "items": [],
        }
    )
    assert "方法分（原始）：没测到" in report
    assert "0.0" not in report


def test_equating_comes_with_the_assumption_it_rests_on() -> None:
    """校正过就必须把那句假设一起显示 —— 否则读的人以为 6.8 是量出来的。

    ★ `reason` 与 `description` 不一样时**两句都要出现**：
    `reason` 是"为什么补这么多"，`description` 里那句假设是
    "这个数凭什么成立"。只显示前者的后果是读的人把它当成量出来的。
    """
    report = runbench.render_report(
        {
            "run_id": "BR-0003",
            "method_score": 3.33,
            "equated_score": 6.66,
            "equating": {
                "shift": 3.33,
                "reason": "锚题比基线低 3.33",
                "description": "前提：他对锚题的能力与基线时相同",
            },
            "confidence": "provisional",
            "confidence_reason": "含锚题",
            "items": [],
        }
    )
    assert "等值化后：6.66（锚题比基线低 3.33）" in report
    assert "前提：他对锚题的能力与基线时相同" in report


def test_the_same_sentence_is_not_printed_twice() -> None:
    """两句话相同时只打一遍 —— 打两遍会让人以为有两处独立的结论。

    （第一版就是这样：`reason` 与 `description` 在"校正不了"那条路上
    本来就是同一句，于是报告里同一句话连着出现两次。跑真链路时看到的。）
    """
    sentence = "等值化：未校正 —— 没有基线就无从校正"
    report = runbench.render_report(
        {
            "run_id": "BR-0004",
            "method_score": 3.33,
            "equated_score": 3.33,
            "equating": {"shift": None, "reason": sentence, "description": sentence},
            "confidence": "provisional",
            "confidence_reason": "含锚题",
            "items": [],
        }
    )
    assert report.count(sentence) == 1


def test_load_bank_uses_the_runtime_loader(bank_dir: Path) -> None:
    """出卷用的是**运行时那个加载器** —— 它认的题与能跑分的题因此是同一批。"""
    bank = runbench.load_bank(bank_dir)
    assert set(bank) == {"A-1"}
    assert bank["A-1"].item_id == "A-1"
    # 材料也是加载器读出来的（不是这里另找一遍）
    assert "材料正文" in bank["A-1"].material_text


def test_a_broken_item_file_stops_the_paper_instead_of_being_skipped(
    bank_dir: Path,
) -> None:
    """★ 坏文件**停下**，不跳过。

    跳过它的后果：卷子少一格、分数照常算出来，而"他答了全部题"是个假象。
    一个会自己吞掉错误的出卷工具有一个非常坏的性质 ——
    它产出的卷子看起来完全正常。
    """
    (bank_dir / "broken.json").write_text("{ 这不是 JSON", encoding="utf-8")
    with pytest.raises(ValueError):
        runbench.load_bank(bank_dir)
