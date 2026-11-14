"""API 层的结构性约束 —— 用 AST 钉死，而不是靠注释和自觉。

`tools/archcheck.py` 已经管住了 import 方向（ARCH-01/02/03/08）。
这里补的是它**看不见**的四件事：

1. 处理函数有没有偷偷直接改 Session（`session.add/commit/execute/...`）；
2. DTO 有没有退回 `model_validate(orm)`（会撞同名 relationship，见 `schemas.py`）；
3. 每个端点有没有声明 `response_model`（否则 FastAPI 会把 ORM 对象直接序列化出去，
   等于绕过了 DTO 层，而 `from_attributes` 会让它在**能跑通**的情况下悄悄泄漏字段）；
4. 每个写端点是不是真的经 `Router` 走（依赖项里有没有 `Router`）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

from app.config import Settings
from app.main import create_app

API_DIR = Path(__file__).resolve().parents[2] / "app" / "api"

#: 处理函数绝不该直接对 Session 做的动作。
FORBIDDEN_SESSION_CALLS = {
    "add",
    "add_all",
    "delete",
    "commit",
    "flush",
    "execute",
    "merge",
    "refresh",
    "rollback",
    "begin",
    "begin_nested",
}

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: 沙箱域 API 模块。它们的写**不走 Router**（M0 §6.2：沙箱内自由读写）。
#:
#: 两类成员，区别在"全局通道"上：
#: - `folders.py` / `sessions.py` / `discuss.py`：**没有**通往全局的通道。
#:   `discuss.py` 看起来能写全局（它跑 Agent），但它的 Agent 只能经
#:   `app.agent.handoff` 提议 —— 那是 ARCH-12 钉住的唯一一道门。
#:   因此它同样**不得**依赖 Router：多一条依赖就多一条绕过判定表的路。
#: - `sedimentation.py`：沙箱 → 全局的唯一接口，它的两个端点**必须**依赖 Router。
SANDBOX_API_MODULES = frozenset(
    {
        "folders.py",
        "sessions.py",
        "sedimentation.py",
        "discuss.py",
        "attachments.py",
        #: ★ P6+.6：`sidebar.py` 属于沙箱域。
        #:
        #: 判断依据是**它写哪张表**：`sidebar_message` 的 provenance 是
        #: `sandbox_record`，`context_compact` 是 `append_only`。两者都不是
        #: 权威状态 —— 侧边栏的对话不参与上下文采用之外的任何决策，
        #: 而它要写全局**只能**走 `submit_global_update`（提案 → 用户确认），
        #: 那条路在 agent 的交接层里自带 Router 与令牌。
        #:
        #: 和 `discuss.py` 完全同构：那个端点也不依赖 Router，
        #: 却能通过同一个交接层产出全局提案。
        "sidebar.py",
    }
)

#: **基础设施域** API 模块：P6.x 引入的第三类。
#:
#: 为什么必须显式列出来、而不能"反正它不写偏好"就放过：
#: 这份文件里两条断言（写端点必须依赖 Router、全局写端点必须是那 5 条）
#: 的原话都是"**所有**写端点"。第三类域出现时，它们会变红 —— 那是设计好的警报，
#: 逼出一次表态：模型调用配置到底算不算权威状态？
#:
#: 结论是**不算**：它不参与上下文采用、不被 Router 裁定、不进任何决策依据。
#: 改它不会让任何一条基线或偏好变化，因此它不需要治理语义（不需要提案、
#: 不需要审计日志）—— 给它加 Router 反而是把"换一个模型"变成一次治理事件。
#:
#: 但豁免必须被**证明**，不能只是声明。三条新断言钉住它：
#: - `test_infra_modules_declare_only_infra_prefixes`：只能挂 `/api/llm`；
#: - `test_infra_modules_never_import_any_write_domain`：不许 import Router、
#:   也不许 import 全局域/沙箱域的写函数 —— 它只能通过 `app.llm_config` 写自己那张表；
#: - `test_llm_config_writes_do_not_touch_cognition`（在
#:   `tests/test_llm_config_boundary.py`）：写完配置后逐表比对行数、并跑 I1–I7。
INFRA_API_MODULES = frozenset({"llm.py"})

#: 基础设施域唯一允许挂载的路径前缀。由
#: `test_infra_modules_declare_only_infra_prefixes` 校验。
INFRA_PREFIXES = frozenset({"/api/llm"})

#: 沙箱域 API 唯一允许挂载的路径前缀。**由测试校验每个沙箱模块声明的
#: `APIRouter(prefix=...)` 都在这个集合里**，见
#: `test_sandbox_modules_declare_only_sandbox_prefixes`。
#:
#: ★ `/api/sidebar` 之所以属于沙箱域而不是自己开第三类：它跟
#: `/api/sessions` 是**同一个域的两个入口** —— 都写沙箱表、都不需要治理语义。
#: 给它开一类"侧边栏域"会让这份文件里的每一条域断言都要加一个分支，
#: 而那个分支回答的问题（"侧边栏的写要不要过 Router"）与沙箱域完全一样。
SANDBOX_PREFIXES = frozenset({"/api/folders", "/api/sessions", "/api/sidebar"})


def _is_sandbox_route(path: str) -> bool:
    return any(
        path == prefix or path.startswith(prefix + "/") for prefix in SANDBOX_PREFIXES
    )


def _router_prefix(tree: ast.Module) -> str | None:
    """从模块源码里取出 `router = APIRouter(prefix="/api/...")` 的 prefix。"""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        if node.targets[0].id != "router" or not isinstance(node.value, ast.Call):
            continue
        for keyword in node.value.keywords:
            if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                return str(keyword.value.value)
    return None


def _api_modules() -> list[Path]:
    return sorted(path for path in API_DIR.glob("*.py"))


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _decorator_name(node: ast.expr) -> str | None:
    """把 `@router.post(...)` 还原成 `"router.post"`。"""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return None


def _route_functions(tree: ast.Module) -> list[tuple[str, ast.FunctionDef]]:
    out: list[tuple[str, ast.FunctionDef]] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            name = _decorator_name(decorator)
            if name and name.startswith("router."):
                out.append((name, node))
                break
    return out


def _app() -> FastAPI:
    return create_app(
        Settings(database_url="sqlite+pysqlite:///:memory:", seed_on_startup=False)
    )


def _all_routes(app: FastAPI) -> list[APIRoute]:
    """摊平出 app 上的全部 `APIRoute`。

    这一版 FastAPI 把 `include_router` 的结果保留为 `_IncludedRouter` 包装对象
    （`app.routes` 里**看不到**被包含的路由，只有 `original_router` 指向真正的
    `APIRouter`）。不摊平的话，下面的断言会全部空转通过 —— 一个永远为绿、
    却什么都没检查的测试比没有测试更坏。因此这里既递归展开，也断言结果非空。
    """
    out: list[APIRoute] = []
    seen: set[int] = set()

    def walk(routes: object) -> None:
        for route in routes:  # type: ignore[union-attr]
            if isinstance(route, APIRoute):
                out.append(route)
                continue
            inner = getattr(route, "original_router", None)
            if inner is not None and id(inner) not in seen:
                seen.add(id(inner))
                walk(inner.routes)

    walk(app.routes)
    assert out, "摊平后没有任何 APIRoute —— FastAPI 的内部结构变了，请更新 _all_routes"
    return out


def test_api_modules_exist() -> None:
    names = {path.name for path in _api_modules()}
    assert {
        "m1.py",
        "preferences.py",
        "evidence.py",
        "proposals.py",
        "router_logs.py",
        "folders.py",
        "sessions.py",
        "sedimentation.py",
    } <= names


@pytest.mark.parametrize("path", _api_modules(), ids=lambda p: p.name)
def test_api_never_mutates_the_session_directly(path: Path) -> None:
    """API 层不是「薄一点的 service」—— 它**没有**写权限。

    一旦这里出现 `session.add(...)`，P2 的全部保证（能力令牌、判定表、
    审计日志、冲突裁定）都会被绕过，而且绕得毫无痕迹：没有日志，没有提案，
    没有任何东西能事后指认这次写入发生过。
    """
    offenders: list[str] = []
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in FORBIDDEN_SESSION_CALLS:
            continue
        owner = node.func.value
        if isinstance(owner, ast.Name) and owner.id in {"session", "db", "s"}:
            offenders.append(f"{path.name}:{node.lineno} session.{node.func.attr}()")
    assert offenders == [], (
        "API 层直接改 Session 是绕过 Router 的写入路径：\n  " + "\n  ".join(offenders)
    )


def test_no_dto_is_built_from_an_orm_row_with_a_same_named_relationship() -> None:
    """`M1Variable` 与 `UpdateProposal` 上都有与 DTO 字段**同名**的 relationship。

    `model_validate(orm)` 会按名字把 relationship 读进来：
    对 `M1VariableRead.channels` 直接抛一条与真正原因无关的 `Field required`；
    对 `ProposalDetailRead.intents` 更糟 —— 它**能跑通**，只是偷偷多查一次库，
    并让「intent 从哪来」失去唯一答案。因此这两个 DTO 必须显式构造。
    """
    guarded = {
        "m1.py": "M1VariableRead",
        "proposals.py": "ProposalDetailRead",
    }
    offenders: list[str] = []
    for filename, dto in guarded.items():
        source = (API_DIR / filename).read_text(encoding="utf-8")
        if f"{dto}.model_validate" in source:
            offenders.append(f"{filename} 使用了 {dto}.model_validate(...)")
    assert offenders == [], (
        "改用 schemas 里显式逐字段构造的 classmethod：\n  " + "\n  ".join(offenders)
    )


def test_every_route_declares_a_response_model() -> None:
    """没有 `response_model` 的端点会把 ORM 对象直接序列化出去。

    那意味着「API 只能看到 DTO」这条约束在这条路径上失效 ——
    而 FastAPI 的 `from_attributes` 会让它顺利工作，直到某天某个人
    在 ORM 模型上加了一个不该外泄的字段（比如未来的 `internal_note`）。

    **流式端点（SSE）是唯一的例外，而且这个例外不能被意外获得。**

    SSE 没有"一个 JSON 响应体"可言，给它写 `response_model` 是在文档里撒谎。
    但豁免不能按路径名去猜（拼一个 `/stream` 后缀就绕过了）——
    判据是 `response_class` 真的是 `StreamingResponse` 的子类。
    于是要拿到豁免，作者必须**显式声明**这个端点返回的是流；
    而"流式端点的帧里只放显式构造的 dict、不放 ORM 行"这件事由
    `tests/agent/test_events.py` 钉住（`AgentEvent` 只接受 `dict`）。
    """
    streaming: list[str] = []
    offenders: list[str] = []
    for route in _all_routes(_app()):
        label = f"{sorted(route.methods)[0]} {route.path}"
        # `isinstance(response_class, type)` 这个前置判断是必需的：
        # 没声明 `response_class` 的路由拿到的不是类，而是 FastAPI 的
        # `DefaultPlaceholder`；直接 `issubclass()` 会抛 TypeError，
        # 而那条报错与真正的问题（缺少 response_model）毫无关系。
        response_class = getattr(route, "response_class", None)
        if isinstance(response_class, type) and issubclass(
            response_class, StreamingResponse
        ):
            streaming.append(label)
            continue
        if route.response_model is None:
            offenders.append(label)

    assert offenders == [], "以下端点缺少 response_model：\n  " + "\n  ".join(offenders)
    # 流式端点的名单是显式的：新增一条就必须来这里写一次，
    # 而不是"反正它不会进 offenders 列表"。
    assert sorted(streaming) == ["POST /api/sessions/{session_id}/discuss/stream"], (
        "流式端点名单变了。若这是有意新增的，请更新这条断言；"
        "若它只是恰好没有被 response_model 覆盖到，那是一个需要修的缺陷。\n  "
        + "\n  ".join(sorted(streaming))
    )


def test_every_write_route_depends_on_the_router() -> None:
    """写端点必须经 Router —— 依赖项里得有 `Router`。

    UI 直写之所以仍是「受治理的写入」，唯一的原因就是它在代码上也走这条依赖。

    **P4 之后的修正**：这条断言原先覆盖全部写端点。P4 引入了第二个合法的写入域
    （Session 沙箱，M0 §6.2「沙箱内自由读写」），它的写**不应该**过 Router ——
    会话内的计划调整、笔记、聊天不改变任何权威状态，让它们排队等判定表
    会把"改一句计划措辞"变成一次治理事件。

    于是断言从「所有写都过 Router」改成**双向钉住边界**：
    全局域的写必须过；沙箱域的写必须不过。后者见
    `test_session_write_routes_never_touch_the_router`。
    """
    offenders: list[str] = []
    for path in _api_modules():
        if path.name in SANDBOX_API_MODULES or path.name in INFRA_API_MODULES:
            continue
        for decorator, function in _route_functions(_tree(path)):
            if decorator.rsplit(".", 1)[-1] not in {"post", "put", "patch", "delete"}:
                continue
            annotations = {ast.unparse(arg.annotation) for arg in function.args.args if arg.annotation}
            if not any("Router" in text for text in annotations):
                offenders.append(f"{path.name}:{function.lineno} {function.name}")
    assert offenders == [], (
        "以下全局域写端点没有依赖 Router：\n  " + "\n  ".join(offenders)
    )


def test_session_write_routes_never_touch_the_router() -> None:
    """沙箱域的写端点**不得**依赖 Router —— 唯一例外是那两个交接点。

    ARCH-11 已经在 import 层面禁止 `app.session_domain` 认识 Router。
    这条测试补的是它看不见的另一半：**调用方那一层**也不能随手把 Router
    塞进沙箱端点。否则沙箱里的写入就有了第二条通往外部的路，
    而「沉淀是沙箱 → 全局的唯一接口」会退化成一句注释。

    `sedimentation.py` 里的两个端点正是那个唯一接口：
    `POST /sediment`（预览/执行沉淀）与 `POST /complete`（完成并产出 R4a 提案）。
    """
    sandbox_files = {
        path for path in _api_modules() if path.name in SANDBOX_API_MODULES
    }
    #: 允许（且必须）依赖 Router 的交接点，按「文件名 → 函数名」精确列出。
    handoffs = {
        ("sedimentation.py", "sediment"),
        ("sedimentation.py", "complete"),
    }

    offenders: list[str] = []
    missing_handoff: list[str] = []
    seen: set[tuple[str, str]] = set()

    for path in sandbox_files:
        for decorator, function in _route_functions(_tree(path)):
            if decorator.rsplit(".", 1)[-1] not in {"post", "put", "patch", "delete"}:
                continue
            annotations = {
                ast.unparse(arg.annotation) for arg in function.args.args if arg.annotation
            }
            has_router = any("Router" in text for text in annotations)
            key = (path.name, function.name)
            if key in handoffs:
                seen.add(key)
                if not has_router:
                    missing_handoff.append(f"{path.name}:{function.lineno} {function.name}")
                continue
            if has_router:
                offenders.append(f"{path.name}:{function.lineno} {function.name}")

    assert offenders == [], (
        "沙箱域的写端点依赖了 Router（会造出第二条通往全局的路）：\n  "
        + "\n  ".join(offenders)
    )
    assert missing_handoff == [], (
        "沉淀交接点必须依赖 Router，否则沉淀根本没经过判定表：\n  "
        + "\n  ".join(missing_handoff)
    )
    assert seen == handoffs, "交接点改名或删除了 —— 请同步更新这份清单与设计说明"


def test_only_the_declared_handoff_file_imports_the_router() -> None:
    """沙箱域 API 的 import 层检查：**annotation 检查漏掉的那个洞**。

    `test_session_write_routes_never_touch_the_router` 看的是函数签名里有没有
    `Router`。而 `from app.router import Router` 写在函数体里、或者干脆写成一个
    模块级 import 却没进签名，签名检查一个字也看不见 ——
    那时"沙箱里多出第二条通往全局的路"是真的，而测试是绿的。

    P6 的 `discuss.py` 让这个洞变得危险：它跑 Agent，天然"离全局很近"。
    它唯一该走的门是 `app.agent.handoff`（ARCH-12），
    所以这里把「除了那个声明过的交接文件之外，谁都不许 import Router」
    在**每个沙箱 API 模块**上钉一遍。
    """
    #: 允许 import Router 的文件：它里面装着声明过的交接点。
    #: 不是按文件名豁免，而是按"这个文件里真的有交接点"——
    #: 见上一条测试里 `handoffs` 那份清单与 `seen == handoffs` 的断言。
    handoff_files = {"sedimentation.py"}

    offenders: list[str] = []
    for path in _api_modules():
        if path.name not in SANDBOX_API_MODULES or path.name in handoff_files:
            continue
        for module in _imported_modules(_tree(path)):
            if module == "app.router" or module.startswith("app.router."):
                offenders.append(f"{path.name} → {module}")

    assert offenders == [], (
        "沙箱域 API 模块不得 import Router（那会造出第二条通往全局的路）：\n  "
        + "\n  ".join(offenders)
    )

    # 检查器本身必须真的能抓到东西。否则"零违规"可能只是因为它什么都没提取出来——
    # 这种假绿正是本文件开头那段话要防的。
    declared = next(p for p in _api_modules() if p.name in handoff_files)
    assert any(
        module == "app.router" or module.startswith("app.router.")
        for module in _imported_modules(_tree(declared))
    ), "提取器没在交接文件里找到 Router —— 说明这个检查是假绿"


def _imported_modules(tree: ast.Module) -> list[str]:
    """文件里出现过的完整模块名，包括 `from a.b import c` 里的 `a.b`。

    刻意**不**把 `from app.router import Router` 里的 `Router` 当模块名：
    那个位置可能是模块也可能是类，而把它当模块名会让检查器对
    `from app.router.contracts import X` 这种合法写法误报。
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def test_audit_log_endpoint_is_read_only() -> None:
    """一个能手工追加日志的端点会让整个审计体系失去意义。"""
    routes = [
        route for route in _all_routes(_app()) if route.path.startswith("/api/router-logs")
    ]
    assert routes, "没找到 /api/router-logs 路由"
    methods = {method for route in routes for method in route.methods}
    assert methods & WRITE_METHODS == set()
    assert "GET" in methods


