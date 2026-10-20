"""UpdatePolicyRegistry —— 数据驱动 + A9 命名空间归一化。

A9 决定把变量主键规范成 `V-SE`、路由 target 写成 `M1.V-SE`。
决定本身不重要，**它的唯一性**才重要：只要别处也能手写 `f"M1.{x}"`，
归一化就会在半年后名存实亡。因此这里用 AST 把「只允许在一个地方拼接」钉死。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.global_domain.enums import (
    ALL_M1_VARIABLES,
    M1_PREFERENCE_VARIABLES,
    M1_TRAIT_VARIABLES,
    IntentAction,
    TriggerType,
)
from app.router import registry
from app.router.registry import (
    C_TARGET_ROOT,
    M2_TARGET_ROOT,
    SOURCE_BY_TRIGGER,
    TargetClass,
    resolve_target,
    source_for_trigger,
    target_for_variable,
    variable_id_from_target,
)

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def test_every_variable_roundtrips_through_the_namespace_mapping() -> None:
    for variable_id in sorted(ALL_M1_VARIABLES):
        target = target_for_variable(variable_id)
        assert target == f"M1.{variable_id}"
        assert variable_id_from_target(target) == variable_id


def test_variable_id_from_target_rejects_non_m1_targets() -> None:
    import pytest

    with pytest.raises(ValueError):
        variable_id_from_target("M2.preferences")


def test_every_registered_variable_resolves_to_the_documented_class() -> None:
    for variable_id in sorted(M1_PREFERENCE_VARIABLES):
        resolved = resolve_target(target_for_variable(variable_id))
        assert resolved is not None
        assert resolved.target_class is TargetClass.M1_PREFERENCE
        assert resolved.variable_id == variable_id

    for variable_id in sorted(M1_TRAIT_VARIABLES):
        resolved = resolve_target(target_for_variable(variable_id))
        assert resolved is not None
        assert resolved.target_class is TargetClass.M1_TRAIT
        assert resolved.variable_id == variable_id


def test_every_seeded_variable_is_resolvable() -> None:
    """种子里的变量必须全部能解析 —— 否则真实写入会被判「未注册」。"""
    from app.seeds.m1_variables import VARIABLES

    for row in VARIABLES:
        variable_id = row["variable_id"]
        assert resolve_target(target_for_variable(variable_id)) is not None
        assert variable_id in ALL_M1_VARIABLES


def test_m2_and_c_roots_accept_dotted_suffixes() -> None:
    for target, expected in (
        (M2_TARGET_ROOT, TargetClass.M2_PREFERENCE),
        (f"{M2_TARGET_ROOT}.SP1", TargetClass.M2_PREFERENCE),
        (C_TARGET_ROOT, TargetClass.C_EVIDENCE),
        (f"{C_TARGET_ROOT}.reading", TargetClass.C_EVIDENCE),
    ):
        resolved = resolve_target(target)
        assert resolved is not None
        assert resolved.target_class is expected
        assert resolved.variable_id is None


def test_unknown_targets_do_not_resolve() -> None:
    for target in ("M1.V-NOPE", "M9.x", "M2", "C", "", "M1.", "m1.V-SE"):
        assert resolve_target(target) is None


def test_every_rule_allows_no_change() -> None:
    """§5.3 的「任意 | no_change | 任意」行必须对每个 target 都成立。"""
    for target_class, rule in registry.RULES.items():
        assert IntentAction.NO_CHANGE in rule.allowed_actions, target_class


def test_every_rule_declares_a_value_schema_for_its_write_action() -> None:
    """会写数据的 action 必须有参数结构声明，否则就退化成「随便写点什么」。"""
    for target_class, rule in registry.RULES.items():
        writing = rule.allowed_actions - {IntentAction.NO_CHANGE}
        assert writing, target_class
        assert rule.value_schema is not None, target_class


def test_policy_keys_are_unique_per_behaviour() -> None:
    """一个 `policy_key` = 一个策略模块 = 一张表的写权限。

    ★ P7 从三个变成四个：假说有自己的 `hypothesis_policy`，而不是复用
    `c_policy`。这不是"多了一个策略"，而是**收窄**：`c_policy` 只 import
    `insert_evidence`、`hypothesis_policy` 只 import `insert_hypothesis`，
    于是"假说被顺手写成证据"在 import 层面就不可能 ——
    和 `c_policy` 里那条「C 永不自动改写 M1」用同一个手法。
    """
    keys = {rule.policy_key for rule in registry.RULES.values()}
    assert keys == {"m1_policy", "m2_policy", "c_policy", "hypothesis_policy"}


def test_source_is_derived_from_trigger_not_supplied() -> None:
    """来源是事实，不该由调用方指定 —— 否则 R2 的写入可以被记成用户直写。"""
    assert SOURCE_BY_TRIGGER.keys() == set(TriggerType)
    for trigger in TriggerType:
        assert source_for_trigger(trigger) is SOURCE_BY_TRIGGER[trigger]


def test_m1_target_strings_are_only_built_in_one_place() -> None:
    """A9 的唯一性：`f"M1.{...}"` 只允许出现在 `app/global_domain/targets.py`。

    在别处拼接会让「target 命名空间」这件事有两个实现，
    而两个实现迟早会不一致（例如一处用 `M1.{id}`、另一处用 `M1/{id}`）。

    P5 把这一处从 `app/router/registry.py` 移到了全局域：
    不变式自检需要它，而 ARCH-04/05 让自检既进不了 `app.core`、
    也无法从全局域反向 import 路由层。因此这里盯的文件也随之改变 ——
    **约束本身（只有一处）没有放松。**
    """
    allowed = {APP_DIR / "global_domain" / "targets.py"}
    #: `registry.py` 允许提到 `M1_TARGET_PREFIX`，但只作为 import 转发。
    forwarders = {APP_DIR / "router" / "registry.py"}
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path in allowed:
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if re.search(r"""f["']M1\.[{"]""", line):
                offenders.append(f"{path.relative_to(APP_DIR.parent)}:{lineno}")
            elif "M1_TARGET_PREFIX" in line and path not in forwarders:
                offenders.append(f"{path.relative_to(APP_DIR.parent)}:{lineno}")
    assert offenders == [], "M1 target 拼接散落在 targets.py 之外：" + ", ".join(offenders)


def test_registry_does_not_hold_a_second_copy_of_the_precheck_table() -> None:
    """判定表只有一份（`precheck.PRECHECK_TABLE`）。

    Registry 若也开始记「R2 + measured → auto_apply」，就有两处事实，
    而它们会在某次只改一处的提交里分叉。
    """
    tree = ast.parse(
        (APP_DIR / "router" / "registry.py").read_text(encoding="utf-8")
    )
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "PRECHECK_TABLE"
        for node in ast.walk(tree)
    )
