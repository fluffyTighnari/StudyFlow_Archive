"""抽题：**从清单与历史算出一套题**，而不是"随便抓几道"。

## 为什么抽题要是一个纯函数模块

抽题里全是**会静默移动分数的决定**：抽了哪些难度、抽了哪些专题、
哪些题他做过。这些决定如果散在 API 层或前端，症状永远是同一句话 ——
「这次的分数好像不太对」，而没有任何一步会报错。

所以这里只有纯函数：**给定题库 + 清单 + 历史 + 种子 → 一套题**，
不碰数据库、不碰 HTTP、不看时钟。于是它可以被逐条测试，
也于是**同一种子必须抽出同一套题**（v0.3 §6.3 的"可复现"）。

## 三条规则，各有各的理由

### ① 锚题每卷必出（≥2 题）

锚题是**等值化的分母**（§5.6）。一次运行里锚题通过率一变，
全部历史分数的可比性同时失效 —— 所以"这次要不要抽锚题"不能是抽签结果。

### ② 难度直方图固定为「易 1 : 中 2 : 难 1」

如果抽样不管难度，题池越大分数噪声越大，而**噪声会被误读成"我进步了"**。
固定构成让"换题"对分数的影响在期望上为零，剩下的才是真实变化。

### ③ 领域：定向过滤 + 跨运行轮换

领域和难度是**同一类东西** —— 它们都会静默地移动分数，而且都不会报错。

- **定向**：`measured` 通道要的是"他在陌生材料上的表现"。
  一份材料**只要有一个专题他勾了熟悉，就不算陌生** —— 因为他能靠那一格读下去
  （`BenchmarkItem.topics` 是列表正是为此）。
  少了这条过滤，V-SE 会静默地测出他的领域知识，
  而计划生成器会把它当成"他的提取能力"。**这个链条上没有任何一步会报错。**
- **轮换**：同领域反复出现会让材料**变熟悉**，于是分数被静默抬高。
  所以优先选**最久没用过**的专题，而不只是"一次运行内不重复"。

## 抽不出来就**拒绝**，不降级

这一条是本模块最重要的设计。缺一道"难"题时，可以：
（甲）用一道"中"题补上 —— 分数悄悄偏向一个方向；
（乙）少抽一道 —— 方法分跌破最小题数，`NULL` 加 `partial`；
（丙）拒绝，并说清缺什么。

**只有丙不会说谎。** 甲和乙都会产出一次"看起来正常"的运行，
而它们的分数与别的运行**不可比** —— 那种不可比没有任何症状。

（`_require` 里每一条拒绝都带**可操作的数字**：缺几道、缺哪个难度、
被熟悉度过滤掉几道。只说"抽不出题"的报告，下场是没人能修它。）
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from app.benchmark.items import BenchItem

#: 难度直方图：一个"块"= 易 1 + 中 2 + 难 1（v0.3 §6.3）。
#: 用块而不是散装数字，是因为它表达了**比例**这件事；
#: 散装写法（`{"易": 1, "中": 2, "难": 1}`）在块数改变时要同时改三处。
HISTOGRAM_BLOCK: tuple[str, ...] = ("易", "中", "中", "难")

#: 锚题下限。少于两道，"锚题通过率"就没有分辨力（§6.3）。
MIN_ANCHORS = 2


class SamplingRefused(ValueError):
    """抽不出一套**符合规则**的题。**不降级** —— 见模块文档结尾。"""


# --------------------------------------------------------------------------- #
# 历史
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class History:
    """过去那些运行留下的、会影响这次抽题的两件事。

    ★ 为什么单独一个类型，而不是三个 dict 参数：
    这三个映射必须**同源**（同一次遍历算出来的）。分开传时，
    很容易出现"用了新运行的 exposure，却配了旧运行的 recency" ——
    而那种错的表现是轮换悄悄失效，看不出任何异常。
    """

    #: item_id → 他做过几次。
    item_exposure: Mapping[str, int] = field(default_factory=dict)
    #: item_id → 最近一次出现在第几次运行（越大越新）。没出现过的不在表里。
    item_last_run: Mapping[str, int] = field(default_factory=dict)
    #: topic_id → 最近一次出现在第几次运行。
    topic_last_run: Mapping[str, int] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "History":
        return cls()


def summarize_history(
    runs_oldest_first: Iterable[tuple[Sequence[str], Sequence[str]]],
) -> History:
    """把「历次运行用过的题与专题」压成一份 `History`。

    参数是 `(item_ids, topic_ids)` 的序列，**按时间从旧到新**。
    序号就是它在序列里的位置 —— 这样"最久没用过"有一个可比较的值，
    而不需要引入时间戳（时间戳会让测试依赖时钟）。
    """
    exposure: dict[str, int] = {}
    item_last: dict[str, int] = {}
    topic_last: dict[str, int] = {}
    for index, (item_ids, topic_ids) in enumerate(runs_oldest_first):
        for item_id in item_ids:
            exposure[item_id] = exposure.get(item_id, 0) + 1
            item_last[item_id] = index
        for topic_id in topic_ids:
            topic_last[topic_id] = index
    return History(exposure, item_last, topic_last)


# --------------------------------------------------------------------------- #
# 计划
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SampledItem:
    """抽中的一道题，**带上它是怎么被抽中的**。

    `why` 不是装饰：用户问"为什么考这篇"时，答案必须是算出来的那一句，
    而不是事后编的一句解释。
    """

    item_id: str
    role: str
    difficulty: str
    topics: tuple[str, ...]
    why: str

    @property
    def is_anchor(self) -> bool:
        return self.role == "anchor"


@dataclass(frozen=True)
class SamplingPlan:
    """一套题，以及"为什么是这一套"。"""

    variable_id: str
    channel: str
    method_id: str
    form: str
    seed: int
    items: tuple[SampledItem, ...]
    #: 本次涉及的专题（按首次出现顺序）—— 要写进 `benchmark_run.topic_snapshot`。
    topics: tuple[str, ...]
    notes: tuple[str, ...] = ()

    @property
    def item_ids(self) -> list[str]:
        return [item.item_id for item in self.items]

    @property
    def anchor_ids(self) -> list[str]:
        return [item.item_id for item in self.items if item.is_anchor]

    @property
    def difficulty_mix(self) -> dict[str, int]:
        mix: dict[str, int] = {}
        for item in self.items:
            mix[item.difficulty] = mix.get(item.difficulty, 0) + 1
        return mix

    def describe(self) -> str:
        """一行给人看的摘要（进 e2e 报告与界面提示）。"""
        mix = "、".join(f"{k}{v}" for k, v in sorted(self.difficulty_mix.items()))
        return (
            f"{self.form}：{len(self.items)} 题（锚题 {len(self.anchor_ids)}），"
            f"难度 {mix}，专题 {len(self.topics)} 个，种子 {self.seed}"
        )


# --------------------------------------------------------------------------- #
# 主函数
# --------------------------------------------------------------------------- #


def plan_sampling(
    bank: Sequence[BenchItem],
    *,
    variable_id: str,
    channel: str,
    method_id: str,
    form: str,
    familiar: Iterable[str],
    interested: Iterable[str] = (),
    history: History | None = None,
    seed: int = 0,
    blocks: int = 1,
) -> SamplingPlan:
    """算出一套题。**抽不出符合规则的题就拒绝**，理由里带数字。

    `familiar` 是他自己勾的"我熟悉哪些专题"；`interested` 是第二次勾选
    （"在不熟悉里我对哪些更感兴趣"）。

    ★ `interested` **不参与筛选**，只影响优先级。这是 v0.3 §6 的
    「兴趣是记录，不是筛选」：用兴趣筛掉一半材料，等于让"他对什么感兴趣"
    变成"他实际被考了什么"，而那正是红线 7（用一个理由解释自己的行为）
    换个面孔回来。所以它出现在 `why` 里，不出现在过滤条件里。
    """
    history = history or History.empty()
    familiar_set = set(familiar)
    interested_set = set(interested)

    cell = [
        item
        for item in bank
        if item.variable_id == variable_id
        and item.channel == channel
        and item.method_id == method_id
        and item.status != "retired"
    ]
    if not cell:
        raise SamplingRefused(
            f"题库里没有 {variable_id}/{channel}/{method_id} 的题（共 {len(bank)} 道题）。"
            f"题库缺了这一个格子的题，而不是抽题规则抽不出"
        )

    anchors = [item for item in cell if item.is_anchor]
    if len(anchors) < MIN_ANCHORS:
        raise SamplingRefused(
            f"{variable_id}/{channel} 只有 {len(anchors)} 道锚题，至少要 {MIN_ANCHORS} 道 —— "
            f"锚题是等值化的分母（§5.6），少于两道时'锚题通过率'没有分辨力"
        )

    regulars = [item for item in cell if not item.is_anchor]
    unfamiliar, dropped_familiar = _split_by_familiarity(regulars, familiar_set)

    want = _histogram_wants(blocks)
    picked_regulars = _pick_regulars(
        unfamiliar,
        want=want,
        history=history,
        interested=interested_set,
        seed=seed,
        dropped_familiar=dropped_familiar,
    )

    items = tuple(
        [
            SampledItem(
                item_id=item.item_id,
                role=item.role,
                difficulty=item.difficulty,
                topics=item.topics,
                why="锚题：每卷必出，它是本次等值化的基准（§5.6）",
            )
            for item in sorted(anchors, key=lambda i: i.item_id)
        ]
        + picked_regulars
    )

    topics: list[str] = []
    for item in items:
        for topic_id in item.topics:
            if topic_id not in topics:
                topics.append(topic_id)

    notes = _notes(
        anchors=anchors,
        picked=picked_regulars,
        dropped_familiar=dropped_familiar,
        topics=topics,
        interested=interested_set,
        history=history,
    )

    return SamplingPlan(
        variable_id=variable_id,
        channel=channel,
        method_id=method_id,
        form=form,
        seed=seed,
        items=items,
        topics=tuple(topics),
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #


def _split_by_familiarity(
    items: Sequence[BenchItem], familiar: set[str]
) -> tuple[list[BenchItem], list[BenchItem]]:
    """按「这份材料对他陌生吗」分两堆。

    ★ 判据是**"他勾熟悉的专题里一个都没沾到"**，不是"主专题不熟悉"。
    一份材料涉及两个专题时，只要有一个他熟，他就能靠那一格读下去 ——
    于是它**不是**在测"面对陌生材料"。
    """
    unfamiliar: list[BenchItem] = []
    dropped: list[BenchItem] = []
    for item in items:
        (dropped if familiar.intersection(item.topics) else unfamiliar).append(item)
    return unfamiliar, dropped


def _histogram_wants(blocks: int) -> dict[str, int]:
    if blocks < 1:
        raise SamplingRefused(f"blocks 至少是 1，收到 {blocks}")
    wants: dict[str, int] = {}
    for difficulty in HISTOGRAM_BLOCK * blocks:
        wants[difficulty] = wants.get(difficulty, 0) + 1
    return wants


def _recency_of(item: BenchItem, history: History) -> int:
    """这道题"有多久没露面" —— 用它的专题里**最新**的那次计算。

    取最新而不是最旧：一道题挂着两个专题，其中一个上次刚用过，
    那这次再出现就已经算"重复领域"了。
    """
    return max(
        (history.topic_last_run.get(topic_id, -1) for topic_id in item.topics),
        default=-1,
    )


def _pick_regulars(
    pool: Sequence[BenchItem],
    *,
    want: Mapping[str, int],
    history: History,
    interested: set[str],
    seed: int,
    dropped_familiar: Sequence[BenchItem],
) -> list[SampledItem]:
    """按「未做过 → 最久没用过的领域 → 兴趣 → 种子」挑普通题。

    排序键就是这个优先级顺序。**排序而不是打分加权**：权重的取值会被
    反复讨论，而顺序可以逐条说出理由 —— 而且顺序里每一层都能单独测试。
    """
    by_difficulty: dict[str, list[BenchItem]] = {}
    for item in pool:
        by_difficulty.setdefault(item.difficulty, []).append(item)

    shortfalls = {
        difficulty: count - len(by_difficulty.get(difficulty, []))
        for difficulty, count in want.items()
        if len(by_difficulty.get(difficulty, [])) < count
    }
    if shortfalls:
        detail = "；".join(f"{d} 缺 {n} 道" for d, n in sorted(shortfalls.items()))
        raise SamplingRefused(
            f"抽不出「易 1 : 中 2 : 难 1」这个构成：{detail}。"
            f"当前可用普通题 "
            f"{_count_by_difficulty(pool)}。"
            f"另有 {len(dropped_familiar)} 道题被他标成熟悉而被排除"
            f"（{'、'.join(i.item_id for i in dropped_familiar) or '无'}）。"
            f"★ 不用别的难度补缺 —— 那样分数会悄悄偏向一个方向，而且不会报错"
        )

    picked: list[SampledItem] = []
    for difficulty, count in sorted(want.items()):
        candidates = sorted(
            by_difficulty[difficulty],
            key=lambda i: _order_key(i, history, interested, seed),
        )
        for item in candidates[:count]:
            picked.append(
                SampledItem(
                    item_id=item.item_id,
                    role=item.role,
                    difficulty=item.difficulty,
                    topics=item.topics,
                    why=_why(item, history, interested),
                )
            )
    return picked


def _tiebreak(seed: int, item_id: str) -> float:
    """`(种子, 题目 id)` → 一个稳定的伪随机数。

    ★ **这里不能用 `rng.random()`**：`sorted(key=...)` 按**列表顺序**回调，
    所以"共享一个 rng"会让同一个种子在不同题库文件顺序下抽出不同的题 ——
    而"同一种子抽出同一套"是 §6.3 的明文要求。

    这个 bug 的发现方式值得记下来：**它不会在"跑一次"里出现**。
    只有把同一个题库倒过来喂进去、再比对结果才会暴露。
    所以 `test_candidates_are_order_independent` 是一条**顺序无关性**测试，
    而不是"抽出来的题对不对"的测试。

    用 `(seed, item_id)` 而不是 `hash(item_id)`：后者受 `PYTHONHASHSEED`
    影响，会让"同一份数据两次运行结果不同"，那比顺序依赖更难查。
    """
    return random.Random(f"{seed}:{item_id}").random()


def _order_key(
    item: BenchItem, history: History, interested: set[str], seed: int
) -> tuple:
    """优先级：未做过 → 最久没用过的领域 → 感兴趣 → 种子打散。

    ★ 最后那层用**种子**而不是 `item_id` 排序：按 id 排会让
    "总是先抽到 SE-TXT-001"，而那个顺序**看起来像规则其实不是** ——
    它会让题池的扩充永远只影响排在后面的题。
    """
    return (
        history.item_exposure.get(item.item_id, 0),
        _recency_of(item, history),
        0 if interested.intersection(item.topics) else 1,
        _tiebreak(seed, item.item_id),
    )


def _why(item: BenchItem, history: History, interested: set[str]) -> str:
    exposure = history.item_exposure.get(item.item_id, 0)
    parts = [f"{item.difficulty}题"]
    if exposure == 0:
        parts.append("没做过")
    else:
        parts.append(f"做过 {exposure} 次")
    recency = _recency_of(item, history)
    parts.append(
        "领域没用过" if recency < 0 else f"领域上次用于第 {recency + 1} 次运行"
    )
    if interested.intersection(item.topics):
        parts.append("在他勾的兴趣里（记录，不用于筛选）")
    return "；".join(parts)


def _count_by_difficulty(pool: Sequence[BenchItem]) -> str:
    counts: dict[str, int] = {}
    for item in pool:
        counts[item.difficulty] = counts.get(item.difficulty, 0) + 1
    if not counts:
        return "0 道"
    return "、".join(f"{k} {v} 道" for k, v in sorted(counts.items()))


def _notes(
    *,
    anchors: Sequence[BenchItem],
    picked: Sequence[SampledItem],
    dropped_familiar: Sequence[BenchItem],
    topics: Sequence[str],
    interested: set[str],
    history: History,
) -> list[str]:
    """给这一次抽题留几句**能核对的话**。"""
    notes: list[str] = []
    notes.append(
        f"锚题 {len(anchors)} 道全出（{ '、'.join(i.item_id for i in anchors) }）"
    )
    notes.append(f"普通题按「易 1 : 中 2 : 难 1」抽了 {len(picked)} 道")
    if dropped_familiar:
        notes.append(
            f"{len(dropped_familiar)} 道题因他勾了熟悉而被排除："
            f"{'、'.join(i.item_id for i in dropped_familiar)}"
        )
    if len(topics) < 2:
        notes.append(
            "★ 这次只涉及一个专题 —— 「同一运行内跨领域取样」没做到。"
            "题池在这一格还不够宽，补题时优先补另一个专题"
        )
    if interested:
        notes.append(
            "兴趣只影响优先级、不参与筛选（v0.3 §6：兴趣是记录，不是筛选）"
        )
    if not history.item_exposure:
        notes.append("这是他第一次跑 —— 没有历史可轮换，未做过的优先自然满足")
    return notes


__all__ = [
    "HISTOGRAM_BLOCK",
    "MIN_ANCHORS",
    "History",
    "SampledItem",
    "SamplingPlan",
    "SamplingRefused",
    "plan_sampling",
    "summarize_history",
]
