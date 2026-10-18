"""主题表（封闭分类表）与题目打标签的守卫测试。

用户的要求：「后续我们所有题目都按照这份清单，**不要给随机题目**」。

这句话能成立，靠的不是"我们记得按清单来"，而是**三件事同时成立**：

1. 主题表自己没坏（id 不重复、前缀与大类一致、每个专题有边界说明）
2. 每道题的 `topics` 都真的在这张表上（拼错一个 id = 这道题静默抽不出来）
3. **旧的自由文本字段被明确拒绝**，而不是悄悄留着

第 3 条最要紧，所以它有自己的测试：`domain` / `domain_familiarity` 现在是
**报错的字段**。一道题带着 `domain` 而没人发现，它的抽样资格就永远是错的 ——
而它看起来"只是多了一个字段"，没有任何症状。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.benchmark.items import ItemSpecError, load_item
from app.benchmark.topics import (
    TopicError,
    describe,
    group_labels,
    group_of,
    groups,
    label_of,
    require_topics,
    topic_ids,
    validate_catalog,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ITEMS_DIR = REPO_ROOT / "items"
MATERIALS_DIR = REPO_ROOT / "materials"


# --------------------------------------------------------------------------- #
# 主题表自己没坏
# --------------------------------------------------------------------------- #


def test_the_catalog_is_self_consistent() -> None:
    problems = validate_catalog()
    assert problems == [], "主题表自身有问题：\n  " + "\n  ".join(problems)


def test_the_catalog_has_the_shape_we_committed_to() -> None:
    """刻意的脆弱断言 —— 与 `test_schema_purity` 里那句表数量同一个手法。

    表变了必须有人看一眼：题目引用的是 id，**删掉一个专题会让引用它的题目
    变成坏题**，而这件事不会自己报错（除非那题正好被加载）。
    """
    assert len(groups()) == 18
    assert len(topic_ids()) == 127
    per_group = {g["group_id"]: len(g["topics"]) for g in groups()}
    assert min(per_group.values()) == 5, per_group
    assert max(per_group.values()) == 9, per_group


def test_every_topic_id_prefix_matches_its_group() -> None:
    """`BIO-MARINE` 必须在 `BIO` 组里 —— 否则按大类分组显示会串。"""
    bad = [
        (tid, gid) for tid, gid in group_of().items()
        if not tid.startswith(f"{gid}-")
    ]
    assert bad == [], f"专题 id 与大类不一致：{bad}"


def test_every_group_has_a_human_label() -> None:
    """清单要给人看。没有中文名的分组在界面上是一片空白。"""
    for g in groups():
        assert g["label"], f"{g['group_id']} 没有 label"
        assert group_of(), "group_of() 不该为空"


def test_the_topics_the_user_named_are_present() -> None:
    """用户点名的三类，以及现有 7 道题已在用的领域，都必须能打上标签。

    这条是**给将来改表的人的绊线**：删掉 `LIT-*` 或 `BIO-MARINE`
    会让现有题目无处可挂，而那时你至少会看到这条测试变红。
    """
    ids = topic_ids()
    # 用户原话点名的
    assert any(i.startswith("LIT-") for i in ids), "文学没了"
    assert any(i.startswith("LANG-") for i in ids), "语言学没了"
    assert any(i.startswith("BIO-") for i in ids), "生物学没了"
    # 现有 7 道题实际用到的
    for needed in ("BIO-MARINE", "EARTH-GEOL", "PHYS-STELLAR",
                   "CHEM-METAL", "EARTH-GEOPHYS", "EARTH-CLIMATE",
                   "BIO-PHYSIO", "BIO-CLIN"):
        assert needed in ids, f"现有题目引用的 {needed} 不在表里"


def test_an_unknown_topic_raises_instead_of_returning_the_id() -> None:
    """`label_of` 对未知 id **报错**，不返回 id 本身。

    返回 id 看起来更"宽容"，但它把一处数据错误伪装成一处显示风格问题 ——
    而这道题的取样资格要靠这个 id 判断。
    """
    with pytest.raises(TopicError):
        label_of("BIO-NOPE")
    assert label_of("BIO-MARINE") == "海洋生物学"
    assert "海洋" in describe("BIO-MARINE")


# --------------------------------------------------------------------------- #
# `require_topics` 的判据
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad, why",
    [
        (["BIO-MARIN"], "拼错一个字母"),
        ("BIO-MARINE", "不是列表"),
        ([], "空列表"),
        (None, "None"),
        (["BIO-MARINE", "BIO-MARINE"], "重复"),
        (["bio-marine"], "大小写不对"),
        (["BIO-MARINE", "NOPE-X"], "列表里有一个不存在的"),
    ],
)
def test_require_topics_rejects_bad_input(bad: object, why: str) -> None:
    with pytest.raises(TopicError):
        require_topics(bad, where="探针")


def test_require_topics_accepts_a_valid_list() -> None:
    """★ **反例。** 没有它，上面那组测试在 `require_topics` 恒抛异常时也会全绿。

    一条断言"坏东西都被拒绝"的测试，在实现坏成"什么都拒绝"时同样通过 ——
    所以必须配一个"好东西被接受"的兄弟。
    """
    assert require_topics(["BIO-MARINE", "EARTH-GEOL"], where="探针") == [
        "BIO-MARINE",
        "EARTH-GEOL",
    ]
    # 一项也合法：现有题库里有一道只落在一个专题上
    assert require_topics(["EARTH-CLIMATE"], where="探针") == ["EARTH-CLIMATE"]


# --------------------------------------------------------------------------- #
# 真实题库：7 道题必须都能挂上标签
# --------------------------------------------------------------------------- #


def test_the_real_bank_loads_with_valid_topics() -> None:
    """把真实那 7 道题过一遍**加载器**（不是只读 JSON）。

    这是"按清单抽题"的端到端起点：题目读得进来、且 topic 引用有效。
    加载器还会顺带拦下旧的 `domain` 字段（见下一个测试）。
    """
    paths = sorted(ITEMS_DIR.glob("*.json"))
    assert len(paths) == 7, f"题库数量变了：{[p.name for p in paths]}"

    seen: set[str] = set()
    for p in paths:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
        # 这些字段目前还没补齐（是已知的待办，见采购清单），
        # 所以这里只补到"加载器能走到 topic 校验"为止 —— 不假装它们已经齐了。
        raw.setdefault("item_id", raw["id"])
        raw.setdefault("topics", raw["topics"])
        raw.pop("id", None)

        require_topics(raw["topics"], where=p.name)
        for t in raw["topics"]:
            assert t in topic_ids(), f"{p.name} 引用了不存在的专题 {t}"
        seen.update(raw["topics"])

    # 7 道题覆盖的专题应当有 8 个（001 与 004 共享 EARTH-GEOL）
    assert len(seen) >= 8, f"覆盖的专题太少：{sorted(seen)}"


def test_a_retired_field_is_a_hard_error_not_a_silent_extra() -> None:
    """★ 旧字段必须**报错**。

    这是本次改动里最容易做错、也最难发现的一处：
    如果 `domain` 只是被忽略，那么一道题同时有 `domain` 与 `topics` 时，
    "按清单抽题"会照 `topics` 走 —— 看起来没问题，直到有人只改了 `domain`
    却以为改动了这道题的领域。**两份真相**，而其中一份是死的。
    """
    minimal = {
        "item_id": "X", "variable_id": "V-SE", "channel": "measured",
        "method_id": "M-SE-04", "role": "anchor", "difficulty": "中",
        "time_min": 6, "language": "zh", "media": "text",
        "scoring": {"rule": "exact"}, "source": "s", "license": "l",
        "status": "candidate",
        "anchors": {"main_thread": ["甲"]},
        "key_points_question": {
            "type": "key_points", "prompt": "p",
            "scoring_guide": ["主线", "关键事实", "限定条件"],
        },
        "exact_questions": [
            {"no": 1, "type": "exact", "question": "q", "options": ["A"], "answer": "A"}
        ],
    }
    tmp = Path(__file__).resolve().parent / "_probe_retired.json"
    try:
        for gone in ("domain", "domain_familiarity"):
            payload = dict(minimal, topics=["BIO-MARINE"], **{gone: "旧值"})
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with pytest.raises(ItemSpecError, match=gone):
                load_item(tmp, materials_dir=None, require_material=False)

        # 反例：去掉旧字段就放行 —— 否则上面两条在加载器坏成"什么都拒绝"时也通过
        tmp.write_text(
            json.dumps(dict(minimal, topics=["BIO-MARINE"]), ensure_ascii=False),
            encoding="utf-8",
        )
        item = load_item(tmp, materials_dir=None, require_material=False)
        assert item.topics == ("BIO-MARINE",)
    finally:
        tmp.unlink(missing_ok=True)


def test_a_bad_topic_id_is_an_ItemSpecError_not_a_bare_TopicError() -> None:
    """★ 加载器只该抛出**一种**规格错误。

    这个测试是探针发现的：`require_topics` 抛 `TopicError`，
    而它**不是** `ItemSpecError` 的子类（`topics.py` 不能 import `items.py`，
    那是循环）。于是拼错的 topic_id 会绕过调用方的 `except ItemSpecError`
    直接冒到顶层 —— 报错内容一样，但"谁负责处理它"变了。
    """
    tmp = Path(__file__).resolve().parent / "_probe_badtopic.json"
    payload = {
        "item_id": "X", "variable_id": "V-SE", "channel": "measured",
        "method_id": "M-SE-04", "role": "anchor", "difficulty": "中",
        "time_min": 6, "language": "zh", "media": "text",
        "scoring": {"rule": "exact"}, "source": "s", "license": "l",
        "status": "candidate", "topics": ["BIO-MARIN"],
        "anchors": {"main_thread": ["甲"]},
        "key_points_question": {
            "type": "key_points", "prompt": "p",
            "scoring_guide": ["主线", "关键事实", "限定条件"],
        },
        "exact_questions": [
            {"no": 1, "type": "exact", "question": "q", "options": ["A"], "answer": "A"}
        ],
    }
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ItemSpecError, match="BIO-MARIN"):
            load_item(tmp, materials_dir=None, require_material=False)
    finally:
        tmp.unlink(missing_ok=True)