def test_no_second_write_path_to_preferences_or_evidence() -> None:
    """审一遍**全局域**的全部写端点：只允许这些。

    如果哪天多出一条「删除偏好」的端点而没有配套的治理语义，
    这条测试会拦下来 —— 它要的就是逼出那个设计讨论，而不是让实现悄悄溜过去。

    **P4 之后的修正**：沙箱域的写端点（`/api/sessions/**`、`/api/folders`）
    不再列进这份清单。它们不写全局，因此不属于"通往偏好/证据的第二条路"。
    但清单本身一个字都没放松：全局域的写端点仍然是精确的集合，
    新增一条就必须同时更新这里和设计说明。

    ★ **基准块（本轮）一次加了 6 条，这是全局写入面的一次真实扩大** ——
    不是"顺手多写了点东西"。它们的共同点是：都经过 Router 写入全局域
    （`benchmark_run` / `benchmark_response` / `domain_profile`），
    且都在 `PROVENANCE` 里逐张表表了态。其中 `domains` 是 **R6**：
    过 Router 记账、但**不过判定表**（它是测量的参照，不是关于特质的断言）。
    这条测试要求的就是有人把这个讨论写下来 —— 现在写在这里。
    """
    writes = {
        (sorted(route.methods)[0], route.path)
        for route in _all_routes(_app())
        if route.methods & WRITE_METHODS
        and not _is_sandbox_route(route.path)
        and not _is_infra_route(route.path)
    }
    assert writes == {
        ("POST", "/api/preferences"),
        ("POST", "/api/evidence"),
        # 假说（P7）。它与上一条是**一对**而不是一条带参数：
        # 两个端点各自只装一种可信度，于是"他试没试过"由他点了哪个表单决定，
        # 不由调用方传参决定。合成一条 + `kind` 参数会让调用方替用户判断。
        ("POST", "/api/hypotheses"),
        # ↓ P7.1：条目生命周期（归档 / 编辑 / 恢复），两族逐条同构。
        #   它们的共同点：都经 Router、都走判定表、都**不是物理删除**。
        ("DELETE", "/api/evidence/{evidence_id}"),
        ("PUT", "/api/evidence/{evidence_id}"),
        ("POST", "/api/evidence/{evidence_id}/restore"),
        ("DELETE", "/api/hypotheses/{hypothesis_id}"),
        ("PUT", "/api/hypotheses/{hypothesis_id}"),
        ("POST", "/api/hypotheses/{hypothesis_id}/restore"),
        ("POST", "/api/proposals"),
        ("POST", "/api/proposals/{proposal_id}/resolve"),
        ("POST", "/api/m1/{variable_id}/rollback"),
        # 领域勾选清单（R6，过 Router 记账、不过判定表）
        ("POST", "/api/benchmark/domains"),
        # 基准运行的生命周期（不进判定表：运行记录是断言的依据，不是断言）
        ("POST", "/api/benchmark/runs"),
        ("POST", "/api/benchmark/runs/{run_id}/status"),
        ("POST", "/api/benchmark/runs/{run_id}/restart"),
        ("POST", "/api/benchmark/runs/{run_id}/responses"),
        ("POST", "/api/benchmark/runs/{run_id}/finish"),
        # ↓ P9：**计分**。它同样是全局写入（改写 `benchmark_response` 的计分五列），
        #   所以必须经 Router。与上面几条的区别是它改的是**已有的那一行**，
        #   而不是追加 —— 这是刻意的：计分是对同一次作答的重新判定，
        #   追加会让"他答了几题"越算越大（而那正是最小题数的分母）。
        #   它只改分数与判官痕迹，`raw_answer` 一个字都不动：
        #   判分变了要能看出来**是判分变了**，不是他改了答案。
        ("POST", "/api/benchmark/runs/{run_id}/score"),
        # 自评单例（P25）。它与基准运行同一个姿态：**运行记录不是断言**，
        # 因此不进判定表；但它确实写全局（11 条 `stated` 基线），
        # 所以必须经 Router —— 也就必须出现在这份清单里。
        # ★ 它是**另一把尺子**：不给人材料、不计时判分、只产 `stated` 通道。
        ("POST", "/api/self-assessment/submit"),
        # 基线测卷（固定卷，P25.1）。与上面两族同姿态：**运行记录不是断言**，
        # 因此不进判定表；但它确实写全局（`baseline_paper_run`），所以必须经
        # Router —— 也就必须出现在这份清单里。
        # 三条对应一次作答的生命周期：建运行 / 逐段存档 / 收卷。
        # 存档与收卷都是**改写同一行**而不是追加：一份卷子只有一份作答。
        ("POST", "/api/benchmark/baseline/runs"),
        ("POST", "/api/benchmark/baseline/runs/{run_id}/answers"),
        ("POST", "/api/benchmark/baseline/runs/{run_id}/finish"),
    }

    # **反向也钉一遍**：确认上面那条"排除"真的排掉了东西。
    # 否则将来有人把 `_is_infra_route` 写成恒假，这里会静默通过 ——
    # 而那时 llm 的写端点已经在"全局写"的集合里被悄悄容忍了。
    infra_writes = {
        (sorted(route.methods)[0], route.path)
        for route in _all_routes(_app())
        if route.methods & WRITE_METHODS and _is_infra_route(route.path)
    }
    assert infra_writes == {
        ("POST", "/api/llm/profiles"),
        ("PATCH", "/api/llm/profiles/{profile_id}"),
        ("DELETE", "/api/llm/profiles/{profile_id}"),
        ("POST", "/api/llm/profiles/{profile_id}/activate"),
        ("POST", "/api/llm/profiles/{profile_id}/models"),
        ("POST", "/api/llm/profiles/{profile_id}/probe"),
    }, "基础设施域的写端点集合变了 —— 新增一条请同时更新这里与设计说明"


