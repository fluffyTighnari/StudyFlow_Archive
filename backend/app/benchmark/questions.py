"""题型分支：一道题**必须有什么**，取决于它是什么题（P11）。

## 为什么规格要按题型分支

在此之前，规格是一张**所有题共用的必填表**：

```
anchors 必有 / key_points_question 必有 / exact_questions 必有 / 材料必有
```

那对"读一份材料、答几道题"的提取题是对的。但它**表达不了自评量表**——
`M-SE-01 行为指标自评`（"3-5 条行为描述，条目均值 → 0-10"）既没有材料，
也没有埋点，更没有对错：

- 逼它挂一份材料，作者只能**编一份假材料**来满足校验，
  而那份材料会被后面的抽样、引文核对当真；
- 放宽成"全都可有可无"，则一道真的缺了埋点的提取题会静默通过 ——
  它的评分指南从此无法核对。

所以规格按**题型**分支：每道题声明它带哪些问题块，**每块只管自己那几项**。
"缺字段"的报错因此变得**具体**：不再是"这道题不合格"，
而是"这道题声明了要点题，但没有 `anchors`"。

## 这个模块的第二个作用：把分支口径**只写一遍**

`tools/checkitems.py`（作者侧质检）与 `app/benchmark/items.py`（运行时加载）
必须对"什么算合格"有一致的看法。两份判断的下场是
**校验器放行的题，加载器读不懂** —— 而那时作者看到的是"我的题通过了"。
两边都调用这里的 `question_kinds` / `requires_material` / `load_self_report`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

KIND_KEY_POINTS = "key_points"
KIND_EXACT = "exact"
KIND_EXACT_BLANK = "exact_blank"
KIND_SELF_REPORT = "self_report"
KIND_OBSERVATION = "observation"

#: 需要**材料**才能成立的问题块。
#:
#: 自评量表不在其中 —— 它没有可读的东西。这不是"宽松"，是它本来就不同：
#: 让一个自评量表去引用一份材料，等于让"你平时怎么读东西"这道题
#: 依赖于某篇具体文章，而那件事与他的倾向无关。
MATERIAL_KINDS = frozenset({KIND_KEY_POINTS, KIND_EXACT})

#: 客观题（有对错）。**它们只做标尺，不产出读数**（红线 7）。
OBJECTIVE_KINDS = frozenset({KIND_EXACT, KIND_EXACT_BLANK})


class SpecError(ValueError):
    """规格不合法。**不猜、不填默认值。**"""


# --------------------------------------------------------------------------- #
# 自评量表
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SelfReportOption:
    value: str
    label: str
    weight: float


@dataclass(frozen=True)
class SelfReportStatement:
    no: int
    text: str
    #: 反向计分句：`1 - weight`。
    #:
    #: ★ 自评量表**必须有反向句**才可信：全是正向句时，
    #: "看到就选最右边那个"这种作答习惯会直接抬高分数 ——
    #: 而那不是他的倾向，是他填表的习惯。
    reverse: bool = False


@dataclass(frozen=True)
class SelfReportQuestion:
    prompt: str
    options: tuple[SelfReportOption, ...]
    statements: tuple[SelfReportStatement, ...]

    def option(self, value: str) -> SelfReportOption | None:
        return next((o for o in self.options if o.value == value), None)

    def statement(self, no: int) -> SelfReportStatement | None:
        return next((s for s in self.statements if s.no == no), None)

    def points_of(self, no: int, value: str) -> float | None:
        """某句选了某个选项 → 归一化到 0-1 的点数。认不出就 `None`。"""
        statement = self.statement(no)
        option = self.option(value)
        if statement is None or option is None:
            return None
        weight = option.weight
        return 1.0 - weight if statement.reverse else weight


def load_self_report(raw: Any, *, where: str) -> SelfReportQuestion:
    """校验一份自评量表。**选项权重是仪器的一部分，必须由题目声明。**

    ★ 为什么权重不能由模型或界面临时定：它是**测量刻度**。
    同一份量表两次作答若用了不同的刻度，两次分数不可比 ——
    而不可比的两个数放在一起，看起来只是"他变了"。
    """
    if not isinstance(raw, dict):
        raise SpecError(f"{where} 的 `self_report_question` 不是对象")
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        raise SpecError(f"{where} 的自评量表没有 `prompt`")

    raw_options = raw.get("options")
    if not isinstance(raw_options, list) or len(raw_options) < 2:
        raise SpecError(f"{where} 的自评量表至少要两个选项（一个选项没有分辨力）")
    options: list[SelfReportOption] = []
    seen_values: set[str] = set()
    for entry in raw_options:
        if not isinstance(entry, dict):
            raise SpecError(f"{where} 的选项不是对象：{entry!r}")
        value = str(entry.get("value") or "").strip()
        label = str(entry.get("label") or "").strip()
        if not value or not label:
            raise SpecError(f"{where} 的选项缺 `value`/`label`：{entry!r}")
        if value in seen_values:
            raise SpecError(f"{where} 的选项 `value` 重复：{value}")
        seen_values.add(value)
        try:
            weight = float(entry["weight"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SpecError(f"{where} 的选项 `{value}` 没有可用的 `weight`（{exc}）") from exc
        if not 0.0 <= weight <= 1.0:
            raise SpecError(f"{where} 的选项 `{value}` 权重 {weight} 不在 0-1")
        options.append(SelfReportOption(value=value, label=label, weight=weight))

    # ★ 声明顺序必须与权重单调一致。
    # 反过来的话，"界面上排第几"与"值多少分"是两套顺序 ——
    # 于是同一份量表换个显示顺序就换个分数，而没有任何东西会报错。
    weights = [option.weight for option in options]
    if any(a >= b for a, b in zip(weights, weights[1:])):
        raise SpecError(
            f"{where} 的选项权重不是随声明顺序严格递增的：{weights} —— "
            f"界面上的排列顺序必须就是分值顺序，否则换个顺序就换个分数"
        )
    if weights[0] != 0.0 or weights[-1] != 1.0:
        raise SpecError(
            f"{where} 的选项权重没有顶到两端（{weights[0]}..{weights[-1]}）—— "
            f"两端不顶格时，一个'从来没有'与一个'一直如此'的人拿不到 0 / 10，"
            f"而这个天花板不会有任何报错"
        )

    raw_statements = raw.get("statements")
    if not isinstance(raw_statements, list) or not raw_statements:
        raise SpecError(f"{where} 的自评量表一条句子都没有")
    statements: list[SelfReportStatement] = []
    seen_nos: set[int] = set()
    for entry in raw_statements:
        if not isinstance(entry, dict):
            raise SpecError(f"{where} 的句子不是对象：{entry!r}")
        try:
            no = int(entry["no"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SpecError(f"{where} 的句子没有可用的 `no`（{exc}）") from exc
        if no in seen_nos:
            raise SpecError(f"{where} 的句子编号重复：{no}")
        seen_nos.add(no)
        text = str(entry.get("text") or "").strip()
        if not text:
            raise SpecError(f"{where} 的第 {no} 句没有文字")
        statements.append(
            SelfReportStatement(no=no, text=text, reverse=bool(entry.get("reverse", False)))
        )
    return SelfReportQuestion(
        prompt=prompt, options=tuple(options), statements=tuple(statements)
    )


# --------------------------------------------------------------------------- #
# 一道题带哪些问题块
# --------------------------------------------------------------------------- #


def has_blanks(exact_questions: list[dict]) -> bool:
    """有没有**多空填空**。它必须与单空题分开记（§5.2：一空与全空不能同分）。"""
    from app.benchmark.items import BLANK

    return any(len(BLANK.findall(str(q.get("expected") or ""))) >= 2 for q in exact_questions)


def question_kinds(raw: dict) -> tuple[str, ...]:
    """这道题**声明**带了哪些问题块。**只看声明，不看它是否完整** ——
    完整性由各自的校验负责，混在一起会让报错说不清是哪一块缺了东西。
    """
    kinds: list[str] = []
    if raw.get("key_points_question"):
        kinds.append(KIND_KEY_POINTS)
    exact = raw.get("exact_questions") or []
    if exact:
        kinds.append(KIND_EXACT_BLANK if has_blanks(list(exact)) else KIND_EXACT)
    if raw.get("self_report_question"):
        kinds.append(KIND_SELF_REPORT)
    scoring = raw.get("scoring") or {}
    if isinstance(scoring, dict) and scoring.get("rule") == KIND_OBSERVATION:
        kinds.append(KIND_OBSERVATION)
    return tuple(kinds)


def requires_material(kinds: tuple[str, ...]) -> bool:
    """只有"读材料答题"那几类需要材料。

    没有它的话，`load_item` 没法区分"这道题忘了挂材料"（必须报错）
    与"这道题本来就没有材料"（自评量表）—— 而这两种情况在文件系统里
    长得一模一样：`materials/<id>.md` 不存在。
    """
    return bool(set(kinds) & MATERIAL_KINDS)


__all__ = [
    "KIND_EXACT",
    "KIND_EXACT_BLANK",
    "KIND_KEY_POINTS",
    "KIND_OBSERVATION",
    "KIND_SELF_REPORT",
    "MATERIAL_KINDS",
    "OBJECTIVE_KINDS",
    "SelfReportOption",
    "SelfReportQuestion",
    "SelfReportStatement",
    "SpecError",
    "has_blanks",
    "load_self_report",
    "question_kinds",
    "requires_material",
]
