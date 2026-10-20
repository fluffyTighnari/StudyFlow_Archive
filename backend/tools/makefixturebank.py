"""生成一份**完整的**小题库，作为夹具（不是产品内容）。

## 为什么要有它

仓库里那份 `items/` 是**外部信息·渐进补**的内容，它现在缺规格字段
（`variable_id` / `channel` / `method_id` / `role` / `scoring` / `source` /
`license` / `status`）。那意味着：**抽题机制做完了没有，无法靠跑一次来回答** ——
只能靠人说"我做完了"。

一份完整的小题库把这件事变成可验证的：
- 单测与 `e2echeck` 都指向**同一个目录**（两份夹具一定会漂移）；
- 起服务时用 `--items-dir tests/fixtures/bank`，抽题就能真的跑起来。

这份夹具刻意做小（10 道题）但**每一档的候选都比名额多** ——
否则"名额定死了谁上"，抽题优先级就测不到。

★ 它不是产品题库：题面是占位文字，`source` 写着"夹具"。
产品题库是另一件事（外部信息，渐进补）。
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
BANK = ROOT / "tests" / "fixtures" / "bank"
MATERIALS = ROOT / "tests" / "fixtures" / "materials"

#: `(item_id, role, difficulty, topics)`
SPEC = [
    ("A-1", "anchor", "中", ["BIO-MARINE"]),
    ("A-2", "anchor", "易", ["EARTH-GEOL"]),
    ("R-E0", "regular", "易", ["PHYS-QUANTUM"]),
    ("R-E1", "regular", "易", ["CHEM-ORGANIC"]),
    ("R-M0", "regular", "中", ["MATH-STAT"]),
    ("R-M1", "regular", "中", ["LIT-POETRY"]),
    ("R-M2", "regular", "中", ["CS-ALGO"]),
    ("R-M3", "regular", "中", ["HIST-ANCIENT"]),
    ("R-H0", "regular", "难", ["PSY-COG"]),
    ("R-H1", "regular", "难", ["LAW-IP"]),
]


def evidence(item_id: str) -> dict[str, str]:
    """夹具的"埋点证据句" —— **材料正文由这些句子拼出来**。

    ★ 这不是为了好看：`tools/checkitems.py` 会核对**每条引文都真的出现在材料里**，
    而这条核对正是判分能用得上引文的前提（Benchmark v0.3 §5.2.1）。
    夹具若自带"引文对不上"的毛病，它就会变成一个**天天报红的工具**，
    下场是没人再看它 —— 于是真题库出问题时也没人看。
    """
    return {
        "main": f"{item_id} 的主线证据句：这条主线说的是什么。",
        "fact": f"{item_id} 的事实证据句：一个关键事实与它的数据。",
        "limit": f"{item_id} 的限定条件证据句：结论只在某个范围内成立。",
    }


def material_text(item_id: str) -> str:
    lines = evidence(item_id)
    return (
        f"# {item_id} 的材料（夹具）\n\n"
        f"这是一篇用于验证抽题机制的占位材料，它不构成产品题库的一部分。\n\n"
        f"{lines['main']}\n\n"
        f"{lines['fact']}\n\n"
        f"{lines['limit']}\n"
    )


def key_points(item_id: str) -> list[str]:
    lines = evidence(item_id)
    return [lines["main"], lines["fact"], lines["limit"]]


def item_json(item_id: str, role: str, difficulty: str, topics: list[str]) -> dict:
    lines = evidence(item_id)
    return {
        "item_id": item_id,
        "variable_id": "V-SE",
        "channel": "measured",
        "method_id": "M-SE-04",
        "role": role,
        "difficulty": difficulty,
        "topics": topics,
        "time_min": 6,
        "language": "zh",
        "media": "text",
        "prompt": "读下面这篇材料，用三句话概括：① 主线 ② 三个关键事实 ③ 一个易忽略的限定条件",
        "answer": {"key_points": key_points(item_id)},
        "scoring": {"rule": "key_points", "weight_source": "difficulty"},
        "source": "夹具（不是产品题库）",
        "license": "个人使用",
        "status": "verified",
        "anchors": {
            "main_thread": {
                "statement": f"{item_id} 的主线陈述",
                "evidence_sentences": [lines["main"]],
            },
            "key_facts": [
                {"fact": f"{item_id} 的一个关键事实", "evidence_sentences": [lines["fact"]]}
            ],
            "limit_conditions": [
                {
                    "condition": f"{item_id} 的一个限定条件",
                    "evidence_sentences": [lines["limit"]],
                }
            ],
        },
        "key_points_question": {
            "type": "key_points",
            "prompt": "三句话概括：主线 / 一个关键事实 / 一个限定条件",
            "scoring_guide": key_points(item_id),
        },
        "exact_questions": [
            {
                "no": 1,
                "type": "choice",
                "question": f"{item_id}：这篇材料的限定条件是什么？",
                "options": [lines["limit"], "没有限定条件", "所有结论都无条件成立"],
                "answer": lines["limit"],
            }
        ],
    }


def self_report_item() -> dict:
    """一道**自评量表**（P11）—— 没有材料、没有埋点、没有对错。

    ★ 它存在的理由是**证明"没有材料的题"是合法的**：在此之前
    规格是一张所有题共用的必填表（anchors / key_points_question /
    exact_questions / 材料全都要），于是一个自评量表**无法被表达** ——
    作者只能编一份假材料来满足校验，而那份假材料会被抽样与引文核对当真。

    它进的是 `stated` 通道（M-SE-01 行为指标自评），与"读材料答题"
    完全不是一回事：它问的是"你平时怎样"。
    """
    return {
        "item_id": "S-1",
        "variable_id": "V-SE",
        "channel": "stated",
        "method_id": "M-SE-01",
        "role": "regular",
        "difficulty": "易",
        "topics": ["BIO-MARINE"],
        "time_min": 3,
        "language": "zh",
        "media": "text",
        "prompt": "下面几句话，哪些像平时的你？",
        "scoring": {"rule": "self_report", "weight_source": "difficulty"},
        "source": "夹具（不是产品题库）",
        "license": "个人使用",
        "status": "verified",
        # ★ 反向句是刻意的（3 号）：全是正向句时，"看到就选最右边那个"
        # 这种作答习惯会直接抬高分数 —— 而那不是他的倾向，是他填表的习惯。
        "self_report_question": {
            "prompt": "下面几句话，哪些像平时的你？",
            "options": [
                {"value": "never", "label": "从不", "weight": 0.0},
                {"value": "sometimes", "label": "偶尔", "weight": 0.34},
                {"value": "often", "label": "经常", "weight": 0.67},
                {"value": "always", "label": "总是", "weight": 1.0},
            ],
            "statements": [
                {"no": 1, "text": "读完一段密集材料后，我能说出它的主线"},
                {"no": 2, "text": "我能注意到容易被忽略的限定条件"},
                {"no": 3, "text": "我常常读到一半才发现自己没抓住重点", "reverse": True},
            ],
        },
    }


def observation_item() -> dict:
    """一道**行为观测题**（P11），它用的量是**隐性参数**。

    ★ 关键在 `scoring.observation_table.unit = "switch_count"`：
    这个量由系统在作答过程中顺手记下（`benchmark_response.switch_count`），
    **不由他填写**（让他手写"我切换了几次"会把一个行为量变成自评量）。

    ★ 而且这个量**不是自动计分的**：它必须由题目**明示**一张换算表。
    为什么：在 4 档量表上切换两次与在 10 档量表上切换两次不是同一件事，
    自动换算等于替一门还没定义的测量学做决定。
    """
    return {
        "item_id": "O-1",
        "variable_id": "V-HSR",
        "channel": "experienced",
        "method_id": "M-HSR-05",
        "role": "regular",
        "difficulty": "中",
        "topics": ["BIO-MARINE"],
        "time_min": 4,
        "language": "zh",
        "media": "text",
        "prompt": "遇到卡点时你会怎么做（观测：本次作答中的求助与切换行为）",
        "scoring": {
            "rule": "observation",
            "weight_source": "difficulty",
            "observation_table": {
                "table_id": "v-hsr-switch-1",
                "unit": "switch_count",
                "bands": [
                    {"upper": 1.0, "score": 10.0, "label": "一次就定"},
                    {"upper": 4.0, "score": 7.0, "label": "有来回"},
                    {"upper": None, "score": 4.0, "label": "反复横跳"},
                ],
            },
        },
        "source": "夹具（不是产品题库）",
        "license": "个人使用",
        "status": "verified",
        "self_report_question": None,
    }


def main() -> None:
    BANK.mkdir(parents=True, exist_ok=True)
    MATERIALS.mkdir(parents=True, exist_ok=True)
    for item_id, role, difficulty, topics in SPEC:
        (BANK / f"{item_id}.json").write_text(
            json.dumps(item_json(item_id, role, difficulty, topics), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        (MATERIALS / f"{item_id}.md").write_text(
            material_text(item_id), encoding="utf-8"
        )
    # ★ 这两道题**故意不写材料**：它们证明"没有材料的题"是合法的一类。
    # 给它们补一份材料会让 `load_item` 的材料分支重新变成必填，
    # 而那正是这一轮要修掉的东西。
    for item in (self_report_item(), observation_item()):
        (BANK / f"{item['item_id']}.json").write_text(
            json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"写了 {len(SPEC) + 2} 道题到 {BANK}")
    print(f"写了 {len(SPEC)} 份材料到 {MATERIALS}（自评/观测题没有材料）")


if __name__ == "__main__":
    main()