def _is_infra_route(route_path: str) -> bool:
    """该路径属于基础设施域。

    与 `_is_sandbox_route` 同口径：**按前缀判断，而前缀本身由另一条测试
    从模块声明处校验**（`test_infra_modules_declare_only_infra_prefixes`）——
    否则"是不是基础设施域"就成了被测对象自己说了算。
    """
    return any(
        route_path == prefix or route_path.startswith(f"{prefix}/")
        for prefix in INFRA_PREFIXES
    )


def test_infra_modules_declare_only_infra_prefixes() -> None:
    """基础设施域模块只能挂 `/api/llm`。

    这是"豁免"的代价：既然免掉了 Router 依赖与全局写端点清单，
    就必须保证它**够不着别的域** —— 一个能挂 `/api/preferences` 的
    "基础设施"模块，会让上面两条断言同时失效而看不出因果。
    """
    offenders: list[str] = []
    for path in _api_modules():
        if path.name not in INFRA_API_MODULES:
            continue
        prefix = _router_prefix(_tree(path))
        if prefix not in INFRA_PREFIXES:
            offenders.append(f"{path.name}: prefix={prefix!r}")
    assert offenders == [], "基础设施域模块挂了其它前缀：\n  " + "\n  ".join(offenders)
    # 确认这个检查器真的在看东西（假绿防护）。
    assert _router_prefix(_tree(next(p for p in _api_modules() if p.name in INFRA_API_MODULES)))


