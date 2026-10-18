"""架构依赖规则检查器 —— 四重保障之①（M0 §4.1 / §4.3）。

纯 AST 静态分析，无运行时依赖。被 `tests/architecture/test_dependency_rules.py`
调用，也可作为 CLI 独立运行：

    cd backend && python -m tools.archcheck

它回答一个问题：**有没有任何模块，在代码结构上绕过了路由层去写全局？**

检测两类引用：
1. import 语句（`import a.b` / `from a.b import c`），含相对 import 解析。
2. 属性访问链（`cap.issue_token`），即使签发函数是通过别名间接取到的也能抓到。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# 规则表
# --------------------------------------------------------------------------- #

# 「不在 app.router 子树内」的模块。
_NOT_ROUTER = r"^(?!app\.router(?:\.|$))app(?:\.|$)"

#: `app.agent` 子树，**但把 `app.agent.handoff` 排除在外**。
#:
#: 负向前瞻只挡住"紧跟 handoff 之后就是结尾或点"这一个形态，
#: 因此 `app.agent.handoff`、`app.agent.handoff.x` 都不会命中，
#: 而 `app.agent.handoff_extra` 仍会被挡住（它不是那道门）。
_AGENT_EXCEPT_HANDOFF = r"^app\.agent(?:\.(?!handoff(?:\.|$))[^.]+)*$"

#: 「`app.router` 除去 `contracts`」。
#:
#: `^app\.router$` 那一支是刻意的：`from app.router import contracts` 会执行
#: `app/router/__init__.py`，而那里 export 了 `Router` —— 所以它也算违规。
_ROUTER_EXCEPT_CONTRACTS = r"^app\.router$|^app\.router\.(?!contracts(?:\.|$))"


@dataclass(frozen=True)
class Rule:
    """一条依赖禁令：source 匹配的模块，不得引用 target 匹配的模块。"""

    id: str
    source: str
    target: str
    reason: str


RULES: tuple[Rule, ...] = (
    Rule(
        "ARCH-01",
        _NOT_ROUTER,
        r"^app\.global_domain\.writes(?:\.|$)",
        "全局写入是 Router 的专属权限：任何非 app.router.* 模块不得引用 app.global_domain.writes（M0 §4.1①）",
    ),
    Rule(
        "ARCH-02",
        _NOT_ROUTER,
        r"^app\.router\.policies(?:\.|$)",
        "更新策略实现对 LLM 与外部不可见：只有 Router 内部可引用 app.router.policies（M0 §7.3）",
    ),
    Rule(
        "ARCH-03",
        _NOT_ROUTER,
        r"^app\.core\.capability\.issue_token$",
        "令牌签发入口唯一：只有 app.router.* 可以 import issue_token（M0 §4.2②）",
    ),
    Rule(
        "ARCH-04",
        r"^app\.core(?:\.|$)",
        r"^app\.(?:global_domain|session_domain|benchmark|router|agent|api)(?:\.|$)",
        "app.core 是底层基础设施，不得依赖任何上层域（分层方向）",
    ),
    Rule(
        "ARCH-05",
        r"^app\.global_domain(?:\.|$)",
        r"^app\.(?:session_domain|benchmark|router|agent|api)(?:\.|$)",
        "全局权威域不得反向依赖沙箱域或调用方（M0 §3）",
    ),
    Rule(
        "ARCH-06",
        r"^app\.session_domain(?:\.|$)",
        r"^app\.(?:agent|api)(?:\.|$)",
        "沙箱域不得依赖其调用方（分层方向）",
    ),
    Rule(
        "ARCH-07",
        r"^app\.global_domain\.reads$",
        r"^app\.global_domain\.writes(?:\.|$)",
        "读模块不得持有写入口：全局读路径必须与写路径物理分离（M0 §4.3）",
    ),
    Rule(
        "ARCH-08",
        r"^(?!app\.(?:global_domain|router|seeds)(?:\.|$))app(?:\.|$)",
        r"^app\.global_domain\.models$",
        "全局 ORM 模型只对权威域 / Router / 种子开放：其他层必须经 reads.py 或 schemas.py，"
        "防止绕过 writes.py 直接 session.add()（M0 §4.1①）",
    ),
    Rule(
        "ARCH-09",
        r"^app\.router(?:\.|$)",
        r"^app\.session_domain(?:\.|$)",
        "写入裁定只看全局证据：stage 永不参与写入裁定（M0 §7.4）。"
        "Router 一旦能读到 Session 内的即时感受，「experienced 需 ≥2 条独立 Session 证据」"
        "这类门槛就会被绕过——因为当下感受随手可得，而沉淀后的证据不是",
    ),
    Rule(
        "ARCH-10",
        r"^app\.router(?:\.|$)",
        r"^app\.(?:agent|api)(?:\.|$)",
        "Router 与 Agent 解耦（M0 §13.4）：Agent 调 Router，Router 不认识 Agent。"
        "R1/R2 不经 Agent 但必经 Router；反过来 Router 依赖 Agent 会让"
        "「无 LLM 也能治理」这一 MVP 验收前提失效",
    ),
    Rule(
        "ARCH-11",
        r"^app\.session_domain(?:\.|$)",
        r"^app\.router(?:\.|$)",
        "沙箱域不认识 Router（P4 新增）。会话内的写入**永远不经过路由**，"
        "而「沉淀」是另一件事：由 `app/api/` 把沙箱里的候选条目映射成 UpdateIntent 再提交。"
        "若沙箱域能 import Router，它就有了「顺手写全局」的能力，"
        "而 §7.4 那套「stage 永不参与写入裁定」的隔离会退化成一句注释",
    ),
    Rule(
        "ARCH-12",
        # 「app.agent 子树，但排除 app.agent.handoff 本身」。
        _AGENT_EXCEPT_HANDOFF,
        # 「app.router 除去 contracts」——
        # `app.router` 这个包本身也算违规：`from app.router import contracts` 会执行
        # `app/router/__init__.py`，而那里 export 了 `Router`。
        _ROUTER_EXCEPT_CONTRACTS,
        "Agent 通往全局的唯一一道门是 `app.agent.handoff`（P6 新增）。"
        "沙箱→全局的翻译如果散落在多个模块里，那么「谁在什么时候把会话里的东西"
        "变成了结论」就有多个答案，而审计要回答的正是这个问题。"
        "**`app.router.contracts` 是唯一的例外**：它是写入契约（UpdateIntent 的形状），"
        "不是能力。M8 §4.6 要求工具 schema 从真实契约生成、不许手抄一份，"
        "所以 `tools.py` 必须能读到它。这个例外是安全的，因为真正拦得住的是"
        "ARCH-01（非路由层不得引用 global_domain.writes）与 ARCH-03（令牌签发入口唯一）——"
        "读得到契约、拿不到令牌，仍然写不进任何东西。",
    ),
)


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ImportRef:
    """一次被引用到的完整路径，可能是模块（a.b）或模块内名字（a.b.name）。"""

    target: str
    lineno: int
    raw: str


@dataclass
class ModuleRef:
    path: str
    file: Path
    imports: list[ImportRef]


@dataclass(frozen=True)
class Violation:
    rule_id: str
    source_module: str
    target: str
    lineno: int
    file: Path
    reason: str

    def render(self) -> str:
        return (
            f"[{self.rule_id}] {self.source_module}:{self.lineno} → {self.target}\n"
            f"    {self.reason}\n"
            f"    文件：{self.file}"
        )


class ArchCheckError(RuntimeError):
    """检查器**自身**无法完成分析（例如目标文件有语法错误）。

    与 `Violation` 严格区分：违规是「代码违反规则」，本异常是「规则无法被应用到代码上」。
    两者都必须让 CI 失败，但排障方向完全不同——混在一起会让人误以为架构被破坏。
    """


# --------------------------------------------------------------------------- #
# 扫描
# --------------------------------------------------------------------------- #


def _iter_py_files(package_dir: Path):
    for path in sorted(package_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _module_path(package_dir: Path, package_name: str, file: Path) -> tuple[str, bool]:
    rel = file.relative_to(package_dir)
    parts = list(rel.with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    module = ".".join([package_name, *parts]) if parts else package_name
    return module, is_package


def _resolve_from(node: ast.ImportFrom, current_module: str, is_package: bool) -> str:
    """把 `from ...x import y` 解析成绝对模块路径。"""
    if node.level == 0:
        return node.module or ""

    parts = current_module.split(".")
    if not is_package:
        parts = parts[:-1]  # 当前文件是模块时，先退到它所在的包
    drop = node.level - 1
    if drop:
        parts = parts[:-drop] if drop <= len(parts) else []
    base = ".".join(parts)
    if node.module:
        return f"{base}.{node.module}" if base else node.module
    return base


def _collect_imports(
    tree: ast.Module, current_module: str, is_package: bool
) -> tuple[list[ImportRef], dict[str, str]]:
    """返回 (被引用路径列表, 别名映射)。

    别名映射用于第二遍的属性访问检测，例如 `import app.core.capability as cap`
    会得到 {"cap": "app.core.capability"}，从而 `cap.issue_token` 可被解析。
    """
    refs: list[ImportRef] = []
    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                refs.append(ImportRef(alias.name, node.lineno, f"import {alias.name}"))
                bound = alias.asname or alias.name.split(".")[0]
                aliases[bound] = alias.name if alias.asname else bound
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(node, current_module, is_package)
            if not base:
                continue
            refs.append(
                ImportRef(base, node.lineno, f"from {base} import ...")
            )
            for alias in node.names:
                if alias.name == "*":
                    continue
                full = f"{base}.{alias.name}"
                refs.append(
                    ImportRef(full, node.lineno, f"from {base} import {alias.name}")
                )
                aliases[alias.asname or alias.name] = full

    return refs, aliases


def _dotted(node: ast.AST) -> str | None:
    """把 `a.b.c` 形式的属性访问链还原成字符串；无法还原时返回 None。"""
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    return ".".join(reversed(parts))


def _collect_attribute_refs(
    tree: ast.Module, aliases: dict[str, str]
) -> list[ImportRef]:
    """检测别名链上的属性访问，堵住「先 import 模块、再取属性」的绕行路径。"""
    refs: list[ImportRef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        chain = _dotted(node)
        if chain is None:
            continue
        head, _, rest = chain.partition(".")
        base = aliases.get(head)
        if base is None or not rest:
            continue
        refs.append(ImportRef(f"{base}.{rest}", node.lineno, f"属性访问 {chain}"))
    return refs


def scan(package_dir: Path, package_name: str | None = None) -> list[ModuleRef]:
    """扫描一个包目录，返回每个模块的引用清单。"""
    package_dir = Path(package_dir)
    if package_name is None:
        package_name = package_dir.name

    modules: list[ModuleRef] = []
    for file in _iter_py_files(package_dir):
        module, is_package = _module_path(package_dir, package_name, file)
        try:
            source = file.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ArchCheckError(f"无法以 UTF-8 读取 {file}：{exc}") from exc
        try:
            tree = ast.parse(source, filename=str(file))
        except SyntaxError as exc:
            raise ArchCheckError(
                f"无法解析 {file}:{exc.lineno} —— {exc.msg}。"
                "架构规则无法应用到一个解析失败的文件上，因此这是一次硬失败，"
                "不是「检查通过」。"
            ) from exc
        imports, aliases = _collect_imports(tree, module, is_package)
        imports.extend(_collect_attribute_refs(tree, aliases))
        modules.append(ModuleRef(path=module, file=file, imports=imports))
    return modules


# --------------------------------------------------------------------------- #
# 检查
# --------------------------------------------------------------------------- #


def check(
    package_dir: Path,
    package_name: str | None = None,
    rules: tuple[Rule, ...] = RULES,
) -> list[Violation]:
    """返回全部违规项（空列表 = 通过）。"""
    violations: list[Violation] = []
    seen: set[tuple[str, str, int, str]] = set()

    for module in scan(package_dir, package_name):
        for rule in rules:
            if not re.search(rule.source, module.path):
                continue
            for ref in module.imports:
                if not re.search(rule.target, ref.target):
                    continue
                key = (rule.id, module.path, ref.lineno, ref.target)
                if key in seen:
                    continue
                seen.add(key)
                violations.append(
                    Violation(
                        rule_id=rule.id,
                        source_module=module.path,
                        target=ref.target,
                        lineno=ref.lineno,
                        file=module.file,
                        reason=rule.reason,
                    )
                )

    return sorted(violations, key=lambda v: (v.rule_id, v.source_module, v.lineno))


def format_violations(violations: list[Violation]) -> str:
    if not violations:
        return "架构依赖检查通过：无违规。"
    lines = [f"架构依赖检查失败：发现 {len(violations)} 处违规。", ""]
    lines.extend(v.render() for v in violations)
    return "\n".join(lines)


def main() -> int:
    import sys

    # Windows 控制台默认 cp1252，直接 print 中文会 UnicodeEncodeError。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    backend_root = Path(__file__).resolve().parent.parent
    app_dir = backend_root / "app"
    try:
        violations = check(app_dir)
    except ArchCheckError as exc:
        print(f"架构依赖检查无法完成（这是硬失败，不是通过）：\n    {exc}")
        return 2
    print(format_violations(violations))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
