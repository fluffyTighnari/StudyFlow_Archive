"""架构依赖规则断言 —— 四重保障之①（M0 §4.1①/§4.3）。

两组测试：
1. **真实代码树必须干净** —— 任何新增模块一旦越界，测试立刻红。
2. **检查器本身必须真的能抓到违规** —— 用合成的违规模块反向验证。
   缺了这一组，第 1 组可能只是因为检查器失灵而永远为绿（P0 的完成判据正是这一条）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.archcheck import RULES, ArchCheckError, check, format_violations, scan

APP_DIR = Path(__file__).resolve().parents[2] / "app"


# --------------------------------------------------------------------------- #
# 1. 真实代码树
# --------------------------------------------------------------------------- #


def test_real_app_tree_satisfies_all_architecture_rules() -> None:
    violations = check(APP_DIR)
    assert violations == [], "\n" + format_violations(violations)


def test_checker_actually_scans_the_real_tree() -> None:
    """防止 APP_DIR 写错导致「零文件扫描 = 零违规」的假绿。"""
    modules = scan(APP_DIR)
    assert len(modules) >= 10
    paths = {m.path for m in modules}
    assert "app.router.capability" in paths
    assert "app.global_domain.writes" in paths
    assert "app.global_domain.reads" in paths


def test_rule_ids_are_unique_and_documented() -> None:
    ids = [r.id for r in RULES]
    assert len(ids) == len(set(ids))
    assert all(r.reason.strip() for r in RULES)


# --------------------------------------------------------------------------- #
# 2. 反向验证：合成违规模块必须被抓到
# --------------------------------------------------------------------------- #


def _make_package(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "app"
    root.mkdir(parents=True, exist_ok=True)
    (root / "__init__.py").write_text("", encoding="utf-8")
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def _stub_modules() -> dict[str, str]:
    """让 import 目标在磁盘上真实存在的空壳模块。"""
    return {
        "core/__init__.py": "",
        "core/capability.py": "",
        "global_domain/__init__.py": "",
        "global_domain/models.py": "",
        "global_domain/reads.py": "",
        "global_domain/writes.py": "",
        "router/__init__.py": "",
        "router/policies/__init__.py": "",
        "session_domain/__init__.py": "",
        "agent/__init__.py": "",
        "api/__init__.py": "",
    }


def _violations(tmp_path: Path, files: dict[str, str]) -> list:
    merged = {**_stub_modules(), **files}
    root = _make_package(tmp_path, merged)
    return check(root, "app")


def test_detects_arch01_non_router_importing_global_writes(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        {"api/routes.py": "from app.global_domain.writes import insert_evidence\n"},
    )
    assert {v.rule_id for v in violations} == {"ARCH-01"}


def test_detects_arch01_relative_import(tmp_path: Path) -> None:
    """相对 import 也必须被解析出来（`from ..global_domain import writes`）。"""
    violations = _violations(
        tmp_path,
        {"api/routes.py": "from ..global_domain import writes\n"},
    )
    assert "ARCH-01" in {v.rule_id for v in violations}


def test_detects_arch02_policy_implementation_leak(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        {"api/routes.py": "from app.router.policies import m1_policy\n"},
    )
    assert "ARCH-02" in {v.rule_id for v in violations}


def test_detects_arch03_direct_token_issuance(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        {"agent/loop.py": "from app.core.capability import issue_token\n"},
    )
    assert "ARCH-03" in {v.rule_id for v in violations}


def test_detects_arch03_token_issuance_via_attribute_alias(tmp_path: Path) -> None:
    """堵住「先 import 模块、再取属性」的绕行路径。"""
    violations = _violations(
        tmp_path,
        {
            "agent/loop.py": (
                "from app.core import capability as cap\n"
                "\n"
                "def steal():\n"
                "    return cap.issue_token()\n"
            )
        },
    )
    assert "ARCH-03" in {v.rule_id for v in violations}


def test_allows_router_to_import_writes_and_issue_tokens(tmp_path: Path) -> None:
    """反向断言：router 子树的合法用法不得被误报。"""
    violations = _violations(
        tmp_path,
        {
            "router/service.py": (
                "from app.core.capability import RouterToken, issue_token\n"
                "from app.global_domain.writes import insert_evidence\n"
                "from app.router.policies import m1_policy\n"
            )
        },
    )
    assert violations == [], format_violations(violations)


def test_detects_arch04_core_depending_on_upper_layer(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        {"core/helpers.py": "from app.global_domain import models\n"},
    )
    assert "ARCH-04" in {v.rule_id for v in violations}


def test_detects_arch05_global_domain_depending_on_router(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        {"global_domain/reads.py": "from app.router import service\n"},
    )
    assert "ARCH-05" in {v.rule_id for v in violations}


def test_detects_arch06_session_domain_depending_on_agent(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        {"session_domain/service.py": "from app.agent import loop\n"},
    )
    assert "ARCH-06" in {v.rule_id for v in violations}


def test_detects_arch07_reads_importing_writes(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        {"global_domain/reads.py": "from app.global_domain import writes\n"},
    )
    assert "ARCH-07" in {v.rule_id for v in violations}


def test_detects_arch08_agent_touching_global_orm(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        {"agent/tools.py": "from app.global_domain.models import M1BaselineVersion\n"},
    )
    assert "ARCH-08" in {v.rule_id for v in violations}


def test_detects_arch08_api_touching_global_orm(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        {"api/routes.py": "from app.global_domain.models import CEvidence\n"},
    )
    assert "ARCH-08" in {v.rule_id for v in violations}


def test_allows_global_domain_and_seeds_to_use_models(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        {
            "global_domain/reads.py": "from app.global_domain.models import CEvidence\n",
            "seeds/__init__.py": "",
            "seeds/m1.py": "from app.global_domain.models import M1Variable\n",
        },
    )
    assert violations == [], format_violations(violations)


def test_clean_synthetic_package_has_no_violations(tmp_path: Path) -> None:
    violations = _violations(tmp_path, {"api/routes.py": "from app.core import errors\n"})
    assert violations == []


def test_detects_arch09_router_depending_on_session_domain(tmp_path: Path) -> None:
    """「stage 永不参与写入裁定」不能只是一句注释。

    如果 Router 能读到 Session 内的即时感受，§7.3 的
    「experienced 需 ≥2 条独立 Session 证据」就会被绕过 ——
    当下感受随手可得，而沉淀后的证据不是。
    """
    violations = _violations(
        tmp_path,
        {"router/service.py": "from app.session_domain import service\n"},
    )
    assert "ARCH-09" in {v.rule_id for v in violations}


def test_detects_arch10_router_depending_on_agent(tmp_path: Path) -> None:
    """Router 与 Agent 解耦：Agent 可以 import Router，反向不行。"""
    violations = _violations(
        tmp_path,
        {"router/service.py": "from app.agent import loop\n"},
    )
    assert "ARCH-10" in {v.rule_id for v in violations}


def test_detects_arch10_router_depending_on_api(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        {"router/service.py": "from app.api import routes\n"},
    )
    assert "ARCH-10" in {v.rule_id for v in violations}


def test_allows_router_to_import_global_domain_and_policies(tmp_path: Path) -> None:
    """ARCH-09/ARCH-10 不得误伤 Router 对权威域的合法依赖。"""
    violations = _violations(
        tmp_path,
        {
            "router/service.py": (
                "from app.global_domain.reads import current_baseline_version\n"
                "from app.global_domain.writes import insert_preference\n"
                "from app.router.policies import m2_policy\n"
            )
        },
    )
    assert violations == [], format_violations(violations)


def test_detects_arch11_session_domain_depending_on_router(tmp_path: Path) -> None:
    """「沙箱域不认识 Router」不能只是一句注释（P4 新增）。

    沙箱域手里有 stage，而 stage 里全是用户当下的即时感受。一旦它能 import
    Router，就同时具备了「随手写全局」的能力与「绕开沉淀手续」的便利 ——
    而 §7.4「stage 永不参与写入裁定」的隔离会立刻退化成文档上的一句话。

    注意这条禁令**比** M0 §4.3 里给 `agent.tools` 的许可更严：
    Agent 工具层可以 import `router.contracts`（仅 schema），沙箱域连 contracts
    都不该碰 —— 映射成 `UpdateIntent` 是 `app/api/` 的事，只在那一个地方发生。
    """
    for module_path in (
        "from app.router import Router\n",
        "from app.router.contracts import UpdateIntent\n",
        "from app.router.service import Router\n",
    ):
        violations = _violations(tmp_path, {"session_domain/writes.py": module_path})
        assert "ARCH-11" in {v.rule_id for v in violations}, module_path


def test_arch11_does_not_forbid_session_domain_reading_the_authoritative_domain(
    tmp_path: Path,
) -> None:
    """沙箱**可以**读全局（讨论上下文包正需要它），只是不能写、不能碰 Router。

    读与写在这里必须分开对待：读让沙箱能装配出有依据的上下文，
    写才是那条需要手续的路。
    """
    violations = _violations(
        tmp_path,
        {
            "session_domain/context.py": (
                "from app.global_domain.reads import list_active_constraints\n"
                "from app.global_domain.enums import Basis\n"
                "from app.global_domain.targets import target_for_variable\n"
            )
        },
    )
    assert violations == [], format_violations(violations)


def test_detects_arch12_agent_modules_reaching_router_directly(tmp_path: Path) -> None:
    """「Agent 通往全局只有 handoff 一道门」不能只是一句注释（P6 新增）。

    Agent 手里有整个沙箱（stage、笔记、计划修订），而它同时是最容易被
    诱导去做"顺手写一条全局"的那一层 —— 它读得到用户的话，也读得到
    自己的推理，而后者不是证据。把翻译成 `UpdateIntent` 的权力收在**一个**
    模块里，审计才能回答「谁在什么时候把会话里的东西变成了结论」。

    四种形态都要被抓到，尤其是 `from app.router import ...`：
    它会执行 `app/router/__init__.py`，而那里 export 了 `Router`。
    """
    for module_path in (
        "from app.router import Router\n",
        "from app.router.service import Router\n",
        "from app.router.capability import new_token\n",
        "import app.router.policies.m2_policy\n",
    ):
        violations = _violations(tmp_path, {"agent/loop.py": module_path})
        assert "ARCH-12" in {v.rule_id for v in violations}, module_path


def test_arch12_exempts_only_the_write_contract(tmp_path: Path) -> None:
    """`app.router.contracts` 是唯一的例外，而且它是**必须**的例外。

    M8 §4.6 要求 `submit_global_update` 的 `value` schema 从真实契约生成
    （A19 的 `statement` 规范就写在 `M2PreferenceValue.statement` 的字段说明里），
    不许手抄一份 —— 手抄的那份会在契约改动后悄悄过期。

    读得到契约、拿不到令牌，仍然写不进任何东西：真正拦得住的是
    ARCH-01（非路由层不得引用 `global_domain.writes`）与 ARCH-03（令牌签发入口唯一）。
    这条测试把"例外只有 contracts"钉住 —— 否则下次有人会把整个 `app.router` 放进来。
    """
    violations = _violations(
        tmp_path,
        {
            "agent/tools.py": (
                "from app.router.contracts import M1ParameterValue\n"
                "from app.router.contracts import M2PreferenceValue\n"
            )
        },
    )
    assert violations == [], format_violations(violations)


def test_arch12_still_applies_to_handoff_siblings_with_similar_names(
    tmp_path: Path,
) -> None:
    """`app.agent.handoff_extra` **不是**那道门。

    负向前瞻写松一点（只挡 `handoff.`）就会让一个名字相近的模块悄悄拿到 Router。
    """
    violations = _violations(
        tmp_path, {"agent/handoff_extra.py": "from app.router.service import Router\n"}
    )
    assert "ARCH-12" in {v.rule_id for v in violations}


def test_arch12_allows_the_handoff_module_itself(tmp_path: Path) -> None:
    """门本身当然要能开。"""
    violations = _violations(
        tmp_path,
        {
            "agent/handoff.py": (
                "from app.router import Router, SubmitRequest, UpdateIntent\n"
                "from app.agent.tools import ToolContext, ToolResult\n"
            )
        },
    )
    assert violations == [], format_violations(violations)


def test_detects_arch13_external_layer_reaching_back_into_the_agent(tmp_path: Path) -> None:
    """外挂能力层只提供**能力**，不提供**用法**（P14 新增）。

    `app/external` 是"MCP / 搜索是什么"，`app/agent/external.py` 是
    "它们以什么姿态出现在模型面前"。后者 import 前者是正常的，
    反过来成环 —— 更要紧的是：一旦 `app.external` 能 import `app.agent`，
    「一个第三方服务器能碰到什么」就从一句可核对的话
    变成一件要跨模块读才能回答的事，而那一层存在的全部意义就是回答它。
    """
    for module_path in (
        "from app.agent.tools import ToolSpec\n",
        "from app.agent.external import ExternalToolbox\n",
        "from app.api.external import router\n",
        "import app.router.service\n",
        "from app.session_domain import reads\n",
    ):
        violations = _violations(tmp_path, {"external/mcp.py": module_path})
        assert "ARCH-13" in {v.rule_id for v in violations}, module_path


def test_arch13_allows_the_external_layer_to_stand_alone(tmp_path: Path) -> None:
    """它当然可以只依赖 `app.core`（错误类型、通用工具）—— 那是它的底座。"""
    violations = _violations(
        tmp_path,
        {
            "external/mcp.py": (
                "import json\nfrom app.core.errors import DomainError\n"
            )
        },
    )
    assert violations == [], format_violations(violations)


def test_unparseable_file_is_a_hard_failure_not_a_pass(tmp_path: Path) -> None:
    """语法错误必须抛错，**绝不能**被当成「零违规 = 通过」。

    这正是本项目差点踩到的坑：一次带 BOM 的写入让 ast.parse 抛异常，
    如果没有这层区分，检查器崩溃会被误读为「架构没问题」。
    """
    root = _make_package(
        tmp_path, {**_stub_modules(), "api/broken.py": "def (:\n"}
    )
    with pytest.raises(ArchCheckError, match="无法解析"):
        check(root, "app")