def test_infra_modules_never_import_any_write_domain() -> None:
    """基础设施域不许 import Router，也不许 import 任何域的写函数。

    它唯一被允许的写入对象是自己的那张表（`app.llm_config`）。
    这条断言是"它不写权威状态"在**代码结构**上的表达 ——
    `tests/test_llm_config_boundary.py` 从**行为**上再证一遍（逐表比对行数）。
    结构 + 行为两层都钉住，是因为单看任一层都有绕过方式：
    import 一个"中立"的模块、或者用 SQL 直写。
    """
    forbidden_prefixes = (
        "app.router",
        "app.global_domain.writes",
        "app.session_domain.writes",
    )
    offenders: list[str] = []
    for path in _api_modules():
        if path.name not in INFRA_API_MODULES:
            continue
        for module in _imported_modules(_tree(path)):
            if any(
                module == prefix or module.startswith(f"{prefix}.")
                for prefix in forbidden_prefixes
            ):
                offenders.append(f"{path.name} → {module}")
    assert offenders == [], (
        "基础设施域 API 不许 import 写域（那会造出绕过 Router 的认知写入路径）：\n  "
        + "\n  ".join(offenders)
    )


def test_sandbox_modules_declare_only_sandbox_prefixes() -> None:
    """每个沙箱域 API 模块声明的 `APIRouter(prefix=...)` 必须落在沙箱前缀内。

    这条测试存在的理由是**上一条不能靠路径自己判断自己**：
    如果沙箱域能挂 `/api/preferences`，那"沙箱内自由写"就变成了
    "全应用自由写"，而 `test_no_second_write_path_to_preferences_or_evidence`
    会把它当成一条新的全局写端点拦下来 —— 但那时已经很难看出
    它其实是沙箱域违规挂载造成的。这里从模块**声明**处拦，因果最清楚。
    """
    offenders: list[str] = []
    for path in _api_modules():
        if path.name not in SANDBOX_API_MODULES:
            continue
        prefix = _router_prefix(_tree(path))
        if prefix not in SANDBOX_PREFIXES:
            offenders.append(f"{path.name}: prefix={prefix!r}")
    assert offenders == [], (
        "沙箱模块挂了沙箱之外的路径前缀：\n  " + "\n  ".join(offenders)
    )


