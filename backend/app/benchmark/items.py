"""题库：规格、加载、以及**引文核对的原语**。

这个模块是「一道题长什么样」的**单一真相**。`tools/checkitems.py` 与
抽题/计分都从这里取规格，而不是各自抄一份 —— 两份规格的下场是
校验器放行的题，抽题器读不懂。

## 为什么引文原语放在这里而不是校验器里

因为"引文是否真的在原文里"**不只是作者侧的质检问题**，它在运行时还要用两次：

1. 要点题判分时，判官要附上"它凭用户答案里的哪句话判的"，
   而那句话要**在用户答案里核对得上**（否则判官可以编）。
2. 材料被改过之后，历史上那些引文是否仍然成立。

同一件事在两处各写一遍，第二次就会与第一次有微妙差别 ——
而"微妙差别"正是引文核对唯一不能有的东西。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# 规格字段（来源：Benchmark基准测验_v0.3.md §6.2）
# --------------------------------------------------------------------------- #

#: 缺了它们，**链路跑不起来**（不知道往哪个变量的哪个通道写、不知道是不是锚题）。
SPEC_BLOCKING = {
    "item_id": "写入与抽题的主键（作者侧文件里常写成 `id`）",
    "variable_id": "服务哪个变量（本批应为 V-SE）",
    "channel": "落哪个证据通道（本批应为 measured）",
    "method_id": "由哪个方法覆盖（V-SE.measured 应为 M-SE-04）",
    "role": "anchor | regular —— 等值化靠锚题，缺了就没法校正难度漂移",
    "difficulty": "易|中|难 —— 分数是难度加权算出来的",
}

#: 缺了它们，**能跑但会静默失准**（抽样选不对题、分数不可比）。
SPEC_SAMPLING = {
    "domain_familiarity": "low|mid|high —— 抽题的定向过滤靠它；缺了只能靠人记着",
    "time_min": "单题耗时（时间预算与作废题判据）",
    "domain": "领域（跨运行轮换靠它）",
    "language": "语言",
    "media": "text|image|audio|table",
    "scoring": "计分规则（rule 与权重来源）",
    "source": "出处（可追溯）",
    "license": "许可",
    "status": "candidate|verified|retired",
}

#: 作者侧文件里可能用的别名 → 规格字段名。
#:
#: 接受别名而不是要求作者改名，是因为**改名会动到他们已经写好的东西**，
#: 而别名映射是一行代码。但别名只允许"长得不一样、指的是同一个东西"，
#: 不允许两个字段同时存在却不同值 —— 那是两份真相，`load_item` 会直接报错。
FIELD_ALIASES: dict[str, str] = {
    "id": "item_id",
    "reading_time_est": "time_min",
}

VALID_ROLES = ("anchor", "regular")
VALID_DIFFICULTIES = ("易", "中", "难")
VALID_FAMILIARITY = ("low", "mid", "high")

#: §5.3 的难度权重（易 1.0 / 中 1.4 / 难 2.0）。
DIFFICULTY_WEIGHT: dict[str, float] = {"易": 1.0, "中": 1.4, "难": 2.0}


class ItemSpecError(ValueError):
    """题目缺规格字段或取值非法。**不猜、不填默认值** —— 见 `load_item`。"""


# --------------------------------------------------------------------------- #
# 文本原语
# --------------------------------------------------------------------------- #

#: 引文里用来表示"中间省略了"的记号。拆开逐段核对 ——
#: 拼接引文本身是允许的，**允许的是省略，不允许的是改字**。
ELLIPSIS = re.compile(r"[…]{2,}|\.{3,}")

#: 填空题的空位。
BLANK = re.compile(r"_{2,}")

#: 答案里的多空分隔符。
ANSWER_SEP = re.compile(r"[；;]")

#: 所有引号字符折叠成**同一个**。
#:
#: ★ 这里必须折叠成同一个，不能只抹平「弯/直」—— 引文用「‘…’」而原文用
#: 「"…"」是最常见的写法差异。本模块的第一版把「‘」折成 `'`、「"」折成 `"`，
#: 于是把一条**完全正确**的引文报成了「在原文中找不到」。
#: **一个只会误报的核对器，下场是没人再看它。**
QUOTES = "‘’“”'\"「」『』"

#: 所有破折号/连字符折叠成 `-`（含 U+2010/U+2011 这两种少见的连字符）。
DASHES = "–—−‐‑"

_FOLD = str.maketrans({**{c: '"' for c in QUOTES}, **{c: "-" for c in DASHES}})

#: 片段末尾的句末标点。引文常在一句话中间截断并自行补一个句号
#: （原文此处还在继续），那是引用习惯，不是改字。
TAIL_PUNCT = "。！？；，、：:;,.!?"

#: 括号注释。**引文略去原文的括号注释是合法做法**。
PAREN = re.compile(r"（[^）]*）|\([^)]*\)")

#: 「词/分句边界」。抹平后命中还不够，**必须再看一眼原文的下一个字符**：
#: 如果那不是标点也不是结尾，说明引文是在**一个词的中间**截断的
#: （"量子力学是" 命中 "量子力学是什么"）—— 那是实质分歧，不是标点差异。
BOUNDARY = set("。！？；，、：:;,.!?\"()（）[]【】-·《》〈〉")


def norm(text: str) -> str:
    """去掉**全部**空白后比较。

    与 `agent/handoff.py::verify_stated_quote` 同一口径：空白不算改字，
    换行/缩进/全角空格都不该让一条真引文被判成假的。
    """
    return re.sub(r"\s+", "", text)


def fold(text: str) -> str:
    """抹平空白 + 引号写法 + 破折号写法。**不改字、不删字。**"""
    return norm(text).translate(_FOLD)


def strip_tail(text: str) -> str:
    """去掉片段末尾的句末标点。"""
    return fold(text).rstrip(TAIL_PUNCT)


def no_paren(text: str) -> str:
    """去掉括号及其内容。**两侧都要做** —— 漏掉的是引文那侧时也要能命中。"""
    return PAREN.sub("", text)


def follows_boundary(hay_fold: str, pos: int, length: int) -> bool:
    """原文在片段结束处是否刚好到了词/分句边界。"""
    end = pos + length
    return end >= len(hay_fold) or hay_fold[end] in BOUNDARY


def divergence(frag: str, material: str) -> tuple[str, str, int]:
    """找出片段与原文的**分歧点**。

    返回 `(引文侧后文, 原文侧后文, 命中前缀长度)`。
    二分找「最长的、仍然出现在原文里的前缀」—— 前缀性质保证二分成立。
    报告分歧点而不是只报"找不到"，是因为**要改哪里**才是这个工具的价值。
    """
    lo, hi = 0, len(frag)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if frag[:mid] in material:
            lo = mid
        else:
            hi = mid - 1
    pos = material.find(frag[:lo]) if lo else -1
    frag_tail = frag[lo : lo + 16]
    mat_tail = material[pos + lo : pos + lo + 16] if pos >= 0 else "（材料里完全没有）"
    return frag_tail, mat_tail, lo


# --------------------------------------------------------------------------- #
# 题目对象
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BenchItem:
    """一道校验过的题。**构造即校验** —— 缺字段在加载期就报错，不留到判分时。"""

    item_id: str
    variable_id: str
    channel: str
    method_id: str
    role: str
    difficulty: str
    domain: str
    domain_familiarity: str
    time_min: float
    language: str
    media: str
    scoring: dict
    source: str
    license: str
    status: str
    title: str
    material_path: Path | None
    material_text: str
    anchors: dict
    key_points_question: dict | None
    exact_questions: list[dict]
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def weight(self) -> float:
        return DIFFICULTY_WEIGHT[self.difficulty]

    @property
    def is_anchor(self) -> bool:
        return self.role == "anchor"


def apply_aliases(raw: dict) -> dict:
    """把作者侧的别名字段搬到规格名下。两个都写且不一致 → 报错。

    报错而不是"以规格名为准"，是因为那会**静默丢掉作者写的东西** ——
    而作者写 `id` 与 `item_id` 两个不同值时，他显然有一个是有意的。
    """
    out = dict(raw)
    for alias, canonical in FIELD_ALIASES.items():
        if alias not in out:
            continue
        value = out.pop(alias)
        if canonical in out and out[canonical] != value:
            raise ItemSpecError(
                f"`{alias}` 与 `{canonical}` 同时存在且取值不同"
                f"（{value!r} vs {out[canonical]!r}）—— 不知道该信哪个"
            )
        out.setdefault(canonical, value)
    return out


def missing_spec(raw: dict) -> tuple[list[str], list[str]]:
    """返回 `(阻断字段缺哪些, 抽样字段缺哪些)`。**不猜、不填默认值。**"""
    blocking = [k for k in SPEC_BLOCKING if raw.get(k) is None]
    sampling = [k for k in SPEC_SAMPLING if raw.get(k) is None]
    return blocking, sampling


def load_item(
    path: Path,
    *,
    materials_dir: Path | None = None,
    require_material: bool = True,
) -> BenchItem:
    """读一道题并校验。**任何规格问题都在这里抛出，不往后传。**

    刻意不提供"缺了就用默认值"的口子：`role` 默认成 `regular` 会让
    一道本该当锚题的题悄悄退出等值化，而症状是"分数慢慢变得不可比" ——
    这种错没人会归因到一行默认值上。
    """
    raw = apply_aliases(json.loads(path.read_text(encoding="utf-8-sig")))
    blocking, sampling = missing_spec(raw)
    if blocking:
        detail = "；".join(f"{k}（{SPEC_BLOCKING[k]}）" for k in blocking)
        raise ItemSpecError(f"{path.name} 缺**阻断链路**的字段：{detail}")
    if sampling:
        detail = "；".join(f"{k}（{SPEC_SAMPLING[k]}）" for k in sampling)
        raise ItemSpecError(f"{path.name} 缺会影响抽样的字段：{detail}")

    if raw["role"] not in VALID_ROLES:
        raise ItemSpecError(f"{path.name} role={raw['role']!r} 不在 {VALID_ROLES}")
    if raw["difficulty"] not in VALID_DIFFICULTIES:
        raise ItemSpecError(
            f"{path.name} difficulty={raw['difficulty']!r} 不在 {VALID_DIFFICULTIES}"
        )
    if raw["domain_familiarity"] not in VALID_FAMILIARITY:
        raise ItemSpecError(
            f"{path.name} domain_familiarity={raw['domain_familiarity']!r}"
            f" 不在 {VALID_FAMILIARITY}"
        )

    material_text = ""
    material_path: Path | None = None
    if materials_dir is not None:
        candidates = [
            materials_dir / f"{raw['item_id']}.md",
            materials_dir / f"{raw['item_id']}.txt",
        ]
        material_path = next((c for c in candidates if c.is_file()), None)
        if material_path is None and require_material:
            raise ItemSpecError(
                f"{path.name} 找不到对应材料：期望 {candidates[0].name}"
                f" 或 {candidates[1].name}（在 {materials_dir}）"
            )
        if material_path is not None:
            material_text = material_path.read_text(encoding="utf-8-sig")

    anchors = raw.get("anchors") or {}
    kp = raw.get("key_points_question")
    exact = raw.get("exact_questions") or []
    if not isinstance(anchors, dict) or not anchors:
        raise ItemSpecError(f"{path.name} 缺少 `anchors`（埋点）—— 无法核对评分指南")
    if not isinstance(kp, dict):
        raise ItemSpecError(f"{path.name} 缺少 `key_points_question`")
    if not exact:
        raise ItemSpecError(
            f"{path.name} 没有 `exact_questions` —— 提取题之外没有可客观判分的题"
        )

    return BenchItem(
        item_id=str(raw["item_id"]),
        variable_id=str(raw["variable_id"]),
        channel=str(raw["channel"]),
        method_id=str(raw["method_id"]),
        role=str(raw["role"]),
        difficulty=str(raw["difficulty"]),
        domain=str(raw["domain"]),
        domain_familiarity=str(raw["domain_familiarity"]),
        time_min=float(raw["time_min"]),
        language=str(raw["language"]),
        media=str(raw["media"]),
        scoring=dict(raw["scoring"]),
        source=str(raw["source"]),
        license=str(raw["license"]),
        status=str(raw["status"]),
        title=str(raw.get("title") or raw["item_id"]),
        material_path=material_path,
        material_text=material_text,
        anchors=anchors,
        key_points_question=kp,
        exact_questions=list(exact),
        raw=raw,
    )


def load_bank(
    items_dir: Path, *, materials_dir: Path | None = None
) -> list[BenchItem]:
    """加载整个题库目录。**一道不合法就整体失败**（不跳过）。

    跳过坏题的运行会**悄悄少测一个变量**，而报告上只显示"这次跑了 14 题"
    —— 少的那一题没有任何痕迹。宁可跑不起来。
    """
    paths = sorted(items_dir.glob("*.json"))
    if not paths:
        raise ItemSpecError(f"{items_dir} 里没有任何 *.json")
    items = [
        load_item(p, materials_dir=materials_dir or items_dir.parent / "materials")
        for p in paths
    ]
    seen: dict[str, str] = {}
    for item, path in zip(items, paths, strict=True):
        if item.item_id in seen:
            raise ItemSpecError(
                f"item_id 重复：{item.item_id}（{seen[item.item_id]} 与 {path.name}）"
            )
        seen[item.item_id] = path.name
    return items


__all__ = [
    "ANSWER_SEP",
    "BLANK",
    "BOUNDARY",
    "BenchItem",
    "DASHES",
    "DIFFICULTY_WEIGHT",
    "ELLIPSIS",
    "FIELD_ALIASES",
    "ItemSpecError",
    "PAREN",
    "QUOTES",
    "SPEC_BLOCKING",
    "SPEC_SAMPLING",
    "TAIL_PUNCT",
    "VALID_DIFFICULTIES",
    "VALID_FAMILIARITY",
    "VALID_ROLES",
    "apply_aliases",
    "divergence",
    "fold",
    "follows_boundary",
    "load_bank",
    "load_item",
    "missing_spec",
    "no_paren",
    "norm",
    "strip_tail",
]
