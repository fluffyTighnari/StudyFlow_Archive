"""分段说明的**取值**：`6.2` 分落在哪一段、那一段在说什么（P10）。

## 为什么这也要有一份纯函数

区间边界是这个模块里唯一容易出错的地方，而它错起来**没有任何症状**：

- `4.0` 属于「低端」还是「中段」？
- `10.0` 属于「高端」吗（还是被"小于 10"漏掉，让满分变成一个没有说明的分数）？
- 两段重叠时谁赢？

这三件事若散在界面、API、报告各写一次，同一个 `4.0` 会在两个地方显示成
两段不同的话 —— 而用户看到的是"这个系统说话前后不一致"，没人会去查边界。

所以约定只有一处：**左闭右开，最后一段右闭**。

## 为什么"没有说明"是一个必须说出口的状态

分段说明是**内容**（会渐进补），而分数是**机制**。一段都没写时，
调用方必须能区分"这个分没有对应的说明"与"这个分是 0"——
`band_for` 返回 `None` 而不是编一个默认段。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: 分段说明覆盖的完整区间。分数一定落在这里面（0-10 的量表）。
SCALE_MIN = 0.0
SCALE_MAX = 10.0


@dataclass(frozen=True)
class Band:
    low: float
    high: float
    label: str
    description: str
    draft: bool = False


class BandError(ValueError):
    """分段说明本身写错了（不覆盖、有洞、重叠、区间倒置）。**不修，报出来。**"""


def as_bands(raw: Sequence[dict]) -> tuple[Band, ...]:
    """把 JSON 形态的分段说明转成 `Band`，顺带校验。

    ★ 校验放在这里而不是"信任入库的数据"：入库的那份可能来自手写种子、
    也可能来自以后的界面编辑。**一条有洞的分段表意味着某些分数没有说明**，
    而那正是我们要防的静默状态。
    """
    bands: list[Band] = []
    for entry in raw:
        try:
            bands.append(
                Band(
                    low=float(entry["low"]),
                    high=float(entry["high"]),
                    label=str(entry["label"]),
                    description=str(entry["description"]),
                    draft=bool(entry.get("draft", False)),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BandError(f"分段说明缺字段或取值非法：{entry!r}（{exc}）") from exc
    check_bands(tuple(bands))
    return tuple(bands)


def check_bands(bands: Sequence[Band]) -> None:
    """分段表必须**首尾相接、无洞、无重叠**地覆盖 0-10。

    ★ 检查的**顺序**是刻意的：先逐段查自身是否合法（区间倒置、空说明），
    再查整表的覆盖与相接。

    反过来的话，一张写着 `10-4` 的表会被报成"最高一段到不了 10"——
    那句话把人引向"少写了一段"，而真正的问题是那一段写反了。
    **诊断信息指错方向，比没有诊断信息更贵。**
    """
    if not bands:
        raise BandError("分段说明是空的 —— 那么每个分数都没有说明")
    for band in bands:
        if band.high <= band.low:
            raise BandError(f"分段 {band.label} 的区间是倒的或空的：{band.low}-{band.high}")
        if not band.description.strip():
            raise BandError(f"分段 {band.label} 没有说明文字")
    ordered = sorted(bands, key=lambda band: band.low)
    if ordered[0].low != SCALE_MIN:
        raise BandError(f"分段说明没有从 {SCALE_MIN} 开始（最低一段从 {ordered[0].low}）")
    if ordered[-1].high != SCALE_MAX:
        raise BandError(f"分段说明没有覆盖到 {SCALE_MAX}（最高一段到 {ordered[-1].high}）")
    for left, right in zip(ordered, ordered[1:]):
        if left.high != right.low:
            where = "重叠" if left.high > right.low else "有洞"
            raise BandError(
                f"分段说明在 {left.high} 处{where}："
                f"{left.label} 到 {left.high}，{right.label} 从 {right.low} 开始 —— "
                f"落在那里（或那段区间）的分数会有两个答案或没有答案"
            )


def band_for(bands: Sequence[Band], score: float) -> Band | None:
    """`score` 落在哪一段。**左闭右开，最后一段右闭。**

    `None` = 这张表里没有它的位置（表为空、或分数在 0-10 之外）。
    刻意不夹范围、不兜底：一个 11 分的分数落到"高端"会掩盖上游的错。
    """
    if not bands:
        return None
    ordered = sorted(bands, key=lambda band: band.low)
    for index, band in enumerate(ordered):
        last = index == len(ordered) - 1
        if band.low <= score < band.high or (last and score == band.high):
            return band
    return None


def describe_score(bands: Sequence[Band], score: float | None) -> str:
    """给用户的一句话。`score is None`（没测到）与"没有说明"分开说。

    ★ 三种状态、三句话，不能混成一句：

    | 状态 | 该说的话 |
    |---|---|
    | `None` | 这次没测到（**不是 0 分**） |
    | 有分数、没有分段说明 | 分数本身有效，只是还没写说明 |
    | 有分数、有说明 | 说明那句话 |
    """
    if score is None:
        return "这次没测到 —— 不是一个 0 分"
    band = band_for(bands, score)
    if band is None:
        return f"{score} 分（这个变量还没有写分段说明）"
    prefix = "〔初稿〕" if band.draft else ""
    return f"{score} 分 · {band.label}（{band.low:g}-{band.high:g}）：{prefix}{band.description}"


__all__ = [
    "SCALE_MAX",
    "SCALE_MIN",
    "Band",
    "BandError",
    "as_bands",
    "band_for",
    "check_bands",
    "describe_score",
]