def test_app_health_is_a_liveness_probe_only() -> None:
    """`/api/health` 不得返回任何全局状态摘要 —— 那会成为一个绕过 `reads` 的读路径。"""
    route = next(
        route for route in _all_routes(_app()) if route.path == "/api/health"
    )
    assert set(route.response_model.model_fields) == {"status"}


def test_the_openapi_document_can_still_be_generated() -> None:
    """`/openapi.json` 必须能生成 —— 一条路由的属性就能让它整个 500。

    真实事故：P6+.1 加了 `POST /discuss/stream` 之后，**整个 `/docs` 与
    `/openapi.json` 变成 500**，而当时没有任何测试盯着这件事（文档里还写着
    "`/docs` 37 paths"）。根因在 FastAPI 里：它靠

        if route.status_code is not None: status_code = ...
        else: inspect.signature(current_response_class.__init__) 里找 status_code

    去推断响应码。我们的 `EventStreamResponse` 重写了 `__init__` 却没有
    暴露名为 `status_code` 的参数，于是那个 `status_code` 局部变量从未被赋值，
    `get_openapi_path` 抛 `UnboundLocalError`。

    修法是**在路由上显式写 `status_code=200`** —— 不依赖框架的推断技巧。
    这条测试盯的是结果（文档页能不能生成），不是那句修法。
    """
    app = _app()
    with TestClient(app) as client:
        response = client.get("/openapi.json")
        assert response.status_code == 200, response.text

        paths = response.json()["paths"]
        # 37 → 44：P6.x 的模型调用配置（`/api/llm/*`，7 条路径）。
        # 44 → 46：P6+.5 的会话资料（`/api/sessions/{id}/attachments` 与
        #         `.../attachments/{attachment_id}`，2 条路径承载 4 个方法）。
        # 46 → 55：基准块（`/api/benchmark/*`，9 条路径）——
        #         领域勾选清单 3 条（`domains` / `domains/history` / `state`）
        #         + 运行生命周期 6 条。**这是后端第一次能真的跑一次基准测试**：
        #         在此之前 `benchmark_run` 表只有一个测试在写它。
        # 这个数字是给人看的**同步锚**：路径数变了就必须来改一次，
        # 顺带想起"文档里那个数字也要改"。
        # 55 → 59：侧边栏常驻模型（`/api/sidebar/*`，4 条路径）——
        #         `state` / `context` / `discuss` / `messages`。
        #         **这是侧边栏第一次真的是一个功能**：此前那 8 条需求
        #         （常驻、有记忆、自动 compact、显示 token、能写偏好…）
        #         全部卡在"没有端点"上。
        # 这个数字是给人看的**同步锚**：路径数变了就必须来改一次，
        # 顺带想起"文档里那个数字也要改"。
        # 59 → 60：假说（`/api/hypotheses`，P7 语义拆分）。
        #         **这不是"多了一个功能"，是把一条一直能走通、但要走通只能
        #         把 `experienced` 谎报成 `stated` 的路补上了名字。**
        #         同一轮里 `C.evidence` + `stated` 由「自动生效」变成「拒绝」。
        # ★ P7.1：+4 条路径（两族各自的 `{id}` 与 `{id}/restore`）=
        #   归档 / 编辑 / 恢复。用户裁定「解释权永远在用户手里」，
        #   于是 C 证据与假说都拿到了完整的条目生命周期。
        #   P13：+4 条路径前缀（`GET|POST /clarifications` 与
        #   `/{id}/answer`、`/{id}/dismiss`）—— 澄清问题（任务不明确时反问）。
        #   P14：0 条 —— 外挂能力只加了 `GET /api/external/tools`（在 70 之内）。
        #   P15：+2 条路径（`GET|POST /annotations` 与
        #   `GET|DELETE /annotations/{id}`）—— 标注某句 + 看推断理由。
        #   ★ 注意 POST+GET 是**同一条路径**：路径数因此只涨 2，不是 4。
        #   P15 续：+2 条路径（`GET /reflection` 指引 与
        #   `GET|POST /reflections` 感想）—— 复盘之后按指引写感想。
        #   ★ 单数 `reflection` 是**指引**（现在该怎么写），
        #     复数 `reflections` 是**写下的那些**。刻意不合成一条路径：
        #     合并会得到一个「GET 有时返回指引、有时返回列表」的端点。
        # 74 → 76：侧栏整理（`PATCH|DELETE /api/folders/{id}` 与
        #         `PATCH|DELETE /api/sessions/{id}`）—— **两条路径承载四个方法**。
        #         为什么"挪一位"塞进 `PATCH` 的 `move` 字段而不是单开 `/{id}/move`：
        #         挪动是对同一个资源的修改，不是一种新动作；顺带省掉两条路径，
        #         也让"改名 + 归档 + 挪动"能在**一个请求**里原子完成。
        #         ★ 这一组里 `PATCH` **改不了 `state`**：状态只能走 `/transitions`。
        #         那不是省事，是"归档 ≠ 放弃"这条口径的结构保证。
        # 76 → 77：`GET /api/sessions/{id}/context` —— 上下文计量独立成一条路径。
        #         它不是 `/state` 的一部分：窗口上限来自**此刻生效的模型**，
        #         换模型之后这一栏要能单独刷新；塞进 `/state` 会让会话数据
        #         因为"用哪个模型"而改变形状。
        # 77 → 78：`POST /api/sessions/{id}/archive-review` —— 归档后的自动复盘。
        #         ★ 它**不写用户消息**：内部指令若落成"用户说过的话"，
        #         会污染 `stated` 的证据链；只落模型回复。
        # 78 → 83：自评单例（`/api/self-assessment/*`，5 条路径）。
        #         它与基准测试分开：不给人材料、只产 `stated`，全屏作答。
        # 83 → 89：基线测卷（`/api/benchmark/baseline/*`，6 条路径）——
        #         卷面、运行列表、上次运行、单次运行、逐段存档、收卷。
        #         它是**固定卷**：一次覆盖多段多变量，与按变量抽题的
        #         `/api/benchmark/runs` 不是同一件事。
    assert len(paths) == 89, f"路径数变了（现在是 {len(paths)}）：请同步文档里的数字"

    stream = paths["/api/sessions/{session_id}/discuss/stream"]["post"]
    assert "200" in stream["responses"], "流式路由没有声明 200 响应"


