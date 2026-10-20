"""计分器：**五层量表链**（Benchmark v0.3 §5）。

```
原始记录 raw      答对/答错、命中的要点数、秒数、次数、等级
   ↓  单题计分（每种题型一套规则）        ← score_item / score_key_points
单题分 item_score ∈ [0,10]
   ↓  方法内聚合（权重 = 该题难度）        ← aggregate_method
方法分 method_score ∈ [0,10]
   ↓  通道内多方法合成（权重 = 方法优先级）  ← compose_channel
通道分 channel_score ∈ [0,10]              ← ★ 落库的就是它，到此为止
```

## 三条不能松的口径

### ① `NULL` ≠ 0

「这题没测到」与「这题得 0 分」是两件事。把两者混起来，
一次**太容易的任务**会静默地压低分数（V-HSR 的例子：他没卡住，
于是"没有求助"被算成"求助得很差"）。所以作废题 `item_score=None`，
而且**不参与分子也不参与分母**。

### ② 标尺不产出读数（红线 7）

有正确答案的题（`maps_to_score=ruler`）**只能校准这一次运行**，
不得产出 `measured` 读数 —— 它的分数随**能力**移动，而我们要回答的是**倾向**。
本模块不判断这条（那是 `tools/auditmethods.py` 的职责），
但聚合时会把"标尺题"排除出读数，见 `aggregate_method(rulers_excluded=True)`。

### ③ 换算表必须写死并公开

`observation` 类题的分数由一张**明示的分档表**给出，而不是"看了一下给个 6 分"。
换算表带**版本**（`table_id`），因为它会改，而改过之后
"这个分数当时是怎么算的"必须还能回答 —— 所以它进 `scoring_method`。

## 这一层为什么是纯的

一次 60 分钟的全面卷只产生一个分数。如果那个分数错了，**无法再跑一次来查** ——
人已经学会了那些材料。所以决定分数的每一步都要能在没有用户、没有数据库、
没有模型的情况下复算。这个模块只吃数据、只吐数据。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import re

from app.benchmark.items import ANSWER_SEP, DIFFICULTY_WEIGHT, fold
from app.benchmark.judge import JudgeVerdict, verdict_note
from app.benchmark.questions import (
    KIND_EXACT,
    KIND_EXACT_BLANK,
    KIND_KEY_POINTS,
    KIND_OBSERVATION,
    KIND_SELF_REPORT,
    SelfReportQuestion,
)

# --------------------------------------------------------------------------- #
# 最小题数（§5.3）：低于它就不给分
# --------------------------------------------------------------------------- #

#: 客观/要点类：少于 3 题不给分。
MIN_ITEMS_DEFAULT = 3
#: 观测类：少于 2 个独立观测点不给分。
MIN_ITEMS_OBSERVATION = 2

#: 方法优先级权重（§5.4）。**只在同一通道内合成** ——
#: 跨通道取均值是被明令禁止的：`stated` 的自评分与 `measured` 的任务分
#: 不是同一个量纲，平均它们等于把"他觉得自己怎样"和"他实际怎样"搅成一个谁都不是的数。
METHOD_PRIORITY_WEIGHT: dict[str, float] = {"P1": 1.0, "P2": 0.7, "P3": 0.4}


class ScoringError(ValueError):
    """计分输入本身有问题（题号越界、换算表缺档…）。**不猜**。"""


# --------------------------------------------------------------------------- #
# 哪些题型产出读数（红线 7 的落点）
# --------------------------------------------------------------------------- #

#: 产出**读数**的题型。
#:
#: ★ 这张表必须只有一份，因为有两个地方要用它，而它们看的是**不同的东西**：
#:
#: | 谁 | 看什么 |
#: |---|---|
#: | 写入路径（计分时） | 刚刚算出来的那个结果 |
#: | 读回路径（`/finish` 聚合） | 库里存着的那一行 |
#:
#: 两处各判一次、判法只要差一点，就会出现「计分时说这题算读数、
#: 汇总时又说它不算」—— 分数少算了，而**每一步看起来都对**。
#: 所以判定收敛到这一个函数：写入路径由它得出结论，
#: 读回路径由它从 `kind` 反推。
#:
#: ★ **`self_report` 产出读数**（P11）。它进的是 `stated` 通道，
#: 而红线 7 针对的是**正误题**（`exact` / `exact_blank`）——
#: 它们的分数随**能力**移动。一份自评量表问的是"你平时怎样"，
#: 它不随能力移动，所以它算读数。把它也排除掉，
#: `stated` 通道会永远拿不到任何读数，而那看起来只是"还没测"。
READING_KINDS: frozenset[str] = frozenset(
    {KIND_KEY_POINTS, KIND_OBSERVATION, KIND_SELF_REPORT}
)


def counts_toward_reading(kind: str) -> bool:
    """这个题型产出读数吗（§5.0 红线 7）。

    正误题（`exact` / `exact_blank`）**不产出**：它的分数随**能力**移动，
    而我们要回答的是**倾向**。它只做等值化标尺。
    """
    return kind in READING_KINDS


# --------------------------------------------------------------------------- #
# 隐性参数（P11）
# --------------------------------------------------------------------------- #

#: 作答过程中**顺手就能记下**、但不由他填写的那几个量。
#:
#: | 参数 | 它是什么 | 为什么值得记 |
#: |---|---|---|
#: | `elapsed_sec` | 用时（秒） | 最直接的投入量 |
#: | `hesitation_sec` | 犹豫（秒） | 看到题到第一次动手之间停了多久 |
#: | `switch_count` | 切换次数 | 在材料/选项之间来回了几次 |
#: | `help_request_count` | 求助次数 | 主动开口问了几次 |
#: | `hint_used_count` | 提示使用次数 | 用了几次提示 |
#:
#: ★ **它们只被记录，不被自动计分。**
#:
#: 一个量该换算成几分，取决于**当时的仪器**：在 4 档量表上切换两次、
#: 与在 10 档量表上切换两次，不是同一件事。所以这里不提供任何默认换算 ——
#: 想要它的分数，就在题目的 `observation_table` 里**明示**一条换算表，
#: 把 `unit` 写成这里的名字。没有声明就没有分数，
#: 而不是"用一个看起来合理的默认值凑一个分出来"。
#:
#: ★ 这张表与 `benchmark_response` 的列**必须一一对应**，
#: `tests/benchmark/test_scoring.py` 会去 ORM 上逐列核对 —— 两处各写一遍的话，
#: 改列名之后这里会安静地读到一个永远为 `None` 的值，
#: 而那表现为"这条观测点总是没记到"。
IMPLICIT_PARAMETERS: dict[str, str] = {
    "elapsed_sec": "用时（秒）",
    "hesitation_sec": "犹豫（秒）—— 看到题到第一次动手之间停了多久",
    "switch_count": "切换次数 —— 在材料/选项之间来回了几次",
    "help_request_count": "求助次数 —— 主动开口问了几次",
    "hint_used_count": "提示使用次数 —— 用了几次提示",
}


# --------------------------------------------------------------------------- #
# 单题分
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScoredItem:
    """一道题的计分结果，**带上"这个数是怎么来的"**。

    `scoring_method` 是一串机器可读的审计串（如 `llm:key_points(命中 2/3,
    引文核对降级 1)`）。它的价值在于**事后能回答"当时用的是哪条规则"** ——
    规则会改，而这个字符串不会跟着变。
    """

    item_score: float | None
    scoring_method: str
    judged_by: str
    judge_model: str | None = None
    judge_note: str | None = None
    judged_hits: list[dict] | None = None
    #: 这一题参不参与读数聚合。标尺题（正误题）不参与（§5.0）。
    counts_toward_reading: bool = True
    #: 为什么作废（`item_score is None` 时必须有）。
    void_reason: str = ""


def _parse_number(text: str) -> float | None:
    """从作答里读出一个数。读不出就 `None`（**不猜、不当 0**）。

    允许前后有单位或说明文字（`"180 秒"`、`"约 3 次"`）——
    他要写的是意思，不是我们的格式。但**只认一个数**：
    `"3 到 5 次"` 读不出唯一的量，那就作废，而不是取第一个数。
    """
    found = re.findall(r"-?\d+(?:\.\d+)?", text or "")
    if len(found) != 1:
        return None
    return float(found[0])


def _unit(value: float) -> float:
    """0-10 → 0-1，并夹在范围内。

    ★ 夹范围不是"防御性编程"：`raw_answer` 里可能出现任何东西，
    而一个 12 分的单题分会**悄悄污染**它上面的两层聚合
    （`item_score/10` 之后权重和分母都会偏），且不会有任何报错。
    """
    return min(1.0, max(0.0, value / 10.0))


def _normalize_answer(text: str) -> str:
    """比对答案用：折平排版差异 + 去掉空白。

    ★ 与引文核对（`app.core.quotes`）**刻意不同**：

    | | 折什么 | 为什么 |
    |---|---|---|
    | 引文核对 | 只去空白 | 比的是「原文里有没有这一段」—— 排版差异意味着**不是同一段原文** |
    | 答案比对 | 折引号/破折号 + 去空白 | 比的是「他答对了没有」—— `2-3` 与 `2–3` 是**输入法差异**，不是知识差异 |

    把这两件事混成一套规则，一定会在某一侧错：要么判分把正确的答案判错
    （用户填 `350；2-3`），要么引文核对放行一段改过字的"引文"。
    折换表来自 `items.py` 的 `fold` —— 那里已经在为**作者侧**核对做同一件事，
    两处共用一张表，才不会有第三种折法。
    """
    return "".join(fold(text).split())


def score_exact(raw_answer: str, expected: str) -> ScoredItem:
    """客观题：对 = 10，错 = 0。

    ★ `expected` 只取**第一个**答案（多选/成对题的比较见 `exact_blank`）：
    客观题有唯一正确答案，而"答案里带分号"是作者侧的多空写法。
    """
    target = ANSWER_SEP.split(expected)[0]
    ok = _normalize_answer(raw_answer) == _normalize_answer(target)
    return ScoredItem(
        item_score=10.0 if ok else 0.0,
        scoring_method="code:exact",
        judged_by="code",
        judge_note=f"答案{'一致' if ok else '不一致'}",
        # 正误题**只是标尺**（§5.0）：它校准这一次运行，不产出 `measured` 读数。
        counts_toward_reading=counts_toward_reading("exact"),
    )


def score_exact_blank(raw_answer: str, expected: str) -> ScoredItem:
    """多空填空：`10 × 命中空数 / 总空数`。

    ★ 它与 `exact` **必须分开**：一题两空时按「对=10/错=0」给分，
    "两空全对"与"只对一空"拿一样的分 —— 分数失去分辨力，
    而这个损失**不会报错**。分开记之后，回看一条分数时能知道是哪种规则算出来的。
    """
    blanks = [part for part in ANSWER_SEP.split(expected) if part.strip()]
    if not blanks:
        raise ScoringError(f"exact_blank 的答案分不出空：{expected!r}")
    given = [_normalize_answer(part) for part in ANSWER_SEP.split(raw_answer)]
    hit = sum(
        1
        for index, blank in enumerate(blanks)
        if index < len(given) and given[index] == _normalize_answer(blank)
    )
    return ScoredItem(
        item_score=round(10.0 * hit / len(blanks), 2),
        scoring_method=f"code:exact_blank({hit}/{len(blanks)} 空)",
        judged_by="code",
        judge_note=f"命中 {hit}/{len(blanks)} 空（按空序比对，不按位置）",
        counts_toward_reading=counts_toward_reading("exact_blank"),
    )


def score_key_points(
    guide: Sequence[str], raw_answer: str, verdict: JudgeVerdict
) -> ScoredItem:
    """要点题：`10 × 核实过的命中数 / 要点总数`（§5.2.1）。

    ★ 分母是**题目的要点总数**，不是"判官报了几条"。
    后者会让一个只报 1 条命中的判官给出 10 分。
    """
    if not guide:
        raise ScoringError("要点题的评分指南是空的 —— 没有分母就无从判分")
    if not verdict.ok:
        # ★ 判官坏了 ≠ 0 分。见模块文档与 `judge.py`。
        return ScoredItem(
            item_score=None,
            scoring_method="llm:key_points(判官无有效输出)",
            judged_by="llm",
            judge_model=verdict.model,
            judge_note=verdict_note(verdict),
            judged_hits=None,
            counts_toward_reading=counts_toward_reading("key_points"),
            void_reason=f"判官没有给出可用的判定：{verdict.error}",
        )
    hits = list(verdict.verified_hits)
    score = round(10.0 * len(hits) / len(guide), 2)
    downgraded = len(verdict.downgraded)
    method = f"llm:key_points(命中 {len(hits)}/{len(guide)}"
    if downgraded:
        method += f"，引文核对降级 {downgraded}"
    method += ")"
    return ScoredItem(
        item_score=score,
        scoring_method=method,
        judged_by="llm",
        judge_model=verdict.model,
        judge_note=verdict_note(verdict),
        judged_hits=[
            {"no": hit.no, "quote": hit.quote, "verified": hit.verified, "why": hit.why}
            for hit in verdict.hits
        ],
        counts_toward_reading=counts_toward_reading("key_points"),
    )


@dataclass(frozen=True)
class ObservationBand:
    """换算表的一档。`upper=None` 表示"这条及以下"。"""

    upper: float | None
    score: float
    label: str


@dataclass(frozen=True)
class ObservationTable:
    """行为量的换算表（§5.2）。**带版本**，因为它会改。"""

    table_id: str
    unit: str
    bands: tuple[ObservationBand, ...]

    def score_of(
        self, raw_value: float | None, *, missing: str = "没有卡点"
    ) -> ScoredItem:
        """把原始量映射到 0-10。

        ★ `raw_value is None` → 这题**作废**（`item_score=None`），不是 0 分。
        把两者混起来会让一次太容易的任务静默地压低分数。

        `missing` 说明"为什么没有这个量"。它必须由调用方给具体的那一句：
        没卡住（任务没难住他）与没记到（这次没采到那个参数）**是两件事**，
        而它们都会让这一题没有分数 —— 混成一句，事后就分不清
        "是他太强"还是"我们忘了记"。
        """
        if raw_value is None:
            return ScoredItem(
                item_score=None,
                scoring_method=f"code:observation({self.table_id}):未测到",
                judged_by="code",
                judge_note=f"{missing} —— 该观测点作废，不是 0 分",
                counts_toward_reading=counts_toward_reading(KIND_OBSERVATION),
                void_reason=f"{missing}，这个观测点没测到",
            )
        for band in self.bands:
            if band.upper is None or raw_value <= band.upper:
                return ScoredItem(
                    item_score=band.score,
                    scoring_method=f"code:observation({self.table_id}:{band.label})",
                    judged_by="code",
                    judge_note=f"{self.unit}={raw_value} → {band.score}（{band.label}）",
                    counts_toward_reading=counts_toward_reading(KIND_OBSERVATION),
                )
        raise ScoringError(
            f"换算表 {self.table_id} 没覆盖 {raw_value} —— "
            f"最后一档必须用 upper=None 兜底，否则落表外的值会变成一个没人发现的默认分"
        )


def observation_value(
    table: ObservationTable,
    *,
    raw_answer: str,
    implicit: Mapping[str, float | int | None],
) -> tuple[float | None, str]:
    """这次观测要的那个量是多少，以及**它是从哪来的**。

    ★ 隐性参数（用时/犹豫/切换/求助/提示）与"他从材料里抄下来的数字"
    走同一个出口：题目声明 `unit`，这里按 `unit` 去取。

    为什么隐性参数**必须由题目声明才生效**：一个量换算成几分取决于仪器
    （4 档量表上切换两次 ≠ 10 档量表上切换两次）。所以这里不做任何默认换算 ——
    没有声明就没有分数。**记录不等于计分**：
    把这些量自动算进分数，等于替一门我们还没定义的测量学做决定。
    """
    if table.unit in IMPLICIT_PARAMETERS:
        value = implicit.get(table.unit)
        if value is None:
            return None, f"这次没记到 `{table.unit}`（{IMPLICIT_PARAMETERS[table.unit]}）"
        return float(value), f"{table.unit}={value}"
    parsed = _parse_number(raw_answer)
    if parsed is None:
        return None, f"位置里给的是 {raw_answer.strip()!r}，读不出一个数"
    return parsed, f"{table.unit}={parsed}"


def score_observation(
    table: ObservationTable,
    *,
    raw_answer: str,
    implicit: Mapping[str, float | int | None] | None = None,
) -> ScoredItem:
    """观测题的**唯一入口**：先决定量是多少，再查表。

    两条作废路径都走这里，因此"没卡住"与"没记到"的措辞**只在一处**决定。
    """
    value, how = observation_value(
        table, raw_answer=raw_answer, implicit=dict(implicit or {})
    )
    return table.score_of(value, missing=how)


# --------------------------------------------------------------------------- #
# 自评量表（P11）
# --------------------------------------------------------------------------- #

#: `1=often；2=sometimes` 里的一格。序号 + 选项值。
#:
#: 兼容全角等号与中文冒号：作答界面用什么写法不该决定分数能不能算出来，
#: 但**认不出就必须作废**，不许"猜一个最接近的选项"。
_SELF_CHOICE = re.compile(r"^\s*(\d+)\s*[=＝:：]\s*(.+?)\s*$")


def parse_self_choices(
    raw_answer: str, question: SelfReportQuestion
) -> tuple[dict[int, str] | None, str]:
    """解析自评作答。返回 `(每句选了什么, 不能用的原因)`。

    `None` = 这份作答**不完整或不可读** → 这一题作废（不是 0 分）。

    ★ 为什么"少答一句"也作废，而不是按答了的那几句求均值：
    一份量表少答的那一句**不是随机的**（跳过通常意味着那句不好答），
    按剩下的求均值会得到一个系统偏移的分数，而它看起来完全正常。
    少答就是没测到 —— 让它作废，他会看到"这次没测到"，
    而不是一个悄悄偏了 1.5 分的读数。

    ★ 为什么"认不出的选项"不作废成 0：认不出说明**量表与作答不是同一版**
    （选项改过、或者这是另一份量表的值），那是一个应当被看见的上游问题。
    """
    if not raw_answer or not raw_answer.strip():
        return None, "自评量表一个字都没答"
    choices: dict[int, str] = {}
    for part in ANSWER_SEP.split(raw_answer):
        if not part.strip():
            continue
        match = _SELF_CHOICE.match(part)
        if match is None:
            return None, f"这一格读不出「序号=选项」：{part.strip()!r}"
        no = int(match.group(1))
        value = match.group(2).strip()
        if question.statement(no) is None:
            return None, f"第 {no} 句不在这份量表里（共 {len(question.statements)} 句）"
        if no in choices:
            return None, f"第 {no} 句答了两次（{choices[no]!r} 与 {value!r}）"
        if question.option(value) is None:
            allowed = "、".join(option.value for option in question.options)
            return None, f"第 {no} 句的选项 {value!r} 不在这份量表里（可选：{allowed}）"
        choices[no] = value
    missing = [s.no for s in question.statements if s.no not in choices]
    if missing:
        return None, f"第 {'、'.join(str(no) for no in missing)} 句没答"
    return choices, ""


def score_self_report(
    question: SelfReportQuestion | None, raw_answer: str
) -> ScoredItem:
    """自评量表：`10 × 各句点数的均值`（每句点数 ∈ 0-1，反向句取 `1-w`）。

    ★ **必须有反向句**：全是正向句时，"看到就选最右边那个"这种作答习惯
    会直接抬高分数 —— 而那不是他的倾向，是他填表的习惯。
    这一条不是靠"记得写反向句"，而是 `tests` 里有一条检查
    要求量表里至少有一句反向句（内容侧的要求写在题里，机制侧不猜）。
    """
    if question is None:
        raise ScoringError("这道题没有声明自评量表（`self_report_question`）—— 无从判分")
    choices, why = parse_self_choices(raw_answer, question)
    if choices is None:
        return ScoredItem(
            item_score=None,
            scoring_method="code:self_report(未采纳)",
            judged_by="code",
            judge_note=f"这份自评读不出来：{why}",
            counts_toward_reading=counts_toward_reading(KIND_SELF_REPORT),
            void_reason=why,
        )
    points = 0.0
    reverses = 0
    for statement in question.statements:
        value = choices[statement.no]
        earned = question.points_of(statement.no, value)
        if earned is None:  # pragma: no cover - 解析阶段已经挡住了
            raise ScoringError(f"第 {statement.no} 句的选项 {value!r} 算不出点数")
        points += earned
        reverses += 1 if statement.reverse else 0
    total = len(question.statements)
    score = round(10.0 * points / total, 2)
    return ScoredItem(
        item_score=score,
        scoring_method=(
            f"code:self_report({total} 句，反向 {reverses}，"
            f"选项 {len(question.options)} 档)"
        ),
        judged_by="code",
        judge_note=f"各句点数之和 {points:.3g} / {total} 句 → {score}",
        counts_toward_reading=counts_toward_reading(KIND_SELF_REPORT),
    )


# --------------------------------------------------------------------------- #
# 方法内聚合
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MethodAggregate:
    """方法分，以及它为什么是这个值。"""

    method_score: float | None
    item_count: int
    used_count: int
    void_count: int
    #: `complete` = 达到最小题数；`partial` = 没达到（分是 `None`）。
    completeness: str
    detail: str
    #: 被排除在读数之外的标尺题数（正误题）。**必须单独说** ——
    #: 否则「只凑到 0 道可用于读数的题」会被读成「他没有作答」。
    ruler_count: int = 0

    @property
    def is_partial(self) -> bool:
        return self.completeness == "partial"


def aggregate_method(
    items: Sequence[tuple[ScoredItem, str]],
    *,
    min_items: int = MIN_ITEMS_DEFAULT,
    rulers_excluded: bool = True,
) -> MethodAggregate:
    """方法内聚合：`10 × Σ(w·s) / Σw`（§5.3）。

    参数是 `(计分结果, 难度)` 的序列 —— 难度是**题目的属性**，不是计分结果的，
    所以由调用方带进来，而不是从结果里猜。

    ★ 两种题**不参与**：
    - 作废题（`item_score is None`）：分子分母都不进（"没测到"不是"0 分"）；
    - 标尺题（`counts_toward_reading=False`）：§5.0 —— 它校准运行，不产出读数。

    ★ 最小题数按**参与的题数**算，不按题目总数算：
    一次抽了 6 道题、其中 3 道是标尺题时，读数只有 3 道题撑着，
    而"够不够"问的正是这 3 道。按总数算会让它显得很够。
    """
    counted = [
        (result, difficulty)
        for result, difficulty in items
        if result.item_score is not None
        and (result.counts_toward_reading or not rulers_excluded)
    ]
    void = [r for r, _ in items if r.item_score is None]
    rulers = [
        r for r, _ in items if r.item_score is not None and not r.counts_toward_reading
    ]
    if len(counted) < min_items:
        why = f"只凑到 {len(counted)} 道可用于读数的题（要 {min_items} 道）"
        if rulers and rulers_excluded:
            why += (
                f"；另有 {len(rulers)} 道是标尺题（正误题只做等值化，" 
                f"不产出读数 —— 红线 7）"
            )
        if void:
            why += f"；另有 {len(void)} 道作废（没测到，不是 0 分）"
        return MethodAggregate(
            method_score=None,
            item_count=len(items),
            used_count=len(counted),
            void_count=len(void),
            ruler_count=len(rulers),
            completeness="partial",
            detail=why + " —— 不给分：低于最小题数时，一个数看起来像读数，其实只是噪声",
        )
    weights = []
    for result, difficulty in counted:
        weight = DIFFICULTY_WEIGHT.get(difficulty)
        if weight is None:
            raise ScoringError(f"难度 {difficulty!r} 不在权重表里（易/中/难）")
        weights.append(weight * _unit(result.item_score or 0.0))
    total_weight = sum(
        DIFFICULTY_WEIGHT[difficulty] for _, difficulty in counted
    )
    score = round(10.0 * sum(weights) / total_weight, 2)
    return MethodAggregate(
        method_score=score,
        item_count=len(items),
        used_count=len(counted),
        void_count=len(void),
        ruler_count=len(rulers),
        completeness="complete",
        detail=(
            f"{len(counted)} 道题难度加权（易 1.0 / 中 1.4 / 难 2.0）→ {score}"
            + (f"；{len(void)} 道作废（没测到）" if void else "")
            + (f"；{len(rulers)} 道标尺题未参与读数" if rulers else "")
        ),
    )


# --------------------------------------------------------------------------- #
# 通道内多方法合成
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ChannelAggregate:
    channel_score: float | None
    used_methods: tuple[str, ...]
    detail: str
    #: 因为没达到最小题数而被排除的方法。**必须说出来** ——
    #: 不然"为什么这个通道分这么低"没有答案。
    excluded_methods: tuple[str, ...] = field(default=())


def compose_channel(
    methods: Sequence[tuple[str, float | None, str]],
) -> ChannelAggregate:
    """通道分：`Σ(w_m · method_score_m) / Σw_m`（§5.4）。

    参数是 `(method_id, method_score, priority)`。`method_score=None`
    （没达到最小题数）的方法**不参与**，但要记进 `excluded_methods`。

    ★ **只在同一通道内合成**。跨通道取均值是被明令禁止的 ——
    调用方拿到的是"某个通道的分"，把两个通道的平均起来
    等于把"他觉得自己怎样"和"他实际怎样"搅成一个谁都不是的数。
    """
    usable = [
        (method_id, score, priority)
        for method_id, score, priority in methods
        if score is not None
    ]
    excluded = tuple(
        method_id for method_id, score, _ in methods if score is None
    )
    if not usable:
        return ChannelAggregate(
            channel_score=None,
            used_methods=(),
            excluded_methods=excluded,
            detail="这个通道没有一个方法给出了分 —— 通道分只能是 NULL，不是 0",
        )
    total = 0.0
    weighted = 0.0
    for method_id, score, priority in usable:
        weight = METHOD_PRIORITY_WEIGHT.get(priority)
        if weight is None:
            raise ScoringError(
                f"方法优先级 {priority!r} 不在权重表里（P1/P2/P3）—— "
                f"方法目录里少了这一列的取值"
            )
        total += weight
        weighted += weight * (score or 0.0)
    score = round(weighted / total, 2)
    detail = (
        f"{len(usable)} 个方法按优先级加权（P1 1.0 / P2 0.7 / P3 0.4）→ {score}"
    )
    if excluded:
        detail += f"；{len(excluded)} 个方法没达到最小题数，未参与：{'、'.join(excluded)}"
    return ChannelAggregate(
        channel_score=score,
        used_methods=tuple(method_id for method_id, _, _ in usable),
        excluded_methods=excluded,
        detail=detail,
    )


# --------------------------------------------------------------------------- #
# 置信判定（§5.5）
# --------------------------------------------------------------------------- #


def decide_confidence(
    *,
    has_stated: bool,
    measured_used: int,
    anchor_count: int,
    all_objective: bool,
    has_observation: bool,
    min_items: int = MIN_ITEMS_DEFAULT,
) -> tuple[str, str]:
    """`hypothesis` / `provisional` / `calibrated` —— **可判定条件**，不是标签。

    ★ 这条规则的作用是**让 `calibrated` 可以达不到**。
    一个三档标签如果永远只能取最高档，它就不是标签，是装饰。

    返回 `(取值, 理由)`。理由要给用户看 —— 一个没有理由的置信档
    会被读成"系统在谦虚"或"系统在吹牛"，两种读法都没法核对。
    """
    if measured_used < min_items:
        return (
            "hypothesis",
            f"只有 {measured_used} 道读数题（要 {min_items} 道）"
            + ("，且只有自评通道有分" if has_stated else ""),
        )
    if anchor_count < 2:
        return (
            "provisional",
            f"达到最小题数，但只用了 {anchor_count} 道锚题 —— "
            f"难度没有校正，分数在换题时会漂移",
        )
    if has_observation:
        return (
            "provisional",
            "达到最小题数且有锚题，但含行为观测题（换算表是人定的档）",
        )
    if not all_objective:
        return (
            "provisional",
            "达到最小题数且有锚题，但含非客观题（判分依赖判官）",
        )
    return (
        "calibrated",
        f"达到最小题数、含 {anchor_count} 道锚题、且全部为客观/要点类",
    )


def iter_scores(items: Iterable[ScoredItem]) -> Iterable[float]:
    """只迭代**有效的**分数（给"≥2 条独立证据"这类门槛用）。"""
    for item in items:
        if item.item_score is not None:
            yield item.item_score


__all__ = [
    "IMPLICIT_PARAMETERS",
    "METHOD_PRIORITY_WEIGHT",
    "MIN_ITEMS_DEFAULT",
    "MIN_ITEMS_OBSERVATION",
    "READING_KINDS",
    "ChannelAggregate",
    "MethodAggregate",
    "ObservationBand",
    "ObservationTable",
    "ScoredItem",
    "ScoringError",
    "aggregate_method",
    "compose_channel",
    "counts_toward_reading",
    "decide_confidence",
    "iter_scores",
    "observation_value",
    "parse_self_choices",
    "score_exact",
    "score_exact_blank",
    "score_key_points",
    "score_observation",
    "score_self_report",
]
