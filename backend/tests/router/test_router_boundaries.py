"""P2 的结构性边界 —— 「唯一写入入口」这句话必须能被测试验证。

三条断言：

1. Router 不把令牌交出去（拿不到令牌 = 绕不过它）；
2. Router 不决定事务边界（不 commit）；
3. Router 不依赖下游层（ARCH-09/ARCH-10 的真实代码树版本）。
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.core.capability import RouterToken
from app.router import Router
from app.router import __all__ as ROUTER_EXPORTS

APP_DIR = Path(__file__).resolve().parents[2] / "app"
ROUTER_DIR = APP_DIR / "router"


def _router_python_files() -> list[Path]:
    files = sorted(ROUTER_DIR.rglob("*.py"))
    assert len(files) >= 8, f"router 包的文件数异常：{len(files)}"
    return files


# --------------------------------------------------------------------------- #
# 1. 令牌不外泄
# --------------------------------------------------------------------------- #


def test_router_does_not_hand_out_its_token(router: Router) -> None:
    """任何**公开**属性都不得是 RouterToken。

    令牌是「唯一写入入口」的实现手段：只要它不外泄，
    调用方即便 import 了 `global_domain.writes` 也会被 `require(token)` 拒绝。
    """
    for name in dir(router):
        if name.startswith("_"):
            continue
        assert not isinstance(getattr(router, name), RouterToken), (
            f"Router.{name} 泄漏了令牌，绕行路径因此成立"
        )
    # 令牌确实存在 —— 否则上面的断言会因为「什么都没有」而假绿。
    assert isinstance(router._token, RouterToken)


def test_returned_contracts_carry_no_write_handles(router, seeded) -> None:
    """返回值里不得夹带 Session / Token / 写函数。"""
    from tests.router.helpers import m1_intent, request

    result = router.submit(request("R2", m1_intent()))
    for value in result.model_dump().values():
        assert not isinstance(value, RouterToken)

    assert not hasattr(result, "session")
    assert not hasattr(result, "token")


def test_forging_a_token_is_impossible() -> None:
    """令牌不可构造 —— 所以「拿一个 token 对象」不是一条可行路径。"""
    import pytest

    with pytest.raises(TypeError):
        RouterToken()


# --------------------------------------------------------------------------- #
# 2. 事务边界归调用方
# --------------------------------------------------------------------------- #


def test_router_never_commits() -> None:
    """Router 不决定事务边界。

    提交由调用方（API 层 / 测试）决定，这样一次请求要么整体生效、
    要么整体不生效；而单条 intent 的失败由 SAVEPOINT 隔离，不拖垮同批其它条。
    """
    offenders: list[str] = []
    for path in _router_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"commit", "rollback"}:
                offenders.append(f"{path.relative_to(APP_DIR.parent)}:{node.lineno}")
    assert offenders == [], "Router 不得直接提交或回滚事务：" + ", ".join(offenders)


def test_router_does_not_swallow_broad_exceptions() -> None:
    """裸 `except Exception` 会把「写入真的失败了」变成一条静默的日志。

    策略层主动拒绝（`PolicyRefused` / `PolicyError`）应当被捕获并留痕；
    其它异常必须冒泡 —— 它意味着我们没预料到的情况。
    """
    offenders: list[str] = []
    for path in _router_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if node.type is None:
                offenders.append(f"{path.relative_to(APP_DIR.parent)}:{node.lineno}")
            elif isinstance(node.type, ast.Name) and node.type.id in {
                "Exception",
                "BaseException",
            }:
                offenders.append(f"{path.relative_to(APP_DIR.parent)}:{node.lineno}")
    assert offenders == [], "Router 不得捕获泛化异常：" + ", ".join(offenders)


# --------------------------------------------------------------------------- #
# 3. 分层边界（ARCH-09 / ARCH-10 的真实代码树版本）
# --------------------------------------------------------------------------- #


def test_router_does_not_import_downstream_layers() -> None:
    """stage 永不参与写入裁定；Router 不认识 Agent / API。"""
    forbidden = ("app.session_domain", "app.agent", "app.api")
    offenders: list[str] = []
    for path in _router_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                if module.startswith(forbidden):
                    offenders.append(
                        f"{path.relative_to(APP_DIR.parent)}:{node.lineno} → {module}"
                    )
    assert offenders == [], "Router 依赖了下游层：" + ", ".join(offenders)


def test_archcheck_covers_the_router_package() -> None:
    """确保 ARCH-09/ARCH-10 作用在真实代码树上（而不是只对合成样例生效）。"""
    from tools.archcheck import RULES, scan

    modules = scan(APP_DIR)
    paths = {module.path for module in modules}
    assert "app.router.service" in paths
    assert "app.router.precheck" in paths
    assert "app.router.conflict" in paths
    assert "app.router.registry" in paths

    rule_ids = {rule.id for rule in RULES}
    assert {"ARCH-09", "ARCH-10"} <= rule_ids


# --------------------------------------------------------------------------- #
# 4. 对外契约面
# --------------------------------------------------------------------------- #


def test_router_exports_only_the_documented_names() -> None:
    """`app.router` 的对外面必须窄 —— 判定表与策略实现都不在其中。"""
    assert set(ROUTER_EXPORTS) == {
        "Router",
        "UpdateIntent",
        "SubmitRequest",
        "SubmitResult",
        "Decision",
        "IntentResult",
        "PrecheckResult",
        "ConflictReport",
        "BenchmarkAdvisory",
        "RollbackRequest",
        "RollbackResult",
    }
    import app.router as router_package

    assert not hasattr(router_package, "PRECHECK_TABLE")
    assert not hasattr(router_package, "m1_policy")
    assert not hasattr(router_package, "Registry")


def test_token_issuance_stays_inside_the_router_capability_module() -> None:
    """ARCH-03 的真实代码树版本：签发入口只有一处。"""
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path == APP_DIR / "router" / "capability.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if any(alias.name == "issue_token" for alias in node.names):
                    offenders.append(f"{path.relative_to(APP_DIR.parent)}:{node.lineno}")
    assert offenders == [], "令牌签发散落在 router.capability 之外：" + ", ".join(offenders)