def test_app_factory_is_reusable_with_independent_databases() -> None:
    """`create_app` 必须能造出互不影响的两个实例（否则测试之间会串数据）。"""
    first = _app()
    second = _app()
    assert first is not second
    assert isinstance(first, FastAPI)


# --------------------------------------------------------------------------- #
# 列表响应的统一信封
# --------------------------------------------------------------------------- #


def test_every_list_endpoint_uses_the_envelope() -> None:
    """★ 所有列表端点的数组都必须在 `items` 下 —— 一个裸数组都不许有。

    ## 这条测试为什么必须存在

    形状统一这件事**没有别的地方会守**：每个端点的单测都对着自己的形状写，
    每个都「符合自己的约定」。而形状不一致的代价由**调用方**付 ——
    他写第 18 个列表页时又要看一眼文档，看错的表现是
    `data.map is not a function`：一个离出错点很远的运行时错误。

    ## 判据是「数组在 items 下」，不是「信封里只有 items」

    `/api/sessions/{id}/attachments` 合法地带着 `session_id` 兄弟字段，
    而「信封能装兄弟字段」正是引入信封的**理由**（总数 / 下一页 / 用了什么过滤）。
    我第一版写成了 `set(body) == {"items"}`，于是那个端点被自己的设计判成缺陷 ——
    **检查比设计更严，是一种更难发现的错误。**

    ## 为什么它比「形状一致」强

    「混用了 N 种形状」只能说**有问题**，说不出**该是什么样**，于是它可以一直红着。
    钉住「必须是 items」才是一条能拦住回归的检查 ——
    新加一个裸数组端点会立刻变红，而不必靠记性。
    """
    spec = _app().openapi()
    components = spec["components"]["schemas"]

    bare: list[str] = []
    suspicious: list[str] = []
    enveloped: dict[str, str] = {}
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            schema = (
                ((operation.get("responses") or {}).get("200") or {})
                .get("content", {})
                .get("application/json", {})
                .get("schema")
                or {}
            )
            where = f"{method.upper()} {path}"
            if schema.get("type") == "array":
                bare.append(where)
                continue
            name = (schema.get("$ref") or "").rsplit("/", 1)[-1]
            component = components.get(name) or {}
            properties = component.get("properties") or {}
            if component.get("x-mouxue-envelope") == "items":
                enveloped[where] = name
                continue
            # 「只有一条数组字段」的对象也算列表端点 —— 否则
            # 有人把信封写成 `{"rows": [...]}` 就能绕过上面那条裸数组检查。
            # `BenchmarkPlanRead` 有 9 个字段，因此**不会**被这条误伤。
            arrays = [
                key
                for key, value in properties.items()
                if value.get("type") == "array"
            ]
            if len(properties) == 1 and len(arrays) == 1:
                suspicious.append(f"{where}（字段 {arrays[0]!r}）")

    assert bare == [], (
        "这些端点返回裸数组，必须是 {\"items\": [...]}：\n  " + "\n  ".join(bare)
    )
    assert suspicious == [], (
        "这些端点看起来是列表端点，但没有用 ItemsResponse：\n  "
        + "\n  ".join(suspicious)
        + "\n（继承 `ItemsResponse[...]` 即可，它会带上 `x-mouxue-envelope` 标记）"
    )
    assert len(enveloped) == 23, (
        f"列表端点数变了（现在 {len(enveloped)} 个）：新增列表端点必须用 "
        f"`ItemsResponse[...]`，同时更新 tools/e2echeck.py 的 LIST_ENDPOINTS"
    )
    # 信封里的 `items` 必须是**具体的**条目 schema，不能退化成 `Any` / 无类型数组 ——
    # 统一形状不该拿类型精度去换。这条同时允许"信封 + 兄弟字段"
    # （`AttachmentListRead` 就带着 `session_id`，而那是信封存在的理由）。
    for where, name in enveloped.items():
        items_schema = components[name]["properties"]["items"]
        ref = (items_schema.get("items") or {}).get("$ref")
        assert isinstance(ref, str), (
            f"{where} 的信封里 items 没有具体条目类型（{items_schema}）—— "
            f"统一形状不该拿类型精度去换"
        )
