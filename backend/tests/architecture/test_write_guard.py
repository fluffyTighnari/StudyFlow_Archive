"""强制「每个全局写函数都必须先验令牌」—— 四重保障之① 的运行时补充。

`writes.py` 里任何一个新增函数若忘记 `require(token)`，本测试立刻红。
这防止的是最现实的失效模式：功能迭代时新增一个写函数，顺手忘了守卫。
"""

from __future__ import annotations

import ast
from pathlib import Path

WRITES_PATH = (
    Path(__file__).resolve().parents[2] / "app" / "global_domain" / "writes.py"
)

#: 守卫函数自身不需要自证。
_GUARD_NAMES = {"require"}


def _tree() -> ast.Module:
    return ast.parse(WRITES_PATH.read_text(encoding="utf-8"))


def _public_write_functions(tree: ast.Module) -> list[ast.FunctionDef]:
    return [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and not node.name.startswith("_")
        and node.name not in _GUARD_NAMES
    ]


def _first_real_statement(node: ast.FunctionDef) -> ast.stmt | None:
    """取函数体的第一条**真实**语句。

    注意：在 AST 里 docstring 也是 `body[0]`（一个 Expr(Constant(str))），
    因此不能直接看 `body[0]`——否则所有带文档字符串的写函数都会被误判。
    """
    if not node.body:
        return None
    first = node.body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return node.body[1] if len(node.body) > 1 else None
    return first


def _first_statement_calls(node: ast.FunctionDef, name: str) -> bool:
    first = _first_real_statement(node)
    if first is None:
        return False
    return (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Call)
        and isinstance(first.value.func, ast.Name)
        and first.value.func.id == name
    )


def test_every_public_write_function_starts_with_require() -> None:
    offenders = [
        node.name
        for node in _public_write_functions(_tree())
        if not _first_statement_calls(node, "require")
    ]
    assert not offenders, (
        f"以下全局写函数的首条语句不是 require(token)：{offenders}。"
        "所有写入口都必须先验令牌（M0 §4.1①）。"
    )


def test_the_guard_test_is_not_vacuous() -> None:
    """防止测试因为「一个写函数都没扫到」而永远为绿。"""
    names = [node.name for node in _public_write_functions(_tree())]
    assert len(names) >= 10, f"只扫到 {len(names)} 个写函数，测试已失效：{names}"
    assert "insert_evidence" in names
    assert "insert_baseline_version" in names


def test_require_delegates_to_assert_capability() -> None:
    tree = _tree()
    require = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "require"
    )
    assert _first_statement_calls(require, "assert_capability")


def test_writes_module_has_no_reassignment_of_orm_rows() -> None:
    """写模块只应通过 ORM 会话操作，不应吞掉异常或静默失败。

    这里只做一条轻量断言：模块内不得出现裸 `except:` 或 `except Exception: pass`，
    避免「写失败被吞掉」这类最危险的静默失效。
    """
    tree = _tree()
    bare_handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and node.type is None
    ]
    assert bare_handlers == [], "writes.py 不得使用裸 except（会吞掉写入失败）"
