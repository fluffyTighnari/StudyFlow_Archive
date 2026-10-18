"""偏好的**硬度语义**（缺口 A18 / A19，用户裁定）。

## A18：`basis=stated` 一律按硬约束（HC1）消费

起因是用户的一句反问：「偏好和认知基准应该是独立的才对，为何偏好一定需要归档？」
查证后发现 A14 的**前提**是我编的（原文把 M2 §4 针对 **C 条目**的归档规定安到了偏好头上），
而这一问顺带暴露出一个**从未被决定过**的问题：`basis=stated` 的偏好，运行时到底按
硬约束消费还是按软偏好消费？

两者的行为**完全相反**：

| | 硬约束（HC1 口径） | 软偏好（SP1 口径） |
|---|---|---|
| 能不能被任务情况推翻 | **不能** —— 违反 = 计划不合格 | 能，但必须留痕（`overridden_soft_prefs`） |
| 典型的例子 | 「我讨厌看网课，因为浪费时间」 | 「我倾向于先看例子」 |

文档本身是矛盾的：§2.2 把「用户偏好的学习形式」列为 **SP1 软偏好**、来源写的是
M1 的 V-SMP/V-ACT/V-OSP（即**从 M1 测出来的**倾向），而 **HC1** 是「不违背用户明确
提出的时间、资源和任务要求」、红线 2 规定 **HC1–HC4 优先于任何软偏好**。
用户裁定：**`basis=stated` 的走前者**。

## A19：理由内联在 `statement` 里

用户裁定**不新增 `reason` 字段**，理由写在 `statement` 中。于是规范必须写在
**LLM 真正读得到的地方**（契约的字段说明），而不只是文档里 —— 这组测试里有一条
专门钉住这件事，因为「规范写在 LLM 看不见的地方」等于没有规范。

## 这组测试在防什么

防的是「硬度判定散落成多份」。判断一条偏好硬不硬，看起来只是
`basis == "stated"` 这一行 —— 但一旦它在计划生成、HC1 校验、前端展示里各写一份，
**任何一份写错都不会报错**，只会让 LLM 悄悄推翻用户说过的话，
而那正是 M2 红线 2 明令禁止的。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from app.global_domain import reads
from app.global_domain.enums import HARD_CONSTRAINT_BASIS, Basis, IntentAction
from app.global_domain.models import M2Preference
from app.global_domain.schemas import M2PreferenceRead
from app.router import Router, SubmitRequest, UpdateIntent
from app.router.capability import new_token
from app.router.contracts import M2PreferenceValue
from app.router.precheck import PRECHECK_TABLE
from app.global_domain.writes import insert_preference


def _statement_of(pref: M2Preference) -> str:
    return pref.statement


# --------------------------------------------------------------------------- #
# 判定集合本身
# --------------------------------------------------------------------------- #


def test_exactly_stated_counts_as_a_constraint() -> None:
    """集合内容被反射钉死：**悄悄扩大硬度范围会立刻红**。

    这不是「重复实现一遍」——它保护的是这个集合的**意图**。
    比如有人为了让「LLM 观察到的方法」也享受约束待遇，把 `EXPERIENCED` 加进来，
    那会让 M2 §2.2 的「软偏好可被推翻但必须留痕」整条失效，
    而所有既有测试仍然全绿（因为没人断言过集合的**上界**）。
    """
    assert HARD_CONSTRAINT_BASIS == frozenset({Basis.STATED})
    assert Basis.MEASURED not in HARD_CONSTRAINT_BASIS
    assert Basis.EXPERIENCED not in HARD_CONSTRAINT_BASIS
    assert Basis.INFERRED not in HARD_CONSTRAINT_BASIS


def test_the_classification_is_exhaustive_over_reachable_states() -> None:
    """**可达状态**上，二分是穷尽的 —— 不留「这个 basis 该怎么办」的空白。

    `Basis` 有四个取值，但「偏好」这个目标上只有三个可达
    （判定表里 `add_preference` 的行不含 `measured`）。
    若将来有人加了一行 `add_preference + measured`，这条会红：
    因为那时「measured 的偏好算硬还是软」就成了一个**必须回答**的问题，
    而它不该由 `is_hard_constraint` 的默认分支静默回答。
    """
    reachable = set()
    for rule in PRECHECK_TABLE:
        if IntentAction.ADD_PREFERENCE in rule.actions:
            reachable |= set(rule.bases)

    assert Basis.MEASURED not in reachable, (
        "判定表里出现了 add_preference + measured —— "
        "请先裁定「measured 的偏好算硬约束还是软偏好」，再更新本用例"
    )
    # 三个可达 basis 全部被明确归类（stated 硬，另两个软）。
    assert reachable, "判定表里一行 add_preference 都没有，说明筛选条件写错了"
    assert reachable & HARD_CONSTRAINT_BASIS == {Basis.STATED}
    assert reachable - HARD_CONSTRAINT_BASIS == {
        Basis.EXPERIENCED,
        Basis.INFERRED,
    }


# --------------------------------------------------------------------------- #
# 存储侧：三个函数是同一个集合的二分
# --------------------------------------------------------------------------- #


def _write(session: Session, *, statement: str, basis: Basis, priority: int = 5):
    """直接落库一行 —— 绕开 Router 是**有意**的。

    这组测试要验证的是**读侧的归类函数**，不是写入路径；走 Router 会让
    「basis 从哪里来」掺进被测对象。写入路径的测试在 `tests/router/` 与
    `tests/acceptance/` 里各自覆盖。
    """
    return insert_preference(
        new_token(),
        session,
        statement=statement,
        priority=priority,
        basis=basis,
        target_field=None,
        value=None,
        router_log_id=None,
    )


@pytest.fixture
def mixed(seeded: Session) -> Session:
    """三条声明 + 两条 LLM 推断，外加一条已归档的。"""
    _write(seeded, statement="我讨厌看网课，因为浪费时间", basis=Basis.STATED)
    _write(seeded, statement="我习惯先看例子再看定义", basis=Basis.STATED)
    _write(seeded, statement="我晚上完全不想学新东西", basis=Basis.STATED)
    _write(seeded, statement="你可能更适合主动回忆", basis=Basis.EXPERIENCED)
    _write(seeded, statement="你似乎偏好短时段学习", basis=Basis.INFERRED)

    archived = _write(seeded, statement="我以前喜欢看视频", basis=Basis.STATED)
    from app.global_domain.enums import LifecycleStatus
    from app.global_domain.writes import set_preference_status

    set_preference_status(
        new_token(), seeded, archived.pref_id, LifecycleStatus.ARCHIVED
    )
    seeded.flush()
    return seeded


def test_constraints_and_soft_preferences_partition_the_active_set(
    mixed: Session,
) -> None:
    """二分必须**既无交集、又无遗漏**。

    只断言「约束里有我给的那三条」是不够的：那样把一条软偏好同时算进约束里
    也照样绿。分区性质（不相交 + 并集等于全集）才能同时挡住这两类错误。
    """
    active = reads.list_active_preferences(mixed)
    hard = reads.list_active_constraints(mixed)
    soft = reads.list_active_soft_preferences(mixed)

    assert len(active) == 5, "归档的那条不该出现在 active 里"
    assert {p.pref_id for p in hard} & {p.pref_id for p in soft} == set()
    assert {p.pref_id for p in hard} | {p.pref_id for p in soft} == {
        p.pref_id for p in active
    }
    assert len(hard) == 3
    assert len(soft) == 2


def test_is_hard_constraint_agrees_with_the_partition_functions(mixed: Session) -> None:
    """单条判定与集合函数必须一致 —— 否则消费方用哪个都会踩到不一致的那一个。"""
    for pref in reads.list_active_preferences(mixed):
        in_constraints = pref in reads.list_active_constraints(mixed)
        assert reads.is_hard_constraint(pref) is in_constraints


def test_the_archived_constraint_is_not_consumed(mixed: Session) -> None:
    """归档的声明不再生效 —— 但也不是"从没说过"。

    这条区分是「改主意了」与「这条不该存在」的落点：前者靠新声明取代，
    后者才是撤回。两个函数都只看 `active`。
    """
    archived = next(
        p for p in reads.list_preferences(mixed) if p.statement == "我以前喜欢看视频"
    )
    assert reads.is_hard_constraint(archived), "它的 basis 仍是 stated"
    assert archived not in reads.list_active_constraints(mixed)
    assert archived in reads.list_preferences(mixed), "归档不等于删除"


def test_target_field_does_not_decide_hardness(seeded: Session) -> None:
    """**硬度只看 `basis`** —— 带槽位与不带槽位同样硬。

    这一条防的是一种很自然、但会削弱用户声明的推理：
    「这条偏好有 SP1 槽位，所以它是呈现层的一个可调参数，可以被推翻」。
    按用户裁定，`stated` 的偏好无论有没有槽位都是约束。
    """
    with_field = insert_preference(
        new_token(),
        seeded,
        statement="呈现优先用例子",
        priority=5,
        basis=Basis.STATED,
        target_field="SP1.presentation",
        value="example_first",
        router_log_id=None,
    )
    without_field = insert_preference(
        new_token(),
        seeded,
        statement="我讨厌看网课，因为浪费时间",
        priority=5,
        basis=Basis.STATED,
        target_field=None,
        value=None,
        router_log_id=None,
    )
    assert reads.is_hard_constraint(with_field)
    assert reads.is_hard_constraint(without_field)


# --------------------------------------------------------------------------- #
# 读模型侧：派生值必须与存储侧判定一致
# --------------------------------------------------------------------------- #


def test_the_read_model_derives_hardness_from_the_same_constant(
    mixed: Session,
) -> None:
    """`is_constraint` 是给前端与 Agent 的**唯一**口径。

    它之所以必须随读模型一起给出：否则前端一定会自己写一遍
    `basis === 'stated'`，而那份拷贝会在某次 enum 改动后悄悄过时 ——
    过时的后果是界面把一条用户禁令显示成"参考建议"，然后 LLM 照着推翻它。
    """
    for row in reads.list_preferences(mixed):
        dto = M2PreferenceRead.model_validate(row)
        assert dto.is_constraint is reads.is_hard_constraint(row)


def test_the_read_model_exposes_hardness_in_json(mixed: Session) -> None:
    """`is_constraint` 必须真的出现在序列化结果里。

    用 `computed_field` 而不是 `@property` 就是为了这件事：
    后者在 Python 里读得到、在 JSON 里**静默消失**，而前端只看得见 JSON。
    """
    row = next(
        p
        for p in reads.list_preferences(mixed)
        if p.statement == "我讨厌看网课，因为浪费时间"
    )
    payload = json.loads(M2PreferenceRead.model_validate(row).model_dump_json())
    assert payload["is_constraint"] is True

    soft = next(
        p for p in reads.list_preferences(mixed) if p.basis is Basis.INFERRED
    )
    soft_payload = json.loads(M2PreferenceRead.model_validate(soft).model_dump_json())
    assert soft_payload["is_constraint"] is False


# --------------------------------------------------------------------------- #
# A19：规范必须写在 LLM 读得到的地方
# --------------------------------------------------------------------------- #


def _field_description(model: type, field: str) -> str:
    schema = model.model_json_schema()
    return schema["properties"][field].get("description", "")


def test_the_llm_facing_contract_carries_the_reason_convention() -> None:
    """「理由内联」这条规范必须在**契约的字段说明**里，而不只在文档里。

    文档是给人看的；LLM 看到的是工具 schema。规范写在它看不见的地方
    就等于没有规范 —— 这条测试守的是**载体**，不是措辞。
    """
    description = _field_description(M2PreferenceValue, "statement")
    assert "理由" in description

    # 而「priority 不是可推翻的难度」这件事同样要说在它读得到的地方：
    # 否则 LLM 会用它给用户的约束"排个不太重要的位置"，然后合理地推翻它。
    priority = _field_description(M2PreferenceValue, "priority")
    assert "可推翻" in priority

    target_field = _field_description(M2PreferenceValue, "target_field")
    assert "硬" in target_field


def test_the_ui_facing_dto_carries_the_same_conventions() -> None:
    """UI 直写与 LLM 提议走同一条路，因此两处的字段说明必须说同一件事。"""
    from app.global_domain.schemas import PreferenceCreate

    assert "理由" in _field_description(PreferenceCreate, "statement")
    assert "可推翻" in _field_description(PreferenceCreate, "priority")


def test_a_stated_preference_written_through_the_router_reads_back_as_a_constraint(
    seeded: Session, router: Router
) -> None:
    """端到端：用户从界面直写一条偏好，读回来必须是约束。

    上面那些都是单元级的一致性；这一条确认**真实的写入路径**产出的
    确实是 `basis=stated`（若 R1 哪天被改成写 `experienced`，
    上面所有测试都绿，而「用户声明即事实」已经失效）。
    """
    result = router.submit(
        SubmitRequest(
            trigger_type="R1",
            intents=[
                UpdateIntent(
                    intent_id="UI-9001",
                    action="add_preference",
                    target="M2.preferences",
                    value={
                        "statement": "我讨厌看网课，因为浪费时间",
                        "priority": 8,
                    },
                    basis="stated",
                    reasoning="用户在界面直接写入偏好（R1）",
                    confidence="high",
                )
            ],
        )
    )
    assert result.results[0].status.value == "applied"

    pref = next(
        p
        for p in reads.list_active_constraints(seeded)
        if p.statement == "我讨厌看网课，因为浪费时间"
    )
    assert pref.basis is Basis.STATED
    assert pref.router_log_id, "经 Router 写入的行必须带日志 —— 否则 I1 会失败"


def test_a_settled_llm_inference_reads_back_as_a_soft_preference(
    seeded: Session, router: Router
) -> None:
    """对照组：LLM 推断出来的偏好是**软**的，不能混进约束集。

    没有这一半，「所有偏好都是约束」也能让上面的测试通过 ——
    而那等于把 M2 §2.2 整节作废（软偏好之所以叫软，就是因为它可以被任务情况推翻）。
    """
    result = router.submit(
        SubmitRequest(
            trigger_type="R4c",
            source_ref="S-001",
            intents=[
                UpdateIntent(
                    intent_id="UI-9002",
                    action="add_preference",
                    target="M2.preferences",
                    value={"statement": "你可能更适合主动回忆", "priority": 5},
                    basis="experienced",
                    reasoning="手动沉淀：本次发现",
                    confidence="medium",
                )
            ],
        )
    )
    assert result.results[0].status.value == "applied", "R4c 加偏好直接生效（A6）"

    row = next(
        p
        for p in reads.list_active_soft_preferences(seeded)
        if p.statement == "你可能更适合主动回忆"
    )
    assert row.basis is Basis.EXPERIENCED
    assert row not in reads.list_active_constraints(seeded)
