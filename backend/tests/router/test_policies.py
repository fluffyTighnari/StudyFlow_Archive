"""策略层 —— 每条策略都必须**只**做它能做的事。

策略层是「怎么写」的知识。它的风险不是写错值，而是**写宽了**：
某天 m1_policy 顺手也去改偏好、c_policy 顺手也去写基线。
这类扩张不会让任何现有测试变红，因此必须用结构断言提前钉住。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import select

from app.global_domain.enums import (
    EvidenceChannel,
    LifecycleStatus,
    TriggerType,
)
from app.global_domain.models import M2Preference
from app.global_domain.reads import list_active_preferences
from app.router import precheck as precheck_module
from app.router.policies import base, c_policy, m1_policy, m2_policy, noop_policy
from app.router.policies.base import PolicyContext, PolicyRefused
from app.router.registry import resolve_target, target_for_variable
from tests.router.helpers import m1_intent, pref_intent, request

POLICY_DIR = Path(m1_policy.__file__).resolve().parent


def _imported_names(module) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[-1] for alias in node.names)
    return names


def _ctx(session, token, intent, *, user_confirmed: bool, log_id: str = "RL-0001"):
    resolved = resolve_target(intent.target)
    assert resolved is not None
    value, error = precheck_module.validate_value(resolved, intent)
    assert error is None
    return PolicyContext(
        session=session,
        token=token,
        trigger=TriggerType.R2,
        intent=intent,
        resolved=resolved,
        value=value,
        source_ref=None,
        log_id=log_id,
        user_confirmed=user_confirmed,
    )


# --------------------------------------------------------------------------- #
# 红线 3 的第二道门（第一道在 precheck）
# --------------------------------------------------------------------------- #


def test_m1_policy_refuses_an_unconfirmed_parameter_write(seeded, token) -> None:
    """红线 3 不能只由上游状态决定。

    precheck 已经把该确认的标出来了，这层守卫是冗余的 —— 冗余是故意的：
    全局权威域的最后一道门不该只有一个上游函数说了算。
    """
    ctx = _ctx(seeded, token, m1_intent(basis="measured"), user_confirmed=False)
    with pytest.raises(PolicyRefused):
        m1_policy.apply(ctx)


def test_m1_policy_writes_when_confirmed(seeded, token) -> None:
    ctx = _ctx(seeded, token, m1_intent(basis="measured"), user_confirmed=True)
    outcome = m1_policy.apply(ctx)

    assert outcome.applied is True
    assert outcome.rows_written == 1
    assert outcome.old_value is None
    assert outcome.new_value is not None
    assert outcome.new_value["channel"] == EvidenceChannel.MEASURED.value
    assert outcome.new_value["score"] == 7.0


def test_m1_policy_records_the_previous_version_on_overwrite(seeded, token) -> None:
    m1_policy.apply(_ctx(seeded, token, m1_intent("I-1", score=5.0), user_confirmed=True))
    outcome = m1_policy.apply(
        _ctx(seeded, token, m1_intent("I-2", score=9.0), user_confirmed=True)
    )

    assert outcome.old_value is not None
    assert outcome.old_value["score"] == 5.0
    assert outcome.new_value["score"] == 9.0


# --------------------------------------------------------------------------- #
# 各策略的写入边界
# --------------------------------------------------------------------------- #


def test_c_policy_structurally_cannot_touch_m1() -> None:
    """C 的写入永不自动改写 M1 —— 用代码结构而不是注释保证。"""
    imported = _imported_names(c_policy)
    assert "insert_baseline_version" not in imported
    assert "rollback_baseline_version" not in imported
    assert not hasattr(c_policy, "insert_baseline_version")

    tree = ast.parse(Path(c_policy.__file__).read_text(encoding="utf-8"))
    touched = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }
    assert "insert_baseline_version" not in touched


def test_m1_policy_cannot_touch_preferences_or_evidence() -> None:
    imported = _imported_names(m1_policy)
    assert "insert_evidence" not in imported
    assert "insert_preference" not in imported


def test_m2_policy_cannot_touch_m1_or_evidence() -> None:
    imported = _imported_names(m2_policy)
    assert "insert_baseline_version" not in imported
    assert "insert_evidence" not in imported


def test_noop_policy_writes_nothing(seeded, token) -> None:
    from tests.router.helpers import intent

    ctx = _ctx(
        seeded,
        token,
        intent(action="no_change", target="M1.V-SE", basis="experienced"),
        user_confirmed=True,
    )
    outcome = noop_policy.apply(ctx)
    assert outcome.applied is True
    assert outcome.rows_written == 0
    assert outcome.new_value is None


def test_every_policy_module_exposes_the_same_entry_point() -> None:
    """统一入口是策略分发能保持「一张表」的前提。"""
    for module in (m1_policy, m2_policy, c_policy, noop_policy):
        assert callable(getattr(module, "apply", None)), module.__name__


def test_policy_base_does_not_import_any_policy_implementation() -> None:
    """`base` 是最底层契约，反向依赖会让「策略包」变成环。"""
    imported = _imported_names(base)
    assert not {name for name in imported if name.endswith("_policy")}


# --------------------------------------------------------------------------- #
# 行为：M2 的同字段替换
# --------------------------------------------------------------------------- #


def test_same_target_field_archives_the_older_preference(router, seeded) -> None:
    """M0 §7.3：同 ID 冲突「用户最新修改优先」。

    实现是「归档旧行 + 插入新行」而不是就地改写 —— 偏好是会变的，
    而「他三个月前说过什么」本身就是要保留的信息。
    """
    router.submit(
        request(
            "R1",
            pref_intent("I-1", statement="先例后理", target_field="M2.SP1", priority=5),
        )
    )
    router.submit(
        request(
            "R1",
            pref_intent("I-2", statement="先理后例", target_field="M2.SP1", priority=8),
        )
    )

    (active,) = list_active_preferences(seeded)
    assert active.statement == "先理后例"

    everything = list(seeded.scalars(select(M2Preference)))
    assert len(everything) == 2
    archived = [row for row in everything if row.status is LifecycleStatus.ARCHIVED]
    assert [row.pref_id for row in archived] == ["P-0001"]
    # 内容一字未改 —— 归档只改生命周期状态。
    assert archived[0].statement == "先例后理"


def test_different_target_fields_coexist(router, seeded) -> None:
    router.submit(
        request("R1", pref_intent("I-1", target_field="M2.SP1", statement="A"))
    )
    router.submit(
        request("R1", pref_intent("I-2", target_field="M2.SP2", statement="B"))
    )
    assert len(list_active_preferences(seeded)) == 2


def test_preferences_without_target_field_never_supersede(router, seeded) -> None:
    """没有 target_field 就没有「同一个偏好」这回事，不该胡乱归档。"""
    router.submit(request("R1", pref_intent("I-1", statement="A", target_field=None)))
    router.submit(request("R1", pref_intent("I-2", statement="B", target_field=None)))
    assert len(list_active_preferences(seeded)) == 2


# --------------------------------------------------------------------------- #
# A20：显式指认被推翻的那一条
# --------------------------------------------------------------------------- #


def _write_pref(router, intent_id: str, **value_kwargs) -> None:
    router.submit(request("R1", pref_intent(intent_id, **value_kwargs)))


def test_explicit_supersede_archives_the_named_preference(router, seeded) -> None:
    """用户改主意：「其实我能接受精品网课」。

    两条都没有 `target_field`，因此**槽位规则管不着它们** —— 在 A20 之前
    这两条会同时 active、同时是硬约束，计划生成时拿到一对互相矛盾的约束。
    """
    _write_pref(router, "I-1", statement="我讨厌网课，因为浪费时间")
    _write_pref(
        router,
        "I-2",
        statement="我能接受精品网课，如果讲得好",
        supersedes_pref_id="P-0001",
    )

    (active,) = list_active_preferences(seeded)
    assert active.pref_id == "P-0002"
    assert active.supersedes_pref_id == "P-0001"

    old = seeded.get(M2Preference, "P-0001")
    assert old is not None and old.status is LifecycleStatus.ARCHIVED
    # 归档不是删除：用户三个月前说过的那句话一字未改。
    assert old.statement == "我讨厌网课，因为浪费时间"


def test_supersede_reason_names_the_archived_preference(router, seeded) -> None:
    """日志里必须读得出「它推翻了谁」—— 否则事后无法解释偏好为什么换了。"""
    _write_pref(router, "I-1", statement="我讨厌网课")
    result = router.submit(
        request(
            "R1",
            pref_intent("I-2", statement="精品网课可以", supersedes_pref_id="P-0001"),
        )
    )
    assert result.results[0].status.value == "applied"
    assert "P-0001" in result.results[0].reason
    assert "明确改主意" in result.results[0].reason


def test_supersede_pointing_at_a_missing_id_is_refused(router, seeded) -> None:
    """模型编了一个 id。

    若静默忽略，用户会以为旧约束已经退休 —— 而实际上两条并存、
    矛盾照旧留在基线里。这正是 A20 要消灭的状态，所以必须**整条拒绝**。
    """
    result = router.submit(
        request(
            "R1",
            pref_intent("I-1", statement="精品网课可以", supersedes_pref_id="P-9999"),
        )
    )
    assert result.results[0].status.value == "rejected"
    assert "P-9999" in result.results[0].reason
    assert list_active_preferences(seeded) == []


def test_supersede_pointing_at_an_archived_preference_is_refused(router, seeded) -> None:
    """「再推翻一次」说明对当前状态的判断有误 —— 拒绝比将错就错安全。"""
    _write_pref(router, "I-1", statement="A")
    _write_pref(router, "I-2", statement="B", supersedes_pref_id="P-0001")
    result = router.submit(
        request("R1", pref_intent("I-3", statement="C", supersedes_pref_id="P-0001"))
    )
    assert result.results[0].status.value == "rejected"
    assert "不再生效" in result.results[0].reason
    assert [p.pref_id for p in list_active_preferences(seeded)] == ["P-0002"]


def test_inferred_cannot_supersede_a_stated_constraint(seeded, token) -> None:
    """**A20 里唯一一条不可协商的规则。**

    用户亲口说过的约束，只能由用户自己改主意（也是 `stated`）来推翻。
    否则等于让模型推断出的东西**静默抹掉用户的话**（M2 红线 2）。

    这里直接调策略而不走 Router：判定表决定「要不要问用户」，
    而本测试问的是「就算用户点了同意，这条写入合法吗」—— 后者是策略的职责。
    """
    _write_directly(seeded, token, "I-1", statement="我讨厌网课，因为浪费时间")
    ctx = _ctx(
        seeded,
        token,
        pref_intent("I-2", statement="网课其实还行", supersedes_pref_id="P-0001",
                    basis="inferred"),
        user_confirmed=True,
    )
    with pytest.raises(PolicyRefused) as excinfo:
        m2_policy.apply(ctx)
    message = str(excinfo.value)
    assert "P-0001" in message
    assert "红线 2" in message

    assert [p.pref_id for p in list_active_preferences(seeded)] == ["P-0001"]


def test_stated_may_supersede_stated(seeded, token) -> None:
    """用户自己改口永远允许 —— 这正是 A20 存在的意义。"""
    _write_directly(seeded, token, "I-1", statement="我讨厌网课")
    outcome = m2_policy.apply(
        _ctx(
            seeded,
            token,
            pref_intent("I-2", statement="精品网课可以", supersedes_pref_id="P-0001"),
            user_confirmed=True,
        )
    )
    assert outcome.applied is True
    assert outcome.rows_written == 2
    assert outcome.old_value is not None and outcome.old_value["pref_id"] == "P-0001"
    assert outcome.new_value["supersedes_pref_id"] == "P-0001"


def test_explicit_and_slot_naming_different_rows_is_refused(seeded, token) -> None:
    """「我推翻的是 P-0001」与「P-0002 占着我要用的槽位」不可能同时为真。

    宁可拒绝，也不"两条都归档" —— `router_log.old_value` 只能装一条旧值，
    归档两条却只记一条就是静默丢一个旧值（P5.2 的教训）。
    """
    _write_directly(seeded, token, "I-1", statement="通用声明")  # P-0001
    _write_directly(
        seeded, token, "I-2", statement="占着槽位", target_field="SP1.presentation"
    )  # P-0002

    ctx = _ctx(
        seeded,
        token,
        pref_intent(
            "I-3",
            statement="抢槽位同时推翻第一条",
            target_field="SP1.presentation",
            supersedes_pref_id="P-0001",
        ),
        user_confirmed=True,
    )
    with pytest.raises(PolicyRefused) as excinfo:
        m2_policy.apply(ctx)
    assert "P-0001" in str(excinfo.value) and "P-0002" in str(excinfo.value)

    assert len(list_active_preferences(seeded)) == 2


def test_explicit_and_slot_naming_the_same_row_archives_once(seeded, token) -> None:
    """两处指认同一条时只归档一次 —— 否则 `rows_written` 会凭巧合多算一行。"""
    _write_directly(
        seeded, token, "I-1", statement="旧呈现偏好", target_field="SP1.presentation"
    )
    outcome = m2_policy.apply(
        _ctx(
            seeded,
            token,
            pref_intent(
                "I-2",
                statement="新呈现偏好",
                target_field="SP1.presentation",
                supersedes_pref_id="P-0001",
            ),
            user_confirmed=True,
        )
    )
    assert outcome.rows_written == 2
    assert [p.pref_id for p in list_active_preferences(seeded)] == ["P-0002"]


def _write_directly(session, token, intent_id: str, **value_kwargs) -> None:
    """直接经策略写一条偏好（绕过判定表），用于构造前置状态。"""
    m2_policy.apply(
        _ctx(session, token, pref_intent(intent_id, **value_kwargs), user_confirmed=True)
    )


def test_m1_policy_rejects_a_target_without_variable_id(seeded, token) -> None:
    """防御性断言：M1 类 target 必须带 variable_id（Registry 保证，但策略不假定）。"""
    from app.router.policies.base import PolicyError

    intent = m1_intent(basis="measured")
    ctx = _ctx(seeded, token, intent, user_confirmed=True)
    forged = PolicyContext(
        session=ctx.session,
        token=ctx.token,
        trigger=ctx.trigger,
        intent=ctx.intent,
        resolved=resolve_target("M2.preferences"),
        value=ctx.value,
        source_ref=None,
        log_id=ctx.log_id,
        user_confirmed=True,
    )
    with pytest.raises(PolicyError):
        m1_policy.apply(forged)


def test_registry_variable_lookup_is_the_only_source_of_variable_ids() -> None:
    """策略从 `resolved.variable_id` 取变量，而不是从 target 字符串里切。"""
    resolved = resolve_target(target_for_variable("V-HSR"))
    assert resolved is not None
    assert resolved.variable_id == "V-HSR"
