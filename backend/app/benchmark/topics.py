"""题目主题表 —— **封闭**分类表的唯一读取入口。

用户的要求：「后续我们所有题目都按照这份清单，**不要给随机题目**」。
这句话要成立，前提是每道题的领域必须是**这张表上的一个引用**，
而不是一段自由文本。现在题库里写的是：

    "domain": "地质学 / 海洋生物学"

从这个字符串里**算不出**它是否落在某人的熟悉区，所以「按清单抽题」
在那时是无法实现的，而不是"还没实现"。

## 数据在哪

数据文件是 `app/seeds/domain_topics.json`（**单一来源**）。
这个模块只读它，不复制它 —— 两份分类表的下场是题目引用了其中一份、
校验器查的是另一份，于是"校验通过"不再意味着"抽题认得"。

## 为什么 `app/benchmark` 直接读 seeds 下的文件，而不是 import seeds 模块

`app/benchmark/` 的立身之本是这个包**纯**：没有数据库、不需要能力令牌、
不 import `app.router`（理由写在 `app/benchmark/__init__.py`）。
一个 JSON 数据文件不破坏这条 —— 它是数据，不是行为。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parents[1] / "seeds" / "domain_topics.json"


class TopicError(ValueError):
    """引用了主题表里不存在的专题，或主题表本身有问题。"""


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        raw = _DATA.read_text(encoding="utf-8")
    except FileNotFoundError as exc:  # pragma: no cover - 部署问题，不是数据问题
        raise TopicError(f"主题表文件不存在：{_DATA}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover
        raise TopicError(f"主题表不是合法 JSON：{_DATA} —— {exc}") from exc


def groups() -> list[dict]:
    """大类列表（含各自的 topics）。界面分组显示用。"""
    return _data()["groups"]


@lru_cache(maxsize=1)
def topic_ids() -> frozenset[str]:
    return frozenset(t["topic_id"] for g in groups() for t in g["topics"])


@lru_cache(maxsize=1)
def topic_labels() -> dict[str, str]:
    return {t["topic_id"]: t["label"] for g in groups() for t in g["topics"]}


@lru_cache(maxsize=1)
def topic_covers() -> dict[str, str]:
    """`topic_id` → 边界说明（帮助人判断"我算不算熟悉这个"）。"""
    return {t["topic_id"]: t.get("covers", "") for g in groups() for t in g["topics"]}


@lru_cache(maxsize=1)
def group_of() -> dict[str, str]:
    return {t["topic_id"]: g["group_id"] for g in groups() for t in g["topics"]}


@lru_cache(maxsize=1)
def group_labels() -> dict[str, str]:
    return {g["group_id"]: g["label"] for g in groups()}


def label_of(topic_id: str) -> str:
    """`BIO-MARINE` → `海洋生物学`。未知 id **报错**，不返回 id 本身。

    返回 id 看起来更"宽容"，但它会把一处数据错误（拼错的 id）
    伪装成一处显示风格问题 —— 而这道题的取样资格要靠这个 id 判断。
    """
    labels = topic_labels()
    if topic_id not in labels:
        raise TopicError(f"主题表里没有这个专题：{topic_id!r}")
    return labels[topic_id]


def describe(topic_id: str) -> str:
    """`BIO-MARINE（海洋生物学：海洋生物类群、深海生态…）` —— 给人看的。"""
    return f"{label_of(topic_id)}（{topic_covers().get(topic_id, '')}）"


def require_topics(ids: object, *, where: str) -> list[str]:
    """校验一个题目上的 `topics`。

    规则（与 `load_item` 的其余部分同一个口径：**不猜、不填默认值**）：

    - 必须是**非空列表**（一项也行 —— 现有题目里有一道只落在一个专题上）
    - 每项必须是主题表里的 id
    - 不得重复（同一道题重复列一个专题，会让"轮换"把它算两次）
    """
    if not isinstance(ids, list) or not ids:
        raise TopicError(
            f"{where}：`topics` 必须是非空的专题 id 列表（现在：{ids!r}）—— "
            f"它是「按清单抽题」的唯一依据；缺了这道题就无法被正确抽取"
        )
    known = topic_ids()
    bad = [x for x in ids if not isinstance(x, str) or x not in known]
    if bad:
        raise TopicError(
            f"{where}：这些专题不在主题表里 {bad} —— "
            f"拼错的 id 会让这道题静默地抽不出来（或抽错），"
            f"所以这里直接报错。全部可选值见 app/seeds/domain_topics.json"
        )
    if len(set(ids)) != len(ids):
        raise TopicError(f"{where}：`topics` 里有重复项 {ids!r}")
    return list(ids)


def validate_catalog() -> list[str]:
    """检查主题表**自己**是否自洽。返回问题列表（空 = 通过）。

    这是给校验器与测试用的：题目引用得对，前提是表本身没坏。
    """
    problems: list[str] = []
    seen: dict[str, str] = {}
    for g in groups():
        gid = g.get("group_id", "")
        if not g.get("label"):
            problems.append(f"大类 {gid!r} 没有 label")
        topics = g.get("topics") or []
        if not topics:
            problems.append(f"大类 {gid!r} 一个专题都没有")
        for t in topics:
            tid = t.get("topic_id", "")
            if not tid:
                problems.append(f"大类 {gid!r} 里有一个专题没有 topic_id")
                continue
            if tid in seen:
                problems.append(f"专题 id 重复：{tid}（{seen[tid]} 与 {gid}）")
            seen[tid] = gid
            if not tid.startswith(f"{gid}-"):
                problems.append(f"专题 {tid} 的前缀与所在大类 {gid} 不一致")
            if not t.get("label"):
                problems.append(f"专题 {tid} 没有 label")
            if not t.get("covers"):
                problems.append(f"专题 {tid} 没有 covers（人无法判断算不算熟悉）")
    return problems
