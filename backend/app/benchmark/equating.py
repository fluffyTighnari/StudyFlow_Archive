"""等值化：把一次运行的分数换算到**与前几次可比**的刻度上（§5.6，P12）。

## 它要解决的问题

同一个人的两次运行不是同一套题。若第二次抽到的题整体更难，他会显得"退步"——
而那不是他的变化，是**题目的变化**。锚题（每次都出的那几道）就是用来发现这件事的。

## ★ 它做不到什么（这一节比上面那节更重要）

一个用户做不了 IRT：没有几百人、几百题去估计题目参数。用锚题做校正，
底下压着一个**必须说出口的假设**：

> 他对同一道锚题的能力没有变化。

所以任何一次校正都在说：「我假设这道锚题上你和我都没变；
于是这次与基线之间的差，我**归给仪器**。」

这个假设在两次运行挨得近时大致成立，隔半年就不成立 ——
而系统**分不出**"他退步了"与"仪器漂了"。
所以：

- 校正量、参与校正的锚题、基线来自哪一次运行，全部**写进结果**，
  让读的人自己判断这个假设成不成立；
- 可用锚题不足时**不校正**（`shift=None`），并说明为什么 ——
  一道锚题的波动分不清是能力还是仪器，那时把差量归给仪器是**编一个理由**。

## 与红线 7 的关系

正误题**只做标尺**。锚题在这里的用途正是标尺用途：
它不产出读数，它**校准读数**。所以锚题分可以是正误题的通过率，
也可以是代码题/要点题的分 —— 只要它在两次运行里是**同一道题**。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

#: 至少要有这么多道**共有锚题**才敢把差量归给仪器。
#:
#: 一道锚题的波动分不清是能力还是仪器（这道题恰好他这次没抓住？），
#: 把差量按一道题归给仪器，是在用**一个数**解释一件需要多个数才能说的事。
#: 与 `MIN_ITEMS_OBSERVATION = 2` 同一个道理。
MIN_EQUATING_ANCHORS = 2

#: 换算后的分数不出量程。**夹范围**在这里不是防御性编程：
#: 校正量来自另一批题，量纲不同，越界是真实可能发生的。
SCALE_MIN = 0.0
SCALE_MAX = 10.0


@dataclass(frozen=True)
class AnchorComparison:
    """一道锚题的这次读数 vs 基线读数。"""

    item_id: str
    baseline: float
    current: float

    @property
    def delta(self) -> float:
        """`基线 - 本次`。**正数 = 这次比基线低**（该往上补）。"""
        return self.baseline - self.current


@dataclass(frozen=True)
class EquatingResult:
    """校正结果：一个量、一批证据、一句必须说出口的假设。"""

    #: 加到原始方法分上的量。`None` = **做不了校正**（不是"校正量为 0"）。
    shift: float | None
    anchors: tuple[AnchorComparison, ...]
    #: 不能校正时的原因；`shift is not None` 时为空。
    reason: str
    #: 参与校正的锚题里，基线取自哪几次运行（供人核）。
    baseline_runs: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return self.shift is not None

    def correct(self, score: float | None) -> float | None:
        """把校正量加到分数上，并夹在量程内。`score is None` 原样返回。

        ★ 没测到就是没测到：给一个 `None` 的分数加校正量会把它变成数字，
        而那正是"没测到 ≠ 0 分"要防的事。
        """
        if score is None or self.shift is None:
            return score
        return round(min(SCALE_MAX, max(SCALE_MIN, score + self.shift)), 2)

    def describe(self) -> str:
        """给用户/日志的一句话，**必须带上假设**。

        ★ 两句都必须出现「**等值化**」这个词：这句话会被用户当作
        "这一次到底校正了没有"的回答，而它同时要与
        「等值化校正尚未实现」那种**过期的话**区分得开（P15 把最后一句
        过期的话删掉了，原因就是它和这段描述出现在同一个响应里）。
        """
        if self.shift is None:
            return f"等值化：未校正 —— {self.reason}"
        pairs = "、".join(
            f"{a.item_id} 基线 {a.baseline:g} → 本次 {a.current:g}"
            for a in self.anchors
        )
        return (
            f"等值化：校正 {self.shift:+.2f}"
            f"（{len(self.anchors)} 道锚题：{pairs}）"
            f"｜依据的假设：**他对这几道锚题的能力与基线时相同** —— "
            f"于是差值归给仪器。这个假设隔得越久越不成立"
        )


def equate(
    current: Mapping[str, float | None],
    baselines: Mapping[str, float],
    *,
    baseline_runs: Mapping[str, str] | None = None,
    min_anchors: int = MIN_EQUATING_ANCHORS,
) -> EquatingResult:
    """本次锚题读数 vs 基线 → 校正量。

    参数都是"锚题 id → 分数"。`current` 里 `None` 的题（这次没测到）**不参与**：
    拿一个没测到的锚题去校正，等于用一个空缺解释另一个空缺。
    """
    pairs: list[AnchorComparison] = []
    runs: list[str] = []
    seen_runs: set[str] = set()
    for item_id, now in sorted(current.items()):
        if now is None or item_id not in baselines:
            continue
        pairs.append(
            AnchorComparison(item_id=item_id, baseline=float(baselines[item_id]), current=float(now))
        )
        run_id = (baseline_runs or {}).get(item_id)
        if run_id and run_id not in seen_runs:
            seen_runs.add(run_id)
            runs.append(run_id)

    if not pairs:
        return EquatingResult(
            shift=None,
            anchors=(),
            reason=(
                "这次与历史运行没有共有的锚题读数 —— "
                "**没有基线就无从校正**，本次读数将作为将来的基线"
            ),
        )
    if len(pairs) < min_anchors:
        return EquatingResult(
            shift=None,
            anchors=tuple(pairs),
            reason=(
                f"共有的锚题只有 {len(pairs)} 道（需要 ≥{min_anchors}）—— "
                f"一道锚题的波动分不清是能力还是仪器，把差量归给仪器是**编一个理由**"
            ),
            baseline_runs=tuple(runs),
        )

    shift = round(sum(pair.delta for pair in pairs) / len(pairs), 2)
    return EquatingResult(
        shift=shift, anchors=tuple(pairs), reason="", baseline_runs=tuple(runs)
    )


__all__ = [
    "MIN_EQUATING_ANCHORS",
    "SCALE_MAX",
    "SCALE_MIN",
    "AnchorComparison",
    "EquatingResult",
    "equate",
]
